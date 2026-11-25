"""MCK-527: server transactions must be valid on the Sentry wire.

``before_send_transaction`` scrubs student secrets out of the event. The
scrub used to rewrite the timestamps and ids too (``2026-11-25T19:00:01Z``
became ``2026-11-[redacted]:00:01Z``), so Relay rejected every transaction
as ``invalid_transaction`` and answered HTTP 400 for a mangled envelope
``event_id`` (the SDK reports that as ``network_error``). These tests run the
real SDK with the production options and check the bytes that would be sent.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest import mock

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

import sentry_sdk  # noqa: E402
from sentry_sdk.transport import Transport  # noqa: E402

import sentry_wire  # noqa: E402
from app import create_app  # noqa: E402

try:  # Optional: the real Relay normalizer, the same code Sentry ingest runs.
    from sentry_relay.processing import StoreNormalizer
except ImportError:  # pragma: no cover - depends on the dev machine
    StoreNormalizer = None

STUDENT_NAME = "Zorblaxine"
HEX32 = re.compile(r"^[0-9a-f]{32}$")
HEX16 = re.compile(r"^[0-9a-f]{16}$")
RFC3339 = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$")
MAX_SPANS = 1000
MAX_DESCRIPTION = 200
MAX_PAYLOAD_BYTES = 200_000
MAX_SPAN_DATA_KEYS = 50


def _when(text: str) -> datetime:
    """Parse a wire timestamp, failing the test on a malformed one."""
    assert isinstance(text, str) and RFC3339.match(text), text
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def validate_transaction_envelope(raw: bytes) -> dict:
    """Check one serialized envelope the way Relay's parser would.

    Args:
        raw: Bytes from ``Envelope.serialize()``.

    Returns:
        The parsed transaction payload.

    Raises:
        AssertionError: Any required field is missing, malformed, out of
            order, or over a size limit.
    """
    header_line, rest = raw.split(b"\n", 1)
    header = json.loads(header_line)
    assert HEX32.match(header["event_id"]), header["event_id"]
    _when(header["sent_at"])
    item_header_line, rest = rest.split(b"\n", 1)
    item_header = json.loads(item_header_line)
    assert item_header["type"] == "transaction", item_header
    payload_bytes = rest.rstrip(b"\n")
    if "length" in item_header:
        assert item_header["length"] == len(payload_bytes)
    assert len(payload_bytes) <= MAX_PAYLOAD_BYTES, len(payload_bytes)
    event = json.loads(payload_bytes)

    assert event["type"] == "transaction"
    assert event["event_id"] == header["event_id"]
    assert isinstance(event.get("transaction"), str) and event["transaction"]
    assert "[redacted]" not in event["transaction"]
    start, end = _when(event["start_timestamp"]), _when(event["timestamp"])
    assert start <= end, (start, end)
    assert event["sdk"]["name"], event["sdk"]
    assert all(pkg.get("name") for pkg in event["sdk"].get("packages", []))

    trace = event["contexts"]["trace"]
    assert HEX32.match(trace["trace_id"]), trace["trace_id"]
    assert HEX16.match(trace["span_id"]), trace["span_id"]
    assert trace.get("op")
    dsc = header.get("trace")
    if dsc:
        assert dsc["trace_id"] == trace["trace_id"]

    spans = event.get("spans") or []
    assert len(spans) <= MAX_SPANS
    known = {trace["span_id"]}
    for span in spans:
        assert HEX16.match(span["span_id"]), span["span_id"]
        assert span["span_id"] not in known
        assert span["trace_id"] == trace["trace_id"]
        assert span["parent_span_id"] in known | {s["span_id"] for s in spans}
        assert span.get("op")
        assert len(span.get("description") or "") <= MAX_DESCRIPTION
        s_start, s_end = _when(span["start_timestamp"]), _when(span["timestamp"])
        assert s_start <= s_end
        assert start <= s_start and s_end <= end, (span["description"], start, end)
        assert len(span.get("data") or {}) <= MAX_SPAN_DATA_KEYS
        known.add(span["span_id"])
    return event


def relay_accepts(event: dict) -> None:
    """Run Relay's own normalizer. Raises when Relay would call it invalid."""
    normalizer = StoreNormalizer(
        project_id=1,
        is_renormalize=False,
        enable_trimming=True,
        remove_other=False,
        normalize_user_agent=False,
    )
    out = normalizer.normalize_event(json.loads(json.dumps(event)))
    assert not (out.get("_meta") or {}), out["_meta"]


class _Wire(Transport):
    """Keep the serialized bytes of every transaction envelope."""

    sent: list[bytes] = []

    def capture_envelope(self, envelope) -> None:
        """Serialize exactly what the HTTP transport would post."""
        if any(item.type == "transaction" for item in envelope.items):
            _Wire.sent.append(envelope.serialize())


def _event(**over) -> dict:
    """A realistic transaction event with ids that trip the old scrub."""
    event = {
        "type": "transaction",
        "event_id": "ab1234cdef5678ab9012cdef3456ab78",
        "transaction": "student_state",
        "transaction_info": {"source": "custom"},
        "platform": "python",
        "release": "f31a59dba917013e2f37243e8660097e402b5a1c",
        "environment": "production",
        "start_timestamp": "2026-11-25T19:00:01.123456Z",
        "timestamp": "2026-11-25T19:00:01.223456Z",
        "sdk": {
            "name": "sentry.python.flask",
            "version": "2.77.0",
            "packages": [{"name": "pypi:sentry-sdk", "version": "2.77.0"}],
        },
        "contexts": {
            "runtime": {"name": "CPython", "version": "3.12.0"},
            "os": {"name": "Linux", "version": "6.8.0"},
            "trace": {
                "trace_id": "1234ab5678cd9012ef3456ab7890cd12",
                "span_id": "1234abcd5678ef90",
                "parent_span_id": None,
                "op": "http.server",
                "origin": "auto.http.flask",
            },
        },
        "spans": [
            {
                "trace_id": "1234ab5678cd9012ef3456ab7890cd12",
                "span_id": "9876abcd5432ef10",
                "parent_span_id": "1234abcd5678ef90",
                "op": "db",
                "description": "live.state.auth",
                "start_timestamp": "2026-11-25T19:00:01.130000Z",
                "timestamp": "2026-11-25T19:00:01.140000Z",
                "data": {"server_ms": 3},
            }
        ],
    }
    event.update(over)
    return event


class WireFieldsSurviveScrubTests(unittest.TestCase):
    """Unit checks on the production scrub callbacks."""

    def test_transaction_wire_fields_are_untouched(self) -> None:
        """Timestamps, ids and the SDK descriptor leave the scrub unchanged."""
        before = _event()
        scrubbed = sentry_wire.before_send_transaction(_event())
        for key in ("event_id", "start_timestamp", "timestamp", "release", "type"):
            self.assertEqual(scrubbed[key], before[key], key)
        self.assertEqual(scrubbed["sdk"], before["sdk"])
        self.assertEqual(scrubbed["contexts"]["runtime"], before["contexts"]["runtime"])
        self.assertEqual(scrubbed["contexts"]["os"], before["contexts"]["os"])
        self.assertEqual(scrubbed["contexts"]["os"]["name"], "Linux")
        self.assertEqual(scrubbed["contexts"]["trace"], before["contexts"]["trace"])
        self.assertEqual(scrubbed["spans"], before["spans"])
        validate_transaction_envelope(_envelope_bytes(scrubbed))

    def test_random_ids_never_change(self) -> None:
        """Many random ids: the old scrub changed about half of them."""
        for _ in range(300):
            ids = {
                "event_id": uuid.uuid4().hex,
                "trace_id": uuid.uuid4().hex,
                "span_id": uuid.uuid4().hex[:16],
            }
            event = _event(event_id=ids["event_id"])
            event["contexts"]["trace"].update(trace_id=ids["trace_id"], span_id=ids["span_id"])
            event["spans"][0].update(trace_id=ids["trace_id"], parent_span_id=ids["span_id"])
            scrubbed = sentry_wire.before_send_transaction(event)
            self.assertEqual(scrubbed["event_id"], ids["event_id"])
            self.assertEqual(scrubbed["contexts"]["trace"]["trace_id"], ids["trace_id"])
            self.assertEqual(scrubbed["contexts"]["trace"]["span_id"], ids["span_id"])
            self.assertEqual(scrubbed["spans"][0]["trace_id"], ids["trace_id"])

    def test_error_event_keeps_its_id_and_timestamp(self) -> None:
        """``before_send`` has the same wire fields, so it must keep them."""
        event = {
            "event_id": "ab1234cdef5678ab9012cdef3456ab78",
            "timestamp": "2026-11-25T19:00:01.223456Z",
            "level": "error",
            "message": "lookup failed",
            "sdk": {"name": "sentry.python.flask", "version": "2.77.0"},
        }
        scrubbed = sentry_wire.before_send(dict(event), {})
        self.assertEqual(scrubbed["event_id"], event["event_id"])
        self.assertEqual(scrubbed["timestamp"], event["timestamp"])
        self.assertEqual(scrubbed["sdk"]["name"], "sentry.python.flask")

    def test_log_keeps_trace_ids(self) -> None:
        """Log items carry trace_id and span_id too."""
        log = {
            "timestamp": 1795645819.8,
            "trace_id": "1234ab5678cd9012ef3456ab7890cd12",
            "span_id": "1234abcd5678ef90",
            "level": "warning",
            "body": "slow",
            "attributes": {},
        }
        scrubbed = sentry_wire.before_send_log(dict(log))
        self.assertEqual(scrubbed["trace_id"], log["trace_id"])
        self.assertEqual(scrubbed["span_id"], log["span_id"])

    def test_privacy_scrub_still_applies_to_everything_else(self) -> None:
        """Only wire fields are exempt: names, PINs, codes and emails go."""
        event = _event(
            extra={"name": STUDENT_NAME, "note": "student Zorblaxine typed PIN 4321"},
            request={
                "url": "https://alc.example/api/student/state?v=visit-token",
                "data": {"name": STUDENT_NAME},
                "headers": {"Cookie": "x=y"},
            },
        )
        event["contexts"]["user_label"] = "join code Q7Z9K"
        event["spans"][0]["description"] = "lookup for teacher@gmail.com code Q7Z9K"
        scrubbed = sentry_wire.before_send_transaction(event)
        text = json.dumps(scrubbed)
        for secret in (STUDENT_NAME, "4321", "Q7Z9K", "teacher@gmail.com", "visit-token"):
            self.assertNotIn(secret, text, secret)
        self.assertNotIn("data", scrubbed["request"])
        self.assertNotIn("name", scrubbed["extra"])

    def test_nested_wire_key_names_are_still_scrubbed(self) -> None:
        """Secrets under timestamp/release/type outside wire paths are scrubbed.

        Those key names are exempt only at the event root, on
        ``contexts.trace`` ids, at each span root, and at the log record
        root. A nested copy in extra, tags, span data or breadcrumb data
        still goes through the text scrub. Root wire fields stay intact.
        """
        secret = f"student {STUDENT_NAME} typed PIN 4321 join code Q7Z9K"
        nested = {"timestamp": secret, "release": secret, "type": secret}
        event = _event(
            extra=dict(nested),
            tags=dict(nested),
            breadcrumbs={"values": [{"category": "http", "data": dict(nested)}]},
        )
        event["spans"][0]["data"] = {"server_ms": 3, **nested}
        root = {
            "event_id": event["event_id"],
            "timestamp": event["timestamp"],
            "start_timestamp": event["start_timestamp"],
            "type": event["type"],
            "platform": event["platform"],
            "release": event["release"],
            "trace": dict(event["contexts"]["trace"]),
            "os": dict(event["contexts"]["os"]),
            "span_id": event["spans"][0]["span_id"],
            "span_trace_id": event["spans"][0]["trace_id"],
            "span_parent": event["spans"][0]["parent_span_id"],
            "span_start": event["spans"][0]["start_timestamp"],
            "span_end": event["spans"][0]["timestamp"],
        }
        scrubbed = sentry_wire.before_send_transaction(event)
        for key in ("event_id", "timestamp", "start_timestamp", "type", "platform", "release"):
            self.assertEqual(scrubbed[key], root[key], key)
        self.assertEqual(scrubbed["contexts"]["trace"], root["trace"])
        self.assertEqual(scrubbed["contexts"]["os"], root["os"])
        span = scrubbed["spans"][0]
        self.assertEqual(span["span_id"], root["span_id"])
        self.assertEqual(span["trace_id"], root["span_trace_id"])
        self.assertEqual(span["parent_span_id"], root["span_parent"])
        self.assertEqual(span["start_timestamp"], root["span_start"])
        self.assertEqual(span["timestamp"], root["span_end"])

        locations = [
            scrubbed["extra"],
            scrubbed["tags"],
            span["data"],
            scrubbed["breadcrumbs"]["values"][0]["data"],
        ]
        for blob in locations:
            for key in ("timestamp", "release", "type"):
                self.assertIn(key, blob, key)
                value = blob[key]
                self.assertIsInstance(value, str)
                self.assertNotIn(STUDENT_NAME, value, (key, value))
                self.assertNotIn("4321", value, (key, value))
                self.assertNotIn("Q7Z9K", value, (key, value))
                self.assertIn("[redacted]", value, (key, value))
        self.assertEqual(span["data"]["server_ms"], 3)


def _envelope_bytes(event: dict) -> bytes:
    """Serialize ``event`` through the SDK's own envelope class."""
    from sentry_sdk.envelope import Envelope

    envelope = Envelope(
        headers={"event_id": event["event_id"], "sent_at": "2026-11-25T19:00:02.000000Z"}
    )
    envelope.add_transaction(event)
    return envelope.serialize()


class StudentStateEnvelopeTests(unittest.TestCase):
    """A sampled ``student_state`` transaction from the real app is valid."""

    def setUp(self) -> None:
        """App with a live class and a joined student, SDK sampling at 1."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.staff = self.app.test_client()
        self.student = self.app.test_client()
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCF3M"
        )
        self.staff.get("/auth/google?portal=staff")
        self.staff.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.staff.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )
        created = self.staff.post(
            "/api/staff/classes",
            json={
                "offering_id": offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": [STUDENT_NAME],
            },
        )
        class_id = created.get_json()["class"]["id"]
        live = self.school.start_live_class_session(class_id, int(teacher["id"]))
        self.student.post(
            "/auth/student-code",
            data={"code": live["session_code"], "name": STUDENT_NAME},
        )
        self.student.post("/student/mood", data={"mood": "good"})
        self.student.post("/student/character", data={"character": "fox"})
        _Wire.sent = []
        self._env = mock.patch.dict(
            os.environ, {sentry_wire.LIVE_TRACE_RATE_ENV: "1"}
        )
        self._env.start()
        options = sentry_wire.sentry_init_options("https://key@o0.ingest.sentry.io/1")
        options["transport"] = _Wire
        sentry_sdk.init(**options)

    def tearDown(self) -> None:
        """Stop Sentry and close the db."""
        sentry_sdk.flush()
        sentry_sdk.get_client().close()
        sentry_sdk.init()
        self._env.stop()
        self.school.close()
        self.tmp.cleanup()

    def test_sampled_student_state_serializes_into_a_valid_envelope(self) -> None:
        """Full and unchanged polls both produce envelopes Relay accepts."""
        body = self.student.get("/api/student/state").get_json()
        for _ in range(25):
            self.student.get(
                "/api/student/state",
                query_string={"seq": body["state_seq"], "stamp": body["stamp"]},
            )
        sentry_sdk.flush()
        self.assertGreaterEqual(len(_Wire.sent), 20)
        names_seen: set[str] = set()
        for raw in _Wire.sent:
            self.assertNotIn(STUDENT_NAME.encode(), raw)
            self.assertNotIn(b"[redacted]", raw)
            event = validate_transaction_envelope(raw)
            if event["transaction"] != "student_state":
                continue
            names_seen.update(str(s["description"]) for s in event["spans"])
            if StoreNormalizer is not None:
                relay_accepts(event)
        for wanted in ("live.state.auth", "live.state.serialize"):
            self.assertIn(wanted, names_seen)


@unittest.skipIf(StoreNormalizer is None, "sentry-relay is not installed")
class RelayNormalizerTests(unittest.TestCase):
    """Relay's own normalizer: the fixed scrub passes, the old output fails."""

    def test_scrubbed_transaction_is_valid(self) -> None:
        """The production scrub output is accepted by Relay."""
        relay_accepts(sentry_wire.before_send_transaction(_event()))

    def test_mangled_timestamp_is_what_relay_rejected(self) -> None:
        """Pin the failure: a ``[redacted]`` timestamp is an invalid transaction."""
        bad = _event(
            start_timestamp="2026-11-[redacted]:00:01.123456Z",
            timestamp="2026-11-[redacted]:00:01.223456Z",
        )
        with self.assertRaises(Exception) as ctx:
            relay_accepts(bad)
        self.assertIn("InvalidTransaction", type(ctx.exception).__name__)


if __name__ == "__main__":
    unittest.main()

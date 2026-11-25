#!/usr/bin/env python3
"""MCK-522: live-class timing instrumentation. Measurement only."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
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
from live_news_wire import _public_card, prompt_event  # noqa: E402
from minds_on import MINDS_ON_CHOICES  # noqa: E402
from sentry_wire import (  # noqa: E402
    LIVE_TRACE_RATE_ENV,
    LIVE_TRACE_SAMPLE_RATE,
    is_live_trace_path,
    live_trace_sample_rate,
    live_trace_span,
    local_iso_to_epoch_ms,
    sentry_browser_context,
    traces_sampler,
)

NODE = shutil.which("node")

STUDENT_NAME = "Zorblaxine"


class RateTests(unittest.TestCase):
    """One env knob scopes the live-class rate."""

    def test_default_and_override(self) -> None:
        """Unset keeps 5%; the env value applies, clamps, and ignores junk."""
        with mock.patch.dict(os.environ, {LIVE_TRACE_RATE_ENV: ""}):
            self.assertEqual(live_trace_sample_rate(), LIVE_TRACE_SAMPLE_RATE)
        with mock.patch.dict(os.environ, {LIVE_TRACE_RATE_ENV: "0.25"}):
            self.assertEqual(live_trace_sample_rate(), 0.25)
        with mock.patch.dict(os.environ, {LIVE_TRACE_RATE_ENV: "7"}):
            self.assertEqual(live_trace_sample_rate(), 1.0)
        for junk in ("fast", "-1", "nan"):
            with mock.patch.dict(os.environ, {LIVE_TRACE_RATE_ENV: junk}):
                self.assertEqual(live_trace_sample_rate(), LIVE_TRACE_SAMPLE_RATE, junk)

    def test_only_live_paths_follow_the_knob(self) -> None:
        """State, submit, publish, and images follow it. Pages and polls do not."""
        with mock.patch.dict(os.environ, {LIVE_TRACE_RATE_ENV: "0.25"}):
            for path in (
                "/api/student/state",
                "/api/live-sessions/4/state",
                "/api/live-sessions/4/items/9/publish",
                "/api/student/live-prompt/response",
                "/api/classes/3/live-question-images/abc.png",
            ):
                self.assertTrue(is_live_trace_path(path), path)
                self.assertEqual(
                    traces_sampler({"wsgi_environ": {"PATH_INFO": path}}), 0.25, path
                )
            self.assertEqual(
                traces_sampler({"wsgi_environ": {"PATH_INFO": "/health"}}), 0.0
            )
            self.assertEqual(
                traces_sampler({"wsgi_environ": {"PATH_INFO": "/api/student/heartbeat"}}),
                0.0,
            )
            self.assertEqual(
                traces_sampler({"wsgi_environ": {"PATH_INFO": "/student/home"}}),
                sentry_wire.DEFAULT_TRACES_SAMPLE_RATE,
            )

    def test_browser_meta_carries_the_rate(self) -> None:
        """The page gets the same rate for the browser timing spans."""
        with mock.patch.dict(os.environ, {LIVE_TRACE_RATE_ENV: "0.25"}):
            self.assertEqual(sentry_browser_context()["sentry_live_rate"], "0.25")
        with mock.patch.dict(os.environ, {LIVE_TRACE_RATE_ENV: ""}):
            self.assertEqual(sentry_browser_context()["sentry_live_rate"], "0.05")

    def test_local_iso_to_epoch_ms(self) -> None:
        """Stored naive local stamps convert; junk gives None."""
        self.assertIsNone(local_iso_to_epoch_ms(None))
        self.assertIsNone(local_iso_to_epoch_ms("soon"))
        first = local_iso_to_epoch_ms("2026-11-25T14:00:00")
        second = local_iso_to_epoch_ms("2026-11-25T14:00:05")
        assert first is not None and second is not None
        self.assertEqual(second - first, 5000)


class SpanOverheadTests(unittest.TestCase):
    """An unsampled or unconfigured request pays almost nothing."""

    def test_no_transaction_creates_no_span(self) -> None:
        """With no sampled parent, start_span is never called."""
        with mock.patch.object(sentry_sdk, "start_span") as start:
            with live_trace_span("live.state", "live.state.build.card") as span:
                self.assertIsNone(span)
            start.assert_not_called()

    def test_span_errors_still_propagate(self) -> None:
        """An exception inside the block is not swallowed."""
        with self.assertRaises(ValueError):
            with live_trace_span("live.state", "live.state.auth"):
                raise ValueError("boom")


class _Capture(Transport):
    """Collect transactions in memory."""

    events: list[dict] = []

    def capture_envelope(self, envelope) -> None:
        """Keep every transaction payload."""
        for item in envelope.items:
            if item.type == "transaction":
                _Capture.events.append(item.payload.json)


class LiveTimingServerTests(unittest.TestCase):
    """Spans, server stamps, and privacy on a real class."""

    def setUp(self) -> None:
        """Isolated app, live session, joined student, Sentry at rate 1."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite", data_dir=root, testing=True
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.staff = self.app.test_client()
        self.student = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
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
        self.class_id = created.get_json()["class"]["id"]
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])
        self.session_code = live["session_code"]
        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": STUDENT_NAME},
        )
        self.student.post("/student/mood", data={"mood": "good"})
        self.student.post("/student/character", data={"character": "fox"})
        _Capture.events = []
        self._env = mock.patch.dict(os.environ, {LIVE_TRACE_RATE_ENV: "1"})
        self._env.start()
        options = sentry_wire.sentry_init_options("https://key@o0.ingest.sentry.io/1")
        options["transport"] = _Capture
        sentry_sdk.init(**options)

    def tearDown(self) -> None:
        """Stop Sentry, close the db."""
        sentry_sdk.flush()
        sentry_sdk.get_client().close()
        sentry_sdk.init()
        self._env.stop()
        self.school.close()
        self.tmp.cleanup()

    def _transactions(self, name: str) -> list[dict]:
        sentry_sdk.flush()
        return [e for e in _Capture.events if e.get("transaction") == name]

    @staticmethod
    def _span_names(event: dict) -> set[str]:
        return {str(span.get("description")) for span in event.get("spans") or []}

    def _publish_minds_on(self) -> tuple[int, int]:
        """Publish the Minds-On item over HTTP; return (item id, prompt id)."""
        item = next(
            row
            for row in self.school.ensure_live_session_items(self.session_id)
            if str(row.get("item_id") or "") == "minds_on"
        )
        res = self.staff.post(
            f"/api/live-sessions/{self.session_id}/items/{int(item['id'])}/publish",
            json={"publish_mode": "individual"},
        )
        self.assertEqual(res.status_code, 200, res.get_json())
        data = res.get_json()
        prompt = data["item"]["prompt_id"]
        return int(item["id"]), int(prompt)

    def test_student_state_has_sub_spans_and_server_clock(self) -> None:
        """Full and unchanged polls trace their parts and carry server_ms."""
        full = self.student.get("/api/student/state")
        body = full.get_json()
        self.assertIsInstance(body["server_ms"], int)
        unchanged = self.student.get(
            "/api/student/state",
            query_string={"seq": body["state_seq"], "stamp": body["stamp"]},
        ).get_json()
        self.assertTrue(unchanged.get("unchanged"), unchanged)
        self.assertIsInstance(unchanged["server_ms"], int)
        events = self._transactions("student_state")
        self.assertEqual(len(events), 2)
        full_event = next(
            e for e in events if e["contexts"]["trace"]["data"].get("state.kind") == "full"
        )
        names = self._span_names(full_event)
        for wanted in (
            "live.state.auth",
            "live.state.unchanged_check",
            "live.state.stamp",
            "live.state.build",
            "live.state.build.base",
            "live.state.build.card",
            "live.state.build.media_teacher",
            "live.state.build.groups_meta",
            "live.state.build.engagement",
            "live.state.serialize",
        ):
            self.assertIn(wanted, names)
        self.assertGreater(full_event["contexts"]["trace"]["data"]["state.bytes"], 100)
        short = next(
            e
            for e in events
            if e["contexts"]["trace"]["data"].get("state.kind") == "unchanged"
        )
        self.assertIn("live.state.unchanged_check", self._span_names(short))
        self.assertNotIn("live.state.build", self._span_names(short))

    def test_tally_patch_span_and_landing_time(self) -> None:
        """A thin patch traces its read and says when the newest answer landed."""
        item_id, prompt_id = self._publish_minds_on()
        with self.school._lock:
            self.school.conn.execute(
                "UPDATE live_session_items SET show_live_results = 1 WHERE id = ?",
                (item_id,),
            )
            self.school.conn.commit()
        submit = self.student.post(
            "/api/student/live-prompt/response",
            json={"prompt_id": prompt_id, "response": {"choice": MINDS_ON_CHOICES[0]}},
        )
        self.assertEqual(submit.status_code, 200, submit.get_json())
        state = self.student.get("/api/student/state").get_json()
        patched = self.student.get(
            "/api/student/state",
            query_string={
                "seq": state["state_seq"],
                "stamp": state["stamp"],
                "tally_touch": "0",
            },
        ).get_json()
        self.assertTrue(patched.get("unchanged"), patched)
        self.assertTrue(patched.get("active_questions"), patched)
        self.assertIsInstance(patched["tally_at_ms"], int)
        self.assertGreater(patched["tally_at_ms"], 1_700_000_000_000)
        self.assertEqual(
            patched["tally_at_ms"], self.school._live_tally_latest_ms([item_id])
        )
        patch_events = [
            e
            for e in self._transactions("student_state")
            if e["contexts"]["trace"]["data"].get("state.kind") == "patch"
        ]
        self.assertEqual(len(patch_events), 1)
        names = self._span_names(patch_events[0])
        self.assertIn("live.state.tally_stamp", names)
        self.assertIn("live.state.tally_patch", names)

    def test_publish_card_carries_the_stamp_and_spans(self) -> None:
        """Teacher publish traces its stages; the wire card keeps published_ms."""
        item_id, _prompt_id = self._publish_minds_on()
        events = self._transactions("api_publish_live_item")
        self.assertEqual(len(events), 1)
        names = self._span_names(events[0])
        for wanted in (
            "live.publish.write",
            "live.publish.card",
            "live.publish.emit",
            "live.publish.respond",
        ):
            self.assertIn(wanted, names)
        data = events[0]["contexts"]["trace"]["data"]
        self.assertIn("publish.write_ms", data)
        self.assertTrue(data["publish.has_card"])
        from live_news_wire import log_for

        tape = log_for(self.school.data_dir).since(self.session_id, 0)
        cards = [e["card"] for e in tape if e.get("type") == "prompt" and e.get("card")]
        self.assertTrue(cards, tape)
        self.assertGreater(cards[-1]["published_ms"], 1_700_000_000_000)
        self.assertEqual(cards[-1]["item_id"], item_id)
        state = self.student.get("/api/student/state").get_json()
        row = next(r for r in state["active_questions"] if int(r["id"]) == item_id)
        self.assertIsInstance(row["published_ms"], int)
        self.assertLess(abs(row["published_ms"] - cards[-1]["published_ms"]), 5000)

    def test_wire_card_clean_keeps_only_a_number(self) -> None:
        """The postcard cleaner keeps a sane stamp and drops junk."""
        card = {"item_id": 3, "kind": "mc", "text": "Pick", "options": ["a", "b"]}
        self.assertEqual(
            _public_card({**card, "published_ms": 1795000000123})["published_ms"],
            1795000000123,
        )
        self.assertNotIn("published_ms", _public_card({**card, "published_ms": "x"}))
        self.assertNotIn("published_ms", _public_card({**card, "published_ms": -5}))
        self.assertNotIn("published_ms", _public_card({**card, "published_ms": 10**15}))
        self.assertEqual(prompt_event(1, 2, None)["type"], "prompt")

    def test_submit_spans(self) -> None:
        """The individual submit traces facing, write, tally, and emit."""
        _item_id, prompt_id = self._publish_minds_on()
        res = self.student.post(
            "/api/student/live-prompt/response",
            json={"prompt_id": prompt_id, "response": {"choice": MINDS_ON_CHOICES[0]}},
        )
        self.assertEqual(res.status_code, 200, res.get_json())
        events = self._transactions("api_student_live_prompt_response")
        self.assertEqual(len(events), 1)
        names = self._span_names(events[0])
        for wanted in (
            "live.submit.facing",
            "live.submit.write",
            "live.submit.tally",
            "live.submit.emit",
        ):
            self.assertIn(wanted, names)
        data = events[0]["contexts"]["trace"]["data"]
        self.assertEqual(data["submit.path"], "individual")
        self.assertFalse(data["submit.replay"])

    def test_image_route_spans(self) -> None:
        """A live question image traces access and send with status and bytes."""
        folder = Path(self.school.data_dir) / "live-question-images" / str(self.class_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        res = self.student.get(
            f"/api/classes/{self.class_id}/live-question-images/pic.png"
        )
        self.assertEqual(res.status_code, 200)
        events = self._transactions("serve_live_question_image")
        self.assertEqual(len(events), 1)
        names = self._span_names(events[0])
        self.assertIn("live.image.access", names)
        self.assertIn("live.image.send", names)
        data = events[0]["contexts"]["trace"]["data"]
        self.assertEqual(data["image.status"], 200)
        self.assertEqual(data["image.bytes"], 72)

    def test_no_student_secrets_in_any_transaction(self) -> None:
        """No name, join code, PIN, or answer reaches a transaction."""
        _item_id, prompt_id = self._publish_minds_on()
        self.student.get("/api/student/state")
        self.student.post(
            "/api/student/live-prompt/response",
            json={"prompt_id": prompt_id, "response": {"choice": MINDS_ON_CHOICES[0]}},
        )
        state = self.student.get("/api/student/state").get_json()
        self.student.get(
            "/api/student/state",
            query_string={"seq": state["state_seq"], "stamp": state["stamp"], "v": "tok123"},
        )
        sentry_sdk.flush()
        self.assertTrue(_Capture.events)
        blob = json.dumps(_Capture.events)
        for secret in (
            STUDENT_NAME,
            self.session_code,
            MINDS_ON_CHOICES[0],
            "tok123",
            "teacher@gmail.com",
        ):
            self.assertNotIn(secret, blob, secret)


@unittest.skipUnless(NODE, "node is not installed")
class LiveTimingBrowserTests(unittest.TestCase):
    """live_timing.js rules and the browser sampler."""

    def test_live_timing_rules(self) -> None:
        """Clock offset, push/state arrival, images, timeout, tally, privacy."""
        done = subprocess.run(
            [NODE, str(LMS_DIR / "static" / "live_timing.test.mjs")],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(done.returncode, 0, done.stderr or done.stdout)
        self.assertIn("ok", done.stdout)

    def test_browser_sampler_uses_the_live_rate_for_live_spans_only(self) -> None:
        """live.client.* uses the meta rate; other traces keep their rules."""
        script = r"""
const fs = require("fs");
const vm = require("vm");
const src = fs.readFileSync(process.argv[1], "utf8");
function meta(content) { return { content, getAttribute() { return this.content; } }; }
function run(rate) {
  const metas = {
    'meta[name="lloves-sentry-dsn"]': meta("https://public@o0.ingest.sentry.io/1"),
    'meta[name="lloves-sentry-environment"]': meta("tip"),
  };
  if (rate !== null) metas['meta[name="lloves-sentry-live-rate"]'] = meta(rate);
  const sandbox = {
    console,
    location: { origin: "https://alc.example" },
    document: { querySelector(sel) { return metas[sel] || null; } },
    fetch: function () { return Promise.resolve({ status: 200, ok: true }); },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.Sentry = {
    init(opts) { sandbox.__opts = opts; },
    browserTracingIntegration() { return { name: "BrowserTracing" }; },
    setUser() {}, setTags() {}, captureMessage() {}, captureException() {}, addBreadcrumb() {},
  };
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox);
  return sandbox.__opts;
}
function assert(cond, msg) { if (!cond) throw new Error(msg); }
let opts = run("0.25");
assert(typeof opts.tracesSampler === "function", "tracesSampler set");
assert(opts.tracesSampleRate === undefined, "no fixed rate beside the sampler");
assert(opts.tracesSampler({ name: "live.client.publish_to_paint" }) === 0.25, "live rate");
assert(opts.tracesSampler({ name: "live.client.tally_update", parentSampled: false }) === 0.25, "live ignores parent");
assert(opts.tracesSampler({ name: "/student/home" }) === 0.05, "page rate");
assert(opts.tracesSampler({ name: "/student/home", parentSampled: true }) === 1, "follows sampled parent");
assert(opts.tracesSampler({ name: "/student/home", parentSampled: false }) === 0, "follows unsampled parent");
assert(opts.tracesSampler(undefined) === 0.05, "no context");
opts = run(null);
assert(opts.tracesSampler({ name: "live.client.x" }) === 0.05, "missing meta keeps 5%");
opts = run("");
assert(opts.tracesSampler({ name: "live.client.x" }) === 0.05, "blank meta keeps 5%");
opts = run("junk");
assert(opts.tracesSampler({ name: "live.client.x" }) === 0.05, "bad meta keeps 5%");
opts = run("9");
assert(opts.tracesSampler({ name: "live.client.x" }) === 1, "clamps to 1");
console.log("ok");
"""
        done = subprocess.run(
            [NODE, "-e", script, str(LMS_DIR / "static" / "sentry-live.js")],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(done.returncode, 0, done.stderr or done.stdout)
        self.assertIn("ok", done.stdout)


if __name__ == "__main__":
    unittest.main()

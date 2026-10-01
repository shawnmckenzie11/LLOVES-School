#!/usr/bin/env python3
"""LiveNewsWire SSE postcards: emit, resume, scope, and the shed."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from live_news_wire import (  # noqa: E402
    STREAMS_PER_WORKER,
    LiveNewsLog,
    NewsAudience,
    _acquire_stream,
    emit_session_news,
    event_visible,
    events_for_teacher_patch,
    news_db_path,
    reset_logs,
    reset_stream_budget,
    response_landed_events,
)


class LiveNewsLogTests(unittest.TestCase):
    """Tape, scope, and postcard shape without a live class."""

    def setUp(self) -> None:
        """Fresh sqlite tape and a clear stream budget."""
        self.tmp = tempfile.TemporaryDirectory()
        reset_stream_budget()
        reset_logs()
        self.log = LiveNewsLog(news_db_path(self.tmp.name))

    def tearDown(self) -> None:
        """Close the tape so the temp dir can go."""
        self.log.close()
        reset_logs()
        reset_stream_budget()
        self.tmp.cleanup()

    def test_postcard_drops_stem_and_resumes_after_last_id(self) -> None:
        """Stored events are thin, and Last-Event-ID skips what the tab has."""
        first = self.log.append(
            7,
            [
                {
                    "type": "state_seq",
                    "state_seq": 1,
                    "stem": "<p>full question</p>",
                    "roster": [{"name": "Aspen"}],
                }
            ],
        )
        self.assertEqual(len(first), 1)
        self.assertNotIn("stem", first[0])
        self.assertNotIn("roster", first[0])
        second = self.log.append(
            7,
            [{"type": "stage", "stage": "teams", "state_seq": 2}],
        )
        replay = self.log.since(7, int(first[0]["id"]))
        self.assertEqual([row["type"] for row in replay], ["stage"])
        self.assertEqual(replay[0]["id"], second[0]["id"])
        self.assertLess(len(json.dumps(replay[0])), 400)

    def test_student_scope_hides_teacher_board_and_other_self_taps(self) -> None:
        """Staff sees flag_work. A student sees only their own self tap."""
        events = response_landed_events(state_seq=4, student_id=9, count=3)
        events.append({"type": "flag_work", "kind": "publish", "state_seq": 4})
        stored = self.log.append(3, events)
        staff = NewsAudience("staff")
        aspen = NewsAudience("student", student_id=9)
        birch = NewsAudience("student", student_id=10)
        self.assertEqual(
            [row["type"] for row in stored if event_visible(row, staff)],
            ["response_landed", "response_landed", "flag_work"],
        )
        visible_aspen = [row for row in stored if event_visible(row, aspen)]
        self.assertEqual(
            [row.get("scope") for row in visible_aspen],
            ["class", "self"],
        )
        visible_birch = [row for row in stored if event_visible(row, birch)]
        self.assertEqual([row.get("scope") for row in visible_birch], ["class"])
        self.assertNotIn("flag_work", [row["type"] for row in visible_birch])

    def test_teacher_patch_emits_stage_without_a_suitcase(self) -> None:
        """A stage advance is state_seq plus stage, not a roster dump."""
        events = events_for_teacher_patch(
            {"stage": "join", "state_seq": 0, "prompt_ref": "join", "page_id": ""},
            {
                "stage": "teams",
                "state_seq": 1,
                "prompt_ref": "spark",
                "page_id": "welcome",
                "round_flags": {},
            },
        )
        kinds = [row["type"] for row in events]
        self.assertIn("state_seq", kinds)
        self.assertIn("stage", kinds)
        self.assertIn("prompt", kinds)
        self.assertIn("slide", kinds)
        blob = json.dumps(events)
        self.assertNotIn("stem", blob)
        self.assertNotIn("<", blob)


class LiveNewsRouteTests(unittest.TestCase):
    """Staff advance emits, the stream resumes, and a full worker sheds busy."""

    def setUp(self) -> None:
        """One teacher, one live session, empty wire tape."""
        reset_stream_budget()
        reset_logs()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.client.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Aspen", "Birch"],
            },
        )
        self.class_id = created.get_json()["class"]["id"]
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])

    def tearDown(self) -> None:
        """Close db, tape, and temp dir."""
        reset_logs()
        reset_stream_budget()
        self.school.close()
        self.tmp.cleanup()

    def test_advance_emits_stage_and_stream_replays_it(self) -> None:
        """POST advance writes a stage postcard the SSE route can replay."""
        nxt = self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"advance": "next"},
        )
        self.assertEqual(nxt.status_code, 200, nxt.get_json())
        self.assertEqual(nxt.get_json()["teacher_state"]["stage"], "teams")
        log = LiveNewsLog(news_db_path(self.tmp.name))
        try:
            rows = log.since(self.session_id, 0)
        finally:
            log.close()
        kinds = [row["type"] for row in rows]
        self.assertIn("stage", kinds)
        self.assertIn("state_seq", kinds)
        stage = next(row for row in rows if row["type"] == "stage")
        self.assertEqual(stage["stage"], "teams")
        self.assertNotIn("stem", stage)
        anonymous = self.app.test_client()
        denied = anonymous.get(f"/api/live/session/{self.session_id}/events")
        self.assertEqual(denied.status_code, 401)
        stream = self.client.get(f"/api/live/session/{self.session_id}/events")
        self.assertEqual(stream.status_code, 200)
        self.assertIn("text/event-stream", stream.content_type)
        body = stream.get_data(as_text=True)
        self.assertIn("event: hello", body)
        self.assertIn("event: stage", body)
        self.assertIn('"stage":"teams"', body)
        self.assertNotIn("location.reload", body)
        self.assertIn("event: ping", body)
        cursor = int(stage["id"])
        again = self.client.get(
            f"/api/live/session/{self.session_id}/events",
            headers={"Last-Event-ID": str(cursor)},
        )
        resumed = again.get_data(as_text=True)
        self.assertIn("event: hello", resumed)
        self.assertNotIn('"stage":"teams"', resumed)

    def test_student_join_taps_staff_wire_so_class_list_updates(self) -> None:
        """A join writes a staff-only postcard; staff /state then lists them.

        Since the staff tab polls ``/state`` only every 20s, the join tap is
        what makes the ClassList show a new student live.
        """
        code = str(self.school.get_live_session(self.session_id)["session_code"])
        student = self.app.test_client()
        joined = student.post(
            "/auth/student-code", json={"code": code, "name": "Aspen"}
        )
        self.assertLess(joined.status_code, 400, joined.get_data(as_text=True))
        log = LiveNewsLog(news_db_path(self.tmp.name))
        try:
            rows = log.since(self.session_id, 0)
        finally:
            log.close()
        taps = [
            row
            for row in rows
            if row["type"] == "flag_work" and row.get("kind") == "join"
        ]
        self.assertEqual(len(taps), 1, rows)
        tap = taps[0]
        self.assertTrue(event_visible(tap, NewsAudience("staff")))
        self.assertFalse(
            event_visible(tap, NewsAudience("student", student_id=1))
        )
        self.assertNotIn("codename", tap)
        stream = self.client.get(f"/api/live/session/{self.session_id}/events")
        body = stream.get_data(as_text=True)
        self.assertIn("event: flag_work", body)
        self.assertIn('"kind":"join"', body)
        state = self.client.get(
            f"/api/live-sessions/{self.session_id}/state?light=1"
        ).get_json()
        present = [
            row.get("codename")
            for row in state.get("attendees") or []
            if not row.get("left_at")
        ]
        self.assertIn("Aspen", present)

    def test_shed_is_busy_not_a_reload(self) -> None:
        """A worker at the stream cap returns one busy postcard."""
        for _ in range(STREAMS_PER_WORKER):
            self.assertTrue(_acquire_stream())
        shed = self.client.get(f"/api/live/session/{self.session_id}/events")
        self.assertEqual(shed.status_code, 200)
        body = shed.get_data(as_text=True)
        self.assertIn("event: busy", body)
        self.assertIn('"retry":true', body)
        self.assertIn("retry: 20000", body)
        self.assertNotIn("location.reload", body)
        payload = json.loads(body.split("data: ", 1)[1].split("\n", 1)[0])
        self.assertEqual(payload, {"type": "busy", "retry": True})

    def test_emit_drops_ping_and_unknown_types(self) -> None:
        """Ping and fat unknown types never land on the resume tape."""
        stored = emit_session_news(
            self.school,
            self.session_id,
            [{"type": "ping"}, {"type": "not-a-type", "stem": "nope"}],
        )
        self.assertEqual(stored, [])


class NoStreamPollTests(unittest.TestCase):
    """A tab the wire did not stream to must not wait the 20s safety net."""

    def test_shed_or_reconnecting_tab_polls_at_the_pre_wire_pace(self) -> None:
        """Open stream: 20s. Shed, connecting, or no wire: 4s.

        The worker cap is 4 streams (16 per machine), so a class of 25
        always has tabs that get ``busy`` and hear no publish postcard.
        """
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        wire = (LMS_DIR / "static" / "live_news_wire.js").resolve().as_uri()
        script = f"""
import {{ FALLBACK_POLL_MS, NO_STREAM_POLL_MS, fallbackPollMs }} from {json.dumps(wire)};
const out = {{
  fallback: FALLBACK_POLL_MS,
  noStream: NO_STREAM_POLL_MS,
  open: fallbackPollMs({{ hasStream: () => true }}),
  shed: fallbackPollMs({{ hasStream: () => false }}),
  none: fallbackPollMs(null),
}};
console.log(JSON.stringify(out));
"""
        done = subprocess.run(
            [node, "--input-type=module", "-e", script],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        got = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual(got["open"], got["fallback"])
        self.assertEqual(got["shed"], got["noStream"])
        self.assertEqual(got["none"], got["noStream"])
        self.assertLessEqual(got["noStream"], 4000)
        self.assertGreaterEqual(got["fallback"], 15000)

    def test_staff_and_student_polls_use_the_stream_aware_pace(self) -> None:
        """Both live tabs schedule their fallback through ``fallbackPollMs``."""
        static = LMS_DIR / "static"
        staff = (static / "staff_ap.js").read_text(encoding="utf-8")
        student = (static / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn("return fallbackPollMs(staffNewsWire);", staff)
        schedule = student.split("function scheduleStudentPoll()")[1].split(
            "function "
        )[0]
        self.assertIn("fallbackPollMs(studentNewsWire)", schedule)
        self.assertNotIn(": STUDENT_POLL_BASE_MS;", schedule)

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

    def test_join_tap_light_state_ticks_the_cached_class_list_row(self) -> None:
        """MCK-119: the ClassList row for a joiner flips present on a light poll.

        The staff tab caches ``class_list`` from a full ``/state``. The join
        tap only light-fetches, and light ``/state`` has no ``class_list``.
        The cached ``present: false`` must be overlaid from ``attendees``,
        or the joiner stays unticked (hidden under Hide Absent) until a
        ``state_seq`` bump forces a full snapshot.
        """
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        full = self.client.get(f"/api/live-sessions/{self.session_id}/state")
        cached = full.get_json()["class_list"]
        self.assertEqual(
            {row["codename"]: row["present"] for row in cached},
            {"Aspen": False, "Birch": False},
        )
        code = str(self.school.get_live_session(self.session_id)["session_code"])
        joined = self.app.test_client().post(
            "/auth/student-code", json={"code": code, "name": "Aspen"}
        )
        self.assertLess(joined.status_code, 400, joined.get_data(as_text=True))
        light = self.client.get(
            f"/api/live-sessions/{self.session_id}/state?light=1"
        ).get_json()
        self.assertNotIn("class_list", light)
        self.assertEqual(light["state_seq"], full.get_json()["state_seq"])
        got = _run_class_list_overlay(node, cached, light["attendees"])
        self.assertEqual(
            {row["codename"]: row["present"] for row in got},
            {"Aspen": True, "Birch": False},
        )

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

    def test_student_poll_slows_after_end_and_jitters(self) -> None:
        """MCK-88 M1 + L1: celebrate/ended slows the poll; healthy delays jitter.

        A celebrate-End reply is 200 ``{celebrate: true, status: "waiting"}``,
        not 409, so the tab must read the body to know the class is over.
        """
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        wire = (LMS_DIR / "static" / "live_news_wire.js").resolve().as_uri()
        script = f"""
import {{
  ENDED_POLL_MS, FALLBACK_POLL_MS, NO_STREAM_POLL_MS,
  studentFallbackPollMs, studentSessionOver,
}} from {json.dumps(wire)};
const shed = {{ hasStream: () => false }};
const open = {{ hasStream: () => true }};
const samples = [];
for (let i = 0; i < 200; i += 1) samples.push(studentFallbackPollMs(shed, false));
const out = {{
  ended: ENDED_POLL_MS,
  fallback: FALLBACK_POLL_MS,
  noStream: NO_STREAM_POLL_MS,
  shedLow: studentFallbackPollMs(shed, false, 0),
  shedHigh: studentFallbackPollMs(shed, false, 1),
  shedMid: studentFallbackPollMs(shed, false, 0.5),
  openMid: studentFallbackPollMs(open, false, 0.5),
  overLow: studentFallbackPollMs(shed, true, 0),
  overOpenMid: studentFallbackPollMs(open, true, 0.5),
  distinct: new Set(samples).size,
  sampleMin: Math.min(...samples),
  sampleMax: Math.max(...samples),
  celebrate: studentSessionOver({{ celebrate: true, status: "waiting" }}),
  endedBody: studentSessionOver({{ status: "ended", celebrate: false }}),
  active: studentSessionOver({{ status: "active", celebrate: false }}),
  waiting: studentSessionOver({{ status: "waiting" }}),
  unchanged: studentSessionOver({{ ok: true, unchanged: true, state_seq: 3 }}),
  none: studentSessionOver(null),
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
        # L1: the 4 s shed poll is jittered upward only, 4 s to 4.8 s, so it
        # never runs faster than the 4 s floor.
        self.assertEqual(got["shedLow"], got["noStream"])
        self.assertEqual(got["shedMid"], round(got["noStream"] * 1.1))
        self.assertEqual(got["shedHigh"], round(got["noStream"] * 1.2))
        self.assertGreater(got["distinct"], 20)
        self.assertGreaterEqual(got["sampleMin"], got["noStream"])
        self.assertLessEqual(got["sampleMax"], round(got["noStream"] * 1.2))
        self.assertEqual(got["openMid"], round(got["fallback"] * 1.1))
        # M1: once over, even a shed tab waits ~30 s, never the 4 s pace.
        self.assertGreaterEqual(got["ended"], got["fallback"])
        self.assertEqual(got["overOpenMid"], round(got["ended"] * 1.1))
        self.assertGreater(got["overLow"], got["noStream"] * 5)
        self.assertTrue(got["celebrate"])
        self.assertTrue(got["endedBody"])
        self.assertFalse(got["active"])
        self.assertFalse(got["waiting"])
        self.assertFalse(got["unchanged"])
        self.assertFalse(got["none"])

    def test_healthy_student_poll_never_beats_its_base(self) -> None:
        """MCK-88: jitter never takes the healthy poll under 4 s / 20 s / 30 s.

        Ops wave 9e22188 saw 4 s-band gaps down to 2.88 s (p5 3.3 s) with
        #186's ±20% jitter. Every healthy delay must be base to base +20%.
        """
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        wire = (LMS_DIR / "static" / "live_news_wire.js").resolve().as_uri()
        script = f"""
import {{
  ENDED_POLL_MS, FALLBACK_POLL_MS, NO_STREAM_POLL_MS, studentFallbackPollMs,
}} from {json.dumps(wire)};
const shed = {{ hasStream: () => false }};
const open = {{ hasStream: () => true }};
const bands = {{
  shed: [shed, false, NO_STREAM_POLL_MS],
  open: [open, false, FALLBACK_POLL_MS],
  over: [shed, true, ENDED_POLL_MS],
}};
const out = {{}};
for (const [name, [wire, over, base]] of Object.entries(bands)) {{
  const samples = [];
  for (let i = 0; i < 5000; i += 1) samples.push(studentFallbackPollMs(wire, over));
  for (const r of [0, -1, 1, 2, undefined]) samples.push(studentFallbackPollMs(wire, over, r));
  out[name] = {{ base, min: Math.min(...samples), max: Math.max(...samples), distinct: new Set(samples).size }};
}}
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
        self.assertEqual(got["shed"]["base"], 4000)
        for name, band in got.items():
            self.assertGreaterEqual(band["min"], band["base"], name)
            self.assertLessEqual(band["max"], round(band["base"] * 1.2), name)
            self.assertGreater(band["distinct"], 20, name)

    def test_student_tick_records_session_over_from_full_body(self) -> None:
        """The flag is set from each full body, after the ``unchanged`` early return."""
        student = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        tick = student.split("async function tick()")[1].split(
            'document.getElementById("live-response")'
        )[0]
        unchanged_at = tick.index("if (data.unchanged)")
        flag_at = tick.index("studentSessionIsOver = studentSessionOver(data);")
        self.assertLess(unchanged_at, flag_at)
        self.assertLess(flag_at, tick.index("paintCelebrate(data)"))

    def test_staff_and_student_polls_use_the_stream_aware_pace(self) -> None:
        """Both live tabs schedule their fallback through ``fallbackPollMs``."""
        static = LMS_DIR / "static"
        staff = (static / "staff_ap.js").read_text(encoding="utf-8")
        student = (static / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn("return fallbackPollMs(staffNewsWire);", staff)
        schedule = student.split("function scheduleStudentPoll()")[1].split(
            "function "
        )[0]
        self.assertIn(
            "studentFallbackPollMs(studentNewsWire, studentSessionIsOver)", schedule
        )
        wire = (static / "live_news_wire.js").read_text(encoding="utf-8")
        helper = wire.split("export function studentFallbackPollMs(")[1]
        self.assertIn("fallbackPollMs(wire)", helper.split("\n}")[0])
        self.assertNotIn(": STUDENT_POLL_BASE_MS;", schedule)


def _run_class_list_overlay(
    node: str, rows: list, attendees: list
) -> list:
    """Run ``overlayClassListPresence`` under node and return its rows.

    Args:
        node: Path to the node binary.
        rows: Cached ``class_list`` rows.
        attendees: ``attendees`` from a ``/state`` body.
    """
    module = (LMS_DIR / "static" / "class_list_presence.js").resolve().as_uri()
    script = f"""
import {{ overlayClassListPresence }} from {json.dumps(module)};
const rows = {json.dumps(rows)};
const attendees = {json.dumps(attendees)};
console.log(JSON.stringify(overlayClassListPresence(rows, attendees)));
"""
    done = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if done.returncode != 0:
        raise AssertionError(done.stderr)
    return json.loads(done.stdout.strip().splitlines()[-1])


class ClassListLightPresenceTests(unittest.TestCase):
    """MCK-119: light polls keep ClassList ticks in step with attendees."""

    def setUp(self) -> None:
        """Skip without node; the overlay is a browser module."""
        self.node = shutil.which("node")
        if self.node is None:
            self.skipTest("node is not installed")

    def test_overlay_ticks_joiner_unticks_leaver_and_skips_guests(self) -> None:
        """Join ticks, leave unticks, never-joined stays absent, guests skip."""
        cached = [
            {"student_id": 1, "codename": "Aspen", "present": False, "joined": False},
            {"student_id": 2, "codename": "Birch", "present": True, "joined": True},
            {"student_id": 3, "codename": "Cedar", "present": False, "joined": False},
            {"student_id": 4, "codename": "Dogwood", "present": False, "joined": True,
             "left_at": "2026-10-02T09:20:00"},
        ]
        attendees = [
            {"student_id": 1, "codename": "Aspen", "left_at": None},
            {"student_id": 2, "codename": "Birch", "left_at": "2026-10-02T09:30:00"},
            {"student_id": 4, "codename": "Dogwood", "left_at": "2026-10-02T09:20:00"},
            {"student_id": 4, "codename": "Dogwood", "left_at": None},
            {"student_id": None, "codename": "Guest", "unmatched": 1, "left_at": None},
        ]
        got = {
            row["codename"]: row
            for row in _run_class_list_overlay(self.node, cached, attendees)
        }
        self.assertEqual(len(got), 4)
        self.assertTrue(got["Aspen"]["present"])
        self.assertTrue(got["Aspen"]["joined"])
        self.assertFalse(got["Birch"]["present"])
        self.assertEqual(got["Birch"]["left_at"], "2026-10-02T09:30:00")
        self.assertFalse(got["Cedar"]["present"])
        self.assertFalse(got["Cedar"]["joined"])
        self.assertTrue(got["Dogwood"]["present"])
        self.assertIsNone(got["Dogwood"]["left_at"])

    def test_staff_poll_overlays_presence_before_the_classlist_repaint(self) -> None:
        """``pollLiveSessionAttendees`` syncs the cache before painting ticks."""
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn(
            'import { overlayClassListPresence } from "/static/class_list_presence.js";',
            js,
        )
        body = js.split("async function pollLiveSessionAttendees(")[1].split(
            "\nasync function "
        )[0]
        overlay = body.find(
            "lastClassListFull = overlayClassListPresence(lastClassListFull, rows);"
        )
        repaint = body.find("await applySessionPresentTicks(")
        self.assertGreater(overlay, 0)
        self.assertGreater(repaint, overlay)

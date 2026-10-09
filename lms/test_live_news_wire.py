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

    def test_guest_join_does_not_tick_the_roster_row_with_its_attendee_id(
        self,
    ) -> None:
        """MCK-119 MED-1: a guest's attendee PK must not tick roster id = PK.

        On a fresh session the first attendee row is id 1 and the first
        roster student is id 1. A guest joining first must leave that
        roster row unticked after the overlay.
        """
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        allowed = self.client.post(
            f"/api/live-sessions/{self.session_id}/guests",
            json={"allow_unmatched_guests": True},
        )
        self.assertEqual(allowed.status_code, 200, allowed.get_json())
        cached = self.client.get(
            f"/api/live-sessions/{self.session_id}/state"
        ).get_json()["class_list"]
        code = str(self.school.get_live_session(self.session_id)["session_code"])
        joined = self.app.test_client().post(
            "/auth/student-code", json={"code": code, "name": "Quill"}
        )
        self.assertLess(joined.status_code, 400, joined.get_data(as_text=True))
        light = self.client.get(
            f"/api/live-sessions/{self.session_id}/state?light=1"
        ).get_json()
        guests = [row for row in light["attendees"] if row.get("student_id") is None]
        self.assertEqual(len(guests), 1, light["attendees"])
        roster_ids = {int(row["student_id"]) for row in cached}
        # The collision this test exists for: attendee PK is a roster id.
        self.assertIn(int(guests[0]["id"]), roster_ids)
        got = _run_class_list_overlay(node, cached, light["attendees"])
        self.assertEqual([row for row in got if row["present"]], [])

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


# Real staff_ap.js ClassList functions the guest-first harness runs.
CLASS_LIST_FUNCTIONS = (
    "projectedClassListStudents",
    "classListVisibleStudents",
    "classListRosterOrder",
    "appendAttendanceStudentRow",
    "renderAttendanceList",
    "selectedPresent",
    "updateAttCount",
)


def _staff_function_source(js: str, name: str) -> str:
    """Return the full source of a top-level ``function name(...) {...}``.

    Args:
        js: Whole staff_ap.js text.
        name: Function name to extract.
    """
    start = js.index(f"\nfunction {name}(") + 1
    open_at = js.index("{", js.index(")", start))
    depth = 0
    for index in range(open_at, len(js)):
        char = js[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return js[start : index + 1]
    raise AssertionError(f"unbalanced {name}")


CLASS_LIST_HARNESS = r"""
import vm from "node:vm";
import fs from "node:fs";
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const { overlayClassListPresence } = await import(input.module);
class El {
  constructor(id) { this.id = id || ""; this.children = []; this.className = ""; this.dataset = {}; this.attrs = {}; this.style = { setProperty() {} }; this.innerHTML = ""; this.textContent = ""; this.hidden = false; }
  get classList() { const el = this; return { add(c) { el.className = `${el.className} ${c}`.trim(); } }; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  appendChild(child) { this.children.push(child); return child; }
  set innerHTML(v) { this._html = v; if (v === "") this.children = []; }
  get innerHTML() { return this._html || ""; }
}
const els = Object.fromEntries(["ap-att-list", "ap-att-cols", "ap-att-count", "ap-att-summary", "live-header-present"].map((id) => [id, new El(id)]));
const list = els["ap-att-list"];
const ctx = {
  Number, String, Boolean, Map, Set, Object, Array, JSON,
  $: (id) => els[id] || null,
  document: {
    createElement: () => new El(),
    querySelectorAll: (sel) => (sel === "#ap-att-list .ap-att-row.is-present" ? list.children.filter((c) => /\bis-present\b/.test(c.className)) : []),
  },
  teacherState: { hide_absent: input.hideAbsent },
  overlayState: { students: input.roster.map((r) => ({ id: r.student_id, codename: r.codename })) },
  lastClassListFull: input.roster,
  lastClassList: input.roster,
  sessionPresentIds: new Set(),
  sessionGuests: [],
  sessionLateIds: new Set(),
  sessionCareerTotals: {},
  sessionGamePoints: {},
  sortStudents: (rows) => rows,
  nameSort: null,
  classListGroupsByTeam: () => false,
  previewRosterTeams: () => [],
  assignedRosterTeams: () => [],
  studentTeamColor: () => "",
  classListPts: (n) => String(n),
  displayName: (s) => s.codename,
  escapeHtml: (v) => String(v),
  paintDivisionMeter: () => {},
  paintScoreboardPreviewTotals: () => {},
};
vm.createContext(ctx);
vm.runInContext(input.src, ctx);
// One light /state body, applied the way pollLiveSessionAttendees does.
const rows = input.attendees;
ctx.lastClassListFull = overlayClassListPresence(ctx.lastClassListFull, rows);
ctx.lastClassList = ctx.lastClassListFull;
const present = rows.filter((row) => !row.left_at);
ctx.sessionGuests = present
  .filter((row) => Boolean(row.unmatched) || row.student_id == null)
  .map((row) => ({ participant_uuid: String(row.participant_uuid || ""), codename: row.codename, unmatched: true }));
ctx.sessionPresentIds = new Set(present.map((row) => Number(row.student_id)).filter((n) => Number.isFinite(n) && n > 0));
ctx.renderAttendanceList();
console.log(JSON.stringify({
  ticked: list.children.filter((c) => /\bis-present\b/.test(c.className) && !/is-guest/.test(c.className)).map((c) => Number(c.dataset.studentId)),
  listed: list.children.filter((c) => !/is-guest/.test(c.className)).map((c) => Number(c.dataset.studentId)),
  header: els["live-header-present"].textContent,
}));
"""


def _run_class_list_render(
    node: str, roster: list, attendees: list, *, hide_absent: bool
) -> dict:
    """Overlay one light poll and render the ClassList with staff_ap.js code.

    Args:
        node: Path to the node binary.
        roster: Cached ``class_list`` rows from the last full ``/state``.
        attendees: ``attendees`` from a light ``/state`` body.
        hide_absent: Teacher's Hide Absent switch.

    Returns:
        ``{ticked, listed, header}``: roster ids shown ticked, roster ids
        listed, and the "Present N" header text.
    """
    js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
    payload = {
        "module": (LMS_DIR / "static" / "class_list_presence.js").resolve().as_uri(),
        "src": "\n".join(_staff_function_source(js, name) for name in CLASS_LIST_FUNCTIONS),
        "roster": roster,
        "attendees": attendees,
        "hideAbsent": hide_absent,
    }
    done = subprocess.run(
        [node, "--input-type=module", "-e", CLASS_LIST_HARNESS],
        input=json.dumps(payload),
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
        # Real /state attendees carry their own row key as ``id``. The
        # guest's attendee id 3 equals Cedar's roster id (gate MED-1).
        attendees = [
            {"id": 11, "student_id": 1, "codename": "Aspen", "left_at": None},
            {"id": 12, "student_id": 2, "codename": "Birch", "left_at": "2026-10-02T09:30:00"},
            {"id": 13, "student_id": 4, "codename": "Dogwood", "left_at": "2026-10-02T09:20:00"},
            {"id": 14, "student_id": 4, "codename": "Dogwood", "left_at": None},
            {"id": 3, "student_id": None, "codename": "Guest", "unmatched": 1, "left_at": None},
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

    def test_guest_joining_first_never_ticks_the_roster_student_with_its_id(self) -> None:
        """Gate MED-1: a guest's attendee id is not a roster id.

        The guest is the session's first attendee, so its row key is 1,
        the same as Alder's roster id. Alder never joined: he must stay
        unticked (and hidden under Hide Absent), and "Present N" must not
        go up: it matches the same guest with an id no roster row has.
        Fails on 331e411, where ``student_id ?? id`` ticked Alder and the
        header read one higher than that.
        """
        roster = [
            {"id": 1, "student_id": 1, "codename": "Alder", "present": False, "joined": False},
            {"id": 2, "student_id": 2, "codename": "Birch", "present": False, "joined": False},
            {"id": 3, "student_id": 3, "codename": "Clover", "present": False, "joined": False},
        ]
        guest = {
            "id": 1,
            "student_id": None,
            "unmatched": 1,
            "codename": "Visitor Quill",
            "participant_uuid": "g-1",
            "left_at": None,
        }
        overlay = {
            row["codename"]: row
            for row in _run_class_list_overlay(self.node, roster, [guest])
        }
        self.assertFalse(overlay["Alder"]["present"])
        self.assertFalse(overlay["Alder"]["joined"])
        for hide_absent in (False, True):
            with self.subTest(hide_absent=hide_absent):
                got = _run_class_list_render(
                    self.node, roster, [guest], hide_absent=hide_absent
                )
                # Same guest, but its row key matches no roster row.
                control = _run_class_list_render(
                    self.node, roster, [{**guest, "id": 999}], hide_absent=hide_absent
                )
                self.assertEqual(got["ticked"], [])
                self.assertEqual(control["ticked"], [])
                self.assertEqual(got["header"], control["header"])
                if hide_absent:
                    self.assertEqual(got["listed"], [])
        # A real roster joiner next to the guest: only Birch ticks.
        birch = {"id": 2, "student_id": 2, "codename": "Birch", "left_at": None}
        got = _run_class_list_render(self.node, roster, [guest, birch], hide_absent=False)
        self.assertEqual(got["ticked"], [2])
        self.assertEqual(got["header"], "Present 2")

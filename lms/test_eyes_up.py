"""MCK-26: one-tap "Eyes up" pause for every student screen.

Session-global ``teacher_state.eyes_up`` rides the existing teacher-state
POST and the student ``/api/student/state`` poll. It does not change the
page, and C2/C3 ``text_ride`` freeze behaviour stays separate.
"""

from __future__ import annotations

import os
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
from live_news_wire import events_for_teacher_patch  # noqa: E402
from live_teacher_state import (  # noqa: E402
    apply_teacher_state_update,
    default_teacher_state,
    public_teacher_state,
)


class EyesUpStateTests(unittest.TestCase):
    """Pure teacher-state rules."""

    def test_default_off(self) -> None:
        self.assertIs(default_teacher_state()["eyes_up"], False)
        self.assertIs(public_teacher_state({})["eyes_up"], False)
        self.assertIs(public_teacher_state(None)["eyes_up"], False)

    def test_stored_value_round_trips(self) -> None:
        self.assertIs(public_teacher_state({"eyes_up": True})["eyes_up"], True)
        self.assertIs(public_teacher_state({"eyes_up": "true"})["eyes_up"], True)
        self.assertIs(public_teacher_state({"eyes_up": "junk"})["eyes_up"], False)

    def test_toggle_keeps_page_and_bumps_seq(self) -> None:
        start = apply_teacher_state_update(
            None, stage="play", page_id="welcome", live_slot="C2",
            text_ride={"frozen": True},
        )
        on = apply_teacher_state_update(start, eyes_up=True)
        self.assertIs(on["eyes_up"], True)
        self.assertEqual(on["state_seq"], start["state_seq"] + 1)
        for key in ("stage", "page_id", "live_slot", "text_ride", "student_view", "prompt_ref"):
            self.assertEqual(on[key], start[key], key)
        off = apply_teacher_state_update(on, eyes_up=False)
        self.assertIs(off["eyes_up"], False)
        self.assertEqual(off["page_id"], "welcome")
        self.assertTrue(off["text_ride"]["frozen"])

    def test_other_patches_leave_it_alone(self) -> None:
        on = apply_teacher_state_update(None, eyes_up=True)
        moved = apply_teacher_state_update(on, advance="next")
        self.assertIs(moved["eyes_up"], True)
        ride = apply_teacher_state_update(
            apply_teacher_state_update(moved, live_slot="C3"), text_ride={"frozen": True}
        )
        self.assertIs(ride["eyes_up"], True)
        self.assertTrue(ride["text_ride"]["frozen"])

    def test_freeze_does_not_imply_eyes_up(self) -> None:
        frozen = apply_teacher_state_update(
            apply_teacher_state_update(None, live_slot="C2"), text_ride={"frozen": True}
        )
        self.assertIs(frozen["eyes_up"], False)

    def test_bad_value_rejected(self) -> None:
        with self.assertRaises(ValueError):
            apply_teacher_state_update(None, eyes_up="maybe")

    def test_news_wire_sends_existing_state_seq_postcard(self) -> None:
        before = apply_teacher_state_update(None, stage="play")
        after = apply_teacher_state_update(before, eyes_up=True)
        events = events_for_teacher_patch(before, after)
        self.assertEqual(
            events, [{"type": "state_seq", "state_seq": after["state_seq"]}]
        )


class EyesUpHttpTests(unittest.TestCase):
    """Teacher POST → student /api/student/state, refresh, release."""

    def setUp(self) -> None:
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
        self.offering = self.school.assign_course(
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
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple", "Cedar"],
            },
        )
        self.assertEqual(created.status_code, 200)
        self.class_id = int(created.get_json()["class"]["id"])
        run = self.staff.post(
            f"/staff/class/{self.class_id}/run-live", follow_redirects=False
        )
        self.assertEqual(run.status_code, 302)
        live = self.school.get_active_live_session_for_class(self.class_id)
        assert live is not None
        self.session_code = str(live["session_code"])
        self.live_session_id = int(live["id"])
        self._join(self.student, "Maple")

    def tearDown(self) -> None:
        self.school.close()
        self.tmp.cleanup()

    def _join(self, client, name: str) -> None:
        client.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": name},
            follow_redirects=False,
        )
        client.post("/student/mood", data={"mood": "good"})
        client.post("/student/character", data={"character": "fox"})

    def _patch(self, body: dict):
        return self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/teacher-state", json=body
        )

    def _student_state(self, client=None, **params) -> dict:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        res = (client or self.student).get(
            "/api/student/state" + (f"?{query}" if query else "")
        )
        self.assertEqual(res.status_code, 200)
        return res.get_json()

    def test_toggle_reaches_students_and_releases(self) -> None:
        idle = self._student_state()
        self.assertIs(idle["teacher_state"]["eyes_up"], False)
        page_before = idle["teacher_state"]["page_id"]
        stage_before = idle["teacher_state"]["stage"]

        on = self._patch({"eyes_up": True})
        self.assertEqual(on.status_code, 200, on.get_json())
        self.assertIs(on.get_json()["teacher_state"]["eyes_up"], True)

        # The student's next poll with its old seq/stamp is not "unchanged".
        polled = self._student_state(seq=idle["state_seq"], stamp=idle["stamp"])
        self.assertNotIn("unchanged", polled)
        self.assertIs(polled["teacher_state"]["eyes_up"], True)
        self.assertEqual(polled["teacher_state"]["page_id"], page_before)
        self.assertEqual(polled["teacher_state"]["stage"], stage_before)

        # A steady poll while on short-circuits as before (no extra load).
        steady = self._student_state(seq=polled["state_seq"], stamp=polled["stamp"])
        self.assertTrue(steady.get("unchanged"), steady)

        off = self._patch({"eyes_up": False})
        self.assertEqual(off.status_code, 200)
        released = self._student_state(seq=polled["state_seq"], stamp=polled["stamp"])
        self.assertIs(released["teacher_state"]["eyes_up"], False)
        self.assertEqual(released["teacher_state"]["page_id"], page_before)

    def test_survives_student_refresh_and_late_join(self) -> None:
        self.assertEqual(self._patch({"eyes_up": True}).status_code, 200)
        # Refresh: reload home, first poll carries no seq/stamp.
        home = self.student.get("/student/home")
        self.assertEqual(home.status_code, 200)
        self.assertIn("/static/student-portal.js", home.get_data(as_text=True))
        fresh = self._student_state()
        self.assertIs(fresh["teacher_state"]["eyes_up"], True)
        # A student who joins while paused is paused too.
        late = self.app.test_client()
        self._join(late, "Cedar")
        self.assertIs(self._student_state(late)["teacher_state"]["eyes_up"], True)
        # Staff GET (teacher refresh) shows the toggle on.
        staff_get = self.staff.get(
            f"/api/live-sessions/{self.live_session_id}/teacher-state"
        ).get_json()
        self.assertIs(staff_get["teacher_state"]["eyes_up"], True)

    def test_bad_value_is_400_and_state_unchanged(self) -> None:
        res = self._patch({"eyes_up": "maybe"})
        self.assertEqual(res.status_code, 400)
        self.assertIs(self._student_state()["teacher_state"]["eyes_up"], False)

    def test_student_cannot_toggle(self) -> None:
        res = self.student.post(
            f"/api/live-sessions/{self.live_session_id}/teacher-state",
            json={"eyes_up": True},
        )
        self.assertIn(res.status_code, (302, 401, 403))
        self.assertIs(self._student_state()["teacher_state"]["eyes_up"], False)

    def test_c2_freeze_unchanged_and_independent(self) -> None:
        c2 = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"challenge": "C2"},
        )
        self.assertEqual(c2.status_code, 200, c2.get_json())
        frozen = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"frozen": True},
        )
        self.assertEqual(frozen.status_code, 200, frozen.get_json())
        self.assertTrue(frozen.get_json()["text_ride"]["frozen"])
        self.assertEqual(frozen.get_json()["teacher_state"]["cue_id"], "cue.freeze")
        self.assertIs(frozen.get_json()["teacher_state"]["eyes_up"], False)

        self.assertEqual(self._patch({"eyes_up": True}).status_code, 200)
        on = self._student_state()["teacher_state"]
        self.assertTrue(on["text_ride"]["frozen"])
        self.assertEqual(on["cue_id"], "cue.freeze")
        self.assertEqual(on["live_slot"], "C2")

        cons = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"cons_item": "C2-CONS-1"},
        )
        self.assertEqual(cons.status_code, 200, cons.get_json())
        self.assertIs(cons.get_json()["teacher_state"]["eyes_up"], True)

        self.assertEqual(self._patch({"eyes_up": False}).status_code, 200)
        off = self._student_state()["teacher_state"]
        self.assertTrue(off["text_ride"]["frozen"])
        self.assertEqual(off["text_ride"]["cons_item"], "C2-CONS-1")

    def test_end_while_paused_releases_students(self) -> None:
        """HIGH-1: End (celebration) clears eyes_up; Release after End is a no-op 200."""
        self.assertEqual(self._patch({"eyes_up": True}).status_code, 200)
        before = self._student_state()
        self.assertIs(before["teacher_state"]["eyes_up"], True)
        end = self.staff.post(
            f"/staff/class/{self.class_id}/end-live", follow_redirects=False
        )
        self.assertIn(end.status_code, (200, 302), end.get_data(as_text=True)[:200])
        row = self.school.get_live_session(self.live_session_id)
        self.assertEqual(row["status"], "ended")
        after = self._student_state(seq=before["state_seq"], stamp=before["stamp"])
        self.assertTrue(after.get("celebrate"), after)
        self.assertIs(after["teacher_state"]["eyes_up"], False)
        # Reload on the celebration screen: still released.
        self.assertIs(self._student_state()["teacher_state"]["eyes_up"], False)
        stored = self.school.live_session_teacher_state_payload(self.live_session_id)
        self.assertIs(stored["eyes_up"], False)
        # Release after End is harmless and idempotent (no 400).
        for _ in range(2):
            late = self._patch({"eyes_up": False})
            self.assertEqual(late.status_code, 200, late.get_json())
            self.assertTrue(late.get_json()["ended"])
            self.assertIs(late.get_json()["teacher_state"]["eyes_up"], False)
        # Turning it ON after End is still refused.
        self.assertEqual(self._patch({"eyes_up": True}).status_code, 400)
        # Mixed bodies after End keep the old 400.
        self.assertEqual(
            self._patch({"eyes_up": False, "stage": "play"}).status_code, 400
        )

    def test_end_masks_a_stale_paused_blob(self) -> None:
        """An ended session never pauses students even if the blob says so."""
        self.assertEqual(self._patch({"eyes_up": True}).status_code, 200)
        self.school.close_live_class_for_celebration(self.class_id)
        import json as _json

        with self.school._lock:
            raw = self.school.conn.execute(
                "SELECT teacher_state_json FROM live_class_sessions WHERE id = ?",
                (self.live_session_id,),
            ).fetchone()["teacher_state_json"]
            blob = _json.loads(raw)
            blob["eyes_up"] = True
            self.school.conn.execute(
                "UPDATE live_class_sessions SET teacher_state_json = ? WHERE id = ?",
                (_json.dumps(blob), self.live_session_id),
            )
            self.school.conn.commit()
        self.assertIs(self._student_state()["teacher_state"]["eyes_up"], False)

    def test_end_live_class_session_releases(self) -> None:
        self.assertEqual(self._patch({"eyes_up": True}).status_code, 200)
        seq = self.school.live_session_teacher_state_payload(self.live_session_id)[
            "state_seq"
        ]
        self.school.end_live_class_session(self.live_session_id)
        state = self.school.live_session_teacher_state_payload(self.live_session_id)
        self.assertIs(state["eyes_up"], False)
        self.assertEqual(state["state_seq"], seq + 1)
        # Idempotent: a second end does not bump again.
        self.school.end_live_class_session(self.live_session_id)
        again = self.school.live_session_teacher_state_payload(self.live_session_id)
        self.assertEqual(again["state_seq"], seq + 1)

    def test_quit_while_paused_releases_students(self) -> None:
        """Quit wipes the session; students leave, and a late Release is a 200."""
        self.assertEqual(self._patch({"eyes_up": True}).status_code, 200)
        before = self._student_state()
        quit_ = self.staff.post(
            f"/staff/class/{self.class_id}/quit-live", follow_redirects=False
        )
        self.assertIn(quit_.status_code, (200, 302))
        after = self.student.get(
            f"/api/student/state?seq={before['state_seq']}&stamp={before['stamp']}"
        ).get_json()
        self.assertFalse((after.get("teacher_state") or {}).get("eyes_up"), after)
        self.assertNotEqual(after.get("status"), "live")
        late = self._patch({"eyes_up": False})
        self.assertEqual(late.status_code, 200, late.get_json())
        self.assertTrue(late.get_json()["ended"])
        # Non-release writes to a wiped session still 404.
        self.assertEqual(self._patch({"eyes_up": True}).status_code, 404)

    def test_new_session_starts_released(self) -> None:
        self.assertEqual(self._patch({"eyes_up": True}).status_code, 200)
        self.school.end_live_class_session(self.live_session_id)
        # Ended payloads are never paused on the client (eyesUpOn), and a
        # new Run Live Class starts from default_teacher_state().
        run = self.staff.post(
            f"/staff/class/{self.class_id}/run-live", follow_redirects=False
        )
        self.assertEqual(run.status_code, 302)
        live = self.school.get_active_live_session_for_class(self.class_id)
        assert live is not None
        self.assertNotEqual(int(live["id"]), self.live_session_id)
        fresh = self.school.live_session_teacher_state_payload(int(live["id"]))
        self.assertIs(fresh["eyes_up"], False)


class EyesUpClientTests(unittest.TestCase):
    """Static wiring plus the node overlay harness."""

    def test_overlay_harness(self) -> None:
        node = LMS_DIR / "static" / "eyes_up.test.mjs"
        result = subprocess.run(
            ["node", str(node)], cwd=str(node.parent),
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("ok", result.stdout)

    def test_student_portal_paints_from_existing_poll(self) -> None:
        js = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn('import { paintEyesUp } from "/static/eyes_up.js";', js)
        tick = js[js.index("async function tick()"):]
        tick = tick[: tick.index("\n}\n")]
        self.assertIn("paintEyesUp(data);", tick)
        overlay = (LMS_DIR / "static" / "eyes_up.js").read_text(encoding="utf-8")
        # No new request: the overlay module never fetches or polls.
        for token in ("fetch(", "XMLHttpRequest", "setInterval(", "EventSource", "WebSocket"):
            self.assertNotIn(token, overlay)
        self.assertIn("// copy: Wonder", overlay)
        # Wonder copy pass: three overlay lines.
        self.assertIn('line: "Look at the board.", // copy: Wonder', overlay)
        self.assertIn('saved: "Your work is saved.", // copy: Wonder', overlay)

    def test_teacher_toggle_markup_and_patch(self) -> None:
        html = (LMS_DIR / "templates" / "staff" / "course.html").read_text(encoding="utf-8")
        self.assertIn('id="live-eyes-up"', html)
        self.assertIn('aria-pressed="false"', html)
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("JSON.stringify({ eyes_up: want })", js)
        self.assertIn("if (res?.ended) {", js)
        self.assertIn("paintEyesUpToggle();", js[js.index("function adoptTeacherState("):])
        # Labels are declared before teacherState so early adopts cannot hit the TDZ.
        self.assertLess(js.index("const EYES_UP_LABEL"), js.index("let teacherState = {"))
        # Wonder copy pass: "Resume students", never "Release students".
        self.assertIn('const EYES_UP_RESUME_LABEL = "Resume students"; // copy: Wonder', js)
        self.assertIn('"Every student screen is paused. Tap to resume."', js)
        self.assertIn("Tap again to resume.", html)
        for text in (js, html):
            self.assertNotIn("Release students", text)
            self.assertNotIn("to release.", text)
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn('.live-header-eyes-up[aria-pressed="true"]', css)


if __name__ == "__main__":
    unittest.main()

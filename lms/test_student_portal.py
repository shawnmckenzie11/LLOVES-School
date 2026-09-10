#!/usr/bin/env python3
"""Student live-class portal: join, mood, character, home, and rank flag."""

from __future__ import annotations

import os
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


class StudentPortalTests(unittest.TestCase):
    """Mood → character → home, plus staff show-rank for student state."""

    def setUp(self) -> None:
        """Isolated app with one rostered class and separate staff/student clients."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
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
                "codenames": ["Maple", "Aspen"],
            },
        )
        self.assertEqual(created.status_code, 200)
        self.class_id = int(created.get_json()["class"]["id"])
        run = self.staff.post(
            f"/staff/class/{self.class_id}/run-live",
            follow_redirects=False,
        )
        self.assertEqual(run.status_code, 302)
        live = self.school.get_active_live_session_for_class(self.class_id)
        self.assertIsNotNone(live)
        assert live is not None
        self.session_code = str(live["session_code"])
        self.live_session_id = int(live["id"])

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def test_join_mood_home_and_show_rank(self) -> None:
        """Join → mood good + Join Class → home; rank hidden until enabled."""
        join = self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.assertEqual(join.status_code, 302)
        self.assertIn("/student/mood", join.headers.get("Location", ""))
        attendees = self.school.list_live_session_attendees(
            self.live_session_id, present_only=True
        )
        self.assertEqual(len(attendees), 1)
        self.assertEqual(attendees[0]["codename"], "Maple")

        mood_page = self.student.get("/student/mood")
        self.assertEqual(mood_page.status_code, 200)
        mood_html = mood_page.get_data(as_text=True)
        self.assertIn("Join Class", mood_html)
        self.assertNotIn("Choose your character", mood_html)
        self.assertNotIn("Optional — pick a face", mood_html)
        self.assertNotIn("mood-label", mood_html)
        self.assertEqual(mood_html.count('name="mood"'), 3)
        self.assertIn('value="good"', mood_html)
        self.assertIn('value="ok"', mood_html)
        self.assertIn('value="low"', mood_html)
        self.assertNotIn('value="tired"', mood_html)
        self.assertNotIn("Energetic", mood_html)
        self.assertIn("/static/mood/good.svg", mood_html)
        self.assertIn("/static/mood/ok.svg", mood_html)
        self.assertIn("/static/mood/low.svg", mood_html)
        self.assertNotIn("😊", mood_html)

        mood = self.student.post("/student/mood", data={"mood": "good"}, follow_redirects=False)
        self.assertEqual(mood.status_code, 302)
        self.assertIn("/student/home", mood.headers.get("Location", ""))
        self.assertNotIn("/student/character", mood.headers.get("Location", ""))

        char = self.student.get("/student/character", follow_redirects=False)
        self.assertEqual(char.status_code, 302)
        self.assertIn("/student/home", char.headers.get("Location", ""))

        home = self.student.get("/student/home")
        self.assertEqual(home.status_code, 200)

        state = self.student.get("/api/student/state")
        self.assertEqual(state.status_code, 200)
        payload = state.get_json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["show_rank"])
        self.assertNotIn("rank", payload.get("me") or {})

        toggle = self.staff.post(
            f"/api/classes/{self.class_id}/show-rank",
            json={"enabled": True},
        )
        self.assertEqual(toggle.status_code, 200)
        self.assertTrue(toggle.get_json().get("show_rank"))

        ranked = self.student.get("/api/student/state").get_json()
        self.assertTrue(ranked["show_rank"])

    def test_home_rejects_ended_session(self) -> None:
        """Ending the live session clears student access on home/state."""
        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.student.post("/student/mood", data={"mood": "good"})
        home_ok = self.student.get("/student/home")
        self.assertEqual(home_ok.status_code, 200)

        attendees = self.school.list_live_session_attendees(self.live_session_id)
        token = attendees[0].get("visit_token")
        self.assertTrue(token)

        self.school.end_live_class_session(self.live_session_id)
        home_gone = self.student.get("/student/home", follow_redirects=False)
        self.assertEqual(home_gone.status_code, 302)
        self.assertIn("/", home_gone.headers.get("Location", ""))
        self.assertNotIn("/student/home", home_gone.headers.get("Location", ""))

        # Fresh client: visit token no longer resumes an ended session.
        other = self.app.test_client()
        visit = other.get(f"/student/s/{token}", follow_redirects=False)
        self.assertEqual(visit.status_code, 302)
        self.assertNotIn("/student/", visit.headers.get("Location", ""))

    def test_visit_token_resumes_home(self) -> None:
        """Opaque /student/s/<token> rebinds cookie and reaches home."""
        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.student.post("/student/mood", data={"mood": "good"})
        attendees = self.school.list_live_session_attendees(self.live_session_id)
        token = str(attendees[0]["visit_token"])

        fresh = self.app.test_client()
        visit = fresh.get(f"/student/s/{token}", follow_redirects=False)
        self.assertEqual(visit.status_code, 302)
        self.assertIn("/student/home", visit.headers.get("Location", ""))
        home = fresh.get("/student/home")
        self.assertEqual(home.status_code, 200)
        self.assertIn("live-response", home.get_data(as_text=True))

    def test_prompt_stub_round_trip(self) -> None:
        """Staff sets MC prompt; student polls and submits a response."""
        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.student.post("/student/mood", data={"mood": "good"})

        set_prompt = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/prompts",
            json={
                "slide_index": 2,
                "kind": "mc",
                "payload": {"prompt": "Pick one", "choices": ["A", "B"]},
            },
        )
        self.assertEqual(set_prompt.status_code, 200, set_prompt.get_json())
        prompt = set_prompt.get_json()["prompt"]
        self.assertEqual(prompt["kind"], "mc")
        self.assertEqual(prompt["slide_index"], 2)

        state = self.student.get("/api/student/state").get_json()
        self.assertIsNotNone(state.get("prompt"))
        self.assertEqual(state["prompt"]["id"], prompt["id"])
        self.assertIsNone(state.get("my_response"))

        submit = self.student.post(
            "/api/student/live-prompt/response",
            json={"prompt_id": prompt["id"], "response": {"choice": "B"}},
        )
        self.assertEqual(submit.status_code, 200, submit.get_json())
        self.assertTrue(submit.get_json().get("ack"))

        again = self.student.get("/api/student/live-prompt").get_json()
        self.assertEqual(again["my_response"]["response"]["choice"], "B")

    def test_pick_preserves_live_session_id(self) -> None:
        """student_pick rebind keeps the live session + visit token keys."""
        from student_portal import bind_student_session

        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        with self.student.session_transaction() as sess:
            live_id = sess.get("student_live_session_id")
            token = sess.get("student_visit_token")
            offering_id = sess.get("student_offering_id")
            code = sess.get("student_live_code")
        self.assertEqual(live_id, self.live_session_id)
        self.assertTrue(token)

        cls = self.school.game.get_class(self.class_id)
        student = self.school.game.find_student_by_codename(self.class_id, "Maple")
        assert student is not None
        offering = self.school.get_offering(int(offering_id))
        # Rebind the way student_pick does after section choice.
        with self.student.session_transaction() as sess:
            bind_student_session(
                sess,
                offering,
                cls,
                student,
                live_session_id=int(live_id),
                session_code=str(code),
                visit_token=str(token),
            )
            self.assertEqual(sess.get("student_live_session_id"), self.live_session_id)
            self.assertEqual(sess.get("student_visit_token"), token)

    def test_multi_tab_visit_token_isolates_identity(self) -> None:
        """Two student joins in one browser: token header keeps each codename."""
        join_maple = self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.assertEqual(join_maple.status_code, 302)
        maple_loc = join_maple.headers.get("Location", "")
        self.assertIn("v=", maple_loc)
        attendees = self.school.list_live_session_attendees(self.live_session_id)
        maple_token = str(
            next(row for row in attendees if row["codename"] == "Maple")["visit_token"]
        )

        join_aspen = self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Aspen"},
            follow_redirects=False,
        )
        self.assertEqual(join_aspen.status_code, 302)
        attendees = self.school.list_live_session_attendees(self.live_session_id)
        aspen_token = str(
            next(row for row in attendees if row["codename"] == "Aspen")["visit_token"]
        )
        self.assertNotEqual(maple_token, aspen_token)

        # Cookie now reflects Aspen; Maple token header must still resolve Maple.
        maple_state = self.student.get(
            "/api/student/state",
            headers={"X-Student-Visit-Token": maple_token},
        )
        self.assertEqual(maple_state.status_code, 200)
        self.assertEqual(maple_state.get_json()["me"]["codename"], "Maple")

        aspen_state = self.student.get(
            "/api/student/state",
            headers={"X-Student-Visit-Token": aspen_token},
        )
        self.assertEqual(aspen_state.status_code, 200)
        self.assertEqual(aspen_state.get_json()["me"]["codename"], "Aspen")

        mood_maple = self.student.post(
            "/student/mood",
            data={"mood": "good", "visit_token": maple_token},
            headers={"X-Student-Visit-Token": maple_token},
            follow_redirects=False,
        )
        self.assertEqual(mood_maple.status_code, 302)
        maple = self.school.game.get_student(
            self.class_id,
            int(
                self.school.game.find_student_by_codename(self.class_id, "Maple")["id"]
            ),
        )
        aspen = self.school.game.get_student(
            self.class_id,
            int(
                self.school.game.find_student_by_codename(self.class_id, "Aspen")["id"]
            ),
        )
        self.assertEqual(maple.get("mood"), "good")
        self.assertIsNone(aspen.get("mood"))

        leave_maple = self.student.post(
            "/api/student/leave",
            json={"visit_token": maple_token},
            headers={"X-Student-Visit-Token": maple_token},
        )
        self.assertEqual(leave_maple.status_code, 204)
        aspen_state_after = self.student.get(
            "/api/student/state",
            headers={"X-Student-Visit-Token": aspen_token},
        )
        self.assertEqual(aspen_state_after.status_code, 200)
        self.assertEqual(aspen_state_after.get_json()["me"]["codename"], "Aspen")

    def test_second_join_same_name_rejected(self) -> None:
        """A name already present in the live class cannot sign in again."""
        first = self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.assertEqual(first.status_code, 302)
        attendees = self.school.list_live_session_attendees(
            self.live_session_id, present_only=True
        )
        self.assertEqual(len(attendees), 1)
        first_token = str(attendees[0]["visit_token"])

        other = self.app.test_client()
        second = other.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.assertEqual(second.status_code, 409)
        self.assertIn("already signed in", second.get_data(as_text=True).lower())
        still = self.school.list_live_session_attendees(
            self.live_session_id, present_only=True
        )
        self.assertEqual(len(still), 1)
        self.assertEqual(str(still[0]["visit_token"]), first_token)

    def test_rejoin_after_leave_allowed(self) -> None:
        """After leaving, the same name may join the live class again."""
        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        first_token = str(
            self.school.list_live_session_attendees(self.live_session_id)[0][
                "visit_token"
            ]
        )
        leave = self.student.post("/api/student/leave")
        self.assertEqual(leave.status_code, 204)
        again = self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.assertEqual(again.status_code, 302)
        second = self.school.list_live_session_attendees(
            self.live_session_id, present_only=True
        )
        self.assertEqual(len(second), 1)
        self.assertEqual(str(second[0]["visit_token"]), first_token)
        self.assertEqual(
            str(second[0]["participant_uuid"]),
            str(
                self.school.list_live_session_attendees(self.live_session_id)[0][
                    "participant_uuid"
                ]
            ),
        )

    def test_join_mints_stable_uuid_and_token(self) -> None:
        """One logical person keeps the same uuid and rejoin token on refresh."""
        from student_portal import REJOIN_COOKIE_NAME

        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        first = self.school.list_live_session_attendees(self.live_session_id)[0]
        uuid1 = str(first["participant_uuid"])
        token1 = str(first["visit_token"])
        self.assertTrue(uuid1)
        self.assertTrue(token1)

        home = self.student.get("/student/home", follow_redirects=False)
        self.assertEqual(home.status_code, 200)
        still = self.school.list_live_session_attendees(
            self.live_session_id, present_only=True
        )
        self.assertEqual(len(still), 1)
        self.assertEqual(str(still[0]["visit_token"]), token1)
        self.assertEqual(str(still[0]["participant_uuid"]), uuid1)

        landing = self.student.get("/", follow_redirects=False)
        self.assertEqual(landing.status_code, 302)
        self.assertIn("/student/home", landing.headers.get("Location", ""))
        self.assertTrue(self.student.get_cookie(REJOIN_COOKIE_NAME))

        again = self.school.list_live_session_attendees(self.live_session_id)
        self.assertEqual(str(again[0]["visit_token"]), token1)
        self.assertEqual(str(again[0]["participant_uuid"]), uuid1)

    def test_rejoin_cookie_cleared_when_session_ends(self) -> None:
        """Ended sessions drop the httpOnly auto-resume cookie."""
        from student_portal import REJOIN_COOKIE_NAME

        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.assertTrue(self.student.get_cookie(REJOIN_COOKIE_NAME))
        self.school.end_live_class_session(self.live_session_id)
        landing = self.student.get("/", follow_redirects=False)
        self.assertEqual(landing.status_code, 200)
        self.assertIsNone(self.student.get_cookie(REJOIN_COOKIE_NAME))

    def test_pagehide_script_does_not_leave(self) -> None:
        """student-live-session.js heartbeats instead of leaving on pagehide."""
        text = (LMS_DIR / "static" / "student-live-session.js").read_text(
            encoding="utf-8"
        )
        self.assertNotIn('addEventListener("pagehide"', text)
        self.assertIn("/api/student/heartbeat", text)
        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.student.get("/student/home")
        present = self.school.list_live_session_attendees(
            self.live_session_id, present_only=True
        )
        self.assertEqual(len(present), 1)

    def test_heartbeat_stale_marks_left(self) -> None:
        """Missed heartbeats set left_at; the same token can still resume."""
        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        with self.school._lock:
            self.school.conn.execute(
                """
                UPDATE live_session_attendees
                SET last_heartbeat_at = '2000-01-01T00:00:00'
                WHERE live_session_id = ?
                """,
                (self.live_session_id,),
            )
            self.school.conn.commit()
        state = self.staff.get(f"/api/live-sessions/{self.live_session_id}/state")
        self.assertEqual(state.status_code, 200)
        payload = state.get_json()
        self.assertEqual(payload["count"], 0)
        self.assertTrue(payload["attendees"][0].get("left_at"))
        beat = self.student.post("/api/student/heartbeat")
        self.assertEqual(beat.status_code, 200)
        present = self.school.list_live_session_attendees(
            self.live_session_id, present_only=True
        )
        self.assertEqual(len(present), 1)

    def test_guest_flag_off_rejects_unmatched(self) -> None:
        """Allow guests defaults off; unmatched names are rejected."""
        other = self.app.test_client()
        rv = other.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "River"},
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 401)
        self.assertEqual(
            len(self.school.list_live_session_attendees(self.live_session_id)),
            0,
        )

    def test_guest_flag_on_mints_unmatched_uuid(self) -> None:
        """Checked Allow guests mints a uuid and flags the overlay row."""
        toggle = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/guests",
            json={"allow_unmatched_guests": True},
        )
        self.assertEqual(toggle.status_code, 200)
        self.assertTrue(toggle.get_json()["allow_unmatched_guests"])
        guest = self.app.test_client()
        rv = guest.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "River Smith"},
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302)
        self.assertIn("/student/home", rv.headers.get("Location", ""))
        rows = self.school.list_live_session_attendees(self.live_session_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["codename"], "River")
        self.assertTrue(int(rows[0]["unmatched"]))
        self.assertTrue(rows[0]["participant_uuid"])
        self.assertIn(rows[0]["student_id"], (None, 0, ""))
        state = self.staff.get(f"/api/live-sessions/{self.live_session_id}/state")
        body = state.get_json()
        attendee = body["attendees"][0]
        self.assertTrue(attendee["unmatched"])
        self.assertEqual(attendee["codename"], "River")
        self.assertNotIn("visit_token", attendee)
        dumped = state.get_data(as_text=True)
        self.assertNotIn(str(rows[0]["visit_token"]), dumped)

    def test_state_api_strips_rejoin_token(self) -> None:
        """Staff overlay state must not leak visit_token / rejoin secrets."""
        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        token = str(
            self.school.list_live_session_attendees(self.live_session_id)[0][
                "visit_token"
            ]
        )
        state = self.staff.get(f"/api/live-sessions/{self.live_session_id}/state")
        payload = state.get_json()
        self.assertNotIn("visit_token", payload.get("session") or {})
        for row in payload["attendees"]:
            self.assertNotIn("visit_token", row)
        self.assertNotIn(token, state.get_data(as_text=True))

    def test_name_collision_picker_without_last_names(self) -> None:
        """Two roster first names collide into a picker of Codenames."""
        with self.school.game._lock:
            self.school.game.conn.execute(
                "UPDATE students SET first_name = 'Alex' WHERE class_id = ?",
                (self.class_id,),
            )
            self.school.game.conn.commit()
        other = self.app.test_client()
        rv = other.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Alex"},
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 409)
        html = rv.get_data(as_text=True)
        self.assertIn("More than one student", html)
        self.assertIn("Maple", html)
        self.assertIn("Aspen", html)
        self.assertNotIn("last_display", html.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)

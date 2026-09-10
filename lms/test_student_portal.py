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

    def _complete_checkin(
        self,
        *,
        mood: str | None = "good",
        character: str = "fox",
        visit_token: str = "",
        headers: dict | None = None,
    ) -> None:
        """Finish mood then avatar so home is reachable."""
        hdrs = dict(headers or {})
        mood_data: dict[str, str] = {"skip": "1"} if not mood else {"mood": mood}
        char_data = {"character": character}
        if visit_token:
            mood_data["visit_token"] = visit_token
            char_data["visit_token"] = visit_token
            hdrs.setdefault("X-Student-Visit-Token", visit_token)
        self.student.post("/student/mood", data=mood_data, headers=hdrs)
        self.student.post("/student/character", data=char_data, headers=hdrs)

    def test_join_mood_home_and_show_rank(self) -> None:
        """Join → mood → avatar → home; rank hidden until enabled."""
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
        self.assertIn("Continue", mood_html)
        self.assertNotIn("Join Class", mood_html)
        self.assertNotIn("Choose your Avatar", mood_html)
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

        before_mood = self.student.get("/student/character", follow_redirects=False)
        self.assertEqual(before_mood.status_code, 302)
        self.assertIn("/student/mood", before_mood.headers.get("Location", ""))

        mood = self.student.post("/student/mood", data={"mood": "good"}, follow_redirects=False)
        self.assertEqual(mood.status_code, 302)
        self.assertIn("/student/character", mood.headers.get("Location", ""))
        self.assertNotIn("/student/home", mood.headers.get("Location", ""))

        char_page = self.student.get("/student/character")
        self.assertEqual(char_page.status_code, 200)
        char_html = char_page.get_data(as_text=True)
        self.assertIn("Choose your Avatar", char_html)
        self.assertNotIn("Pick one, then join class", char_html)
        self.assertIn("Join Class", char_html)
        self.assertIn("Skip", char_html)
        self.assertLess(char_html.find("Skip"), char_html.find("Join Class"))
        self.assertEqual(char_html.count('name="character"'), 6)
        self.assertIn("🦊", char_html)
        self.assertIn("🐼", char_html)
        self.assertIn("🦄", char_html)
        self.assertIn("🐙", char_html)
        self.assertIn("🐲", char_html)
        self.assertIn("🦉", char_html)
        self.assertIn('value="fox"', char_html)
        self.assertIn('value="owl"', char_html)

        home_blocked = self.student.get("/student/home", follow_redirects=False)
        self.assertEqual(home_blocked.status_code, 302)
        self.assertIn("/student/character", home_blocked.headers.get("Location", ""))

        char = self.student.post(
            "/student/character", data={"character": "fox"}, follow_redirects=False
        )
        self.assertEqual(char.status_code, 302)
        self.assertIn("/student/home", char.headers.get("Location", ""))

        home = self.student.get("/student/home")
        self.assertEqual(home.status_code, 200)

        overlay = self.staff.get(f"/api/live-sessions/{self.live_session_id}/state")
        self.assertEqual(overlay.status_code, 200)
        present = [
            row
            for row in overlay.get_json().get("attendees") or []
            if not row.get("left_at")
        ]
        self.assertEqual(len(present), 1)
        self.assertEqual(present[0].get("character"), "fox")
        self.assertEqual(present[0].get("mood"), "good")

        state = self.student.get("/api/student/state")
        self.assertEqual(state.status_code, 200)
        payload = state.get_json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["show_rank"])
        self.assertEqual((payload.get("me") or {}).get("character"), "fox")
        self.assertNotIn("rank", payload.get("me") or {})

        toggle = self.staff.post(
            f"/api/classes/{self.class_id}/show-rank",
            json={"enabled": True},
        )
        self.assertEqual(toggle.status_code, 200)
        self.assertTrue(toggle.get_json().get("show_rank"))

        ranked = self.student.get("/api/student/state").get_json()
        self.assertTrue(ranked["show_rank"])

    def test_skip_mood_still_requires_avatar(self) -> None:
        """Skip on mood continues to avatar; home waits for the pick."""
        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        skipped = self.student.post(
            "/student/mood", data={"skip": "1"}, follow_redirects=False
        )
        self.assertEqual(skipped.status_code, 302)
        self.assertIn("/student/character", skipped.headers.get("Location", ""))
        maple = self.school.game.find_student_by_codename(self.class_id, "Maple")
        assert maple is not None
        self.assertIsNone(self.school.game.get_mood(self.class_id, int(maple["id"])))

        home_blocked = self.student.get("/student/home", follow_redirects=False)
        self.assertEqual(home_blocked.status_code, 302)
        self.assertIn("/student/character", home_blocked.headers.get("Location", ""))

        self.student.post("/student/character", data={"character": "owl"})
        home = self.student.get("/student/home")
        self.assertEqual(home.status_code, 200)
        overlay = self.staff.get(f"/api/live-sessions/{self.live_session_id}/state")
        present = [
            row
            for row in overlay.get_json().get("attendees") or []
            if not row.get("left_at")
        ]
        self.assertEqual(present[0].get("character"), "owl")
        self.assertIsNone(present[0].get("mood"))

    def test_skip_avatar_reaches_home(self) -> None:
        """Skip on avatar joins class without storing an icon."""
        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.student.post("/student/mood", data={"mood": "ok"})
        skipped = self.student.post(
            "/student/character", data={"skip": "1"}, follow_redirects=False
        )
        self.assertEqual(skipped.status_code, 302)
        self.assertIn("/student/home", skipped.headers.get("Location", ""))
        home = self.student.get("/student/home")
        self.assertEqual(home.status_code, 200)
        maple = self.school.game.find_student_by_codename(self.class_id, "Maple")
        assert maple is not None
        self.assertIsNone(
            self.school.game.get_student(self.class_id, int(maple["id"])).get(
                "character_key"
            )
        )
        overlay = self.staff.get(f"/api/live-sessions/{self.live_session_id}/state")
        present = [
            row
            for row in overlay.get_json().get("attendees") or []
            if not row.get("left_at")
        ]
        self.assertIsNone(present[0].get("character"))

    def test_home_rejects_ended_session(self) -> None:
        """Ending the live session clears student access on home/state."""
        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self._complete_checkin()
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
        self._complete_checkin()
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
        self._complete_checkin()

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
        self.assertIn("/student/character", mood_maple.headers.get("Location", ""))
        self.student.post(
            "/student/character",
            data={"character": "panda", "visit_token": maple_token},
            headers={"X-Student-Visit-Token": maple_token},
            follow_redirects=False,
        )
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
        self.assertNotEqual(str(second[0]["visit_token"]), first_token)


if __name__ == "__main__":
    unittest.main(verbosity=2)

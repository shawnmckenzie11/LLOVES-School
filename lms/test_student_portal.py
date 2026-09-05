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
                "codenames": ["Maple"],
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


if __name__ == "__main__":
    unittest.main(verbosity=2)

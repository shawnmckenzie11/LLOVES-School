#!/usr/bin/env python3
"""Staff Grok bots showcase: data order, staff gating, and bot cards."""

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

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)

from app import create_app  # noqa: E402
from bots import list_bots  # noqa: E402


class BotsShowcaseTests(unittest.TestCase):
    """Staff-only /staff/bots page, featured Module Engineer, and Wonder card."""

    def setUp(self) -> None:
        """Isolated sqlite + Flask test client."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _login_staff(self, email: str = "teacher@gmail.com") -> None:
        """Register and finish mock Google + 2SV as staff."""
        self.school.register_staff(email)
        self.client.get("/auth/google?portal=staff")
        self.client.get(f"/auth/google/callback?email={email}&name=T")
        user = self.school.get_user_by_email(email)
        assert user is not None
        self.client.post("/verify-email", data={"code": user["verification_code"]})

    def test_module_engineer_is_first_card(self) -> None:
        """Showcase data leads with the featured Module Engineer bot."""
        bots = list_bots()
        self.assertGreaterEqual(len(bots), 1)
        first = bots[0]
        self.assertFalse(first.get("placeholder"))
        self.assertEqual(first["slug"], "module-engineer")
        self.assertEqual(first["name"], "Module Engineer")
        self.assertEqual(first["aka"], "dr eggbot")
        self.assertTrue(first.get("featured"))
        self.assertEqual(first["status"], "active")
        self.assertIn("Student storyline", first["perspectives"])
        self.assertIn("Teacher storyline", first["perspectives"])

    def test_wonder_card_is_present_and_not_featured(self) -> None:
        """Wonder occupies a real slot; Module Engineer stays the featured first card."""
        bots = list_bots()
        self.assertEqual(bots[0]["slug"], "module-engineer")
        self.assertTrue(bots[0].get("featured"))
        wonder = next((bot for bot in bots if bot.get("slug") == "wonder"), None)
        self.assertIsNotNone(wonder)
        assert wonder is not None
        self.assertEqual(wonder["name"], "Wonder")
        self.assertEqual(wonder["aka"], "Hall of Wonder / Celebrations")
        self.assertEqual(
            wonder["role"],
            "Student-facing Celebrations / Hall of Wonder delight voice",
        )
        self.assertEqual(
            wonder["focus"],
            [
                "Warm, precise celebration + Hall of Wonder copy",
                "Live-class media-pane captions / unlock toasts / micro-moments",
                "Quarantine chrome so challenge media stays wondrous",
            ],
        )
        self.assertEqual(
            wonder["perspectives"],
            [
                "Student delight",
                "Teacher-facing toast timing (with Live-Class / ELC)",
            ],
        )
        self.assertEqual(wonder["status"], "active")
        self.assertFalse(wonder.get("featured"))
        self.assertEqual(
            wonder["note"],
            "Quietly makes the picture and the celebration feel human.",
        )
        self.assertFalse(wonder.get("placeholder"))

    def test_placeholders_leave_room_for_later_bots(self) -> None:
        """Exactly one empty slot stays in the list for the next card."""
        placeholders = [bot for bot in list_bots() if bot.get("placeholder")]
        self.assertEqual(len(placeholders), 1)

    def test_anonymous_is_sent_to_staff_login(self) -> None:
        """Unsigned visitors are not shown the staff bots page."""
        rv = self.client.get("/staff/bots", follow_redirects=False)
        self.assertEqual(rv.status_code, 302)
        self.assertIn("/auth/google", rv.headers.get("Location", ""))
        self.assertIn("portal=staff", rv.headers.get("Location", ""))

    def test_staff_page_renders_module_engineer(self) -> None:
        """Signed-in staff see the featured card and open slots."""
        self._login_staff()
        rv = self.client.get("/staff/bots")
        self.assertEqual(rv.status_code, 200)
        html = rv.get_data(as_text=True)
        self.assertIn("Module Engineer", html)
        self.assertIn("dr eggbot", html)
        self.assertIn("Lead / Module Engineer", html)
        self.assertIn("Student storyline", html)
        self.assertIn("Teacher storyline", html)
        self.assertIn("C1→C3 storylines", html)
        self.assertIn('id="module-engineer"', html)
        self.assertIn("Wonder", html)
        self.assertIn("Hall of Wonder / Celebrations", html)
        self.assertIn('id="wonder"', html)
        self.assertIn("Open slot", html)
        self.assertIn("Staff · development", html)
        self.assertNotIn("href=\"/student", html)

    def test_staff_home_and_course_nav_link_to_bots(self) -> None:
        """Staff home corner and course chrome both expose /staff/bots."""
        self._login_staff()
        home = self.client.get("/staff")
        self.assertEqual(home.status_code, 200)
        home_html = home.get_data(as_text=True)
        self.assertIn("/staff/bots", home_html)
        self.assertIn(">Bots<", home_html)

        self.school.activate_from_semester_json()
        teacher = self.school.get_user_by_email("teacher@gmail.com")
        assert teacher is not None
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCF3M"
        )
        cls = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCF3M",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Maple"],
            offering_id=int(offering["id"]),
            teacher_user_id=int(teacher["id"]),
        )
        course = self.client.get(f"/staff/class/{cls['id']}")
        self.assertEqual(course.status_code, 200)
        course_html = course.get_data(as_text=True)
        self.assertIn("/staff/bots", course_html)

    def test_it_dashboard_links_to_bots(self) -> None:
        """Admin chrome also points at the staff bots page."""
        self.client.get("/auth/google?portal=it")
        self.client.get(
            "/auth/google/callback?email=solutions@mckenzian.com&name=Shawn"
        )
        user = self.school.get_user_by_email("solutions@mckenzian.com")
        assert user is not None
        self.client.post("/verify-email", data={"code": user["verification_code"]})
        rv = self.client.get("/it")
        self.assertEqual(rv.status_code, 200)
        html = rv.get_data(as_text=True)
        self.assertIn("/staff/bots", html)
        self.assertIn(">Bots<", html)
        bots = self.client.get("/staff/bots")
        self.assertEqual(bots.status_code, 200)
        self.assertIn("Module Engineer", bots.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()

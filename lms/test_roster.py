#!/usr/bin/env python3
"""LLOVES roster path: Codenames, no CSV, shared live_access_code."""

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


class RosterTests(unittest.TestCase):
    """Populate Class API and Grades Codename sort."""

    def setUp(self) -> None:
        """Isolated app with one assigned teacher."""
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
            data={"code": self.school.get_user_by_email("teacher@gmail.com")["verification_code"]},
        )

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def test_populate_rejects_csv(self) -> None:
        """Canvas CSV is not offered on the LLOVES path."""
        rv = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "csv_text": "Student,ID\nNope,1\n",
                "codenames": ["Maple"],
            },
        )
        self.assertEqual(rv.status_code, 400)
        self.assertIn("CSV", rv.get_json()["error"])

    def test_populate_codenames_and_grades_sort(self) -> None:
        """Populate stores Codenames; dashboard sorts A–Z."""
        rv = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "T/Th/F",
                "time": "2:00pm",
                "codenames": ["Zebra", "Aspen"],
            },
        )
        self.assertEqual(rv.status_code, 200)
        class_id = rv.get_json()["class"]["id"]
        self.assertEqual(rv.get_json()["class"]["live_access_code"], self.offering["live_access_code"])
        dash = self.client.get(f"/api/classes/{class_id}/dashboard?sort=az")
        self.assertEqual(dash.status_code, 200)
        names = [s["codename"] for s in dash.get_json()["students"]]
        self.assertEqual(names, ["Aspen", "Zebra"])
        course = self.client.get(f"/staff/class/{class_id}?tab=grades")
        self.assertEqual(course.status_code, 200)
        html = course.get_data(as_text=True)
        self.assertNotIn('placeholder="Last name"', html)
        self.assertIn("Track Attendance &amp; Participation", html)
        self.assertNotIn(">Grades</a>", html)
        self.assertIn("<h1>MCF3M</h1>", html)
        self.assertNotIn("Tue/Thu/Fri", html)
        self.assertIn("Start Live Class Tracker", html)
        self.assertIn("Class Data View", html)
        self.assertNotIn(">Add Student</h2>", html)
        self.assertNotIn("id=\"add-student\"", html)
        self.assertIn(">Log TOTAL</h2>", html)
        self.assertNotIn("Begin a New Game", html)

    def test_staff_home_populate_vs_edit(self) -> None:
        """Empty offerings say Populate Class; existing sections say Edit Class."""
        home = self.client.get("/staff")
        self.assertEqual(home.status_code, 200)
        empty = home.get_data(as_text=True)
        self.assertRegex(
            empty,
            r'<button[^>]*class="btn-populate secondary"[^>]*>Populate Class</button>',
        )
        self.assertNotIn("Edit Class", empty)
        self.assertNotIn("Repopulate Class", empty)
        self.assertNotIn("OPEN COURSE", empty)
        self.assertNotIn("Schedule:", empty)

        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple"],
            },
        )
        self.assertEqual(created.status_code, 200)
        class_id = created.get_json()["class"]["id"]

        filled = self.client.get("/staff").get_data(as_text=True)
        self.assertRegex(
            filled,
            r'<button[^>]*class="btn-populate secondary"[^>]*>Edit Class</button>',
        )
        self.assertIn(f'data-class-id="{class_id}"', filled)
        self.assertNotRegex(
            filled,
            r'<button[^>]*class="btn-populate secondary"[^>]*>Populate Class</button>',
        )
        self.assertNotIn("Repopulate Class", filled)
        self.assertIn("OPEN COURSE", filled)
        self.assertIn("Schedule: Mon/Wed/Fri · 2:00pm", filled)
        self.assertIn(f"/staff/class/{class_id}", filled)

        dash = self.client.get(f"/staff/class/{class_id}").get_data(as_text=True)
        self.assertIn("<h1>MCF3M</h1>", dash)
        self.assertIn("Track Attendance &amp; Participation", dash)
        self.assertIn(">Modules</a>", dash)
        self.assertIn(">Syllabus</a>", dash)
        header, _, _ = dash.partition('class="tabs"')
        self.assertNotIn("Mon/Wed/Fri", header)
        self.assertNotIn("Student code", header)

    def test_edit_class_roster_keeps_section_and_remaining_students(self) -> None:
        """PUT roster adds/removes Codenames on the same class id."""
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple", "Aspen"],
            },
        )
        self.assertEqual(created.status_code, 200)
        class_id = created.get_json()["class"]["id"]
        first = self.client.get(f"/api/classes/{class_id}/dashboard?sort=az")
        maple = next(s for s in first.get_json()["students"] if s["codename"] == "Maple")
        rv = self.client.put(
            f"/api/staff/classes/{class_id}/roster",
            json={"codenames": ["Maple", "Cedar"]},
        )
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(rv.get_json()["class"]["id"], class_id)
        names = [s["codename"] for s in rv.get_json()["students"]]
        self.assertEqual(sorted(names), ["Cedar", "Maple"])
        kept = next(s for s in rv.get_json()["students"] if s["codename"] == "Maple")
        self.assertEqual(kept["id"], maple["id"])
        still_post = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "T/Th/F",
                "time": "2:00pm",
                "codenames": ["Birch"],
            },
        )
        self.assertEqual(still_post.status_code, 200)
        self.assertNotEqual(still_post.get_json()["class"]["id"], class_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)

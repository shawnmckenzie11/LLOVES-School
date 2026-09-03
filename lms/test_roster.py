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
        course = self.client.get(f"/staff/class/{class_id}?tab=ap&view=participation")
        self.assertEqual(course.status_code, 200)
        html = course.get_data(as_text=True)
        self.assertNotIn('placeholder="Last name"', html)
        self.assertIn("Attendance &amp; Participation", html)
        self.assertIn(">Grades</a>", html)
        self.assertIn("<h1>MCF3M</h1>", html)
        self.assertNotIn("Tue/Thu/Fri", html)
        self.assertIn("Log Participation", html)
        self.assertIn("round-view-select", html)
        self.assertNotIn("Start Live Class Tracker", html)
        self.assertNotIn(">Add Student</h2>", html)
        self.assertNotIn("id=\"add-student\"", html)
        self.assertIn(">Log TOTAL</h2>", html)
        self.assertNotIn("Begin a New Game", html)
        self.assertNotIn("Track Attendance &amp; Participation", html)

        att = self.client.get(f"/staff/class/{class_id}?tab=ap&view=attendance")
        self.assertEqual(att.status_code, 200)
        att_html = att.get_data(as_text=True)
        self.assertIn("Take Attendance", att_html)
        self.assertIn("id=\"attendance-grid\"", att_html)

        grades = self.client.get(f"/staff/class/{class_id}?tab=gradebook")
        self.assertEqual(grades.status_code, 200)
        grades_html = grades.get_data(as_text=True)
        self.assertIn("gradebook-root", grades_html)
        self.assertIn("Participation", grades_html)

        legacy_tab = self.client.get(
            f"/staff/class/{class_id}?tab=grades", follow_redirects=False
        )
        self.assertEqual(legacy_tab.status_code, 302)
        self.assertIn("tab=ap", legacy_tab.headers.get("Location", ""))

        legacy = self.client.get(f"/class/{class_id}", follow_redirects=False)
        self.assertEqual(legacy.status_code, 302)
        self.assertIn("tab=ap", legacy.headers.get("Location", ""))

    def test_attendance_grid_and_finalize_only(self) -> None:
        """Week grid + Done path persist present marks without teams."""
        rv = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Aspen", "Birch"],
            },
        )
        class_id = rv.get_json()["class"]["id"]
        begin = self.client.post(
            f"/api/classes/{class_id}/begin",
            json={"meeting_date": "2026-09-09"},
        )
        self.assertEqual(begin.status_code, 200)
        students = begin.get_json()["students"]
        present_id = int(students[0]["id"])
        done = self.client.post(
            f"/api/classes/{class_id}/game/finalize-attendance",
            json={"present_ids": [present_id]},
        )
        self.assertEqual(done.status_code, 200)
        self.assertTrue(done.get_json()["ok"])
        open_game = self.school.game.conn.execute(
            "SELECT id FROM games WHERE class_id = ? AND status != 'ended'",
            (class_id,),
        ).fetchone()
        self.assertIsNone(open_game)
        grid = self.client.get(f"/api/classes/{class_id}/attendance-grid?sort=az")
        self.assertEqual(grid.status_code, 200)
        payload = grid.get_json()
        self.assertIn("weeks", payload)
        self.assertGreater(len(payload["weeks"]), 0)
        self.assertIn("date_labels", payload)
        self.assertIn("day_meta", payload)
        # First instructional day 2026-09-08 is Tuesday → "S8".
        flat_labels = [lab for week in payload["date_labels"] for lab in week if lab]
        self.assertIn("S8", flat_labels)
        cell = payload["cells"].get(f"{present_id}:2026-09-09")
        self.assertTrue(cell)
        other = int(students[1]["id"])
        self.assertIs(payload["cells"].get(f"{other}:2026-09-09"), False)
        self.assertEqual(payload["day_totals"].get("2026-09-09"), 1)
        day = self.client.get(
            f"/api/classes/{class_id}/attendance-day?date=2026-09-09"
        )
        self.assertEqual(day.status_code, 200)
        day_body = day.get_json()
        self.assertTrue(day_body["logged"])
        self.assertEqual(day_body["present_ids"], [present_id])
        cleared = self.client.post(
            f"/api/classes/{class_id}/attendance-day/clear",
            json={"date": "2026-09-09"},
        )
        self.assertEqual(cleared.status_code, 200)
        self.assertIsNone(
            cleared.get_json()["cells"].get(f"{present_id}:2026-09-09")
        )

    def test_second_day_attendance_and_suggested_date(self) -> None:
        """After day one is finalized, log-context suggests the next school day."""
        rv = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Aspen", "Birch"],
            },
        )
        class_id = rv.get_json()["class"]["id"]
        day1 = "2026-09-09"
        day2 = "2026-09-10"
        begin1 = self.client.post(
            f"/api/classes/{class_id}/begin",
            json={"meeting_date": day1},
        )
        students = begin1.get_json()["students"]
        present_id = int(students[0]["id"])
        done1 = self.client.post(
            f"/api/classes/{class_id}/game/finalize-attendance",
            json={"present_ids": [present_id], "meeting_date": day1},
        )
        self.assertEqual(done1.status_code, 200)
        ctx = self.client.get(f"/api/classes/{class_id}/log-context").get_json()
        self.assertEqual(ctx["suggested_date"], day2)
        begin2 = self.client.post(
            f"/api/classes/{class_id}/begin",
            json={"meeting_date": day2},
        )
        self.assertEqual(begin2.status_code, 200)
        done2 = self.client.post(
            f"/api/classes/{class_id}/game/finalize-attendance",
            json={"present_ids": [present_id], "meeting_date": day2},
        )
        self.assertEqual(done2.status_code, 200)
        grid = self.client.get(f"/api/classes/{class_id}/attendance-grid?sort=az").get_json()
        self.assertTrue(grid["cells"].get(f"{present_id}:{day1}"))
        self.assertTrue(grid["cells"].get(f"{present_id}:{day2}"))

    def test_participation_grid_matches_attendance_columns(self) -> None:
        """Participation tab grid shares semester date columns with attendance."""
        rv = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Aspen"],
            },
        )
        class_id = rv.get_json()["class"]["id"]
        att = self.client.get(f"/api/classes/{class_id}/attendance-grid?sort=az").get_json()
        part = self.client.get(f"/api/classes/{class_id}/participation-grid?sort=az").get_json()
        self.assertEqual(att["weeks"], part["weeks"])
        self.assertEqual(att["date_labels"], part["date_labels"])

    def test_log_context_and_live_day_setting(self) -> None:
        """Admin live-day gate flips valid picker dates; log-context exposes it."""
        rv = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Aspen"],
            },
        )
        class_id = rv.get_json()["class"]["id"]
        ctx = self.client.get(f"/api/classes/{class_id}/log-context")
        self.assertEqual(ctx.status_code, 200)
        body = ctx.get_json()
        self.assertIn("valid_dates", body)
        self.assertFalse(body["only_live_class_days"])
        # Gate off → instructional days include Tue 2026-09-08.
        isos = {row["iso"] for row in body["valid_dates"]}
        self.assertIn("2026-09-08", isos)
        self.school.set_only_live_class_days(True)
        gated = self.client.get(f"/api/classes/{class_id}/log-context").get_json()
        self.assertTrue(gated["only_live_class_days"])
        live_isos = {row["iso"] for row in gated["valid_dates"]}
        self.assertNotIn("2026-09-08", live_isos)  # Tue not M/W/F
        self.assertIn("2026-09-09", live_isos)  # Wed

    def test_staff_home_has_ap_shortcut(self) -> None:
        """Course cards expose a Log Attendance & Participation shortcut."""
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple"],
            },
        )
        class_id = created.get_json()["class"]["id"]
        home = self.client.get("/staff").get_data(as_text=True)
        self.assertIn("btn-ap-shortcut", home)
        self.assertIn(f"/staff/class/{class_id}?tab=ap&amp;view=attendance&amp;take=1", home)
        self.assertIn("Log Attendance", home)

    def test_ungamified_live_scoring(self) -> None:
        """No-gamify path starts live scoring with one Class team."""
        rv = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Aspen", "Birch"],
            },
        )
        class_id = rv.get_json()["class"]["id"]
        begin = self.client.post(
            f"/api/classes/{class_id}/begin",
            json={"meeting_date": "2026-09-09"},
        )
        students = begin.get_json()["students"]
        ids = [int(s["id"]) for s in students]
        live = self.client.post(
            f"/api/classes/{class_id}/game/ungamified",
            json={"present_ids": ids, "meeting_date": "2026-09-09"},
        )
        self.assertEqual(live.status_code, 200)
        state = live.get_json()
        self.assertEqual(state["game"]["status"], "live")
        self.assertEqual(len(state["teams"]), 1)
        award = self.client.post(
            f"/api/classes/{class_id}/game/score",
            json={"kind": "student", "id": ids[0], "amount": 5},
        )
        self.assertEqual(award.status_code, 200)

    def test_gradebook_weights_defaults_and_persist(self) -> None:
        """Grades scaffold seeds 15/60/25 and persists weight edits."""
        rv = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Cedar"],
            },
        )
        class_id = rv.get_json()["class"]["id"]
        book = self.client.get(f"/api/classes/{class_id}/gradebook")
        self.assertEqual(book.status_code, 200)
        body = book.get_json()
        self.assertEqual(body["weights"]["participation"], 15.0)
        self.assertEqual(body["weights"]["term"], 60.0)
        self.assertEqual(body["weights"]["exam"], 25.0)
        ids = [c["id"] for c in body["categories"]]
        self.assertEqual(ids, ["participation", "term", "exam"])
        self.assertTrue(body["categories"][2]["placeholder"])
        self.assertIn("weight_edit_endpoint", body)
        updated = self.client.post(
            f"/api/classes/{class_id}/grade-weights",
            json={"participation": 20, "term": 55, "exam": 25},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["weights"]["participation"], 20.0)
        again = self.client.get(f"/api/classes/{class_id}/grade-weights")
        self.assertEqual(again.get_json()["weights"]["term"], 55.0)

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
        self.assertIn("Attendance &amp; Participation", dash)
        self.assertIn(">Grades</a>", dash)
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

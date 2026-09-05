#!/usr/bin/env python3
"""LLOVES roster path: Codenames, no CSV, per-class live_access_code."""

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
        self.assertIsNone(rv.get_json()["class"]["live_access_code"])
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
        self.assertIn("Begin Class Tracking", att_html)
        self.assertNotIn(">Mood</a>", att_html)
        self.assertNotIn("id=\"mood-grid\"", att_html)
        self.assertNotIn("id=\"ap-att-all\"", att_html)
        self.assertNotIn("id=\"ap-att-done\"", att_html)
        self.assertNotIn(">All present<", att_html)

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

    def test_picked_meeting_date_lands_in_matching_column(self) -> None:
        """Attendance finalized for a picker date fills that calendar column."""
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
        begin = self.client.post(
            f"/api/classes/{class_id}/begin",
            json={"meeting_date": "2026-09-08"},
        )
        self.assertEqual(begin.status_code, 200)
        moved = self.client.post(
            f"/api/classes/{class_id}/game/meeting",
            json={"meeting_date": "2026-09-11"},
        )
        self.assertEqual(moved.status_code, 200)
        self.assertEqual(moved.get_json()["session"]["meeting_date"], "2026-09-11")
        present_id = int(begin.get_json()["students"][0]["id"])
        done = self.client.post(
            f"/api/classes/{class_id}/game/finalize-attendance",
            json={"present_ids": [present_id], "meeting_date": "2026-09-11"},
        )
        self.assertEqual(done.status_code, 200)
        grid = self.client.get(f"/api/classes/{class_id}/attendance-grid?sort=az").get_json()
        self.assertTrue(grid["cells"].get(f"{present_id}:2026-09-11"))
        self.assertIsNone(grid["cells"].get(f"{present_id}:2026-09-08"))

    def test_setup_rounds_then_start_scoring(self) -> None:
        """Create Teams can pause on rounds setup before live scoring."""
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
        ids = [int(s["id"]) for s in begin.get_json()["students"]]
        self.client.post(
            f"/api/classes/{class_id}/game/attendance",
            json={"present_ids": ids, "meeting_date": "2026-09-09"},
        )
        assigned = self.client.post(
            f"/api/classes/{class_id}/game/assign",
            json={"n_teams": 2, "mode": "random"},
        )
        teams = [
            {"id": t["id"], "name": t["name"]}
            for t in assigned.get_json()["teams"]
        ]
        renamed = self.client.post(
            f"/api/classes/{class_id}/game/rename",
            json={"teams": teams, "go_live": False},
        )
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.get_json()["game"]["status"], "rounds")
        live = self.client.post(
            f"/api/classes/{class_id}/game/start-rounds",
            json={
                "rounds": [
                    {"kind": "formative", "minutes": 15},
                    {"kind": "open", "minutes": 20},
                ]
            },
        )
        self.assertEqual(live.status_code, 200)
        state = live.get_json()
        self.assertEqual(state["game"]["status"], "live")
        self.assertEqual(state["game"]["round_title"], "Formative")
        self.assertEqual(state["game"]["round_count"], 2)
        self.assertEqual(state["game"]["round_duration_sec"], 15 * 60)

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
        """Course cards expose Take Attendance, Log Participation, and Run Live Class."""
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
        self.assertIn("Take Attendance", home)
        self.assertIn("Log Participation", home)
        self.assertNotIn("Take Attendance &amp; Log Participation", home)
        self.assertIn(f"/staff/class/{class_id}?tab=ap&amp;view=attendance", home)
        self.assertIn(
            f"/staff/class/{class_id}?tab=ap&amp;view=participation&amp;participate=1",
            home,
        )
        self.assertNotIn("take=1", home)
        self.assertIn(f"/staff/class/{class_id}/run-live", home)
        self.assertIn("Run Live Class", home)
        self.assertNotIn("Live Class in Progress", home)
        self.assertIn("Explore Course", home)
        self.assertIn("Edit Roster", home)

        self.client.post(f"/staff/class/{class_id}/run-live")
        live_home = self.client.get("/staff").get_data(as_text=True)
        self.assertIn("Live Class in Progress", live_home)
        self.assertNotIn("Run Live Class", live_home)
        self.assertIn(f"/staff/class/{class_id}/end-live", live_home)
        self.assertIn(">End<", live_home)
        ended = self.client.post(
            f"/staff/class/{class_id}/end-live",
            follow_redirects=False,
        )
        self.assertEqual(ended.status_code, 302)
        self.assertIn("/staff", ended.headers.get("Location", ""))
        self.assertIsNone(self.school.get_active_live_session_for_class(class_id))
        idle_home = self.client.get("/staff").get_data(as_text=True)
        self.assertIn("Run Live Class", idle_home)
        self.assertNotIn("Live Class in Progress", idle_home)

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
        """Empty offerings say Populate Class; existing sections say Edit Roster."""
        home = self.client.get("/staff")
        self.assertEqual(home.status_code, 200)
        empty = home.get_data(as_text=True)
        self.assertIn("<span>Populate Class</span>", empty)
        self.assertNotIn("<span>Edit Roster</span>", empty)
        self.assertNotIn("Edit Class", empty)
        self.assertNotIn("Repopulate Class", empty)
        self.assertNotIn("OPEN COURSE", empty)
        self.assertNotIn("Explore Course", empty)
        self.assertNotIn("Run Live Class", empty)
        self.assertNotIn("Schedule:", empty)
        self.assertIn("Schedule set when you Populate Class", empty)

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
        self.assertIn("<span>Edit Roster</span>", filled)
        self.assertIn(f'data-class-id="{class_id}"', filled)
        self.assertNotIn("<span>Populate Class</span>", filled)
        self.assertNotIn("Repopulate Class", filled)
        self.assertNotIn("OPEN COURSE", filled)
        self.assertIn("Explore Course", filled)
        self.assertIn("M | W | F | 2:00 PM", filled)
        self.assertNotIn("Schedule set when you Populate Class", filled)
        self.assertIn(f"/staff/class/{class_id}", filled)
        self.assertNotIn("Student code appears when you Run Live Class", filled)
        self.assertNotIn('class="course-card-join">Student code', filled)

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

    def test_run_live_mints_unique_session_code(self) -> None:
        """Run Live Class mints a session; a second Run is blocked until ended."""
        first = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple"],
            },
        )
        class_id = first.get_json()["class"]["id"]
        run = self.client.post(
            f"/staff/class/{class_id}/run-live",
            follow_redirects=False,
        )
        self.assertEqual(run.status_code, 302)
        location = run.headers.get("Location", "")
        self.assertIn(f"/staff/class/{class_id}", location)
        self.assertIn("tab=ap", location)
        self.assertIn("take=1", location)
        self.assertIn("live_session_id=", location)
        session_one = self.school.get_active_live_session_for_class(class_id)
        self.assertIsNotNone(session_one)
        assert session_one is not None
        minted = str(session_one["session_code"])
        self.assertEqual(len(minted), 8)
        self.assertNotEqual(minted, self.offering["live_access_code"])
        blocked = self.client.post(
            f"/staff/class/{class_id}/run-live",
            follow_redirects=False,
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertIn(
            b"already have a live class running",
            blocked.get_data(),
        )
        still_active = self.school.get_active_live_session_for_class(class_id)
        self.assertIsNotNone(still_active)
        assert still_active is not None
        self.assertEqual(int(still_active["id"]), int(session_one["id"]))
        self.assertEqual(still_active["session_code"], minted)
        self.school.end_live_class_session(int(session_one["id"]))
        again = self.client.post(
            f"/staff/class/{class_id}/run-live",
            follow_redirects=False,
        )
        self.assertEqual(again.status_code, 302)
        session_two = self.school.get_active_live_session_for_class(class_id)
        self.assertIsNotNone(session_two)
        assert session_two is not None
        self.assertNotEqual(int(session_two["id"]), int(session_one["id"]))
        self.assertNotEqual(session_two["session_code"], minted)
        ended = self.school.get_live_session(int(session_one["id"]))
        assert ended is not None
        self.assertEqual(ended["status"], "ended")
        home = self.client.get("/staff").get_data(as_text=True)
        self.assertNotIn(minted, home)
        self.assertNotIn(str(session_two["session_code"]), home)
        self.assertNotIn("Student code appears when you Run Live Class", home)
        overlay = self.client.get(f"/live-overlay/{int(session_two['id'])}")
        self.assertEqual(overlay.status_code, 200)
        overlay_html = overlay.get_data(as_text=True)
        self.assertIn("live-overlay", overlay_html)
        self.assertIn("live_session_overlay.js", overlay_html)

    def test_two_classes_get_distinct_session_codes(self) -> None:
        """Same teacher cannot run two concurrent live sessions; IT can force-end."""
        first = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple"],
            },
        )
        class_a = first.get_json()["class"]["id"]
        other = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]),
            ontario_code="MCF3M",
            new_section=True,
        )
        second = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": other["id"],
                "days": "T/Th/F",
                "time": "2:00pm",
                "codenames": ["Birch"],
            },
        )
        class_b = second.get_json()["class"]["id"]
        self.client.post(f"/staff/class/{class_a}/run-live")
        session_a = self.school.get_active_live_session_for_class(class_a)
        self.assertIsNotNone(session_a)
        assert session_a is not None
        home_live = self.client.get("/staff").get_data(as_text=True)
        self.assertIn("Live Class in Progress", home_live)
        self.assertIn(f"/staff/class/{class_a}/end-live", home_live)
        self.assertNotIn(f"/staff/class/{class_b}/end-live", home_live)
        self.assertNotIn(f"/staff/class/{class_a}/run-live", home_live)
        self.assertNotIn(f"/staff/class/{class_b}/run-live", home_live)
        blocked = self.client.post(
            f"/staff/class/{class_b}/run-live",
            follow_redirects=False,
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertIsNone(self.school.get_active_live_session_for_class(class_b))
        active = self.client.get("/api/live-sessions/active")
        self.assertEqual(active.status_code, 200)
        codes = {row["session_code"] for row in active.get_json()["sessions"]}
        self.assertEqual(codes, {session_a["session_code"]})
        state = self.client.get(
            f"/api/live-sessions/{int(session_a['id'])}/state"
        )
        self.assertEqual(state.status_code, 200)
        payload = state.get_json()
        self.assertEqual(payload["code"], session_a["session_code"])
        self.assertEqual(payload["phase"], "live")
        self.assertEqual(payload["count"], 0)

        # Distinct codes across teachers after ending the first session.
        other_teacher = self.school.register_staff("other-teacher@gmail.com")
        other_offering = self.school.assign_course(
            teacher_user_id=int(other_teacher["id"]),
            ontario_code="MCR3U",
        )
        other_client = self.app.test_client()
        other_client.get("/auth/google?portal=staff")
        other_client.get(
            "/auth/google/callback?email=other-teacher@gmail.com&name=O"
        )
        other_user = self.school.get_user_by_email("other-teacher@gmail.com")
        assert other_user is not None
        other_client.post(
            "/verify-email",
            data={"code": other_user["verification_code"]},
        )
        other_class_rv = other_client.post(
            "/api/staff/classes",
            json={
                "offering_id": other_offering["id"],
                "days": "M/W/F",
                "time": "10:40am",
                "codenames": ["Cedar"],
            },
        )
        self.assertEqual(other_class_rv.status_code, 200, other_class_rv.get_json())
        other_class = other_class_rv.get_json()["class"]["id"]
        other_client.post(f"/staff/class/{other_class}/run-live")
        session_b = self.school.get_active_live_session_for_class(other_class)
        self.assertIsNotNone(session_b)
        assert session_b is not None
        self.assertNotEqual(session_a["session_code"], session_b["session_code"])

        # IT force-end clears stuck sessions.
        it = self.app.test_client()
        it.get("/auth/google?portal=it")
        it.get("/auth/google/callback?email=solutions@mckenzian.com&name=IT")
        it_user = self.school.get_user_by_email("solutions@mckenzian.com")
        assert it_user is not None
        it.post("/verify-email", data={"code": it_user["verification_code"]})
        end_one = it.post(f"/api/it/live-sessions/{int(session_a['id'])}/end")
        self.assertEqual(end_one.status_code, 200)
        self.assertTrue(end_one.get_json()["ok"])
        ended_state = self.client.get(
            f"/api/live-sessions/{int(session_a['id'])}/state"
        )
        self.assertEqual(ended_state.get_json()["phase"], "ended")
        self.assertIsNone(
            self.school.get_active_live_session_by_code(session_a["session_code"])
        )
        end_all = it.post("/api/it/live-sessions/end-all")
        self.assertEqual(end_all.status_code, 200)
        self.assertGreaterEqual(end_all.get_json()["ended_count"], 1)
        self.assertIsNone(self.school.get_active_live_session_for_class(other_class))

        # Original teacher can start class B after the block is cleared.
        retry_b = self.client.post(
            f"/staff/class/{class_b}/run-live",
            follow_redirects=False,
        )
        self.assertEqual(retry_b.status_code, 302)
        session_b2 = self.school.get_active_live_session_for_class(class_b)
        self.assertIsNotNone(session_b2)

    def test_student_join_records_attendee_and_leave_wipes_mood(self) -> None:
        """Active session join binds attendee; leave sets left_at and clears mood."""
        first = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple"],
            },
        )
        class_id = first.get_json()["class"]["id"]
        self.client.post(f"/staff/class/{class_id}/run-live")
        live = self.school.get_active_live_session_for_class(class_id)
        assert live is not None
        self.client.get("/logout")
        student = self.app.test_client()
        join = student.post(
            "/auth/student-code",
            data={"code": live["session_code"], "name": "Maple"},
            follow_redirects=False,
        )
        self.assertEqual(join.status_code, 302)
        attendees = self.school.list_live_session_attendees(
            int(live["id"]), present_only=True
        )
        self.assertEqual(len(attendees), 1)
        student.post("/student/mood", data={"mood": "good"})
        maple = self.school.game.find_student_by_codename(class_id, "Maple")
        assert maple is not None
        self.assertEqual(
            self.school.game.get_mood(class_id, int(maple["id"])), "good"
        )
        leave = student.post("/api/student/leave")
        self.assertEqual(leave.status_code, 204)
        left = self.school.list_live_session_attendees(int(live["id"]))
        self.assertEqual(len(left), 1)
        self.assertIsNotNone(left[0].get("left_at"))
        self.assertIsNone(self.school.game.get_mood(class_id, int(maple["id"])))
        self.assertTrue(self.school.has_active_live_sessions())

    def test_run_live_wipes_prior_moods(self) -> None:
        """Run Live Class clears leftover mood faces before Mark Attendance."""
        first = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple"],
            },
        )
        class_id = first.get_json()["class"]["id"]
        maple = self.school.game.find_student_by_codename(class_id, "Maple")
        assert maple is not None
        self.school.game.set_mood(class_id, int(maple["id"]), "good")
        self.assertEqual(self.school.game.get_mood(class_id, int(maple["id"])), "good")
        self.client.post(f"/staff/class/{class_id}/run-live")
        self.assertIsNone(self.school.game.get_mood(class_id, int(maple["id"])))

    def test_begin_class_tracking_stays_authed_after_student_join(self) -> None:
        """Same-browser student join must not wipe staff Begin Class Tracking auth."""
        first = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple"],
            },
        )
        class_id = first.get_json()["class"]["id"]
        self.client.post(f"/staff/class/{class_id}/run-live")
        live = self.school.get_active_live_session_for_class(class_id)
        assert live is not None
        begin = self.client.post(
            f"/api/classes/{class_id}/begin",
            json={"meeting_date": "2026-09-09"},
        )
        self.assertEqual(begin.status_code, 200)
        # Same test client joins as student (shared cookie jar).
        self.client.post(
            "/auth/student-code",
            data={"code": live["session_code"], "name": "Maple"},
        )
        maple = self.school.game.find_student_by_codename(class_id, "Maple")
        assert maple is not None
        att = self.client.post(
            f"/api/classes/{class_id}/game/attendance",
            json={"present_ids": [int(maple["id"])], "meeting_date": "2026-09-09"},
        )
        self.assertEqual(att.status_code, 200, att.get_data(as_text=True))
        self.assertNotIn("Authentication required", att.get_data(as_text=True))
        self.assertEqual(att.get_json()["game"]["status"], "teams")

    def test_join_auto_marks_present_when_attendance_open(self) -> None:
        """Join marks present on the open Mark Attendance meeting column."""
        first = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple", "Birch"],
            },
        )
        class_id = first.get_json()["class"]["id"]
        self.client.post(f"/staff/class/{class_id}/run-live")
        live = self.school.get_active_live_session_for_class(class_id)
        assert live is not None
        self.school.game.begin_game(class_id)
        maple = self.school.game.find_student_by_codename(class_id, "Maple")
        assert maple is not None
        self.client.get("/logout")
        student = self.app.test_client()
        student.post(
            "/auth/student-code",
            data={"code": live["session_code"], "name": "Maple"},
        )
        game = self.school.game._game_row(class_id)
        with self.school.game._lock:
            row = self.school.game.conn.execute(
                """
                SELECT present FROM session_scores
                WHERE session_id = ? AND student_id = ?
                """,
                (int(game["session_id"]), int(maple["id"])),
            ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(int(row["present"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

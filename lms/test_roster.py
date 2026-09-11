#!/usr/bin/env python3
"""LLOVES roster path: Codenames, no CSV, per-class live_access_code."""

from __future__ import annotations

import json
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
        self.assertNotIn("Log Participation", html)
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
        self.assertIn("take=1", att_html)
        self.assertIn("id=\"att-take-card\"", att_html)
        self.assertIn("id=\"attendance-grid\"", att_html)
        self.assertIn("id=\"att-take-done\"", att_html)
        self.assertIn(">All present<", att_html)
        # Inline take card lives on the A&P attendance tab; wizard is on Run Live Class.
        self.assertNotIn("id=\"ap-att-log\"", att_html)
        self.assertNotIn(">Mood</a>", att_html)
        self.assertNotIn("id=\"mood-grid\"", att_html)
        self.assertNotIn("id=\"ap-att-all\"", att_html)
        self.assertNotIn("id=\"ap-att-done\"", att_html)

        live = self.client.get(f"/staff/class/{class_id}?tab=live")
        self.assertEqual(live.status_code, 200)
        live_html = live.get_data(as_text=True)
        self.assertIn("Run Live Class", live_html)
        self.assertIn("Next →", live_html)
        self.assertIn("ap-att-log", live_html)
        self.assertNotIn("id=\"track-accordion\"", live_html)
        self.assertIn("id=\"live-header\"", live_html)
        self.assertIn("id=\"live-stage-rail\"", live_html)
        self.assertIn("id=\"class-list-pane\"", live_html)
        self.assertIn("id=\"team-assign-pane\"", live_html)
        self.assertIn("id=\"round-slide-settings\"", live_html)
        self.assertIn("id=\"media-artifact-zone\"", live_html)
        self.assertIn("id=\"question-artifact-zone\"", live_html)
        self.assertIn("id=\"results-strip\"", live_html)
        self.assertIn("id=\"ap-att-log\"", live_html)
        self.assertIn("id=\"ap-join-billboard\"", live_html)
        self.assertIn("id=\"ap-join-billboard-code\"", live_html)
        self.assertIn("id=\"ap-join-billboard-copy\"", live_html)
        billboard_open = live_html.split('id="ap-join-billboard"', 1)[1].split(">", 1)[0]
        self.assertIn("hidden", billboard_open)

        grades = self.client.get(f"/staff/class/{class_id}?tab=gradebook")
        self.assertEqual(grades.status_code, 200)
        grades_html = grades.get_data(as_text=True)
        self.assertIn("gradebook-root", grades_html)
        self.assertNotIn(">Overview</h2>", grades_html)
        self.assertIn("Grading Scheme", grades_html)
        self.assertIn("Term Mark", grades_html)
        self.assertIn("scheme-term-module", grades_html)
        self.assertIn("Module 1", grades_html)

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

    def test_end_game_honors_picker_meeting_date(self) -> None:
        """Live End Game persists participation on the picker-chosen day column."""
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
            json={"meeting_date": "2026-09-08"},
        )
        ids = [int(s["id"]) for s in begin.get_json()["students"]]
        live = self.client.post(
            f"/api/classes/{class_id}/game/ungamified",
            json={"present_ids": ids, "meeting_date": "2026-09-11"},
        )
        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.get_json()["session"]["meeting_date"], "2026-09-11")
        scored = self.client.post(
            f"/api/classes/{class_id}/game/score",
            json={"kind": "student", "id": ids[0], "amount": 4},
        )
        self.assertEqual(scored.status_code, 200)
        ended = self.client.post(
            f"/api/classes/{class_id}/game/end",
            json={"meeting_date": "2026-09-11"},
        )
        self.assertEqual(ended.status_code, 200)
        self.assertEqual(ended.get_json()["meeting_date"], "2026-09-11")
        part = self.client.get(
            f"/api/classes/{class_id}/participation-grid?sort=az"
        ).get_json()
        cell = part["cells"].get(f"{ids[0]}:2026-09-11")
        self.assertIsNotNone(cell)
        self.assertEqual(float(cell["points"]), 4.0)
        empty = part["cells"].get(f"{ids[0]}:2026-09-08") or {}
        self.assertEqual(float(empty.get("points") or 0), 0.0)
        ctx = self.client.get(f"/api/classes/{class_id}/log-context").get_json()
        self.assertIn("2026-09-11", ctx.get("logged_dates") or [])

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
        """Course cards expose Take Attendance and Run Live Class."""
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
        self.assertNotIn("Log Participation", home)
        self.assertNotIn("Take Attendance &amp; Log Participation", home)
        self.assertIn(f"/staff/class/{class_id}?tab=ap&amp;view=attendance&amp;take=1", home)
        self.assertIn("take=1", home)
        self.assertIn(f"/staff/class/{class_id}?tab=live&amp;run=1", home)
        self.assertIn("Run Live Class", home)
        self.assertNotIn("Live Class in Progress", home)
        self.assertIn("Explore Course", home)
        self.assertIn("Edit Roster", home)

        self.client.post(f"/staff/class/{class_id}/run-live")
        live_home = self.client.get("/staff").get_data(as_text=True)
        self.assertIn("Live Class in Progress", live_home)
        self.assertNotIn("Run Live Class", live_home)
        self.assertIn(f"/staff/class/{class_id}/end-live", live_home)
        self.assertIn(">End Live Class<", live_home)
        self.assertNotIn(">End<", live_home)
        self.assertIn("All session data will be lost.", live_home)
        self.assertIn("return confirm(this.dataset.confirm)", live_home)
        ended = self.client.post(
            f"/staff/class/{class_id}/end-live",
            follow_redirects=False,
        )
        self.assertEqual(ended.status_code, 302)
        self.assertIn("/staff", ended.headers.get("Location", ""))
        self.assertIsNone(self.school.get_active_live_session_for_class(class_id))
        self.assertEqual(self.school.list_live_sessions_for_class(class_id), [])
        idle_home = self.client.get("/staff").get_data(as_text=True)
        self.assertIn("Run Live Class", idle_home)
        self.assertNotIn("Live Class in Progress", idle_home)
        self.assertNotIn("End Live Class", idle_home)

    def test_staff_home_end_live_targets_active_session_on_other_cards(self) -> None:
        """In Progress cards always offer End targeting the active class_id.

        When the live session belongs to class A, a card for class B still
        shows End Live Class and posts ``/staff/class/{A}/end-live``. That
        remains true after A is archived off the dashboard.
        """
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
        self.assertNotEqual(class_a, class_b)

        self.client.post(f"/staff/class/{class_a}/run-live")
        both_cards = self.client.get("/staff").get_data(as_text=True)
        self.assertEqual(both_cards.count(">Live Class in Progress<"), 2)
        self.assertEqual(both_cards.count(">End Live Class<"), 2)
        self.assertEqual(both_cards.count(f"/staff/class/{class_a}/end-live"), 2)
        self.assertNotIn(f"/staff/class/{class_b}/end-live", both_cards)
        self.assertIn("course-action-live-row", both_cards)
        self.assertIn(f"/staff/class/{class_b}", both_cards)

        self.school.archive_offering(int(self.offering["id"]))
        orphan_card = self.client.get("/staff").get_data(as_text=True)
        self.assertNotIn(f"/staff/class/{class_a}?", orphan_card)
        self.assertNotIn(f"/staff/class/{class_a}\"", orphan_card)
        self.assertIn(">Live Class in Progress<", orphan_card)
        self.assertIn(">End Live Class<", orphan_card)
        self.assertIn(f"/staff/class/{class_a}/end-live", orphan_card)
        self.assertNotIn(f"/staff/class/{class_b}/end-live", orphan_card)
        self.assertEqual(orphan_card.count(">Live Class in Progress<"), 1)
        self.assertEqual(orphan_card.count(">End Live Class<"), 1)

        ended = self.client.post(
            f"/staff/class/{class_a}/end-live",
            follow_redirects=False,
        )
        self.assertEqual(ended.status_code, 302)
        self.assertIsNone(self.school.get_active_live_session_for_class(class_a))
        idle = self.client.get("/staff").get_data(as_text=True)
        self.assertIn("Run Live Class", idle)
        self.assertNotIn("Live Class in Progress", idle)
        self.assertNotIn("End Live Class", idle)

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
        self.assertEqual(state["game"]["round_title"], "Open Question Round")
        self.assertEqual(state["game"]["round_count"], 1)
        award = self.client.post(
            f"/api/classes/{class_id}/game/score",
            json={"kind": "student", "id": ids[0], "amount": 5},
        )
        self.assertEqual(award.status_code, 200)

    def test_individual_prepare_then_start_open_round(self) -> None:
        """Individual tracking parks on rounds, then Start Round opens scoring."""
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
        prepared = self.client.post(
            f"/api/classes/{class_id}/game/ungamified",
            json={
                "present_ids": ids,
                "meeting_date": "2026-09-09",
                "go_live": False,
            },
        )
        self.assertEqual(prepared.status_code, 200)
        prep = prepared.get_json()
        self.assertEqual(prep["game"]["status"], "rounds")
        self.assertEqual(len(prep["teams"]), 1)
        self.assertEqual(prep["teams"][0]["name"], "Class")
        rejected = self.client.post(
            f"/api/classes/{class_id}/game/start-rounds",
            json={"rounds": [{"kind": "break", "minutes": 5, "title": "Snack"}]},
        )
        self.assertEqual(rejected.status_code, 400)
        live = self.client.post(
            f"/api/classes/{class_id}/game/start-rounds",
            json={"rounds": [{"kind": "open", "minutes": 15}]},
        )
        self.assertEqual(live.status_code, 200)
        state = live.get_json()
        self.assertEqual(state["game"]["status"], "live")
        self.assertEqual(state["game"]["round_title"], "Open Question Round")
        self.assertEqual(state["game"]["round_count"], 1)
        self.assertEqual(state["game"]["round_duration_sec"], 15 * 60)
        nxt = self.client.post(
            f"/api/classes/{class_id}/game/append-round",
            json={"kind": "open", "minutes": 10},
        )
        self.assertEqual(nxt.status_code, 200)
        self.assertEqual(nxt.get_json()["game"]["round"], 2)
        bad = self.client.post(
            f"/api/classes/{class_id}/game/append-round",
            json={"kind": "formative", "minutes": 10},
        )
        self.assertEqual(bad.status_code, 400)
        ok_ch = self.client.post(
            f"/api/classes/{class_id}/game/append-round",
            json={"kind": "challenge", "minutes": 10},
        )
        self.assertEqual(ok_ch.status_code, 200)

    def test_gradebook_weights_defaults_and_persist(self) -> None:
        """Grades scaffold seeds 10/65/25 and persists weight edits."""
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
        self.assertEqual(body["weights"]["participation"], 10.0)
        self.assertEqual(body["weights"]["term"], 65.0)
        self.assertEqual(body["weights"]["exam"], 25.0)
        ids = [c["id"] for c in body["categories"]]
        self.assertEqual(ids, ["participation", "term", "exam"])
        self.assertEqual(body["categories"][0]["label"], "Att & Participation")
        self.assertTrue(body["categories"][0]["placeholder"])
        self.assertTrue(body["categories"][2]["placeholder"])
        self.assertIn("weight_edit_endpoint", body)
        self.assertEqual(body["module_1"]["window"]["start"], "2026-09-10")
        self.assertEqual(body["module_1"]["window"]["end"], "2026-09-23")
        cedar = next(s for s in body["students"] if s["codename"] == "Cedar")
        detail = body["module_1"]["students"][str(cedar["id"])]
        self.assertFalse(detail["earned_100"])
        self.assertIsNone(detail["score"])
        updated = self.client.post(
            f"/api/classes/{class_id}/grade-weights",
            json={"participation": 20, "term": 55, "exam": 25},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["weights"]["participation"], 20.0)
        again = self.client.get(f"/api/classes/{class_id}/grade-weights")
        self.assertEqual(again.get_json()["weights"]["term"], 55.0)

    def test_staff_home_js_parses(self) -> None:
        """Staff home module must parse or Populate Class is a dead click."""
        src = (LMS_DIR / "static" / "staff_home.js").read_text(encoding="utf-8")
        stripped = "\n".join(
            line for line in src.splitlines() if not line.startswith("import ")
        )
        proc = subprocess.run(
            ["node", "-e", f"new Function({json.dumps(stripped)})"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('el.classList.contains("btn-populate")', src)
        self.assertIn("Roster setup does not need a module pack", src)

    def test_assign_without_pack_keeps_populate_enabled(self) -> None:
        """No .imscc yet: teacher still sees a working Populate Class control."""
        self.assertIsNone(self.offering.get("library_id"))
        home = self.client.get("/staff")
        self.assertEqual(home.status_code, 200)
        html = home.get_data(as_text=True)
        self.assertIn("<span>Populate Class</span>", html)
        self.assertIn(f'data-offering-id="{self.offering["id"]}"', html)
        self.assertIn('class="course-action btn-populate secondary"', html)
        self.assertNotIn("btn-populate secondary is-disabled", html)
        self.assertNotIn("disabled aria-disabled", html)
        self.assertIn("Ask Admin to attach a module pack", html)
        self.assertIn('id="error" class="error" hidden', html)
        self.assertNotIn('class="error hidden"', html)
        status = self.client.get(
            f"/staff/offerings/{int(self.offering['id'])}/module-pack/status"
        )
        self.assertEqual(status.status_code, 200)
        body = status.get_json()
        self.assertFalse(body.get("busy"))
        self.assertEqual(body.get("badge"), "No pack")
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
        self.assertTrue(created.get_json().get("ok"))

    def test_staff_home_populate_vs_edit(self) -> None:
        """Empty offerings say Populate Class; existing sections say Edit Roster."""
        home = self.client.get("/staff")
        self.assertEqual(home.status_code, 200)
        empty = home.get_data(as_text=True)
        self.assertIn("<span>Populate Class</span>", empty)
        self.assertIn(
            '<script type="module" src="/static/staff_home.js"></script>', empty
        )
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

        run = self.client.post(
            f"/staff/class/{class_id}/run-live",
            follow_redirects=False,
        )
        self.assertEqual(run.status_code, 302)
        blocked = self.client.put(
            f"/api/staff/classes/{class_id}/roster",
            json={"codenames": ["Maple", "Cedar", "Oak"]},
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("live class", blocked.get_json().get("error", "").lower())
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
        """Run Live Class mints a session; same-class rerun reuses it."""
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
        self.assertIn("tab=live", location)
        self.assertIn("live_session_id=", location)
        self.assertNotIn("tab=ap", location)
        session_one = self.school.get_active_live_session_for_class(class_id)
        self.assertIsNotNone(session_one)
        assert session_one is not None
        minted = str(session_one["session_code"])
        self.assertEqual(len(minted), 8)
        self.assertNotEqual(minted, self.offering["live_access_code"])
        reuse = self.client.post(
            f"/staff/class/{class_id}/run-live",
            follow_redirects=False,
        )
        self.assertEqual(reuse.status_code, 302)
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

    def test_run_live_tab_shows_join_code_billboard(self) -> None:
        """Active join code is in the Run Live Class tab, not only the overlay."""
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
        idle = self.client.get(f"/staff/class/{class_id}?tab=live").get_data(as_text=True)
        self.assertIn('id="ap-join-billboard"', idle)
        self.assertIn("Copy", idle)
        idle_open = idle.split('id="ap-join-billboard"', 1)[1].split(">", 1)[0]
        self.assertIn("hidden", idle_open)

        self.client.post(f"/staff/class/{class_id}/run-live")
        session = self.school.get_active_live_session_for_class(class_id)
        self.assertIsNotNone(session)
        assert session is not None
        code = str(session["session_code"])
        live = self.client.get(f"/staff/class/{class_id}?tab=live").get_data(as_text=True)
        self.assertIn("JOIN CODE", live)
        self.assertIn('id="ap-guest-on-chip"', live)
        self.assertIn("Guests on", live)
        self.assertIn(code, live)
        self.assertIn(f">{code}<", live)
        self.assertIn('id="ap-join-billboard-copy"', live)
        live_open = live.split('id="ap-join-billboard"', 1)[1].split(">", 1)[0]
        self.assertNotIn("hidden", live_open)

        self.school.end_live_class_session(int(session["id"]))
        ended = self.client.get(f"/staff/class/{class_id}?tab=live").get_data(as_text=True)
        self.assertNotIn(code, ended)
        ended_open = ended.split('id="ap-join-billboard"', 1)[1].split(">", 1)[0]
        self.assertIn("hidden", ended_open)

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

    def _seed_live_session_payload(self, class_id: int, session_id: int, codename: str) -> None:
        """Attach attendee, prompt, response, observation, and media to a session."""
        student = self.school.game.find_student_by_codename(class_id, codename)
        assert student is not None
        attendee = self.school.record_live_session_attendee(
            int(session_id),
            int(student["id"]),
            codename=codename,
        )
        self.school.set_live_session_slides(
            int(session_id),
            meeting_date="2026-09-09",
            presentation_id="deck-hung",
            presentation_url="https://example.test/deck",
            slides_json={"problems": [1]},
        )
        self.school.set_live_session_active_media(
            int(session_id),
            url="/static/mood/good.svg",
            title="Hung media",
        )
        prompt = self.school.set_live_session_prompt(
            int(session_id),
            slide_index=0,
            kind="mc",
            payload={"prompt": "2+2?"},
        )
        self.school.submit_live_prompt_response(
            int(prompt["id"]),
            student_id=int(student["id"]),
            response={"choice": "4"},
            participant_uuid=str(attendee["participant_uuid"]),
        )
        self.school.create_observation(
            live_session_id=int(session_id),
            class_id=int(class_id),
            observer_user_id=int(self.teacher["id"]),
            scope="class",
            note="Session evidence that must be wiped",
        )

    def _count_session_children(self, session_ids: list[int]) -> dict[str, int]:
        """Count live-session child rows still stored for the given ids."""
        if not session_ids:
            return {"attendees": 0, "prompts": 0, "responses": 0, "observations": 0}
        placeholders = ",".join("?" * len(session_ids))
        attendees = self.school.conn.execute(
            f"SELECT COUNT(*) FROM live_session_attendees WHERE live_session_id IN ({placeholders})",
            session_ids,
        ).fetchone()[0]
        prompts = self.school.conn.execute(
            f"SELECT COUNT(*) FROM live_session_prompts WHERE live_session_id IN ({placeholders})",
            session_ids,
        ).fetchone()[0]
        observations = self.school.conn.execute(
            f"SELECT COUNT(*) FROM observations WHERE live_session_id IN ({placeholders})",
            session_ids,
        ).fetchone()[0]
        responses = self.school.conn.execute(
            f"""
            SELECT COUNT(*) FROM live_session_responses
            WHERE prompt_id IN (
                SELECT id FROM live_session_prompts
                WHERE live_session_id IN ({placeholders})
            )
            """,
            session_ids,
        ).fetchone()[0]
        return {
            "attendees": int(attendees),
            "prompts": int(prompts),
            "responses": int(responses),
            "observations": int(observations),
        }

    def test_end_live_class_wipes_all_sessions_and_data(self) -> None:
        """End Live Class deletes every session for the class, not just status."""
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
        other = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]),
            ontario_code="MCF3M",
            new_section=True,
        )
        other_created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": other["id"],
                "days": "T/Th/F",
                "time": "2:00pm",
                "codenames": ["Birch"],
            },
        )
        other_class_id = other_created.get_json()["class"]["id"]

        self.client.post(f"/staff/class/{other_class_id}/run-live")
        other_live = self.school.get_active_live_session_for_class(other_class_id)
        assert other_live is not None
        other_id = int(other_live["id"])
        self._seed_live_session_payload(other_class_id, other_id, "Birch")
        self.school.end_live_class_session(other_id)
        self.assertIsNotNone(self.school.get_live_session(other_id))

        self.client.post(f"/staff/class/{class_id}/run-live")
        first = self.school.get_active_live_session_for_class(class_id)
        assert first is not None
        first_id = int(first["id"])
        self._seed_live_session_payload(class_id, first_id, "Maple")
        self.school.end_live_class_session(first_id)
        self.assertIsNotNone(self.school.get_live_session(first_id))

        self.client.post(f"/staff/class/{class_id}/run-live")
        second = self.school.get_active_live_session_for_class(class_id)
        assert second is not None
        second_id = int(second["id"])
        self.assertNotEqual(second_id, first_id)
        self._seed_live_session_payload(class_id, second_id, "Maple")
        before = self._count_session_children([first_id, second_id])
        self.assertGreater(before["attendees"], 0)
        self.assertGreater(before["prompts"], 0)
        self.assertGreater(before["responses"], 0)
        self.assertGreater(before["observations"], 0)
        self.assertEqual(len(self.school.list_live_sessions_for_class(class_id)), 2)

        self.school.register_staff("outsider@gmail.com")
        other_client = self.app.test_client()
        other_client.get("/auth/google?portal=staff")
        other_client.get("/auth/google/callback?email=outsider@gmail.com&name=O")
        outsider = self.school.get_user_by_email("outsider@gmail.com")
        assert outsider is not None
        other_client.post(
            "/verify-email",
            data={"code": outsider["verification_code"]},
        )
        denied = other_client.post(
            f"/staff/class/{class_id}/end-live",
            follow_redirects=False,
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(len(self.school.list_live_sessions_for_class(class_id)), 2)

        wiped = self.client.post(
            f"/staff/class/{class_id}/end-live",
            follow_redirects=False,
        )
        self.assertEqual(wiped.status_code, 302)
        self.assertIsNone(self.school.get_live_session(first_id))
        self.assertIsNone(self.school.get_live_session(second_id))
        self.assertIsNone(self.school.get_active_live_session_for_class(class_id))
        self.assertEqual(self.school.list_live_sessions_for_class(class_id), [])
        self.assertEqual(
            self._count_session_children([first_id, second_id]),
            {"attendees": 0, "prompts": 0, "responses": 0, "observations": 0},
        )
        maple = self.school.game.find_student_by_codename(class_id, "Maple")
        assert maple is not None
        self.assertIsNone(self.school.game.get_mood(class_id, int(maple["id"])))
        self.assertIsNotNone(self.school.get_live_session(other_id))
        self.assertGreater(
            self._count_session_children([other_id])["attendees"], 0
        )

        restart = self.client.post(
            f"/staff/class/{class_id}/run-live",
            follow_redirects=False,
        )
        self.assertEqual(restart.status_code, 302)
        fresh = self.school.get_active_live_session_for_class(class_id)
        self.assertIsNotNone(fresh)
        assert fresh is not None
        self.assertEqual(fresh["status"], "active")
        self.assertEqual(len(self.school.list_live_sessions_for_class(class_id)), 1)
        children = self._count_session_children([int(fresh["id"])])
        self.assertEqual(children["attendees"], 0)
        self.assertEqual(children["responses"], 0)
        self.assertEqual(children["observations"], 0)
        self.assertFalse(fresh.get("active_media_json"))

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

    def test_late_join_after_scoring_marks_l_and_assigns_team(self) -> None:
        """Mid-scoring join marks Late and places the student on a random team."""
        first = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple", "Birch", "Cedar", "Oak"],
            },
        )
        class_id = first.get_json()["class"]["id"]
        self.client.post(f"/staff/class/{class_id}/run-live")
        live = self.school.get_active_live_session_for_class(class_id)
        assert live is not None
        maple = self.school.game.find_student_by_codename(class_id, "Maple")
        birch = self.school.game.find_student_by_codename(class_id, "Birch")
        cedar = self.school.game.find_student_by_codename(class_id, "Cedar")
        oak = self.school.game.find_student_by_codename(class_id, "Oak")
        assert maple and birch and cedar and oak
        present_ids = [int(maple["id"]), int(birch["id"]), int(cedar["id"])]
        self.school.game.begin_game(
            class_id, meeting_date=__import__("datetime").date(2026, 9, 9)
        )
        self.school.game.save_attendance(class_id, present_ids)
        self.school.game.assign_teams(class_id, 2, "random")
        rename = self.school.game.game_state(class_id)
        self.school.game.rename_teams(
            class_id,
            [{"id": t["id"], "name": t["name"]} for t in rename["teams"]],
            go_live=False,
        )
        self.school.game.start_live_with_rounds(
            class_id,
            [{"kind": "open", "minutes": 20}],
        )
        before_teams = {
            int(m["id"]): int(t["id"])
            for t in self.school.game.game_state(class_id)["teams"]
            for m in t["members"]
        }
        self.assertNotIn(int(oak["id"]), before_teams)

        marked = self.school.game.admit_late_joiner(
            class_id, int(oak["id"]), rng=__import__("random").Random(0)
        )
        members = {
            int(m["id"]): int(t["id"])
            for t in marked["teams"]
            for m in t["members"]
        }
        self.assertIn(int(oak["id"]), members)
        oak_student = next(s for s in marked["students"] if int(s["id"]) == int(oak["id"]))
        self.assertTrue(oak_student["present"])
        self.assertTrue(oak_student["late"])

        grid = self.school.attendance_week_grid(class_id)
        cell = grid["cells"].get(f"{int(oak['id'])}:2026-09-09")
        self.assertEqual(cell, "L")

        # Idempotent on second call (already a member).
        again = self.school.game.admit_late_joiner(class_id, int(oak["id"]))
        self.assertIn(int(oak["id"]), {
            int(m["id"]) for t in again["teams"] for m in t["members"]
        })


if __name__ == "__main__":
    unittest.main(verbosity=2)

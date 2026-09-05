#!/usr/bin/env python3
"""Focused tests for Math Game Show CSV, teams, schedule, and game persist."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

from csv_import import parse_canvas_grades_csv, parse_student_name  # noqa: E402
from schedule import (  # noqa: E402
    format_header_label,
    heuristic_year_semester,
    next_meeting_datetime,
    parse_semester_field,
    picker_year_semester,
    unique_header_label,
    wizard_defaults,
)
from db import (  # noqa: E402
    DEFAULT_STAT_WINDOW,
    GameShowDB,
    as_points,
    leader_periods,
    member_round_points,
    normalize_rounds_config,
    normalize_stat_window,
    parse_codename_column_csv,
    points_label,
    roster_from_codenames,
    round_ends_at_ms,
    round_remaining_sec,
    split_amount,
    stat_window_label,
)
from teams import assign_balanced, assign_manual  # noqa: E402
import server as game_server  # noqa: E402

REAL_CSV = Path("/Users/shawnscomputer/Downloads/2026-08-31T1109_Grades-MCF3M-SM.csv")
FIXTURE_CSV = APP_DIR / "fixtures" / "sample-canvas-grades.csv"


class NameParseTests(unittest.TestCase):
    """Canvas Student column → last display + first name."""

    def test_comma_name(self) -> None:
        """Last, First splits on the comma."""
        last, first = parse_student_name("Axxx, Mubarik")
        self.assertEqual(last, "Axxx")
        self.assertEqual(first, "Mubarik")

    def test_spaces_around_comma(self) -> None:
        """Trim extra spaces in ``Sxxxxxx , Joy ``."""
        last, first = parse_student_name("Sxxxxxx , Joy ")
        self.assertEqual(last, "Sxxxxxx")
        self.assertEqual(first, "Joy")

    def test_zxx_hafsa_no_comma(self) -> None:
        """No comma: last token after the final space is the first name."""
        last, first = parse_student_name("Zxx Hafsa")
        self.assertEqual(last, "Zxx")
        self.assertEqual(first, "Hafsa")


class CsvImportTests(unittest.TestCase):
    """Canvas gradebook shape: header, posting row, Points Possible, students."""

    def test_fixture_seventeen_and_hafsa(self) -> None:
        """Sanitized fixture has 17 students and parses Zxx Hafsa."""
        roster = parse_canvas_grades_csv(FIXTURE_CSV.read_text(encoding="utf-8"))
        self.assertEqual(len(roster), 17)
        hafsa = next(s for s in roster if s["canvas_id"] == "90017")
        self.assertEqual(hafsa["last_display"], "Zxx")
        self.assertEqual(hafsa["first_name"], "Hafsa")
        joy = next(s for s in roster if s["canvas_id"] == "90012")
        self.assertEqual(joy["last_display"], "Sxxxxxx")
        self.assertEqual(joy["first_name"], "Joy")

    def test_real_export_if_present(self) -> None:
        """Downloads MCF3M export: 17 students, skip meta, parse Zxx Hafsa."""
        if not REAL_CSV.is_file():
            self.skipTest("real Canvas export not on this machine")
        roster = parse_canvas_grades_csv(REAL_CSV.read_text(encoding="utf-8"))
        self.assertEqual(len(roster), 17)
        hafsa = next(s for s in roster if "Hafsa" in s["first_name"] or s["student_raw"].endswith("Hafsa"))
        self.assertEqual(hafsa["last_display"], "Zxx")
        self.assertEqual(hafsa["first_name"], "Hafsa")
        self.assertTrue(all(s["canvas_id"] for s in roster))
        labels = {s["student_raw"].strip().lower() for s in roster}
        self.assertFalse(any("points possible" in x for x in labels))


class ScheduleTests(unittest.TestCase):
    """semester.json defaults and next-meeting labels."""

    def test_semester_json_defaults(self) -> None:
        """Wizard year/semester come from frameworks/semester.json."""
        year, sem = parse_semester_field("2026-2027 S1")
        self.assertEqual(year, "2026/27")
        self.assertEqual(sem, "Semester 1")
        defaults = wizard_defaults(today=date(2026, 8, 31))
        self.assertEqual(defaults["year"], "2026/27")
        self.assertEqual(defaults["semester"], "Semester 1")
        self.assertEqual(defaults["course_code"], "MCF3M")
        self.assertEqual(defaults["picker_year"], "2026/27")
        self.assertEqual(defaults["picker_semester"], "Semester 1")

    def test_heuristic_august_is_upcoming_s1(self) -> None:
        """Jul–Aug counts as upcoming Semester 1 so summer prep matches JSON."""
        self.assertEqual(
            heuristic_year_semester(date(2026, 8, 31)),
            ("2026/27", "Semester 1"),
        )
        self.assertEqual(
            picker_year_semester(date(2026, 8, 31)),
            ("2026/27", "Semester 1"),
        )

    def test_next_meeting_tue_sep_8(self) -> None:
        """T/Th/F @ 2:00pm before school starts → Tue 9/8 2:00pm."""
        when = next_meeting_datetime(
            "Tue/Thu/Fri",
            "2:00pm",
            today=date(2026, 8, 31),
        )
        self.assertEqual(when.date(), date(2026, 9, 8))
        self.assertEqual(format_header_label(when, "2:00pm"), "Tue 9/8 2:00pm")

    def test_unique_header_suffix(self) -> None:
        """Second play of the same slot becomes ``_2``, then ``_3``."""
        base = "Tue 9/8 2:00pm"
        self.assertEqual(unique_header_label(base, set()), base)
        self.assertEqual(unique_header_label(base, {base}), "Tue 9/8 2:00pm_2")
        self.assertEqual(
            unique_header_label(base, {base, "Tue 9/8 2:00pm_2"}),
            "Tue 9/8 2:00pm_3",
        )


class BalancedAssignTests(unittest.TestCase):
    """Balanced assignment keeps high TOTALs apart and tightens team sums."""

    def test_high_totals_split(self) -> None:
        """Top two career scores are not on the same team of two."""
        ids = [1, 2, 3, 4]
        totals = [100, 90, 10, 5]
        teams = assign_balanced(ids, 2, totals)
        self.assertEqual(len(teams), 2)
        high = {1, 2}
        self.assertNotEqual(high & set(teams[0]), high)
        self.assertTrue(1 in teams[0] or 1 in teams[1])
        self.assertTrue(2 in teams[0] or 2 in teams[1])
        team_of_1 = 0 if 1 in teams[0] else 1
        team_of_2 = 0 if 2 in teams[0] else 1
        self.assertNotEqual(team_of_1, team_of_2)

    def test_snake_turnaround_is_rebalanced(self) -> None:
        """Greedy + swaps beat a snake draft that stacks mid scores."""
        ids = [1, 2, 3, 4, 5, 6]
        totals = [100, 50, 50, 50, 40, 10]
        teams = assign_balanced(ids, 2, totals)
        self.assertEqual([len(team) for team in teams], [3, 3])
        sums = []
        for team in teams:
            sums.append(sum(totals[ids.index(sid)] for sid in team))
        self.assertLessEqual(max(sums) - min(sums), 10)

    def test_even_sizes_when_sums_already_tie(self) -> None:
        """A 2-vs-4 deal with equal sums is rejected in favor of 3 vs 3."""
        ids = [1, 2, 3, 4, 5, 6]
        totals = [100, 50, 50, 50, 40, 10]
        teams = assign_balanced(ids, 2, totals)
        sizes = sorted(len(team) for team in teams)
        self.assertEqual(sizes, [3, 3])

    def test_seven_on_three_sizes_off_by_one(self) -> None:
        """Seven students on three teams are 3-2-2, never 4-2-1."""
        ids = [1, 2, 3, 4, 5, 6, 7]
        totals = [90, 80, 40, 30, 20, 15, 10]
        teams = assign_balanced(ids, 3, totals)
        sizes = sorted(len(team) for team in teams)
        self.assertEqual(sizes, [2, 2, 3])
        self.assertEqual(set(sizes), {2, 3})


class ManualAssignTests(unittest.TestCase):
    """Teacher-chosen team slots."""

    def test_places_each_student(self) -> None:
        """Present students land on the requested teams; empty teams are rejected."""
        buckets = assign_manual(
            [10, 20, 30],
            2,
            [
                {"student_id": 10, "team_index": 0},
                {"student_id": 20, "team_index": 1},
                {"student_id": 30, "team_index": 0},
            ],
        )
        self.assertEqual(buckets, [[10, 30], [20]])
        with self.assertRaises(ValueError):
            assign_manual(
                [10, 20],
                2,
                [
                    {"student_id": 10, "team_index": 0},
                    {"student_id": 20, "team_index": 0},
                ],
            )


class GamePersistTests(unittest.TestCase):
    """Create class → dashboard colors/TOTAL → game → End Game log."""

    def setUp(self) -> None:
        """Temp data dir and one imported fixture class."""
        self.tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self.tmp.name)
        self.db = GameShowDB(data_dir / "app.sqlite", data_dir)
        csv_text = FIXTURE_CSV.read_text(encoding="utf-8")
        self.cls = self.db.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCF3M",
            days_preset="T/Th/F",
            time_label="2:00pm",
            csv_text=csv_text,
            today=date(2026, 8, 31),
        )

    def tearDown(self) -> None:
        """Close sqlite and drop the temp dir."""
        self.db.close()
        self.tmp.cleanup()

    def test_dashboard_template_red_zero(self) -> None:
        """New class: one date column, all absent/red, TOTAL 0."""
        dash = self.db.dashboard(self.cls["id"])
        self.assertEqual(len(dash["students"]), 17)
        self.assertEqual(len(dash["sessions"]), 1)
        self.assertEqual(dash["sessions"][0]["header_label"], "Tue 9/8 2:00pm")
        self.assertEqual(dash["sessions"][0]["status"], "template")
        for student in dash["students"]:
            cell = dash["cells"][f"{dash['sessions'][0]['id']}:{student['id']}"]
            self.assertFalse(cell["present"])
            self.assertEqual(cell["points"], 0)
            self.assertEqual(cell["points_r1"], 0)
            self.assertEqual(cell["points_r2"], 0)
            self.assertEqual(cell["points_r3"], 0)
            self.assertEqual(dash["totals"][str(student["id"])], 0)
            self.assertEqual(dash["live_subtotals"][str(student["id"])], 0)
        self.assertEqual(dash["columns"][0]["kind"], "session")
        listed = self.db.list_classes("2026/27", "Semester 1")
        self.assertTrue(any(c["id"] == self.cls["id"] for c in listed))

    def test_add_remove_student_and_column(self) -> None:
        """Teachers can append a roster row and a dated class column."""
        class_id = self.cls["id"]
        dash = self.db.add_student(class_id, "Ada", "Lovelace")
        self.assertEqual(len(dash["students"]), 18)
        ada = next(s for s in dash["students"] if s["first_name"] == "Ada")
        self.assertEqual(ada["last_display"], "Lovelace")
        first_session = dash["sessions"][0]["id"]
        self.assertFalse(dash["cells"][f"{first_session}:{ada['id']}"]["present"])
        dash = self.db.add_session_column(class_id, date(2026, 9, 10), "3:15pm")
        self.assertEqual(len(dash["sessions"]), 2)
        extra = next(s for s in dash["sessions"] if s["id"] != first_session)
        self.assertEqual(extra["header_label"], "Thu 9/10 3:15pm")
        self.assertEqual(extra["source"], "manual")
        dash = self.db.delete_session_column(class_id, extra["id"])
        self.assertEqual(len(dash["sessions"]), 1)
        dash = self.db.delete_student(class_id, ada["id"])
        self.assertEqual(len(dash["students"]), 17)
        with self.assertRaises(ValueError):
            self.db.add_student(class_id, "", "NoFirst")

    def test_freeze_subtotal_then_new_games_split_windows(self) -> None:
        """Frozen column keeps the old total; live SUBTOTAL only counts new games."""
        class_id = self.cls["id"]
        state = self.db.begin_game(class_id, today=date(2026, 8, 31))
        present = [s["id"] for s in state["students"][:4]]
        scorer = present[0]
        self.db.save_attendance(class_id, present)
        self.db.assign_teams(class_id, 2, "random")
        teams = self.db.game_state(class_id)["teams"]
        self.db.rename_teams(class_id, [{"id": t["id"], "name": t["name"]} for t in teams])
        self.db.award_points(class_id, kind="student", target_id=scorer, amount=10)
        self.db.end_game(class_id)
        dash = self.db.freeze_subtotal(class_id, name="Term 1")
        frozen_cols = [c for c in dash["columns"] if c["kind"] == "subtotal"]
        self.assertEqual(len(frozen_cols), 1)
        self.assertEqual(frozen_cols[0]["name"], "Term 1")
        self.assertEqual(dash["totals"][str(scorer)], 10)
        self.assertEqual(dash["live_subtotals"][str(scorer)], 0)
        self.assertEqual(
            dash["cells"][f"sub:{frozen_cols[0]['id']}:{scorer}"]["points"], 10
        )
        dash = self.db.rename_subtotal(class_id, frozen_cols[0]["id"], "Q1 freeze")
        self.assertEqual(
            next(c for c in dash["columns"] if c["kind"] == "subtotal")["name"],
            "Q1 freeze",
        )
        self.db.begin_game(class_id, today=date(2026, 8, 31))
        self.db.save_attendance(class_id, present)
        self.db.assign_teams(class_id, 2, "random")
        teams = self.db.game_state(class_id)["teams"]
        self.db.rename_teams(class_id, [{"id": t["id"], "name": t["name"]} for t in teams])
        self.db.award_points(class_id, kind="student", target_id=scorer, amount=5)
        self.db.end_game(class_id)
        dash = self.db.dashboard(class_id)
        self.assertEqual(dash["totals"][str(scorer)], 15)
        self.assertEqual(dash["live_subtotals"][str(scorer)], 5)
        self.assertEqual(
            dash["cells"][f"sub:{frozen_cols[0]['id']}:{scorer}"]["points"], 10
        )
        kinds = [c["kind"] for c in dash["columns"]]
        self.assertEqual(kinds, ["session", "subtotal", "session"])

    def test_delete_subtotal_column_restores_live_window(self) -> None:
        """Removing a freeze drops the snapshot; live SUBTOTAL recounts those lessons."""
        class_id = self.cls["id"]
        state = self.db.begin_game(class_id, today=date(2026, 8, 31))
        present = [s["id"] for s in state["students"][:4]]
        scorer = present[0]
        self.db.save_attendance(class_id, present)
        self.db.assign_teams(class_id, 2, "random")
        teams = self.db.game_state(class_id)["teams"]
        self.db.rename_teams(class_id, [{"id": t["id"], "name": t["name"]} for t in teams])
        self.db.award_points(class_id, kind="student", target_id=scorer, amount=10)
        self.db.end_game(class_id)
        dash = self.db.freeze_subtotal(class_id, name="Term 1")
        frozen = next(c for c in dash["columns"] if c["kind"] == "subtotal")
        self.assertEqual(dash["live_subtotals"][str(scorer)], 0)
        dash = self.db.delete_subtotal(class_id, frozen["id"])
        self.assertFalse(any(c["kind"] == "subtotal" for c in dash["columns"]))
        self.assertNotIn(f"sub:{frozen['id']}:{scorer}", dash["cells"])
        self.assertEqual(dash["live_subtotals"][str(scorer)], 10)
        self.assertEqual(dash["totals"][str(scorer)], 10)
        with self.assertRaises(KeyError):
            self.db.delete_subtotal(class_id, frozen["id"])

    def test_new_game_after_freeze_stays_right_of_subtotal(self) -> None:
        """A new game column sits after a freeze even if its calendar date is earlier."""
        class_id = self.cls["id"]
        state = self.db.begin_game(class_id, today=date(2026, 8, 31))
        present = [s["id"] for s in state["students"][:4]]
        scorer = present[0]
        self.db.save_attendance(class_id, present)
        self.db.assign_teams(class_id, 2, "random")
        teams = self.db.game_state(class_id)["teams"]
        self.db.rename_teams(class_id, [{"id": t["id"], "name": t["name"]} for t in teams])
        self.db.award_points(class_id, kind="student", target_id=scorer, amount=10)
        self.db.end_game(class_id)
        later = self.db.add_session_column(class_id, date(2026, 9, 22), "2:00pm")
        later_id = next(s["id"] for s in later["sessions"] if "9/22" in s["header_label"])
        dash = self.db.freeze_subtotal(class_id, name="Term 1")
        frozen = next(c for c in dash["columns"] if c["kind"] == "subtotal")
        self.db.begin_game(class_id, today=date(2026, 8, 31))
        self.db.save_attendance(class_id, present)
        self.db.assign_teams(class_id, 2, "random")
        teams = self.db.game_state(class_id)["teams"]
        self.db.rename_teams(class_id, [{"id": t["id"], "name": t["name"]} for t in teams])
        self.db.award_points(class_id, kind="student", target_id=scorer, amount=5)
        self.db.end_game(class_id)
        dash = self.db.dashboard(class_id)
        kinds = [c["kind"] for c in dash["columns"]]
        self.assertEqual(kinds[-2:], ["subtotal", "session"])
        self.assertEqual(dash["cells"][f"sub:{frozen['id']}:{scorer}"]["points"], 10)
        self.assertEqual(dash["live_subtotals"][str(scorer)], 5)
        self.assertEqual(dash["totals"][str(scorer)], 15)
        new_session = dash["columns"][-1]
        self.assertEqual(new_session["kind"], "session")
        self.assertNotEqual(new_session["id"], later_id)

    def test_game_scoring_end_log_and_colors(self) -> None:
        """4 present, 2 random teams, individual+team awards, End Game persist."""
        class_id = self.cls["id"]
        state = self.db.begin_game(class_id, today=date(2026, 8, 31))
        self.assertEqual(state["game"]["status"], "attendance")
        present = [s["id"] for s in state["students"][:4]]
        absent_ids = [s["id"] for s in state["students"][4:]]
        state = self.db.save_attendance(class_id, present)
        state = self.db.assign_teams(class_id, 2, "random")
        self.assertEqual(len(state["teams"]), 2)
        names = [{"id": t["id"], "name": t["name"]} for t in state["teams"]]
        state = self.db.rename_teams(class_id, names)
        self.assertEqual(state["game"]["status"], "live")
        member = state["teams"][0]["members"][0]
        team = state["teams"][0]
        state = self.db.award_points(class_id, kind="student", target_id=member["id"], amount=5)
        self.assertTrue(state["game"]["last_event"]["celebrate"])
        self.assertEqual(state["game"]["last_event"]["first_name"], member["first_name"])
        state = self.db.award_points(
            class_id,
            kind="team",
            target_id=team["id"],
            amount=10,
            team_rule="team_only",
        )
        board = self.db.scoreboard(class_id)
        self.assertTrue(board["live"])
        scored = next(t for t in board["teams"] if t["id"] == team["id"])
        self.assertEqual(scored["score"], 15)
        self.assertTrue(board["last_event"]["celebrate"])
        self.assertEqual(board["last_event"]["caption"], "Small Team Bonus +10")
        ended = self.db.end_game(class_id)
        self.assertTrue(Path(ended["log_path"]).is_file())
        log_lines = Path(ended["log_path"]).read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(log_lines), 2)
        rec = json.loads(log_lines[0])
        self.assertEqual(rec["from"], "teacher")
        self.assertEqual(rec["amount"], 5)
        dash = self.db.dashboard(class_id)
        session = dash["sessions"][0]
        self.assertEqual(session["status"], "ended")
        self.assertTrue(session["log_path"])
        present_cell = dash["cells"][f"{session['id']}:{member['id']}"]
        self.assertTrue(present_cell["present"])
        self.assertEqual(present_cell["points"], 5)
        absent_cell = dash["cells"][f"{session['id']}:{absent_ids[0]}"]
        self.assertFalse(absent_cell["present"])
        self.assertEqual(absent_cell["points"], 0)
        self.assertEqual(dash["totals"][str(member["id"])], 5)
        final = self.db.scoreboard(class_id)
        self.assertFalse(final["live"])
        self.assertTrue(final["final"])
        self.assertEqual(final["status"], "ended")
        self.assertEqual(len(final["teams"]), 2)
        self.assertEqual(sum(t["score"] for t in final["teams"]), 15)
        for team in final["teams"]:
            self.assertNotIn("members", team)
            self.assertTrue(team["players"])
            for player in team["players"]:
                self.assertIn("first_name", player)
                self.assertTrue(player["first_name"])
                self.assertIn("student_id", player)
                self.assertGreater(int(player["student_id"]), 0)
                self.assertEqual(set(player.keys()), {"first_name", "student_id"})
        winner = max(final["teams"], key=lambda t: t["score"])
        self.assertIn(member["first_name"], [p["first_name"] for p in winner["players"]])

    def test_final_scoreboard_clears_when_idle(self) -> None:
        """Final Score stays until Begin / Quit; leftover ended games stay hidden."""
        class_id = self.cls["id"]
        state = self.db.begin_game(class_id, today=date(2026, 8, 31))
        present = [s["id"] for s in state["students"][:4]]
        self.db.save_attendance(class_id, present)
        self.db.assign_teams(class_id, 2, "random")
        teams = self.db.game_state(class_id)["teams"]
        self.db.rename_teams(class_id, [{"id": t["id"], "name": t["name"]} for t in teams])
        member = self.db.game_state(class_id)["teams"][0]["members"][0]
        self.db.award_points(class_id, kind="student", target_id=member["id"], amount=5)
        self.db.end_game(class_id)
        ended = self.db.scoreboard()
        self.assertTrue(ended["final"])
        self.assertFalse(ended["live"])
        self.assertTrue(any(t["score"] == 5 for t in ended["teams"]))
        self.db.begin_game(class_id, today=date(2026, 8, 31))
        board = self.db.scoreboard()
        self.assertFalse(board["live"])
        self.assertFalse(board["final"])
        self.assertEqual(board["teams"], [])
        with self.db._lock:
            self.db._set_scoreboard_game(None)
            self.db.conn.commit()
        leftover = self.db.scoreboard()
        self.assertFalse(leftover["live"])
        self.assertFalse(leftover["final"])
        self.assertEqual(leftover["teams"], [])

    def test_scoreboard_follows_open_dashboard(self) -> None:
        """A leftover live game on another class does not take the board."""
        csv_text = FIXTURE_CSV.read_text(encoding="utf-8")
        other = self.db.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCF3M",
            days_preset="M/W/F",
            time_label="8:00am",
            csv_text=csv_text,
            today=date(2026, 8, 31),
        )
        first_id = self.cls["id"]
        self.db.begin_game(first_id, today=date(2026, 8, 31))
        present = [s["id"] for s in self.db.game_state(first_id)["students"][:4]]
        self.db.save_attendance(first_id, present)
        self.db.assign_teams(first_id, 2, "random")
        teams = self.db.game_state(first_id)["teams"]
        self.db.rename_teams(first_id, [{"id": t["id"], "name": t["name"]} for t in teams])
        live = self.db.scoreboard()
        self.assertTrue(live["live"])
        self.assertEqual(live["class_id"], first_id)
        self.db.dashboard(other["id"])
        board = self.db.scoreboard()
        self.assertFalse(board["live"])
        self.assertFalse(board["final"])
        self.assertEqual(board["teams"], [])
        self.assertEqual(board["class_id"], other["id"])

    def test_balanced_uses_career_total(self) -> None:
        """After one ended session, balanced assignment splits high TOTALs."""
        class_id = self.cls["id"]
        state = self.db.begin_game(class_id)
        four = [s["id"] for s in state["students"][:4]]
        self.db.save_attendance(class_id, four)
        self.db.assign_teams(class_id, 2, "random")
        teams = self.db.game_state(class_id)["teams"]
        self.db.rename_teams(class_id, [{"id": t["id"], "name": t["name"]} for t in teams])
        live = self.db.game_state(class_id)
        a = live["teams"][0]["members"][0]["id"]
        b = live["teams"][1]["members"][0]["id"]
        self.db.award_points(class_id, kind="student", target_id=a, amount=50)
        self.db.award_points(class_id, kind="student", target_id=b, amount=40)
        self.db.end_game(class_id)
        # Next meeting column (Thu 9/10) then balanced on the same four.
        state = self.db.begin_game(class_id, today=date(2026, 9, 10))
        self.assertEqual(state["session"]["header_label"], "Thu 9/10 2:00pm")
        self.db.save_attendance(class_id, four)
        state = self.db.assign_teams(class_id, 2, "balanced")
        team_of = {}
        for team in state["teams"]:
            for member in team["members"]:
                team_of[member["id"]] = team["id"]
        self.assertNotEqual(team_of[a], team_of[b])

    def test_same_slot_suffix_not_next_day(self) -> None:
        """A second game on the same calendar slot is ``_2``, not the next day."""
        class_id = self.cls["id"]
        state = self.db.begin_game(class_id, today=date(2026, 8, 31))
        present = [s["id"] for s in state["students"][:4]]
        self.db.save_attendance(class_id, present)
        self.db.assign_teams(class_id, 2, "random")
        teams = self.db.game_state(class_id)["teams"]
        self.db.rename_teams(class_id, [{"id": t["id"], "name": t["name"]} for t in teams])
        self.db.end_game(class_id)
        again = self.db.begin_game(class_id, today=date(2026, 8, 31))
        self.assertEqual(again["session"]["header_label"], "Tue 9/8 2:00pm_2")
        headers = [s["header_label"] for s in self.db.dashboard(class_id)["sessions"]]
        self.assertEqual(headers, ["Tue 9/8 2:00pm", "Tue 9/8 2:00pm_2"])

    def test_cancel_setup_restores_template(self) -> None:
        """Cancel before Create Teams drops the in-progress game."""
        class_id = self.cls["id"]
        self.db.begin_game(class_id, today=date(2026, 8, 31))
        self.db.cancel_setup(class_id)
        dash = self.db.dashboard(class_id)
        self.assertEqual(len(dash["sessions"]), 1)
        self.assertEqual(dash["sessions"][0]["status"], "template")
        self.assertEqual(dash["sessions"][0]["header_label"], "Tue 9/8 2:00pm")
        self.assertIsNone(dash["open_game"])

    def test_finalize_attendance_only_keeps_column(self) -> None:
        """Done path ends the session with present marks and no open game."""
        class_id = self.cls["id"]
        state = self.db.begin_game(class_id, today=date(2026, 8, 31))
        present = [s["id"] for s in state["students"][:2]]
        absent = [s["id"] for s in state["students"][2:3]]
        result = self.db.finalize_attendance_only(class_id, present)
        self.assertTrue(result["ok"])
        dash = self.db.dashboard(class_id)
        self.assertIsNone(dash["open_game"])
        ended = [s for s in dash["sessions"] if s["status"] == "ended"]
        self.assertEqual(len(ended), 1)
        session_id = ended[0]["id"]
        for sid in present:
            cell = dash["cells"][f"{session_id}:{sid}"]
            self.assertTrue(cell["present"])
            self.assertEqual(cell["points"], 0)
        for sid in absent:
            cell = dash["cells"][f"{session_id}:{sid}"]
            self.assertFalse(cell["present"])

    def test_quit_live_game_discards_scores(self) -> None:
        """Quit Game drops a live session so nothing is written to the sheet."""
        class_id = self.cls["id"]
        state = self.db.begin_game(class_id, today=date(2026, 8, 31))
        present = [s["id"] for s in state["students"][:4]]
        self.db.save_attendance(class_id, present)
        self.db.assign_teams(class_id, 2, "random")
        teams = self.db.game_state(class_id)["teams"]
        self.db.rename_teams(class_id, [{"id": t["id"], "name": t["name"]} for t in teams])
        member = self.db.game_state(class_id)["teams"][0]["members"][0]
        self.db.award_points(class_id, kind="student", target_id=member["id"], amount=5)
        self.db.cancel_setup(class_id)
        dash = self.db.dashboard(class_id)
        self.assertIsNone(dash["open_game"])
        self.assertEqual(len(dash["sessions"]), 1)
        self.assertEqual(dash["sessions"][0]["status"], "template")
        cell = dash["cells"][f"{dash['sessions'][0]['id']}:{member['id']}"]
        self.assertEqual(cell["points"], 0)
        self.assertEqual(dash["totals"][str(member["id"])], 0)

    def test_begin_again_discards_unfinished_setup(self) -> None:
        """A second Begin a New Game starts at attendance, not Create Teams."""
        class_id = self.cls["id"]
        state = self.db.begin_game(class_id, today=date(2026, 8, 31))
        present = [s["id"] for s in state["students"][:4]]
        self.db.save_attendance(class_id, present)
        self.db.assign_teams(class_id, 2, "random")
        self.assertEqual(self.db.game_state(class_id)["game"]["status"], "names")
        again = self.db.begin_game(class_id, today=date(2026, 8, 31))
        self.assertEqual(again["game"]["status"], "attendance")
        self.assertEqual(again["session"]["header_label"], "Tue 9/8 2:00pm")
        dash = self.db.dashboard(class_id)
        self.assertEqual(dash["open_game"]["status"], "attendance")

    def test_begin_resumes_live_and_blocks_delete(self) -> None:
        """A live game is resumed; its column cannot be deleted."""
        class_id = self.cls["id"]
        state = self.db.begin_game(class_id, today=date(2026, 8, 31))
        present = [s["id"] for s in state["students"][:4]]
        self.db.save_attendance(class_id, present)
        self.db.assign_teams(class_id, 2, "random")
        teams = self.db.game_state(class_id)["teams"]
        self.db.rename_teams(class_id, [{"id": t["id"], "name": t["name"]} for t in teams])
        session_id = int(state["session"]["id"])
        again = self.db.begin_game(class_id, today=date(2026, 8, 31))
        self.assertEqual(again["game"]["status"], "live")
        with self.assertRaises(ValueError):
            self.db.delete_session_column(class_id, session_id)

    def test_delete_column_during_setup(self) -> None:
        """Deleting the in-progress setup column discards the leftover game."""
        class_id = self.cls["id"]
        state = self.db.begin_game(class_id, today=date(2026, 8, 31))
        present = [s["id"] for s in state["students"][:4]]
        self.db.save_attendance(class_id, present)
        self.db.assign_teams(class_id, 2, "random")
        session_id = int(state["session"]["id"])
        dash = self.db.delete_session_column(class_id, session_id)
        self.assertIsNone(dash["open_game"])
        self.assertEqual(len(dash["sessions"]), 0)

    def test_manual_date_override(self) -> None:
        """Teacher can point the setup session at another calendar day."""
        class_id = self.cls["id"]
        self.db.begin_game(class_id, today=date(2026, 8, 31))
        state = self.db.set_meeting_date(
            class_id, date(2026, 9, 10), today=date(2026, 8, 31)
        )
        self.assertEqual(state["session"]["header_label"], "Thu 9/10 2:00pm")
        self.assertEqual(state["session"]["meeting_date"], "2026-09-10")
        state = self.db.set_meeting_date(
            class_id, date(2026, 9, 10), time_label="3:15pm", today=date(2026, 8, 31)
        )
        self.assertEqual(state["session"]["header_label"], "Thu 9/10 3:15pm")
        self.assertEqual(state["session"]["time"], "3:15pm")
        with self.assertRaises(ValueError):
            self.db.set_meeting_date(
                class_id, date(2026, 8, 1), today=date(2026, 8, 31)
            )

    def test_team_credit_rules(self) -> None:
        """each_member, split_members, and team_only credit individuals differently."""
        self.assertEqual(split_amount(10, 3), [3.3, 3.3, 3.3])
        class_id = self.cls["id"]
        state = self.db.begin_game(class_id, today=date(2026, 8, 31))
        present = [s["id"] for s in state["students"][:4]]
        self.db.save_attendance(class_id, present)
        self.db.assign_teams(class_id, 2, "random")
        teams = self.db.game_state(class_id)["teams"]
        self.db.rename_teams(class_id, [{"id": t["id"], "name": t["name"]} for t in teams])
        live = self.db.game_state(class_id)
        team = next(t for t in live["teams"] if len(t["members"]) >= 2)
        n = len(team["members"])
        before = {m["id"]: m["session_points"] for m in team["members"]}
        state = self.db.award_points(
            class_id, kind="team", target_id=team["id"], amount=10, team_rule="each_member"
        )
        scored = next(t for t in state["teams"] if t["id"] == team["id"])
        for member in scored["members"]:
            self.assertEqual(member["session_points"], before[member["id"]] + 10)
        self.assertEqual(scored["bucket"], 0)
        self.assertEqual(scored["score"], scored["individual_sum"])
        self.assertEqual(scored["individual_sum"], 10 * n)

        self.db.end_game(class_id)
        state = self.db.begin_game(class_id, today=date(2026, 8, 31))
        self.db.save_attendance(class_id, present)
        self.db.assign_teams(class_id, 2, "random")
        teams = self.db.game_state(class_id)["teams"]
        self.db.rename_teams(class_id, [{"id": t["id"], "name": t["name"]} for t in teams])
        live = self.db.game_state(class_id)
        team = next(t for t in live["teams"] if len(t["members"]) >= 2)
        n = len(team["members"])
        state = self.db.award_points(
            class_id, kind="team", target_id=team["id"], amount=10, team_rule="split_members"
        )
        scored = next(t for t in state["teams"] if t["id"] == team["id"])
        credits = [m["session_points"] for m in scored["members"]]
        share = round(10 / n, 1)
        self.assertTrue(all(c == share for c in credits))
        leftover = round(10 - share * n, 1)
        self.assertEqual(scored["bucket"], leftover)
        self.assertEqual(scored["individual_sum"], round(share * n, 1))
        self.assertEqual(scored["score"], 10)

        state = self.db.award_points(
            class_id, kind="team", target_id=team["id"], amount=7, team_rule="team_only"
        )
        scored = next(t for t in state["teams"] if t["id"] == team["id"])
        self.assertEqual(scored["bucket"], leftover + 7)
        self.assertEqual(
            sum(m["session_points"] for m in scored["members"]),
            round(share * n, 1),
        )
        self.assertEqual(scored["score"], 17)
        dash = self.db.dashboard(class_id)
        # Still live; credited cells already show split points, not the team-only 7.
        session_id = state["session"]["id"]
        member_id = scored["members"][0]["id"]
        self.assertEqual(
            dash["cells"][f"{session_id}:{member_id}"]["points"],
            scored["members"][0]["session_points"],
        )
        state = self.db.award_points(
            class_id, kind="team", target_id=team["id"], amount=-5, team_rule="team_only"
        )
        scored = next(t for t in state["teams"] if t["id"] == team["id"])
        self.assertEqual(scored["bucket"], leftover + 7 - 5)
        self.assertEqual(scored["score"], 12)
        self.assertEqual(
            sum(m["session_points"] for m in scored["members"]),
            round(share * n, 1),
        )


    def test_split_three_keeps_board_score(self) -> None:
        """10 split three ways is 3.3 each; ESPN team total stays 10."""
        class_id = self.cls["id"]
        state = self.db.begin_game(class_id, today=date(2026, 8, 31))
        present = [s["id"] for s in state["students"][:4]]
        self.db.save_attendance(class_id, present)
        self.db.assign_teams(
            class_id,
            2,
            "manual",
            assignments=[
                {"student_id": present[0], "team_index": 0},
                {"student_id": present[1], "team_index": 0},
                {"student_id": present[2], "team_index": 0},
                {"student_id": present[3], "team_index": 1},
            ],
        )
        teams = self.db.game_state(class_id)["teams"]
        self.db.rename_teams(class_id, [{"id": t["id"], "name": t["name"]} for t in teams])
        live = self.db.game_state(class_id)
        trio = next(t for t in live["teams"] if len(t["members"]) == 3)
        state = self.db.award_points(
            class_id, kind="team", target_id=trio["id"], amount=10, team_rule="split_members"
        )
        scored = next(t for t in state["teams"] if t["id"] == trio["id"])
        self.assertEqual([m["session_points"] for m in scored["members"]], [3.3, 3.3, 3.3])
        self.assertEqual(scored["individual_sum"], 9.9)
        self.assertEqual(scored["bucket"], 0.1)
        self.assertEqual(scored["score"], 10)
        board = self.db.scoreboard(class_id)
        shown = next(t for t in board["teams"] if t["id"] == trio["id"])
        self.assertEqual(shown["score"], 10)

    def _go_live(self, n_present: int = 4) -> dict:
        """Mark attendance, assign teams, and Create Teams.

        Args:
            n_present: How many roster students to mark present.

        Returns:
            Live ``game_state`` payload.
        """
        class_id = self.cls["id"]
        state = self.db.begin_game(class_id, today=date(2026, 8, 31))
        present = [s["id"] for s in state["students"][:n_present]]
        self.db.save_attendance(class_id, present)
        self.db.assign_teams(class_id, 2, "random")
        teams = self.db.game_state(class_id)["teams"]
        return self.db.rename_teams(
            class_id, [{"id": t["id"], "name": t["name"]} for t in teams]
        )

    def test_live_game_starts_round_1_timer(self) -> None:
        """Create Teams starts Round 1 with a 20:00 clock."""
        state = self._go_live()
        game = state["game"]
        self.assertEqual(game["status"], "live")
        self.assertEqual(game["round"], 1)
        self.assertEqual(game["round_title"], "Open Question")
        self.assertGreaterEqual(game["round_remaining_sec"], 1190)
        self.assertLessEqual(game["round_remaining_sec"], 1200)
        self.assertTrue(game["round_ends_at"])
        self.assertIsInstance(game["round_ends_at_ms"], int)
        member = state["teams"][0]["members"][0]
        self.assertEqual(member["session_points"], 0)
        self.assertEqual(member["points_r1"], 0)
        self.assertEqual(member["points_r2"], 0)
        self.assertEqual(member["points_r3"], 0)
        board = self.db.scoreboard()
        self.assertEqual(board["round"], 1)
        self.assertEqual(board["round_title"], "Open Question")
        self.assertGreaterEqual(board["round_remaining_sec"], 1190)

    def test_meet_teams_and_anticipation_overlay_phases(self) -> None:
        """Meet the Teams and anticipation expose overlay timer fields before live."""
        class_id = self.cls["id"]
        state = self.db.begin_game(class_id, today=date(2026, 8, 31))
        present = [s["id"] for s in state["students"][:4]]
        self.db.save_attendance(class_id, present)
        self.db.assign_teams(class_id, 2, "random")
        meet = self.db.start_meet_teams(class_id, minutes=3)
        self.assertEqual(meet["game"]["overlay_phase"], "meet_teams")
        self.assertEqual(meet["game"]["round_title"], "Meet the Teams")
        self.assertIsInstance(meet["game"]["round_ends_at_ms"], int)
        self.assertGreaterEqual(meet["game"]["round_remaining_sec"], 170)
        board = self.db.scoreboard()
        self.assertTrue(board["live"])
        self.assertEqual(board["overlay_phase"], "meet_teams")
        self.assertEqual(board["round_title"], "Meet the Teams")
        self.assertGreaterEqual(len(board["teams"]), 2)
        teams = meet["teams"]
        self.db.rename_teams(
            class_id,
            [{"id": t["id"], "name": t["name"]} for t in teams],
            go_live=False,
        )
        anti = self.db.start_anticipation(class_id)
        self.assertEqual(anti["game"]["overlay_phase"], "anticipation")
        self.assertEqual(anti["game"]["status"], "rounds")
        anti_board = self.db.scoreboard()
        self.assertEqual(anti_board["overlay_phase"], "anticipation")
        live = self.db.start_live_with_rounds(class_id)
        self.assertEqual(live["game"]["status"], "live")
        self.assertIsNone(live["game"]["overlay_phase"])
        self.assertEqual(live["game"]["round_title"], "Open Question")

    def test_add_late_student_to_live_team(self) -> None:
        """An absent roster student can join a live team at zero points."""
        state = self._go_live(n_present=4)
        class_id = self.cls["id"]
        absent = next(s for s in state["students"] if not s["present"])
        team = state["teams"][0]
        before = team["score"]
        before_n = len(team["members"])
        updated = self.db.add_late_student(class_id, absent["id"], team["id"])
        self.assertIn(absent["id"], updated["present_ids"])
        joined = next(t for t in updated["teams"] if t["id"] == team["id"])
        late = next(m for m in joined["members"] if m["id"] == absent["id"])
        self.assertEqual(len(joined["members"]), before_n + 1)
        self.assertEqual(late["session_points"], 0)
        self.assertEqual(late["points_r1"], 0)
        self.assertEqual(late["points_r2"], 0)
        self.assertEqual(late["points_r3"], 0)
        self.assertEqual(joined["score"], before)
        scored = self.db.award_points(
            class_id, kind="student", target_id=absent["id"], amount=1
        )
        after = next(t for t in scored["teams"] if t["id"] == team["id"])
        self.assertEqual(after["score"], before + 1)
        with self.assertRaises(ValueError):
            self.db.add_late_student(class_id, absent["id"], team["id"])
        already = state["teams"][0]["members"][0]["id"]
        with self.assertRaises(ValueError):
            self.db.add_late_student(class_id, already, state["teams"][1]["id"])

    def test_cannot_add_late_student_before_live(self) -> None:
        """Late add is only for the Teacher Game Dashboard (live)."""
        class_id = self.cls["id"]
        state = self.db.begin_game(class_id, today=date(2026, 8, 31))
        absent = next(s for s in state["students"] if not s.get("present"))
        with self.assertRaises(ValueError):
            self.db.add_late_student(class_id, absent["id"], 1)

    def test_cannot_skip_to_round_3(self) -> None:
        """Only the next round can be started."""
        self._go_live()
        class_id = self.cls["id"]
        with self.assertRaises(ValueError):
            self.db.start_round(class_id, 3)
        with self.assertRaises(ValueError):
            self.db.start_round(class_id, 1)
        state = self.db.start_round(class_id, 2)
        self.assertEqual(state["game"]["round"], 2)
        self.assertEqual(state["game"]["round_title"], "Team Challenge")
        self.assertGreaterEqual(state["game"]["round_remaining_sec"], 590)
        self.assertLessEqual(state["game"]["round_remaining_sec"], 600)
        third = self.db.start_round(class_id, 3)
        self.assertEqual(third["game"]["round"], 3)
        self.assertEqual(third["game"]["round_title"], "Formative")
        with self.assertRaises(ValueError):
            self.db.start_round(class_id, 2)

    def test_normalize_rounds_allows_duplicates_and_break(self) -> None:
        """Sequential plans may repeat kinds and include titled Breaks."""
        plan = normalize_rounds_config(
            [
                {"kind": "open", "minutes": 5},
                {"kind": "open", "minutes": 8},
                {"kind": "break", "minutes": 3, "title": "Snack"},
            ]
        )
        self.assertEqual(len(plan), 3)
        self.assertEqual(plan[0]["bucket"], 1)
        self.assertEqual(plan[1]["bucket"], 1)
        self.assertIsNone(plan[2]["bucket"])
        self.assertEqual(plan[2]["title"], "Snack")
        with self.assertRaises(ValueError):
            normalize_rounds_config([{"kind": "break", "minutes": 3}])

    def test_append_round_duplicate_open_shares_r1_bucket(self) -> None:
        """Two Open Question rounds both credit points_r1; Break rejects awards."""
        class_id = self.cls["id"]
        state = self.db.begin_game(class_id, today=date(2026, 8, 31))
        present = [s["id"] for s in state["students"][:4]]
        self.db.save_attendance(class_id, present)
        self.db.assign_teams(class_id, 2, "random")
        teams = self.db.game_state(class_id)["teams"]
        self.db.rename_teams(
            class_id,
            [{"id": t["id"], "name": t["name"]} for t in teams],
            go_live=False,
        )
        live = self.db.start_live_with_rounds(
            class_id, [{"kind": "open", "minutes": 10}]
        )
        self.assertEqual(live["game"]["round"], 1)
        self.assertEqual(live["game"]["round_count"], 1)
        member = live["teams"][0]["members"][0]
        self.db.award_points(class_id, kind="student", target_id=member["id"], amount=4)
        nxt = self.db.append_and_start_round(
            class_id, {"kind": "open", "minutes": 5}
        )
        self.assertEqual(nxt["game"]["round"], 2)
        self.assertEqual(nxt["game"]["round_title"], "Open Question")
        self.assertEqual(nxt["game"]["round_count"], 2)
        self.db.award_points(class_id, kind="student", target_id=member["id"], amount=3)
        phone = self.db.student_live_payload(class_id, member["id"])
        self.assertEqual(phone["round_label"], "Round 2 · Open Question")
        scored = self.db.game_state(class_id)
        member_live = next(
            m for t in scored["teams"] for m in t["members"] if m["id"] == member["id"]
        )
        self.assertEqual(member_live["points_r1"], 7)
        self.assertEqual(member_live["session_points"], 7)
        brk = self.db.append_and_start_round(
            class_id, {"kind": "break", "minutes": 2, "title": "Water"}
        )
        self.assertEqual(brk["game"]["round_title"], "Water")
        self.assertEqual(brk["game"]["round_kind"], "break")
        with self.assertRaises(ValueError):
            self.db.award_points(
                class_id, kind="student", target_id=member["id"], amount=1
            )

    def test_r2_awards_do_not_change_r1(self) -> None:
        """New awards tag the current round; the lesson total is the sum."""
        state = self._go_live()
        class_id = self.cls["id"]
        member = state["teams"][0]["members"][0]
        self.db.award_points(class_id, kind="student", target_id=member["id"], amount=5)
        self.db.start_round(class_id, 2)
        self.db.award_points(class_id, kind="student", target_id=member["id"], amount=3)
        live = self.db.game_state(class_id)
        member_live = next(
            m for t in live["teams"] for m in t["members"] if m["id"] == member["id"]
        )
        self.assertEqual(member_live["session_points"], 8)
        self.assertEqual(member_live["points_r1"], 5)
        self.assertEqual(member_live["points_r2"], 3)
        self.assertEqual(member_live["points_r3"], 0)
        dash = self.db.dashboard(class_id)
        session_id = state["session"]["id"]
        cell = dash["cells"][f"{session_id}:{member['id']}"]
        self.assertEqual(cell["points_r1"], 5)
        self.assertEqual(cell["points_r2"], 3)
        self.assertEqual(cell["points_r3"], 0)
        self.assertEqual(cell["points"], 8)
        self.assertEqual(dash["totals"][str(member["id"])], 8)
        self.assertEqual(dash["live_subtotals"][str(member["id"])], 8)
        event_round = self.db.conn.execute(
            'SELECT "round" AS rnd FROM point_events ORDER BY seq DESC LIMIT 1'
        ).fetchone()
        self.assertEqual(int(event_round["rnd"]), 2)

    def test_timer_zero_does_not_advance_round(self) -> None:
        """An expired clock stays on that round; Start Round 2 still works."""
        self._go_live()
        class_id = self.cls["id"]
        with self.db._lock:
            self.db.conn.execute(
                "UPDATE games SET round_started_at = ? WHERE class_id = ? AND status = 'live'",
                ("2000-01-01T00:00:00", class_id),
            )
            self.db.conn.commit()
        stale = self.db.game_state(class_id)
        self.assertEqual(stale["game"]["round"], 1)
        self.assertEqual(stale["game"]["round_remaining_sec"], 0)
        moved = self.db.start_round(class_id, 2)
        self.assertEqual(moved["game"]["round"], 2)
        self.assertGreater(moved["game"]["round_remaining_sec"], 0)

    def test_team_only_stays_one_espn_total(self) -> None:
        """Team-only buckets are one running ESPN total, not round columns."""
        state = self._go_live()
        class_id = self.cls["id"]
        team = state["teams"][0]
        member = team["members"][0]
        self.db.award_points(
            class_id, kind="team", target_id=team["id"], amount=10, team_rule="team_only"
        )
        self.db.start_round(class_id, 2)
        self.db.award_points(
            class_id, kind="team", target_id=team["id"], amount=7, team_rule="team_only"
        )
        live = self.db.game_state(class_id)
        scored = next(t for t in live["teams"] if t["id"] == team["id"])
        self.assertEqual(scored["score"], 17)
        scored_member = next(m for m in scored["members"] if m["id"] == member["id"])
        self.assertEqual(scored_member["session_points"], 0)
        self.assertEqual(scored_member["points_r1"], 0)
        self.assertEqual(scored_member["points_r2"], 0)
        self.assertEqual(scored_member["points_r3"], 0)
        dash = self.db.dashboard(class_id)
        cell = dash["cells"][f"{state['session']['id']}:{member['id']}"]
        self.assertEqual(cell["points"], 0)
        self.assertEqual(cell["points_r1"], 0)
        self.assertEqual(cell["points_r2"], 0)

    def test_scoreboard_leaders_and_most_improved(self) -> None:
        """Live ticker names Open Question leaders; next lesson adds Most Improved."""
        state = self._go_live()
        class_id = self.cls["id"]
        member = state["teams"][0]["members"][0]
        first = member["first_name"]
        self.db.award_points(class_id, kind="student", target_id=member["id"], amount=5)
        board = self.db.scoreboard()
        self.assertTrue(board["live"])
        self.assertTrue(
            any("Open Question Leaders" in row and first in row for row in board["leaders"])
        )
        self.assertFalse(any("Most Improved" in row for row in board["leaders"]))
        self.db.end_game(class_id)
        final = self.db.scoreboard()
        self.assertTrue(
            any("Open Question Leaders" in row and first in row for row in final["leaders"]),
            "ended/idle boards still use scored class columns",
        )
        self.db.begin_game(class_id, today=date(2026, 9, 2))
        present = [s["id"] for s in self.db.game_state(class_id)["students"][:4]]
        self.db.save_attendance(class_id, present)
        self.db.assign_teams(class_id, 2, "random")
        teams = self.db.game_state(class_id)["teams"]
        self.db.rename_teams(class_id, [{"id": t["id"], "name": t["name"]} for t in teams])
        waiting = self.db.scoreboard()
        self.assertTrue(
            any("Open Question Leaders" in row and first in row for row in waiting["leaders"]),
            "empty live column must still use the last scored class column",
        )
        self.assertFalse(any("Most Improved" in row for row in waiting["leaders"]))
        live = self.db.game_state(class_id)
        again = next(m for t in live["teams"] for m in t["members"] if m["id"] == member["id"])
        self.db.award_points(class_id, kind="student", target_id=again["id"], amount=10)
        climbed = self.db.scoreboard()
        self.assertTrue(
            any(
                "Open Question Most Improved" in row and first in row and "+5" in row
                for row in climbed["leaders"]
            )
        )
        self.assertEqual(climbed["stat_window"], "last_class")
        self.assertEqual(climbed["stat_window_label"], "last class")

    def _roster_pair(self) -> tuple[dict, dict, list[int]]:
        """Two named students and a four-person present list from this class.

        Returns:
            ``(student_a, student_b, present_ids)``.
        """
        dash = self.db.dashboard(self.cls["id"])
        students = dash["students"]
        return students[0], students[1], [int(s["id"]) for s in students[:4]]

    def _live_game(self, present_ids: list[int], *, today: date) -> dict:
        """Attendance → teams → Create Teams for this roster.

        Args:
            present_ids: Student ids marked present.
            today: Reference date for the meeting slot.
        """
        class_id = self.cls["id"]
        self.db.begin_game(class_id, today=today)
        self.db.save_attendance(class_id, present_ids)
        self.db.assign_teams(class_id, 2, "random")
        teams = self.db.game_state(class_id)["teams"]
        return self.db.rename_teams(
            class_id, [{"id": t["id"], "name": t["name"]} for t in teams]
        )

    def _score_and_end(self, amounts: dict[int, int]) -> None:
        """Award Open Question points on the live game and End Game.

        Args:
            amounts: student_id → points (zero amounts are skipped).
        """
        class_id = self.cls["id"]
        for sid, amount in amounts.items():
            if amount:
                self.db.award_points(
                    class_id, kind="student", target_id=int(sid), amount=int(amount)
                )
        self.db.end_game(class_id)

    def _open_question_row(self, board: dict, kind: str, first: str) -> str | None:
        """Return the ticker line for one student, if present.

        Args:
            board: Scoreboard payload.
            kind: ``Leaders`` or ``Most Improved``.
            first: Student first name.
        """
        needle = f"Open Question {kind}"
        for row in board.get("leaders") or []:
            if needle in row and first in row:
                return row
        return None

    def test_stat_window_defaults_to_last_class(self) -> None:
        """Dashboard and live ticker start on last class (current behavior)."""
        dash = self.db.dashboard(self.cls["id"])
        self.assertEqual(dash["stat_window"], DEFAULT_STAT_WINDOW)
        self.assertEqual(dash["stat_window"], "last_class")
        a, _b, present = self._roster_pair()
        self._live_game(present, today=date(2026, 8, 31))
        self.db.award_points(
            self.cls["id"], kind="student", target_id=a["id"], amount=5
        )
        board = self.db.scoreboard()
        self.assertEqual(board["stat_window"], "last_class")
        self.assertEqual(board["stat_window_label"], "last class")
        self.assertIsNotNone(self._open_question_row(board, "Leaders", a["first_name"]))
        self.assertFalse(any("Most Improved" in row for row in board["leaders"]))

    def test_stat_window_last_class_skips_empty_live(self) -> None:
        """After two scored games and an empty live game, last class is the latest scored."""
        a, b, present = self._roster_pair()
        self._live_game(present, today=date(2026, 8, 31))
        self._score_and_end({a["id"]: 5, b["id"]: 10})
        self._live_game(present, today=date(2026, 9, 2))
        self._score_and_end({a["id"]: 12, b["id"]: 3})
        self._live_game(present, today=date(2026, 9, 9))
        board = self.db.scoreboard()
        self.assertEqual(board["stat_window"], "last_class")
        self.assertTrue(board["live"])
        a_row = self._open_question_row(board, "Leaders", a["first_name"])
        self.assertIsNotNone(a_row)
        self.assertIn("12", a_row or "")
        self.assertIsNone(self._open_question_row(board, "Leaders", b["first_name"]))
        mi = self._open_question_row(board, "Most Improved", a["first_name"])
        self.assertIsNotNone(mi)
        self.assertIn("+7", mi or "")

    def test_stat_window_last_week_averages_and_most_improved(self) -> None:
        """Last week is the mean of two classes; Most Improved only after four scored."""
        a, b, present = self._roster_pair()
        class_id = self.cls["id"]
        live = self._live_game(present, today=date(2026, 8, 31))
        first_session = int(live["session"]["id"])
        self.db.award_points(class_id, kind="student", target_id=a["id"], amount=5)
        self.db.set_stat_window(class_id, "last_week")
        one = self.db.scoreboard()
        self.assertEqual(one["stat_window"], "last_week")
        self.assertEqual(one["stat_window_label"], stat_window_label("last_week"))
        self.assertIsNotNone(self._open_question_row(one, "Leaders", a["first_name"]))
        self.assertIn("5", self._open_question_row(one, "Leaders", a["first_name"]) or "")
        self.assertFalse(any("Most Improved" in row for row in one["leaders"]))
        self.db.end_game(class_id)
        self.db.delete_session_column(class_id, first_session)

        self._live_game(present, today=date(2026, 9, 2))
        self._score_and_end({a["id"]: 2, b["id"]: 10})
        self._live_game(present, today=date(2026, 9, 9))
        self._score_and_end({a["id"]: 2, b["id"]: 10})
        self._live_game(present, today=date(2026, 9, 10))
        two = self.db.scoreboard()
        self.assertEqual(two["stat_window"], "last_week")
        b_row = self._open_question_row(two, "Leaders", b["first_name"])
        self.assertIsNotNone(b_row)
        self.assertIn("10", b_row or "")
        self.assertIsNone(self._open_question_row(two, "Leaders", a["first_name"]))
        self.assertFalse(any("Most Improved" in row for row in two["leaders"]))
        self.db.cancel_setup(class_id)

        self._live_game(present, today=date(2026, 9, 11))
        self._score_and_end({a["id"]: 10, b["id"]: 6})
        self._live_game(present, today=date(2026, 9, 14))
        self._score_and_end({a["id"]: 10, b["id"]: 6})
        self._live_game(present, today=date(2026, 9, 16))
        four = self.db.scoreboard()
        a_week = self._open_question_row(four, "Leaders", a["first_name"])
        self.assertIsNotNone(a_week)
        self.assertIn("10", a_week or "")
        self.assertIsNone(self._open_question_row(four, "Leaders", b["first_name"]))
        mi = self._open_question_row(four, "Most Improved", a["first_name"])
        self.assertIsNotNone(mi)
        self.assertIn("+8", mi or "")

    def test_stat_window_year_averages_all_and_omits_most_improved(self) -> None:
        """This year averages every scored column and has no Most Improved."""
        a, b, present = self._roster_pair()
        class_id = self.cls["id"]
        self._live_game(present, today=date(2026, 8, 31))
        self._score_and_end({a["id"]: 2, b["id"]: 10})
        self._live_game(present, today=date(2026, 9, 2))
        self._score_and_end({a["id"]: 2, b["id"]: 10})
        self._live_game(present, today=date(2026, 9, 9))
        self._score_and_end({a["id"]: 20, b["id"]: 10})
        self._live_game(present, today=date(2026, 9, 10))
        self.db.set_stat_window(class_id, "year")
        board = self.db.scoreboard()
        self.assertEqual(board["stat_window"], "year")
        self.assertEqual(board["stat_window_label"], "this year")
        b_row = self._open_question_row(board, "Leaders", b["first_name"])
        self.assertIsNotNone(b_row)
        self.assertIn("10", b_row or "")
        self.assertIsNone(self._open_question_row(board, "Leaders", a["first_name"]))
        self.assertFalse(any("Most Improved" in row for row in board["leaders"]))
        with self.assertRaises(ValueError):
            self.db.set_stat_window(class_id, "semester")


class StatWindowUnitTests(unittest.TestCase):
    """Period picker keys and Leader/Most Improved window shapes."""

    def test_normalize_and_labels(self) -> None:
        """Known windows pass through; anything else is rejected."""
        self.assertEqual(normalize_stat_window("last_week"), "last_week")
        self.assertEqual(stat_window_label("year"), "this year")
        with self.assertRaises(ValueError):
            normalize_stat_window("semester")

    def test_leader_period_shapes(self) -> None:
        """Last week needs two columns; Most Improved needs four; year has no prior."""
        ids = [1, 2, 3, 4]
        self.assertEqual(leader_periods(ids, "last_class"), ([4], [3]))
        self.assertEqual(leader_periods(ids, "last_week"), ([3, 4], [1, 2]))
        self.assertEqual(leader_periods(ids, "year"), ([1, 2, 3, 4], None))
        self.assertEqual(leader_periods([1], "last_week"), ([1], None))
        self.assertEqual(leader_periods([1, 2], "last_week"), ([1, 2], None))
        self.assertEqual(leader_periods([1, 2, 3], "last_week"), ([2, 3], None))
        self.assertEqual(leader_periods([], "year"), ([], None))


class MemberRoundPointsTests(unittest.TestCase):
    """Teacher-game member payload keeps the lesson sum and round buckets."""

    def test_points_label_drops_trailing_zero(self) -> None:
        """Ticker text matches the sheet: 5 not 5.0, 3.3 stays 3.3."""
        self.assertEqual(points_label(5), "5")
        self.assertEqual(points_label(3.3), "3.3")

    def test_empty_row_is_zeros(self) -> None:
        """Missing scores are 0, not None."""
        self.assertEqual(
            member_round_points(None),
            {"session_points": 0.0, "points_r1": 0.0, "points_r2": 0.0, "points_r3": 0.0},
        )

    def test_session_points_stay_the_sum(self) -> None:
        """The helper does not re-add buckets; it reports stored totals."""
        row = member_round_points(
            {"points": 8, "points_r1": 5, "points_r2": 3, "points_r3": 0}
        )
        self.assertEqual(row["session_points"], 8)
        self.assertEqual(row["points_r1"], 5)
        self.assertEqual(row["points_r2"], 3)
        self.assertEqual(row["points_r3"], 0)


class RoundClockTests(unittest.TestCase):
    """Server-side remaining seconds never go below 0."""

    def test_full_second_before_drop_and_clamp(self) -> None:
        """A just-started 20:00 clock still reads 1200 until a full second elapses."""
        started = datetime(2026, 9, 8, 14, 0, 0)
        almost = datetime(2026, 9, 8, 14, 0, 0, 400000)
        self.assertEqual(round_remaining_sec(started.isoformat(), 1200, now=almost), 1200)
        later = datetime(2026, 9, 8, 14, 0, 1)
        self.assertEqual(round_remaining_sec(started.isoformat(), 1200, now=later), 1199)
        expired = datetime(2026, 9, 8, 14, 30, 0)
        self.assertEqual(round_remaining_sec(started.isoformat(), 1200, now=expired), 0)
        self.assertEqual(round_remaining_sec(None, 1200, now=started), 0)
        end_ms = round_ends_at_ms(started.isoformat(), 1200)
        expected = int((started + timedelta(seconds=1200)).timestamp() * 1000)
        self.assertEqual(end_ms, expected)
        self.assertIsNone(round_ends_at_ms(None, 1200))


class RoundSchemaTests(unittest.TestCase):
    """Existing session_scores.points become Round 1 on migrate."""

    def test_legacy_points_become_r1(self) -> None:
        """Old rows copy ``points`` into ``points_r1``; R2/R3 stay 0."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        data_dir = Path(tmp.name)
        db_path = data_dir / "app.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE classes (
                id INTEGER PRIMARY KEY,
                year TEXT NOT NULL,
                semester TEXT NOT NULL,
                course_code TEXT NOT NULL,
                days TEXT NOT NULL,
                time TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE students (
                id INTEGER PRIMARY KEY,
                class_id INTEGER NOT NULL,
                canvas_id TEXT NOT NULL,
                last_display TEXT NOT NULL,
                first_name TEXT NOT NULL
            );
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY,
                class_id INTEGER NOT NULL,
                starts_at TEXT NOT NULL,
                header_label TEXT NOT NULL,
                status TEXT NOT NULL,
                log_path TEXT,
                source TEXT NOT NULL DEFAULT 'game'
            );
            CREATE TABLE session_scores (
                session_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                present INTEGER NOT NULL DEFAULT 0,
                points REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (session_id, student_id)
            );
            CREATE TABLE games (
                id INTEGER PRIMARY KEY,
                class_id INTEGER NOT NULL,
                session_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                event_seq INTEGER NOT NULL DEFAULT 0,
                last_event_json TEXT,
                owns_session INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE point_events (
                id INTEGER PRIMARY KEY,
                session_id INTEGER NOT NULL,
                game_id INTEGER NOT NULL,
                seq INTEGER NOT NULL,
                ts TEXT NOT NULL,
                from_kind TEXT NOT NULL,
                from_id INTEGER,
                to_kind TEXT NOT NULL,
                to_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                team_rule TEXT
            );
            INSERT INTO classes VALUES (
                1, '2026/27', 'Semester 1', 'MCF3M', 'Tue/Thu/Fri', '2:00pm',
                '2026-08-31T12:00:00'
            );
            INSERT INTO students VALUES (1, 1, '1', 'Doe', 'Ada');
            INSERT INTO sessions VALUES (
                1, 1, '2026-09-08T14:00:00', 'Tue 9/8 2:00pm', 'ended', NULL, 'game'
            );
            INSERT INTO session_scores VALUES (1, 1, 1, 12);
            """
        )
        conn.commit()
        conn.close()
        db = GameShowDB(db_path, data_dir)
        self.addCleanup(db.close)
        row = db.conn.execute(
            """
            SELECT points, points_r1, points_r2, points_r3
            FROM session_scores WHERE student_id = 1
            """
        ).fetchone()
        self.assertEqual(as_points(row["points"]), 12)
        self.assertEqual(as_points(row["points_r1"]), 12)
        self.assertEqual(as_points(row["points_r2"]), 0)
        self.assertEqual(as_points(row["points_r3"]), 0)
        game_cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(games)")}
        self.assertIn("current_round", game_cols)
        self.assertIn("round_started_at", game_cols)
        self.assertIn("round_duration_sec", game_cols)
        event_cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(point_events)")}
        self.assertIn("round", event_cols)


def _http_json(base: str, path: str, payload: dict | None = None) -> dict:
    """GET or POST JSON against the test server.

    Args:
        base: Origin such as ``http://127.0.0.1:9xxx``.
        path: URL path.
        payload: If set, POST this object; otherwise GET.

    Returns:
        Parsed JSON body.
    """
    data = None
    headers = {}
    method = "GET"
    if payload is not None:
        method = "POST"
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(base + path, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        raise AssertionError(f"{exc.code} {path}: {body}") from exc


class HttpApiTests(unittest.TestCase):
    """Smoke the stdlib server the same way the teacher UI will."""

    @classmethod
    def setUpClass(cls) -> None:
        """Bind an ephemeral localhost port with a temp database."""
        cls.tmp = tempfile.TemporaryDirectory()
        data_dir = Path(cls.tmp.name)
        game_server.DB = GameShowDB(data_dir / "app.sqlite", data_dir)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), game_server.GameShowHandler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        """Stop the test server."""
        cls.httpd.shutdown()
        cls.httpd.server_close()
        if game_server.DB is not None:
            game_server.DB.close()
        cls.tmp.cleanup()

    def test_pages_and_full_flow(self) -> None:
        """Home HTML, defaults, create, dashboard, game, scoreboard, End Game."""
        with urlopen(self.base + "/", timeout=5) as resp:
            home = resp.read().decode("utf-8")
        self.assertIn("Create New Class", home)
        defaults = _http_json(self.base, "/api/defaults")
        self.assertEqual(defaults["course_code"], "MCF3M")
        created = _http_json(
            self.base,
            "/api/classes",
            {
                "year": "2026/27",
                "semester": "Semester 1",
                "course_code": "MCF3M",
                "days": "T/Th/F",
                "time": "2:00pm",
                "csv_text": FIXTURE_CSV.read_text(encoding="utf-8"),
            },
        )
        class_id = created["class"]["id"]
        listed = _http_json(self.base, "/api/classes")
        self.assertTrue(any(c["id"] == class_id for c in listed["classes"]))
        dash = _http_json(self.base, f"/api/classes/{class_id}/dashboard")
        self.assertEqual(len(dash["students"]), 17)
        self.assertEqual(dash["sessions"][0]["header_label"], "Tue 9/8 2:00pm")
        begin = _http_json(self.base, f"/api/classes/{class_id}/begin", {})
        present = [s["id"] for s in begin["students"][:4]]
        moved = _http_json(
            self.base,
            f"/api/classes/{class_id}/game/meeting",
            {"meeting_date": "2026-09-18", "time": "2:00pm"},
        )
        self.assertIn("Fri 9/18 2:00pm", moved["session"]["header_label"])
        _http_json(self.base, f"/api/classes/{class_id}/game/cancel", {})
        begin = _http_json(self.base, f"/api/classes/{class_id}/begin", {})
        _http_json(
            self.base,
            f"/api/classes/{class_id}/game/attendance",
            {"present_ids": present},
        )
        assigned = _http_json(
            self.base,
            f"/api/classes/{class_id}/game/assign",
            {"n_teams": 2, "mode": "random"},
        )
        _http_json(
            self.base,
            f"/api/classes/{class_id}/game/rename",
            {"teams": [{"id": t["id"], "name": t["name"]} for t in assigned["teams"]]},
        )
        member = assigned["teams"][0]["members"][0]
        _http_json(
            self.base,
            f"/api/classes/{class_id}/game/score",
            {"kind": "student", "id": member["id"], "amount": 5},
        )
        live_board = _http_json(self.base, f"/api/classes/{class_id}/scoreboard")
        self.assertTrue(live_board["live"])
        self.assertEqual(live_board["last_event"]["first_name"], member["first_name"])
        self.assertTrue(live_board["last_event"]["celebrate"])
        _http_json(
            self.base,
            f"/api/classes/{class_id}/game/score",
            {
                "kind": "team",
                "id": assigned["teams"][0]["id"],
                "amount": 10,
                "team_rule": "team_only",
            },
        )
        board = _http_json(self.base, f"/api/classes/{class_id}/scoreboard")
        self.assertTrue(board["live"])
        self.assertEqual(len(board["teams"]), 2)
        ended = _http_json(self.base, f"/api/classes/{class_id}/game/end", {})
        self.assertTrue(ended["ok"])
        final = _http_json(self.base, f"/api/classes/{class_id}/scoreboard")
        self.assertTrue(final["final"])
        self.assertFalse(final["live"])
        self.assertEqual(final["status"], "ended")
        self.assertEqual(len(final["teams"]), 2)
        _http_json(self.base, f"/api/classes/{class_id}/begin", {})
        idle = _http_json(self.base, "/api/scoreboard")
        self.assertFalse(idle["final"])
        self.assertFalse(idle["live"])
        self.assertEqual(idle["teams"], [])
        dash2 = _http_json(self.base, f"/api/classes/{class_id}/dashboard")
        self.assertEqual(dash2["sessions"][0]["status"], "ended")
        with urlopen(self.base + f"/api/sessions/{dash2['sessions'][0]['id']}/log", timeout=5) as resp:
            log_body = resp.read().decode("utf-8")
        self.assertIn('"from": "teacher"', log_body)

    def test_round_api_and_scoreboard(self) -> None:
        """POST next round only; game_state and scoreboard expose the clock."""
        created = _http_json(
            self.base,
            "/api/classes",
            {
                "year": "2026/27",
                "semester": "Semester 1",
                "course_code": "MCF3M",
                "days": "T/Th/F",
                "time": "2:00pm",
                "csv_text": FIXTURE_CSV.read_text(encoding="utf-8"),
            },
        )
        class_id = created["class"]["id"]
        begin = _http_json(self.base, f"/api/classes/{class_id}/begin", {})
        present = [s["id"] for s in begin["students"][:4]]
        _http_json(
            self.base,
            f"/api/classes/{class_id}/game/attendance",
            {"present_ids": present},
        )
        assigned = _http_json(
            self.base,
            f"/api/classes/{class_id}/game/assign",
            {"n_teams": 2, "mode": "random"},
        )
        _http_json(
            self.base,
            f"/api/classes/{class_id}/game/rename",
            {"teams": [{"id": t["id"], "name": t["name"]} for t in assigned["teams"]]},
        )
        live = _http_json(self.base, f"/api/classes/{class_id}/game")
        self.assertEqual(live["game"]["round"], 1)
        self.assertEqual(live["game"]["round_title"], "Open Question")
        self.assertGreaterEqual(live["game"]["round_remaining_sec"], 1190)
        skip = Request(
            self.base + f"/api/classes/{class_id}/game/round",
            data=json.dumps({"round": 3}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urlopen(skip, timeout=5)
            self.fail("expected 400 when skipping to round 3")
        except HTTPError as exc:
            body = exc.read().decode("utf-8")
            exc.close()
            self.assertEqual(exc.code, 400)
            self.assertIn("Cannot skip", body)
        moved = _http_json(
            self.base, f"/api/classes/{class_id}/game/round", {"round": 2}
        )
        self.assertEqual(moved["game"]["round"], 2)
        self.assertEqual(moved["game"]["round_title"], "Team Challenge")
        board = _http_json(self.base, "/api/scoreboard")
        self.assertTrue(board["live"])
        self.assertEqual(board["round"], 2)
        self.assertEqual(board["round_title"], "Team Challenge")
        self.assertGreaterEqual(board["round_remaining_sec"], 590)
        member = assigned["teams"][0]["members"][0]
        _http_json(
            self.base,
            f"/api/classes/{class_id}/game/score",
            {"kind": "student", "id": member["id"], "amount": 5},
        )
        dash = _http_json(self.base, f"/api/classes/{class_id}/dashboard")
        session_id = moved["session"]["id"]
        cell = dash["cells"][f"{session_id}:{member['id']}"]
        self.assertEqual(cell["points_r1"], 0)
        self.assertEqual(cell["points_r2"], 5)
        self.assertEqual(cell["points"], 5)
        live_scored = _http_json(self.base, f"/api/classes/{class_id}/game")
        found = next(
            m
            for t in live_scored["teams"]
            for m in t["members"]
            if m["id"] == member["id"]
        )
        self.assertEqual(found["session_points"], 5)
        self.assertEqual(found["points_r1"], 0)
        self.assertEqual(found["points_r2"], 5)
        self.assertEqual(found["points_r3"], 0)
        board_after = _http_json(self.base, "/api/scoreboard")
        self.assertTrue(
            any("Team Challenge Leaders" in row for row in board_after.get("leaders") or [])
        )
        for team in board_after["teams"]:
            self.assertNotIn("members", team)
            self.assertNotIn("points_r1", team)
            for player in team.get("players") or []:
                self.assertEqual(set(player.keys()), {"first_name", "student_id"})

    def test_stat_window_post_persists_on_dashboard(self) -> None:
        """POST /stat-window stores the period for this class and paints the dashboard."""
        created = _http_json(
            self.base,
            "/api/classes",
            {
                "year": "2026/27",
                "semester": "Semester 1",
                "course_code": "MCF3M",
                "days": "T/Th/F",
                "time": "2:00pm",
                "csv_text": FIXTURE_CSV.read_text(encoding="utf-8"),
            },
        )
        class_id = created["class"]["id"]
        dash = _http_json(self.base, f"/api/classes/{class_id}/dashboard")
        self.assertEqual(dash["stat_window"], "last_class")
        with urlopen(self.base + f"/class/{class_id}", timeout=5) as resp:
            html = resp.read().decode("utf-8")
        self.assertIn("Last class", html)
        self.assertIn("Last week", html)
        self.assertIn("This year", html)
        self.assertIn('data-stat-window="last_week"', html)
        saved = _http_json(
            self.base,
            f"/api/classes/{class_id}/stat-window",
            {"window": "year"},
        )
        self.assertEqual(saved["stat_window"], "year")
        again = _http_json(self.base, f"/api/classes/{class_id}/dashboard")
        self.assertEqual(again["stat_window"], "year")

    def test_add_late_student_and_delete_subtotal(self) -> None:
        """HTTP add-student on a live game and delete a frozen SUBTOTAL column."""
        created = _http_json(
            self.base,
            "/api/classes",
            {
                "year": "2026/27",
                "semester": "Semester 1",
                "course_code": "MCF3M",
                "days": "T/Th/F",
                "time": "2:00pm",
                "csv_text": FIXTURE_CSV.read_text(encoding="utf-8"),
            },
        )
        class_id = created["class"]["id"]
        begin = _http_json(self.base, f"/api/classes/{class_id}/begin", {})
        present = [s["id"] for s in begin["students"][:4]]
        late = next(s for s in begin["students"] if s["id"] not in present)
        _http_json(
            self.base,
            f"/api/classes/{class_id}/game/attendance",
            {"present_ids": present},
        )
        assigned = _http_json(
            self.base,
            f"/api/classes/{class_id}/game/assign",
            {"n_teams": 2, "mode": "random"},
        )
        _http_json(
            self.base,
            f"/api/classes/{class_id}/game/rename",
            {"teams": [{"id": t["id"], "name": t["name"]} for t in assigned["teams"]]},
        )
        team_id = assigned["teams"][0]["id"]
        added = _http_json(
            self.base,
            f"/api/classes/{class_id}/game/add-student",
            {"student_id": late["id"], "team_id": team_id},
        )
        self.assertIn(late["id"], added["present_ids"])
        joined = next(t for t in added["teams"] if t["id"] == team_id)
        self.assertTrue(any(m["id"] == late["id"] for m in joined["members"]))
        _http_json(self.base, f"/api/classes/{class_id}/game/end", {})
        frozen = _http_json(self.base, f"/api/classes/{class_id}/subtotals", {"name": "Term 1"})
        sub = next(c for c in frozen["columns"] if c["kind"] == "subtotal")
        deleted = _http_json(
            self.base,
            f"/api/classes/{class_id}/subtotals/delete",
            {"id": sub["id"]},
        )
        self.assertFalse(any(c["kind"] == "subtotal" for c in deleted["columns"]))


class LlovesCodenameRosterTests(unittest.TestCase):
    """LLOVES Populate Class uses Codenames instead of a Canvas CSV."""

    def setUp(self) -> None:
        """Temp database for Codename-only class creation."""
        self.tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self.tmp.name)
        self.db = GameShowDB(data_dir / "app.sqlite", data_dir)

    def tearDown(self) -> None:
        """Close sqlite and drop the temp dir."""
        self.db.close()
        self.tmp.cleanup()

    def test_create_class_from_codenames(self) -> None:
        """Roster stores Codenames; first_name matches for the ticker."""
        created = self.db.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCF3M",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Maple", "Cedar"],
            today=date(2026, 8, 31),
        )
        dash = self.db.dashboard(created["id"], sort="az")
        names = [s["codename"] for s in dash["students"]]
        self.assertEqual(names, ["Cedar", "Maple"])
        self.assertEqual(dash["students"][0]["first_name"], "Cedar")

    def test_reject_csv_and_codenames_together(self) -> None:
        """LLOVES path cannot also ingest a Canvas CSV."""
        with self.assertRaises(ValueError):
            self.db.create_class(
                year="2026/27",
                semester="Semester 1",
                course_code="MCF3M",
                days_preset="M/W/F",
                time_label="2:00pm",
                csv_text="Student,ID\nA,1\n",
                codenames=["Maple"],
            )

    def test_roster_from_codenames_rejects_comma(self) -> None:
        """Commas would split the Grades column."""
        with self.assertRaises(ValueError):
            roster_from_codenames(["Maple, Syrup"])

    def test_parse_codename_column_csv(self) -> None:
        """First column only; skip empties and a Codename header."""
        names = parse_codename_column_csv("Codename,Extra\nMaple,x\n\nCedar,\n")
        self.assertEqual(names, ["Maple", "Cedar"])
        with self.assertRaises(ValueError):
            parse_codename_column_csv("Codename\n\n")

    def test_dashboard_sorts_codenames_za(self) -> None:
        """Sort toggle is A–Z / Z–A on Codename."""
        created = self.db.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCF3M",
            days_preset="T/Th/F",
            time_label="2:00pm",
            codenames=["Maple", "Cedar", "Birch"],
            today=date(2026, 8, 31),
        )
        za = self.db.dashboard(created["id"], sort="za")
        self.assertEqual([s["codename"] for s in za["students"]], ["Maple", "Cedar", "Birch"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

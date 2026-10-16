#!/usr/bin/env python3
"""MCK-240: Winner page scoreboard, classmate-hidden MVP vote, and session purge."""

from __future__ import annotations

import inspect
import json
import logging
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
import gamification_winner as winner_mod  # noqa: E402
from gamification_winner import (  # noqa: E402
    WINNER_COPY,
    bump_tally,
    cast_split,
    insert_voter,
)
from live_teacher_state import default_teacher_state  # noqa: E402

_FORBIDDEN_KEYS = {
    "student_id",
    "voter_id",
    "nominee_id",
    "first_name",
    "last_name",
    "last_display",
}


def _walk_keys(value: object) -> set[str]:
    """Collect every dict key in a JSON-like tree."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            found.add(str(key))
            found.update(_walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_walk_keys(child))
    return found


class GamificationWinnerTests(unittest.TestCase):
    """Group scoreboard, winning-team poll, split ballots, and purge."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCF3M"
        )
        self.offering_id = int(offering["id"])
        self.teacher_id = int(teacher["id"])
        self.class_id = int(
            self.school.game.create_class(
                year="2026/27",
                semester="Semester 1",
                course_code="MCF3M",
                days_preset="M/W/F",
                time_label="2:00pm",
                codenames=["Aspen", "Birch", "Cedar", "Dale"],
                offering_id=self.offering_id,
                teacher_user_id=self.teacher_id,
            )["id"]
        )
        rows = [
            dict(row)
            for row in self.school.game.conn.execute(
                "SELECT id, codename FROM students WHERE class_id = ? ORDER BY id",
                (self.class_id,),
            ).fetchall()
        ]
        self.ids = {str(row["codename"]): int(row["id"]) for row in rows}
        live = self.school.start_live_class_session(self.class_id, self.teacher_id)
        self.session_id = int(live["id"])
        self.school.game.begin_game(self.class_id)
        for codename, student_id in self.ids.items():
            self.school.join_live_class_session(
                self.session_id, student_id, codename=codename
            )
        self.school.setup_live_session_groups(
            self.session_id,
            n_teams=2,
            mode="manual",
            present_ids=list(self.ids.values()),
            assignments=[
                {"student_id": self.ids["Aspen"], "team_index": 0},
                {"student_id": self.ids["Birch"], "team_index": 0},
                {"student_id": self.ids["Cedar"], "team_index": 1},
                {"student_id": self.ids["Dale"], "team_index": 1},
            ],
        )
        self.school.game.conn.execute(
            """
            UPDATE students
            SET first_name = ?, last_display = ?
            WHERE id = ?
            """,
            ("Jordan", "Lee", self.ids["Aspen"]),
        )
        self.school.game.conn.commit()

    def tearDown(self) -> None:
        self.school.close()
        self.tmp.cleanup()

    def _teams(self) -> list[dict]:
        state = self.school.game.game_state(self.class_id)
        return list(state["teams"])

    def _team_named(self, name: str) -> dict:
        return next(team for team in self._teams() if team["name"] == name)

    def _bind(self, codename: str) -> None:
        with self.client.session_transaction() as sess:
            sess["student_offering_id"] = self.offering_id
            sess["student_class_id"] = self.class_id
            sess["student_id"] = self.ids[codename]
            sess["student_live_session_id"] = self.session_id

    def _page(self, codename: str) -> dict:
        self._bind(codename)
        body = self.client.get("/api/student/state").get_json()
        self.assertTrue(body.get("ok"), body)
        page = body.get("gamification") or {}
        self.assertIsInstance(page, dict)
        return page

    def _insert_item(self, placement: str) -> int:
        now = "2026-10-16T12:00:00"
        with self.school._lock:
            cur = self.school.conn.execute(
                """
                INSERT INTO live_session_items (
                    live_session_id, placement_key, item_id, stage, sort_order,
                    kind, item_json, status, publish_mode, response_mode,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'play', 0, 'rank', '{}', 'closed',
                          'group', 'group_submit', ?, ?)
                """,
                (self.session_id, placement, placement, now, now),
            )
            self.school.conn.commit()
        return int(cur.lastrowid)

    def _score_group_and_solo(self) -> None:
        """Team A bucket +6, Aspen solo 3 and chip 2, Cedar challenge 4."""
        team_a = self._team_named("Team 1")
        team_b = self._team_named("Team 2")
        self.school.game.award_points(
            self.class_id,
            kind="team",
            target_id=int(team_a["id"]),
            amount=6,
            team_rule="team_only",
        )
        prompt = self.school.set_live_session_prompt(
            self.session_id,
            slide_index=240,
            kind="mc",
            payload={
                "kind": "mc",
                "prompt": "Solo",
                "choices": ["A", "B"],
                "key": "A",
            },
        )
        self.school.submit_live_prompt_response(
            int(prompt["id"]), self.ids["Aspen"], {"choice": "A"}
        )
        self.school.award_live_prompt_points(
            self.session_id,
            int(prompt["id"]),
            mode="correct",
            amount=3,
        )
        self.school.game.award_points(
            self.class_id,
            kind="student",
            target_id=self.ids["Aspen"],
            amount=2,
            label="Chip",
        )
        item_id = self._insert_item("mck240-race")
        now = "2026-10-16T12:00:00"
        with self.school._lock:
            self.school.conn.execute(
                """
                INSERT INTO live_rank_race_awards (
                    live_item_id, student_id, team_id, points, created_at, paid_at
                ) VALUES (?, ?, ?, 4, ?, ?)
                """,
                (item_id, self.ids["Cedar"], int(team_b["id"]), now, now),
            )
            self.school.conn.execute(
                """
                INSERT INTO live_group_members (
                    live_item_id, team_id, student_id, joined_at, awarded_points
                ) VALUES (?, ?, ?, ?, 4)
                """,
                (item_id, int(team_b["id"]), self.ids["Cedar"], now),
            )
            self.school.conn.commit()
        self.school.game.award_points(
            self.class_id,
            kind="student",
            target_id=self.ids["Cedar"],
            amount=4,
            label="Team challenge",
        )

    def test_groups_do_not_turn_the_page_on(self) -> None:
        self.assertFalse(default_teacher_state()["gamification_on"])
        teacher = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertFalse(teacher["gamification_on"])
        self.assertTrue(teacher["scoreboard_visible"])
        page = self._page("Aspen")
        self.assertFalse(page["on"])
        self.assertEqual(page["copy"]["winner.toggle"], "Winner page")

    def test_scoreboard_keeps_group_and_challenge_points(self) -> None:
        self._score_group_and_solo()
        self.school.set_live_session_teacher_state(
            self.session_id, gamification_on=True
        )
        page = self._page("Cedar")
        self.assertTrue(page["on"])
        board = {row["name"]: row for row in page["scoreboard"]}
        self.assertEqual(board["Team 1"]["score"], 6)
        self.assertEqual(board["Team 2"]["score"], 4)
        self.assertTrue(board["Team 1"]["leading"])
        self.assertEqual(page["winner_name"], "Team 1")
        self.assertEqual(board["Team 1"]["codenames"], ["Aspen", "Birch"])
        blob = json.dumps(page)
        self.assertNotIn("Jordan", blob)
        self.assertNotIn("Lee", blob)
        self.assertIn("Aspen", blob)
        self.assertFalse(_walk_keys(page) & _FORBIDDEN_KEYS)
        self.assertFalse(page["poll"]["eligible"])
        self.assertFalse(page["poll"]["open"])

    def test_only_the_winning_team_votes_and_mvp_gets_an_avatar(self) -> None:
        self._score_group_and_solo()
        self.school.set_live_session_teacher_state(
            self.session_id, gamification_on=True
        )
        winner = self._page("Aspen")
        self.assertTrue(winner["poll"]["eligible"])
        self.assertEqual(winner["poll"]["options"], ["Aspen", "Birch"])
        birch = self._page("Birch")
        self.assertEqual(birch["poll"]["options"], ["Aspen", "Birch"])
        before = self.school.live_student_poll_stamp(self.session_id, self.class_id)
        self._bind("Cedar")
        denied = self.client.post(
            "/api/student/mvp-vote",
            json={"nominee_codename": "Cedar"},
        )
        self.assertEqual(denied.status_code, 400)
        self.assertNotIn("Cedar", denied.get_json()["error"])
        self._bind("Aspen")
        legal = self.client.post(
            "/api/student/mvp-vote",
            json={"nominee_codename": "Jordan"},
        )
        self.assertEqual(legal.status_code, 400)
        self.assertNotIn("Jordan", legal.get_data(as_text=True))
        first = self.client.post(
            "/api/student/mvp-vote",
            json={"nominee_codename": "Birch"},
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.get_json(), {"ok": True, "voted": True})
        self.assertNotIn("Birch", first.get_data(as_text=True))
        after = self.school.live_student_poll_stamp(self.session_id, self.class_id)
        self.assertNotEqual(before, after)
        # Ballot count sits immediately before the presence token.
        self.assertEqual(before.rsplit(":", 2)[-2], "0")
        self.assertEqual(after.rsplit(":", 2)[-2], "1")
        with self.assertRaises(ValueError):
            self.school.cast_mvp_vote(self.session_id, self.ids["Aspen"], "Birch")
        tallies = winner_mod.tally_map(self.school.conn, self.session_id)
        self.assertEqual(tallies[self.ids["Birch"]], 1)
        self.assertEqual(tallies[self.ids["Aspen"]], 0)
        self.assertEqual(
            winner_mod.voter_count(self.school.conn, self.session_id), 1
        )
        again = self.client.post(
            "/api/student/mvp-vote",
            json={"nominee_codename": "Aspen"},
        )
        self.assertEqual(again.status_code, 400)
        self.assertEqual(again.get_json()["error"], "You already voted")
        self.assertNotIn("Aspen", again.get_json()["error"])
        self.assertEqual(
            winner_mod.tally_map(self.school.conn, self.session_id)[self.ids["Birch"]],
            1,
        )
        self.school.cast_mvp_vote(self.session_id, self.ids["Birch"], "Birch")
        done = self._page("Dale")
        self.assertEqual(done["mvp"]["codename"], "Birch")
        self.assertTrue(done["mvp"]["reward"])
        self.assertIsNone(done["tie"])
        self.assertNotIn("Jordan", json.dumps(done))
        grant = self.school.conn.execute(
            """
            SELECT student_id, source, source_ref, status
            FROM avatar_reward_grants
            WHERE source = 'mvp'
            """
        ).fetchone()
        self.assertIsNotNone(grant)
        self.assertEqual(int(grant["student_id"]), self.ids["Birch"])
        self.assertEqual(grant["source_ref"], f"mvp:{self.session_id}")
        self.assertNotIn("voter", str(grant["source_ref"]))
        self.assertEqual(grant["status"], "pending")

    def test_split_tables_have_no_join_path(self) -> None:
        self._score_group_and_solo()
        self.school.set_live_session_teacher_state(
            self.session_id, gamification_on=True
        )
        source = (LMS_DIR / "gamification_winner.py").read_text(encoding="utf-8")
        self.assertNotIn("import logging", source)
        self.assertNotIn("logger", source)
        self.assertNotIn("print(", source)
        voter_sql = inspect.getsource(insert_voter).split('"""')[3]
        tally_sql = inspect.getsource(bump_tally).split('"""')[3]
        self.assertNotIn("nominee", voter_sql)
        self.assertNotIn("voter", tally_sql)
        conn = self.school.conn
        voter_cols = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(gamification_mvp_voters)")
        }
        tally_cols = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(gamification_mvp_tallies)")
        }
        self.assertEqual(voter_cols & tally_cols, {"session_id"})
        self.assertEqual(voter_cols, {"session_id", "voter_id"})
        self.assertEqual(tally_cols, {"session_id", "nominee_id", "tally"})
        for name in voter_cols | tally_cols:
            lowered = name.casefold()
            self.assertNotIn("time", lowered)
            self.assertNotIn("created", lowered)
            self.assertNotIn("updated", lowered)
            self.assertFalse(lowered.endswith("ts"))
        self.assertEqual(
            conn.execute("PRAGMA foreign_key_list(gamification_mvp_voters)").fetchall(),
            [],
        )
        self.assertEqual(
            conn.execute("PRAGMA foreign_key_list(gamification_mvp_tallies)").fetchall(),
            [],
        )
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("SELECT rowid FROM gamification_mvp_voters").fetchall()
        records: list[str] = []

        class _Grab(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record.getMessage())

        grab = _Grab()
        root = logging.getLogger()
        root.addHandler(grab)
        try:
            self.school.cast_mvp_vote(self.session_id, self.ids["Aspen"], "Birch")
        finally:
            root.removeHandler(grab)
        blob = " ".join(records)
        self.assertNotIn(str(self.ids["Aspen"]), blob)
        self.assertNotIn(str(self.ids["Birch"]), blob)
        with self.assertRaises(ValueError):
            cast_split(
                conn,
                self.session_id,
                self.ids["Dale"],
                999999,
                [self.ids["Aspen"], self.ids["Birch"]],
            )
        voters = winner_mod.voter_id_set(conn, self.session_id)
        self.assertNotIn(self.ids["Dale"], voters)
        joined = conn.execute(
            """
            SELECT v.voter_id AS voter_id, t.nominee_id AS nominee_id
            FROM gamification_mvp_voters v
            JOIN gamification_mvp_tallies t ON t.session_id = v.session_id
            WHERE v.session_id = ?
            """,
            (self.session_id,),
        ).fetchall()
        nominees = {int(row["nominee_id"]) for row in joined}
        self.assertEqual(len(joined), len(voters) * len(nominees))
        self.assertGreater(len(nominees), 1)
        self.assertEqual(
            {str(row[1]) for row in conn.execute("PRAGMA table_info(gamification_mvp_voters)")},
            {"session_id", "voter_id"},
        )

    def test_votes_purge_with_the_session_and_the_avatar_stays(self) -> None:
        self._score_group_and_solo()
        self.school.set_live_session_teacher_state(
            self.session_id, gamification_on=True
        )
        self.school.cast_mvp_vote(self.session_id, self.ids["Aspen"], "Aspen")
        self.school.cast_mvp_vote(self.session_id, self.ids["Birch"], "Aspen")
        self.assertEqual(
            winner_mod.voter_count(self.school.conn, self.session_id), 2
        )
        self.school._purge_session_news(self.session_id)
        self.assertEqual(winner_mod.voter_count(self.school.conn, self.session_id), 0)
        self.assertEqual(winner_mod.tally_map(self.school.conn, self.session_id), {})
        kept = self.school.conn.execute(
            "SELECT source_ref FROM avatar_reward_grants WHERE source = 'mvp'"
        ).fetchone()
        self.assertEqual(kept["source_ref"], f"mvp:{self.session_id}")
        self.school.cast_mvp_vote(self.session_id, self.ids["Aspen"], "Birch")
        self.school.wipe_live_sessions_for_class(self.class_id)
        self.assertEqual(winner_mod.voter_count(self.school.conn, self.session_id), 0)
        self.assertEqual(winner_mod.tally_map(self.school.conn, self.session_id), {})
        still = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM avatar_reward_grants WHERE source = 'mvp'"
        ).fetchone()
        self.assertEqual(int(still["n"]), 1)

    def test_a_tie_has_no_poll_and_a_split_vote_has_no_avatar(self) -> None:
        team_a = self._team_named("Team 1")
        team_b = self._team_named("Team 2")
        for team in (team_a, team_b):
            self.school.game.award_points(
                self.class_id,
                kind="team",
                target_id=int(team["id"]),
                amount=5,
                team_rule="team_only",
            )
        self.school.set_live_session_teacher_state(
            self.session_id, gamification_on=True
        )
        page = self._page("Aspen")
        self.assertEqual(page["winner_name"], "")
        self.assertFalse(page["poll"]["open"])
        self.assertIsNone(page["mvp"])
        with self.assertRaises(ValueError) as tied:
            self.school.cast_mvp_vote(self.session_id, self.ids["Aspen"], "Birch")
        self.assertEqual(str(tied.exception), "No winning team yet")

    def test_consensus_counts_and_a_rank_team_row_does_not_double_it(self) -> None:
        team_a = self._team_named("Team 1")
        item_id = self._insert_item("mck240-consensus")
        now = "2026-10-16T12:00:00"
        with self.school._lock:
            self.school.conn.execute(
                """
                INSERT INTO live_group_responses (
                    live_item_id, team_id, awarded_points, created_at, updated_at
                ) VALUES (?, ?, 2, ?, ?)
                """,
                (item_id, int(team_a["id"]), now, now),
            )
            self.school.conn.commit()
        for codename in ("Aspen", "Birch"):
            self.school.game.award_points(
                self.class_id,
                kind="student",
                target_id=self.ids[codename],
                amount=2,
            )
        self.school.set_live_session_teacher_state(
            self.session_id, gamification_on=True
        )
        counted = {row["name"]: row["score"] for row in self._page("Aspen")["scoreboard"]}
        self.assertEqual(counted["Team 1"], 4)
        with self.school._lock:
            self.school.conn.execute(
                """
                INSERT INTO live_rank_team_awards (
                    live_item_id, team_id, points, team_rule, created_at, updated_at
                ) VALUES (?, ?, 2, 'each_member', ?, ?)
                """,
                (item_id, int(team_a["id"]), now, now),
            )
            self.school.conn.commit()
        excluded = {row["name"]: row["score"] for row in self._page("Aspen")["scoreboard"]}
        self.assertEqual(excluded["Team 1"], 0)

    def _assert_no_id_pair(self, value: object, ballots: dict[int, int]) -> None:
        """Fail when one object holds both a voter id and their nominee id.

        Args:
            value: Student JSON tree.
            ballots: Voter roster id to nominee roster id.
        """
        if isinstance(value, dict):
            ints = [item for item in value.values() if isinstance(item, int)]
            for voter_id, nominee_id in ballots.items():
                if voter_id in ints and nominee_id in ints and voter_id != nominee_id:
                    self.fail(f"voter {voter_id} paired with {nominee_id} in {value}")
            for child in value.values():
                self._assert_no_id_pair(child, ballots)
        elif isinstance(value, list):
            ints = [item for item in value if isinstance(item, int)]
            for voter_id, nominee_id in ballots.items():
                if voter_id in ints and nominee_id in ints and voter_id != nominee_id:
                    self.fail(f"voter {voter_id} paired with {nominee_id} in {value}")
            for child in value:
                self._assert_no_id_pair(child, ballots)

    def test_students_never_see_who_voted_for_whom(self) -> None:
        """Student state and the vote API do not pair a voter with a choice."""
        self._score_group_and_solo()
        self.school.set_live_session_teacher_state(
            self.session_id, gamification_on=True
        )
        ballots = {
            self.ids["Aspen"]: self.ids["Birch"],
            self.ids["Birch"]: self.ids["Aspen"],
        }
        self._bind("Aspen")
        first = self.client.post(
            "/api/student/mvp-vote",
            json={"nominee_codename": "Birch"},
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.get_json(), {"ok": True, "voted": True})
        self.assertNotIn("Birch", first.get_data(as_text=True))
        self.assertTrue(winner_mod.student_payload_hides_ballots(first.get_json()))
        self._bind("Birch")
        second = self.client.post(
            "/api/student/mvp-vote",
            json={"nominee_codename": "Aspen"},
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.get_json(), {"ok": True, "voted": True})
        self.assertNotIn("Aspen", second.get_data(as_text=True))
        for codename in ("Aspen", "Birch", "Cedar", "Dale"):
            self._bind(codename)
            state = self.client.get("/api/student/state")
            self.assertEqual(state.status_code, 200)
            body = state.get_json()
            page = body.get("gamification") or {}
            self.assertTrue(winner_mod.student_payload_hides_ballots(page))
            self.assertFalse(_walk_keys(body) & {"voter_id", "nominee_id"})
            self.assertEqual(
                page["copy"]["winner.hidden"],
                "Votes are hidden from classmates",
            )
            blob = json.dumps(page)
            self.assertNotIn("Aspen voted for Birch", blob)
            self.assertNotIn("Birch voted for Aspen", blob)
            self.assertNotIn('"Aspen": "Birch"', blob)
            self.assertNotIn('"Birch": "Aspen"', blob)
            self._assert_no_id_pair(page, ballots)
            home = self.client.get("/student/home")
            home_text = home.get_data(as_text=True)
            self.assertNotIn("voter_id", home_text)
            self.assertNotIn("nominee_id", home_text)
            self.assertNotIn("Aspen voted for Birch", home_text)

    def test_staff_hard_delete_removes_vote_rows(self) -> None:
        """Hard-deleting a staff member removes that class's MVP vote rows."""
        self._score_group_and_solo()
        self.school.set_live_session_teacher_state(
            self.session_id, gamification_on=True
        )
        self.school.cast_mvp_vote(self.session_id, self.ids["Aspen"], "Birch")
        self.assertEqual(winner_mod.voter_count(self.school.conn, self.session_id), 1)
        self.assertEqual(
            winner_mod.tally_map(self.school.conn, self.session_id)[self.ids["Birch"]],
            1,
        )
        self.school.delete_staff_permanently(self.teacher_id, 999999)
        self.assertEqual(winner_mod.voter_count(self.school.conn, self.session_id), 0)
        self.assertEqual(winner_mod.tally_map(self.school.conn, self.session_id), {})

    def test_copy_keys_are_in_the_staff_and_student_shells(self) -> None:
        html = (LMS_DIR / "templates" / "staff" / "course.html").read_text(encoding="utf-8")
        self.assertIn('id="ap-gamification-toggle"', html)
        self.assertIn('data-copy-key="winner.toggle"', html)
        self.assertNotIn('id="ap-gamification-toggle" checked', html)
        home = (LMS_DIR / "templates" / "student" / "home.html").read_text(encoding="utf-8")
        self.assertIn('id="gamification-winner"', home)
        js = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn("winner.poll_prompt", js)
        self.assertIn("paintGamificationWinner", js)
        staff = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("gamification_on", staff)
        css = (LMS_DIR / "static" / "student-portal.css").read_text(encoding="utf-8")
        self.assertIn(".gamification-winner .winner-poll-options", css)
        self.assertIn("winner.mvp_reward", WINNER_COPY)
        self.assertEqual(WINNER_COPY["winner.mvp_reward"], "Avatar reward")
        self.assertEqual(WINNER_COPY["winner.hidden"], "Votes are hidden from classmates")
        self.assertIn('data-copy-key="winner.hidden"', html)
        self.assertIn("Votes are hidden from classmates", html)
        self.assertIn("winner.hidden", js)
        self.assertNotIn("anonymous", WINNER_COPY.values())

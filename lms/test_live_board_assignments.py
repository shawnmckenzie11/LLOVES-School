#!/usr/bin/env python3
"""Stable whiteboard team buckets across lock restore (MCK-218)."""

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

from app import create_app  # noqa: E402
from live_board_assignments import assignment_board_key, board_key_for_sort_order  # noqa: E402


class LiveBoardAssignmentTests(unittest.TestCase):
    """Board keys stay on team:<sort_order> when game team rows are reminted."""

    def setUp(self) -> None:
        """One staff class, live session, and four-student roster."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.staff = self.app.test_client()
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
                "codenames": ["Maple", "Aspen", "Birch", "Cedar"],
            },
        )
        self.assertEqual(created.status_code, 200, created.get_json())
        self.class_id = int(created.get_json()["class"]["id"])
        run = self.staff.post(
            f"/staff/class/{self.class_id}/run-live",
            follow_redirects=False,
        )
        self.assertEqual(run.status_code, 302)
        live = self.school.get_active_live_session_for_class(self.class_id)
        assert live is not None
        self.session_id = int(live["id"])
        self.run_key = self.school.live_board_run_key(self.session_id)

    def tearDown(self) -> None:
        """Close sqlite and remove the temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _assign(self) -> None:
        """Begin attendance and assign two teams via the real API."""
        begin = self.staff.post(
            f"/api/classes/{self.class_id}/begin",
            json={"meeting_date": "2026-09-09"},
        )
        self.assertEqual(begin.status_code, 200, begin.get_json())
        ids = [int(student["id"]) for student in begin.get_json()["students"]]
        self.student_id = ids[0]
        self.staff.post(
            f"/api/classes/{self.class_id}/game/attendance",
            json={"present_ids": ids, "meeting_date": "2026-09-09"},
        )
        assigned = self.staff.post(
            f"/api/classes/{self.class_id}/game/assign",
            json={"n_teams": 2, "mode": "random", "present_ids": ids},
        )
        self.assertEqual(assigned.status_code, 200, assigned.get_json())

    def test_assign_writes_team_sort_order_board_key(self) -> None:
        """Generate teams records team:0 / team:1 buckets for the live run."""
        self._assign()
        key = assignment_board_key(
            self.school.conn,
            class_id=self.class_id,
            run_key=self.run_key,
            student_id=self.student_id,
        )
        self.assertTrue(str(key or "").startswith("team:"))
        access = self.school.student_board_access(self.session_id, self.student_id)
        self.assertEqual(access["collab_key"], key)

    def test_simulated_restart_keeps_board_key(self) -> None:
        """A new SchoolDB connection still resolves the same collab bucket."""
        self._assign()
        before = self.school.student_live_board_key(self.session_id, self.student_id)
        self.school.game._team_index_cache.clear()
        after = self.school.student_live_board_key(self.session_id, self.student_id)
        self.assertEqual(before, after)

    def test_restore_after_wipe_keeps_board_key(self) -> None:
        """Wiped game rows + lock restore do not move the student's board bucket."""
        self._assign()
        before = self.school.student_live_board_key(self.session_id, self.student_id)
        with self.school.game._lock:
            game = self.school.game._game_row(self.class_id)
            game_id = int(game["id"])
            self.school.game.conn.execute(
                "DELETE FROM game_memberships WHERE game_id = ?", (game_id,)
            )
            self.school.game.conn.execute(
                "DELETE FROM game_teams WHERE game_id = ?", (game_id,)
            )
            self.school.game.conn.commit()
        self.school.game._forget_team_index(class_id=self.class_id)
        self.school.game.restore_team_lock(self.class_id, create=False)
        after = self.school.student_live_board_key(self.session_id, self.student_id)
        self.assertEqual(before, after)

    def test_double_assign_is_idempotent(self) -> None:
        """ON CONFLICT DO NOTHING keeps the first bucket when assign runs twice."""
        self._assign()
        first = assignment_board_key(
            self.school.conn,
            class_id=self.class_id,
            run_key=self.run_key,
            student_id=self.student_id,
        )
        self.school.record_live_board_assignment_for_student(
            self.session_id, self.student_id, 99
        )
        second = assignment_board_key(
            self.school.conn,
            class_id=self.class_id,
            run_key=self.run_key,
            student_id=self.student_id,
        )
        self.assertEqual(first, second)

    def test_test_student_excluded_from_assignments(self) -> None:
        """Test Student never receives a live_board_assignments row."""
        test_row = self.school.ensure_class_test_student(self.class_id)
        assert test_row is not None
        self._assign()
        key = assignment_board_key(
            self.school.conn,
            class_id=self.class_id,
            run_key=self.run_key,
            student_id=int(test_row["student_id"]),
        )
        self.assertIsNone(key)
        collab = self.school.student_live_board_key(
            self.session_id, int(test_row["student_id"])
        )
        self.assertEqual(collab, "shared")

    def test_discarded_run_deletes_assignment_rows(self) -> None:
        """Discarding a test run clears its board assignment rows."""
        self._assign()
        self.school.conn.execute(
            """
            INSERT INTO participation_items (
                class_id, run_key, live_module, live_slot, question_key,
                item_json, prompt_kind, offered_at
            ) VALUES (?, ?, 'M1', 'C1', 'item:q0', '{}', 'poll', '2026-09-09T12:00:00')
            """,
            (self.class_id, self.run_key),
        )
        self.school.conn.commit()
        self.school.set_participation_run_discarded(
            self.class_id, self.run_key, True, user_id=int(self.teacher["id"])
        )
        key = assignment_board_key(
            self.school.conn,
            class_id=self.class_id,
            run_key=self.run_key,
            student_id=self.student_id,
        )
        self.assertIsNone(key)

    def _roster_by_codename(self) -> dict[str, int]:
        """Begin attendance and return codename → roster id."""
        begin = self.staff.post(
            f"/api/classes/{self.class_id}/begin",
            json={"meeting_date": "2026-09-09"},
        )
        self.assertEqual(begin.status_code, 200, begin.get_json())
        return {
            str(student["codename"]): int(student["id"])
            for student in begin.get_json()["students"]
        }

    def _place_late_joiner_on_second_team(self, *, scoring_live: bool) -> int:
        """Leave Team 2 smaller, then join the missing student through the screen path.

        The student class-code screen and the dashboard rejoin both call
        ``join_live_class_session``. Before Start Round the open game is
        still ``names``, so that lands in ``_assign_joiner_if_teams_locked``.
        After Start Round the game is ``live``, so it lands in
        ``admit_late_joiner``.

        Args:
            scoring_live: When True, start a scoring round before the join.

        Returns:
            Roster id of the student who joined late.
        """
        roster = self._roster_by_codename()
        present = [roster["Maple"], roster["Aspen"], roster["Birch"]]
        self.staff.post(
            f"/api/classes/{self.class_id}/game/attendance",
            json={"present_ids": present, "meeting_date": "2026-09-09"},
        )
        assigned = self.staff.post(
            f"/api/classes/{self.class_id}/game/assign",
            json={
                "n_teams": 2,
                "mode": "manual",
                "present_ids": present,
                "assignments": [
                    {"student_id": roster["Maple"], "team_index": 0},
                    {"student_id": roster["Aspen"], "team_index": 0},
                    {"student_id": roster["Birch"], "team_index": 1},
                ],
            },
        )
        self.assertEqual(assigned.status_code, 200, assigned.get_json())
        first_key = assignment_board_key(
            self.school.conn,
            class_id=self.class_id,
            run_key=self.run_key,
            student_id=roster["Maple"],
        )
        self.assertEqual(first_key, board_key_for_sort_order(0))
        if scoring_live:
            self.school.game.start_live_with_rounds(
                self.class_id, [{"kind": "open", "minutes": 20}]
            )
            self.assertEqual(
                str(self.school.game._game_row(self.class_id)["status"]), "live"
            )
        late_id = roster["Cedar"]
        self.school.join_live_class_session(
            self.session_id, late_id, codename="Cedar"
        )
        return late_id

    def _assert_late_joiner_pinned_to_second_team(self, late_id: int) -> None:
        """The joiner's membership sort order and board pin are both 1."""
        state = self.school.game.game_state(self.class_id)
        home = next(
            team
            for team in state["teams"]
            if any(int(member["id"]) == late_id for member in team["members"])
        )
        self.assertEqual(int(home["sort_order"]), 1)
        key = assignment_board_key(
            self.school.conn,
            class_id=self.class_id,
            run_key=self.run_key,
            student_id=late_id,
        )
        self.assertEqual(key, "team:1")
        self.assertEqual(
            self.school.student_live_board_key(self.session_id, late_id), "team:1"
        )

    def test_locked_teams_late_join_pins_second_team(self) -> None:
        """A joiner placed on sort_order 1 is pinned to team:1, not team:0."""
        late_id = self._place_late_joiner_on_second_team(scoring_live=False)
        self._assert_late_joiner_pinned_to_second_team(late_id)

    def test_join_screen_after_scoring_writes_matching_pin(self) -> None:
        """After Start Round, the class-code join writes a pin for that team.

        ``join_live_class_session`` calls ``admit_late_joiner`` once the game
        status is ``live``. That path has to insert the pin row itself.
        """
        late_id = self._place_late_joiner_on_second_team(scoring_live=True)
        self._assert_late_joiner_pinned_to_second_team(late_id)

    def test_reopen_app_keeps_sort_order_board_key(self) -> None:
        """Closing the database and booting again keeps the same collab bucket."""
        self._assign()
        before = self.school.student_live_board_key(self.session_id, self.student_id)
        self.assertTrue(str(before).startswith("team:"))
        db_path = Path(self.tmp.name) / "lloves.sqlite"
        data_dir = Path(self.tmp.name)
        self.school.close()
        reopened = create_app(db_path=db_path, data_dir=data_dir, testing=True)
        self.school = reopened.config["SCHOOL_DB"]
        after = self.school.student_live_board_key(self.session_id, self.student_id)
        self.assertEqual(before, after)

    def test_restore_logs_pin_for_missing_team(self) -> None:
        """A pin whose sort order is gone is logged and does not raise."""
        self._assign()
        self.school.conn.execute(
            """
            UPDATE live_board_assignments
            SET team_sort_order = 9, board_key = 'team:9'
            WHERE class_id = ? AND student_id = ?
            """,
            (self.class_id, self.student_id),
        )
        self.school.conn.commit()
        with self.school.game._lock:
            game = self.school.game._game_row(self.class_id)
            game_id = int(game["id"])
            self.school.game.conn.execute(
                "DELETE FROM game_memberships WHERE game_id = ?", (game_id,)
            )
            self.school.game.conn.execute(
                "DELETE FROM game_teams WHERE game_id = ?", (game_id,)
            )
            self.school.game.conn.commit()
        self.school.game._forget_team_index(class_id=self.class_id)
        with self.assertLogs("board_assignment_restore", level="WARNING") as caught:
            self.school.game.restore_team_lock(self.class_id, create=False)
        self.assertTrue(
            any(f"student_id={self.student_id}" in line and "sort_order=9" in line for line in caught.output)
        )
        self.assertIsNone(
            self.school.student_team_id_for_class(self.class_id, self.student_id)
        )


if __name__ == "__main__":
    unittest.main()

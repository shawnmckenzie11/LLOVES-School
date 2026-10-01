#!/usr/bin/env python3
"""MCK-72: End Live Class adds participation on top of manual points.

End used to write ``points = n`` (the +1/question count) for every credited
student, which wiped the teacher's manual awards from that day's column.
Shawn's call (Oct 1, 2026): one combined column, credit added on top of
manual points, never overwriting them, and once per class run.
"""

from __future__ import annotations

import unittest
from typing import Any

import test_live_shell as shell


class EndParticipationAddsTests(shell.LiveShellTests):
    """Reuse the live-shell fixture. Only the tests below run here."""

    def _live_game_with_award(self, student_id: int, amount: int) -> int:
        """Open a live game with this student present and award points."""
        game = self.school.game
        with game._lock:
            roster = [
                int(row["id"])
                for row in game.conn.execute(
                    "SELECT id FROM students WHERE class_id = ? ORDER BY id",
                    (self.class_id,),
                )
            ]
        other = next(x for x in roster if x != int(student_id))
        game.begin_game(self.class_id)
        game.save_attendance(self.class_id, [student_id, other])
        teams = game.assign_teams(
            self.class_id,
            2,
            "manual",
            assignments=[
                {"student_id": student_id, "team_index": 0},
                {"student_id": other, "team_index": 1},
            ],
        )
        game.rename_teams(
            self.class_id,
            [{"id": team["id"], "name": team["name"]} for team in teams["teams"]],
        )
        game.award_points(
            self.class_id, kind="student", target_id=student_id, amount=amount
        )
        return int(game._game_row(self.class_id)["session_id"])

    def _cell(self, session_id: int, student_id: int) -> dict[str, Any]:
        """One ``session_scores`` row."""
        with self.school.game._lock:
            row = self.school.game.conn.execute(
                """
                SELECT points, points_r1, points_r2, points_r3
                FROM session_scores WHERE session_id = ? AND student_id = ?
                """,
                (session_id, student_id),
            ).fetchone()
        return dict(row)

    def test_end_route_adds_credit_on_top_of_manual_points(self) -> None:
        """+5 by hand, n questions answered: the column shows 5 + n."""
        sid, student_id = self._open_live_with_aspen_answers()
        column = self._live_game_with_award(student_id, 5)
        before = self._cell(column, student_id)
        self.assertEqual(float(before["points"]), 5.0)
        credits = self.school.participation_round_credits_for_class(self.class_id)
        n = int(credits.get(student_id) or 0)
        self.assertGreaterEqual(n, 1, credits)
        ended = self.client.post(
            f"/staff/class/{self.class_id}/end-live", follow_redirects=False
        )
        self.assertEqual(ended.status_code, 302)
        after = self._cell(column, student_id)
        self.assertEqual(float(after["points"]), 5.0 + n)
        # Round buckets keep the manual award and gain the +1 flags.
        self.assertEqual(
            float(after["points_r1"]), float(before["points_r1"]) + 1.0
        )

    def test_repeat_credit_for_one_run_does_not_double(self) -> None:
        """A live sync, then End, then End again: the credit lands once."""
        student_id = self._aspen_id()
        column = self._live_game_with_award(student_id, 5)
        game = self.school.game
        game.write_live_participation(self.class_id, {student_id: 2}, run_key="run-a")
        self.assertEqual(float(self._cell(column, student_id)["points"]), 7.0)
        game.write_live_participation(self.class_id, {student_id: 3}, run_key="run-a")
        self.assertEqual(float(self._cell(column, student_id)["points"]), 8.0)
        game.persist_end_class_column(
            self.class_id, [student_id], {student_id: 3}, run_key="run-a"
        )
        self.assertEqual(float(self._cell(column, student_id)["points"]), 8.0)
        game.persist_end_class_column(
            self.class_id, [student_id], {student_id: 3}, run_key="run-a"
        )
        self.assertEqual(float(self._cell(column, student_id)["points"]), 8.0)

    def test_a_second_run_on_the_same_column_adds_its_own_credit(self) -> None:
        """Two runs credit the same column: each run's credit counts once."""
        student_id = self._aspen_id()
        column = self._live_game_with_award(student_id, 5)
        game = self.school.game
        game.write_live_participation(self.class_id, {student_id: 2}, run_key="run-a")
        game.write_live_participation(self.class_id, {student_id: 1}, run_key="run-b")
        game.write_live_participation(self.class_id, {student_id: 1}, run_key="run-b")
        self.assertEqual(float(self._cell(column, student_id)["points"]), 8.0)


def load_tests(
    loader: unittest.TestLoader, _tests: Any, _pattern: Any
) -> unittest.TestSuite:
    """Run only this file's tests, not the inherited live-shell ones."""
    names = [
        name
        for name, value in vars(EndParticipationAddsTests).items()
        if name.startswith("test_") and callable(value)
    ]
    return unittest.TestSuite(
        EndParticipationAddsTests(name) for name in sorted(names)
    )


if __name__ == "__main__":
    unittest.main()

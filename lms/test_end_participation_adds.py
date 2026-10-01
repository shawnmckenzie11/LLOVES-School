#!/usr/bin/env python3
"""MCK-72: End Live Class adds participation on top of manual points.

End used to write ``points = n`` (the +1/question count) for every credited
student, which wiped the teacher's manual awards from that day's column.
Shawn's call (Oct 1, 2026): one combined column, credit added on top of
manual points, never overwriting them, and once per class run.
"""

from __future__ import annotations

import importlib.util
import multiprocessing
import sys
import unittest
from pathlib import Path
from typing import Any

import test_live_shell as shell
from paths import MGS_DIR


def _end_in_worker(
    db_path: str,
    data_dir: str,
    class_id: int,
    present: list[int],
    credits: dict[int, int],
    run_key: str,
    barrier: Any,
    out: Any,
) -> None:
    """One gunicorn-like worker: its own connection, then End at the barrier."""
    if str(MGS_DIR) not in sys.path:
        sys.path.append(str(MGS_DIR))
    spec = importlib.util.spec_from_file_location("mgs_db", MGS_DIR / "db.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("mgs_db", mod)
    spec.loader.exec_module(mod)
    game = mod.GameShowDB(Path(db_path), Path(data_dir))
    try:
        barrier.wait(timeout=30)
        result = game.persist_end_class_column(
            class_id, present, credits, run_key=run_key
        )
        out.put(("ok", result))
    except Exception as exc:  # noqa: BLE001 - reported to the parent
        out.put(("error", repr(exc)))
    finally:
        game.conn.close()


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

    def _columns(self) -> int:
        """Class-day columns (``sessions`` rows) for the class."""
        with self.school.game._lock:
            row = self.school.game.conn.execute(
                "SELECT COUNT(*) AS n FROM sessions WHERE class_id = ?",
                (self.class_id,),
            ).fetchone()
        return int(row["n"])

    def test_concurrent_ends_in_separate_processes_credit_once(self) -> None:
        """Ops repro: Ends racing in different workers added the credit twice."""
        student_id = self._aspen_id()
        column = self._live_game_with_award(student_id, 5)
        columns_before = self._columns()
        game = self.school.game
        db_path = str(game.conn.execute("PRAGMA database_list").fetchone()["file"])
        data_dir = str(Path(self.tmp.name))
        ctx = multiprocessing.get_context("spawn")
        workers = 4
        barrier = ctx.Barrier(workers)
        out = ctx.Queue()
        procs = [
            ctx.Process(
                target=_end_in_worker,
                args=(
                    db_path,
                    data_dir,
                    self.class_id,
                    [student_id],
                    {student_id: 3},
                    "run-race",
                    barrier,
                    out,
                ),
            )
            for _ in range(workers)
        ]
        for proc in procs:
            proc.start()
        results = [out.get(timeout=60) for _ in procs]
        for proc in procs:
            proc.join(timeout=60)
        self.assertEqual([kind for kind, _ in results], ["ok"] * workers, results)
        self.assertEqual(float(self._cell(column, student_id)["points"]), 8.0)
        self.assertEqual(self._columns(), columns_before, "duplicate class-day column")
        wrote = [r for _, r in results if r and not r.get("already_persisted")]
        self.assertEqual(len(wrote), 1, results)

    def test_second_end_for_a_run_adds_no_column(self) -> None:
        """A repeat End (or a retry after close failed) leaves no empty column."""
        student_id = self._aspen_id()
        self._live_game_with_award(student_id, 5)
        game = self.school.game
        columns_before = self._columns()
        first = game.persist_end_class_column(
            self.class_id, [student_id], {student_id: 2}, run_key="run-a"
        )
        self.assertFalse(first.get("already_persisted"))
        second = game.persist_end_class_column(
            self.class_id, [student_id], {student_id: 2}, run_key="run-a"
        )
        self.assertTrue(second.get("already_persisted"), second)
        self.assertEqual(self._columns(), columns_before)

    def test_failed_end_write_can_be_retried(self) -> None:
        """A write that raises releases the claim, so the retry persists."""
        student_id = self._aspen_id()
        column = self._live_game_with_award(student_id, 5)
        game = self.school.game
        real = game._add_live_credit_unlocked

        def boom(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("injected")

        game._add_live_credit_unlocked = boom
        try:
            with self.assertRaises(RuntimeError):
                game.persist_end_class_column(
                    self.class_id, [student_id], {student_id: 2}, run_key="run-a"
                )
        finally:
            game._add_live_credit_unlocked = real
        # Rolled back: the column is still open and unchanged.
        self.assertEqual(float(self._cell(column, student_id)["points"]), 5.0)
        again = game.persist_end_class_column(
            self.class_id, [student_id], {student_id: 2}, run_key="run-a"
        )
        self.assertFalse(again.get("already_persisted"), again)
        self.assertEqual(float(self._cell(column, student_id)["points"]), 7.0)

    def test_credit_can_drop_to_zero(self) -> None:
        """A sync credited 2, End finds 0: the 2 is taken back."""
        student_id = self._aspen_id()
        column = self._live_game_with_award(student_id, 5)
        game = self.school.game
        game.write_live_participation(self.class_id, {student_id: 2}, run_key="run-a")
        self.assertEqual(float(self._cell(column, student_id)["points"]), 7.0)
        game.persist_end_class_column(self.class_id, [student_id], {}, run_key="run-a")
        self.assertEqual(float(self._cell(column, student_id)["points"]), 5.0)

    def test_lowering_a_credit_never_goes_negative(self) -> None:
        """Teacher zeroed the cell, then the credit dropped: points stay 0."""
        student_id = self._aspen_id()
        column = self._live_game_with_award(student_id, 1)
        game = self.school.game
        game.write_live_participation(self.class_id, {student_id: 3}, run_key="run-a")
        with game._lock:
            game.conn.execute(
                """
                UPDATE session_scores
                SET points = 0, points_r1 = 0, points_r2 = 0, points_r3 = 0
                WHERE session_id = ? AND student_id = ?
                """,
                (column, student_id),
            )
        game.write_live_participation(self.class_id, {student_id: 2}, run_key="run-a")
        cell = self._cell(column, student_id)
        for key in ("points", "points_r1", "points_r2", "points_r3"):
            self.assertGreaterEqual(float(cell[key]), 0.0, cell)


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

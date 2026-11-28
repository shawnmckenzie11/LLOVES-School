#!/usr/bin/env python3
"""MCK-542: recompute inflated End Class attendance and credit columns.

Seeded demo data only. Assertions never print names, emails, codes, or ids.
"""

from __future__ import annotations

import contextlib
import io
import sqlite3
import sys
import unittest
import uuid
from pathlib import Path

import test_live_shell as shell

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import mck542_recompute_attendance as cli  # noqa: E402
from mck542_attendance_recompute import (  # noqa: E402
    LOG_TABLE,
    apply_recompute,
    plan_recompute,
    revert_run,
)


class RecomputeAttendanceTests(unittest.TestCase):
    """Two days, two different students, with day 2 inflated like the bug."""

    def setUp(self) -> None:
        """Reuse the live-shell app, then end day 1 (Aspen) and day 2 (Birch)."""
        self._shell = shell.LiveShellTests("setUp")
        self._shell.setUp()
        self.school = self._shell.school
        self.game = self.school.game
        self.class_id = self._shell.class_id
        self.aspen = self._student("Aspen")
        self.birch = self._student("Birch")
        self.run1, self.col1, self.sid1 = self._end_day(self.aspen, "Aspen")
        self.run2, self.col2, self.sid2 = self._end_day(self.birch, "Birch")
        self.cedar = self._add_student()
        # Day 2 as the old rule left it: Aspen (day 1 only) marked present
        # and credited one question on day 2.
        self._set_present(self.col2, self.aspen, 1)
        with self.game._lock, self.game._write_txn():
            self.game._add_live_credit_unlocked(
                self.col2, self.aspen, self.run2, 1
            )

    def tearDown(self) -> None:
        self._shell.tearDown()

    def _student(self, codename: str) -> int:
        """Roster id of a seeded student."""
        return int(
            self.game.find_student_by_codename(self.class_id, codename)["id"]
        )

    def _end_day(self, student: int, codename: str) -> tuple[str, int, int]:
        """Run and End one live class with one student. Returns run, column, live id."""
        live = self.school.start_live_class_session(
            self.class_id, int(self._shell.teacher["id"])
        )
        sid = int(live["id"])
        self.school.join_live_class_session(sid, student, codename=codename)
        if codename == "Birch":
            # Birch really answers one question on day 2.
            from minds_on import MINDS_ON_CHOICES

            self.school.ensure_waiting_room_minds_on(sid)
            prompt = self.school.get_active_live_prompt(sid)
            self.school.submit_live_prompt_response(
                int(prompt["id"]), student, {"choice": MINDS_ON_CHOICES[0]}
            )
        run = str(live["run_key"])
        result = self.school.finish_live_class(
            self.class_id, celebrate=True, run_key=run, live_run=True
        )
        self.assertTrue(result.get("ok"), result)
        with self.school._lock:
            row = self.school.conn.execute(
                "SELECT mgs_session_id FROM live_class_sessions WHERE id = ?",
                (sid,),
            ).fetchone()
        return run, int(row["mgs_session_id"]), sid

    def _add_student(self) -> int:
        """A rostered student who attended nothing, with score rows."""
        with self.game._lock:
            cur = self.game.conn.execute(
                "INSERT INTO students (class_id, last_display, first_name, row_uid) "
                "VALUES (?, 'X', 'Cedar', ?)",
                (self.class_id, uuid.uuid4().hex),
            )
            sid = int(cur.lastrowid)
            for col in (self.col1, self.col2):
                self.game.conn.execute(
                    "INSERT OR IGNORE INTO session_scores (session_id, student_id) "
                    "VALUES (?, ?)",
                    (col, sid),
                )
        return sid

    def _set_present(self, column: int, student: int, value: int) -> None:
        """Write one present flag directly (what a manual edit or the bug leaves)."""
        with self.game._lock:
            self.game.conn.execute(
                "INSERT OR IGNORE INTO session_scores (session_id, student_id) "
                "VALUES (?, ?)",
                (column, student),
            )
            self.game.conn.execute(
                "UPDATE session_scores SET present = ? "
                "WHERE session_id = ? AND student_id = ?",
                (value, column, student),
            )

    def _present(self, column: int) -> set[int]:
        """Present student ids in a column."""
        with self.game._lock:
            rows = self.game.conn.execute(
                "SELECT student_id FROM session_scores WHERE session_id = ? AND present = 1",
                (column,),
            ).fetchall()
        return {int(r["student_id"]) for r in rows}

    def _points(self, column: int, student: int) -> float:
        """Participation points of one student in one column."""
        with self.game._lock:
            row = self.game.conn.execute(
                "SELECT points FROM session_scores WHERE session_id = ? AND student_id = ?",
                (column, student),
            ).fetchone()
        return float(row["points"])

    def _attendee_rows(self) -> int:
        """Total attendee rows (must never shrink)."""
        with self.school._lock:
            return int(
                self.school.conn.execute(
                    "SELECT COUNT(*) FROM live_session_attendees"
                ).fetchone()[0]
            )

    def _state(self) -> list[tuple]:
        """Every score row and ledger row, to prove nothing else moved."""
        with self.game._lock:
            scores = self.game.conn.execute(
                "SELECT * FROM session_scores ORDER BY session_id, student_id"
            ).fetchall()
            ledger = self.game.conn.execute(
                "SELECT * FROM live_participation_credits ORDER BY run_key, student_id"
            ).fetchall()
        return [tuple(r) for r in scores] + [tuple(r) for r in ledger]

    def _log_rows(self) -> int:
        """Rows in the change log, or 0 when the table does not exist."""
        with self.game._lock:
            try:
                return int(
                    self.game.conn.execute(f"SELECT COUNT(*) FROM {LOG_TABLE}").fetchone()[0]
                )
            except sqlite3.OperationalError:
                return 0

    def _cli(self, *args: str) -> tuple[int, str]:
        """Run the script against the test database; return exit code and output."""
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--db", str(self.school.db_path), *args])
        return code, out.getvalue() + err.getvalue()

    def test_dry_run_prints_counts_and_writes_nothing(self) -> None:
        """Default mode reports the change without touching any table."""
        before, attendees = self._state(), self._attendee_rows()
        code, text = self._cli()
        self.assertEqual(code, 0, text)
        self.assertEqual(self._state(), before)
        self.assertEqual(self._log_rows(), 0)
        self.assertEqual(self._attendee_rows(), attendees)
        self.assertIn("DRY RUN", text)
        self.assertIn("columns_scanned=2", text)
        self.assertIn("columns_would_change=1", text)
        self.assertIn("students_present_to_absent=1", text)
        self.assertIn("credit_students_lowered=1", text)
        self.assertIn("credits_unverifiable_left_alone=1", text)
        for word in ("Aspen", "Birch", "Cedar"):
            self.assertNotIn(word, text)

    def test_apply_needs_backup_flag(self) -> None:
        """--apply alone is refused and changes nothing."""
        before = self._state()
        code, _ = self._cli("--apply")
        self.assertEqual(code, 2)
        self.assertEqual(self._state(), before)

    def test_apply_corrects_day_two_only_and_keeps_manual_points(self) -> None:
        """Day 2 loses the day-1 student; day 1, attendee rows and manual points stay."""
        with self.game._lock:
            self.game.conn.execute(
                "UPDATE session_scores SET points = points + 5 "
                "WHERE session_id = ? AND student_id = ?",
                (self.col2, self.aspen),
            )
        birch_points = self._points(self.col2, self.birch)
        day1 = self._present(self.col1)
        attendees = self._attendee_rows()
        plans = plan_recompute(self.school)
        apply_recompute(self.school, plans)
        self.assertEqual(self._present(self.col2), {self.birch})
        self.assertEqual(self._present(self.col1), day1)
        self.assertEqual(self._points(self.col2, self.aspen), 5.0)
        self.assertEqual(self._points(self.col2, self.birch), birch_points)
        self.assertEqual(self._attendee_rows(), attendees)

    def test_cli_apply_then_dry_run_finds_nothing(self) -> None:
        """The script's own --apply writes, and a following dry run is clean."""
        code, text = self._cli("--apply", "--backup-confirmed")
        self.assertEqual(code, 0, text)
        self.assertIn("run_id:", text)
        self.assertEqual(self._present(self.col2), {self.birch})
        code, text = self._cli()
        self.assertIn("columns_would_change=0", text)

    def test_second_run_changes_nothing(self) -> None:
        """Apply twice: the second plan is empty and the log does not grow."""
        apply_recompute(self.school, plan_recompute(self.school))
        logged, state = self._log_rows(), self._state()
        again = plan_recompute(self.school)
        self.assertFalse(any(p.would_change for p in again))
        apply_recompute(self.school, again)
        self.assertEqual(self._log_rows(), logged)
        self.assertEqual(self._state(), state)

    def test_catchup_and_test_runs_are_excluded(self) -> None:
        """A ~cu session's column is neither scanned nor changed."""
        with self.game._lock:
            cur = self.game.conn.execute(
                "INSERT INTO sessions (class_id, starts_at, header_label, status) "
                "VALUES (?, '2026-11-02', 'cu', 'ended')",
                (self.class_id,),
            )
            cu_col = int(cur.lastrowid)
        for sid in (self.aspen, self.birch):
            self._set_present(cu_col, sid, 1)
        with self.school._lock:
            self.school.conn.execute(
                "INSERT INTO live_class_sessions (class_id, offering_id, session_code, "
                "status, started_at, ended_at, mgs_session_id, run_key) "
                "VALUES (?, ?, 'CUX', 'ended', '2026-11-02T10:00:00', "
                "'2026-11-02T11:00:00', ?, 'r~cu~q1')",
                (self.class_id, int(self._shell.offering["id"]), cu_col),
            )
            self.school.conn.commit()
        plans = plan_recompute(self.school)
        self.assertEqual(len(plans), 2)
        apply_recompute(self.school, plans)
        self.assertEqual(self._present(cu_col), {self.aspen, self.birch})

    def test_manual_edits_are_preserved(self) -> None:
        """Present flags the bug cannot explain stay; a manually changed column is skipped."""
        self._set_present(self.col2, self.cedar, 1)  # manual add, attended nothing
        apply_recompute(self.school, plan_recompute(self.school))
        self.assertEqual(self._present(self.col2), {self.birch, self.cedar})
        # A teacher marks an attendee absent after End: skip the column.
        self._set_present(self.col2, self.aspen, 1)
        self._set_present(self.col2, self.birch, 0)
        plans = plan_recompute(self.school)
        col2 = next(p for p in plans if p.column_id == self.col2)
        self.assertEqual(col2.conflicts, 1)
        self.assertFalse(col2.attendance_changes)
        apply_recompute(self.school, plans)
        self.assertEqual(self._present(self.col2), {self.aspen, self.cedar})

    def test_revert_restores_inflated_state(self) -> None:
        """--revert puts attendance and credit points back."""
        before = self._state()
        run_id = apply_recompute(self.school, plan_recompute(self.school))
        self.assertNotEqual(self._state(), before)
        done = revert_run(self.school, run_id)
        self.assertEqual(done, {"attendance": 1, "credits": 1})
        self.assertEqual(self._state(), before)


if __name__ == "__main__":
    unittest.main()

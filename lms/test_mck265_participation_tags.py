"""MCK-265: tag untagged participation sessions from the class calendar."""

from __future__ import annotations

import io
import os
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
sys.path.insert(0, str(LMS_DIR.parent / "scripts"))

from participation_schedule import participation_date_slots  # noqa: E402
from participation_tag_backfill import (  # noqa: E402
    apply_participation_tag_backfill,
    plan_participation_tag_backfill,
)
from test_participation_mck198 import Base  # noqa: E402


class ScheduleMapTests(unittest.TestCase):
    """Shawn's September anchors, including two slots on 23 Sep."""

    def test_september_module_one_includes_two_slots_on_sept_23(self) -> None:
        """M1 C4 and M1 O2 share 23 Sep when Friday office exists."""
        mapped = participation_date_slots("M/W/F", include_office=True)
        self.assertEqual(mapped[date(2026, 9, 14)], [("M1", "C1")])
        self.assertEqual(mapped[date(2026, 9, 16)], [("M1", "C2")])
        self.assertEqual(mapped[date(2026, 9, 18)], [("M1", "O1")])
        self.assertEqual(mapped[date(2026, 9, 21)], [("M1", "C3")])
        self.assertEqual(mapped[date(2026, 9, 23)], [("M1", "C4"), ("M1", "O2")])
        self.assertNotIn(date(2026, 10, 12), mapped)
        self.assertEqual(mapped[date(2026, 10, 14)], [("M3", "C2")])
        self.assertNotIn(("M3", "C1"), [pair for pairs in mapped.values() for pair in pairs])

    def test_without_friday_office_sept_23_is_only_c4(self) -> None:
        """Office slots are omitted when Friday office is not on the schedule."""
        mapped = participation_date_slots("M/W/F", include_office=False)
        self.assertNotIn(date(2026, 9, 18), mapped)
        self.assertEqual(mapped[date(2026, 9, 23)], [("M1", "C4")])
        self.assertEqual(mapped[date(2026, 9, 14)], [("M1", "C1")])


class TagBackfillTests(Base):
    """Apply fills blank item tags and leaves everything else alone."""

    def _blank(self, when: str) -> tuple[str, int]:
        """End a class, clear its module, and set the game session start.

        Args:
            when: Naive UTC ``starts_at`` for the game session.

        Returns:
            The run key and the game session id.
        """
        run_key = self.run_class(
            "M1",
            "C1",
            1,
            0,
            {self.students[0]: 1},
            wipe=False,
        )
        self._attach_column(run_key, when)
        return run_key, self._session_id(run_key)

    def _attach_column(self, run_key: str, when: str) -> None:
        """Give the run a participation column, then blank its module.

        Args:
            run_key: Counted participation run.
            when: Naive UTC start stored on the game session.
        """
        self.school.game.persist_end_class_column(
            self.class_id,
            [self.students[0]],
            {self.students[0]: 1},
            include_attendance=True,
            include_participation=True,
            run_key=run_key,
            create_game=True,
        )
        self.school.conn.execute(
            """
            UPDATE participation_items
               SET live_module = '', live_slot = ''
             WHERE class_id = ? AND run_key = ?
            """,
            (self.class_id, run_key),
        )
        self.school.conn.commit()
        session_id = self._session_id(run_key)
        self.school.game.conn.execute(
            "UPDATE sessions SET starts_at = ? WHERE id = ?",
            (when, session_id),
        )
        self.school.game.conn.commit()

    def _session_id(self, run_key: str) -> int:
        """Game session id stored on the run's participation credit."""
        row = self.school.game.conn.execute(
            "SELECT session_id FROM live_participation_credits WHERE run_key = ? LIMIT 1",
            (run_key,),
        ).fetchone()
        self.assertIsNotNone(row, run_key)
        return int(row["session_id"])

    def _modules(self, run_key: str) -> set[tuple[str, str]]:
        """Stored module and slot pairs for one run."""
        rows = self.school.conn.execute(
            """
            SELECT live_module, live_slot FROM participation_items
             WHERE class_id = ? AND run_key = ?
            """,
            (self.class_id, run_key),
        ).fetchall()
        return {(str(row["live_module"]), str(row["live_slot"])) for row in rows}

    def _marks(self) -> int:
        """Row count of participation module marks."""
        return int(
            self.school.conn.execute(
                "SELECT COUNT(*) AS n FROM participation_module_marks"
            ).fetchone()["n"]
        )

    def test_same_day_tags_and_unmapped_date_do_not_guess(self) -> None:
        """23 Sep is C4 then O2. A date off the calendar stays blank."""
        later, later_id = self._blank("2026-09-23T18:00:00")
        earlier, earlier_id = self._blank("2026-09-23T14:00:00")
        self.assertGreater(earlier_id, later_id)
        stray, _stray_id = self._blank("2026-08-01T14:00:00")
        kept = self.run_class(
            "M2", "C1", 1, 0, {self.students[0]: 1}, wipe=False
        )
        self._attach_column(kept, "2026-09-14T14:00:00")
        self.school.conn.execute(
            """
            UPDATE participation_items
               SET live_module = 'M2', live_slot = 'C1'
             WHERE class_id = ? AND run_key = ?
            """,
            (self.class_id, kept),
        )
        self.school.conn.commit()
        before_marks = self._marks()
        plan = plan_participation_tag_backfill(self.school)
        by_session = {step.session_id: step for step in plan}
        self.assertEqual((by_session[earlier_id].module, by_session[earlier_id].slot), ("M1", "C4"))
        self.assertEqual((by_session[later_id].module, by_session[later_id].slot), ("M1", "O2"))
        stray_step = next(step for step in plan if step.run_key == stray)
        self.assertTrue(stray_step.unmapped)
        self.assertNotIn(kept, {step.run_key for step in plan})
        written = apply_participation_tag_backfill(self.school, plan)
        self.assertGreater(written, 0)
        self.assertEqual(self._modules(earlier), {("M1", "C4")})
        self.assertEqual(self._modules(later), {("M1", "O2")})
        self.assertEqual(self._modules(stray), {("", "")})
        self.assertEqual(self._modules(kept), {("M2", "C1")})
        self.assertEqual(self._marks(), before_marks)
        again = plan_participation_tag_backfill(self.school)
        self.assertEqual({step.run_key for step in again}, {stray})
        self.assertEqual(apply_participation_tag_backfill(self.school, again), 0)
        self.assertEqual(self._modules(earlier), {("M1", "C4")})
        self.assertEqual(self._modules(stray), {("", "")})


class ScriptTests(Base):
    """The CLI defaults to a dry run and refuses production."""

    def test_production_is_refused_before_any_write(self) -> None:
        """Fly and ``/data`` are refused unless both production flags match."""
        from backfill_participation_tags import main, refusal_reason

        quiet = {
            "FLY_APP_NAME": "",
            "FLASK_ENV": "development",
            "LLOVES_DATA_DIR": "",
            "DATA_DIR": "",
            "LLOVES_DB": "",
        }
        with mock.patch.dict(os.environ, quiet, clear=False):
            self.assertIsNone(refusal_reason(Path("/tmp/local.sqlite")))
        with mock.patch.dict(os.environ, {"FLY_APP_NAME": "lloves-lms"}, clear=False):
            code = main(["--db", "/tmp/does-not-matter.sqlite"])
        self.assertEqual(code, 2)

    def test_dry_run_lists_a_tag_and_apply_is_idempotent(self) -> None:
        """Dry-run prints the proposal. Apply twice does not change the database."""
        from backfill_participation_tags import main

        run_key = self.run_class(
            "M1", "C1", 1, 0, {self.students[0]: 1}, wipe=False
        )
        self.school.game.persist_end_class_column(
            self.class_id,
            [self.students[0]],
            {self.students[0]: 1},
            include_attendance=True,
            include_participation=True,
            run_key=run_key,
            create_game=True,
        )
        self.school.conn.execute(
            """
            UPDATE participation_items
               SET live_module = '', live_slot = ''
             WHERE class_id = ? AND run_key = ?
            """,
            (self.class_id, run_key),
        )
        self.school.conn.commit()
        row = self.school.game.conn.execute(
            "SELECT session_id FROM live_participation_credits WHERE run_key = ?",
            (run_key,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.school.game.conn.execute(
            "UPDATE sessions SET starts_at = ? WHERE id = ?",
            ("2026-09-14T14:00:00", int(row["session_id"])),
        )
        self.school.game.conn.commit()
        self.school.game.set_current_class(self.class_id)
        pinned = str(self.class_id)
        db = str(Path(self.tmp.name) / "lloves.sqlite")
        env = {
            "FLY_APP_NAME": "",
            "FLASK_ENV": "development",
            "LLOVES_DATA_DIR": "",
            "DATA_DIR": "",
            "LLOVES_DB": "",
            "LOCAL_DEV_LOGIN": "",
        }

        def _run(argv: list[str]) -> tuple[int, str, str]:
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, env, clear=False), mock.patch(
                "sys.stdout", out
            ), mock.patch("sys.stderr", err):
                code = main(argv)
            return code, out.getvalue(), err.getvalue()

        self.school.close()
        code, text, err = _run(["--db", db])
        self.assertEqual(code, 0, err)
        self.assertIn("DRY RUN", text)
        self.assertIn("2026-09-14 M1 C1", text)
        self.assertIn("rows_changed=0", text)
        self.assertIn("DRY RUN, nothing written", text)
        code, applied, err = _run(["--apply", "--db", db])
        self.assertEqual(code, 0, err + applied)
        self.assertIn("APPLIED", applied)
        self.assertIn(f"current_class_id={pinned}->{pinned}", applied)
        self.assertNotIn("rows_changed=0", applied)
        code, second, err = _run(["--apply", "--db", db])
        self.assertEqual(code, 0, err + second)
        self.assertIn("untagged=0", second)
        self.assertIn("rows_changed=0", second)


if __name__ == "__main__":
    unittest.main()

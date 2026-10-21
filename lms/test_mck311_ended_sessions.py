#!/usr/bin/env python3
"""MCK-311: Start, Quit, and list-delete never remove another ended session."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from test_participation_mck198 import Base  # noqa: E402


class EndedSessionRetentionTests(Base):
    """Ended live-class rows survive the next Start and a different Quit."""

    def _end(self, *, is_test: bool = False, module: str = "M1", slot: str = "C1") -> int:
        """Start one class, stamp its lesson, and End it.

        Args:
            is_test: True for a rehearsal.
            module: Teacher module, such as ``M1``.
            slot: Teacher slot, such as ``C1``.

        Returns:
            ``live_class_sessions.id`` of the ended row.
        """
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=is_test
        )
        sid = int(live["id"])
        self.school.set_live_session_teacher_state(
            sid, live_module=module, live_slot=slot
        )
        self.school.finish_live_class(self.class_id, celebrate=True, live_run=True)
        return sid

    def _runs(self, run_key: str) -> int:
        """Count participation runs stored for this class and run key.

        Args:
            run_key: ``live_class_sessions.run_key``.
        """
        row = self.school.conn.execute(
            """
            SELECT COUNT(*) AS n FROM participation_runs
            WHERE class_id = ? AND run_key = ?
            """,
            (self.class_id, run_key),
        ).fetchone()
        return int(row["n"] or 0)

    def _grade_rows(self, session_id: int) -> int:
        """Count gradebook score rows for this live session's tracker column.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        live = self.school.get_live_session(session_id)
        tracker = None if live is None else live.get("mgs_session_id")
        if tracker in (None, ""):
            return 0
        row = self.school.game.conn.execute(
            "SELECT COUNT(*) AS n FROM session_scores WHERE session_id = ?",
            (int(tracker),),
        ).fetchone()
        return int(row["n"] or 0)

    def test_start_keeps_earlier_ended_sessions(self) -> None:
        """The next Start closes celebration and leaves every ended row."""
        first = self._end(module="M1", slot="C1")
        first_key = str(self.school.get_live_session(first)["run_key"])
        self.assertGreater(self._runs(first_key), 0)
        grades = self._grade_rows(first)
        second = self._end(module="M1", slot="C2")
        second_key = str(self.school.get_live_session(second)["run_key"])
        third = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.assertNotEqual(int(third["id"]), first)
        self.assertNotEqual(int(third["id"]), second)
        for sid, key in ((first, first_key), (second, second_key)):
            row = self.school.get_live_session(sid)
            self.assertIsNotNone(row)
            self.assertEqual(row["status"], "ended")
            self.assertFalse(self.school.session_is_celebrating(sid))
            self.assertEqual(str(row["run_key"]), key)
            self.assertGreater(self._runs(key), 0)
        self.assertEqual(self._grade_rows(first), grades)

    def test_quit_deletes_only_the_active_session(self) -> None:
        """Quit removes the running session and no earlier ended session."""
        ended = self._end()
        ended_key = str(self.school.get_live_session(ended)["run_key"])
        active = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        active_id = int(active["id"])
        self.school.finish_live_class(
            self.class_id, celebrate=False, persist=False
        )
        self.assertIsNone(self.school.get_live_session(active_id))
        kept = self.school.get_live_session(ended)
        self.assertIsNotNone(kept)
        self.assertEqual(kept["status"], "ended")
        self.assertEqual(str(kept["run_key"]), ended_key)
        self.assertGreater(self._runs(ended_key), 0)

    def test_lesson_slides_delete_keeps_attendance_and_participation(self) -> None:
        """Removing a Lesson Slides entry does not remove the class record."""
        sid = self._end(module="M2", slot="C1")
        row = self.school.get_live_session(sid)
        run_key = str(row["run_key"])
        grades = self._grade_rows(sid)
        self.school.delete_lesson_slides_entry(
            self.class_id, "M2", "C1", False, run_key
        )
        kept = self.school.get_live_session(sid)
        self.assertIsNotNone(kept)
        self.assertEqual(kept["status"], "ended")
        self.assertGreater(self._runs(run_key), 0)
        self.assertEqual(self._grade_rows(sid), grades)
        latest = [
            item
            for item in self.school.list_lesson_slides_latest(self.class_id)
            if str(item.get("run_key") or "") == run_key
        ]
        self.assertEqual(latest, [])

    def test_delete_test_keeps_other_ended_sessions(self) -> None:
        """Deleting one test run leaves the real ended class in place."""
        real = self._end(is_test=False, module="M3", slot="C1")
        real_key = str(self.school.get_live_session(real)["run_key"])
        test = self._end(is_test=True, module="M3", slot="C2")
        self.school.delete_test_live_class(self.class_id, test)
        self.assertIsNone(self.school.get_live_session(test))
        kept = self.school.get_live_session(real)
        self.assertIsNotNone(kept)
        self.assertEqual(kept["status"], "ended")
        self.assertEqual(int(kept["is_test"] or 0), 0)
        self.assertGreater(self._runs(real_key), 0)


if __name__ == "__main__":
    unittest.main()

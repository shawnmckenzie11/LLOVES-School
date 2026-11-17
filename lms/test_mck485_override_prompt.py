#!/usr/bin/env python3
"""MCK-485: Start warns only when Attendance still shows that day."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from test_mck471_delete_instance_metrics import _count  # noqa: E402
from test_participation_mck198 import Base  # noqa: E402

DAY = "2026-11-17"
OTHER_DAY = "2026-11-18"


class LoggedDayTests(Base):
    """The override list matches the attendance grid after a Lesson Slides delete."""

    def _end_marked(self, module: str, slot: str) -> tuple[int, str, int]:
        """End one marked class. See the Lesson Slides delete suite.

        Args:
            module: Teacher module, such as ``M1``.
            slot: Teacher slot, such as ``C1``.

        Returns:
            ``(live_class_sessions.id, run_key, tracker session id)``.
        """
        from test_mck471_delete_instance_metrics import DeleteInstanceMetricsTests

        return DeleteInstanceMetricsTests._end_marked(self, module, slot)

    def _pin_day(self, tracker_id: int, iso: str) -> None:
        """Point one tracker column at a calendar day.

        Args:
            tracker_id: ``sessions.id``.
            iso: ``YYYY-MM-DD`` meeting day.
        """
        self.school.conn.execute(
            "UPDATE sessions SET starts_at = ? WHERE id = ?",
            (f"{iso}T14:00:00", int(tracker_id)),
        )
        self.school.conn.commit()

    def _grid_mark(self, iso: str):
        """Attendance cell for the first roster row on ``iso``.

        Args:
            iso: ``YYYY-MM-DD`` meeting day.

        Returns:
            The grid mark, or None when that day has no mark.
        """
        grid = self.school.attendance_week_grid(self.class_id)
        return grid["cells"].get(f"{int(self.students[0])}:{iso}")

    def test_logged_check_is_true_when_the_grid_shows_the_day(self) -> None:
        """A day with a present mark is logged, and the grid shows it."""
        _sid, _run_key, tracker = self._end_marked("M1", "C1")
        self._pin_day(tracker, DAY)
        self.assertIn(DAY, self.school._logged_meeting_dates(self.class_id))
        self.assertIn(
            DAY, self.school.log_context_for_class(self.class_id)["logged_dates"]
        )
        self.assertIs(self._grid_mark(DAY), True)
        self.assertTrue(
            self.school.attendance_for_date(self.class_id, date.fromisoformat(DAY))["logged"]
        )

    def test_delete_clears_the_logged_check_for_that_day(self) -> None:
        """Deleting the Lesson Slides run leaves that day unlogged."""
        _sid, run_key, tracker = self._end_marked("M1", "C1")
        self._pin_day(tracker, DAY)
        self.school.delete_lesson_slides_entry(
            self.class_id, "M1", "C1", False, run_key
        )
        self.assertNotIn(DAY, self.school._logged_meeting_dates(self.class_id))
        self.assertNotIn(
            DAY, self.school.log_context_for_class(self.class_id)["logged_dates"]
        )
        self.assertIsNone(self._grid_mark(DAY))
        logged = self.school.attendance_for_date(self.class_id, date.fromisoformat(DAY))
        self.assertFalse(logged["logged"])

    def test_leftover_hidden_column_does_not_count_as_logged(self) -> None:
        """A column delete could not remove still does not mark the day logged.

        An open game keeps the tracker row. Attendance hides it. A second
        day that was not deleted stays logged.
        """
        _sid, run_key, tracker = self._end_marked("M1", "C1")
        _other_sid, _other_key, other = self._end_marked("M1", "C2")
        self._pin_day(tracker, DAY)
        self._pin_day(other, OTHER_DAY)
        self.school.conn.execute(
            """
            INSERT INTO games (class_id, session_id, status, created_at)
            VALUES (?, ?, 'live', ?)
            """,
            (self.class_id, int(tracker), f"{DAY}T14:00:00"),
        )
        self.school.conn.commit()
        self.school.delete_lesson_slides_entry(
            self.class_id, "M1", "C1", False, run_key
        )
        left = _count(
            self.school.conn,
            "SELECT COUNT(*) AS n FROM sessions WHERE id = ?",
            (int(tracker),),
        )
        self.assertEqual(left, 1)
        logged = self.school._logged_meeting_dates(self.class_id)
        self.assertNotIn(DAY, logged)
        self.assertIn(OTHER_DAY, logged)
        self.assertIsNone(self._grid_mark(DAY))
        self.assertIs(self._grid_mark(OTHER_DAY), True)


class OverrideDialogTests(unittest.TestCase):
    """Start asks with the in-page dialog, not a browser confirm."""

    def test_confirm_uses_dialog_not_window_confirm(self) -> None:
        """The override question is a dialog. Cancel does not start the class."""
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        html = (LMS_DIR / "templates" / "staff" / "course.html").read_text(encoding="utf-8")
        start = js.index("async function confirmOverrideIfLogged")
        body = js[start : js.index("function selectedRunKind", start)]
        self.assertNotIn("window.confirm", body)
        self.assertIn("set-class-override-dialog", body)
        self.assertIn("dialog.showModal()", body)
        self.assertIn('return choice === "continue"', body)
        self.assertIn("return false", body)
        reload = js[js.index("async function reloadLoggedDates") : start]
        self.assertIn('cache: "no-store"', reload)
        dialog_at = html.index('id="set-class-override-dialog"')
        dialog = html[dialog_at : html.index("</dialog>", dialog_at)]
        self.assertIn("end-live-dialog", dialog)
        self.assertIn('value="cancel"', dialog)
        self.assertIn(">Cancel<", dialog)
        self.assertIn('value="continue"', dialog)
        self.assertIn(">Continue<", dialog)
        self.assertIn("autofocus", dialog)
        apply = js[
            js.index("async function applyValidateDateChoice") : js.index(
                "function closeReservedLiveWindow"
            )
        ]
        self.assertIn("!(await confirmOverrideIfLogged(iso))", apply)
        self.assertLess(
            apply.index("confirmOverrideIfLogged"),
            apply.index("continueRunLiveAfterPrompt"),
        )


if __name__ == "__main__":
    unittest.main()

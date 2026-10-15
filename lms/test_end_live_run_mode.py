#!/usr/bin/env python3
"""MCK-233: scheduled-start window for End Live Class default mode."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))

from end_live_run_mode import (  # noqa: E402
    LIVE_WINDOW_AFTER_START_MIN,
    LIVE_WINDOW_BEFORE_START_MIN,
    LiveClassStartSchedule,
    default_end_live_run_mode,
    resolve_live_run_from_posted_run_mode,
)

TORONTO = ZoneInfo("America/Toronto")


class EndLiveRunModeWindowTests(unittest.TestCase):
    """Default Live only within -10 / +90 minutes of scheduled start."""

    def setUp(self) -> None:
        self.sched = LiveClassStartSchedule(days_key="M/W/F", start_time_label="9:15am")
        self.wed = datetime(2026, 10, 14, tzinfo=TORONTO)

    def _at(self, hour: int, minute: int, second: int = 0) -> datetime:
        return self.wed.replace(hour=hour, minute=minute, second=second)

    def test_inside_window_defaults_live(self) -> None:
        self.assertEqual(default_end_live_run_mode(self._at(10, 0), self.sched), "live")

    def test_ten_minutes_before_start_edge(self) -> None:
        self.assertEqual(default_end_live_run_mode(self._at(9, 5, 0), self.sched), "live")

    def test_one_second_before_window_is_test(self) -> None:
        self.assertEqual(default_end_live_run_mode(self._at(9, 4, 59), self.sched), "test")

    def test_ninety_minutes_after_start_edge(self) -> None:
        self.assertEqual(default_end_live_run_mode(self._at(10, 45, 0), self.sched), "live")

    def test_one_second_after_window_is_test(self) -> None:
        self.assertEqual(default_end_live_run_mode(self._at(10, 45, 1), self.sched), "test")

    def test_empty_schedule_defaults_test(self) -> None:
        sched = LiveClassStartSchedule(days_key="", start_time_label="")
        self.assertEqual(default_end_live_run_mode(self._at(10, 0), sched), "test")

    def test_tuesday_is_test(self) -> None:
        tue = datetime(2026, 10, 20, 9, 30, tzinfo=TORONTO)
        self.assertEqual(default_end_live_run_mode(tue, self.sched), "test")


    def test_two_pm_schedule_654pm_defaults_test(self) -> None:
        """2:00 PM bell; session at 6:54 PM Toronto is outside +90 min window."""
        sched = LiveClassStartSchedule(days_key="M/W/F", start_time_label="2:00pm")
        wed = datetime(2026, 10, 14, 18, 54, tzinfo=TORONTO)
        self.assertEqual(default_end_live_run_mode(wed, sched), "test")

    def test_naive_started_at_is_utc_converted_to_toronto(self) -> None:
        """Naive started_at is UTC; 22:54 UTC is 6:54 PM Toronto (EDT) → Test."""
        sched = LiveClassStartSchedule(days_key="M/W/F", start_time_label="2:00pm")
        naive_utc = datetime(2026, 10, 14, 22, 54)  # 6:54 PM America/Toronto
        self.assertEqual(default_end_live_run_mode(naive_utc, sched), "test")

    def test_omitted_run_mode_uses_schedule_default(self) -> None:
        self.assertTrue(
            resolve_live_run_from_posted_run_mode(None, default_mode="live")
        )
        self.assertFalse(
            resolve_live_run_from_posted_run_mode(None, default_mode="test")
        )
        self.assertTrue(
            resolve_live_run_from_posted_run_mode("live", default_mode="test")
        )
        self.assertFalse(
            resolve_live_run_from_posted_run_mode("test", default_mode="live")
        )

    def test_window_constants(self) -> None:
        self.assertEqual((LIVE_WINDOW_BEFORE_START_MIN, LIVE_WINDOW_AFTER_START_MIN), (10, 90))


if __name__ == "__main__":
    unittest.main()

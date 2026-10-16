#!/usr/bin/env python3
"""MCK-269 Mobbin labels for test live classes."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))

from live_class_test_labels import (  # noqa: E402
    format_test_lesson_slides_label,
    format_test_participation_label,
    test_same_day_indices,
)


class TestLiveClassLabelTests(unittest.TestCase):
    """Formatted TEST · dates and same-day (2) suffixes."""

    def test_participation_label_single_day(self) -> None:
        """First test on a day uses TEST · Mon D without suffix."""
        text = format_test_participation_label("2026-10-16T18:00:00")
        self.assertEqual(text, "TEST · Oct 16")

    def test_participation_label_second_same_day(self) -> None:
        """Second test on the same school day appends (2)."""
        text = format_test_participation_label(
            "2026-10-16T20:00:00", same_day_index=2
        )
        self.assertEqual(text, "TEST · Oct 16 (2)")

    def test_lesson_slides_label(self) -> None:
        """Lesson Slides rows include weekday in the title."""
        text = format_test_lesson_slides_label("2026-10-16T18:00:00")
        self.assertEqual(text, "TEST Fri Oct 16")

    def test_same_day_indices_order(self) -> None:
        """Same-day index follows ended_at order."""
        rows = [
            {"live_session_id": 10, "ended_at": "2026-10-16T14:00:00"},
            {"live_session_id": 11, "ended_at": "2026-10-16T16:00:00"},
        ]
        indices = test_same_day_indices(rows)
        self.assertEqual(indices[10], 1)
        self.assertEqual(indices[11], 2)


if __name__ == "__main__":
    unittest.main()

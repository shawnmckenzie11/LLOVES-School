#!/usr/bin/env python3
"""Tests for shared participation question weights (MCK-229 / MCK-230)."""

from __future__ import annotations

import unittest

from participation_weight import (
    participation_question_weight,
    question_mode_label,
    total_participation_weight,
)


class ParticipationWeightTests(unittest.TestCase):
    """MC questions weigh 1; others 0 for now."""

    def test_mc_individual_and_group_weigh_one(self) -> None:
        """Multiple-choice items count whether individual or group."""
        self.assertEqual(participation_question_weight({"type": "mc"}), 1)
        self.assertEqual(
            participation_question_weight({"type": "multiple_choice", "publish_mode": "group"}),
            1,
        )

    def test_non_mc_weighs_zero(self) -> None:
        """Rank and numeric items are 0 until MCK-230 extends weights."""
        self.assertEqual(participation_question_weight({"type": "rank"}), 0)
        self.assertEqual(participation_question_weight({"type": "numeric"}), 0)

    def test_total_weight_sums_rows(self) -> None:
        """Denominator helper sums a question list."""
        rows = [{"type": "mc"}, {"type": "rank"}, {"type": "mc"}]
        self.assertEqual(total_participation_weight(rows), 2)

    def test_mode_label(self) -> None:
        """Individual vs group label for the Lesson Slides table."""
        self.assertEqual(question_mode_label("individual", "individual"), "individual")
        self.assertEqual(question_mode_label("group", "individual"), "group")


if __name__ == "__main__":
    unittest.main()

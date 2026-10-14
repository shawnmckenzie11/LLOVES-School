#!/usr/bin/env python3
"""MCK-230 shared participation weight helpers."""

from __future__ import annotations

import unittest

from participation_weight import (
    count_participation_opportunities,
    item_is_multiple_choice,
    item_participation_weight,
)


class ParticipationWeightTests(unittest.TestCase):
    """Deck opportunity weights align with Attendance & Participation."""

    def test_mc_counts_as_one(self) -> None:
        """Multiple-choice items weigh 1 in individual or group publish modes."""
        self.assertTrue(item_is_multiple_choice("mc"))
        self.assertEqual(
            item_participation_weight("mc", publish_mode="individual"),
            1,
        )
        self.assertEqual(
            item_participation_weight(
                "multiple_choice",
                publish_mode="group_consensus",
                item_json={"type": "multiple_choice"},
            ),
            1,
        )

    def test_non_mc_is_zero(self) -> None:
        """Rank and numeric types do not add opportunities."""
        self.assertEqual(item_participation_weight("rank"), 0)
        self.assertEqual(item_participation_weight("numeric"), 0)

    def test_scope_filter_sums_weights(self) -> None:
        """Checkbox scope limits which deck rows count toward the denominator."""
        items = [
            {
                "prompt_kind": "mc",
                "publish_mode": "individual",
                "item_json": {"type": "mc"},
            },
            {
                "prompt_kind": "mc",
                "publish_mode": "group_consensus",
                "item_json": {"type": "mc"},
            },
            {"prompt_kind": "rank", "publish_mode": "individual"},
        ]
        self.assertEqual(
            count_participation_opportunities(
                items, include_individual=True, include_group=False
            ),
            1,
        )
        self.assertEqual(
            count_participation_opportunities(
                items, include_individual=True, include_group=True
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()

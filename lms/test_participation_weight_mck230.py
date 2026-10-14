#!/usr/bin/env python3
"""Unit tests for MCK-230 participation_weight helpers."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))

import participation_weight as W  # noqa: E402


class ParticipationWeightTests(unittest.TestCase):
    """Deck opportunity weights and display formatting."""

    def test_mc_counts_one_other_types_zero(self) -> None:
        self.assertEqual(W.item_participation_weight("mc", publish_mode="individual"), 1)
        self.assertEqual(
            W.item_participation_weight("poll", item_json={"type": "mc"}), 1
        )
        self.assertEqual(W.item_participation_weight("numeric"), 0)

    def test_scope_checkboxes(self) -> None:
        items = [
            {"publish_mode": "individual", "prompt_kind": "mc", "question_key": "item:a"},
            {"publish_mode": "group_shared", "prompt_kind": "mc", "question_key": "item:b"},
        ]
        self.assertEqual(
            W.count_participation_opportunities(
                items, include_individual=True, include_group=False
            ),
            1,
        )
        self.assertEqual(
            W.count_participation_opportunities(
                items,
                include_individual=True,
                include_group=False,
                live_answered_keys=frozenset({"item:a"}),
            ),
            1,
        )
        self.assertEqual(
            W.count_participation_opportunities(
                items, include_individual=True, include_group=True
            ),
            2,
        )

    def test_live_answered_keys_exclude_unanswered_deck_mc(self) -> None:
        items = [
            {"publish_mode": "individual", "prompt_kind": "mc", "question_key": "item:q0"},
            {"publish_mode": "individual", "prompt_kind": "mc", "question_key": "item:q1"},
        ]
        self.assertEqual(
            W.count_participation_opportunities(
                items,
                include_individual=True,
                include_group=False,
                live_answered_keys=frozenset({"item:q0"}),
            ),
            1,
        )

    def test_legacy_percent_cap(self) -> None:
        pct = W.participation_percent(5, 3, session_ended_at="2026-10-01T12:00:00")
        self.assertEqual(pct, 100)
        pct_new = W.participation_percent(5, 3, session_ended_at="2026-10-15T12:00:00")
        self.assertEqual(pct_new, 167)


if __name__ == "__main__":
    unittest.main()

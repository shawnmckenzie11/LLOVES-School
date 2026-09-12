#!/usr/bin/env python3
"""Beat 11: team-count max is present count; division-strength bands."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT / "tools" / "math-game-show"))

from teams import division_strength, team_count_bounds  # noqa: E402


class TeamCountBoundsTests(unittest.TestCase):
    """Stepper max tracks present students; min stays 1."""

    def test_min_is_always_one(self) -> None:
        """Count 1 means no teams, even when the room is full."""
        for present in (0, 1, 3, 12, 24):
            low, _high = team_count_bounds(present)
            self.assertEqual(low, 1)

    def test_max_equals_present_count(self) -> None:
        """Max is the present count, not a low hard-cap of 2 or 8."""
        self.assertEqual(team_count_bounds(0), (1, 1))
        self.assertEqual(team_count_bounds(1), (1, 1))
        self.assertEqual(team_count_bounds(3), (1, 3))
        self.assertEqual(team_count_bounds(12), (1, 12))
        self.assertEqual(team_count_bounds(24), (1, 24))
        self.assertGreater(team_count_bounds(12)[1], 8)


class DivisionStrengthTests(unittest.TestCase):
    """Even-split quality bands for the TEAMS OptionsStrip meter."""

    def test_count_one_is_individuals(self) -> None:
        """Count 1 hides the meter (individuals / no teams)."""
        self.assertEqual(division_strength(0, 1), "individuals")
        self.assertEqual(division_strength(3, 1), "individuals")
        self.assertEqual(division_strength(12, 1), "individuals")

    def test_three_students_two_teams_is_not_recommended(self) -> None:
        """N=3 / K=2 yields sizes 2+1 — degenerate."""
        self.assertEqual(division_strength(3, 2), "not_recommended")

    def test_singletons_are_not_recommended(self) -> None:
        """All-singleton or leftover-singleton splits are degenerate."""
        self.assertEqual(division_strength(3, 3), "not_recommended")
        self.assertEqual(division_strength(4, 3), "not_recommended")
        self.assertEqual(division_strength(4, 4), "not_recommended")
        self.assertEqual(division_strength(5, 3), "not_recommended")
        self.assertEqual(division_strength(12, 12), "not_recommended")

    def test_even_splits_are_optimal_or_okay(self) -> None:
        """Even-split algorithm lands Optimal (remainder ≤ 1, size ≥ 2) or Okay."""
        self.assertEqual(division_strength(4, 2), "optimal")
        self.assertEqual(division_strength(5, 2), "optimal")
        self.assertEqual(division_strength(6, 2), "optimal")
        self.assertEqual(division_strength(6, 3), "optimal")
        self.assertEqual(division_strength(8, 2), "optimal")
        self.assertEqual(division_strength(9, 3), "optimal")
        self.assertEqual(division_strength(8, 3), "okay")
        self.assertEqual(division_strength(10, 4), "okay")
        self.assertIn(division_strength(12, 5), {"optimal", "okay"})
        self.assertIn(division_strength(20, 4), {"optimal", "okay"})

    def test_more_teams_than_present_is_not_recommended(self) -> None:
        """K > N cannot fill every team."""
        self.assertEqual(division_strength(2, 3), "not_recommended")
        self.assertEqual(division_strength(0, 2), "not_recommended")


if __name__ == "__main__":
    unittest.main()

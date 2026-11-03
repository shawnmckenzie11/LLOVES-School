#!/usr/bin/env python3
"""A slide Forward must never send every phone to ``/state`` (MCK-283, MCK-441).

The load gate counts full ``/state`` builds in the second after each
Forward. Builds caused by a team's own tap postcard are that team's tap,
not the Forward, and three teams tapping in one second must not read as a
class-wide reload. Anything else in that second still counts.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "lms"))

import load_class  # noqa: E402


def _rows(builds: int, team: int, *, at: float = 30.2) -> list[tuple]:
    """One Forward at t=30 in session ``1`` plus builds 0.2 s later."""
    rows: list[tuple] = [(0.0, "student_state_build", "1", 200, 5.0)]
    rows.append((30.0, "slide_forward", "1", 200, 5.0))
    rows += [(at, "student_state_build", "1", 200, 5.0)] * builds
    rows += [(at, "student_state_build_team", "1", 200, 5.0)] * team
    return rows


class SlideReloadPeakTests(unittest.TestCase):
    """``slide_reload_peak`` separates a Forward from team taps."""

    def test_forward_that_reloads_every_phone_is_counted(self) -> None:
        self.assertEqual(load_class.slide_reload_peak([], _rows(15, 0)), 15)

    def test_three_team_taps_in_the_same_second_are_not_the_forward(self) -> None:
        self.assertEqual(load_class.slide_reload_peak([], _rows(15, 15)), 0)
        self.assertEqual(
            load_class.slide_reload_peak([], _rows(15, 15), count_team_taps=True), 15
        )

    def test_mixed_window_keeps_non_tap_builds(self) -> None:
        self.assertEqual(load_class.slide_reload_peak([], _rows(12, 5)), 7)

    def test_build_after_the_window_is_ignored(self) -> None:
        self.assertEqual(load_class.slide_reload_peak([], _rows(15, 0, at=31.5)), 0)


class GroupPostcardTests(unittest.TestCase):
    """Only a group postcard naming one team is a team tap."""

    def test_group_with_team(self) -> None:
        raw = json.dumps({"type": "response_landed", "scope": "group", "team_id": 4})
        self.assertTrue(load_class._group_postcard(raw))

    def test_group_without_team_is_class_wide(self) -> None:
        raw = json.dumps({"type": "response_landed", "scope": "group"})
        self.assertFalse(load_class._group_postcard(raw))

    def test_class_scope(self) -> None:
        raw = json.dumps({"type": "response_landed", "scope": "class"})
        self.assertFalse(load_class._group_postcard(raw))

    def test_garbage(self) -> None:
        self.assertFalse(load_class._group_postcard("not json"))


class ForwardPostcardTests(unittest.TestCase):
    """A Forward is a paint-in-place postcard, not a ``/state`` trigger."""

    def test_google_slide_is_not_a_phone_light_postcard(self) -> None:
        self.assertNotIn("google_slide", load_class._PHONE_LIGHT)

    def test_route_emits_only_google_slide(self) -> None:
        src = (REPO / "lms" / "routes_live.py").read_text()
        start = src.index("def _emit_google_slide_news(")
        end = src.index("def _require_slide_placement(", start)
        body = src[start:end]
        self.assertIn('"type": "google_slide"', body)
        for kind in sorted(load_class._PHONE_LIGHT):
            self.assertNotIn(f'"type": "{kind}"', body, kind)


if __name__ == "__main__":
    unittest.main()

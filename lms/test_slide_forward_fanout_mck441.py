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


class ForwardCheckFaultTests(unittest.TestCase):
    """The 8-Forward 30-seat check fails on each injected fault."""

    @classmethod
    def setUpClass(cls) -> None:
        import logging
        import os
        import tempfile

        logging.disable(logging.WARNING)
        os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")
        cls.tmp = tempfile.TemporaryDirectory()
        fixtures, app = load_class.seed(
            Path(cls.tmp.name),
            30,
            None,
            classes=1,
            items=1,
            include_mc=True,
            retain=True,
        )
        cls.fx = fixtures[0]
        cls.school = app.config["SCHOOL_DB"]
        load_class._budget_show_prepared_page(cls.school, cls.fx)
        load_class._budget_attach_slide_deck(cls.school, cls.fx, 22)
        mc = next(item for item in cls.fx["items"] if item.get("kind") == "mc")
        # A group item is open, so phones have real team ids.
        load_class._budget_publish(cls.fx, mc)

    @classmethod
    def tearDownClass(cls) -> None:
        import logging

        logging.disable(logging.NOTSET)
        cls.tmp.cleanup()

    def _run(self, hook=None) -> list[str]:
        slides = load_class._budget_slide_forwards(
            self.school, self.fx, 8, after_forward=hook
        )
        return load_class.slide_forward_errors(slides, 8)

    def _emit(self, events: list[dict]) -> None:
        from live_news_wire import emit_session_news

        emit_session_news(self.school, int(self.fx["session_id"]), events)

    def test_clean_forwards_pass(self) -> None:
        self.assertEqual(self._run(), [])

    def test_phone_that_reloaded_after_a_forward_fails(self) -> None:
        phone = self.fx["students"][3]

        def hook(index: int) -> None:
            if index == 4:
                # This phone's stored stamp no longer matches, so its next
                # /state is a full rebuild, as if the Forward moved it.
                phone["stamp"] = "stale-after-forward"

        errors = self._run(hook)
        self.assertTrue(any("rebuilt" in e for e in errors), errors)

    def test_reload_postcard_on_a_forward_fails(self) -> None:
        def hook(index: int) -> None:
            if index == 2:
                self._emit([{"type": "state_seq", "state_seq": 1}])

        errors = self._run(hook)
        self.assertTrue(any("/state postcards" in e for e in errors), errors)

    def test_reload_postcard_to_another_team_fails(self) -> None:
        sid = int(self.fx["session_id"])
        first = self.fx["students"][0]
        other = next(
            row for row in self.fx["students"] if int(row["team"]) != int(first["team"])
        )
        first_teams = self.school.student_open_group_team_ids(sid, int(first["id"]))
        other_teams = self.school.student_open_group_team_ids(sid, int(other["id"]))
        self.assertTrue(other_teams, "the open group item gives phones team ids")
        team_id = next(iter(other_teams))
        self.assertTrue(first_teams, "student 0 has a team, so it does not hear other teams")
        self.assertNotIn(team_id, first_teams)

        def hook(index: int) -> None:
            if index == 5:
                self._emit(
                    [
                        {
                            "type": "response_landed",
                            "scope": "group",
                            "team_id": int(team_id),
                            "state_seq": 1,
                        }
                    ]
                )

        errors = self._run(hook)
        self.assertTrue(any("/state postcards" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()

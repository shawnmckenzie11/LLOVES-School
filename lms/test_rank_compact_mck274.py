#!/usr/bin/env python3
"""MCK-274: the open teacher card for a team rank is the compact stack.

MCK-171 painted projector lanes (large type, Team 1 / Team 2 shapes) on
the published card and blanked the class order so the MCK-154 stack could
not render. This test fails if that pair comes back.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from test_rank_challenge_mck171 import KEY_IDS, ChallengeHarness

LMS_DIR = Path(__file__).resolve().parent
STAFF = LMS_DIR / "static" / "staff_ap.js"


class CompactTeacherSourceTests(unittest.TestCase):
    """Staff shell: open team rank does not paint projector lanes."""

    def test_open_card_uses_the_compact_stack(self) -> None:
        """A published team rank stays on rankStackHtml, with strip Reveal."""
        staff = STAFF.read_text(encoding="utf-8")
        self.assertNotIn("raceLanesHtml", staff)
        fn = staff.split("function rankRaceTeacherHtml")[1].split("\nfunction ")[0]
        self.assertIn("if (!revealed) return", fn)
        self.assertIn("if (teacherSkipsRaceReveal(race.results)) return", fn)
        collate = staff.split("function rankCollateHtml")[1].split("function groupSubmitTeacherHtml")[0]
        self.assertIn("rankStackHtml", collate)
        submit = staff.split("function groupSubmitTeacherHtml")[1].split("\nfunction ")[0]
        self.assertIn("rankCollateHtml", submit)
        self.assertIn("scoreTeamsButtonHtml", submit)
        showing = staff.split("const raceLanesShowing")[1].split(";")[0]
        self.assertNotIn("result?.race", showing)


class CompactTeacherViewTests(ChallengeHarness):
    """Teacher results for an open team challenge keep the compact collate."""

    def test_open_rank_keeps_class_order_for_two_teams(self) -> None:
        """Two teams, one order sent: the stack has rows and that place."""
        item = self._challenge()
        view = self._view(item)
        self.assertGreaterEqual(len(view["rank"]["teams"]), 2)
        self.assertEqual(len(view["rank"]["class_order"]), 4)
        self.assertTrue(all(team["order"] is None for team in view["rank"]["teams"]))
        self.assertNotIn("order", json.dumps(view["race"]))
        self._order("Ava", item, KEY_IDS)
        for name in ("Ava", "Cy", "Eli"):
            self._post(name, item, "rank-agree", {"agree": True, "order": KEY_IDS})
        view = self._view(item)
        submitted = next(row for row in view["rank"]["teams"] if row["status"] == "submitted")
        self.assertEqual(self._pos(item, submitted["order"]), KEY_IDS)
        self.assertEqual(len(view["rank"]["class_order"]), 4)
        self.assertNotIn("order", json.dumps(view["race"]))
        # Student phones still do not receive another team's order.
        ben = self.students["Ben"].get("/api/student/state").get_data(as_text=True)
        self.assertNotIn(json.dumps(self._as_student(item, KEY_IDS)).replace(" ", ""), ben.replace(" ", ""))

    def test_close_still_exposes_score_teams_results(self) -> None:
        """Close leaves hand-scored results for the MCK-185 pop-up."""
        item = self._challenge()
        self._order("Ava", item, KEY_IDS)
        for name in ("Ava", "Cy", "Eli"):
            self._post(name, item, "rank-agree", {"agree": True, "order": KEY_IDS})
        closed = self._close(item)
        view = self._view(closed)
        results = view["race"]["results"]
        self.assertFalse(results["pays"])
        self.assertEqual(len(view["rank"]["class_order"]), 4)
        self.assertGreaterEqual(len(results["teams"]), 2)


if __name__ == "__main__":
    unittest.main()

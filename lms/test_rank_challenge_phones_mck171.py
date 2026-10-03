#!/usr/bin/env python3
"""MCK-171 Team challenge (c): phone states at 390px.

Node harness for ``static/rank_challenge_phone.js`` (every state's cue line,
Wonder v3 final copy), the student portal wiring, and the server fields the
phone reads (team slot, closed-by line, no key / score before the reveal).
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from test_rank_challenge_mck171 import KEY_IDS, ChallengeHarness

LMS_DIR = Path(__file__).resolve().parent
STATIC = LMS_DIR / "static"


class PhoneViewTests(unittest.TestCase):
    """Pure phone builders (node) and the portal wiring."""

    def test_phone_node_harness(self) -> None:
        proc = subprocess.run(
            ["node", str(STATIC / "rank_challenge_phone.test.mjs")],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("ok", proc.stdout)

    def test_portal_wiring(self) -> None:
        portal = (STATIC / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn('from "/static/rank_challenge_phone.js"', portal)
        self.assertIn("if (rank && item?.group_submit?.race) return rankChallengeCardHtml(item);", portal)
        self.assertIn('postGroupFlow(card, "rank-agree", { agree, order })', portal)
        self.assertIn('rankInputHtml(options, shown, "group", { noSubmit: true })', portal)
        # No countdown on phones while a challenge is open.
        self.assertIn("if (teamChallengeOpen(payload)) {", portal)

    def test_phone_css_targets(self) -> None:
        css = (STATIC / "student-portal.css").read_text(encoding="utf-8")
        block = css[css.index("MCK-171 Team challenge on the phone") :]
        self.assertIn("min-height: 52px", block)
        self.assertIn("min-height: 48px", block)


class PhonePayloadTests(ChallengeHarness):
    """What the phone gets from the server."""

    def test_slot_matches_the_projector_lane(self) -> None:
        item = self._challenge()
        lanes = {t["team_id"]: t["slot"] for t in self._view(item)["race"]["teams"]}
        for name in ("Ava", "Ben"):
            card = self._card(name, item)
            self.assertEqual(card["race"]["slot"], lanes[card["team_id"]])

    def test_student_state_has_no_key_or_score(self) -> None:
        item = self._challenge()
        self._order("Ava", item, KEY_IDS)
        rv = self.students["Ava"].get("/api/student/state")
        self.assertEqual(rv.status_code, 200)
        body = rv.get_data(as_text=True)
        self.assertNotIn("rank_key", body)
        self.assertNotIn("awarded_points", json.dumps(self._card("Ava", item)))


if __name__ == "__main__":
    unittest.main()

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

    def test_one_cue_line_in_rank_together(self) -> None:
        """Mobbin MUST-FIX: the old group line never sits above the race cue."""
        portal = (STATIC / "student-portal.js").read_text(encoding="utf-8")
        start = portal.index("const groupInstruction =")
        block = portal[start : portal.index("groupInstructionHtml(item, content)", start)]
        self.assertIn('item.group_submit?.race?.mode !== "together"', block)

    def test_not_yet_is_an_outline_button(self) -> None:
        phone = (STATIC / "rank_challenge_phone.js").read_text(encoding="utf-8")
        self.assertIn('class="race-phone-btn is-outline" data-race-agree="0"', phone)
        css = (STATIC / "student-portal.css").read_text(encoding="utf-8")
        rule = css[css.index(".race-phone-btn.is-outline {") :][:200]
        self.assertIn("border: 2px solid #5eead4", rule)
        self.assertNotIn("opacity", rule)

    def test_no_waiting_line_behind_a_card(self) -> None:
        css = (STATIC / "student-portal.css").read_text(encoding="utf-8")
        self.assertIn(
            ".student-live-response:has(.student-live-card) > .student-wait {\n  display: none;", css
        )

    def test_rank_badges_are_the_team_positions(self) -> None:
        """Badges show each option's place in the team order, never ids or
        authored numbers (rows come in display order, MCK-176)."""
        portal = (STATIC / "student-portal.js").read_text(encoding="utf-8")
        start = portal.index("function rankInputHtml(")
        src = portal[start : portal.index("\n}\n", start) + 3]
        script = (
            "const escapeText = (s) => String(s);\n" + src + "\n"
            "const opts = [{id:'o1',label:'A'},{id:'o2',label:'B'},{id:'o3',label:'C'},{id:'o4',label:'D'},{id:'o5',label:'E'}];\n"
            "const html = rankInputHtml(opts, ['o1','o3','o4','o5','o2'], 'group', { noSubmit: true });\n"
            "const badges = [...html.matchAll(/data-rank-id=\"([^\"]+)\" data-rank-place=\"(\\d+)\"[\\s\\S]*?rank-badge[^>]*>(\\d*)</g)].map(m => [m[1], m[2], m[3]]);\n"
            "console.log(JSON.stringify(badges));\n"
        )
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        badges = json.loads(proc.stdout)
        self.assertEqual(
            badges,
            [["o1", "1", "1"], ["o2", "5", "5"], ["o3", "2", "2"], ["o4", "3", "3"], ["o5", "4", "4"]],
        )


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

    def test_race_options_content_and_order_share_one_alias_set(self) -> None:
        """MCK-176: the phone matches race.options to the content rows and
        the team order by id, so all three carry the same aliases."""
        item = self._challenge()
        self._order("Ava", item, KEY_IDS)
        data = self.students["Ava"].get("/api/student/state").get_json()
        rows = [
            r for r in (data.get("active_questions") or []) + (data.get("live_items") or [])
            if int(r.get("id") or 0) == int(item["id"])
        ]
        self.assertTrue(rows)
        real = set(self._real_ids(item))
        for row in rows:
            content = row["content"]
            content_ids = {o["id"] for o in (content.get("rank_options") or content.get("options")) if isinstance(o, dict)}
            race_ids = [o["id"] for o in row["group_submit"]["race"]["options"]]
            self.assertEqual(set(race_ids), content_ids)
            self.assertEqual(set(row["group_submit"]["order"]), content_ids)
            self.assertFalse(content_ids & real)
            self.assertEqual(self._pos(item, row["group_submit"]["order"]), KEY_IDS)


if __name__ == "__main__":
    unittest.main()

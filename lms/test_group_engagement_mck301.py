#!/usr/bin/env python3
"""MCK-301: ranking check does not send, and group MC sends only on a match.

A correct rank with no check stores 4/2. Any other rank completion stores
2/2. Catch-up stores 2/2 and never 4. A group multiple-choice send stores
1/1, and the server refuses the send until every present member matches.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
if str(LMS_DIR) not in sys.path:
    sys.path.insert(0, str(LMS_DIR))

from group_engagement import match_reason, match_tally
from test_group_engagement_mck293 import TEAM_A, TEAM_B, GroupEngagementTests
from test_rank_challenge_mck171 import KEY_IDS


class MatchRuleTests(unittest.TestCase):
    """Pure match tally. No database."""

    def test_ready_only_when_every_present_member_matches(self) -> None:
        partial = match_tally(["North", "North", ""], 3)
        self.assertEqual(partial["matched"], 2)
        self.assertFalse(partial["ready"])
        self.assertEqual(partial["choice"], "")
        split = match_tally(["North", "East", ""], 3)
        self.assertEqual(split["matched"], 1)
        self.assertFalse(split["ready"])
        ready = match_tally(["North", "North", "North"], 3)
        self.assertTrue(ready["ready"])
        self.assertEqual(ready["choice"], "North")
        self.assertEqual(match_reason(2, 3), "Everyone needs the same pick (2 of 3 match)")


class CheckDoesNotSendTests(GroupEngagementTests):
    """Check and the last card stay drafts. Send is a separate final write."""

    def _place_only(self, item: dict[str, Any], names: tuple[str, ...], order: list[str]) -> None:
        """Place cards without the helper's send."""
        for token in order:
            status = 0
            for name in names:
                rv = self._post(name, item, "rank-turn", {"option_id": token})
                status = rv.status_code
                if status == 200:
                    break
            else:
                self.fail(f"place status {status}")

    def _submit_count(self, item: dict[str, Any], name: str) -> int:
        team_id = self._team(name, item)
        with self.school._lock:
            row = self.school.conn.execute(
                """
                SELECT submit_count FROM live_group_responses
                WHERE live_item_id = ? AND team_id = ?
                """,
                (int(item["id"]), int(team_id)),
            ).fetchone()
        return int(row["submit_count"] or 0) if row is not None else 0

    def test_check_and_last_card_do_not_send(self) -> None:
        item = self._open()
        self._place_only(item, TEAM_A, KEY_IDS[:2])
        checked = self._check(item)
        self.assertEqual(checked.status_code, 200, checked.get_data(as_text=True)[:240])
        body = checked.get_json()["group_submit"]
        self.assertEqual(body["check"]["message"], "Checked: 2 of 4 in the right spot")
        self.assertFalse(body["submitted"])
        self.assertEqual(self._submit_count(item, "Ava"), 0)
        self._place_only(item, TEAM_A, KEY_IDS[2:])
        full = self._card("Ava", item)
        self.assertTrue(full["turns"]["done"])
        self.assertFalse(full["submitted"])
        self.assertEqual(self._submit_count(item, "Ava"), 0)
        again = self._check(item)
        self.assertEqual(again.status_code, 400)
        self.assertEqual(self._submit_count(item, "Ava"), 0)

    def test_send_is_final_and_scores_two_after_a_check(self) -> None:
        item = self._open()
        self._place_only(item, TEAM_A, KEY_IDS)
        self._check(item)
        early = self._post("Ava", item, "group-submit", {})
        # The order is already complete, so this send is the final write.
        self.assertEqual(early.status_code, 200, early.get_data(as_text=True)[:240])
        sent = early.get_json()["group_submit"]
        self.assertTrue(sent["submitted"])
        self.assertTrue(sent["check"]["used"])
        repeat = self._post("Cy", item, "group-submit", {})
        self.assertEqual(repeat.status_code, 200, repeat.get_data(as_text=True)[:180])
        self.assertEqual(self._submit_count(item, "Ava"), 1)
        key = self._question_key(item)
        self._end()
        self._assert_team(key, TEAM_A, 2)
        self._assert_team(key, TEAM_B, 0)

    def test_correct_without_a_check_is_four_of_two(self) -> None:
        item = self._open()
        self._place_only(item, TEAM_A, KEY_IDS)
        sent = self._post("Ava", item, "group-submit", {})
        self.assertEqual(sent.status_code, 200, sent.get_data(as_text=True)[:200])
        key = self._question_key(item)
        self._end()
        self._assert_team(key, TEAM_A, 4)
        dash = self.school.student_dashboard_payload(self.class_id, self.ids["Ava"])
        self.assertEqual(dash["group_engagement"], "4/2")

    def test_wrong_order_is_two_of_two(self) -> None:
        item = self._open()
        self._place_only(item, TEAM_A, list(reversed(KEY_IDS)))
        sent = self._post("Ava", item, "group-submit", {})
        self.assertEqual(sent.status_code, 200, sent.get_data(as_text=True)[:200])
        key = self._question_key(item)
        self._end()
        self._assert_team(key, TEAM_A, 2)
        dash = self.school.student_dashboard_payload(self.class_id, self.ids["Ava"])
        self.assertEqual(dash["group_engagement"], "2/2")

    def test_catchup_caps_at_two_of_two(self) -> None:
        item = self._open()
        self._place_only(item, TEAM_A, KEY_IDS)
        self._post("Ava", item, "group-submit", {})
        key = self._question_key(item)
        self._end()
        with self.school._lock:
            link_row = self.school.conn.execute(
                """
                SELECT token FROM catchup_links
                WHERE class_id = ? AND run_key = ? AND COALESCE(is_test, 0) = 0
                """,
                (self.class_id, self._run_key()),
            ).fetchone()
        self.assertIsNotNone(link_row)
        token = str(link_row["token"])
        link = self.school.get_catchup_link(token)
        ben = self.ids["Ben"]
        ava = self.ids["Ava"]
        questions = [q for q in self.school.catchup_questions(token, ben) if q.get("group_rank")]
        self.assertEqual(len(questions), 1)
        content = questions[0]["content"]
        options = content.get("rank_options") or content.get("options") or []
        public_ids = [str(opt["id"]) for opt in options]
        self.assertTrue(self.school.submit_catchup_answer(token, ben, key, public_ids))
        self.assertEqual(self._rows()[(ben, key)], (2, 2, "catchup"))
        self.assertTrue(self.school._store_group_engagement_catchup(link, ben, key, list(reversed(public_ids))))
        self.assertEqual(self._rows()[(ben, key)], (2, 2, "catchup"))
        self.assertEqual(self._rows()[(ava, key)], (4, 2, "live"))
        self.assertFalse(any(q.get("group_rank") for q in self.school.catchup_questions(token, ava)))


class GroupMcMatchTests(GroupEngagementTests):
    """Group multiple choice sends only when the present members match."""

    def _mc(self) -> dict[str, Any]:
        row = self._add({
            "type": "mc",
            "text": "Pick one.",
            "options": ["North", "East", "South", "West"],
            "correct_index": 0,
        })
        return self._publish(row)

    def test_server_refuses_until_every_pick_matches_then_scores_one(self) -> None:
        item = self._mc()
        self.assertEqual(self._post("Ava", item, "group-pick", {"choice": "North"}).status_code, 200)
        self.assertEqual(self._post("Cy", item, "group-pick", {"choice": "East"}).status_code, 200)
        refused = self._post("Ava", item, "group-submit", {"choice": "North", "why": "because"})
        self.assertEqual(refused.status_code, 400, refused.get_data(as_text=True)[:240])
        self.assertIn("1 of 3 match", refused.get_json()["error"])
        self.assertNotIn("why", refused.get_json()["error"].lower())
        self.assertEqual(self._post("Eli", item, "group-pick", {"choice": "North"}).status_code, 200)
        still = self._post("Ava", item, "group-submit", {})
        self.assertEqual(still.status_code, 400, still.get_data(as_text=True)[:240])
        self.assertIn("2 of 3 match", still.get_json()["error"])
        self.assertEqual(self._post("Cy", item, "group-pick", {"choice": "North"}).status_code, 200)
        card = self._card("Ava", item)
        self.assertTrue(card["match"]["ready"])
        letters = {row["name"]: row["value"] for row in card["member_picks"]}
        self.assertEqual(set(letters.values()), {"North"})
        sent = self._post("Eli", item, "group-submit", {"choice": "East", "why": ""})
        self.assertEqual(sent.status_code, 200, sent.get_data(as_text=True)[:240])
        locked = sent.get_json()["group_submit"]
        self.assertTrue(locked["submitted"])
        self.assertEqual(locked["submitted_choice"], "North")
        self.assertEqual(locked["submitted_why"], "")
        key = self._question_key(item)
        self._end()
        self._assert_team(key, TEAM_A, 1, possible=1)
        self._assert_team(key, TEAM_B, 0, possible=1)
        dash = self.school.student_dashboard_payload(self.class_id, self.ids["Ava"])
        self.assertEqual(dash["group_engagement"], "1/1")


if __name__ == "__main__":
    unittest.main()

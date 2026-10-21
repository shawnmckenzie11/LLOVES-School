#!/usr/bin/env python3
"""MCK-301: ranking check does not send, and group MC sends only on a match.

A correct rank with no check stores 4/2. Any other rank completion stores
2/2. Catch-up stores 2/2 and never 4. A group multiple-choice send stores
1/1, and the server refuses the send until every present member matches.
"""

from __future__ import annotations

import sys
import json
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


class OldScaleRowTests(GroupEngagementTests):
    """Old 1-possible rank rows stay stored and are only rescaled on screen."""

    def test_old_one_point_rank_is_not_rewritten_or_offered(self) -> None:
        """End and catch-up leave a 1/1 rank row. Teachers see 2/2.

        The row is inserted before End, which is the write that would
        otherwise replace it with the current scale. Catch-up does not
        list it and a direct catch-up store does not change it.
        """
        item = self._open()
        self._place(item, TEAM_A, KEY_IDS)
        key = self._question_key(item)
        ava = self.ids["Ava"]
        run_key = self._run_key()
        with self.school._lock:
            self.school.conn.execute(
                """
                INSERT INTO group_engagement (
                    class_id, run_key, question_key, student_id,
                    earned, possible, source, recorded_at
                ) VALUES (?, ?, ?, ?, 1, 1, 'live', '2026-09-01T00:00:00Z')
                """,
                (self.class_id, run_key, key, ava),
            )
            self.school.conn.commit()
        self._end()
        self.assertEqual(self._rows()[(ava, key)], (1, 1, "live"))
        shown = self.school.group_engagement_fractions(
            self.class_id, run_key=run_key
        )
        self.assertEqual(shown[ava], "2/2")
        summary = self.school.engagement_summary_for_run(self.class_id, run_key)
        stamped = {}
        for group in list(summary["groups"]) + [{"students": summary["ungrouped"]}]:
            for student in group.get("students") or []:
                if "group_engagement" in student:
                    stamped[int(student["student_id"])] = student["group_engagement"]
        self.assertEqual(stamped.get(ava), "2/2")
        grid = self.school.participation_week_grid(self.class_id)
        self.assertEqual(grid["group_engagement"].get(str(ava)), "2/2")
        with self.school._lock:
            link_row = self.school.conn.execute(
                """
                SELECT token FROM catchup_links
                WHERE class_id = ? AND run_key = ? AND COALESCE(is_test, 0) = 0
                """,
                (self.class_id, run_key),
            ).fetchone()
        self.assertIsNotNone(link_row)
        token = str(link_row["token"])
        offered = [
            q for q in self.school.catchup_questions(token, ava) if q.get("group_rank")
        ]
        self.assertEqual(offered, [])
        link = self.school.get_catchup_link(token)
        self.assertIsNotNone(summary)
        with self.school._lock:
            stored = self.school.conn.execute(
                """
                SELECT item_json FROM group_engagement_items
                WHERE class_id = ? AND run_key = ? AND question_key = ?
                """,
                (self.class_id, run_key, key),
            ).fetchone()
        item_json = json.loads(stored["item_json"])
        options = item_json.get("rank_options") or item_json.get("options") or []
        order = [str(opt["id"]) for opt in options]
        self.assertEqual(len(order), 4)
        self.assertTrue(
            self.school._store_group_engagement_catchup(link, ava, key, order)
        )
        self.assertEqual(self._rows()[(ava, key)], (1, 1, "live"))

    def _insert_old_rank(self, item: dict[str, Any], student_id: int, earned: int) -> tuple[str, str]:
        """Store one old-scale rank row. Does not change earned or possible later.

        The item row is rank so display mapping can see the kind. The
        student's current module is stamped so the dashboard includes it.

        Args:
            item: Published live rank.
            student_id: Roster id.
            earned: Stored points. Possible stays 1.

        Returns:
            ``(run_key, question_key)``.
        """
        key = self._question_key(item)
        run_key = self._run_key()
        dash = self.school.student_dashboard_payload(self.class_id, student_id)
        module = str(dash.get("current_module") or "M1")
        with self.school._lock:
            self.school.conn.execute(
                """
                INSERT INTO group_engagement_items (
                    class_id, run_key, question_key, live_module, live_slot,
                    item_json, prompt_kind, offered_at
                ) VALUES (?, ?, ?, ?, 'C1', '{}', 'rank', '2026-09-01T00:00:00Z')
                """,
                (self.class_id, run_key, key, module),
            )
            self.school.conn.execute(
                """
                INSERT INTO group_engagement (
                    class_id, run_key, question_key, student_id,
                    earned, possible, source, recorded_at
                ) VALUES (?, ?, ?, ?, ?, 1, 'live', '2026-09-01T00:00:00Z')
                """,
                (self.class_id, run_key, key, student_id, int(earned)),
            )
            self.school.conn.commit()
        return run_key, key

    def _student_points(self, student_id: int) -> tuple[str, str]:
        """Dashboard fraction and the live-card fraction for one student.

        Args:
            student_id: Roster id.

        Returns:
            ``(dashboard, live card)``. Both read stored rows.
        """
        dash = self.school.student_dashboard_payload(self.class_id, student_id)
        card = self.school.student_group_engagement_display(
            self.class_id, self.session_id, student_id
        )
        return str(dash["group_engagement"]), card

    def test_old_two_of_one_displays_as_four_of_two(self) -> None:
        """An old 2/1 rank shows as 4/2. The stored row stays 2/1.

        A checked send would rescore as 2/2 from the live items. The
        student dashboard and the live card both show the stored 4/2.
        """
        item = self._open()
        self._place(item, TEAM_A, KEY_IDS[:2])
        checked = self._check(item)
        self.assertEqual(checked.status_code, 200, checked.get_data(as_text=True)[:180])
        self._place(item, TEAM_A, KEY_IDS[2:])
        ava = self.ids["Ava"]
        run_key, key = self._insert_old_rank(item, ava, 2)
        live_earned, live_possible = self.school._live_group_engagement_totals(
            self.session_id, ava
        )
        self.assertEqual((live_earned, live_possible), (2, 2))
        teacher = self.school.group_engagement_fractions(self.class_id, run_key=run_key)
        self.assertEqual(teacher[ava], "4/2")
        dash, card = self._student_points(ava)
        self.assertEqual(dash, "4/2")
        self.assertEqual(card, "4/2")
        self._end()
        self.assertEqual(self._rows()[(ava, key)], (2, 1, "live"))
        teacher = self.school.group_engagement_fractions(self.class_id, run_key=run_key)
        self.assertEqual(teacher[ava], "4/2")
        dash, card = self._student_points(ava)
        self.assertEqual(dash, "4/2")
        self.assertEqual(card, "4/2")

    def test_old_zero_of_one_displays_as_zero_of_two(self) -> None:
        """An old 0/1 rank shows as 0/2. The stored row stays 0/1.

        A correct send with no check would rescore as 4/2 from the live
        items. Student views stay on the stored 0/2.
        """
        item = self._open()
        self._place(item, TEAM_A, KEY_IDS)
        ava = self.ids["Ava"]
        run_key, key = self._insert_old_rank(item, ava, 0)
        live_earned, live_possible = self.school._live_group_engagement_totals(
            self.session_id, ava
        )
        self.assertEqual((live_earned, live_possible), (4, 2))
        teacher = self.school.group_engagement_fractions(self.class_id, run_key=run_key)
        self.assertEqual(teacher[ava], "0/2")
        dash, card = self._student_points(ava)
        self.assertEqual(dash, "0/2")
        self.assertEqual(card, "0/2")
        self._end()
        self.assertEqual(self._rows()[(ava, key)], (0, 1, "live"))
        teacher = self.school.group_engagement_fractions(self.class_id, run_key=run_key)
        self.assertEqual(teacher[ava], "0/2")
        dash, card = self._student_points(ava)
        self.assertEqual(dash, "0/2")
        self.assertEqual(card, "0/2")

    def test_old_two_plus_new_four_displays_eight_of_four(self) -> None:
        """An old 2/1 plus a new 4/2 shows as 8/4. Both stored rows stay put.

        Before End the live items would rescore both sends. The student
        card shows only the stored old row until the new row is written.
        """
        first = self._open()
        self._place(first, TEAM_A, KEY_IDS)
        ava = self.ids["Ava"]
        run_key, first_key = self._insert_old_rank(first, ava, 2)
        second = self._open()
        self._place(second, TEAM_A, KEY_IDS)
        second_key = self._question_key(second)
        live_earned, live_possible = self.school._live_group_engagement_totals(
            self.session_id, ava
        )
        self.assertEqual((live_earned, live_possible), (8, 4))
        dash, card = self._student_points(ava)
        self.assertEqual(dash, "4/2")
        self.assertEqual(card, "4/2")
        self._end()
        rows = self._rows()
        self.assertEqual(rows[(ava, first_key)], (2, 1, "live"))
        self.assertEqual(rows[(ava, second_key)][:2], (4, 2))
        teacher = self.school.group_engagement_fractions(self.class_id, run_key=run_key)
        self.assertEqual(teacher[ava], "8/4")
        dash, card = self._student_points(ava)
        self.assertEqual(dash, "8/4")
        self.assertEqual(card, "8/4")
        grid = self.school.participation_week_grid(self.class_id)
        self.assertEqual(grid["group_engagement"].get(str(ava)), "8/4")

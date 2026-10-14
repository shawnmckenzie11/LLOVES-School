"""MCK-216: student Team challenge lock-in (no strip, Undo, explicit lock)."""

from __future__ import annotations

import unittest

from lms.test_rank_challenge_mck171 import ChallengeHarness, KEY_IDS


class Mck216StudentLockTests(ChallengeHarness):
    """Lock in our order; no auto-lock on last spot (Take turns)."""

    def test_together_locks_on_explicit_tap_not_agree_quorum(self) -> None:
        item = self._challenge()
        self._order("Ava", item, KEY_IDS)
        card = self._card("Ava", item)
        self.assertTrue(card["race"]["can_lock"])
        self.assertNotIn("agree", card["race"])
        self.assertEqual(self._row(item, "Ava")["submit_count"], 0)
        rv = self._post("Ava", item, "rank-agree", {"agree": True, "order": KEY_IDS})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        card = rv.get_json()["group_submit"]
        self.assertTrue(card["race"]["locked"])
        self.assertEqual(self._row(item, "Ava")["submit_count"], 1)

    def test_together_undo_last_editor_before_lock(self) -> None:
        item = self._challenge()
        self._order("Ava", item, KEY_IDS[:3])
        self._order("Cy", item, KEY_IDS)
        cy = self._card("Cy", item)
        self.assertTrue(cy["race"]["can_undo"])
        self.assertFalse(self._card("Ava", item)["race"].get("can_undo"))
        rv = self._post("Cy", item, "group-draft", {"undo_last": True})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        self.assertFalse(rv.get_json()["group_submit"]["race"]["can_lock"])

    def test_take_turns_race_waits_for_lock_in(self) -> None:
        item = self._challenge(mode="turns")
        order = ["Ava", "Cy", "Eli", "Ava"]
        for name, opt in zip(order, KEY_IDS):
            rv = self._post(name, item, "rank-turn", {"option_id": opt})
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        card = rv.get_json()["group_submit"]
        self.assertTrue(card["turns"]["done"])
        self.assertFalse(card["race"]["locked"])
        self.assertTrue(card["race"]["can_lock"])
        self.assertTrue(card["turns"]["can_undo"])
        self.assertIsNone(self._row(item, "Ava")["finalized_at"])
        rv = self._post("Ava", item, "rank-agree", {"agree": True, "order": KEY_IDS})
        self.assertEqual(rv.status_code, 200)
        self.assertTrue(rv.get_json()["group_submit"]["race"]["locked"])


    def test_locked_hides_full_order_until_close(self) -> None:
        """Server must not send the MCK-185 full-order notice at lock-in."""
        item = self._challenge()
        self._order("Ava", item, KEY_IDS)
        rv = self._post("Ava", item, "rank-agree", {"agree": True, "order": KEY_IDS})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        card = self._card("Ava", item)
        self.assertTrue(card["race"]["locked"])
        self.assertNotIn("full_order", card)
        self._close(item)
        self.assertIn("full_order", self._card("Ava", item))


if __name__ == "__main__":
    unittest.main()

"""MCK-310: a locked group ranking stays locked for every teammate.

The take-turns phone posts an empty order because its spots are not rank
rows. That empty list is not a different order. Once the stored order is
locked, a second tap is refused and the order-changed line is not used.
"""

from __future__ import annotations

import unittest

from lms.school_db import RACE_AGREE_RESET_COPY, RACE_LOCKED_COPY
from lms.test_rank_challenge_mck171 import KEY_IDS, NAMES, ChallengeHarness


class RankLockAcrossTeammatesTests(ChallengeHarness):
    """Lock in our order is final for the whole team."""

    def _mates(self) -> tuple[str, str, str]:
        """The three roster ids that share the first team."""
        return (NAMES[0], NAMES[2], NAMES[4])

    def test_take_turns_empty_order_locks_every_teammate(self) -> None:
        """An empty lock body sends the stored order and hides it afterwards."""
        first, second, third = self._mates()
        item = self._challenge(mode="turns")
        self.assertEqual(self._order(first, item, KEY_IDS).status_code, 200)
        locked = self._post(first, item, "rank-agree", {"agree": True, "order": []})
        self.assertEqual(locked.status_code, 200)
        body = locked.get_json()
        self.assertTrue(body["group_submit"]["race"]["locked"])
        self.assertFalse(body["group_submit"]["race"]["can_lock"])
        self.assertNotIn("order changed", str(body.get("error") or "").lower())
        for name in (first, second, third):
            card = self._card(name, item)
            self.assertTrue(card["race"]["locked"])
            self.assertFalse(card["race"]["can_lock"])
            self.assertFalse(card["turns"].get("can_undo"))
            self.assertNotIn(RACE_AGREE_RESET_COPY, str(card.get("error") or ""))
        again = self._post(second, item, "rank-agree", {"agree": True, "order": []})
        self.assertEqual(again.status_code, 409)
        refused = again.get_json()
        self.assertTrue(refused.get("locked"))
        self.assertEqual(refused.get("error"), RACE_LOCKED_COPY)
        self.assertNotIn(RACE_AGREE_RESET_COPY, str(refused.get("error") or ""))
        self.assertTrue(refused["group_submit"]["race"]["locked"])
        self.assertFalse(refused["group_submit"]["race"]["can_lock"])
        mismatched = self._post(
            third, item, "rank-agree", {"agree": True, "order": ["o1", "o2", "o3", "o4"]}
        )
        self.assertEqual(mismatched.status_code, 409)
        mismatch = mismatched.get_json()
        self.assertTrue(mismatch.get("locked"))
        self.assertNotEqual(mismatch.get("error"), RACE_AGREE_RESET_COPY)
        self.assertEqual(self._row(item, first).get("submit_count"), 1)

    def test_together_repeat_lock_is_refused_for_every_teammate(self) -> None:
        """Rank together locks once. Later taps, including an empty order, are refused."""
        first, second, third = self._mates()
        item = self._challenge()
        self.assertEqual(self._order(first, item, KEY_IDS).status_code, 200)
        self._lock_once(item, (first, second, third), KEY_IDS)
        empty = self._post(third, item, "rank-agree", {"agree": True, "order": []})
        self.assertEqual(empty.status_code, 409)
        refused = empty.get_json()
        self.assertTrue(refused.get("locked"))
        self.assertEqual(refused.get("error"), RACE_LOCKED_COPY)
        self.assertNotIn(RACE_AGREE_RESET_COPY, str(refused.get("error") or ""))
        for name in (first, second, third):
            card = self._card(name, item)
            self.assertTrue(card["race"]["locked"])
            self.assertFalse(card["race"].get("can_lock"))
        self.assertEqual(self._row(item, first).get("submit_count"), 1)

    def test_a_different_open_order_still_conflicts(self) -> None:
        """Before lock, a non-empty mismatch still reports that the order changed."""
        first, second, _third = self._mates()
        item = self._challenge(mode="turns")
        self.assertEqual(self._order(first, item, KEY_IDS).status_code, 200)
        rv = self._post(
            second, item, "rank-agree", {"agree": True, "order": ["o1", "o2", "o3", "o4"]}
        )
        self.assertEqual(rv.status_code, 409)
        body = rv.get_json()
        self.assertEqual(body.get("reason"), "order_changed")
        self.assertEqual(body.get("error"), RACE_AGREE_RESET_COPY)
        self.assertFalse(body.get("locked"))
        self.assertFalse(body["group_submit"]["race"]["locked"])
        self.assertEqual(self._row(item, first).get("submit_count"), 0)


if __name__ == "__main__":
    unittest.main()

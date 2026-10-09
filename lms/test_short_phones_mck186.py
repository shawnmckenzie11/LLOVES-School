"""MCK-186: #251 gate follow-ups.

- Short phones: while a question card is on screen the phone header folds
  into one compact bar and the Scores panel into one strip, so the card
  gets the screen (360x640 went from a 76px window to about 450px).
- The presence token in the student poll stamp is a short keyed hash, not
  count / SUM(id) / SUM(id²), and a presence blip keeps the last token.
- One window resize listener for every floating pane, not one per
  rebuilt question card.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest import mock

import test_rank_turns_mck184 as t184

LMS_DIR = Path(__file__).resolve().parent
STATIC = LMS_DIR / "static"


class PresenceTokenTests(t184.TurnsHarness):
    """The stamp's last field while a Take turns item is open."""

    def _stamp(self) -> str:
        return self.school.live_student_poll_stamp(self.session_id, self.class_id)

    def _token(self) -> str:
        return self._stamp().rsplit(":", 1)[1]

    def test_token_is_a_short_hash_not_counts_or_row_ids(self) -> None:
        self._turns_item()
        token = self._token()
        self.assertRegex(token, r"^h[0-9a-f]{12}$")
        raw = self.school._present_attendee_rev(self.session_id)
        self.assertRegex(raw, r"^[sp]\d+\.\d+\.\d+$")  # raw stays on the server
        stamp = self._poll("Ava")["stamp"]
        self.assertIn(token, stamp)
        self.assertNotIn(raw, stamp)
        self.assertNotIn(raw[1:], stamp)

    def test_token_moves_only_when_presence_moves(self) -> None:
        item = self._turns_item()
        first = self._token()
        self.assertEqual(self._token(), first)
        self._leave("Eli")
        after_leave = self._token()
        self.assertNotEqual(after_leave, first)
        self.assertEqual(self._token(), after_leave)

    def test_presence_blip_keeps_the_last_token(self) -> None:
        self._turns_item()
        good = self._token()
        with mock.patch.object(self.school, "_present_attendee_rev", return_value=""):
            self.assertEqual(self._token(), good)  # no drop to "" and back
        self.assertEqual(self._token(), good)

    def test_token_is_keyed_with_the_app_secret(self) -> None:
        self._turns_item()
        token = self._token()
        self.school.rank_alias_secret = "a-different-secret"
        self.assertNotEqual(self._token(), token)

    def test_waiting_phone_still_rebuilds_after_a_leave(self) -> None:
        """The #251 fix still holds with the hashed token."""
        item = self._turns_item()
        self._place("Ava", item, "o1")
        self._place("Cy", item, "o2")
        turns = self._missed_push_then_poll("Ava", item, lambda: self._leave("Eli"))
        self.assertTrue(turns["can_place"])


class ShortPhoneLayoutTests(unittest.TestCase):
    css = (STATIC / "student-portal.css").read_text(encoding="utf-8")

    def _block(self) -> str:
        start = self.css.index("/* MCK-186:")
        return self.css[start : self.css.index("@media (min-width: 48rem)", start)]

    def test_compact_header_and_scores_only_with_a_card_on_a_phone(self) -> None:
        block = self._block()
        self.assertIn("@media (max-width: 719.98px)", block)
        gate = "body.student-home:has(#live-question-stack-body > .student-live-card)"
        rules = re.findall(r"^  ([^\s}][^{\n]*)\{", block, flags=re.M)
        self.assertTrue(rules)
        for selector in rules:
            self.assertTrue(selector.startswith(gate), selector)
        for part in (".student-me ", ".me-stats", ".me-team-list", ".student-scoreboard", ".sb-espn-board"):
            self.assertIn(part, block)
        # Nothing is hidden: the bar keeps Save View, points, team and scores.
        self.assertNotIn("display: none", block)
        self.assertIn("overflow-x: auto", block)  # many teams scroll sideways


class PaneResizeListenerTests(unittest.TestCase):
    js = (STATIC / "student-portal.js").read_text(encoding="utf-8")

    def test_one_window_resize_listener(self) -> None:
        self.assertEqual(self.js.count('window.addEventListener("resize"'), 1)
        start = self.js.index("function bindFloatingPane(pane)")
        body = self.js[start : self.js.index("\n}\n", start)]
        self.assertNotIn('addEventListener("resize"', body)
        self.assertIn("paneResets.set(pane, resetPane);", body)
        self.assertIn("bindPaneResizeOnce();", body)
        self.assertIn("const paneResets = new WeakMap();", self.js)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Waiting-room meet-math payload helpers and seed/clear gates."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)

from meet_math import (  # noqa: E402
    MEET_MATH_CHOICES,
    MEET_MATH_ITEM_ID,
    MEET_MATH_PROMPT,
    WAITING_ROOM_WAIT_LINE,
    is_meet_math_payload,
    meet_math_prompt_payload,
)


class MeetMathHelperTests(unittest.TestCase):
    """Authoritative M1C1 meet-math copy and payload identity."""

    def test_payload_is_linear_rate_mc(self) -> None:
        """M1C1 waiting-room item is the prior-module linear-rate MC."""
        payload = meet_math_prompt_payload()
        self.assertEqual(payload["item_id"], MEET_MATH_ITEM_ID)
        self.assertEqual(payload["prompt"], MEET_MATH_PROMPT)
        self.assertEqual(payload["choices"], list(MEET_MATH_CHOICES))
        self.assertIn("Every step up adds the same amount", payload["choices"])
        self.assertTrue(is_meet_math_payload(payload))
        self.assertFalse(is_meet_math_payload({"item_id": "C1-CONS-1"}))
        self.assertFalse(is_meet_math_payload(None))
        self.assertEqual(
            WAITING_ROOM_WAIT_LINE, "Waiting room — class is about to begin."
        )
        self.assertNotIn("start scoring", WAITING_ROOM_WAIT_LINE)


if __name__ == "__main__":
    unittest.main(verbosity=2)

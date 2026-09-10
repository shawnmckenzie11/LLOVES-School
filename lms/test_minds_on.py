#!/usr/bin/env python3
"""Waiting-room Minds-On payload helpers and seed/clear gates."""

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

from minds_on import (  # noqa: E402
    MINDS_ON_CHOICES,
    MINDS_ON_ITEM_ID,
    MINDS_ON_LABEL,
    MINDS_ON_PROMPT,
    WAITING_ROOM_WAIT_LINE,
    is_minds_on_payload,
    minds_on_prompt_payload,
)


class MindsOnHelperTests(unittest.TestCase):
    """Authoritative M1C1 Minds-On copy and payload identity."""

    def test_payload_is_linear_rate_mc(self) -> None:
        """M1C1 waiting-room item is the prior-module linear-rate MC."""
        payload = minds_on_prompt_payload()
        self.assertEqual(payload["item_id"], MINDS_ON_ITEM_ID)
        self.assertEqual(payload["item_id"], "minds_on")
        self.assertEqual(payload["label"], MINDS_ON_LABEL)
        self.assertEqual(payload["label"], "Minds-On")
        self.assertEqual(payload["prompt"], MINDS_ON_PROMPT)
        self.assertEqual(payload["choices"], list(MINDS_ON_CHOICES))
        self.assertIn("Every step up adds the same amount", payload["choices"])
        self.assertTrue(is_minds_on_payload(payload))
        self.assertTrue(is_minds_on_payload({"item_id": "meet-math"}))
        self.assertFalse(is_minds_on_payload({"item_id": "C1-CONS-1"}))
        self.assertFalse(is_minds_on_payload(None))
        self.assertEqual(
            WAITING_ROOM_WAIT_LINE, "Waiting room — class is about to begin."
        )
        self.assertNotIn("start scoring", WAITING_ROOM_WAIT_LINE)
        self.assertNotIn("meet-math", payload["label"].lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)

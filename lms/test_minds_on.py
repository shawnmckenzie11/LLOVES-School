#!/usr/bin/env python3
"""Waiting-room Minds-On payload helpers and seed/clear gates."""

from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)

from minds_on import (  # noqa: E402
    MINDS_ON_BRIEF_PATH,
    MINDS_ON_CHOICES,
    MINDS_ON_ITEM_ID,
    MINDS_ON_KEY,
    MINDS_ON_LABEL,
    MINDS_ON_PROMPT,
    WAITING_ROOM_WAIT_LINE,
    is_minds_on_payload,
    minds_on_prompt_payload,
)
from quick_hitter import (  # noqa: E402
    CLEAR_ON_TEAM_CHALLENGE,
    QUICK_HITTER_ARTIFACT_ID,
    QUICK_HITTER_CHANNEL,
    RIDE_CONS,
    RIDE_MINDS_ON,
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
        self.assertEqual(
            payload["prompt"],
            (
                "A straight-line graph has a **constant rate of change**. "
                "Which statement best matches that?"
            ),
        )
        self.assertEqual(
            payload["choices"],
            [
                "Every equal step across adds the same amount up (or down)",
                "The graph curves",
                "Second differences in a table are constant",
                "Not sure",
            ],
        )
        self.assertEqual(payload["choices"], list(MINDS_ON_CHOICES))
        self.assertEqual(payload["key"], MINDS_ON_KEY)
        self.assertEqual(payload["key"], "A")
        self.assertEqual(
            MINDS_ON_BRIEF_PATH,
            "catalogue/challenges/module-briefs/minds-on/MCF3M-M1-C1-minds-on-student.md",
        )
        brief = REPO_ROOT / "content-builder" / MINDS_ON_BRIEF_PATH
        self.assertTrue(brief.is_file(), brief)
        self.assertIn(
            "Every equal step across adds the same amount up (or down)",
            brief.read_text(encoding="utf-8"),
        )
        escaped = (
            MINDS_ON_PROMPT.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        rendered = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
        self.assertIn("<strong>constant rate of change</strong>", rendered)
        self.assertNotIn("**", rendered)
        self.assertTrue(is_minds_on_payload(payload))
        self.assertTrue(is_minds_on_payload({"item_id": "meet-math"}))
        self.assertFalse(is_minds_on_payload({"item_id": "C1-CONS-1"}))
        self.assertFalse(is_minds_on_payload(None))
        self.assertEqual(
            WAITING_ROOM_WAIT_LINE, "Waiting room — class is about to begin."
        )
        self.assertNotIn("start scoring", WAITING_ROOM_WAIT_LINE)
        self.assertNotIn("meet-math", payload["label"].lower())
        self.assertEqual(payload["artifact_id"], QUICK_HITTER_ARTIFACT_ID)
        self.assertEqual(payload["ride"], RIDE_MINDS_ON)
        self.assertEqual(payload["channel"], QUICK_HITTER_CHANNEL)
        self.assertTrue(payload["ephemeral"])
        self.assertFalse(payload["durable_store"])
        self.assertEqual(payload["clear_on"], CLEAR_ON_TEAM_CHALLENGE)
        self.assertEqual(payload["chain_index"], 1)
        self.assertEqual(payload["chain_length"], 1)
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["item_id"], MINDS_ON_ITEM_ID)
        self.assertEqual(payload["items"][0]["kind"], "mc")
        self.assertEqual(payload["items"][0]["prompt"], MINDS_ON_PROMPT)
        self.assertEqual(payload["items"][0]["choices"], list(MINDS_ON_CHOICES))
        self.assertEqual(payload["items"][0]["key"], "A")
        brief_text = brief.read_text(encoding="utf-8")
        self.assertIn("one MC only", brief_text)
        self.assertIn("items.length === 1", brief_text)
        self.assertNotIn("carousel", payload)
        self.assertFalse(
            is_minds_on_payload(
                {
                    "artifact_id": QUICK_HITTER_ARTIFACT_ID,
                    "ride": RIDE_CONS,
                    "item_id": "C1-CONS-1",
                }
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

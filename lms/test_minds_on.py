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
    MINDS_ON_C2_BRIEF_PATH,
    MINDS_ON_C2_CHOICES,
    MINDS_ON_C2_KEY,
    MINDS_ON_C2_PROMPT,
    MINDS_ON_C3_BRIEF_PATH,
    MINDS_ON_C3_CHOICES,
    MINDS_ON_C3_KEY,
    MINDS_ON_C3_PROMPT,
    MINDS_ON_CHOICES,
    MINDS_ON_ITEM_ID,
    MINDS_ON_KEY,
    MINDS_ON_LABEL,
    MINDS_ON_PROMPT,
    WAITING_ROOM_WAIT_LINE,
    is_minds_on_payload,
    minds_on_prompt_payload,
    parse_minds_on_student_md,
)
from meet_team import meet_team_prompt_payload  # noqa: E402
from quick_hitter import (  # noqa: E402
    CLEAR_ON_TEAM_CHALLENGE,
    QUICK_HITTER_ARTIFACT_ID,
    QUICK_HITTER_CHANNEL,
    RIDE_CONS,
    RIDE_MEET_TEAM,
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
        self.assertFalse(
            is_minds_on_payload(
                {
                    "artifact_id": QUICK_HITTER_ARTIFACT_ID,
                    "ride": RIDE_MEET_TEAM,
                    "item_id": "meet-team",
                }
            )
        )
        self.assertFalse(is_minds_on_payload(meet_team_prompt_payload()))

    def test_c2_and_c3_payloads_are_one_mc(self) -> None:
        """C2/C3 waiting-room items stay one MC with their own soft keys."""
        c2 = minds_on_prompt_payload("C2")
        self.assertEqual(c2["live_slot"], "C2")
        self.assertEqual(c2["feedback_id"], "C2-minds_on")
        self.assertEqual(c2["prompt"], MINDS_ON_C2_PROMPT)
        self.assertEqual(c2["choices"], list(MINDS_ON_C2_CHOICES))
        self.assertEqual(c2["key"], MINDS_ON_C2_KEY)
        self.assertEqual(len(c2["items"]), 1)
        self.assertTrue(c2["ephemeral"])
        self.assertFalse(c2["durable_store"])
        self.assertEqual(c2["clear_on"], CLEAR_ON_TEAM_CHALLENGE)
        c2_brief = REPO_ROOT / "content-builder" / MINDS_ON_C2_BRIEF_PATH
        self.assertTrue(c2_brief.is_file(), c2_brief)
        self.assertIn("a > 0 (it opens upward)", c2_brief.read_text(encoding="utf-8"))
        c3 = minds_on_prompt_payload("C3")
        self.assertEqual(c3["live_slot"], "C3")
        self.assertEqual(c3["feedback_id"], "C3-minds_on")
        self.assertEqual(c3["prompt"], MINDS_ON_C3_PROMPT)
        self.assertEqual(c3["choices"], list(MINDS_ON_C3_CHOICES))
        self.assertEqual(c3["key"], MINDS_ON_C3_KEY)
        self.assertEqual(len(c3["items"]), 1)
        c3_brief = REPO_ROOT / "content-builder" / MINDS_ON_C3_BRIEF_PATH
        self.assertTrue(c3_brief.is_file(), c3_brief)
        self.assertIn("some stay free", c3_brief.read_text(encoding="utf-8"))
        unknown = minds_on_prompt_payload("C9")
        self.assertEqual(unknown["live_slot"], "C1")
        self.assertEqual(unknown["prompt"], MINDS_ON_PROMPT)
        self.assertTrue(is_minds_on_payload(c2))
        self.assertTrue(is_minds_on_payload(c3))
        for payload, brief in ((c2, c2_brief), (c3, c3_brief)):
            for field in ("chips", "curriculum_chips", "expectation_codes"):
                self.assertNotIn(field, payload)
                self.assertNotIn(field, payload["items"][0])
            brief_text = brief.read_text(encoding="utf-8")
            self.assertIn("No curriculum chips", brief_text)
            stem_block = brief_text.split("## Stem (student-facing)")[1]
            self.assertNotIn("A2.", stem_block)
        parsed_c2 = parse_minds_on_student_md(c2_brief.read_text(encoding="utf-8"))
        self.assertEqual(parsed_c2["prompt"], MINDS_ON_C2_PROMPT)
        self.assertEqual(parsed_c2["choices"], list(MINDS_ON_C2_CHOICES))
        self.assertEqual(parsed_c2["key"], MINDS_ON_C2_KEY)
        self.assertEqual(parsed_c2["key"], "A")
        parsed_c3 = parse_minds_on_student_md(c3_brief.read_text(encoding="utf-8"))
        self.assertEqual(parsed_c3["prompt"], MINDS_ON_C3_PROMPT)
        self.assertEqual(parsed_c3["choices"], list(MINDS_ON_C3_CHOICES))
        self.assertEqual(parsed_c3["key"], MINDS_ON_C3_KEY)
        self.assertEqual(parsed_c3["key"], "B")


if __name__ == "__main__":
    unittest.main(verbosity=2)

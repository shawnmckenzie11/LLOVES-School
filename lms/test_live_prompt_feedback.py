#!/usr/bin/env python3
"""M1C1 instant text feedback: keyed table + submit resolution."""

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

from live_media import (  # noqa: E402
    DEFAULT_LIVE_MEDIA_STEM,
    student_cons_prompt_payload,
    c1_cons_catalog,
)
from live_prompt_feedback import (  # noqa: E402
    M1C1_FEEDBACK,
    public_feedback_fragment,
    resolve_live_prompt_feedback,
    strip_teacher_prompt_fields,
)
from meet_math import meet_math_prompt_payload  # noqa: E402


KEYS_MD = (
    REPO_ROOT
    / "content-builder"
    / "catalogue"
    / "challenges"
    / "module-briefs"
    / "quick-hitters"
    / "MCF3M-M1-C1-feedback-keys.md"
)


class LivePromptFeedbackHelperTests(unittest.TestCase):
    """Soft-key juice for Minds-On and CONS; never the Team Challenge stem."""

    def test_minds_on_by_choice_letter_and_text(self) -> None:
        """Linear-rate MC key A returns the by_choice line."""
        payload = meet_math_prompt_payload()
        key_line = M1C1_FEEDBACK["minds_on"]["by_choice"]["A"]
        via_letter = resolve_live_prompt_feedback(payload, {"choice": "A"})
        self.assertEqual(via_letter["source"], "by_choice")
        self.assertEqual(via_letter["text"], key_line)
        via_text = resolve_live_prompt_feedback(
            payload, {"choice": "Every step up adds the same amount"}
        )
        self.assertEqual(via_text["text"], key_line)
        other = resolve_live_prompt_feedback(payload, {"choice": "The graph curves"})
        self.assertEqual(other["source"], "by_choice")
        self.assertEqual(other["text"], M1C1_FEEDBACK["minds_on"]["by_choice"]["B"])
        self.assertNotIn("key", strip_teacher_prompt_fields(payload))

    def test_legacy_and_rename_item_ids(self) -> None:
        """meet-math, minds_on, and minds-on share the same soft key."""
        line = M1C1_FEEDBACK["minds_on"]["by_choice"]["A"]
        for item_id in ("meet-math", "minds_on", "minds-on"):
            resolved = resolve_live_prompt_feedback(
                {"item_id": item_id, "choices": ["Every step up adds the same amount"]},
                {"choice": "A"},
            )
            self.assertEqual(resolved["text"], line, item_id)

    def test_cons_mc_and_share_lines(self) -> None:
        """CONS-1…3 use by_choice; CONS-4/5 use on_submit share lines."""
        cons1 = student_cons_prompt_payload(c1_cons_catalog()[0])
        hit = resolve_live_prompt_feedback(cons1, {"choice": "a > 0"})
        self.assertEqual(hit["source"], "by_choice")
        self.assertEqual(hit["text"], M1C1_FEEDBACK["C1-CONS-1"]["by_choice"]["B"])
        cons2 = resolve_live_prompt_feedback(
            {"item_id": "C1-CONS-2", "choices": ["c > 0", "c < 0", "c = 0"]},
            {"choice": "C"},
        )
        self.assertEqual(cons2["text"], M1C1_FEEDBACK["C1-CONS-2"]["by_choice"]["C"])
        cons3 = resolve_live_prompt_feedback(
            {"item_id": "C1-CONS-3"}, {"choice": "C"}
        )
        self.assertEqual(cons3["text"], M1C1_FEEDBACK["C1-CONS-3"]["by_choice"]["C"])
        share = resolve_live_prompt_feedback(
            {"item_id": "C1-CONS-4"}, {"text": "opens up so a > 0"}
        )
        self.assertEqual(share["source"], "on_submit")
        self.assertEqual(share["text"], M1C1_FEEDBACK["C1-CONS-4"]["on_submit"])
        still = resolve_live_prompt_feedback(
            {"item_id": "C1-CONS-5"}, {"text": "stretch?"}
        )
        self.assertEqual(still["text"], M1C1_FEEDBACK["C1-CONS-5"]["on_submit"])

    def test_team_challenge_stem_has_no_feedback(self) -> None:
        """Stem / generic MC is not a quick-hitter key."""
        self.assertIsNone(
            resolve_live_prompt_feedback(
                {"prompt": DEFAULT_LIVE_MEDIA_STEM, "choices": ["A", "B"]},
                {"choice": "A"},
            )
        )
        self.assertIsNone(
            public_feedback_fragment(
                {"item_id": "team-challenge", "choices": ["A"]},
                {"choice": "A"},
            )
        )

    def test_strip_teacher_fields(self) -> None:
        """Student GET must not see keys, cement, or the feedback map."""
        cleaned = strip_teacher_prompt_fields(
            {
                "item_id": "C1-CONS-1",
                "prompt": "what must be true about a",
                "key": "B",
                "cement": "opens upward",
                "soft_key": "B",
                "by_choice": {"B": "hidden"},
                "on_submit": "hidden",
                "feedback": {"text": "hidden"},
            }
        )
        self.assertEqual(cleaned["item_id"], "C1-CONS-1")
        for field in (
            "key",
            "cement",
            "soft_key",
            "by_choice",
            "on_submit",
            "feedback",
        ):
            self.assertNotIn(field, cleaned)

    def test_keys_brief_lists_soft_keys(self) -> None:
        """Authoring brief stays in lockstep with the runtime table."""
        self.assertTrue(KEYS_MD.is_file(), KEYS_MD)
        text = KEYS_MD.read_text(encoding="utf-8")
        self.assertIn("soft key **A**", text)
        self.assertIn("soft key **B**", text)
        self.assertIn("soft key **C**", text)
        self.assertIn(M1C1_FEEDBACK["minds_on"]["by_choice"]["A"], text)
        self.assertIn(M1C1_FEEDBACK["C1-CONS-1"]["by_choice"]["B"], text)
        self.assertIn(M1C1_FEEDBACK["C1-CONS-4"]["on_submit"], text)
        self.assertIn("Never attach feedback to the Team Challenge stem", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)

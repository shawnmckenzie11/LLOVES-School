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
    c1_cons_catalog,
    c2_cons_catalog,
    c3_cons_catalog,
    student_cons_prompt_payload,
)
from live_prompt_feedback import (  # noqa: E402
    FEEDBACK_TABLE,
    LEAD_MATCH,
    LEAD_MISS,
    LEAD_WEAK,
    M1C1_FEEDBACK,
    M1C2_FEEDBACK,
    M1C3_FEEDBACK,
    public_feedback_fragment,
    resolve_live_prompt_feedback,
    strip_teacher_prompt_fields,
)
from minds_on import MINDS_ON_CHOICES, minds_on_prompt_payload  # noqa: E402


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
        payload = minds_on_prompt_payload()
        key_line = M1C1_FEEDBACK["minds_on"]["by_choice"]["A"]
        via_letter = resolve_live_prompt_feedback(payload, {"choice": "A"})
        self.assertEqual(via_letter["source"], "by_choice")
        self.assertEqual(via_letter["text"], key_line)
        self.assertEqual(via_letter["lead"], LEAD_MATCH)
        self.assertTrue(via_letter["match"])
        via_text = resolve_live_prompt_feedback(
            payload, {"choice": MINDS_ON_CHOICES[0]}
        )
        self.assertEqual(via_text["text"], key_line)
        self.assertEqual(via_text["lead"], LEAD_MATCH)
        other = resolve_live_prompt_feedback(payload, {"choice": "The graph curves"})
        self.assertEqual(other["source"], "by_choice")
        self.assertEqual(other["text"], M1C1_FEEDBACK["minds_on"]["by_choice"]["B"])
        self.assertEqual(other["lead"], LEAD_MISS)
        self.assertFalse(other["match"])
        self.assertNotEqual(other["lead"], "Wrong.")
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
        self.assertEqual(share["lead"], LEAD_MATCH)
        empty = resolve_live_prompt_feedback({"item_id": "C1-CONS-4"}, {"text": "  "})
        self.assertEqual(empty["lead"], LEAD_WEAK)
        self.assertEqual(empty["source"], "on_submit")
        self.assertFalse(empty["match"])
        still = resolve_live_prompt_feedback(
            {"item_id": "C1-CONS-5"}, {"text": "stretch?"}
        )
        self.assertEqual(still["text"], M1C1_FEEDBACK["C1-CONS-5"]["on_submit"])
        self.assertEqual(still["lead"], LEAD_MATCH)
        local_weak = resolve_live_prompt_feedback(
            {
                "item_id": "C1-CONS-4",
                "on_weak": "Name the opening before you claim a.",
            },
            {"text": ""},
        )
        self.assertEqual(local_weak["source"], "on_weak")
        self.assertEqual(local_weak["lead"], LEAD_WEAK)
        self.assertEqual(local_weak["text"], "Name the opening before you claim a.")

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
                "on_weak": "hidden",
                "feedback": {"text": "hidden"},
                "chips": ["A2.1"],
                "curriculum_chips": ["A2.1"],
                "expectation_codes": ["A2.1"],
                "items": [
                    {
                        "item_id": "minds_on",
                        "key": "A",
                        "cement": "hidden",
                        "prompt": "stem",
                        "chips": ["A2.1"],
                    }
                ],
            }
        )
        self.assertEqual(cleaned["item_id"], "C1-CONS-1")
        for field in (
            "key",
            "cement",
            "soft_key",
            "by_choice",
            "on_submit",
            "on_weak",
            "feedback",
            "chips",
            "curriculum_chips",
            "expectation_codes",
        ):
            self.assertNotIn(field, cleaned)
        self.assertNotIn("key", cleaned["items"][0])
        self.assertNotIn("cement", cleaned["items"][0])
        self.assertNotIn("chips", cleaned["items"][0])
        self.assertEqual(cleaned["items"][0]["prompt"], "stem")

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

    def test_c2_and_c3_minds_on_and_light_cons(self) -> None:
        """C2/C3 use their own Match/Miss lines; C1 table stays untouched."""
        c2 = minds_on_prompt_payload("C2")
        hit = resolve_live_prompt_feedback(c2, {"choice": "A"})
        self.assertEqual(hit["text"], M1C2_FEEDBACK["C2-minds_on"]["by_choice"]["A"])
        miss = resolve_live_prompt_feedback(c2, {"choice": "B"})
        self.assertEqual(miss["text"], M1C2_FEEDBACK["C2-minds_on"]["by_choice"]["B"])
        c3 = minds_on_prompt_payload("C3")
        c3_hit = resolve_live_prompt_feedback(c3, {"choice": "B"})
        self.assertEqual(c3_hit["text"], M1C3_FEEDBACK["C3-minds_on"]["by_choice"]["B"])
        c3_miss = resolve_live_prompt_feedback(c3, {"choice": "A"})
        self.assertEqual(c3_miss["text"], M1C3_FEEDBACK["C3-minds_on"]["by_choice"]["A"])
        cons1 = student_cons_prompt_payload(c2_cons_catalog()[0])
        cons_hit = resolve_live_prompt_feedback(cons1, {"choice": "No"})
        self.assertEqual(cons_hit["text"], M1C2_FEEDBACK["C2-CONS-1"]["by_choice"]["B"])
        share = resolve_live_prompt_feedback(
            student_cons_prompt_payload(c2_cons_catalog()[1]),
            {"text": "the point ties a, h, and k"},
        )
        self.assertEqual(share["text"], M1C2_FEEDBACK["C2-CONS-2"]["on_submit"])
        c3_cons = resolve_live_prompt_feedback(
            student_cons_prompt_payload(c3_cons_catalog()[0]),
            {"choice": "No"},
        )
        self.assertEqual(c3_cons["text"], M1C3_FEEDBACK["C3-CONS-1"]["by_choice"]["B"])
        self.assertIn("C2-CONS-1", FEEDBACK_TABLE)
        self.assertIn("C3-CONS-3", FEEDBACK_TABLE)
        self.assertEqual(
            M1C1_FEEDBACK["minds_on"]["by_choice"]["A"],
            FEEDBACK_TABLE["minds_on"]["by_choice"]["A"],
        )
        c2_md = (
            REPO_ROOT
            / "content-builder"
            / "catalogue"
            / "challenges"
            / "module-briefs"
            / "quick-hitters"
            / "MCF3M-M1-C2-feedback-keys.md"
        )
        c3_md = (
            REPO_ROOT
            / "content-builder"
            / "catalogue"
            / "challenges"
            / "module-briefs"
            / "quick-hitters"
            / "MCF3M-M1-C3-feedback-keys.md"
        )
        self.assertTrue(c2_md.is_file(), c2_md)
        self.assertTrue(c3_md.is_file(), c3_md)
        self.assertIn(M1C2_FEEDBACK["C2-minds_on"]["by_choice"]["A"], c2_md.read_text())
        self.assertIn(M1C3_FEEDBACK["C3-minds_on"]["by_choice"]["B"], c3_md.read_text())
        self.assertIsNone(
            resolve_live_prompt_feedback(
                {"prompt": DEFAULT_LIVE_MEDIA_STEM, "live_slot": "C2"},
                {"choice": "A"},
            )
        )

    def test_public_fragment_includes_lead_not_key(self) -> None:
        """Student submit JSON gets lead + why and never the soft key."""
        payload = minds_on_prompt_payload()
        hit = public_feedback_fragment(payload, {"choice": "A"})
        miss = public_feedback_fragment(payload, {"choice": "B"})
        self.assertEqual(hit["lead"], LEAD_MATCH)
        self.assertEqual(hit["text"], M1C1_FEEDBACK["minds_on"]["by_choice"]["A"])
        self.assertEqual(miss["lead"], LEAD_MISS)
        for fragment in (hit, miss):
            self.assertNotIn("soft_key", fragment)
            self.assertNotIn("by_choice", fragment)
            self.assertNotIn("key", fragment)
            self.assertNotEqual(fragment["lead"], "Wrong.")


if __name__ == "__main__":
    unittest.main(verbosity=2)

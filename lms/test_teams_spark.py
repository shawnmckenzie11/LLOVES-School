#!/usr/bin/env python3
"""Welcome C2 integer-poll copy and payload helpers."""

from __future__ import annotations

import unittest

from live_prompt_feedback import strip_teacher_prompt_fields
from teams_spark import (
    CUE_TEAMS_SPARK,
    TEAMS_SPARK_KIND,
    TEAMS_SPARK_PROMPT,
    TEAMS_SPARK_PROMPT_REF,
    is_teams_spark_payload,
    is_teams_spark_ref,
    staff_teams_spark_card,
    teams_spark_prompt_payload,
)


class TeamsSparkHelperTests(unittest.TestCase):
    """Welcome C2 is one ephemeral integer poll, not an MC riddle."""

    def test_locked_copy_and_integer_only(self) -> None:
        """Stem and integer-only flag are the product lock."""
        payload = teams_spark_prompt_payload()
        self.assertTrue(is_teams_spark_payload(payload))
        self.assertTrue(is_teams_spark_ref(TEAMS_SPARK_PROMPT_REF))
        self.assertEqual(payload["prompt"], TEAMS_SPARK_PROMPT)
        self.assertEqual(payload["kind"], TEAMS_SPARK_KIND)
        self.assertTrue(payload["integer_only"])
        self.assertNotIn("choices", payload)
        self.assertNotIn("key", payload)
        self.assertFalse(payload["gradebook"])
        self.assertFalse(payload["meet_chip"])
        self.assertEqual(CUE_TEAMS_SPARK, "cue.teams_spark")
        cleaned = strip_teacher_prompt_fields(payload)
        self.assertNotIn("key", cleaned)
        self.assertNotIn("teacher_key", cleaned)
        card = staff_teams_spark_card(payload, reveal=False)
        self.assertEqual(card["prompt"], TEAMS_SPARK_PROMPT)
        self.assertTrue(card["integer_only"])
        self.assertEqual(card["teacher_key"], "")
        self.assertFalse(card["reveal"])
        self.assertFalse(is_teams_spark_payload({"item_id": "minds_on"}))


if __name__ == "__main__":
    unittest.main()

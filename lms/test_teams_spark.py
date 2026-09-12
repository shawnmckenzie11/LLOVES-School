#!/usr/bin/env python3
"""Locked TEAMS shared-spark copy and payload helpers."""

from __future__ import annotations

import unittest

from live_prompt_feedback import strip_teacher_prompt_fields
from teams_spark import (
    CUE_TEAMS_SPARK,
    TEAMS_SPARK_CHOICES,
    TEAMS_SPARK_KEY,
    TEAMS_SPARK_PROMPT,
    TEAMS_SPARK_PROMPT_REF,
    TEAMS_SPARK_STUDENT_FEEDBACK,
    TEAMS_SPARK_TEACHER_KEY,
    is_teams_spark_payload,
    is_teams_spark_ref,
    staff_teams_spark_card,
    teams_spark_prompt_payload,
)


class TeamsSparkHelperTests(unittest.TestCase):
    """Beat 25 locked riddle stays one ephemeral MC."""

    def test_locked_copy_and_teacher_only_key(self) -> None:
        """Stem, 8·9·17·0 choices, and teacher key are the product lock."""
        payload = teams_spark_prompt_payload()
        self.assertTrue(is_teams_spark_payload(payload))
        self.assertTrue(is_teams_spark_ref(TEAMS_SPARK_PROMPT_REF))
        self.assertEqual(payload["prompt"], TEAMS_SPARK_PROMPT)
        self.assertEqual(payload["choices"], list(TEAMS_SPARK_CHOICES))
        self.assertEqual(payload["key"], TEAMS_SPARK_KEY)
        self.assertEqual(payload["teacher_key"], TEAMS_SPARK_TEACHER_KEY)
        self.assertFalse(payload["gradebook"])
        self.assertFalse(payload["meet_chip"])
        self.assertEqual(CUE_TEAMS_SPARK, "cue.teams_spark")
        cleaned = strip_teacher_prompt_fields(payload)
        self.assertNotIn("key", cleaned)
        self.assertNotIn("teacher_key", cleaned)
        self.assertEqual(
            cleaned["student_feedback_after_reveal"], TEAMS_SPARK_STUDENT_FEEDBACK
        )
        card = staff_teams_spark_card(payload, reveal=False)
        self.assertIn("9", card["teacher_key"])
        self.assertFalse(card["reveal"])
        self.assertFalse(is_teams_spark_payload({"item_id": "minds_on"}))


if __name__ == "__main__":
    unittest.main()

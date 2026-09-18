#!/usr/bin/env python3
"""Unit tests for ingest MC → live MC normalization."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

from bank_mc_normalize import (  # noqa: E402
    bank_matches_module,
    is_primary_module_test_bank,
    normalize_bank_mc,
    parse_module_token,
    simplify_html,
)


def ingest_mc_payload() -> dict:
    """Return the MC payload shape produced by ``test_ingest`` fixtures."""
    return {
        "stem_html": "What is a cell wall?",
        "points_possible": 2.0,
        "choices": [
            {"id": "a1", "html": "A rigid outer layer", "correct": True},
            {"id": "a2", "html": "A type of enzyme", "correct": False},
            {"id": "a3", "html": "<em>An organelle</em>", "correct": False},
        ],
        "correct_ids": ["a1"],
        "position": 1,
    }


class BankMcNormalizeTests(unittest.TestCase):
    """Normalizer edge cases from ingest fixtures."""

    def test_parse_module_token(self) -> None:
        """Module tokens parse to one-based numbers."""
        self.assertEqual(parse_module_token("M1"), 1)
        self.assertIsNone(parse_module_token("m12"))
        self.assertIsNone(parse_module_token("hello"))

    def test_simplify_html_strips_tags(self) -> None:
        """HTML stems and choices collapse to plain text."""
        self.assertEqual(
            simplify_html("<p>What is a <strong>cell</strong> wall?</p>"),
            "What is a cell wall?",
        )
        self.assertEqual(simplify_html("<em>An organelle</em>"), "An organelle")

    def test_normalize_happy_path(self) -> None:
        """A valid ingest MC maps to live letter answer and options."""
        live, err = normalize_bank_mc(
            question_id=10,
            bank_id=3,
            item_type="multiple_choice_question",
            payload=ingest_mc_payload(),
        )
        assert live is not None and err is None
        self.assertEqual(live["type"], "mc")
        self.assertIn("cell wall", live["text"].lower())
        self.assertEqual(live["options"][0], "A rigid outer layer")
        self.assertEqual(live["options"][2], "An organelle")
        self.assertEqual(live["correct_answer"], "A")
        self.assertEqual(live["points"], 2.0)
        self.assertEqual(live["source_question_id"], 10)
        self.assertEqual(live["source_bank_id"], 3)

    def test_normalize_rejects_zero_correct(self) -> None:
        """Zero correct choices return an error reason."""
        payload = ingest_mc_payload()
        for choice in payload["choices"]:
            choice["correct"] = False
        payload["correct_ids"] = []
        live, err = normalize_bank_mc(
            question_id=1,
            bank_id=1,
            item_type="multiple_choice_question",
            payload=payload,
        )
        self.assertIsNone(live)
        self.assertEqual(err, "no_correct_answer")

    def test_normalize_rejects_multiple_correct(self) -> None:
        """More than one correct choice is rejected."""
        payload = ingest_mc_payload()
        payload["choices"][1]["correct"] = True
        live, err = normalize_bank_mc(
            question_id=1,
            bank_id=1,
            item_type="multiple_choice_question",
            payload=payload,
        )
        self.assertIsNone(live)
        self.assertEqual(err, "multiple_correct_answers")

    def test_normalize_rejects_non_mc(self) -> None:
        """Short-answer rows are skipped."""
        live, err = normalize_bank_mc(
            question_id=1,
            bank_id=1,
            item_type="short_answer_question",
            payload={"correct_answers": ["x"]},
        )
        self.assertIsNone(live)
        self.assertEqual(err, "not_multiple_choice")

    def test_normalize_uses_overlay(self) -> None:
        """Staff overlay fields override ingest payload."""
        live, err = normalize_bank_mc(
            question_id=5,
            bank_id=2,
            item_type="multiple_choice_question",
            payload=ingest_mc_payload(),
            overlay={
                "stem_text": "Overlay stem",
                "options_json": json.dumps(["One", "Two", "Three"]),
                "correct_answer": "C",
                "points": 3.5,
            },
        )
        assert live is not None and err is None
        self.assertEqual(live["text"], "Overlay stem")
        self.assertEqual(live["options"], ["One", "Two", "Three"])
        self.assertEqual(live["correct_answer"], "C")
        self.assertEqual(live["points"], 3.5)

    def test_bank_matches_module_heuristics(self) -> None:
        """Bank titles and import keys match module heuristics."""
        self.assertTrue(
            bank_matches_module(
                title="Chapter 1 Quiz",
                import_key="bank:ch1",
                module_number=1,
            )
        )
        self.assertTrue(
            bank_matches_module(
                title="Module 2 Test",
                import_key="bank:m2test",
                module_number=2,
            )
        )
        self.assertFalse(
            bank_matches_module(
                title="Unit 2 Quiz",
                import_key="bank:unit2",
                module_number=1,
            )
        )



    def test_is_primary_module_test_bank(self) -> None:
        """Primary test pools match canonical module test titles only."""
        self.assertTrue(
            is_primary_module_test_bank(title="Module 1 Test", module_number=1)
        )
        self.assertTrue(
            is_primary_module_test_bank(title="MCF 3M Module 1 Test", module_number=1)
        )
        self.assertTrue(
            is_primary_module_test_bank(
                title="Module 1: Introduction to the Quadratic Function TEST",
                module_number=1,
            )
        )
        self.assertFalse(
            is_primary_module_test_bank(title="Chapter 1 MC", module_number=1)
        )
        self.assertFalse(
            is_primary_module_test_bank(title="Module 1 Assignment", module_number=1)
        )

if __name__ == "__main__":
    unittest.main()

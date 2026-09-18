#!/usr/bin/env python3
"""Unit tests for staff Question banks serialize/sanitize helpers."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from bank_edit import (  # noqa: E402
    EMPTY_BANKS_MESSAGE,
    sanitize_bank_html,
    serialize_staff_question,
    truncate_stem,
)


class BankEditHelperTests(unittest.TestCase):
    """Pure helpers used by the staff Question banks tab."""

    def test_empty_copy_is_exact(self) -> None:
        """Wonder empty-state copy is locked."""
        self.assertEqual(EMPTY_BANKS_MESSAGE, "No banks imported yet.")

    def test_truncate_stem_adds_ellipsis(self) -> None:
        """Long stems collapse to one preview line."""
        long = "x" * 200
        preview = truncate_stem(long, limit=140)
        self.assertEqual(len(preview), 141)
        self.assertTrue(preview.endswith("…"))

    def test_sanitize_strips_script_and_keeps_table(self) -> None:
        """Teacher HTML is rebuilt through the MC sanitizer."""
        html = (
            "<script>alert(1)</script><table><tr><td>7</td></tr></table>"
            "<p>Keep</p>"
        )
        clean = sanitize_bank_html(html)
        self.assertNotIn("<script", clean.lower())
        self.assertNotIn("alert(1)", clean)
        self.assertIn("<table>", clean)
        self.assertIn("Keep", clean)

    def test_serialize_marks_imported_overlay(self) -> None:
        """An overlay on an ingest row is a local LMS edit."""
        view = serialize_staff_question(
            {
                "id": 4,
                "bank_id": 2,
                "import_key": "qti:q2",
                "item_type": "multiple_choice_question",
                "title": "Old",
                "payload": {
                    "stem_html": "<p>Imported stem</p>",
                    "choices": [
                        {"id": "A", "html": "One", "correct": True},
                        {"id": "B", "html": "Two", "correct": False},
                    ],
                },
            },
            {
                "stem_text": "Edited stem",
                "options_json": '["Alpha", "Beta"]',
                "correct_answer": "B",
                "points": 3,
            },
        )
        self.assertTrue(view["edited_in_lms"])
        self.assertFalse(view["authored_in_lms"])
        self.assertEqual(view["item_type"], "multiple_choice_question")
        self.assertEqual(view["stem_plain"], "Edited stem")
        self.assertEqual(view["choices"][1]["text"], "Beta")
        self.assertTrue(view["choices"][1]["correct"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

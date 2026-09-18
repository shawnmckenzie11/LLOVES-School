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
    CANCEL_LABEL,
    DONE_LABEL,
    EDITED_IN_LMS_CHIP,
    EDIT_BANK_LABEL,
    EMPTY_BANKS_MESSAGE,
    REMOVE_FROM_BANK_TITLE,
    SAVE_TOAST,
    WONDER_COPY,
    sanitize_bank_html,
    serialize_staff_question,
    truncate_stem,
)


class BankEditHelperTests(unittest.TestCase):
    """Pure helpers used by the staff Question banks tab."""

    def test_wonder_section_nine_copy_is_exact(self) -> None:
        """Wonder IA §9 microcopy is locked — do not paraphrase."""
        self.assertEqual(EMPTY_BANKS_MESSAGE, "No banks imported yet.")
        self.assertEqual(EDIT_BANK_LABEL, "Edit bank")
        self.assertEqual(DONE_LABEL, "Done")
        self.assertEqual(CANCEL_LABEL, "Cancel")
        self.assertEqual(REMOVE_FROM_BANK_TITLE, "Remove from bank?")
        self.assertEqual(EDITED_IN_LMS_CHIP, "Edited in LMS")
        self.assertEqual(SAVE_TOAST, "Saved.")
        self.assertEqual(
            WONDER_COPY,
            {
                "empty_banks": "No banks imported yet.",
                "edit_bank": "Edit bank",
                "done": "Done",
                "cancel": "Cancel",
                "remove_from_bank": "Remove from bank?",
                "edited_in_lms": "Edited in LMS",
                "save_toast": "Saved.",
            },
        )

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

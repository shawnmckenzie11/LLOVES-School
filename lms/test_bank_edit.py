#!/usr/bin/env python3
"""Unit tests for staff Question banks serialize/sanitize helpers."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from bank_edit import (  # noqa: E402
    EDITED_IN_LMS_CHIP,
    EMPTY_BANKS_MESSAGE,
    REMOVE_FROM_BANK_TITLE,
    SAVE_TOAST,
    WONDER_COPY,
    preview_stem_html,
    sanitize_bank_html,
    serialize_staff_question,
    truncate_stem,
    truncate_stem_preserving_math,
)
from components import get_question_bank  # noqa: E402


class BankEditHelperTests(unittest.TestCase):
    """Pure helpers used by the staff Question banks tab."""

    def test_wonder_section_nine_copy_is_exact(self) -> None:
        """Wonder IA §9 microcopy is locked — do not paraphrase."""
        self.assertEqual(EMPTY_BANKS_MESSAGE, "No banks imported yet.")
        self.assertEqual(REMOVE_FROM_BANK_TITLE, "Remove from bank?")
        self.assertEqual(EDITED_IN_LMS_CHIP, "Edited in LMS")
        self.assertEqual(SAVE_TOAST, "Saved to this course’s copy.")
        self.assertEqual(
            WONDER_COPY,
            {
                "empty_banks": "No banks imported yet.",
                "remove_from_bank": "Remove from bank?",
                "edited_in_lms": "Edited in LMS",
                "save_toast": "Saved to this course’s copy.",
            },
        )
        self.assertNotIn("edit_bank", WONDER_COPY)
        self.assertNotIn("done", WONDER_COPY)
        self.assertNotIn("cancel", WONDER_COPY)

    def test_truncate_stem_adds_ellipsis(self) -> None:
        """Long stems collapse to one preview line."""
        long = "x" * 200
        preview = truncate_stem(long, limit=140)
        self.assertEqual(len(preview), 141)
        self.assertTrue(preview.endswith("…"))

    def test_preview_stem_html_wraps_tex(self) -> None:
        """Browse stems keep KaTeX-ready spans instead of raw ``$\\frac$``."""
        html = preview_stem_html(r"Evaluate $\frac{1}{2}$ of the set")
        self.assertIn("math-latex", html)
        self.assertIn(r"\frac{1}{2}", html)
        self.assertNotIn(r"$\frac", html)

    def test_truncate_stem_preserving_math_does_not_split_dollars(self) -> None:
        """A cut that lands inside ``$...$`` extends to the closing dollar."""
        stem = "Prefix " + ("word " * 20) + r"$x^2$ and more after that"
        clipped = truncate_stem_preserving_math(stem, limit=len("Prefix " + ("word " * 20) + "$x"))
        self.assertIn("$x^2$", clipped)
        self.assertEqual(clipped.count("$"), 2)

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

    def test_sanitize_wraps_dollar_tex(self) -> None:
        """Bank HTML with $TeX$ becomes a math-latex span."""
        clean = sanitize_bank_html("<p>Evaluate $x^2 + 1$</p>")
        self.assertIn('class="math-latex"', clean)
        self.assertIn("data-latex=", clean)
        self.assertNotIn("$x^2", clean)

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
        self.assertIn("Edited stem", view["stem_preview_html"])
        self.assertEqual(view["choices"][1]["text"], "Beta")
        self.assertTrue(view["choices"][1]["correct"])


class CourseLibraryBankLookupTests(unittest.TestCase):
    """Question-bank reads and writes use the course-owned shell."""

    def setUp(self) -> None:
        """Pack-less course whose banks live on the shell library."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.client = self.app.test_client()
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="SBI4U"
        )
        self.shell_id = int(self.offering["shell_library_id"])
        self.bank_id = int(
            self.school.conn.execute(
                """
                SELECT id FROM question_banks
                WHERE library_id = ? AND import_key = 'shell-course'
                """,
                (self.shell_id,),
            ).fetchone()["id"]
        )
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="SBI4U",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["A1"],
            offering_id=int(self.offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )
        self.class_id = int(created["id"])
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        user = self.school.get_user_by_email("teacher@gmail.com")
        assert user is not None
        self.client.post("/verify-email", data={"code": user["verification_code"]})

    def tearDown(self) -> None:
        """Close sqlite and remove the temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def test_get_question_bank_reads_the_course_library(self) -> None:
        """The shell bank is visible, and a different library cannot see it."""
        bank = get_question_bank(self.school, self.shell_id, self.bank_id)
        self.assertIsNotNone(bank)
        self.assertEqual(bank["import_key"], "shell-course")
        self.assertEqual(int(bank["library_id"]), self.shell_id)
        self.assertIsNone(get_question_bank(self.school, self.shell_id + 50, self.bank_id))
        self.assertIsNone(get_question_bank(self.school, 0, self.bank_id))

    def test_course_page_add_lands_in_the_shell(self) -> None:
        """Adding from the course page stores the question on the shell bank."""
        saved = self.client.post(
            f"/api/staff/class/{self.class_id}/question-bank/{self.bank_id}/questions",
            json={
                "stem_text": "Shell course bank stem",
                "options": ["One", "Two"],
                "correct_answer": "A",
                "points": 1,
            },
        )
        self.assertEqual(saved.status_code, 200, saved.get_data(as_text=True))
        question_id = int(saved.get_json()["question"]["id"])
        row = self.school.conn.execute(
            """
            SELECT b.library_id, b.id AS bank_id
            FROM questions q
            JOIN question_banks b ON b.id = q.bank_id
            WHERE q.id = ?
            """,
            (question_id,),
        ).fetchone()
        self.assertEqual(int(row["library_id"]), self.shell_id)
        self.assertEqual(int(row["bank_id"]), self.bank_id)
        listed = self.client.get(
            f"/api/staff/class/{self.class_id}/question-bank/{self.bank_id}"
        )
        self.assertEqual(listed.status_code, 200, listed.get_data(as_text=True))
        ids = {int(item["id"]) for item in listed.get_json()["questions"]}
        self.assertIn(question_id, ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)

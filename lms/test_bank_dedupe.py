#!/usr/bin/env python3
"""Unit tests for MCF3M / MCR3U bank duplicate detection."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from bank_dedupe import (  # noqa: E402
    apply_visible_bank_question_counts,
    library_canonical_ids,
    normalize_question_text,
    question_fingerprint,
    select_canonical_questions,
)
from bank_edit import list_staff_bank_questions  # noqa: E402
from school_db import _now  # noqa: E402


class BankDedupeHelperTests(unittest.TestCase):
    """Fingerprint and merge-rule helpers."""

    def test_normalize_treats_tex_wrappers_as_same_stem(self) -> None:
        """Dollar TeX, caret HTML, and entities collapse to one key."""
        a = normalize_question_text("Find $x^2$ when x &lt; 5")
        b = normalize_question_text("Find <span class='math-latex' data-latex='x^2'>x^2</span> when x < 5")
        c = normalize_question_text("Find x^2 when x < 5")
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_select_canonical_prefers_overlay_then_richer_html(self) -> None:
        """Edited LMS row wins; otherwise richest HTML / lowest id."""
        kept, dropped = select_canonical_questions(
            [
                {
                    "id": 1,
                    "stem_plain": "Find x^2",
                    "stem_html": "<p>Find x^2</p>",
                    "options": ["2", "4"],
                    "bank_title": "Chapter 1 Quiz",
                },
                {
                    "id": 2,
                    "stem_plain": "Find $x^2$",
                    "stem_html": "<p>Find <span class='math-latex' data-latex='x^2'>x^2</span></p>",
                    "options": ["2", "4"],
                    "bank_title": "Module 1 Test",
                    "edited_in_lms": True,
                },
                {
                    "id": 3,
                    "stem_plain": "A different stem",
                    "options": ["A", "B"],
                },
            ]
        )
        ids = [row["id"] for row in kept]
        self.assertEqual(ids, [2, 3])
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0]["duplicate_of"], 2)

    def test_empty_stems_do_not_merge(self) -> None:
        """Image-only empties stay distinct by id."""
        kept, dropped = select_canonical_questions(
            [
                {"id": 10, "stem_plain": "", "options": []},
                {"id": 11, "stem_plain": "", "options": []},
            ]
        )
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, [])


class BankDedupeLibraryTests(unittest.TestCase):
    """Library-wide hide across two MCF3M banks; MCR3U stays independent."""

    def setUp(self) -> None:
        """Create two course libraries with a shared stem."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.school = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        ).config["SCHOOL_DB"]
        self.mcf = int(self.school.create_library("MCF3M", origin="upload")["id"])
        self.mcr = int(self.school.create_library("MCR3U", origin="upload")["id"])

    def tearDown(self) -> None:
        """Close sqlite and remove temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _bank(self, library_id: int, title: str, key: str) -> int:
        """Insert one question bank."""
        cur = self.school.conn.execute(
            """
            INSERT INTO question_banks (
                library_id, import_key, title, settings_json, created_at
            ) VALUES (?, ?, ?, '{}', ?)
            """,
            (int(library_id), key, title, _now()),
        )
        self.school.conn.commit()
        return int(cur.lastrowid)

    def _mc(
        self,
        bank_id: int,
        key: str,
        stem: str,
        options: list[str] | None = None,
    ) -> int:
        """Insert one multiple-choice question."""
        opts = options or ["First option", "Second option"]
        choices = [
            {"id": "a", "html": opts[0], "correct": True},
            {"id": "b", "html": opts[1], "correct": False},
        ]
        payload = {
            "stem_html": stem,
            "points_possible": 1.0,
            "choices": choices,
            "correct_ids": ["a"],
        }
        cur = self.school.conn.execute(
            """
            INSERT INTO questions (
                bank_id, import_key, item_type, title, payload_json, created_at
            ) VALUES (?, ?, 'multiple_choice_question', ?, ?, ?)
            """,
            (int(bank_id), key, stem[:40], json.dumps(payload), _now()),
        )
        self.school.conn.commit()
        return int(cur.lastrowid)

    def test_hides_within_and_across_mcf3m_banks(self) -> None:
        """Same stem in two MCF3M banks collapses to the Module 1 Test copy."""
        quiz = self._bank(self.mcf, "Chapter 1 Quiz", "qti:quiz")
        test = self._bank(self.mcf, "Module 1 Test", "qti:test")
        unique = self._bank(self.mcf, "Module 2 Extra", "qti:extra")
        self._mc(quiz, "q1", "<p>Find $x^2$</p>")
        keep = self._mc(test, "q2", "<p>Find x^2</p>")
        extra = self._mc(unique, "q3", "<p>A different stem</p>")
        mapping = library_canonical_ids(self.school, self.mcf)
        self.assertEqual(mapping[keep], keep)
        self.assertEqual(mapping[extra], extra)
        hidden = [qid for qid, winner in mapping.items() if qid != winner]
        self.assertEqual(len(hidden), 1)
        quiz_views = list_staff_bank_questions(self.school, self.mcf, quiz)
        test_views = list_staff_bank_questions(self.school, self.mcf, test)
        extra_views = list_staff_bank_questions(self.school, self.mcf, unique)
        self.assertEqual(quiz_views, [])
        self.assertEqual([row["id"] for row in test_views], [keep])
        self.assertEqual([row["id"] for row in extra_views], [extra])
        counted = apply_visible_bank_question_counts(
            self.school,
            self.mcf,
            [
                {"id": quiz, "question_count": 1},
                {"id": test, "question_count": 1},
                {"id": unique, "question_count": 1},
            ],
        )
        by_id = {int(row["id"]): int(row["question_count"]) for row in counted}
        self.assertEqual(by_id[quiz], 0)
        self.assertEqual(by_id[test], 1)
        self.assertEqual(by_id[unique], 1)

    def test_mcr3u_does_not_collapse_against_mcf3m(self) -> None:
        """The same stem in MCR3U is a different library, so it stays."""
        mcf_bank = self._bank(self.mcf, "Module 1 Test", "qti:mcf")
        mcr_bank = self._bank(self.mcr, "Module 1 Test", "qti:mcr")
        mcf_id = self._mc(mcf_bank, "q1", "<p>Find x^2</p>")
        mcr_id = self._mc(mcr_bank, "q1", "<p>Find x^2</p>")
        mcf_map = library_canonical_ids(self.school, self.mcf)
        mcr_map = library_canonical_ids(self.school, self.mcr)
        self.assertEqual(mcf_map[mcf_id], mcf_id)
        self.assertEqual(mcr_map[mcr_id], mcr_id)
        self.assertEqual(len(list_staff_bank_questions(self.school, self.mcr, mcr_bank)), 1)

    def test_search_module_bank_mcs_drops_duplicate(self) -> None:
        """Live import search returns one row for a repeated stem."""
        test = self._bank(self.mcf, "Module 1 Test", "qti:m1-test")
        quiz = self._bank(self.mcf, "Module 1 Practice", "qti:m1-prac")
        self._mc(test, "q1", "<p>Find the vertex of y = x^2</p>")
        self._mc(quiz, "q2", "<p>Find the vertex of y = x^2</p>")
        self.school.confirm_module_bank_links(self.mcf, 1, [test, quiz])
        hits = self.school.search_module_bank_mcs(self.mcf, 1, "")
        self.assertEqual(hits["total"], 1)
        self.assertEqual(len(hits["items"]), 1)

    def test_fingerprints_ignore_option_order(self) -> None:
        """A/B swap of the same choices still matches."""
        a = question_fingerprint(
            {"id": 1, "stem_plain": "Pick one", "options": ["Alpha", "Beta"]}
        )
        b = question_fingerprint(
            {"id": 2, "stem_plain": "Pick one", "options": ["Beta", "Alpha"]}
        )
        self.assertEqual(a, b)

    def test_near_duplicate_frac_and_punctuation(self) -> None:
        """``\\frac{1}{2}``, ``1/2``, and a trailing period share a key."""
        a = normalize_question_text(r"Find $\frac{1}{2}$.")
        b = normalize_question_text("Find 1/2")
        self.assertEqual(a, b)

    def test_prefer_better_tex_as_canonical(self) -> None:
        """When stems match after normalize, keep the richer TeX copy."""
        kept, dropped = select_canonical_questions(
            [
                {
                    "id": 1,
                    "stem_plain": "Find 1/2",
                    "stem_html": "<p>Find 1/2</p>",
                    "options": ["A", "B"],
                    "bank_title": "Module 1 Extra",
                },
                {
                    "id": 2,
                    "stem_plain": r"Find $\frac{1}{2}$",
                    "stem_html": r"<p>Find $\frac{1}{2}$</p>",
                    "options": ["A", "B"],
                    "bank_title": "Module 1 Extra",
                },
            ]
        )
        self.assertEqual([row["id"] for row in kept], [2])
        self.assertEqual(dropped[0]["duplicate_of"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

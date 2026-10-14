#!/usr/bin/env python3
"""MCK-229: Lesson Slides one table per live class (latest real run)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from lesson_slides_table import build_lesson_slides_table, filter_questions  # noqa: E402
from test_mck207_real_vs_test_runs import RealVsTestFlowTests  # noqa: E402


class LessonSlidesTableTests(RealVsTestFlowTests):
    """API + ground-truth row after a real run."""

    def setUp(self) -> None:
        """Flask client for HTTP assertions on top of the MCK-207 flow fixture."""
        super().setUp()
        self.client = self.app.test_client()

    def test_table_api_requires_teacher(self) -> None:
        """Anonymous clients cannot read the Lesson Slides table."""
        anon = self.client.get(f"/api/classes/{self.class_id}/lesson-slides/table")
        self.assertIn(anon.status_code, (302, 401, 403))

    def test_real_run_populates_latest_row_and_table(self) -> None:
        """Ending a real class writes lesson_slides_latest and exposes join code."""
        run_key = self._end_run("M1", "C1", ended_iso="2026-10-14T14:30:00")
        latest = self.school.list_lesson_slides_latest(self.class_id)
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["run_key"], run_key)
        self.assertTrue(latest[0]["questions"])
        payload = build_lesson_slides_table(self.school, self.class_id)
        row = next(r for r in payload["rows"] if r["label"] == "M1C1")
        self.assertTrue(row["has_run"])
        self.assertTrue(row["join_code"])
        self.assertTrue(row["catchup_enabled"])

    def test_test_student_gets_default_pin_and_excluded_from_participation(self) -> None:
        """Test Student uses generated default PINs, not a hardcoded production PIN."""
        info = self.school.ensure_class_test_student(self.class_id)
        assert info is not None
        self.assertEqual(info["codename"], self.school.TEST_STUDENT_CODENAME)
        stu_id = int(info["student_id"])
        self.assertIn(stu_id, self.school.test_student_ids(self.class_id))
        secret = "unit-test-pin-secret"
        assigned = self.school.ensure_default_pins_for_class(self.class_id, secret)
        self.assertGreaterEqual(int(assigned["assigned"]), 1)
        with self.school.game._lock:
            row = self.school.game.conn.execute(
                "SELECT pin_hash, default_pin_ciphertext FROM students WHERE id = ?",
                (stu_id,),
            ).fetchone()
        self.assertTrue(str(row["pin_hash"] or "").strip())
        self.assertTrue(str(row["default_pin_ciphertext"] or "").strip())
        part = self.school.class_participation(self.class_id)
        student_ids = {int(r["student_id"]) for r in part["students"]}
        self.assertNotIn(stu_id, student_ids)

    def test_lesson_slides_tab_mounts_table(self) -> None:
        """Staff Lesson Slides tab loads the new table shell."""
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        code = self.school.get_user_by_email("teacher@gmail.com")["verification_code"]
        self.client.post("/verify-email", data={"code": code})
        page = self.client.get(f"/staff/class/{self.class_id}?tab=lesson-slides")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn('id="ls-run-table"', html)
        self.assertNotIn('id="ls-module-sections"', html)


class FilterQuestionsTests(unittest.TestCase):
    """Client-side filter tokens."""

    def test_weighted_only(self) -> None:
        """Weighted filter keeps MC rows only."""
        rows = [
            {"mode": "individual", "weight": 1},
            {"mode": "group", "weight": 0},
        ]
        out = filter_questions(rows, "weighted")
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main()

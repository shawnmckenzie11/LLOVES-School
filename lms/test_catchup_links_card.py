#!/usr/bin/env python3
"""MCK-203 / MCK-229: catch-up links card module (legacy) and Lesson Slides table."""

from __future__ import annotations

import os
import subprocess
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


class CatchupCardStaticTests(unittest.TestCase):
    """Node harness for the catch-up card module (still used elsewhere)."""

    def test_node_harness(self) -> None:
        """Grouping, grey rows, newest run, copy, and preview pass in node."""
        node = LMS_DIR / "static" / "catchup_links_card.test.mjs"
        result = subprocess.run(
            ["node", str(node)],
            cwd=str(node.parent),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("ok", result.stdout)

    def test_lesson_slides_script_targets_table_api(self) -> None:
        """Lesson Slides loads the MCK-229 table script and API."""
        script = (LMS_DIR / "static" / "staff_lesson_slides.js").read_text(encoding="utf-8")
        self.assertIn("/lesson-slides/table", script)
        self.assertIn("ls-run-table", script)
        self.assertNotIn("catchup_links_card.js", script)


class CatchupCardHttpTests(unittest.TestCase):
    """Lesson Slides staff tab and catch-up link API."""

    def setUp(self) -> None:
        """Isolated sqlite and a signed-in teacher who owns one class."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M",
        )
        self.class_id = int(self.school.game.create_class(
            year="2026/27", semester="Semester 1", course_code="MCF3M", days_preset="M/W/F",
            time_label="2:00pm", codenames=["Maple"], offering_id=int(offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )["id"])

    def tearDown(self) -> None:
        """Close the temp database."""
        self.school.close()
        self.tmp.cleanup()

    def _sign_in(self) -> None:
        """Finish the staff Google callback and email code."""
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        code = self.school.get_user_by_email("teacher@gmail.com")["verification_code"]
        self.client.post("/verify-email", data={"code": code})

    def _insert_link(
        self, *, token: str, run_key: str, module: str, slot: str, is_open: bool, created_at: str,
    ) -> None:
        """Insert one catch-up link row for the card payload."""
        self.school.conn.execute(
            """
            INSERT INTO catchup_links (
                token, class_id, run_key, live_module, live_slot, is_open, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (token, self.class_id, run_key, module, slot, 1 if is_open else 0, created_at, created_at),
        )
        self.school.conn.execute(
            """
            INSERT INTO participation_runs (
                class_id, run_key, is_real, auto_is_real, discarded, ended_at
            ) VALUES (?, ?, 1, 1, 0, ?)
            """,
            (self.class_id, run_key, created_at),
        )
        self.school.conn.commit()

    def test_lesson_slides_page_mounts_table_for_teacher(self) -> None:
        """The staff Lesson Slides tab includes the run table mount."""
        anon = self.client.get(f"/staff/class/{self.class_id}?tab=lesson-slides")
        self.assertIn(anon.status_code, (302, 401, 403))
        self._sign_in()
        page = self.client.get(f"/staff/class/{self.class_id}?tab=lesson-slides")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn('id="ls-run-table"', html)
        self.assertIn("/static/staff_lesson_slides.js", html)

    def test_payload_fields_and_open_post(self) -> None:
        """GET returns the card fields; POST {open} closes the link."""
        self._insert_link(
            token="cardTok", run_key="session:9:2026-10-13T09:00:00", module="M1", slot="C4",
            is_open=True, created_at="2026-10-13T09:00:00",
        )
        self._sign_in()
        res = self.client.get(f"/api/classes/{self.class_id}/catchup-links")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("ok"))
        links = data.get("catchup_links") or []
        self.assertEqual(len(links), 1)
        self.assertIn("url", links[0])
        closed = self.client.post(
            f"/api/classes/{self.class_id}/catchup-links/cardTok",
            json={"open": False},
        )
        self.assertEqual(closed.status_code, 200)


if __name__ == "__main__":
    unittest.main()

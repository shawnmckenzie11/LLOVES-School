#!/usr/bin/env python3
"""MCK-203: Catch-up links card on the teacher Lesson Slides page."""

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
    """Node harness plus the staff page shell the card mounts on."""

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

    def test_staff_shell_hides_the_card_below_desktop(self) -> None:
        """The card uses staff styles and is hidden under desktop width."""
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn("body.staff-shell .catchup-links-card", css)
        self.assertIn("max-width: 959px", css)
        self.assertIn(".catchup-badge.is-muted", css)
        self.assertIn(".catchup-slot.is-pending", css)
        self.assertIn("font-family: inherit", css)
        script = (LMS_DIR / "static" / "staff_lesson_slides.js").read_text(encoding="utf-8")
        self.assertIn('from "./catchup_links_card.js"', script)
        self.assertIn("CATCHUP_LOAD_ERROR", script)
        self.assertIn("/catchup-links", script)
        card = (LMS_DIR / "static" / "catchup_links_card.js").read_text(encoding="utf-8")
        self.assertIn("CATCHUP_LOAD_ERROR", card)
        self.assertNotIn('createElement("iframe")', card)
        self.assertNotIn("createElement(\"embed\")", card)


class CatchupCardHttpTests(unittest.TestCase):
    """Lesson Slides is staff-only and reads the catch-up link payload."""

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
        self.school.conn.commit()

    def test_lesson_slides_page_mounts_the_card_for_the_teacher(self) -> None:
        """The staff Lesson Slides tab includes the module mount and the script."""
        anon = self.client.get(f"/staff/class/{self.class_id}?tab=lesson-slides")
        self.assertIn(anon.status_code, (302, 401, 403))
        self._sign_in()
        page = self.client.get(f"/staff/class/{self.class_id}?tab=lesson-slides")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn('id="ls-module-sections"', html)
        self.assertIn("/static/staff_lesson_slides.js", html)
        self.assertIn('type="module"', html)
        self.assertNotIn("<iframe", html.lower())

    def test_payload_fields_and_open_post(self) -> None:
        """GET returns the card fields; POST {open} closes the link."""
        self._insert_link(
            token="cardTok", run_key="session:9:2026-10-13T09:00:00", module="M1", slot="C4",
            is_open=True, created_at="2026-10-13T09:00:00",
        )
        self._sign_in()
        body = self.client.get(f"/api/classes/{self.class_id}/catchup-links").get_json()
        link = body["catchup_links"][0]
        for key in ("token", "url", "live_module", "live_slot", "is_open", "created_at", "run_key"):
            self.assertIn(key, link)
        self.assertTrue(link["url"].endswith("/catchup/cardTok"))
        self.assertTrue(link["is_open"])
        closed = self.client.post(
            f"/api/classes/{self.class_id}/catchup-links/cardTok", json={"open": False},
        )
        self.assertEqual(closed.status_code, 200)
        self.assertFalse(closed.get_json()["open"])
        again = self.client.get(f"/api/classes/{self.class_id}/catchup-links").get_json()
        self.assertFalse(again["catchup_links"][0]["is_open"])


if __name__ == "__main__":
    unittest.main()

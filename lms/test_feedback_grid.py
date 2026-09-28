#!/usr/bin/env python3
"""Staff Feedback tab: live-class columns and Clear (same pattern as Attendance)."""

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

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402


class FeedbackGridTests(unittest.TestCase):
    """How-was-class sheet can drop one live-class column."""

    def setUp(self) -> None:
        """Isolated app with one assigned teacher and rostered class."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.client.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Aspen", "Birch"],
            },
        )
        self.class_id = created.get_json()["class"]["id"]

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _insert_feedback(
        self,
        *,
        token: str,
        module: str = "M1",
        slot: str = "C1",
        mood: str = "good",
        before: str = "ok",
        student_name: str = "Aspen",
        comment: str = "note",
    ) -> None:
        """Write one submitted How-was-class row."""
        roster = self.school.game.dashboard(self.class_id, sort="az")["students"]
        sid = next(
            int(row["id"]) for row in roster if row["codename"] == student_name
        )
        with self.school._lock:
            self.school.conn.execute(
                """
                INSERT INTO live_class_feedback (
                    class_id, student_id, participant_uuid, codename,
                    meeting_date, token, mood, before_mood, live_module,
                    live_slot, comment, submitted_at, created_at
                ) VALUES (?, ?, '', ?, '2026-09-09', ?, ?, ?, ?, ?,
                          ?, '2026-09-09T12:00:00', '2026-09-09T12:00:00')
                """,
                (
                    int(self.class_id),
                    sid,
                    student_name,
                    token,
                    mood,
                    before,
                    module,
                    slot,
                    comment,
                ),
            )
            self.school.conn.commit()

    def test_feedback_tab_has_clear_control(self) -> None:
        """Feedback toolbar matches Attendance Clear mode wiring."""
        page = self.client.get(
            f"/staff/class/{self.class_id}?tab=ap&view=feedback"
        )
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn('id="fb-clear"', html)
        self.assertIn(">Clear<", html)
        self.assertIn('id="fb-clear-hint"', html)
        self.assertIn("staff_feedback.js", html)
        self.assertIn('id="feedback-grid-data"', html)
        js = (LMS_DIR / "static" / "staff_feedback.js").read_text(encoding="utf-8")
        self.assertIn("feedback-live/clear", js)
        self.assertIn("data-clear-key", js)
        self.assertNotIn("moodGlyph", js)
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn("#fb-clear.on", css)
        self.assertNotIn("mood-face", js)
        self.assertNotIn('colspan="4"', js)

    def test_summary_cell_is_one_column_per_live_key(self) -> None:
        """Feedback ledger is one text summary per live key, with a detail sheet."""
        self._insert_feedback(token="tok-c1", slot="C1", mood="good", before="ok")
        self._insert_feedback(token="tok-c2", slot="C2", mood="low", before="good")
        self._insert_feedback(
            token="tok-birch",
            slot="C1",
            mood="ok",
            before="ok",
            student_name="Birch",
            comment="",
        )
        page = self.client.get(
            f"/staff/class/{self.class_id}?tab=ap&view=feedback"
        )
        html = page.get_data(as_text=True)
        self.assertNotIn("mood-face", html)
        self.assertNotIn('colspan="4"', html)
        self.assertNotIn("<th>Before</th>", html)
        self.assertNotIn("<th>Note</th>", html)
        self.assertIn('data-live-key="M1C1"', html)
        self.assertIn('data-live-key="M1C2"', html)
        self.assertIn("ok → good · +1", html)
        self.assertIn("good → low · −2", html)
        self.assertIn("ok → ok · 0", html)
        self.assertNotIn("ok → ok · 0 ·", html)
        self.assertIn(">note<", html)
        self.assertIn('aria-label="Aspen, M1C1, ok → good · +1, note"', html)
        self.assertIn('aria-label="Birch, M1C1, ok → ok · 0"', html)
        self.assertIn('id="feedback-comment-dialog"', html)
        self.assertIn('id="feedback-detail-before"', html)
        self.assertIn('id="feedback-detail-comment"', html)
        self.assertIn(">Before<", html)
        self.assertIn(">After<", html)
        self.assertIn(">Score<", html)
        self.assertIn(">Comment<", html)
        hint = html.split('class="hint">', 1)[1].split("</p>", 1)[0]
        self.assertNotIn("icon", hint.lower())
        self.assertIn("−1", hint)
        self.assertIn("end-live-options", html)
        self.assertIn('name="save_attendance"', html)
        self.assertIn('name="save_participation"', html)
        self.assertIn('name="end_options"', html)
        home = (LMS_DIR / "templates" / "staff" / "home.html").read_text(
            encoding="utf-8"
        )
        course = (LMS_DIR / "templates" / "staff" / "course.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('class="end-live-options"', home)
        self.assertIn('class="end-live-options"', course)
        self.assertIn('class="end-live-option"', home)
        self.assertIn('class="end-live-option"', course)
        self.assertIn('name="save_attendance" value="1" checked', home)
        self.assertIn('name="save_participation" value="1" checked', course)
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn("grid-template-columns: 20px minmax(0, 1fr)", css)
        self.assertIn("min-height: 44px", css)
        self.assertIn("gap: 10px", css)
        self.assertIn("margin-top: 18px", css)
        self.assertIn("justify-content: space-between", css)

    def test_clear_one_live_class_leaves_the_other(self) -> None:
        """Clear M1C1 drops that column and keeps M1C2."""
        self._insert_feedback(token="tok-c1", slot="C1")
        self._insert_feedback(token="tok-c2", slot="C2", mood="low", before="good")
        before = self.school.feedback_grid_for_class(self.class_id)
        keys = {col["key"] for col in before["columns"]}
        self.assertEqual(keys, {"M1C1", "M1C2"})
        cleared = self.client.post(
            f"/api/classes/{self.class_id}/feedback-live/clear",
            json={"key": "M1C1"},
        )
        self.assertEqual(cleared.status_code, 200, cleared.get_json())
        body = cleared.get_json()
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("cleared_key"), "M1C1")
        self.assertGreaterEqual(int(body.get("deleted") or 0), 1)
        leftover = {col["key"] for col in body.get("columns") or []}
        self.assertEqual(leftover, {"M1C2"})
        page = self.client.get(
            f"/staff/class/{self.class_id}?tab=ap&view=feedback"
        )
        html = page.get_data(as_text=True)
        self.assertNotIn('data-live-key="M1C1"', html)
        self.assertIn('data-live-key="M1C2"', html)

    def test_clear_rejects_bad_key(self) -> None:
        """Malformed live-class keys do not wipe the table."""
        self._insert_feedback(token="tok-keep")
        bad = self.client.post(
            f"/api/classes/{self.class_id}/feedback-live/clear",
            json={"key": "live-1"},
        )
        self.assertEqual(bad.status_code, 400)
        grid = self.school.feedback_grid_for_class(self.class_id)
        self.assertEqual([col["key"] for col in grid["columns"]], ["M1C1"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

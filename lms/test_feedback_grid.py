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

    def _student_id(self) -> int:
        """Return Aspen's roster id."""
        roster = self.school.game.dashboard(self.class_id, sort="az")["students"]
        return int(roster[0]["id"])

    def _insert_feedback(
        self,
        *,
        token: str,
        module: str = "M1",
        slot: str = "C1",
        mood: str = "good",
        before: str = "ok",
    ) -> None:
        """Write one submitted How-was-class row."""
        sid = self._student_id()
        with self.school._lock:
            self.school.conn.execute(
                """
                INSERT INTO live_class_feedback (
                    class_id, student_id, participant_uuid, codename,
                    meeting_date, token, mood, before_mood, live_module,
                    live_slot, comment, submitted_at, created_at
                ) VALUES (?, ?, '', 'Aspen', '2026-09-09', ?, ?, ?, ?, ?,
                          'note', '2026-09-09T12:00:00', '2026-09-09T12:00:00')
                """,
                (int(self.class_id), sid, token, mood, before, module, slot),
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

#!/usr/bin/env python3
"""MCK-59/60/61: engagement summary, rubric, and teacher overrides."""

from __future__ import annotations

import json
import logging
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
import engagement_rubric as R  # noqa: E402
import participation as P  # noqa: E402
from engagement_events import clean_payload  # noqa: E402
from engagement_summary import build_summary, suggest_student  # noqa: E402


class RubricTests(unittest.TestCase):
    def test_draft_level_label(self) -> None:
        cfg = R.load_rubric()
        self.assertEqual(cfg.get("level_label"), "draft level")
        self.assertIn("Engagement L3", R.format_engagement_level(3, cfg))
        self.assertIn("draft level", R.format_engagement_level(3, cfg))

    def test_engagement_bands_differ_from_participation(self) -> None:
        """MCK-198 participation uses 80/70/60/50; engagement rubric is separate."""
        cfg = R.load_rubric()
        floors = {lvl: floor for lvl, floor in cfg["_levels_sorted"]}
        self.assertNotEqual(floors.get(4), 0.80)
        self.assertEqual(P.level_for(P.percent(8, 10)), 4)
        self.assertEqual(R.level_for_score(0.81, cfg), 3)

    def test_evidence_floor_caps_level(self) -> None:
        self.assertEqual(R.apply_evidence_floor(4, 1, R.load_rubric()), 1)


class SummaryTests(unittest.TestCase):
    def test_reason_and_evidence(self) -> None:
        rows = [
            {
                "scope": "student",
                "student_id": 1,
                "signal": "individual_answer",
                "count": 2,
                "offered": 3,
                "share_of_group": None,
            }
        ]
        out = suggest_student(rows, 1)
        self.assertIn("2/3 individual", out["reason"])
        self.assertEqual(out["evidence"][0]["signal"], "individual_answer")


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.client = self.app.test_client()
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.teacher_a = self.school.register_staff("a@gmail.com")
        self.teacher_b = self.school.register_staff("b@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher_a["id"]), ontario_code="MCR3U"
        )
        self.class_id = int(
            self.school.game.create_class(
                year="2026/27",
                semester="Semester 1",
                course_code="MCR3U",
                days_preset="M/W/F",
                time_label="2:00pm",
                codenames=["Ava", "Ben"],
                offering_id=int(offering["id"]),
                teacher_user_id=int(self.teacher_a["id"]),
            )["id"]
        )
        self.students = [
            int(r[0])
            for r in self.school.game.conn.execute(
                "SELECT id FROM students WHERE class_id = ? ORDER BY id", (self.class_id,)
            ).fetchall()
        ]
        live = self.school.start_live_class_session(self.class_id, int(self.teacher_a["id"]))
        self.session_id = int(live["id"])
        self.run_key = str(live["run_key"])
        ava = self.students[0]
        self.school.engagement.append(
            {
                "class_id": self.class_id,
                "live_session": self.session_id,
                "run_key": self.run_key,
                "student_id": ava,
                "first_name": "",
                "team_id": None,
                "item_ref": "1",
                "kind": "item.offered",
                "ts": "2026-10-01T10:01:00",
                "payload": clean_payload({"mode": "individual"}),
            }
        )
        self.school.engagement.append(
            {
                "class_id": self.class_id,
                "live_session": self.session_id,
                "run_key": self.run_key,
                "student_id": ava,
                "first_name": "",
                "team_id": None,
                "item_ref": "1",
                "kind": "answer.submit",
                "ts": "2026-10-01T10:02:00",
                "payload": clean_payload({}),
            }
        )
        self.school.cleanup_live_session_response_data(self.session_id)

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)
        self.tmp.cleanup()

    def _login(self, email: str) -> None:
        self.client.get("/auth/google?portal=staff")
        self.client.get(f"/auth/google/callback?email={email}&name=T")
        user = self.school.get_user_by_email(email)
        code = user.get("verification_code") if user else None
        if code:
            self.client.post("/verify-email", data={"code": code})

    def test_staff_summary_and_override(self) -> None:
        self._login("a@gmail.com")
        rv = self.client.get(f"/api/classes/{self.class_id}/engagement-summary")
        self.assertEqual(rv.status_code, 200)
        body = rv.get_json()
        self.assertTrue(body["ok"])
        summary = body["summary"]
        self.assertEqual(summary["run_key"], self.run_key)
        self.assertIn("blind_spots", summary)
        self.assertEqual(summary["rubric"]["level_label"], "draft level")
        student = (summary.get("ungrouped") or summary["groups"][0]["students"])[0]
        sid = int(student["student_id"])
        put = self.client.put(
            f"/api/classes/{self.class_id}/engagement-summary/{self.run_key}/students/{sid}",
            json={"level": 4, "note": "Strong verbal share"},
        )
        self.assertEqual(put.status_code, 200)
        updated = put.get_json()["summary"]
        again = next(
            s
            for s in (updated.get("ungrouped") or updated["groups"][0]["students"])
            if int(s["student_id"]) == sid
        )
        self.assertEqual(again["override"]["level"], 4)
        self.assertIn("Engagement L4", again["effective_label"])

    def test_other_teacher_denied(self) -> None:
        self._login("b@gmail.com")
        rv = self.client.get(f"/api/classes/{self.class_id}/engagement-summary")
        self.assertEqual(rv.status_code, 403)

    def test_engagement_tab_on_staff_course(self) -> None:
        self._login("a@gmail.com")
        html = self.client.get(
            f"/staff/class/{self.class_id}?tab=ap&view=engagement"
        ).get_data(as_text=True)
        self.assertIn("engagement-summary-root", html)
        self.assertIn("End-of-class engagement", html)
        self.assertNotIn("student_engagement", html.lower())


if __name__ == "__main__":
    unittest.main()

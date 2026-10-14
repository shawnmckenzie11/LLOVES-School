#!/usr/bin/env python3
"""MCK-215: student live-class card shows Tier-1 engagement and gated team score."""

from __future__ import annotations

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
from engagement_events import clean_payload, now_iso  # noqa: E402
from engagement_live import (  # noqa: E402
    group_question_started,
    tier1_engagement_display,
)
from engagement_rollup import RollupRow  # noqa: E402


class LiveEngagementHelperTests(unittest.TestCase):
    def test_tier1_display_formats_count_offered(self) -> None:
        rows = [
            RollupRow(
                scope="student",
                signal="individual_answer",
                count=2,
                offered=3,
                student_id=7,
            )
        ]
        self.assertEqual(tier1_engagement_display(rows, 7), "2/3")
        self.assertEqual(tier1_engagement_display(rows, 8), "0/0")

    def test_group_question_started_from_offers(self) -> None:
        events = [
            {"kind": "item.offered", "payload": {"mode": "individual"}},
            {"kind": "item.offered", "payload": {"mode": "group"}},
        ]
        self.assertFalse(group_question_started(events[:1]))
        self.assertTrue(group_question_started(events))


class StudentLiveCardTests(unittest.TestCase):
    def setUp(self) -> None:
        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.client = self.app.test_client()
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCR3U"
        )
        self.class_id = int(
            self.school.game.create_class(
                year="2026/27",
                semester="Semester 1",
                course_code="MCR3U",
                days_preset="M/W/F",
                time_label="2:00pm",
                codenames=["Ava"],
                offering_id=int(offering["id"]),
                teacher_user_id=int(teacher["id"]),
            )["id"]
        )
        self.student_id = int(
            self.school.game.conn.execute(
                "SELECT id FROM students WHERE class_id = ? LIMIT 1", (self.class_id,)
            ).fetchone()[0]
        )
        live = self.school.start_live_class_session(self.class_id, int(teacher["id"]))
        self.session_id = int(live["id"])
        self.run_key = str(live["run_key"])
        self.school.join_live_class_session(
            self.session_id, self.student_id, codename="Ava"
        )
        offering_id = int(offering["id"])
        with self.client.session_transaction() as sess:
            sess["student_offering_id"] = offering_id
            sess["student_class_id"] = self.class_id
            sess["student_id"] = self.student_id
            sess["student_live_session_id"] = self.session_id
            sess["student_participant_uuid"] = "mck215-test"

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)
        self.tmp.cleanup()

    def _append(self, kind: str, *, item_ref: str = "", mode: str = "") -> None:
        payload = clean_payload({"mode": mode}) if mode else clean_payload({})
        self.school.engagement.append(
            {
                "class_id": self.class_id,
                "live_session": self.session_id,
                "run_key": self.run_key,
                "student_id": self.student_id if kind != "item.offered" else None,
                "first_name": "",
                "team_id": None,
                "item_ref": item_ref,
                "kind": kind,
                "ts": now_iso(),
                "payload": payload,
            }
        )

    def test_home_template_engagement_label(self) -> None:
        html = (LMS_DIR / "templates" / "student" / "home.html").read_text(encoding="utf-8")
        self.assertIn("Engagement", html)
        self.assertIn('id="me-engagement"', html)
        self.assertNotIn("My points", html)
        self.assertIn('id="me-team-stat" hidden', html)

    def test_state_tier1_updates_and_team_hidden_until_group(self) -> None:
        self.school.record_engagement_event(
            "present.join", session_id=self.session_id, student_id=self.student_id
        )
        self._append("item.offered", item_ref="q1", mode="individual")
        self._append("item.offered", item_ref="q2", mode="individual")
        self._append("answer.submit", item_ref="q1")
        state = self.client.get("/api/student/state").get_json()
        me = state.get("me") or {}
        self.assertEqual(me.get("engagement"), "1/2")
        self.assertFalse(me.get("show_team_score"))

        self._append("item.offered", item_ref="g1", mode="group")
        state2 = self.client.get("/api/student/state").get_json()
        me2 = state2.get("me") or {}
        self.assertEqual(me2.get("engagement"), "1/2")
        self.assertTrue(me2.get("show_team_score"))

    def test_student_portal_paints_engagement(self) -> None:
        js = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn('getElementById("me-engagement")', js)
        self.assertIn("me.show_team_score", js)
        self.assertIn("meTeamStatEl.hidden", js)


if __name__ == "__main__":
    unittest.main()

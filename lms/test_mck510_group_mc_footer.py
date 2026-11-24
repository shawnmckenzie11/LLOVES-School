#!/usr/bin/env python3
"""MCK-510: group MC shares the individual Close / Minimize footer."""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from live_news_wire import NewsAudience, event_visible, log_for, reset_logs  # noqa: E402


class GroupMcFooterTests(unittest.TestCase):
    """Teacher strip and student minimize for group multiple choice."""

    def setUp(self) -> None:
        """Two teams, one group MC question."""

        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("teacher-mck510@example.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCR3U"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher-mck510@example.com&name=T")
        code = self.school.get_user_by_email("teacher-mck510@example.com")["verification_code"]
        self.client.post("/verify-email", data={"code": code})
        self.class_id = int(
            self.school.game.create_class(
                year="2026/27",
                semester="Semester 1",
                course_code="MCR3U",
                days_preset="M/W/F",
                time_label="2:00pm",
                codenames=["A1", "B1", "A2", "B2"],
                offering_id=int(offering["id"]),
                teacher_user_id=int(teacher["id"]),
            )["id"]
        )
        self.session_id = int(
            self.school.start_live_class_session(self.class_id, int(teacher["id"]))["id"]
        )
        self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"live_module": "M1", "live_slot": "C2", "stage": "round", "page_id": "round_1"},
        )
        self.school.game.begin_game(self.class_id)
        with self.school.game._lock:
            rows = self.school.game.conn.execute(
                "SELECT id FROM students WHERE class_id = ? ORDER BY id",
                (self.class_id,),
            ).fetchall()
        self.student_ids = [int(row["id"]) for row in rows]
        for sid in self.student_ids:
            self.school.join_live_class_session(self.session_id, sid)
        self.school.setup_live_session_groups(
            self.session_id,
            n_teams=2,
            mode="manual",
            present_ids=self.student_ids,
            assignments=[
                {"student_id": sid, "team_index": index % 2}
                for index, sid in enumerate(self.student_ids)
            ],
        )
        self.school.ensure_live_session_items(self.session_id)

    def tearDown(self) -> None:
        """Drop the temp database and news tape."""

        logging.disable(logging.NOTSET)
        reset_logs()
        self.school.close()
        self.tmp.cleanup()

    def _add_publish(self, body: dict[str, Any], mode: str) -> dict[str, Any]:
        """Add a question and publish in the requested mode."""

        body = {"page_number": 4, "stage": "round", **body}
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/add-question", json=body
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        item_id = rv.get_json()["placement"]["item"]["id"]
        row = next(
            record
            for record in self.school.list_live_session_items(self.session_id)
            if record["item_id"] == item_id
        )
        rv = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{row['id']}/publish",
            json={"publish_mode": mode},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        return rv.get_json()["item"]

    def _group_mc(self) -> dict[str, Any]:
        """One published group multiple-choice row."""

        return self._add_publish(
            {
                "type": "mc",
                "text": "Slope?",
                "options": ["2", "1", "-2", "0"],
                "correct_index": 0,
            },
            "group_submit",
        )

    def test_staff_paint_uses_close_not_reveal_for_group_mc(self) -> None:
        """Group MC wires Close/Minimize like individual; group rank still Reveals."""

        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("function liveGroupMcUsesIndividualFooter(", js)
        paint = js.split("function paintLiveQuestionCards()")[1].split(
            "function individualLifecycleResultsHtml("
        )[0]
        self.assertIn("liveGroupMcUsesIndividualFooter(item, card)", paint)
        self.assertIn("const groupMcFooter = groupChrome && liveGroupMcUsesIndividualFooter", paint)
        self.assertIn("const mcCloseMinimizeFooter = !groupChrome || groupMcFooter", paint)
        self.assertIn("active && groupChrome && !groupMcFooter", paint)
        self.assertIn("closed && liveItemId && mcCloseMinimizeFooter", paint)
        self.assertIn("active && liveItemId && mcCloseMinimizeFooter", paint)
        close = paint.split("const closeHtml =")[1].split("const resultGraph")[0]
        self.assertIn(">Close</button>", close)
        self.assertNotIn(">Reveal</button>", close)
        reveal = paint.split("const revealHtml =")[1].split("const minimizeHtml")[0]
        self.assertIn(">Reveal</button>", reveal)

    def test_student_minimize_label_matches_individual_mc(self) -> None:
        """Closed group MC cards keep the student × with aria-label Minimize."""

        portal = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        card = portal.split("const minimizeControl =")[1].split("const layout =")[0]
        self.assertIn('aria-label="Minimize"', card)
        self.assertIn('status === "closed"', card)
        self.assertNotIn("groupSubmit", card)

    def test_group_mc_close_and_minimize_match_individual_lifecycle(self) -> None:
        """Close and Minimize settings work on group MC like individual MC."""

        published = self._group_mc()
        live_id = int(published["id"])
        team = 0
        choice = "2"
        for index, sid in enumerate(self.student_ids):
            if index % 2 != team:
                continue
            self.school.submit_group_mc_pick(self.session_id, live_id, sid, choice=choice)
        self.school.submit_group_mc_answer(
            self.session_id, live_id, self.student_ids[0], choice=choice, why="same"
        )
        closed = self.school.close_live_session_item(self.session_id, live_id)
        self.assertEqual(str(closed.get("status")), "closed")

        cursor = log_for(self.school.data_dir).since(self.session_id, 0)
        tip = int(cursor[-1]["id"]) if cursor else 0
        response = self.client.patch(
            f"/api/live-sessions/{self.session_id}/items/{live_id}/settings",
            json={"students_minimized": True},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:200])
        fresh = [
            row
            for row in log_for(self.school.data_dir).since(self.session_id, tip)
            if row.get("type") == "results_view"
        ]
        self.assertEqual(len(fresh), 1)
        event = fresh[0]
        self.assertEqual(event.get("minimized"), 1)
        self.assertNotIn("student_id", event)
        self.assertTrue(
            event_visible(event, NewsAudience("student", student_id=self.student_ids[0]))
        )
        stored = self.school.get_live_session_item(self.session_id, live_id)
        self.assertTrue(stored.get("students_minimized"))
        self.assertEqual(str(stored.get("status")), "closed")
        self.assertNotIn("student_id", json.dumps(stored))


if __name__ == "__main__":
    unittest.main()
#!/usr/bin/env python3
"""MCK-493: student check, teacher Minimize, one card after Add New.

Fixtures only. Assertions do not print names, emails, or join codes.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
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
from live_news_wire import NewsAudience, event_visible, log_for, reset_logs  # noqa: E402

STAFF = "staff-mck493@example.com"
SEAT = "S1"


class ResultsTweakTests(unittest.TestCase):
    """Checkmark source, Minimize postcard, and one published numeric item."""

    def setUp(self) -> None:
        """One live class and a signed-in teacher."""

        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff(STAFF)
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCR3U"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get(f"/auth/google/callback?email={STAFF}&name=T")
        code = self.school.get_user_by_email(STAFF)["verification_code"]
        self.client.post("/verify-email", data={"code": code})
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": int(offering["id"]),
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": [SEAT],
            },
        )
        self.class_id = int(created.get_json()["class"]["id"])
        live = self.school.start_live_class_session(self.class_id, int(teacher["id"]))
        self.session_id = int(live["id"])
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M1",
            live_slot="C2",
            stage="round",
            page_number=4,
        )
        self.school.ensure_live_session_items(self.session_id)
        student = self.school.game.find_student_by_codename(self.class_id, SEAT)
        self.assertIsNotNone(student)
        self.student_id = int(student["id"])
        self.school.join_live_class_session(
            self.session_id, self.student_id, codename=SEAT
        )

    def tearDown(self) -> None:
        """Close sqlite and drop the temp news tape."""

        logging.disable(logging.NOTSET)
        reset_logs()
        self.school.close()
        self.tmp.cleanup()

    def _numeric(self) -> dict:
        """Add and publish one numeric question. Return the lifecycle row."""

        placed = self.school.add_staff_question_to_class_playlist(
            self.class_id,
            "M1",
            "C2",
            question_type="numeric",
            text="How many roots?",
            page_number=4,
            stage="round",
            correct_answer="2",
            tolerance=0,
            tolerance_kind="absolute",
        )
        token = str(placed.get("item_id") or "")
        row = next(
            item
            for item in self.school.list_live_session_items(self.session_id)
            if str(item.get("item_id") or "") == token
        )
        return self.school.publish_live_session_item(
            self.session_id, int(row["id"]), publish_mode="individual"
        )

    def _copies(self, item_id: str) -> list[dict]:
        """Lifecycle rows whose item id is this Add New question."""

        return [
            row
            for row in self.school.list_live_session_items(self.session_id)
            if str(row.get("item_id") or "") == item_id
        ]

    def test_one_publish_writes_one_item(self) -> None:
        """Add New plus Publish stores one lifecycle row and one student card."""

        published = self._numeric()
        item_id = str(published.get("item_id") or "")
        copies = self._copies(item_id)
        self.assertEqual(len(copies), 1)
        cards = [
            card
            for card in self.school.live_session_question_cards(self.session_id)
            if str(card.get("id") or "") == item_id
        ]
        self.assertEqual(len(cards), 1)
        self.assertEqual(int(cards[0]["live_item_id"]), int(published["id"]))
        payload = self.school.student_live_items_payload(self.session_id, self.student_id)
        seen = [
            row
            for row in (payload.get("active_questions") or [])
            if str(row.get("item_id") or "") == item_id
        ]
        self.assertEqual(len(seen), 1)
        prompt = payload.get("prompt") or {}
        prompt_body = prompt.get("payload") if isinstance(prompt.get("payload"), dict) else {}
        self.assertNotEqual(str(prompt_body.get("item_id") or ""), item_id)

    def test_show_answer_stays_off_the_student_payload(self) -> None:
        """The correct value stays out until Show answer is on."""

        published = self._numeric()
        prompt = self.school._prompt_for_live_item(published)
        self.assertIsNotNone(prompt)
        self.school.submit_live_prompt_response(
            int(prompt["id"]), self.student_id, {"value": 4}
        )
        hidden = self.school.student_live_items_payload(self.session_id, self.student_id)
        blob = json.dumps(
            next(
                row
                for row in hidden.get("active_questions") or []
                if int(row.get("id") or 0) == int(published["id"])
            )
        )
        self.assertNotIn("correct_value", blob)
        self.assertNotIn("correct_answer", blob)
        self.school.update_live_session_item_settings(
            self.session_id, int(published["id"]), show_answer=True
        )
        shown = self.school.student_live_items_payload(self.session_id, self.student_id)
        row = next(
            item
            for item in shown.get("active_questions") or []
            if int(item.get("id") or 0) == int(published["id"])
        )
        self.assertEqual(str((row.get("results") or {}).get("correct_value")), "2")

    def test_minimize_is_one_class_flag_and_keeps_responses(self) -> None:
        """Minimize rides results_view, stores nothing per student, and keeps answers."""

        published = self._numeric()
        prompt = self.school._prompt_for_live_item(published)
        self.assertIsNotNone(prompt)
        self.school.submit_live_prompt_response(
            int(prompt["id"]), self.student_id, {"value": 4}
        )
        before = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM live_session_responses"
        ).fetchone()["n"]
        cursor = log_for(self.school.data_dir).since(self.session_id, 0)
        tip = int(cursor[-1]["id"]) if cursor else 0
        response = self.client.patch(
            f"/api/live-sessions/{self.session_id}/items/{int(published['id'])}/settings",
            json={"students_minimized": True},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:180])
        fresh = [
            row
            for row in log_for(self.school.data_dir).since(self.session_id, tip)
            if row.get("type") == "results_view"
        ]
        self.assertEqual(len(fresh), 1)
        event = fresh[0]
        self.assertEqual(event.get("minimized"), 1)
        self.assertNotIn("student_id", event)
        self.assertNotIn("answer", event)
        self.assertTrue(
            event_visible(event, NewsAudience("student", student_id=self.student_id))
        )
        after = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM live_session_responses"
        ).fetchone()["n"]
        self.assertEqual(int(before), int(after))
        stored = self.school.conn.execute(
            "SELECT response_json FROM live_session_responses WHERE prompt_id = ?",
            (int(prompt["id"]),),
        ).fetchone()
        self.assertIn("4", str(stored["response_json"]))
        student = self.school.student_live_items_payload(self.session_id, self.student_id)
        row = next(
            item
            for item in student.get("active_questions") or []
            if int(item.get("id") or 0) == int(published["id"])
        )
        self.assertTrue(row.get("students_minimized"))
        self.assertEqual(str(row.get("status")), "active")
        again = self.client.patch(
            f"/api/live-sessions/{self.session_id}/items/{int(published['id'])}/settings",
            json={"students_minimized": False},
        )
        self.assertEqual(again.status_code, 200)
        restored = self.school.get_live_session_item(self.session_id, int(published["id"]))
        self.assertFalse(restored.get("students_minimized"))

    def test_student_check_and_one_shell_are_in_the_page(self) -> None:
        """The phone graph uses a check, and the prompt shell yields to the card."""

        portal = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        graph = portal.split("function lifecycleResultsHtml(")[1].split(
            "function lifecycleClassConsensusHtml("
        )[0]
        self.assertIn('keyMark: "check"', graph)
        self.assertIn("controls: false", graph)
        paint = portal.split("function paintPrompt(")[1].split("function renderPromptBody(")[0]
        self.assertLess(paint.index("cardOwnsLivePrompt"), paint.index("renderPromptBody"))
        self.assertIn("promptShell.innerHTML = \"\"", paint)
        staff = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn('aria-label="${label}"', staff)
        self.assertIn("Minimize for students", staff)
        self.assertIn("Restore for students", staff)
        self.assertLess(
            staff.index("studentMinimizeButtonHtml"),
            staff.index('data-close-live-item="${liveItemId}"'),
        )
        node = subprocess.run(
            ["node", "lms/static/mc_results_graph.test.mjs"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(node.returncode, 0, node.stderr or node.stdout)
        once = subprocess.run(
            ["node", "lms/static/live_prompt_once.test.mjs"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(once.returncode, 0, once.stderr or once.stdout)


if __name__ == "__main__":
    unittest.main()

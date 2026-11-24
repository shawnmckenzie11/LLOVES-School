#!/usr/bin/env python3
"""MCK-512: Split Poll and Show Your Work in the question editor."""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from participation_weight import item_counts_for_participation, item_participation_weight  # noqa: E402


class EditorPickerMarkupTests(unittest.TestCase):
    """The shared editor exposes Poll and Show Your Work, not Answer with."""

    def test_picker_offers_poll_and_show_your_work_only(self) -> None:
        """Type chips are Poll, Show Your Work, and no legacy Answer with row."""
        html = (LMS_DIR / "templates" / "partials" / "question_editor.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('name="live-add-q-type" value="poll"', html)
        self.assertIn(">Poll</span>", html)
        self.assertIn('name="live-add-q-type" value="show_your_work"', html)
        self.assertIn(">Show Your Work</span>", html)
        self.assertNotIn('name="live-add-q-answer-with"', html)
        self.assertNotIn("Typing and whiteboard", html)
        js = (LMS_DIR / "static" / "question_editor.js").read_text(encoding="utf-8")
        self.assertIn('SHOW_YOUR_WORK_PICKER = "show_your_work"', js)
        self.assertNotIn('name="live-add-q-answer-with"', js)


class LegacyBothPollTests(unittest.TestCase):
    """Legacy both polls stay both in storage and hide the typing box in the UI layer."""

    def test_both_maps_to_show_your_work_without_rewriting_on_load(self) -> None:
        """Legacy both opens as Show Your Work (typing hidden) and is preserved on save."""
        js = (LMS_DIR / "static" / "question_editor.js").read_text(encoding="utf-8")
        self.assertIn('initialAnswerWith === "both" ? "both" : "board"', js)
        ui = (LMS_DIR / "static" / "question_board_ui.js").read_text(encoding="utf-8")
        # The typing box on the phone board is always hidden, including legacy "both".
        self.assertNotIn('!== "both"', ui)
        self.assertIn("typed.hidden = true;", ui)
        self.assertIn("text.hidden = true;", ui)

    def test_participation_still_counts_board_and_both(self) -> None:
        """Participation weight keeps board and both poll opportunities."""
        board = {"type": "poll", "answer_with": "board"}
        both = {"type": "poll", "answer_with": "both"}
        self.assertTrue(item_counts_for_participation("share", board))
        self.assertTrue(item_counts_for_participation("share", both))
        self.assertEqual(
            item_participation_weight("share", publish_mode="individual", item_json=board),
            1,
        )
        self.assertEqual(
            item_participation_weight("share", publish_mode="individual", item_json=both),
            1,
        )


class BoardBackgroundReadTests(unittest.TestCase):
    """Show Your Work passes the background image through to the student board API."""

    def setUp(self) -> None:
        """One staff fixture and a test live class."""
        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite", data_dir=root, testing=True
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("fixture-staff@example.com")
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get(
            "/auth/google/callback?email=fixture-staff@example.com&name=T"
        )
        code = self.school.get_user_by_email("fixture-staff@example.com")[
            "verification_code"
        ]
        self.client.post("/verify-email", data={"code": code})
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": int(offering["id"]),
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["SeatA"],
            },
        )
        self.class_id = int(created.get_json()["class"]["id"])
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=True
        )
        self.session_id = int(live["id"])
        self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"live_module": "M1", "live_slot": "C2", "stage": "round"},
        )
        row = self.school.game.find_student_by_codename(self.class_id, "SeatA")
        self.assertIsNotNone(row)
        self.student_id = int(row["id"])
        self.school.join_live_class_session(
            self.session_id, self.student_id, codename="SeatA"
        )

    def tearDown(self) -> None:
        """Close the temp database."""
        logging.disable(logging.NOTSET)
        self.school.close()
        self.tmp.cleanup()

    def test_board_background_image_on_student_read(self) -> None:
        """A board poll with image_as_board_background serves image_url on read."""
        image = "/static/bank-graphs/parabola-grid.svg"
        body = {
            "type": "poll",
            "text": "Trace the graph",
            "page_number": 1,
            "stage": "round",
            "answer_with": "board",
            "image_url": image,
            "image_as_board_background": True,
            "save_to_bank": False,
        }
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/add-question",
            json=body,
        )
        self.assertLess(rv.status_code, 400, rv.get_json())
        self.school.ensure_live_session_items(self.session_id)
        item = None
        for row in self.school.list_live_session_items(self.session_id):
            payload = row.get("item") if isinstance(row.get("item"), dict) else {}
            if str(payload.get("text") or "") == "Trace the graph":
                item = row
                break
        self.assertIsNotNone(item)
        published = self.school.publish_live_session_item(
            self.session_id,
            int(item["id"]),
            publish_mode="individual",
        )
        prompt_id = int(published["prompt_id"])
        phone = self.school.read_student_question_board(
            self.session_id, prompt_id, self.student_id
        )
        self.assertIn(image, str(phone.get("image_url") or ""))
        dash = self.school.question_dashboard(self.session_id, prompt_id)
        self.assertIn(image, str(dash.get("image_url") or ""))


class BoardBackgroundClientTests(unittest.TestCase):
    """Student board UI applies the background image helper."""

    def test_student_renderer_wires_background_helper(self) -> None:
        """Question board mount uses applyCanvasBoardBackground on first paint and reuse."""
        js = (LMS_DIR / "static" / "question_board_ui.js").read_text(encoding="utf-8")
        self.assertIn("applyCanvasBoardBackground", js)
        self.assertIn("data.image_url", js)
        bg = (LMS_DIR / "static" / "board_background.js").read_text(encoding="utf-8")
        self.assertIn("export function applyCanvasBoardBackground", bg)
        formative = (LMS_DIR / "static" / "formative_live_board.js").read_text(encoding="utf-8")
        self.assertIn("drawFittedBoardBackground", formative)
        self.assertIn("payload.image_url", formative)


if __name__ == "__main__":
    unittest.main()

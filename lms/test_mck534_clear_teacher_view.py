#!/usr/bin/env python3
"""MCK-534: student Clear and Undo update the teacher board view.

Seeded demo data only. Assertions do not print names, emails, codes, or ids.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from question_board import body_is_presence  # noqa: E402


def _stroke_ids(view: dict) -> set[str]:
    """Stroke ids on a snapshot canvas view or a payload that contains one."""
    canvas = view.get("canvas_view") if isinstance(view.get("canvas_view"), dict) else view
    strokes = canvas.get("strokes") if isinstance(canvas, dict) else []
    return {str(row.get("id") or "") for row in strokes or [] if isinstance(row, dict)}


def _op_types(payload: dict) -> set[str]:
    """Op types on a since-seq teacher tile or panel delta."""
    ops = list(payload.get("ops") or []) + list(payload.get("ops_since") or [])
    return {str(op.get("type") or op.get("op") or "") for op in ops if isinstance(op, dict)}


class BodyIsPresenceTests(unittest.TestCase):
    """Phone Clear and Undo bodies must be classified as presence writes."""

    def test_phone_clear_and_undo_are_presence(self) -> None:
        """A board_wipe or unwipe body has no ops list, same as stroke_remove."""
        self.assertTrue(body_is_presence({"op": "board_wipe", "wipe_id": "w1", "id": "w1"}))
        self.assertTrue(
            body_is_presence({"op": "board_unwipe", "unwipe_id": "u1", "id": "u1", "wipe_id": "w1"})
        )
        self.assertTrue(body_is_presence({"op": "stroke_remove", "stroke_id": "s1"}))
        self.assertTrue(body_is_presence({"equation_id": "e1", "latex": "x^2", "x": 0.2, "y": 0.3}))
        self.assertTrue(
            body_is_presence({"points": [[0.1, 0.2], [0.3, 0.4]], "stroke_id": "s1", "ended": True})
        )
        self.assertFalse(body_is_presence({"ops": [{"type": "stroke_add", "id": "s1"}]}))


class ClearUpdatesTeacherViewTests(unittest.TestCase):
    """HTTP: a student Clear or Undo shows on the teacher dashboard and panel."""

    def setUp(self) -> None:
        """One staff fixture, two synthetic students, a test live class."""
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
        self.client.get("/auth/google/callback?email=fixture-staff@example.com&name=T")
        code = self.school.get_user_by_email("fixture-staff@example.com")["verification_code"]
        self.client.post("/verify-email", data={"code": code})
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": int(offering["id"]),
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["S1", "S2"],
            },
        )
        self.class_id = int(created.get_json()["class"]["id"])
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=True
        )
        self.session_id = int(live["id"])
        self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"live_module": "M1", "live_slot": "C2", "stage": "round", "page_number": 1},
        )
        self.school.game.begin_game(self.class_id)
        self.ids = []
        for codename in ("S1", "S2"):
            row = self.school.game.find_student_by_codename(self.class_id, codename)
            self.assertIsNotNone(row)
            self.ids.append(int(row["id"]))
            self.school.join_live_class_session(
                self.session_id, int(row["id"]), codename=codename
            )

    def tearDown(self) -> None:
        """Close the temp database."""
        logging.disable(logging.NOTSET)
        self.school.close()
        self.tmp.cleanup()

    def _add(self, text: str) -> int:
        """Publish an individual board question and return its prompt id."""
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/add-question",
            json={
                "type": "poll",
                "text": text,
                "page_number": 1,
                "stage": "round",
                "answer_with": "board",
                "save_to_bank": False,
            },
        )
        self.assertLess(rv.status_code, 400, rv.get_json())
        self.school.ensure_live_session_items(self.session_id)
        item = None
        for row in self.school.list_live_session_items(self.session_id):
            stored = row.get("item") if isinstance(row.get("item"), dict) else {}
            if str(stored.get("text") or "") == text:
                item = row
                break
        self.assertIsNotNone(item)
        published = self.school.publish_live_session_item(
            self.session_id, int(item["id"]), publish_mode="individual"
        )
        self.assertIsNotNone(published.get("prompt_id"))
        return int(published["prompt_id"])

    def _phone(self, index: int):
        """A student client. The visit token stays out of assertions."""
        attendee = self.school.get_live_session_attendee(self.session_id, self.ids[index])
        self.assertIsNotNone(attendee)
        client = self.app.test_client()
        client.environ_base["HTTP_X_STUDENT_VISIT_TOKEN"] = str(attendee["visit_token"])
        return client

    def _draw(self, phone, prompt_id: int, stroke_id: str, salt: int = 0):
        """Post one finished stroke the way the phone presence queue does."""
        x0 = round(0.2 + salt * 0.05, 3)
        y0 = round(0.3 + salt * 0.05, 3)
        x1 = round(0.4 + salt * 0.05, 3)
        y1 = round(0.5 + salt * 0.05, 3)
        rv = phone.post(
            f"/api/student/question-board/{prompt_id}/ops",
            json={
                "points": [[x0, y0], [x1, y1]],
                "stroke_id": stroke_id,
                "ended": True,
                "x": x1,
                "y": y1,
            },
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        return rv.get_json()

    def _tile(self, prompt_id: int, board_key: str, cursors: dict | None = None) -> dict:
        """Teacher dashboard tile for one board. Fail without dumping roster fields."""
        query = ""
        if cursors:
            query = "?cursors=" + quote(json.dumps(cursors), safe="")
        rv = self.client.get(
            f"/api/live-sessions/{self.session_id}/questions/{prompt_id}/boards{query}"
        )
        self.assertEqual(rv.status_code, 200)
        body = rv.get_json() or {}
        for tile in body.get("tiles") or []:
            if str(tile.get("board_key") or "") == board_key:
                return tile
        self.fail("teacher tile missing")
        return {}

    def _panel(self, prompt_id: int, board_key: str, since: int = 0) -> dict:
        """Teacher side-panel payload for one board."""
        query = f"?since={int(since)}" if since else ""
        rv = self.client.get(
            f"/api/live-sessions/{self.session_id}/questions/{prompt_id}/boards/"
            f"{quote(board_key, safe='')}{query}"
        )
        self.assertEqual(rv.status_code, 200)
        return rv.get_json() or {}

    def test_student_clear_empties_the_teacher_view(self) -> None:
        """Clear erases the phone and the teacher tile, panel, and later snapshot."""
        prompt_id = self._add("Board clear sync")
        phone = self._phone(0)
        drawn = self._draw(phone, prompt_id, "ink-a")
        board_key = str(drawn.get("board_key") or "")
        self.assertTrue(board_key)
        held = int(drawn.get("board_seq") or 0)
        self.assertIn("ink-a", _stroke_ids(drawn))
        self.assertIn("ink-a", _stroke_ids(self._panel(prompt_id, board_key)))
        self.assertIn("stroke_add", _op_types(self._tile(prompt_id, board_key)))

        cleared = phone.post(
            f"/api/student/question-board/{prompt_id}/ops",
            json={"op": "board_wipe", "wipe_id": "wipe-a", "id": "wipe-a", "since": held},
        )
        self.assertEqual(cleared.status_code, 200, cleared.get_json())
        body = cleared.get_json() or {}
        self.assertTrue(body.get("ok"))
        self.assertIn("board_wipe", _op_types(body))

        phone_view = phone.get(f"/api/student/question-board/{prompt_id}")
        self.assertEqual(phone_view.status_code, 200)
        self.assertEqual(_stroke_ids(phone_view.get_json() or {}), set())

        panel = self._panel(prompt_id, board_key)
        self.assertTrue(panel.get("snapshot"))
        self.assertEqual(_stroke_ids(panel), set())
        self.assertIn("board_wipe", _op_types(self._tile(prompt_id, board_key)))

        # A teacher poll that already had the stroke still receives the wipe.
        delta_tile = self._tile(prompt_id, board_key, cursors={board_key: held})
        self.assertIn("board_wipe", _op_types(delta_tile))
        delta_panel = self._panel(prompt_id, board_key, since=held)
        self.assertIn("board_wipe", _op_types(delta_panel))

    def test_student_undo_updates_the_teacher_view(self) -> None:
        """Undo removes only that stroke on the phone and on the teacher view."""
        prompt_id = self._add("Board undo sync")
        phone = self._phone(0)
        first = self._draw(phone, prompt_id, "keep")
        board_key = str(first.get("board_key") or "")
        second = self._draw(phone, prompt_id, "drop", salt=1)
        held = int(second.get("board_seq") or 0)
        self.assertEqual(_stroke_ids(self._panel(prompt_id, board_key)), {"keep", "drop"})

        undone = phone.post(
            f"/api/student/question-board/{prompt_id}/ops",
            json={"op": "stroke_remove", "stroke_id": "drop", "since": held},
        )
        self.assertEqual(undone.status_code, 200, undone.get_json())
        self.assertIn("stroke_remove", _op_types(undone.get_json() or {}))
        self.assertEqual(_stroke_ids(self._panel(prompt_id, board_key)), {"keep"})
        self.assertIn("stroke_remove", _op_types(self._tile(prompt_id, board_key, cursors={board_key: held})))
        phone_view = phone.get(f"/api/student/question-board/{prompt_id}")
        self.assertEqual(_stroke_ids(phone_view.get_json() or {}), {"keep"})

    def test_undo_of_clear_restores_the_teacher_view(self) -> None:
        """Unwipe after Clear puts the stroke back on the teacher panel and tile."""
        prompt_id = self._add("Board unwipe sync")
        phone = self._phone(0)
        drawn = self._draw(phone, prompt_id, "back")
        board_key = str(drawn.get("board_key") or "")
        held = int(drawn.get("board_seq") or 0)
        wiped = phone.post(
            f"/api/student/question-board/{prompt_id}/ops",
            json={"op": "board_wipe", "wipe_id": "wipe-b", "id": "wipe-b", "since": held},
        )
        self.assertEqual(wiped.status_code, 200, wiped.get_json())
        after_wipe = int((wiped.get_json() or {}).get("board_seq") or 0)
        self.assertEqual(_stroke_ids(self._panel(prompt_id, board_key)), set())
        restored = phone.post(
            f"/api/student/question-board/{prompt_id}/ops",
            json={
                "op": "board_unwipe",
                "unwipe_id": "unwipe-b",
                "id": "unwipe-b",
                "wipe_id": "wipe-b",
                "since": after_wipe,
            },
        )
        self.assertEqual(restored.status_code, 200, restored.get_json())
        self.assertIn("board_unwipe", _op_types(restored.get_json() or {}))
        # The side panel snapshot has the stroke again.
        self.assertEqual(_stroke_ids(self._panel(prompt_id, board_key)), {"back"})
        # The live gallery treats a cursor past the head as a snapshot request.
        snapped = self._tile(prompt_id, board_key, cursors={board_key: 10**15})
        self.assertTrue(snapped.get("snapshot"))
        self.assertEqual(_stroke_ids(snapped), {"back"})



if __name__ == "__main__":
    unittest.main()

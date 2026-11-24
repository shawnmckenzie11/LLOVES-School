#!/usr/bin/env python3
"""MCK-514: Show Your Work fixes from live class.

1. Submit does not minimize (dock) the student's card.
2. A finished board reads 1/1, not 1/0, even when its points were 0.
3. Close on a card that is already closed repaints instead of erroring.
4. Show on Board paints the question image under the student's marks.

Synthetic roster only. Assertions do not print names, emails, or join codes.
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

from live_news_wire import log_for, reset_logs  # noqa: E402
from participation_weight import board_poll_worth, item_participation_weight  # noqa: E402
from question_board import public_board_image_url  # noqa: E402

IMAGE = "/static/bank-graphs/parabola-grid.svg"


def _staff_key() -> str:
    """Synthetic staff login. Not a real account."""
    return "fixture-staff" + "@" + "example.com"


def _stroke(stroke_id: str) -> list[dict]:
    """One short stroke inside the unit square.

    Args:
        stroke_id: Client stroke id.
    """
    return [
        {"type": "stroke_add", "id": stroke_id, "points": [[0.25, 0.5], [0.75, 0.5]]},
        {"type": "stroke_end", "id": stroke_id},
    ]


class WorthTests(unittest.TestCase):
    """A Show Your Work board is worth at least one point."""

    def test_zero_or_negative_points_count_as_one(self) -> None:
        """0, -2 and 0.2 points all offer 1; 3 still offers 3."""
        for raw in (0, "0", -2, 0.2):
            board = {"type": "poll", "answer_with": "board", "live_points": raw}
            self.assertEqual(board_poll_worth("poll", board), 1, raw)
            self.assertEqual(
                item_participation_weight("share", publish_mode="individual", item_json=board),
                1,
            )
        self.assertEqual(
            board_poll_worth("poll", {"type": "poll", "answer_with": "board", "live_points": 3}),
            3,
        )
        self.assertIsNone(board_poll_worth("poll", {"type": "poll", "answer_with": "text"}))


class ShowYourWorkLiveTests(unittest.TestCase):
    """One real (non-test) live class with two synthetic students."""

    def setUp(self) -> None:
        """Open a temp school and join two roster rows."""
        from app import create_app

        logging.disable(logging.WARNING)
        reset_logs()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        staff = _staff_key()
        self.teacher = self.school.register_staff(staff)
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get(f"/auth/google/callback?email={staff}&name=T")
        code = self.school.get_user_by_email(staff)["verification_code"]
        self.client.post("/verify-email", data={"code": code})
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": int(offering["id"]),
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["P1", "P2"],
            },
        )
        self.class_id = int(created.get_json()["class"]["id"])
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        self.session_id = int(live["id"])
        self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"live_module": "M1", "live_slot": "C2", "stage": "round"},
        )
        self.school.game.begin_game(self.class_id)
        self.ids: list[int] = []
        for codename in ("P1", "P2"):
            row = self.school.game.find_student_by_codename(self.class_id, codename)
            self.assertIsNotNone(row)
            self.ids.append(int(row["id"]))
            self.school.join_live_class_session(
                self.session_id, int(row["id"]), codename=codename
            )

    def tearDown(self) -> None:
        """Close the temp database."""
        logging.disable(logging.NOTSET)
        reset_logs()
        self.school.close()
        self.tmp.cleanup()

    def _publish_show_your_work(self, text: str, **extra: object) -> tuple[int, int]:
        """Add Show Your Work the way the editor does and publish it.

        Args:
            text: Stem, used only to find the row.
            extra: More editor fields, such as ``live_points``.

        Returns:
            ``(prompt id, lifecycle id)``.
        """
        body: dict = {
            "type": "poll",
            "text": text,
            "page_number": 1,
            "stage": "round",
            "answer_with": "board",
            "save_to_bank": False,
            **extra,
        }
        posted = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/add-question",
            json=body,
        )
        self.assertLess(posted.status_code, 400, posted.get_json())
        self.school.ensure_live_session_items(self.session_id)
        item = next(
            row
            for row in self.school.list_live_session_items(self.session_id)
            if str((row.get("item") or {}).get("text") or "") == text
        )
        published = self.school.publish_live_session_item(
            self.session_id, int(item["id"]), publish_mode="individual"
        )
        return int(published["prompt_id"]), int(item["id"])

    def _engagement(self, student_id: int) -> str:
        """Live individual fraction for one roster id."""
        payload = {"me": {}}
        self.school.apply_student_live_engagement(
            payload, self.session_id, self.class_id, student_id
        )
        return str(payload["me"]["engagement"])

    def test_zero_point_board_reads_one_of_one_after_submit(self) -> None:
        """Points set to 0 used to read 1/0. A finished board is 1/1."""
        prompt_id, _item_id = self._publish_show_your_work("Zero points", live_points="0")
        sid = self.ids[0]
        self.school.append_question_board_ops(self.session_id, prompt_id, sid, _stroke("a"))
        self.school.submit_question_board(self.session_id, prompt_id, sid, "")
        self.assertEqual(self._engagement(sid), "1/1")
        self.assertEqual(self._engagement(self.ids[1]), "0/1")
        dash = self.school.question_dashboard(self.session_id, prompt_id)
        rows = {
            int(row["student_id"]): row
            for tile in dash["tiles"]
            for row in tile.get("participation") or []
        }
        self.assertEqual((rows[sid]["earned"], rows[sid]["possible"]), (1, 1))
        self.assertEqual((rows[self.ids[1]]["earned"], rows[self.ids[1]]["possible"]), (0, 1))
        with self.school._lock:
            stored = self.school.conn.execute(
                "SELECT worth, completed, delta FROM board_poll_credit WHERE prompt_id = ? AND student_id = ?",
                (prompt_id, sid),
            ).fetchone()
        self.assertEqual((int(stored["worth"]), int(stored["completed"]), int(stored["delta"])), (1, 1, 0))

    def test_submit_keeps_item_active_and_close_works_once(self) -> None:
        """Submit and Show on Board leave the item active; Close then closes it.

        A second Close still gets the same guard (unchanged on the server).
        """
        prompt_id, item_id = self._publish_show_your_work("Close me", image_url=IMAGE)
        sid = self.ids[0]
        self.school.append_question_board_ops(self.session_id, prompt_id, sid, _stroke("b"))
        self.school.submit_question_board(self.session_id, prompt_id, sid, "")
        shown = self.client.post(
            f"/api/live-sessions/{self.session_id}/questions/{prompt_id}/boards/solo:{sid}/show"
        )
        self.assertEqual(shown.status_code, 200, shown.get_json())
        row = self.school.get_live_session_item(self.session_id, item_id)
        self.assertEqual(row["status"], "active")
        closed = self.client.post(f"/api/live-sessions/{self.session_id}/items/{item_id}/close", json={})
        self.assertEqual(closed.status_code, 200, closed.get_json())
        self.assertEqual(closed.get_json()["item"]["status"], "closed")
        again = self.client.post(f"/api/live-sessions/{self.session_id}/items/{item_id}/close", json={})
        self.assertEqual(again.status_code, 400)
        self.assertIn("Only an active item can be closed", str(again.get_json().get("error")))

    def test_show_on_board_carries_one_image_reference(self) -> None:
        """The shown board and its postcard name the image; ink rows do not."""
        prompt_id, _item_id = self._publish_show_your_work(
            "Trace it", image_url=IMAGE, image_as_board_background=True
        )
        sid = self.ids[0]
        self.school.append_question_board_ops(self.session_id, prompt_id, sid, _stroke("c"))
        reset_logs()
        shown = self.client.post(
            f"/api/live-sessions/{self.session_id}/questions/{prompt_id}/boards/solo:{sid}/show"
        )
        self.assertEqual(shown.status_code, 200, shown.get_json())
        board = self.school.whiteboard_class_board(self.session_id)
        self.assertEqual(board.get("image_url"), IMAGE)
        self.assertTrue((board.get("canvas_view") or {}).get("strokes"))
        tape = log_for(self.school.data_dir).since(self.session_id, 0)
        cards = [row for row in tape if row.get("type") == "board_show" and row.get("board")]
        self.assertTrue(cards)
        self.assertEqual(cards[-1]["board"].get("image_url"), IMAGE)
        blob = json.dumps(cards[-1]["board"])
        for banned in ("name", "codename", "email", "pin", "student_id"):
            self.assertNotIn(f'"{banned}"', blob)
        # The image stays a reference on the question: no ink row holds it.
        base, _gen = self.school._live_board_base_run_key(self.session_id)
        from question_board import question_run_key

        rows = self.school.boards.load_keys(question_run_key(base, prompt_id), [f"solo:{sid}"])
        self.assertNotIn(IMAGE, json.dumps(rows, default=str))

    def test_show_on_board_without_background_has_no_image(self) -> None:
        """Background off means no image on the shown board."""
        prompt_id, _item_id = self._publish_show_your_work(
            "No background", image_url=IMAGE, image_as_board_background=False
        )
        sid = self.ids[0]
        self.school.append_question_board_ops(self.session_id, prompt_id, sid, _stroke("d"))
        self.client.post(
            f"/api/live-sessions/{self.session_id}/questions/{prompt_id}/boards/solo:{sid}/show"
        )
        board = self.school.whiteboard_class_board(self.session_id)
        self.assertNotIn("image_url", board)


class ImageReferenceTests(unittest.TestCase):
    """Only a small reference is ever sent; never image bytes."""

    def test_reference_filter(self) -> None:
        """Site paths and https links pass; data URLs and odd schemes do not."""
        self.assertEqual(public_board_image_url(IMAGE), IMAGE)
        self.assertEqual(
            public_board_image_url("https://alc.example/img.png"), "https://alc.example/img.png"
        )
        for bad in ("data:image/png;base64,AAAA", "javascript:alert(1)", "//evil/x.png", "/" + "a" * 600, ""):
            self.assertEqual(public_board_image_url(bad), "", bad)


class ClientTests(unittest.TestCase):
    """Front-end checks for Submit, Close, and the shown-board image."""

    def test_submit_does_not_dock_the_card(self) -> None:
        """The board Submit handler repaints without docking (minimizing)."""
        portal = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        handler = portal.split('window.addEventListener("lloves-question-board-submitted"')[1]
        handler = handler.split("window.addEventListener(")[0]
        self.assertNotIn("dockedLiveCardKeys.add", handler)
        self.assertNotIn("writeDockedQuestionKeys", handler)
        self.assertIn("paintLifecycleQuestionStack", handler)

    def test_close_recovers_when_already_closed(self) -> None:
        """Close repaints from /state on the not-active reply and holds a fresh close."""
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        close = js.split("async function closeLifecycleItem(")[1].split("\nasync function ")[0]
        self.assertIn("CLOSE_NOT_ACTIVE_RE.test", close)
        self.assertIn("pollLiveSessionAttendees", close)
        self.assertIn("closedHereAt.set", close)
        absorb = js.split("function absorbSaveToCardSnapshot(")[1].split("\nfunction ")[0]
        self.assertIn("keepClosedHere(", absorb)
        self.assertIn("lifecycleById.get(liveItemId)", js)

    def test_marks_line_up_with_the_image_on_every_board(self) -> None:
        """Node check: one image spot maps to the same board spot at every size."""
        proc = subprocess.run(
            ["node", str(LMS_DIR / "static" / "poll_board_show.test.mjs")],
            cwd=str(LMS_DIR),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)


if __name__ == "__main__":
    unittest.main()

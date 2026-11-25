#!/usr/bin/env python3
"""MCK-520: Show All cache stamp reads board counters from the board store.

When the board store is not the school sqlite connection (Postgres in
production), reading counters from ``school.conn`` freezes the cache and
phones see backgrounds without ink.

Synthetic roster only. Assertions do not print names, emails, or join codes.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
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

from live_news_wire import reset_logs  # noqa: E402
from question_board import public_board_image_url, question_run_key  # noqa: E402

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


class SplitBoardStoreMixin:
    """Put board ops on a second sqlite file, like Postgres in production."""

    board_conn: sqlite3.Connection

    def _attach_split_board_store(self) -> None:
        """Replace ``school.boards`` with a store on another connection."""
        from board_ops import SqliteBoardOps

        board_path = Path(self.tmp.name) / "boards.sqlite"
        conn = sqlite3.connect(board_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        store = SqliteBoardOps(conn, self.school._lock)
        store.ensure_schema()
        self.school.boards = store
        self.board_conn = conn

    def _strokes(self, payload: object) -> int:
        """Count strokes on every board in a ``class_boards`` list.

        Args:
            payload: Student live payload or board read dict.
        """
        boards = payload.get("class_boards") if isinstance(payload, dict) else payload
        total = 0
        if isinstance(boards, list):
            for board in boards:
                view = board.get("canvas_view") if isinstance(board, dict) else None
                total += len((view or {}).get("strokes") or [])
        return total


class ShowAllSplitStoreTests(SplitBoardStoreMixin, unittest.TestCase):
    """Question-board Show All with ink on a separate board database."""

    def setUp(self) -> None:
        """Open a school and split the board store onto another sqlite file."""
        from app import create_app

        logging.disable(logging.WARNING)
        reset_logs()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self._attach_split_board_store()
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
        """Close databases."""
        logging.disable(logging.NOTSET)
        reset_logs()
        self.board_conn.close()
        self.school.close()
        self.tmp.cleanup()

    def _publish(self, text: str, **extra: object) -> int:
        """Publish one board question and return its prompt id."""
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
        return int(published["prompt_id"])

    def test_split_store_rebuilds_after_ink(self) -> None:
        """Counters on the board store move the cache when ink arrives."""
        prompt_id = self._publish(
            "Split stamp", image_url=IMAGE, image_as_board_background=True
        )
        shown = self.client.post(
            f"/api/live-sessions/{self.session_id}/questions/{prompt_id}/boards/show-all"
        )
        self.assertEqual(shown.status_code, 200, shown.get_json())
        empty = self.school.assemble_student_live_payload(
            self.session_id, self.class_id, self.ids[0]
        )
        self.assertEqual(self._strokes(empty), 0)
        boards = empty.get("class_boards") or []
        self.assertEqual(len(boards), len(self.ids))
        for board in boards:
            self.assertEqual(board.get("image_url"), IMAGE)
            self.assertNotIn("name", board)
            self.assertEqual(public_board_image_url(board.get("image_url")), IMAGE)

        for index, sid in enumerate(self.ids):
            self.school.append_question_board_ops(
                self.session_id, prompt_id, sid, _stroke(f"split-{index}")
            )
        live = self.school.assemble_student_live_payload(
            self.session_id, self.class_id, self.ids[0]
        )
        self.assertGreater(self._strokes(live), 0)
        for board in live.get("class_boards") or []:
            self.assertEqual(board.get("image_url"), IMAGE)
            self.assertNotIn("name", board)
            self.assertTrue((board.get("canvas_view") or {}).get("strokes"))

        base, _gen = self.school._live_board_base_run_key(self.session_id)
        run = question_run_key(base, prompt_id)
        seqs = self.school.boards.head_seqs(run, [f"solo:{sid}" for sid in self.ids])
        self.assertTrue(all(int(seqs.get(f"solo:{sid}") or 0) > 0 for sid in self.ids))
        with self.school.conn:
            rows = self.school.conn.execute(
                "SELECT next_seq FROM board_counters WHERE run_key = ?",
                (run,),
            ).fetchall()
        self.assertEqual(rows, [])

    def test_quiet_poll_and_fresh_ink_on_split_store(self) -> None:
        """Quiet polls reuse the grid; one new stroke rebuilds teacher and student copies."""
        prompt_id = self._publish("Cache split", image_url=IMAGE)
        sid = self.ids[0]
        self.school.append_question_board_ops(
            self.session_id, prompt_id, sid, _stroke("cached-split")
        )
        shown = self.client.post(
            f"/api/live-sessions/{self.session_id}/questions/{prompt_id}/boards/show-all"
        )
        self.assertEqual(shown.status_code, 200, shown.get_json())
        calls = {"n": 0}
        original = self.school._question_show_all_cards

        def wrapped(*args: object, **kwargs: object) -> list:
            """Count grid builds."""
            calls["n"] += 1
            return original(*args, **kwargs)

        self.school._question_show_all_cards = wrapped  # type: ignore[method-assign]
        first = self.school.assemble_student_live_payload(
            self.session_id, self.class_id, sid
        )
        teacher = self.school.whiteboard_class_boards(self.session_id)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(self._strokes(first), 1)
        self.assertIsNot(first.get("class_boards"), teacher)
        teacher_blob = json.dumps(teacher, default=str).lower()
        self.assertIn('"name"', teacher_blob)
        student_blob = json.dumps(first.get("class_boards"), default=str).lower()
        self.assertNotIn('"name"', student_blob)
        self.school.append_question_board_ops(
            self.session_id, prompt_id, sid, _stroke("fresh-split")
        )
        third = self.school.assemble_student_live_payload(
            self.session_id, self.class_id, sid
        )
        self.assertEqual(calls["n"], 2)
        self.assertEqual(self._strokes(third), 2)
        self.assertNotIn(
            '"name"',
            json.dumps(third.get("class_boards"), default=str).lower(),
        )


class SessionGallerySplitStoreTests(SplitBoardStoreMixin, unittest.TestCase):
    """Session-gallery Show All uses the same counter stamp."""

    def setUp(self) -> None:
        """Start live class with a split board store."""
        from app import create_app

        logging.disable(logging.WARNING)
        reset_logs()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self._attach_split_board_store()
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
        self.school.game.begin_game(self.class_id)
        self.ids: list[int] = []
        for codename in ("P1", "P2"):
            row = self.school.game.find_student_by_codename(self.class_id, codename)
            self.assertIsNotNone(row)
            self.ids.append(int(row["id"]))
            self.school.join_live_class_session(
                self.session_id, int(row["id"]), codename=codename
            )

    def _publish_gallery(self) -> None:
        """Publish the session whiteboard so gallery cards exist."""
        self.school.ensure_live_session_items(self.session_id)
        whiteboard = next(
            row
            for row in self.school.list_live_session_items(self.session_id)
            if str(row.get("kind") or "") == "whiteboard"
        )
        self.school.publish_live_session_item(
            self.session_id, int(whiteboard["id"]), publish_mode="individual"
        )

    def tearDown(self) -> None:
        """Close databases."""
        logging.disable(logging.NOTSET)
        reset_logs()
        self.board_conn.close()
        self.school.close()
        self.tmp.cleanup()

    def test_gallery_split_store_rebuilds_after_ink(self) -> None:
        """Gallery Show All picks up ink when counters live on the board store."""
        self._publish_gallery()
        shown = self.client.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/show-all"
        )
        self.assertEqual(shown.status_code, 200, shown.get_json())
        empty = self.school.assemble_student_live_payload(
            self.session_id, self.class_id, self.ids[0]
        )
        self.assertEqual(self._strokes(empty), 0)

        for index, sid in enumerate(self.ids):
            self.school.append_live_board_ops(
                self.session_id,
                f"solo:{sid}",
                owner=str(sid),
                ops=_stroke(f"gallery-{index}"),
            )
        live = self.school.assemble_student_live_payload(
            self.session_id, self.class_id, self.ids[0]
        )
        self.assertGreater(self._strokes(live), 0)
        for board in live.get("class_boards") or []:
            self.assertNotIn("name", board)

    def test_gallery_quiet_poll_and_fresh_ink_on_split_store(self) -> None:
        """Gallery grid caching follows counter moves on the board store."""
        self._publish_gallery()
        sid = self.ids[0]
        self.school.append_live_board_ops(
            self.session_id,
            f"solo:{sid}",
            owner=str(sid),
            ops=_stroke("gallery-cached"),
        )
        shown = self.client.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/show-all"
        )
        self.assertEqual(shown.status_code, 200, shown.get_json())
        calls = {"n": 0}
        original = self.school._session_show_all_cards

        def wrapped(*args: object, **kwargs: object) -> list:
            """Count gallery grid builds."""
            calls["n"] += 1
            return original(*args, **kwargs)

        self.school._session_show_all_cards = wrapped  # type: ignore[method-assign]
        first = self.school.assemble_student_live_payload(
            self.session_id, self.class_id, sid
        )
        teacher = self.school.whiteboard_class_boards(self.session_id)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(self._strokes(first), 1)
        self.assertIsNot(first.get("class_boards"), teacher)
        self.school.append_live_board_ops(
            self.session_id,
            f"solo:{sid}",
            owner=str(sid),
            ops=_stroke("gallery-fresh"),
        )
        third = self.school.assemble_student_live_payload(
            self.session_id, self.class_id, sid
        )
        self.assertEqual(calls["n"], 2)
        self.assertEqual(self._strokes(third), 2)


if __name__ == "__main__":
    unittest.main()
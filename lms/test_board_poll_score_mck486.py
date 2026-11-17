#!/usr/bin/env python3
"""MCK-486 and MCK-487: removed questions drop their points; whiteboard polls score.

Synthetic roster only. Assertions do not print names, emails, or join codes.
"""

from __future__ import annotations

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

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from board_poll_score import adjust  # noqa: E402
from engagement_rollup import compute_rollup  # noqa: E402
from participation_weight import board_poll_worth, item_participation_weight  # noqa: E402


def _stroke(stroke_id: str) -> list[dict]:
    """One short stroke inside the unit square.

    Args:
        stroke_id: Client stroke id.
    """
    return [
        {"type": "stroke_add", "id": stroke_id, "points": [[0.2, 0.2], [0.4, 0.5]]},
        {"type": "stroke_end", "id": stroke_id},
    ]


def _staff_key() -> str:
    """Synthetic staff login. Not a real account."""
    return "fixture-staff" + "@" + "example.com"


class BoardPollWorthTests(unittest.TestCase):
    """Points come from the question, and the rollup adds them once."""

    def test_text_poll_stays_one_and_board_poll_uses_stored_points(self) -> None:
        """A text poll is 1. A whiteboard poll uses the points from Add New."""
        text = {"type": "poll", "answer_with": "text", "live_points": 9}
        board = {"type": "poll", "answer_with": "board", "live_points": 4}
        self.assertIsNone(board_poll_worth("poll", text))
        self.assertEqual(item_participation_weight("poll", item_json=text), 1)
        self.assertEqual(board_poll_worth("poll", board), 4)
        self.assertEqual(
            item_participation_weight("share", item_json=board),
            4,
        )
        self.assertIsNone(
            board_poll_worth(
                "poll",
                {"type": "poll", "answer_with": "board", "publish_mode": "group_shared"},
            )
        )

    def test_rollup_adds_worth_once_per_question(self) -> None:
        """An offer of 4 and one completion is 4/4. A second event does not double it."""
        events = [
            {
                "id": 1,
                "kind": "present.join",
                "ts": "2026-11-17T10:00:00",
                "student_id": 7,
                "item_ref": "",
                "payload": {},
            },
            {
                "id": 2,
                "kind": "item.offered",
                "ts": "2026-11-17T10:01:00",
                "student_id": None,
                "item_ref": "10",
                "payload": {"mode": "individual", "worth": 4},
            },
            {
                "id": 3,
                "kind": "answer.submit",
                "ts": "2026-11-17T10:02:00",
                "student_id": 7,
                "item_ref": "99",
                "payload": {"worth": 4},
            },
            {
                "id": 4,
                "kind": "answer.change",
                "ts": "2026-11-17T10:03:00",
                "student_id": 7,
                "item_ref": "99",
                "payload": {"worth": 4},
            },
        ]
        rows = compute_rollup(
            events,
            class_id=1,
            live_session=1,
            run_key="run",
            team_for_student={7: None},
            student_ids=[7],
        )
        cell = next(
            row for row in rows if row.scope == "student" and row.signal == "individual_answer"
        )
        self.assertEqual(cell.count, 4)
        self.assertEqual(cell.offered, 4)


class BoardPollLiveTests(unittest.TestCase):
    """Live class: text polls, whiteboard polls, remove, and teacher steps."""

    def setUp(self) -> None:
        """One staff fixture, two synthetic students, a test live class."""
        from app import create_app

        logging.disable(logging.WARNING)
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
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=True
        )
        self.session_id = int(live["id"])
        self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"live_module": "M1", "live_slot": "C2", "stage": "round"},
        )
        self.school.game.begin_game(self.class_id)
        self.ids = []
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
        self.school.close()
        self.tmp.cleanup()

    def _engagement(self, student_id: int) -> str:
        """Live card fraction for one roster id."""
        payload = {"me": {}}
        self.school.apply_student_live_engagement(
            payload, self.session_id, self.class_id, student_id
        )
        return str(payload["me"]["engagement"])

    def _add(self, text: str, *, answer_with: str = "text", points: int | None = None) -> dict:
        """Add and publish one poll. Returns the lifecycle row.

        Args:
            text: Stem, used only to find the row.
            answer_with: ``text``, ``board``, or ``both``.
            points: Points stored at create time. Omitted leaves the default.
        """
        body = {
            "type": "poll",
            "text": text,
            "page_number": 1,
            "stage": "round",
            "answer_with": answer_with,
            "save_to_bank": False,
        }
        if points is not None:
            body["live_points"] = points
        posted = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/add-question",
            json=body,
        )
        self.assertLess(posted.status_code, 400, posted.get_json())
        self.school.ensure_live_session_items(self.session_id)
        item = self._item_by_text(text)
        self.school.publish_live_session_item(
            self.session_id, int(item["id"]), publish_mode="individual"
        )
        return self._item_by_text(text)

    def _item_by_text(self, text: str) -> dict:
        """Lifecycle row whose stem is ``text``."""
        for row in self.school.list_live_session_items(self.session_id):
            item = row.get("item") if isinstance(row.get("item"), dict) else {}
            if str(item.get("text") or "") == text:
                return row
        self.fail("question missing")
        return {}

    def test_text_poll_stays_one_over_one_when_points_are_higher(self) -> None:
        """A text poll with points 9 still scores 1/1 on submit."""
        item = self._add("Text poll", answer_with="text", points=9)
        self.assertEqual(self._engagement(self.ids[0]), "0/1")
        self.school.submit_live_prompt_response(
            int(item["prompt_id"]), self.ids[0], {"text": "ok"}
        )
        self.assertEqual(self._engagement(self.ids[0]), "1/1")
        self.assertEqual(self._engagement(self.ids[1]), "0/1")

    def test_stroke_or_text_awards_the_question_points_once(self) -> None:
        """A whiteboard poll offers its points. A stroke or text earns them once."""
        item = self._add("Board poll", answer_with="board", points=4)
        prompt_id = int(item["prompt_id"])
        self.assertEqual(self._engagement(self.ids[0]), "0/4")
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[0], _stroke("once")
        )
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[0], _stroke("again")
        )
        self.assertEqual(self._engagement(self.ids[0]), "4/4")
        self.assertEqual(self._engagement(self.ids[1]), "0/4")
        other = self._add("Typed board", answer_with="both", points=3)
        self.school.apply_question_presence(
            self.session_id,
            int(other["prompt_id"]),
            self.ids[1],
            {"text_id": "t1", "text": "note", "x": 0.2, "y": 0.3},
        )
        self.assertEqual(self._engagement(self.ids[1]), "3/7")
        self.assertEqual(self._engagement(self.ids[0]), "4/7")

    def test_remove_clears_that_question_for_every_student(self) -> None:
        """Remove completely drops one question's earned and possible points."""
        text = self._add("Keep poll", answer_with="text")
        board = self._add("Drop poll", answer_with="board", points=4)
        self.school.submit_live_prompt_response(
            int(text["prompt_id"]), self.ids[0], {"text": "ok"}
        )
        self.school.submit_live_prompt_response(
            int(text["prompt_id"]), self.ids[1], {"text": "ok"}
        )
        self.school.append_question_board_ops(
            self.session_id, int(board["prompt_id"]), self.ids[0], _stroke("drop")
        )
        self.assertEqual(self._engagement(self.ids[0]), "5/5")
        self.assertEqual(self._engagement(self.ids[1]), "1/5")
        self.school.remove_class_playlist_item(
            self.class_id, "M1", "C2", str(board["item_id"])
        )
        self.assertEqual(self._engagement(self.ids[0]), "1/1")
        self.assertEqual(self._engagement(self.ids[1]), "1/1")
        self.school.remove_class_playlist_item(
            self.class_id, "M1", "C2", str(text["item_id"])
        )
        self.assertEqual(self._engagement(self.ids[0]), "0/0")
        self.assertEqual(self._engagement(self.ids[1]), "0/0")

    def test_remove_leaves_the_same_question_in_another_class(self) -> None:
        """A bank item shared with another section keeps that section's points."""
        board = self._add("Shared poll", answer_with="board", points=4)
        prompt_id = int(board["prompt_id"])
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[0], _stroke("mine")
        )
        conn = self.school.conn
        key = conn.execute(
            "SELECT question_key FROM board_poll_credit WHERE prompt_id = ?",
            (prompt_id,),
        ).fetchone()["question_key"]
        self.assertTrue(key)
        conn.execute(
            """
            INSERT INTO board_poll_credit (
                run_key, prompt_id, student_id, question_key, worth,
                completed, delta, updated_at
            ) VALUES ('other-section-run', 987654, 1, ?, 4, 1, 1,
                      '2026-11-17T10:00:00')
            """,
            (key,),
        )
        conn.commit()
        self.school.remove_class_playlist_item(
            self.class_id, "M1", "C2", str(board["item_id"])
        )
        rows = conn.execute(
            "SELECT run_key FROM board_poll_credit WHERE question_key = ?",
            (key,),
        ).fetchall()
        self.assertEqual([r["run_key"] for r in rows], ["other-section-run"])

    def test_teacher_steps_change_earned_points_only(self) -> None:
        """Up and down sit on the earned points. Offered points stay put."""
        item = self._add("Adjust poll", answer_with="board", points=4)
        prompt_id = int(item["prompt_id"])
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[0], _stroke("base")
        )
        self.assertEqual(self._engagement(self.ids[0]), "4/4")
        raised = adjust(self.school, self.session_id, prompt_id, self.ids[0], 1)
        self.assertEqual(raised["earned"], 5)
        self.assertEqual(raised["possible"], 4)
        self.assertEqual(self._engagement(self.ids[0]), "5/4")
        adjust(self.school, self.session_id, prompt_id, self.ids[0], -1)
        self.assertEqual(self._engagement(self.ids[0]), "4/4")
        for _ in range(6):
            adjust(self.school, self.session_id, prompt_id, self.ids[0], -1)
        self.assertEqual(self._engagement(self.ids[0]), "0/4")
        self.assertEqual(self._engagement(self.ids[1]), "0/4")

    def test_dashboard_lists_participation_beside_the_board(self) -> None:
        """The teacher tile carries earned and possible, not a second dashboard."""
        item = self._add("Dash poll", answer_with="board", points=2)
        self.school.apply_question_presence(
            self.session_id,
            int(item["prompt_id"]),
            self.ids[0],
            {"points": [[0.1, 0.2], [0.3, 0.4]], "stroke_id": "p"},
        )
        dash = self.school.question_dashboard(self.session_id, int(item["prompt_id"]))
        tiles = {int(row["student_ids"][0]): row for row in dash["tiles"]}
        done = tiles[self.ids[0]]["participation"][0]
        waiting = tiles[self.ids[1]]["participation"][0]
        self.assertEqual(done["earned"], 2)
        self.assertEqual(done["possible"], 2)
        self.assertEqual(waiting["earned"], 0)
        self.assertEqual(waiting["possible"], 2)
        ui = (LMS_DIR / "static" / "question_board_ui.js").read_text(encoding="utf-8")
        self.assertIn('data-wb-part="score"', ui)
        self.assertIn("wireBoardScore", ui)
        self.assertIn("Lower participation", ui)
        self.assertIn("Raise participation", ui)

    def test_end_participation_uses_the_question_points(self) -> None:
        """After End, Attendance & Participation shows the board points, not 1."""
        item = self._add("Ended poll", answer_with="board", points=4)
        self.school.append_question_board_ops(
            self.session_id, int(item["prompt_id"]), self.ids[0], _stroke("end")
        )
        self.school.end_live_class_session(self.session_id, record_participation=True)
        with self.school._lock:
            link = self.school.conn.execute(
                "SELECT run_key FROM catchup_links WHERE class_id = ?",
                (self.class_id,),
            ).fetchone()
            self.school.conn.execute(
                """
                UPDATE participation_runs
                SET is_real = 1, discarded = 0, teacher_kind_set = 1
                WHERE class_id = ? AND run_key = ?
                """,
                (self.class_id, str(link["run_key"])),
            )
            self.school.conn.commit()
        data = self.school.class_participation(
            self.class_id, include_test_students=True
        )
        by_id = {row["student_id"]: row for row in data["students"]}
        done = by_id[self.ids[0]]["modules"]["M1"]
        other = by_id[self.ids[1]]["modules"]["M1"]
        self.assertEqual(done["answered"], 4)
        self.assertEqual(done["offered"], 4)
        self.assertEqual(other["answered"], 0)
        self.assertEqual(other["offered"], 4)

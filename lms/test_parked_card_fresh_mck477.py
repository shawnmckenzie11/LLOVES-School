#!/usr/bin/env python3
"""MCK-477: a parked card's next read matches a fresh build.

No in-process cache. Each case mutates the row the card renders and
compares the serving connection with a second connection on the same file.
Synthetic roster labels only. Failures do not print join codes.
"""

from __future__ import annotations

import json
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
from school_db import SchoolDB  # noqa: E402
from test_team_draw_mck242 import _INKED_PNG, _png_b64  # noqa: E402


class ParkedCardFreshTests(unittest.TestCase):
    """Parked questions stay current for every student on the next read."""

    def setUp(self) -> None:
        """One staff session and two synthetic students."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db_path = root / "lloves.sqlite"
        self.app = create_app(db_path=self.db_path, data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("fixture-staff@example.com")
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
        )
        self.client = self.app.test_client()
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
                "codenames": ["S1", "S2", "S3", "S4"],
            },
        )
        self.class_id = int(created.get_json()["class"]["id"])
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=True
        )
        self.session_id = int(live["id"])
        self.run_key = str(live.get("run_key") or "")
        self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={
                "live_module": "M1",
                "live_slot": "C2",
                "stage": "round",
                "page_number": 4,
                "class_set": True,
            },
        )
        self.school.game.begin_game(self.class_id)
        self.ids = {
            label: int(self.school.game.find_student_by_codename(self.class_id, label)["id"])
            for label in ("S1", "S2", "S3", "S4")
        }
        self.s1 = self.ids["S1"]
        self.s2 = self.ids["S2"]
        for label, student_id in self.ids.items():
            self.school.join_live_class_session(
                self.session_id, student_id, codename=label
            )

    def tearDown(self) -> None:
        """Close both sqlite handles."""
        self.school.close()
        self.tmp.cleanup()

    def _add(self, body: dict[str, Any], *, mode: str = "individual") -> dict[str, Any]:
        """Publish one question on page 4 and return its lifecycle row."""
        payload = {"page_number": 4, "stage": "round", "save_to_bank": False, **body}
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/add-question",
            json=payload,
        )
        self.assertLess(rv.status_code, 400, rv.get_json())
        item_id = str(rv.get_json()["placement"]["item"]["id"])
        self.school.ensure_live_session_items(self.session_id)
        row = next(
            item
            for item in self.school.list_live_session_items(self.session_id)
            if str(item.get("item_id") or "") == item_id
        )
        return self.school.publish_live_session_item(
            self.session_id, int(row["id"]), publish_mode=mode
        )

    def _park(self) -> None:
        """Move the teacher off the question's page."""
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M1",
            live_slot="C2",
            stage="round",
            page_number=9,
        )

    def _cards(self, school: SchoolDB, student_id: int) -> list[dict[str, Any]]:
        """Every card pool for one student."""
        payload = school.student_live_items_payload(self.session_id, student_id)
        return list(payload.get("active_questions") or []) + list(
            payload.get("saved_cards") or []
        ) + list(payload.get("closed_results") or [])

    def _card(self, school: SchoolDB, student_id: int, item_id: str) -> dict[str, Any]:
        """One question card from any pool."""
        for row in self._cards(school, student_id):
            if str(row.get("item_id") or "") == item_id:
                return row
        self.fail(f"missing card {item_id}")
        raise AssertionError

    def _fresh(self, student_id: int, item_id: str) -> dict[str, Any]:
        """The same card from a new connection, with no process memory."""
        other = SchoolDB(self.db_path, self.db_path.parent, live_database_url="")
        try:
            return self._card(other, student_id, item_id)
        finally:
            other.close()

    def _assert_matches_fresh(self, student_id: int, item_id: str) -> dict[str, Any]:
        """The serving read equals a connection that has never seen this process."""
        served = self._card(self.school, student_id, item_id)
        fresh = self._fresh(student_id, item_id)
        self.assertEqual(served, fresh)
        return served

    def test_first_answer_after_parking_matches_a_fresh_build(self) -> None:
        """An answer sent after the card was parked shows on the next read."""
        row = self._add({
            "type": "mc",
            "text": "Parked first answer",
            "options": ["Red", "Blue", "Green", "Gold"],
            "correct_index": 0,
        })
        item_id = str(row["item_id"])
        self._park()
        before = self._assert_matches_fresh(self.s1, item_id)
        self.assertIsNone(before.get("my_response"))
        prompt = self.school._prompt_for_live_item(row)
        self.school.submit_live_prompt_response(
            int(prompt["id"]), self.s1, {"choice": "Red"}
        )
        card = self._assert_matches_fresh(self.s1, item_id)
        self.assertEqual((card.get("my_response") or {}).get("response", {}).get("choice"), "Red")

    def test_replaced_answer_matches_a_fresh_build(self) -> None:
        """Replacing an answer updates the card even when the row id stays."""
        row = self._add({
            "type": "mc",
            "text": "Replace answer",
            "options": ["Red", "Blue", "Green", "Gold"],
            "correct_index": 0,
        })
        item_id = str(row["item_id"])
        prompt = self.school._prompt_for_live_item(row)
        self.school.submit_live_prompt_response(
            int(prompt["id"]), self.s1, {"choice": "Red"}
        )
        self._park()
        self.school.submit_live_prompt_response(
            int(prompt["id"]), self.s1, {"choice": "Blue"}
        )
        card = self._assert_matches_fresh(self.s1, item_id)
        self.assertEqual((card.get("my_response") or {}).get("response", {}).get("choice"), "Blue")
        before = self.school.live_student_poll_stamp(self.session_id, self.class_id, self.s1)
        self.school.submit_live_prompt_response(
            int(prompt["id"]), self.s1, {"choice": "Green"}
        )
        self.assertNotEqual(
            self.school.live_student_poll_stamp(self.session_id, self.class_id, self.s1),
            before,
        )

    def test_score_override_matches_a_fresh_build(self) -> None:
        """A points write shows on the card without relying on updated_at."""
        row = self._add({
            "type": "mc",
            "text": "Score override",
            "options": ["Red", "Blue", "Green", "Gold"],
            "correct_index": 0,
        })
        item_id = str(row["item_id"])
        prompt = self.school._prompt_for_live_item(row)
        self.school.submit_live_prompt_response(
            int(prompt["id"]), self.s1, {"choice": "Red"}
        )
        self._park()
        with self.school._lock:
            self.school.conn.execute(
                """
                UPDATE live_session_responses
                SET awarded_points = 4
                WHERE prompt_id = ? AND student_id = ?
                """,
                (int(prompt["id"]), self.s1),
            )
            self.school.conn.commit()
        card = self._assert_matches_fresh(self.s1, item_id)
        self.assertEqual((card.get("my_response") or {}).get("awarded_points"), 4)

    def test_board_feedback_matches_a_fresh_build(self) -> None:
        """Teacher feedback on a board is on the next parked read."""
        from question_board import save_copy_feedback
        from test_question_board_mck468 import _stroke

        row = self._add({
            "type": "poll",
            "text": "Board note",
            "answer_with": "board",
        })
        item_id = str(row["item_id"])
        prompt = self.school._prompt_for_live_item(row)
        prompt_id = int(prompt["id"])
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.s1, _stroke("ink")
        )
        self.school.submit_question_board(self.session_id, prompt_id, self.s1, "")
        self._park()
        with self.school._lock:
            copy = self.school.conn.execute(
                """
                SELECT run_key, board_key FROM board_answer_copies
                WHERE class_id = ? AND board_key = ?
                """,
                (self.class_id, f"solo:{self.s1}"),
            ).fetchone()
        self.assertIsNotNone(copy)
        save_copy_feedback(
            self.school,
            self.class_id,
            str(copy["run_key"]),
            str(copy["board_key"]),
            "Check the axis.",
        )
        card = self._assert_matches_fresh(self.s1, item_id)
        self.assertEqual(card.get("board_feedback"), "Check the axis.")

    def test_prompt_rewrite_without_updated_at_matches_a_fresh_build(self) -> None:
        """A prompt body change shows even when updated_at is left alone."""
        row = self._add({
            "type": "mc",
            "text": "Original stem",
            "options": ["Red", "Blue", "Green", "Gold"],
            "correct_index": 0,
        })
        item_id = str(row["item_id"])
        prompt = self.school._prompt_for_live_item(row)
        self._park()
        body = dict(prompt["payload"])
        body["text"] = "Rewritten stem"
        with self.school._lock:
            self.school.conn.execute(
                """
                UPDATE live_session_prompts
                SET payload = ?
                WHERE id = ?
                """,
                (json.dumps(body), int(prompt["id"])),
            )
            self.school.conn.commit()
        card = self._assert_matches_fresh(self.s1, item_id)
        self.assertEqual((card.get("content") or {}).get("text"), "Rewritten stem")

    def test_group_submit_updates_both_teammates(self) -> None:
        """One group send is on both teammates' next parked read."""
        self.school.setup_live_session_groups(
            self.session_id,
            n_teams=2,
            mode="manual",
            present_ids=list(self.ids.values()),
            assignments=[
                {"student_id": self.s1, "team_index": 0},
                {"student_id": self.s2, "team_index": 0},
                {"student_id": self.ids["S3"], "team_index": 1},
                {"student_id": self.ids["S4"], "team_index": 1},
            ],
        )
        self.school.set_live_session_teacher_state(
            self.session_id, run_as_group=True, groups_configured=True
        )
        row = self._add(
            {
                "type": "mc",
                "text": "Group send",
                "options": ["Red", "Blue", "Green", "Gold"],
                "correct_index": 0,
            },
            mode="group_submit",
        )
        item_id = str(row["item_id"])
        self._park()
        for student_id in (self.s1, self.s2):
            self.school.submit_group_mc_pick(
                self.session_id, int(row["id"]), student_id, choice="Red"
            )
        self.school.submit_group_mc_answer(
            self.session_id, int(row["id"]), self.s1, choice="Red", why="shared"
        )
        for student_id in (self.s1, self.s2):
            card = self._assert_matches_fresh(student_id, item_id)
            self.assertTrue((card.get("group_submit") or {}).get("submitted"))

    def test_team_draw_submit_updates_both_teammates(self) -> None:
        """A team drawing is on both teammates' next parked read."""
        self.school.setup_live_session_groups(
            self.session_id,
            n_teams=2,
            mode="manual",
            present_ids=list(self.ids.values()),
            assignments=[
                {"student_id": self.s1, "team_index": 0},
                {"student_id": self.s2, "team_index": 0},
                {"student_id": self.ids["S3"], "team_index": 1},
                {"student_id": self.ids["S4"], "team_index": 1},
            ],
        )
        self.school.set_live_session_teacher_state(
            self.session_id, run_as_group=True, groups_configured=True
        )
        row = self._add({"type": "team_draw", "text": "Draw check"})
        with self.school._lock:
            self.school.conn.execute(
                "UPDATE live_session_items SET response_mode = 'team_draw' WHERE id = ?",
                (int(row["id"]),),
            )
            self.school.conn.commit()
        item_id = str(row["item_id"])
        self._park()
        self.school.submit_team_draw(
            self.session_id,
            int(row["id"]),
            self.s1,
            image_b64=_png_b64(_INKED_PNG),
        )
        for student_id in (self.s1, self.s2):
            card = self._assert_matches_fresh(student_id, item_id)
            state = card.get("group_submit") or {}
            self.assertTrue(state.get("submitted"))

    def test_second_student_first_answer_matches_a_fresh_build(self) -> None:
        """One student's first answer does not stick to the other card."""
        row = self._add({
            "type": "mc",
            "text": "Two students",
            "options": ["Red", "Blue", "Green", "Gold"],
            "correct_index": 0,
        })
        item_id = str(row["item_id"])
        prompt = self.school._prompt_for_live_item(row)
        self.school.submit_live_prompt_response(
            int(prompt["id"]), self.s1, {"choice": "Red"}
        )
        self._park()
        other = self._assert_matches_fresh(self.s2, item_id)
        self.assertIsNone(other.get("my_response"))
        self.school.submit_live_prompt_response(
            int(prompt["id"]), self.s2, {"choice": "Blue"}
        )
        second = self._assert_matches_fresh(self.s2, item_id)
        first = self._assert_matches_fresh(self.s1, item_id)
        self.assertEqual((second.get("my_response") or {}).get("response", {}).get("choice"), "Blue")
        self.assertEqual((first.get("my_response") or {}).get("response", {}).get("choice"), "Red")


if __name__ == "__main__":
    unittest.main()

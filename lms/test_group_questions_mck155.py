#!/usr/bin/env python3
"""MCK-155: group question marks (S3), instructions (S4), still writing (S5).

Real MCR3U M1 C2 deck metadata and real teacher routes: Add New, then
Publish to groups. Students answer through the school DB like the
student routes do.
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
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402


class GroupQuestionTests(unittest.TestCase):
    """Two teams of two (Ava + Cy, Ben + Dee), live on M1 C2 round."""

    def setUp(self) -> None:
        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite", data_dir=root, testing=True
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
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
                codenames=["Ava", "Ben", "Cy", "Dee"],
                offering_id=int(offering["id"]),
                teacher_user_id=int(teacher["id"]),
            )["id"]
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        code = self.school.get_user_by_email("teacher@gmail.com")["verification_code"]
        self.client.post("/verify-email", data={"code": code})
        self.session_id = int(
            self.school.start_live_class_session(self.class_id, int(teacher["id"]))["id"]
        )
        self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"live_module": "M1", "live_slot": "C2", "stage": "round", "page_id": "round_1"},
        )
        self.school.game.begin_game(self.class_id)
        with self.school.game._lock:
            self.students = [
                dict(row)
                for row in self.school.game.conn.execute(
                    "SELECT * FROM students WHERE class_id = ? ORDER BY id",
                    (self.class_id,),
                ).fetchall()
            ]
        self.ids = [int(s["id"]) for s in self.students]
        for student in self.students:
            self.school.join_live_class_session(
                self.session_id, int(student["id"]), codename=str(student["codename"])
            )
        self.school.setup_live_session_groups(
            self.session_id,
            n_teams=2,
            mode="manual",
            present_ids=self.ids,
            assignments=[{"student_id": sid, "team_index": i % 2} for i, sid in enumerate(self.ids)],
        )
        self.school.ensure_live_session_items(self.session_id)

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)
        self.school.close()
        self.tmp.cleanup()

    def _add_publish(self, body: dict[str, Any], mode: str) -> dict[str, Any]:
        """Add New + Publish through the teacher routes."""

        body = {"page_number": 4, "stage": "round", **body}
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/add-question", json=body
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        item_id = rv.get_json()["placement"]["item"]["id"]
        row = next(
            r for r in self.school.list_live_session_items(self.session_id) if r["item_id"] == item_id
        )
        rv = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{row['id']}/publish",
            json={"publish_mode": mode},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        return rv.get_json()["item"]

    def _mc(self) -> dict[str, Any]:
        return self._add_publish(
            {"type": "mc", "text": "Slope of y = 2x + 1?", "options": ["2", "1", "-2", "0"], "correct_index": 0},
            "group_submit",
        )

    def _send(self, item: dict[str, Any], student_index: int, choice: str) -> None:
        sid = self.ids[student_index]
        self.school.submit_group_mc_answer(
            self.session_id, int(item["id"]), sid, choice=choice, why="because"
        )

    def _student_row(self, student_index: int, item: dict[str, Any]) -> dict[str, Any]:
        payload = self.school.student_live_items_payload(self.session_id, self.ids[student_index])
        rows = [*payload.get("live_items", []), *payload.get("closed_results", [])]
        return next(r for r in rows if int(r["id"]) == int(item["id"]))

    # S3 ------------------------------------------------------------------
    def test_teacher_sees_group_mc_marks_before_close(self) -> None:
        item = self._mc()
        self._send(item, 0, "2")  # Ava's team: right
        self._send(item, 1, "-2")  # Ben's team: wrong
        view = self.school.live_session_item_results(self.session_id, int(item["id"]))
        self.assertEqual(view["answer_key"], "A")
        marks = {row["team_id"]: row.get("correct") for row in view["status_board"]}
        self.assertEqual(sorted(marks.values()), [False, True])
        self.assertEqual(view["correct_count"], 1)
        light = self.school.light_group_results(self.session_id)[str(item["id"])]
        self.assertEqual(
            sorted(team.get("correct") for team in light["teams"]), [False, True]
        )

    def test_students_see_no_correct_while_open(self) -> None:
        item = self._mc()
        self._send(item, 0, "2")
        self.assertNotIn('"correct"', json.dumps(self._student_row(0, item)))

    def test_closed_reveal_marks_own_row_only_with_results_on(self) -> None:
        item = self._mc()
        self._send(item, 0, "2")
        self._send(item, 1, "-2")
        self.school.close_live_session_item(self.session_id, int(item["id"]))
        self.school.update_live_session_item_settings(
            self.session_id, int(item["id"]), show_live_results=True
        )
        row = self._student_row(0, item)
        reveal = row["results"]["reveal"]
        with_mark = [r for r in reveal if "correct" in r]
        self.assertEqual(len(with_mark), 1)
        self.assertTrue(with_mark[0]["correct"])
        self.school.update_live_session_item_settings(
            self.session_id, int(item["id"]), show_live_results=False
        )
        hidden = self._student_row(0, item)
        self.assertNotIn('"correct"', json.dumps((hidden.get("results") or {}).get("reveal") or []))

    def test_individual_tally_strips_correct_for_students_until_close(self) -> None:
        item = self._add_publish(
            {"type": "mc", "text": "Pick 2", "options": ["2", "3", "4", "5"], "correct_index": 0},
            "individual",
        )
        self.school.update_live_session_item_settings(
            self.session_id, int(item["id"]), show_live_results=True
        )
        prompt = self.school._prompt_for_live_item(item)
        self.school.submit_live_prompt_response(int(prompt["id"]), self.ids[0], {"choice": "A"})
        open_row = self._student_row(0, item)
        for choice in (open_row.get("results") or {}).get("choices") or []:
            self.assertNotIn("correct", choice)
        teacher = self.school.live_session_item_results(self.session_id, int(item["id"]))
        tally = teacher.get("tally") or teacher
        self.assertTrue(any(c.get("correct") for c in tally.get("choices") or []))

    def test_poll_and_rank_have_no_key(self) -> None:
        """Only keyed MC gets right/wrong marks."""

        poll = self._add_publish(
            {"type": "poll", "text": "Favourite?", "options": ["Red", "Blue"]}, "individual"
        )
        self.assertEqual(self.school._mc_key_letter(poll), "")
        self.assertIsNone(self.school._group_mc_answer_correct(poll, "Red"))
        rank = self._add_publish(
            {"type": "rank", "text": "Order", "options": ["a", "b", "c"]}, "group_submit"
        )
        self.assertEqual(self.school._mc_key_letter(rank), "")
        view = self.school.live_session_item_results(self.session_id, int(rank["id"]))
        self.assertNotIn("answer_key", view)

    def test_teacher_js_marks_and_hide_key(self) -> None:
        staff = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        mark = staff.split("function answerMarkHtml")[1].split("\n}\n")[0]
        self.assertIn("✓", mark)
        self.assertIn("✗", mark)
        self.assertIn("MARK_COPY.correct", mark)
        self.assertIn("MARK_COPY.incorrect", mark)
        self.assertIn('summary: "{k} of {n} groups correct"', staff)
        self.assertIn('hideKey: "Hide key"', staff)
        self.assertIn("is-key-hidden", staff)
        html = (LMS_DIR / "templates" / "staff" / "course.html").read_text(encoding="utf-8")
        self.assertIn('id="live-hide-key-btn"', html)
        self.assertIn('aria-pressed="false"', html)

    # S5 ------------------------------------------------------------------
    def _open(self) -> dict[str, Any]:
        # Add New has no open type; numeric runs the same consensus flow
        # ("everyone writes first, then the group sends one").
        return self._add_publish(
            {"type": "numeric", "text": "What is the slope?", "correct_answer": "2"},
            "group_consensus",
        )

    def _vote(self, item: dict[str, Any], student_index: int, value: Any) -> dict[str, Any]:
        return self.school.submit_group_consensus_vote(
            self.session_id, int(item["id"]), self.ids[student_index], {"value": value}
        )

    def test_still_writing_names_until_everyone_answers(self) -> None:
        item = self._open()
        team = self._vote(item, 0, 2)["team"]  # Ava; Cy still writing
        self.assertEqual(team["status"], "collecting_votes")
        self.assertEqual(team["waiting_names"], ["Cy"])
        self.assertEqual(team["waiting_count"], 1)
        self.assertFalse(team["can_finalize"])
        # An early send does not finalize.
        try:
            self.school.finalize_group_consensus_answer(
                self.session_id, int(item["id"]), self.ids[0], {"value": 3}
            )
        except (ValueError, KeyError):
            pass
        state = self.school.student_group_consensus_state(
            self.school.get_live_session_item(self.session_id, int(item["id"])), self.ids[0]
        )
        self.assertNotEqual(state["status"], "finalized")
        done = self._vote(item, 2, 2)["team"]  # Cy
        self.assertIn(done["status"], {"discussion", "awaiting_team_answer"})
        self.assertNotIn("waiting_names", done)
        self.assertTrue(done["can_finalize"])

    def test_student_js_uses_wonder_copy(self) -> None:
        student = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        consensus = student.split("function lifecycleConsensusHtml")[1].split("\nfunction ")[0]
        self.assertIn("consensusWaitHtml(group", consensus)
        self.assertIn("GROUP_INSTRUCTION_COPY.ready", consensus)
        self.assertNotIn("teammates have responded", consensus)
        self.assertIn("groupInstructionHtml(item, content)", student)

    def test_group_instructions_node_harness(self) -> None:
        proc = subprocess.run(
            ["node", str(LMS_DIR / "static" / "group_instructions.test.mjs")],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("ok", proc.stdout)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""MCK-154 S2: a question added to a live deck mid-class reaches students.

Real deck metadata (MCR3U M1 C2), real routes: Add New -> Publish -> the
question is in the student's live list and answerable. Also pins the root
cause: removing the newest question freed its lifecycle row id, the next
Add New reused it, and its prompt slot (``20000 + id``) still held the
removed question's prompt and answers, so students saw the new card as
already submitted.
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


class LiveAddedQuestionTests(unittest.TestCase):
    """Add New while a class is live, against an isolated database."""

    def setUp(self) -> None:
        """One teacher, four students joined, live on M1 C2 round."""

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
        self.client.get(
            "/auth/google/callback?email=teacher@gmail.com&name=T"
        )
        code = self.school.get_user_by_email("teacher@gmail.com")[
            "verification_code"
        ]
        self.client.post("/verify-email", data={"code": code})
        self.session_id = int(
            self.school.start_live_class_session(
                self.class_id, int(teacher["id"])
            )["id"]
        )
        rv = self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={
                "live_module": "M1",
                "live_slot": "C2",
                "stage": "round",
                "page_id": "round_1",
            },
        )
        self.assertLess(rv.status_code, 400)
        self.school.game.begin_game(self.class_id)
        with self.school.game._lock:
            self.students = [
                dict(row)
                for row in self.school.game.conn.execute(
                    "SELECT * FROM students WHERE class_id = ? ORDER BY id",
                    (self.class_id,),
                ).fetchall()
            ]
        for student in self.students:
            self.school.join_live_class_session(
                self.session_id,
                int(student["id"]),
                codename=str(student["codename"]),
            )
        self.school.ensure_live_session_items(self.session_id)

    def tearDown(self) -> None:
        """Close sqlite and remove the temp directory."""

        logging.disable(logging.NOTSET)
        self.school.close()
        self.tmp.cleanup()

    def _add(self, text: str, kind: str = "rank") -> tuple[str, dict[str, Any]]:
        """Add New through the teacher route; return item id + lifecycle row."""

        body: dict[str, Any] = {
            "type": kind,
            "text": text,
            "page_number": 4,
            "stage": "round",
            "options": ["Slope", "Intercept", "Domain"],
        }
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/add-question",
            json=body,
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        item = rv.get_json()["placement"]["item"]
        row = next(
            r
            for r in self.school.list_live_session_items(self.session_id)
            if r["item_id"] == item["id"]
        )
        return str(item["id"]), row

    def _publish(self, row: dict[str, Any], mode: str = "individual") -> dict[str, Any]:
        """Publish through the teacher route."""

        rv = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{row['id']}/publish",
            json={"publish_mode": mode},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        return rv.get_json()["item"]

    def _student_row(self, student_index: int, item_id: str) -> dict[str, Any] | None:
        """The student's live row for ``item_id``, or None."""

        payload = self.school.student_live_items_payload(
            self.session_id, int(self.students[student_index]["id"])
        )
        return next(
            (r for r in payload["live_items"] if r["item_id"] == item_id), None
        )

    def test_live_added_rank_reaches_students(self) -> None:
        """Add New (rank) -> Publish -> in every student's list, answerable."""

        item_id, row = self._add("Order these by steepness")
        published = self._publish(row)
        self.assertEqual(published["status"], "active")
        prompt = self.school._prompt_for_live_item(published)
        self.assertIsNotNone(prompt)
        self.assertEqual(prompt["payload"].get("item_id"), item_id)
        for index in range(len(self.students)):
            seen = self._student_row(index, item_id)
            self.assertIsNotNone(seen, f"student {index} missing the question")
            self.assertEqual(seen["status"], "active")
            self.assertFalse(seen.get("my_response"))
            labels = [o.get("label") for o in seen["content"].get("rank_options") or []]
            self.assertEqual(labels, ["Slope", "Intercept", "Domain"])

    def test_live_added_rank_reaches_students_in_group_mode(self) -> None:
        """Same flow published to groups."""

        ids = [int(s["id"]) for s in self.students]
        self.school.setup_live_session_groups(
            self.session_id,
            n_teams=2,
            mode="manual",
            present_ids=ids,
            assignments=[
                {"student_id": sid, "team_index": i % 2}
                for i, sid in enumerate(ids)
            ],
        )
        item_id, row = self._add("Group order")
        published = self._publish(row, "group_submit")
        self.assertEqual(published["response_mode"], "group_submit")
        for index in range(len(self.students)):
            seen = self._student_row(index, item_id)
            self.assertIsNotNone(seen, f"student {index} missing the group question")
            self.assertEqual(seen["status"], "active")
            self.assertTrue(seen["content"].get("rank_options"))

    def test_re_added_question_does_not_inherit_removed_answers(self) -> None:
        """Remove the newest question, add another: fresh prompt, no answers."""

        first_id, first_row = self._add("First rank")
        first = self._publish(first_row)
        first_prompt = self.school._prompt_for_live_item(first)
        student_id = int(self.students[0]["id"])
        self.school.submit_live_prompt_response(
            int(first_prompt["id"]),
            student_id,
            {"order": [row["id"] for row in first_prompt["payload"]["rank_options"]]},
        )
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/playlist-item",
            json={"item_id": first_id, "action": "remove"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        second_id, second_row = self._add("Second rank")
        # The bug needs the freed row id to be reused; pin that it is.
        self.assertEqual(int(second_row["id"]), int(first_row["id"]))
        second = self._publish(second_row)
        second_prompt = self.school._prompt_for_live_item(second)
        self.assertNotEqual(int(second_prompt["id"]), int(first_prompt["id"]))
        self.assertEqual(second_prompt["payload"].get("item_id"), second_id)
        seen = self._student_row(0, second_id)
        self.assertIsNotNone(seen)
        self.assertEqual(seen["status"], "active")
        self.assertFalse(seen.get("my_response"), "inherited a removed answer")
        results = self.school.live_session_item_results(
            self.session_id, int(second["id"])
        )
        self.assertEqual(int(results.get("response_count") or 0), 0)

    def test_ensure_again_keeps_its_own_prompt(self) -> None:
        """Re-ensuring a published item keeps its prompt (no fork)."""

        _item_id, row = self._add("Keep me")
        first = self._publish(row)
        prompt = self.school._prompt_for_live_item(first)
        self.school.ensure_live_session_items(self.session_id)
        fresh = next(
            r
            for r in self.school.list_live_session_items(self.session_id)
            if int(r["id"]) == int(row["id"])
        )
        again = self.school._ensure_prompt_for_live_item(fresh)
        self.assertEqual(int(again["id"]), int(prompt["id"]))
        self.assertEqual(int(again["slide_index"]), 20000 + int(row["id"]))

    def test_new_marker_is_teacher_only(self) -> None:
        """Added mid-class carries the session id; students never get it."""

        item_id, row = self._add("Marked")
        self.assertEqual(
            int(row["item"].get("added_live_session_id") or 0), self.session_id
        )
        self._publish(row)
        payload = self.school.student_live_items_payload(
            self.session_id, int(self.students[0]["id"])
        )
        self.assertNotIn("added_live_session_id", json.dumps(payload))
        seen = self._student_row(0, item_id)
        self.assertIsNotNone(seen)

    def test_teacher_card_renders_new_chip_only_before_publish(self) -> None:
        """The chip is gated on this session and the inactive status."""

        staff = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        gate = staff.split("function liveQuestionAddedThisClass")[1].split("\n}\n")[0]
        self.assertIn("added_live_session_id", gate)
        self.assertIn('status === "inactive"', gate)
        self.assertIn('<span class="live-question-new-chip">New</span>', staff)
        student = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertNotIn("added_live_session_id", student)
        self.assertNotIn("live-question-new-chip", student)


class RankStackNodeHarness(unittest.TestCase):
    """MCK-154 S1: run the rank_stack.js node checks."""

    def test_rank_stack_node_harness(self) -> None:
        """``node static/rank_stack.test.mjs`` prints ok."""

        proc = subprocess.run(
            ["node", str(LMS_DIR / "static" / "rank_stack.test.mjs")],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("ok", proc.stdout)


if __name__ == "__main__":
    unittest.main()

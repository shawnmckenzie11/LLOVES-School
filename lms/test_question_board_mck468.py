#!/usr/bin/env python3
"""MCK-468: submit docking, group gate, catch-up credit, feedback, submissions.

Synthetic roster labels only. Assertions do not print names, emails, or join codes.
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

from app import create_app  # noqa: E402
from participation_weight import participation_fraction  # noqa: E402


def _stroke(stroke_id: str, salt: int = 0) -> list[dict]:
    """One finished stroke inside the unit square.

    Args:
        stroke_id: Client stroke id.
        salt: Shifts the polyline so two strokes are not identical.
    """
    points = []
    for i in range(4):
        points.append([round(((i * 3 + salt) % 90) / 100, 3), round(((i * 5 + salt) % 90) / 100, 3)])
    return [
        {"type": "stroke_add", "id": stroke_id, "points": points},
        {"type": "stroke_end", "id": stroke_id},
    ]


class QuestionBoardFollowupTests(unittest.TestCase):
    """The five follow-ups on the existing question-board path."""

    def setUp(self) -> None:
        """One staff fixture, three synthetic students, a test live class."""
        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
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
                "codenames": ["S1", "S2", "S3"],
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
        for codename in ("S1", "S2", "S3"):
            row = self.school.game.find_student_by_codename(self.class_id, codename)
            self.assertIsNotNone(row)
            self.ids.append(int(row["id"]))
            self.school.join_live_class_session(self.session_id, int(row["id"]), codename=codename)
        self.school.setup_live_session_groups(
            self.session_id,
            n_teams=2,
            mode="manual",
            present_ids=self.ids,
            assignments=[
                {"student_id": self.ids[0], "team_index": 0},
                {"student_id": self.ids[1], "team_index": 0},
                {"student_id": self.ids[2], "team_index": 1},
            ],
        )
        self.school.set_live_session_teacher_state(
            self.session_id, groups_configured=True, run_as_group=True
        )

    def tearDown(self) -> None:
        """Close the temp database."""
        logging.disable(logging.NOTSET)
        self.school.close()
        self.tmp.cleanup()

    def _add(self, text: str, *, group: bool = False) -> tuple[int, int]:
        """Publish a board poll and return ``(prompt id, live item id)``.

        Args:
            text: Stem.
            group: One shared team board.
        """
        body = {
            "type": "poll",
            "text": text,
            "page_number": 1,
            "stage": "round",
            "answer_with": "board",
            "save_to_bank": False,
        }
        if group:
            body["live_response_mode"] = "group"
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/add-question",
            json=body,
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
            self.session_id,
            int(item["id"]),
            publish_mode="group_shared" if group else "individual",
        )
        self.assertIsNotNone(published.get("prompt_id"))
        return int(published["prompt_id"]), int(item["id"])

    def _cell(self, student_id: int) -> dict:
        """Participation cell for one student in M1."""
        data = self.school.class_participation(self.class_id, include_test_students=True)
        match = next(row for row in data["students"] if row["student_id"] == student_id)
        return match["modules"]["M1"]

    def test_group_submit_stays_shut_until_each_member_or_a_skip(self) -> None:
        """One teammate's ink does not unlock Submit. Skip does."""
        prompt_id, item_id = self._add("Board gate", group=True)
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[0], _stroke("only-one")
        )
        with self.assertRaises(Exception) as caught:
            self.school.submit_question_board(self.session_id, prompt_id, self.ids[0], "")
        self.assertEqual(getattr(caught.exception, "status", 0), 409)
        phone = self.school.read_student_question_board(self.session_id, prompt_id, self.ids[0])
        self.assertFalse(phone["submit_ready"])
        dash = self.school.question_dashboard(self.session_id, prompt_id)
        waiting_ids = [
            int(row["student_id"])
            for tile in dash["tiles"]
            for row in tile.get("question_waits") or []
            if not row.get("skipped")
        ]
        self.assertIn(self.ids[1], waiting_ids)
        skipped = self.school.skip_question_for_student(self.session_id, item_id, self.ids[1])
        self.assertFalse(skipped["repeated"])
        phone = self.school.read_student_question_board(self.session_id, prompt_id, self.ids[0])
        self.assertTrue(phone["submit_ready"])
        sent = self.school.submit_question_board(self.session_id, prompt_id, self.ids[0], "")
        self.assertTrue(sent["ok"])
        self.assertTrue(sent["group"])

    def test_catchup_board_records_one_of_one(self) -> None:
        """A ~cu board submit is 1/1 once the question has a live answer.

        The stored prompt kind is share, so the deck used to give the
        question weight 0 and the catch-up row never entered the fraction.
        """
        prompt_id, _item_id = self._add("Board credit")
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[0], _stroke("live")
        )
        self.school.submit_question_board(self.session_id, prompt_id, self.ids[0], "")
        self.school.end_live_class_session(self.session_id, record_participation=True)
        with self.school._lock:
            live = self.school.conn.execute(
                """
                SELECT question_key, answer_json FROM participation_answers
                WHERE class_id = ? AND student_id = ? AND source = 'live'
                """,
                (self.class_id, self.ids[0]),
            ).fetchone()
            part = self.school.conn.execute(
                """
                SELECT id FROM participation_items
                WHERE class_id = ? AND question_key = ?
                """,
                (self.class_id, live["question_key"]),
            ).fetchone()
            link = self.school.conn.execute(
                "SELECT token, run_key FROM catchup_links WHERE class_id = ?",
                (self.class_id,),
            ).fetchone()
        with self.school._lock:
            self.school.conn.execute(
                """
                UPDATE participation_runs
                SET is_real = 1, discarded = 0, teacher_kind_set = 1
                WHERE class_id = ? AND run_key = ?
                """,
                (self.class_id, str(link["run_key"])),
            )
            self.school.conn.commit()
        run_key = f"{link['run_key']}~cu~q{int(part['id'])}"
        self.school.boards.append_ops(
            run_key,
            f"solo:{self.ids[1]}",
            owner=str(self.ids[1]),
            ops=_stroke("cu"),
        )
        from catchup_session import establish_catchup_identity

        phone = self.app.test_client()
        stamp = self.school.catchup_pin_stamp(self.class_id, self.ids[1])
        with phone.session_transaction() as sess:
            establish_catchup_identity(sess, self.school, self.class_id, self.ids[1], stamp)
        submitted = phone.post(
            f"/api/catchup/{link['token']}/questions/{int(part['id'])}/board/submit",
            json={"text": ""},
        )
        self.assertEqual(submitted.status_code, 200, submitted.get_json())
        caught = self._cell(self.ids[1])
        self.assertEqual(
            participation_fraction(caught["answered"], caught["offered"]),
            "1/1",
        )
        live_cell = self._cell(self.ids[0])
        self.assertEqual(
            participation_fraction(live_cell["answered"], live_cell["offered"]),
            "1/1",
        )
        with self.school._lock:
            stored = self.school.conn.execute(
                """
                SELECT source FROM participation_answers
                WHERE class_id = ? AND student_id = ? AND question_key = ?
                """,
                (self.class_id, self.ids[1], live["question_key"]),
            ).fetchone()
        self.assertEqual(stored["source"], "catchup")

    def test_teacher_note_sits_on_the_copy_and_the_student_card(self) -> None:
        """Feedback is text on the compact copy, for one student or one team."""
        prompt_id, _item_id = self._add("Board note")
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[0], _stroke("ink")
        )
        self.school.submit_question_board(self.session_id, prompt_id, self.ids[0], "")
        key = f"solo:{self.ids[0]}"
        saved = self.client.post(
            f"/api/live-sessions/{self.session_id}/questions/{prompt_id}/boards/{key}/note",
            json={"text": "Look at the intercept."},
        )
        self.assertEqual(saved.status_code, 200, saved.get_json())
        phone = self.school.read_student_question_board(self.session_id, prompt_id, self.ids[0])
        self.assertEqual(phone["feedback"], "Look at the intercept.")
        from question_board import student_live_feedback

        self.assertEqual(
            student_live_feedback(self.school, self.session_id, prompt_id, self.ids[0]),
            "Look at the intercept.",
        )
        state = self.school.student_live_items_payload(self.session_id, self.ids[0])
        prompt_ids = [
            int((row.get("prompt") or {}).get("id") or 0)
            for row in (state.get("live_items") or [])
        ]
        self.assertIn(prompt_id, prompt_ids)
        cards = [
            row
            for row in state.get("live_items") or []
            if int((row.get("prompt") or {}).get("id") or 0) == prompt_id
        ]
        self.assertEqual(cards[0].get("board_feedback"), "Look at the intercept.")
        other = self.school.read_student_question_board(self.session_id, prompt_id, self.ids[2])
        self.assertEqual(other.get("feedback") or "", "")

    def test_lesson_slides_dashboard_reads_the_copy_and_saves_a_note(self) -> None:
        """Catch-up submissions are the same gallery, and a note sticks."""
        prompt_id, _item_id = self._add("Board archive")
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[0], _stroke("kept")
        )
        self.school.submit_question_board(self.session_id, prompt_id, self.ids[0], "")
        self.school.end_live_class_session(self.session_id, record_participation=True)
        with self.school._lock:
            live = self.school.conn.execute(
                """
                SELECT question_key, answer_json FROM participation_answers
                WHERE class_id = ? AND student_id = ? AND source = 'live'
                """,
                (self.class_id, self.ids[0]),
            ).fetchone()
            part = self.school.conn.execute(
                "SELECT id FROM participation_items WHERE class_id = ? AND question_key = ?",
                (self.class_id, live["question_key"]),
            ).fetchone()
            link = self.school.conn.execute(
                "SELECT token, run_key FROM catchup_links WHERE class_id = ?",
                (self.class_id,),
            ).fetchone()
        run_key = f"{link['run_key']}~cu~q{int(part['id'])}"
        self.school.boards.append_ops(
            run_key,
            f"solo:{self.ids[2]}",
            owner=str(self.ids[2]),
            ops=_stroke("later"),
        )
        from catchup_session import establish_catchup_identity

        phone = self.app.test_client()
        stamp = self.school.catchup_pin_stamp(self.class_id, self.ids[2])
        with phone.session_transaction() as sess:
            establish_catchup_identity(sess, self.school, self.class_id, self.ids[2], stamp)
        submitted = phone.post(
            f"/api/catchup/{link['token']}/questions/{int(part['id'])}/board/submit",
            json={"text": ""},
        )
        self.assertEqual(submitted.status_code, 200, submitted.get_json())
        gallery = self.client.get(
            f"/api/classes/{self.class_id}/lesson-slides/boards"
            f"?run_key={link['run_key']}&question_key={live['question_key']}"
        )
        self.assertEqual(gallery.status_code, 200, gallery.get_json())
        body = gallery.get_json()
        self.assertTrue(body["visible"])
        self.assertTrue(body["read_only"])
        sources = {str(tile.get("source") or "") for tile in body["tiles"]}
        self.assertIn("live", sources)
        self.assertIn("catchup", sources)
        catch = next(tile for tile in body["tiles"] if tile.get("source") == "catchup")
        strokes = (catch.get("canvas_view") or {}).get("strokes") or []
        self.assertTrue(strokes)
        noted = self.client.post(
            f"/api/classes/{self.class_id}/lesson-slides/boards/{catch['board_key']}",
            json={"text": "Finish the last step."},
        )
        self.assertEqual(noted.status_code, 200, noted.get_json())
        again = self.client.get(
            f"/api/classes/{self.class_id}/lesson-slides/boards/{catch['board_key']}"
        )
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.get_json().get("feedback"), "Finish the last step.")

    def test_pages_reuse_the_dock_skip_and_gallery(self) -> None:
        """Submit docks, Skip is the existing button, Lesson Slides mounts the gallery."""
        portal = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        board = (LMS_DIR / "static" / "question_board_ui.js").read_text(encoding="utf-8")
        slides = (LMS_DIR / "static" / "staff_lesson_slides.js").read_text(encoding="utf-8")
        self.assertIn("lloves-question-board-submitted", portal)
        self.assertIn('kind: "question"', portal)
        self.assertIn("boardFeedbackHtml", portal)
        self.assertIn("submit_ready", board)
        self.assertIn("dataset.questionSkip", board)
        self.assertIn("Skip for this question", board)
        self.assertIn("mountSubmissionDashboard", slides)
        self.assertIn("mountTeacherGrid", board)


if __name__ == "__main__":
    unittest.main()

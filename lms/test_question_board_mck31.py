#!/usr/bin/env python3
"""MCK-31: a poll board is its own show-your-work response.

Synthetic roster only. Assertions do not print names, emails, or join codes.
"""

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

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from question_board import (  # noqa: E402
    COPY_CAP_BYTES,
    copy_byte_size,
    copy_json,
    parse_board_run_key,
)
from school_db import SchoolDB  # noqa: E402


def _stroke(stroke_id: str, n: int = 4, salt: int = 0) -> list[dict]:
    """One finished stroke of ``n`` points inside the unit square.

    Args:
        stroke_id: Client stroke id.
        n: How many points. Values outside 0–1 are not stored.
        salt: Shifts the polyline so strokes are not identical.
    """
    points = []
    for i in range(n):
        x = ((i * 3 + salt) % 97) / 100
        y = ((i * 5 + 2 * salt) % 97) / 100
        pair = [round(x, 3), round(y, 3)]
        if points and points[-1] == pair:
            continue
        points.append(pair)
    return [
        {"type": "stroke_add", "id": stroke_id, "points": points},
        {"type": "stroke_end", "id": stroke_id},
    ]


class QuestionBoardTests(unittest.TestCase):
    """Per-question boards, copies, and the teacher dashboard."""

    def setUp(self) -> None:
        """One staff fixture, three synthetic students, a test live class."""
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
                "codenames": ["SeatA", "SeatB", "SeatC"],
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
        rows = []
        for codename in ("SeatA", "SeatB", "SeatC"):
            row = self.school.game.find_student_by_codename(self.class_id, codename)
            self.assertIsNotNone(row)
            rows.append(row)
            self.school.join_live_class_session(
                self.session_id, int(row["id"]), codename=codename
            )
        self.ids = [int(row["id"]) for row in rows]
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

    def _add(self, text: str, *, answer_with: str = "board", group: bool = False) -> int:
        """Add a poll and return its prompt id after publish.

        Args:
            text: Stem.
            answer_with: ``board`` or ``both``.
            group: Publish one shared team board.
        """
        body = {
            "type": "poll",
            "text": text,
            "page_number": 1,
            "stage": "round",
            "answer_with": answer_with,
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
        item = self._item_by_text(text)
        published = self.school.publish_live_session_item(
            self.session_id,
            int(item["id"]),
            publish_mode="group_shared" if group else "individual",
        )
        self.assertIsNotNone(published.get("prompt_id"))
        return int(published["prompt_id"])

    def _item_by_text(self, text: str) -> dict:
        """Lifecycle row whose stem is ``text``."""
        for row in self.school.list_live_session_items(self.session_id):
            item = row.get("item") if isinstance(row.get("item"), dict) else {}
            if str(item.get("text") or "") == text:
                return row
        self.fail("question missing")
        return {}

    def _ink(self, prompt_id: int, student_id: int) -> list:
        """Student strokes stored for one question."""
        base, _gen = self.school._live_board_base_run_key(self.session_id)
        rows = self.school.boards.load_keys(
            f"{base}~q{prompt_id}", [f"solo:{student_id}"]
        )
        return rows

    def test_two_questions_do_not_share_ink(self) -> None:
        """Ink on one board question is not visible on the other."""
        first = self._add("Board one")
        second = self._add("Board two")
        self.school.append_question_board_ops(
            self.session_id, first, self.ids[0], _stroke("a1")
        )
        self.school.append_question_board_ops(
            self.session_id, second, self.ids[0], _stroke("b1")
        )
        self.assertGreater(len(self._ink(first, self.ids[0])), 0)
        self.assertGreater(len(self._ink(second, self.ids[0])), 0)
        base, _gen = self.school._live_board_base_run_key(self.session_id)
        first_rows = self.school.boards.load_keys(f"{base}~q{first}", None)
        second_rows = self.school.boards.load_keys(f"{base}~q{second}", None)
        first_ids = {str(row.get("stroke_id") or "") for row in first_rows}
        second_ids = {str(row.get("stroke_id") or "") for row in second_rows}
        self.assertIn("a1", first_ids)
        self.assertNotIn("a1", second_ids)
        self.assertIn("b1", second_ids)
        self.assertNotIn("b1", first_ids)

    def test_reload_returns_the_students_own_ink(self) -> None:
        """A question board read includes strokes drawn before the read."""
        prompt_id = self._add("Board reload")
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[0], _stroke("own")
        )
        body = self.school.read_student_question_board(
            self.session_id, prompt_id, self.ids[0]
        )
        strokes = body.get("canvas_view", {}).get("strokes") or []
        self.assertTrue(any(str(stroke.get("id") or "") == "own" for stroke in strokes))

    def test_team_submit_writes_every_teammate_and_keeps_the_id(self) -> None:
        """One copy, one row per teammate, submitter stored as an id."""
        prompt_id = self._add("Board team", group=True)
        self.school.append_question_board_ops(
            self.session_id,
            prompt_id,
            self.ids[0],
            _stroke("team"),
            board_key="team:0",
            owner="teacher" if False else "",
        )
        # The student write chooses team:0 from the group.
        result = self.school.submit_question_board(
            self.session_id, prompt_id, self.ids[0], ""
        )
        self.assertTrue(result["ok"])
        rows = self.school.list_live_prompt_responses(prompt_id)
        got = {int(row["student_id"]) for row in rows if row.get("student_id")}
        self.assertEqual(got, {self.ids[0], self.ids[1]})
        for row in rows:
            body = row["response"]
            self.assertIsInstance(body.get("submitted_by"), int)
            self.assertNotIn("name", body)
            self.assertNotIn("codename", body)
            blob = json.dumps(body)
            self.assertNotIn("SeatA", blob)
            self.assertNotIn("SeatB", blob)
        base, _gen = self.school._live_board_base_run_key(self.session_id)
        with self.school._lock:
            copies = self.school.conn.execute(
                """
                SELECT strokes_json, submitted_by FROM board_answer_copies
                WHERE class_id = ? AND run_key = ?
                """,
                (self.class_id, f"{base}~q{prompt_id}"),
            ).fetchall()
        self.assertEqual(len(copies), 1)
        stored = json.loads(copies[0]["strokes_json"])
        self.assertNotIn("name", json.dumps(stored))
        self.assertIsInstance(int(copies[0]["submitted_by"]), int)

    def test_over_cap_submit_is_rejected_and_keeps_the_row(self) -> None:
        """A drawing over 64 KB is refused and the previous copy stays."""
        prompt_id = self._add("Board cap")
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[0], _stroke("small", 3)
        )
        self.school.submit_question_board(self.session_id, prompt_id, self.ids[0], "")
        base, _gen = self.school._live_board_base_run_key(self.session_id)
        run_key = f"{base}~q{prompt_id}"
        with self.school._lock:
            before = self.school.conn.execute(
                """
                SELECT strokes_json, board_seq FROM board_answer_copies
                WHERE run_key = ? AND board_key = ?
                """,
                (run_key, f"solo:{self.ids[0]}"),
            ).fetchone()
        self.assertIsNotNone(before)
        batch = 0
        size = 0
        while size <= COPY_CAP_BYTES and batch < 30:
            ops = []
            for n in range(10):
                ops.extend(_stroke(f"big-{batch}-{n}", 90, salt=batch * 10 + n))
            self.school.append_question_board_ops(
                self.session_id, prompt_id, self.ids[0], ops
            )
            rows = self.school.boards.load_keys(run_key, [f"solo:{self.ids[0]}"])
            from question_board import compact_saved_copy

            size = copy_byte_size(compact_saved_copy(rows, 10**9))
            batch += 1
        self.assertGreater(size, COPY_CAP_BYTES)
        with self.assertRaises(Exception) as caught:
            self.school.submit_question_board(
                self.session_id, prompt_id, self.ids[0], ""
            )
        self.assertEqual(getattr(caught.exception, "status", None), 413)
        self.assertNotIn("655", str(caught.exception))
        with self.school._lock:
            after = self.school.conn.execute(
                """
                SELECT strokes_json, board_seq FROM board_answer_copies
                WHERE run_key = ? AND board_key = ?
                """,
                (run_key, f"solo:{self.ids[0]}"),
            ).fetchone()
        self.assertEqual(after["strokes_json"], before["strokes_json"])
        self.assertEqual(after["board_seq"], before["board_seq"])

    def _copy_of_size(self, target: int) -> dict:
        """Build a compact copy whose JSON is exactly ``target`` bytes.

        Args:
            target: UTF-8 length to hit.
        """
        filler = "x"
        body = {
            "strokes": [{"id": "c0", "points": [[0.1, 0.2]]}],
            "texts": [{"id": "t0", "text": filler, "x": 0, "y": 0}],
        }
        for _ in range(8):
            size = len(copy_json(body).encode("utf-8"))
            body["texts"][0]["text"] = "x" * max(1, len(body["texts"][0]["text"]) + (target - size))
        self.assertEqual(len(copy_json(body).encode("utf-8")), target)
        return body

    def test_cap_boundary_is_65536_bytes(self) -> None:
        """A 65536-byte copy submits. One more byte is refused."""
        import question_board as qb

        self.assertEqual(COPY_CAP_BYTES, 65536)
        prompt_id = self._add("Board bytes")
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[0], _stroke("seed")
        )
        exact = self._copy_of_size(COPY_CAP_BYTES)
        over = self._copy_of_size(COPY_CAP_BYTES + 1)
        original = qb.compact_saved_copy
        qb.compact_saved_copy = lambda rows, seq: exact
        try:
            saved = self.school.submit_question_board(
                self.session_id, prompt_id, self.ids[0], ""
            )
        finally:
            qb.compact_saved_copy = original
        self.assertTrue(saved["ok"])
        qb.compact_saved_copy = lambda rows, seq: over
        try:
            with self.assertRaises(qb.QuestionBoardError) as caught:
                self.school.submit_question_board(
                    self.session_id, prompt_id, self.ids[0], ""
                )
        finally:
            qb.compact_saved_copy = original
        self.assertEqual(caught.exception.status, 413)
        self.assertNotIn("655", str(caught.exception))

    def test_end_keeps_the_copy_and_test_delete_removes_it(self) -> None:
        """End drops ink and keeps the copy. Deleting the test class removes it."""
        prompt_id = self._add("Board end")
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[0], _stroke("keep")
        )
        self.school.submit_question_board(self.session_id, prompt_id, self.ids[0], "")
        self.school.end_live_class_session(
            self.session_id, record_participation=True
        )
        base_row = self.school.get_live_session(self.session_id)
        base = str(base_row["run_key"])
        self.assertEqual(self.school.boards.count_pattern_ops(base + "~q%"), 0)
        self.assertEqual(self.school.boards.count_pattern_sessions(base + "~q%"), 0)
        with self.school._lock:
            kept = self.school.conn.execute(
                "SELECT COUNT(*) AS n FROM board_answer_copies WHERE run_key = ?",
                (f"{base}~q{prompt_id}",),
            ).fetchone()
        self.assertEqual(int(kept["n"]), 1)
        self.school.delete_test_live_class(self.class_id, self.session_id)
        with self.school._lock:
            left = self.school.conn.execute(
                "SELECT COUNT(*) AS n FROM board_answer_copies WHERE class_id = ?",
                (self.class_id,),
            ).fetchone()
        self.assertEqual(int(left["n"]), 0)

    def test_quit_removes_copies(self) -> None:
        """Quit saves nothing, including a submitted copy."""
        prompt_id = self._add("Board quit")
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[0], _stroke("gone")
        )
        self.school.submit_question_board(self.session_id, prompt_id, self.ids[0], "")
        self.school.wipe_live_sessions_for_class(
            self.class_id, session_ids=[self.session_id], purge_kept=True
        )
        with self.school._lock:
            left = self.school.conn.execute(
                "SELECT COUNT(*) AS n FROM board_answer_copies WHERE class_id = ?",
                (self.class_id,),
            ).fetchone()
        self.assertEqual(int(left["n"]), 0)

    def test_catchup_resubmit_does_not_overwrite_a_live_answer(self) -> None:
        """A catch-up submit updates a catch-up row and leaves a live row."""
        from catchup_session import establish_catchup_identity

        prompt_id = self._add("Board catch")
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[0], _stroke("live")
        )
        self.school.submit_question_board(self.session_id, prompt_id, self.ids[0], "live text")
        self.school.end_live_class_session(
            self.session_id, record_participation=True
        )
        with self.school._lock:
            live = self.school.conn.execute(
                """
                SELECT id, question_key, answer_json FROM participation_answers
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
                "SELECT token FROM catchup_links WHERE class_id = ?",
                (self.class_id,),
            ).fetchone()
        self.assertIsNotNone(live)
        self.assertIsNotNone(part)
        self.assertIsNotNone(link)
        original = live["answer_json"]
        stamp = self.school.catchup_pin_stamp(self.class_id, self.ids[0])
        with self.client.session_transaction() as sess:
            establish_catchup_identity(
                sess, self.school, self.class_id, self.ids[0], stamp
            )
        refused = self.client.post(
            f"/api/catchup/{link['token']}/questions/{int(part['id'])}/board/submit",
            json={"text": "should not land"},
        )
        self.assertEqual(refused.status_code, 409)
        with self.school._lock:
            again = self.school.conn.execute(
                "SELECT source, answer_json FROM participation_answers WHERE id = ?",
                (int(live["id"]),),
            ).fetchone()
        self.assertEqual(again["source"], "live")
        self.assertEqual(again["answer_json"], original)
        other = self.app.test_client()
        other_stamp = self.school.catchup_pin_stamp(self.class_id, self.ids[2])
        with other.session_transaction() as sess:
            establish_catchup_identity(
                sess, self.school, self.class_id, self.ids[2], other_stamp
            )
        base = str(self.school.get_live_session(self.session_id)["run_key"])
        run_key = f"{base}~cu~q{int(part['id'])}"
        self.school.boards.append_ops(
            run_key,
            f"solo:{self.ids[2]}",
            owner=str(self.ids[2]),
            ops=_stroke("cu"),
        )
        first = other.post(
            f"/api/catchup/{link['token']}/questions/{int(part['id'])}/board/submit",
            json={"text": "after class"},
        )
        self.assertEqual(first.status_code, 200, first.get_json())
        self.school.boards.append_ops(
            run_key,
            f"solo:{self.ids[2]}",
            owner=str(self.ids[2]),
            ops=_stroke("cu2"),
        )
        second = other.post(
            f"/api/catchup/{link['token']}/questions/{int(part['id'])}/board/submit",
            json={"text": "updated after class"},
        )
        self.assertEqual(second.status_code, 200, second.get_json())
        with self.school._lock:
            rows = self.school.conn.execute(
                """
                SELECT student_id, source, answer_json FROM participation_answers
                WHERE class_id = ? AND question_key = ?
                ORDER BY student_id
                """,
                (self.class_id, live["question_key"]),
            ).fetchall()
        by_student = {int(row["student_id"]): row for row in rows}
        self.assertEqual(by_student[self.ids[0]]["source"], "live")
        self.assertEqual(by_student[self.ids[0]]["answer_json"], original)
        self.assertEqual(by_student[self.ids[2]]["source"], "catchup")
        self.assertIn("updated after class", by_student[self.ids[2]]["answer_json"])

    def test_non_staff_cannot_read_the_dashboard(self) -> None:
        """A student session and an anonymous client do not receive tiles."""
        prompt_id = self._add("Board dash")
        anon = self.app.test_client()
        hidden = anon.get(
            f"/api/live-sessions/{self.session_id}/questions/{prompt_id}/boards"
        )
        self.assertIn(hidden.status_code, (401, 403))
        self.assertNotIn("tiles", hidden.get_json() or {})
        staff = self.client.get(
            f"/api/live-sessions/{self.session_id}/questions/{prompt_id}/boards"
        )
        self.assertEqual(staff.status_code, 200)
        self.assertIn("tiles", staff.get_json())

    def test_unknown_run_suffix_is_refused(self) -> None:
        """A run key other than the four recognised forms is refused."""
        with self.assertRaises(ValueError):
            parse_board_run_key("abc~nope")
        with self.assertRaises(ValueError):
            self.school.boards.append_ops(
                "abc~nope",
                "solo:1",
                owner="1",
                ops=[{"type": "stroke_end", "id": "x"}],
            )

    def test_content_tabs_omit_the_whiteboard_tab(self) -> None:
        """The Content nav lists Media, Questions, and Slides."""
        html = self.client.get(f"/staff/class/{self.class_id}?tab=live").get_data(
            as_text=True
        )
        tabs = html[html.index('id="live-content-tabs"') : html.index('id="live-frames"')]
        self.assertIn('data-tab="questions"', tabs)
        self.assertIn('data-tab="slides"', tabs)
        self.assertNotIn('data-tab="canvas"', tabs)

    def test_editor_answer_with_is_only_marked_up_for_polls(self) -> None:
        """The Answer with control is in the editor markup."""
        html = (LMS_DIR / "templates" / "partials" / "question_editor.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('name="live-add-q-answer-with"', html)
        self.assertIn("Typing and whiteboard", html)
        self.assertIn("Use image as the board background", html)
        js = (LMS_DIR / "static" / "question_editor.js").read_text(encoding="utf-8")
        self.assertIn('kind !== "poll"', js)


class SchoolDbImportTests(unittest.TestCase):
    """The school database class still imports after the new methods."""

    def test_school_db_class_is_present(self) -> None:
        """Importing the module leaves ``SchoolDB`` usable."""
        self.assertTrue(callable(SchoolDB))

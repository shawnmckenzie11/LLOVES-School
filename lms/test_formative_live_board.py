#!/usr/bin/env python3
"""MCK-270 per-student formative boards.

Strokes stay in board_ops. Done and release stay in live_board_feedback.
These tests cover the data rules: spoofed ids, hidden feedback, test-class
delete, and names blanked on the server.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from board_ops import MAX_OPS_PER_BATCH  # noqa: E402
from live_canvas import MAX_POINTS, MAX_STROKES  # noqa: E402


class FormativeLimitTests(unittest.TestCase):
    """Caps the live board already uses stay put."""

    def test_point_stroke_and_batch_caps(self) -> None:
        """MAX_POINTS, MAX_STROKES, and MAX_OPS_PER_BATCH are unchanged."""
        self.assertEqual(MAX_POINTS, 360)
        self.assertEqual(MAX_STROKES, 80)
        self.assertEqual(MAX_OPS_PER_BATCH, 32)

    def test_client_module_parses(self) -> None:
        """The grid script is valid JavaScript."""
        completed = subprocess.run(
            ["node", "--check", "lms/static/formative_live_board.js"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)


class FormativeLiveBoardTests(unittest.TestCase):
    """Teacher grid and the signed-in student's own board."""

    def setUp(self) -> None:
        """One live class with Maple, Cedar, and Pine joined."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
            live_database_url="",
        )
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.staff = self.app.test_client()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        self.staff.get("/auth/google?portal=staff")
        self.staff.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.staff.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )
        created = self.staff.post(
            "/api/staff/classes",
            json={
                "offering_id": int(offering["id"]),
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple", "Cedar", "Pine"],
            },
        )
        self.class_id = int(created.get_json()["class"]["id"])
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])
        self.session_code = str(live.get("session_code") or "")
        maple = self.school.game.find_student_by_codename(self.class_id, "Maple")
        cedar = self.school.game.find_student_by_codename(self.class_id, "Cedar")
        pine = self.school.game.find_student_by_codename(self.class_id, "Pine")
        assert maple is not None and cedar is not None and pine is not None
        self.maple_id = int(maple["id"])
        self.cedar_id = int(cedar["id"])
        self.pine_id = int(pine["id"])
        self.school.game.begin_game(self.class_id)
        for row in (maple, cedar, pine):
            self.school.join_live_class_session(
                self.session_id, int(row["id"]), codename=str(row["codename"])
            )
        self.school.setup_live_session_groups(
            self.session_id,
            n_teams=2,
            mode="manual",
            present_ids=[self.maple_id, self.cedar_id, self.pine_id],
            assignments=[
                {"student_id": self.maple_id, "team_index": 0},
                {"student_id": self.cedar_id, "team_index": 0},
                {"student_id": self.pine_id, "team_index": 1},
            ],
        )
        self.maple = self.app.test_client()
        self.cedar_client = self.app.test_client()
        self.maple_token = self._visit_token(self.maple_id)
        self.cedar_token = self._visit_token(self.cedar_id)

    def tearDown(self) -> None:
        """Close sqlite and remove the temp dir."""
        db = self.app.config.get("SCHOOL_DB")
        if db is not None:
            db.close()
        self.tmp.cleanup()

    def _visit_token(self, student_id: int) -> str:
        """Return the attendee visit token for one roster id.

        Args:
            student_id: Roster id.
        """
        row = self.school.conn.execute(
            """
            SELECT visit_token FROM live_session_attendees
            WHERE live_session_id = ? AND student_id = ?
            """,
            (self.session_id, int(student_id)),
        ).fetchone()
        return str(row["visit_token"] if row else "")

    def _student_headers(self, token: str) -> dict[str, str]:
        """Header map for one joined student. The token is not logged.

        Args:
            token: Visit token for that attendee.
        """
        return {"X-Student-Visit-Token": token}

    def _text_op(self, text: str, op_id: str) -> dict[str, object]:
        """One text label op with no name and no secret fields.

        Args:
            text: Label body.
            op_id: Client id.
        """
        return {
            "type": "text_upsert",
            "id": op_id,
            "text": text,
            "x": 0.2,
            "y": 0.3,
            "color": "#2563eb",
        }

    def _participation_rows(self) -> int:
        """Count gradebook participation rows. Boards must not add any."""
        total = 0
        for table in (
            "participation_answers",
            "participation_items",
            "participation_module_marks",
        ):
            row = self.school.conn.execute(
                f"SELECT COUNT(*) AS n FROM {table}"
            ).fetchone()
            total += int(row["n"] or 0)
        return total

    def test_student_id_in_the_query_is_ignored(self) -> None:
        """A caller cannot read or write another student's board by id."""
        cedar_ink = self.cedar_client.post(
            "/api/student/formative-board/ops",
            json={
                "student_id": self.maple_id,
                "ops": [self._text_op("cedar-secret-ink", "cedar-ink")],
            },
            headers=self._student_headers(self.cedar_token),
        )
        self.assertEqual(cedar_ink.status_code, 200, cedar_ink.get_data(as_text=True))
        spoofed = self.maple.get(
            f"/api/student/formative-board?student_id={self.cedar_id}",
            headers=self._student_headers(self.maple_token),
        )
        self.assertEqual(spoofed.status_code, 200, spoofed.get_data(as_text=True))
        body = spoofed.get_json()
        self.assertEqual(body["student_id"], self.maple_id)
        self.assertEqual(body["board_key"], f"student:{self.maple_id}")
        self.assertNotIn("cedar-secret-ink", json.dumps(body))
        maple_ink = self.maple.post(
            "/api/student/formative-board/ops",
            json={
                "student_id": self.cedar_id,
                "ops": [self._text_op("maple-only-ink", "maple-ink")],
            },
            headers=self._student_headers(self.maple_token),
        )
        self.assertEqual(maple_ink.status_code, 200, maple_ink.get_data(as_text=True))
        self.assertIn("maple-only-ink", json.dumps(maple_ink.get_json()))
        cedar_view = self.cedar_client.get(
            "/api/student/formative-board",
            headers=self._student_headers(self.cedar_token),
        )
        self.assertNotIn("maple-only-ink", json.dumps(cedar_view.get_json()))
        for key in (f"fb:{self.cedar_id}", f"student:{self.cedar_id}"):
            denied = self.maple.get(
                f"/api/student/board/{key}",
                headers=self._student_headers(self.maple_token),
            )
            self.assertEqual(denied.status_code, 403, key)

    def test_feedback_stays_hidden_until_release(self) -> None:
        """Send feedback is what makes teacher ink visible to that student."""
        posted = self.staff.post(
            f"/api/live-sessions/{self.session_id}/formative-boards/{self.maple_id}/feedback",
            json={"ops": [self._text_op("check vertex", "fb-vertex")]},
        )
        self.assertEqual(posted.status_code, 200, posted.get_data(as_text=True))
        hidden = self.maple.get(
            "/api/student/formative-board",
            headers=self._student_headers(self.maple_token),
        )
        hidden_body = hidden.get_json()
        self.assertNotIn("feedback", hidden_body)
        self.assertNotIn("check vertex", json.dumps(hidden_body))
        released = self.staff.post(
            f"/api/live-sessions/{self.session_id}/formative-boards/{self.maple_id}/release"
        )
        self.assertEqual(released.status_code, 200, released.get_data(as_text=True))
        shown = self.maple.get(
            "/api/student/formative-board",
            headers=self._student_headers(self.maple_token),
        )
        shown_body = shown.get_json()
        self.assertIn("check vertex", json.dumps(shown_body.get("feedback")))

    def test_done_does_not_write_the_gradebook(self) -> None:
        """Done sets submitted_at and leaves participation rows alone."""
        before = self._participation_rows()
        done = self.maple.post(
            "/api/student/formative-board/done",
            headers=self._student_headers(self.maple_token),
        )
        self.assertEqual(done.status_code, 200, done.get_data(as_text=True))
        self.assertEqual(self._participation_rows(), before)
        row = self.school.conn.execute(
            """
            SELECT submitted_at FROM live_board_feedback
            WHERE live_session_id = ? AND student_id = ?
            """,
            (self.session_id, self.maple_id),
        ).fetchone()
        self.assertTrue(str(row["submitted_at"] or "").strip())

    def test_names_are_blanked_on_the_server(self) -> None:
        """Tiles and Show on board use the attendee codename, or nothing."""
        self.school.conn.execute(
            """
            UPDATE students
            SET first_name = ?, last_display = ?
            WHERE id = ?
            """,
            ("Secretfirst", "NotThis", self.maple_id),
        )
        self.school.conn.commit()
        hidden = self.staff.get(
            f"/api/live-sessions/{self.session_id}/formative-boards"
        )
        self.assertEqual(hidden.status_code, 200, hidden.get_data(as_text=True))
        hidden_body = hidden.get_json()
        maple = next(
            tile
            for tile in hidden_body["tiles"]
            if int(tile["student_id"]) == self.maple_id
        )
        self.assertEqual(maple["codename"], "")
        hidden_blob = json.dumps(hidden_body)
        self.assertNotIn("Secretfirst", hidden_blob)
        self.assertNotIn("NotThis", hidden_blob)
        self.assertNotIn("Maple", hidden_blob)
        if self.session_code:
            self.assertNotIn(self.session_code, hidden_blob)
        self.school.set_live_session_teacher_state(
            self.session_id, whiteboard_always_show_names=True
        )
        named = self.staff.get(
            f"/api/live-sessions/{self.session_id}/formative-boards"
        ).get_json()
        maple_named = next(
            tile
            for tile in named["tiles"]
            if int(tile["student_id"]) == self.maple_id
        )
        self.assertEqual(maple_named["codename"], "Maple")
        named_blob = json.dumps(named)
        self.assertNotIn("Secretfirst", named_blob)
        self.assertNotIn("NotThis", named_blob)
        self.school.set_live_session_teacher_state(
            self.session_id, whiteboard_projector_names=False
        )
        off = self.staff.post(
            f"/api/live-sessions/{self.session_id}/formative-boards/{self.maple_id}/show"
        )
        self.assertEqual(off.status_code, 200, off.get_data(as_text=True))
        self.assertEqual(off.get_json()["codename"], "")
        self.school.set_live_session_teacher_state(
            self.session_id, whiteboard_projector_names=True
        )
        on = self.staff.post(
            f"/api/live-sessions/{self.session_id}/formative-boards/{self.maple_id}/show"
        ).get_json()
        self.assertEqual(on["codename"], "Maple")
        self.assertNotIn("first_name", on)
        self.assertNotIn("session_code", on)
        if self.session_code:
            self.assertNotIn(self.session_code, json.dumps(on))

    def test_tile_poll_and_show_are_teacher_only(self) -> None:
        """A joined student cannot poll the grid or project a board."""
        tiles = self.maple.get(
            f"/api/live-sessions/{self.session_id}/formative-boards",
            headers=self._student_headers(self.maple_token),
        )
        show = self.maple.post(
            f"/api/live-sessions/{self.session_id}/formative-boards/{self.cedar_id}/show",
            headers=self._student_headers(self.maple_token),
        )
        self.assertIn(tiles.status_code, (401, 403))
        self.assertIn(show.status_code, (401, 403))

    def test_tile_poll_reads_every_board_in_one_query(self) -> None:
        """The grid delta is one since_many call and one UNION statement."""
        calls = {"fn": 0, "sql": 0}
        original = self.school.boards.since_many
        real_exec = self.school.boards._exec

        def counting(sql: str, params: tuple = ()) -> object:
            """Count the combined delta statement."""
            if "UNION ALL" in str(sql):
                calls["sql"] += 1
            return real_exec(sql, params)

        def wrapped(run_key: str, cursors: dict) -> dict:
            """Count since_many and watch its statements."""
            calls["fn"] += 1
            self.school.boards._exec = counting
            try:
                return original(run_key, cursors)
            finally:
                self.school.boards._exec = real_exec

        self.school.boards.since_many = wrapped
        try:
            response = self.staff.get(
                f"/api/live-sessions/{self.session_id}/formative-boards"
            )
        finally:
            self.school.boards.since_many = original
            self.school.boards._exec = real_exec
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(calls["fn"], 1)
        self.assertEqual(calls["sql"], 1)
        self.assertEqual(len(response.get_json()["tiles"]), 3)

    def test_test_class_delete_removes_feedback_and_ops(self) -> None:
        """Delete drops live_board_feedback and counts leftover board ops."""
        self.maple.post(
            "/api/student/formative-board/ops",
            json={"ops": [self._text_op("keep-until-delete", "del-ink")]},
            headers=self._student_headers(self.maple_token),
        )
        self.school.mark_formative_done(self.session_id, self.maple_id)
        self.school.release_formative_feedback(self.session_id, self.maple_id)
        run_key = self.school.live_board_run_key(self.session_id)
        self.assertGreater(self.school.boards.count_run_ops(run_key), 0)
        with self.assertRaises(RuntimeError):
            self.school._assert_test_session_gone_unlocked(
                self.session_id, run_key, set()
            )
        with self.assertRaises(KeyError):
            self.school.delete_test_live_class(self.class_id + 99, self.session_id)
        deleted = self.school.delete_test_live_class(self.class_id, self.session_id)
        self.assertTrue(deleted["ok"])
        left = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM live_board_feedback WHERE live_session_id = ?",
            (self.session_id,),
        ).fetchone()
        self.assertEqual(int(left["n"] or 0), 0)
        self.assertEqual(self.school.boards.count_run_ops(run_key), 0)

    def test_cleanup_removes_feedback_rows(self) -> None:
        """End-of-session cleanup deletes the new table with the board ops."""
        self.school.mark_formative_done(self.session_id, self.maple_id)
        self.maple.post(
            "/api/student/formative-board/ops",
            json={"ops": [self._text_op("gone-on-cleanup", "clean-ink")]},
            headers=self._student_headers(self.maple_token),
        )
        run_key = self.school.live_board_run_key(self.session_id)
        self.school.cleanup_live_session_response_data(self.session_id)
        left = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM live_board_feedback WHERE live_session_id = ?",
            (self.session_id,),
        ).fetchone()
        self.assertEqual(int(left["n"] or 0), 0)
        self.assertEqual(self.school.boards.count_run_ops(run_key), 0)


if __name__ == "__main__":
    unittest.main()

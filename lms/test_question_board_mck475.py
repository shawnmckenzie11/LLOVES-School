#!/usr/bin/env python3
"""MCK-475: Submit locks the question board the class already uses.

Synthetic roster labels only. Assertions do not print names, emails, or join codes.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
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
from board_ops import BoardSessionClosed  # noqa: E402
from question_board import DOWN_LINE, LOCKED_LINE, QuestionBoardError  # noqa: E402


def _stroke(stroke_id: str, salt: int = 0) -> list[dict]:
    """One finished stroke inside the unit square.

    Args:
        stroke_id: Client stroke id.
        salt: Shifts the polyline so two strokes are not identical.
    """
    points = []
    for i in range(4):
        points.append(
            [
                round(((i * 3 + salt) % 90) / 100, 3),
                round(((i * 5 + salt) % 90) / 100, 3),
            ]
        )
    return [
        {"type": "stroke_add", "id": stroke_id, "points": points},
        {"type": "stroke_end", "id": stroke_id},
    ]


class QuestionBoardSubmitLockTests(unittest.TestCase):
    """Student edits stop after Submit. The teacher board still shows the ink."""

    def setUp(self) -> None:
        """One staff fixture, three synthetic students, a test live class."""
        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
            live_database_url=getattr(self, "live_database_url", None),
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

    def _add(self, text: str, *, group: bool = False, answer_with: str = "board") -> int:
        """Publish a board poll and return its prompt id.

        Args:
            text: Stem.
            group: One shared team board.
            answer_with: ``board`` or ``both``.
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
        return int(published["prompt_id"])

    def _phone(self, index: int):
        """A student client. The visit token stays out of assertions.

        Args:
            index: Position in the synthetic roster.
        """
        attendee = self.school.get_live_session_attendee(self.session_id, self.ids[index])
        self.assertIsNotNone(attendee)
        token = str(attendee["visit_token"])
        client = self.app.test_client()
        client.environ_base["HTTP_X_STUDENT_VISIT_TOKEN"] = token
        return client

    def _stroke_ids(self, view: dict) -> set[str]:
        """Stroke ids on one board view.

        Args:
            view: A canvas view or a board payload that contains one.
        """
        canvas = view.get("canvas_view") if isinstance(view.get("canvas_view"), dict) else view
        strokes = canvas.get("strokes") if isinstance(canvas, dict) else []
        return {str(row.get("id") or "") for row in strokes or [] if isinstance(row, dict)}

    def test_submit_rejects_later_student_edits_and_keeps_the_teacher_view(self) -> None:
        """Strokes, text, undo, clear, and a second submit are refused.

        The teacher panel still has the submitted stroke, and teacher ink
        still lands.
        """
        prompt_id = self._add("Board lock")
        phone = self._phone(0)
        base = f"/api/student/question-board/{prompt_id}"
        drawn = phone.post(
            f"{base}/ops",
            json={
                "points": [[0.2, 0.3], [0.4, 0.5]],
                "stroke_id": "kept",
                "ended": True,
                "x": 0.4,
                "y": 0.5,
            },
        )
        self.assertEqual(drawn.status_code, 200, drawn.get_json())
        sent = phone.post(f"{base}/submit", json={"text": ""})
        self.assertEqual(sent.status_code, 200, sent.get_json())
        body = sent.get_json()
        self.assertTrue(body.get("editing_locked"))
        self.assertEqual(body.get("status_line"), "Submitted.")
        phone_view = self.school.read_student_question_board(
            self.session_id, prompt_id, self.ids[0]
        )
        self.assertTrue(phone_view["editing_locked"])
        self.assertFalse(phone_view["read_only"])
        self.assertEqual(phone_view["status_line"], "Submitted.")
        self.assertIn("kept", self._stroke_ids(phone_view))
        before = json.dumps(phone_view.get("canvas_view") or {}, sort_keys=True)
        refused = {
            "stroke": {
                "points": [[0.1, 0.1], [0.2, 0.2]],
                "stroke_id": "late",
                "ended": True,
            },
            "text": {"text_id": "t-late", "text": "nope", "x": 0.2, "y": 0.2},
            "undo": {"op": "stroke_remove", "stroke_id": "kept"},
            "ops": {"ops": _stroke("cleared")},
        }
        for label, payload in refused.items():
            reply = phone.post(f"{base}/ops", json=payload)
            self.assertEqual(reply.status_code, 409, label)
            self.assertEqual((reply.get_json() or {}).get("error"), LOCKED_LINE)
        after = self.school.read_student_question_board(
            self.session_id, prompt_id, self.ids[0]
        )
        self.assertEqual(json.dumps(after.get("canvas_view") or {}, sort_keys=True), before)
        self.assertNotIn("late", self._stroke_ids(after))
        with self.assertRaises(QuestionBoardError) as caught:
            self.school.submit_question_board(self.session_id, prompt_id, self.ids[0], "again")
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(str(caught.exception), LOCKED_LINE)
        key = f"solo:{self.ids[0]}"
        self.school.append_question_board_ops(
            self.session_id,
            prompt_id,
            0,
            _stroke("mark"),
            owner="teacher",
            board_key=key,
        )
        opened = self.school.question_open_board(self.session_id, prompt_id, key)
        shown = self._stroke_ids(opened)
        self.assertIn("kept", shown)
        self.assertIn("mark", shown)
        self.assertNotIn("late", shown)

    def test_group_board_locks_for_every_teammate(self) -> None:
        """One Submit stops the other teammate's strokes and the shared draft."""
        prompt_id = self._add("Board team lock", group=True)
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[0], _stroke("one")
        )
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[1], _stroke("two", salt=2)
        )
        sent = self.school.submit_question_board(self.session_id, prompt_id, self.ids[0], "")
        self.assertTrue(sent["editing_locked"])
        with self.assertRaises(QuestionBoardError) as caught:
            self.school.apply_question_presence(
                self.session_id,
                prompt_id,
                self.ids[1],
                {
                    "points": [[0.6, 0.6], [0.7, 0.7]],
                    "stroke_id": "three",
                    "ended": True,
                },
            )
        self.assertEqual(caught.exception.status, 409)
        with self.assertRaises(QuestionBoardError) as draft:
            self.school.save_team_draft(self.session_id, prompt_id, self.ids[1], "later")
        self.assertEqual(draft.exception.status, 409)
        view = self.school.read_student_question_board(
            self.session_id, prompt_id, self.ids[1]
        )
        self.assertTrue(view["editing_locked"])
        self.assertNotIn("update", str(view["status_line"]).lower())
        self.assertTrue(str(view["status_line"]).startswith("Submitted"))
        ids = self._stroke_ids(view)
        self.assertIn("one", ids)
        self.assertIn("two", ids)
        self.assertNotIn("three", ids)

    def _op_count(self, run_key: str, board_key: str) -> int:
        """How many stored ops this question board has.

        Args:
            run_key: Question run key.
            board_key: ``solo:`` or ``team:`` key.
        """
        return len(self.school.boards.load_keys(run_key, [board_key]))

    def _copy_json(self, run_key: str, board_key: str) -> str:
        """Saved copy JSON for one board.

        Args:
            run_key: Question run key.
            board_key: ``solo:`` or ``team:`` key.
        """
        with self.school._lock:
            row = self.school.conn.execute(
                """
                SELECT strokes_json FROM board_answer_copies
                WHERE run_key = ? AND board_key = ?
                """,
                (run_key, board_key),
            ).fetchone()
        self.assertIsNotNone(row)
        return str(row["strokes_json"])

    def test_legacy_ink_routes_reject_after_submit(self) -> None:
        """Old board and canvas writers do not append ops after Submit.

        Teacher Clear still runs. The saved copy stays the submitted ink.
        """
        prompt_id = self._add("Board legacy lock")
        phone = self._phone(0)
        base = f"/api/student/question-board/{prompt_id}"
        drawn = phone.post(
            f"{base}/ops",
            json={
                "points": [[0.2, 0.3], [0.5, 0.4]],
                "stroke_id": "kept",
                "ended": True,
            },
        )
        self.assertEqual(drawn.status_code, 200, drawn.get_json())
        sent = phone.post(f"{base}/submit", json={"text": ""})
        self.assertEqual(sent.status_code, 200, sent.get_json())
        view = self.school.read_student_question_board(
            self.session_id, prompt_id, self.ids[0]
        )
        run_key = str(view["run_key"])
        board_key = str(view["board_key"])
        before_ops = self._op_count(run_key, board_key)
        before_copy = self._copy_json(run_key, board_key)
        solo = quote(board_key, safe="")
        posts = [
            (
                "student-ops",
                phone,
                f"/api/student/board/{solo}/ops",
                {"ops": _stroke("late-student"), "run_key": run_key},
            ),
            (
                "session-ops",
                phone,
                f"/api/live-sessions/{self.session_id}/board/{solo}/ops",
                {"ops": _stroke("late-session"), "run_key": run_key},
            ),
            (
                "canvas-text",
                phone,
                "/api/student/canvas-presence",
                {"run_key": run_key, "text_id": "tx-late", "text": "later", "x": 0.2, "y": 0.3},
            ),
            (
                "canvas-undo",
                phone,
                "/api/student/canvas-presence",
                {"run_key": run_key, "op": "stroke_remove", "stroke_id": "kept"},
            ),
        ]
        for label, client, path, payload in posts:
            reply = client.post(path, json=payload)
            self.assertEqual(reply.status_code, 409, label)
            self.assertEqual((reply.get_json() or {}).get("error"), LOCKED_LINE)
            self.assertEqual(self._op_count(run_key, board_key), before_ops, label)
            self.assertEqual(self._copy_json(run_key, board_key), before_copy, label)
        cleared = self.client.post(
            f"/api/live-sessions/{self.session_id}/questions/{prompt_id}/boards/{solo}/clear",
            json={},
        )
        self.assertEqual(cleared.status_code, 200, cleared.get_json())

    def test_team_legacy_route_rejects_a_teammate_after_submit(self) -> None:
        """The session board route 409s a teammate on the shared key."""
        prompt_id = self._add("Board team legacy", group=True)
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[0], _stroke("one")
        )
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[1], _stroke("two", salt=2)
        )
        self.school.submit_question_board(self.session_id, prompt_id, self.ids[0], "")
        view = self.school.read_student_question_board(
            self.session_id, prompt_id, self.ids[1]
        )
        run_key = str(view["run_key"])
        board_key = str(view["board_key"])
        before_ops = self._op_count(run_key, board_key)
        before_copy = self._copy_json(run_key, board_key)
        mate = self._phone(1)
        reply = mate.post(
            f"/api/live-sessions/{self.session_id}/board/{quote(board_key, safe='')}/ops",
            json={"ops": _stroke("three", salt=4), "run_key": run_key},
        )
        self.assertEqual(reply.status_code, 409, reply.get_json())
        self.assertEqual((reply.get_json() or {}).get("error"), LOCKED_LINE)
        self.assertEqual(self._op_count(run_key, board_key), before_ops)
        self.assertEqual(self._copy_json(run_key, board_key), before_copy)
        self.assertNotIn("three", self._stroke_ids(self.school.read_student_question_board(
            self.session_id, prompt_id, self.ids[1]
        )))

    def test_team_canvas_presence_text_rejects_after_group_submit(self) -> None:
        """Canvas text after a group Submit is refused on the question run.

        With no group whiteboard item the writer picks ``solo:<id>`` and
        would miss the team copy. The lock covers every member of that run.
        """
        prompt_id = self._add("Board team text", group=True)
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[0], _stroke("team-ink")
        )
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[1], _stroke("team-mate", salt=2)
        )
        self.school.submit_question_board(self.session_id, prompt_id, self.ids[0], "")
        view = self.school.read_student_question_board(
            self.session_id, prompt_id, self.ids[1]
        )
        run_key = str(view["run_key"])
        team_key = str(view["board_key"])
        solo_key = f"solo:{self.ids[1]}"
        before_team = self._op_count(run_key, team_key)
        before_copy = self._copy_json(run_key, team_key)
        phone = self._phone(1)
        posts = [
            (
                "student-text",
                "/api/student/canvas-presence",
                {"run_key": run_key, "text_id": "tx-team", "text": "later", "x": 0.2, "y": 0.3},
            ),
            (
                "session-text",
                f"/api/live-sessions/{self.session_id}/canvas-presence",
                {"run_key": run_key, "text_id": "tx-team-2", "text": "again", "x": 0.4, "y": 0.5},
            ),
        ]
        for label, path, payload in posts:
            reply = phone.post(path, json=payload)
            self.assertEqual(reply.status_code, 409, label)
            self.assertEqual((reply.get_json() or {}).get("error"), LOCKED_LINE)
        self.assertEqual(self._op_count(run_key, team_key), before_team)
        self.assertEqual(self._op_count(run_key, solo_key), 0)
        self.assertEqual(self._copy_json(run_key, team_key), before_copy)

    def test_stroke_paused_before_the_write_is_rejected_after_submit(self) -> None:
        """A stroke held before the store write cannot land after Submit.

        Pausing inside the write holds the lock, so Submit cannot pass it.
        Pausing before ``append_ops`` enters that write is the gap the
        parent allowed: the early check had already passed, Submit committed,
        and the stroke was still stored. The check now runs in the write,
        so this stroke is refused.
        """
        prompt_id = self._add("Board race")
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[0], _stroke("seed")
        )
        view = self.school.read_student_question_board(
            self.session_id, prompt_id, self.ids[0]
        )
        run_key = str(view["run_key"])
        board_key = str(view["board_key"])
        store = self.school.boards
        original = store.append_ops
        entered = threading.Event()
        release = threading.Event()

        def wrapped(run_key_arg, board_key_arg, *, owner, ops, client_batch_id=None):
            """Hold the racing stroke before the store transaction starts."""
            if any(isinstance(op, dict) and op.get("id") == "race" for op in ops):
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("stroke release")
            return original(
                run_key_arg,
                board_key_arg,
                owner=owner,
                ops=ops,
                client_batch_id=client_batch_id,
            )

        store.append_ops = wrapped
        errors: list[BaseException] = []

        def writer() -> None:
            """Append the stroke that must lose to Submit."""
            try:
                self.school.append_question_board_ops(
                    self.session_id, prompt_id, self.ids[0], _stroke("race", salt=3)
                )
            except BaseException as exc:  # noqa: BLE001 - reported after join
                errors.append(exc)

        stroke = threading.Thread(target=writer)
        stroke.start()
        try:
            self.assertTrue(entered.wait(3))
            sent = self.school.submit_question_board(
                self.session_id, prompt_id, self.ids[0], ""
            )
            before = self._op_count(run_key, board_key)
            release.set()
            stroke.join(3)
        finally:
            release.set()
            stroke.join(3)
            store.append_ops = original
        self.assertFalse(stroke.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], BoardSessionClosed)
        self.assertTrue(getattr(errors[0], "submitted", False))
        self.assertTrue(sent["editing_locked"])
        self.assertEqual(self._op_count(run_key, board_key), before)
        fresh = self.school.read_student_question_board(
            self.session_id, prompt_id, self.ids[0]
        )
        ids = self._stroke_ids(fresh)
        self.assertIn("seed", ids)
        self.assertNotIn("race", ids)
        saved = json.loads(self._copy_json(run_key, board_key))
        self.assertEqual(len(saved.get("strokes") or []), 1)



def _pg_admin_url() -> str:
    """Admin URL for a private presence database, or empty when unset."""
    return os.environ.get("LIVE_PRESENCE_TEST_URL", "").strip()


@unittest.skipUnless(_pg_admin_url(), "LIVE_PRESENCE_TEST_URL is not set")
class PostgresQuestionSubmitLockTests(QuestionBoardSubmitLockTests):
    """Submit and the legacy lock when live ink is on Postgres."""

    def setUp(self) -> None:
        """Attach a private Postgres presence database, then the sqlite fixture."""
        import uuid
        from urllib.parse import urlsplit, urlunsplit

        import psycopg

        admin = _pg_admin_url()
        self.pg_db = f"lloves_mck475_{uuid.uuid4().hex[:8]}"
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{self.pg_db}"')
        self.addCleanup(self._drop_pg, admin)
        parts = urlsplit(admin)
        self.live_database_url = urlunsplit(
            (parts.scheme, parts.netloc, f"/{self.pg_db}", parts.query, "")
        )
        super().setUp()
        self.assertIsNotNone(self.school.presence)
        self.assertEqual(type(self.school.boards).__name__, "PostgresBoardOps")

    def _drop_pg(self, admin: str) -> None:
        """Drop this test's Postgres database.

        Args:
            admin: Admin connection string.
        """
        import psycopg

        school = getattr(self, "school", None)
        if school is not None:
            try:
                school.close()
            except Exception:
                pass
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{self.pg_db}" WITH (FORCE)')

    def _sqlite_board_ops(self, run_key: str) -> int:
        """Count sqlite board rows for one run. Postgres ink stays at zero.

        Args:
            run_key: Question run key.
        """
        row = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM board_ops WHERE run_key = ?",
            (run_key,),
        ).fetchone()
        return int(row["n"])

    def test_drawing_only_submit_saves_the_stroke_and_locks(self) -> None:
        """A drawing-only Submit reads Postgres ink, saves it, and locks."""
        prompt_id = self._add("Pg draw")
        phone = self._phone(0)
        base = f"/api/student/question-board/{prompt_id}"
        drawn = phone.post(
            f"{base}/ops",
            json={
                "points": [[0.2, 0.3], [0.5, 0.4]],
                "stroke_id": "ink",
                "ended": True,
            },
        )
        self.assertEqual(drawn.status_code, 200, drawn.get_json())
        sent = phone.post(f"{base}/submit", json={"text": ""})
        self.assertEqual(sent.status_code, 200, sent.get_json())
        self.assertTrue((sent.get_json() or {}).get("editing_locked"))
        view = self.school.read_student_question_board(
            self.session_id, prompt_id, self.ids[0]
        )
        run_key = str(view["run_key"])
        board_key = str(view["board_key"])
        self.assertEqual(self._sqlite_board_ops(run_key), 0)
        saved = json.loads(self._copy_json(run_key, board_key))
        self.assertGreaterEqual(len(saved.get("strokes") or []), 1)
        self.assertIn("ink", self._stroke_ids(view))

    def test_both_answer_submit_keeps_the_stroke(self) -> None:
        """A text-and-board Submit keeps the Postgres drawing in the copy."""
        prompt_id = self._add("Pg both", answer_with="both")
        phone = self._phone(0)
        base = f"/api/student/question-board/{prompt_id}"
        drawn = phone.post(
            f"{base}/ops",
            json={
                "points": [[0.1, 0.2], [0.3, 0.4]],
                "stroke_id": "both-ink",
                "ended": True,
            },
        )
        self.assertEqual(drawn.status_code, 200, drawn.get_json())
        sent = phone.post(f"{base}/submit", json={"text": "twelve"})
        self.assertEqual(sent.status_code, 200, sent.get_json())
        view = self.school.read_student_question_board(
            self.session_id, prompt_id, self.ids[0]
        )
        run_key = str(view["run_key"])
        board_key = str(view["board_key"])
        self.assertEqual(self._sqlite_board_ops(run_key), 0)
        row = self.school.conn.execute(
            """
            SELECT text, strokes_json FROM board_answer_copies
            WHERE run_key = ? AND board_key = ?
            """,
            (run_key, board_key),
        ).fetchone()
        self.assertEqual(str(row["text"]), "twelve")
        saved = json.loads(str(row["strokes_json"]))
        self.assertGreaterEqual(len(saved.get("strokes") or []), 1)
        self.assertIn("both-ink", self._stroke_ids(view))

    def test_legacy_ink_route_rejects_after_submit_on_postgres(self) -> None:
        """The legacy student board route is 409 after a Postgres Submit."""
        prompt_id = self._add("Pg legacy")
        phone = self._phone(0)
        base = f"/api/student/question-board/{prompt_id}"
        drawn = phone.post(
            f"{base}/ops",
            json={
                "points": [[0.2, 0.2], [0.6, 0.6]],
                "stroke_id": "kept",
                "ended": True,
            },
        )
        self.assertEqual(drawn.status_code, 200, drawn.get_json())
        sent = phone.post(f"{base}/submit", json={"text": ""})
        self.assertEqual(sent.status_code, 200, sent.get_json())
        view = self.school.read_student_question_board(
            self.session_id, prompt_id, self.ids[0]
        )
        run_key = str(view["run_key"])
        board_key = str(view["board_key"])
        before = self._op_count(run_key, board_key)
        before_copy = self._copy_json(run_key, board_key)
        solo = quote(board_key, safe="")
        reply = phone.post(
            f"/api/student/board/{solo}/ops",
            json={"ops": _stroke("late"), "run_key": run_key},
        )
        self.assertEqual(reply.status_code, 409, reply.get_json())
        self.assertEqual((reply.get_json() or {}).get("error"), LOCKED_LINE)
        self.assertEqual(self._op_count(run_key, board_key), before)
        self.assertEqual(self._copy_json(run_key, board_key), before_copy)
        self.assertEqual(self._sqlite_board_ops(run_key), 0)

    def test_failed_snapshot_read_rolls_back_so_retry_commits(self) -> None:
        """A failed Postgres snapshot closes the school transaction.

        The 503 leaves no copy and another connection can write. The
        retry commits a copy a fresh connection can see.
        """
        prompt_id = self._add("Pg snapshot")
        phone = self._phone(0)
        base = f"/api/student/question-board/{prompt_id}"
        drawn = phone.post(
            f"{base}/ops",
            json={
                "points": [[0.2, 0.3], [0.4, 0.5]],
                "stroke_id": "snap",
                "ended": True,
            },
        )
        self.assertEqual(drawn.status_code, 200, drawn.get_json())
        view = self.school.read_student_question_board(
            self.session_id, prompt_id, self.ids[0]
        )
        run_key = str(view["run_key"])
        board_key = str(view["board_key"])
        db_file = self._school_db_file()
        boards = self.school.boards
        original = boards.load_keys_held
        state = {"failed": False}

        def fail_once(run_key_arg, keys):
            """Fail the first ink snapshot, then read the store."""
            if not state["failed"]:
                state["failed"] = True
                raise RuntimeError("snapshot down")
            return original(run_key_arg, keys)

        boards.load_keys_held = fail_once
        try:
            failed = phone.post(f"{base}/submit", json={"text": ""})
        finally:
            boards.load_keys_held = original
        self.assertTrue(state["failed"])
        self.assertEqual(failed.status_code, 503)
        self.assertEqual((failed.get_json() or {}).get("error"), DOWN_LINE)
        self.assertFalse(self.school.conn.in_transaction)
        self._other_connection_can_write(db_file)
        self.assertIsNone(self._fresh_copy(db_file, run_key, board_key))
        sent = phone.post(f"{base}/submit", json={"text": ""})
        self.assertEqual(sent.status_code, 200, sent.get_json())
        self.assertTrue((sent.get_json() or {}).get("editing_locked"))
        self.assertFalse(self.school.conn.in_transaction)
        saved = self._fresh_copy(db_file, run_key, board_key)
        self.assertIsNotNone(saved)
        self.assertTrue(str(saved["submitted_at"] or "").strip())
        strokes = json.loads(str(saved["strokes_json"] or "{}")).get("strokes") or []
        self.assertGreaterEqual(len(strokes), 1)

    def _school_db_file(self) -> str:
        """Path of the school sqlite file.

        Returns:
            Absolute path from ``PRAGMA database_list``.
        """
        row = self.school.conn.execute("PRAGMA database_list").fetchone()
        return str(row[2])

    def _other_connection_can_write(self, db_file: str) -> None:
        """Take the sqlite write lock from a second connection.

        An open ``BEGIN IMMEDIATE`` on the school connection blocks this.

        Args:
            db_file: School sqlite path.
        """
        other = sqlite3.connect(db_file, timeout=1)
        try:
            other.execute("BEGIN IMMEDIATE")
            other.execute("SELECT 1")
            other.commit()
        finally:
            other.close()

    def _fresh_copy(self, db_file: str, run_key: str, board_key: str):
        """Read the saved copy from a connection that did not write it.

        Args:
            db_file: School sqlite path.
            run_key: Question run key.
            board_key: ``solo:`` or ``team:`` key.

        Returns:
            The copy row, or ``None`` when nothing is committed.
        """
        other = sqlite3.connect(db_file, timeout=1)
        other.row_factory = sqlite3.Row
        try:
            return other.execute(
                """
                SELECT submitted_at, strokes_json FROM board_answer_copies
                WHERE run_key = ? AND board_key = ?
                """,
                (run_key, board_key),
            ).fetchone()
        finally:
            other.close()


class QuestionBoardLockClientTests(unittest.TestCase):
    """The existing whiteboard closes text entry and ignores later edits."""

    def test_lock_closes_text_entry_and_blocks_controls(self) -> None:
        """Submit state: text box closed, Submit gone, no stroke undo redo or clear."""
        script = r"""
import { bindWhiteboard } from './lms/static/live_whiteboard.js';
import { lockSubmittedQuestionBoard } from './lms/static/question_board_ui.js';

class HTMLElement {}
class HTMLCanvasElement extends HTMLElement {}
class HTMLButtonElement extends HTMLElement {}
class HTMLTextAreaElement extends HTMLElement {}
globalThis.HTMLElement = HTMLElement;
globalThis.HTMLCanvasElement = HTMLCanvasElement;
globalThis.HTMLButtonElement = HTMLButtonElement;
globalThis.HTMLTextAreaElement = HTMLTextAreaElement;
globalThis.Element = class Element {};

function makeEl(proto) {
  const listeners = {};
  const node = {
    hidden: false,
    disabled: false,
    readOnly: false,
    value: '',
    style: { cursor: '', removeProperty() {} },
    dataset: {},
    className: '',
    classList: { add() {}, remove() {}, toggle() {} },
    children: [],
    innerHTML: '',
    width: 720,
    height: 360,
    clientWidth: 720,
    clientHeight: 360,
    offsetLeft: 0,
    offsetTop: 0,
    parentElement: null,
    appendChild(child) {
      node.children.push(child);
      child.parentElement = node;
      return child;
    },
    insertAdjacentElement(_where, child) { return node.appendChild(child); },
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    removeEventListener() {},
    setAttribute() {},
    removeAttribute() {},
    getAttribute() { return null; },
    querySelector() { return null; },
    getBoundingClientRect() {
      return { width: 720, height: 360, left: 0, top: 0, right: 720, bottom: 360 };
    },
    setPointerCapture() {},
    focus() {},
    select() {},
    blur() {},
    getContext() {
      return {
        setTransform() {}, clearRect() {}, beginPath() {}, moveTo() {},
        lineTo() {}, stroke() {}, fill() {}, arc() {}, save() {}, restore() {},
        setLineDash() {}, measureText() { return { width: 10 }; },
        fillText() {},
      };
    },
    dispatch(type, props) {
      const event = {
        clientX: 40, clientY: 50, pointerId: 1, preventDefault() {},
        stopPropagation() {}, target: node, ...props,
      };
      for (const fn of listeners[type] || []) fn(event);
    },
  };
  if (proto) Object.setPrototypeOf(node, proto.prototype);
  return node;
}

const stage = makeEl(HTMLElement);
const canvas = makeEl(HTMLCanvasElement);
stage.appendChild(canvas);
const pen = makeEl(HTMLButtonElement);
const textBtn = makeEl(HTMLButtonElement);
const erase = makeEl(HTMLButtonElement);
const undo = makeEl(HTMLButtonElement);
const redo = makeEl(HTMLButtonElement);
const clear = makeEl(HTMLButtonElement);
const submit = makeEl(HTMLButtonElement);
const typed = makeEl(HTMLTextAreaElement);
const tools = {
  querySelectorAll() { return [pen, textBtn, erase, undo, redo, clear]; },
};
globalThis.document = {
  body: makeEl(HTMLElement),
  createElement() { return makeEl(HTMLElement); },
  getElementById() { return null; },
  addEventListener() {},
};
globalThis.window = globalThis;
window.addEventListener = () => {};
window.setTimeout = (fn) => { fn(); return 1; };
window.clearTimeout = () => {};
window.devicePixelRatio = 1;
window.requestAnimationFrame = (fn) => { fn(); return 1; };
globalThis.ResizeObserver = undefined;

const calls = { points: 0, undo: 0, redo: 0, clear: 0, text: 0 };
const board = bindWhiteboard(canvas, {
  penBtn: pen,
  textBtn,
  eraseBtn: erase,
  undoBtn: undo,
  redoBtn: redo,
  formative: { enabled: false, members: [] },
  owner: () => '1',
  onPoints() { calls.points += 1; },
  onUndo() { calls.undo += 1; },
  onRedo() { calls.redo += 1; },
  onClear() { calls.clear += 1; },
  onText() { calls.text += 1; },
});
board.setCollab(true);
clear.addEventListener('click', () => erase.dispatch('click'));
function draw(x1, y1, x2, y2) {
  canvas.dispatch('pointerdown', { clientX: x1, clientY: y1 });
  canvas.dispatch('pointermove', { clientX: x2, clientY: y2 });
  canvas.dispatch('pointerup', { clientX: x2, clientY: y2 });
}
draw(30, 40, 180, 120);
draw(40, 200, 200, 80);
undo.dispatch('click');
if (board.getInk().strokes.length !== 1 || calls.undo !== 1 || calls.points < 2) {
  console.error('setup ink ' + JSON.stringify({ ink: board.getInk().strokes.length, calls }));
  process.exit(1);
}
board.openTextAt(0.4, 0.4);
const editor = stage.children.find((child) => String(child.className).includes('canvas-text-editor'));
if (!editor || editor.hidden) {
  console.error('text entry did not open');
  process.exit(2);
}
editor.value = 'draft label';
const before = { ...calls, ink: board.getInk().strokes.length };
lockSubmittedQuestionBoard({ board, submit, typed, tools });
if (!editor.hidden || editor.value) {
  console.error('text entry stayed open');
  process.exit(3);
}
if (!submit.hidden || !submit.disabled) {
  console.error('submit stayed');
  process.exit(4);
}
if (!typed.readOnly) {
  console.error('typed answer stayed editable');
  process.exit(5);
}
for (const btn of [pen, textBtn, erase, undo, redo, clear]) {
  if (!btn.disabled) {
    console.error('control stayed enabled');
    process.exit(6);
  }
}
draw(80, 90, 220, 140);
undo.dispatch('click');
redo.dispatch('click');
erase.dispatch('click');
clear.dispatch('click');
textBtn.dispatch('click');
canvas.dispatch('pointerdown', { clientX: 90, clientY: 90 });
board.clear();
board.setEnabled(true);
draw(100, 100, 240, 160);
if (
  calls.points !== before.points ||
  calls.undo !== before.undo ||
  calls.redo !== before.redo ||
  calls.clear !== before.clear ||
  calls.text !== before.text ||
  board.getInk().strokes.length !== before.ink ||
  !editor.hidden
) {
  console.error('locked board still edited ' + JSON.stringify({ calls, ink: board.getInk().strokes.length, hidden: editor.hidden }));
  process.exit(7);
}
console.log('locked');
"""
        proc = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("locked", proc.stdout)

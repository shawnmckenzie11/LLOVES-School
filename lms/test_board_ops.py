#!/usr/bin/env python3
"""Append-only whiteboard ops: sequence, delta, isolation, undo, purge."""

from __future__ import annotations

import json
import os
import subprocess
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from board_ops import (  # noqa: E402
    MAX_DELTA_OPS,
    BoardOpRejected,
    BoardSessionClosed,
    SqliteBoardOps,
)
from live_presence import apply_locked_ddl  # noqa: E402


class SqliteBoardOpsTests(unittest.TestCase):
    """Sequence allocation and since-seq reads on a private sqlite file."""

    def setUp(self) -> None:
        """Create one sqlite file and a board store on it."""
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "boards.sqlite"
        conn = self._connect(self.path)
        self.store = SqliteBoardOps(conn)
        self.store.ensure_schema()
        self.conn = conn

    def tearDown(self) -> None:
        """Close the boot connection and remove the temp file."""
        self.conn.close()
        self.tmp.cleanup()

    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        """Open one sqlite connection that can share the file with others.

        Args:
            path: Database file.

        Returns:
            A connection in autocommit mode with a busy timeout.
        """
        conn = sqlite3.connect(path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def test_concurrent_appends_have_unique_increasing_seqs(self) -> None:
        """Eight connections appending together lose no ops and share no seq."""
        workers = 8
        per_worker = 25
        barrier = threading.Barrier(workers)
        errors: list[BaseException] = []
        seqs: list[int] = []
        lock = threading.Lock()

        def worker(index: int) -> None:
            """Append one new stroke per turn from a private connection.

            Args:
                index: Worker number, used in stroke ids.
            """
            conn = self._connect(self.path)
            store = SqliteBoardOps(conn)
            try:
                barrier.wait(timeout=10)
                for turn in range(per_worker):
                    result = store.append_ops(
                        9,
                        "team:1",
                        owner=f"w{index}",
                        ops=[
                            {
                                "type": "stroke_add",
                                "id": f"w{index}-{turn}",
                                "points": [[0.1, 0.2]],
                                "x": 0.1,
                                "y": 0.2,
                            }
                        ],
                    )
                    with lock:
                        seqs.append(int(result["board_seq"]))
            except BaseException as exc:  # noqa: BLE001 - the test thread reports it
                errors.append(exc)
            finally:
                conn.close()

        threads = [
            threading.Thread(target=worker, args=(index,)) for index in range(workers)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        expected = workers * per_worker
        self.assertEqual(sorted(seqs), list(range(1, expected + 1)))
        rows = self.store.load_keys(9, ["team:1"])
        self.assertEqual(len(rows), expected)
        stored = [int(row["board_seq"]) for row in rows]
        self.assertEqual(stored, list(range(1, expected + 1)))

    def test_since_seq_is_ordered_and_a_wide_gap_is_a_snapshot(self) -> None:
        """since returns later ops in order; a gap past the cap is a snapshot."""
        for index in range(3):
            self.store.append_ops(
                1,
                "team:4",
                owner="11",
                ops=[
                    {
                        "type": "stroke_add",
                        "id": f"early-{index}",
                        "points": [[0.2, 0.3]],
                        "x": 0.2,
                        "y": 0.3,
                    }
                ],
            )
        mid = self.store.since(1, "team:4", 1)
        self.assertFalse(mid["snapshot"])
        self.assertEqual([op["id"] for op in mid["ops"]], ["early-1", "early-2"])
        self.assertEqual([op["board_seq"] for op in mid["ops"]], [2, 3])
        current = self.store.current_seq(1, "team:4")
        empty = self.store.since(1, "team:4", current)
        self.assertEqual(empty["ops"], [])
        self.assertFalse(empty["snapshot"])
        self.assertEqual(empty["board_seq"], current)
        for index in range(MAX_DELTA_OPS - 1):
            self.store.append_ops(
                1,
                "team:4",
                owner="11",
                ops=[
                    {
                        "type": "stroke_add",
                        "id": f"late-{index}",
                        "points": [[0.4, 0.5]],
                        "x": 0.4,
                        "y": 0.5,
                    }
                ],
            )
        # Three early ops plus (MAX_DELTA_OPS - 1) more is one past the cap.
        gap = self.store.since(1, "team:4", 0)
        self.assertTrue(gap["snapshot"])
        self.assertEqual(gap["ops"], [])
        self.assertGreater(int(gap["board_seq"]), MAX_DELTA_OPS)
        tail = self.store.since(1, "team:4", int(gap["board_seq"]) - 1)
        self.assertFalse(tail["snapshot"])
        self.assertEqual(len(tail["ops"]), 1)
        self.assertEqual(tail["ops"][0]["board_seq"], int(gap["board_seq"]))

    def test_client_batch_id_does_not_append_twice(self) -> None:
        """A repeated client batch id returns the original sequence."""
        body = [
            {
                "type": "stroke_add",
                "id": "once",
                "points": [[0.1, 0.1]],
                "x": 0.1,
                "y": 0.1,
            }
        ]
        first = self.store.append_ops(
            1, "teacher", owner="teacher", ops=body, client_batch_id="batch-a"
        )
        second = self.store.append_ops(
            1, "teacher", owner="teacher", ops=body, client_batch_id="batch-a"
        )
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["board_seq"], second["board_seq"])
        self.assertEqual(len(self.store.load_keys(1, ["teacher"])), 1)

    def test_own_remove_drops_the_stroke_and_a_foreign_remove_is_rejected(self) -> None:
        """stroke_remove works for the owner and is refused for anyone else."""
        self.store.append_ops(
            1,
            "team:2",
            owner="11",
            ops=[
                {
                    "type": "stroke_add",
                    "id": "mine",
                    "points": [[0.3, 0.3]],
                    "x": 0.3,
                    "y": 0.3,
                }
            ],
        )
        with self.assertRaises(BoardOpRejected):
            self.store.append_remove(1, "team:2", owner="12", stroke_id="mine")
        removed = self.store.append_remove(1, "team:2", owner="11", stroke_id="mine")
        self.assertEqual(removed["ops"][0]["type"], "stroke_remove")
        from board_ops import fold_to_public_blob

        blob = fold_to_public_blob(self.store.load_keys(1, ["team:2"]))
        self.assertEqual(blob["strokes"]["teams"].get("2") or [], [])

    def test_more_than_eighty_strokes_stay_on_the_board(self) -> None:
        """Folding ops does not drop the oldest strokes at the old cap."""
        for index in range(90):
            self.store.append_ops(
                1,
                "team:3",
                owner="11",
                ops=[
                    {
                        "type": "stroke_add",
                        "id": f"keep-{index}",
                        "points": [[0.2, 0.2]],
                        "x": 0.2,
                        "y": 0.2,
                    }
                ],
            )
        from board_ops import fold_to_public_blob

        blob = fold_to_public_blob(self.store.load_keys(1, ["team:3"]))
        strokes = blob["strokes"]["teams"]["3"]
        self.assertEqual(len(strokes), 90)
        self.assertEqual(strokes[0]["id"], "keep-0")
        self.assertEqual(strokes[-1]["id"], "keep-89")


    def test_since_omits_ops_past_the_reported_head(self) -> None:
        """A row newer than the counter is not included in this reply."""
        for index in range(2):
            self.store.append_ops(
                1,
                "shared",
                owner="11",
                ops=[
                    {
                        "type": "stroke_add",
                        "id": f"head-{index}",
                        "points": [[0.1, 0.2]],
                    }
                ],
            )
        self.conn.execute(
            """
            INSERT INTO board_ops (
                run_key, board_key, board_seq, op, owner, created_at
            ) VALUES ('1', 'shared', 50, ?, '9', '2026-01-01T00:00:00')
            """,
            (
                json.dumps(
                    {"type": "stroke_add", "id": "orphan", "points": [[0.1, 0.1]]}
                ),
            ),
        )
        delta = self.store.since(1, "shared", 0)
        self.assertEqual(delta["board_seq"], 2)
        self.assertFalse(delta["snapshot"])
        self.assertEqual([op["board_seq"] for op in delta["ops"]], [1, 2])
        self.assertNotIn(50, [op["board_seq"] for op in delta["ops"]])

    def test_ensure_schema_twice_changes_nothing(self) -> None:
        """Opening the board schema again leaves the same tables in place."""
        self.store.ensure_schema()
        self.store.ensure_schema()
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'board_sessions'"
        ).fetchone()
        self.assertIsNotNone(row)

    def test_purge_and_a_racing_write_do_not_reuse_a_sequence(self) -> None:
        """Writers that overlap a purge raise closed, and leave no orphan rows."""
        errors: list[BaseException] = []

        def writer() -> None:
            """Append until the session closes, recording unexpected errors."""
            conn = self._connect(self.path)
            try:
                store = SqliteBoardOps(conn)
                for index in range(40):
                    try:
                        store.append_ops(
                            7,
                            "shared",
                            owner="1",
                            ops=[
                                {
                                    "type": "stroke_add",
                                    "id": f"s-{threading.get_ident()}-{index}",
                                    "points": [[0.1, 0.1]],
                                }
                            ],
                        )
                    except BoardSessionClosed:
                        return
            except Exception as exc:
                errors.append(exc)
            finally:
                conn.close()

        threads = [threading.Thread(target=writer) for _ in range(4)]
        for thread in threads:
            thread.start()
        self.store.purge(7)
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(errors, [])
        leftover = self.conn.execute(
            "SELECT COUNT(*) AS n FROM board_ops WHERE run_key = '7'"
        ).fetchone()
        counters = self.conn.execute(
            "SELECT COUNT(*) AS n FROM board_counters WHERE run_key = '7'"
        ).fetchone()
        self.assertEqual(int(leftover["n"]), 0)
        self.assertEqual(int(counters["n"]), 0)
        with self.assertRaises(BoardSessionClosed):
            self.store.append_ops(
                7,
                "shared",
                owner="1",
                ops=[{"type": "stroke_add", "id": "after", "points": [[0.2, 0.2]]}],
            )

    def test_locked_ddl_survives_a_duplicate_type_and_runs_twice(self) -> None:
        """A UniqueViolation on one statement does not abort the rest of setup."""

        class UniqueViolation(Exception):
            """Stand-in for psycopg's duplicate pg_type error."""

        class FakeRaw:
            """Records DDL and fails the first CREATE TABLE once."""

            def __init__(self) -> None:
                self.statements: list[str] = []
                self.fail_once = True

            def transaction(self):
                """Act as the psycopg transaction context."""
                return self

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            def execute(self, sql: str, params=None):
                """Record ``sql`` and raise once on the first create."""
                self.statements.append(sql)
                if "CREATE TABLE" in sql and self.fail_once:
                    self.fail_once = False
                    raise UniqueViolation("pg_type_typname_nsp_index")
                return self

        raw = FakeRaw()
        statements = [
            "CREATE TABLE IF NOT EXISTS live_presence_sessions (id BIGINT PRIMARY KEY)",
            "CREATE INDEX IF NOT EXISTS live_presence_sessions_class ON live_presence_sessions (class_id)",
        ]
        apply_locked_ddl(raw, statements, 87421300)
        self.assertIn("SELECT pg_advisory_xact_lock(%s)", raw.statements)
        self.assertIn("ROLLBACK TO SAVEPOINT ddl_step", raw.statements)
        self.assertTrue(any(sql.startswith("CREATE INDEX") for sql in raw.statements))
        first = list(raw.statements)
        apply_locked_ddl(raw, statements, 87421300)
        self.assertGreater(len(raw.statements), len(first))


class BoardRouteTests(unittest.TestCase):
    """HTTP isolation, /state, undo, and purge on the school sqlite path."""

    def setUp(self) -> None:
        """Staff session with one live class and a logged-in teacher."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.client.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Aspen", "Birch"],
            },
        )
        self.class_id = int(created.get_json()["class"]["id"])
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])

    def tearDown(self) -> None:
        """Close the school db and the temp directory."""
        self.school.close()
        self.tmp.cleanup()

    def _whiteboard(self) -> dict:
        """Return the seeded whiteboard lifecycle row."""
        self.school.ensure_live_session_items(self.session_id)
        for row in self.school.list_live_session_items(self.session_id):
            if str(row.get("kind") or "") == "whiteboard":
                return row
        self.fail("whiteboard lifecycle row missing")
        return {}

    def _publish_two_groups(self) -> dict[str, str]:
        """Publish a shared group board and return visit tokens by codename.

        Returns:
            Visit tokens for Aspen, Birch, and Cedar.
        """
        self.school.game.add_student(self.class_id, codename="Cedar")
        students = [
            dict(row)
            for row in self.school.game.conn.execute(
                "SELECT id, codename FROM students WHERE class_id = ? ORDER BY id",
                (self.class_id,),
            ).fetchall()
        ]
        mates = [row for row in students if row["codename"] in ("Aspen", "Birch")]
        other = next(row for row in students if row["codename"] == "Cedar")
        self.school.game.begin_game(self.class_id)
        tokens: dict[str, str] = {}
        for student in students:
            joined = self.school.join_live_class_session(
                self.session_id,
                int(student["id"]),
                codename=str(student["codename"]),
            )
            tokens[str(student["codename"])] = str(joined["attendee"]["visit_token"])
        self.school.setup_live_session_groups(
            self.session_id,
            n_teams=2,
            mode="manual",
            present_ids=[int(student["id"]) for student in students],
            assignments=[
                {"student_id": int(student["id"]), "team_index": 0}
                for student in mates
            ]
            + [{"student_id": int(other["id"]), "team_index": 1}],
        )
        row = self._whiteboard()
        published = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{int(row['id'])}/publish",
            json={"publish_mode": "group_shared"},
        )
        self.assertEqual(published.status_code, 200, published.get_json())
        return tokens

    def _student(self, token: str):
        """Return a test client bound to one student visit token.

        Args:
            token: ``X-Student-Visit-Token`` value.
        """
        client = self.app.test_client()
        client.environ_base["HTTP_X_STUDENT_VISIT_TOKEN"] = token
        return client

    def _student_id(self, codename: str) -> int:
        """Roster id for a codename in this class.

        Args:
            codename: Student codename.
        """
        row = self.school.game.find_student_by_codename(self.class_id, codename)
        return int(row["id"])

    def test_cross_team_and_teacher_cannot_read_another_groups_ops(self) -> None:
        """A student is limited to their team; staff get no group board."""
        tokens = self._publish_two_groups()
        aspen = self._student(tokens["Aspen"])
        cedar = self._student(tokens["Cedar"])
        drawn = aspen.post(
            "/api/student/canvas-presence",
            json={
                "run_key": self.school.live_board_run_key(self.session_id),
                "x": 0.2,
                "y": 0.3,
                "points": [[0.2, 0.3]],
                "stroke_id": "aspen-ink",
                "ended": True,
            },
        )
        self.assertEqual(drawn.status_code, 200, drawn.get_json())
        cedar_team = self.school.student_team_id_for_class(
            self.class_id, self._student_id("Cedar")
        )
        aspen_team = self.school.student_team_id_for_class(
            self.class_id, self._student_id("Aspen")
        )
        self.assertNotEqual(aspen_team, cedar_team)
        foreign = aspen.get(f"/api/student/board/team:{int(cedar_team)}?since=0")
        self.assertEqual(foreign.status_code, 403, foreign.get_json())
        foreign_write = aspen.post(
            f"/api/student/board/team:{int(cedar_team)}/ops",
            json={
                "run_key": self.school.live_board_run_key(self.session_id),
                "ops": [
                    {
                        "type": "stroke_add",
                        "id": "sneak",
                        "points": [[0.9, 0.9]],
                    }
                ]
            },
        )
        self.assertEqual(foreign_write.status_code, 403, foreign_write.get_json())
        mine = aspen.get("/api/student/board/mine?since=0&teacher_since=0")
        self.assertEqual(mine.status_code, 200, mine.get_json())
        mine_body = mine.get_json()
        self.assertIn("aspen-ink", str(mine_body.get("ops")))
        self.assertNotIn("sneak", str(mine_body))
        cedar_view = cedar.get("/api/student/board/mine?since=0")
        self.assertEqual(cedar_view.status_code, 200, cedar_view.get_json())
        self.assertNotIn("aspen-ink", str(cedar_view.get_json()))
        staff_group = self.client.get(
            f"/api/live-sessions/{self.session_id}/board/team:{int(aspen_team)}?since=0"
        )
        self.assertEqual(staff_group.status_code, 403, staff_group.get_json())
        staff_teacher = self.client.get(
            f"/api/live-sessions/{self.session_id}/board/teacher?since=0"
        )
        self.assertEqual(staff_teacher.status_code, 200, staff_teacher.get_json())
        self.assertNotIn("aspen-ink", str(staff_teacher.get_json()))

    def test_undo_is_visible_to_a_teammate_and_rejected_for_the_other_owner(self) -> None:
        """Own stroke_remove lands in the delta; a teammate cannot remove it."""
        tokens = self._publish_two_groups()
        aspen = self._student(tokens["Aspen"])
        birch = self._student(tokens["Birch"])
        drawn = aspen.post(
            "/api/student/canvas-presence",
            json={
                "run_key": self.school.live_board_run_key(self.session_id),
                "x": 0.4,
                "y": 0.4,
                "points": [[0.4, 0.4], [0.45, 0.5]],
                "stroke_id": "aspen-undo",
                "ended": True,
            },
        )
        self.assertEqual(drawn.status_code, 200, drawn.get_json())
        stolen = birch.post(
            "/api/student/canvas-presence",
            json={"run_key": self.school.live_board_run_key(self.session_id),"op": "stroke_remove", "stroke_id": "aspen-undo"},
        )
        self.assertEqual(stolen.status_code, 403, stolen.get_json())
        before = birch.get("/api/student/board/mine?since=0").get_json()
        seen = int(before["board_seq"])
        removed = aspen.post(
            "/api/student/canvas-presence",
            json={"run_key": self.school.live_board_run_key(self.session_id),"op": "stroke_remove", "stroke_id": "aspen-undo"},
        )
        self.assertEqual(removed.status_code, 200, removed.get_json())
        delta = birch.get(f"/api/student/board/mine?since={seen}").get_json()
        kinds = [op.get("type") for op in delta.get("ops") or []]
        self.assertIn("stroke_remove", kinds)
        self.assertTrue(
            any(op.get("id") == "aspen-undo" for op in delta.get("ops") or [])
        )
        blob = self.school.live_session_canvas_sync(self.session_id)
        flat = []
        for bucket in (blob["strokes"]["teams"] or {}).values():
            flat.extend(bucket)
        self.assertNotIn("aspen-undo", {stroke["id"] for stroke in flat})

    def test_state_omits_ink_and_the_stamp_ignores_strokes(self) -> None:
        """Staff and student /state carry no ink, and ink does not move the stamp."""
        tokens = self._publish_two_groups()
        before = self.school.live_student_poll_stamp(self.session_id, self.class_id)
        aspen = self._student(tokens["Aspen"])
        drawn = aspen.post(
            "/api/student/canvas-presence",
            json={
                "run_key": self.school.live_board_run_key(self.session_id),
                "x": 0.15,
                "y": 0.25,
                "points": [[0.15, 0.25]],
                "stroke_id": "stamp-ink",
                "ended": True,
            },
        )
        self.assertEqual(drawn.status_code, 200, drawn.get_json())
        after = self.school.live_student_poll_stamp(self.session_id, self.class_id)
        self.assertEqual(before, after)
        staff = self.client.get(f"/api/live-sessions/{self.session_id}/state")
        self.assertEqual(staff.status_code, 200, staff.get_json())
        staff_body = staff.get_json()
        self.assertNotIn("canvas_rev", staff_body)
        self.assertNotIn("canvas_sync", staff_body)
        self.assertEqual(
            staff_body["session"]["canvas_sync"]["strokes"]["teacher"], []
        )
        self.assertEqual(staff_body["session"]["canvas_sync"]["strokes"]["teams"], {})
        self.assertNotIn("stamp-ink", str(staff_body))
        student = aspen.get("/api/student/state")
        self.assertEqual(student.status_code, 200, student.get_json())
        student_body = student.get_json()
        self.assertNotIn("canvas_rev", student_body)
        self.assertNotIn("canvas_sync", student_body)
        self.assertNotIn("stamp-ink", str(student_body))

    def test_ending_the_session_purges_board_ops(self) -> None:
        """End class deletes ops, counters, and batch rows for that session."""
        tokens = self._publish_two_groups()
        aspen = self._student(tokens["Aspen"])
        drawn = aspen.post(
            "/api/student/canvas-presence",
            json={
                "run_key": self.school.live_board_run_key(self.session_id),
                "x": 0.5,
                "y": 0.5,
                "point": [0.5, 0.5],
                "stroke_id": "gone-soon",
                "client_batch_id": "purge-batch",
            },
        )
        self.assertEqual(drawn.status_code, 200, drawn.get_json())
        access = self.school.student_board_access(
            self.session_id, self._student_id("Aspen")
        )
        key = str(access["collab_key"])
        run_key = self.school.live_board_run_key(self.session_id)
        self.assertGreater(self.school.boards.current_seq(run_key, key), 0)
        ended = self.school.end_live_class_session(self.session_id)
        self.assertEqual(ended["status"], "ended")
        self.assertEqual(self.school.boards.load_keys(run_key, None), [])
        self.assertEqual(self.school.boards.current_seq(run_key, key), 0)

    def test_ending_the_session_rejects_later_ink(self) -> None:
        """Ink posted while a session is ending is 409, not a uniqueness 500.

        Purge runs before the status flip. The attendee is still joined and
        the session row is still active, which is the window that reused
        sequence 1 against an orphan row.
        """
        tokens = self._publish_two_groups()
        aspen = self._student(tokens["Aspen"])
        first = aspen.post(
            "/api/student/canvas-presence",
            json={
                "run_key": self.school.live_board_run_key(self.session_id),
                "x": 0.2,
                "y": 0.2,
                "point": [0.2, 0.2],
                "stroke_id": "before-purge",
            },
        )
        self.assertEqual(first.status_code, 200, first.get_json())
        self.school.purge_board_ops(self.session_id)
        drawn = aspen.post(
            "/api/student/canvas-presence",
            json={
                "run_key": self.school.live_board_run_key(self.session_id),
                "x": 0.4,
                "y": 0.4,
                "point": [0.4, 0.4],
                "stroke_id": "during-end",
                "client_batch_id": "during-end-batch",
            },
        )
        self.assertEqual(drawn.status_code, 409, drawn.get_json())
        body = drawn.get_json()
        self.assertTrue(body.get("ended"))
        run_key = self.school.live_board_run_key(self.session_id)
        self.assertEqual(self.school.boards.load_keys(run_key, None), [])
        ended = self.school.end_live_class_session(self.session_id)
        self.assertEqual(ended["status"], "ended")
        with self.assertRaises(BoardSessionClosed):
            self.school.apply_live_canvas_presence(
                self.session_id,
                owner=str(self._student_id("Aspen")),
                name="Aspen",
                point=[0.6, 0.6],
                stroke_id="after-status",
            )


    def _rejoin_published_groups(self) -> dict[str, str]:
        """Rejoin the roster on the current session and republish the board.

        End and Quit cancel the game. Cedar is already on the roster, so
        this begins a new game without inserting that student again.

        Returns:
            Visit tokens keyed by codename.
        """
        students = [
            dict(row)
            for row in self.school.game.conn.execute(
                "SELECT id, codename FROM students WHERE class_id = ? ORDER BY id",
                (self.class_id,),
            ).fetchall()
        ]
        mates = [row for row in students if row["codename"] in ("Aspen", "Birch")]
        other = next(row for row in students if row["codename"] == "Cedar")
        self.school.game.begin_game(self.class_id)
        tokens: dict[str, str] = {}
        for student in students:
            joined = self.school.join_live_class_session(
                self.session_id,
                int(student["id"]),
                codename=str(student["codename"]),
            )
            tokens[str(student["codename"])] = str(joined["attendee"]["visit_token"])
        self.school.setup_live_session_groups(
            self.session_id,
            n_teams=2,
            mode="manual",
            present_ids=[int(student["id"]) for student in students],
            assignments=[
                {"student_id": int(student["id"]), "team_index": 0}
                for student in mates
            ]
            + [{"student_id": int(other["id"]), "team_index": 1}],
        )
        row = self._whiteboard()
        published = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{int(row['id'])}/publish",
            json={"publish_mode": "group_shared"},
        )
        self.assertEqual(published.status_code, 200, published.get_json())
        return tokens

    def _draw(
        self,
        client,
        stroke_id: str,
        *,
        run_key: str | None = None,
        omit_run_key: bool = False,
    ):
        """Post one finished stroke and return the response.

        Args:
            client: Student test client.
            stroke_id: Stroke id to store.
            run_key: Run claim. Defaults to the current live run.
            omit_run_key: Send no run key, which the server must reject.
        """
        body = {
            "x": 0.2,
            "y": 0.3,
            "points": [[0.2, 0.3]],
            "stroke_id": stroke_id,
            "ended": True,
        }
        if run_key is not None:
            body["run_key"] = run_key
        elif not omit_run_key:
            body["run_key"] = self.school.live_board_run_key(self.session_id)
        return client.post("/api/student/canvas-presence", json=body)

    def _op_count(self, run_key: str) -> int:
        """Count board ops stored under one run key.

        Args:
            run_key: Key minted when that live run started.
        """
        row = self.school.boards.conn.execute(
            "SELECT COUNT(*) FROM board_ops WHERE run_key = ?",
            (run_key,),
        ).fetchone()
        return int(row[0])

    def test_restart_after_end_draws_on_an_empty_board_at_seq_1(self) -> None:
        """A reused session id does not keep the previous run closed."""
        tokens = self._publish_two_groups()
        aspen = self._student(tokens["Aspen"])
        first = self._draw(aspen, "old-ink")
        self.assertEqual(first.status_code, 200, first.get_json())
        old_id = self.session_id
        old_key = self.school.live_board_run_key(old_id)
        self.school.finish_live_class(
            self.class_id,
            celebrate=True,
            save_attendance=False,
            save_participation=False,
        )
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])
        new_key = self.school.live_board_run_key(self.session_id)
        self.assertEqual(self.session_id, old_id)
        self.assertNotEqual(new_key, old_key)
        closed = self.school.boards.conn.execute(
            "SELECT COUNT(*) FROM board_sessions WHERE closed = 1"
        ).fetchone()
        self.assertEqual(int(closed[0]), 0)
        tokens = self._rejoin_published_groups()
        aspen = self._student(tokens["Aspen"])
        empty = aspen.get("/api/student/board/mine?since=0")
        self.assertEqual(empty.status_code, 200, empty.get_json())
        empty_body = empty.get_json()
        self.assertFalse(empty_body.get("ended"))
        self.assertEqual(empty_body.get("ops") or [], [])
        self.assertEqual(int(empty_body.get("board_seq") or 0), 0)
        self.assertNotIn("old-ink", str(empty_body))
        drawn = self._draw(aspen, "new-ink")
        self.assertEqual(drawn.status_code, 200, drawn.get_json())
        mine = aspen.get("/api/student/board/mine?since=0")
        self.assertEqual(mine.status_code, 200, mine.get_json())
        body = mine.get_json()
        self.assertFalse(body.get("ended"))
        seqs = [int(op["board_seq"]) for op in body.get("ops") or []]
        self.assertEqual(seqs[0], 1)
        self.assertIn("new-ink", str(body))
        self.assertNotIn("old-ink", str(body))
        self.assertEqual(self._op_count(old_key), 0)

    def test_quit_live_drops_strokes_before_the_next_run(self) -> None:
        """Quit deletes ops for that run, so a reused id starts at seq 1."""
        tokens = self._publish_two_groups()
        aspen = self._student(tokens["Aspen"])
        first = self._draw(aspen, "quit-ink")
        self.assertEqual(first.status_code, 200, first.get_json())
        old_key = self.school.live_board_run_key(self.session_id)
        self.assertGreater(self._op_count(old_key), 0)
        self.school.finish_live_class(
            self.class_id, persist=False, celebrate=False
        )
        self.assertEqual(self._op_count(old_key), 0)
        counters = self.school.boards.conn.execute(
            "SELECT COUNT(*) FROM board_counters WHERE run_key = ?",
            (old_key,),
        ).fetchone()
        self.assertEqual(int(counters[0]), 0)
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])
        tokens = self._rejoin_published_groups()
        aspen = self._student(tokens["Aspen"])
        empty = aspen.get("/api/student/board/mine?since=0").get_json()
        self.assertFalse(empty.get("ended"))
        self.assertNotIn("quit-ink", str(empty))
        self.assertEqual(int(empty.get("board_seq") or 0), 0)
        drawn = self._draw(aspen, "after-quit")
        self.assertEqual(drawn.status_code, 200, drawn.get_json())
        mine = aspen.get("/api/student/board/mine?since=0").get_json()
        seqs = [int(op["board_seq"]) for op in mine.get("ops") or []]
        self.assertEqual(seqs[0], 1)
        self.assertIn("after-quit", str(mine))
        self.assertNotIn("quit-ink", str(mine))

    def test_stale_run_key_after_restart_is_rejected(self) -> None:
        """A POST carrying the previous run key is 409 and stores nothing."""
        tokens = self._publish_two_groups()
        aspen = self._student(tokens["Aspen"])
        first = self._draw(aspen, "before-restart")
        self.assertEqual(first.status_code, 200, first.get_json())
        old_key = self.school.live_board_run_key(self.session_id)
        self.school.finish_live_class(
            self.class_id,
            celebrate=True,
            save_attendance=False,
            save_participation=False,
        )
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])
        new_key = self.school.live_board_run_key(self.session_id)
        tokens = self._rejoin_published_groups()
        aspen = self._student(tokens["Aspen"])
        stale = self._draw(aspen, "stale-ink", run_key=old_key)
        self.assertEqual(stale.status_code, 409, stale.get_json())
        stale_body = stale.get_json()
        self.assertTrue(stale_body.get("stale_run"))
        self.assertFalse(stale_body.get("ended"))
        closed = self.school.boards.conn.execute(
            "SELECT COUNT(*) FROM board_sessions WHERE closed = 1"
        ).fetchone()
        self.assertEqual(int(closed[0]), 0)
        self.assertEqual(self._op_count(old_key), 0)
        self.assertEqual(self._op_count(new_key), 0)
        mine = aspen.get("/api/student/board/mine?since=0").get_json()
        self.assertFalse(mine.get("ended"))
        self.assertNotIn("stale-ink", str(mine))
        self.assertNotIn("before-restart", str(mine))


    def test_every_board_write_route_requires_the_current_run(self) -> None:
        """Missing or foreign run keys are 409 and do not store the stroke."""
        tokens = self._publish_two_groups()
        aspen = self._student(tokens["Aspen"])
        run = self.school.live_board_run_key(self.session_id)
        before = self._op_count(run)

        def post(client, path: str, body: dict):
            """POST one board write and return the response."""
            return client.post(path, json=body)

        def rejected(response, stroke_id: str) -> None:
            """A refused write is stale_run and leaves the board unchanged."""
            self.assertEqual(response.status_code, 409, response.get_json())
            body = response.get_json()
            self.assertTrue(body.get("stale_run"), body)
            self.assertNotIn("ended", body)
            self.assertNotIn(stroke_id, str(self.school.live_session_canvas_sync(self.session_id)))
            self.assertEqual(self._op_count(run), before)

        ink = {
            "x": 0.2,
            "y": 0.2,
            "points": [[0.2, 0.2]],
            "ended": True,
        }
        ops = {
            "ops": [
                {"type": "stroke_add", "id": "route-op", "points": [[0.4, 0.4]]}
            ]
        }
        routes = [
            (aspen, "/api/student/canvas-presence", {**ink, "stroke_id": "miss-student"}),
            (aspen, "/api/student/board/mine/ops", {**ops, "ops": [{"type": "stroke_add", "id": "miss-mine", "points": [[0.4, 0.4]]}]}),
            (
                aspen,
                f"/api/live-sessions/{self.session_id}/canvas-presence",
                {**ink, "stroke_id": "miss-session"},
            ),
            (
                aspen,
                f"/api/live-sessions/{self.session_id}/board/mine/ops",
                {"ops": [{"type": "stroke_add", "id": "miss-session-ops", "points": [[0.5, 0.5]]}]},
            ),
            (
                self.client,
                f"/api/live-sessions/{self.session_id}/canvas-presence",
                {**ink, "stroke_id": "miss-staff"},
            ),
            (
                self.client,
                f"/api/live-sessions/{self.session_id}/board/teacher/ops",
                {"ops": [{"type": "stroke_add", "id": "miss-teacher", "points": [[0.6, 0.6]]}]},
            ),
        ]
        for client, path, body in routes:
            rejected(post(client, path, body), "miss")
        for client, path, body in routes:
            foreign = dict(body)
            foreign["run_key"] = "not-this-run"
            rejected(post(client, path, foreign), "miss")
        ok = post(
            aspen,
            "/api/student/canvas-presence",
            {**ink, "stroke_id": "kept-student", "run_key": run},
        )
        self.assertEqual(ok.status_code, 200, ok.get_json())
        self.assertEqual(ok.get_json().get("run_key"), run)
        mine = aspen.get("/api/student/board/mine?since=0")
        self.assertEqual(mine.status_code, 200, mine.get_json())
        self.assertEqual(mine.get_json().get("run_key"), run)
        self.assertIn("kept-student", str(mine.get_json()))
        staff = post(
            self.client,
            f"/api/live-sessions/{self.session_id}/board/teacher/ops",
            {
                "run_key": run,
                "ops": [{"type": "stroke_add", "id": "kept-teacher", "points": [[0.1, 0.1]]}],
            },
        )
        self.assertEqual(staff.status_code, 200, staff.get_json())
        teacher = self.client.get(
            f"/api/live-sessions/{self.session_id}/board/teacher?since=0"
        )
        self.assertEqual(teacher.get_json().get("run_key"), run)
        self.assertIn("kept-teacher", str(teacher.get_json()))

    def test_shared_board_poll_runs_with_or_without_a_stream(self) -> None:
        """Every shown shared-board tab polls, one request in flight."""
        wb = (LMS_DIR / "static" / "live_whiteboard.js").read_text(encoding="utf-8")
        student = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        staff = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        wire = (LMS_DIR / "static" / "live_news_wire.js").read_text(encoding="utf-8")
        presence = (LMS_DIR / "live_presence.py").read_text(encoding="utf-8")
        boards = (LMS_DIR / "board_ops.py").read_text(encoding="utf-8")
        self.assertIn("export const BOARD_DELTA_POLL_MS = 1750", wb)
        self.assertIn("let inFlight = false", wb)
        self.assertIn("if (stopped || inFlight) return", wb)
        self.assertNotIn("opts.hasStream()", wb)
        self.assertIn("result.ended", wb)
        self.assertIn("stroke_add does not append points onto an existing stroke", wb)
        self.assertIn("/api/student/board/mine?", student)
        self.assertNotIn("hasStream()", student)
        self.assertNotIn("/api/student/board/mine", staff)
        self.assertIn("/board/teacher?since=", staff)
        self.assertIn("run_key:", student)
        self.assertIn("run_key:", staff)
        self.assertIn("Board refreshed for the new class.", wb)
        self.assertNotIn("alert(", student)
        self.assertNotIn("alert(", staff)
        self.assertIn("drop()", student)
        self.assertIn("drop()", staff)
        self.assertIn("hasStream()", wire)
        self.assertIn("pg_advisory_xact_lock", presence)
        self.assertIn("pg_advisory_xact_lock", boards)
        self.assertIn("CREATE TABLE IF NOT EXISTS live_presence_sessions", presence)
        self.assertIn("CREATE TABLE IF NOT EXISTS board_ops", boards)

    def test_client_cursor_drops_ops_at_or_below_and_duplicate_keys(self) -> None:
        """The client keeps seqs above the cursor and ignores a repeated op key."""
        script = """
import { boardOpKey, cursorAfterOps, opsAboveCursor } from './lms/static/live_whiteboard.js';
const ops = [
  { board_seq: 1, board_key: 'shared', id: 'a', type: 'stroke_add' },
  { board_seq: 2, board_key: 'shared', id: 'b', type: 'pts_append' },
  { board_seq: 3, board_key: 'shared', id: 'b', type: 'pts_append' },
  { board_seq: 2, board_key: 'shared', id: 'dup', type: 'stroke_add' },
];
const fresh = opsAboveCursor(ops, 1);
if (fresh.map((op) => op.board_seq).join(',') !== '2,2,3') {
  console.error(JSON.stringify(fresh));
  process.exit(1);
}
const next = cursorAfterOps(1, fresh);
if (next !== 3) process.exit(2);
if (opsAboveCursor(ops, next).length !== 0) process.exit(3);
if (cursorAfterOps(5, [{ board_seq: 2 }]) !== 5) process.exit(4);
const seen = new Set();
const key = boardOpKey({ board_seq: 2, board_key: 'shared', type: 'pts_append', id: 'b' });
if (key !== 'shared:2') process.exit(5);
seen.add(key);
if (seen.has(boardOpKey({ board_seq: 2, board_key: 'shared', type: 'pts_append', id: 'b', points: [[1, 1]] }))) {
  // same op key is the duplicate the client drops
} else {
  process.exit(6);
}
const later = boardOpKey({ board_seq: 4, board_key: 'shared', type: 'pts_append', id: 'b' });
if (later === key || seen.has(later)) process.exit(7);
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)



    def test_client_drops_a_stale_run_without_retrying_it(self) -> None:
        """A changed run key is remembered, and a queued op is not sent."""
        script = """
import { createPresenceQueue, noteBoardRun, boardWriteRejected } from './lms/static/live_whiteboard.js';
const state = { key: '' };
if (noteBoardRun(state, 'abc') !== 'first') process.exit(1);
if (noteBoardRun(state, 'abc') !== 'same') process.exit(2);
if (noteBoardRun(state, 'def') !== 'changed' || state.key !== 'def') process.exit(3);
if (boardWriteRejected(409, { stale_run: true }) !== 'stale') process.exit(4);
if (boardWriteRejected(409, { ended: true }) !== 'ended') process.exit(5);
if (boardWriteRejected(409, { stale_run: true, ended: true }) !== 'stale') process.exit(6);
if (boardWriteRejected(200, { ok: true }) !== '') process.exit(7);
const sent = [];
const queue = createPresenceQueue({
  send(body) {
    sent.push(body.stroke_id || body.op);
    return Promise.resolve({ ok: true, run_key: 'def' });
  },
});
queue.push({ stroke_id: 'a', points: [[0, 0]] });
queue.push({ op: 'stroke_remove', stroke_id: 'b' });
queue.drop();
await new Promise((resolve) => setTimeout(resolve, 20));
queue.push({ stroke_id: 'c', points: [[1, 1]], ended: true });
await new Promise((resolve) => setTimeout(resolve, 20));
if (sent.join(',') !== 'a,c') {
  console.error(JSON.stringify(sent));
  process.exit(8);
}
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)


    def test_student_stale_refresh_discards_the_open_stroke(self) -> None:
        """A student stale_run refresh clears the board and does not re-post.

        The open stroke ends, the op cache is reset, and pointer samples
        after the 409 are not sent under the new run key. An ended refetch
        resets as well.
        """
        script = r"""
import { readFileSync, writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

class HTMLElement {}
class HTMLCanvasElement extends HTMLElement {}
class HTMLButtonElement extends HTMLElement {}
class HTMLInputElement extends HTMLElement {}
class Element {}
globalThis.HTMLElement = HTMLElement;
globalThis.HTMLButtonElement = HTMLButtonElement;
globalThis.HTMLInputElement = HTMLInputElement;
globalThis.HTMLCanvasElement = HTMLCanvasElement;
globalThis.Element = Element;

function makeEl(tag) {
  const listeners = {};
  const node = {
    tagName: String(tag || 'div').toUpperCase(),
    hidden: false,
    style: { cursor: '', removeProperty() {} },
    dataset: {},
    className: '',
    classList: { add() {}, remove() {}, toggle() {} },
    children: [],
    parentElement: null,
    width: 720,
    height: 360,
    appendChild(child) {
      node.children.push(child);
      child.parentElement = node;
      return child;
    },
    insertAdjacentElement(_where, child) {
      return node.appendChild(child);
    },
    addEventListener(type, fn) {
      (listeners[type] ||= []).push(fn);
    },
    removeEventListener() {},
    setAttribute() {},
    getAttribute() { return null; },
    removeAttribute() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    getBoundingClientRect() {
      return { width: 720, height: 360, left: 0, top: 0, right: 720, bottom: 360 };
    },
    setPointerCapture() {},
    releasePointerCapture() {},
    getContext() {
      return {
        setTransform() {}, clearRect() {}, beginPath() {}, moveTo() {},
        lineTo() {}, stroke() {}, fill() {}, arc() {}, save() {}, restore() {},
      };
    },
    dispatch(type, props) {
      const event = {
        type,
        clientX: 0,
        clientY: 0,
        pointerId: 1,
        preventDefault() {},
        stopPropagation() {},
        target: node,
        ...props,
      };
      for (const fn of listeners[type] || []) fn(event);
    },
  };
  return node;
}

const canvas = makeEl('canvas');
Object.setPrototypeOf(canvas, HTMLCanvasElement.prototype);
const body = makeEl('body');
body.dataset = { codename: '' };
const byId = { 'student-canvas': canvas, body };
globalThis.document = {
  body,
  title: '',
  getElementById(id) { return byId[id] || null; },
  createElement(tag) { return makeEl(tag); },
  addEventListener() {},
};
globalThis.window = globalThis;
window.addEventListener = () => {};
window.setTimeout = setTimeout;
window.clearTimeout = clearTimeout;
window.setInterval = setInterval;
window.clearInterval = clearInterval;
window.devicePixelRatio = 1;
window.requestAnimationFrame = (fn) => { fn(); return 1; };
window.location = { origin: 'http://localhost', pathname: '/student' };
window.innerWidth = 1280;

const root = '/workspace';
const staticHref = pathToFileURL(root + '/lms/static/').href;
let src = readFileSync(root + '/lms/static/student-portal.js', 'utf8');
src = src.replaceAll('"/static/common.js"', JSON.stringify(pathToFileURL(root + '/tools/math-game-show/static/common.js').href));
src = src.replaceAll('"/static/', '"' + staticHref);
src = src.replace('void tick();', '');
src = src.replace('setInterval(tickDisplayTime, 250);', '');
src += '\nexport { bindStudentCanvas, refreshStudentBoard, studentBoardRun, studentPresenceQueue };\n';
const out = '/tmp/student-board-refresh-harness.mjs';
writeFileSync(out, src);

const posts = [];
let mode = 'stale';
let refreshSeen = false;
globalThis.fetch = async (url, init) => {
  const target = String(url);
  const json = (status, data) => ({
    ok: status >= 200 && status < 300,
    status,
    async json() { return data; },
  });
  if (target.includes('/api/student/canvas-presence')) {
    posts.push(JSON.parse(init.body));
    if (mode === 'ended') {
      return json(409, { ok: false, error: 'Session has ended.', ended: true });
    }
    if (posts.length === 1) {
      return json(409, { ok: false, error: 'This board is from an earlier class.', stale_run: true });
    }
    return json(200, { ok: true, run_key: 'new-run' });
  }
  if (target.includes('/api/student/board/mine')) {
    refreshSeen = true;
    if (mode === 'ended') return json(200, { ok: false, ended: true });
    return json(200, {
      ok: true,
      run_key: 'new-run',
      snapshot: true,
      canvas_view: { strokes: [], texts: [], cursors: [] },
      board_seq: 0,
      teacher_board_seq: 0,
    });
  }
  return json(200, { ok: true });
};

const mod = await import(pathToFileURL(out).href);
if (typeof mod.bindStudentCanvas.resetRun !== 'function') {
  console.error('resetRun was not wired');
  process.exit(2);
}
let resets = 0;
const origReset = mod.bindStudentCanvas.resetRun;
mod.bindStudentCanvas.resetRun = (view) => {
  resets += 1;
  return origReset(view);
};
mod.bindStudentCanvas.setAlign('team');
mod.studentBoardRun.key = 'old-run';

function waitFor(pred) {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    const tick = () => {
      if (pred()) return resolve();
      if (Date.now() - start > 2000) return reject(new Error('timeout ' + posts.length + ' refresh=' + refreshSeen));
      setTimeout(tick, 15);
    };
    tick();
  });
}

canvas.dispatch('pointerdown', { clientX: 30, clientY: 40 });
canvas.dispatch('pointermove', { clientX: 120, clientY: 80 });
await waitFor(() => posts.length >= 1 && refreshSeen);
const strokeId = posts[0].stroke_id;
if (posts[0].run_key !== 'old-run') {
  console.error('first post key ' + posts[0].run_key);
  process.exit(3);
}
if (mod.studentBoardRun.key !== 'new-run') {
  console.error('run key after refresh ' + mod.studentBoardRun.key);
  process.exit(4);
}
if (resets < 1) {
  console.error('reset not called');
  process.exit(5);
}
const afterRefresh = posts.length;
canvas.dispatch('pointermove', { clientX: 200, clientY: 90 });
canvas.dispatch('pointermove', { clientX: 260, clientY: 140 });
canvas.dispatch('pointerup', { clientX: 280, clientY: 160 });
await new Promise((resolve) => setTimeout(resolve, 250));
const replayed = posts.slice(afterRefresh).filter((body) => body.stroke_id === strokeId);
if (replayed.length || posts.length !== afterRefresh) {
  console.error(JSON.stringify(posts.map((body) => ({
    id: body.stroke_id, key: body.run_key, ended: body.ended, n: (body.points || []).length,
  }))));
  process.exit(6);
}

mode = 'ended';
refreshSeen = false;
const resetsBeforeEnd = resets;
const postsBeforeEnd = posts.length;
mod.studentBoardRun.key = 'old-run';
canvas.dispatch('pointerdown', { clientX: 40, clientY: 50 });
canvas.dispatch('pointermove', { clientX: 140, clientY: 70 });
await waitFor(() => posts.length > postsBeforeEnd && refreshSeen);
canvas.dispatch('pointermove', { clientX: 220, clientY: 100 });
canvas.dispatch('pointerup', { clientX: 250, clientY: 120 });
await new Promise((resolve) => setTimeout(resolve, 250));
if (posts.length !== postsBeforeEnd + 1) {
  console.error('ended re-posted ' + JSON.stringify(posts.slice(postsBeforeEnd)));
  process.exit(7);
}
if (resets <= resetsBeforeEnd) {
  console.error('ended refetch did not reset');
  process.exit(8);
}
if (!posts[postsBeforeEnd].run_key) {
  console.error('ended post missing key');
  process.exit(9);
}
process.exit(0);
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_canvas_presence_in_the_start_after_end_gap_is_409(self) -> None:
        """A stroke after End deletes the row is 409 ended, not a 500.

        Start removes the session a few milliseconds after End. The
        attendee gate can already have passed. ``KeyError: live session N``
        from that gap is a closed class.
        """
        tokens = self._publish_two_groups()
        aspen = self._student(tokens["Aspen"])
        sid = self.session_id
        run_key = self.school.live_board_run_key(sid)
        original = self.school.apply_live_canvas_presence

        def vanish_then_apply(*args, **kwargs):
            """Delete the live row, then run the real presence write."""
            with self.school._lock:
                self.school.conn.execute(
                    "DELETE FROM live_class_sessions WHERE id = ?",
                    (sid,),
                )
                self.school.conn.commit()
            return original(*args, **kwargs)

        self.school.apply_live_canvas_presence = vanish_then_apply
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        drawn = aspen.post(
            "/api/student/canvas-presence",
            json={
                "run_key": run_key,
                "x": 0.4,
                "y": 0.4,
                "points": [[0.4, 0.4]],
                "stroke_id": "gap-stroke",
                "client_batch_id": "gap-batch",
            },
        )
        self.assertEqual(drawn.status_code, 409, drawn.get_data(as_text=True))
        body = drawn.get_json()
        self.assertTrue(body.get("ended"))
        self.assertNotIn("stale_run", body)




    def test_pen_up_posts_stroke_end_when_the_point_repeats(self) -> None:
        """A duplicate pen-up sample still posts the stroke_end batch.

        ``pushPoint`` drops a point that matches the last sample, and
        ``flushPending`` used to return when nothing new was queued. The
        lift has to reach the server anyway. ``resetRun`` also clears
        cursor chips.
        """
        script = r"""
import { bindWhiteboard } from './lms/static/live_whiteboard.js';

class HTMLElement {}
class HTMLCanvasElement extends HTMLElement {}
class HTMLButtonElement extends HTMLElement {}
globalThis.HTMLElement = HTMLElement;
globalThis.HTMLCanvasElement = HTMLCanvasElement;
globalThis.HTMLButtonElement = HTMLButtonElement;
globalThis.Element = class Element {};

function makeEl() {
  const listeners = {};
  const node = {
    hidden: false,
    style: { cursor: '', removeProperty() {} },
    dataset: {},
    className: '',
    classList: { add() {}, remove() {}, toggle() {} },
    innerHTML: '',
    width: 720,
    height: 360,
    parentElement: null,
    appendChild(child) { child.parentElement = node; return child; },
    insertAdjacentElement(_where, child) { return node.appendChild(child); },
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    removeEventListener() {},
    setAttribute() {},
    getAttribute() { return null; },
    querySelector() { return null; },
    getBoundingClientRect() {
      return { width: 720, height: 360, left: 0, top: 0, right: 720, bottom: 360 };
    },
    setPointerCapture() {},
    getContext() {
      return {
        setTransform() {}, clearRect() {}, beginPath() {}, moveTo() {},
        lineTo() {}, stroke() {}, fill() {}, arc() {}, save() {}, restore() {},
      };
    },
    dispatch(type, props) {
      const event = {
        clientX: 0, clientY: 0, pointerId: 1, preventDefault() {},
        stopPropagation() {}, target: node, ...props,
      };
      for (const fn of listeners[type] || []) fn(event);
    },
  };
  return node;
}

const canvas = makeEl();
Object.setPrototypeOf(canvas, HTMLCanvasElement.prototype);
const cursors = makeEl();
Object.setPrototypeOf(cursors, HTMLElement.prototype);
globalThis.document = {
  body: makeEl(),
  createElement() { return makeEl(); },
  getElementById() { return null; },
  addEventListener() {},
};
globalThis.window = globalThis;
window.addEventListener = () => {};
window.setTimeout = setTimeout;
window.clearTimeout = clearTimeout;
window.devicePixelRatio = 1;
window.requestAnimationFrame = (fn) => { fn(); return 1; };

const batches = [];
const board = bindWhiteboard(canvas, {
  cursorLayer: cursors,
  owner: () => 'teacher',
  onPoints(points, ended, strokeId) {
    batches.push({ ended: Boolean(ended), strokeId, n: points.length });
  },
});
board.importRemote({
  cursors: [{ name: 'Aspen', owner: '1', x: 0.2, y: 0.4, color: '#123456' }],
}, { collab: false });
if (!String(cursors.innerHTML).includes('Aspen')) {
  console.error('cursor missing ' + cursors.innerHTML);
  process.exit(1);
}
board.resetRun({ strokes: [], texts: [], cursors: [] });
if (String(cursors.innerHTML).includes('Aspen') || String(cursors.innerHTML).includes('canvas-cursor-chip')) {
  console.error('cursor stayed ' + cursors.innerHTML);
  process.exit(2);
}
canvas.dispatch('pointerdown', { clientX: 30, clientY: 40 });
canvas.dispatch('pointermove', { clientX: 140, clientY: 90 });
await new Promise((resolve) => setTimeout(resolve, 120));
const opened = batches.filter((row) => !row.ended);
if (!opened.length) {
  console.error('no open batch ' + JSON.stringify(batches));
  process.exit(3);
}
const strokeId = opened[0].strokeId;
canvas.dispatch('pointerup', { clientX: 140, clientY: 90 });
await new Promise((resolve) => setTimeout(resolve, 40));
const ended = batches.filter((row) => row.ended && row.strokeId === strokeId);
if (!ended.length) {
  console.error(JSON.stringify(batches));
  process.exit(4);
}
process.exit(0);
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_staff_ended_refresh_discards_the_open_stroke(self) -> None:
        """End with no restart resets the teacher board and does not re-post.

        The poll's ``ended`` payload and a 409 ``ended`` write both call
        ``resetRun``. Pointer samples after that stay off the wire.
        """
        script = r"""
import { readFileSync, writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

class HTMLElement {}
class HTMLCanvasElement extends HTMLElement {}
class HTMLButtonElement extends HTMLElement {}
class HTMLInputElement extends HTMLElement {}
class HTMLFormElement extends HTMLElement {}
class HTMLDialogElement extends HTMLElement {}
class HTMLSelectElement extends HTMLElement {}
class HTMLIFrameElement extends HTMLElement {}
globalThis.HTMLElement = HTMLElement;
globalThis.HTMLCanvasElement = HTMLCanvasElement;
globalThis.HTMLButtonElement = HTMLButtonElement;
globalThis.HTMLInputElement = HTMLInputElement;
globalThis.HTMLFormElement = HTMLFormElement;
globalThis.HTMLDialogElement = HTMLDialogElement;
globalThis.HTMLSelectElement = HTMLSelectElement;
globalThis.HTMLIFrameElement = HTMLIFrameElement;
globalThis.Element = class Element {};

function makeEl(tag) {
  const listeners = {};
  const node = {
    tagName: String(tag || 'div').toUpperCase(),
    hidden: false,
    style: { cursor: '', removeProperty() {} },
    dataset: {},
    className: '',
    classList: { add() {}, remove() {}, toggle() {} },
    children: [],
    parentElement: null,
    width: 720,
    height: 360,
    innerHTML: '',
    appendChild(child) { node.children.push(child); child.parentElement = node; return child; },
    insertAdjacentElement(_where, child) { return node.appendChild(child); },
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    removeEventListener() {},
    setAttribute() {},
    getAttribute() { return null; },
    removeAttribute() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    getBoundingClientRect() {
      return { width: 720, height: 360, left: 0, top: 0, right: 720, bottom: 360 };
    },
    setPointerCapture() {},
    getContext() {
      return {
        setTransform() {}, clearRect() {}, beginPath() {}, moveTo() {},
        lineTo() {}, stroke() {}, fill() {}, arc() {}, save() {}, restore() {},
      };
    },
    dispatch(type, props) {
      const event = {
        clientX: 0, clientY: 0, pointerId: 1, preventDefault() {},
        stopPropagation() {}, target: node, ...props,
      };
      for (const fn of listeners[type] || []) fn(event);
    },
  };
  return node;
}

const canvas = makeEl('canvas');
Object.setPrototypeOf(canvas, HTMLCanvasElement.prototype);
const root = makeEl('div');
root.dataset = { classId: '1', liveSessionId: '9', apView: '' };
const byId = { 'ap-root': root, 'live-canvas-stub': canvas };
globalThis.document = {
  body: makeEl('body'),
  title: '',
  getElementById(id) { return byId[id] || null; },
  createElement(tag) { return makeEl(tag); },
  addEventListener() {},
  querySelector() { return null; },
  querySelectorAll() { return []; },
};
globalThis.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
globalThis.location = {
  search: '', href: 'http://localhost/staff/class/1', origin: 'http://localhost',
  pathname: '/staff/class/1',
};
globalThis.window = globalThis;
window.addEventListener = () => {};
window.setTimeout = setTimeout;
window.clearTimeout = clearTimeout;
window.setInterval = setInterval;
window.clearInterval = clearInterval;
window.devicePixelRatio = 1;
window.requestAnimationFrame = (fn) => { fn(); return 1; };
window.location = location;
window.innerWidth = 1280;
window.confirm = () => false;

const repo = '/workspace';
const staticHref = pathToFileURL(repo + '/lms/static/').href;
let src = readFileSync(repo + '/lms/static/staff_ap.js', 'utf8');
src = src.replaceAll('"/static/common.js"', JSON.stringify(pathToFileURL(repo + '/tools/math-game-show/static/common.js').href));
src = src.replaceAll('"/static/', '"' + staticHref);
src += '\nexport { bindEphemeralCanvas, refreshTeacherBoard, teacherBoard, teacherBoardRun, teacherState };\n';
const out = '/tmp/staff-board-refresh-harness.mjs';
writeFileSync(out, src);

const posts = [];
globalThis.fetch = async (url, init) => {
  const target = String(url);
  const json = (status, data) => ({
    ok: status >= 200 && status < 300,
    status,
    async json() { return data; },
  });
  if (target.includes('/canvas-presence') && init && init.method === 'POST') {
    posts.push(JSON.parse(init.body));
    return json(409, { ok: false, error: 'Session has ended.', ended: true });
  }
  if (target.includes('/board/teacher')) {
    return json(200, { ok: true, ended: true, status: 'ended', run_key: 'run-old' });
  }
  return json(200, { ok: true });
};

const mod = await import(pathToFileURL(out).href);
mod.bindEphemeralCanvas();
if (!mod.teacherBoard || typeof mod.teacherBoard.resetRun !== 'function') {
  console.error('teacher board was not bound');
  process.exit(2);
}
let resets = 0;
const origReset = mod.teacherBoard.resetRun;
mod.teacherBoard.resetRun = (view) => {
  resets += 1;
  return origReset(view);
};
mod.teacherState.canvas_align = 'team';
mod.teacherBoardRun.key = 'run-old';

function waitFor(pred, label) {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    const tick = () => {
      if (pred()) return resolve();
      if (Date.now() - start > 2000) return reject(new Error(label + ' resets=' + resets + ' posts=' + posts.length));
      setTimeout(tick, 15);
    };
    tick();
  });
}

await waitFor(() => resets >= 1, 'poll ended');
canvas.dispatch('pointerdown', { clientX: 30, clientY: 40 });
canvas.dispatch('pointermove', { clientX: 150, clientY: 80 });
await waitFor(() => posts.length >= 1, 'first post');
const strokeId = posts[0].stroke_id;
if (!posts[0].run_key) {
  console.error('missing run key');
  process.exit(3);
}
const after = posts.length;
const resetsAfterPost = resets;
canvas.dispatch('pointermove', { clientX: 220, clientY: 110 });
canvas.dispatch('pointerup', { clientX: 260, clientY: 140 });
await new Promise((resolve) => setTimeout(resolve, 250));
const replayed = posts.slice(after).filter((body) => body.stroke_id === strokeId);
if (replayed.length || posts.length !== after) {
  console.error(JSON.stringify(posts.map((body) => ({
    id: body.stroke_id, ended: body.ended, n: (body.points || []).length,
  }))));
  process.exit(4);
}
if (resets < resetsAfterPost) {
  console.error('ended write did not reset');
  process.exit(5);
}
process.exit(0);
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)




if __name__ == "__main__":
    unittest.main()

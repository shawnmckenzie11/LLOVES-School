#!/usr/bin/env python3
"""Append-only whiteboard ops: sequence, delta, isolation, undo, purge."""

from __future__ import annotations

import os
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
    SqliteBoardOps,
)


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
            json={"op": "stroke_remove", "stroke_id": "aspen-undo"},
        )
        self.assertEqual(stolen.status_code, 403, stolen.get_json())
        before = birch.get("/api/student/board/mine?since=0").get_json()
        seen = int(before["board_seq"])
        removed = aspen.post(
            "/api/student/canvas-presence",
            json={"op": "stroke_remove", "stroke_id": "aspen-undo"},
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
        self.assertGreater(self.school.boards.current_seq(self.session_id, key), 0)
        ended = self.school.end_live_class_session(self.session_id)
        self.assertEqual(ended["status"], "ended")
        self.assertEqual(self.school.boards.load_keys(self.session_id, None), [])
        self.assertEqual(self.school.boards.current_seq(self.session_id, key), 0)

    def test_no_stream_poll_is_one_in_flight(self) -> None:
        """The shared-board poll is 1.5–2s, one in flight, and skips a stream."""
        wb = (LMS_DIR / "static" / "live_whiteboard.js").read_text(encoding="utf-8")
        student = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        wire = (LMS_DIR / "static" / "live_news_wire.js").read_text(encoding="utf-8")
        self.assertIn("export const BOARD_DELTA_POLL_MS = 1750", wb)
        self.assertIn("let inFlight = false", wb)
        self.assertIn("if (stopped || inFlight) return", wb)
        self.assertIn("opts.hasStream()", wb)
        self.assertIn("result.ended", wb)
        self.assertIn("/api/student/board/mine?", student)
        self.assertIn("hasStream()", wire)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Stable whiteboard team buckets across lock restore (MCK-218)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from live_board_assignments import assignment_board_key, board_key_for_sort_order  # noqa: E402


class LiveBoardAssignmentTests(unittest.TestCase):
    """Board keys stay on team:<sort_order> when game team rows are reminted."""

    def setUp(self) -> None:
        """One staff class, live session, and four-student roster."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.staff = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
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
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple", "Aspen", "Birch", "Cedar"],
            },
        )
        self.assertEqual(created.status_code, 200, created.get_json())
        self.class_id = int(created.get_json()["class"]["id"])
        run = self.staff.post(
            f"/staff/class/{self.class_id}/run-live",
            follow_redirects=False,
        )
        self.assertEqual(run.status_code, 302)
        live = self.school.get_active_live_session_for_class(self.class_id)
        assert live is not None
        self.session_id = int(live["id"])
        self.run_key = self.school.live_board_run_key(self.session_id)

    def tearDown(self) -> None:
        """Close sqlite and remove the temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _assign(self) -> None:
        """Begin attendance and assign two teams via the real API."""
        begin = self.staff.post(
            f"/api/classes/{self.class_id}/begin",
            json={"meeting_date": "2026-09-09"},
        )
        self.assertEqual(begin.status_code, 200, begin.get_json())
        ids = [int(student["id"]) for student in begin.get_json()["students"]]
        self.student_id = ids[0]
        self.staff.post(
            f"/api/classes/{self.class_id}/game/attendance",
            json={"present_ids": ids, "meeting_date": "2026-09-09"},
        )
        assigned = self.staff.post(
            f"/api/classes/{self.class_id}/game/assign",
            json={"n_teams": 2, "mode": "random", "present_ids": ids},
        )
        self.assertEqual(assigned.status_code, 200, assigned.get_json())

    def test_assign_writes_team_sort_order_board_key(self) -> None:
        """Generate teams records team:0 / team:1 buckets for the live run."""
        self._assign()
        key = assignment_board_key(
            self.school.conn,
            class_id=self.class_id,
            run_key=self.run_key,
            student_id=self.student_id,
        )
        self.assertTrue(str(key or "").startswith("team:"))
        access = self.school.student_board_access(self.session_id, self.student_id)
        self.assertEqual(access["collab_key"], key)

    def test_simulated_restart_keeps_board_key(self) -> None:
        """A new SchoolDB connection still resolves the same collab bucket."""
        self._assign()
        before = self.school.student_live_board_key(self.session_id, self.student_id)
        self.school.game._team_index_cache.clear()
        after = self.school.student_live_board_key(self.session_id, self.student_id)
        self.assertEqual(before, after)

    def test_restore_after_wipe_keeps_board_key(self) -> None:
        """Wiped game rows + lock restore do not move the student's board bucket."""
        self._assign()
        before = self.school.student_live_board_key(self.session_id, self.student_id)
        with self.school.game._lock:
            game = self.school.game._game_row(self.class_id)
            game_id = int(game["id"])
            self.school.game.conn.execute(
                "DELETE FROM game_memberships WHERE game_id = ?", (game_id,)
            )
            self.school.game.conn.execute(
                "DELETE FROM game_teams WHERE game_id = ?", (game_id,)
            )
            self.school.game.conn.commit()
        self.school.game._forget_team_index(class_id=self.class_id)
        self.school.game.restore_team_lock(self.class_id, create=False)
        after = self.school.student_live_board_key(self.session_id, self.student_id)
        self.assertEqual(before, after)

    def test_double_assign_is_idempotent(self) -> None:
        """ON CONFLICT DO NOTHING keeps the first bucket when assign runs twice."""
        self._assign()
        first = assignment_board_key(
            self.school.conn,
            class_id=self.class_id,
            run_key=self.run_key,
            student_id=self.student_id,
        )
        self.school.record_live_board_assignment_for_student(
            self.session_id, self.student_id, 99
        )
        second = assignment_board_key(
            self.school.conn,
            class_id=self.class_id,
            run_key=self.run_key,
            student_id=self.student_id,
        )
        self.assertEqual(first, second)

    def test_test_student_excluded_from_assignments(self) -> None:
        """Test Student never receives a live_board_assignments row."""
        test_row = self.school.ensure_class_test_student(self.class_id)
        assert test_row is not None
        self._assign()
        key = assignment_board_key(
            self.school.conn,
            class_id=self.class_id,
            run_key=self.run_key,
            student_id=int(test_row["student_id"]),
        )
        self.assertIsNone(key)
        collab = self.school.student_live_board_key(
            self.session_id, int(test_row["student_id"])
        )
        self.assertEqual(collab, "shared")

    def test_discarded_run_deletes_assignment_rows(self) -> None:
        """Discarding a test run clears its board assignment rows."""
        self._assign()
        self.school.conn.execute(
            """
            INSERT INTO participation_items (
                class_id, run_key, live_module, live_slot, question_key,
                item_json, prompt_kind, offered_at
            ) VALUES (?, ?, 'M1', 'C1', 'item:q0', '{}', 'poll', '2026-09-09T12:00:00')
            """,
            (self.class_id, self.run_key),
        )
        self.school.conn.commit()
        self.school.set_participation_run_discarded(
            self.class_id, self.run_key, True, user_id=int(self.teacher["id"])
        )
        key = assignment_board_key(
            self.school.conn,
            class_id=self.class_id,
            run_key=self.run_key,
            student_id=self.student_id,
        )
        self.assertIsNone(key)


if __name__ == "__main__":
    unittest.main()

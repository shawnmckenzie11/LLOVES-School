#!/usr/bin/env python3
"""Student ids are monotonic (max+1); deleted ids are never reused."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from student_portal import REJOIN_COOKIE_NAME  # noqa: E402


class StudentIdMonotonicTests(unittest.TestCase):
    """New roster rows always get a fresh id with no inherited child rows."""

    def setUp(self) -> None:
        """Staff-owned class with one live session."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCF3M"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        code = self.school.get_user_by_email("teacher@gmail.com")["verification_code"]
        self.client.post("/verify-email", data={"code": code})
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple", "Cedar"],
            },
        )
        self.class_id = int(created.get_json()["class"]["id"])
        live = self.school.start_live_class_session(self.class_id, int(teacher["id"]))
        self.session_id = int(live["id"])
        self.session_code = str(live["session_code"])

    def tearDown(self) -> None:
        """Close sqlite and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def test_deleted_student_id_is_not_reused_and_rows_do_not_inherit(self) -> None:
        """After delete, the next insert uses max+1; old cookie and rows stay orphaned."""
        game = self.school.game
        with game._lock:
            cedar_id = int(
                game.conn.execute(
                    "SELECT id FROM students WHERE class_id = ? AND codename = 'Cedar'",
                    (self.class_id,),
                ).fetchone()[0]
            )
        join = self.app.test_client()
        rv = join.post(
            "/auth/student-code",
            json={"code": self.session_code, "name": "Cedar"},
        )
        self.assertLess(rv.status_code, 400, rv.get_data(as_text=True))
        attendee = self.school.list_live_session_attendees(self.session_id)[0]
        old_token = str(attendee["visit_token"])
        begin = self.client.post(
            f"/api/classes/{self.class_id}/begin",
            json={"meeting_date": "2026-09-11"},
        )
        self.assertEqual(begin.status_code, 200)
        self.client.post(
            f"/api/classes/{self.class_id}/game/finalize-attendance",
            json={"present_ids": [cedar_id], "meeting_date": "2026-09-11"},
        )
        with game._lock:
            game.conn.execute(
                """
                INSERT INTO participation_answers (
                    class_id, student_id, run_key, live_module, question_key,
                    source, answer_json, answered_at
                ) VALUES (?, ?, 'rk1', 'M1', 'item:1', 'catchup', '{}', '2026-09-11T12:00:00')
                """,
                (self.class_id, cedar_id),
            )
            game.conn.commit()
        deleted = self.client.post(
            f"/api/classes/{self.class_id}/students/delete",
            json={"student_id": cedar_id},
        )
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        added = self.client.post(
            f"/api/classes/{self.class_id}/students",
            json={"codename": "Birch"},
        )
        self.assertEqual(added.status_code, 200, added.get_json())
        with game._lock:
            birch_id = int(
                game.conn.execute(
                    "SELECT id FROM students WHERE class_id = ? AND codename = 'Birch'",
                    (self.class_id,),
                ).fetchone()[0]
            )
        self.assertGreater(birch_id, cedar_id)
        with game._lock:
            inherited = game.conn.execute(
                """
                SELECT COUNT(*) AS n FROM participation_answers
                WHERE student_id = ? AND question_key = 'item:1'
                """,
                (birch_id,),
            ).fetchone()["n"]
            score_row = game.conn.execute(
                """
                SELECT present, points FROM session_scores
                WHERE student_id = ? ORDER BY session_id DESC LIMIT 1
                """,
                (birch_id,),
            ).fetchone()
        self.assertEqual(int(inherited), 0)
        self.assertIsNotNone(score_row)
        self.assertEqual(int(score_row["present"]), 0)
        self.assertEqual(float(score_row["points"] or 0), 0.0)
        resolved = self.school.resolve_student_visit_token(old_token)
        if resolved and resolved.get("student_id") is not None:
            self.assertNotEqual(int(resolved["student_id"]), birch_id)
        with game._lock:
            gone = game.conn.execute(
                "SELECT 1 FROM students WHERE id = ?", (cedar_id,)
            ).fetchone()
        self.assertIsNone(gone)



class TestStudentLiveUiTests(unittest.TestCase):
    """Test Student is omitted from live Class List count and team assign preview."""

    def setUp(self) -> None:
        """Two real students plus synthetic Test Student."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCF3M"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        code = self.school.get_user_by_email("teacher@gmail.com")["verification_code"]
        self.client.post("/verify-email", data={"code": code})
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Aspen", "Birch"],
            },
        )
        self.class_id = int(created.get_json()["class"]["id"])
        live = self.school.start_live_class_session(self.class_id, int(teacher["id"]))
        self.session_id = int(live["id"])
        self.test_id = int(self.school.ensure_class_test_student(self.class_id)["student_id"])

    def tearDown(self) -> None:
        """Close sqlite and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def test_class_list_projection_omits_test_student(self) -> None:
        """Live Class List rows and count exclude the synthetic roster row."""
        rows = self.school.live_class_roster_projection(self.session_id)
        ids = {int(r["student_id"]) for r in rows}
        self.assertNotIn(self.test_id, ids)
        self.assertEqual(len(rows), 2)

    def test_game_state_teams_omit_test_student(self) -> None:
        """Assign-teams preview uses only real students (Test Student not on Team 1)."""
        game = self.school.game
        with game._lock:
            real_ids = [
                int(r[0])
                for r in game.conn.execute(
                    """
                    SELECT id FROM students
                    WHERE class_id = ? AND COALESCE(is_test_student, 0) = 0
                    ORDER BY id
                    """,
                    (self.class_id,),
                ).fetchall()
            ]
        game.begin_game(self.class_id)
        game.save_attendance(self.class_id, real_ids)
        state = game.assign_teams(self.class_id, 2, "balanced")
        member_ids = {
            int(m["id"])
            for team in state["teams"]
            for m in team.get("members") or []
        }
        self.assertNotIn(self.test_id, member_ids)
        self.assertEqual(len(member_ids), 2)


if __name__ == "__main__":
    unittest.main()

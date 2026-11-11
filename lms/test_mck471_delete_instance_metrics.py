#!/usr/bin/env python3
"""MCK-471: deleting a Lesson Slides instance drops its attendance and participation."""

from __future__ import annotations

import json
import logging
import os
import sys
import unittest
import uuid
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from test_participation_mck198 import Base  # noqa: E402


def _count(conn, sql: str, params: tuple) -> int:
    """Return one integer count. The SQL must select ``n``.

    Args:
        conn: Open sqlite connection.
        sql: SELECT that aliases the count as ``n``.
        params: Bound values for ``sql``.
    """
    row = conn.execute(sql, params).fetchone()
    return int(row["n"] or 0)


class DeleteInstanceMetricsTests(Base):
    """One deleted run loses its marks. The sibling run stays."""

    def _end_marked(self, module: str, slot: str) -> tuple[int, str, int]:
        """End one real class after one roster student joins and answers.

        Args:
            module: Teacher module, such as ``M1``.
            slot: Teacher slot, such as ``C1``.

        Returns:
            ``(live_class_sessions.id, run_key, tracker session id)``.
        """
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=False
        )
        sid = int(live["id"])
        now = "2026-10-19T18:05:00"
        self.school.conn.execute(
            """
            UPDATE live_class_sessions
            SET teacher_state_json = ?, started_at = ?
            WHERE id = ?
            """,
            (json.dumps({"live_module": module, "live_slot": slot}), now, sid),
        )
        prompt_id = self.school.conn.execute(
            """
            INSERT INTO live_session_prompts (
                live_session_id, slide_index, kind, payload, created_at, updated_at
            ) VALUES (?, 1, 'poll', ?, ?, ?)
            """,
            (
                sid,
                json.dumps({"text": "q0", "type": "mc", "options": [{"text": "A"}]}),
                now,
                now,
            ),
        ).lastrowid
        self.school.conn.execute(
            """
            INSERT INTO live_session_items (
                live_session_id, placement_key, item_id, stage, kind, item_json,
                prompt_id, status, publish_mode, response_mode, published_at,
                created_at, updated_at
            ) VALUES (?, 'q0', 'q0', 'round', 'poll', ?, ?, 'closed',
                      'individual', 'individual', ?, ?, ?)
            """,
            (
                sid,
                json.dumps(
                    {
                        "text": "q0",
                        "type": "mc",
                        "options": [{"text": "A"}],
                        "correct_answer": "A",
                        "key": "A",
                    }
                ),
                int(prompt_id),
                now,
                now,
                now,
            ),
        )
        self.school.conn.execute(
            """
            INSERT INTO live_session_responses (
                prompt_id, student_id, response_json, created_at, updated_at
            ) VALUES (?, ?, '{}', ?, ?)
            """,
            (int(prompt_id), int(self.students[0]), now, now),
        )
        self.school.conn.commit()
        self.school.join_live_class_session(sid, int(self.students[0]))
        self.school.finish_live_class(self.class_id, celebrate=True, live_run=True)
        row = self.school.conn.execute(
            """
            SELECT run_key, mgs_session_id
            FROM live_class_sessions WHERE id = ?
            """,
            (sid,),
        ).fetchone()
        self.assertIsNotNone(row["mgs_session_id"])
        return sid, str(row["run_key"]), int(row["mgs_session_id"])

    def _runs(self, run_key: str) -> int:
        """Count participation runs for this class and run key.

        Args:
            run_key: ``live_class_sessions.run_key``.
        """
        return _count(
            self.school.conn,
            """
            SELECT COUNT(*) AS n FROM participation_runs
            WHERE class_id = ? AND run_key = ?
            """,
            (self.class_id, run_key),
        )

    def _present(self, tracker_id: int) -> int:
        """Sum of present marks on one tracker column.

        Args:
            tracker_id: ``sessions.id``.
        """
        return _count(
            self.school.conn,
            """
            SELECT COALESCE(SUM(present), 0) AS n
            FROM session_scores WHERE session_id = ?
            """,
            (int(tracker_id),),
        )

    def test_delete_drops_that_instance_and_keeps_the_sibling(self) -> None:
        """Deleting C1 removes its marks. C2 attendance and participation stay."""
        _first_id, first_key, first_tracker = self._end_marked("M1", "C1")
        _second_id, second_key, second_tracker = self._end_marked("M1", "C2")
        self.assertGreater(self._runs(first_key), 0)
        self.assertGreater(self._runs(second_key), 0)
        self.assertGreaterEqual(self._present(first_tracker), 1)
        self.assertGreaterEqual(self._present(second_tracker), 1)
        before = self.school.begin_module_board(self.class_id)
        self.assertEqual(int(before["held"]), 2)
        self.school.delete_lesson_slides_entry(
            self.class_id, "M1", "C1", False, first_key
        )
        self.assertEqual(self._runs(first_key), 0)
        self.assertEqual(self._present(first_tracker), 0)
        self.assertGreater(self._runs(second_key), 0)
        self.assertGreaterEqual(self._present(second_tracker), 1)
        after = self.school.begin_module_board(self.class_id)
        self.assertEqual(int(after["held"]), 1)
        self.assertEqual(after["students"][0]["attended_text"], "1 / 1")
        answers = _count(
            self.school.conn,
            """
            SELECT COUNT(*) AS n FROM participation_answers
            WHERE class_id = ? AND run_key = ?
            """,
            (self.class_id, first_key),
        )
        self.assertEqual(answers, 0)
        sibling_answers = _count(
            self.school.conn,
            """
            SELECT COUNT(*) AS n FROM participation_answers
            WHERE class_id = ? AND run_key = ?
            """,
            (self.class_id, second_key),
        )
        self.assertGreater(sibling_answers, 0)

    def test_report_ignores_a_dismissed_run_whose_rows_were_missed(self) -> None:
        """A missed participation row for a deleted run does not count."""
        _sid, run_key, _tracker = self._end_marked("M1", "C1")
        self.school.delete_lesson_slides_entry(
            self.class_id, "M1", "C1", False, run_key
        )
        now = "2026-10-19T18:05:00"
        self.school.conn.execute(
            """
            INSERT INTO participation_runs (
                class_id, run_key, is_real, auto_is_real, discarded, ended_at
            ) VALUES (?, ?, 1, 1, 0, ?)
            """,
            (self.class_id, run_key, now),
        )
        self.school.conn.execute(
            """
            INSERT INTO participation_items (
                class_id, run_key, live_module, live_slot, question_key,
                item_json, prompt_kind, offered_at
            ) VALUES (?, ?, 'M1', 'C1', 'item:q0', '{}', 'mc', ?)
            """,
            (self.class_id, run_key, now),
        )
        self.school.conn.execute(
            """
            INSERT INTO participation_answers (
                class_id, run_key, live_module, question_key, student_id,
                source, answer_json, answered_at
            ) VALUES (?, ?, 'M1', 'item:q0', ?, 'live', '{}', ?)
            """,
            (self.class_id, run_key, int(self.students[0]), now),
        )
        self.school.conn.commit()
        board = self.school.begin_module_board(self.class_id)
        self.assertEqual(int(board["held"]), 0)
        self.assertEqual(board["students"][0]["participation_text"], "0 / 0")

    def test_orphan_cleanup_removes_only_orphans_and_is_a_noop(self) -> None:
        """Boot cleanup deletes dismissed runs only, then deletes nothing."""
        _sid, kept_key, kept_tracker = self._end_marked("M1", "C2")
        orphan_key = f"orphan-{uuid.uuid4().hex}"
        now = "2026-10-19T18:05:00"
        self.school.conn.execute(
            """
            INSERT INTO lesson_slides_dismissed (
                class_id, live_module, live_slot, is_test, run_key, dismissed_at
            ) VALUES (?, 'M1', 'C1', 0, ?, ?)
            """,
            (self.class_id, orphan_key, now),
        )
        self.school.conn.execute(
            """
            INSERT INTO participation_runs (
                class_id, run_key, is_real, auto_is_real, discarded, ended_at
            ) VALUES (?, ?, 1, 1, 0, ?)
            """,
            (self.class_id, orphan_key, now),
        )
        self.school.conn.execute(
            """
            INSERT INTO participation_items (
                class_id, run_key, live_module, live_slot, question_key,
                item_json, prompt_kind, offered_at
            ) VALUES (?, ?, 'M1', 'C1', 'item:q0', '{}', 'mc', ?)
            """,
            (self.class_id, orphan_key, now),
        )
        self.school.conn.execute(
            """
            INSERT INTO participation_answers (
                class_id, run_key, live_module, question_key, student_id,
                source, answer_json, answered_at
            ) VALUES (?, ?, 'M1', 'item:q0', ?, 'live', '{}', ?)
            """,
            (self.class_id, orphan_key, int(self.students[0]), now),
        )
        cur = self.school.conn.execute(
            """
            INSERT INTO sessions (class_id, starts_at, header_label, status, source)
            VALUES (?, ?, 'M1 C1', 'ended', 'game')
            """,
            (self.class_id, now),
        )
        orphan_tracker = int(cur.lastrowid)
        self.school.conn.execute(
            """
            INSERT INTO session_scores (session_id, student_id, present, late, points)
            VALUES (?, ?, 1, 0, 1)
            """,
            (orphan_tracker, int(self.students[0])),
        )
        self.school.conn.execute(
            """
            INSERT INTO live_participation_credits (
                run_key, student_id, session_id, points, points_r1, points_r2, points_r3
            ) VALUES (?, ?, ?, 1, 0, 0, 0)
            """,
            (orphan_key, int(self.students[0]), orphan_tracker),
        )
        plain_key = f"plain-{uuid.uuid4().hex}"
        self.school.conn.execute(
            """
            INSERT INTO participation_runs (
                class_id, run_key, is_real, auto_is_real, discarded, ended_at
            ) VALUES (?, ?, 1, 1, 0, ?)
            """,
            (self.class_id, plain_key, now),
        )
        kept_session_id = int(
            self.school.conn.execute(
                "SELECT id FROM live_class_sessions WHERE run_key = ?",
                (kept_key,),
            ).fetchone()["id"]
        )
        self.school.conn.execute(
            """
            INSERT INTO live_session_attendees (
                live_session_id, participant_uuid, visit_token, joined_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                kept_session_id,
                f"p-{uuid.uuid4().hex}",
                f"v-{uuid.uuid4().hex}",
                now,
            ),
        )
        self.school.conn.execute("PRAGMA foreign_keys = OFF")
        self.school.conn.execute(
            """
            INSERT INTO live_session_attendees (
                live_session_id, participant_uuid, visit_token, joined_at
            ) VALUES (?, ?, ?, ?)
            """,
            (999999, f"p-{uuid.uuid4().hex}", f"v-{uuid.uuid4().hex}", now),
        )
        self.school.conn.execute("PRAGMA foreign_keys = ON")
        self.school.conn.commit()
        kept_present = self._present(kept_tracker)
        kept_runs = self._runs(kept_key)
        sibling_attendees = _count(
            self.school.conn,
            """
            SELECT COUNT(*) AS n FROM live_session_attendees
            WHERE live_session_id = ?
            """,
            (kept_session_id,),
        )
        self.assertGreaterEqual(sibling_attendees, 1)
        logging.disable(logging.NOTSET)
        try:
            with self.assertLogs("school_db", level="INFO") as first_logs:
                first = self.school.purge_orphaned_attendance_participation()
        finally:
            logging.disable(logging.WARNING)
        self.assertGreater(first["participation"], 0)
        self.assertGreaterEqual(first["attendance_columns"], 1)
        self.assertGreaterEqual(first["attendees"], 1)
        self.assertEqual(self._runs(orphan_key), 0)
        self.assertEqual(self._present(orphan_tracker), 0)
        self.assertEqual(self._runs(kept_key), kept_runs)
        self.assertEqual(self._present(kept_tracker), kept_present)
        self.assertEqual(self._runs(plain_key), 1)
        still_there = _count(
            self.school.conn,
            """
            SELECT COUNT(*) AS n FROM live_session_attendees
            WHERE live_session_id = (
                SELECT id FROM live_class_sessions WHERE run_key = ?
            )
            """,
            (kept_key,),
        )
        self.assertEqual(still_there, sibling_attendees)
        dangling = _count(
            self.school.conn,
            """
            SELECT COUNT(*) AS n FROM live_session_attendees
            WHERE live_session_id = 999999
            """,
            (),
        )
        self.assertEqual(dangling, 0)
        logging.disable(logging.NOTSET)
        try:
            with self.assertLogs("school_db", level="INFO") as second_logs:
                second = self.school.purge_orphaned_attendance_participation()
        finally:
            logging.disable(logging.WARNING)
        self.assertEqual(second["participation"], 0)
        self.assertEqual(second["engagement"], 0)
        self.assertEqual(second["attendance_columns"], 0)
        self.assertEqual(second["attendees"], 0)
        self.assertEqual(second["presence_sessions"], 0)
        self.assertEqual(self._runs(kept_key), kept_runs)
        self.assertEqual(self._runs(plain_key), 1)
        logged = "\n".join(first_logs.output + second_logs.output)
        self.assertNotIn("@", logged)
        self.assertNotIn("Ava", logged)
        self.assertIn("participation=", logged)


if __name__ == "__main__":
    unittest.main()

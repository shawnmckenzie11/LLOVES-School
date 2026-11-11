#!/usr/bin/env python3
"""MCK-471: deleting a Lesson Slides instance drops its attendance and participation."""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

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
                class_id, live_module, live_slot, is_test, run_key,
                dismissed_at, metrics_removed
            ) VALUES (?, 'M1', 'C1', 0, ?, ?, 1)
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

    def test_legacy_dismissal_keeps_attendance_and_participation(self) -> None:
        """A hide-and-keep dismissal does not delete marks or hide them."""
        _sid, run_key, tracker = self._end_marked("M2", "C1")
        self.school.conn.execute(
            """
            INSERT INTO lesson_slides_dismissed (
                class_id, live_module, live_slot, is_test, run_key,
                dismissed_at, metrics_removed
            ) VALUES (?, 'M2', 'C1', 0, ?, '2026-10-19T18:05:00', 0)
            """,
            (self.class_id, run_key),
        )
        self.school.conn.commit()
        self.school.purge_orphaned_attendance_participation()
        self.assertGreater(self._runs(run_key), 0)
        self.assertGreaterEqual(self._present(tracker), 1)
        slots = {
            str(row["slot"])
            for row in self.school._begin_module_meetings(self.class_id).get("M2", [])
        }
        self.assertIn("C1", slots)

    def test_mcr3u_module5_cleanup_is_once_and_narrow(self) -> None:
        """The one-time pass clears only dismissed MCR3U module 5 runs."""
        self.school.conn.execute(
            "DELETE FROM school_settings WHERE key = ?",
            ("mck471_mcr3u_m5_dismissed_metrics",),
        )
        now = "2026-10-19T18:05:00"
        target = f"m5-{uuid.uuid4().hex}"
        other_module = f"m1-{uuid.uuid4().hex}"
        kept_real = f"m5-live-{uuid.uuid4().hex}"
        self._plant_dismissed_answer(self.class_id, target, "M5", "C1", metrics_removed=0)
        self._plant_dismissed_answer(self.class_id, other_module, "M1", "C2", metrics_removed=0)
        self.school.conn.execute(
            """
            INSERT INTO participation_runs (
                class_id, run_key, is_real, auto_is_real, discarded, ended_at
            ) VALUES (?, ?, 1, 1, 0, ?)
            """,
            (self.class_id, kept_real, now),
        )
        self.school.conn.execute(
            """
            INSERT INTO participation_answers (
                class_id, run_key, live_module, question_key, student_id,
                source, answer_json, answered_at
            ) VALUES (?, ?, 'M5', 'item:kept', ?, 'live', '{}', ?)
            """,
            (self.class_id, kept_real, int(self.students[0]), now),
        )
        mcf_offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        other_class = self._class_with_code("MCF3M", int(mcf_offering["id"]), "Cara")
        section_class = self._class_with_code("MCR3U-2", int(mcf_offering["id"]), "Drew")
        numbered_class = self._class_with_code("MCR3U-12", int(mcf_offering["id"]), "Evan")
        mcr_offering_id = int(
            self.school.conn.execute(
                "SELECT offering_id FROM classes WHERE id = ?",
                (self.class_id,),
            ).fetchone()["offering_id"]
        )
        other_code_class = self._class_with_code("HIST", mcr_offering_id, "Faye")
        other_key = f"mcf-{uuid.uuid4().hex}"
        section_key = f"sec-{uuid.uuid4().hex}"
        numbered_key = f"num-{uuid.uuid4().hex}"
        other_code_key = f"off-{uuid.uuid4().hex}"
        self._plant_dismissed_answer(other_class, other_key, "M5", "C1", metrics_removed=0)
        self._plant_dismissed_answer(section_class, section_key, "M5", "C1", metrics_removed=0)
        self._plant_dismissed_answer(numbered_class, numbered_key, "M5", "C1", metrics_removed=0)
        self._plant_dismissed_answer(
            other_code_class, other_code_key, "M5", "C1", metrics_removed=0
        )
        self.school.conn.commit()
        codes = {
            int(row["id"]): str(row["course_code"])
            for row in self.school.conn.execute(
                "SELECT id, course_code FROM classes"
            )
        }
        self.assertEqual(codes[section_class], "MCR3U-2")
        self.assertEqual(codes[numbered_class], "MCR3U-12")
        self.assertEqual(codes[other_code_class], "HIST")
        offering_code = self.school.conn.execute(
            """
            SELECT o.ontario_code
            FROM classes c
            JOIN course_offerings o ON o.id = c.offering_id
            WHERE c.id = ?
            """,
            (other_code_class,),
        ).fetchone()
        self.assertEqual(str(offering_code["ontario_code"]), "MCR3U")
        section_offering = self.school.conn.execute(
            """
            SELECT o.ontario_code
            FROM classes c
            JOIN course_offerings o ON o.id = c.offering_id
            WHERE c.id = ?
            """,
            (section_class,),
        ).fetchone()
        self.assertEqual(str(section_offering["ontario_code"]), "MCF3M")
        logging.disable(logging.NOTSET)
        try:
            with self.assertLogs("school_db", level="INFO") as logs:
                self.school.purge_orphaned_attendance_participation()
        finally:
            logging.disable(logging.WARNING)
        self.assertEqual(self._answers(self.class_id, target), 0)
        self.assertEqual(self._answers(self.class_id, other_module), 1)
        self.assertEqual(self._answers(self.class_id, kept_real), 1)
        self.assertEqual(self._answers(other_class, other_key), 1)
        self.assertEqual(self._answers(section_class, section_key), 1)
        self.assertEqual(self._answers(numbered_class, numbered_key), 1)
        self.assertEqual(self._answers(other_code_class, other_code_key), 1)
        logged = "\n".join(logs.output)
        self.assertNotIn("@", logged)
        for name in ("Ava", "Cara", "Drew", "Evan", "Faye"):
            self.assertNotIn(name, logged)
        late = f"m5-late-{uuid.uuid4().hex}"
        self._plant_dismissed_answer(self.class_id, late, "M5", "C2", metrics_removed=0)
        self.school.conn.commit()
        self.school.purge_orphaned_attendance_participation()
        self.assertEqual(self._answers(self.class_id, late), 1)
        self.assertEqual(self._answers(other_class, other_key), 1)
        self.assertEqual(self._answers(section_class, section_key), 1)
        self.assertEqual(self._answers(numbered_class, numbered_key), 1)
        self.assertEqual(self._answers(other_code_class, other_code_key), 1)

    def _class_with_code(self, course_code: str, offering_id: int, codename: str) -> int:
        """Create one class stored under ``course_code``.

        Args:
            course_code: Exact ``classes.course_code``.
            offering_id: Offering to attach. Its Ontario code is not the match.
            codename: Roster label for the new class.

        Returns:
            ``classes.id``.
        """
        return int(
            self.school.game.create_class(
                year="2026/27",
                semester="Semester 1",
                course_code=course_code,
                days_preset="M/W/F",
                time_label="2:00pm",
                codenames=[codename],
                offering_id=int(offering_id),
                teacher_user_id=int(self.teacher["id"]),
            )["id"]
        )

    def _plant_dismissed_answer(
        self,
        class_id: int,
        run_key: str,
        module: str,
        slot: str,
        *,
        metrics_removed: int,
    ) -> None:
        """Insert one dismissed run and one participation answer.

        Args:
            class_id: Game-show ``classes.id``.
            run_key: Run key for the planted row.
            module: Module token such as ``M5``.
            slot: Slot token such as ``C1``.
            metrics_removed: 1 for a new-semantics delete, else 0.
        """
        now = "2026-10-19T18:05:00"
        self.school.conn.execute(
            """
            INSERT INTO lesson_slides_dismissed (
                class_id, live_module, live_slot, is_test, run_key,
                dismissed_at, metrics_removed
            ) VALUES (?, ?, ?, 0, ?, ?, ?)
            """,
            (int(class_id), module, slot, run_key, now, int(metrics_removed)),
        )
        self.school.conn.execute(
            """
            INSERT INTO participation_runs (
                class_id, run_key, is_real, auto_is_real, discarded, ended_at
            ) VALUES (?, ?, 1, 1, 0, ?)
            """,
            (int(class_id), run_key, now),
        )
        self.school.conn.execute(
            """
            INSERT INTO participation_answers (
                class_id, run_key, live_module, question_key, student_id,
                source, answer_json, answered_at
            ) VALUES (?, ?, ?, 'item:q', ?, 'live', '{}', ?)
            """,
            (int(class_id), run_key, module, int(self.students[0]), now),
        )

    def _answers(self, class_id: int, run_key: str) -> int:
        """Count participation answers for one class and run.

        Args:
            class_id: Game-show ``classes.id``.
            run_key: Participation run key.
        """
        return _count(
            self.school.conn,
            """
            SELECT COUNT(*) AS n FROM participation_answers
            WHERE class_id = ? AND run_key = ?
            """,
            (int(class_id), run_key),
        )

    def test_null_run_key_keeps_the_shared_column(self) -> None:
        """A sibling credit with a null run key keeps the tracker column."""
        _sid, run_key, tracker = self._end_marked("M1", "C3")
        offering = self.school.conn.execute(
            "SELECT offering_id FROM classes WHERE id = ?",
            (self.class_id,),
        ).fetchone()
        self.school.conn.execute(
            """
            INSERT INTO live_class_sessions (
                class_id, offering_id, session_code, status, started_at,
                run_key, mgs_session_id, is_test
            ) VALUES (?, ?, 'sib-null-key', 'ended', '2026-10-19T18:00:00', NULL, ?, 0)
            """,
            (self.class_id, int(offering["offering_id"]), tracker),
        )
        self.school.conn.commit()
        self.school.delete_lesson_slides_entry(self.class_id, "M1", "C3", False, run_key)
        left = _count(
            self.school.conn,
            "SELECT COUNT(*) AS n FROM sessions WHERE id = ?",
            (tracker,),
        )
        self.assertEqual(left, 1)

    def test_open_game_column_stays_out_of_the_gradebook(self) -> None:
        """An open game blocks the column delete, and the gradebook skips it."""
        _sid, run_key, tracker = self._end_marked("M1", "C4")
        self.school.conn.execute(
            """
            INSERT INTO games (class_id, session_id, status, created_at)
            VALUES (?, ?, 'live', '2026-10-19T18:05:00')
            """,
            (self.class_id, tracker),
        )
        self.school.conn.commit()
        self.school.delete_lesson_slides_entry(self.class_id, "M1", "C4", False, run_key)
        left = _count(
            self.school.conn,
            "SELECT COUNT(*) AS n FROM sessions WHERE id = ?",
            (tracker,),
        )
        self.assertEqual(left, 1)
        sessions, scores = self.school._class_tracker_rows(self.class_id)
        self.assertNotIn(tracker, {int(row["id"]) for row in sessions})
        self.assertFalse(any(int(row["session_id"]) == tracker for row in scores))

    def test_catchup_label_stays_while_the_link_stays(self) -> None:
        """Questions stay in catch-up when the catch-up link stays."""
        sid, run_key, _tracker = self._end_marked("M4", "C1")
        before = self.school.lesson_slides_questions_for_session(sid, self.class_id, run_key)
        self.assertTrue(any(q.get("in_catchup") for q in before))
        links = _count(
            self.school.conn,
            "SELECT COUNT(*) AS n FROM catchup_links WHERE class_id = ? AND run_key = ?",
            (self.class_id, run_key),
        )
        self.assertEqual(links, 1)
        self.school.delete_lesson_slides_entry(self.class_id, "M4", "C1", False, run_key)
        after = self.school.lesson_slides_questions_for_session(sid, self.class_id, run_key)
        self.assertTrue(any(q.get("in_catchup") for q in after))
        self.assertEqual(
            _count(
                self.school.conn,
                "SELECT COUNT(*) AS n FROM catchup_links WHERE class_id = ? AND run_key = ?",
                (self.class_id, run_key),
            ),
            1,
        )
        self.assertEqual(self._answers(self.class_id, run_key), 0)

    def test_presence_delete_names_only_that_session(self) -> None:
        """Deleting one run drops that presence session and no other."""
        sid, run_key, _tracker = self._end_marked("M6", "C1")

        class _Presence:
            """Records which session ids a delete asks to drop."""

            def __init__(self) -> None:
                self.dropped: list[int] = []

            def delete_session_tree(self, session_id: int) -> int:
                """Remember one session id."""
                self.dropped.append(int(session_id))
                return 1

            def delete_sessions_not_in(self, _living_ids: list[int]) -> int:
                """A broad sweep is a test failure."""
                raise AssertionError("broad presence sweep")

        presence = _Presence()
        self.school.presence = presence
        self.school.delete_lesson_slides_entry(self.class_id, "M6", "C1", False, run_key)
        self.assertEqual(presence.dropped, [sid])


class BootPresenceSweepTests(unittest.TestCase):
    """Boot must not delete presence that sqlite did not name."""

    def test_empty_list_deletes_no_presence_rows(self) -> None:
        """``delete_sessions_not_in([])`` does not issue a delete."""
        from live_presence import LivePresenceStore

        store = LivePresenceStore.__new__(LivePresenceStore)
        statements: list[str] = []

        class _Raw:
            """Connection stand-in that records SQL."""

            autocommit = True

            def execute(self, sql: str, _params: object = None) -> "_Raw":
                """Record one statement and return this stand-in."""
                statements.append(sql)
                return self

            def fetchone(self) -> dict[str, int]:
                """Pretend one presence session exists."""
                return {"n": 1}

            def commit(self) -> None:
                """Ignore commit."""

            def rollback(self) -> None:
                """Ignore rollback."""

        class _Conn:
            """Context manager around ``_Raw``."""

            def __init__(self, raw: _Raw) -> None:
                self.connection = raw

            def __enter__(self) -> "_Conn":
                return self

            def __exit__(self, *_exc: object) -> bool:
                return False

        store._conn = lambda: _Conn(_Raw())  # type: ignore[method-assign]
        self.assertEqual(store.delete_sessions_not_in([]), 0)
        self.assertEqual(statements, [])

    def test_boot_keeps_presence_that_sqlite_does_not_list(self) -> None:
        """An empty school boot leaves a Postgres presence session in place."""
        from school_db import SchoolDB

        class _Presence:
            """Presence stand-in with one session and one attendee."""

            def __init__(self) -> None:
                self.sessions = {9, 99}
                self.attendees = {9: 1, 99: 1}
                self.not_in_calls: list[list[int]] = []

            def delete_session_tree(self, session_id: int) -> int:
                """Drop one named session. Returns 1 when it was stored."""
                had = int(session_id) in self.sessions or int(session_id) in self.attendees
                self.sessions.discard(int(session_id))
                self.attendees.pop(int(session_id), None)
                return 1 if had else 0

            def delete_sessions_not_in(self, living_ids: list[int]) -> int:
                """Record a broad sweep. An empty list would wipe everything."""
                self.not_in_calls.append([int(sid) for sid in living_ids])
                if not list(living_ids):
                    self.sessions.clear()
                    self.attendees.clear()
                    return 1
                return 0

            def close(self) -> None:
                """Match the presence pool close the school calls on shutdown."""

        recording = _Presence()
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)

        def _attach(school: SchoolDB) -> None:
            """Point this boot at the recording instead of Postgres."""
            school.presence = recording

        with patch.object(SchoolDB, "_attach_live_presence", _attach):
            school = SchoolDB(Path(root.name) / "school.sqlite", Path(root.name), live_database_url="")
        self.addCleanup(school.close)
        self.assertEqual(recording.sessions, {9, 99})
        self.assertEqual(recording.attendees, {9: 1, 99: 1})
        self.assertEqual(recording.not_in_calls, [])


if __name__ == "__main__":
    unittest.main()

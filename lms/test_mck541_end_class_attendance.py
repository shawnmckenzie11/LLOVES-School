#!/usr/bin/env python3
"""MCK-541: End Class marks present only the ending session's attendees.

Seeded demo data only. Assertions do not print names, emails, codes, or ids.
"""

from __future__ import annotations

import uuid
import unittest

import test_live_shell as shell


class EndClassOwnSessionAttendanceTests(unittest.TestCase):
    """End Class must not union attendees from earlier or catch-up runs."""

    def setUp(self) -> None:
        """Reuse the live-shell app fixture without collecting its tests."""
        self._shell = shell.LiveShellTests("setUp")
        self._shell.setUp()
        self.school = self._shell.school
        self.class_id = self._shell.class_id
        self.teacher = self._shell.teacher
        self.client = self._shell.client

    def tearDown(self) -> None:
        self._shell.tearDown()

    def _aspen(self) -> int:
        row = self.school.game.find_student_by_codename(self.class_id, "Aspen")
        assert row is not None
        return int(row["id"])

    def _birch(self) -> int:
        row = self.school.game.find_student_by_codename(self.class_id, "Birch")
        assert row is not None
        return int(row["id"])

    def _present_ids(self, column_id: int) -> set[int]:
        with self.school.game._lock:
            rows = self.school.game.conn.execute(
                """
                SELECT student_id FROM session_scores
                WHERE session_id = ? AND present = 1
                """,
                (int(column_id),),
            ).fetchall()
        return {int(row["student_id"]) for row in rows}

    def _column_for_run(self, run_key: str) -> int:
        with self.school._lock:
            row = self.school.conn.execute(
                """
                SELECT mgs_session_id FROM live_class_sessions
                WHERE run_key = ?
                """,
                (str(run_key),),
            ).fetchone()
        assert row is not None and row["mgs_session_id"] not in (None, "")
        return int(row["mgs_session_id"])

    def _attendee_count(self, session_id: int) -> int:
        with self.school._lock:
            row = self.school.conn.execute(
                """
                SELECT COUNT(*) AS n FROM live_session_attendees
                WHERE live_session_id = ?
                """,
                (int(session_id),),
            ).fetchone()
        return int(row["n"] or 0)

    def _end(self, run_key: str) -> None:
        result = self.school.finish_live_class(
            self.class_id,
            celebrate=True,
            run_key=str(run_key),
            live_run=True,
        )
        self.assertTrue(result.get("ok"), result)

    def test_second_day_marks_only_its_own_students(self) -> None:
        """Two sessions on different days: day 2 present is only day 2's joiners."""
        first = self._aspen()
        second = self._birch()
        live1 = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        sid1 = int(live1["id"])
        run1 = str(live1["run_key"])
        self.school.join_live_class_session(sid1, first, codename="Aspen")
        self._end(run1)
        column1 = self._column_for_run(run1)
        first_present = self._present_ids(column1)
        self.assertEqual(first_present, {first})
        attendees1 = self._attendee_count(sid1)

        live2 = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        sid2 = int(live2["id"])
        run2 = str(live2["run_key"])
        self.assertNotEqual(run2, run1)
        self.school.join_live_class_session(sid2, second, codename="Birch")
        self.assertEqual(
            self.school.present_student_ids_for_live_class(
                self.class_id, run_key=run2
            ),
            [second],
        )
        self._end(run2)
        column2 = self._column_for_run(run2)
        self.assertNotEqual(column2, column1)
        self.assertEqual(self._present_ids(column1), first_present)
        self.assertEqual(self._present_ids(column2), {second})
        self.assertEqual(self._attendee_count(sid1), attendees1)
        self.assertEqual(self._attendee_count(sid2), 1)

    def test_catchup_run_is_excluded_from_live_attendance(self) -> None:
        """A ``~cu~`` attendee row is not counted when the live session ends."""
        first = self._aspen()
        second = self._birch()
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        sid = int(live["id"])
        run = str(live["run_key"])
        self.school.join_live_class_session(sid, first, codename="Aspen")
        cu_run = f"{run}~cu~q1"
        with self.school._lock:
            offering_id = int(live["offering_id"])
            cur = self.school.conn.execute(
                """
                INSERT INTO live_class_sessions (
                    class_id, offering_id, teacher_user_id, session_code,
                    status, started_at, ended_at, run_key
                ) VALUES (?, ?, ?, ?, 'ended', ?, ?, ?)
                """,
                (
                    int(self.class_id),
                    offering_id,
                    int(self.teacher["id"]),
                    "CUTEST01",
                    str(live["started_at"]),
                    str(live["started_at"]),
                    cu_run,
                ),
            )
            cu_sid = int(cur.lastrowid)
            self.school.conn.execute(
                """
                INSERT INTO live_session_attendees (
                    live_session_id, student_id, participant_uuid, visit_token,
                    codename, unmatched, joined_at, left_at, last_heartbeat_at
                ) VALUES (?, ?, ?, ?, '', 0, ?, ?, ?)
                """,
                (
                    cu_sid,
                    second,
                    str(uuid.uuid4()),
                    "cu-visit-token-mck541",
                    str(live["started_at"]),
                    str(live["started_at"]),
                    str(live["started_at"]),
                ),
            )
            self.school.conn.commit()
        cu_attendees = self._attendee_count(cu_sid)
        self.assertEqual(cu_attendees, 1)
        self.assertEqual(
            self.school.present_student_ids_for_live_class(
                self.class_id, run_key=cu_run
            ),
            [],
        )
        self.assertEqual(
            self.school.present_student_ids_for_live_class(
                self.class_id, run_key=run
            ),
            [first],
        )
        self._end(run)
        column = self._column_for_run(run)
        self.assertEqual(self._present_ids(column), {first})
        self.assertEqual(self._attendee_count(cu_sid), cu_attendees)
        self.assertEqual(self._attendee_count(sid), 1)

    def test_participation_credits_ignore_other_sessions(self) -> None:
        """Leftover answers from an earlier session are not credited on End."""
        first = self._aspen()
        second = self._birch()
        live1 = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        sid1 = int(live1["id"])
        run1 = str(live1["run_key"])
        self.school.join_live_class_session(sid1, first, codename="Aspen")
        self.school.ensure_waiting_room_minds_on(sid1)
        prompt1 = self.school.get_active_live_prompt(sid1)
        assert prompt1 is not None
        from minds_on import MINDS_ON_CHOICES

        self.school.submit_live_prompt_response(
            int(prompt1["id"]), first, {"choice": MINDS_ON_CHOICES[0]}
        )
        self.assertGreater(
            int(
                self.school.participation_question_credits_for_class(
                    self.class_id, run_key=run1
                ).get(first)
                or 0
            ),
            0,
        )
        # Leave session 1 ended with its responses still in place.
        with self.school._lock:
            self.school.conn.execute(
                """
                UPDATE live_class_sessions
                SET status = 'ended', ended_at = started_at
                WHERE id = ?
                """,
                (sid1,),
            )
            self.school.conn.commit()

        live2 = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        sid2 = int(live2["id"])
        run2 = str(live2["run_key"])
        self.school.join_live_class_session(sid2, second, codename="Birch")
        self.school.ensure_waiting_room_minds_on(sid2)
        prompt2 = self.school.get_active_live_prompt(sid2)
        assert prompt2 is not None
        self.school.submit_live_prompt_response(
            int(prompt2["id"]), second, {"choice": MINDS_ON_CHOICES[0]}
        )
        credits = self.school.participation_question_credits_for_class(
            self.class_id, run_key=run2
        )
        self.assertNotIn(first, credits)
        self.assertGreater(int(credits.get(second) or 0), 0)
        leftover = self.school.participation_question_credits_for_class(
            self.class_id, run_key=run1
        )
        self.assertGreater(int(leftover.get(first) or 0), 0)
        self.assertNotIn(second, leftover)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""MCK-311: Start, Quit, and list-delete never remove another ended session."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from test_participation_mck198 import Base  # noqa: E402


class EndedSessionRetentionTests(Base):
    """Ended live-class rows survive the next Start and a different Quit."""

    def _end(self, *, is_test: bool = False, module: str = "M1", slot: str = "C1") -> int:
        """Start one class, stamp its lesson, and End it.

        Args:
            is_test: True for a rehearsal.
            module: Teacher module, such as ``M1``.
            slot: Teacher slot, such as ``C1``.

        Returns:
            ``live_class_sessions.id`` of the ended row.
        """
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=is_test
        )
        sid = int(live["id"])
        self.school.set_live_session_teacher_state(
            sid, live_module=module, live_slot=slot
        )
        self.school.finish_live_class(self.class_id, celebrate=True, live_run=True)
        return sid

    def _runs(self, run_key: str) -> int:
        """Count participation runs stored for this class and run key.

        Args:
            run_key: ``live_class_sessions.run_key``.
        """
        row = self.school.conn.execute(
            """
            SELECT COUNT(*) AS n FROM participation_runs
            WHERE class_id = ? AND run_key = ?
            """,
            (self.class_id, run_key),
        ).fetchone()
        return int(row["n"] or 0)

    def _grade_rows(self, session_id: int) -> int:
        """Count gradebook score rows for this live session's tracker column.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        live = self.school.get_live_session(session_id)
        tracker = None if live is None else live.get("mgs_session_id")
        if tracker in (None, ""):
            return 0
        row = self.school.game.conn.execute(
            "SELECT COUNT(*) AS n FROM session_scores WHERE session_id = ?",
            (int(tracker),),
        ).fetchone()
        return int(row["n"] or 0)

    def test_start_keeps_earlier_ended_sessions(self) -> None:
        """The next Start closes celebration and leaves every ended row."""
        first = self._end(module="M1", slot="C1")
        first_key = str(self.school.get_live_session(first)["run_key"])
        self.assertGreater(self._runs(first_key), 0)
        grades = self._grade_rows(first)
        second = self._end(module="M1", slot="C2")
        second_key = str(self.school.get_live_session(second)["run_key"])
        third = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.assertNotEqual(int(third["id"]), first)
        self.assertNotEqual(int(third["id"]), second)
        for sid, key in ((first, first_key), (second, second_key)):
            row = self.school.get_live_session(sid)
            self.assertIsNotNone(row)
            self.assertEqual(row["status"], "ended")
            self.assertFalse(self.school.session_is_celebrating(sid))
            self.assertEqual(str(row["run_key"]), key)
            self.assertGreater(self._runs(key), 0)
        self.assertEqual(self._grade_rows(first), grades)

    def test_quit_deletes_only_the_active_session(self) -> None:
        """Quit removes the running session and no earlier ended session."""
        ended = self._end()
        ended_key = str(self.school.get_live_session(ended)["run_key"])
        active = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        active_id = int(active["id"])
        self.school.finish_live_class(
            self.class_id, celebrate=False, persist=False
        )
        self.assertIsNone(self.school.get_live_session(active_id))
        kept = self.school.get_live_session(ended)
        self.assertIsNotNone(kept)
        self.assertEqual(kept["status"], "ended")
        self.assertEqual(str(kept["run_key"]), ended_key)
        self.assertGreater(self._runs(ended_key), 0)

    def test_lesson_slides_delete_keeps_attendance_and_participation(self) -> None:
        """Removing a Lesson Slides entry does not remove the class record."""
        sid = self._end(module="M2", slot="C1")
        row = self.school.get_live_session(sid)
        run_key = str(row["run_key"])
        grades = self._grade_rows(sid)
        self.school.delete_lesson_slides_entry(
            self.class_id, "M2", "C1", False, run_key
        )
        kept = self.school.get_live_session(sid)
        self.assertIsNotNone(kept)
        self.assertEqual(kept["status"], "ended")
        self.assertGreater(self._runs(run_key), 0)
        self.assertEqual(self._grade_rows(sid), grades)
        latest = [
            item
            for item in self.school.list_lesson_slides_latest(self.class_id)
            if str(item.get("run_key") or "") == run_key
        ]
        self.assertEqual(latest, [])

    def test_delete_test_keeps_other_ended_sessions(self) -> None:
        """Deleting one test run leaves the real ended class in place."""
        real = self._end(is_test=False, module="M3", slot="C1")
        real_key = str(self.school.get_live_session(real)["run_key"])
        test = self._end(is_test=True, module="M3", slot="C2")
        self.school.delete_test_live_class(self.class_id, test)
        self.assertIsNone(self.school.get_live_session(test))
        kept = self.school.get_live_session(real)
        self.assertIsNotNone(kept)
        self.assertEqual(kept["status"], "ended")
        self.assertEqual(int(kept["is_test"] or 0), 0)
        self.assertGreater(self._runs(real_key), 0)


    def _count(self, sql: str, params: tuple) -> int:
        """Return one integer count. The SQL must select ``n``.

        Args:
            sql: SELECT that aliases the count as ``n``.
            params: Bound values for ``sql``.
        """
        row = self.school.conn.execute(sql, params).fetchone()
        return int(row["n"] or 0)

    def _plant_owned_rows(
        self, session_id: int, run_key: str, slot: str, *, links: bool
    ) -> None:
        """Insert the rows Quit must remove for one session.

        Catch-up and Lesson Slides are inserted only when ``links`` is
        set, on ``slot``, so an ended run's existing link stays put.

        Args:
            session_id: ``live_class_sessions.id``.
            run_key: That session's run key.
            slot: Lesson slot for a new catch-up and slides row.
            links: When True, also insert catch-up and Lesson Slides.
        """
        stamp = "2026-10-21T10:00:00"
        student_id = int(self.students[0])
        cur = self.school.conn.execute(
            """
            INSERT INTO live_session_items (
                live_session_id, placement_key, item_id, stage, kind,
                item_json, status, created_at, updated_at
            ) VALUES (?, 'p1', 'item-1', 'round', 'poll', '{}', 'closed', ?, ?)
            """,
            (int(session_id), stamp, stamp),
        )
        item_id = int(cur.lastrowid)
        self.school.conn.execute(
            """
            INSERT INTO live_group_members (
                live_item_id, team_id, student_id, joined_at
            ) VALUES (?, 1, ?, ?)
            """,
            (item_id, student_id, stamp),
        )
        self.school.conn.execute(
            """
            INSERT INTO live_group_votes (
                live_item_id, team_id, student_id, response_json,
                created_at, updated_at
            ) VALUES (?, 1, ?, '{}', ?, ?)
            """,
            (item_id, student_id, stamp, stamp),
        )
        self.school.conn.execute(
            """
            INSERT INTO live_group_responses (
                live_item_id, team_id, created_at, updated_at
            ) VALUES (?, 1, ?, ?)
            """,
            (item_id, stamp, stamp),
        )
        self.school.conn.execute(
            """
            INSERT INTO group_engagement (
                class_id, run_key, question_key, student_id,
                earned, possible, source, recorded_at
            ) VALUES (?, ?, 'item-1', ?, 1, 1, 'live', ?)
            """,
            (self.class_id, run_key, student_id, stamp),
        )
        self.school.conn.execute(
            """
            INSERT INTO group_engagement_items (
                class_id, run_key, question_key, live_module, live_slot,
                item_json, prompt_kind, offered_at
            ) VALUES (?, ?, 'item-1', 'M1', ?, '{}', 'poll', ?)
            """,
            (self.class_id, run_key, slot, stamp),
        )
        if links:
            self.school.conn.execute(
                """
                INSERT INTO catchup_links (
                    token, class_id, run_key, live_module, live_slot, is_open,
                    created_at, updated_at, is_test, live_session_id
                ) VALUES (?, ?, ?, 'M1', ?, 1, ?, ?, 0, ?)
                """,
                (
                    f"link-{int(session_id)}",
                    self.class_id,
                    run_key,
                    slot,
                    stamp,
                    stamp,
                    int(session_id),
                ),
            )
            self.school.conn.execute(
                """
                INSERT INTO lesson_slides_latest (
                    class_id, live_module, live_slot, is_test, live_session_id,
                    run_key, ended_at, questions_json, updated_at
                ) VALUES (?, 'M1', ?, 0, ?, ?, ?, '[]', ?)
                """,
                (self.class_id, slot, int(session_id), run_key, stamp, stamp),
            )
        self.school.conn.commit()


    def _plant_catchup(self, session_id: int, run_key: str, slot: str) -> None:
        """Insert one catch-up row for a session that End did not link.

        End writes a catch-up link only after a live answer. This plants
        the row Quit must leave alone when it belongs to an earlier run.

        Args:
            session_id: ``live_class_sessions.id``.
            run_key: That session's run key.
            slot: Lesson slot for the link.
        """
        stamp = "2026-10-21T10:00:00"
        self.school.conn.execute(
            """
            INSERT INTO catchup_links (
                token, class_id, run_key, live_module, live_slot, is_open,
                created_at, updated_at, is_test, live_session_id
            ) VALUES (?, ?, ?, 'M1', ?, 1, ?, ?, 0, ?)
            """,
            (
                f"link-{int(session_id)}",
                self.class_id,
                run_key,
                slot,
                stamp,
                stamp,
                int(session_id),
            ),
        )
        self.school.conn.commit()

    def _owned_counts(self, session_id: int, run_key: str) -> dict[str, int]:
        """Count Quit-owned rows for one session id and run key.

        Args:
            session_id: ``live_class_sessions.id``.
            run_key: That session's run key.
        """
        sid = int(session_id)
        return {
            "items": self._count(
                "SELECT COUNT(*) AS n FROM live_session_items WHERE live_session_id = ?",
                (sid,),
            ),
            "votes": self._count(
                """
                SELECT COUNT(*) AS n FROM live_group_votes
                WHERE live_item_id IN (
                    SELECT id FROM live_session_items WHERE live_session_id = ?
                )
                """,
                (sid,),
            ),
            "members": self._count(
                """
                SELECT COUNT(*) AS n FROM live_group_members
                WHERE live_item_id IN (
                    SELECT id FROM live_session_items WHERE live_session_id = ?
                )
                """,
                (sid,),
            ),
            "responses": self._count(
                """
                SELECT COUNT(*) AS n FROM live_group_responses
                WHERE live_item_id IN (
                    SELECT id FROM live_session_items WHERE live_session_id = ?
                )
                """,
                (sid,),
            ),
            "engagement": self._count(
                """
                SELECT COUNT(*) AS n FROM group_engagement
                WHERE class_id = ? AND run_key = ?
                """,
                (self.class_id, run_key),
            ),
            "engagement_items": self._count(
                """
                SELECT COUNT(*) AS n FROM group_engagement_items
                WHERE class_id = ? AND run_key = ?
                """,
                (self.class_id, run_key),
            ),
            "catchup": self._count(
                """
                SELECT COUNT(*) AS n FROM catchup_links
                WHERE class_id = ? AND (run_key = ? OR live_session_id = ?)
                """,
                (self.class_id, run_key, sid),
            ),
            "slides": self._count(
                """
                SELECT COUNT(*) AS n FROM lesson_slides_latest
                WHERE class_id = ? AND (run_key = ? OR live_session_id = ?)
                """,
                (self.class_id, run_key, sid),
            ),
        }

    def _empty_owned(self) -> dict[str, int]:
        """Zero counts for every Quit-owned table."""
        return {
            "items": 0,
            "votes": 0,
            "members": 0,
            "responses": 0,
            "engagement": 0,
            "engagement_items": 0,
            "catchup": 0,
            "slides": 0,
        }

    def test_quit_deletes_its_rows_and_keeps_the_ended_session(self) -> None:
        """Quit removes only the running session's saved rows."""
        ended = self._end(module="M1", slot="C1")
        ended_key = str(self.school.get_live_session(ended)["run_key"])
        self._plant_owned_rows(ended, ended_key, "C4", links=False)
        self._plant_catchup(ended, ended_key, "C4")
        ended_before = self._owned_counts(ended, ended_key)
        grades = self._grade_rows(ended)
        self.assertGreater(ended_before["items"], 0)
        self.assertGreater(ended_before["votes"], 0)
        self.assertGreater(ended_before["engagement"], 0)
        self.assertGreater(ended_before["catchup"], 0)
        self.assertGreater(ended_before["slides"], 0)
        active = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        active_id = int(active["id"])
        active_key = str(active["run_key"])
        self._plant_owned_rows(active_id, active_key, "C3", links=True)
        self.assertGreater(self._owned_counts(active_id, active_key)["slides"], 0)
        self.school.finish_live_class(
            self.class_id, celebrate=False, persist=False
        )
        self.assertIsNone(self.school.get_live_session(active_id))
        self.assertEqual(self._owned_counts(active_id, active_key), self._empty_owned())
        kept = self.school.get_live_session(ended)
        self.assertIsNotNone(kept)
        self.assertEqual(kept["status"], "ended")
        self.assertEqual(self._owned_counts(ended, ended_key), ended_before)
        self.assertGreater(self._runs(ended_key), 0)
        self.assertEqual(self._grade_rows(ended), grades)

    def test_quit_then_start_begins_empty(self) -> None:
        """The next Start does not inherit rows from the quit session."""
        ended = self._end(module="M1", slot="C2")
        ended_key = str(self.school.get_live_session(ended)["run_key"])
        self._plant_owned_rows(ended, ended_key, "C4", links=False)
        self._plant_catchup(ended, ended_key, "C4")
        ended_before = self._owned_counts(ended, ended_key)
        active = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        active_id = int(active["id"])
        active_key = str(active["run_key"])
        self._plant_owned_rows(active_id, active_key, "C3", links=True)
        self.school.finish_live_class(
            self.class_id, celebrate=False, persist=False
        )
        nxt = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        nxt_id = int(nxt["id"])
        nxt_key = str(nxt["run_key"])
        self.assertEqual(self._owned_counts(nxt_id, nxt_key)["items"], 0)
        self.assertEqual(self._owned_counts(nxt_id, nxt_key)["votes"], 0)
        self.assertEqual(self._owned_counts(nxt_id, nxt_key)["members"], 0)
        self.assertEqual(self._owned_counts(nxt_id, nxt_key)["responses"], 0)
        self.assertEqual(
            self._owned_counts(active_id, active_key), self._empty_owned()
        )
        self.assertNotEqual(nxt_key, active_key)
        self.assertEqual(self._owned_counts(ended, ended_key), ended_before)

    def _tracker_totals(self, tracker_id: int) -> tuple[int, float, int]:
        """Present sum, points sum, and score-row count for one column.

        Args:
            tracker_id: Gradebook ``sessions.id``.
        """
        row = self.school.game.conn.execute(
            """
            SELECT COALESCE(SUM(present), 0) AS present,
                   COALESCE(SUM(points), 0) AS points,
                   COUNT(*) AS n
            FROM session_scores
            WHERE session_id = ?
            """,
            (int(tracker_id),),
        ).fetchone()
        return int(row["present"] or 0), float(row["points"] or 0), int(row["n"] or 0)

    def _grade_columns(self) -> int:
        """Class-day columns, excluding the blank template row."""
        row = self.school.game.conn.execute(
            """
            SELECT COUNT(*) AS n FROM sessions
            WHERE class_id = ? AND status != 'template'
            """,
            (self.class_id,),
        ).fetchone()
        return int(row["n"] or 0)

    def test_end_twice_on_the_same_slot_is_not_double_counted(self) -> None:
        """A second End of the same slot does not add attendance or credit."""
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        sid = int(live["id"])
        run_key = str(live["run_key"])
        stamp = "2026-10-21T10:00:00"
        self.school.set_live_session_teacher_state(
            sid, live_module="M1", live_slot="C1"
        )
        self.school.join_live_class_session(sid, int(self.students[0]))
        prompt = self.school.conn.execute(
            """
            INSERT INTO live_session_prompts (
                live_session_id, slide_index, kind, payload, created_at, updated_at
            ) VALUES (?, 1, 'poll', '{}', ?, ?)
            """,
            (sid, stamp, stamp),
        )
        prompt_id = int(prompt.lastrowid)
        self.school.conn.execute(
            """
            INSERT INTO live_session_items (
                live_session_id, placement_key, item_id, stage, kind, item_json,
                prompt_id, status, publish_mode, response_mode, published_at,
                created_at, updated_at
            ) VALUES (?, 'q0', 'q0', 'round', 'poll', '{}', ?, 'closed',
                      'individual', 'individual', ?, ?, ?)
            """,
            (sid, prompt_id, stamp, stamp, stamp),
        )
        self.school.conn.execute(
            """
            INSERT INTO live_session_responses (
                prompt_id, student_id, response_json, created_at, updated_at
            ) VALUES (?, ?, '{}', ?, ?)
            """,
            (prompt_id, int(self.students[0]), stamp, stamp),
        )
        self.school.conn.commit()
        columns_before = self._grade_columns()
        first = self.school.finish_live_class(
            self.class_id, celebrate=True, run_key=run_key, live_run=True
        )
        self.assertFalse(first.get("already_ended"))
        live_row = self.school.get_live_session(sid)
        self.assertIsNotNone(live_row["mgs_session_id"])
        tracker_id = int(live_row["mgs_session_id"])
        present, points, score_rows = self._tracker_totals(tracker_id)
        runs = self._runs(run_key)
        items = self._count(
            """
            SELECT COUNT(*) AS n FROM participation_items
            WHERE class_id = ? AND run_key = ?
            """,
            (self.class_id, run_key),
        )
        columns = self._grade_columns()
        self.assertEqual(columns, columns_before + 1)
        self.assertGreater(present, 0)
        self.assertGreater(runs, 0)
        self.assertGreater(items, 0)
        self.assertGreater(score_rows, 0)
        second = self.school.finish_live_class(
            self.class_id, celebrate=True, run_key=run_key, live_run=True
        )
        self.assertTrue(second.get("already_ended"))
        present_after, points_after, score_rows_after = self._tracker_totals(tracker_id)
        self.assertEqual(self._grade_columns(), columns)
        self.assertEqual(present_after, present)
        self.assertEqual(points_after, points)
        self.assertEqual(score_rows_after, score_rows)
        self.assertEqual(self._runs(run_key), runs)
        self.assertEqual(
            self._count(
                """
                SELECT COUNT(*) AS n FROM participation_items
                WHERE class_id = ? AND run_key = ?
                """,
                (self.class_id, run_key),
            ),
            items,
        )
        self.assertEqual(self._grade_rows(sid), score_rows)

if __name__ == "__main__":
    unittest.main()

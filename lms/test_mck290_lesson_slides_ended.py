#!/usr/bin/env python3
"""MCK-290: an ended live class stays on Lesson Slides without a stored row."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from lesson_slides_table import build_lesson_slides_table  # noqa: E402
from test_mck233_test_run_checkbox import EndLiveToggleTests  # noqa: E402


class EndedLessonSlidesTests(EndLiveToggleTests):
    """Lesson Slides lists End and skips Quit, even when the stored row is gone."""

    def _end_slot_with_no_answers(self, module: str, slot: str, *, publish: bool = True) -> int:
        """End one live class that received no student answer.

        Args:
            module: Teacher module, such as ``M3``.
            slot: Teacher slot, such as ``C1``.
            publish: When true, publish one individual question so the
                participation copy survives after the session row is gone.

        Returns:
            ``live_class_sessions.id``.
        """
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        sid = int(live["id"])
        now = "2026-10-19T18:05:00"
        c = self.school.conn
        c.execute(
            "UPDATE live_class_sessions SET started_at = ?, teacher_state_json = ? WHERE id = ?",
            (now, json.dumps({"live_module": module, "live_slot": slot}), sid),
        )
        if publish:
            pid = c.execute(
                "INSERT INTO live_session_prompts (live_session_id, slide_index, kind, payload, "
                "created_at, updated_at) VALUES (?, 1, 'poll', '{}', ?, ?)",
                (sid, now, now),
            ).lastrowid
            c.execute(
                "INSERT INTO live_session_items (live_session_id, placement_key, item_id, stage, kind, "
                "item_json, prompt_id, status, publish_mode, response_mode, published_at, created_at, updated_at) "
                "VALUES (?, 'q0', 'q0', 'round', 'poll', ?, ?, 'closed', 'individual', 'individual', ?, ?, ?)",
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
                    pid,
                    now,
                    now,
                    now,
                ),
            )
        c.commit()
        self.school.finish_live_class(self.class_id, celebrate=True, live_run=True)
        return sid

    def _row(self, label: str) -> dict:
        """One Lesson Slides row by its class label.

        Args:
            label: Table label such as ``M3C1``.
        """
        payload = build_lesson_slides_table(self.school, self.class_id)
        return next(item for item in payload["rows"] if item["label"] == label)

    def _start_open_slot(self, module: str, slot: str) -> int:
        """Start a live class and leave it open. No End.

        Args:
            module: Teacher module, such as ``M5``.
            slot: Teacher slot, such as ``C1``.

        Returns:
            ``live_class_sessions.id``.
        """
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        sid = int(live["id"])
        self.school.conn.execute(
            "UPDATE live_class_sessions SET teacher_state_json = ? WHERE id = ?",
            (json.dumps({"live_module": module, "live_slot": slot}), sid),
        )
        self.school.conn.commit()
        return sid

    def _assert_not_run(self, label: str) -> None:
        """The slot is not Delivered and has no last-run time.

        Args:
            label: Table label such as ``M5C1``.
        """
        row = self._row(label)
        self.assertFalse(row["has_run"])
        self.assertNotEqual(row["status_label"], "Delivered")
        self.assertEqual(row["last_run_at"], "")
        self.assertEqual(row["catchup_access"], "none")

    def test_end_with_no_published_question_writes_the_row(self) -> None:
        """End writes Lesson Slides even when no individual question was published."""
        self._end_slot_with_no_answers("M2", "C4", publish=False)
        latest = self.school.list_lesson_slides_latest(self.class_id)
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["live_module"], "M2")
        self.assertEqual(latest[0]["live_slot"], "C4")
        self.assertFalse(self.school.list_catchup_links(self.class_id))
        row = self._row("M2C4")
        self.assertTrue(row["has_run"])
        self.assertEqual(row["status_label"], "Delivered")
        self.assertEqual(row["catchup_access"], "none")

    def test_live_with_zero_pushes_shows(self) -> None:
        """A Live End with no published question shows as Delivered."""
        self._end_slot_with_no_answers("M7", "C2", publish=False)
        row = self._row("M7C2")
        self.assertTrue(row["has_run"])
        self.assertEqual(row["status_label"], "Delivered")
        self.assertFalse(row["is_test"])
        self.assertTrue(row["can_delete"])
        self.assertTrue(row["run_key"])

    def test_open_office_with_zero_answers_shows(self) -> None:
        """An Open Office End with no answers writes the row and still lists it."""
        self._end_slot_with_no_answers("M7", "O1", publish=False)
        latest = self.school.list_lesson_slides_latest(self.class_id)
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["live_module"], "M7")
        self.assertEqual(latest[0]["live_slot"], "O1")
        self.assertEqual(int(latest[0].get("is_test") or 0), 0)
        row = self._row("M7O1")
        self.assertTrue(row["has_run"])
        self.assertIn("Open office", row["line_label"])
        self.assertTrue(row["can_delete"])
        self.assertTrue(row["run_key"])
        self.school.conn.execute(
            "DELETE FROM lesson_slides_latest WHERE class_id = ?",
            (self.class_id,),
        )
        self.school.conn.commit()
        again = self._row("M7O1")
        self.assertTrue(again["has_run"])

    def _end_test(self, module: str, slot: str) -> int:
        """End one test class. No questions are published.

        Args:
            module: Teacher module, such as ``M4``.
            slot: Teacher slot, such as ``C2``.

        Returns:
            ``live_class_sessions.id``.
        """
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=True
        )
        sid = int(live["id"])
        self.school.conn.execute(
            "UPDATE live_class_sessions SET teacher_state_json = ? WHERE id = ?",
            (json.dumps({"live_module": module, "live_slot": slot}), sid),
        )
        self.school.conn.commit()
        self.school.finish_live_class(self.class_id, celebrate=True, live_run=True)
        return sid

    def _kept_rows(self) -> tuple:
        """Counts that a Lesson Slides test delete must leave unchanged."""
        scores = self.school.game.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(present), 0), COALESCE(SUM(points), 0) "
            "FROM session_scores"
        ).fetchone()
        credits = self.school.game.conn.execute(
            "SELECT COUNT(*) FROM live_participation_credits"
        ).fetchone()
        live_slides = self.school.conn.execute(
            """
            SELECT live_module, live_slot, run_key
            FROM lesson_slides_latest
            WHERE class_id = ? AND COALESCE(is_test, 0) = 0
            ORDER BY live_module, live_slot, run_key
            """,
            (self.class_id,),
        ).fetchall()
        sessions = self.school.conn.execute(
            """
            SELECT id, COALESCE(is_test, 0), status
            FROM live_class_sessions
            WHERE class_id = ?
            ORDER BY id
            """,
            (self.class_id,),
        ).fetchall()
        links = self.school.conn.execute(
            "SELECT COUNT(*) FROM catchup_links WHERE class_id = ?",
            (self.class_id,),
        ).fetchone()
        return (
            tuple(scores),
            int(credits[0]),
            tuple(tuple(row) for row in live_slides),
            tuple(tuple(row) for row in sessions),
            int(links[0]),
        )

    def _staff_client(self, email: str):
        """Sign in one staff account. The verification code is not logged.

        Args:
            email: Staff address already registered.
        """
        client = self.app.test_client()
        client.get("/auth/google?portal=staff")
        client.get(f"/auth/google/callback?email={email}&name=T")
        code = self.school.get_user_by_email(email)["verification_code"]
        client.post("/verify-email", data={"code": code})
        return client

    def _test_rows(self) -> list[dict]:
        """Lesson Slides rows marked as a test entry."""
        payload = build_lesson_slides_table(self.school, self.class_id)
        return [item for item in payload["rows"] if item.get("is_test")]

    def _delete_body(self, row: dict) -> dict:
        """JSON body for deleting one Lesson Slides entry.

        Args:
            row: One table row.
        """
        return {
            "module": row["module"],
            "slot": row["slot"],
            "is_test": bool(row["is_test"]),
            "run_key": row["run_key"],
        }

    def _post_delete(self, row: dict, client=None):
        """POST the Lesson Slides delete for one row.

        Args:
            row: Table row to remove from the list.
            client: Optional signed-in client. Defaults to the owner.
        """
        http = client or self.client
        return http.post(
            f"/api/classes/{self.class_id}/lesson-slides/entries/delete",
            json=self._delete_body(row),
        )

    def _class_data_fingerprint(self) -> tuple:
        """Attendance, participation, sessions, and catch-up counts.

        Lesson Slides rows are not included. A list delete must leave
        this tuple unchanged.
        """
        scores = self.school.game.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(present), 0), COALESCE(SUM(points), 0) "
            "FROM session_scores"
        ).fetchone()
        credits = self.school.game.conn.execute(
            "SELECT COUNT(*) FROM live_participation_credits"
        ).fetchone()
        sessions = self.school.conn.execute(
            """
            SELECT id, COALESCE(is_test, 0), status
            FROM live_class_sessions
            WHERE class_id = ?
            ORDER BY id
            """,
            (self.class_id,),
        ).fetchall()
        links = self.school.conn.execute(
            "SELECT COUNT(*) FROM catchup_links WHERE class_id = ?",
            (self.class_id,),
        ).fetchone()
        runs = self.school.conn.execute(
            """
            SELECT run_key, COALESCE(is_real, 0), COALESCE(discarded, 0)
            FROM participation_runs
            WHERE class_id = ?
            ORDER BY run_key
            """,
            (self.class_id,),
        ).fetchall()
        return (
            tuple(scores),
            int(credits[0]),
            tuple(tuple(row) for row in sessions),
            int(links[0]),
            tuple(tuple(row) for row in runs),
        )

    def test_test_end_shows_with_delete(self) -> None:
        """A Test End is listed with a Test tag, and that row can be deleted."""
        sid = self._end_test("M4", "C2")
        latest = [
            row
            for row in self.school.list_lesson_slides_latest(self.class_id)
            if int(row.get("is_test") or 0)
        ]
        self.assertEqual(len(latest), 1)
        shown = self._test_rows()
        self.assertEqual(len(shown), 1)
        self.assertEqual(shown[0]["status_label"], "Test")
        self.assertTrue(shown[0]["can_delete"])
        self.assertEqual(int(shown[0]["live_session_id"]), sid)
        template = self._row("M4C2")
        self.assertFalse(template["has_run"])
        self.assertFalse(template["can_delete"])

    def test_delete_removes_only_the_test_entry(self) -> None:
        """Delete drops the test Lesson Slides row and leaves the rest."""
        self._end_slot_with_no_answers("M2", "C4", publish=False)
        sid = self._end_test("M4", "C2")
        before = self._kept_rows()
        shown = self._test_rows()[0]
        response = self._post_delete(shown)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._kept_rows(), before)
        self.assertEqual(self._test_rows(), [])
        stored = [
            row
            for row in self.school.list_lesson_slides_latest(self.class_id)
            if int(row.get("is_test") or 0)
        ]
        self.assertEqual(stored, [])
        live = self._row("M2C4")
        self.assertTrue(live["has_run"])
        self.assertEqual(live["status_label"], "Delivered")
        self.assertTrue(live["can_delete"])
        run = self.school.conn.execute(
            "SELECT run_key FROM live_class_sessions WHERE id = ?",
            (sid,),
        ).fetchone()
        self.school.conn.execute(
            """
            UPDATE participation_runs
            SET is_real = 1, discarded = 0
            WHERE class_id = ? AND run_key = ?
            """,
            (self.class_id, str(run["run_key"])),
        )
        self.school.conn.commit()
        self.assertEqual(self._test_rows(), [])
        self.assertFalse(self._row("M4C2")["has_run"])

    def test_delete_each_mode_removes_only_that_entry(self) -> None:
        """Live, Open Office, and Test deletes hide that list entry only."""
        live_id = self._end_slot_with_no_answers("M7", "C2", publish=False)
        live = self._row("M7C2")
        self.assertTrue(live["can_delete"])
        before_live = self._class_data_fingerprint()
        removed = self._post_delete(live)
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(self._class_data_fingerprint(), before_live)
        self.assertEqual(
            self.school.conn.execute(
                "SELECT status FROM live_class_sessions WHERE id = ?",
                (live_id,),
            ).fetchone()["status"],
            "ended",
        )
        self.assertFalse(self._row("M7C2")["has_run"])
        self.assertFalse(self._row("M7C2")["can_delete"])

        office_id = self._end_slot_with_no_answers("M7", "O1", publish=False)
        office = self._row("M7O1")
        self.assertTrue(office["can_delete"])
        before_office = self._class_data_fingerprint()
        self.school.delete_lesson_slides_entry(
            self.class_id,
            office["module"],
            office["slot"],
            office["is_test"],
            office["run_key"],
        )
        self.assertEqual(self._class_data_fingerprint(), before_office)
        labels = [
            item["label"]
            for item in build_lesson_slides_table(self.school, self.class_id)["rows"]
        ]
        self.assertNotIn("M7O1", labels)
        self.assertEqual(
            self.school.conn.execute(
                "SELECT status FROM live_class_sessions WHERE id = ?",
                (office_id,),
            ).fetchone()["status"],
            "ended",
        )

        sid = self._end_test("M4", "C2")
        before_test = self._kept_rows()
        session_delete = self.client.post(
            f"/api/classes/{self.class_id}/lesson-slides/test-entries/{sid}/delete",
            json={},
        )
        self.assertEqual(session_delete.status_code, 200)
        self.assertEqual(self._kept_rows(), before_test)
        self.assertEqual(self._test_rows(), [])
        self.assertFalse(self._row("M7C2")["has_run"])

    def test_later_end_after_delete_shows(self) -> None:
        """A new End of the same slot shows after the previous entry was deleted."""
        self._end_slot_with_no_answers("M6", "C1", publish=False)
        first = self._row("M6C1")
        self.assertEqual(self._post_delete(first).status_code, 200)
        self.assertFalse(self._row("M6C1")["has_run"])
        self._end_slot_with_no_answers("M6", "C1", publish=False)
        again = self._row("M6C1")
        self.assertTrue(again["has_run"])
        self.assertEqual(again["status_label"], "Delivered")
        self.assertNotEqual(again["run_key"], first["run_key"])

    def test_delete_refused_for_other_teachers(self) -> None:
        """Another teacher, and school IT, cannot delete this section's entry."""
        self._end_slot_with_no_answers("M2", "C4", publish=False)
        row = self._row("M2C4")
        body = self._delete_body(row)
        self.school.register_staff("other-teacher@gmail.com")
        other = self._staff_client("other-teacher@gmail.com")
        refused = other.post(
            f"/api/classes/{self.class_id}/lesson-slides/entries/delete",
            json=body,
        )
        self.assertEqual(refused.status_code, 403)
        it_user = self.school.register_staff("it-reviewer@gmail.com")
        self.school.conn.execute(
            "UPDATE users SET role = 'it' WHERE id = ?",
            (int(it_user["id"]),),
        )
        self.school.conn.commit()
        it_client = self._staff_client("it-reviewer@gmail.com")
        it_refused = it_client.post(
            f"/api/classes/{self.class_id}/lesson-slides/entries/delete",
            json=body,
        )
        self.assertEqual(it_refused.status_code, 403)
        self.assertTrue(self._row("M2C4")["has_run"])
        allowed = self._post_delete(row)
        self.assertEqual(allowed.status_code, 200)
        self.assertFalse(self._row("M2C4")["has_run"])

    def _end_marked(self, module: str, slot: str, *, is_test: bool) -> int:
        """End one class after one roster student joins and answers.

        Args:
            module: Teacher module, such as ``M5``.
            slot: Teacher slot, such as ``C3``.
            is_test: True for a test class.

        Returns:
            ``live_class_sessions.id``.
        """
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=is_test
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
        return sid

    def _tracker_id(self, session_id: int) -> int:
        """Gradebook column id for one live session.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        row = self.school.conn.execute(
            "SELECT mgs_session_id FROM live_class_sessions WHERE id = ?",
            (int(session_id),),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["mgs_session_id"])
        return int(row["mgs_session_id"])

    def test_live_delete_leaves_attendance_and_participation(self) -> None:
        """Deleting a Live entry leaves the gradebook marks in place."""
        sid = self._end_marked("M5", "C3", is_test=False)
        tracker = self._tracker_id(sid)
        before = self._class_data_fingerprint()
        row = self._row("M5C3")
        self.assertEqual(self._post_delete(row).status_code, 200)
        self.assertEqual(self._class_data_fingerprint(), before)
        self.assertFalse(self._row("M5C3")["has_run"])
        score = self.school.game.conn.execute(
            """
            SELECT COALESCE(SUM(present), 0), COALESCE(SUM(points), 0)
            FROM session_scores
            WHERE session_id = ?
            """,
            (tracker,),
        ).fetchone()
        self.assertGreaterEqual(int(score[0]), 1)
        self.assertGreater(float(score[1]), 0)
        part = self.school.participation_week_grid(self.class_id)
        self.assertGreater(
            sum(float(value or 0) for value in (part.get("totals") or {}).values()),
            0,
        )

    def test_test_end_is_excluded_from_attendance_and_participation(self) -> None:
        """A Test End writes marks that attendance and participation totals skip."""
        before_part = self.school.participation_week_grid(self.class_id)
        before_points = sum(
            float(value or 0) for value in (before_part.get("totals") or {}).values()
        )
        before_attendance = self.school.attendance_week_grid(self.class_id)
        before_days = sum(
            int(value or 0) for value in (before_attendance.get("day_totals") or {}).values()
        )
        student_id = int(self.students[0])
        before_answered = 0
        summary = self.school.class_participation(self.class_id)
        for student in summary["students"]:
            if int(student["student_id"]) != student_id:
                continue
            before_answered = sum(
                int((cell or {}).get("answered") or 0)
                for cell in (student.get("modules") or {}).values()
            )
        sid = self._end_marked("M8", "C4", is_test=True)
        tracker = self._tracker_id(sid)
        raw = self.school.game.attendance_score_rows(self.class_id)
        present = sum(
            1
            for row in raw.get("scores") or []
            if int(row.get("session_id") or 0) == tracker and int(row.get("present") or 0) == 1
        )
        self.assertGreaterEqual(present, 1)
        points = self.school.game.conn.execute(
            "SELECT COALESCE(SUM(points), 0) FROM session_scores WHERE session_id = ?",
            (tracker,),
        ).fetchone()
        self.assertGreater(float(points[0]), 0)
        grid = self.school.attendance_week_grid(self.class_id)
        self.assertNotIn(tracker, {int(item["id"]) for item in grid.get("sessions") or []})
        after_days = sum(int(value or 0) for value in (grid.get("day_totals") or {}).values())
        self.assertEqual(after_days, before_days)
        part = self.school.participation_week_grid(self.class_id)
        after_points = sum(float(value or 0) for value in (part.get("totals") or {}).values())
        self.assertEqual(after_points, before_points)
        meta = (part.get("session_meta") or {}).get(str(tracker)) or {}
        self.assertTrue(meta.get("is_test"))
        after = self.school.class_participation(self.class_id)
        answered = 0
        for student in after["students"]:
            if int(student["student_id"]) != student_id:
                continue
            answered = sum(
                int((cell or {}).get("answered") or 0)
                for cell in (student.get("modules") or {}).values()
            )
        self.assertEqual(answered, before_answered)
        run = self.school.conn.execute(
            "SELECT COALESCE(is_real, 0) FROM participation_runs WHERE class_id = ? AND run_key = (SELECT run_key FROM live_class_sessions WHERE id = ?)",
            (self.class_id, sid),
        ).fetchone()
        self.assertEqual(int(run[0]), 0)

    def test_real_end_without_answers_stays_delivered_after_the_row_is_gone(self) -> None:
        """A real End with no individual answers still lists from its participation run."""
        self._end_slot_with_no_answers("M2", "C3", publish=False)
        self.school.conn.execute(
            "DELETE FROM lesson_slides_latest WHERE class_id = ?",
            (self.class_id,),
        )
        self.school.conn.commit()
        row = self._row("M2C3")
        self.assertTrue(row["has_run"])
        self.assertEqual(row["status_label"], "Delivered")
        self.assertEqual(row["catchup_access"], "none")

    def test_second_close_keeps_a_real_end(self) -> None:
        """Force-end after End Class does not remove the Delivered row."""
        sid = self._end_slot_with_no_answers("M6", "C1", publish=False)
        again = self.school.end_live_class_session(sid)
        self.assertIsNotNone(again)
        self.assertEqual(str(again["status"]), "ended")
        row = self._row("M6C1")
        self.assertTrue(row["has_run"])
        self.assertEqual(row["status_label"], "Delivered")

    def test_it_force_end_stays_not_run(self) -> None:
        """IT force-end closes the session and does not mark the class Delivered.

        The IT route calls ``end_live_class_session``.
        """
        sid = self._start_open_slot("M5", "C1")
        ended = self.school.end_live_class_session(sid)
        self.assertIsNotNone(ended)
        self.assertEqual(str(ended["status"]), "ended")
        runs = self.school.conn.execute(
            """
            SELECT COUNT(*) FROM participation_runs
            WHERE class_id = ? AND COALESCE(is_real, 0) = 1
              AND COALESCE(discarded, 0) = 0
            """,
            (self.class_id,),
        ).fetchone()[0]
        self.assertEqual(int(runs), 0)
        self._assert_not_run("M5C1")

    def _cancel_open_game(self, sid: int) -> None:
        """Open a game, then post the cancel route that ends the live session.

        Args:
            sid: ``live_class_sessions.id`` that must end without a run.
        """
        self.school.game.begin_game(self.class_id)
        resp = self.client.post(
            f"/api/classes/{self.class_id}/game/cancel",
            json={},
        )
        self.assertEqual(resp.status_code, 200)
        status = self.school.conn.execute(
            "SELECT status FROM live_class_sessions WHERE id = ?",
            (sid,),
        ).fetchone()
        self.assertIsNotNone(status)
        self.assertEqual(str(status["status"]), "ended")

    def test_game_cancel_stays_not_run(self) -> None:
        """The live-class cancel button posts ``/game/cancel`` and stays not run."""
        sid = self._start_open_slot("M5", "C2")
        self._cancel_open_game(sid)
        self._assert_not_run("M5C2")

    def test_close_card_without_saving_stays_not_run(self) -> None:
        """Take Attendance 'Close the card without saving' posts ``/game/cancel``."""
        sid = self._start_open_slot("M5", "C3")
        self._cancel_open_game(sid)
        self._assert_not_run("M5C3")

    def test_deleted_latest_row_still_lists_the_ended_session(self) -> None:
        """The table reads the ended session when ``lesson_slides_latest`` is stale."""
        self._end_slot_with_no_answers("M3", "C1")
        stored = self.school.conn.execute(
            """
            SELECT COUNT(*) FROM lesson_slides_latest
            WHERE class_id = ? AND live_module = 'M3' AND live_slot = 'C1'
              AND COALESCE(is_test, 0) = 0
            """,
            (self.class_id,),
        ).fetchone()[0]
        self.assertEqual(int(stored), 1)
        ended = self.school.conn.execute(
            """
            SELECT ended_at FROM live_class_sessions
            WHERE class_id = ? AND status = 'ended'
            ORDER BY id DESC LIMIT 1
            """,
            (self.class_id,),
        ).fetchone()
        self.school.conn.execute(
            """
            UPDATE lesson_slides_latest
            SET ended_at = ?, run_key = ?
            WHERE class_id = ? AND live_module = 'M3' AND live_slot = 'C1'
              AND COALESCE(is_test, 0) = 0
            """,
            ("2026-01-01T00:00:00", "older-run", self.class_id),
        )
        self.school.conn.commit()
        row = self._row("M3C1")
        self.assertTrue(row["has_run"])
        self.assertEqual(row["last_run_at"], str(ended["ended_at"]))
        self.assertNotIn("2026-01-01", row["last_run_at"])
        self.assertEqual(row["catchup_access"], "none")

    def test_items_remain_after_the_session_row_is_gone(self) -> None:
        """A later Start can remove the session. The participation run still lists."""
        self._end_slot_with_no_answers("M3", "C2")
        self.school.conn.execute(
            "DELETE FROM lesson_slides_latest WHERE class_id = ?",
            (self.class_id,),
        )
        self.school.conn.execute(
            "DELETE FROM live_class_sessions WHERE class_id = ?",
            (self.class_id,),
        )
        self.school.conn.commit()
        row = self._row("M3C2")
        self.assertTrue(row["has_run"])
        self.assertEqual(row["module"], "M3")
        self.assertEqual(row["slot"], "C2")

    def test_quit_does_not_list_a_run(self) -> None:
        """Quit writes no Lesson Slides run."""
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        sid = int(live["id"])
        self.school.conn.execute(
            "UPDATE live_class_sessions SET teacher_state_json = ? WHERE id = ?",
            (json.dumps({"live_module": "M4", "live_slot": "C1"}), sid),
        )
        self.school.conn.commit()
        self.school.finish_live_class(self.class_id, persist=False, celebrate=False)
        row = self._row("M4C1")
        self.assertFalse(row["has_run"])
        self.assertEqual(row["catchup_access"], "none")
        self.assertEqual(row["last_run_at"], "")

    def test_slot_outside_the_template_still_lists(self) -> None:
        """A real end whose module is not in the course files still gets a row.

        End keeps modules such as M9. A slot outside C1–C4 is stored as C1,
        so this uses a real slot on a module the course files do not have.
        """
        self._end_slot_with_no_answers("M9", "C1")
        self.school.conn.execute(
            "DELETE FROM lesson_slides_latest WHERE class_id = ?",
            (self.class_id,),
        )
        self.school.conn.commit()
        row = self._row("M9C1")
        self.assertTrue(row["has_run"])
        self.assertTrue(row["not_in_template"])
        self.assertEqual(row["status_label"], "Not in template")


    def _leave_one_answer(self, module: str, slot: str) -> int:
        """Start a class, publish one question, and store one roster answer.

        The session stays active so a route can end it.

        Args:
            module: Teacher module, such as ``M4``.
            slot: Teacher slot, such as ``C1``.

        Returns:
            ``live_class_sessions.id``.
        """
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
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
        return sid

    def _open_scoring_game(self) -> None:
        """Open the scoring game and mark it live so ``/game/end`` accepts it."""
        self.school.game.begin_game(self.class_id)
        self.school.game.conn.execute(
            """
            UPDATE games
            SET status = 'live'
            WHERE class_id = ? AND status != 'ended'
            """,
            (self.class_id,),
        )
        self.school.game.conn.commit()

    def _saved_answer_count(self) -> int:
        """How many participation answers this class kept for the roster student."""
        row = self.school.conn.execute(
            """
            SELECT COUNT(*) FROM participation_answers
            WHERE class_id = ? AND student_id = ?
            """,
            (self.class_id, int(self.students[0])),
        ).fetchone()
        return int(row[0])

    def _live_answer_count(self, session_id: int) -> int:
        """How many live response rows this session still holds.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        row = self.school.conn.execute(
            """
            SELECT COUNT(*)
            FROM live_session_responses r
            INNER JOIN live_session_prompts p ON p.id = r.prompt_id
            WHERE p.live_session_id = ?
            """,
            (int(session_id),),
        ).fetchone()
        return int(row[0])

    def test_game_end_saves_participation_catchup_and_slides(self) -> None:
        """POST ``/game/end`` saves participation, catch-up, and Lesson Slides."""
        sid = self._leave_one_answer("M4", "C3")
        self._open_scoring_game()
        resp = self.client.post(
            f"/api/classes/{self.class_id}/game/end",
            json={},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body.get("live_sessions_ended"), [sid])
        run = self.school.conn.execute(
            """
            SELECT COALESCE(is_real, 0), COALESCE(discarded, 0)
            FROM participation_runs
            WHERE class_id = ?
              AND run_key = (
                  SELECT run_key FROM live_class_sessions WHERE id = ?
              )
            """,
            (self.class_id, sid),
        ).fetchone()
        self.assertIsNotNone(run)
        self.assertEqual((int(run[0]), int(run[1])), (1, 0))
        self.assertTrue(self.school.list_catchup_links(self.class_id))
        latest = self.school.list_lesson_slides_latest(self.class_id)
        self.assertEqual(
            [(row["live_module"], row["live_slot"]) for row in latest],
            [("M4", "C3")],
        )
        shown = self._row("M4C3")
        self.assertTrue(shown["has_run"])
        self.assertEqual(shown["status_label"], "Delivered")
        self.assertNotEqual(shown["catchup_access"], "none")
        self.assertGreaterEqual(self._saved_answer_count(), 1)
        self.assertEqual(self._live_answer_count(sid), 0)

    def test_failed_end_save_is_retried_by_a_later_force_end(self) -> None:
        """A failed End keeps answers, and a later force-end saves them."""
        sid = self._leave_one_answer("M6", "C2")

        def fail_capture(*_args, **_kwargs) -> None:
            """Simulate the participation save failing after End starts."""
            raise RuntimeError("participation capture failed")

        original = self.school._capture_participation_unlocked
        self.school._capture_participation_unlocked = fail_capture
        try:
            self.school.finish_live_class(
                self.class_id, celebrate=True, live_run=True
            )
        finally:
            self.school._capture_participation_unlocked = original
        status = self.school.conn.execute(
            "SELECT status FROM live_class_sessions WHERE id = ?",
            (sid,),
        ).fetchone()
        self.assertEqual(str(status["status"]), "ended")
        self.assertGreaterEqual(self._live_answer_count(sid), 1)
        self.assertEqual(self._saved_answer_count(), 0)
        self.assertFalse(self.school.list_catchup_links(self.class_id))
        self._assert_not_run("M6C2")
        self.school.end_live_class_session(sid)
        run = self.school.conn.execute(
            """
            SELECT COALESCE(is_real, 0)
            FROM participation_runs
            WHERE class_id = ?
              AND run_key = (
                  SELECT run_key FROM live_class_sessions WHERE id = ?
              )
            """,
            (self.class_id, sid),
        ).fetchone()
        self.assertIsNotNone(run)
        self.assertEqual(int(run[0]), 1)
        self.assertTrue(self.school.list_catchup_links(self.class_id))
        shown = self._row("M6C2")
        self.assertTrue(shown["has_run"])
        self.assertEqual(shown["status_label"], "Delivered")
        self.assertGreaterEqual(self._saved_answer_count(), 1)
        self.assertEqual(self._live_answer_count(sid), 0)

    def test_force_end_with_an_answer_still_skips_the_save(self) -> None:
        """IT force-end of an open class does not turn unsaved answers into a run."""
        sid = self._leave_one_answer("M5", "C4")
        self.school.end_live_class_session(sid)
        self._assert_not_run("M5C4")
        self.assertEqual(self._saved_answer_count(), 0)
        self.assertEqual(self._live_answer_count(sid), 0)
        self.assertFalse(self.school.list_catchup_links(self.class_id))



if __name__ == "__main__":
    unittest.main()

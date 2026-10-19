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


if __name__ == "__main__":
    unittest.main()

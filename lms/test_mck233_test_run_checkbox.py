#!/usr/bin/env python3
"""MCK-233: End Live Test/Live toggle, schedule default, catch-up subset."""

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from catchup_backfill import repair_catchup  # noqa: E402
from end_live_run_mode import LiveClassStartSchedule, default_end_live_run_mode  # noqa: E402
from test_participation_mck198 import Base  # noqa: E402

TORONTO = ZoneInfo("America/Toronto")


class EndLiveToggleTests(Base):
    """Teacher Test/Live toggle at End Live Class."""

    def setUp(self) -> None:
        super().setUp()
        self.client = self.app.test_client()
        offering_id = int(
            self.school.game.conn.execute(
                "SELECT offering_id FROM classes WHERE id = ?", (self.class_id,)
            ).fetchone()[0]
        )
        self.school.set_offering_schedule(
            offering_id,
            live_days="M/W/F",
            live_time="9:15am",
            live_end_time="10:30am",
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        code = self.school.get_user_by_email("teacher@gmail.com")["verification_code"]
        self.client.post("/verify-email", data={"code": code})

    def _start_live_with_two_questions(self, *, started_at: str) -> tuple[int, str]:
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        sid = int(live["id"])
        run_key = str(live.get("run_key") or "")
        c = self.school.conn
        c.execute(
            "UPDATE live_class_sessions SET started_at = ?, teacher_state_json = ? WHERE id = ?",
            (started_at, json.dumps({"live_module": "M3", "live_slot": "C1"}), sid),
        )
        now = started_at
        for idx, key in enumerate(("q0", "q1"), start=1):
            pid = c.execute(
                "INSERT INTO live_session_prompts (live_session_id, slide_index, kind, payload, "
                "created_at, updated_at) VALUES (?, ?, 'poll', '{}', ?, ?)",
                (sid, idx, now, now),
            ).lastrowid
            c.execute(
                "INSERT INTO live_session_items (live_session_id, placement_key, item_id, stage, kind, "
                "item_json, prompt_id, status, publish_mode, response_mode, published_at, created_at, updated_at) "
                "VALUES (?, ?, ?, 'round', 'poll', ?, ?, 'closed', 'individual', 'individual', ?, ?, ?)",
                (
                    sid,
                    key,
                    key,
                    json.dumps(
                        {
                            "text": key,
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
        # Only q0 gets a student answer (q1 published but no data).
        q0_prompt = c.execute(
            "SELECT prompt_id FROM live_session_items WHERE live_session_id = ? AND placement_key = 'q0'",
            (sid,),
        ).fetchone()[0]
        c.execute(
            """
            INSERT INTO live_session_responses (prompt_id, student_id, response_json, created_at, updated_at)
            VALUES (?, ?, '{}', ?, ?)
            """,
            (int(q0_prompt), int(self.students[0]), now, now),
        )
        c.commit()
        return sid, run_key

    def test_live_run_records_catchup_and_lesson_slides(self) -> None:
        started = datetime(2026, 10, 14, 9, 20, tzinfo=TORONTO).replace(microsecond=0).isoformat()
        self._start_live_with_two_questions(started_at=started)
        resp = self.client.post(
            f"/staff/class/{self.class_id}/end-live",
            data={
                "end_options": "1",
                "save_attendance": "1",
                "save_participation": "1",
                "run_mode": "live",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        links = self.school.list_catchup_links(self.class_id)
        self.assertEqual(len(links), 1)
        run_key = links[0]["run_key"]
        item_count = self.school.conn.execute(
            "SELECT COUNT(*) FROM participation_items WHERE class_id = ? AND run_key = ?",
            (self.class_id, run_key),
        ).fetchone()[0]
        self.assertEqual(int(item_count), 2)
        latest = self.school.list_lesson_slides_latest(self.class_id)
        self.assertEqual(len(latest), 1)
        questions = latest[0]["questions"]
        self.assertEqual(len(questions), 2)
        in_catchup = [q for q in questions if q.get("in_catchup")]
        self.assertEqual(len(in_catchup), 2)
        self.assertEqual(
            {q["question_key"] for q in in_catchup},
            {"item:q0", "item:q1"},
        )
        unanswered_student = self.students[1] if len(self.students) > 1 else self.students[0]
        qs = self.school.catchup_questions(links[0]["token"], unanswered_student)
        self.assertEqual(len(qs), 2)
        self.assertEqual(
            {q["question_key"] for q in qs},
            {"item:q0", "item:q1"},
        )
        qs_answered_live = self.school.catchup_questions(
            links[0]["token"], self.students[0]
        )
        self.assertEqual(len(qs_answered_live), 1)
        self.assertEqual(qs_answered_live[0]["question_key"], "item:q1")

    def test_posted_run_mode_does_not_skip_a_live_session(self) -> None:
        """End no longer has a Test choice. A live session still saves."""
        started = datetime(2026, 10, 14, 9, 20, tzinfo=TORONTO).replace(microsecond=0).isoformat()
        _, run_key = self._start_live_with_two_questions(started_at=started)
        resp = self.client.post(
            f"/staff/class/{self.class_id}/end-live",
            data={
                "end_options": "1",
                "save_attendance": "1",
                "save_participation": "1",
                "run_mode": "test",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len(self.school.list_catchup_links(self.class_id)), 1)
        kind = self.school.participation_run_kind(self.class_id, run_key)
        assert kind is not None
        self.assertTrue(kind["is_real"])

    def test_override_live_default_to_test(self) -> None:
        """Schedule helper still knows the window. End ignores a posted Test choice."""
        started = datetime(2026, 10, 14, 9, 20, tzinfo=TORONTO)
        sched = LiveClassStartSchedule(days_key="M/W/F", start_time_label="9:15am")
        self.assertEqual(default_end_live_run_mode(started, sched), "live")
        self.assertEqual(
            self.school.default_end_live_run_mode_for_class(
                self.class_id, started.isoformat()
            ),
            "live",
        )
        self._start_live_with_two_questions(started_at=started.isoformat())
        self.client.post(
            f"/staff/class/{self.class_id}/end-live",
            data={"end_options": "1", "run_mode": "test"},
            follow_redirects=False,
        )
        self.assertEqual(len(self.school.list_catchup_links(self.class_id)), 1)

    def test_outside_window_live_choice_still_kept_after_backfill(self) -> None:
        """Regression: afternoon start does not auto-flip to test on repair."""
        afternoon = datetime(2026, 10, 14, 15, 0, tzinfo=TORONTO).replace(microsecond=0).isoformat()
        self._start_live_with_two_questions(started_at=afternoon)
        self.client.post(
            f"/staff/class/{self.class_id}/end-live",
            data={"end_options": "1", "run_mode": "live"},
            follow_redirects=False,
        )
        links = self.school.list_catchup_links(self.class_id)
        self.assertEqual(len(links), 1)
        run_key = links[0]["run_key"]
        self.school.conn.execute("DELETE FROM live_class_sessions")
        self.school.conn.commit()
        report = repair_catchup(
            self.school, run_targets=[(self.class_id, run_key[:8])], apply=True
        )
        actions = "\n".join(r.action for r in report.runs).lower()
        self.assertNotIn("classify test", actions)
        kind = self.school.participation_run_kind(self.class_id, run_key)
        assert kind is not None
        self.assertTrue(kind["is_real"])


    def test_live_run_zero_live_answers_no_catchup_link(self) -> None:
        """Live End with no student responses still lists Lesson Slides.

        Catch-up stays absent until an individual answer exists.
        """
        started = datetime(2026, 10, 14, 9, 20, tzinfo=TORONTO).replace(microsecond=0).isoformat()
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        sid = int(live["id"])
        c = self.school.conn
        c.execute(
            "UPDATE live_class_sessions SET started_at = ?, teacher_state_json = ? WHERE id = ?",
            (started, json.dumps({"live_module": "M3", "live_slot": "C1"}), sid),
        )
        now = started
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
        resp = self.client.post(
            f"/staff/class/{self.class_id}/end-live",
            data={
                "end_options": "1",
                "save_attendance": "1",
                "save_participation": "1",
                "run_mode": "live",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(self.school.list_catchup_links(self.class_id))
        latest = self.school.list_lesson_slides_latest(self.class_id)
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["live_module"], "M3")
        self.assertEqual(latest[0]["live_slot"], "C1")
        from lesson_slides_table import build_lesson_slides_table

        row = next(
            item
            for item in build_lesson_slides_table(self.school, self.class_id)["rows"]
            if item["label"] == "M3C1"
        )
        self.assertTrue(row["has_run"])
        self.assertEqual(row["catchup_access"], "none")

    def test_omitted_run_mode_saves_a_live_session(self) -> None:
        """A non-test session saves even outside the old schedule window."""
        afternoon = datetime(2026, 10, 14, 18, 54, tzinfo=TORONTO).replace(microsecond=0).isoformat()
        self.school.set_offering_schedule(
            int(
                self.school.game.conn.execute(
                    "SELECT offering_id FROM classes WHERE id = ?", (self.class_id,)
                ).fetchone()[0]
            ),
            live_days="M/W/F",
            live_time="2:00pm",
            live_end_time="3:30pm",
        )
        self._start_live_with_two_questions(started_at=afternoon)
        self.client.post(
            f"/staff/class/{self.class_id}/end-live",
            data={"end_options": "1", "save_attendance": "1", "save_participation": "1"},
            follow_redirects=False,
        )
        self.assertEqual(len(self.school.list_catchup_links(self.class_id)), 1)


    def test_ensure_participation_run_kind_idempotent(self) -> None:
        """Second write returns stored kind without overwriting."""
        run_key = "mck233-idempotent-run"
        first = self.school.ensure_participation_run_kind(
            self.class_id, run_key, is_real=True
        )
        self.assertTrue(first)
        second = self.school.ensure_participation_run_kind(
            self.class_id, run_key, is_real=False
        )
        self.assertTrue(second)
        kind = self.school.participation_run_kind(self.class_id, run_key)
        assert kind is not None
        self.assertTrue(kind["is_real"])


if __name__ == "__main__":
    unittest.main()

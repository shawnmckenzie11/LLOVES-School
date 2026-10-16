#!/usr/bin/env python3
"""MCK-269: a test live class saves, stays out of real counts, and can be deleted."""

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

from test_participation_mck198 import Base  # noqa: E402


class TestLiveClassTests(Base):
    """Run a real class, then a test class, then delete the test class."""

    def setUp(self) -> None:
        super().setUp()
        self.client = self.app.test_client()
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        code = self.school.get_user_by_email("teacher@gmail.com")["verification_code"]
        self.client.post("/verify-email", data={"code": code})

    def _publish_one_answer(self, session_id: int, module: str, slot: str) -> None:
        """Publish one individual item and one roster answer on a live session."""
        now = "2026-10-14T13:20:00"
        c = self.school.conn
        c.execute(
            """
            UPDATE live_class_sessions
            SET teacher_state_json = ?, started_at = ?
            WHERE id = ?
            """,
            (json.dumps({"live_module": module, "live_slot": slot}), now, int(session_id)),
        )
        prompt_id = c.execute(
            """
            INSERT INTO live_session_prompts (
                live_session_id, slide_index, kind, payload, created_at, updated_at
            ) VALUES (?, 1, 'poll', '{}', ?, ?)
            """,
            (int(session_id), now, now),
        ).lastrowid
        c.execute(
            """
            INSERT INTO live_session_items (
                live_session_id, placement_key, item_id, stage, kind, item_json,
                prompt_id, status, publish_mode, response_mode, published_at,
                created_at, updated_at
            ) VALUES (?, 'q0', 'q0', 'round', 'poll', ?, ?, 'closed',
                      'individual', 'individual', ?, ?, ?)
            """,
            (
                int(session_id),
                json.dumps({"text": "q0", "type": "mc", "options": [{"text": "A"}]}),
                prompt_id,
                now,
                now,
                now,
            ),
        )
        c.execute(
            """
            INSERT INTO live_session_responses (
                prompt_id, student_id, response_json, created_at, updated_at
            ) VALUES (?, ?, '{}', ?, ?)
            """,
            (int(prompt_id), int(self.students[0]), now, now),
        )
        c.commit()

    def _snapshot(self) -> dict:
        """Real counts a test class must not move."""
        totals = self.school.official_gradebook_totals(self.class_id)["totals"]
        picker = self.school.set_class_picker(self.class_id)
        attendance = self.school.attendance_week_grid(self.class_id)
        participation = self.school.participation_week_grid(self.class_id)
        slides = [
            (str(row["live_module"]), str(row["live_slot"]))
            for row in self.school.list_lesson_slides_latest(self.class_id)
            if not int(row.get("is_test") or 0)
        ]
        test_ids = self.school.test_mgs_session_ids(self.class_id)
        present = 0
        marks = self.school.game.attendance_score_rows(self.class_id)
        for row in marks.get("scores") or []:
            if int(row.get("session_id") or 0) in test_ids:
                continue
            if int(row.get("present") or 0):
                present += 1
        real_sessions = [
            row
            for row in marks.get("sessions") or []
            if str(row.get("status") or "") != "template"
            and int(row.get("id") or 0) not in test_ids
        ]
        return {
            "totals": dict(totals),
            "label": picker["label"],
            "module": picker["module"],
            "slot": picker["slot"],
            "part_totals": dict(participation.get("totals") or {}),
            "slides": slides,
            "attendance_columns": len(real_sessions),
            "grid_days": len(attendance.get("date_labels") or []),
            "present": present,
        }

    def test_is_test_column_migrates(self) -> None:
        cols = {
            row["name"]
            for row in self.school.conn.execute("PRAGMA table_info(live_class_sessions)")
        }
        self.assertIn("is_test", cols)
        indexed = {
            str(row["name"])
            for row in self.school.conn.execute(
                "PRAGMA index_info(idx_catchup_links_class_lesson)"
            )
        }
        self.assertIn("is_test", indexed)
        slide_cols = {
            row["name"]
            for row in self.school.conn.execute("PRAGMA table_info(lesson_slides_latest)")
        }
        self.assertIn("is_test", slide_cols)
        self.assertIn("live_session_id", slide_cols)

    def test_run_test_then_delete_leaves_real_counts(self) -> None:
        real = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=False
        )
        self._publish_one_answer(int(real["id"]), "M1", "C1")
        self.school.finish_live_class(
            self.class_id,
            save_attendance=True,
            save_participation=True,
            celebrate=True,
            live_run=True,
        )
        tracker = self.school.game.conn.execute(
            """
            SELECT id FROM sessions
            WHERE class_id = ? AND status != 'template'
            ORDER BY id DESC LIMIT 1
            """,
            (self.class_id,),
        ).fetchone()
        self.assertIsNotNone(tracker)
        self.school.game.conn.execute(
            """
            UPDATE session_scores
            SET present = 1, points = 4
            WHERE session_id = ? AND student_id = ?
            """,
            (int(tracker["id"]), int(self.students[0])),
        )
        self.school.game.conn.commit()
        before = self._snapshot()
        self.assertEqual(before["present"], 1)
        self.assertGreater(float(list(before["totals"].values())[0]), 0)

        test = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=True
        )
        self.assertEqual(int(test["is_test"]), 1)
        self._publish_one_answer(int(test["id"]), "M2", "C1")
        self.school.game._set_current_class(self.class_id)
        current_before = self.school.game._current_class_id()
        self.school.begin_or_resume_live_game(self.class_id)
        self.assertEqual(self.school.game._current_class_id(), current_before)
        self.school.finish_live_class(
            self.class_id,
            save_attendance=True,
            save_participation=True,
            celebrate=True,
            live_run=False,
        )
        self.assertEqual(self._snapshot(), before)
        test_slides = [
            row
            for row in self.school.list_lesson_slides_latest(self.class_id)
            if int(row.get("is_test") or 0) == 1
        ]
        self.assertEqual(len(test_slides), 1)
        link = self.school.conn.execute(
            """
            SELECT token, is_open FROM catchup_links
            WHERE live_session_id = ? AND COALESCE(is_test, 0) = 1
            """,
            (int(test["id"]),),
        ).fetchone()
        self.assertIsNotNone(link)
        resolved = self.school.resolve_catchup_link(str(link["token"]))
        self.assertIsNotNone(resolved)
        self.assertEqual(int(resolved["is_test"]), 1)
        part = self.school.participation_week_grid(self.class_id)
        flags = [meta.get("is_test") for meta in (part.get("session_meta") or {}).values()]
        self.assertIn(True, flags)

        with self.assertRaises(PermissionError):
            real_id = int(real["id"])
            if self.school.get_live_session(real_id) is None:
                real_id = int(
                    self.school.conn.execute(
                        """
                        SELECT id FROM live_class_sessions
                        WHERE class_id = ? AND COALESCE(is_test, 0) = 0
                        ORDER BY id DESC LIMIT 1
                        """,
                        (self.class_id,),
                    ).fetchone()["id"]
                )
            self.school.delete_test_live_class(self.class_id, real_id)

        class _Presence:
            def __init__(self) -> None:
                self.deleted: list[int] = []

            def delete_session_tree(self, session_id: int) -> None:
                """Record the presence delete. No join code is stored."""
                self.deleted.append(int(session_id))

        presence = _Presence()
        self.school.presence = presence
        result = self.school.delete_test_live_class(self.class_id, int(test["id"]))
        self.assertEqual(result, {"ok": True, "deleted_session_id": int(test["id"])})
        self.assertEqual(presence.deleted, [int(test["id"])])
        self.assertIsNone(self.school.get_live_session(int(test["id"])))
        self.assertIsNone(self.school.resolve_catchup_link(str(link["token"])))
        self.assertEqual(self._snapshot(), before)
        self.assertFalse(
            [
                row
                for row in self.school.list_lesson_slides_latest(self.class_id)
                if int(row.get("is_test") or 0) == 1
            ]
        )

    def test_attendance_only_test_is_tagged_in_participation(self) -> None:
        """A test class with no answers still shows as TEST, not the next real slot."""
        test = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=True
        )
        self.school.set_live_session_teacher_state(
            int(test["id"]), live_module="M2", live_slot="C3"
        )
        self.school.begin_or_resume_live_game(self.class_id)
        self.school.finish_live_class(
            self.class_id,
            save_attendance=True,
            save_participation=True,
            celebrate=True,
            live_run=True,
        )
        grid = self.school.participation_week_grid(self.class_id)
        tagged = [
            column
            for band in (grid.get("module_bands") or {}).get("bands") or []
            for column in band.get("columns") or []
            if column.get("is_test")
        ]
        self.assertEqual(len(tagged), 1)
        self.assertEqual(tagged[0]["module"], "M2")
        self.assertEqual(tagged[0]["slot"], "C3")
        self.assertEqual(int(tagged[0]["live_session_id"]), int(test["id"]))
        roles = {
            str(band.get("module")): band.get("role")
            for band in (grid.get("module_bands") or {}).get("bands") or []
        }
        self.assertEqual(roles.get("M2"), "open")
        current = (grid.get("module_bands") or {}).get("current_module")
        self.assertNotEqual(current, "M2")

    def test_start_wipe_keeps_the_other_kind(self) -> None:
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=False
        )
        self.school.finish_live_class(self.class_id, celebrate=True, live_run=True)
        test = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=True
        )
        self.school.finish_live_class(self.class_id, celebrate=True, live_run=True)
        self.assertTrue(self.school.session_is_celebrating(int(live["id"])))
        self.assertTrue(self.school.session_is_celebrating(int(test["id"])))
        fresh_live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=False
        )
        self.assertIsNone(self.school.get_live_session(int(live["id"])))
        self.assertIsNotNone(self.school.get_live_session(int(test["id"])))
        self.school.finish_live_class(self.class_id, celebrate=True, live_run=True)
        self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=True
        )
        self.assertIsNone(self.school.get_live_session(int(test["id"])))
        self.assertIsNotNone(self.school.get_live_session(int(fresh_live["id"])))

    def test_course_page_picks_test_at_the_start(self) -> None:
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        body = page.get_data(as_text=True)
        self.assertIn('name="set-class-run-kind"', body)
        self.assertNotIn('name="run_mode"', body)
        self.assertIn("Delete test class", body)
        self.assertIn('name="save_attendance"', body)
        self.assertIn('name="save_participation"', body)


if __name__ == "__main__":
    unittest.main()

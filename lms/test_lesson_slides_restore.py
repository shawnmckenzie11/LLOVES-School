#!/usr/bin/env python3
"""Restore an ended live class that dropped off Lesson Slides."""

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import date
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from cli import register_cli  # noqa: E402
from lesson_slides_restore import (  # noqa: E402
    diagnose_lesson_slides_day,
    restore_lesson_slides_session,
)
from lesson_slides_table import build_lesson_slides_table  # noqa: E402
from test_participation_mck198 import Base  # noqa: E402

# Wednesday Nov 4 2026, 2:05pm Toronto, stored as zone-less UTC like production.
# The class schedule is M/W/F ending 10:30am, so this run is off-schedule.
STARTED_UTC = "2026-11-04T19:05:00"
ENDED_UTC = "2026-11-04T20:20:00"
DAY = date(2026, 11, 4)
DECK_URL = "https://docs.google.com/presentation/d/restore-deck/edit"


class _RestoreFixture(Base):
    """Signed-in class owner, an M2 C1 deck, and session helpers."""

    def setUp(self) -> None:
        """Sign in the class owner and give M2 C1 a deck link."""
        super().setUp()
        self.client = self.app.test_client()
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        code = self.school.get_user_by_email("teacher@gmail.com")["verification_code"]
        self.client.post("/verify-email", data={"code": code})
        self.school.upsert_lesson_slide_deck(
            self.class_id, 2, 1, presentation_id="restore-deck", presentation_url=DECK_URL
        )

    def _class_with_one_answer(self, module: str = "M2", slot: str = "C1") -> int:
        """Start a class at 2:05pm, publish one question, store one answer.

        The session stays active so the test can close it.

        Args:
            module: Teacher module.
            slot: Teacher slot.

        Returns:
            ``live_class_sessions.id``.
        """
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        sid = int(live["id"])
        c = self.school.conn
        c.execute(
            "UPDATE live_class_sessions SET started_at = ?, teacher_state_json = ? WHERE id = ?",
            (STARTED_UTC, json.dumps({"live_module": module, "live_slot": slot}), sid),
        )
        item = {"text": "q0", "type": "mc", "options": [{"text": "A"}], "correct_answer": "A"}
        pid = c.execute(
            "INSERT INTO live_session_prompts (live_session_id, slide_index, kind, payload, "
            "created_at, updated_at) VALUES (?, 1, 'poll', ?, ?, ?)",
            (sid, json.dumps(item), STARTED_UTC, STARTED_UTC),
        ).lastrowid
        c.execute(
            "INSERT INTO live_session_items (live_session_id, placement_key, item_id, stage, kind, "
            "item_json, prompt_id, status, publish_mode, response_mode, published_at, created_at, "
            "updated_at) VALUES (?, 'q0', 'q0', 'round', 'poll', ?, ?, 'closed', 'individual', "
            "'individual', ?, ?, ?)",
            (sid, json.dumps(item), int(pid), STARTED_UTC, STARTED_UTC, STARTED_UTC),
        )
        c.execute(
            "INSERT INTO live_session_responses (prompt_id, student_id, response_json, "
            "created_at, updated_at) VALUES (?, ?, '{\"choice\": \"A\"}', ?, ?)",
            (int(pid), int(self.students[0]), STARTED_UTC, STARTED_UTC),
        )
        c.commit()
        return sid

    def _close_take_attendance_card(self, sid: int) -> None:
        """Close the Take Attendance card, which posts ``/game/cancel``.

        Args:
            sid: Session that the cancel ends.
        """
        self.school.game.begin_game(self.class_id)
        resp = self.client.post(f"/api/classes/{self.class_id}/game/cancel", json={})
        self.assertEqual(resp.status_code, 200)
        self.school.conn.execute(
            "UPDATE live_class_sessions SET ended_at = ? WHERE id = ?", (ENDED_UTC, sid)
        )
        self.school.conn.commit()

    def _row(self, label: str) -> dict:
        """One Lesson Slides row by label.

        Args:
            label: Table label such as ``M2C1``.
        """
        payload = build_lesson_slides_table(self.school, self.class_id)
        return next(item for item in payload["rows"] if item["label"] == label)

    def _counts(self) -> tuple:
        """Rows a dry run must leave unchanged."""
        c = self.school.conn
        return tuple(
            int(c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "participation_runs",
                "participation_items",
                "participation_answers",
                "catchup_links",
                "lesson_slides_latest",
                "lesson_slides_dismissed",
            )
        )


class RestoreLessonSlidesTests(_RestoreFixture):
    """Sessions closed without End are explained and can be put back."""

    def test_closed_card_is_explained_and_restored(self) -> None:
        """A class closed from the attendance card comes back with its links."""
        sid = self._class_with_one_answer()
        self._close_take_attendance_card(sid)
        self.assertFalse(self._row("M2C1")["has_run"])

        found = diagnose_lesson_slides_day(self.school, DAY, course="MCR3U")
        self.assertEqual([d.session_id for d in found], [sid])
        diag = found[0]
        self.assertIn("closed without End Class", diag.cause)
        self.assertTrue(diag.restorable)
        self.assertFalse(diag.shown)
        self.assertGreaterEqual(diag.snapshot_answers, 1)
        self.assertIn("2026-11-04 14:05", diag.summary_line())

        before = self._counts()
        plan = restore_lesson_slides_session(self.school, sid)
        self.assertTrue(all(line.startswith(("session=", "would")) for line in plan))
        self.assertEqual(self._counts(), before)

        done = restore_lesson_slides_session(self.school, sid, apply=True)
        self.assertEqual(done[-1], "on_lesson_slides=yes")
        row = self._row("M2C1")
        self.assertTrue(row["has_run"])
        self.assertEqual(row["status_label"], "Delivered")
        self.assertEqual(row["deck_url"], DECK_URL)
        self.assertNotEqual(row["catchup_access"], "none")
        run = self.school.conn.execute(
            "SELECT is_real, discarded, teacher_kind_set, ended_at FROM participation_runs "
            "WHERE class_id = ? AND run_key = ?",
            (self.class_id, diag.run_key),
        ).fetchone()
        self.assertEqual((int(run[0]), int(run[1]), int(run[2])), (1, 0, 1))
        self.assertEqual(str(run[3]), ENDED_UTC)
        answers = self.school.conn.execute(
            "SELECT COUNT(*) FROM participation_answers WHERE class_id = ? AND student_id = ?",
            (self.class_id, int(self.students[0])),
        ).fetchone()[0]
        self.assertEqual(int(answers), 1)
        link = self.school.conn.execute(
            "SELECT live_session_id, open_until FROM catchup_links WHERE class_id = ? AND run_key = ?",
            (self.class_id, diag.run_key),
        ).fetchone()
        self.assertEqual(int(link["live_session_id"]), sid)
        self.assertTrue(str(link["open_until"]).startswith("2026-11-"))

        with self.assertRaises(ValueError):
            restore_lesson_slides_session(self.school, sid, apply=True)

    def test_deleted_entry_is_cleared(self) -> None:
        """A run deleted from Lesson Slides is listed again after restore."""
        sid = self._class_with_one_answer()
        self.school.finish_live_class(self.class_id, celebrate=True, live_run=True)
        self.school.conn.execute(
            "UPDATE live_class_sessions SET started_at = ? WHERE id = ?", (STARTED_UTC, sid)
        )
        self.school.conn.commit()
        shown = self._row("M2C1")
        self.assertTrue(shown["has_run"])
        self.school.delete_lesson_slides_entry(
            self.class_id, "M2", "C1", False, shown["run_key"]
        )
        self.assertFalse(self._row("M2C1")["has_run"])
        diag = diagnose_lesson_slides_day(self.school, DAY)[0]
        self.assertTrue(diag.dismissed)
        self.assertIn("deleted from Lesson Slides", diag.cause)
        restore_lesson_slides_session(self.school, sid, apply=True)
        self.assertTrue(self._row("M2C1")["has_run"])
        self.assertEqual(self.school.lesson_slides_dismissed_keys(self.class_id), set())

    def test_failed_end_save_is_retried(self) -> None:
        """When End's save failed, restore saves the stored live answers."""
        sid = self._class_with_one_answer("M3", "C2")

        def fail_capture(*_args, **_kwargs) -> None:
            """Simulate the participation save failing inside End."""
            raise RuntimeError("participation capture failed")

        original = self.school._capture_participation_unlocked
        self.school._capture_participation_unlocked = fail_capture
        try:
            self.school.finish_live_class(self.class_id, celebrate=True, live_run=True)
        finally:
            self.school._capture_participation_unlocked = original
        self.assertFalse(self._row("M3C2")["has_run"])
        diag = diagnose_lesson_slides_day(self.school, DAY)[0]
        self.assertTrue(diag.unsaved_answers)
        self.assertIn("save failed", diag.cause)
        done = restore_lesson_slides_session(self.school, sid, apply=True)
        self.assertIn("retry the End save from the stored live answers", done)
        self.assertTrue(self._row("M3C2")["has_run"])

    def test_open_test_and_quit_sessions_are_refused(self) -> None:
        """Open, test, and Quit classes are reported and never restored."""
        open_sid = self._class_with_one_answer("M4", "C1")
        diag = diagnose_lesson_slides_day(self.school, DAY)[0]
        self.assertIn("never ended", diag.cause)
        self.assertFalse(diag.restorable)
        with self.assertRaises(ValueError):
            restore_lesson_slides_session(self.school, open_sid)
        self.school.finish_live_class(self.class_id, persist=False, celebrate=False)
        self.assertEqual(diagnose_lesson_slides_day(self.school, DAY), [])
        with self.assertRaisesRegex(ValueError, "Quit"):
            restore_lesson_slides_session(self.school, open_sid)

        test = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=True
        )
        test_sid = int(test["id"])
        self.school.conn.execute(
            "UPDATE live_class_sessions SET started_at = ?, teacher_state_json = ? WHERE id = ?",
            (STARTED_UTC, json.dumps({"live_module": "M4", "live_slot": "C2"}), test_sid),
        )
        self.school.conn.commit()
        self.school.end_live_class_session(test_sid)
        diag = next(
            d for d in diagnose_lesson_slides_day(self.school, DAY) if d.session_id == test_sid
        )
        self.assertFalse(diag.restorable)
        with self.assertRaises(ValueError):
            restore_lesson_slides_session(self.school, test_sid)

    def test_day_filter_uses_toronto_dates(self) -> None:
        """A late-evening class counts on its Toronto date, not its UTC date."""
        sid = self._class_with_one_answer()
        self.school.conn.execute(
            "UPDATE live_class_sessions SET started_at = ? WHERE id = ?",
            ("2026-11-05T03:30:00", sid),
        )
        self.school.conn.commit()
        self.assertEqual([d.session_id for d in diagnose_lesson_slides_day(self.school, DAY)], [sid])
        self.assertEqual(diagnose_lesson_slides_day(self.school, date(2026, 11, 5)), [])
        self.assertEqual(diagnose_lesson_slides_day(self.school, DAY, course="MCF3M"), [])


class RestoreLessonSlidesCliTests(_RestoreFixture):
    """The ``restore-lesson-slides`` command is dry-run unless applied."""

    def setUp(self) -> None:
        """Register the CLI on the test app."""
        super().setUp()
        register_cli(self.app)
        self.runner = self.app.test_cli_runner()

    def test_requires_a_date_or_session(self) -> None:
        """No arguments is an error, and --apply needs --session."""
        self.assertNotEqual(self.runner.invoke(args=["restore-lesson-slides"]).exit_code, 0)
        result = self.runner.invoke(
            args=["restore-lesson-slides", "--date", "2026-11-04", "--apply"]
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--apply requires --session", result.output)

    def test_list_then_dry_run_then_apply(self) -> None:
        """The day list names the session; only --apply writes."""
        sid = self._class_with_one_answer()
        self._close_take_attendance_card(sid)
        listed = self.runner.invoke(
            args=["restore-lesson-slides", "--date", "2026-11-04", "--course", "MCR3U"]
        )
        self.assertEqual(listed.exit_code, 0, listed.output)
        self.assertIn(f"session={sid}", listed.output)
        self.assertIn("[restorable]", listed.output)
        before = self._counts()
        dry = self.runner.invoke(args=["restore-lesson-slides", "--session", str(sid)])
        self.assertEqual(dry.exit_code, 0, dry.output)
        self.assertIn("DRY RUN", dry.output)
        self.assertEqual(self._counts(), before)
        applied = self.runner.invoke(
            args=["restore-lesson-slides", "--session", str(sid), "--apply"]
        )
        self.assertEqual(applied.exit_code, 0, applied.output)
        self.assertIn("on_lesson_slides=yes", applied.output)
        self.assertTrue(self._row("M2C1")["has_run"])

    def test_production_apply_needs_override_and_phrase(self) -> None:
        """On Fly, --apply needs the override flag and the typed phrase."""
        sid = self._class_with_one_answer()
        self._close_take_attendance_card(sid)
        os.environ["FLY_APP_NAME"] = "lloves-lms"
        try:
            refused = self.runner.invoke(
                args=["restore-lesson-slides", "--session", str(sid), "--apply"]
            )
            self.assertNotEqual(refused.exit_code, 0)
            wrong = self.runner.invoke(
                args=[
                    "restore-lesson-slides", "--session", str(sid), "--apply",
                    "--i-know-this-is-prod",
                ],
                input="RESTORE:0\n",
            )
            self.assertNotEqual(wrong.exit_code, 0)
            self.assertFalse(self._row("M2C1")["has_run"])
            ok = self.runner.invoke(
                args=[
                    "restore-lesson-slides", "--session", str(sid), "--apply",
                    "--i-know-this-is-prod",
                ],
                input=f"RESTORE:{sid}\n",
            )
            self.assertEqual(ok.exit_code, 0, ok.output)
            self.assertIn("lesson_slides_restore_audit", ok.output)
        finally:
            os.environ.pop("FLY_APP_NAME", None)
        self.assertTrue(self._row("M2C1")["has_run"])


if __name__ == "__main__":
    unittest.main()

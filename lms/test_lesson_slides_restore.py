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
    diagnose_session,
    move_lesson_slides_run,
    parse_slot_token,
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


M4C1_DECK = "https://docs.google.com/presentation/d/m4c1-deck/edit"
EARLIER_STARTED_UTC = "2026-11-02T19:05:00"
EARLIER_ENDED_UTC = "2026-11-02T20:20:00"


class MoveLessonSlidesRunTests(_RestoreFixture):
    """A class started under the wrong slot moves and the slot it took is rebuilt."""

    def _ended_real_class(self, module: str, slot: str, *, started: str, ended: str) -> int:
        """Run one class to End as a real class with fixed start and end times.

        Args:
            module: Teacher module.
            slot: Teacher slot.
            started: Zone-less UTC start.
            ended: Zone-less UTC end.

        Returns:
            ``live_class_sessions.id``.
        """
        sid = self._class_with_one_answer(module, slot)
        self.school.conn.execute(
            "UPDATE live_class_sessions SET started_at = ?, meeting_date = ? WHERE id = ?",
            (started, started[:10], sid),
        )
        self.school.finish_live_class(self.class_id, celebrate=True, live_run=True)
        c = self.school.conn
        key = str(c.execute("SELECT run_key FROM live_class_sessions WHERE id = ?", (sid,)).fetchone()[0])
        self.school.set_participation_run_real(self.class_id, key, True)
        c.execute("UPDATE live_class_sessions SET ended_at = ? WHERE id = ?", (ended, sid))
        c.execute(
            "UPDATE participation_runs SET ended_at = ? WHERE class_id = ? AND run_key = ?",
            (ended, self.class_id, key),
        )
        c.execute(
            "UPDATE lesson_slides_latest SET ended_at = ? WHERE class_id = ? AND run_key = ?",
            (ended, self.class_id, key),
        )
        c.commit()
        return sid

    def _session(self, sid: int) -> dict:
        """Run key and join code of one session.

        Args:
            sid: ``live_class_sessions.id``.
        """
        row = self.school.conn.execute(
            "SELECT run_key, session_code FROM live_class_sessions WHERE id = ?", (sid,)
        ).fetchone()
        return {"run_key": str(row[0]), "code": str(row[1])}

    def _link(self, run_key: str) -> dict | None:
        """Catch-up link row for one run, or ``None``.

        Args:
            run_key: Participation run key.
        """
        row = self.school.conn.execute(
            "SELECT * FROM catchup_links WHERE class_id = ? AND run_key = ?",
            (self.class_id, run_key),
        ).fetchone()
        return dict(row) if row is not None else None

    def setUp(self) -> None:
        """Give M4 C1 its own deck link."""
        super().setUp()
        self.school.upsert_lesson_slide_deck(
            self.class_id, 4, 1, presentation_id="m4c1-deck", presentation_url=M4C1_DECK
        )

    def test_wrong_slot_moves_and_the_earlier_class_comes_back(self) -> None:
        """Nov 4 saved as M4 C1 moves to M4 C3; Nov 2's M4 C1 gets its row and link back."""
        earlier = self._ended_real_class(
            "M4", "C1", started=EARLIER_STARTED_UTC, ended=EARLIER_ENDED_UTC
        )
        a = self._session(earlier)
        a_token = self._link(a["run_key"])["token"]
        wrong = self._ended_real_class("M4", "C1", started=STARTED_UTC, ended=ENDED_UTC)
        b = self._session(wrong)
        self.school.conn.execute(
            "INSERT INTO live_class_feedback (class_id, codename, meeting_date, token, mood, "
            "live_module, live_slot, created_at) VALUES (?, 'Maple', '2026-11-04', 'fb', 4, "
            "'M4', 'C1', ?)",
            (self.class_id, ENDED_UTC),
        )
        self.school.conn.commit()

        self.assertEqual(self._row("M4C1")["run_key"], b["run_key"])
        self.assertIsNone(self._link(a["run_key"]))
        self.assertEqual(self._link(b["run_key"])["token"], a_token)
        self.assertFalse(self._row("M4C3")["has_run"])

        before = self._counts()
        plan = move_lesson_slides_run(self.school, wrong, "M4", "C3")
        self.assertEqual(self._counts(), before)
        self.assertEqual(self._row("M4C1")["run_key"], b["run_key"])
        self.assertIn("would move this class from M4 C1 to M4 C3", plan)
        self.assertTrue(any(f"(session={earlier}," in line for line in plan), plan)
        self.assertTrue(any("live_class_feedback=1" in line for line in plan), plan)

        done = move_lesson_slides_run(self.school, wrong, "M4", "C3", apply=True)
        self.assertEqual(done[-1], "on_lesson_slides=yes as M4 C3")

        moved = self._row("M4C3")
        self.assertEqual(moved["run_key"], b["run_key"])
        self.assertEqual(moved["deck_url"], M4C1_DECK)
        restored = self._row("M4C1")
        self.assertEqual(restored["run_key"], a["run_key"])
        self.assertEqual(restored["deck_url"], M4C1_DECK)

        a_link = self._link(a["run_key"])
        b_link = self._link(b["run_key"])
        self.assertEqual((a_link["live_module"], a_link["live_slot"]), ("M4", "C1"))
        self.assertEqual(a_link["token"], a_token)
        self.assertEqual(a_link["portal_join_code"], a["code"])
        self.assertEqual((b_link["live_module"], b_link["live_slot"]), ("M4", "C3"))
        self.assertEqual(b_link["portal_join_code"], b["code"])
        self.assertNotEqual(b_link["token"], a_token)

        c = self.school.conn
        slots = {
            tuple(r)
            for r in c.execute(
                "SELECT live_module, live_slot FROM participation_items WHERE class_id = ? AND run_key = ?",
                (self.class_id, b["run_key"]),
            )
        }
        self.assertEqual(slots, {("M4", "C3")})
        feedback = c.execute("SELECT live_module, live_slot FROM live_class_feedback").fetchone()
        self.assertEqual(tuple(feedback), ("M4", "C3"))
        diag = diagnose_session(self.school, wrong)
        self.assertEqual((diag.module, diag.slot), ("M4", "C3"))
        self.assertEqual((diagnose_session(self.school, earlier).module,), ("M4",))

        with self.assertRaisesRegex(ValueError, "already has a saved real class"):
            move_lesson_slides_run(self.school, wrong, "M4", "C1")
        with self.assertRaisesRegex(ValueError, "already M4 C3"):
            move_lesson_slides_run(self.school, wrong, "M4", "C3")

    def test_move_without_an_earlier_class_clears_the_slot(self) -> None:
        """With nothing earlier in M4 C1 the slot is emptied and the link token kept."""
        sid = self._ended_real_class("M4", "C1", started=STARTED_UTC, ended=ENDED_UTC)
        b = self._session(sid)
        token = self._link(b["run_key"])["token"]
        plan = move_lesson_slides_run(self.school, sid, "M4", "C3")
        self.assertIn("would clear M4 C1: no earlier real class was found there", plan)
        move_lesson_slides_run(self.school, sid, "M4", "C3", apply=True)
        self.assertFalse(self._row("M4C1")["has_run"])
        self.assertEqual(self._row("M4C3")["run_key"], b["run_key"])
        self.assertEqual(self._link(b["run_key"])["token"], token)

    def test_refuses_unsaved_and_unknown_targets(self) -> None:
        """A run never saved as real, or a slot the course lacks, is refused."""
        sid = self._class_with_one_answer("M4", "C1")
        self._close_take_attendance_card(sid)
        with self.assertRaisesRegex(ValueError, "no saved real run"):
            move_lesson_slides_run(self.school, sid, "M4", "C3")
        ended = self._ended_real_class("M4", "C2", started=STARTED_UTC, ended=ENDED_UTC)
        with self.assertRaisesRegex(ValueError, "not a live class"):
            move_lesson_slides_run(self.school, ended, "M4", "C9")
        with self.assertRaises(ValueError):
            parse_slot_token("Module 4")
        self.assertEqual(parse_slot_token("m4 c3"), ("M4", "C3"))
        self.assertEqual(parse_slot_token("M4·C3"), ("M4", "C3"))


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

    def test_move_to_dry_runs_then_needs_the_move_phrase(self) -> None:
        """--move-to plans first and, on Fly, applies only after MOVE:<id>:<slot>."""
        sid = self._class_with_one_answer("M2", "C1")
        self.school.finish_live_class(self.class_id, celebrate=True, live_run=True)
        key = str(
            self.school.conn.execute(
                "SELECT run_key FROM live_class_sessions WHERE id = ?", (sid,)
            ).fetchone()[0]
        )
        self.school.set_participation_run_real(self.class_id, key, True)
        self.assertNotEqual(
            self.runner.invoke(args=["restore-lesson-slides", "--date", "2026-11-04", "--move-to", "M2C3"]).exit_code,
            0,
        )
        dry = self.runner.invoke(args=["restore-lesson-slides", "--session", str(sid), "--move-to", "M2C3"])
        self.assertEqual(dry.exit_code, 0, dry.output)
        self.assertIn("move lesson slides run to M2 C3 (DRY RUN)", dry.output)
        self.assertIn("would move this class from M2 C1 to M2 C3", dry.output)
        self.assertTrue(self._row("M2C1")["has_run"])
        os.environ["FLY_APP_NAME"] = "lloves-lms"
        try:
            args = [
                "restore-lesson-slides", "--session", str(sid), "--move-to", "M2C3",
                "--apply", "--i-know-this-is-prod",
            ]
            wrong = self.runner.invoke(args=args, input=f"RESTORE:{sid}\n")
            self.assertNotEqual(wrong.exit_code, 0)
            self.assertTrue(self._row("M2C1")["has_run"])
            ok = self.runner.invoke(args=args, input=f"MOVE:{sid}:M2C3\n")
            self.assertEqual(ok.exit_code, 0, ok.output)
            self.assertIn("moved_to=M2C3", ok.output)
        finally:
            os.environ.pop("FLY_APP_NAME", None)
        self.assertFalse(self._row("M2C1")["has_run"])
        self.assertEqual(self._row("M2C3")["run_key"], key)


if __name__ == "__main__":
    unittest.main()

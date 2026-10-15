#!/usr/bin/env python3
"""MCK-229: Lesson Slides run policy and backfill safety (ELC student-data fixes)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from lesson_slides_backfill import backfill_lesson_slides_latest  # noqa: E402
from lesson_slides_run_policy import (  # noqa: E402
    RunEndInstant,
    compare_run_end_instant,
    effective_run_is_real,
    run_end_instant_for_ranking,
    run_qualifies_for_lesson_slides_latest,
)
from live_class_run import parse_ended_at  # noqa: E402
from test_mck229_lesson_slides_backfill import LessonSlidesBackfillTests  # noqa: E402




def _set_run_started_at(school, class_id: int, run_key: str, started_iso: str) -> None:
    """Align session start with schedule-window tests (real/test uses start time)."""
    school.conn.execute(
        """
        UPDATE live_class_sessions SET started_at = ?
        WHERE class_id = ? AND run_key = ?
        """,
        (started_iso, int(class_id), str(run_key)),
    )
    school.conn.commit()


class RunPolicyScheduleTests(LessonSlidesBackfillTests):
    """Recompute real/test from scheduled start window (not stored ``is_real`` alone)."""

    def test_pre_mck207_run_classified_by_start_window(self) -> None:
        """A run with ``is_real=0`` defaults still counts when it ended in the start window."""
        run_key = LessonSlidesBackfillTests._ended_real_run_keep_session(
            self,
            "M2",
            "C1",
            ended_iso="2026-10-14T14:29:00",
        )
        self.school.conn.execute(
            """
            UPDATE participation_runs
            SET is_real = 0, auto_is_real = 0, discarded = 0, ended_at = NULL
            WHERE class_id = ? AND run_key = ?
            """,
            (int(self.class_id), run_key),
        )
        _set_run_started_at(self.school, self.class_id, run_key, "2026-10-14T13:15:00")
        self.school.conn.commit()
        self.assertTrue(effective_run_is_real(self.school, self.class_id, run_key))

    def test_outside_start_window_is_not_real_without_teacher_override(self) -> None:
        """Tuesday afternoon runs stay test when flags were never set."""
        run_key = LessonSlidesBackfillTests._ended_real_run_keep_session(
            self,
            "M2",
            "C1",
            ended_iso="2026-10-20T15:00:00",
        )
        self.school.conn.execute(
            """
            UPDATE participation_runs
            SET is_real = 0, auto_is_real = 0, discarded = 0, ended_at = NULL
            WHERE class_id = ? AND run_key = ?
            """,
            (int(self.class_id), run_key),
        )
        _set_run_started_at(self.school, self.class_id, run_key, "2026-10-20T13:15:00")
        self.school.conn.commit()
        self.assertFalse(effective_run_is_real(self.school, self.class_id, run_key))

    def test_teacher_manual_real_test_not_overridden_by_schedule(self) -> None:
        """MCK-207 teacher toggles win over start-window recomputation."""
        outside_key = LessonSlidesBackfillTests._ended_real_run_keep_session(
            self,
            "M2",
            "C2",
            ended_iso="2026-10-20T15:00:00",
        )
        self.school.conn.execute(
            """
            UPDATE participation_runs
            SET is_real = 0, auto_is_real = 0, discarded = 0, ended_at = NULL
            WHERE class_id = ? AND run_key = ?
            """,
            (int(self.class_id), outside_key),
        )
        self.school.conn.commit()
        self.assertFalse(effective_run_is_real(self.school, self.class_id, outside_key))
        self.school.set_participation_run_real(
            self.class_id, outside_key, True, user_id=int(self.teacher["id"])
        )
        self.assertTrue(effective_run_is_real(self.school, self.class_id, outside_key))

        inside_key = LessonSlidesBackfillTests._ended_real_run_keep_session(
            self,
            "M2",
            "C3",
            ended_iso="2026-10-14T14:29:00",
        )
        self.assertTrue(effective_run_is_real(self.school, self.class_id, inside_key))
        self.school.set_participation_run_real(
            self.class_id, inside_key, False, user_id=int(self.teacher["id"])
        )
        self.assertFalse(effective_run_is_real(self.school, self.class_id, inside_key))
        self.assertTrue(self.school.participation_run_discarded(self.class_id, inside_key))


class RunPolicyRankingTests(unittest.TestCase):
    """Latest run selection uses parsed datetimes."""

    def test_string_order_would_be_wrong(self) -> None:
        """Lexicographic order differs from chronological order."""
        older = RunEndInstant(
            value=parse_ended_at("2026-10-03T10:00:00"),
            inferred=True,
            iso="2026-10-3T10:00:00",
        )
        newer = RunEndInstant(
            value=parse_ended_at("2026-10-22T10:00:00"),
            inferred=True,
            iso="2026-10-22T10:00:00",
        )
        self.assertGreater(compare_run_end_instant(newer, older), 0)
        self.assertTrue(str(older.iso) > str(newer.iso))  # lexicographic order lies


class InferredEndedAtStorageTests(LessonSlidesBackfillTests):
    """Guessed end times never land in ``participation_runs.ended_at``."""

    def test_backfill_stores_inferred_on_lesson_slides_latest_only(self) -> None:
        """Inferred snapshot time is flagged on ``lesson_slides_latest``."""
        run_key = LessonSlidesBackfillTests._ended_real_run_keep_session(
            self,
            "M1",
            "C1",
            ended_iso="2026-10-14T14:29:00",
        )
        self.school.conn.execute("DELETE FROM live_class_sessions WHERE class_id = ?", (self.class_id,))
        self.school.conn.execute(
            "UPDATE participation_runs SET ended_at = NULL WHERE class_id = ? AND run_key = ?",
            (int(self.class_id), run_key),
        )
        self.school.conn.commit()
        backfill_lesson_slides_latest(self.school)
        pr = self.school.conn.execute(
            "SELECT ended_at FROM participation_runs WHERE class_id = ? AND run_key = ?",
            (int(self.class_id), run_key),
        ).fetchone()
        self.assertFalse(str(pr["ended_at"] or "").strip())
        latest = self.school.list_lesson_slides_latest(self.class_id)[0]
        self.assertEqual(int(latest.get("ended_at_inferred") or 0), 1)
        self.assertTrue(str(latest.get("inferred_ended_at") or "").strip())


class RealStudentAnswerGateTests(LessonSlidesBackfillTests):
    """Runs without a real student answer cannot become latest."""

    def test_test_student_only_run_is_skipped(self) -> None:
        """Only Test Student answers do not qualify for backfill."""
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        sid = int(live["id"])
        c = self.school.conn
        run_key = str(
            c.execute("SELECT run_key FROM live_class_sessions WHERE id = ?", (sid,)).fetchone()[0]
        )
        c.execute(
            "UPDATE live_class_sessions SET teacher_state_json = ? WHERE id = ?",
            (json.dumps({"live_module": "M3", "live_slot": "C2"}), sid),
        )
        now = "2026-10-14T14:29:00"
        c.execute(
            """
            INSERT INTO participation_items (
                class_id, run_key, live_module, live_slot, question_key, item_json,
                prompt_kind, offered_at
            ) VALUES (?, ?, 'M3', 'C2', 'item:q0', '{}', 'poll', ?)
            """,
            (int(self.class_id), run_key, now),
        )
        c.execute(
            """
            INSERT INTO participation_runs (
                class_id, run_key, is_real, auto_is_real, discarded, ended_at
            ) VALUES (?, ?, 1, 1, 0, NULL)
            """,
            (int(self.class_id), run_key),
        )
        test_id = int(self.school.ensure_class_test_student(self.class_id)["student_id"])
        c.execute(
            """
            INSERT INTO participation_answers (
                class_id, run_key, live_module, question_key, student_id, source,
                answer_json, answered_at
            ) VALUES (?, ?, 'M3', 'item:q0', ?, 'live', '{}', ?)
            """,
            (int(self.class_id), run_key, test_id, now),
        )
        c.commit()
        _, ok = run_qualifies_for_lesson_slides_latest(self.school, self.class_id, run_key)
        self.assertFalse(ok)
        report = backfill_lesson_slides_latest(self.school)
        self.assertEqual(report["inserted"] + report["updated"], 0)



class CatchupCoalesceTests(LessonSlidesBackfillTests):
    """``list_catchup_links`` treats missing ``is_real`` as false, then recomputes."""

    def test_catchup_link_shown_when_schedule_says_real(self) -> None:
        """NULL ``is_real`` no longer defaults to real in SQL."""
        run_key = LessonSlidesBackfillTests._ended_real_run_keep_session(
            self,
            "M4",
            "C1",
            ended_iso="2026-10-14T14:29:00",
        )
        self.school.conn.execute(
            """
            UPDATE participation_runs
            SET is_real = 0, auto_is_real = 0, ended_at = NULL
            WHERE class_id = ? AND run_key = ?
            """,
            (int(self.class_id), run_key),
        )
        _set_run_started_at(self.school, self.class_id, run_key, "2026-10-14T13:15:00")
        self.school.conn.commit()
        links = self.school.list_catchup_links(self.class_id)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["run_key"], run_key)


class BootBackfillSurvivesTests(unittest.TestCase):
    """Startup continues when lesson slides backfill raises."""

    def test_create_app_when_backfill_raises(self) -> None:
        with patch(
            "lesson_slides_backfill.backfill_lesson_slides_latest",
            side_effect=RuntimeError("lesson slides backfill failed"),
        ):
            tmp = tempfile.TemporaryDirectory()
            root = Path(tmp.name)
            app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
            school = app.config["SCHOOL_DB"]
            self.assertIsNotNone(school.conn.execute("SELECT 1").fetchone())
            school.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()

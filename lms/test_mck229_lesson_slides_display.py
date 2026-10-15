#!/usr/bin/env python3
"""MCK-229 follow-up: Lesson Slides display and backfill safety."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from lesson_slides_backfill import backfill_lesson_slides_latest  # noqa: E402
from lesson_slides_table import build_lesson_slides_table  # noqa: E402
from live_class_run import parse_ended_at  # noqa: E402
from test_mck207_real_vs_test_runs import RealVsTestFlowTests  # noqa: E402
from test_mck229_lesson_slides_backfill import LessonSlidesBackfillTests  # noqa: E402


class LessonSlidesTorontoDisplayTests(unittest.TestCase):
    """Naive UTC stored timestamps map to Toronto wall clock for display."""

    def test_parse_ended_at_treats_naive_as_utc(self) -> None:
        """14:29 UTC on an EDT day is 10:29 AM in Toronto."""
        dt = parse_ended_at("2026-10-14T14:29:00")
        self.assertEqual(str(dt.tzinfo), "America/Toronto")
        self.assertEqual((dt.hour, dt.minute), (10, 29))

    def test_staff_script_formats_in_toronto(self) -> None:
        """Client formatter documents Toronto and naive-UTC parsing."""
        js = (LMS_DIR / "static" / "staff_lesson_slides.js").read_text(encoding="utf-8")
        self.assertIn("parseStoredUtcIso", js)
        self.assertIn("America/Toronto", js)




class ParticipationEndedAtBackfillSafetyTests(RealVsTestFlowTests):
    """Lesson slides backfill never mass-writes ``participation_runs.ended_at`` (#322)."""

    def test_ended_at_backfill_never_overwrites_or_changes_flags(self) -> None:
        """``backfill_lesson_slides_latest`` does not touch stored run flags or ``ended_at``."""
        run_key = LessonSlidesBackfillTests._ended_real_run_keep_session(
            self,
            "M2",
            "C2",
            ended_iso="2026-10-14T14:29:00",
        )
        c = self.school.conn
        c.execute(
            """
            UPDATE participation_runs
            SET ended_at = ?, is_real = 1, discarded = 0
            WHERE class_id = ? AND run_key = ?
            """,
            ("2026-10-01T08:00:00", int(self.class_id), run_key),
        )
        c.execute(
            """
            INSERT INTO live_result_snapshots (
                live_session_id, class_id, session_code, run_key, live_module, live_slot,
                source, question_key, snapshot_at
            ) VALUES (0, ?, '', ?, 'M2', 'C2', 'prompt', 'item:q0', '2026-10-20T20:00:00')
            """,
            (int(self.class_id), run_key),
        )
        c.commit()
        before = dict(
            c.execute(
                "SELECT is_real, discarded, ended_at FROM participation_runs WHERE class_id = ? AND run_key = ?",
                (int(self.class_id), run_key),
            ).fetchone()
        )
        backfill_lesson_slides_latest(self.school)
        backfill_lesson_slides_latest(self.school)
        after = dict(
            c.execute(
                "SELECT is_real, discarded, ended_at FROM participation_runs WHERE class_id = ? AND run_key = ?",
                (int(self.class_id), run_key),
            ).fetchone()
        )
        self.assertEqual(after, before)

    def test_ended_at_backfill_fills_only_empty(self) -> None:
        """Missing ``ended_at`` stays empty on ``participation_runs``; latest row uses inferred time."""
        c = self.school.conn
        c.execute(
            """
            INSERT INTO participation_runs (
                class_id, run_key, is_real, auto_is_real, discarded, ended_at
            ) VALUES (?, 'orphan-run', 1, 1, 0, NULL)
            """,
            (int(self.class_id),),
        )
        c.execute(
            """
            INSERT INTO participation_items (
                class_id, run_key, live_module, live_slot, question_key, item_json,
                prompt_kind, offered_at
            ) VALUES (?, 'orphan-run', 'M1', 'C3', 'item:q0', '{}', 'poll', '2026-10-14T15:00:00')
            """,
            (int(self.class_id),),
        )
        student_id = int(self.students[0])
        c.execute(
            """
            INSERT INTO participation_answers (
                class_id, run_key, live_module, question_key, student_id, source,
                answer_json, answered_at
            ) VALUES (?, 'orphan-run', 'M1', 'item:q0', ?, 'live', '{}', '2026-10-14T15:00:00')
            """,
            (int(self.class_id), student_id),
        )
        c.commit()
        backfill_lesson_slides_latest(self.school)
        row = c.execute(
            "SELECT is_real, discarded, ended_at FROM participation_runs WHERE run_key = 'orphan-run'"
        ).fetchone()
        self.assertEqual(int(row["is_real"]), 1)
        self.assertEqual(int(row["discarded"]), 0)
        self.assertFalse(str(row["ended_at"] or "").strip())
        latest = c.execute(
            """
            SELECT ended_at_inferred, inferred_ended_at FROM lesson_slides_latest
            WHERE class_id = ? AND live_module = 'M1' AND live_slot = 'C3'
            """,
            (int(self.class_id),),
        ).fetchone()
        self.assertIsNotNone(latest)
        self.assertEqual(int(latest["ended_at_inferred"]), 1)
        self.assertTrue(str(latest["inferred_ended_at"] or "").strip())


class LessonSlidesJoinCodeDisplayTests(LessonSlidesBackfillTests):
    """Backfilled rows use catch-up portal code when session code is empty."""

    def test_backfilled_row_shows_portal_not_empty_session_code(self) -> None:
        """Join code is catch-up portal (or dash), not blank session_code."""
        run_key = "42ca747b-e2f0-4c3b-9a1d-0e5f6a7b8c9d"
        LessonSlidesBackfillTests._ended_real_run_keep_session(
            self,
            "M3",
            "C1",
            ended_iso="2026-10-14T14:29:00",
            run_key=run_key,
        )
        self.school.conn.execute(
            """
            UPDATE catchup_links SET portal_join_code = ?
            WHERE class_id = ? AND UPPER(live_module) = 'M3' AND UPPER(live_slot) = 'C1'
            """,
            ("R3W1U", int(self.class_id)),
        )
        self.school.conn.execute("DELETE FROM live_class_sessions WHERE class_id = ?", (self.class_id,))
        self.school.conn.commit()
        backfill_lesson_slides_latest(self.school)
        latest = self.school.list_lesson_slides_latest(self.class_id)[0]
        self.assertEqual(str(latest.get("session_code") or "").strip(), "")
        payload = build_lesson_slides_table(self.school, self.class_id)
        row = next(r for r in payload["rows"] if r["label"] == "M3C1")
        self.assertEqual(row["join_code"], "R3W1U")
        self.assertTrue(str(row["last_run_at"]).startswith("2026-10-14"))


class LessonSlidesBootBackfillTests(unittest.TestCase):
    """Boot must survive lesson_slides backfill failures."""

    def test_create_app_when_backfill_raises(self) -> None:
        """SchoolDB opens when ``backfill_lesson_slides_latest`` raises."""
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

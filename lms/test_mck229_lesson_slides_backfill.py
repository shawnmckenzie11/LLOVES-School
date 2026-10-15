#!/usr/bin/env python3
"""MCK-229: backfill lesson_slides_latest + catch-up run fallback."""

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

from lesson_slides_backfill import backfill_lesson_slides_latest  # noqa: E402
from lesson_slides_table import build_lesson_slides_table  # noqa: E402
from test_mck207_real_vs_test_runs import RealVsTestFlowTests  # noqa: E402


class LessonSlidesBackfillTests(RealVsTestFlowTests):
    """Backfill and UI fallback when ``lesson_slides_latest`` is empty."""


    def _ensure_real_student_answer(self, run_key: str, module: str, slot: str, answered_at: str) -> None:
        """Backfill requires at least one non–Test Student participation answer."""
        from participation_real_students import exclude_participation_answer_student_ids

        c = self.school.conn
        test_ids = exclude_participation_answer_student_ids(int(self.class_id), self.school)
        roster = list(getattr(self, "students", None) or [])
        if not roster:
            roster = [
                int(r[0])
                for r in self.school.game.conn.execute(
                    "SELECT id FROM students WHERE class_id = ? ORDER BY id",
                    (int(self.class_id),),
                ).fetchall()
            ]
        student_id = None
        for sid in roster:
            if int(sid) not in test_ids:
                student_id = int(sid)
                break
        if student_id is None and roster:
            student_id = int(roster[0])
        item = c.execute(
            """
            SELECT question_key FROM participation_items
            WHERE class_id = ? AND run_key = ? LIMIT 1
            """,
            (int(self.class_id), str(run_key)),
        ).fetchone()
        qkey = str(item[0] if item else "item:q0")
        c.execute(
            """
            INSERT OR IGNORE INTO participation_answers (
                class_id, run_key, live_module, question_key, student_id, source,
                answer_json, answered_at
            ) VALUES (?, ?, ?, ?, ?, 'live', '{}', ?)
            """,
            (int(self.class_id), str(run_key), module, qkey, student_id, answered_at),
        )
        c.commit()

    def _ended_real_run_keep_session(
        self,
        module: str,
        slot: str,
        *,
        ended_iso: str,
        run_key: str | None = None,
        live_run: bool = True,
    ) -> str:
        """End a real run but keep the session row for backfill (prod-shaped)."""
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        sid = int(live["id"])
        c = self.school.conn
        if run_key:
            c.execute("UPDATE live_class_sessions SET run_key = ? WHERE id = ?", (run_key, sid))
        c.execute(
            "UPDATE live_class_sessions SET teacher_state_json = ? WHERE id = ?",
            (json.dumps({"live_module": module, "live_slot": slot}), sid),
        )
        now = ended_iso
        pid = c.execute(
            "INSERT INTO live_session_prompts (live_session_id, slide_index, kind, payload, "
            "created_at, updated_at) VALUES (?, 0, 'poll', '{}', ?, ?)",
            (sid, now, now),
        ).lastrowid
        c.execute(
            "INSERT INTO live_session_items (live_session_id, placement_key, item_id, stage, kind, "
            "item_json, prompt_id, status, publish_mode, response_mode, published_at, created_at, updated_at) "
            "VALUES (?, 'q0', 'q0', 'round', 'poll', ?, ?, 'closed', 'individual', 'individual', ?, ?, ?)",
            (
                sid,
                '{"text": "Q", "type": "mc", "options": [{"text": "A"}], "correct_answer": "A", "key": "A"}',
                pid,
                now,
                now,
                now,
            ),
        )
        c.execute(
            "INSERT INTO live_session_responses (prompt_id, student_id, response_json, created_at, updated_at) "
            "VALUES (?, ?, '{}', ?, ?)",
            (int(pid), int(self.students[0]), now, now),
        )
        c.commit()
        c.execute(
            "UPDATE live_class_sessions SET ended_at = ?, status = 'ended' WHERE id = ?",
            (ended_iso, sid),
        )
        c.commit()
        key = str(
            c.execute("SELECT run_key FROM live_class_sessions WHERE id = ?", (sid,)).fetchone()[0]
            or ""
        )
        self.school.cleanup_live_session_response_data(sid, live_run=live_run)
        LessonSlidesBackfillTests._ensure_real_student_answer(self, key, module, slot, now)
        c.execute("DELETE FROM lesson_slides_latest WHERE class_id = ?", (int(self.class_id),))
        c.commit()
        return key

    def test_m3c1_shows_portal_code_without_latest_row(self) -> None:
        """Catch-up link run fills the table when ``lesson_slides_latest`` is empty."""
        run_key = "42ca747b-e2f0-4c3b-9a1d-0e5f6a7b8c9d"
        self._ended_real_run_keep_session(
            "M3",
            "C1",
            ended_iso="2026-10-14T14:30:00",
            run_key=run_key,
        )
        self.school.conn.execute(
            """
            UPDATE catchup_links SET portal_join_code = ?
            WHERE class_id = ? AND UPPER(live_module) = 'M3' AND UPPER(live_slot) = 'C1'
            """,
            ("R3W1U", int(self.class_id)),
        )
        self.school.conn.commit()
        self.assertFalse(self.school.list_lesson_slides_latest(self.class_id))
        payload = build_lesson_slides_table(self.school, self.class_id)
        row = next(r for r in payload["rows"] if r["label"] == "M3C1")
        self.assertTrue(row["has_run"])
        self.assertEqual(row["join_code"], "R3W1U")
        self.assertEqual(row["run_key"], run_key)

    def test_backfill_twice_is_idempotent(self) -> None:
        """Repeated backfill leaves ``lesson_slides_latest`` unchanged."""
        run_key = self._ended_real_run_keep_session(
            "M2",
            "C1",
            ended_iso="2026-10-14T14:30:00",
        )
        first = backfill_lesson_slides_latest(self.school)
        self.assertGreaterEqual(first["inserted"] + first["updated"], 1)
        snapshot = self.school.list_lesson_slides_latest(self.class_id)
        self.assertEqual(len(snapshot), 1)
        self.assertEqual(snapshot[0]["run_key"], run_key)
        second = backfill_lesson_slides_latest(self.school)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["updated"], 0)
        self.assertGreaterEqual(second["skipped"], 1)
        again = self.school.list_lesson_slides_latest(self.class_id)
        self.assertEqual(again, snapshot)

    def test_backfill_ignores_test_runs(self) -> None:
        """A newer test run does not replace an older real run in the backfill."""
        real_key = self._ended_real_run_keep_session(
            "M4",
            "C1",
            ended_iso="2026-10-07T14:30:00",
        )
        test_key = self._ended_real_run_keep_session(
            "M4",
            "C1",
            ended_iso="2026-10-20T15:00:00",
            live_run=False,
        )
        self.assertFalse(self.school.participation_run_kind(self.class_id, test_key)["is_real"])
        backfill_lesson_slides_latest(self.school)
        latest = self.school.list_lesson_slides_latest(self.class_id)
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["run_key"], real_key)

    def test_backfill_ignores_discarded_runs(self) -> None:
        """Discarded runs are skipped even when they ended more recently."""
        older = self._ended_real_run_keep_session(
            "M5",
            "C1",
            ended_iso="2026-10-07T14:30:00",
        )
        newer = self._ended_real_run_keep_session(
            "M5",
            "C1",
            ended_iso="2026-10-16T14:30:00",
        )
        self.school.set_participation_run_real(self.class_id, newer, False, user_id=1)
        self.assertTrue(self.school.participation_run_discarded(self.class_id, newer))
        backfill_lesson_slides_latest(self.school)
        latest = self.school.list_lesson_slides_latest(self.class_id)
        self.assertEqual(latest[0]["run_key"], older)


    def test_backfill_after_session_deleted(self) -> None:
        """Backfill works when prod wiped the ended ``live_class_sessions`` row."""
        run_key = self._ended_real_run_keep_session(
            "M2",
            "C1",
            ended_iso="2026-10-14T14:30:00",
        )
        sid = self.school.conn.execute(
            "SELECT id FROM live_class_sessions WHERE class_id = ? AND run_key = ?",
            (int(self.class_id), run_key),
        ).fetchone()
        self.assertIsNotNone(sid)
        self.school.conn.execute("DELETE FROM live_class_sessions WHERE id = ?", (int(sid["id"]),))
        self.school.conn.commit()
        report = backfill_lesson_slides_latest(self.school)
        self.assertGreaterEqual(report["inserted"] + report["updated"], 1)
        latest = self.school.list_lesson_slides_latest(self.class_id)
        row = next(r for r in latest if r["live_module"] == "M2" and r["live_slot"] == "C1")
        self.assertEqual(row["run_key"], run_key)
        self.assertTrue(row["questions"])

    def test_class9_m3c1_prod_shape_backfill(self) -> None:
        """Prod-shaped M3C1 (run 42ca747b, portal R3W1U) backfills without a session row."""
        run_key = "42ca747b-e2f0-4c3b-9a1d-0e5f6a7b8c9d"
        self._ended_real_run_keep_session(
            "M3",
            "C1",
            ended_iso="2026-10-14T14:30:00",
            run_key=run_key,
        )
        self.school.conn.execute(
            """
            UPDATE catchup_links SET portal_join_code = ?
            WHERE class_id = ? AND UPPER(live_module) = 'M3' AND UPPER(live_slot) = 'C1'
            """,
            ("R3W1U", int(self.class_id)),
        )
        self.school.conn.execute(
            "DELETE FROM live_class_sessions WHERE class_id = ?", (int(self.class_id),)
        )
        self.school.conn.commit()
        backfill_lesson_slides_latest(self.school)
        latest = self.school.list_lesson_slides_latest(self.class_id)
        row = next(r for r in latest if r["live_module"] == "M3" and r["live_slot"] == "C1")
        self.assertEqual(row["run_key"], run_key)
        payload = build_lesson_slides_table(self.school, self.class_id)
        ui = next(r for r in payload["rows"] if r["label"] == "M3C1")
        self.assertEqual(ui["join_code"], "R3W1U")
        self.assertTrue(ui["has_run"])

    def test_fallback_ignores_test_run_catchup(self) -> None:
        """Catch-up fallback does not treat a test/discarded run as has_run."""
        real_key = self._ended_real_run_keep_session(
            "M1",
            "C2",
            ended_iso="2026-10-14T14:30:00",
        )
        test_key = self._ended_real_run_keep_session(
            "M1",
            "C2",
            ended_iso="2026-10-20T15:00:00",
            live_run=False,
        )
        self.school.conn.execute(
            """
            UPDATE catchup_links SET run_key = ?
            WHERE class_id = ? AND UPPER(live_module) = 'M1' AND UPPER(live_slot) = 'C2'
            """,
            (test_key, int(self.class_id)),
        )
        self.school.conn.execute("DELETE FROM lesson_slides_latest WHERE class_id = ?", (self.class_id,))
        self.school.conn.commit()
        payload = build_lesson_slides_table(self.school, self.class_id)
        row = next(r for r in payload["rows"] if r["label"] == "M1C2")
        self.assertFalse(row["has_run"])
        self.school.conn.execute(
            """
            UPDATE catchup_links SET run_key = ?
            WHERE class_id = ? AND UPPER(live_module) = 'M1' AND UPPER(live_slot) = 'C2'
            """,
            (real_key, int(self.class_id)),
        )
        self.school.conn.commit()
        payload2 = build_lesson_slides_table(self.school, self.class_id)
        row2 = next(r for r in payload2["rows"] if r["label"] == "M1C2")
        self.assertTrue(row2["has_run"])


def _pg_admin_url() -> str:
    return (os.environ.get("LIVE_PRESENCE_TEST_URL") or "").strip()


@unittest.skipUnless(_pg_admin_url(), "LIVE_PRESENCE_TEST_URL is not set")
class LessonSlidesBackfillPostgresPresenceTests(LessonSlidesBackfillTests):
    """Same backfill cases with Postgres live presence (school DB stays sqlite)."""

    def _presence_url(self) -> str:
        import uuid
        from urllib.parse import urlsplit, urlunsplit

        import psycopg

        admin = _pg_admin_url()
        self.pg_db = f"lloves_mck229_{uuid.uuid4().hex[:8]}"
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{self.pg_db}"')
        self.addCleanup(self._drop_pg, admin)
        parts = urlsplit(admin)
        return urlunsplit((parts.scheme, parts.netloc, f"/{self.pg_db}", parts.query, ""))

    def _drop_pg(self, admin: str) -> None:
        import psycopg

        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{self.pg_db}" WITH (FORCE)')

    def setUp(self) -> None:
        import logging
        import tempfile
        from pathlib import Path

        from app import create_app

        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
            live_database_url=self._presence_url(),
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.assertEqual(self.school.live_presence_status(), "postgres")
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
        )
        self.school.set_offering_schedule(
            int(offering["id"]),
            live_days="M/W/F",
            live_time="9:15am",
            live_end_time="10:30am",
        )
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCR3U",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Ava", "Ben"],
            offering_id=int(offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )
        self.class_id = int(created["id"] if isinstance(created, dict) else created)
        g = self.school.game.conn
        self.students = [
            int(r[0])
            for r in g.execute(
                "SELECT id FROM students WHERE class_id = ? ORDER BY id",
                (self.class_id,),
            ).fetchall()
        ]

    def tearDown(self) -> None:
        self.school.close()
        self.tmp.cleanup()

    def test_backfill_twice_is_idempotent(self) -> None:
        """Postgres presence: backfill remains idempotent."""
        super().test_backfill_twice_is_idempotent()



if __name__ == "__main__":
    unittest.main()

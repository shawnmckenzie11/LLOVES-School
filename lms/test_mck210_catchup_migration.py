#!/usr/bin/env python3
"""MCK-210 one-time catch-up repair startup migration tests."""

from __future__ import annotations

import inspect
import json
import logging
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from catchup_backfill import BackfillRunReport, RepairReport  # noqa: E402
import mck210_catchup_migration as migration  # noqa: E402
from mck210_catchup_migration import (  # noqa: E402
    MIGRATION_KEY,
    migration_marker,
    plan_mck210_migration,
    prod_catchup_migration_eligible,
    run_mck210_catchup_migration_if_needed,
    validate_mck210_repair_plan,
)


def _good_plan_report() -> RepairReport:
    """Synthetic dry-run plan that satisfies the exact ops table."""
    rows = [
        BackfillRunReport(9, "MCR3U", "prod-1b01-run", "M2", "C3", "would classify real"),
        BackfillRunReport(10, "MCR3U", "prod-9a20-run", "M2", "C3", "would classify real"),
        BackfillRunReport(10, "MCR3U", "prod-8312-run", "M2", "C3", "would classify test"),
        BackfillRunReport(9, "MCR3U", "prod-7e0c-run", "M2", "C4", "would classify real"),
        BackfillRunReport(10, "MCR3U", "prod-4373-run", "M2", "C4", "would classify real"),
        BackfillRunReport(9, "MCR3U", "prod-3b4b-run", "M2", "C3", "would classify test"),
        BackfillRunReport(9, "MCR3U", "prod-a5d4-run", "M2", "C1", "would classify test"),
        BackfillRunReport(8, "MCF3M", "prod-d20a-run", "M2", "C3", "would classify real"),
        BackfillRunReport(8, "MCF3M", "prod-0dae-run", "M2", "C3", "would classify test"),
        BackfillRunReport(8, "MCF3M", "prod-2af3-run", "M2", "C4", "would classify real"),
        BackfillRunReport(9, "MCR3U", "prod-1b01-run", "M2", "C3", "would reopen link", detail="link_id=5"),
        BackfillRunReport(10, "MCR3U", "prod-9a20-run", "M2", "C3", "would mint open link", detail="new token"),
        BackfillRunReport(10, "MCR3U", "prod-8312-run", "M2", "C3", "would discard link", detail="link_id=6"),
        BackfillRunReport(9, "MCR3U", "prod-7e0c-run", "M2", "C4", "would reopen link", detail="link_id=8"),
        BackfillRunReport(10, "MCR3U", "prod-4373-run", "M2", "C4", "would reopen link", detail="link_id=7"),
        BackfillRunReport(8, "MCF3M", "prod-d20a-run", "M2", "C3", "would mint open link", detail="new token"),
        BackfillRunReport(8, "MCF3M", "prod-0dae-run", "M2", "C3", "would discard link", detail="link_id=9"),
        BackfillRunReport(8, "MCF3M", "prod-2af3-run", "M2", "C4", "would reopen link", detail="link_id=10"),
    ]
    return RepairReport(dry_run=True, runs=rows)


class ValidatePlanTests(unittest.TestCase):
    def test_match_accepts_ops_plan(self) -> None:
        ok, reason = validate_mck210_repair_plan(_good_plan_report())
        self.assertTrue(ok, reason)

    def test_swap_1b01_and_3b4b_classify_aborts(self) -> None:
        report = _good_plan_report()
        for row in report.runs:
            if "1b01" in row.run_key and "classify" in row.action:
                row.action = "would classify test"
            if "3b4b" in row.run_key and "classify" in row.action:
                row.action = "would classify real"
        ok, reason = validate_mck210_repair_plan(report)
        self.assertFalse(ok)
        self.assertIn("1b01", reason)

    def test_8312_discard_link_7_aborts(self) -> None:
        report = _good_plan_report()
        for row in report.runs:
            if "8312" in row.run_key and "discard" in row.action:
                row.detail = "link_id=7"
        ok, reason = validate_mck210_repair_plan(report)
        self.assertFalse(ok)
        self.assertIn("8312", reason)

    def test_unexpected_mint_aborts(self) -> None:
        report = _good_plan_report()
        report.runs.append(
            BackfillRunReport(
                9,
                "MCR3U",
                "prod-a5d4-run",
                "M2",
                "C1",
                "would mint open link",
                detail="new token",
            )
        )
        ok, reason = validate_mck210_repair_plan(report)
        self.assertFalse(ok)
        self.assertIn("a5d4", reason)


class MigrationNoOpTests(unittest.TestCase):
    def setUp(self) -> None:
        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_not_eligible_on_fresh_db(self) -> None:
        self.assertFalse(prod_catchup_migration_eligible(self.school))
        result = run_mck210_catchup_migration_if_needed(self.school)
        self.assertEqual(result, {"status": "skipped_not_eligible"})
        self.assertIsNone(migration_marker(self.school.conn))


class ProdMigrationFixture(unittest.TestCase):
    """Prod-shaped sqlite: offerings 10–12, classes 8–10, ten snapshot runs."""

    OFFERING_IDS = (10, 11, 12)
    RUN_TARGETS = migration.RUN_TARGETS

    def setUp(self) -> None:
        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.class_ids: dict[str, int] = {8: 8, 9: 9, 10: 10}
        self._seed_offerings_and_classes()
        self._seed_students()
        self._seed_snapshot_runs()
        self.school.conn.execute("DELETE FROM live_class_sessions")
        self.school.conn.commit()

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)
        self.tmp.cleanup()

    def _seed_offerings_and_classes(self) -> None:
        sem = self.school.active_semester()
        assert sem is not None
        tid = int(self.teacher["id"])
        now = datetime.now(timezone.utc).isoformat()
        specs = [
            (10, "MCR3U", 1, "9:15am"),
            (11, "MCF3M", 1, "2:00pm"),
            (12, "MCR3U", 5, "9:15am"),
        ]
        for oid, code, section, time_label in specs:
            self.school.conn.execute(
                """
                INSERT INTO course_offerings (
                    id, semester_id, ontario_code, teacher_user_id, tenant_id,
                    live_access_code, created_at, section_index, live_days, live_time, live_end_time
                ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, 'M/W/F', ?, NULL)
                """,
                (oid, int(sem["id"]), code, tid, f"PIN{oid}", now, section, time_label),
            )
        self.school.conn.commit()
        class_specs = [
            (9, 10, "MCR3U", "9:15am"),
            (10, 12, "MCR3U", "9:15am"),
            (8, 11, "MCF3M", "2:00pm"),
        ]
        for cid, oid, code, time_label in class_specs:
            self.school.game.conn.execute(
                """
                INSERT INTO classes (id, year, semester, course_code, days, time, created_at, offering_id)
                VALUES (?, '2026/27', 'Semester 1', ?, 'M/W/F', ?, ?, ?)
                """,
                (cid, code, time_label, now, oid),
            )
        self.school.game.conn.execute(
            "UPDATE sqlite_sequence SET seq = MAX(seq, 10) WHERE name = 'classes'"
        )
        self.school.game.conn.commit()

    def _insert_snapshot(
        self,
        suffix: str,
        class_id: int,
        module: str,
        slot: str,
        ended_utc: str,
        *,
        link_id: int | None = None,
        link_closed: bool = False,
        link_token: str | None = None,
        wrong_run_key: str | None = None,
        is_test_rehearsal: bool = False,
    ) -> None:
        run_key = wrong_run_key or f"prod-{suffix}-run"
        stu = self.students[0]
        offering_id = int(
            self.school.game.conn.execute(
                "SELECT offering_id FROM classes WHERE id = ?", (class_id,)
            ).fetchone()[0]
        )
        sid = int(
            self.school.conn.execute(
                """
                INSERT INTO live_class_sessions (
                    class_id, offering_id, teacher_user_id, session_code, status,
                    started_at, ended_at, run_key, teacher_state_json
                ) VALUES (?, ?, ?, ?, 'ended', '2026-10-01T12:00:00', ?, ?, '{}')
                """,
                (class_id, offering_id, int(self.teacher["id"]), f"S{suffix}", ended_utc, run_key),
            ).lastrowid
        )
        self.school.conn.execute(
            """
            INSERT INTO live_result_snapshots (
                live_session_id, class_id, session_code, run_key,
                live_module, live_slot, source, question_key, question_json,
                student_id, participant_uuid, codename, team_id,
                answer_json, awarded_points, answered_at, snapshot_at
            ) VALUES (?, ?, 'P', ?, ?, ?, 'prompt', 'item:q', '{}', ?, NULL, '', NULL,
                      '{}', NULL, ?, ?)
            """,
            (sid, class_id, run_key, module, slot, stu, ended_utc, ended_utc),
        )
        if is_test_rehearsal:
            self.school.conn.execute(
                """
                INSERT OR IGNORE INTO participation_runs (
                    class_id, run_key, is_real, auto_is_real, discarded
                ) VALUES (?, ?, 0, 0, 0)
                """,
                (class_id, run_key),
            )
        if link_id is not None or link_token:
            token = link_token or f"tok-{suffix}"
            open_flag = 0 if link_closed else 1
            self.school.conn.execute(
                """
                INSERT INTO catchup_links (
                    id, token, class_id, run_key, live_module, live_slot, is_open,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(link_id) if link_id is not None else None,
                    token,
                    class_id,
                    run_key,
                    module,
                    slot,
                    open_flag,
                    ended_utc,
                    ended_utc,
                ),
            )
        self.school.conn.commit()

    def _seed_students(self) -> None:
        g = self.school.game.conn
        for cid in (8, 9, 10):
            g.execute(
                "INSERT INTO students (class_id, canvas_id, last_display, first_name, codename) "
                "VALUES (?, ?, 'S1', 'Stu', 'Stu')",
                (cid, f"canvas-{cid}"),
            )
        g.commit()
        self.students = [
            int(r[0])
            for r in g.execute(
                "SELECT id FROM students WHERE class_id = 9 ORDER BY id LIMIT 1"
            ).fetchall()
        ]

    def _seed_snapshot_runs(self) -> None:
        real_1030 = "2026-10-14T14:30:00"
        real_1155 = "2026-10-14T15:55:00"
        test_utc = "2026-10-20T22:00:00"
        real_mcf = "2026-10-14T19:15:00"
        c9, c10, c8 = 9, 10, 8
        self._insert_snapshot("1b01", c9, "M2", "C3", real_1030, link_id=5, link_closed=True)
        self._insert_snapshot("9a20", c10, "M2", "C3", real_1155)
        self._insert_snapshot(
            "8312", c10, "M2", "C3", test_utc, link_id=6, link_closed=True, link_token="tok-8312", is_test_rehearsal=True)
        self._insert_snapshot("7e0c", c9, "M2", "C4", real_1030, link_id=8, link_closed=True)
        self._insert_snapshot("4373", c10, "M2", "C4", real_1155, link_id=7, link_closed=True)
        self._insert_snapshot("3b4b", c9, "M2", "C3", test_utc, is_test_rehearsal=True)
        self._insert_snapshot("a5d4", c9, "M2", "C1", test_utc, is_test_rehearsal=True)
        self._insert_snapshot("7656", c9, "M2", "C1", test_utc, is_test_rehearsal=True)
        self._insert_snapshot("d20a", c8, "M2", "C3", real_mcf)
        self._insert_snapshot(
            "0dae", c8, "M2", "C3", test_utc, link_id=9, link_closed=True, link_token="tok-0dae", is_test_rehearsal=True)
        self._insert_snapshot("2af3", c8, "M2", "C4", real_mcf, link_id=10, link_closed=True)
        self.school.conn.execute(
            """
            INSERT INTO catchup_links (
                id, token, class_id, run_key, live_module, live_slot, is_open,
                created_at, updated_at
            ) VALUES (4, '02fa', 9, 'prod-02fa-run', 'M2', 'C2', 1, ?, ?)
            """,
            (real_1030, real_1030),
        )
        self.school.conn.execute(
            """
            INSERT INTO catchup_links (
                id, token, class_id, run_key, live_module, live_slot, is_open,
                created_at, updated_at
            ) VALUES (2, 'good-link-2', 9, 'prod-protected-2', 'M2', 'C5', 1, ?, ?)
            """,
            (real_1030, real_1030),
        )
        self.school.conn.commit()


class MigrationApplyTests(ProdMigrationFixture):
    def test_fixture_matches_prod_slots(self) -> None:
        """4373 is class 10; 7656 is an M2 C1 test; links 6, 7, 8, and 10 exist."""

        snaps = self.school.conn.execute(
            """
            SELECT class_id, live_module, live_slot, run_key
            FROM live_result_snapshots
            WHERE run_key IN ('prod-4373-run', 'prod-7656-run')
            """
        ).fetchall()
        by_key = {str(row[3]): row for row in snaps}
        row_4373 = by_key["prod-4373-run"]
        self.assertEqual(int(row_4373[0]), 10)
        self.assertEqual(str(row_4373[1]), "M2")
        self.assertEqual(str(row_4373[2]), "C4")
        row_7656 = by_key["prod-7656-run"]
        self.assertEqual(int(row_7656[0]), 9)
        self.assertEqual(str(row_7656[1]), "M2")
        self.assertEqual(str(row_7656[2]), "C1")
        kind = self.school.conn.execute(
            "SELECT is_real FROM participation_runs WHERE run_key = 'prod-7656-run'"
        ).fetchone()
        self.assertIsNotNone(kind)
        self.assertEqual(int(kind[0]), 0)
        link_ids = {
            int(row[0])
            for row in self.school.conn.execute("SELECT id FROM catchup_links").fetchall()
        }
        self.assertTrue({6, 7, 8, 10}.issubset(link_ids))
        protected = self.school.conn.execute(
            "SELECT run_key FROM catchup_links WHERE id = 2"
        ).fetchone()
        self.assertIsNotNone(protected)
        self.assertNotIn("7656", str(protected[0]))

    def test_eligible_and_plan_matches(self) -> None:
        """plan() previews only. Offering ends are filled by the real sync first."""

        self.assertTrue(prod_catchup_migration_eligible(self.school))
        plans = self.school.plan_sync_offering_live_schedules(
            offering_ids=list(migration.OFFERING_IDS)
        )
        self.assertTrue(plans)
        self.assertEqual(
            next(row["live_end_time"] for row in plans if int(row["offering_id"]) == 12),
            "11:55am",
        )
        self.school.apply_sync_offering_live_schedules(plans)
        before_links = [
            tuple(row)
            for row in self.school.conn.execute(
                "SELECT id, token, run_key, is_open FROM catchup_links ORDER BY id"
            ).fetchall()
        ]
        with self.school._lock:
            _, preview = plan_mck210_migration(self.school)
        after_links = [
            tuple(row)
            for row in self.school.conn.execute(
                "SELECT id, token, run_key, is_open FROM catchup_links ORDER BY id"
            ).fetchall()
        ]
        self.assertEqual(after_links, before_links)
        self.assertTrue(preview.dry_run)
        source = inspect.getsource(plan_mck210_migration)
        self.assertNotIn("apply=True", source)
        self.assertNotIn("apply_sync_offering_live_schedules", source)
        link5 = self.school.conn.execute(
            "SELECT is_open FROM catchup_links WHERE id = 5"
        ).fetchone()
        self.assertEqual(int(link5[0]), 0)

    def test_apply_log_omits_token_prefix(self) -> None:
        """The boot apply log must not print the first characters of a new token."""

        logging.disable(logging.NOTSET)
        try:
            with self.assertLogs("mck210_catchup_migration", level="WARNING") as logs:
                result = run_mck210_catchup_migration_if_needed(self.school)
        finally:
            logging.disable(logging.WARNING)
        self.assertEqual(result["status"], "applied")
        blob = "\n".join(logs.output)
        self.assertIn("minted", blob)
        self.assertNotIn("token=", blob)

    def test_apply_idempotent(self) -> None:
        first = run_mck210_catchup_migration_if_needed(self.school)
        self.assertEqual(first["status"], "applied")
        link5 = self.school.conn.execute(
            "SELECT is_open, token FROM catchup_links WHERE id = 5"
        ).fetchone()
        self.assertEqual(int(link5[0]), 1)
        second = run_mck210_catchup_migration_if_needed(self.school)
        self.assertEqual(second, {"status": "already_applied"})
        marker = migration_marker(self.school.conn)
        assert marker is not None
        self.assertEqual(marker["migration_key"], MIGRATION_KEY)
        detail = json.loads(marker["detail_json"])
        self.assertEqual([tuple(x) for x in detail["run_targets"]], list(migration.RUN_TARGETS))

    def test_abort_on_mismatch_leaves_no_marker(self) -> None:
        # MCK-233: rehearsal is teacher/stored kind, not snapshot time.
        self.school.conn.execute(
            """
            INSERT OR REPLACE INTO participation_runs (
                class_id, run_key, is_real, auto_is_real, discarded
            ) VALUES (9, 'prod-7e0c-run', 0, 0, 0)
            """
        )
        self.school.conn.commit()
        result = run_mck210_catchup_migration_if_needed(self.school)
        self.assertEqual(result["status"], "aborted")
        self.assertIsNone(migration_marker(self.school.conn))
        ends = self.school.conn.execute(
            "SELECT live_end_time FROM course_offerings WHERE id IN (10, 11, 12)"
        ).fetchall()
        self.assertTrue(all(row[0] in (None, "") for row in ends))


if __name__ == "__main__":
    logging.disable(logging.NOTSET)
    unittest.main()

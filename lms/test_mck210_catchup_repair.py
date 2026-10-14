#!/usr/bin/env python3
"""MCK-210: backfill/repair uses real-class rule and shared open catch-up links."""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from catchup_backfill import backfill_catchup_from_snapshots, repair_catchup  # noqa: E402
from cli import register_cli  # noqa: E402
from test_participation_mck198 import Base  # noqa: E402


class ProdShapeRepairTests(Base):
    """Two runs per slot (test + real) with a closed link on the test run."""

    def setUp(self) -> None:
        super().setUp()
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

    def _snap(
        self,
        *,
        module: str,
        slot: str,
        run_key: str,
        ended_at: str,
        student_id: int,
    ) -> None:
        stamp = ended_at
        sid = 8000 + len(run_key)
        self.school.conn.execute(
            """
            INSERT INTO live_result_snapshots (
                live_session_id, class_id, session_code, run_key,
                live_module, live_slot, source, question_key, question_json,
                student_id, participant_uuid, codename, team_id,
                answer_json, awarded_points, answered_at, snapshot_at
            ) VALUES (?, ?, 'PRD', ?, ?, ?, 'prompt', 'item:q1', ?, ?, NULL, '', NULL, ?, NULL, ?, ?)
            """,
            (
                sid,
                self.class_id,
                run_key,
                module,
                slot,
                json.dumps({"text": "Q", "type": "mc"}),
                student_id,
                json.dumps({"value": "A"}),
                stamp,
                stamp,
            ),
        )
        self.school.conn.commit()

    def _seed_m2_c3_pair(self) -> tuple[str, str]:
        """Mimic prod: test 8312 vs real 9a20 for M2 C3."""
        s0 = self.students[0]
        test_key = "prod-run-test-8312abcd"
        real_key = "prod-run-real-9a20efgh"
        self._snap(
            module="M2",
            slot="C3",
            run_key=test_key,
            ended_at="2026-10-20T15:00:00",
            student_id=s0,
        )
        self._snap(
            module="M2",
            slot="C3",
            run_key=real_key,
            ended_at="2026-10-14T10:30:00",
            student_id=s0,
        )
        token = "closedlink8312"
        now = "2026-10-21T12:00:00"
        self.school.conn.execute(
            """
            INSERT INTO catchup_links (
                token, class_id, run_key, live_module, live_slot, is_open, created_at, updated_at
            ) VALUES (?, ?, ?, 'M2', 'C3', 0, ?, ?)
            """,
            (token, self.class_id, test_key, now, now),
        )
        self.school.conn.commit()
        return test_key, real_key

    def test_repair_dry_run_prod_shape(self) -> None:
        test_key, real_key = self._seed_m2_c3_pair()
        report = repair_catchup(self.school, module="M2", slot="C3", apply=False)
        lines = report.summary_lines()
        text = "\n".join(lines)
        self.assertIn("8312", text)
        self.assertIn("9a20", text)
        self.assertIn("would classify test", text)
        self.assertIn("would classify real", text)
        self.assertIn("would open shared link", text)
        self.assertIn("repoint", text)
        self.assertIsNone(self.school.participation_run_kind(self.class_id, real_key))

    def test_repair_apply_fixes_link_and_classification(self) -> None:
        test_key, real_key = self._seed_m2_c3_pair()
        repair_catchup(self.school, module="M2", slot="C3", apply=True)
        links = self.school.list_catchup_links(self.class_id)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["run_key"], real_key)
        self.assertTrue(int(links[0]["is_open"]))
        self.assertTrue(self.school.participation_run_kind(self.class_id, real_key)["is_real"])
        self.assertFalse(self.school.participation_run_kind(self.class_id, test_key)["is_real"])
        client = self.app.test_client()
        resp = client.get(f"/catchup/{links[0]['token']}", follow_redirects=False)
        self.assertNotEqual(resp.status_code, 403)

    def test_backfill_two_runs_picks_real_not_test(self) -> None:
        s0 = self.students[0]
        test_key = "pair-test-0dae"
        real_key = "pair-real-d20a"
        self._snap(
            module="M2",
            slot="C4",
            run_key=test_key,
            ended_at="2026-10-21T16:00:00",
            student_id=s0,
        )
        self._snap(
            module="M2",
            slot="C4",
            run_key=real_key,
            ended_at="2026-10-16T10:30:00",
            student_id=s0,
        )
        backfill_catchup_from_snapshots(self.school, module="M2", slot="C4", apply=True)
        link = self.school.list_catchup_links(self.class_id)[0]
        self.assertEqual(link["run_key"], real_key)
        self.assertTrue(int(link["is_open"]))
        self.assertIsNone(
            self.school.conn.execute(
                "SELECT 1 FROM catchup_links WHERE class_id = ? AND run_key = ?",
                (self.class_id, test_key),
            ).fetchone()
        )


class ProdFixtureDryRunReport(unittest.TestCase):
    """Print planned repair actions for prod-like run-key prefixes (documentation)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_app()
        cls.school = cls.app.config["SCHOOL_DB"]
        cls.class_id = int(
            cls.school.game.conn.execute("SELECT id FROM classes LIMIT 1").fetchone()[0]
        )
        cls.school.conn.execute("DELETE FROM catchup_links")
        cls.school.conn.execute("DELETE FROM live_result_snapshots")
        cls.school.conn.execute("DELETE FROM participation_items")
        cls.school.conn.execute("DELETE FROM participation_answers")
        cls.school.conn.execute("DELETE FROM participation_runs")
        cls.school.conn.commit()
        offering_id = int(
            cls.school.game.conn.execute(
                "SELECT offering_id FROM classes WHERE id = ?", (cls.class_id,)
            ).fetchone()[0]
        )
        cls.school.set_offering_schedule(
            offering_id,
            live_days="M/W/F",
            live_time="9:15am",
            live_end_time="10:30am",
        )
        cls._seed_all()

    @classmethod
    def _insert(cls, run_suffix: str, module: str, slot: str, ended_at: str, closed: bool) -> str:
        run_key = f"mck210-{run_suffix}"
        stu_row = cls.school.game.conn.execute(
            "SELECT id FROM students WHERE class_id = ? LIMIT 1", (cls.class_id,)
        ).fetchone()
        sid = int(stu_row[0]) if stu_row else 1
        stamp = ended_at
        cls.school.conn.execute(
            """
            INSERT INTO live_result_snapshots (
                live_session_id, class_id, session_code, run_key,
                live_module, live_slot, source, question_key, question_json,
                student_id, participant_uuid, codename, team_id,
                answer_json, awarded_points, answered_at, snapshot_at
            ) VALUES (?, ?, 'FX', ?, ?, ?, 'prompt', 'item:x', '{}', 1, NULL, '', NULL, '{}', NULL, ?, ?)
            """,
            (9000 + len(run_suffix), cls.class_id, run_key, module, slot, stamp, stamp),
        )
        if closed:
            cls.school.conn.execute(
                """
                INSERT INTO catchup_links (
                    token, class_id, run_key, live_module, live_slot, is_open, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (f"tok-{run_suffix}", cls.class_id, run_key, module, slot, stamp, stamp),
            )
        cls.school.conn.commit()
        return run_key

    @classmethod
    def _seed_all(cls) -> None:
        # Real runs (Wed/Fri bell window) — no catch-up row yet (prod backfill gap).
        for suffix, mod, slot in (
            ("9a20", "M2", "C3"),
            ("d20a", "M2", "C4"),
            ("1b01", "M2", "C1"),
            ("7e0c", "M2", "C1"),
            ("4373", "M3", "C1"),
            ("2af3", "M3", "C1"),
        ):
            cls._insert(suffix, mod, slot, "2026-10-14T10:30:00", closed=False)
        # Test runs (wrong day/time) — some wrongly own closed links.
        for suffix, mod, slot, closed in (
            ("8312", "M2", "C3", True),
            ("0dae", "M2", "C4", True),
            ("3b4b", "M2", "C1", False),
            ("a5d4", "M2", "C1", False),
        ):
            cls._insert(suffix, mod, slot, "2026-10-20T15:00:00", closed=closed)

    def test_report_prefix_actions(self) -> None:
        prefixes = ("9a20", "8312", "d20a", "0dae", "1b01", "7e0c", "4373", "2af3", "3b4b", "a5d4")
        report = repair_catchup(self.school, apply=False)
        for prefix in prefixes:
            rows = [r for r in report.runs if prefix in r.run_key]
            self.assertTrue(rows, f"missing rows for {prefix}")
            actions = {r.action for r in rows}
            if prefix in ("8312", "0dae", "3b4b", "a5d4"):
                self.assertTrue(
                    any("test" in a for a in actions),
                    f"{prefix} expected test classification, got {actions}",
                )
            if prefix in ("9a20", "d20a", "1b01", "7e0c", "4373", "2af3"):
                self.assertTrue(
                    any("real" in a for a in actions),
                    f"{prefix} expected real classification, got {actions}",
                )


class RepairCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        register_cli(self.app)
        self.runner = self.app.test_cli_runner()

    def test_repair_dry_run_default(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = self.runner.invoke(args=["repair-catchup"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("DRY RUN", result.output)


if __name__ == "__main__":
    logging.disable(logging.NOTSET)
    unittest.main()

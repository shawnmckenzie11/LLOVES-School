#!/usr/bin/env python3
"""MCK-210: UTC timestamps, offering end times, run-key repair (no session rows)."""

from __future__ import annotations

import logging
import os
import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from catchup_backfill import repair_catchup  # noqa: E402
from catchup_schedule import CatchupScheduleError, schedule_defaults_for_offering  # noqa: E402
from cli import register_cli  # noqa: E402
from live_class_run import LiveClassSchedule, classify_run_end, parse_ended_at  # noqa: E402
from test_participation_mck198 import Base  # noqa: E402

PROD_RUN_SCOPES: dict[str, tuple[str, str, str]] = {
    "9a20": ("M2", "C3", "real"),
    "8312": ("M2", "C3", "test"),
    "1b01": ("M2", "C3", "real"),
    "7e0c": ("M2", "C4", "real"),
    "4373": ("M2", "C4", "real"),
    "3b4b": ("M2", "C3", "test"),
    "a5d4": ("M2", "C1", "test"),
    "d20a": ("M2", "C3", "real"),
    "0dae": ("M2", "C3", "test"),
    "2af3": ("M2", "C4", "real"),
}


class UtcParseTests(unittest.TestCase):
    def test_utc_bell_end_classifies_real(self) -> None:
        sched = LiveClassSchedule(days_key="M/W/F", end_time_label="10:30am")
        ended = parse_ended_at("2026-10-14T14:30:00")
        self.assertTrue(classify_run_end(ended, sched))

    def test_naive_local_would_be_wrong_without_utc(self) -> None:
        sched = LiveClassSchedule(days_key="M/W/F", end_time_label="10:30am")
        ended = parse_ended_at("2026-10-14T14:30:00")
        self.assertEqual(str(ended.tzinfo), "America/Toronto")
        self.assertEqual(ended.hour, 10)
        self.assertEqual(ended.minute, 30)


class SectionScheduleSeedTests(unittest.TestCase):
    """Prod offering 12 / MCR3U section 5 must map to 11:55am from seed (not course default)."""

    def test_section_five_seed_row_is_1155(self) -> None:
        row = schedule_defaults_for_offering("MCR3U", 5)
        assert row is not None
        self.assertEqual(row["live_end_time"], "11:55am")

    def test_section_two_seed_row_still_1155(self) -> None:
        row = schedule_defaults_for_offering("MCR3U", 2)
        assert row is not None
        self.assertEqual(row["live_end_time"], "11:55am")


class SectionScheduleTests(Base):
    def test_section_five_uses_offering_end_not_section_two(self) -> None:
        teacher = self.teacher
        off2 = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCR3U", new_section=True
        )
        off5 = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCR3U", new_section=True
        )
        self.school.conn.execute(
            "UPDATE course_offerings SET section_index = 2 WHERE id = ?",
            (int(off2["id"]),),
        )
        self.school.conn.execute(
            "UPDATE course_offerings SET section_index = 5 WHERE id = ?",
            (int(off5["id"]),),
        )
        self.school.conn.commit()
        self.school.set_offering_schedule(
            int(off2["id"]), live_days="M/W/F", live_time="9:15am", live_end_time="11:55am"
        )
        self.school.set_offering_schedule(
            int(off5["id"]), live_days="M/W/F", live_time="2:00pm", live_end_time="3:15pm"
        )
        created5 = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCR3U",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["S5A"],
            offering_id=int(off5["id"]),
            teacher_user_id=int(teacher["id"]),
        )
        class5 = int(created5["id"] if isinstance(created5, dict) else created5)
        sched = self.school.live_class_schedule_for_class(class5)
        assert sched is not None
        self.assertEqual(sched["live_end_time"], "3:15pm")
        self.assertTrue(
            self.school.preview_participation_run_real(class5, ended_at="2026-10-14T19:15:00")
        )


class ProdFixtureBase(Base):
    def setUp(self) -> None:
        super().setUp()
        self.class_ids: dict[str, int] = {}
        self.offering_ids: list[int] = []
        self.offering_id_class_10: int | None = None
        self.links: dict[str, int] = {}
        self._seed_classes()
        self._seed_snapshot_runs()
        self._wipe_live_sessions()

    def _make_offering_class(self, key: str, code: str, section: int, *, time_label: str) -> int:
        off = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]),
            ontario_code=code,
            new_section=section > 1,
        )
        oid = int(off["id"])
        if section > 1:
            self.school.conn.execute(
                "UPDATE course_offerings SET section_index = ? WHERE id = ?",
                (section, oid),
            )
        self.school.conn.execute(
            """
            UPDATE course_offerings
            SET live_days = 'M/W/F', live_time = ?, live_end_time = NULL
            WHERE id = ?
            """,
            (time_label, oid),
        )
        self.school.conn.commit()
        self.offering_ids.append(oid)
        if key == "class_10":
            self.offering_id_class_10 = oid
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code=code,
            days_preset="M/W/F",
            time_label=time_label,
            codenames=[f"{key}-{code}"],
            offering_id=oid,
            teacher_user_id=int(self.teacher["id"]),
        )
        cid = int(created["id"] if isinstance(created, dict) else created)
        self.class_ids[key] = cid
        return cid

    def _seed_classes(self) -> None:
        self._make_offering_class("class_9", "MCR3U", 1, time_label="9:15am")
        self._make_offering_class("class_10", "MCR3U", 5, time_label="9:15am")
        self._make_offering_class("mcf3m", "MCF3M", 1, time_label="2:00pm")

    def _sync_offering_ends(self) -> None:
        plans = self.school.plan_sync_offering_live_schedules(offering_ids=self.offering_ids)
        self.school.apply_sync_offering_live_schedules(plans)

    def _insert_snapshot(
        self,
        suffix: str,
        class_key: str,
        module: str,
        slot: str,
        ended_utc: str,
        *,
        with_answers: bool = True,
        link_id: int | None = None,
        link_closed: bool = False,
        link_token: str | None = None,
    ) -> str:
        cid = self.class_ids[class_key]
        run_key = f"prod-{suffix}-run"
        stu = self.students[0]
        offering_id = int(
            self.school.game.conn.execute(
                "SELECT offering_id FROM classes WHERE id = ?", (cid,)
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
                (
                    cid,
                    offering_id,
                    int(self.teacher["id"]),
                    f"S{suffix}",
                    ended_utc,
                    run_key,
                ),
            ).lastrowid
        )
        if with_answers:
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
                (sid, cid, run_key, module, slot, stu, ended_utc, ended_utc),
            )
        if link_id is not None or link_token:
            token = link_token or f"tok-{suffix}"
            open_flag = 0 if link_closed else 1
            if link_id is not None:
                self.school.conn.execute(
                    """
                    INSERT INTO catchup_links (
                        id, token, class_id, run_key, live_module, live_slot, is_open,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (int(link_id), token, cid, run_key, module, slot, open_flag, ended_utc, ended_utc),
                )
                self.links[suffix] = int(link_id)
            else:
                cur = self.school.conn.execute(
                    """
                    INSERT INTO catchup_links (
                        token, class_id, run_key, live_module, live_slot, is_open,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (token, cid, run_key, module, slot, open_flag, ended_utc, ended_utc),
                )
                self.links[suffix] = int(cur.lastrowid)
        self.school.conn.commit()
        return run_key

    def _seed_snapshot_runs(self) -> None:
        real_utc_mcr_1030 = "2026-10-14T14:30:00"
        real_utc_mcr_1155 = "2026-10-14T15:55:00"
        test_utc = "2026-10-20T22:00:00"
        real_utc_mcf = "2026-10-14T19:15:00"
        self._insert_snapshot(
            "1b01", "class_9", "M2", "C3", real_utc_mcr_1030, link_id=5, link_closed=True
        )
        self._insert_snapshot("9a20", "class_10", "M2", "C3", real_utc_mcr_1155)
        self._insert_snapshot("8312", "class_10", "M2", "C3", test_utc)
        self._insert_snapshot("7e0c", "class_9", "M2", "C4", real_utc_mcr_1030)
        self._insert_snapshot("4373", "class_9", "M2", "C4", real_utc_mcr_1030)
        self._insert_snapshot("3b4b", "class_9", "M2", "C3", test_utc)
        self._insert_snapshot("a5d4", "class_9", "M2", "C1", test_utc)
        self._insert_snapshot(
            "7656", "class_9", "M2", "C4", real_utc_mcr_1030, link_id=2, link_token="good-link-2"
        )
        self._insert_snapshot("d20a", "mcf3m", "M2", "C3", real_utc_mcf)
        self._insert_snapshot(
            "0dae", "mcf3m", "M2", "C3", test_utc, link_token="tok-0dae", link_closed=True
        )
        self._insert_snapshot("2af3", "mcf3m", "M2", "C4", real_utc_mcf)

    def _wipe_live_sessions(self) -> None:
        self.school.conn.execute("DELETE FROM live_class_sessions")
        self.school.conn.commit()


class OfferingEndTimeTests(ProdFixtureBase):
    def test_repair_blocked_until_offering_end_synced(self) -> None:
        cid = self.class_ids["class_9"]
        with self.assertRaises(CatchupScheduleError):
            repair_catchup(self.school, run_targets=[(cid, "1b01")], apply=False)

    def test_sync_offering_live_schedules_from_seed(self) -> None:
        plans = self.school.plan_sync_offering_live_schedules(offering_ids=self.offering_ids)
        self.assertGreaterEqual(len(plans), 3)
        self._sync_offering_ends()
        sched9 = self.school.live_class_schedule_for_class(
            self.class_ids["class_9"], offering_end_required=True
        )
        sched10 = self.school.live_class_schedule_for_class(
            self.class_ids["class_10"], offering_end_required=True
        )
        assert sched9 is not None and sched10 is not None
        self.assertEqual(sched9["live_end_time"], "10:30am")
        self.assertEqual(sched10["live_end_time"], "11:55am")

    def test_section_five_offering_plans_1155_for_class_10(self) -> None:
        assert self.offering_id_class_10 is not None
        plans = self.school.plan_sync_offering_live_schedules(
            offering_ids=[self.offering_id_class_10]
        )
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["live_end_time"], "11:55am")
        self.assertEqual(plans[0]["section_index"], 5)


class RunKeyRepairTests(ProdFixtureBase):
    def setUp(self) -> None:
        super().setUp()
        self._sync_offering_ends()

    def test_repair_1b01_reopens_link_5_on_class_9(self) -> None:
        cid = self.class_ids["class_9"]
        report = repair_catchup(self.school, run_targets=[(cid, "1b01")], apply=True)
        text = "\n".join(r.action for r in report.runs)
        self.assertIn("reopen", text)
        link = self.school.conn.execute(
            "SELECT is_open, run_key FROM catchup_links WHERE id = 5"
        ).fetchone()
        self.assertEqual(int(link[0]), 1)
        self.assertIn("1b01", str(link[1]))

    def test_repair_9a20_mints_on_class_10(self) -> None:
        cid = self.class_ids["class_10"]
        self.assertEqual(self.school.list_catchup_links(cid), [])
        report = repair_catchup(self.school, run_targets=[(cid, "9a20")], apply=True)
        self.assertIn("mint", "\n".join(r.action for r in report.runs))
        after = self.school.list_catchup_links(cid)
        self.assertEqual(len(after), 1)
        self.assertIn("9a20", after[0]["run_key"])

    def test_repair_8312_marks_test_on_class_10(self) -> None:
        cid = self.class_ids["class_10"]
        report = repair_catchup(self.school, run_targets=[(cid, "8312")], apply=True)
        actions = "\n".join(r.action for r in report.runs).lower()
        self.assertIn("test", actions)
        still_closed = int(
            self.school.conn.execute(
                "SELECT is_open FROM catchup_links WHERE id = 5"
            ).fetchone()[0]
        )
        self.assertEqual(still_closed, 0)

    def test_repair_3b4b_leaves_link_2_and_7656_alone(self) -> None:
        cid = self.class_ids["class_9"]
        before = self.school.get_catchup_link("good-link-2")
        repair_catchup(self.school, run_targets=[(cid, "3b4b")], apply=True)
        after = self.school.get_catchup_link("good-link-2")
        self.assertEqual(before["token"], after["token"])
        kind = self.school.participation_run_kind(cid, "prod-3b4b-run")
        assert kind is not None
        self.assertFalse(kind["is_real"])

    def test_prod_fixture_dry_run_all_scopes(self) -> None:
        targets: list[tuple[int, str]] = []
        for suffix in PROD_RUN_SCOPES:
            if suffix in ("9a20", "8312"):
                cid = self.class_ids["class_10"]
            elif suffix in ("1b01", "7e0c", "4373", "3b4b", "a5d4"):
                cid = self.class_ids["class_9"]
            else:
                cid = self.class_ids["mcf3m"]
            targets.append((cid, suffix))
        report = repair_catchup(self.school, run_targets=targets, apply=False)
        for suffix, (_mod, _slot, kind) in PROD_RUN_SCOPES.items():
            rows = [r for r in report.runs if suffix in r.run_key]
            self.assertTrue(rows, suffix)
            actions = " ".join(r.action for r in rows)
            if kind == "test":
                self.assertRegex(actions, r"test|skip")
            else:
                self.assertRegex(actions, r"real|mint|reopen|participation")


class ProdFixtureDryRunReport(unittest.TestCase):
    def test_empty_targets_is_ok(self) -> None:
        report = repair_catchup(_FakeSchool(), run_targets=[], apply=False)
        self.assertEqual(report.runs, [])


class _FakeSchool:
    def require_live_class_schedule_for_class(self, class_id: int) -> dict[str, str]:
        return {"live_days": "M/W/F", "live_end_time": "10:30am"}

    def get_catchup_repair_run_row(self, class_id: int, run_key_match: str) -> None:
        return None

    def resolve_snapshot_run_key(self, class_id: int, run_key_match: str) -> str:
        raise ValueError("no snapshots")


class RepairCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        register_cli(self.app)
        self.runner = self.app.test_cli_runner()

    def test_repair_requires_targets(self) -> None:
        result = self.runner.invoke(args=["repair-catchup"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--class-id", result.output)


if __name__ == "__main__":
    logging.disable(logging.NOTSET)
    unittest.main()

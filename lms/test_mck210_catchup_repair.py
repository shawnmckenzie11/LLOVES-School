#!/usr/bin/env python3
"""MCK-210: UTC timestamps, per-section schedules, run-scoped repair."""

from __future__ import annotations

import json
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
from catchup_backfill import backfill_catchup_from_snapshots, repair_catchup  # noqa: E402
from cli import register_cli  # noqa: E402
from live_class_run import LiveClassSchedule, classify_run_end, parse_ended_at  # noqa: E402
from test_participation_mck198 import Base  # noqa: E402

# Prod scopes (MCK-210 ops read-only); session ids are filled at deploy time via SQL.
PROD_RUN_SCOPES: dict[str, tuple[str, str, str, str]] = {
    "9a20": ("MCR3U", "M2", "C3", "real"),
    "8312": ("MCR3U", "M2", "C3", "test"),
    "1b01": ("MCR3U", "M2", "C3", "real"),
    "7e0c": ("MCR3U", "M2", "C4", "real"),
    "4373": ("MCR3U", "M2", "C4", "real"),
    "3b4b": ("MCR3U", "M2", "C4", "test"),
    "a5d4": ("MCR3U", "M2", "C4", "test"),
    "d20a": ("MCF3M", "M2", "C4", "real"),
    "0dae": ("MCF3M", "M2", "C4", "test"),
    "2af3": ("MCF3M", "M2", "C4", "real"),
}


class UtcParseTests(unittest.TestCase):
    """Zone-less prod timestamps are UTC before Toronto classification."""

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


class SectionScheduleTests(Base):
    """Section 5 afternoon end must not inherit section 2's 11:55am seed."""

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
        real_utc = "2026-10-14T19:15:00"
        self.assertTrue(
            self.school.preview_participation_run_real(class5, ended_at=real_utc)
        )


class ProdFixtureBase(Base):
    """Temp DB with multiple classes matching prod run-key scopes."""

    def setUp(self) -> None:
        super().setUp()
        self.classes: dict[str, int] = {}
        self.sessions: dict[str, int] = {}
        self.links: dict[str, int] = {}
        self._seed_classes()
        self._seed_runs()

    def _make_offering(self, code: str, section: int, end: str) -> int:
        off = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]),
            ontario_code=code,
            new_section=section > 1,
        )
        if section > 1:
            self.school.conn.execute(
                "UPDATE course_offerings SET section_index = ? WHERE id = ?",
                (section, int(off["id"])),
            )
            self.school.conn.commit()
        self.school.set_offering_schedule(
            int(off["id"]),
            live_days="M/W/F",
            live_time="9:15am" if code == "MCR3U" else "2:00pm",
            live_end_time=end,
        )
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code=code,
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=[f"{code}{section}"],
            offering_id=int(off["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )
        return int(created["id"] if isinstance(created, dict) else created)

    def _seed_classes(self) -> None:
        self.classes["mcr3u_c3"] = self._make_offering("MCR3U", 5, "10:30am")
        self.classes["mcr3u_c4"] = self._make_offering("MCR3U", 6, "10:30am")
        self.classes["mcf3m_c4"] = self._make_offering("MCF3M", 1, "3:15pm")

    def _insert_run(
        self,
        suffix: str,
        class_id: int,
        module: str,
        slot: str,
        ended_utc: str,
        *,
        with_answers: bool = True,
        link_closed: bool = False,
    ) -> int:
        run_key = f"prod-{suffix}-run"
        stu = self.students[0]
        offering_id = int(
            self.school.game.conn.execute(
                "SELECT offering_id FROM classes WHERE id = ?", (class_id,)
            ).fetchone()[0]
        )
        code = f"P{suffix}{len(self.sessions)}"
        sid = int(
            self.school.conn.execute(
                """
                INSERT INTO live_class_sessions (
                    class_id, offering_id, teacher_user_id, session_code, status,
                    started_at, ended_at, run_key, teacher_state_json
                ) VALUES (?, ?, ?, ?, 'ended', '2026-10-01T12:00:00', ?, ?, ?)
                """,
                (
                    class_id,
                    offering_id,
                    int(self.teacher["id"]),
                    code,
                    ended_utc,
                    run_key,
                    json.dumps({"live_module": module, "live_slot": slot}),
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
                (
                    sid,
                    class_id,
                    run_key,
                    module,
                    slot,
                    stu,
                    ended_utc,
                    ended_utc,
                ),
            )
        self.school.conn.commit()
        self.sessions[suffix] = sid
        if link_closed:
            lid = self.school.conn.execute(
                """
                INSERT INTO catchup_links (
                    token, class_id, run_key, live_module, live_slot, is_open,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    f"tok-{suffix}",
                    class_id,
                    run_key,
                    module,
                    slot,
                    ended_utc,
                    ended_utc,
                ),
            ).lastrowid
            self.school.conn.commit()
            self.links[suffix] = int(lid)
        return sid

    def _seed_runs(self) -> None:
        real_utc_mcr = "2026-10-14T14:30:00"
        test_utc = "2026-10-20T22:00:00"
        real_utc_mcf = "2026-10-14T19:15:00"
        c3 = self.classes["mcr3u_c3"]
        c4m = self.classes["mcr3u_c4"]
        c4f = self.classes["mcf3m_c4"]
        self._insert_run("9a20", c3, "M2", "C3", real_utc_mcr)
        self._insert_run("8312", c3, "M2", "C3", test_utc, link_closed=True)
        self._insert_run("1b01", c3, "M2", "C3", real_utc_mcr)
        self._insert_run("7e0c", c4m, "M2", "C4", real_utc_mcr)
        self._insert_run("4373", c4m, "M2", "C4", real_utc_mcr)
        self._insert_run("3b4b", c4m, "M2", "C4", test_utc)
        self._insert_run("a5d4", c4m, "M2", "C4", test_utc)
        self._insert_run("d20a", c4f, "M2", "C4", real_utc_mcf)
        self._insert_run("0dae", c4f, "M2", "C4", test_utc, link_closed=True)
        self._insert_run("2af3", c4f, "M2", "C4", real_utc_mcf)
        good = self._insert_run("7656", c4m, "M2", "C4", real_utc_mcr)
        self.school.conn.execute(
            """
            INSERT INTO catchup_links (
                token, class_id, run_key, live_module, live_slot, is_open,
                created_at, updated_at
            ) VALUES ('good-link-2', ?, ?, 'M2', 'C4', 1, ?, ?)
            """,
            (
                c4m,
                f"prod-7656-run",
                real_utc_mcr,
                real_utc_mcr,
            ),
        )
        self.school.conn.commit()
        self.links["7656"] = 2
        self.sessions["7656"] = good


class RunScopedRepairTests(ProdFixtureBase):
    def test_repair_real_discards_test_token_and_mints_new(self) -> None:
        old_token = self.school.get_catchup_link("tok-8312")["token"]
        report = repair_catchup(self.school, session_ids=[self.sessions["9a20"]], apply=True)
        text = "\n".join(r.action for r in report.runs)
        self.assertIn("discard", text)
        self.assertIn("mint", text)
        self.assertIsNone(self.school.get_catchup_link(old_token))
        links = self.school.list_catchup_links(self.classes["mcr3u_c3"])
        self.assertEqual(len(links), 1)
        self.assertIn("9a20", links[0]["run_key"])
        self.assertNotEqual(links[0]["token"], old_token)
        self.assertTrue(int(links[0]["is_open"]))

    def test_repair_test_does_not_touch_sibling_real_link(self) -> None:
        before = self.school.get_catchup_link("good-link-2")
        repair_catchup(self.school, session_ids=[self.sessions["3b4b"]], apply=True)
        after = self.school.get_catchup_link("good-link-2")
        self.assertEqual(before["token"], after["token"])
        self.assertEqual(before["run_key"], after["run_key"])
        self.assertFalse(self.school.participation_run_kind(
            self.classes["mcr3u_c4"], f"prod-3b4b-run"
        )["is_real"])

    def test_repair_test_writes_no_participation(self) -> None:
        repair_catchup(self.school, session_ids=[self.sessions["8312"]], apply=True)
        n = self.school.conn.execute(
            "SELECT COUNT(*) FROM participation_items WHERE run_key LIKE '%8312%'"
        ).fetchone()[0]
        self.assertEqual(int(n), 0)

    def test_prod_fixture_dry_run_prefixes(self) -> None:
        ids = [self.sessions[s] for s in PROD_RUN_SCOPES if s in self.sessions]
        report = repair_catchup(self.school, session_ids=ids, apply=False)
        for suffix, (_code, _mod, _slot, kind) in PROD_RUN_SCOPES.items():
            rows = [r for r in report.runs if suffix in r.run_key]
            self.assertTrue(rows, suffix)
            actions = " ".join(r.action for r in rows)
            if kind == "test":
                self.assertIn("test", actions)
            else:
                self.assertIn("real", actions)


class ProdFixtureDryRunReport(unittest.TestCase):
    """Does not touch the shared environment database."""

    def test_empty_session_list_is_ok(self) -> None:
        report = repair_catchup(_FakeSchool(), session_ids=[], apply=False)
        self.assertEqual(report.runs, [])


class _FakeSchool:
    """Minimal stub for empty repair scope tests."""

    def get_live_session_catchup_repair_row(self, session_id: int) -> None:
        return None


class RepairCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        register_cli(self.app)
        self.runner = self.app.test_cli_runner()

    def test_repair_requires_run_id(self) -> None:
        result = self.runner.invoke(args=["repair-catchup"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--run", result.output)


if __name__ == "__main__":
    logging.disable(logging.NOTSET)
    unittest.main()

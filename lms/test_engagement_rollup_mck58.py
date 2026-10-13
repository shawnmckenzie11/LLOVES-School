#!/usr/bin/env python3
"""MCK-58 P1.1: engagement rollup at End Live Class."""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from engagement_events import clean_payload  # noqa: E402
from engagement_rollup import compute_rollup, rollup_row_to_store  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self) -> None:
        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
        )
        self.class_id = int(
            self.school.game.create_class(
                year="2026/27",
                semester="Semester 1",
                course_code="MCR3U",
                days_preset="M/W/F",
                time_label="2:00pm",
                codenames=["Ava Smith", "Ben Jones", "Cara Lee"],
                offering_id=int(offering["id"]),
                teacher_user_id=int(self.teacher["id"]),
            )["id"]
        )
        rows = self.school.game.conn.execute(
            "SELECT id FROM students WHERE class_id = ? ORDER BY id", (self.class_id,)
        ).fetchall()
        self.students = [int(r[0]) for r in rows]
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        self.session_id = int(live["id"])
        self.run_key = str(live["run_key"])

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)
        self.tmp.cleanup()

    def _append(self, kind: str, *, student_id: int | None = None, item_ref: str = "", ts: str, payload: dict | None = None) -> None:
        self.school.engagement.append(
            {
                "class_id": self.class_id,
                "live_session": self.session_id,
                "run_key": self.run_key,
                "student_id": student_id,
                "first_name": "",
                "team_id": None,
                "item_ref": item_ref,
                "kind": kind,
                "ts": ts,
                "payload": clean_payload(payload),
            }
        )

    def _rollup(self, run_key: str | None = None) -> list[dict]:
        return self.school.list_engagement_rollup(self.class_id, run_key=run_key or self.run_key)

    def _student_row(self, rows: list[dict], student_id: int, signal: str) -> dict:
        for row in rows:
            if row["scope"] == "student" and row["student_id"] == student_id and row["signal"] == signal:
                return row
        self.fail(f"missing student {student_id} signal {signal}")


class ComputeTests(unittest.TestCase):
    def test_na_offered_when_item_type_never_offered(self) -> None:
        events = [
            {
                "id": 1,
                "kind": "present.join",
                "ts": "2026-10-01T09:59:00",
                "student_id": 10,
                "item_ref": "",
                "payload": {},
            },
            {
                "id": 2,
                "kind": "item.offered",
                "ts": "2026-10-01T10:00:00",
                "student_id": None,
                "item_ref": "1",
                "payload": {"mode": "individual"},
            },
            {
                "id": 3,
                "kind": "answer.submit",
                "ts": "2026-10-01T10:01:00",
                "student_id": 10,
                "item_ref": "1",
                "payload": {},
            },
        ]
        rows = compute_rollup(
            events,
            class_id=1,
            live_session=1,
            run_key="rk",
            team_for_student={10: 1},
            student_ids=[10],
        )
        vote = next(r for r in rows if r.scope == "student" and r.signal == "group_vote")
        self.assertIsNone(vote.offered)
        ans = next(r for r in rows if r.scope == "student" and r.signal == "individual_answer")
        self.assertEqual(ans.offered, 1)
        self.assertEqual(ans.count, 1)

    def test_offered_only_while_present(self) -> None:
        ava, ben = 1, 2
        events = [
            {"id": 1, "kind": "present.join", "ts": "2026-10-01T10:00:00", "student_id": ava, "item_ref": "", "payload": {}},
            {"id": 2, "kind": "item.offered", "ts": "2026-10-01T10:05:00", "student_id": None, "item_ref": "q1", "payload": {"mode": "individual"}},
            {"id": 3, "kind": "present.join", "ts": "2026-10-01T10:10:00", "student_id": ben, "item_ref": "", "payload": {}},
            {"id": 4, "kind": "item.offered", "ts": "2026-10-01T10:15:00", "student_id": None, "item_ref": "q2", "payload": {"mode": "individual"}},
        ]
        rows = compute_rollup(
            events,
            class_id=1,
            live_session=1,
            run_key="rk",
            team_for_student={ava: 1, ben: 1},
            student_ids=[ava, ben],
        )
        ava_row = next(r for r in rows if r.scope == "student" and r.student_id == ava and r.signal == "individual_answer")
        ben_row = next(r for r in rows if r.scope == "student" and r.student_id == ben and r.signal == "individual_answer")
        self.assertEqual(ava_row.offered, 2)
        self.assertEqual(ben_row.offered, 1)

    def test_share_of_group_and_even_split(self) -> None:
        events = [
            {"id": 1, "kind": "present.join", "ts": "2026-10-01T10:00:00", "student_id": 1, "item_ref": "", "payload": {}},
            {"id": 2, "kind": "present.join", "ts": "2026-10-01T10:00:01", "student_id": 2, "item_ref": "", "payload": {}},
            {"id": 3, "kind": "item.offered", "ts": "2026-10-01T10:01:00", "student_id": None, "item_ref": "g1", "payload": {"mode": "group"}},
            {"id": 4, "kind": "group.vote", "ts": "2026-10-01T10:02:00", "student_id": 1, "item_ref": "g1", "payload": {}},
            {"id": 5, "kind": "group.vote", "ts": "2026-10-01T10:02:01", "student_id": 1, "item_ref": "g2", "payload": {}},
            {"id": 6, "kind": "group.vote", "ts": "2026-10-01T10:02:02", "student_id": 2, "item_ref": "g1", "payload": {}},
        ]
        rows = compute_rollup(
            events,
            class_id=1,
            live_session=1,
            run_key="rk",
            team_for_student={1: 9, 2: 9},
            student_ids=[1, 2],
        )
        heavy = next(r for r in rows if r.scope == "student" and r.student_id == 1 and r.signal == "group_vote")
        light = next(r for r in rows if r.scope == "student" and r.student_id == 2 and r.signal == "group_vote")
        self.assertEqual(heavy.count, 2)
        self.assertEqual(light.count, 1)
        self.assertAlmostEqual(heavy.share_of_group, 2 / 3)
        self.assertAlmostEqual(light.share_of_group, 1 / 3)
        self.assertAlmostEqual(heavy.share_vs_even, 4 / 3)
        self.assertAlmostEqual(light.share_vs_even, 2 / 3)


class EndRollupTests(Base):
    def test_cleanup_writes_rollup_rows(self) -> None:
        ava, ben = self.students[0], self.students[1]
        self._append("present.join", student_id=ava, ts="2026-10-01T10:00:00")
        self._append("present.join", student_id=ben, ts="2026-10-01T10:00:01")
        self._append("item.offered", ts="2026-10-01T10:01:00", item_ref="1", payload={"mode": "individual"})
        self._append("answer.submit", student_id=ava, item_ref="1", ts="2026-10-01T10:02:00")
        self.school.cleanup_live_session_response_data(self.session_id)
        rows = self._rollup()
        self.assertGreater(len(rows), 0)
        ava = self._student_row(rows, ava, "individual_answer")
        self.assertEqual(ava["count"], 1)
        self.assertEqual(ava["offered"], 1)

    def test_rollup_idempotent_on_second_cleanup(self) -> None:
        self._append("answer.submit", student_id=self.students[0], item_ref="1", ts="2026-10-01T10:00:00")
        self.school.cleanup_live_session_response_data(self.session_id)
        first = len(self._rollup())
        self.school.cleanup_live_session_response_data(self.session_id)
        self.assertEqual(len(self._rollup()), first)

    def test_concurrent_rollup_inserts_one_cell_set(self) -> None:
        """Two Ends at once must not double rows (Ops: 36 vs 18)."""
        self._append("answer.submit", student_id=self.students[0], item_ref="1", ts="2026-10-01T10:00:00")
        events = self.school.list_engagement_events(self.class_id, run_key=self.run_key)
        team_for = {
            sid: self.school.student_team_id_for_class(self.class_id, sid) for sid in self.students
        }
        computed = compute_rollup(
            events,
            class_id=self.class_id,
            live_session=self.session_id,
            run_key=self.run_key,
            team_for_student=team_for,
            student_ids=self.students,
        )
        payload = [
            rollup_row_to_store(
                row,
                class_id=self.class_id,
                live_session=self.session_id,
                run_key=self.run_key,
                rolled_at="2026-10-01T10:00:00",
            )
            for row in computed
        ]
        expected = len(payload)
        barrier = threading.Barrier(2)
        inserted: list[int] = []
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                barrier.wait()
                inserted.append(self.school.engagement.insert_rollup_batch(payload))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(sum(inserted), expected)
        self.assertEqual(len(self._rollup()), expected)
        self.assertEqual(self.school.engagement.insert_rollup_batch(payload), 0)

    def test_concurrent_cleanup_rollup_once(self) -> None:
        """Parallel End cleanup must leave one rollup row per cell."""
        self._append("answer.submit", student_id=self.students[0], item_ref="1", ts="2026-10-01T10:00:00")
        expected = len(self.students) * 9
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                barrier.wait()
                self.school.cleanup_live_session_response_data(self.session_id)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(self._rollup()), expected)

    def test_rollup_survives_wipe(self) -> None:
        self._append("group.vote", student_id=self.students[0], item_ref="9", ts="2026-10-01T10:00:00")
        self.school.cleanup_live_session_response_data(self.session_id)
        before = len(self._rollup())
        self.assertGreater(before, 0)
        self.school.wipe_live_sessions_for_class(self.class_id)
        self.assertEqual(len(self._rollup()), before)

    def test_group_scope_row_after_end(self) -> None:
        self.school.game.begin_game(self.class_id, today=date(2026, 10, 1))
        for sid in self.students[:2]:
            self.school.game.mark_student_present_on_open_session(self.class_id, sid)
        self.school.game.assign_teams(self.class_id, 2, "random")
        self._append("item.offered", ts="2026-10-01T10:00:00", item_ref="g", payload={"mode": "group"})
        self._append("group.vote", student_id=self.students[0], item_ref="g", ts="2026-10-01T10:01:00")
        self.school.cleanup_live_session_response_data(self.session_id)
        group_rows = [r for r in self._rollup() if r["scope"] == "group" and r["signal"] == "group_vote"]
        self.assertEqual(len(group_rows), 2)
        self.assertEqual(sum(int(r["count"]) for r in group_rows), 1)
        self.assertTrue(all(int(r["offered"]) == 1 for r in group_rows))


if __name__ == "__main__":
    unittest.main()

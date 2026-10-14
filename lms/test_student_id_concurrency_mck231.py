#!/usr/bin/env python3
"""MCK-231 (MCK-229 gate MED1/MED2): atomic student id allocation across
processes, and at most one Test Student per class."""

from __future__ import annotations

import multiprocessing as mp
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)

from app import create_app  # noqa: E402

PROCS = 4
PER_PROC = 25


def _worker(game_cls, db_path: str, store: str, class_id: int, tag: int, out) -> None:
    """One gunicorn-worker-like process: own connection, PER_PROC inserts."""
    game = game_cls(Path(db_path), Path(store), lock=threading.RLock())
    game.conn.execute("PRAGMA busy_timeout=10000")
    ids, errors = [], []
    for n in range(PER_PROC):
        try:
            with game._lock:
                sid = game._insert_student_row(
                    class_id,
                    canvas_id=f"codename:p{tag}n{n}",
                    last_display="",
                    first_name=f"P{tag}N{n}",
                    codename=f"P{tag}N{n}",
                )
                game.conn.commit()
            ids.append(sid)
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))
    out.put((ids, errors))


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app = create_app(db_path=self.root / "lloves.sqlite", data_dir=self.root, testing=True, live_database_url="")
        self.school = self.app.config["SCHOOL_DB"]
        self.class_id = int(
            self.school.game.create_class(
                year="2026/27", semester="Semester 1", course_code="MCF3M",
                days_preset="M/W/F", time_label="2:00pm", codenames=["Maple"],
            )["id"]
        )

    def tearDown(self) -> None:
        self.school.close()
        self.tmp.cleanup()


class AtomicAllocatorTests(_Base):
    def test_parallel_processes_get_unique_ids_and_no_errors(self) -> None:
        ctx = mp.get_context("fork")
        out = ctx.Queue()
        game = self.school.game
        procs = [
            ctx.Process(target=_worker, args=(type(game), str(game.db_path), str(self.root), self.class_id, i, out))
            for i in range(PROCS)
        ]
        for p in procs:
            p.start()
        results = [out.get(timeout=120) for _ in procs]
        for p in procs:
            p.join(timeout=60)
        ids = [i for r, _ in results for i in r]
        errors = [e for _, e in results for e in e]
        self.assertEqual(errors, [])
        self.assertEqual(len(ids), PROCS * PER_PROC)
        self.assertEqual(len(set(ids)), len(ids))
        nxt = game.conn.execute("SELECT next_id FROM student_id_seq WHERE id = 1").fetchone()[0]
        top = game.conn.execute("SELECT MAX(id) FROM students").fetchone()[0]
        self.assertEqual(int(nxt), int(top) + 1)

    def test_parallel_threads_on_shared_connection(self) -> None:
        game = self.school.game
        ids, errors = [], []

        def go(n: int) -> None:
            try:
                with game._lock:
                    ids.append(game._insert_student_row(
                        self.class_id, canvas_id=f"codename:t{n}", last_display="",
                        first_name=f"T{n}", codename=f"T{n}"))
                    game.conn.commit()
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=go, args=(n,)) for n in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(set(ids)), 40)


class OneTestStudentTests(_Base):
    def _count(self) -> int:
        return int(self.school.game.conn.execute(
            "SELECT COUNT(*) FROM students WHERE class_id = ? AND is_test_student = 1",
            (self.class_id,)).fetchone()[0])

    def test_concurrent_ensure_makes_one(self) -> None:
        results, errors = [], []

        def go() -> None:
            try:
                results.append(self.school.ensure_class_test_student(self.class_id)["student_id"])
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=go) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(self._count(), 1)
        self.assertEqual(len(set(results)), 1)

    def test_database_refuses_a_second_test_student(self) -> None:
        self.school.ensure_class_test_student(self.class_id)
        with self.assertRaises(sqlite3.IntegrityError):
            self.school.game._insert_student_row(
                self.class_id, canvas_id="test-student:dup", last_display="",
                first_name="Test Student", codename="Test Student 2", is_test_student=True)
        self.school.game.conn.rollback() if self.school.game.conn.in_transaction else None
        self.assertEqual(self._count(), 1)


if __name__ == "__main__":
    unittest.main()

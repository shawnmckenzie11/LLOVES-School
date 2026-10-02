#!/usr/bin/env python3
"""MCK-109: the news tape's WAL switch and the WAL retry budget.

``live_news_wire.sqlite`` switched to WAL on first open with no lock and no
retry. On a fresh data dir the workers race that switch the way they raced
``lloves.sqlite`` before #192. Separately, each WAL retry could wait out the
30 s ``busy_timeout``, so the worst case was far past the 120 s gunicorn
worker timeout.
"""

from __future__ import annotations

import inspect
import multiprocessing
import os
import sqlite3
import sys
import tempfile
import traceback
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)

import live_news_wire  # noqa: E402
import school_db  # noqa: E402

WORKERS = 4
FRESH_ROUNDS = 24
#: gunicorn ``--timeout`` in production.
WORKER_TIMEOUT_S = 120


def _tape_worker(path: str, start, results) -> None:
    """Child process: wait on the barrier, then open the tape like a worker."""
    try:
        start.wait(timeout=60)
        log = live_news_wire.LiveNewsLog(path)
        log.append(1, [{"type": "state_seq", "state_seq": 1}])
        log.close()
        results.append("ok")
    except BaseException:  # noqa: BLE001 - report every open failure
        results.append(traceback.format_exc())


class _FakeClock:
    """Stands in for ``time`` in ``school_db``: sleeps only move the clock."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        """Fake monotonic seconds."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Advance the clock instead of sleeping."""
        self.now += float(seconds)


class _AlwaysBusy:
    """Connection whose WAL switch waits out ``busy_timeout`` and is BUSY."""

    def __init__(self, clock: _FakeClock, timeout_ms: int) -> None:
        self.clock = clock
        self.timeout_ms = timeout_ms
        self.attempts = 0
        self._row: tuple = ()

    def execute(self, sql: str):
        """Track ``busy_timeout``; every WAL pragma burns it, then fails."""
        text = sql.replace(" ", "").lower()
        if text.startswith("pragmabusy_timeout="):
            self.timeout_ms = int(text.split("=", 1)[1])
            self._row = (self.timeout_ms,)
        elif text == "pragmabusy_timeout":
            self._row = (self.timeout_ms,)
        elif "journal_mode" in text:
            self.attempts += 1
            self.clock.sleep(self.timeout_ms / 1000)
            raise sqlite3.OperationalError("database is locked")
        return self

    def fetchone(self):
        """Last pragma reply."""
        return self._row


class WalBudgetTests(unittest.TestCase):
    """The WAL switch can't hold a worker past its timeout."""

    def setUp(self) -> None:
        """Swap in the fake clock."""
        self.clock = _FakeClock()
        self._time = school_db.time
        school_db.time = self.clock

    def tearDown(self) -> None:
        """Put the real ``time`` back."""
        school_db.time = self._time

    def test_always_busy_switch_stops_well_under_the_worker_timeout(self) -> None:
        """Every attempt waits its full busy_timeout; the total stays capped."""
        conn = _AlwaysBusy(self.clock, school_db.SQLITE_BUSY_TIMEOUT_MS)
        with self.assertRaises(sqlite3.OperationalError):
            school_db.enable_sqlite_wal(conn)
        self.assertGreater(conn.attempts, 1, "BUSY was not retried")
        budget = getattr(school_db, "SQLITE_WAL_BUDGET_SECONDS", 30.0)
        self.assertLessEqual(self.clock.now, budget)
        self.assertLess(self.clock.now, WORKER_TIMEOUT_S / 2)
        # The connection gets its normal wait back for every later query.
        self.assertEqual(conn.timeout_ms, school_db.SQLITE_BUSY_TIMEOUT_MS)

    def test_real_connection_keeps_its_busy_timeout(self) -> None:
        """After configure, lloves.sqlite is WAL with the 30 s wait."""
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(str(Path(tmp) / "a.sqlite"))
            try:
                school_db.configure_sqlite_connection(conn)
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                wait = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            finally:
                conn.close()
        self.assertEqual(str(mode).lower(), "wal")
        self.assertEqual(int(wait), school_db.SQLITE_BUSY_TIMEOUT_MS)


class NewsTapeFreshOpenTests(unittest.TestCase):
    """``live_news_wire.sqlite`` on a brand-new data dir."""

    def test_connect_switches_wal_under_the_boot_lock_with_retry(self) -> None:
        """Same pattern as lloves.sqlite: lock, then the retrying switch."""
        source = inspect.getsource(live_news_wire._connect)
        self.assertIn("with boot_schema_lock(path):", source)
        locked = source.split("with boot_schema_lock(path):", 1)[1]
        self.assertIn("enable_sqlite_wal(conn", locked)
        self.assertLess(
            locked.index("enable_sqlite_wal(conn"),
            locked.index("CREATE TABLE IF NOT EXISTS live_news_events"),
        )
        self.assertNotIn("journal_mode", source)

    def test_parallel_fresh_opens_never_raise(self) -> None:
        """Four workers open a tape that does not exist yet; none dies."""
        ctx = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as tmp:
            failures: list[str] = []
            with ctx.Manager() as manager:
                for round_no in range(FRESH_ROUNDS):
                    path = live_news_wire.news_db_path(Path(tmp) / f"fresh{round_no}")
                    self.assertFalse(path.exists())
                    start = manager.Barrier(WORKERS)
                    results = manager.list()
                    procs = [
                        ctx.Process(target=_tape_worker, args=(str(path), start, results))
                        for _ in range(WORKERS)
                    ]
                    for proc in procs:
                        proc.start()
                    for proc in procs:
                        proc.join(timeout=120)
                    got = list(results)
                    self.assertEqual(len(got), WORKERS, got)
                    failures.extend(
                        f"round {round_no}: {item}" for item in got if item != "ok"
                    )
                    conn = sqlite3.connect(str(path))
                    try:
                        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                        rows = conn.execute(
                            "SELECT COUNT(*) FROM live_news_events"
                        ).fetchone()[0]
                    finally:
                        conn.close()
                    self.assertEqual(str(mode).lower(), "wal")
                    if not [f for f in failures if f.startswith(f"round {round_no}:")]:
                        self.assertEqual(int(rows), WORKERS)
            self.assertEqual(failures, [], "\n".join(failures))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""MCK-104 / MCK-76: startup schema migrations race across gunicorn workers.

Production runs four gunicorn workers without ``--preload``. Each worker
builds ``SchoolDB`` on the same sqlite file, and every additive migration is
``PRAGMA table_info`` then ``ALTER TABLE ... ADD COLUMN``. On a DB missing a
column (fresh volume, or a deploy that adds one) two workers can both see it
missing and the second ``ALTER`` dies with ``duplicate column name`` (Sentry
LLOVES-LMS-3 ``run_key``; Ops Mac wave ``team_names_approved``).

A brand-new file has a second race: every worker switched the file to WAL
before taking ``boot_schema_lock``, and SQLite can answer that with BUSY at
once (``database is locked`` at ``PRAGMA journal_mode = WAL``; Ops wave
9e22188, 3 of 18 fresh boots).
"""

from __future__ import annotations

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
os.environ.pop("GOOGLE_CLIENT_SECRET", None)

from school_db import SchoolDB  # noqa: E402

WORKERS = 4
#: Fresh-file rounds. Main crashed about 1 round in 5 on the box, so 24 clean
#: rounds would happen by chance well under 1% of the time.
FRESH_ROUNDS = 24
#: (table, column) pairs dropped before each cold boot. Both raced in prod.
MISSING = (("games", "team_names_approved"), ("live_class_sessions", "run_key"))


def _boot_worker(db_path: str, start, results) -> None:
    """Child process: wait on the barrier, then boot SchoolDB like a worker.

    Args:
        db_path: Shared sqlite file.
        start: ``multiprocessing.Barrier`` so all workers boot together.
        results: Manager list that collects ``"ok"`` or a traceback.
    """
    try:
        start.wait(timeout=60)
        db = SchoolDB(Path(db_path), Path(db_path).parent, live_database_url="")
        db.close()
        results.append("ok")
    except BaseException:  # noqa: BLE001 — report every boot failure
        results.append(traceback.format_exc())


def _load_game_show_db():
    """Import Math Game Show ``db.py`` the way ``SchoolDB`` does."""
    import importlib.util

    from paths import MGS_DIR

    spec = importlib.util.spec_from_file_location("mgs_db", MGS_DIR / "db.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("mgs_db", mod)
    spec.loader.exec_module(mod)
    return mod


def _game_worker(db_path: str, start, results, locked: bool) -> None:
    """Child process: open only ``GameShowDB`` (its ``_migrate`` ALTERs).

    Args:
        db_path: Shared sqlite file.
        start: Barrier so all workers migrate together.
        results: Manager list that collects ``"ok"`` or a traceback.
        locked: Take ``boot_schema_lock`` like ``SchoolDB.__init__`` does.
    """
    from contextlib import nullcontext

    from school_db import boot_schema_lock

    try:
        mod = _load_game_show_db()
        start.wait(timeout=60)
        path = Path(db_path)
        with boot_schema_lock(path) if locked else nullcontext():
            game = mod.GameShowDB(path, path.parent)
        game.conn.close()
        results.append("ok")
    except BaseException:  # noqa: BLE001 — report every boot failure
        results.append(traceback.format_exc())


def _columns(db_path: Path, table: str) -> set[str]:
    """Column names of ``table`` read on a fresh connection.

    Args:
        db_path: sqlite file.
        table: Table name.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _drop_columns(db_path: Path) -> None:
    """Drop the raced columns (and any index on them) to fake an old DB.

    Args:
        db_path: sqlite file that already has the full schema.
    """
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    try:
        for table, column in MISSING:
            for name, sql in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = ?",
                (table,),
            ).fetchall():
                if sql and column in sql:
                    conn.execute(f'DROP INDEX "{name}"')
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    finally:
        conn.close()


@unittest.skipIf(sqlite3.sqlite_version_info < (3, 35, 0), "needs ALTER TABLE DROP COLUMN")
class BootSchemaRaceTests(unittest.TestCase):
    """N worker processes cold-boot one DB that is missing columns."""

    def test_parallel_cold_boot_adds_missing_columns_once(self) -> None:
        """Four workers boot together; none dies on a duplicate column."""
        ctx = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "lloves.sqlite"
            SchoolDB(db_path, Path(tmp), live_database_url="").close()
            failures: list[str] = []
            with ctx.Manager() as manager:
                for _round in range(3):
                    _drop_columns(db_path)
                    for table, column in MISSING:
                        self.assertNotIn(column, _columns(db_path, table))
                    start = manager.Barrier(WORKERS)
                    results = manager.list()
                    procs = [
                        ctx.Process(target=_boot_worker, args=(str(db_path), start, results))
                        for _ in range(WORKERS)
                    ]
                    for proc in procs:
                        proc.start()
                    for proc in procs:
                        proc.join(timeout=120)
                    got = list(results)
                    self.assertEqual(len(got), WORKERS, got)
                    failures.extend(item for item in got if item != "ok")
                    for table, column in MISSING:
                        self.assertIn(column, _columns(db_path, table))
            self.assertEqual(failures, [], "\n".join(failures))

    def test_parallel_cold_boot_on_a_brand_new_file(self) -> None:
        """Four workers boot a file that does not exist yet; none dies.

        This is the WAL race: the file is not in WAL mode, so every worker
        switches it. Each round uses a new file.
        """
        ctx = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as tmp:
            failures: list[str] = []
            with ctx.Manager() as manager:
                for round_no in range(FRESH_ROUNDS):
                    folder = Path(tmp) / f"fresh{round_no}"
                    folder.mkdir()
                    db_path = folder / "lloves.sqlite"
                    self.assertFalse(db_path.exists())
                    start = manager.Barrier(WORKERS)
                    results = manager.list()
                    procs = [
                        ctx.Process(target=_boot_worker, args=(str(db_path), start, results))
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
                    conn = sqlite3.connect(str(db_path))
                    try:
                        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                    finally:
                        conn.close()
                    self.assertEqual(str(mode).lower(), "wal")
            self.assertEqual(failures, [], "\n".join(failures))

    def test_parallel_game_show_migrate_under_the_boot_lock(self) -> None:
        """``GameShowDB`` ALTERs (``team_names_approved``) also serialize.

        The school lock above staggers workers, so this boots only the game
        tables to keep the second lock honest.
        """
        ctx = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "lloves.sqlite"
            SchoolDB(db_path, Path(tmp), live_database_url="").close()
            failures: list[str] = []
            with ctx.Manager() as manager:
                for _round in range(3):
                    conn = sqlite3.connect(str(db_path), isolation_level=None)
                    conn.execute("ALTER TABLE games DROP COLUMN team_names_approved")
                    conn.close()
                    start = manager.Barrier(WORKERS)
                    results = manager.list()
                    procs = [
                        ctx.Process(
                            target=_game_worker, args=(str(db_path), start, results, True)
                        )
                        for _ in range(WORKERS)
                    ]
                    for proc in procs:
                        proc.start()
                    for proc in procs:
                        proc.join(timeout=120)
                    got = list(results)
                    self.assertEqual(len(got), WORKERS, got)
                    failures.extend(item for item in got if item != "ok")
                    self.assertIn("team_names_approved", _columns(db_path, "games"))
            self.assertEqual(failures, [], "\n".join(failures))

    def test_school_db_wraps_both_migration_phases_in_the_lock(self) -> None:
        """Both ``LovesDB`` DDL and ``GameShowDB`` construction take the lock."""
        source = (LMS_DIR / "school_db.py").read_text(encoding="utf-8")
        loves = source.split("class LovesDB:")[1].split("def close(self)")[0]
        locked = loves.split("with boot_schema_lock(db_path):")[1]
        self.assertLess(
            locked.index("self.conn.executescript(SCHEMA)"),
            locked.index("self._ensure_live_class_feature_schema()"),
        )
        self.assertLess(
            locked.index("self._ensure_live_class_feature_schema()"),
            locked.index("self._attach_live_presence()"),
        )
        self.assertLess(
            locked.index("configure_sqlite_connection(self.conn)"),
            locked.index("self.conn.executescript(SCHEMA)"),
        )
        before_lock = loves.split("with boot_schema_lock(db_path):")[0]
        self.assertNotIn("configure_sqlite_connection(self.conn)", before_lock)
        school = source.split("class SchoolDB(LovesDB):")[1]
        self.assertIn(
            "with boot_schema_lock(path):\n            self.game = mod.GameShowDB(",
            school,
        )


class _BusyThenOk:
    """Stand-in connection: the WAL pragma fails ``fail`` times first."""

    def __init__(self, fail: int, message: str = "database is locked") -> None:
        self.fail = fail
        self.message = message
        self.calls = 0

    def execute(self, sql: str):
        """Raise for the first ``fail`` WAL pragmas, then succeed."""
        if "journal_mode" in sql:
            self.calls += 1
            if self.calls <= self.fail:
                raise sqlite3.OperationalError(self.message)
        return self

    def fetchone(self):
        """Mimic the pragma's one-row reply."""
        return ("wal",)


class WalSwitchRetryTests(unittest.TestCase):
    """``enable_sqlite_wal`` retries BUSY and nothing else (MCK-104)."""

    def setUp(self) -> None:
        """Make the backoff instant for these tests."""
        import school_db

        self.school_db = school_db
        self._delays = school_db.SQLITE_WAL_RETRY_DELAYS
        school_db.SQLITE_WAL_RETRY_DELAYS = (0.0,) * len(self._delays)

    def tearDown(self) -> None:
        """Restore the real backoff."""
        self.school_db.SQLITE_WAL_RETRY_DELAYS = self._delays

    def test_busy_then_ok_succeeds(self) -> None:
        """Two BUSY answers, then WAL: no error."""
        conn = _BusyThenOk(2)
        self.school_db.enable_sqlite_wal(conn)
        self.assertEqual(conn.calls, 3)

    def test_busy_forever_raises_after_the_last_retry(self) -> None:
        """BUSY on every try still raises once the retries run out."""
        conn = _BusyThenOk(1000)
        with self.assertRaises(sqlite3.OperationalError):
            self.school_db.enable_sqlite_wal(conn)
        self.assertEqual(conn.calls, len(self._delays) + 1)

    def test_other_errors_are_not_retried(self) -> None:
        """A non-BUSY error raises on the first try."""
        conn = _BusyThenOk(1, message="disk I/O error")
        with self.assertRaises(sqlite3.OperationalError):
            self.school_db.enable_sqlite_wal(conn)
        self.assertEqual(conn.calls, 1)


if __name__ == "__main__":
    unittest.main()

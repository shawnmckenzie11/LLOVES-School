#!/usr/bin/env python3
"""MCK-14 / Sentry LLOVES-LMS-2: concurrent boot replace of course expectations.

Production runs four gunicorn workers without ``--preload``. Each one calls
``seed_curriculum`` → ``replace_course_expectations("MCF3M", ...)`` on the
same sqlite file at the same moment. The connection is in autocommit mode,
so the DELETE and each INSERT used to commit on their own and a second
worker's replace could land in the middle of the first one's inserts.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, Iterator

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)

from curriculum import load_mcf3m_expectation_rows  # noqa: E402
from school_db import SchoolDB  # noqa: E402


def _rows(count: int = 12) -> list[dict[str, Any]]:
    """Return ``count`` distinct specific expectations under one overall."""
    rows: list[dict[str, Any]] = [
        {
            "kind": "overall",
            "code": "A1",
            "parent_code": None,
            "strand": "A Test strand",
            "statement": "Overall statement",
        }
    ]
    for index in range(1, count):
        rows.append(
            {
                "kind": "specific",
                "code": f"A1.{index}",
                "parent_code": "A1",
                "strand": "A Test strand",
                "statement": f"Specific statement {index}",
            }
        )
    return rows


class ReplaceCourseExpectationsRaceTests(unittest.TestCase):
    """Two workers (two sqlite connections) replacing one course at boot."""

    def setUp(self) -> None:
        """Open two SchoolDB handles on one sqlite file, like two workers."""
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "lloves.sqlite"
        self.first = SchoolDB(self.db_path, root, live_database_url="")
        self.second = SchoolDB(self.db_path, root, live_database_url="")

    def tearDown(self) -> None:
        """Close both handles and remove the temp dir."""
        self.first.close()
        self.second.close()
        self._tmp.cleanup()

    def _stored_codes(self) -> list[str]:
        """Codes stored for the test course, read on a fresh connection."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            return sorted(
                str(row[0])
                for row in conn.execute(
                    "SELECT code FROM expectations WHERE course_code = 'TST3M'"
                )
            )
        finally:
            conn.close()

    def test_second_worker_replace_mid_insert_does_not_collide(self) -> None:
        """A worker that replaces mid-way through another's inserts must not
        make the first worker re-insert a code that already exists."""
        rows = _rows()
        second_done = threading.Event()
        errors: list[BaseException] = []

        def second_worker() -> None:
            try:
                self.second.replace_course_expectations(
                    "TST3M", rows, status="verified"
                )
            except BaseException as exc:  # noqa: BLE001 - surfaced below
                errors.append(exc)
            finally:
                second_done.set()

        thread = threading.Thread(target=second_worker)

        def interleaved() -> Iterator[dict[str, Any]]:
            """Yield the first half, let the other worker run, then the rest.

            Before the fix the other worker finishes inside the wait. With
            the write transaction it blocks on the sqlite lock until this
            replace commits, so the wait just times out.
            """
            half = len(rows) // 2
            for item in rows[:half]:
                yield item
            thread.start()
            second_done.wait(timeout=1.0)
            for item in rows[half:]:
                yield item

        stored = self.first.replace_course_expectations(
            "TST3M", interleaved(), status="verified"  # type: ignore[arg-type]
        )
        thread.join(timeout=60)
        self.assertFalse(thread.is_alive(), "second worker never finished")
        self.assertEqual(errors, [])
        self.assertEqual(stored, len(rows))
        self.assertEqual(
            self._stored_codes(), sorted(str(row["code"]) for row in rows)
        )

    def test_parallel_boot_replace_of_mcf3m_from_four_workers(self) -> None:
        """Four handles replacing the real MCF3M seed at once all succeed."""
        rows = load_mcf3m_expectation_rows()
        self.assertTrue(rows, "MCF3M seed missing")
        root = Path(self._tmp.name)
        extra = [SchoolDB(self.db_path, root, live_database_url="") for _ in range(2)]
        handles = [self.first, self.second, *extra]
        start = threading.Barrier(len(handles))
        errors: list[BaseException] = []

        def boot(handle: SchoolDB) -> None:
            start.wait()
            try:
                for _ in range(5):
                    handle.replace_course_expectations(
                        "MCF3M", rows, status="verified"
                    )
            except BaseException as exc:  # noqa: BLE001 - surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=boot, args=(h,)) for h in handles]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=120)
        finally:
            for handle in extra:
                handle.close()
        self.assertEqual(errors, [])
        conn = sqlite3.connect(str(self.db_path))
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM expectations WHERE course_code = 'MCF3M'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, len({str(row["code"]).strip() for row in rows}))

    def test_repeated_code_in_source_list_does_not_fail(self) -> None:
        """A duplicated code in the seed keeps the last row instead of raising."""
        rows = _rows(4)
        duplicate = dict(rows[2], statement="Corrected statement")
        stored = self.first.replace_course_expectations(
            "TST3M", [*rows, duplicate], status="verified"
        )
        self.assertEqual(stored, len(rows))
        self.assertEqual(self._stored_codes(), sorted(str(r["code"]) for r in rows))
        statement = self.first.conn.execute(
            "SELECT statement FROM expectations WHERE course_code = 'TST3M' AND code = ?",
            (duplicate["code"],),
        ).fetchone()[0]
        self.assertEqual(statement, "Corrected statement")

    def test_failed_replace_rolls_back_and_keeps_old_rows(self) -> None:
        """An error mid-replace leaves the previous rows and no open transaction."""
        rows = _rows(4)
        self.first.replace_course_expectations("TST3M", rows, status="verified")

        def broken() -> Iterator[dict[str, Any]]:
            yield rows[0]
            raise RuntimeError("seed read failed")

        with self.assertRaises(RuntimeError):
            self.first.replace_course_expectations(
                "TST3M", broken(), status="verified"  # type: ignore[arg-type]
            )
        self.assertFalse(self.first.conn.in_transaction)
        self.assertEqual(self._stored_codes(), sorted(str(r["code"]) for r in rows))
        # The connection is still usable for the next write.
        self.assertEqual(
            self.first.replace_course_expectations("TST3M", rows, status="verified"),
            len(rows),
        )


if __name__ == "__main__":
    unittest.main()

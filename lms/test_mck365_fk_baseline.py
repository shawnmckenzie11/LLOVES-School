#!/usr/bin/env python3
"""Storage evidence for the student-uid migration.

Synthetic rows only. Violation checks compare the exact
``PRAGMA foreign_key_check`` tuples, not merely how many there are.
No names, pins, or legacy values are printed.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
MGS_DIR = REPO_ROOT / "tools" / "math-game-show"
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(MGS_DIR))

os.environ.setdefault("FLASK_SECRET_KEY", "mck365-fk-secret")

from student_uid_migration import (  # noqa: E402
    _Blocked,
    _CLASS_CSV,
    _fk_violation_set,
    _reject_student_fk_violations,
    migrate_students_off_canvas_id,
)
from db import GameShowDB  # noqa: E402

# Parent preflight: db about 56MB plus a 4MB WAL, 891MB free on /data.
_PREFLIGHT_DB_BYTES = 56 * 1024 * 1024
_PREFLIGHT_WAL_BYTES = 4 * 1024 * 1024
_PREFLIGHT_FREE_BYTES = 891 * 1024 * 1024


def _open(path: Path) -> sqlite3.Connection:
    """Open an autocommit connection the migration can use.

    Args:
        path: SQLite file to create.

    Returns:
        Connection with foreign keys enforced.
    """
    conn = sqlite3.connect(path)
    conn.isolation_level = None
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _students_and_score(conn: sqlite3.Connection) -> None:
    """Create one legacy student and one attendance row that points at them.

    Args:
        conn: Empty connection.
    """
    conn.executescript(
        """
        CREATE TABLE classes (
            id INTEGER PRIMARY KEY,
            course_code TEXT NOT NULL
        );
        CREATE TABLE students (
            id INTEGER PRIMARY KEY,
            class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
            canvas_id TEXT NOT NULL,
            last_display TEXT NOT NULL,
            first_name TEXT NOT NULL
        );
        CREATE TABLE session_scores (
            session_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            present INTEGER NOT NULL DEFAULT 0,
            points REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (session_id, student_id)
        );
        INSERT INTO classes (id, course_code) VALUES (1, 'MCF3M');
        INSERT INTO students (id, class_id, canvas_id, last_display, first_name)
        VALUES (1, 1, 'syn-1', 'Row', 'One');
        INSERT INTO session_scores (session_id, student_id, present, points)
        VALUES (1, 1, 1, 4);
        """
    )


class FkSetPolicyTests(unittest.TestCase):
    """Exact violation sets, plus a hard stop for student-related rows."""

    def test_equal_counts_with_different_rows_are_not_the_same_set(self) -> None:
        """A replaced rowid is a new violation even when the count matches."""
        before = (("questions", 1, "question_banks", 0),)
        after = (("questions", 9, "question_banks", 0),)
        self.assertEqual(len(before), len(after))
        self.assertNotEqual(before, after)

    def test_question_and_outline_orphans_stay_exact_and_students_migrate(self) -> None:
        """The reported baseline directions are kept tuple for tuple."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        conn = _open(root / "school.sqlite")
        self.addCleanup(conn.close)
        _students_and_score(conn)
        conn.executescript(
            """
            CREATE TABLE question_banks (
                id INTEGER PRIMARY KEY,
                library_id INTEGER NOT NULL,
                import_key TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE questions (
                id INTEGER PRIMARY KEY,
                bank_id INTEGER NOT NULL
                    REFERENCES question_banks(id) ON DELETE CASCADE,
                import_key TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE module_outlines (
                id INTEGER PRIMARY KEY,
                library_id INTEGER NOT NULL,
                import_key TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE module_items (
                id INTEGER PRIMARY KEY,
                outline_id INTEGER NOT NULL
                    REFERENCES module_outlines(id) ON DELETE CASCADE,
                import_key TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "INSERT INTO questions (id, bank_id, import_key, title, created_at) "
            "VALUES (11, 50, 'q', 'Q', 't')"
        )
        conn.execute(
            "INSERT INTO questions (id, bank_id, import_key, title, created_at) "
            "VALUES (12, 51, 'q2', 'Q2', 't')"
        )
        conn.execute(
            "INSERT INTO module_items (id, outline_id, import_key, title, created_at) "
            "VALUES (21, 80, 'm', 'M', 't')"
        )
        conn.execute("PRAGMA foreign_keys = ON")
        before = _fk_violation_set(conn)
        self.assertEqual(
            before,
            (
                ("module_items", 21, "module_outlines", 0),
                ("questions", 11, "question_banks", 0),
                ("questions", 12, "question_banks", 0),
            ),
        )
        _reject_student_fk_violations(before, tuple())
        uploads = root / "uploads"
        uploads.mkdir()
        (uploads / "class-1.csv").write_text("legacy\n", encoding="utf-8")
        library = root / "libraries" / "pack-1"
        library.mkdir(parents=True)
        (library / "keep.bin").write_bytes(b"untouched")
        (uploads / "notes.txt").write_text("keep\n", encoding="utf-8")
        result = migrate_students_off_canvas_id(conn, uploads_dir=uploads)
        self.assertEqual(result["status"], "applied")
        after = _fk_violation_set(conn)
        self.assertEqual(after, before)
        self.assertEqual(len(after), len(before))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(students)")}
        self.assertNotIn("canvas_id", cols)
        self.assertIn("row_uid", cols)
        score = conn.execute(
            "SELECT present, points FROM session_scores WHERE student_id = 1"
        ).fetchone()
        self.assertEqual(int(score["present"]), 1)
        self.assertEqual(float(score["points"]), 4.0)
        self.assertFalse((uploads / "class-1.csv").exists())
        self.assertTrue((uploads / "notes.txt").is_file())
        self.assertEqual((library / "keep.bin").read_bytes(), b"untouched")
        again = migrate_students_off_canvas_id(conn, uploads_dir=uploads)
        self.assertEqual(again["status"], "noop")
        self.assertEqual(_fk_violation_set(conn), before)

    def test_a_student_violation_blocks_and_deletes_nothing(self) -> None:
        """An orphan attendance row is not cleaned up and the drop rolls back."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        conn = _open(root / "school.sqlite")
        self.addCleanup(conn.close)
        _students_and_score(conn)
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "INSERT INTO session_scores (session_id, student_id, present, points) "
            "VALUES (2, 999, 1, 1)"
        )
        conn.execute("PRAGMA foreign_keys = ON")
        before = _fk_violation_set(conn)
        self.assertTrue(any(row[2] == "students" for row in before))
        uploads = root / "uploads"
        uploads.mkdir()
        (uploads / "class-4.csv").write_text("legacy\n", encoding="utf-8")
        with self.assertRaises(Exception):
            _reject_student_fk_violations(
                before,
                (("session_scores", "student_id", "id", "NO ACTION", "CASCADE"),),
            )
        result = migrate_students_off_canvas_id(conn, uploads_dir=uploads)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "student_foreign_key_violation")
        self.assertEqual(_fk_violation_set(conn), before)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(students)")}
        self.assertIn("canvas_id", cols)
        orphan = conn.execute(
            "SELECT present FROM session_scores WHERE student_id = 999"
        ).fetchone()
        self.assertIsNotNone(orphan)
        self.assertTrue((uploads / "class-4.csv").is_file())

    def test_rollback_keeps_the_violation_set_and_the_csv(self) -> None:
        """A failpoint rolls the schema back and does not sweep uploads."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        conn = _open(root / "school.sqlite")
        self.addCleanup(conn.close)
        _students_and_score(conn)
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.executescript(
            """
            CREATE TABLE question_banks (id INTEGER PRIMARY KEY);
            CREATE TABLE questions (
                id INTEGER PRIMARY KEY,
                bank_id INTEGER NOT NULL REFERENCES question_banks(id)
            );
            INSERT INTO questions (id, bank_id) VALUES (3, 70);
            """
        )
        conn.execute("PRAGMA foreign_keys = ON")
        before = _fk_violation_set(conn)
        uploads = root / "uploads"
        uploads.mkdir()
        (uploads / "class-9.csv").write_text("legacy\n", encoding="utf-8")
        from student_uid_migration import StudentUidMigrationInterrupted

        with self.assertRaises(StudentUidMigrationInterrupted):
            migrate_students_off_canvas_id(
                conn, uploads_dir=uploads, failpoint="after_drop"
            )
        self.assertEqual(_fk_violation_set(conn), before)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(students)")}
        self.assertIn("canvas_id", cols)
        self.assertTrue((uploads / "class-9.csv").is_file())


class StartupTouchTests(unittest.TestCase):
    """What a candidate boot and the uid migration create or delete."""

    def test_csv_sweep_pattern_ignores_libraries_and_other_uploads(self) -> None:
        """Only ``uploads/class-<digits>.csv`` matches the post-commit delete."""
        self.assertIsNotNone(_CLASS_CSV.fullmatch("class-12.csv"))
        self.assertIsNone(_CLASS_CSV.fullmatch("class-12.csv.bak"))
        self.assertIsNone(_CLASS_CSV.fullmatch("libraries/class-12.csv"))
        self.assertIsNone(_CLASS_CSV.fullmatch("keep.bin"))
        self.assertIsNone(_CLASS_CSV.fullmatch("notes.txt"))

    def test_opening_the_game_database_creates_logs_and_uploads_only(self) -> None:
        """Startup directories are logs and uploads. A library file stays."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        library = root / "libraries" / "pack-1"
        library.mkdir(parents=True)
        (library / "keep.bin").write_bytes(b"pack")
        db = GameShowDB(root / "lloves.sqlite", root)
        self.addCleanup(db.close)
        self.assertTrue((root / "logs").is_dir())
        self.assertTrue((root / "uploads").is_dir())
        self.assertTrue((root / "lloves.sqlite").is_file())
        self.assertEqual((library / "keep.bin").read_bytes(), b"pack")
        names = {path.name for path in root.iterdir()}
        self.assertIn("logs", names)
        self.assertIn("uploads", names)
        self.assertIn("libraries", names)
        self.assertNotIn("class-1.csv", {path.name for path in (root / "uploads").iterdir()})

    def test_preflight_free_space_covers_one_database_copy_plus_the_wal(self) -> None:
        """891MB free is larger than one extra copy of the 56MB file and 4MB WAL.

        The students rebuild writes new pages into the same SQLite file.
        In WAL mode those pages land in the WAL until checkpoint. A
        conservative bound is one extra copy of the database file plus
        the existing WAL. Rollback of an open transaction discards
        uncommitted WAL frames and does not need a second copy. The
        gradebook CSV delete happens only after commit, so a database
        rollback cannot put that file back.
        """
        conservative = _PREFLIGHT_DB_BYTES + _PREFLIGHT_WAL_BYTES
        self.assertLess(conservative, _PREFLIGHT_FREE_BYTES)
        self.assertEqual(conservative, 60 * 1024 * 1024)


class TimerHeldBackfillTests(unittest.TestCase):
    """An old games table gains timer_held 0 without moving the clock."""

    def test_null_clocks_stay_null_and_an_explicit_hold_survives_reopen(self) -> None:
        """Attendance leftovers and an ended clock are not rewritten."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        path = root / "app.sqlite"
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE classes (
                id INTEGER PRIMARY KEY,
                year TEXT NOT NULL,
                semester TEXT NOT NULL,
                course_code TEXT NOT NULL,
                days TEXT NOT NULL,
                time TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE students (
                id INTEGER PRIMARY KEY,
                class_id INTEGER NOT NULL,
                canvas_id TEXT NOT NULL,
                last_display TEXT NOT NULL,
                first_name TEXT NOT NULL
            );
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY,
                class_id INTEGER NOT NULL,
                starts_at TEXT NOT NULL,
                header_label TEXT NOT NULL,
                status TEXT NOT NULL,
                log_path TEXT
            );
            CREATE TABLE session_scores (
                session_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                present INTEGER NOT NULL DEFAULT 0,
                points INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (session_id, student_id)
            );
            CREATE TABLE games (
                id INTEGER PRIMARY KEY,
                class_id INTEGER NOT NULL,
                session_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                event_seq INTEGER NOT NULL DEFAULT 0,
                last_event_json TEXT,
                owns_session INTEGER NOT NULL DEFAULT 0,
                round_started_at TEXT,
                round_duration_sec INTEGER
            );
            CREATE TABLE point_events (
                id INTEGER PRIMARY KEY,
                session_id INTEGER NOT NULL,
                game_id INTEGER NOT NULL,
                seq INTEGER NOT NULL,
                ts TEXT NOT NULL,
                from_kind TEXT NOT NULL,
                from_id INTEGER,
                to_kind TEXT NOT NULL,
                to_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                team_rule TEXT
            );
            INSERT INTO classes VALUES (
                1, '2026/27', 'Semester 1', 'MCF3M', 'M/W/F', '2:00pm', 't0'
            );
            INSERT INTO students VALUES (1, 1, 'syn', 'Row', 'One');
            INSERT INTO sessions VALUES (1, 1, 't1', 'label', 'ended', NULL);
            INSERT INTO games (
                id, class_id, session_id, status, created_at, round_started_at,
                round_duration_sec
            ) VALUES
                (1, 1, 1, 'attendance', 't0', NULL, NULL),
                (2, 1, 1, 'ended', 't0', '2026-09-01T12:00:00', 120);
            """
        )
        conn.commit()
        conn.close()
        db = GameShowDB(path, root)
        self.addCleanup(db.close)
        rows = db.conn.execute(
            """
            SELECT id, status, round_started_at, round_duration_sec, timer_held
            FROM games ORDER BY id
            """
        ).fetchall()
        self.assertIsNone(rows[0]["round_started_at"])
        self.assertIsNone(rows[0]["round_duration_sec"])
        self.assertEqual(int(rows[0]["timer_held"]), 0)
        self.assertEqual(rows[1]["round_started_at"], "2026-09-01T12:00:00")
        self.assertEqual(int(rows[1]["round_duration_sec"]), 120)
        self.assertEqual(int(rows[1]["timer_held"]), 0)
        db.conn.execute("UPDATE games SET timer_held = 1 WHERE id = 2")
        db.conn.commit()
        db.close()
        db = GameShowDB(path, root)
        self.addCleanup(db.close)
        again = db.conn.execute(
            """
            SELECT id, round_started_at, round_duration_sec, timer_held
            FROM games ORDER BY id
            """
        ).fetchall()
        self.assertEqual(int(again[0]["timer_held"]), 0)
        self.assertIsNone(again[0]["round_started_at"])
        self.assertEqual(int(again[1]["timer_held"]), 1)
        self.assertEqual(again[1]["round_started_at"], "2026-09-01T12:00:00")
        self.assertEqual(int(again[1]["round_duration_sec"]), 120)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""MCK-365: drop the legacy external student column without moving awards.

Synthetic rows only. Assertions count leaks instead of printing names,
legacy values, or fingerprints.
"""

from __future__ import annotations

import json
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

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")
os.environ.setdefault("FLASK_SECRET_KEY", "mck365-test-secret")

from celebration import (  # noqa: E402
    _fp_key,
    _resolve_ref,
    _roster_token,
    _student_fp,
)
from student_uid_migration import (  # noqa: E402
    CELEBRATION_SETTING_KEYS,
    MIGRATION_KEY,
    StudentUidMigrationInterrupted,
    celebration_fp_key,
    celebration_key_id,
    migrate_students_off_canvas_id,
    student_fp,
)
from csv_import import parse_canvas_grades_csv  # noqa: E402
from app import create_app  # noqa: E402

SECRET = "mck365-test-secret"
LEGACY_A = "legacy-a"
LEGACY_B = "legacy-b"
LEGACY_REUSED = "legacy-old"
LEGACY_NEW = "legacy-new"
LEGACY_GONE = "legacy-gone"


def _fp(class_id: int, student_id: int, token: str) -> str:
    """Fingerprint under the test secret."""
    return student_fp(celebration_fp_key(SECRET), class_id, student_id, token)


def _leak_count(blob: str, needles: tuple[str, ...]) -> int:
    """How many secret needles appear in ``blob``."""
    return sum(blob.count(needle) for needle in needles)


class _Key:
    """Stand-in school so celebration's key helper can be compared."""

    _celebration_fp_secret = SECRET


class KeyAgreementTests(unittest.TestCase):
    """The migration and the celebration board must hash with the same key."""

    def test_fp_key_matches_celebration(self) -> None:
        """A drift here would rewrite awards onto the wrong fingerprint."""
        self.assertEqual(celebration_fp_key(SECRET), _fp_key(_Key()))
        self.assertEqual(
            student_fp(celebration_fp_key(SECRET), 1, 2, "abc"),
            _student_fp(_Key(), 1, 2, "abc"),
        )


class _LegacyDb:
    """Disposable file with the pre-change students table and award rows."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.isolation_level = None
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._build()

    def close(self) -> None:
        """Close the fixture connection."""
        self.conn.close()

    def _build(self) -> None:
        """Create a realistic slice: roster, scores, grants, fingerprints."""
        self.conn.executescript(
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
                first_name TEXT NOT NULL,
                codename TEXT,
                character_key TEXT,
                pin_hash TEXT,
                default_pin_ciphertext TEXT,
                pin_set_by TEXT,
                is_test_student INTEGER NOT NULL DEFAULT 0,
                UNIQUE(class_id, canvas_id)
            );
            CREATE INDEX idx_students_class ON students(class_id);
            CREATE UNIQUE INDEX idx_students_class_codename
                ON students(class_id, codename)
                WHERE codename IS NOT NULL AND TRIM(codename) != '';
            CREATE UNIQUE INDEX uq_students_one_test_student
                ON students(class_id) WHERE is_test_student = 1;
            CREATE TRIGGER students_keep AFTER UPDATE ON students
            BEGIN
                SELECT 1;
            END;
            CREATE TABLE session_scores (
                session_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                present INTEGER NOT NULL DEFAULT 0,
                points REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (session_id, student_id)
            );
            CREATE TABLE roster_notes (
                id INTEGER PRIMARY KEY,
                class_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                note TEXT NOT NULL
            );
            CREATE TABLE avatar_reward_grants (
                id INTEGER PRIMARY KEY,
                class_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                status TEXT NOT NULL,
                avatar_key TEXT,
                created_at TEXT NOT NULL,
                claimed_at TEXT
            );
            CREATE TABLE school_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE student_id_seq (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                next_id INTEGER NOT NULL
            );
            INSERT INTO classes (id, course_code) VALUES (1, 'MCF3M');
            INSERT INTO student_id_seq (id, next_id) VALUES (1, 20);
            """
        )
        rows = (
            (1, LEGACY_A, "Sam", "Sam"),
            (2, LEGACY_B, "Sam", "Sam 2"),
            (7, LEGACY_NEW, "Pat", "Pat"),
        )
        for sid, legacy, first, code in rows:
            self.conn.execute(
                """
                INSERT INTO students (
                    id, class_id, canvas_id, last_display, first_name, codename,
                    pin_hash, is_test_student
                ) VALUES (?, 1, ?, '', ?, ?, ?, 0)
                """,
                (sid, legacy, first, code, "hash-" + str(sid)),
            )
            self.conn.execute(
                "INSERT INTO session_scores (session_id, student_id, present, points) VALUES (1, ?, 1, ?)",
                (sid, sid),
            )
            self.conn.execute(
                "INSERT INTO roster_notes (class_id, student_id, note) VALUES (1, ?, ?)",
                (sid, "kept"),
            )
        self.conn.execute(
            """
            INSERT INTO avatar_reward_grants (
                id, class_id, student_id, source, source_ref, status, avatar_key,
                created_at, claimed_at
            ) VALUES (1, 1, 1, 'shoutout', 'shoutout:keep', 'claimed', ?, 't0', 't1')
            """,
            ("avatar-keep",),
        )
        self.conn.execute(
            """
            INSERT INTO avatar_reward_grants (
                id, class_id, student_id, source, source_ref, status, avatar_key,
                created_at
            ) VALUES (2, 1, 7, 'engaged', 'engaged:old', 'pending', NULL, 't0')
            """
        )
        award = {
            "class_id": 1,
            "student_id": 1,
            "fp": _fp(1, 1, LEGACY_A),
            "blurb": "keep",
            "featured_at": "t0",
        }
        identity = {"class_id": 1, "student_id": 1, "fp": _fp(1, 1, LEGACY_A), "featured_at": "t0"}
        snapshot = {
            "version": 3,
            "kid": celebration_key_id(celebration_fp_key(SECRET)),
            "cards": [
                {
                    "course": "MCF3M",
                    "students": [
                        {"class_id": 1, "student_id": 1, "fp": _fp(1, 1, LEGACY_A)},
                        {"class_id": 1, "student_id": 50, "fp": _fp(1, 50, LEGACY_GONE)},
                        {"class_id": 1, "student_id": 7, "fp": _fp(1, 7, LEGACY_REUSED)},
                    ],
                }
            ],
        }
        for key, payload in (
            ("celebration_featured_award", award),
            ("celebration_featured_award_id", identity),
            ("celebration_public_snapshot", snapshot),
        ):
            self.conn.execute(
                "INSERT INTO school_settings (key, value, updated_at) VALUES (?, ?, 't0')",
                (key, json.dumps(payload)),
            )

    def ids(self) -> list[int]:
        """Student primary keys in order."""
        return [int(row[0]) for row in self.conn.execute("SELECT id FROM students ORDER BY id")]

    def uids(self) -> list[str]:
        """row_uid values in id order, after the column exists."""
        return [
            str(row[0])
            for row in self.conn.execute("SELECT row_uid FROM students ORDER BY id")
        ]


class MigrationTests(unittest.TestCase):
    """Rebuild, rerun, reuse, and rollback on a disposable file."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _LegacyDb(Path(self.tmp.name) / "school.sqlite")
        self.uploads = Path(self.tmp.name) / "uploads"
        self.uploads.mkdir()
        (self.uploads / "class-1.csv").write_text("Student,ID\nSam,EXTERNAL\n", encoding="utf-8")
        (self.uploads / "notes.csv").write_text("keep\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_failure_rolls_back_and_then_migrates(self) -> None:
        """An interrupted drop leaves the original rows usable, then a rerun applies."""
        before_ids = self.db.ids()
        before_scores = self._count("session_scores")
        with self.assertRaises(StudentUidMigrationInterrupted):
            migrate_students_off_canvas_id(
                self.db.conn,
                uploads_dir=self.uploads,
                secret=SECRET,
                failpoint="after_drop",
            )
        self.assertEqual(int(self.db.conn.execute("PRAGMA foreign_keys").fetchone()[0]), 1)
        self.assertIn("canvas_id", self._columns())
        self.assertIsNone(
            self.db.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'students_mck365'"
            ).fetchone()
        )
        self.assertEqual(self.db.ids(), before_ids)
        self.assertEqual(self._count("session_scores"), before_scores)
        self.assertTrue((self.uploads / "class-1.csv").is_file())
        self.db.conn.execute(
            """
            INSERT INTO students (id, class_id, canvas_id, last_display, first_name, codename)
            VALUES (8, 1, 'legacy-extra', '', 'Jo', 'Jo')
            """
        )
        self.db.conn.execute(
            "INSERT INTO session_scores (session_id, student_id, present, points) VALUES (1, 8, 1, 8)"
        )
        result = migrate_students_off_canvas_id(
            self.db.conn, uploads_dir=self.uploads, secret=SECRET
        )
        self.assertEqual(result["status"], "applied")
        self.assertNotIn("canvas_id", self._columns())
        self.assertEqual(self.db.ids(), [1, 2, 7, 8])
        self.assertEqual(self._count("session_scores"), before_scores + 1)
        self.assertEqual(self._count("roster_notes"), 3)
        self.assertFalse((self.uploads / "class-1.csv").is_file())
        self.assertTrue((self.uploads / "notes.csv").is_file())
        self.assertEqual(_leak_count(self._detail(), (LEGACY_A, LEGACY_REUSED, "Sam", "hash-1")), 0)

    def test_awards_stay_with_the_same_rows(self) -> None:
        """Matching fingerprints follow row_uid. A reused id does not inherit."""
        migrate_students_off_canvas_id(self.db.conn, uploads_dir=self.uploads, secret=SECRET)
        uids = {int(row["id"]): str(row["row_uid"]) for row in self.db.conn.execute("SELECT id, row_uid FROM students")}
        self.assertEqual(len(uids), len(set(uids.values())))
        snapshot = json.loads(
            self.db.conn.execute(
                "SELECT value FROM school_settings WHERE key = 'celebration_public_snapshot'"
            ).fetchone()[0]
        )
        by_student = {int(ref["student_id"]): ref["fp"] for ref in snapshot["cards"][0]["students"]}
        self.assertEqual(by_student[1], _fp(1, 1, uids[1]))
        self.assertEqual(by_student[50], _fp(1, 50, LEGACY_GONE))
        self.assertEqual(by_student[7], _fp(1, 7, LEGACY_REUSED))
        self.assertNotEqual(by_student[7], _fp(1, 7, uids[7]))
        award = json.loads(
            self.db.conn.execute(
                "SELECT value FROM school_settings WHERE key = 'celebration_featured_award'"
            ).fetchone()[0]
        )
        self.assertEqual(award["fp"], _fp(1, 1, uids[1]))
        kept = self.db.conn.execute(
            "SELECT status, avatar_key, source_ref FROM avatar_reward_grants WHERE student_id = 1"
        ).fetchone()
        self.assertEqual(tuple(kept), ("claimed", "avatar-keep", "shoutout:keep"))
        self.assertIsNone(
            self.db.conn.execute(
                "SELECT 1 FROM avatar_reward_grants WHERE student_id = 7"
            ).fetchone()
        )
        pins = [
            str(row[0])
            for row in self.db.conn.execute("SELECT pin_hash FROM students ORDER BY id")
        ]
        self.assertEqual(pins, ["hash-1", "hash-2", "hash-7"])
        seq = int(self.db.conn.execute("SELECT next_id FROM student_id_seq").fetchone()[0])
        self.assertEqual(seq, 20)
        renamed = self.db.conn.execute("SELECT row_uid FROM students WHERE id = 1").fetchone()[0]
        self.db.conn.execute("UPDATE students SET first_name = 'Renamed' WHERE id = 1")
        self.assertEqual(
            self.db.conn.execute("SELECT row_uid FROM students WHERE id = 1").fetchone()[0],
            renamed,
        )
        again = migrate_students_off_canvas_id(
            self.db.conn, uploads_dir=self.uploads, secret=SECRET
        )
        self.assertEqual(again["status"], "noop")
        self.assertEqual(
            [str(row[0]) for row in self.db.conn.execute("SELECT row_uid FROM students ORDER BY id")],
            [uids[sid] for sid in sorted(uids)],
        )
        marker = self.db.conn.execute(
            "SELECT status, applied_at FROM lms_data_migrations WHERE migration_key = ?",
            (MIGRATION_KEY,),
        ).fetchone()
        self.assertEqual(marker["status"], "applied")
        applied_at = marker["applied_at"]
        migrate_students_off_canvas_id(self.db.conn, secret=SECRET)
        self.assertEqual(
            self.db.conn.execute(
                "SELECT applied_at FROM lms_data_migrations WHERE migration_key = ?",
                (MIGRATION_KEY,),
            ).fetchone()[0],
            applied_at,
        )
        fks = list(self.db.conn.execute("PRAGMA foreign_key_list(students)"))
        self.assertEqual(fks[0]["table"], "classes")
        self.assertEqual(fks[0]["on_delete"], "CASCADE")
        child = list(self.db.conn.execute("PRAGMA foreign_key_list(session_scores)"))
        self.assertEqual(child[0]["table"], "students")
        self.assertEqual(child[0]["on_delete"], "CASCADE")
        self.assertIsNotNone(
            self.db.conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = 'students_keep'"
            ).fetchone()
        )
        self.db.conn.execute("DELETE FROM classes WHERE id = 1")
        self.assertEqual(self._count("students"), 0)
        self.assertEqual(self._count("session_scores"), 0)
        self.assertEqual(self._count("roster_notes"), 3)

    def test_kid_mismatch_blocks_the_drop(self) -> None:
        """A snapshot signed by another key is not rewritten or dropped."""
        self.db.conn.execute(
            """
            UPDATE school_settings
            SET value = json_set(value, '$.kid', 'not-this-key')
            WHERE key = 'celebration_public_snapshot'
            """
        )
        result = migrate_students_off_canvas_id(self.db.conn, secret=SECRET)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "kid_mismatch")
        self.assertIn("canvas_id", self._columns())
        self.assertEqual(self._count("avatar_reward_grants"), 2)
        self.assertTrue((self.uploads / "class-1.csv").is_file())

    def test_inconsistent_fingerprint_blocks_the_drop(self) -> None:
        """One id with both a match and a miss is not guessed."""
        self.db.conn.execute(
            """
            UPDATE school_settings
            SET value = json_set(value, '$.fp', 'different-fingerprint')
            WHERE key = 'celebration_featured_award_id'
            """
        )
        result = migrate_students_off_canvas_id(self.db.conn, secret=SECRET)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "inconsistent_fingerprint")
        self.assertIn("canvas_id", self._columns())

    def _columns(self) -> set[str]:
        return {str(row[1]) for row in self.db.conn.execute("PRAGMA table_info(students)")}

    def _count(self, table: str) -> int:
        return int(self.db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def _detail(self) -> str:
        row = self.db.conn.execute(
            "SELECT detail_json FROM lms_data_migrations WHERE migration_key = ?",
            (MIGRATION_KEY,),
        ).fetchone()
        return "" if row is None else str(row[0])


class ImportAndRosterTests(unittest.TestCase):
    """Gradebook import keeps duplicate first names and drops external columns."""

    def test_duplicate_names_and_ignored_id_column(self) -> None:
        """Two identical names stay two rows. The ID column is not stored."""
        text = "Student,ID,SIS User ID\nPoints Possible,1,1\n\"Nxx, Sam\",EXTERNAL-1,SIS-1\n\"Nxx, Sam\",EXTERNAL-2,SIS-2\n"
        parsed = parse_canvas_grades_csv(text)
        self.assertEqual(len(parsed), 2)
        self.assertEqual([row["first_name"] for row in parsed], ["Sam", "Sam"])
        self.assertEqual(_leak_count(json.dumps(parsed), ("EXTERNAL-1", "EXTERNAL-2", "SIS-1")), 0)
        bare = parse_canvas_grades_csv("Student\n\"Nxx, Sam\"\n\"Nxx, Sam\"\n")
        self.assertEqual(len(bare), 2)

    def test_repeat_import_creates_new_rows(self) -> None:
        """Importing the same file again does not merge into the first class."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = create_app(
            db_path=Path(tmp.name) / "school.sqlite",
            data_dir=Path(tmp.name),
            testing=True,
            live_database_url="",
        )
        school = app.config["SCHOOL_DB"]
        self.addCleanup(school.close)
        csv_text = "Student\n\"Nxx, Sam\"\n\"Nxx, Sam\"\n"
        first = school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCF3M",
            days_preset="M/W/F",
            time_label="2:00pm",
            csv_text=csv_text,
        )
        second = school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCF3M",
            days_preset="M/W/F",
            time_label="2:00pm",
            csv_text=csv_text,
        )
        rows = list(school.game.conn.execute("SELECT id, class_id, row_uid, first_name FROM students"))
        self.assertEqual(len(rows), 4)
        self.assertEqual(len({str(row["row_uid"]) for row in rows}), 4)
        self.assertEqual({int(row["class_id"]) for row in rows}, {int(first["id"]), int(second["id"])})
        self.assertTrue(all(str(row["first_name"]) == "Sam" for row in rows))
        uploads = Path(tmp.name) / "uploads"
        self.assertEqual(list(uploads.glob("class-*.csv")), [])
        dash = school.game.dashboard(int(first["id"]))
        self.assertTrue(dash["students"])
        hidden = {"row_uid", "pin_hash", "default_pin_ciphertext", "canvas_id"}
        self.assertTrue(hidden.isdisjoint(dash["students"][0]))
        sid = int(dash["students"][0]["id"])
        uid = school.game.get_student(int(first["id"]), sid)["row_uid"]
        school.game.rename_student_name(int(first["id"]), sid, display_name="Renamed")
        self.assertEqual(school.game.get_student(int(first["id"]), sid)["row_uid"], uid)
        blob = " ".join(str(value) for row in rows for value in tuple(row))
        self.assertEqual(_leak_count(blob, ("EXTERNAL-1",)), 0)

    def test_new_award_resolves_only_for_that_uid(self) -> None:
        """A fingerprint does not follow a recycled numeric id."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = create_app(
            db_path=Path(tmp.name) / "school.sqlite",
            data_dir=Path(tmp.name),
            testing=True,
            live_database_url="",
        )
        school = app.config["SCHOOL_DB"]
        self.addCleanup(school.close)
        created = school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCF3M",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Sam", "Sam 2"],
        )
        class_id = int(created["id"])
        sam = school.game.find_student_by_codename(class_id, "Sam")
        assert sam is not None
        ref = {
            "class_id": class_id,
            "student_id": int(sam["id"]),
            "fp": _student_fp(school, class_id, int(sam["id"]), _roster_token(sam)),
        }
        self.assertIsNotNone(_resolve_ref(school, ref))
        school.game.conn.execute("DELETE FROM students WHERE id = ?", (int(sam["id"]),))
        school.game.conn.execute(
            """
            INSERT INTO students (id, class_id, last_display, first_name, codename, row_uid, is_test_student)
            VALUES (?, ?, '', 'Pat', 'Pat', 'different-uid', 0)
            """,
            (int(sam["id"]), class_id),
        )
        self.assertIsNone(_resolve_ref(school, ref))
        self.assertIn("celebration_featured_award", CELEBRATION_SETTING_KEYS)


if __name__ == "__main__":
    unittest.main()

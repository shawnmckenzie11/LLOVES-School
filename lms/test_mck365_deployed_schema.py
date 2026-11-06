#!/usr/bin/env python3
"""Rehearse the deployed v315 schema with synthetic rows.

The fixture is schema, index SQL, and dbstat sizes. It has no roster
rows. Defaults and the students ``UNIQUE(class_id, canvas_id)`` come
from the captured ``CREATE`` statement. The partial metadata file that
dropped DDL is not used as equivalence evidence.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
MGS_DIR = REPO_ROOT / "tools" / "math-game-show"
FIXTURE = LMS_DIR / "fixtures" / "deployed-schema-v315-641c626.json"
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(MGS_DIR))

os.environ.setdefault("FLASK_SECRET_KEY", "deployed-schema-rehearsal")
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from student_uid_migration import (  # noqa: E402
    _Blocked,
    _fk_violation_set,
    _require_same_fk_violations,
    _unsupported_students_ddl,
    celebration_fp_key,
    celebration_key_id,
    migrate_students_off_canvas_id,
    sqlite_rebuild_peak_bytes,
    student_fp,
    student_uid_invariants,
    student_uid_ready,
)
from student_uid_probe import data_mount_fstype, main as probe_main, refuse_reason  # noqa: E402

SECRET = "deployed-schema-rehearsal"
# Captured file sizes from the read-only preflight, matched to page_count.
DB_BYTES = 58_552_320
WAL_BYTES = 4_293_072
FREE_BYTES = 891 * 1024 * 1024


def _load(conn: sqlite3.Connection, payload: dict) -> None:
    """Create every captured table and explicit index.

    Args:
        conn: Empty connection.
        payload: Deployed DDL supplement.
    """
    conn.execute("PRAGMA foreign_keys = OFF")
    rows = payload["sqlite_master"]
    for kind in ("table", "index"):
        for row in rows:
            if row.get("type") != kind:
                continue
            name = str(row.get("name") or "")
            if name.startswith("sqlite_"):
                continue
            sql = row.get("sql")
            if not sql:
                raise AssertionError(f"missing sql for {name}")
            conn.execute(str(sql))


def _open(path: Path) -> sqlite3.Connection:
    """Open an autocommit connection.

    Args:
        path: SQLite file.

    Returns:
        Connection with a row factory.
    """
    conn = sqlite3.connect(path)
    conn.isolation_level = None
    conn.row_factory = sqlite3.Row
    return conn


def _seed(conn: sqlite3.Connection) -> None:
    """Insert synthetic attendance, catch-up, answers, and grants.

    Args:
        conn: Connection already holding the deployed schema.
    """
    key = celebration_fp_key(SECRET)
    kid = celebration_key_id(key)
    match = student_fp(key, 1, 1, "syn-1")
    past = student_fp(key, 1, 50, "gone")
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO classes (id, year, semester, course_code, days, time, created_at) "
        "VALUES (1, '2026/27', 'Semester 1', 'MCF3M', 'M/W/F', '2:00pm', 't0')"
    )
    conn.executemany(
        """
        INSERT INTO students (
            id, class_id, canvas_id, last_display, first_name, codename,
            pin_hash, default_pin_ciphertext, pin_set_by, is_test_student
        ) VALUES (?, 1, ?, 'Row', ?, ?, ?, ?, 'teacher', ?)
        """,
        [
            (1, "syn-1", "One", "One", "h1", "c1", 0),
            (2, "syn-2", "Two", "Two", "h2", "c2", 1),
        ],
    )
    conn.execute(
        "INSERT INTO sessions (id, class_id, starts_at, header_label, status) "
        "VALUES (1, 1, 't1', 'label', 'ended')"
    )
    conn.execute(
        "INSERT INTO session_scores (session_id, student_id, present, points, late) "
        "VALUES (1, 1, 1, 3.5, 1)"
    )
    conn.execute(
        "INSERT INTO live_participation_credits (run_key, student_id, session_id, points) "
        "VALUES ('run-1', 1, 1, 2)"
    )
    conn.execute(
        """
        INSERT INTO participation_answers (
            id, class_id, run_key, question_key, student_id, answer_json, answered_at
        ) VALUES (1, 1, 'run-1', 'q1', 1, '{"pick":1}', 't1')
        """
    )
    conn.execute(
        """
        INSERT INTO catchup_links (id, token, class_id, run_key, created_at, updated_at)
        VALUES (1, 'tok-1', 1, 'catch-1', 't1', 't1')
        """
    )
    conn.execute(
        "INSERT INTO catchup_pin_failures (id, class_id, student_id, failed_at) "
        "VALUES (1, 1, 1, 1.5)"
    )
    conn.execute(
        "INSERT INTO catchup_pin_unlocks (class_id, student_id, unlocked_at) "
        "VALUES (1, 2, 't2')"
    )
    conn.execute(
        """
        INSERT INTO student_moods (
            id, class_id, student_id, meeting_date, mood, updated_at
        ) VALUES (1, 1, 1, '2026-11-06', 'good', 't1')
        """
    )
    conn.executemany(
        """
        INSERT INTO avatar_reward_grants (
            id, class_id, student_id, source, source_ref, status, avatar_key,
            created_at, claimed_at
        ) VALUES (?, 1, ?, ?, ?, ?, ?, 't1', ?)
        """,
        [
            (1, 1, "engaged", "engaged:s", "pending", None, None),
            (2, 1, "mvp", "mvp:s", "claimed", "avatar-1", "t2"),
            (3, 2, "shoutout", "shoutout:s", "pending", None, None),
        ],
    )
    conn.execute(
        """
        INSERT INTO live_class_sessions (
            id, class_id, offering_id, session_code, status, started_at
        ) VALUES (1, 1, 1, 'SYN', 'ended', 't1')
        """
    )
    conn.execute(
        "INSERT INTO live_session_prompts (id, live_session_id, created_at, updated_at) "
        "VALUES (1, 1, 't1', 't1')"
    )
    conn.execute(
        """
        INSERT INTO live_session_responses (
            id, prompt_id, student_id, response_json, created_at, updated_at
        ) VALUES (1, 1, 1, '{"a":1}', 't1', 't1')
        """
    )
    conn.execute(
        "INSERT INTO questions (id, bank_id, import_key, title, created_at) "
        "VALUES (11, 50, 'q', 'Q', 't1')"
    )
    conn.execute(
        "INSERT INTO module_items (id, outline_id, import_key, title, component_type, created_at) "
        "VALUES (21, 80, 'm', 'M', 'page', 't1')"
    )
    snapshot = {
        "version": 3,
        "kid": kid,
        "cards": [
            {
                "course": "MCF3M",
                "students": [
                    {"class_id": 1, "student_id": 1, "fp": match},
                    {"class_id": 1, "student_id": 50, "fp": past},
                ],
            }
        ],
    }
    held = {
        "version": 1,
        "semester_id": "s",
        "kid": kid,
        "cards": [
            {
                "course": "MCF3M",
                "students": [{"class_id": 1, "student_id": 50, "fp": past}],
            }
        ],
    }
    for key_name, body in (
        ("celebration_public_snapshot", snapshot),
        ("celebration_held_cards", held),
    ):
        conn.execute(
            "INSERT INTO school_settings (key, value, updated_at) VALUES (?, ?, 't1')",
            (key_name, json.dumps(body)),
        )
    conn.execute("INSERT INTO student_id_seq (id, next_id) VALUES (1, 3)")
    conn.execute("PRAGMA foreign_keys = ON")


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Row counts for the rehearsed tables.

    Args:
        conn: Open connection.

    Returns:
        Table name to count.
    """
    names = (
        "students",
        "session_scores",
        "live_participation_credits",
        "participation_answers",
        "catchup_links",
        "catchup_pin_failures",
        "catchup_pin_unlocks",
        "student_moods",
        "avatar_reward_grants",
        "live_session_responses",
        "questions",
        "module_items",
    )
    return {
        name: int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
        for name in names
    }



def _dump(conn: sqlite3.Connection, table: str) -> tuple[list[str], list[tuple]]:
    """Every column of ``table``, ordered by its primary key.

    Args:
        conn: Open connection.
        table: Table name from this rehearsal.

    Returns:
        Column names and rows. Values are not logged by the caller.
    """
    info = list(conn.execute(f"PRAGMA table_info({table})"))
    cols = [str(row[1]) for row in info]
    order = [str(row[1]) for row in info if int(row[5])] or cols
    quoted = ", ".join(f'"{name}"' for name in cols)
    ordering = ", ".join(f'"{name}"' for name in order)
    rows = [
        tuple(row)
        for row in conn.execute(f"SELECT {quoted} FROM {table} ORDER BY {ordering}")
    ]
    return cols, rows


def _without(cols: list[str], rows: list[tuple], drop: set[str]) -> list[tuple]:
    """Drop named columns from dumped rows.

    Args:
        cols: Column names aligned with each row.
        rows: Dumped rows.
        drop: Columns that the migration is allowed to change.

    Returns:
        Rows with those columns removed.
    """
    keep = [index for index, name in enumerate(cols) if name not in drop]
    return [tuple(row[index] for index in keep) for row in rows]


_RETAINED = (
    "classes",
    "sessions",
    "session_scores",
    "live_participation_credits",
    "participation_answers",
    "catchup_links",
    "catchup_pin_failures",
    "catchup_pin_unlocks",
    "student_moods",
    "avatar_reward_grants",
    "live_class_sessions",
    "live_session_prompts",
    "live_session_responses",
    "questions",
    "module_items",
    "student_id_seq",
)


class DeployedSchemaRehearsalTests(unittest.TestCase):
    """Synthetic rows on the captured CREATE statements."""

    def setUp(self) -> None:
        """Load the fixture once per test."""
        self.payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "school.sqlite"
        self.conn = _open(self.path)
        self.addCleanup(self.conn.close)
        _load(self.conn, self.payload)
        _seed(self.conn)

    def test_exact_students_ddl_is_supported_and_keeps_partial_uniques(self) -> None:
        """``UNIQUE(class_id, canvas_id)`` is the legacy key. Other uniques stay."""
        sql = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'students'"
        ).fetchone()[0]
        self.assertIn("UNIQUE(class_id, canvas_id)", sql)
        self.assertIsNone(_unsupported_students_ddl(self.conn))
        before_fk = _fk_violation_set(self.conn)
        self.assertEqual(
            before_fk,
            (
                ("module_items", 21, "module_outlines", 0),
                ("questions", 11, "question_banks", 0),
            ),
        )
        before_counts = _counts(self.conn)
        retained = {name: _dump(self.conn, name) for name in _RETAINED}
        student_cols, student_rows = _dump(self.conn, "students")
        students_kept = _without(student_cols, student_rows, {"canvas_id", "row_uid"})
        settings_cols, settings_rows = _dump(self.conn, "school_settings")
        past = json.loads(
            self.conn.execute(
                "SELECT value FROM school_settings WHERE key = 'celebration_held_cards'"
            ).fetchone()[0]
        )["cards"][0]["students"][0]["fp"]
        uploads = Path(self.tmp.name) / "uploads"
        uploads.mkdir()
        (uploads / "class-1.csv").write_text("legacy\n", encoding="utf-8")
        library = Path(self.tmp.name) / "libraries" / "9"
        library.mkdir(parents=True)
        (library / "keep.bin").write_bytes(b"pack")
        result = migrate_students_off_canvas_id(
            self.conn, uploads_dir=uploads, secret=SECRET
        )
        self.assertEqual(result["status"], "applied")
        self.assertGreaterEqual(int(result["fingerprints_rewritten"]), 1)
        self.assertGreaterEqual(int(result["fingerprints_orphaned"]), 1)
        self.assertEqual(_fk_violation_set(self.conn), before_fk)
        self.assertEqual(_counts(self.conn), before_counts)
        for name, (cols, rows) in retained.items():
            after_cols, after_rows = _dump(self.conn, name)
            self.assertEqual(after_cols, cols, name)
            self.assertEqual(after_rows, rows, name)
        after_student_cols, after_student_rows = _dump(self.conn, "students")
        self.assertEqual(
            _without(after_student_cols, after_student_rows, {"canvas_id", "row_uid"}),
            students_kept,
        )
        uid_index = after_student_cols.index("row_uid")
        uids = [row[uid_index] for row in after_student_rows]
        self.assertEqual(len(uids), len(set(uids)))
        for uid in uids:
            self.assertRegex(str(uid), r"^[0-9a-f]{32}$")
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(students)")}
        self.assertNotIn("canvas_id", cols)
        self.assertIn("row_uid", cols)
        self.assertIsNone(_unsupported_students_ddl(self.conn))
        names = {
            str(row[1])
            for row in self.conn.execute("PRAGMA index_list(students)")
        }
        self.assertIn("idx_students_class", names)
        self.assertIn("idx_students_class_codename", names)
        self.assertIn("uq_students_one_test_student", names)
        held = json.loads(
            self.conn.execute(
                "SELECT value FROM school_settings WHERE key = 'celebration_held_cards'"
            ).fetchone()[0]
        )
        self.assertEqual(held["cards"][0]["students"][0]["fp"], past)
        key_index = settings_cols.index("key")
        value_index = settings_cols.index("value")
        after_settings_cols, after_settings_rows = _dump(self.conn, "school_settings")
        self.assertEqual(after_settings_cols, settings_cols)
        changed = {"celebration_public_snapshot", "celebration_held_cards"}
        for before_row, after_row in zip(settings_rows, after_settings_rows):
            self.assertEqual(before_row[key_index], after_row[key_index])
            if before_row[key_index] not in changed:
                self.assertEqual(after_row, before_row)
        self.assertFalse((uploads / "class-1.csv").exists())
        self.assertEqual((library / "keep.bin").read_bytes(), b"pack")
        uids = [
            str(row[0])
            for row in self.conn.execute("SELECT row_uid FROM students ORDER BY id")
        ]
        again = migrate_students_off_canvas_id(
            self.conn, uploads_dir=uploads, secret=SECRET
        )
        self.assertEqual(again["status"], "noop")
        self.assertEqual(
            [
                str(row[0])
                for row in self.conn.execute("SELECT row_uid FROM students ORDER BY id")
            ],
            uids,
        )
        self.assertEqual(_fk_violation_set(self.conn), before_fk)
        invariants = student_uid_invariants(self.conn)
        self.assertTrue(student_uid_ready(again, invariants))

    def test_failpoint_rolls_back_rewards_and_catchup(self) -> None:
        """An interrupted drop keeps grants, catch-up rows, and the CSV."""
        from student_uid_migration import StudentUidMigrationInterrupted

        before = _counts(self.conn)
        before_fk = _fk_violation_set(self.conn)
        retained = {name: _dump(self.conn, name) for name in ("students", *_RETAINED, "school_settings")}
        uploads = Path(self.tmp.name) / "uploads"
        uploads.mkdir()
        (uploads / "class-3.csv").write_text("legacy\n", encoding="utf-8")
        with self.assertRaises(StudentUidMigrationInterrupted):
            migrate_students_off_canvas_id(
                self.conn,
                uploads_dir=uploads,
                secret=SECRET,
                failpoint="after_drop",
            )
        self.assertEqual(_counts(self.conn), before)
        self.assertEqual(_fk_violation_set(self.conn), before_fk)
        for name, (cols, rows) in retained.items():
            after_cols, after_rows = _dump(self.conn, name)
            self.assertEqual(after_cols, cols, name)
            self.assertEqual(after_rows, rows, name)
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(students)")}
        self.assertIn("canvas_id", cols)
        self.assertEqual(
            int(
                self.conn.execute(
                    "SELECT COUNT(*) FROM avatar_reward_grants WHERE status = 'claimed'"
                ).fetchone()[0]
            ),
            1,
        )
        self.assertEqual(
            int(
                self.conn.execute("SELECT COUNT(*) FROM catchup_pin_unlocks").fetchone()[0]
            ),
            1,
        )
        self.assertTrue((uploads / "class-3.csv").is_file())


class ConstraintAndFkTests(unittest.TestCase):
    """Unsupported DDL is blocked. Equal counts are not equal sets."""

    def test_table_unique_without_the_legacy_column_blocks(self) -> None:
        """A UNIQUE list that does not include ``canvas_id`` is not rebuilt."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = _open(Path(tmp.name) / "school.sqlite")
        self.addCleanup(conn.close)
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.executescript(
            """
            CREATE TABLE classes (id INTEGER PRIMARY KEY, course_code TEXT NOT NULL);
            CREATE TABLE students (
                id INTEGER PRIMARY KEY,
                class_id INTEGER NOT NULL REFERENCES classes(id),
                canvas_id TEXT NOT NULL,
                last_display TEXT NOT NULL,
                first_name TEXT NOT NULL,
                UNIQUE(class_id, first_name)
            );
            INSERT INTO classes (id, course_code) VALUES (1, 'MCF3M');
            INSERT INTO students (id, class_id, canvas_id, last_display, first_name)
            VALUES (1, 1, 'syn', 'Row', 'One');
            """
        )
        conn.execute("PRAGMA foreign_keys = ON")
        result = migrate_students_off_canvas_id(conn, secret=SECRET)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "unsupported_students_ddl:table_unique")
        cols = {row[1] for row in conn.execute("PRAGMA table_info(students)")}
        self.assertIn("canvas_id", cols)

    def test_check_autoincrement_collation_and_deferred_fk_block(self) -> None:
        """Each unsupported token blocks and leaves the row in place."""
        statements = {
            "check": "id INTEGER PRIMARY KEY CHECK (id > 0)",
            "autoincrement": "id INTEGER PRIMARY KEY AUTOINCREMENT",
            "collation": "id INTEGER PRIMARY KEY, last_display TEXT NOT NULL COLLATE NOCASE",
            "deferred_foreign_key": (
                "id INTEGER PRIMARY KEY, class_id INTEGER REFERENCES classes(id) "
                "DEFERRABLE INITIALLY DEFERRED"
            ),
        }
        for reason, column_sql in statements.items():
            with self.subTest(reason=reason):
                tmp = tempfile.TemporaryDirectory()
                self.addCleanup(tmp.cleanup)
                conn = _open(Path(tmp.name) / "school.sqlite")
                self.addCleanup(conn.close)
                conn.execute("CREATE TABLE classes (id INTEGER PRIMARY KEY)")
                conn.execute(
                    f"CREATE TABLE students ({column_sql}, canvas_id TEXT NOT NULL, "
                    "first_name TEXT NOT NULL)"
                )
                conn.execute("INSERT INTO classes (id) VALUES (1)")
                columns = {row[1] for row in conn.execute("PRAGMA table_info(students)")}
                if "last_display" in columns and "class_id" in columns:
                    conn.execute(
                        "INSERT INTO students (id, class_id, canvas_id, last_display, first_name) "
                        "VALUES (1, 1, 'syn', 'Row', 'One')"
                    )
                elif "last_display" in columns:
                    conn.execute(
                        "INSERT INTO students (id, canvas_id, last_display, first_name) "
                        "VALUES (1, 'syn', 'Row', 'One')"
                    )
                else:
                    conn.execute(
                        "INSERT INTO students (id, canvas_id, first_name) VALUES (1, 'syn', 'One')"
                    )
                result = migrate_students_off_canvas_id(conn, secret=SECRET)
                self.assertEqual(result["status"], "blocked")
                self.assertTrue(str(result["reason"]).endswith(reason.replace("deferred_foreign_key", "deferred_foreign_key")))
                self.assertIn(reason if reason != "deferred_foreign_key" else "deferred_foreign_key", result["reason"])
                self.assertIn(
                    "canvas_id",
                    {row[1] for row in conn.execute("PRAGMA table_info(students)")},
                )

    def test_equal_count_different_row_blocks_from_pragma(self) -> None:
        """Moving an orphan to a new rowid is a new violation at the same count."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = _open(Path(tmp.name) / "school.sqlite")
        self.addCleanup(conn.close)
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.executescript(
            """
            CREATE TABLE question_banks (id INTEGER PRIMARY KEY);
            CREATE TABLE questions (
                id INTEGER PRIMARY KEY,
                bank_id INTEGER NOT NULL REFERENCES question_banks(id)
            );
            INSERT INTO questions (id, bank_id) VALUES (11, 50);
            INSERT INTO questions (id, bank_id) VALUES (12, 51);
            """
        )
        before = _fk_violation_set(conn)
        conn.execute("DELETE FROM questions WHERE id = 12")
        conn.execute("INSERT INTO questions (id, bank_id) VALUES (99, 51)")
        after = _fk_violation_set(conn)
        self.assertEqual(len(before), len(after))
        self.assertNotEqual(before, after)
        with self.assertRaises(_Blocked) as caught:
            _require_same_fk_violations(before, after)
        self.assertEqual(caught.exception.reason, "foreign_key_check_failed")


class PeakAndProbeTests(unittest.TestCase):
    """dbstat rewrite size, and the probe refuses a host /data mount."""

    def test_peak_ceiling_is_not_the_old_students_size(self) -> None:
        """Old dbstat bytes are not the size of the rebuilt table.

        The ceiling allows every existing page to be rewritten and adds
        24 bytes of WAL frame overhead per page. It does not include
        startup writers or a second copy of ``libraries/``. A synthetic
        rehearsal measurement is not production capacity proof.
        """
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        page_count = int(payload["pragma"]["page_count"])
        page_size = int(payload["pragma"]["page_size"])
        self.assertEqual(page_count * page_size, DB_BYTES)
        frame_overhead = page_count * 24
        ceiling = sqlite_rebuild_peak_bytes(DB_BYTES, WAL_BYTES, None, page_count=page_count)
        self.assertTrue(ceiling["ceiling"])
        self.assertEqual(
            ceiling["peak_bytes"],
            DB_BYTES + WAL_BYTES + DB_BYTES + frame_overhead,
        )
        self.assertLess(int(ceiling["with_same_disk_backup_bytes"]), FREE_BYTES)
        by_name = {row["name"]: int(row["bytes"]) for row in payload["dbstat_by_object"]}
        old_students = by_name["students"] + by_name["sqlite_autoindex_students_1"]
        old_students += by_name["idx_students_class"]
        old_students += by_name["idx_students_class_codename"]
        old_students += by_name["uq_students_one_test_student"]
        self.assertLess(old_students, int(ceiling["peak_bytes"]))
        self.assertNotEqual(int(ceiling["rewritten_bytes"]), old_students)

    def test_probe_refuses_host_data_and_allows_a_private_tmpfs(self) -> None:
        """Importing the probe does not open a database or a socket."""
        host = "26 1 8:1 / /data rw,relatime - ext4 /dev/vda1 rw\n"
        private = "26 1 0:0 / /data rw - tmpfs tmpfs rw\n"
        self.assertEqual(data_mount_fstype(host), "ext4")
        self.assertEqual(data_mount_fstype(private), "tmpfs")
        self.assertIsNotNone(refuse_reason(Path("/data/lloves.sqlite"), host))
        self.assertIsNone(refuse_reason(Path("/data/lloves.sqlite"), private))
        self.assertIsNone(refuse_reason(Path("/tmp/restored.sqlite"), host))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "empty.sqlite"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE students (id INTEGER PRIMARY KEY, row_uid TEXT)")
        conn.commit()
        conn.close()
        mount = Path(tmp.name) / "mountinfo"
        mount.write_text(host, encoding="utf-8")
        code = probe_main([str(path), "--mountinfo", str(mount)])
        self.assertEqual(code, 0)


class ReadinessTests(unittest.TestCase):
    """A blocked retry does not serve a healthy writable app."""

    def test_fresh_app_is_ready_and_a_blocked_file_is_not(self) -> None:
        """Health is 503 and posts are refused when the drop cannot finish."""
        from app import create_app

        fresh_dir = tempfile.TemporaryDirectory()
        self.addCleanup(fresh_dir.cleanup)
        fresh = create_app(
            db_path=Path(fresh_dir.name) / "lloves.sqlite",
            data_dir=Path(fresh_dir.name),
            testing=True,
            live_database_url="",
        )
        self.addCleanup(fresh.config["SCHOOL_DB"].close)
        client = fresh.test_client()
        health = client.get("/health")
        self.assertEqual(health.status_code, 200, health.get_json())
        self.assertTrue(health.get_json()["ok"])
        self.assertIn(health.get_json()["student_uid_migration"], {"applied", "noop"})
        library = Path(fresh_dir.name) / "libraries" / "9" / "keep.bin"
        self.assertFalse(library.exists())

        blocked_dir = tempfile.TemporaryDirectory()
        self.addCleanup(blocked_dir.cleanup)
        path = Path(blocked_dir.name) / "lloves.sqlite"
        prepared = create_app(
            db_path=path,
            data_dir=Path(blocked_dir.name),
            testing=True,
            live_database_url="",
        )
        game = prepared.config["SCHOOL_DB"].game
        game.conn.execute("PRAGMA foreign_keys = OFF")
        game.conn.execute("PRAGMA foreign_keys = OFF")
        game.conn.execute(
            "ALTER TABLE students ADD COLUMN canvas_id TEXT NOT NULL DEFAULT ''"
        )
        game.conn.execute("UPDATE students SET canvas_id = 'syn-' || id")
        class_row = game.conn.execute("SELECT id FROM classes LIMIT 1").fetchone()
        if class_row is None:
            game.conn.execute(
                "INSERT INTO classes (year, semester, course_code, days, time, created_at) "
                "VALUES ('2026/27', 'Semester 1', 'MCF3M', 'M/W/F', '2:00pm', 't')"
            )
            class_id = int(game.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        else:
            class_id = int(class_row[0])
        game.conn.execute(
            "INSERT INTO sessions (class_id, starts_at, header_label, status) "
            "VALUES (?, 't', 'label', 'ended')",
            (class_id,),
        )
        session_id = int(game.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        game.conn.execute(
            "INSERT INTO session_scores (session_id, student_id, present, points) "
            "VALUES (?, 999, 1, 0)",
            (session_id,),
        )
        game.conn.commit()
        prepared.config["SCHOOL_DB"].close()
        (Path(blocked_dir.name) / "libraries" / "9").mkdir(parents=True)
        (Path(blocked_dir.name) / "libraries" / "9" / "keep.bin").write_bytes(b"pack")
        (Path(blocked_dir.name) / "live-question-images" / "1").mkdir(parents=True)
        (Path(blocked_dir.name) / "live-question-images" / "1" / "page.png").write_bytes(b"png")
        blocked = create_app(
            db_path=path,
            data_dir=Path(blocked_dir.name),
            testing=True,
            live_database_url="",
        )
        self.addCleanup(blocked.config["SCHOOL_DB"].close)
        self.assertFalse(blocked.config["STUDENT_UID_READY"])
        client = blocked.test_client()
        health = client.get("/health")
        self.assertEqual(health.status_code, 503)
        self.assertFalse(health.get_json()["ok"])
        self.assertEqual(health.get_json()["student_uid_migration"], "blocked")
        posted = client.post("/api/classes/1/game/timer/stop", json={})
        self.assertEqual(posted.status_code, 503)
        self.assertFalse(posted.get_json()["ok"])
        check = sqlite3.connect(path)
        cols = {row[1] for row in check.execute("PRAGMA table_info(students)")}
        self.assertIn("canvas_id", cols)
        orphan = check.execute(
            "SELECT present FROM session_scores WHERE student_id = 999"
        ).fetchone()
        self.assertIsNotNone(orphan)
        check.close()
        self.assertEqual(
            (Path(blocked_dir.name) / "libraries" / "9" / "keep.bin").read_bytes(),
            b"pack",
        )
        self.assertEqual(
            (Path(blocked_dir.name) / "live-question-images" / "1" / "page.png").read_bytes(),
            b"png",
        )

    def test_startup_sweep_removes_only_stale_tmp_and_class_csv(self) -> None:
        """A young conversion dir and a library file survive boot."""
        from app import create_app

        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        data = Path(root.name)
        (data / "uploads").mkdir()
        (data / "uploads" / "class-4.csv").write_text("legacy\n", encoding="utf-8")
        (data / "uploads" / "notes.txt").write_text("keep\n", encoding="utf-8")
        (data / "libraries" / "3").mkdir(parents=True)
        (data / "libraries" / "3" / "keep.bin").write_bytes(b"pack")
        stale = Path(tempfile.gettempdir()) / f"lloves-slides-rehearsal-{os.getpid()}"
        fresh = Path(tempfile.gettempdir()) / f"lloves-slides-live-{os.getpid()}"
        stale.mkdir()
        fresh.mkdir()
        self.addCleanup(lambda: shutil.rmtree(stale, ignore_errors=True))
        self.addCleanup(lambda: shutil.rmtree(fresh, ignore_errors=True))
        old = time.time() - 1000
        os.utime(stale, (old, old))
        app = create_app(
            db_path=data / "lloves.sqlite",
            data_dir=data,
            testing=True,
            live_database_url="",
        )
        self.addCleanup(app.config["SCHOOL_DB"].close)
        self.assertTrue((data / "logs").is_dir())
        self.assertTrue((data / "uploads").is_dir())
        self.assertTrue((data / "lloves.sqlite").is_file())
        self.assertTrue((data / "lloves.sqlite.boot.lock").is_file())
        self.assertFalse((data / "uploads" / "class-4.csv").exists())
        self.assertTrue((data / "uploads" / "notes.txt").is_file())
        self.assertEqual((data / "libraries" / "3" / "keep.bin").read_bytes(), b"pack")
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.is_dir())


if __name__ == "__main__":
    unittest.main()


class MalformedNoopTests(unittest.TestCase):
    """A no-op must not accept a preexisting uid that is not a real one."""

    def test_malformed_noop_blocks_without_rewriting(self) -> None:
        """Nullable, blank, duplicate, non-unique, and wrong-shaped uids stay put."""
        valid = "a" * 32
        other = "b" * 32
        cases = (
            ("nullable", "row_uid TEXT", None, "row_uid_malformed"),
            ("blank", "row_uid TEXT NOT NULL", "", "row_uid_malformed"),
            ("bad_shape", "row_uid TEXT NOT NULL", "not-a-uid", "row_uid_malformed"),
            ("duplicate", "row_uid TEXT NOT NULL", valid, "row_uid_duplicate"),
            ("not_unique", "row_uid TEXT NOT NULL", valid, "row_uid_not_unique"),
            ("constraint_nullable", "row_uid TEXT", valid, "row_uid_nullable"),
        )
        for name, column_sql, value, reason in cases:
            with self.subTest(name=name):
                tmp = tempfile.TemporaryDirectory()
                self.addCleanup(tmp.cleanup)
                conn = _open(Path(tmp.name) / "school.sqlite")
                self.addCleanup(conn.close)
                unique = "" if name in {"not_unique", "duplicate", "nullable", "blank", "bad_shape", "constraint_nullable"} else ""
                if name == "not_unique":
                    suffix = ""
                elif "NOT NULL" in column_sql and name not in {"duplicate", "not_unique"}:
                    suffix = " UNIQUE"
                else:
                    suffix = ""
                if name == "constraint_nullable":
                    suffix = ""
                conn.execute(
                    f"CREATE TABLE students (id INTEGER PRIMARY KEY, {column_sql}{suffix})"
                )
                if name == "duplicate":
                    conn.execute("INSERT INTO students (id, row_uid) VALUES (1, ?)", (valid,))
                    conn.execute("INSERT INTO students (id, row_uid) VALUES (2, ?)", (valid,))
                elif name == "not_unique":
                    conn.execute("INSERT INTO students (id, row_uid) VALUES (1, ?)", (valid,))
                    conn.execute("INSERT INTO students (id, row_uid) VALUES (2, ?)", (other,))
                else:
                    conn.execute("INSERT INTO students (id, row_uid) VALUES (1, ?)", (value,))
                before = list(conn.execute("SELECT id, row_uid FROM students ORDER BY id"))
                result = migrate_students_off_canvas_id(conn, secret=SECRET)
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["reason"], reason)
                after = list(conn.execute("SELECT id, row_uid FROM students ORDER BY id"))
                self.assertEqual(after, before)
                ledger = conn.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'lms_data_migrations'"
                ).fetchone()
                if ledger is not None:
                    row = conn.execute(
                        "SELECT 1 FROM lms_data_migrations WHERE migration_key = 'mck365_drop_canvas_id_v1'"
                    ).fetchone()
                    self.assertIsNone(row)
                self.assertFalse(student_uid_ready(result, student_uid_invariants(conn)))

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = _open(Path(tmp.name) / "school.sqlite")
        self.addCleanup(conn.close)
        conn.execute(
            "CREATE TABLE students (id INTEGER PRIMARY KEY, row_uid TEXT NOT NULL UNIQUE)"
        )
        conn.execute("INSERT INTO students (id, row_uid) VALUES (1, ?)", (valid,))
        result = migrate_students_off_canvas_id(conn, secret=SECRET)
        self.assertEqual(result["status"], "noop")
        self.assertTrue(student_uid_ready(result, student_uid_invariants(conn)))

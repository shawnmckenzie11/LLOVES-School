#!/usr/bin/env python3
"""Focused tests for normalized course components and blob deduplication."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

from content_store import ContentBlobStore, blob_path  # noqa: E402
from school_db import LovesDB  # noqa: E402


class ContentStoreTests(unittest.TestCase):
    """Shared blob and component schema behavior."""

    def setUp(self) -> None:
        """Create an isolated school database and data volume."""
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = LovesDB(self.root / "lloves.sqlite")
        self.store = ContentBlobStore(self.root, self.db)
        self.library = self.db.create_library("SBI3U", origin="upload")

    def tearDown(self) -> None:
        """Close sqlite and remove temporary files."""
        self.db.close()
        self.tmp.cleanup()

    def test_component_tables_exist(self) -> None:
        """A new and migrated DB contains every normalized component table."""
        names = {
            row["name"]
            for row in self.db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertTrue(
            {
                "blobs",
                "pages",
                "assignments",
                "quizzes",
                "question_banks",
                "questions",
                "module_outlines",
                "module_items",
            }.issubset(names)
        )

    def test_same_bytes_share_one_file_and_row(self) -> None:
        """Identical content is stored once even with different filenames."""
        first = self.store.put_bytes(b"same", filename="page.html")
        second = self.store.put_bytes(b"same", filename="copy.txt")
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(first.path, second.path)
        self.assertEqual(first.path, blob_path(self.root, first.sha256))
        count = self.db.conn.execute(
            "SELECT COUNT(*) AS n FROM blobs"
        ).fetchone()["n"]
        self.assertEqual(count, 1)
        self.assertEqual(first.bytes, 4)
        self.assertEqual(first.mime, "text/html")

    def test_different_bytes_get_different_blobs(self) -> None:
        """Different payloads receive separate digest paths and metadata."""
        first = self.store.put_bytes(b"one", filename="one.pdf")
        second = self.store.put_bytes(b"two", filename="two.pdf")
        self.assertNotEqual(first.sha256, second.sha256)
        self.assertTrue(first.path.is_file())
        self.assertTrue(second.path.is_file())
        self.assertEqual(self.db.get_blob(first.sha256)["mime"], "application/pdf")

    def test_library_delete_cascades_components_not_blob(self) -> None:
        """Removing library metadata does not remove shared blob bytes."""
        stored = self.store.put_bytes(b"shared PDF", filename="lesson.pdf")
        library_id = int(self.library["id"])
        self.db.conn.execute(
            """
            INSERT INTO pages (
                library_id, import_key, kind, title, blob_sha, created_at
            ) VALUES (?, 'page-1', 'pdf', 'Lesson', ?, 'now')
            """,
            (library_id, stored.sha256),
        )
        self.db.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.conn.execute(
                """
                INSERT INTO pages (
                    library_id, import_key, kind, title, created_at
                ) VALUES (?, 'page-1', 'html', 'Duplicate', 'now')
                """,
                (library_id,),
            )
        self.db.conn.rollback()
        surviving = self.db.conn.execute(
            "SELECT COUNT(*) AS n FROM pages WHERE library_id = ?", (library_id,)
        ).fetchone()["n"]
        self.assertEqual(surviving, 1)
        self.db.conn.execute(
            "DELETE FROM content_libraries WHERE id = ?", (library_id,)
        )
        self.db.conn.commit()
        pages = self.db.conn.execute(
            "SELECT COUNT(*) AS n FROM pages WHERE library_id = ?", (library_id,)
        ).fetchone()["n"]
        self.assertEqual(pages, 0)
        self.assertIsNotNone(self.db.get_blob(stored.sha256))
        self.assertTrue(stored.path.is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""MCK-320 repair: choice images stored on a stem are listed, then removable."""

from __future__ import annotations

import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))

from repair_choice_stem_images import (  # noqa: E402
    apply_choice_stem_image_repairs,
    format_choice_stem_image_report,
    plan_choice_stem_image_repairs,
)

ANSWER = "https://cdn.example/answer-5-3.png"
MIRROR = "web_resources/bank-mirror/abc123.png"
GRAPH = "/static/bank-graphs/parabola-grid.svg"


class _Db:
    """Minimal school stand-in: a connection and the write lock."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._lock = threading.Lock()


def _load_cli():
    """Load the repair CLI without running it."""
    path = REPO_ROOT / "scripts" / "repair_mck320_choice_stem_images.py"
    spec = importlib.util.spec_from_file_location("repair_mck320_cli", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _school() -> _Db:
    """Empty sqlite with question rows, libraries, and one untouched side table."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE content_libraries (
            id INTEGER PRIMARY KEY,
            ontario_code TEXT NOT NULL,
            origin TEXT NOT NULL
        );
        CREATE TABLE question_banks (
            id INTEGER PRIMARY KEY,
            library_id INTEGER NOT NULL,
            title TEXT NOT NULL
        );
        CREATE TABLE questions (
            id INTEGER PRIMARY KEY,
            bank_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE course_offerings (
            id INTEGER PRIMARY KEY,
            teacher_user_id INTEGER,
            library_id INTEGER,
            shell_library_id INTEGER
        );
        CREATE TABLE class_live_playlist_placements (
            id INTEGER PRIMARY KEY,
            item_json TEXT NOT NULL,
            source_question_id INTEGER
        );
        CREATE TABLE bank_image_cache (
            source_url TEXT NOT NULL,
            relpath TEXT NOT NULL
        );
        """
    )
    return _Db(conn)


def _leaked_payload() -> dict:
    """One stem that still shows the correct-choice image."""
    return {
        "stem_html": (
            f'<img src="{ANSWER}" alt="five cubed">'
            "<p>Simplify (5<sup>-4</sup>)(5<sup>7</sup>).</p>"
        ),
        "choices": [
            {"id": "a", "html": f'<img src="{ANSWER}" alt="five cubed">', "correct": True},
            {"id": "b", "html": "11", "correct": False},
        ],
    }


def _healthy_payload() -> dict:
    """A graph that is not a choice image."""
    return {
        "stem_html": "<p>What is the vertex?</p>",
        "image_url": GRAPH,
        "choices": [
            {"id": "a", "html": "(0, c)", "correct": True},
            {"id": "b", "html": "(h, k)", "correct": False},
        ],
    }


class ChoiceStemImageRepairTests(unittest.TestCase):
    """Dry-run lists the leak; apply is idempotent; production stays gated."""

    def _seed_libraries(self, school: _Db) -> None:
        """One shared pack and one single-teacher shell."""
        school.conn.executemany(
            "INSERT INTO content_libraries (id, ontario_code, origin) VALUES (?, ?, ?)",
            [(4, "MCF3M", "upload"), (9, "MCF3M", "shell")],
        )
        school.conn.executemany(
            "INSERT INTO question_banks (id, library_id, title) VALUES (?, ?, ?)",
            [(40, 4, "M4"), (90, 9, "Shell")],
        )
        school.conn.executemany(
            "INSERT INTO course_offerings (id, teacher_user_id, library_id, shell_library_id) "
            "VALUES (?, ?, ?, ?)",
            [(1, 101, 4, None), (2, 102, 4, None), (3, 101, None, 9)],
        )

    def test_dry_run_lists_playlist_heroes_not_authored_stems(self) -> None:
        """Authored stem images stay. A promoted playlist hero is listed."""
        school = _school()
        self._seed_libraries(school)
        authored = json.dumps(_leaked_payload())
        school.conn.execute(
            "INSERT INTO questions (id, bank_id, title, payload_json, created_at) "
            "VALUES (7, 40, 'Triangle', ?, 't0')",
            (authored,),
        )
        school.conn.execute(
            "INSERT INTO questions (id, bank_id, title, payload_json, created_at) "
            "VALUES (8, 40, 'Vertex', ?, 't0')",
            (json.dumps(_healthy_payload()),),
        )
        card = json.dumps(
            {
                "text_html": "<p>Simplify (5<sup>-4</sup>)(5<sup>7</sup>).</p>",
                "image_url": ANSWER,
                "options_html": [f'<img src="{ANSWER}">', "11"],
            }
        )
        school.conn.execute(
            "INSERT INTO class_live_playlist_placements (id, item_json, source_question_id) "
            "VALUES (3, ?, 7)",
            (card,),
        )
        school.conn.commit()

        planned = plan_choice_stem_image_repairs(school)
        report = "\n".join(format_choice_stem_image_report(planned, dry_run=True))
        self.assertIn("DRY RUN", report)
        self.assertNotIn(ANSWER, report)
        self.assertNotIn("Simplify", report)
        self.assertIn("count questions=0", report)
        self.assertIn("count class_live_playlist_placements=1", report)
        self.assertIn(
            "class_live_playlist_placements row=3 question_id=7 "
            "library_id=4 shared=yes fields=image_url",
            report,
        )
        self.assertEqual(
            school.conn.execute(
                "SELECT payload_json FROM questions WHERE id = 7"
            ).fetchone()[0],
            authored,
        )

    def test_second_run_changes_zero_rows(self) -> None:
        """Apply clears one playlist hero, then a second run writes nothing."""
        school = _school()
        self._seed_libraries(school)
        authored = json.dumps(_leaked_payload())
        healthy = json.dumps(_healthy_payload())
        school.conn.execute(
            "INSERT INTO questions (id, bank_id, title, payload_json, created_at) "
            "VALUES (7, 40, 'Triangle', ?, 't0')",
            (authored,),
        )
        school.conn.execute(
            "INSERT INTO questions (id, bank_id, title, payload_json, created_at) "
            "VALUES (8, 40, 'Vertex', ?, 't0')",
            (healthy,),
        )
        card = json.dumps(
            {
                "text_html": "<p>Simplify (5<sup>-4</sup>)(5<sup>7</sup>).</p>",
                "image_url": ANSWER,
                "image_src": ANSWER,
                "options_html": [f'<img src="{ANSWER}">', "11"],
            }
        )
        school.conn.execute(
            "INSERT INTO class_live_playlist_placements (id, item_json, source_question_id) "
            "VALUES (3, ?, 7)",
            (card,),
        )
        school.conn.commit()

        planned = plan_choice_stem_image_repairs(school)
        self.assertEqual([item.table for item in planned], ["class_live_playlist_placements"])
        self.assertEqual(planned[0].question_id, 7)
        self.assertEqual(planned[0].fields, ("image_url", "image_src"))
        self.assertEqual(apply_choice_stem_image_repairs(school, planned), 1)

        stored = json.loads(
            school.conn.execute(
                "SELECT item_json FROM class_live_playlist_placements WHERE id = 3"
            ).fetchone()[0]
        )
        self.assertNotIn("image_url", stored)
        self.assertNotIn("image_src", stored)
        self.assertIn("Simplify", stored["text_html"])
        self.assertIn(ANSWER, stored["options_html"][0])
        self.assertEqual(
            school.conn.execute(
                "SELECT id, bank_id, title, payload_json, created_at FROM questions WHERE id = 7"
            ).fetchone()[1:],
            (40, "Triangle", authored, "t0"),
        )
        self.assertEqual(
            school.conn.execute(
                "SELECT payload_json FROM questions WHERE id = 8"
            ).fetchone()[0],
            healthy,
        )
        self.assertEqual(
            school.conn.execute("SELECT origin FROM content_libraries WHERE id = 4").fetchone()[0],
            "upload",
        )

        again = plan_choice_stem_image_repairs(school)
        self.assertEqual(again, [])
        self.assertEqual(apply_choice_stem_image_repairs(school, again), 0)
        self.assertEqual(apply_choice_stem_image_repairs(school, planned), 0)
        self.assertEqual(
            school.conn.execute(
                "SELECT item_json FROM class_live_playlist_placements WHERE id = 3"
            ).fetchone()[0],
            planned[0].new_json,
        )

    def test_mirrored_choice_image_matches_the_remote_choice(self) -> None:
        """A mirror-cache path on image_url is the same image as the choice URL."""
        school = _school()
        school.conn.execute(
            "INSERT INTO content_libraries (id, ontario_code, origin) VALUES (4, 'MCF3M', 'upload')"
        )
        school.conn.execute(
            "INSERT INTO question_banks (id, library_id, title) VALUES (40, 4, 'M4')"
        )
        school.conn.execute(
            "INSERT INTO bank_image_cache (source_url, relpath) VALUES (?, ?)",
            (ANSWER, MIRROR),
        )
        school.conn.execute(
            "INSERT INTO questions (id, bank_id, title, payload_json, created_at) "
            "VALUES (15, 40, 'Simplify', ?, 't0')",
            (json.dumps(_healthy_payload()),),
        )
        card = {
            "text_html": "<p>Simplify (5<sup>-4</sup>)(5<sup>7</sup>).</p>",
            "image_url": MIRROR,
            "options_html": [f'<img src="{ANSWER}">', "11"],
        }
        school.conn.execute(
            "INSERT INTO class_live_playlist_placements (id, item_json, source_question_id) "
            "VALUES (6, ?, 15)",
            (json.dumps(card),),
        )
        school.conn.commit()
        planned = plan_choice_stem_image_repairs(school)
        self.assertEqual(len(planned), 1)
        self.assertEqual(planned[0].table, "class_live_playlist_placements")
        self.assertEqual(planned[0].question_id, 15)
        self.assertEqual(planned[0].library_id, 4)
        self.assertTrue(planned[0].shared)
        self.assertEqual(planned[0].fields, ("image_url",))
        self.assertEqual(apply_choice_stem_image_repairs(school, planned), 1)
        self.assertEqual(plan_choice_stem_image_repairs(school), [])
        self.assertEqual(apply_choice_stem_image_repairs(school, []), 0)

    def test_production_is_refused_without_the_confirm_token(self) -> None:
        """A /data database and a Fly app name are not opened."""
        cli = _load_cli()
        err = io.StringIO()
        with redirect_stderr(err):
            code = cli.main(["--db", "/data/lloves.sqlite"])
        self.assertEqual(code, 2)
        self.assertIn("production", err.getvalue().lower())
        err = io.StringIO()
        with redirect_stderr(err):
            code = cli.main(["--production", "--confirm", "nope", "--db", "/tmp/local.sqlite"])
        self.assertEqual(code, 2)
        self.assertIn("MCK-320", err.getvalue())

    def test_dry_run_does_not_migrate_or_write(self) -> None:
        """Dry-run opens sqlite read-only and leaves the schema untouched."""
        cli = _load_cli()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lloves.sqlite"
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE questions (id INTEGER PRIMARY KEY, payload_json TEXT)")
            conn.execute(
                "INSERT INTO questions (id, payload_json) VALUES (1, ?)",
                (json.dumps({"stem_html": "<p>Hi</p>"}),),
            )
            conn.commit()
            before = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            payload = conn.execute("SELECT payload_json FROM questions").fetchone()[0]
            conn.close()
            code = cli.main(["--db", str(path)])
            self.assertEqual(code, 0)
            conn = sqlite3.connect(path)
            after = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertEqual(after, before)
            self.assertEqual(
                conn.execute("SELECT payload_json FROM questions").fetchone()[0],
                payload,
            )
            conn.close()


class PlaylistReturnScrubTests(unittest.TestCase):
    """Early rehydrate and the playlist list scrub a promoted hero."""

    @classmethod
    def setUpClass(cls) -> None:
        """Open one throwaway school database."""
        import os

        os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")
        from school_db import SchoolDB

        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.school = SchoolDB(root / "lloves.sqlite", root, live_database_url="")

    @classmethod
    def tearDownClass(cls) -> None:
        """Close the database and remove the temp directory."""
        cls.school.close()
        cls._tmp.cleanup()

    def test_rehydrate_early_return_drops_a_promoted_hero(self) -> None:
        """A card that does not need rehydrate still loses a choice hero."""
        answer = "https://cdn.example/answer-5-3.png"
        item = {
            "import_source": "module_bank",
            "source_question_id": 23144,
            "type": "multiple_choice",
            "text": "Simplify.",
            "text_html": "<p>Simplify.</p>",
            "image_url": answer,
            "image_src": answer,
            "options_html": [f'<img src="{answer}">', "<p>other</p>"],
        }
        self.assertFalse(self.school._bank_item_needs_rehydrate(item))
        out = self.school.rehydrate_module_bank_item(8, 1, dict(item))
        self.assertNotIn("image_url", out)
        self.assertNotIn("image_src", out)
        self.assertEqual(out["text_html"], "<p>Simplify.</p>")
        self.assertIn(answer, out["options_html"][0])

    def test_playlist_list_scrubs_a_promoted_hero_and_keeps_an_authored_stem(self) -> None:
        """The placement list scrubs heroes and leaves stored JSON unchanged."""
        answer = "https://cdn.example/answer-5-3.png"
        triangle = "https://cdn.example/files/10/preview"
        promoted = {
            "import_source": "module_bank",
            "source_question_id": 23144,
            "type": "multiple_choice",
            "text_html": "<p>Simplify.</p>",
            "image_url": answer,
            "options_html": [f'<img src="{answer}">'],
        }
        authored = {
            "import_source": "module_bank",
            "source_question_id": 1270,
            "type": "multiple_choice",
            "text_html": f'<img src="{triangle}"><p>Given the triangle above.</p>',
            "image_url": triangle,
            "options_html": [f'<img src="{triangle}">'],
        }
        now = "2026-10-23T00:00:00Z"
        self.school.conn.execute(
            """
            INSERT INTO class_live_playlist_placements (
                class_id, module, slot, page_number, stage, sort_order,
                placement_key, item_id, item_json, source_question_id, created_at
            ) VALUES (8, 'M1', 'C1', 1, 'round', 0, 'promoted', 'p1', ?, 23144, ?)
            """,
            (json.dumps(promoted), now),
        )
        self.school.conn.execute(
            """
            INSERT INTO class_live_playlist_placements (
                class_id, module, slot, page_number, stage, sort_order,
                placement_key, item_id, item_json, source_question_id, created_at
            ) VALUES (8, 'M1', 'C1', 1, 'round', 1, 'authored', 'p2', ?, 1270, ?)
            """,
            (json.dumps(authored), now),
        )
        self.school.conn.commit()
        rows = self.school.list_class_playlist_placements(8, "M1", "C1", real_deck_only=True)
        by_key = {row["placement_key"]: row["item"] for row in rows}
        self.assertNotIn("image_url", by_key["promoted"])
        self.assertIn(answer, by_key["promoted"]["options_html"][0])
        self.assertEqual(by_key["authored"]["image_url"], triangle)
        self.assertIn(triangle, by_key["authored"]["text_html"])
        stored = {
            row[0]: row[1]
            for row in self.school.conn.execute(
                "SELECT placement_key, item_json FROM class_live_playlist_placements"
            )
        }
        self.assertIn(answer, stored["promoted"])
        self.assertIn(triangle, stored["authored"])

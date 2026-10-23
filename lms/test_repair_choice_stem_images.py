"""MCK-320 repair: choice images stored on a stem are listed, then removable."""

from __future__ import annotations

import importlib.util
import io
import json
import sqlite3
import sys
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

    def test_dry_run_lists_library_and_shared_flag(self) -> None:
        """Dry-run names each question's library and whether the pack is shared."""
        school = _school()
        self._seed_libraries(school)
        school.conn.execute(
            "INSERT INTO questions (id, bank_id, title, payload_json, created_at) "
            "VALUES (7, 40, 'Simplify', ?, 't0')",
            (json.dumps(_leaked_payload()),),
        )
        school.conn.execute(
            "INSERT INTO questions (id, bank_id, title, payload_json, created_at) "
            "VALUES (8, 40, 'Vertex', ?, 't0')",
            (json.dumps(_healthy_payload()),),
        )
        school.conn.execute(
            "INSERT INTO questions (id, bank_id, title, payload_json, created_at) "
            "VALUES (11, 90, 'Simplify', ?, 't0')",
            (json.dumps(_leaked_payload()),),
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
        self.assertIn(
            "question_id=7 library_id=4 shared=yes",
            report,
        )
        self.assertIn(
            "question_id=11 library_id=9 shared=no",
            report,
        )
        self.assertNotIn("question_id=8", report)
        self.assertNotIn("class_live_playlist_placements", report)
        self.assertEqual(
            school.conn.execute(
                "SELECT item_json FROM class_live_playlist_placements WHERE id = 3"
            ).fetchone()[0],
            card,
        )

    def test_second_run_changes_zero_rows(self) -> None:
        """Apply rewrites only the leaked question payloads, then a second run writes nothing."""
        school = _school()
        self._seed_libraries(school)
        leaked = json.dumps(_leaked_payload())
        healthy = json.dumps(_healthy_payload())
        school.conn.execute(
            "INSERT INTO questions (id, bank_id, title, payload_json, created_at) "
            "VALUES (7, 40, 'Simplify', ?, 't0')",
            (leaked,),
        )
        school.conn.execute(
            "INSERT INTO questions (id, bank_id, title, payload_json, created_at) "
            "VALUES (8, 40, 'Vertex', ?, 't0')",
            (healthy,),
        )
        card = '{"image_url": "https://cdn.example/answer-5-3.png"}'
        school.conn.execute(
            "INSERT INTO class_live_playlist_placements (id, item_json, source_question_id) "
            "VALUES (3, ?, 7)",
            (card,),
        )
        school.conn.commit()
        before_other = school.conn.execute(
            "SELECT id, bank_id, title, payload_json, created_at FROM questions WHERE id = 8"
        ).fetchone()

        planned = plan_choice_stem_image_repairs(school)
        self.assertEqual([item.question_id for item in planned], [7])
        self.assertTrue(planned[0].shared)
        self.assertEqual(planned[0].library_id, 4)
        self.assertEqual(apply_choice_stem_image_repairs(school, planned), 1)

        stored = json.loads(
            school.conn.execute(
                "SELECT payload_json FROM questions WHERE id = 7"
            ).fetchone()[0]
        )
        self.assertNotIn(ANSWER, stored["stem_html"])
        self.assertIn("Simplify", stored["stem_html"])
        self.assertIn(ANSWER, stored["choices"][0]["html"])
        row = school.conn.execute(
            "SELECT title, created_at, bank_id FROM questions WHERE id = 7"
        ).fetchone()
        self.assertEqual(tuple(row), ("Simplify", "t0", 40))
        self.assertEqual(
            school.conn.execute(
                "SELECT id, bank_id, title, payload_json, created_at FROM questions WHERE id = 8"
            ).fetchone(),
            before_other,
        )
        self.assertEqual(
            school.conn.execute(
                "SELECT item_json FROM class_live_playlist_placements WHERE id = 3"
            ).fetchone()[0],
            card,
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
                "SELECT payload_json FROM questions WHERE id = 7"
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
        payload = {
            "stem_html": "<p>Simplify (5<sup>-4</sup>)(5<sup>7</sup>).</p>",
            "image_url": MIRROR,
            "choices": [
                {"id": "a", "html": f'<img src="{ANSWER}">', "correct": True},
                {"id": "b", "html": "11", "correct": False},
            ],
        }
        school.conn.execute(
            "INSERT INTO questions (id, bank_id, title, payload_json, created_at) "
            "VALUES (15, 40, 'Simplify', ?, 't0')",
            (json.dumps(payload),),
        )
        school.conn.commit()
        planned = plan_choice_stem_image_repairs(school)
        self.assertEqual(len(planned), 1)
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

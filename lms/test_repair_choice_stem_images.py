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
    """Empty sqlite with the JSON columns the repair scans."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE questions (
            id INTEGER PRIMARY KEY,
            payload_json TEXT NOT NULL
        );
        CREATE TABLE class_live_playlist_placements (
            id INTEGER PRIMARY KEY,
            item_json TEXT NOT NULL,
            source_question_id INTEGER
        );
        CREATE TABLE live_session_items (
            id INTEGER PRIMARY KEY,
            item_json TEXT NOT NULL
        );
        CREATE TABLE live_session_prompts (
            id INTEGER PRIMARY KEY,
            payload TEXT NOT NULL
        );
        CREATE TABLE participation_items (
            id INTEGER PRIMARY KEY,
            item_json TEXT NOT NULL
        );
        CREATE TABLE bank_image_cache (
            source_url TEXT NOT NULL,
            relpath TEXT NOT NULL
        );
        """
    )
    return _Db(conn)


class ChoiceStemImageRepairTests(unittest.TestCase):
    """Dry-run lists the leak; apply is idempotent; production stays gated."""

    def test_dry_run_lists_question_ids_and_apply_is_idempotent(self) -> None:
        """A glued choice image is reported, then removed once."""
        school = _school()
        leaked = {
            "stem_html": (
                f'<img src="{ANSWER}" alt="five cubed">'
                "<p>Simplify (5<sup>-4</sup>)(5<sup>7</sup>).</p>"
            ),
            "choices": [
                {"id": "a", "html": f'<img src="{ANSWER}" alt="five cubed">', "correct": True},
                {"id": "b", "html": "11", "correct": False},
            ],
        }
        healthy = {
            "stem_html": "<p>What is the vertex?</p>",
            "image_url": GRAPH,
            "choices": [
                {"id": "a", "html": "(0, c)", "correct": True},
                {"id": "b", "html": "(h, k)", "correct": False},
            ],
        }
        card = {
            "id": "bank-import-42",
            "text_html": "<p>Simplify (5<sup>-4</sup>)(5<sup>7</sup>).</p>",
            "image_url": ANSWER,
            "options_html": [f'<img src="{ANSWER}" alt="five cubed">', "11"],
            "options": ["A", "B"],
        }
        school.conn.execute(
            "INSERT INTO questions (id, payload_json) VALUES (7, ?)",
            (json.dumps(leaked),),
        )
        school.conn.execute(
            "INSERT INTO questions (id, payload_json) VALUES (8, ?)",
            (json.dumps(healthy),),
        )
        school.conn.execute(
            "INSERT INTO class_live_playlist_placements (id, item_json, source_question_id) "
            "VALUES (3, ?, 42)",
            (json.dumps(card),),
        )
        school.conn.commit()
        before = school.conn.execute(
            "SELECT payload_json FROM questions WHERE id = 7"
        ).fetchone()[0]

        planned = plan_choice_stem_image_repairs(school)
        report = format_choice_stem_image_report(planned, dry_run=True)
        self.assertIn("DRY RUN", report[0])
        joined = "\n".join(report)
        self.assertNotIn(ANSWER, joined)
        self.assertNotIn("Simplify", joined)
        self.assertIn("questions row=7 question_id=7 fields=stem_html", joined)
        self.assertIn(
            "class_live_playlist_placements row=3 question_id=42 fields=image_url",
            joined,
        )
        self.assertIn("affected_question_ids: 7,42", joined)
        self.assertIn("affected_row_count: 2", joined)
        self.assertNotIn("row=8", joined)
        unchanged = school.conn.execute(
            "SELECT payload_json FROM questions WHERE id = 7"
        ).fetchone()[0]
        self.assertEqual(unchanged, before)

        written = apply_choice_stem_image_repairs(school, planned)
        self.assertEqual(written, 2)
        stored = json.loads(
            school.conn.execute(
                "SELECT payload_json FROM questions WHERE id = 7"
            ).fetchone()[0]
        )
        self.assertNotIn(ANSWER, stored["stem_html"])
        self.assertIn("Simplify", stored["stem_html"])
        self.assertIn(ANSWER, stored["choices"][0]["html"])
        graph = json.loads(
            school.conn.execute(
                "SELECT payload_json FROM questions WHERE id = 8"
            ).fetchone()[0]
        )
        self.assertEqual(graph["image_url"], GRAPH)
        placed = json.loads(
            school.conn.execute(
                "SELECT item_json FROM class_live_playlist_placements WHERE id = 3"
            ).fetchone()[0]
        )
        self.assertNotIn("image_url", placed)
        self.assertIn(ANSWER, placed["options_html"][0])
        self.assertEqual(plan_choice_stem_image_repairs(school), [])

    def test_mirrored_choice_image_matches_the_remote_choice(self) -> None:
        """A mirror-cache path on image_url is the same image as the choice URL."""
        school = _school()
        school.conn.execute(
            "INSERT INTO bank_image_cache (source_url, relpath) VALUES (?, ?)",
            (ANSWER, MIRROR),
        )
        card = {
            "question_id": 15,
            "text_html": "<p>Simplify (5<sup>-4</sup>)(5<sup>7</sup>).</p>",
            "image_url": MIRROR,
            "options_html": [f'<img src="{ANSWER}">', "11"],
        }
        school.conn.execute(
            "INSERT INTO participation_items (id, item_json) VALUES (4, ?)",
            (json.dumps(card),),
        )
        school.conn.commit()
        planned = plan_choice_stem_image_repairs(school)
        self.assertEqual(len(planned), 1)
        self.assertEqual(planned[0].question_id, 15)
        self.assertEqual(planned[0].fields, ("image_url",))
        apply_choice_stem_image_repairs(school, planned)
        self.assertEqual(plan_choice_stem_image_repairs(school), [])

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

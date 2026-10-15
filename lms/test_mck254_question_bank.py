#!/usr/bin/env python3
"""MCK-254: shared question editor, bank cards, and per-question live defaults."""

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
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from live_defaults import ensure_question_editor_columns  # noqa: E402
from school_db import _now  # noqa: E402


class QuestionEditorTests(unittest.TestCase):
    """Edit mode, save-to-bank, live defaults, and live Add New regression."""

    def setUp(self) -> None:
        """Teacher-owned class with a library the live bank can write."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.client = self.app.test_client()
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
        )
        self.library = self.school.create_library("MCR3U", origin="upload")
        self.library_id = int(self.library["id"])
        self.school.attach_library(int(self.offering["id"]), self.library_id)
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCR3U",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Maple"],
            offering_id=int(self.offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )
        self.class_id = int(created["id"])
        self._login_staff()

    def tearDown(self) -> None:
        """Close sqlite and remove the temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _login_staff(self) -> None:
        """Sign the client in as the assigned teacher."""
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        user = self.school.get_user_by_email("teacher@gmail.com")
        assert user is not None
        self.client.post("/verify-email", data={"code": user["verification_code"]})

    def _add_bank(self, body: dict) -> dict:
        """POST one question into the live bank."""
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-bank/questions",
            json=body,
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        return rv.get_json()["question"]

    def test_edit_mode_markup_is_shared(self) -> None:
        """Both tabs include one editor, with Prev/Next and Save to bank."""
        banks = self.client.get(f"/staff/class/{self.class_id}?tab=question-banks")
        live = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        self.assertEqual(banks.status_code, 200)
        self.assertEqual(live.status_code, 200)
        for page in (banks, live):
            html = page.get_data(as_text=True)
            self.assertEqual(html.count('id="live-add-question-dialog"'), 1)
            self.assertIn('data-question-editor-prev', html)
            self.assertIn('data-question-editor-next', html)
            self.assertIn('id="live-add-q-defaults"', html)
            self.assertIn("Individual", html)
            self.assertIn("Show results", html)
            self.assertIn(">Add to this page</button>", html)
        bank_html = banks.get_data(as_text=True)
        self.assertIn('id="qbank-bulk-move"', bank_html)
        self.assertIn('id="qbank-bulk-kind"', bank_html)
        self.assertIn('id="qbank-bulk-remove"', bank_html)
        self.assertNotIn(">Set points</button>", bank_html)
        self.assertNotIn(">Duplicate</button>", bank_html)
        self.assertNotIn(">Add to live deck</button>", bank_html)
        self.assertNotIn("Import from bank", bank_html)
        editor = (LMS_DIR / "static" / "question_editor.js").read_text(encoding="utf-8")
        self.assertIn('mode === "edit" ? "edit" : "add"', editor)
        self.assertIn("from_editor: true", editor)
        self.assertIn("Save to bank", editor)
        self.assertIn("Edit question", editor)

    def test_editor_save_updates_rank_and_keeps_legacy_patch_readonly(self) -> None:
        """from_editor writes a rank payload. A stem_text PATCH still returns 400."""
        question = self._add_bank(
            {
                "bank_scope": "M1",
                "type": "rank",
                "stem_text": "Order these widths",
                "options": ["narrow", "medium", "wide"],
                "rank_key": [2, 1, 0],
            }
        )
        legacy = self.client.patch(
            f"/api/staff/class/{self.class_id}/question-bank/{question['bank_id']}/questions/{question['id']}",
            json={"stem_text": "Changed"},
        )
        self.assertEqual(legacy.status_code, 400)
        saved = self.client.patch(
            f"/api/staff/class/{self.class_id}/question-bank/{question['bank_id']}/questions/{question['id']}",
            json={
                "from_editor": True,
                "type": "rank",
                "text": "Order these widths now",
                "options": ["narrow", "medium", "wide"],
                "rank_options": question["payload"]["rank_options"],
                "rank_key": question["payload"]["rank_key"],
                "bank_kind": "standard",
            },
        )
        self.assertEqual(saved.status_code, 200, saved.get_data(as_text=True))
        payload = saved.get_json()["question"]["payload"]
        self.assertEqual(payload.get("text"), "Order these widths now")
        self.assertEqual(payload.get("type"), "rank")
        self.assertEqual(
            [row["id"] for row in payload["rank_options"]],
            [row["id"] for row in question["payload"]["rank_options"]],
        )
        self.assertEqual(payload.get("bank_kind"), "standard")
        mc = self._add_bank(
            {
                "bank_scope": "M1",
                "type": "mc",
                "stem_text": "Slope?",
                "options": ["1", "2", "3", "4"],
                "correct_answer": "B",
            }
        )
        edited = self.client.patch(
            f"/api/staff/class/{self.class_id}/question-bank/{mc['bank_id']}/questions/{mc['id']}",
            json={
                "from_editor": True,
                "type": "mc",
                "text": "Slope of y = 2x?",
                "options": ["1", "2", "3", "4"],
                "correct_index": 1,
            },
        )
        self.assertEqual(edited.status_code, 200, edited.get_data(as_text=True))
        again = self.school.search_module_bank_mcs(self.library_id, 1, "Slope of y")
        self.assertTrue(any(int(row["question_id"]) == int(mc["id"]) for row in again["items"]))

    def test_save_to_bank_stores_scope_and_live_defaults(self) -> None:
        """A bank add pins scope and writes only the live defaults that were sent."""
        question = self._add_bank(
            {
                "bank_scope": "M2",
                "type": "mc",
                "stem_text": "Vertex form",
                "options": ["A", "B", "C", "D"],
                "correct_answer": "A",
                "live_response_mode": "group",
                "live_show_results": False,
                "live_points": 2,
            }
        )
        stored = self.school.question_live_defaults(self.library_id, int(question["id"]))
        self.assertEqual(stored["live_response_mode"], "group")
        self.assertIs(stored["live_show_results"], False)
        self.assertEqual(stored["live_points"], 2.0)
        self.assertEqual(stored["bank_scope"], "M2")
        m2 = {
            int(row["question_id"])
            for row in self.school.search_module_bank_mcs(self.library_id, 2, "Vertex")["items"]
        }
        m1 = {
            int(row["question_id"])
            for row in self.school.search_module_bank_mcs(self.library_id, 1, "Vertex")["items"]
        }
        self.assertIn(int(question["id"]), m2)
        self.assertNotIn(int(question["id"]), m1)
        plain = self._add_bank(
            {
                "bank_scope": "M1",
                "type": "poll",
                "stem_text": "Which parent function?",
            }
        )
        untouched = self.school.question_live_defaults(self.library_id, int(plain["id"]))
        self.assertIsNone(untouched["live_response_mode"])
        self.assertIsNone(untouched["live_show_results"])
        self.assertIsNone(untouched["live_points"])
        hidden = self.school.search_module_bank_mcs(self.library_id, 1, "parent function")
        shown = self.school.search_module_bank_mcs(
            self.library_id, 1, "parent function", staff_types=True
        )
        self.assertFalse(any(int(row["question_id"]) == int(plain["id"]) for row in hidden["items"]))
        self.assertTrue(any(int(row["question_id"]) == int(plain["id"]) for row in shown["items"]))

    def test_migration_adds_null_columns_once(self) -> None:
        """An old questions table gains the columns without rewriting existing rows."""
        path = Path(self.tmp.name) / "legacy.sqlite"
        conn = sqlite3.connect(path)
        conn.execute(
            """
            CREATE TABLE questions (
                id INTEGER PRIMARY KEY,
                bank_id INTEGER NOT NULL,
                import_key TEXT NOT NULL,
                item_type TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO questions (
                bank_id, import_key, item_type, title, payload_json, created_at
            ) VALUES (1, 'old-q', 'multiple_choice_question', 'Old stem', '{}', ?)
            """,
            (_now(),),
        )
        conn.commit()
        ensure_question_editor_columns(conn)
        ensure_question_editor_columns(conn)
        row = conn.execute(
            """
            SELECT title, live_response_mode, live_show_results, live_points, bank_scope
            FROM questions
            """
        ).fetchone()
        self.assertEqual(row, ("Old stem", None, None, None, None))
        names = [info[1] for info in conn.execute("PRAGMA table_info(questions)")]
        self.assertEqual(names.count("live_response_mode"), 1)
        self.assertEqual(names.count("bank_scope"), 1)
        conn.close()

    def test_import_and_launch_use_defaults_and_null_stays_individual(self) -> None:
        """Stored defaults start the live card. A null row keeps today's individual start."""
        grouped = self._add_bank(
            {
                "bank_scope": "M1",
                "type": "mc",
                "stem_text": "Grouped launch",
                "options": ["A", "B", "C", "D"],
                "correct_answer": "C",
                "live_response_mode": "group",
                "live_show_results": False,
                "live_points": 3,
            }
        )
        plain = self._add_bank(
            {
                "bank_scope": "M1",
                "type": "mc",
                "stem_text": "Plain launch",
                "options": ["A", "B", "C", "D"],
                "correct_answer": "A",
            }
        )
        ranked = self._add_bank(
            {
                "bank_scope": "M1",
                "type": "rank",
                "stem_text": "Rank preset stays",
                "options": ["one", "two", "three"],
                "rank_key": [0, 1, 2],
            }
        )
        forced = self._add_bank(
            {
                "bank_scope": "M1",
                "type": "rank",
                "stem_text": "Rank forced individual",
                "options": ["one", "two", "three"],
                "rank_key": [0, 1, 2],
                "live_response_mode": "individual",
            }
        )
        group_place = self.school.import_mc_to_class_playlist(
            self.class_id, "M1", "C1", int(grouped["id"]),
            library_id=self.library_id, page_number=1,
        )
        plain_place = self.school.import_mc_to_class_playlist(
            self.class_id, "M1", "C1", int(plain["id"]),
            library_id=self.library_id, page_number=1,
        )
        self.assertEqual(group_place["item"]["response_mode"], "group_submit")
        self.assertIs(group_place["item"]["show_live_results"], False)
        self.assertEqual(group_place["item"]["live_points"], 3.0)
        self.assertEqual(plain_place["item"]["response_mode"], "individual")
        self.assertNotIn("live_points", plain_place["item"])
        session = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), live_module="M1", live_slot="C1"
        )
        self.school.import_mc_to_class_playlist(
            self.class_id, "M1", "C1", int(ranked["id"]),
            library_id=self.library_id, page_number=1,
        )
        self.school.import_mc_to_class_playlist(
            self.class_id, "M1", "C1", int(forced["id"]),
            library_id=self.library_id, page_number=1,
        )
        rows = {
            str(row["item_id"]): row
            for row in self.school.ensure_live_session_items(int(session["id"]))
        }
        group_row = rows[f"bank-import-{int(grouped['id'])}"]
        plain_row = rows[f"bank-import-{int(plain['id'])}"]
        rank_row = rows[f"bank-import-{int(ranked['id'])}"]
        forced_row = rows[f"bank-import-{int(forced['id'])}"]
        self.assertEqual(group_row["response_mode"], "group_submit")
        self.assertEqual(group_row["show_live_results"], 0)
        self.assertEqual(plain_row["response_mode"], "individual")
        self.assertEqual(plain_row["show_live_results"], 1)
        self.assertEqual(rank_row["response_mode"], "group_submit")
        self.assertEqual(forced_row["response_mode"], "individual")

    def test_live_add_without_defaults_stays_individual(self) -> None:
        """Add New that does not send live defaults still posts an individual MC."""
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C1/add-question",
            json={
                "type": "mc",
                "text": "Domain of the square root",
                "options": ["x >= 0", "x > 0", "x <= 0", "all reals"],
                "correct_index": 0,
                "page_number": 1,
                "stage": "round",
                "save_to_bank": False,
            },
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        item = rv.get_json()["placement"]["item"]
        self.assertEqual(item["type"], "mc")
        self.assertEqual(item["response_mode"], "individual")
        self.assertNotIn("live_response_mode", item)
        self.assertEqual(item["text"], "Domain of the square root")

    def test_bulk_move_pins_scope_without_linking_the_bank(self) -> None:
        """Move shows the question on the destination tab only."""
        question = self._add_bank(
            {
                "bank_scope": "M1",
                "type": "mc",
                "stem_text": "Move me",
                "options": ["A", "B", "C", "D"],
                "correct_answer": "A",
            }
        )
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/question-bank/bulk",
            json={
                "action": "move",
                "bank_scope": "M3",
                "questions": [
                    {"bank_id": question["bank_id"], "question_id": question["id"]}
                ],
            },
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        m1 = self.school.search_module_bank_mcs(self.library_id, 1, "Move me")["items"]
        m3 = self.school.search_module_bank_mcs(self.library_id, 3, "Move me")["items"]
        self.assertFalse(any(int(row["question_id"]) == int(question["id"]) for row in m1))
        self.assertTrue(any(int(row["question_id"]) == int(question["id"]) for row in m3))
        links = self.school.conn.execute(
            """
            SELECT module_number FROM course_module_bank_links
            WHERE library_id = ? AND bank_id = ?
            """,
            (self.library_id, int(question["bank_id"])),
        ).fetchall()
        modules = {int(row["module_number"]) for row in links}
        self.assertNotIn(3, modules)

    def test_bulk_move_rejects_a_bad_row_without_writing(self) -> None:
        """A missing question in the batch leaves the valid row on M1."""
        question = self._add_bank(
            {
                "bank_scope": "M1",
                "type": "mc",
                "stem_text": "Stay put",
                "options": ["A", "B", "C", "D"],
                "correct_answer": "A",
            }
        )
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/question-bank/bulk",
            json={
                "action": "move",
                "bank_scope": "M4",
                "questions": [
                    {"bank_id": question["bank_id"], "question_id": question["id"]},
                    {"bank_id": question["bank_id"], "question_id": 999999},
                ],
            },
        )
        self.assertEqual(rv.status_code, 404, rv.get_data(as_text=True))
        stored = self.school.question_live_defaults(self.library_id, int(question["id"]))
        self.assertEqual(stored["bank_scope"], "M1")
        m4 = self.school.search_module_bank_mcs(self.library_id, 4, "Stay put")["items"]
        self.assertFalse(any(int(row["question_id"]) == int(question["id"]) for row in m4))


if __name__ == "__main__":
    unittest.main()

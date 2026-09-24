#!/usr/bin/env python3
"""Course-tab live bank uses the Import search and does not write catalogue items."""

from __future__ import annotations

import json
import os
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
from bank_edit import payload_is_warmup  # noqa: E402
from school_db import _now  # noqa: E402


class LiveBankTabTests(unittest.TestCase):
    """Staff Question banks tab reads the same bank Import searches."""

    def setUp(self) -> None:
        """Teacher-owned class with one confirmed module bank."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
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
        cur = self.school.conn.execute(
            """
            INSERT INTO question_banks (
                library_id, import_key, title, settings_json, created_at
            ) VALUES (?, 'bank:unlinked', 'Cartridge only', '{}', ?)
            """,
            (self.library_id, _now()),
        )
        self.school.conn.commit()
        self.unlinked_bank = int(cur.lastrowid)
        self.school.conn.execute(
            """
            INSERT INTO questions (
                bank_id, import_key, item_type, title, payload_json, created_at
            ) VALUES (?, 'q-cartridge', 'multiple_choice_question', 'Cartridge', ?, ?)
            """,
            (
                self.unlinked_bank,
                json.dumps(
                    {
                        "stem_html": "IMSCC only stem",
                        "choices": [
                            {"id": "a", "html": "A", "correct": True},
                            {"id": "b", "html": "B", "correct": False},
                        ],
                    }
                ),
                _now(),
            ),
        )
        self.school.conn.commit()
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
        self.school.seed_course_wide_warmups(self.library_id)
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

    def test_payload_warmup_tag_matches_import(self) -> None:
        """Warmup kind and tags count; plain MC does not."""
        self.assertTrue(payload_is_warmup({"kind": "warmup"}))
        self.assertTrue(payload_is_warmup({"tags": ["warmup"]}))
        self.assertFalse(payload_is_warmup({"type": "mc"}))

    def test_add_question_lands_in_module_import_not_warmup_bank(self) -> None:
        """A course-tab MC is the Import bank, untagged, and not catalogue JSON."""
        source = (LMS_DIR / "live_bank.py").read_text(encoding="utf-8")
        self.assertNotIn("builder_bank", source)
        self.assertNotIn("write_text", source)
        before = {
            int(row["question_id"])
            for row in self.school.search_bank_scope_mcs(
                self.library_id, "course", 1, "", kind="warmup"
            )["items"]
        }
        self.assertGreater(len(before), 0)
        saved = self.client.post(
            f"/api/staff/class/{self.class_id}/live-bank/questions",
            json={
                "bank_scope": "M2",
                "type": "mc",
                "stem_text": "Live bank module two",
                "options": ["W", "X", "Y", "Z"],
                "correct_answer": "B",
                "points": 1,
            },
        )
        self.assertEqual(saved.status_code, 200, saved.get_data(as_text=True))
        question_id = int(saved.get_json()["question"]["id"])
        payload = saved.get_json()["question"]["payload"]
        self.assertEqual(payload.get("type"), "mc")
        self.assertNotIn("kind", payload)
        m2 = {
            int(row.get("question_id") or 0)
            for row in self.school.search_module_bank_mcs(
                self.library_id, 2, "Live bank module"
            )["items"]
        }
        m1 = {
            int(row.get("question_id") or 0)
            for row in self.school.search_module_bank_mcs(
                self.library_id, 1, "Live bank module"
            )["items"]
        }
        self.assertIn(question_id, m2)
        self.assertNotIn(question_id, m1)
        after = {
            int(row["question_id"])
            for row in self.school.search_bank_scope_mcs(
                self.library_id, "course", 1, "", kind="warmup"
            )["items"]
        }
        self.assertEqual(before, after)
        self.assertNotIn(question_id, after)
        module_hits = self.school.search_module_bank_mcs(self.library_id, 2, "IMSCC only")
        self.assertEqual(module_hits["items"], [])

    def test_rejects_a_fourth_question_type(self) -> None:
        """Essay is still rejected. Rank is a separate allow-list entry."""
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-bank/questions",
            json={"bank_scope": "M1", "type": "essay", "stem_text": "Nope"},
        )
        self.assertEqual(rv.status_code, 400)

    def test_warmup_edit_keeps_kind_and_skips_a_fake_key(self) -> None:
        """Editing a Course Wide warmup does not invent an answer key."""
        rows = self.school.search_bank_scope_mcs(
            self.library_id, "course", 1, "Aisle or window", kind="warmup"
        )["items"]
        self.assertTrue(rows)
        item = rows[0]
        saved = self.client.patch(
            f"/api/staff/class/{self.class_id}/question-bank/{int(item['bank_id'])}"
            f"/questions/{int(item['question_id'])}",
            json={
                "stem_text": "Aisle or window: still a preference?",
                "options": ["Aisle", "Window"],
                "points": 0,
            },
        )
        self.assertEqual(saved.status_code, 200, saved.get_data(as_text=True))
        question = saved.get_json()["question"]
        self.assertEqual(question.get("title"), "Aisle or window")
        self.assertFalse(question.get("correct_answers"))
        self.assertTrue(
            all(not choice.get("correct") for choice in question.get("choices") or [])
        )
        again = self.school.search_bank_scope_mcs(
            self.library_id, "course", 1, "still a preference", kind="warmup"
        )["items"]
        self.assertTrue(again)
        self.assertEqual(again[0].get("question_title"), "Aisle or window")
        self.assertEqual(again[0].get("kind"), "warmup")
        self.assertFalse(again[0].get("correct_answer"))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Module bank mapping and module-scoped MC search tests."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from school_db import _now  # noqa: E402


def _insert_bank(
    school: Any,
    library_id: int,
    *,
    title: str,
    import_key: str,
) -> int:
    """Insert one question bank row for tests."""
    cur = school.conn.execute(
        """
        INSERT INTO question_banks (
            library_id, import_key, title, settings_json, created_at
        ) VALUES (?, ?, ?, '{}', ?)
        """,
        (int(library_id), import_key, title, _now()),
    )
    school.conn.commit()
    return int(cur.lastrowid)


def _insert_mc_question(
    school: Any,
    bank_id: int,
    *,
    import_key: str,
    title: str,
    stem: str,
    correct_index: int = 0,
) -> int:
    """Insert one multiple-choice question row for tests."""
    choices = [
        {"id": "a1", "html": "First option", "correct": correct_index == 0},
        {"id": "a2", "html": "Second option", "correct": correct_index == 1},
        {"id": "a3", "html": "Third option", "correct": correct_index == 2},
    ]
    payload = {
        "stem_html": stem,
        "points_possible": 1.0,
        "choices": choices,
        "correct_ids": [choices[correct_index]["id"]],
    }
    cur = school.conn.execute(
        """
        INSERT INTO questions (
            bank_id, import_key, item_type, title, payload_json, created_at
        ) VALUES (?, ?, 'multiple_choice_question', ?, ?, ?)
        """,
        (int(bank_id), import_key, title, json.dumps(payload), _now()),
    )
    school.conn.commit()
    return int(cur.lastrowid)


class ModuleBankMappingTests(unittest.TestCase):
    """SchoolDB mapping + search without Flask."""

    def setUp(self) -> None:
        """Create library, banks, and questions for module 1 vs module 2."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.school = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        ).config["SCHOOL_DB"]
        self.library = self.school.create_library("MCR3U", origin="upload")
        self.library_id = int(self.library["id"])
        self.m1_bank = _insert_bank(
            self.school,
            self.library_id,
            title="Chapter 1 Quiz",
            import_key="bank:ch1",
        )
        self.m2_bank = _insert_bank(
            self.school,
            self.library_id,
            title="Module 2 Test",
            import_key="bank:m2test",
        )
        self.other_bank = _insert_bank(
            self.school,
            self.library_id,
            title="General Review",
            import_key="bank:review",
        )
        self.m1_q = _insert_mc_question(
            self.school,
            self.m1_bank,
            import_key="q-m1",
            title="M1 roots",
            stem="What is a square root?",
        )
        self.m2_q = _insert_mc_question(
            self.school,
            self.m2_bank,
            import_key="q-m2",
            title="M2 quadratics",
            stem="Which graph is a parabola?",
        )

    def tearDown(self) -> None:
        """Close sqlite and remove temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def test_suggest_module_banks_scopes_heuristics(self) -> None:
        """Module 1 suggestions exclude module 2 banks."""
        payload = self.school.suggest_module_banks(self.library_id, 1)
        suggested_ids = {row["bank_id"] for row in payload["suggested"]}
        self.assertIn(self.m1_bank, suggested_ids)
        self.assertNotIn(self.m2_bank, suggested_ids)
        self.assertNotIn(self.other_bank, suggested_ids)
        self.assertTrue(payload["needs_confirmation"])

    def test_confirm_and_search_are_module_scoped(self) -> None:
        """Search returns only confirmed module banks."""
        empty = self.school.search_module_bank_mcs(self.library_id, 1, "")
        self.assertEqual(empty["items"], [])
        self.school.confirm_module_bank_links(
            self.library_id, 1, [self.m1_bank]
        )
        hits = self.school.search_module_bank_mcs(self.library_id, 1, "square")
        self.assertEqual(hits["filtered"], 1)
        self.assertEqual(hits["items"][0]["question_id"], self.m1_q)
        self.assertEqual(hits["items"][0]["correct_answer"], "A")
        m2_hits = self.school.search_module_bank_mcs(self.library_id, 2, "")
        self.assertEqual(m2_hits["items"], [])



    def test_recommended_module_test_banks_and_partial_confirmation(self) -> None:
        """Recommended test banks stay unconfirmed until linked even when builder is."""
        test_bank = _insert_bank(
            self.school,
            self.library_id,
            title="Module 1 Test",
            import_key="bank:m1test",
        )
        builder_bank = _insert_bank(
            self.school,
            self.library_id,
            title="MCF3M Module 1 Builder Bank",
            import_key="builder-bank:MCF3M:M1",
        )
        _insert_mc_question(
            self.school,
            test_bank,
            import_key="q-test",
            title="Test pool",
            stem="Which graph opens upward?",
        )
        _insert_mc_question(
            self.school,
            builder_bank,
            import_key="q-builder",
            title="Builder pool",
            stem="What is the vertex form?",
        )
        self.school.confirm_module_bank_links(self.library_id, 1, [builder_bank])
        payload = self.school.suggest_module_banks(self.library_id, 1)
        recommended_ids = {row["bank_id"] for row in payload["recommended"]}
        self.assertIn(test_bank, recommended_ids)
        self.assertTrue(payload["needs_confirmation"])
        hits = self.school.search_module_bank_mcs(self.library_id, 1, "")
        self.assertEqual(hits["total"], 1)
        self.school.confirm_module_bank_links(
            self.library_id, 1, [test_bank, builder_bank]
        )
        payload = self.school.suggest_module_banks(self.library_id, 1)
        self.assertFalse(payload["needs_confirmation"])
        hits = self.school.search_module_bank_mcs(self.library_id, 1, "")
        self.assertEqual(hits["total"], 2)

class ModuleBankApiTests(unittest.TestCase):
    """Staff HTTP endpoints for module bank mapping."""

    def setUp(self) -> None:
        """Teacher-owned class with attached library and seeded banks."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
        )
        self.library = self.school.create_library("MCR3U", origin="upload")
        self.library_id = int(self.library["id"])
        self.school.attach_library(int(self.offering["id"]), self.library_id)
        self.m1_bank = _insert_bank(
            self.school,
            self.library_id,
            title="Chapter 1 MC",
            import_key="bank:ch1",
        )
        self.m2_bank = _insert_bank(
            self.school,
            self.library_id,
            title="Module 2 Test",
            import_key="bank:m2",
        )
        _insert_mc_question(
            self.school,
            self.m1_bank,
            import_key="q1",
            title="Factoring",
            stem="Factor x squared minus one",
        )
        _insert_mc_question(
            self.school,
            self.m2_bank,
            import_key="q2",
            title="Vertex",
            stem="Find the vertex",
        )
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
        self._login("teacher@gmail.com")

    def tearDown(self) -> None:
        """Close sqlite and remove temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _login(self, email: str) -> None:
        """Complete local staff login for API calls."""
        self.client.get("/auth/google?portal=staff")
        self.client.get(f"/auth/google/callback?email={email}&name=T")
        self.client.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email(email)["verification_code"]
            },
        )

    def test_list_module_banks_api(self) -> None:
        """GET module-banks returns suggestions and needs_confirmation."""
        rv = self.client.get(
            f"/api/staff/class/{self.class_id}/module-banks?module=M1"
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        body = rv.get_json()
        self.assertTrue(body.get("ok"))
        suggested_ids = {row["bank_id"] for row in body.get("suggested") or []}
        self.assertIn(self.m1_bank, suggested_ids)
        self.assertNotIn(self.m2_bank, suggested_ids)
        self.assertTrue(body.get("needs_confirmation"))

    def test_confirm_and_search_api(self) -> None:
        """Confirm M1 bank then search returns only module 1 MCs."""
        confirm = self.client.post(
            f"/api/staff/class/{self.class_id}/module-banks/confirm",
            json={"module": "M1", "bank_ids": [self.m1_bank]},
        )
        self.assertEqual(confirm.status_code, 200, confirm.get_json())
        self.assertFalse(confirm.get_json().get("needs_confirmation"))
        search = self.client.get(
            f"/api/staff/class/{self.class_id}/module-banks/M1/mc-search?q=factor"
        )
        self.assertEqual(search.status_code, 200, search.get_json())
        items = search.get_json().get("items") or []
        self.assertEqual(len(items), 1)
        self.assertIn("Factor", items[0]["text"])
        blocked = self.client.get(
            f"/api/staff/class/{self.class_id}/module-banks/M2/mc-search?q=vertex"
        )
        self.assertEqual(blocked.status_code, 200, blocked.get_json())
        self.assertEqual(blocked.get_json().get("items"), [])


if __name__ == "__main__":
    unittest.main()

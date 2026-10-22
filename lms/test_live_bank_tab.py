#!/usr/bin/env python3
"""Course-tab live bank uses the Import search and does not write catalogue items."""

from __future__ import annotations

import io
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
from test_catalog import _imscc_bytes  # noqa: E402


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


class PacklessBankWriteTests(unittest.TestCase):
    """Bank writes on a course that has a shell and no shared pack."""

    def setUp(self) -> None:
        """Teacher-owned pack-less course with its default shell banks."""
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
            teacher_user_id=int(self.teacher["id"]), ontario_code="SBI4U"
        )
        self.assertIsNone(self.offering.get("library_id"))
        self.shell_id = int(self.offering["shell_library_id"])
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="SBI4U",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["A1"],
            offering_id=int(self.offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )
        self.class_id = int(created["id"])
        self.module_bank_id = int(
            self.school.conn.execute(
                """
                SELECT id FROM question_banks
                WHERE library_id = ? AND import_key = 'shell-module-01'
                """,
                (self.shell_id,),
            ).fetchone()["id"]
        )
        self._login("teacher@gmail.com")

    def tearDown(self) -> None:
        """Close sqlite and remove the temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _login(self, email: str, portal: str = "staff") -> None:
        """Sign the client in through the dev verification path.

        Args:
            email: Account to open.
            portal: ``staff`` or ``it``.
        """
        self.client.get(f"/auth/google?portal={portal}")
        self.client.get(f"/auth/google/callback?email={email}&name=T")
        user = self.school.get_user_by_email(email)
        assert user is not None
        self.client.post("/verify-email", data={"code": user["verification_code"]})

    def _mc_body(self, stem: str) -> dict:
        """Return a multiple-choice write body.

        Args:
            stem: Question stem stored on the course library.
        """
        return {
            "bank_scope": "M1",
            "type": "mc",
            "stem_text": stem,
            "text": stem,
            "options": ["Red", "Blue", "Green", "Gold"],
            "correct_answer": "B",
            "points": 1,
        }

    def _search_ids(self) -> set[int]:
        """Return question ids the Question banks screen lists for M1."""
        rv = self.client.get(
            f"/api/staff/class/{self.class_id}/module-banks/M1/mc-search"
            "?staff_types=1"
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        body = rv.get_json()
        self.assertNotIn(
            "Ask Admin to attach a module pack",
            json.dumps(body),
        )
        self.assertEqual(int(body["library_id"]), self.shell_id)
        return {
            int(row.get("question_id") or row.get("id") or 0)
            for row in body.get("items") or []
        }

    def _bank_question_ids(self, bank_id: int) -> set[int]:
        """Return question ids stored on one bank.

        Args:
            bank_id: ``question_banks.id`` in the course library.
        """
        rv = self.client.get(
            f"/api/staff/class/{self.class_id}/question-bank/{int(bank_id)}"
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        body = rv.get_json()
        return {int(row["id"]) for row in body.get("questions") or []}

    def _library_of(self, question_id: int) -> tuple[int, str]:
        """Return the library id and import key for one question.

        Args:
            question_id: ``questions.id``.
        """
        row = self.school.conn.execute(
            """
            SELECT b.library_id, q.import_key
            FROM questions q
            JOIN question_banks b ON b.id = q.bank_id
            WHERE q.id = ?
            """,
            (int(question_id),),
        ).fetchone()
        self.assertIsNotNone(row)
        return int(row["library_id"]), str(row["import_key"])

    def _copies(self, question_id: int) -> int:
        """Count rows in the course library that share this question's key.

        Args:
            question_id: ``questions.id``.
        """
        library_id, import_key = self._library_of(question_id)
        count = self.school.conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM questions q
            JOIN question_banks b ON b.id = q.bank_id
            WHERE b.library_id = ? AND q.import_key = ?
            """,
            (library_id, import_key),
        ).fetchone()["n"]
        return int(count)

    def test_both_add_endpoints_list_on_both_surfaces(self) -> None:
        """Course-page and live-bank adds show on the bank and the grid."""
        screen = self.client.get(
            f"/staff/class/{self.class_id}?tab=question-banks"
        )
        self.assertEqual(screen.status_code, 200)
        self.assertNotIn(
            "Ask Admin to attach a module pack",
            screen.get_data(as_text=True),
        )
        catalog = self.client.get(
            f"/api/staff/class/{self.class_id}/components/question-banks"
        )
        self.assertEqual(catalog.status_code, 200, catalog.get_data(as_text=True))
        catalog_body = catalog.get_json()
        self.assertFalse(catalog_body.get("empty"))
        self.assertNotIn(
            "Ask Admin to attach a module pack",
            catalog_body.get("message") or "",
        )
        banks = self.client.get(
            f"/api/staff/class/{self.class_id}/module-banks?module=M1"
        )
        self.assertEqual(banks.status_code, 200, banks.get_data(as_text=True))
        self.assertNotIn(
            "Ask Admin to attach a module pack",
            json.dumps(banks.get_json()),
        )

        live = self.client.post(
            f"/api/staff/class/{self.class_id}/live-bank/questions",
            json=self._mc_body("Shell live stem"),
        )
        self.assertEqual(live.status_code, 200, live.get_data(as_text=True))
        live_question = live.get_json()["question"]
        live_id = int(live_question["id"])
        live_bank = int(live_question["bank_id"])
        self.assertEqual(self._library_of(live_id)[0], self.shell_id)
        self.assertIn(live_id, self._search_ids())
        self.assertIn(live_id, self._bank_question_ids(live_bank))

        course = self.client.post(
            f"/api/staff/class/{self.class_id}/question-bank/"
            f"{self.module_bank_id}/questions",
            json=self._mc_body("Shell course stem"),
        )
        self.assertEqual(course.status_code, 200, course.get_data(as_text=True))
        course_id = int(course.get_json()["question"]["id"])
        self.assertEqual(self._library_of(course_id)[0], self.shell_id)
        self.assertIn(course_id, self._search_ids())
        self.assertIn(course_id, self._bank_question_ids(self.module_bank_id))

        running = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C1/add-question",
            json={
                "type": "mc",
                "text": "Shell class stem",
                "options": ["Red", "Blue", "Green", "Gold"],
                "correct_index": 1,
                "save_to_bank": True,
                "bank_scope": "M1",
                "page_number": 1,
            },
        )
        self.assertEqual(running.status_code, 200, running.get_data(as_text=True))
        placed = running.get_json()["placement"]["item"]
        placed_id = int(placed["source_question_id"])
        self.assertEqual(self._library_of(placed_id)[0], self.shell_id)
        self.assertIn(placed_id, self._search_ids())

    def test_edit_and_delete(self) -> None:
        """Patch and delete change the course library and both lists."""
        saved = self.client.post(
            f"/api/staff/class/{self.class_id}/question-bank/"
            f"{self.module_bank_id}/questions",
            json=self._mc_body("Shell edit stem"),
        )
        self.assertEqual(saved.status_code, 200, saved.get_data(as_text=True))
        question_id = int(saved.get_json()["question"]["id"])
        edited = self.client.patch(
            f"/api/staff/class/{self.class_id}/question-bank/"
            f"{self.module_bank_id}/questions/{question_id}",
            json={
                "stem_text": "Shell edited stem",
                "options": ["Red", "Blue", "Green", "Gold"],
                "correct_answer": "B",
                "points": 1,
            },
        )
        self.assertEqual(edited.status_code, 200, edited.get_data(as_text=True))
        self.assertIn("Shell edited stem", json.dumps(edited.get_json()))
        self.assertIn(question_id, self._search_ids())
        removed = self.client.delete(
            f"/api/staff/class/{self.class_id}/question-bank/"
            f"{self.module_bank_id}/questions/{question_id}"
        )
        self.assertEqual(removed.status_code, 200, removed.get_data(as_text=True))
        self.assertNotIn(question_id, self._search_ids())
        self.assertNotIn(question_id, self._bank_question_ids(self.module_bank_id))

    def test_other_teacher_gets_403(self) -> None:
        """A teacher who does not own the course cannot write or list banks."""
        saved = self.client.post(
            f"/api/staff/class/{self.class_id}/live-bank/questions",
            json=self._mc_body("Shell owned stem"),
        )
        self.assertEqual(saved.status_code, 200, saved.get_data(as_text=True))
        question = saved.get_json()["question"]
        question_id = int(question["id"])
        bank_id = int(question["bank_id"])
        self.school.register_staff("other@gmail.com")
        self._login("other@gmail.com")
        targets = [
            (
                "POST",
                f"/api/staff/class/{self.class_id}/live-bank/questions",
                self._mc_body("Nope"),
            ),
            (
                "POST",
                f"/api/staff/class/{self.class_id}/question-bank/{bank_id}/questions",
                self._mc_body("Nope"),
            ),
            (
                "PATCH",
                f"/api/staff/class/{self.class_id}/question-bank/"
                f"{bank_id}/questions/{question_id}",
                {"stem_text": "Nope", "options": ["Red", "Blue"], "correct_answer": "A"},
            ),
        ]
        for method, url, body in targets:
            rv = self.client.open(url, method=method, json=body)
            self.assertEqual(rv.status_code, 403, rv.get_data(as_text=True))
        deleted = self.client.delete(
            f"/api/staff/class/{self.class_id}/question-bank/"
            f"{bank_id}/questions/{question_id}"
        )
        self.assertEqual(deleted.status_code, 403, deleted.get_data(as_text=True))
        listed = self.client.get(
            f"/api/staff/class/{self.class_id}/question-bank/{bank_id}"
        )
        self.assertEqual(listed.status_code, 403, listed.get_data(as_text=True))
        grid = self.client.get(
            f"/api/staff/class/{self.class_id}/module-banks/M1/mc-search"
        )
        self.assertEqual(grid.status_code, 403, grid.get_data(as_text=True))

    def test_backfill_keeps_the_question_once(self) -> None:
        """A later pack merge leaves the shell question in place, once."""
        saved = self.client.post(
            f"/api/staff/class/{self.class_id}/live-bank/questions",
            json=self._mc_body("Shell kept stem"),
        )
        self.assertEqual(saved.status_code, 200, saved.get_data(as_text=True))
        question_id = int(saved.get_json()["question"]["id"])
        self.assertEqual(self._copies(question_id), 1)
        self._login("solutions@mckenzian.com", portal="it")
        merged = self.client.post(
            f"/it/offerings/{int(self.offering['id'])}/module-pack/backfill",
            data={"module_pack": (io.BytesIO(_imscc_bytes()), "course.imscc")},
            follow_redirects=False,
        )
        self.assertEqual(merged.status_code, 302, merged.get_data(as_text=True))
        offering = self.school.get_offering(int(self.offering["id"]))
        self.assertIsNone(offering.get("library_id"))
        self.assertEqual(int(offering["shell_library_id"]), self.shell_id)
        self.assertEqual(self._library_of(question_id)[0], self.shell_id)
        self.assertEqual(self._copies(question_id), 1)
        self._login("teacher@gmail.com")
        self.assertIn(question_id, self._search_ids())
        self.assertIn(
            question_id,
            self._bank_question_ids(int(saved.get_json()["question"]["bank_id"])),
        )


if __name__ == "__main__":
    unittest.main()

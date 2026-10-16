#!/usr/bin/env python3
"""MCK-262: live-class starting values, bulk remove, server sort, and points."""

from __future__ import annotations

import os
import subprocess
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


class QuestionBankGapTests(unittest.TestCase):
    """The extra bank-page gaps folded into the question-bank layout change."""

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

    def _run_node(self, name: str) -> None:
        """Run one static node test and require it to print ok."""
        script = LMS_DIR / "static" / name
        result = subprocess.run(
            ["node", str(script)],
            cwd=str(script.parent),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("ok", result.stdout)

    def _scope_ids(self, scope: str, needle: str) -> set[int]:
        """Question ids on one bank tab, hiding Course Wide pins from modules."""
        if scope == "all":
            result = self.school.search_bank_scope_mcs(
                self.library_id,
                "all",
                1,
                needle,
                kind="any",
                staff_types=True,
                limit=500,
            )
        elif scope == "course":
            result = self.school.search_module_bank_mcs(
                self.library_id,
                1,
                needle,
                kind="any",
                scope_policy="course_only",
                include_course_placed=False,
                staff_types=True,
                limit=500,
            )
        else:
            result = self.school.search_module_bank_mcs(
                self.library_id,
                int(scope[1:]),
                needle,
                kind="any",
                include_course_placed=False,
                staff_types=True,
                limit=500,
            )
        return {int(row["question_id"]) for row in result["items"]}

    def test_live_start_seeds_response_and_results(self) -> None:
        """Stored group and hidden-results defaults are the live card's start."""
        self._run_node("live_start.test.mjs")
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn('from "/static/live_start.js"', js)
        paint = js.split("function paintLiveQuestionCards()")[1].split(
            "function individualLifecycleResultsHtml("
        )[0]
        self.assertIn("bankLiveStart(item,", paint)
        self.assertIn("card.response_mode = start.response_mode", paint)
        self.assertIn("card.show_live_results = start.show_live_results", paint)
        self.assertIn("start.live_points", paint)

    def test_bulk_remove_is_one_request(self) -> None:
        """Remove deletes every checked question, and a bad row deletes none."""
        first = self._add_bank(
            {
                "bank_scope": "M1",
                "type": "mc",
                "stem_text": "mck262remove first",
                "options": ["A", "B", "C", "D"],
                "correct_answer": "A",
            }
        )
        second = self._add_bank(
            {
                "bank_scope": "M1",
                "type": "mc",
                "stem_text": "mck262remove second",
                "options": ["A", "B", "C", "D"],
                "correct_answer": "B",
            }
        )
        removed = self.client.post(
            f"/api/staff/class/{self.class_id}/question-bank/bulk",
            json={
                "action": "remove",
                "questions": [
                    {"bank_id": first["bank_id"], "question_id": first["id"]},
                    {"bank_id": second["bank_id"], "question_id": second["id"]},
                ],
            },
        )
        self.assertEqual(removed.status_code, 200, removed.get_data(as_text=True))
        self.assertEqual(removed.get_json()["updated"], 2)
        gone = self.school.search_module_bank_mcs(
            self.library_id, 1, "mck262remove", kind="any", staff_types=True
        )
        self.assertEqual(gone["items"], [])
        kept = self._add_bank(
            {
                "bank_scope": "M1",
                "type": "mc",
                "stem_text": "mck262remove stays",
                "options": ["A", "B", "C", "D"],
                "correct_answer": "A",
            }
        )
        rejected = self.client.post(
            f"/api/staff/class/{self.class_id}/question-bank/bulk",
            json={
                "action": "remove",
                "questions": [
                    {"bank_id": kept["bank_id"], "question_id": kept["id"]},
                    {"bank_id": kept["bank_id"], "question_id": 999999},
                ],
            },
        )
        self.assertEqual(rejected.status_code, 404)
        still = self.school.search_module_bank_mcs(
            self.library_id, 1, "mck262remove stays", kind="any", staff_types=True
        )
        self.assertTrue(any(int(row["question_id"]) == int(kept["id"]) for row in still["items"]))
        js = (LMS_DIR / "static" / "course_question_banks.js").read_text(encoding="utf-8")
        self.assertIn("Remove ${count} questions?", js)
        self.assertIn('action: "remove"', js)
        self.assertNotIn('method: "DELETE"', js)
        self.assertNotIn("window.confirm(", js)

    def test_server_sort_runs_before_the_cap(self) -> None:
        """Type order is applied before the row cap, and a bad sort is rejected."""
        rank = self._add_bank(
            {
                "bank_scope": "M1",
                "type": "rank",
                "stem_text": "mck262sort rank these",
                "options": ["narrow", "medium", "wide"],
                "rank_key": [2, 1, 0],
            }
        )
        numeric = self._add_bank(
            {
                "bank_scope": "M1",
                "type": "numeric",
                "stem_text": "mck262sort numeric width",
                "correct_answer": "4",
            }
        )
        mc = self._add_bank(
            {
                "bank_scope": "M1",
                "type": "mc",
                "stem_text": "mck262sort which width",
                "options": ["1", "2", "3", "4"],
                "correct_answer": "B",
            }
        )
        by_type = self.school.search_module_bank_mcs(
            self.library_id,
            1,
            "mck262sort",
            limit=1,
            kind="any",
            staff_types=True,
            sort="type",
        )
        self.assertEqual(by_type["filtered"], 3)
        self.assertEqual(len(by_type["items"]), 1)
        self.assertEqual(by_type["items"][0]["question_type"], "mc")
        self.assertEqual(int(by_type["items"][0]["question_id"]), int(mc["id"]))
        by_bank = self.school.search_module_bank_mcs(
            self.library_id,
            1,
            "mck262sort",
            limit=1,
            kind="any",
            staff_types=True,
            sort="bank",
        )
        self.assertEqual(int(by_bank["items"][0]["question_id"]), int(rank["id"]))
        self.assertLess(int(rank["id"]), int(numeric["id"]))
        page = self.client.get(
            f"/api/staff/class/{self.class_id}/module-banks/M1/mc-search",
            query_string={"q": "mck262sort", "staff_types": "1", "kind": "any", "sort": "type"},
        )
        self.assertEqual(page.status_code, 200, page.get_data(as_text=True))
        self.assertEqual(page.get_json()["items"][0]["question_type"], "mc")
        bad = self.client.get(
            f"/api/staff/class/{self.class_id}/module-banks/M1/mc-search",
            query_string={"sort": "nope"},
        )
        self.assertEqual(bad.status_code, 400)
        js = (LMS_DIR / "static" / "course_question_banks.js").read_text(encoding="utf-8")
        self.assertIn("Showing ${shown} of ${matched}", js)
        self.assertIn('params.set("sort", sort)', js)

    def test_points_use_one_source(self) -> None:
        """A question with its own points, and no live award, is not forced to 1."""
        self._run_node("question_points.test.mjs")
        editor = (LMS_DIR / "static" / "question_editor.js").read_text(encoding="utf-8")
        banks = (LMS_DIR / "static" / "course_question_banks.js").read_text(encoding="utf-8")
        self.assertIn("questionPointsValue(question)", editor)
        self.assertIn("questionPointsValue(item)", banks)
        self.assertNotIn('question.live_points == null || question.live_points === "" ? "1"', editor)
        self.assertIn('stepQuestion(key === "j" ? 1 : -1)', editor)
        self.assertNotIn('stepQuestion(key === "j" ? -1 : 1)', editor)

    def test_all_covers_m0_through_course_and_survives_a_move(self) -> None:
        """All is M0–M8 plus Course Wide, and a Move keeps that sum."""
        self.assertEqual(
            self.school._bank_scope_module_numbers("all", 1),
            list(range(0, 9)),
        )
        self.assertEqual(
            self.school._bank_scope_module_numbers("course", 1),
            list(range(1, 9)),
        )
        needle = "mck262scope"
        m0 = self._add_bank(
            {
                "bank_scope": "M0",
                "type": "mc",
                "stem_text": f"{needle} intro",
                "options": ["A", "B", "C", "D"],
                "correct_answer": "A",
            }
        )
        m1 = self._add_bank(
            {
                "bank_scope": "M1",
                "type": "mc",
                "stem_text": f"{needle} linear",
                "options": ["A", "B", "C", "D"],
                "correct_answer": "A",
            }
        )
        course = self._add_bank(
            {
                "bank_scope": "course",
                "type": "mc",
                "stem_text": f"{needle} wide",
                "options": ["A", "B", "C", "D"],
                "correct_answer": "A",
            }
        )
        ours = {int(m0["id"]), int(m1["id"]), int(course["id"])}

        def placed() -> dict[str, set[int]]:
            """Our questions on each exclusive tab."""
            tabs = [f"M{n}" for n in range(0, 9)] + ["course"]
            return {tab: self._scope_ids(tab, needle) & ours for tab in tabs}

        def assert_adds_up(found: dict[str, set[int]]) -> None:
            """Each of our questions is on exactly one tab, and All has them all."""
            self.assertEqual(set().union(*found.values()), ours)
            self.assertEqual(sum(len(ids) for ids in found.values()), len(ours))
            self.assertEqual(self._scope_ids("all", needle) & ours, ours)

        before = placed()
        self.assertIn(int(m0["id"]), before["M0"])
        self.assertIn(int(m1["id"]), before["M1"])
        self.assertIn(int(course["id"]), before["course"])
        self.assertNotIn(int(m0["id"]), self._scope_ids("course", needle))
        assert_adds_up(before)
        moved = self.client.post(
            f"/api/staff/class/{self.class_id}/question-bank/bulk",
            json={
                "action": "move",
                "bank_scope": "M3",
                "questions": [{"bank_id": m1["bank_id"], "question_id": m1["id"]}],
            },
        )
        self.assertEqual(moved.status_code, 200, moved.get_data(as_text=True))
        after = placed()
        self.assertNotIn(int(m1["id"]), after["M1"])
        self.assertIn(int(m1["id"]), after["M3"])
        assert_adds_up(after)
        course_union = self.school.search_bank_scope_mcs(
            self.library_id, "course", 1, needle, kind="any", staff_types=True, limit=500
        )
        course_ids = {int(row["question_id"]) for row in course_union["items"]}
        self.assertNotIn(int(m0["id"]), course_ids)

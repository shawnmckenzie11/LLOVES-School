#!/usr/bin/env python3
"""MCK-79: Import from bank lists each module's top 6 Contest Questions.

Contest Questions are active ``live_problems`` with kind ``contest`` (or
``cemc``) for the class's course, not question-bank rows (prod has 0 bank
rows tagged contest). A ``module_hint`` part like ``M1C1`` groups a problem
under Module 1; anything else goes to one course-wide Contest group. The
list endpoint is lazy and read-only and links no banks. The batch import
uses the deck's Add New path (a poll card, Kind Contest), only accepts a
group's current top six, de-duplicates ids and is all-or-nothing.

Client: ``content_questions_help.js`` grouping, labels and pick payload run
in node; the picker, its CSS and the Run Live Class wiring are checked as
source.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
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
from live_content_questions import (  # noqa: E402
    CONTENT_QUESTIONS_PER_MODULE,
    clean_content_picks,
    group_key,
    module_summaries,
    problem_group,
    selectable_live_modules,
)
from school_db import _now  # noqa: E402

NODE = shutil.which("node")


def _problem(
    school: Any,
    code: str,
    hint: str,
    title: str,
    *,
    kind: str = "contest",
    sort_order: int = 0,
    active: int = 1,
) -> int:
    """Insert one live problem with a distinct stem and task."""
    cur = school.conn.execute(
        """
        INSERT INTO live_problems (
            ontario_code, module_hint, kind, title, stem_html, task_html,
            active, sort_order, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            code,
            hint,
            kind,
            title,
            f"<p>{title} stem</p>",
            f"<p>{title} task</p>",
            int(active),
            int(sort_order),
            _now(),
        ),
    )
    school.conn.commit()
    return int(cur.lastrowid)


def _bank(school: Any, library_id: int, title: str, key: str) -> int:
    """Insert one question bank."""
    cur = school.conn.execute(
        """
        INSERT INTO question_banks (
            library_id, import_key, title, settings_json, created_at
        ) VALUES (?, ?, ?, '{}', ?)
        """,
        (int(library_id), key, title, _now()),
    )
    school.conn.commit()
    return int(cur.lastrowid)


def _mc(school: Any, bank_id: int, key: str, stem: str) -> int:
    """Insert one MC question (only for the unchanged import-mc guard)."""
    payload = {
        "stem_html": stem,
        "points_possible": 1.0,
        "choices": [
            {"id": "a", "html": f"{stem} yes", "correct": True},
            {"id": "b", "html": f"{stem} no", "correct": False},
        ],
        "correct_ids": ["a"],
    }
    cur = school.conn.execute(
        """
        INSERT INTO questions (
            bank_id, import_key, item_type, title, payload_json, created_at
        ) VALUES (?, ?, 'multiple_choice_question', ?, ?, ?)
        """,
        (int(bank_id), key, stem, json.dumps(payload), _now()),
    )
    school.conn.commit()
    return int(cur.lastrowid)


class SeededGroupingTests(unittest.TestCase):
    """The shipped live_problems seed groups as the PR describes."""

    def test_seed_groups_by_module_hint(self) -> None:
        """MCF3M: two M1 lesson problems, six strand-only in Contest; MCR3U: one M1."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
            school = app.config["SCHOOL_DB"]
            try:
                mcf = selectable_live_modules("MCF3M")
                self.assertEqual(
                    module_summaries(school, "MCF3M", mcf, "M1"),
                    [
                        {"module": "M1", "label": "Module 1", "count": 2},
                        {"module": "COURSE", "label": "Contest", "count": 6},
                    ],
                )
                mcr = selectable_live_modules("MCR3U")
                self.assertEqual(
                    module_summaries(school, "MCR3U", mcr, "M2"),
                    [
                        {"module": "M1", "label": "Module 1", "count": 1},
                        {"module": "M2", "label": "Module 2", "count": 0},
                    ],
                )
            finally:
                school.close()

    def test_problem_group_rules(self) -> None:
        """M<n>C<k> maps to a listed module; strands and others go to Contest."""
        mods = ["M1", "M2", "M3"]
        self.assertEqual(problem_group("A/M1C1", mods), "M1")
        self.assertEqual(problem_group("m2c3", mods), "M2")
        self.assertEqual(problem_group("A", mods), "COURSE")
        self.assertEqual(problem_group("", mods), "COURSE")
        self.assertEqual(problem_group("A/M7C1", mods), "COURSE")
        self.assertEqual(group_key("course", mods), "COURSE")
        self.assertEqual(group_key("M7", mods), "")


class ContentQuestionsApiTests(unittest.TestCase):
    """List and batch import over HTTP as the class teacher."""

    def setUp(self) -> None:
        """MCR3U class with controlled contest live problems (seed ones off)."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite", data_dir=root, testing=True
        )
        self.client = self.app.test_client()
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
        )
        self.library_id = int(self.school.create_library("MCR3U", origin="upload")["id"])
        self.school.attach_library(int(self.offering["id"]), self.library_id)
        school = self.school
        school.conn.execute("UPDATE live_problems SET active = 0 WHERE ontario_code = 'MCR3U'")
        school.conn.commit()
        # Module 1: eight contest problems out of id order (sort_order rules),
        # plus warmup / standard rows with the same hint.
        self.m1 = [
            _problem(school, "MCR3U", "A/M1C1", f"Module one contest {n}", sort_order=n)
            for n in range(8, 0, -1)
        ]
        self.m1.reverse()  # ids descend; sort_order ascending == m1[0] first
        self.m1_warmup = _problem(school, "MCR3U", "A/M1C1", "Warm one", kind="warmup")
        self.m1_other = _problem(school, "MCR3U", "A/M1C2", "Number talk", kind="number_talk")
        self.m1_off = _problem(school, "MCR3U", "A/M1C1", "Retired", active=0, sort_order=1)
        # Module 2: kind spelled three ways.
        self.m2 = [
            _problem(school, "MCR3U", "B/M2C1", "Module two contest", sort_order=10),
            _problem(school, "MCR3U", "B/M2C3", "Module two CEMC", kind="CEMC", sort_order=11),
            _problem(school, "MCR3U", "M2C2", "Module two spaced", kind=" Contest ", sort_order=12),
        ]
        # Course-wide Contest group: strand-only and an unlisted module.
        self.course = [
            _problem(school, "MCR3U", "A", "Strand A contest", sort_order=5),
            _problem(school, "MCR3U", "A/M9C1", "Unlisted module contest", sort_order=6),
        ]
        # Another course's contest problem never shows.
        self.mcf = _problem(school, "MCF3M", "A/M1C1", "Other course", sort_order=0)
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
        """Close sqlite and remove the temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _login(self, email: str) -> None:
        """Sign the client in as one staff account."""
        self.client.get("/auth/google?portal=staff")
        self.client.get(f"/auth/google/callback?email={email}&name=T")
        user = self.school.get_user_by_email(email)
        assert user is not None
        self.client.post("/verify-email", data={"code": user["verification_code"]})

    def _list(self, module: str = "") -> dict[str, Any]:
        """GET the Contest Questions list (optionally one group's top 6)."""
        query = f"?module={module}" if module else ""
        rv = self.client.get(
            f"/api/staff/class/{self.class_id}/live-lessons/contest-questions{query}"
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        return rv.get_json()

    def _group(self, module: str) -> dict[str, Any]:
        """Return one group's loaded rows."""
        group = self._list(module)["group"]
        self.assertIsNotNone(group)
        return group

    def _placement_count(self) -> int:
        """Placement rows on this class's decks."""
        row = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM class_live_playlist_placements WHERE class_id = ?",
            (self.class_id,),
        ).fetchone()
        return int(row["n"])

    def _import(self, picks: list[dict[str, Any]], module: str = "M1", slot: str = "C1"):
        """POST a batch Contest Questions import onto page 4."""
        return self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/{module}/{slot}/import-contest-questions",
            json={"picks": picks, "page_number": 4, "stage": "round"},
        )

    def _deck_questions(self, module: str = "M1", slot: str = "C1") -> list[dict[str, Any]]:
        """Live metadata questions on one class deck."""
        rv = self.client.get(
            f"/api/staff/class/{self.class_id}/live-lessons/{module}/{slot}/deck"
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        return list(rv.get_json()["live_metadata"].get("questions") or [])

    def _deck_problem_ids(self) -> list[int]:
        """``live_problem_id`` of every imported Contest card on M1 C1."""
        return [
            int(q["live_problem_id"])
            for q in self._deck_questions()
            if q.get("live_problem_id")
        ]

    def test_list_is_lazy_groups_plus_one_group(self) -> None:
        """No module: summaries only. ?module=M2: only M2's rows."""
        bare = self._list()
        self.assertEqual(bare["source"], "live_problems")
        self.assertIsNone(bare["group"])
        self.assertEqual(bare["per_module"], 6)
        self.assertEqual(CONTENT_QUESTIONS_PER_MODULE, 6)
        self.assertEqual(
            bare["modules"],
            [
                {"module": "M1", "label": "Module 1", "count": 6},
                {"module": "M2", "label": "Module 2", "count": 3},
                {"module": "COURSE", "label": "Contest", "count": 2},
            ],
        )
        one = self._list("M5")
        self.assertIn({"module": "M5", "label": "Module 5", "count": 0}, one["modules"])
        self.assertEqual(one["group"], {"module": "M5", "label": "Module 5", "count": 0, "items": []})

    def test_top_six_in_sort_order_contest_only(self) -> None:
        """Module 1: first six by sort_order; warmup, other kinds, inactive out."""
        group = self._group("M1")
        ids = [item["question_id"] for item in group["items"]]
        self.assertEqual(ids, self.m1[:6])
        self.assertEqual([i["content_rank"] for i in group["items"]], [1, 2, 3, 4, 5, 6])
        for bad in (self.m1_warmup, self.m1_other, self.m1_off, self.mcf):
            self.assertNotIn(bad, ids)
        first = group["items"][0]
        self.assertEqual(first["question_title"], "Module one contest 1")
        self.assertEqual(first["type"], "poll")
        self.assertEqual(first["kind"], "contest")
        self.assertIn("Module one contest 1 stem", first["text"])
        self.assertIn("Module one contest 1 task", first["text"])
        self.assertLess(first["text"].index("stem"), first["text"].index("task"))

    def test_kind_spellings_and_contest_group(self) -> None:
        """CEMC and ' Contest ' count; strand-only and unlisted modules go to Contest."""
        self.assertEqual([i["question_id"] for i in self._group("M2")["items"]], self.m2)
        course = self._group("COURSE")
        self.assertEqual(course["label"], "Contest")
        self.assertEqual([i["question_id"] for i in course["items"]], self.course)

    def test_list_never_links_banks(self) -> None:
        """Listing reads live_problems only; module bank links are untouched."""
        before = {n: self.school.list_module_bank_links(self.library_id, n) for n in range(1, 9)}
        self._list()
        self._list("M1")
        self._list("COURSE")
        after = {n: self.school.list_module_bank_links(self.library_id, n) for n in range(1, 9)}
        self.assertEqual(before, after)

    def test_bad_module_query_is_400(self) -> None:
        """Unknown group tokens are refused."""
        for bad in ("M9", "X", "CONTEST"):
            rv = self.client.get(
                f"/api/staff/class/{self.class_id}/live-lessons/contest-questions?module={bad}"
            )
            self.assertEqual(rv.status_code, 400, bad)

    def test_import_selected_from_two_groups_onto_current_page(self) -> None:
        """Ticked Module 1 + Contest rows land as poll cards, Kind Contest, in order."""
        rv = self._import(
            [
                {"question_id": self.m1[2], "module": "M1"},
                {"question_id": self.course[0], "module": "COURSE"},
            ],
            module="M1",
            slot="C1",
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        body = rv.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["imported"], 2)
        rows = self.school.conn.execute(
            """
            SELECT page_number, item_json FROM class_live_playlist_placements
            WHERE class_id = ? AND module = 'M1' AND slot = 'C1' ORDER BY id
            """,
            (self.class_id,),
        ).fetchall()
        self.assertEqual([int(r["page_number"]) for r in rows], [4, 4])
        items = [json.loads(r["item_json"]) for r in rows]
        self.assertEqual([i["live_problem_id"] for i in items], [self.m1[2], self.course[0]])
        for item in items:
            self.assertEqual(item["type"], "poll")
            self.assertEqual(item["bank_kind"], "contest")
            self.assertTrue(str(item["id"]).startswith("staff-q-"))
        self.assertEqual(items[0]["question_title"], "Module one contest 3")
        self.assertEqual(self._deck_problem_ids(), [self.m1[2], self.course[0]])

    def test_import_refuses_rows_outside_the_top_six(self) -> None:
        """7th-ranked, warmup, other course, or a wrong group: 404, nothing written."""
        for pick in (
            {"question_id": self.m1[6], "module": "M1"},
            {"question_id": self.m1_warmup, "module": "M1"},
            {"question_id": self.mcf, "module": "M1"},
            {"question_id": self.course[0], "module": "M1"},
        ):
            rv = self._import([pick])
            self.assertEqual(rv.status_code, 404, pick)
        body = rv.get_json()
        self.assertEqual(
            body["error"],
            "One pick is no longer in Module 1's top 6 Contest Questions. "
            "Nothing was imported. Reload the list and pick again.",
        )
        self.assertEqual(body["stale"], [{"question_id": self.course[0], "module": "M1"}])
        self.assertEqual(self._placement_count(), 0)

    def test_bad_bodies_are_400(self) -> None:
        """Empty, malformed, or oversized pick lists."""
        self.assertEqual(self._import([]).status_code, 400)
        self.assertEqual(self._import([{"question_id": "x", "module": "M1"}]).status_code, 400)
        self.assertEqual(self._import([{"question_id": 1, "module": "M9"}]).status_code, 400)
        many = [{"question_id": n, "module": "M1"} for n in range(1, 60)]
        self.assertEqual(self._import(many).status_code, 400)
        self.assertEqual(
            clean_content_picks([{"question_id": 3, "module": "course"}], ["M1"]),
            [(3, "COURSE")],
        )

    def test_same_problem_twice_imports_once(self) -> None:
        """The same id posted twice lands one card."""
        rv = self._import(
            [
                {"question_id": self.m1[0], "module": "M1"},
                {"question_id": self.m1[0], "module": "M1"},
            ]
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        self.assertEqual(rv.get_json()["imported"], 1)
        self.assertEqual(self._deck_problem_ids(), [self.m1[0]])

    def _flaky(self, fail_on: int, *, after_real: bool = False):
        """Patch the Add New path to raise on call ``fail_on``.

        With ``after_real`` the real placement commits first, then raises
        (a failure after the row is written).
        """
        real = self.school.add_staff_question_to_class_playlist
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            """Place, or fail like a vanished deck."""
            calls["n"] += 1
            if calls["n"] == fail_on:
                if after_real:
                    real(*args, **kwargs)
                raise KeyError("deck vanished")
            return real(*args, **kwargs)

        self.school.add_staff_question_to_class_playlist = flaky
        self.addCleanup(setattr, self.school, "add_staff_question_to_class_playlist", real)
        return calls

    def _three(self):
        """Three picks across two groups."""
        return self._import(
            [
                {"question_id": self.m1[0], "module": "M1"},
                {"question_id": self.m1[1], "module": "M1"},
                {"question_id": self.course[0], "module": "COURSE"},
            ]
        )

    def test_failed_placement_rolls_back_the_whole_batch(self) -> None:
        """2nd placement fails: nothing from the batch stays; earlier copy survives."""
        first = self._import([{"question_id": self.m1[0], "module": "M1"}])
        self.assertEqual(first.status_code, 200, first.get_data(as_text=True))
        before = self._placement_count()
        calls = self._flaky(2)
        rv = self._three()
        self.assertEqual(rv.status_code, 409, rv.get_data(as_text=True))
        body = rv.get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["imported"], 0)
        self.assertEqual(body["landed"], [])
        self.assertEqual(body["rolled_back"], 1)
        self.assertIn("Import stopped and was undone; nothing was imported.", body["error"])
        self.assertEqual(calls["n"], 2)
        self.assertEqual(self._placement_count(), before)
        self.assertEqual(self._deck_problem_ids(), [self.m1[0]])

    def test_row_committed_before_a_raise_is_removed(self) -> None:
        """LOW-5a: the failing call wrote its row, then raised; it is undone too."""
        self._flaky(2, after_real=True)
        rv = self._three()
        self.assertEqual(rv.status_code, 409, rv.get_data(as_text=True))
        self.assertEqual(rv.get_json()["rolled_back"], 2)
        self.assertEqual(self._placement_count(), 0)
        self.assertEqual(self._deck_problem_ids(), [])

    def test_resync_failure_during_undo_still_undoes(self) -> None:
        """LOW-5b: a resync error while undoing is logged, rows still removed."""
        self._flaky(3)
        real_sync = self.school._sync_playlist_change
        state = {"undoing": False}

        def sync(*args, **kwargs):
            """Fail only the rollback's resync (after the deletes)."""
            if state["undoing"]:
                raise RuntimeError("live session gone")
            return real_sync(*args, **kwargs)

        real_add = self.school.add_staff_question_to_class_playlist

        def mark(*args, **kwargs):
            """Flag the undo once the patched placement raises."""
            try:
                return real_add(*args, **kwargs)
            except KeyError:
                state["undoing"] = True
                raise

        self.school._sync_playlist_change = sync
        self.school.add_staff_question_to_class_playlist = mark
        self.addCleanup(setattr, self.school, "_sync_playlist_change", real_sync)
        with self.assertLogs("live_content_questions", level="ERROR"):
            rv = self._three()
        self.assertEqual(rv.status_code, 409, rv.get_data(as_text=True))
        self.assertEqual(self._placement_count(), 0)

    def test_delete_failure_during_undo_is_500_with_landed(self) -> None:
        """LOW-5b: if the undo delete fails, say so (500) and list what landed."""
        self._flaky(2)
        real_conn = self.school.conn

        class FailingDelete:
            """Connection proxy that refuses the rollback DELETE."""

            def __getattr__(self, name: str) -> Any:
                """Delegate everything else."""
                return getattr(real_conn, name)

            def execute(self, sql: str, *args: Any) -> Any:
                """Raise on the batch delete."""
                if sql.strip().startswith("DELETE FROM class_live_playlist_placements"):
                    raise sqlite3.OperationalError("database is locked")
                return real_conn.execute(sql, *args)

        add = self.school.add_staff_question_to_class_playlist

        def swap(*args, **kwargs):
            """Swap in the proxy right as the 2nd placement fails."""
            try:
                return add(*args, **kwargs)
            except KeyError:
                self.school.conn = FailingDelete()
                raise

        self.school.add_staff_question_to_class_playlist = swap
        self.addCleanup(setattr, self.school, "conn", real_conn)
        with self.assertLogs("live_content_questions", level="ERROR"):
            rv = self._three()
        self.school.conn = real_conn
        self.assertEqual(rv.status_code, 500, rv.get_data(as_text=True))
        body = rv.get_json()
        self.assertIn("could not be fully undone", body["error"])
        self.assertEqual(body["landed"], [self.m1[0]])

    def test_validation_failure_places_nothing(self) -> None:
        """A bad pick anywhere in the batch is refused before any placement."""
        calls = self._flaky(99)
        rv = self._import(
            [
                {"question_id": self.m1[0], "module": "M1"},
                {"question_id": self.m1[7], "module": "M1"},
            ]
        )
        self.assertEqual(rv.status_code, 404)
        self.assertEqual(calls["n"], 0)
        self.assertEqual(self._placement_count(), 0)

    def test_single_import_mc_guard_is_unchanged(self) -> None:
        """import-mc still refuses another module's bank question."""
        m1_bank = _bank(self.school, self.library_id, "Module 1 Test", "bank:m1-test")
        _mc(self.school, m1_bank, "m1-1", "Module one bank")
        m2_bank = _bank(self.school, self.library_id, "Module 2 Test", "bank:m2-test")
        m2_q = _mc(self.school, m2_bank, "m2-1", "Module two bank")
        self.school.confirm_module_bank_links(self.library_id, 1, [m1_bank])
        self.school.confirm_module_bank_links(self.library_id, 2, [m2_bank])
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C1/import-mc",
            json={"question_id": m2_q, "page_number": 4},
        )
        self.assertEqual(rv.status_code, 404)

    def test_other_teacher_is_forbidden(self) -> None:
        """Only the class teacher can list or import."""
        self.school.register_staff("other@gmail.com")
        self.client = self.app.test_client()
        self._login("other@gmail.com")
        rv = self.client.get(f"/api/staff/class/{self.class_id}/live-lessons/contest-questions")
        self.assertEqual(rv.status_code, 403)
        rv = self._import([{"question_id": self.m1[0], "module": "M1"}])
        self.assertEqual(rv.status_code, 403)

    # MCK-161: polish from the #225 gate (LOW-7, LOW-8, INFO-1..4).

    def test_mck161_low7_noncanonical_target_lands_and_rolls_back(self) -> None:
        """``/m01/c1/`` lands on M1/C1; a failed batch there is really undone."""
        ok = self._import([{"question_id": self.m1[0], "module": "M1"}], module="m01", slot="c1")
        self.assertEqual(ok.status_code, 200, ok.get_data(as_text=True))
        self.assertEqual(self._deck_problem_ids(), [self.m1[0]])
        before = self._placement_count()
        self._flaky(3)
        rv = self._import(
            [
                {"question_id": self.m1[1], "module": "M1"},
                {"question_id": self.m1[2], "module": "M1"},
                {"question_id": self.course[0], "module": "COURSE"},
            ],
            module="M01",
            slot="c1",
        )
        self.assertEqual(rv.status_code, 409, rv.get_data(as_text=True))
        self.assertEqual(rv.get_json()["rolled_back"], 2)
        self.assertEqual(self._placement_count(), before)
        self.assertEqual(self._deck_problem_ids(), [self.m1[0]])

    def test_mck161_low7_unknown_module_is_400(self) -> None:
        """A module Add New would refuse is a 400 before anything is read."""
        for module in ("MX", "M9", "0"):
            rv = self._import([{"question_id": self.m1[0], "module": "M1"}], module=module)
            self.assertEqual(rv.status_code, 400, module)
        self.assertEqual(self._placement_count(), 0)

    def test_mck161_low8_stale_copy_has_no_ids_and_no_double_contest(self) -> None:
        """Course-wide, one module, several groups: clean copy plus the stale list."""
        self.school.conn.execute(
            "UPDATE live_problems SET active = 0 WHERE id IN (?, ?)",
            (self.course[1], self.m1[5]),
        )
        self.school.conn.commit()
        one = self._import(
            [
                {"question_id": self.course[0], "module": "COURSE"},
                {"question_id": self.course[1], "module": "COURSE"},
            ]
        )
        self.assertEqual(one.status_code, 404)
        body = one.get_json()
        self.assertEqual(
            body["error"],
            "One pick is no longer in the course-wide Contest group's top 6. "
            "Nothing was imported. Reload the list and pick again.",
        )
        self.assertEqual(body["stale"], [{"question_id": self.course[1], "module": "COURSE"}])
        self.assertEqual(body["imported"], 0)
        many = self._import(
            [
                {"question_id": self.m1[5], "module": "M1"},
                {"question_id": self.course[1], "module": "COURSE"},
                {"question_id": self.m1[0], "module": "M1"},
            ]
        ).get_json()
        self.assertEqual(
            many["error"],
            "2 picks are no longer in their group's top 6 Contest Questions. "
            "Nothing was imported. Reload the list and pick again.",
        )
        self.assertEqual(
            many["stale"],
            [
                {"question_id": self.m1[5], "module": "M1"},
                {"question_id": self.course[1], "module": "COURSE"},
            ],
        )
        for text in (body["error"], many["error"]):
            self.assertNotIn("Contest Contest", text)
            self.assertNotIn("problem", text)
            self.assertNotIn(str(self.course[1]), text)
        self.assertEqual(self._placement_count(), 0)

    def test_mck161_info1_extra_item_is_an_allowlist(self) -> None:
        """Only live_problem_id / question_title / contest_batch reach item_json."""
        placement = self.school.add_staff_question_to_class_playlist(
            self.class_id,
            "M1",
            "C1",
            question_type="poll",
            text="Real text",
            page_number=1,
            stage="round",
            bank_kind="contest",
            extra_item={
                "live_problem_id": 51,
                "question_title": "Title",
                "contest_batch": "mck79-x",
                "stage": "join",
                "page_number": 9,
                "type": "mc",
                "options": ["a"],
                "correct_answer": "A",
                "bank_kind": "warmup",
                "import_source": "x",
                "text": "OVERRIDE",
                "id": "evil",
            },
        )
        row = self.school.conn.execute(
            "SELECT stage, page_number, item_json FROM class_live_playlist_placements WHERE id = ?",
            (int(placement["id"]),),
        ).fetchone()
        item = json.loads(row["item_json"])
        self.assertEqual(
            (item["live_problem_id"], item["question_title"], item["contest_batch"]),
            (51, "Title", "mck79-x"),
        )
        self.assertEqual(item["type"], "poll")
        self.assertEqual(item["text"], "Real text")
        self.assertEqual(item["bank_kind"], "contest")
        self.assertEqual(item["stage"], row["stage"])
        self.assertEqual(item.get("options"), [])
        self.assertNotIn("correct_answer", item)
        self.assertNotEqual(item.get("import_source"), "x")
        self.assertNotEqual(item["id"], "evil")
        self.assertEqual(int(row["page_number"]), 1)

    def test_mck161_info2_card_text_keeps_the_blank_line(self) -> None:
        """Stored text is stem, blank line, task; the cards render it as paragraphs."""
        rv = self._import([{"question_id": self.m1[0], "module": "M1"}])
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        card = [q for q in self._deck_questions() if q.get("live_problem_id")][0]
        self.assertIn("Module one contest 1 stem\n\nModule one contest 1 task", card["text"])
        staff = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn('|| plainPromptHtml(item.title || item.text || item.prompt || card.text || "")', staff)
        self.assertIn('live-question-html is-paragraphs', staff)
        student = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn('const paragraphs = /\\n\\s*\\n/.test(raw) ? " is-paragraphs" : "";', student)
        for css_name in ("staff-shell.css", "student-portal.css"):
            css = (LMS_DIR / "static" / css_name).read_text(encoding="utf-8")
            start = css.index(".live-question-html.is-paragraphs")
            self.assertIn("white-space: pre-line;", css[start : css.index("}", start)], css_name)

    def test_mck161_info3_resync_failure_drops_inactive_session_items(self) -> None:
        """A failed rollback resync no longer leaves the batch's inactive live items."""
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        session_id = int(live["id"])
        self.school.set_live_session_teacher_state(
            session_id, live_module="M1", live_slot="C1", stage="round"
        )
        self.school.ensure_live_session_items(session_id)
        keep = self._import([{"question_id": self.m1[0], "module": "M1"}])
        self.assertEqual(keep.status_code, 200, keep.get_data(as_text=True))
        keep_key = keep.get_json()["placements"][0]["placement_key"]
        before_items = self._session_item_keys(session_id)
        self.assertIn(keep_key, before_items)

        self._flaky(3)
        real_sync = self.school._sync_playlist_change
        state = {"undoing": False}
        minted: set[str] = set()

        def sync(*args, **kwargs):
            """Fail only the rollback's resync."""
            if state["undoing"]:
                raise RuntimeError("live session gone")
            return real_sync(*args, **kwargs)

        real_add = self.school.add_staff_question_to_class_playlist

        def mark(*args, **kwargs):
            """Remember the live items each placement mints; flag the undo."""
            try:
                out = real_add(*args, **kwargs)
            except KeyError:
                state["undoing"] = True
                raise
            minted.update(self._session_item_keys(session_id) - before_items)
            return out

        self.school._sync_playlist_change = sync
        self.school.add_staff_question_to_class_playlist = mark
        self.addCleanup(setattr, self.school, "_sync_playlist_change", real_sync)
        with self.assertLogs("live_content_questions", level="ERROR"):
            rv = self._import(
                [
                    {"question_id": self.m1[1], "module": "M1"},
                    {"question_id": self.m1[2], "module": "M1"},
                    {"question_id": self.course[0], "module": "COURSE"},
                ]
            )
        self.assertEqual(rv.status_code, 409, rv.get_data(as_text=True))
        self.assertEqual(len(minted), 2, minted)
        after = self._session_item_keys(session_id)
        self.assertFalse(minted & after, minted & after)
        self.assertIn(keep_key, after)

    def _session_item_keys(self, session_id: int) -> set[str]:
        """Placement keys of the session's live items."""
        rows = self.school.conn.execute(
            "SELECT placement_key FROM live_session_items WHERE live_session_id = ?",
            (int(session_id),),
        ).fetchall()
        return {str(row["placement_key"]) for row in rows}

    def test_mck161_info4_unicode_digit_hint_goes_to_contest(self) -> None:
        """``M²C1`` no longer raises; it is course-wide like any unknown hint."""
        self.assertEqual(problem_group("M²C1", ["M1", "M2"]), "COURSE")
        self.assertEqual(problem_group("M١C1", ["M1"]), "COURSE")
        self.assertEqual(problem_group("A/M2C1", ["M1", "M2"]), "M2")
        odd = _problem(self.school, "MCR3U", "M²C1", "Superscript hint", sort_order=7)
        listed = self._list("COURSE")
        self.assertIn(odd, [i["question_id"] for i in listed["group"]["items"]])


HELPER_CASES = r"""
import {
  contentModuleView, contentRowsView, contentGroupHeading, contentPickSummary,
  contentPicksPayload, contentImportDoneText, deckLiveProblemIds,
} from "./static/content_questions_help.js";
const modules = [
  { module: "M1", label: "Module 1", count: 2 },
  { module: "M2", label: "Module 2", count: 0 },
  { module: "M9", label: "Module 9", count: 3 },
  { module: "COURSE", label: "Contest", count: 6 },
];
const view = contentModuleView(modules, "m1");
const fallback = contentModuleView(modules, "M2");
const m1 = contentRowsView({ module: "M1", label: "Module 1", items: [
  { question_id: 11, content_rank: 1, type: "poll", question_title: " Fence garden " },
  { question_id: 12, content_rank: 2, curriculum_open: true },
] }, 6, [12]);
const course = contentRowsView({ module: "COURSE", label: "Contest",
  items: Array.from({ length: 8 }, (_, i) => ({ question_id: 20 + i, type: "poll" })) }, 6);
const m3 = contentRowsView({ module: "M3", label: "Module 3", items: [] }, 6);
const out = {
  modules: view.map((g) => g.module),
  headings: view.map((g) => g.heading),
  open: view.map((g) => g.open),
  fallbackOpen: fallback.map((g) => g.open),
  loaded: [
    contentGroupHeading("Module 2", true, 6),
    contentGroupHeading("Module 4", false, 0),
    contentGroupHeading("Contest", false, 1),
  ],
  m1Meta: m1.rows.map((r) => r.meta),
  m1Titles: m1.rows.map((r) => r.title),
  m1OnDeck: m1.rows.map((r) => r.onDeck),
  courseIds: course.rows.map((r) => r.id),
  courseRanks: course.rows.map((r) => r.rank),
  courseModule: course.rows[0].module,
  empty: [m3.empty, m1.empty],
  none: contentPickSummary([]),
  two: contentPickSummary([{ id: 1 }, { id: 2 }]),
  payload: contentPicksPayload([
    { id: "11", module: "M1" }, { id: "11", module: "COURSE" }, { id: "21", module: "course" },
    { id: "0", module: "M1" }, { id: "5", module: "X" },
  ]),
  done: [contentImportDoneText(1), contentImportDoneText(3)],
  deck: [...deckLiveProblemIds([
    { id: "staff-q-1", live_problem_id: 41 }, { live_problem_id: "7" }, { id: "bank-import-3" },
    { live_problem_id: 9, removed: true }, { live_problem_id: 8, hidden: true }, null,
  ])].sort((a, b) => a - b),
};
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is required for content_questions_help.js")
class ContentQuestionsHelperTests(unittest.TestCase):
    """Pure helper: group list, lazy rows, labels, caps and the pick payload."""

    @classmethod
    def setUpClass(cls) -> None:
        """Run the helper cases once."""
        proc = subprocess.run(
            [NODE, "--input-type=module", "-e", HELPER_CASES],
            cwd=str(LMS_DIR), capture_output=True, text=True, timeout=30, check=False,
        )
        if proc.returncode != 0:
            raise AssertionError(proc.stderr)
        cls.got = json.loads(proc.stdout.strip().splitlines()[-1])

    def test_group_list_opens_the_class_module_or_contest(self) -> None:
        """Server order kept, bad token dropped; Contest opens when the module is empty."""
        self.assertEqual(self.got["modules"], ["M1", "M2", "COURSE"])
        self.assertEqual(
            self.got["headings"],
            [
                "Module 1 · this class · 2 questions",
                "Module 2 · none yet",
                "Contest · 6 questions",
            ],
        )
        self.assertEqual(self.got["open"], [True, False, False])
        # Empty class module stays open (shows "none yet") and Contest opens too.
        self.assertEqual(self.got["fallbackOpen"], [False, True, True])
        self.assertEqual(
            self.got["loaded"],
            ["Module 2 · this class · 6 questions", "Module 4 · none yet", "Contest · 1 question"],
        )

    def test_caps_at_six_titles_and_marks_on_deck(self) -> None:
        """Eight rows trim to six, ranks fall back to position, deck rows marked."""
        self.assertEqual(self.got["courseIds"], [20, 21, 22, 23, 24, 25])
        self.assertEqual(self.got["courseRanks"], [1, 2, 3, 4, 5, 6])
        self.assertEqual(self.got["courseModule"], "COURSE")
        self.assertEqual(self.got["m1Meta"], ["Open prompt", "Open prompt · on deck"])
        self.assertEqual(self.got["m1Titles"], ["Fence garden", ""])
        self.assertEqual(self.got["m1OnDeck"], [False, True])

    def test_empty_state_copy(self) -> None:
        """An empty group says so; a filled one has no empty line."""
        self.assertEqual(self.got["empty"], ["Module 3 has no Contest Questions yet.", ""])

    def test_pick_summary_payload_and_done_line(self) -> None:
        """Button holds until a pick; payload keeps one pick per id, COURSE allowed."""
        self.assertEqual(self.got["none"], {"label": "Import selected", "disabled": True, "count": 0})
        self.assertEqual(self.got["two"], {"label": "Import selected (2)", "disabled": False, "count": 2})
        self.assertEqual(
            self.got["payload"],
            [{"question_id": 11, "module": "M1"}, {"question_id": 21, "module": "COURSE"}],
        )
        self.assertEqual(
            self.got["done"],
            [
                "Imported 1 Contest Question onto this page.",
                "Imported 3 Contest Questions onto this page.",
            ],
        )

    def test_deck_live_problem_ids(self) -> None:
        """Contest cards on the deck by live_problem_id; removed/hidden skipped."""
        self.assertEqual(self.got["deck"], [7, 41])


class ContentQuestionsWiringTests(unittest.TestCase):
    """Picker, CSS and Run Live Class wiring, as source checks."""

    def test_picker_shows_content_only_in_import_mode(self) -> None:
        """The section needs import mode plus load and onImport."""
        src = (LMS_DIR / "static" / "bank_mc_picker.js").read_text(encoding="utf-8")
        self.assertIn('mode === "import" &&', src)
        self.assertIn("data-bank-mc-content", src)
        self.assertIn('type="checkbox" data-content-pick=', src)
        self.assertIn("/static/content_questions_help.js", src)
        self.assertIn("deckLiveProblemIds(", src)
        self.assertIn('data-content-count="${group.count}"', src)
        self.assertIn("<h4>${escapeHtml(contentSectionHeading(CONTENT_PER_MODULE))}</h4>", src)
        self.assertIn('aria-label="Contest Questions"', src)
        for name in ("bank_mc_picker.js", "content_questions_help.js", "staff_ap.js"):
            text = (LMS_DIR / "static" / name).read_text(encoding="utf-8")
            self.assertNotIn("Content Question", text, name)
            self.assertNotIn("deckBankQuestionIds", text, name)

    def test_run_live_class_passes_content_questions(self) -> None:
        """Import from bank loads one group at a time and posts the batch."""
        src = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("/live-lessons/contest-questions?module=", src)
        self.assertIn("/import-contest-questions", src)
        self.assertIn("onImport: (picks) => importLiveContentQuestions(picks)", src)
        self.assertIn("onError: () => refreshLiveDeckAfterContentImportError()", src)
        self.assertIn("onDeckIds:", src)

    def test_picker_loads_lazily_after_the_first_search(self) -> None:
        """Other groups fetch on expand; the section mounts after refreshSearch."""
        src = (LMS_DIR / "static" / "bank_mc_picker.js").read_text(encoding="utf-8")
        self.assertIn('details.addEventListener("toggle"', src)
        self.assertIn("contentOpts.load(module)", src)
        self.assertLess(
            src.index("  await refreshSearch();\n  if (contentOpts) {"),
            src.index("void mountContentQuestions(shell"),
        )

    def test_checkbox_width_reset(self) -> None:
        """lloves.css input{width:100%} must not stretch the content checkbox."""
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        start = css.index('.bank-mc-content-row input[type="checkbox"] {')
        rule = css[start : css.index("}", start)]
        self.assertIn("width: auto;", rule)
        self.assertIn("flex: none;", rule)
        start = css.index(".bank-mc-content-row .bank-mc-picker-row-main {")
        rule = css[start : css.index("}", start)]
        self.assertIn("min-width: 0;", rule)

    def test_picker_is_one_scroll_pane_at_every_width(self) -> None:
        """MED-3: the whole picker scrolls (not only under 640px); list flows in it."""
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        sel = "body.staff-shell .bank-mc-picker.has-content-questions {"
        start = css.index(sel)
        self.assertIn("overflow-y: auto;", css[start : css.index("}", start)])
        before = css[:start]
        self.assertLessEqual(before.count("@media"), before.count("}\n}"))
        start = css.index("body.staff-shell .bank-mc-picker.has-content-questions .bank-mc-picker-list {")
        rule = css[start : css.index("}", start)]
        self.assertIn("flex: none;", rule)
        self.assertIn("overflow-y: visible;", rule)
        self.assertNotIn("max-height: min(16rem, 32vh)", css)

    def test_mck161_low6_search_row_is_sticky(self) -> None:
        """LOW-6 default: the search head sticks; typed results scroll into view."""
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        sel = "body.staff-shell .bank-mc-picker.has-content-questions .bank-mc-picker-head {"
        start = css.index(sel)
        rule = css[start : css.index("}", start)]
        for decl in ("position: sticky;", "top: 0;", "z-index: 2;", "background: #fff;"):
            self.assertIn(decl, rule)
        src = (LMS_DIR / "static" / "bank_mc_picker.js").read_text(encoding="utf-8")
        self.assertIn("searchResultsScroll({", src)
        self.assertIn("revealSearchResults();", src)

    def test_mck161_low8_picker_unticks_stale_picks(self) -> None:
        """On a refused import the picker reloads loaded groups and says what happened."""
        src = (LMS_DIR / "static" / "bank_mc_picker.js").read_text(encoding="utf-8")
        self.assertIn("const stale = await reloadLoadedGroups()", src)
        self.assertIn(".then((present) => contentStalePicks(picks, present))", src)
        self.assertIn("contentStaleText(stale.length)", src)

    @unittest.skipUnless(NODE, "node not installed")
    def test_mck161_helper_node_cases(self) -> None:
        """content_questions_help.test.mjs: copy, stale picks, search reveal."""
        proc = subprocess.run(
            [NODE, str(LMS_DIR / "static" / "content_questions_help.test.mjs")],
            cwd=str(LMS_DIR), capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()

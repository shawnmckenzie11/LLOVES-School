#!/usr/bin/env python3
"""MCK-79: Import from bank lists each module's top 6 Contest Questions.

Contest Questions are bank rows whose Kind is Contest (stored ``contest``).
Server: the list endpoint is lazy and read-only. It returns every Run Live
Class module's linked flag plus one module's top six (pack order, Kind =
Contest only: no Core Math, warmup, Custom or staff-authored rows) and
links no banks. The batch
import uses the same placement path as ``import-mc``, only accepts a
module's current top six, de-duplicates question ids and is all-or-nothing.

Client: ``content_questions_help.js`` grouping, labels and pick payload run
in node; the picker and the Run Live Class wiring are checked as source.
"""

from __future__ import annotations

import json
import os
import shutil
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
    selectable_live_modules,
)
from school_db import _now  # noqa: E402

NODE = shutil.which("node")


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


def _mc(
    school: Any,
    bank_id: int,
    key: str,
    stem: str,
    extra: dict[str, Any] | None = None,
) -> int:
    """Insert one MC question with a distinct stem."""
    payload: dict[str, Any] = {
        "stem_html": stem,
        "points_possible": 1.0,
        "choices": [
            {"id": "a", "html": f"{stem} yes", "correct": True},
            {"id": "b", "html": f"{stem} no", "correct": False},
        ],
        "correct_ids": ["a"],
    }
    payload.update(extra or {})
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


class ContentQuestionsApiTests(unittest.TestCase):
    """List and batch import over HTTP as the class teacher."""

    def setUp(self) -> None:
        """MCR3U class: M1 pack bank unlinked, M2 linked by the teacher."""
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
        lib = self.library_id
        # Module 1: the pack's primary test bank. Eight Contest MCs
        # interleaved with untagged Core Math rows, plus a warmup and a
        # Custom row. Left unlinked (only the staff bank is linked).
        self.m1_bank = _bank(self.school, lib, "Module 1 Test", "bank:m1-test")
        self.m1_qs: list[int] = []
        self.m1_core: list[int] = []
        for n in range(1, 9):
            self.m1_core.append(
                _mc(self.school, self.m1_bank, f"m1-core-{n}", f"Module one core {n}")
            )
            self.m1_qs.append(
                _mc(
                    self.school,
                    self.m1_bank,
                    f"m1-{n}",
                    f"Module one contest {n}",
                    {"bank_kind": "contest"},
                )
            )
        self.m1_warmup = _mc(
            self.school, self.m1_bank, "m1-w", "Warm one", {"kind": "warmup"}
        )
        self.m1_custom = _mc(
            self.school, self.m1_bank, "m1-c", "Custom one", {"bank_kind": "standard"}
        )
        staff_bank = int(self.school._ensure_staff_authored_bank(lib))
        self.school._ensure_module_bank_link(lib, 1, staff_bank)
        # Tagged Contest but staff-authored: still not listed.
        self.staff_q = _mc(
            self.school, staff_bank, "staff-1", "Teacher made one", {"bank_kind": "contest"}
        )
        # Module 2: teacher already linked a non-test bank with three Contest
        # MCs and one Core Math row.
        self.m2_test = _bank(self.school, lib, "Module 2 Test", "bank:m2-test")
        _mc(self.school, self.m2_test, "m2t-1", "Module two test only", {"bank_kind": "contest"})
        self.m2_bank = _bank(self.school, lib, "M2 extras", "bank:m2-extra")
        self.m2_core = _mc(self.school, self.m2_bank, "m2-core", "Module two core")
        self.m2_qs = [
            _mc(
                self.school,
                self.m2_bank,
                f"m2-{n}",
                f"Module two contest {n}",
                {"kind": "contest"},
            )
            for n in range(1, 4)
        ]
        self.school.confirm_module_bank_links(lib, 2, [self.m2_bank])
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
        """GET the Contest Questions list (optionally one module's top 6)."""
        query = f"?module={module}" if module else ""
        rv = self.client.get(
            f"/api/staff/class/{self.class_id}/live-lessons/contest-questions{query}"
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        return rv.get_json()

    def _group(self, module: str) -> dict[str, Any]:
        """Return one module's loaded group."""
        group = self._list(module)["group"]
        self.assertIsNotNone(group)
        return group

    def _links(self) -> dict[int, list[int]]:
        """Module bank links for every module, for before/after checks."""
        return {
            n: sorted(int(r["bank_id"]) for r in self.school.list_module_bank_links(self.library_id, n))
            for n in range(1, 9)
        }

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

    def _deck_bank_ids(self, module: str = "M1", slot: str = "C1") -> list[int]:
        """Bank question ids imported onto one class deck."""
        rv = self.client.get(
            f"/api/staff/class/{self.class_id}/live-lessons/{module}/{slot}/deck"
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        meta = rv.get_json()["live_metadata"]
        ids = []
        for row in meta.get("questions") or []:
            token = str(row.get("id") or "")
            if token.startswith("bank-import-"):
                ids.append(int(token.rsplit("-", 1)[1]))
        return ids

    def test_list_is_lazy_module_summaries_plus_one_group(self) -> None:
        """No module: summaries only. ?module=M2: only M2's rows."""
        modules = selectable_live_modules("MCR3U")
        bare = self._list()
        self.assertEqual([m["module"] for m in bare["modules"]], modules)
        self.assertEqual(bare["modules"][0]["label"], "Module 1")
        self.assertIsNone(bare["group"])
        self.assertEqual(bare["per_module"], 6)
        self.assertEqual(CONTENT_QUESTIONS_PER_MODULE, 6)
        linked = {m["module"]: m["linked"] for m in bare["modules"]}
        # M1 only has the staff-authored bank (not pack content); M2 is linked.
        self.assertFalse(linked["M1"])
        self.assertTrue(linked["M2"])
        self.assertFalse(linked["M3"])
        one = self._list("M2")
        self.assertEqual(one["group"]["module"], "M2")
        self.assertNotIn("groups", one)

    def test_one_module_load_runs_one_search(self) -> None:
        """Opening the picker searches the current module only (was 8)."""
        calls: list[int] = []
        real = self.school.search_module_bank_mcs

        def counting(library_id, module_number, *args, **kwargs):
            """Count module searches."""
            calls.append(int(module_number))
            return real(library_id, module_number, *args, **kwargs)

        self.school.search_module_bank_mcs = counting
        try:
            self._list("M2")
            self._list("M3")  # unlinked: not searched at all
        finally:
            self.school.search_module_bank_mcs = real
        self.assertEqual(calls, [2])

    def test_top_six_in_pack_order_without_warmup_custom_or_staff(self) -> None:
        """M1 (once linked) shows its first six pack MCs, ranked 1–6."""
        self.school.confirm_module_bank_links(self.library_id, 1, [self.m1_bank])
        m1 = self._group("M1")
        ids = [int(item["question_id"]) for item in m1["items"]]
        self.assertEqual(ids, self.m1_qs[:6])
        self.assertEqual([item["content_rank"] for item in m1["items"]], [1, 2, 3, 4, 5, 6])
        self.assertTrue(all(item["module"] == "M1" for item in m1["items"]))
        self.assertTrue(m1["linked"])
        for hidden in (self.m1_warmup, self.m1_custom, self.staff_q):
            self.assertNotIn(hidden, ids)

    def test_mixed_kinds_list_only_contest_rows(self) -> None:
        """Core Math, warmup, Custom and staff rows never appear; Contest does."""
        self.school.confirm_module_bank_links(self.library_id, 1, [self.m1_bank])
        m1 = self._group("M1")
        ids = [int(item["question_id"]) for item in m1["items"]]
        self.assertEqual(ids, self.m1_qs[:6])
        self.assertTrue(all(item.get("kind") == "contest" for item in m1["items"]))
        for hidden in [*self.m1_core, self.m1_warmup, self.m1_custom, self.staff_q]:
            self.assertNotIn(hidden, ids)
        m2_ids = [int(item["question_id"]) for item in self._group("M2")["items"]]
        self.assertEqual(m2_ids, self.m2_qs)
        self.assertNotIn(self.m2_core, m2_ids)
        # The same banks still give Core Math rows to the usual Import search.
        core = self.school.search_module_bank_mcs(self.library_id, 1, "", limit=500, kind="")
        self.assertIn(self.m1_core[0], [int(r["question_id"]) for r in core["items"]])

    def test_fewer_than_six_shows_what_exists_and_unlinked_says_so(self) -> None:
        """M2 has three; M3 has no banks and reports unlinked, no rows."""
        m2 = self._group("M2")
        self.assertEqual([int(i["question_id"]) for i in m2["items"]], self.m2_qs)
        m3 = self._group("M3")
        self.assertEqual(m3["items"], [])
        self.assertFalse(m3["linked"])

    def test_list_never_links_banks(self) -> None:
        """Loading every module, unlinked ones included, writes no links."""
        before = self._links()
        self._list()
        for module in selectable_live_modules("MCR3U"):
            self._list(module)
        self.assertEqual(self._links(), before)
        self.assertNotIn(self.m1_bank, before[1])
        self.assertFalse(self._group("M1")["linked"])

    def test_bad_module_query_is_400(self) -> None:
        """Only Run Live Class modules can be loaded."""
        rv = self.client.get(
            f"/api/staff/class/{self.class_id}/live-lessons/contest-questions?module=M9"
        )
        self.assertEqual(rv.status_code, 400)

    def test_import_selected_from_two_modules_onto_current_page(self) -> None:
        """Picks from M1 and M2 land on M1 C1 page 4 as bank imports."""
        self.school.confirm_module_bank_links(self.library_id, 1, [self.m1_bank])
        rv = self._import(
            [
                {"question_id": self.m1_qs[0], "module": "M1"},
                {"question_id": self.m2_qs[2], "module": "M2"},
                {"question_id": self.m1_qs[0], "module": "M1"},
            ]
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        body = rv.get_json()
        self.assertEqual(body["imported"], 2)
        for placement in body["placements"]:
            self.assertEqual(placement["module"], "M1")
            self.assertEqual(placement["slot"], "C1")
            self.assertEqual(int(placement["page_number"]), 4)
            self.assertEqual(placement["item"]["import_source"], "module_bank")
        self.assertIn("live_metadata", body)
        deck = self._deck_bank_ids()
        self.assertIn(self.m1_qs[0], deck)
        self.assertIn(self.m2_qs[2], deck)
        # M2 links were not widened to pull the M2 question into M1.
        m1_banks = {int(r["bank_id"]) for r in self.school.list_module_bank_links(self.library_id, 1)}
        self.assertNotIn(self.m2_bank, m1_banks)

    def test_import_refuses_rows_outside_the_top_six(self) -> None:
        """The 7th Contest MC, a Core Math, a warmup and a staff row are refused."""
        self.school.confirm_module_bank_links(self.library_id, 1, [self.m1_bank])
        for qid in (self.m1_qs[6], self.m1_core[0], self.m1_warmup, self.staff_q):
            rv = self._import(
                [
                    {"question_id": self.m1_qs[0], "module": "M1"},
                    {"question_id": qid, "module": "M1"},
                ]
            )
            self.assertEqual(rv.status_code, 404, rv.get_data(as_text=True))
        self.assertEqual(self._deck_bank_ids(), [])

    def test_import_refuses_wrong_module_label(self) -> None:
        """An M2 question claimed as M1 is refused."""
        self.school.confirm_module_bank_links(self.library_id, 1, [self.m1_bank])
        rv = self._import([{"question_id": self.m2_qs[0], "module": "M1"}])
        self.assertEqual(rv.status_code, 404)

    def test_bad_bodies_are_400(self) -> None:
        """Empty picks and an unknown module are rejected."""
        self.assertEqual(self._import([]).status_code, 400)
        self.assertEqual(
            self._import([{"question_id": self.m1_qs[0], "module": "M9"}]).status_code, 400
        )
        with self.assertRaises(ValueError):
            clean_content_picks([{"question_id": 1, "module": "M1"}] * 49, ["M1"])

    def test_same_question_under_two_modules_imports_once(self) -> None:
        """A bank linked to two modules can't double a question in one batch."""
        self.school.confirm_module_bank_links(self.library_id, 1, [self.m1_bank])
        self.school.confirm_module_bank_links(self.library_id, 3, [self.m1_bank])
        rv = self._import(
            [
                {"question_id": self.m1_qs[1], "module": "M1"},
                {"question_id": self.m1_qs[1], "module": "M3"},
            ]
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        self.assertEqual(rv.get_json()["imported"], 1)
        self.assertEqual(self._deck_bank_ids(), [self.m1_qs[1]])

    def test_failed_placement_rolls_back_the_whole_batch(self) -> None:
        """2nd placement fails: nothing from the batch stays; earlier copy survives."""
        self.school.confirm_module_bank_links(self.library_id, 1, [self.m1_bank])
        first = self._import([{"question_id": self.m1_qs[0], "module": "M1"}])
        self.assertEqual(first.status_code, 200, first.get_data(as_text=True))
        before = self._placement_count()
        real = self.school.import_mc_to_class_playlist
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            """Place the first pick, then fail like a vanished question."""
            calls["n"] += 1
            if calls["n"] == 2:
                raise KeyError("question vanished")
            return real(*args, **kwargs)

        self.school.import_mc_to_class_playlist = flaky
        try:
            rv = self._import(
                [
                    {"question_id": self.m1_qs[0], "module": "M1"},
                    {"question_id": self.m1_qs[1], "module": "M1"},
                    {"question_id": self.m2_qs[0], "module": "M2"},
                ]
            )
        finally:
            self.school.import_mc_to_class_playlist = real
        self.assertEqual(rv.status_code, 409, rv.get_data(as_text=True))
        body = rv.get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["imported"], 0)
        self.assertEqual(body["landed"], [])
        self.assertEqual(body["rolled_back"], 1)
        self.assertIn("nothing was imported", body["error"])
        self.assertEqual(calls["n"], 2)
        self.assertEqual(self._placement_count(), before)
        # The copy imported before the batch is still on the deck, once.
        self.assertEqual(self._deck_bank_ids(), [self.m1_qs[0]])

    def test_validation_failure_places_nothing(self) -> None:
        """A bad pick anywhere in the batch is refused before any placement."""
        self.school.confirm_module_bank_links(self.library_id, 1, [self.m1_bank])
        calls = {"n": 0}
        real = self.school.import_mc_to_class_playlist

        def counting(*args, **kwargs):
            """Count placements."""
            calls["n"] += 1
            return real(*args, **kwargs)

        self.school.import_mc_to_class_playlist = counting
        try:
            rv = self._import(
                [
                    {"question_id": self.m1_qs[0], "module": "M1"},
                    {"question_id": self.m1_qs[7], "module": "M1"},
                ]
            )
        finally:
            self.school.import_mc_to_class_playlist = real
        self.assertEqual(rv.status_code, 404)
        self.assertEqual(calls["n"], 0)
        self.assertEqual(self._placement_count(), 0)

    def test_single_import_mc_guard_is_unchanged(self) -> None:
        """import-mc still refuses another module's question."""
        self.school.confirm_module_bank_links(self.library_id, 1, [self.m1_bank])
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C1/import-mc",
            json={"question_id": self.m2_qs[0], "page_number": 4},
        )
        self.assertEqual(rv.status_code, 404)

    def test_other_teacher_is_forbidden(self) -> None:
        """Only the class teacher can list or import."""
        self.school.register_staff("other@gmail.com")
        self.client = self.app.test_client()
        self._login("other@gmail.com")
        rv = self.client.get(f"/api/staff/class/{self.class_id}/live-lessons/contest-questions")
        self.assertEqual(rv.status_code, 403)
        rv = self._import([{"question_id": self.m1_qs[0], "module": "M1"}])
        self.assertEqual(rv.status_code, 403)


HELPER_CASES = r"""
import {
  contentModuleView, contentRowsView, contentGroupHeading, contentPickSummary,
  contentPicksPayload, contentImportDoneText, deckBankQuestionIds,
} from "./static/content_questions_help.js";
const modules = [
  { module: "M1", label: "Module 1", linked: true },
  { module: "M2", label: "Module 2", linked: true },
  { module: "M3", label: "Module 3", linked: false },
  { module: "M9", label: "Module 9", linked: true },
];
const view = contentModuleView(modules, "m2");
const m1 = contentRowsView({ module: "M1", label: "Module 1", linked: true, items: [
  { question_id: 11, content_rank: 1, correct_answer: "B", points: 1 },
  { question_id: 12, content_rank: 2, curriculum_open: true },
] }, 6, [12]);
const m2 = contentRowsView({ module: "M2", label: "Module 2", linked: true,
  items: Array.from({ length: 8 }, (_, i) => ({ question_id: 20 + i, correct_answer: "A", points: 2 })) }, 6);
const m3 = contentRowsView({ module: "M3", label: "Module 3", linked: false, items: [] }, 6);
const m4 = contentRowsView({ module: "M4", label: "Module 4", linked: true, items: [] }, 6);
const out = {
  modules: view.map((g) => g.module),
  headings: view.map((g) => g.heading),
  open: view.map((g) => g.open),
  loaded: [
    contentGroupHeading("Module 2", true, true, 6),
    contentGroupHeading("Module 4", false, true, 0),
    contentGroupHeading("Module 1", false, true, 1),
  ],
  m1Meta: m1.rows.map((r) => r.meta),
  m1OnDeck: m1.rows.map((r) => r.onDeck),
  m2Ids: m2.rows.map((r) => r.id),
  m2Ranks: m2.rows.map((r) => r.rank),
  empty: [m3.empty, m4.empty],
  none: contentPickSummary([]),
  two: contentPickSummary([{ id: 1 }, { id: 2 }]),
  payload: contentPicksPayload([
    { id: "11", module: "M1" }, { id: "11", module: "m3" }, { id: "21", module: "M2" },
    { id: "0", module: "M1" }, { id: "5", module: "X" },
  ]),
  done: [contentImportDoneText(1), contentImportDoneText(3)],
  deck: [...deckBankQuestionIds([
    { id: "bank-import-41" }, { item_id: "bank-import-7" }, { id: "staff-q-abc" },
    { id: "bank-import-9", removed: true }, { source_question_id: 5 }, null,
  ])].sort((a, b) => a - b),
};
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is required for content_questions_help.js")
class ContentQuestionsHelperTests(unittest.TestCase):
    """Pure helper: module list, lazy rows, labels, caps and the pick payload."""

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

    def test_module_list_opens_only_the_class_module(self) -> None:
        """Server order kept, bad module dropped, unlinked says so up front."""
        self.assertEqual(self.got["modules"], ["M1", "M2", "M3"])
        self.assertEqual(
            self.got["headings"],
            [
                "Module 1 · open to load",
                "Module 2 · this class · open to load",
                "Module 3 · no bank linked yet",
            ],
        )
        self.assertEqual(self.got["open"], [False, True, False])
        self.assertEqual(
            self.got["loaded"],
            ["Module 2 · this class · 6 questions", "Module 4 · none yet", "Module 1 · 1 question"],
        )

    def test_caps_at_six_labels_rows_and_marks_on_deck(self) -> None:
        """Eight rows trim to six, ranks fall back to position, deck rows marked."""
        self.assertEqual(self.got["m2Ids"], [20, 21, 22, 23, 24, 25])
        self.assertEqual(self.got["m2Ranks"], [1, 2, 3, 4, 5, 6])
        self.assertEqual(self.got["m1Meta"], ["Answer B · 1 pt", "Open prompt · on deck"])
        self.assertEqual(self.got["m1OnDeck"], [False, True])

    def test_empty_state_copy(self) -> None:
        """Unlinked and linked-but-empty modules read differently."""
        self.assertEqual(
            self.got["empty"],
            [
                "No bank linked yet for Module 3. Pick Module 3 under Bank scope to link it.",
                "Module 4 has no Contest questions yet.",
            ],
        )

    def test_pick_summary_payload_and_done_line(self) -> None:
        """Button holds until a pick; payload keeps one pick per question id."""
        self.assertEqual(self.got["none"], {"label": "Import selected", "disabled": True, "count": 0})
        self.assertEqual(self.got["two"], {"label": "Import selected (2)", "disabled": False, "count": 2})
        self.assertEqual(
            self.got["payload"],
            [{"question_id": 11, "module": "M1"}, {"question_id": 21, "module": "M2"}],
        )
        self.assertEqual(
            self.got["done"],
            [
                "Imported 1 Contest Question onto this page.",
                "Imported 3 Contest Questions onto this page.",
            ],
        )

    def test_deck_bank_question_ids(self) -> None:
        """Bank imports on the deck by item id or source id; removed skipped."""
        self.assertEqual(self.got["deck"], [5, 7, 41])


class ContentQuestionsWiringTests(unittest.TestCase):
    """Picker and Run Live Class wiring, as source checks."""

    def test_picker_shows_content_only_in_import_mode(self) -> None:
        """The section needs import mode plus load and onImport."""
        src = (LMS_DIR / "static" / "bank_mc_picker.js").read_text(encoding="utf-8")
        self.assertIn('mode === "import" &&', src)
        self.assertIn("data-bank-mc-content", src)
        self.assertIn('type="checkbox" data-content-pick=', src)
        self.assertIn("/static/content_questions_help.js", src)
        # Labels say Contest (Kind = Contest), not the earlier "Content".
        self.assertIn("<h4>Contest Questions · top ${CONTENT_PER_MODULE} per module</h4>", src)
        self.assertIn('aria-label="Contest Questions"', src)
        for name in ("bank_mc_picker.js", "content_questions_help.js", "staff_ap.js"):
            text = (LMS_DIR / "static" / name).read_text(encoding="utf-8")
            self.assertNotIn("Content Question", text, name)

    def test_run_live_class_passes_content_questions(self) -> None:
        """Import from bank loads one module at a time and posts the batch."""
        src = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("/live-lessons/contest-questions?module=", src)
        self.assertIn("/import-contest-questions", src)
        self.assertIn("onImport: (picks) => importLiveContentQuestions(picks)", src)
        self.assertIn("onError: () => refreshLiveDeckAfterContentImportError()", src)
        self.assertIn("onDeckIds:", src)

    def test_picker_loads_lazily_after_the_first_search(self) -> None:
        """Other modules fetch on expand; the section mounts after refreshSearch."""
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


if __name__ == "__main__":
    unittest.main()

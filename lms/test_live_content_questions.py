#!/usr/bin/env python3
"""MCK-79: Import from bank lists each module's top 6 Content Questions.

Server: the list endpoint groups every Run Live Class module, keeps pack
order, caps at six, and leaves out warmups, Custom and staff-authored rows.
The batch import uses the same placement path as ``import-mc`` and only
accepts a module's current top six.

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
        # Module 1: the pack's primary test bank, eight content MCs plus a
        # warmup and a Custom row. Left unlinked so the list links it.
        self.m1_bank = _bank(self.school, lib, "Module 1 Test", "bank:m1-test")
        self.m1_qs = [
            _mc(self.school, self.m1_bank, f"m1-{n}", f"Module one content {n}")
            for n in range(1, 9)
        ]
        self.m1_warmup = _mc(
            self.school, self.m1_bank, "m1-w", "Warm one", {"kind": "warmup"}
        )
        self.m1_custom = _mc(
            self.school, self.m1_bank, "m1-c", "Custom one", {"bank_kind": "standard"}
        )
        staff_bank = int(self.school._ensure_staff_authored_bank(lib))
        self.school._ensure_module_bank_link(lib, 1, staff_bank)
        self.staff_q = _mc(self.school, staff_bank, "staff-1", "Teacher made one")
        # Module 2: teacher already linked a non-test bank with three MCs.
        self.m2_test = _bank(self.school, lib, "Module 2 Test", "bank:m2-test")
        _mc(self.school, self.m2_test, "m2t-1", "Module two test only")
        self.m2_bank = _bank(self.school, lib, "M2 extras", "bank:m2-extra")
        self.m2_qs = [
            _mc(self.school, self.m2_bank, f"m2-{n}", f"Module two content {n}")
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

    def _list(self) -> dict[str, Any]:
        """GET the Content Questions list as JSON."""
        rv = self.client.get(f"/api/staff/class/{self.class_id}/live-lessons/content-questions")
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        return rv.get_json()

    def _group(self, payload: dict[str, Any], module: str) -> dict[str, Any]:
        """Return one module's group from a list payload."""
        return next(g for g in payload["groups"] if g["module"] == module)

    def _import(self, picks: list[dict[str, Any]], module: str = "M1", slot: str = "C1"):
        """POST a batch Content Questions import onto page 4."""
        return self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/{module}/{slot}/import-content-questions",
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

    def test_every_selectable_module_is_grouped_in_select_order(self) -> None:
        """One labelled group per Run Live Class module, M1 first."""
        payload = self._list()
        modules = selectable_live_modules("MCR3U")
        self.assertEqual(payload["modules"], modules)
        self.assertEqual([g["module"] for g in payload["groups"]], modules)
        self.assertEqual(payload["groups"][0]["label"], "Module 1")
        self.assertEqual(payload["per_module"], 6)
        self.assertEqual(CONTENT_QUESTIONS_PER_MODULE, 6)

    def test_top_six_in_pack_order_without_warmup_custom_or_staff(self) -> None:
        """M1 shows its first six pack MCs, ranked 1–6, tagged M1."""
        m1 = self._group(self._list(), "M1")
        ids = [int(item["question_id"]) for item in m1["items"]]
        self.assertEqual(ids, self.m1_qs[:6])
        self.assertEqual([item["content_rank"] for item in m1["items"]], [1, 2, 3, 4, 5, 6])
        self.assertTrue(all(item["module"] == "M1" for item in m1["items"]))
        self.assertEqual(m1["available"], 8)
        for hidden in (self.m1_warmup, self.m1_custom, self.staff_q):
            self.assertNotIn(hidden, ids)

    def test_fewer_than_six_shows_what_exists_and_empty_modules_say_so(self) -> None:
        """M2 has three; M3 has no banks and reports unlinked."""
        payload = self._list()
        m2 = self._group(payload, "M2")
        self.assertEqual([int(i["question_id"]) for i in m2["items"]], self.m2_qs)
        m3 = self._group(payload, "M3")
        self.assertEqual(m3["items"], [])
        self.assertFalse(m3["linked"])

    def test_list_links_unlinked_test_bank_but_keeps_teacher_links(self) -> None:
        """M1 (staff link only) gains Module 1 Test; M2 keeps the teacher's bank."""
        staff_bank = int(self.school._ensure_staff_authored_bank(self.library_id))
        before_m2 = self.school.list_module_bank_links(self.library_id, 2)
        self._list()
        m1_banks = {int(r["bank_id"]) for r in self.school.list_module_bank_links(self.library_id, 1)}
        self.assertEqual(m1_banks, {self.m1_bank, staff_bank})
        m2_banks = [int(r["bank_id"]) for r in self.school.list_module_bank_links(self.library_id, 2)]
        self.assertEqual(m2_banks, [int(r["bank_id"]) for r in before_m2])
        self.assertNotIn(self.m2_test, m2_banks)
        # A second open writes nothing new.
        self._list()
        again = {int(r["bank_id"]) for r in self.school.list_module_bank_links(self.library_id, 1)}
        self.assertEqual(again, m1_banks)

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
        """The 7th pack MC, a warmup and a staff row are refused; nothing lands."""
        self.school.confirm_module_bank_links(self.library_id, 1, [self.m1_bank])
        for qid in (self.m1_qs[6], self.m1_warmup, self.staff_q):
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
        rv = self.client.get(f"/api/staff/class/{self.class_id}/live-lessons/content-questions")
        self.assertEqual(rv.status_code, 403)
        rv = self._import([{"question_id": self.m1_qs[0], "module": "M1"}])
        self.assertEqual(rv.status_code, 403)


HELPER_CASES = r"""
import {
  contentGroupView, contentPickSummary, contentPicksPayload, contentImportDoneText,
} from "./static/content_questions_help.js";
const groups = [
  { module: "M1", label: "Module 1", linked: true, items: [
    { question_id: 11, content_rank: 1, correct_answer: "B", points: 1 },
    { question_id: 12, content_rank: 2, curriculum_open: true },
  ] },
  { module: "M2", label: "Module 2", linked: true, items: Array.from({ length: 8 }, (_, i) => ({ question_id: 20 + i, correct_answer: "A", points: 2 })) },
  { module: "M3", label: "Module 3", linked: false, items: [] },
  { module: "M4", label: "Module 4", linked: true, items: [] },
  { module: "M9", label: "Module 9", items: [{ question_id: 99 }] },
];
const view = contentGroupView(groups, "m2", 6);
const out = {
  modules: view.map((g) => g.module),
  headings: view.map((g) => g.heading),
  open: view.map((g) => g.open),
  m1Meta: view[0].rows.map((r) => r.meta),
  m2Ids: view[1].rows.map((r) => r.id),
  m2Ranks: view[1].rows.map((r) => r.rank),
  empty: [view[2].empty, view[3].empty],
  none: contentPickSummary([]),
  two: contentPickSummary([{ id: 1 }, { id: 2 }]),
  payload: contentPicksPayload([
    { id: "11", module: "M1" }, { id: "11", module: "m1" }, { id: "21", module: "M2" },
    { id: "0", module: "M1" }, { id: "5", module: "X" },
  ]),
  done: [contentImportDoneText(1), contentImportDoneText(3)],
};
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is required for content_questions_help.js")
class ContentQuestionsHelperTests(unittest.TestCase):
    """Pure helper: grouping, labels, caps and the pick payload."""

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

    def test_groups_by_module_and_opens_the_class_module(self) -> None:
        """Server order kept, bad module dropped, this class's module open."""
        self.assertEqual(self.got["modules"], ["M1", "M2", "M3", "M4"])
        self.assertEqual(
            self.got["headings"],
            [
                "Module 1 · 2 questions",
                "Module 2 · this class · 6 questions",
                "Module 3 · none yet",
                "Module 4 · none yet",
            ],
        )
        self.assertEqual(self.got["open"], [False, True, False, False])

    def test_caps_at_six_and_labels_rows(self) -> None:
        """Eight rows trim to six, ranks fall back to position."""
        self.assertEqual(self.got["m2Ids"], [20, 21, 22, 23, 24, 25])
        self.assertEqual(self.got["m2Ranks"], [1, 2, 3, 4, 5, 6])
        self.assertEqual(self.got["m1Meta"], ["Answer B · 1 pt", "Open prompt"])

    def test_empty_state_copy(self) -> None:
        """Unlinked and linked-but-empty modules read differently."""
        self.assertEqual(
            self.got["empty"],
            [
                "No banks linked to Module 3 yet. Pick Module 3 under Bank scope to link them.",
                "Module 4 has no Content Questions yet.",
            ],
        )

    def test_pick_summary_payload_and_done_line(self) -> None:
        """Button holds until a pick; payload de-duplicates per module."""
        self.assertEqual(self.got["none"], {"label": "Import selected", "disabled": True, "count": 0})
        self.assertEqual(self.got["two"], {"label": "Import selected (2)", "disabled": False, "count": 2})
        self.assertEqual(
            self.got["payload"],
            [{"question_id": 11, "module": "M1"}, {"question_id": 21, "module": "M2"}],
        )
        self.assertEqual(
            self.got["done"],
            [
                "Imported 1 Content Question onto this page.",
                "Imported 3 Content Questions onto this page.",
            ],
        )


class ContentQuestionsWiringTests(unittest.TestCase):
    """Picker and Run Live Class wiring, as source checks."""

    def test_picker_shows_content_only_in_import_mode(self) -> None:
        """The section needs import mode plus load and onImport."""
        src = (LMS_DIR / "static" / "bank_mc_picker.js").read_text(encoding="utf-8")
        self.assertIn('mode === "import" &&', src)
        self.assertIn("data-bank-mc-content", src)
        self.assertIn('type="checkbox" data-content-pick=', src)
        self.assertIn("/static/content_questions_help.js", src)

    def test_run_live_class_passes_content_questions(self) -> None:
        """Import from bank loads the list and posts the batch import."""
        src = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("/live-lessons/content-questions", src)
        self.assertIn("/import-content-questions", src)
        self.assertIn("onImport: (picks) => importLiveContentQuestions(picks)", src)


if __name__ == "__main__":
    unittest.main()

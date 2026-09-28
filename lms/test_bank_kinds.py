"""Kind labels, Custom retag, and local curriculum import populate."""

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
from bank_kinds import (  # noqa: E402
    kind_display_label,
    stem_is_teaching_today,
    storage_bank_kind,
)
from curriculum_bank_seed import seed_library_import_banks, seed_local_question_banks  # noqa: E402
from test_module_bank_import import _insert_bank, _insert_mc_question  # noqa: E402


class BankKindLabelTests(unittest.TestCase):
    """Display remap keeps stored tokens stable."""

    def test_storage_and_display_mapping(self) -> None:
        """Process/Core Math stay untagged; Custom stores as standard."""
        self.assertEqual(storage_bank_kind(""), "")
        self.assertEqual(storage_bank_kind("process"), "")
        self.assertEqual(storage_bank_kind("core-math"), "")
        self.assertEqual(storage_bank_kind("custom"), "standard")
        self.assertEqual(storage_bank_kind("standard"), "standard")
        self.assertEqual(kind_display_label("standard"), "Custom")
        self.assertEqual(kind_display_label(""), "Core Math")
        self.assertTrue(stem_is_teaching_today("You're teaching today class, start here."))
        self.assertTrue(stem_is_teaching_today("You are teaching today class."))
        self.assertFalse(stem_is_teaching_today("Today, January 1, a teacher asked."))

    def test_staff_kind_labels_in_ui(self) -> None:
        """Question Bank, Import picker, Add New, and IT Kind show the new names."""
        course = (LMS_DIR / "templates" / "staff" / "course.html").read_text(encoding="utf-8")
        picker = (LMS_DIR / "static" / "bank_mc_picker.js").read_text(encoding="utf-8")
        banks = (LMS_DIR / "static" / "course_question_banks.js").read_text(encoding="utf-8")
        staff = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        it_html = (LMS_DIR / "templates" / "it" / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn('id="live-bank-kind"', course)
        self.assertIn(">Core Math</option>", course)
        self.assertIn(">Custom</option>", course)
        self.assertNotIn(">Process</option>", course)
        self.assertNotIn(">Standard</option>", course)
        self.assertIn('id="live-add-q-bank-kind"', course)
        self.assertNotIn('id="live-add-q-bank-kind" hidden', course)
        self.assertNotIn('id="live-add-q-bank-kind" disabled', course)
        self.assertIn(">Core Math</option>", picker)
        self.assertIn(">Custom</option>", picker)
        self.assertIn('data-bank-kind', banks)
        self.assertIn("bank_kind", staff)
        self.assertIn(">Custom</option>", it_html)
        self.assertIn('value="standard" selected', it_html)


class TeachingTodayAndLocalImportTests(unittest.TestCase):
    """Module 2 retag and per-course local bank populate."""

    def setUp(self) -> None:
        """Two upload libraries with no IMSCC cartridge."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.mcf = self._library("MCF3M")
        self.mcr = self._library("MCR3U")
        self.class_id = self._class(self.mcf)

    def tearDown(self) -> None:
        """Close sqlite and remove temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _library(self, code: str) -> int:
        """Attach an upload library for one Ontario code."""
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code=code
        )
        library = self.school.create_library(code, origin="upload")
        self.school.attach_library(int(offering["id"]), int(library["id"]))
        return int(library["id"])

    def _class(self, library_offering_unused: int) -> int:
        """Create the MCF3M class used to import a prompt."""
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCF3M",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Maple"],
            offering_id=int(offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )
        return int(created["id"])

    def test_teaching_today_moves_to_custom_on_module_2(self) -> None:
        """The Module 2 teaching stem leaves Core Math and lands in Custom."""
        bank = _insert_bank(
            self.school,
            self.mcf,
            title="Module 2 bank",
            import_key="bank:m2",
        )
        moved = _insert_mc_question(
            self.school,
            bank,
            import_key="teach",
            title="Teaching prompt",
            stem="You're teaching today class, so start with the groups.",
        )
        stay = _insert_mc_question(
            self.school,
            bank,
            import_key="plain",
            title="Plain",
            stem="Factor the quadratic.",
        )
        self.school.confirm_module_bank_links(self.mcf, 2, [bank])
        core = {
            int(row.get("question_id") or 0)
            for row in self.school.search_module_bank_mcs(self.mcf, 2, "")["items"]
        }
        custom = {
            int(row.get("question_id") or 0)
            for row in self.school.search_module_bank_mcs(self.mcf, 2, "", kind="custom")[
                "items"
            ]
        }
        self.assertNotIn(moved, core)
        self.assertIn(stay, core)
        self.assertIn(moved, custom)
        self.assertNotIn(stay, custom)

    def test_curriculum_cache_populates_each_course_module(self) -> None:
        """Import reads each course's cache; MCF3M M2 C1 is not an MCR3U row."""
        cache = Path(self.tmp.name) / "curriculum"
        self._write_item(cache, "MCF3M", 2, "mcf-m2-q", "Expand the binomial for module two.")
        self._write_item(cache, "MCR3U", 2, "mcr-m2-q", "Solve the trig equation for module two.")
        self._write_item(cache, "MCF3M", 1, "mcf-m1-q", "State the domain of the parabola.")
        mcf_summary = seed_library_import_banks(
            self.school, self.mcf, "MCF3M", cache_root=cache
        )
        mcr_summary = seed_library_import_banks(
            self.school, self.mcr, "MCR3U", cache_root=cache
        )
        self.assertGreaterEqual(mcf_summary["curriculum_questions"], 2)
        self.assertGreaterEqual(mcr_summary["curriculum_questions"], 1)
        mcf_m2 = self.school.search_module_bank_mcs(self.mcf, 2, "binomial")
        self.assertEqual(mcf_m2["filtered"], 1)
        self.assertTrue(mcf_m2["items"][0].get("curriculum_open"))
        self.assertEqual(mcf_m2["items"][0].get("type"), "poll")
        leaked = self.school.search_module_bank_mcs(self.mcf, 2, "trig equation")
        self.assertEqual(leaked["items"], [])
        mcr_m2 = self.school.search_module_bank_mcs(self.mcr, 2, "trig")
        self.assertEqual(mcr_m2["filtered"], 1)
        placed = self.school.import_mc_to_class_playlist(
            self.class_id,
            "M2",
            "C1",
            int(mcf_m2["items"][0]["question_id"]),
            library_id=self.mcf,
            page_number=1,
            stage="join",
        )
        self.assertEqual(placed["item"].get("type"), "poll")
        self.assertFalse(placed["item"].get("key"))
        skipped = seed_local_question_banks(self.school, cache_root=cache)
        self.assertTrue(skipped["skipped"])

    def test_add_new_bank_kind_is_custom(self) -> None:
        """Choosing Custom on Add New stores standard and leaves Core Math."""
        saved = self.school.add_staff_question_to_class_playlist(
            self.class_id,
            "M2",
            "C1",
            question_type="mc",
            text="Custom kind check",
            page_number=1,
            stage="round",
            options=["A", "B", "C", "D"],
            correct_index=0,
            save_to_bank=True,
            bank_scope="M2",
            bank_kind="custom",
            library_id=self.mcf,
        )
        question_id = int(saved.get("source_question_id") or 0)
        self.assertGreater(question_id, 0)
        row = self.school.conn.execute(
            "SELECT payload_json FROM questions WHERE id = ?",
            (question_id,),
        ).fetchone()
        payload = json.loads(row["payload_json"])
        self.assertEqual(payload.get("bank_kind"), "standard")
        self.assertNotIn("kind", payload)
        core = {
            int(item.get("question_id") or 0)
            for item in self.school.search_module_bank_mcs(self.mcf, 2, "Custom kind")[
                "items"
            ]
        }
        custom = {
            int(item.get("question_id") or 0)
            for item in self.school.search_module_bank_mcs(
                self.mcf, 2, "Custom kind", kind="standard"
            )["items"]
        }
        self.assertNotIn(question_id, core)
        self.assertIn(question_id, custom)

    @staticmethod
    def _write_item(
        cache: Path, code: str, module: int, smart_id: str, stem: str
    ) -> None:
        """Write one constructed curriculum item under a temp cache root."""
        folder = cache / code / "banks" / f"M{module}" / "A"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "items.json"
        document = {"items": []}
        if path.is_file():
            document = json.loads(path.read_text(encoding="utf-8"))
        document["items"].append(
            {"smart_id": smart_id, "stem": stem, "item_type": "constructed"}
        )
        path.write_text(json.dumps(document), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()

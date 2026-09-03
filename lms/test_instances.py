#!/usr/bin/env python3
"""Focused tests for thin per-teacher instances and shared library pointers."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

from instances import (  # noqa: E402
    copy_syllabus_structure,
    instance_relpath,
    is_template_imscc,
    leftover_pack_imscc,
    materialize_instance,
    migrate_legacy_pack,
    teacher_slug,
    template_pack_paths,
    year_term_from_label,
)
from paths import REPO_ROOT as ROOT  # noqa: E402


def _write_tiny_template(root: Path) -> Path:
    """Create a fake ``courses/CODE`` template with a tiny pack (not 189MB)."""
    sources = root / "sources"
    canvas = root / "canvas"
    unpacked = canvas / "unpacked"
    sources.mkdir(parents=True)
    unpacked.mkdir(parents=True)
    imscc = sources / "course.imscc"
    imscc.write_bytes(b"PK tiny-imscc")
    (unpacked / "imsmanifest.xml").write_text("<manifest/>", encoding="utf-8")
    (canvas / "inventory.json").write_text('{"modules": []}\n', encoding="utf-8")
    return root


class InstancePathTests(unittest.TestCase):
    """YEAR/TERM slugs and teacher folders."""

    def test_year_term_from_label_uses_label_not_year_display(self) -> None:
        """``2026-2027 S1`` becomes path-safe ``2026-2027`` / ``S1``."""
        year, term = year_term_from_label("2026-2027 S1")
        self.assertEqual(year, "2026-2027")
        self.assertEqual(term, "S1")
        self.assertNotIn("/", year)

    def test_instance_relpath_and_teacher_slug(self) -> None:
        """Instance folders are ``instances/CODE/YEAR/TERM/tID``."""
        self.assertEqual(teacher_slug(12), "t12")
        self.assertEqual(
            instance_relpath("mcf3m", "2026-2027", "S1", 12),
            "instances/MCF3M/2026-2027/S1/t12",
        )

    def test_template_pack_paths_reads_any_code_imscc(self) -> None:
        """A content_root with ``sources/*.imscc`` is the template; no code branch."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _write_tiny_template(Path(tmp) / "FakeCourse")
            tmpl = template_pack_paths("FAKE1", str(root))
            self.assertIsNotNone(tmpl.content_root)
            self.assertEqual(tmpl.imscc, root / "sources" / "course.imscc")
            self.assertTrue(tmpl.imscc.is_file())

    def test_template_pack_paths_does_not_invent_catalog_folders(self) -> None:
        """Catalog-only codes do not get a ``courses/<CODE>/`` tree."""
        tmpl = template_pack_paths("SBI3U", None)
        self.assertIsNone(tmpl.content_root)
        self.assertFalse((ROOT / "courses" / "SBI3U").exists())

    def test_no_live_mcf3m_branch_in_resolver(self) -> None:
        """Live pack resolution has no ``== \"MCF3M\"`` special case."""
        for name in ("instances.py", "modules.py"):
            text = (LMS_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn('== "MCF3M"', text)
            self.assertNotIn("== 'MCF3M'", text)


class InstanceCopyTests(unittest.TestCase):
    """Thin instance: manifest + syllabus; never copy the cartridge."""

    def setUp(self) -> None:
        """Temp data dir + tiny template."""
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        self.template = _write_tiny_template(Path(self.tmp.name) / "FakeCourse")

    def tearDown(self) -> None:
        """Remove temp dir."""
        self.tmp.cleanup()

    def test_materialize_is_thin_no_pack_copy(self) -> None:
        """Assign writes manifest + syllabus only; template IMSCC is not copied."""
        (self.template / "roster.json").write_text("[]", encoding="utf-8")
        offering = {
            "id": 7,
            "ontario_code": "FAKE1",
            "teacher_user_id": 3,
        }
        result = materialize_instance(
            self.data,
            offering,
            semester_label="2026-2027 S1",
            teacher_name="Ada",
            content_root=str(self.template),
            library_id=4,
        )
        self.assertEqual(
            result["instance_relpath"],
            "instances/FAKE1/2026-2027/S1/t3",
        )
        self.assertTrue((result["root"] / "manifest.json").is_file())
        self.assertTrue(result["syllabus"].is_dir())
        self.assertFalse((result["pack"] / "course.imscc").exists())
        self.assertFalse((result["pack"] / "inventory.json").exists())
        self.assertFalse((result["root"] / "roster.json").exists())
        self.assertIsNone(result["imscc_path"])
        manifest = json.loads((result["root"] / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["library_id"], 4)
        self.assertTrue((self.template / "sources" / "course.imscc").is_file())
        copied = list(self.data.rglob("*.imscc"))
        self.assertEqual(copied, [])

    def test_fork_from_base_copies_answers_not_pack(self) -> None:
        """A second teacher copies answers JSON, not HTML/CSV, roster, or IMSCC."""
        first = materialize_instance(
            self.data,
            {"id": 1, "ontario_code": "FAKE1", "teacher_user_id": 3},
            semester_label="2026-2027 S1",
            content_root=str(self.template),
            library_id=9,
        )
        roster = first["root"] / "roster.json"
        roster.write_text('{"students": ["Maple"]}\n', encoding="utf-8")
        (first["syllabus"] / "2026-2027-S1.html").write_text("<p>dated</p>", encoding="utf-8")
        (first["syllabus"] / "2026-2027-S1.csv").write_text("date,item\n", encoding="utf-8")
        answers = {
            "course": "FAKE1",
            "semester": "2026-2027 S1",
            "lessons": {"1": {"included": ["g1"], "excluded": ["g2"]}},
        }
        (first["syllabus"] / "2026-2027-S1.answers.json").write_text(
            json.dumps(answers), encoding="utf-8"
        )
        second = materialize_instance(
            self.data,
            {"id": 2, "ontario_code": "FAKE1", "teacher_user_id": 9},
            semester_label="2026-2027 S1",
            content_root=str(self.template),
            library_id=9,
            base_offering={
                "id": 1,
                "ontario_code": "FAKE1",
                "instance_relpath": first["instance_relpath"],
            },
        )
        self.assertNotEqual(first["root"], second["root"])
        self.assertFalse((second["pack"] / "course.imscc").exists())
        self.assertFalse((second["root"] / "roster.json").exists())
        self.assertFalse((second["syllabus"] / "2026-2027-S1.html").exists())
        self.assertFalse((second["syllabus"] / "2026-2027-S1.csv").exists())
        copied = json.loads(
            (second["syllabus"] / "2026-2027-S1.answers.json").read_text(encoding="utf-8")
        )
        self.assertEqual(copied["lessons"]["1"]["included"], ["g1"])
        self.assertEqual(list(self.data.rglob("*.imscc")), [])

    def test_copy_syllabus_structure_skips_html(self) -> None:
        """Helper copies answers JSON only."""
        src = Path(self.tmp.name) / "src_syl"
        dest = Path(self.tmp.name) / "dest_syl"
        src.mkdir()
        (src / "x.html").write_text("nope", encoding="utf-8")
        (src / "x.answers.json").write_text('{"lessons": {}}', encoding="utf-8")
        copy_syllabus_structure(src, dest, new_semester_label="2026-2027 S1")
        self.assertTrue((dest / "x.answers.json").is_file())
        self.assertFalse((dest / "x.html").exists())

    def test_migrate_legacy_module_packs_leaves_copy(self) -> None:
        """Leftover ``module_packs/<id>/`` stays; instance is thin."""
        offering = {"id": 44, "ontario_code": "FAKE1", "teacher_user_id": 8}
        legacy = self.data / "module_packs" / "44"
        legacy.mkdir(parents=True)
        (legacy / "course.imscc").write_bytes(b"PK leftover")
        (legacy / "inventory.json").write_text("{}", encoding="utf-8")
        result = migrate_legacy_pack(
            self.data,
            offering,
            semester_label="2026-2027 S1",
            content_root=str(self.template),
        )
        self.assertTrue((result["root"] / "manifest.json").is_file())
        self.assertFalse((result["pack"] / "course.imscc").exists())
        self.assertTrue(legacy.exists())
        self.assertEqual((legacy / "course.imscc").read_bytes(), b"PK leftover")
        leftover = leftover_pack_imscc(self.data, {**offering, **result})
        self.assertEqual(leftover, legacy / "course.imscc")

    def test_migrate_repo_imscc_does_not_copy_git(self) -> None:
        """A stored pack path outside the instance is not copied into pack/."""
        external = Path(self.tmp.name) / "external" / "course.imscc"
        external.parent.mkdir(parents=True)
        external.write_bytes(b"PK external")
        offering = {
            "id": 50,
            "ontario_code": "FAKE1",
            "teacher_user_id": 8,
            "imscc_path": str(external),
        }
        result = migrate_legacy_pack(
            self.data,
            offering,
            semester_label="2026-2027 S1",
            content_root=str(self.template),
        )
        self.assertTrue((result["root"] / "manifest.json").is_file())
        self.assertFalse((result["pack"] / "course.imscc").exists())
        # External path is remembered as leftover; still not copied under data/.
        self.assertEqual(result["leftover_imscc"], external)
        self.assertEqual(list(self.data.rglob("*.imscc")), [])
        self.assertTrue(external.is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)

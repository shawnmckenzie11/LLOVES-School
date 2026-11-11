"""MCK-472: MCF3M module 5–7 bank figures resolve or the audit names them.

A reference resolves only when the image file is on disk and the LMS
serves it. The seed, teacher bank, live import, Lesson Slides card, and
catch-up card all read that same URL. Other courses and modules do not
gain image fields.
"""

from __future__ import annotations

import base64
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
from curriculum_bank_images import (  # noqa: E402
    assert_images_resolve,
    audit_bank_images,
    audit_mcf3m_modules_5_to_7,
)
from curriculum_bank_seed import seed_library_import_banks  # noqa: E402
from slide_builder import (  # noqa: E402
    load_local_bank_items,
    question_card_figure_html,
)

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
_REFS = (
    "nelson_assets/ch5/p284_q1a_tri_abc.png",
    "nelson_assets/ch5/p284_q1b_tri_def.png",
    "nelson_assets/ch5/feedback.png",
)


def _write_bank(
    cache: Path,
    course: str,
    module: int,
    strand: str,
    item: dict,
) -> None:
    """Write one ``items.json`` under the curriculum cache.

    Args:
        cache: Curriculum root.
        course: Ontario course code.
        module: One-based module index.
        strand: Strand letter.
        item: One bank item.
    """
    folder = cache / course / "banks" / f"M{module}" / strand
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "items.json").write_text(
        json.dumps({"items": [item]}),
        encoding="utf-8",
    )


def _m5_item() -> dict:
    """One MCF3M module 5 item with two figure paths and a feedback image."""
    return {
        "smart_id": "NEL-C5-GS-p274-Q1",
        "stem": "Use the triangles below.\n\na)\n\nb)",
        "image_paths": list(_REFS[:2]),
        "feedback": "<img src='nelson_assets/ch5/feedback.png' alt=''>",
        "source": "nelson",
        "verified": True,
    }


class Mcf3mBankImageTests(unittest.TestCase):
    """Figure paths for modules 5–7 are served; other modules stay plain."""

    def setUp(self) -> None:
        """Empty school plus a curriculum cache."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cache = root / "curriculum"
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root / "data",
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]),
            ontario_code="MCF3M",
        )
        library = self.school.create_library("MCF3M", origin="upload")
        self.school.attach_library(int(offering["id"]), int(library["id"]))
        self.library_id = int(library["id"])
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
        self.class_id = int(created["id"])

    def tearDown(self) -> None:
        """Close sqlite and remove the temp cache."""
        self.school.close()
        self.tmp.cleanup()

    @staticmethod
    def _remove_planted(paths: list[Path]) -> None:
        """Delete PNGs this test wrote into the repo curriculum cache.

        Args:
            paths: Files created only for the HTTP check.
        """
        for path in paths:
            path.unlink(missing_ok=True)

    def _plant(self, *rels: str) -> None:
        """Write a tiny PNG at each course-relative path.

        Args:
            rels: Paths under ``MCF3M/``, such as ``nelson_assets/ch5/a.png``.
        """
        for rel in rels:
            path = self.cache / "MCF3M" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_PNG)

    def test_missing_ref_fails_and_lists_the_path(self) -> None:
        """A bank image with no file fails the audit and names that path."""
        _write_bank(self.cache, "MCF3M", 5, "C", _m5_item())
        report = audit_bank_images("MCF3M", (5, 6, 7), cache_root=self.cache)
        self.assertEqual(report["questions_checked"], 1)
        self.assertEqual(report["refs_found"], 3)
        self.assertEqual(report["refs_resolved"], 0)
        with self.assertRaises(AssertionError) as caught:
            assert_images_resolve(report)
        message = str(caught.exception)
        for ref in _REFS:
            self.assertIn(ref, message)
        self.assertIn("NEL-C5-GS-p274-Q1", message)
        self.assertIn("not in the repo or its git history", message)

    def test_resolved_figures_show_on_bank_live_slides_and_not_elsewhere(self) -> None:
        """A real file is served and copied onto MCF3M M5 only."""
        _write_bank(self.cache, "MCF3M", 5, "C", _m5_item())
        _write_bank(
            self.cache,
            "MCF3M",
            4,
            "A",
            {
                "smart_id": "M4-Q",
                "stem": "Module four stays without a new image field.",
                "image_paths": ["nelson_assets/ch3/m4.png"],
                "source": "nelson",
                "verified": True,
            },
        )
        _write_bank(
            self.cache,
            "MCR3U",
            5,
            "D",
            {
                "smart_id": "MCR-M5",
                "stem": "The other course keeps its stem only.",
                "image_paths": ["nelson_assets/ch5/other.png"],
                "source": "nelson",
                "verified": True,
            },
        )
        self._plant(*_REFS)
        self._plant("nelson_assets/ch3/m4.png")
        (self.cache / "MCR3U" / "nelson_assets" / "ch5").mkdir(parents=True)
        (self.cache / "MCR3U" / "nelson_assets" / "ch5" / "other.png").write_bytes(_PNG)
        live_root = REPO_ROOT / ".local-data" / "curriculum" / "MCF3M"
        planted: list[Path] = []
        for rel in _REFS:
            path = live_root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_PNG)
            planted.append(path)
        self.addCleanup(self._remove_planted, planted)

        report = audit_bank_images("MCF3M", (5,), cache_root=self.cache)
        assert_images_resolve(report)
        self.assertEqual(report["refs_resolved"], 3)

        seed_library_import_banks(
            self.school, self.library_id, "MCF3M", cache_root=self.cache
        )
        mcr_library = self.school.create_library("MCR3U", origin="upload")
        seed_library_import_banks(
            self.school,
            int(mcr_library["id"]),
            "MCR3U",
            cache_root=self.cache,
        )

        found = self.school.search_module_bank_mcs(self.library_id, 5, "triangles")
        rows = [row for row in found["items"] if row.get("curriculum_open")]
        self.assertEqual(len(rows), 1)
        urls = rows[0]["image_urls"]
        self.assertEqual(len(urls), 3)
        self.assertEqual(rows[0]["image_url"], urls[0])
        for url in urls:
            served = self.client.get(url)
            self.assertEqual(served.status_code, 200, url)
            self.assertTrue(served.data.startswith(b"\x89PNG"))
        blocked = self.client.get(
            "/curriculum-bank-images/MCF3M/../secrets.png"
        )
        self.assertEqual(blocked.status_code, 404)

        placed = self.school.import_mc_to_class_playlist(
            self.class_id,
            "M5",
            "C1",
            int(rows[0]["question_id"]),
            library_id=self.library_id,
            page_number=1,
            stage="join",
        )
        self.assertEqual(placed["item"].get("image_urls"), urls)
        self.assertEqual(placed["item"].get("image_url"), urls[0])

        slides = load_local_bank_items(
            "MCF3M", 5, "C", set(), cache_root=self.cache
        )
        self.assertEqual(slides[0]["figure_files"].count("\n"), 2)
        figure_html = question_card_figure_html(slides[0])
        self.assertEqual(figure_html.count("<img"), 3)

        m4 = self.school.search_module_bank_mcs(self.library_id, 4, "Module four")
        m4_rows = [row for row in m4["items"] if row.get("curriculum_open")]
        self.assertEqual(len(m4_rows), 1)
        self.assertNotIn("image_url", m4_rows[0])
        self.assertNotIn("image_urls", m4_rows[0])
        m4_slides = load_local_bank_items(
            "MCF3M", 4, "A", set(), cache_root=self.cache
        )
        self.assertNotIn("figure_files", m4_slides[0])

        mcr = self.school.search_module_bank_mcs(
            int(mcr_library["id"]), 5, "other course"
        )
        mcr_rows = [row for row in mcr["items"] if row.get("curriculum_open")]
        self.assertEqual(len(mcr_rows), 1)
        self.assertNotIn("image_url", mcr_rows[0])

        lint = self.school.conn.execute(
            """
            SELECT excerpt FROM question_lint
            WHERE code = 'broken_image' AND excerpt LIKE '%curriculum-bank-images%'
            """
        ).fetchall()
        self.assertEqual(list(lint), [])

    def test_shipped_mcf3m_m5_m7_refs_are_resolved_or_listed(self) -> None:
        """Every shipped M5–M7 image ref resolves, or the audit lists why not.

        A file that is on disk and still unresolved fails. A file that is
        not in the repo is listed and is not replaced with a drawing.
        """
        report = audit_mcf3m_modules_5_to_7()
        self.assertGreater(report["questions_checked"], 0)
        self.assertEqual(
            report["refs_found"],
            report["refs_resolved"] + len(report["missing"]),
        )
        self.assertEqual(report["broken_but_on_disk"], [])
        for row in report["missing"]:
            self.assertTrue(row["question_id"])
            self.assertTrue(row["ref"])
            self.assertFalse(row["on_disk"])
            self.assertFalse(row["in_git"])
            self.assertIn("not in the repo or its git history", row["reason"])
        if report["missing"]:
            with self.assertRaises(AssertionError) as caught:
                assert_images_resolve(report)
            self.assertIn(report["missing"][0]["ref"], str(caught.exception))
        else:
            assert_images_resolve(report)


if __name__ == "__main__":
    unittest.main()

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
    CARTRIDGE_IMAGE_DIR,
    assert_images_resolve,
    assert_imported_figures_complete,
    audit_bank_images,
    audit_mcf3m_modules_5_to_7,
)
from curriculum_bank_seed import (  # noqa: E402
    apply_mcf3m_m567_cartridge_banks,
    seed_library_import_banks,
)
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

    def test_cartridge_m5_m7_images_resolve_and_replace_nelson(self) -> None:
        """Every image on the cartridge M5–M7 items resolves, and Nelson paths are gone."""
        report = audit_mcf3m_modules_5_to_7()
        self.assertGreater(report["questions_checked"], 0)
        self.assertGreater(report["refs_found"], 0)
        assert_images_resolve(report)
        assert_imported_figures_complete()
        self.assertEqual(report["missing"], [])
        for row in report["refs"]:
            self.assertNotIn("nelson_assets", row["ref"])
            self.assertTrue(row["url"].startswith("/curriculum-bank-images/MCF3M/cartridge/"))
            served = self.client.get(row["url"])
            self.assertEqual(served.status_code, 200, row["url"])
            self.assertGreater(len(served.data), 32)

    def test_seed_replaces_module_bank_and_keeps_old_ids(self) -> None:
        """Cartridge MC replaces the module bank; the old row keeps its id."""
        from curriculum_bank_seed import seed_library_import_banks

        cache = REPO_ROOT / ".local-data" / "curriculum"
        seed_library_import_banks(
            self.school, self.library_id, "MCF3M", cache_root=cache
        )
        found = self.school.search_module_bank_mcs(self.library_id, 5, "triangle ABC")
        rows = [row for row in found["items"] if row.get("type") == "mc"]
        self.assertTrue(rows)
        self.assertTrue(str(rows[0].get("image_url") or "").startswith("/curriculum-bank-images/"))
        self.assertIn(rows[0].get("correct_answer"), list("ABCD"))
        bank_id = int(rows[0]["bank_id"])
        self.school.conn.execute(
            """
            INSERT INTO questions (
                bank_id, import_key, item_type, title, payload_json, created_at
            ) VALUES (?, 'curriculum:MCF3M:M5:old-nelson', 'multiple_choice_question',
                      'old', '{}', '2026-01-01T00:00:00+00:00')
            """,
            (bank_id,),
        )
        old_id = int(self.school.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        self.school.conn.commit()
        seed_library_import_banks(
            self.school, self.library_id, "MCF3M", cache_root=cache
        )
        moved = self.school.conn.execute(
            """
            SELECT b.import_key FROM questions q
            JOIN question_banks b ON b.id = q.bank_id
            WHERE q.id = ?
            """,
            (old_id,),
        ).fetchone()
        self.assertEqual(moved["import_key"], "retired-curriculum:MCF3M:M5")
        still = self.school.search_module_bank_mcs(self.library_id, 5, "old")
        self.assertEqual(
            [row for row in still["items"] if row.get("question_id") == old_id],
            [],
        )


    def test_empty_choice_or_unmapped_figure_fails_the_audit(self) -> None:
        """A blank choice or a QTI image that maps to nothing fails the audit."""
        cache = Path(self.tmp.name) / "gaps"
        bank = cache / "MCF3M" / "banks" / "M5" / "C"
        bank.mkdir(parents=True)
        item = {
            "origin": "canvas_cartridge",
            "smart_id": "gap-item",
            "stem": "Which of the following is equivalent to ?",
            "stem_html": "Which of the following is equivalent to ?",
            "choices": [
                {"id": "a", "html": "", "correct": True},
                {"id": "b", "html": "", "correct": False},
            ],
            "image_urls": [],
            "figure_map": [
                {
                    "src": "https://example.test/missing.png",
                    "kind": "file",
                    "url": "",
                }
            ],
        }
        (bank / "items.json").write_text(json.dumps({"items": [item]}), encoding="utf-8")
        with self.assertRaises(AssertionError) as caught:
            assert_imported_figures_complete(cache_root=cache)
        message = str(caught.exception)
        self.assertIn("gap-item", message)
        self.assertIn("empty choice", message)
        self.assertIn("maps to nothing", message)

    def test_boot_apply_loads_mcf3m_m567_without_local_dev_login(self) -> None:
        """Production boot loads M5–M7 and leaves other courses alone."""
        os.environ.pop("LOCAL_DEV_LOGIN", None)
        other = self.school.create_library("MCR3U", origin="upload")
        first = apply_mcf3m_m567_cartridge_banks(self.school)
        self.assertFalse(first["skipped"])
        self.assertGreater(first["questions"], 0)
        sine = self.school.search_module_bank_mcs(
            self.library_id, 5, "representation of the sine law"
        )
        sine_rows = [row for row in sine["items"] if row.get("type") == "mc"]
        self.assertTrue(sine_rows)
        self.assertTrue(
            any("<img" in str(html) for html in (sine_rows[0].get("options_html") or []))
        )
        triangle = self.school.search_module_bank_mcs(self.library_id, 5, "sin B")
        triangle_rows = [row for row in triangle["items"] if row.get("type") == "mc"]
        self.assertTrue(triangle_rows)
        self.assertTrue(
            str(triangle_rows[0].get("image_url") or "").startswith("/curriculum-bank-images/")
        )
        second = apply_mcf3m_m567_cartridge_banks(self.school)
        self.assertTrue(second["skipped"])
        other_banks = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM question_banks WHERE library_id = ?",
            (int(other["id"]),),
        ).fetchone()
        self.assertEqual(int(other_banks["n"]), 0)
        module_one = self.school.conn.execute(
            """
            SELECT COUNT(*) AS n FROM question_banks
            WHERE library_id = ? AND import_key = 'curriculum-bank:MCF3M:M1'
            """,
            (self.library_id,),
        ).fetchone()
        self.assertEqual(int(module_one["n"]), 0)

    def _m567_boot_rows(self) -> dict[str, int | str | None]:
        """Count banks, questions, links, and the settings key for this library.

        Returns:
            Counts that a refused boot must leave at zero, plus the
            settings value when the key exists.
        """
        banks = self.school.conn.execute(
            """
            SELECT COUNT(*) AS n FROM question_banks
            WHERE library_id = ?
              AND (
                import_key LIKE 'curriculum-bank:MCF3M:M%'
                OR import_key LIKE 'retired-curriculum:MCF3M:M%'
              )
            """,
            (self.library_id,),
        ).fetchone()
        questions = self.school.conn.execute(
            """
            SELECT COUNT(*) AS n FROM questions q
            JOIN question_banks b ON b.id = q.bank_id
            WHERE b.library_id = ?
              AND (
                q.import_key LIKE 'cartridge:MCF3M:M%'
                OR b.import_key LIKE 'retired-curriculum:MCF3M:M%'
              )
            """,
            (self.library_id,),
        ).fetchone()
        links = self.school.conn.execute(
            """
            SELECT COUNT(*) AS n FROM course_module_bank_links
            WHERE library_id = ? AND module_number IN (5, 6, 7)
            """,
            (self.library_id,),
        ).fetchone()
        key = self.school.conn.execute(
            "SELECT value FROM school_settings WHERE key = ?",
            ("mck472_mcf3m_m567_cartridge_v2",),
        ).fetchone()
        return {
            "banks": int(banks["n"]),
            "questions": int(questions["n"]),
            "links": int(links["n"]),
            "key": None if key is None else str(key["value"]),
        }

    def _assert_boot_refused(self) -> None:
        """The refused boot wrote no banks, questions, links, or settings key."""
        state = self._m567_boot_rows()
        self.assertEqual(state["banks"], 0)
        self.assertEqual(state["questions"], 0)
        self.assertEqual(state["links"], 0)
        self.assertIsNone(state["key"])

    def test_boot_apply_refuses_when_image_directory_is_missing(self) -> None:
        """A missing figure directory does not apply, and a later boot does."""
        missing = Path(self.tmp.name) / "no-such-images"
        with self.assertLogs("curriculum_bank_seed", level="WARNING") as logs:
            refused = apply_mcf3m_m567_cartridge_banks(
                self.school, image_dir=missing
            )
        self.assertEqual(refused.get("reason"), "missing-images")
        self.assertFalse(refused["applied"])
        self.assertEqual(refused["questions"], 0)
        self.assertTrue(any("not applied" in line for line in logs.output))
        self._assert_boot_refused()
        applied = apply_mcf3m_m567_cartridge_banks(self.school)
        self.assertTrue(applied["applied"])
        self.assertGreater(applied["questions"], 0)
        self.assertIsNotNone(self._m567_boot_rows()["key"])

    def test_boot_apply_refuses_when_one_referenced_image_is_missing(self) -> None:
        """One missing figure blocks the apply until that file is present."""
        import re

        bank = REPO_ROOT / "lms" / "seeds" / "mcf3m_m567_banks" / "M5.json"
        match = re.search(
            r"/curriculum-bank-images/MCF3M/cartridge/([A-Za-z0-9][A-Za-z0-9._-]*)",
            bank.read_text(encoding="utf-8"),
        )
        self.assertIsNotNone(match)
        assert match is not None
        name = match.group(1)
        partial = Path(self.tmp.name) / "partial-images"
        partial.mkdir()
        for source in CARTRIDGE_IMAGE_DIR.iterdir():
            if source.name == name or not source.is_file():
                continue
            (partial / source.name).symlink_to(source)
        with self.assertLogs("curriculum_bank_seed", level="WARNING"):
            refused = apply_mcf3m_m567_cartridge_banks(
                self.school, image_dir=partial
            )
        self.assertEqual(refused.get("reason"), "missing-images")
        self.assertFalse(refused["applied"])
        self._assert_boot_refused()
        applied = apply_mcf3m_m567_cartridge_banks(self.school)
        self.assertTrue(applied["applied"])
        self.assertGreater(applied["questions"], 0)
        state = self._m567_boot_rows()
        self.assertGreater(state["questions"], 0)
        self.assertGreater(state["banks"], 0)
        self.assertIsNotNone(state["key"])

    def test_boot_apply_refuses_when_bank_json_is_missing(self) -> None:
        """Missing bank JSON does not set the settings key."""
        missing = Path(self.tmp.name) / "no-such-banks"
        with self.assertLogs("curriculum_bank_seed", level="WARNING") as logs:
            refused = apply_mcf3m_m567_cartridge_banks(
                self.school, banks_dir=missing
            )
        self.assertEqual(refused.get("reason"), "missing-json")
        self.assertFalse(refused["applied"])
        self.assertEqual(refused["questions"], 0)
        self.assertTrue(any("missing-json" in line for line in logs.output))
        self._assert_boot_refused()

    def test_boot_apply_refuses_when_one_module_json_is_empty(self) -> None:
        """An empty module file does not set the settings key."""
        banks = Path(self.tmp.name) / "empty-one-module"
        banks.mkdir()
        one = {
            "items": [
                {
                    "stem": "A complete stem.",
                    "origin": "canvas_cartridge",
                    "smart_id": "keep",
                    "choices": [
                        {"id": "a", "html": "yes", "correct": True},
                        {"id": "b", "html": "no", "correct": False},
                    ],
                }
            ]
        }
        (banks / "M5.json").write_text(json.dumps(one), encoding="utf-8")
        (banks / "M6.json").write_text(json.dumps({"items": []}), encoding="utf-8")
        (banks / "M7.json").write_text(json.dumps(one), encoding="utf-8")
        with self.assertLogs("curriculum_bank_seed", level="WARNING") as logs:
            refused = apply_mcf3m_m567_cartridge_banks(
                self.school, banks_dir=banks
            )
        self.assertEqual(refused.get("reason"), "empty-module")
        self.assertFalse(refused["applied"])
        self.assertTrue(any("empty-module" in line for line in logs.output))
        self._assert_boot_refused()

    def test_docker_context_ships_bank_json_and_referenced_images(self) -> None:
        """The Docker build context keeps the banks and every figure they cite."""
        patterns = _dockerignore_patterns(
            (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
        )
        hidden = ".local-data/curriculum/MCF3M/banks/M5/C/items.json"
        self.assertTrue(_dockerignore_excludes(hidden, patterns))
        banks = REPO_ROOT / "lms" / "seeds" / "mcf3m_m567_banks"
        images = REPO_ROOT / "lms" / "seeds" / "mcf3m_m567_images"
        names: set[str] = set()
        email = __import__("re").compile(
            r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
        )
        for module in (5, 6, 7):
            rel = f"lms/seeds/mcf3m_m567_banks/M{module}.json"
            path = banks / f"M{module}.json"
            self.assertTrue(path.is_file(), rel)
            self.assertFalse(_dockerignore_excludes(rel, patterns), rel)
            text = path.read_text(encoding="utf-8")
            self.assertEqual(email.findall(text), [])
            names.update(
                __import__("re").findall(
                    r"/curriculum-bank-images/MCF3M/cartridge/([A-Za-z0-9][A-Za-z0-9._-]*)",
                    text,
                )
            )
        self.assertGreater(len(names), 0)
        for name in sorted(names):
            rel = f"lms/seeds/mcf3m_m567_images/{name}"
            self.assertTrue((images / name).is_file(), rel)
            self.assertFalse(_dockerignore_excludes(rel, patterns), rel)


def _dockerignore_patterns(text: str) -> list[tuple[bool, str]]:
    """Return ``(negated, pattern)`` rows from a ``.dockerignore`` file.

    Args:
        text: File contents.
    """
    patterns: list[tuple[bool, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        patterns.append((negated, line[1:].strip() if negated else line))
    return patterns


def _dockerignore_excludes(rel: str, patterns: list[tuple[bool, str]]) -> bool:
    """True when Docker would omit ``rel`` from the build context.

    Args:
        rel: Path relative to the repository root, using forward slashes.
        patterns: Rows from :func:`_dockerignore_patterns`.
    """
    rel = rel.replace("\\", "/").lstrip("/")
    excluded = False
    for negated, pattern in patterns:
        if _dockerignore_matches(pattern, rel):
            excluded = not negated
    return excluded


def _dockerignore_matches(pattern: str, rel: str) -> bool:
    """True when one dockerignore pattern matches ``rel``.

    Args:
        pattern: One pattern, without a leading ``!``.
        rel: Repository-relative path.
    """
    import fnmatch

    pattern = pattern.replace("\\", "/").strip()
    if pattern.endswith("/"):
        folder = pattern.rstrip("/")
        return rel == folder or rel.startswith(folder + "/")
    if pattern.startswith("**/"):
        tail = pattern[3:]
        parts = rel.split("/")
        return any(fnmatch.fnmatch(part, tail) for part in parts) or fnmatch.fnmatch(
            rel, tail
        )
    if "/" not in pattern:
        return any(fnmatch.fnmatch(part, pattern) for part in rel.split("/"))
    return fnmatch.fnmatch(rel, pattern) or rel == pattern.rstrip("/") or rel.startswith(
        pattern.rstrip("/") + "/"
    )


if __name__ == "__main__":
    unittest.main()

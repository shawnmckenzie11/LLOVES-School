"""MCK-518: stem and option images resolve for MCF3M M6–M8 and MCR3U.

Display reads ``<img>`` in the stem and the choices, plus ``image_url``,
``image_src``, and ``image_paths``. It does not write question rows,
playlist cards, or past class sessions. A missing file is reported.
Off-site links stay refused. Canvas and Equatio URLs still go through
the bank image mirror.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from bank_image_mirror import (  # noqa: E402
    is_remote_bank_image_url,
    rewrite_remote_images_in_html,
)
from curriculum_bank_images import (  # noqa: E402
    borrow_missing_image_fields,
    catalogue_course_code,
    describe_question_images,
    question_images_in_scope,
    serves_figure_modules,
)

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
_STEM = "nelson_assets/mck518/stem.png"
_FIELD = "nelson_assets/mck518/field.png"
_SRC = "nelson_assets/mck518/src.png"
_PATHS = "nelson_assets/mck518/paths.png"
_OPT_B = "nelson_assets/mck518/opt-b.png"
_OPT_A = "nelson_assets/mck518/opt-a.png"
_ALL = (_STEM, _FIELD, _SRC, _PATHS, _OPT_B, _OPT_A)
_CANVAS = "https://school.instructure.com/files/stem.png"
_OFFSITE = "https://evil.example/not-a-bank.png"
_IMPORT_KEY = "cartridge:MCF3M:M6:mck518-stem-options"


def _question(import_key: str = _IMPORT_KEY) -> dict:
    """One MC with a stem image, option images, and the three image fields.

    Choice ``b`` is stored before choice ``a`` so a sort would be visible.

    Args:
        import_key: Stable key students' saved answers do not use, but
            the row must keep.
    """
    return {
        "import_key": import_key,
        "smart_id": "mck518-stem-options",
        "stem_html": f"<p>Which graph?</p><img src='{_STEM}' alt=''>",
        "image_url": _FIELD,
        "image_src": _SRC,
        "image_paths": [_PATHS],
        "choices": [
            {"id": "b", "html": f"<img src='{_OPT_B}' alt=''>", "correct": False},
            {"id": "a", "html": f"<img src='{_OPT_A}' alt=''>", "correct": True},
        ],
    }


def _plant(cache: Path, course: str, *rels: str) -> None:
    """Write a tiny PNG at each course-relative path.

    Args:
        cache: Curriculum root.
        course: Folder name. ``MCR3U-2`` figures live under ``MCR3U``.
        rels: Paths under that course folder.
    """
    for rel in rels:
        path = cache / course / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_PNG)


class QuestionImageDisplayTests(unittest.TestCase):
    """Stem and option images resolve without rewriting stored rows."""

    def setUp(self) -> None:
        """Empty curriculum cache."""
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name) / "curriculum"

    def tearDown(self) -> None:
        """Remove the temp cache."""
        self.tmp.cleanup()

    def test_scope_keeps_storage_gate_and_opens_display(self) -> None:
        """M8 and MCR3U stay out of stored figure modules and in display."""
        self.assertTrue(serves_figure_modules("MCF3M", 6))
        self.assertTrue(serves_figure_modules("MCF3M", 7))
        self.assertFalse(serves_figure_modules("MCF3M", 8))
        self.assertFalse(serves_figure_modules("MCR3U", 1))
        self.assertFalse(serves_figure_modules("MCR3U-2", 4))
        self.assertEqual(catalogue_course_code("MCR3U-2"), "MCR3U")
        for course, module in (
            ("MCF3M", 6),
            ("MCF3M", 7),
            ("MCF3M", 8),
            ("MCR3U", 2),
            ("MCR3U-2", 2),
        ):
            self.assertTrue(question_images_in_scope(course, module), (course, module))
        self.assertFalse(question_images_in_scope("MCF3M", 4))

    def _assert_shown(self, course: str, module: int, folder: str) -> None:
        """Plant figures and require stem and option URLs for one module.

        Args:
            course: Code passed to display, including ``MCR3U-2``.
            module: One-based module index.
            folder: Curriculum folder the files were written under.
        """
        _plant(self.cache, folder, *_ALL)
        original = _question(f"cartridge:{folder}:M{module}:mck518-stem-options")
        snapshot = json.dumps(original)
        shown, missing = describe_question_images(
            course, module, original, cache_root=self.cache
        )
        self.assertEqual(json.dumps(original), snapshot)
        self.assertEqual(missing, [])
        self.assertEqual(shown["import_key"], original["import_key"])
        self.assertEqual([choice["id"] for choice in shown["choices"]], ["b", "a"])
        stem_url = f"/curriculum-bank-images/{folder}/{_STEM}"
        self.assertIn(stem_url, shown["stem_html"])
        self.assertIn(f"/curriculum-bank-images/{folder}/{_OPT_B}", shown["choices"][0]["html"])
        self.assertIn(f"/curriculum-bank-images/{folder}/{_OPT_A}", shown["choices"][1]["html"])
        self.assertEqual(
            shown["options_image_urls"],
            [
                f"/curriculum-bank-images/{folder}/{_OPT_B}",
                f"/curriculum-bank-images/{folder}/{_OPT_A}",
            ],
        )
        gallery = shown["image_urls"]
        for rel in (_FIELD, _SRC, _PATHS):
            self.assertIn(f"/curriculum-bank-images/{folder}/{rel}", gallery)
        self.assertNotIn(stem_url, gallery)
        self.assertNotIn(f"/curriculum-bank-images/{folder}/{_OPT_A}", gallery)
        again, missing_again = describe_question_images(
            course, module, shown, cache_root=self.cache
        )
        self.assertEqual(again["image_urls"], shown["image_urls"])
        self.assertEqual(again["stem_html"], shown["stem_html"])
        self.assertEqual(again["choices"], shown["choices"])
        self.assertEqual(missing_again, [])

    def test_mcf3m_m6_stem_and_options(self) -> None:
        """MCF3M module 6 resolves stem HTML, option HTML, and image fields."""
        self._assert_shown("MCF3M", 6, "MCF3M")

    def test_mcf3m_m7_stem_and_options(self) -> None:
        """MCF3M module 7 resolves the same image shapes."""
        self._assert_shown("MCF3M", 7, "MCF3M")

    def test_mcf3m_m8_stem_and_options_without_storage_gate(self) -> None:
        """MCF3M module 8 resolves images even though storage does not."""
        self.assertFalse(serves_figure_modules("MCF3M", 8))
        self._assert_shown("MCF3M", 8, "MCF3M")

    def test_mcr3u_and_section_two(self) -> None:
        """MCR3U and MCR3U-2 both read the MCR3U figure folder."""
        self._assert_shown("MCR3U", 3, "MCR3U")
        self._assert_shown("MCR3U-2", 5, "MCR3U")

    def test_missing_image_is_reported_and_not_replaced(self) -> None:
        """A path with no file is named and left as stored."""
        item = _question()
        shown, missing = describe_question_images(
            "MCF3M", 6, item, cache_root=self.cache
        )
        refs = {row["ref"] for row in missing}
        self.assertIn(_STEM, refs)
        self.assertIn(_PATHS, refs)
        self.assertIn(_OPT_A, refs)
        for row in missing:
            self.assertEqual(row["question_id"], _IMPORT_KEY)
            self.assertEqual(row["reason"], "image file is not on disk")
        self.assertIn(_STEM, shown["stem_html"])
        self.assertNotIn("placeholder", json.dumps(shown).lower())
        self.assertNotIn("/static/bank-graphs/", json.dumps(shown))
        self.assertNotIn("data:image", json.dumps(shown))

    def test_offsite_refused_and_canvas_left_for_the_mirror(self) -> None:
        """Other sites stay refused. Canvas still uses the image mirror."""
        item = _question()
        upper = "HTTP://evil.example/also-refused.png"
        item["stem_html"] = f"<img src='{_OFFSITE}' alt=''><img src='{upper}' alt=''>"
        item["choices"][0]["html"] = f"<img src='{_CANVAS}' alt=''>"
        item["image_url"] = _OFFSITE
        item["image_src"] = ""
        item["image_paths"] = []
        _shown, missing = describe_question_images(
            "MCF3M", 8, item, cache_root=self.cache
        )
        reasons = {(row["ref"], row["reason"]) for row in missing}
        self.assertIn((_OFFSITE, "off-site image link refused"), reasons)
        self.assertIn((upper, "off-site image link refused"), reasons)
        self.assertNotIn(_CANVAS, {row["ref"] for row in missing})
        self.assertIn(_CANVAS, _shown["choices"][0]["html"])
        self.assertFalse(is_remote_bank_image_url(_OFFSITE))
        self.assertTrue(is_remote_bank_image_url(_CANVAS))
        with mock.patch(
            "bank_image_mirror.mirror_bank_image_url",
            return_value="web_resources/bank-mirror/canvas.png",
        ) as mirror:
            rewritten = rewrite_remote_images_in_html(
                f"<img src='{_CANVAS}' alt=''>",
                school=object(),
                library_id=1,
                class_id=1,
            )
        mirror.assert_called_once()
        self.assertIn("web_resources/bank-mirror/canvas.png", rewritten)
        self.assertNotIn(_OFFSITE, rewritten)

    def test_borrow_fills_a_card_without_touching_either_row(self) -> None:
        """A card copy gains the question's images. Both inputs stay put."""
        question = _question()
        card = {
            "import_key": question["import_key"],
            "text_html": "<p>Which graph?</p>",
            "choices": [
                {"id": "b", "html": "", "correct": False},
                {"id": "a", "html": "", "correct": True},
            ],
        }
        card_snapshot = json.dumps(card)
        question_snapshot = json.dumps(question)
        _plant(self.cache, "MCF3M", *_ALL)
        borrowed = borrow_missing_image_fields(card, question)
        self.assertEqual(json.dumps(card), card_snapshot)
        self.assertEqual(json.dumps(question), question_snapshot)
        self.assertEqual([choice["id"] for choice in borrowed["choices"]], ["b", "a"])
        self.assertIn(_OPT_B, borrowed["choices"][0]["html"])
        shown, missing = describe_question_images(
            "MCF3M", 6, borrowed, cache_root=self.cache
        )
        self.assertEqual(missing, [])
        self.assertIn("/curriculum-bank-images/MCF3M/", shown["choices"][0]["html"])
        self.assertEqual(shown["choices"][0]["id"], "b")


class QuestionImageReadDoesNotWriteTests(unittest.TestCase):
    """Bank search, playlist read, and the student card do not store the fix."""

    def setUp(self) -> None:
        """One MCF3M class and a module 8 question with relative images."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.planted = REPO_ROOT / ".local-data" / "curriculum" / "MCF3M" / "nelson_assets" / "mck518"
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
        self.offering_id = int(offering["id"])
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCF3M",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Maple"],
            offering_id=self.offering_id,
            teacher_user_id=int(self.teacher["id"]),
        )
        self.class_id = int(created["id"])
        for rel in _ALL:
            path = REPO_ROOT / ".local-data" / "curriculum" / "MCF3M" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_PNG)
        payload = _question("cartridge:MCF3M:M8:mck518-stem-options")
        now = "2026-11-24T00:00:00+00:00"
        self.school.conn.execute(
            """
            INSERT INTO question_banks (
                library_id, import_key, title, settings_json, created_at
            ) VALUES (?, 'curriculum-bank:MCF3M:M8', 'MCF3M M8', '{}', ?)
            """,
            (self.library_id, now),
        )
        self.bank_id = int(self.school.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        self.school.conn.execute(
            """
            INSERT INTO questions (
                bank_id, import_key, item_type, title, payload_json, created_at
            ) VALUES (?, ?, 'multiple_choice_question', 'graphs', ?, ?)
            """,
            (
                self.bank_id,
                payload["import_key"],
                json.dumps(payload),
                now,
            ),
        )
        self.question_id = int(self.school.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        self.school.conn.execute(
            """
            INSERT INTO course_module_bank_links (
                library_id, module_number, bank_id, confirmed_at
            ) VALUES (?, 8, ?, ?)
            """,
            (self.library_id, self.bank_id, now),
        )
        card = {
            "type": "mc",
            "text": "Which graph?",
            "text_html": f"<p>Which graph?</p><img src='{_STEM}' alt=''>",
            "options": ["left", "right"],
            "options_html": [
                f"<img src='{_OPT_B}' alt=''>",
                f"<img src='{_OPT_A}' alt=''>",
            ],
            "image_paths": [_PATHS],
            "source_question_id": self.question_id,
            "import_source": "module_bank",
        }
        self.card_json = json.dumps(card)
        self.school.conn.execute(
            """
            INSERT INTO class_live_playlist_placements (
                class_id, module, slot, page_number, stage, sort_order,
                placement_key, item_id, item_json, source_question_id, created_at
            ) VALUES (?, 'M8', 'C1', 1, 'round', 1, 'mck518', 'bank-import-mck518', ?, ?, ?)
            """,
            (self.class_id, self.card_json, self.question_id, now),
        )
        self.school.conn.execute(
            """
            INSERT INTO live_class_sessions (
                class_id, offering_id, teacher_user_id, session_code, status, started_at
            ) VALUES (?, ?, ?, 'mck518', 'ended', ?)
            """,
            (self.class_id, self.offering_id, int(self.teacher["id"]), now),
        )
        self.session_id = int(self.school.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        self.school.conn.execute(
            """
            INSERT INTO live_session_items (
                live_session_id, placement_key, item_id, stage, sort_order,
                item_json, created_at, updated_at
            ) VALUES (?, 'mck518', 'bank-import-mck518', 'round', 1, ?, ?, ?)
            """,
            (self.session_id, self.card_json, now, now),
        )
        self.school.conn.execute(
            """
            INSERT INTO live_session_prompts (
                live_session_id, slide_index, kind, payload, created_at, updated_at
            ) VALUES (?, 0, 'mc', ?, ?, ?)
            """,
            (self.session_id, self.card_json, now, now),
        )
        self.school.conn.commit()

    def tearDown(self) -> None:
        """Close sqlite and delete the planted figures."""
        self.school.close()
        self.tmp.cleanup()
        if self.planted.is_dir():
            for path in self.planted.glob("*.png"):
                path.unlink(missing_ok=True)
            self.planted.rmdir()

    def _stored(self) -> tuple[str, str, str, str, str]:
        """Return the five stored texts that display must leave alone."""
        question = self.school.conn.execute(
            "SELECT import_key, payload_json FROM questions WHERE id = ?",
            (self.question_id,),
        ).fetchone()
        card = self.school.conn.execute(
            "SELECT item_json FROM class_live_playlist_placements WHERE class_id = ?",
            (self.class_id,),
        ).fetchone()
        session = self.school.conn.execute(
            "SELECT item_json FROM live_session_items WHERE live_session_id = ?",
            (self.session_id,),
        ).fetchone()
        prompt = self.school.conn.execute(
            "SELECT payload FROM live_session_prompts WHERE live_session_id = ?",
            (self.session_id,),
        ).fetchone()
        return (
            str(question["import_key"]),
            str(question["payload_json"]),
            str(card["item_json"]),
            str(session["item_json"]),
            str(prompt["payload"]),
        )

    def test_reads_show_images_and_leave_rows_unchanged(self) -> None:
        """Search, the playlist, and the student card resolve images in memory."""
        before = self._stored()
        found = self.school.search_module_bank_mcs(self.library_id, 8, "Which graph")
        rows = [row for row in found["items"] if row.get("type") == "mc"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertIn("/curriculum-bank-images/MCF3M/", str(row.get("text_html") or ""))
        self.assertTrue(
            any("/curriculum-bank-images/MCF3M/" in str(html) for html in row.get("options_html") or [])
        )
        self.assertTrue(str(row.get("image_url") or "").startswith("/curriculum-bank-images/MCF3M/"))
        self.assertEqual(row.get("import_key") or before[0], before[0])
        placements = self.school.list_class_playlist_placements(self.class_id, "M8", "C1")
        self.assertEqual(len(placements), 1)
        shown_card = placements[0]["item"]
        self.assertIn(_STEM, str(shown_card.get("text_html") or ""))
        self.assertNotIn("/curriculum-bank-images/", json.dumps(shown_card))
        public = self.school._student_public_item_payload(
            json.loads(self.card_json),
            class_id=self.class_id,
            module="M8",
        )
        self.assertIn("/curriculum-bank-images/MCF3M/", str(public.get("text_html") or ""))
        self.assertIn(
            "/curriculum-bank-images/MCF3M/",
            str((public.get("options_html") or [""])[0]),
        )
        for url in (
            row.get("image_url"),
            f"/curriculum-bank-images/MCF3M/{_STEM}",
            f"/curriculum-bank-images/MCF3M/{_OPT_A}",
        ):
            served = self.client.get(str(url))
            self.assertEqual(served.status_code, 200, url)
            self.assertTrue(served.data.startswith(b"\x89PNG"))
        self.school.search_module_bank_mcs(self.library_id, 8, "Which graph")
        self.school.list_class_playlist_placements(self.class_id, "M8", "C1")
        self.school._student_public_item_payload(
            json.loads(self.card_json),
            class_id=self.class_id,
            module="M8",
        )
        self.assertEqual(self._stored(), before)

    def test_out_of_scope_module_skips_question_and_file_lookups(self) -> None:
        """Module 1 does not read the question row or stat image files."""
        with mock.patch.object(
            self.school, "_question_payload_for_images"
        ) as question_read, mock.patch.object(
            self.school, "_module_number_for_images"
        ) as module_read:
            public = self.school._student_public_item_payload(
                json.loads(self.card_json),
                class_id=self.class_id,
                module="M1",
            )
        question_read.assert_not_called()
        module_read.assert_not_called()
        self.assertIn(_STEM, str(public.get("text_html") or ""))
        self.assertNotIn("/curriculum-bank-images/", json.dumps(public))

    def test_ensure_live_session_items_keeps_relative_playlist_refs(self) -> None:
        """Session start stores the playlist card, not resolved image URLs."""
        self.school.conn.execute(
            "UPDATE live_class_sessions SET status = 'active' WHERE id = ?",
            (self.session_id,),
        )
        self.school.conn.commit()
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M8",
            live_slot="C1",
            stage="round",
        )
        self.school.ensure_live_session_items(self.session_id)
        row = self.school.conn.execute(
            """
            SELECT item_json FROM live_session_items
            WHERE live_session_id = ? AND item_id = 'bank-import-mck518'
            """,
            (self.session_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        stored = str(row["item_json"])
        playlist = self.school.conn.execute(
            "SELECT item_json FROM class_live_playlist_placements WHERE class_id = ?",
            (self.class_id,),
        ).fetchone()
        self.assertEqual(str(playlist["item_json"]), self.card_json)
        self.assertIn(_STEM, stored)
        self.assertIn(_OPT_A, stored)
        self.assertNotIn("/curriculum-bank-images/", stored)
        self.assertNotIn('"image_url"', stored)


if __name__ == "__main__":
    unittest.main()

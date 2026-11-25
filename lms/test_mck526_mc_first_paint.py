#!/usr/bin/env python3
"""MCK-526: the publish postcard carries display-resolved MC images."""

from __future__ import annotations

import base64
import json
import os
import shutil
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
from live_news_wire import PROMPT_CARD_MAX_BYTES, _public_card  # noqa: E402

NODE = shutil.which("node")

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
_STEM = "nelson_assets/mck526/stem.png"
_FIELD = "nelson_assets/mck526/field.png"
_OPT_A = "nelson_assets/mck526/opt-a.png"
_OPT_B = "nelson_assets/mck526/opt-b.png"
_FORBIDDEN = (
    '"key":',
    '"correct":',
    '"correct_answer":',
    '"correct_ids":',
    '"correct_index":',
    '"answer_key":',
    '"cement":',
    '"soft_key":',
    '"teacher_key":',
    '"rank_key":',
    '"email":',
    '"pin":',
    '"join_code":',
    '"catchup"',
    '"catch_up"',
    '"session_code":',
)


def _plant(rels: tuple[str, ...]) -> Path:
    """Write tiny PNGs under the local MCF3M curriculum cache."""
    root = REPO_ROOT / ".local-data" / "curriculum" / "MCF3M"
    for rel in rels:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_PNG)
    return root


def _legacy_bytes(card: dict) -> int:
    """Size of the pre-fix postcard shape (stem and text options only)."""
    slim = {
        key: card[key]
        for key in (
            "item_id",
            "prompt_id",
            "kind",
            "text",
            "options",
            "mode",
            "flow",
            "rank_mode",
            "published_ms",
            "answer_with",
        )
        if key in card
    }
    return len(json.dumps(slim, separators=(",", ":")))


class McFirstPaintPostcardTests(unittest.TestCase):
    """A published M8 image MC is complete on the first-paint postcard."""

    def setUp(self) -> None:
        """Active MCF3M live class and one image-only option MC."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.planted = _plant((_STEM, _FIELD, _OPT_A, _OPT_B))
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root / "data",
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]),
            ontario_code="MCF3M",
        )
        library = self.school.create_library("MCF3M", origin="upload")
        self.school.attach_library(int(offering["id"]), int(library["id"]))
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCF3M",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["P1"],
            offering_id=int(offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )
        self.class_id = int(created["id"])
        now = "2026-11-25T00:00:00+00:00"
        payload = {
            "import_key": "cartridge:MCF3M:M8:mck526-image-mc",
            "smart_id": "mck526-image-mc",
            "type": "mc",
            "text": "Which graph?",
            "stem_html": f"<p>Which graph?</p><img src='{_STEM}' alt=''>",
            "text_html": f"<p>Which graph?</p><img src='{_STEM}' alt=''>",
            "image_url": _FIELD,
            "image_paths": [_FIELD],
            "options": ["", ""],
            "choices": [
                {
                    "id": "b",
                    "html": f"<img src='{_OPT_B}' alt=''>",
                    "correct": False,
                },
                {
                    "id": "a",
                    "html": f"<img src='{_OPT_A}' alt=''>",
                    "correct": True,
                },
            ],
            "options_html": [
                f"<img src='{_OPT_B}' alt=''>",
                f"<img src='{_OPT_A}' alt=''>",
            ],
            "key": "B",
            "correct_answer": "a",
            "correct_ids": ["a"],
        }
        self.school.conn.execute(
            """
            INSERT INTO question_banks (
                library_id, import_key, title, settings_json, created_at
            ) VALUES (?, 'curriculum-bank:MCF3M:M8', 'MCF3M M8', '{}', ?)
            """,
            (int(library["id"]), now),
        )
        bank_id = int(self.school.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        self.school.conn.execute(
            """
            INSERT INTO questions (
                bank_id, import_key, item_type, title, payload_json, created_at
            ) VALUES (?, ?, 'multiple_choice_question', 'graphs', ?, ?)
            """,
            (bank_id, payload["import_key"], json.dumps(payload), now),
        )
        question_id = int(self.school.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        self.school.conn.execute(
            """
            INSERT INTO course_module_bank_links (
                library_id, module_number, bank_id, confirmed_at
            ) VALUES (?, 8, ?, ?)
            """,
            (int(library["id"]), bank_id, now),
        )
        card = {
            "type": "mc",
            "text": "Which graph?",
            "text_html": payload["text_html"],
            "options": ["", ""],
            "choices": payload["choices"],
            "options_html": payload["options_html"],
            "image_url": _FIELD,
            "image_paths": [_FIELD],
            "source_question_id": question_id,
            "import_source": "module_bank",
            "key": "B",
            "correct_answer": "a",
            "correct_ids": ["a"],
        }
        self.school.conn.execute(
            """
            INSERT INTO class_live_playlist_placements (
                class_id, module, slot, page_number, stage, sort_order,
                placement_key, item_id, item_json, source_question_id, created_at
            ) VALUES (?, 'M8', 'C1', 1, 'round', 1, 'mck526', 'bank-import-mck526', ?, ?, ?)
            """,
            (self.class_id, json.dumps(card), question_id, now),
        )
        self.school.conn.commit()
        self.session_id = int(
            self.school.start_live_class_session(
                self.class_id, int(self.teacher["id"])
            )["id"]
        )
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M8",
            live_slot="C1",
            stage="round",
            page_number=1,
        )
        self.school.ensure_live_session_items(self.session_id)
        item = next(
            row
            for row in self.school.list_live_session_items(self.session_id)
            if str(row.get("item_id") or "") == "bank-import-mck526"
        )
        self.published = self.school.publish_live_session_item(
            self.session_id, int(item["id"]), publish_mode="individual"
        )

    def tearDown(self) -> None:
        """Close sqlite and delete planted figures."""
        self.school.close()
        self.tmp.cleanup()
        if self.planted.is_dir():
            for path in self.planted.glob("mck526/*.png"):
                path.unlink(missing_ok=True)
            folder = self.planted / "mck526"
            if folder.is_dir():
                folder.rmdir()

    def _assert_safe(self, payload: dict) -> None:
        """Require display images and no answer-key or identity fields."""
        blob = json.dumps(payload)
        for token in _FORBIDDEN:
            self.assertNotIn(token, blob, token)
        self.assertNotIn("teacher@gmail.com", blob)

    def test_postcard_has_images_and_no_answer_key(self) -> None:
        """public_push_card and the tape card match /state image fields."""
        raw = self.school.public_push_card(self.published)
        self.assertIsNotNone(raw)
        assert raw is not None
        public = self.school._student_public_item_payload(
            self.published.get("item") if isinstance(self.published.get("item"), dict) else {},
            class_id=self.class_id,
            module="M8",
        )
        stem = f"/curriculum-bank-images/MCF3M/{_STEM}"
        opt_b = f"/curriculum-bank-images/MCF3M/{_OPT_B}"
        opt_a = f"/curriculum-bank-images/MCF3M/{_OPT_A}"
        field = f"/curriculum-bank-images/MCF3M/{_FIELD}"
        self.assertEqual(raw["kind"], "mc")
        self.assertEqual(raw["text"], "Which graph?")
        self.assertIn(stem, str(raw.get("text_html") or ""))
        self.assertIn(stem, str(public.get("text_html") or ""))
        self.assertEqual(str(raw.get("text_html") or ""), str(public.get("text_html") or ""))
        self.assertIn(opt_b, str((raw.get("options_html") or [""])[0]))
        self.assertIn(opt_a, str((raw.get("options_html") or ["", ""])[1]))
        self.assertEqual(raw.get("options_html"), public.get("options_html"))
        self.assertEqual(
            raw.get("options_image_urls") or public.get("options_image_urls"),
            public.get("options_image_urls"),
        )
        self.assertIn(opt_b, raw.get("options_image_urls") or [])
        self.assertIn(field, raw.get("image_urls") or [raw.get("image_url")])
        self.assertTrue(raw.get("options"), raw.get("options"))
        self.assertEqual(len(raw.get("options") or []), 2)
        self._assert_safe(raw)

        wire = _public_card({**raw, "published_ms": 1795000000123, "correct_answer": "a", "key": "B"})
        self.assertIsNotNone(wire)
        assert wire is not None
        self.assertEqual(wire["published_ms"], 1795000000123)
        self.assertIn(stem, str(wire.get("text_html") or ""))
        self.assertIn(opt_b, str((wire.get("options_html") or [""])[0]))
        self.assertIn(opt_a, str((wire.get("options_image_urls") or ["", ""])[1]))
        self.assertIn(field, wire.get("image_urls") or [wire.get("image_url")])
        self._assert_safe(wire)

        new_bytes = len(json.dumps(wire, separators=(",", ":")))
        old_bytes = _legacy_bytes(wire)
        delta = new_bytes - old_bytes
        self.assertGreater(delta, 0)
        self.assertLess(new_bytes, PROMPT_CARD_MAX_BYTES)
        # Recorded for the ship note: this fixture's first-paint add-on.
        self.assertLess(delta, 2500)
        # Typical M8 image-only MC on the tape: 155 -> 750 bytes (+595).
        # The 4096-byte prompt-card cap is unchanged.

    def test_wire_rejects_remote_image_urls_and_keys(self) -> None:
        """Off-site figures and answer keys never make the stored postcard."""
        cleaned = _public_card(
            {
                "item_id": 4,
                "prompt_id": 2,
                "kind": "mc",
                "text": "Which graph?",
                "options": ["A", "B"],
                "image_url": "https://evil.example/x.png",
                "image_urls": ["//evil.example/y.png", "/curriculum-bank-images/MCF3M/ok.png"],
                "options_html": [
                    '<img src="https://evil.example/z.png"><script>alert(1)</script>',
                    '<img src="/curriculum-bank-images/MCF3M/opt-b.png" alt="">',
                ],
                "correct_answer": "A",
                "name": "kept-off-the-wire",
            }
        )
        self.assertIsNotNone(cleaned)
        assert cleaned is not None
        blob = json.dumps(cleaned)
        self.assertNotIn("evil.example", blob)
        self.assertNotIn("script", blob.lower())
        self.assertNotIn("correct_answer", blob)
        self.assertNotIn("kept-off-the-wire", blob)
        self.assertIn("/curriculum-bank-images/MCF3M/ok.png", cleaned.get("image_urls") or [])
        self.assertIn(
            "/curriculum-bank-images/MCF3M/opt-b.png",
            str((cleaned.get("options_html") or ["", ""])[1]),
        )

    @unittest.skipUnless(NODE, "node is required for the first-paint JS harness")
    def test_apply_pushed_question_card_renders_image_only_options(self) -> None:
        """contentFromPushedQuestionCard paints buttons for image-only options."""
        src = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn("contentFromPushedQuestionCard(card)", src)
        self.assertIn("liveTiming.notePush(card)", src)
        result = subprocess.run(
            [NODE, str(LMS_DIR / "static" / "pushed_question_card.test.mjs")],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("ok", result.stdout)


if __name__ == "__main__":
    unittest.main()

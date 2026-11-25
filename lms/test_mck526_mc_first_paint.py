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
from live_mc import extract_choice_labels  # noqa: E402
from live_news_wire import PROMPT_CARD_MAX_BYTES, _public_card  # noqa: E402
from live_prompt_feedback import (  # noqa: E402
    TEACHER_ONLY_FIELDS,
    strip_teacher_prompt_fields,
)

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


class McFirstPaintSubmitTests(unittest.TestCase):
    """Postcard and /state submits share letters, including image-only slots."""

    def setUp(self) -> None:
        """Live MCF3M class, four seats, and groups for pick-then-agree."""
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
        teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]),
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
            codenames=["P1", "P2", "P3", "P4"],
            offering_id=int(offering["id"]),
            teacher_user_id=int(teacher["id"]),
        )
        self.class_id = int(created["id"])
        self.teacher_id = int(teacher["id"])
        self.session_id = int(
            self.school.start_live_class_session(self.class_id, self.teacher_id)["id"]
        )
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M8",
            live_slot="C1",
            stage="round",
            page_number=1,
        )
        self.school.game.begin_game(self.class_id)
        with self.school.game._lock:
            rows = self.school.game.conn.execute(
                "SELECT id, codename FROM students WHERE class_id = ? ORDER BY id",
                (self.class_id,),
            ).fetchall()
        self.ids = [int(row["id"]) for row in rows]
        for row in rows:
            self.school.join_live_class_session(
                self.session_id, int(row["id"]), codename=str(row["codename"])
            )
        self.school.setup_live_session_groups(
            self.session_id,
            n_teams=2,
            mode="manual",
            present_ids=self.ids,
            assignments=[
                {"student_id": sid, "team_index": i % 2} for i, sid in enumerate(self.ids)
            ],
        )
        self.school.ensure_live_session_items(self.session_id)
        self._tokens = 0

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

    def _image_only_card(self) -> dict:
        """MCF3M-style image MC with the key on slot B, not A."""
        htmls = [
            f"<img src='{_OPT_B}' alt=''>",
            f"<img src='{_OPT_A}' alt=''>",
            f"<img src='{_FIELD}' alt=''>",
        ]
        return {
            "type": "mc",
            "text": "Which graph?",
            "options": ["", "", ""],
            "choices": [
                {"id": "x", "html": htmls[0], "correct": False},
                {"id": "a", "html": htmls[1], "correct": True},
                {"id": "z", "html": htmls[2], "correct": False},
            ],
            "options_html": htmls,
            "image_url": _FIELD,
            "key": "B",
            "correct_answer": "B",
            "correct_index": 1,
            "import_source": "module_bank",
        }

    def _mixed_card(self) -> dict:
        """Text options around one image-only slot, keyed on B."""
        return {
            "type": "mc",
            "text": "Which graph?",
            "options": ["Constant rate", "", "A curve"],
            "choices": [
                {"id": "a", "text": "Constant rate"},
                {"id": "b", "html": f"<img src='{_OPT_A}' alt=''>"},
                {"id": "c", "text": "A curve"},
            ],
            "options_html": ["", f"<img src='{_OPT_A}' alt=''>", ""],
            "options_image_urls": ["", _OPT_A, ""],
            "key": "B",
            "correct_answer": "B",
            "correct_index": 1,
            "import_source": "module_bank",
        }

    def _publish(self, card: dict, *, mode: str) -> dict:
        """Insert one playlist row and publish it."""
        self._tokens += 1
        token = f"bank-import-mck526-s{self._tokens}"
        now = "2026-11-25T00:00:00+00:00"
        placed = dict(card)
        placed["item_id"] = token
        self.school.conn.execute(
            """
            INSERT INTO class_live_playlist_placements (
                class_id, module, slot, page_number, stage, sort_order,
                placement_key, item_id, item_json, created_at
            ) VALUES (?, 'M8', 'C1', 1, 'round', ?, ?, ?, ?, ?)
            """,
            (
                self.class_id,
                self._tokens,
                f"mck526-s{self._tokens}",
                token,
                json.dumps(placed),
                now,
            ),
        )
        self.school.conn.commit()
        self.school.ensure_live_session_items(self.session_id)
        item = next(
            row
            for row in self.school.list_live_session_items(self.session_id)
            if str(row.get("item_id") or "") == token
        )
        return self.school.publish_live_session_item(
            self.session_id, int(item["id"]), publish_mode=mode
        )

    def _render_letters(self, item: dict) -> tuple[list[str], list[str]]:
        """Return postcard labels and /state labels for one published item."""
        card = self.school.public_push_card(item)
        self.assertIsNotNone(card)
        assert card is not None
        postcard = list(card.get("options") or [])
        payload = self.school.student_live_items_payload(self.session_id, self.ids[0])
        row = next(
            entry
            for entry in payload.get("live_items") or []
            if int(entry["id"]) == int(item["id"])
        )
        content = row.get("content") or {}
        state = extract_choice_labels(content)
        blob = json.dumps(content)
        for token in ('"correct_ids"', '"correct_index"', '"correct_answer"', '"key":'):
            self.assertNotIn(token, blob, token)
        return postcard, state

    def _tally_by_id(self, item: dict) -> dict[str, dict]:
        """Map teacher tally letters to rows."""
        prompt = self.school._prompt_for_live_item(item)
        assert prompt is not None
        teacher = self.school.live_session_teacher_state_payload(self.session_id)
        tally = self.school._tally_for_prompt(self.session_id, prompt, teacher)
        return {str(row.get("id")): row for row in (tally or {}).get("choices") or []}

    def _assert_keyed_and_same_letters(self, item: dict, expected: list[str]) -> str:
        """Assert scoring and both renders share ``expected``; return the key letter."""
        postcard, state = self._render_letters(item)
        self.assertEqual(postcard, expected)
        self.assertEqual(state, expected)
        self.assertEqual(self.school._mc_option_labels(item), expected)
        self.assertEqual(self.school._mc_key_letter(item), "B")
        self.assertTrue(self.school._group_mc_answer_correct(item, "B"))
        self.assertFalse(self.school._group_mc_answer_correct(item, "A"))
        return "B"

    def test_teacher_only_key_fields_are_stripped(self) -> None:
        """correct, correct_ids, and correct_index stay off the student payload."""
        for field in ("correct", "correct_ids", "correct_index"):
            self.assertIn(field, TEACHER_ONLY_FIELDS)
        out = strip_teacher_prompt_fields(
            {
                "type": "mc",
                "text": "Which graph?",
                "correct": True,
                "correct_ids": ["a"],
                "correct_index": 1,
                "items": [
                    {
                        "correct": True,
                        "correct_ids": ["a"],
                        "correct_index": 1,
                        "text": "x",
                    }
                ],
            }
        )
        for field in ("correct", "correct_ids", "correct_index"):
            self.assertNotIn(field, out)
            self.assertNotIn(field, out["items"][0])
        self.assertEqual(out["text"], "Which graph?")

    def test_text_only_labels_stay_the_option_text(self) -> None:
        """Existing text decks still score and pick by the written labels."""
        item = {
            "item": {
                "type": "mc",
                "options": ["2", "1", "-2", "0"],
                "key": "A",
            }
        }
        self.assertEqual(self.school._mc_option_labels(item), ["2", "1", "-2", "0"])
        self.assertEqual(self.school._mc_key_letter(item), "A")
        self.assertTrue(self.school._group_mc_answer_correct(item, "2"))
        self.assertFalse(self.school._group_mc_answer_correct(item, "1"))

    def test_individual_image_only_submits_tally_together(self) -> None:
        """Postcard and /state both send B; the keyed bar counts both."""
        item = self._publish(self._image_only_card(), mode="individual")
        key = self._assert_keyed_and_same_letters(item, ["A", "B", "C"])
        prompt = self.school._prompt_for_live_item(item)
        assert prompt is not None
        postcard, state = self._render_letters(item)
        self.school.submit_live_prompt_response(
            int(prompt["id"]), self.ids[0], {"choice": postcard[1]}
        )
        self.school.submit_live_prompt_response(
            int(prompt["id"]), self.ids[1], {"choice": state[1]}
        )
        self.school.submit_live_prompt_response(
            int(prompt["id"]), self.ids[2], {"choice": "A"}
        )
        bars = self._tally_by_id(item)
        self.assertEqual(bars[key]["count"], 2)
        self.assertTrue(bars[key]["correct"])
        self.assertEqual(bars["A"]["count"], 1)
        self.assertFalse(bars["A"]["correct"])
        tiers = [
            self.school.student_live_engagement_fields(
                self.class_id, self.session_id, sid
            )["tier1"]
            for sid in self.ids[:3]
        ]
        self.assertEqual(len(set(tiers)), 1, tiers)
        self.assertNotEqual(tiers[0], "0/0")
        for sid in self.ids[:3]:
            row = self.school.get_live_prompt_response(int(prompt["id"]), sid)
            self.assertIsNone((row or {}).get("awarded_points"))

    def test_individual_mixed_list_submits_tally_together(self) -> None:
        """A mixed text and image list still accepts B from both renders."""
        item = self._publish(self._mixed_card(), mode="individual")
        key = self._assert_keyed_and_same_letters(
            item, ["Constant rate", "B", "A curve"]
        )
        prompt = self.school._prompt_for_live_item(item)
        assert prompt is not None
        postcard, state = self._render_letters(item)
        self.school.submit_live_prompt_response(
            int(prompt["id"]), self.ids[0], {"choice": postcard[1]}
        )
        self.school.submit_live_prompt_response(
            int(prompt["id"]), self.ids[1], {"choice": state[1]}
        )
        bars = self._tally_by_id(item)
        self.assertEqual(bars[key]["count"], 2)
        self.assertTrue(bars[key]["correct"])
        tiers = [
            self.school.student_live_engagement_fields(
                self.class_id, self.session_id, sid
            )["tier1"]
            for sid in self.ids[:2]
        ]
        self.assertEqual(tiers[0], tiers[1])
        self.assertNotEqual(tiers[0], "0/0")

    def _send_group(self, item: dict, student_index: int, choices: list[str]) -> None:
        """Teammates pick the given values, then the first sends the shared answer."""
        team = student_index % 2
        members = [index for index, _sid in enumerate(self.ids) if index % 2 == team]
        for index, choice in zip(members, choices):
            self.school.submit_group_mc_pick(
                self.session_id,
                int(item["id"]),
                self.ids[index],
                choice=choice,
            )
        self.school.submit_group_mc_answer(
            self.session_id,
            int(item["id"]),
            self.ids[student_index],
            choice=choices[0],
            why="because",
        )

    def test_group_image_only_picks_and_sends_the_letter(self) -> None:
        """Group pick accepts the image-only letter from both render paths."""
        item = self._publish(self._image_only_card(), mode="group_submit")
        self._assert_keyed_and_same_letters(item, ["A", "B", "C"])
        postcard, state = self._render_letters(item)
        self._send_group(item, 0, [postcard[1], state[1]])
        self.assertTrue(self.school._group_mc_answer_correct(item, "B"))
        view = self.school.live_session_item_results(self.session_id, int(item["id"]))
        self.assertEqual(view["answer_key"], "B")
        marks = [row.get("correct") for row in view["status_board"] if "correct" in row]
        self.assertIn(True, marks)
        self.assertNotIn(False, marks)

    def test_group_mixed_list_accepts_the_image_only_letter(self) -> None:
        """A mixed list does not reject an image-only B pick."""
        item = self._publish(self._mixed_card(), mode="group_submit")
        self._assert_keyed_and_same_letters(item, ["Constant rate", "B", "A curve"])
        postcard, state = self._render_letters(item)
        self._send_group(item, 0, [postcard[1], state[1]])
        self.assertTrue(self.school._group_mc_answer_correct(item, "B"))
        view = self.school.live_session_item_results(self.session_id, int(item["id"]))
        self.assertEqual(view["answer_key"], "B")
        marks = [row.get("correct") for row in view["status_board"] if "correct" in row]
        self.assertEqual(marks, [True])




if __name__ == "__main__":
    unittest.main()

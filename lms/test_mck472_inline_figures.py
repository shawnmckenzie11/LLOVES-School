"""MCK-472: equation images go back into the stem, and TeX still typesets.

The Module 5 Test stem ``if = 12.5`` lost three side labels. The cartridge
``image_urls`` list them after the triangle. The student card has to show
those labels in the sentence and typeset a TeX choice.
"""

from __future__ import annotations

import inspect
import json
import os
import secrets
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
from bank_mc_normalize import normalize_bank_mc  # noqa: E402
from school_db import SchoolDB  # noqa: E402
from bank_stem_figures import (  # noqa: E402
    apply_inline_equation_repairs,
    restore_inline_equation_images,
    unmapped_seed_stems,
)

_BANKS = LMS_DIR / "seeds" / "mcf3m_m567_banks"
_CARD = LMS_DIR / "static" / "live_question_card.js"
_HOLE_ID = "gb1c38846e96a2dff8130d75812d1e198"
_TEX_ID = "bae7bc800cb3df3dc0642e776e0284a3"


def _items(module: int) -> list[dict]:
    """Return the committed items for one MCF3M module.

    Args:
        module: 5, 6, or 7.
    """
    document = json.loads((_BANKS / f"M{module}.json").read_text(encoding="utf-8"))
    items = document.get("items") if isinstance(document, dict) else None
    if not isinstance(items, list):
        raise AssertionError(f"M{module} has no items")
    return [item for item in items if isinstance(item, dict)]


def _item(smart_id: str) -> dict:
    """Return one committed bank item by ``smart_id``.

    Args:
        smart_id: Cartridge question id.
    """
    for module in (5, 6, 7):
        for item in _items(module):
            if str(item.get("smart_id") or "") == smart_id:
                return item
    raise AssertionError(f"missing {smart_id}")


def _choice_html(item: dict) -> str:
    """Join choice HTML for one bank item.

    Args:
        item: Cartridge bank item.
    """
    parts: list[str] = []
    for choice in item.get("choices") or []:
        if isinstance(choice, dict):
            parts.append(str(choice.get("html") or ""))
        else:
            parts.append(str(choice or ""))
    return "\n".join(parts)


def _student_card(content: dict) -> dict[str, str]:
    """Render the student question card the phone and catch-up share.

    Args:
        content: Student-safe question payload.

    Returns:
        ``head`` and ``controls`` HTML from ``live_question_card.js``.
    """
    script = """
const { liveQuestionHeadHtml, liveAnswerControlsHtml } = await import(process.env.CARD_URL);
const content = JSON.parse(process.env.CONTENT);
const format = (value) => String(value ?? "");
const head = liveQuestionHeadHtml(content, "mc", format);
const controls = liveAnswerControlsHtml("mc", content, "individual", "—", "Submit Answer");
process.stdout.write(JSON.stringify({ head, controls }));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "CARD_URL": _CARD.resolve().as_uri(),
            "CONTENT": json.dumps(content),
        },
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    parsed = json.loads(completed.stdout)
    return {"head": str(parsed["head"]), "controls": str(parsed["controls"])}


class InlineEquationImageTests(unittest.TestCase):
    """Holed stems gain their equation images in IMSCC order."""

    def test_seed_holes_map_and_the_triangle_stem_keeps_order(self) -> None:
        """``if = 12.5`` gets the three labels, and the triangle stays a figure."""
        self.assertEqual(unmapped_seed_stems(_BANKS), [])
        item = _item(_HOLE_ID)
        urls = [str(url) for url in item["image_urls"]]
        restored, reason = restore_inline_equation_images(
            str(item["stem_html"]),
            urls,
            choice_html=_choice_html(item),
            stem_text=str(item["stem"]),
        )
        self.assertIsNone(reason)
        self.assertNotIn(urls[0], restored)
        positions = [restored.index(url) for url in urls[1:]]
        self.assertLess(positions[0], restored.index("12.5"))
        self.assertLess(positions[1], restored.index("10.1"))
        self.assertLess(positions[2], restored.index("10.8"))
        self.assertLess(positions[0], positions[1])
        self.assertLess(positions[1], positions[2])
        self.assertNotIn("<span>=</span>", restored)
        again, again_reason = restore_inline_equation_images(
            restored,
            urls,
            choice_html=_choice_html(item),
            stem_text=str(item["stem"]),
        )
        self.assertEqual(again, restored)
        self.assertIsNone(again_reason)

    def test_every_changed_seed_stem_is_stable(self) -> None:
        """Each mapped stem keeps its images on a second pass."""
        changed = 0
        for module in (5, 6, 7):
            for item in _items(module):
                urls = [str(url) for url in (item.get("image_urls") or [])]
                restored, reason = restore_inline_equation_images(
                    str(item.get("stem_html") or ""),
                    urls,
                    choice_html=_choice_html(item),
                    stem_text=str(item.get("stem") or ""),
                )
                self.assertIsNone(reason, item.get("smart_id"))
                if restored == str(item.get("stem_html") or ""):
                    continue
                changed += 1
                again, _again_reason = restore_inline_equation_images(
                    restored,
                    urls,
                    choice_html=_choice_html(item),
                    stem_text=str(item.get("stem") or ""),
                )
                self.assertEqual(again, restored)
                self.assertIn('class="equation-inline"', restored)
        self.assertEqual(changed, 28)

    def test_m7_colon_and_step_holes_gain_their_figures(self) -> None:
        """The three missed Module 7 stems get every equation image in order."""
        statement = _item("gbb5ff752a04da652c83a01bcbdfae7a3")
        restored, reason = restore_inline_equation_images(
            str(statement["stem_html"]),
            list(statement["image_urls"]),
            choice_html=_choice_html(statement),
            stem_text=str(statement["stem"]),
        )
        self.assertIsNone(reason)
        self.assertIn(statement["image_urls"][0], restored)
        self.assertNotIn(": .", restored)

        steps = _item("g389ac0cca77b981014e32bbe39af1f37")
        restored, reason = restore_inline_equation_images(
            str(steps["stem_html"]),
            list(steps["image_urls"]),
            choice_html=_choice_html(steps),
            stem_text=str(steps["stem"]),
        )
        self.assertIsNone(reason)
        self.assertIn(steps["image_urls"][0], restored)
        self.assertNotIn(steps["image_urls"][1], restored)
        self.assertNotIn("<span></span>", restored)

        table = _item("g3fc6b5075ccc94b4771ab6ec2bb8b120")
        restored, reason = restore_inline_equation_images(
            str(table["stem_html"]),
            list(table["image_urls"]),
            choice_html=_choice_html(table),
            stem_text=str(table["stem"]),
        )
        self.assertIsNone(reason)
        positions = [restored.index(url) for url in table["image_urls"]]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn('<td class="pad"></td>', restored)
        self.assertNotIn(": .", restored)

    def test_student_card_shows_the_label_and_typesets_a_tex_choice(self) -> None:
        """The phone card contains the restored label and a KaTeX choice span."""
        hole = _item(_HOLE_ID)
        live, err = normalize_bank_mc(
            question_id=1,
            bank_id=2,
            item_type="multiple_choice_question",
            payload=hole,
        )
        self.assertIsNone(err)
        assert live is not None
        label = str(hole["image_urls"][1])
        self.assertIn(label, live["text_html"])
        self.assertNotIn(str(hole["image_urls"][0]), live["text_html"])
        card = _student_card(live)
        self.assertIn(label, card["head"])
        self.assertIn('class="equation-inline"', card["head"])
        hero, _stem = card["head"].split("<h2>", 1)
        diagram = str(hole["image_urls"][0])
        self.assertIn(diagram, hero)
        self.assertNotIn(label, hero)
        self.assertIn(label, _stem)

        tex = _item(_TEX_ID)
        tex_live, tex_err = normalize_bank_mc(
            question_id=3,
            bank_id=4,
            item_type="multiple_choice_question",
            payload=tex,
        )
        self.assertIsNone(tex_err)
        assert tex_live is not None
        self.assertTrue(
            any('class="math-latex"' in html for html in tex_live["options_html"])
        )
        tex_card = _student_card(tex_live)
        self.assertIn('class="math-latex"', tex_card["controls"])
        self.assertIn("data-latex=", tex_card["controls"])
        self.assertIn(r"\frac{3}{5}", tex_card["controls"])


class InlineEquationRepairTests(unittest.TestCase):
    """Already-imported rows gain the images without a delete."""

    def setUp(self) -> None:
        """Empty school database."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root / "data",
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]

    def tearDown(self) -> None:
        """Close sqlite and remove the temp directory."""
        self.school.close()
        self.tmp.cleanup()

    def _insert_question(
        self,
        payload: dict,
        import_key: str,
        *,
        course: str = "MCF3M",
        bank_key: str = "curriculum-bank:MCF3M:M5",
    ) -> int:
        """Insert one bank question and return its id.

        Args:
            payload: ``questions.payload_json``.
            import_key: Stable key for the question.
            course: Library course code.
            bank_key: Bank import key, including the module.
        """
        library = self.school.create_library(course, origin="upload")
        with self.school._lock:
            bank = self.school.conn.execute(
                """
                INSERT INTO question_banks (
                    library_id, import_key, title, settings_json, created_at
                ) VALUES (?, ?, 'Module bank', '{}', '2026-11-17T00:00:00+00:00')
                """,
                (int(library["id"]), bank_key),
            )
            question = self.school.conn.execute(
                """
                INSERT INTO questions (
                    bank_id, import_key, item_type, title, payload_json, created_at
                ) VALUES (?, ?, 'multiple_choice_question', 'stem', ?, '2026-11-17T00:00:00+00:00')
                """,
                (int(bank.lastrowid), import_key, json.dumps(payload)),
            )
            self.school.conn.commit()
            return int(question.lastrowid)

    def test_repair_updates_bank_and_placement_then_stops(self) -> None:
        """A holed bank row and its copied card gain the label once."""
        item = _item(_HOLE_ID)
        payload = {
            "smart_id": _HOLE_ID,
            "stem_html": item["stem_html"],
            "text": item["stem"],
            "image_urls": item["image_urls"],
            "image_url": item["image_urls"][0],
            "choices": item["choices"],
            "correct_ids": item["correct_ids"],
        }
        question_id = self._insert_question(
            payload, f"cartridge:MCF3M:M5:{_HOLE_ID}"
        )
        other = dict(payload)
        other_id = self._insert_question(
            other,
            f"cartridge:MCR3U:M5:{_HOLE_ID}",
            course="MCR3U",
            bank_key="curriculum-bank:MCR3U:M5",
        )
        edited = dict(payload)
        edited["text"] = "Rewritten stem that no longer matches the cartridge."
        edited["edited_in_lms"] = True
        edited_id = self._insert_question(
            edited,
            f"cartridge:MCF3M:M5:{_HOLE_ID}-edited",
            bank_key="curriculum-bank:MCF3M:M5:edited",
        )
        placed = {
            "type": "mc",
            "text": item["stem"],
            "text_html": item["stem_html"],
            "image_urls": item["image_urls"],
            "image_url": item["image_urls"][0],
            "options_html": ["41", "47"],
        }
        with self.school._lock:
            self.school.conn.execute(
                """
                INSERT INTO class_live_playlist_placements (
                    class_id, module, slot, page_number, stage, sort_order,
                    placement_key, item_id, item_json, source_question_id,
                    created_at
                ) VALUES (1, 'M5', 'C2', 1, 'round', 0, 'place-1', 'item-1', ?, ?, '2026-11-17T00:00:00+00:00')
                """,
                (json.dumps(placed), question_id),
            )
            self.school.conn.commit()
        first = apply_inline_equation_repairs(self.school)
        self.assertEqual(first["questions"], 1)
        self.assertEqual(first["placements"], 1)
        self.assertEqual(first["prompts"], 0)
        self.assertEqual(first["catchup"], 0)
        stored = json.loads(
            self.school.conn.execute(
                "SELECT payload_json FROM questions WHERE id = ?",
                (question_id,),
            ).fetchone()["payload_json"]
        )
        label = str(item["image_urls"][1])
        self.assertIn(label, stored["stem_html"])
        self.assertEqual(stored["choices"], item["choices"])
        self.assertEqual(stored["image_urls"], item["image_urls"])
        card = json.loads(
            self.school.conn.execute(
                "SELECT item_json FROM class_live_playlist_placements WHERE source_question_id = ?",
                (question_id,),
            ).fetchone()["item_json"]
        )
        self.assertIn(label, card["text_html"])
        untouched = json.loads(
            self.school.conn.execute(
                "SELECT payload_json FROM questions WHERE id = ?",
                (other_id,),
            ).fetchone()["payload_json"]
        )
        self.assertEqual(untouched["stem_html"], other["stem_html"])
        edited_row = json.loads(
            self.school.conn.execute(
                "SELECT payload_json FROM questions WHERE id = ?",
                (edited_id,),
            ).fetchone()["payload_json"]
        )
        self.assertEqual(edited_row["stem_html"], edited["stem_html"])
        self.assertNotIn(label, edited_row["stem_html"])
        second = apply_inline_equation_repairs(self.school)
        self.assertEqual(
            second,
            {"questions": 0, "placements": 0, "prompts": 0, "catchup": 0},
        )
        question_count = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM questions"
        ).fetchone()["n"]
        self.assertGreaterEqual(int(question_count), 3)

    def test_repaired_prompt_moves_the_student_poll_stamp(self) -> None:
        """A phone holding the previous stamp rebuilds after the stem HTML changes."""
        revision = inspect.getsource(SchoolDB._rendered_card_revision)
        self.assertEqual(revision.count("FROM live_session_prompts"), 1)
        self.assertIn("$.text_html", revision)
        item = _item(_HOLE_ID)
        payload = {
            "smart_id": _HOLE_ID,
            "stem_html": item["stem_html"],
            "text": item["stem"],
            "image_urls": item["image_urls"],
            "image_url": item["image_urls"][0],
            "choices": item["choices"],
            "correct_ids": item["correct_ids"],
        }
        question_id = self._insert_question(
            payload, f"cartridge:MCF3M:M5:{_HOLE_ID}"
        )
        prompt = {
            "type": "mc",
            "smart_id": _HOLE_ID,
            "source_question_id": question_id,
            "text": item["stem"],
            "text_html": item["stem_html"],
            "image_urls": list(item["image_urls"]),
            "options": ["41", "47", "92", "88"],
        }
        with self.school._lock:
            session = self.school.conn.execute(
                """
                INSERT INTO live_class_sessions (
                    class_id, offering_id, session_code, status, started_at,
                    is_test, teacher_state_json
                ) VALUES (1, 1, ?, 'active', '2026-11-17T00:00:00+00:00', 1, '{}')
                """,
                (secrets.token_hex(8),),
            )
            self.school.conn.execute(
                """
                INSERT INTO live_session_prompts (
                    live_session_id, slide_index, kind, payload, active,
                    created_at, updated_at
                ) VALUES (?, 0, 'mc', ?, 1, '2026-11-16T00:00:00+00:00',
                          '2026-11-16T00:00:00+00:00')
                """,
                (int(session.lastrowid), json.dumps(prompt)),
            )
            self.school.conn.commit()
            session_id = int(session.lastrowid)
        before = self.school.live_student_poll_stamp(session_id, 1)
        calls = {"n": 0}

        def trace(sql: str) -> None:
            calls["n"] += 1

        self.school.conn.set_trace_callback(trace)
        try:
            again = self.school.live_student_poll_stamp(session_id, 1)
        finally:
            self.school.conn.set_trace_callback(None)
        self.assertEqual(again, before)
        poll_queries = calls["n"]
        self.assertGreater(poll_queries, 0)
        repaired = apply_inline_equation_repairs(self.school)
        self.assertEqual(repaired["prompts"], 1)
        stored = self.school.conn.execute(
            "SELECT payload, updated_at FROM live_session_prompts WHERE live_session_id = ?",
            (session_id,),
        ).fetchone()
        self.assertIn(str(item["image_urls"][1]), stored["payload"])
        self.assertNotEqual(stored["updated_at"], "2026-11-16T00:00:00+00:00")
        teacher = self.school.live_session_teacher_state_payload(session_id)
        seq = int(teacher.get("state_seq") or 0)
        self.assertIsNone(
            self.school.student_live_poll_unchanged(session_id, 1, seq, before)
        )
        calls["n"] = 0
        self.school.conn.set_trace_callback(trace)
        try:
            self.school.live_student_poll_stamp(session_id, 1)
        finally:
            self.school.conn.set_trace_callback(None)
        self.assertEqual(calls["n"], poll_queries)

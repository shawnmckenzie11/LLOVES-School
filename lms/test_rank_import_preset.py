#!/usr/bin/env python3
"""MCK-169 S1/S2: Answer order vs Opinion rank rows in Import from bank.

S1 is client-side (``static/bank_rank_rows.js``, node cases in
``bank_rank_rows.test.mjs``) plus picker wiring. S2: importing an Answer
order rank presets that card to Group · take turns; the teacher can change
it until Publish; Opinion ranks keep today's default; nothing is stored on
the bank row.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

import test_group_mc_pick_turns_mck155 as turns_base  # noqa: E402

NODE = shutil.which("node")


class RankImportPresetTests(unittest.TestCase):
    """Real MCR3U class with two teams of two and a live session on M1 C2."""

    # Borrowed methods only: an imported TestCase in module globals would
    # run its whole suite again here.
    _turns_setup = turns_base.PickThenAgreeAndTurnsTests.setUp
    tearDown = turns_base.PickThenAgreeAndTurnsTests.tearDown
    _post = turns_base.PickThenAgreeAndTurnsTests._post
    _card = turns_base.PickThenAgreeAndTurnsTests._card
    _opts = turns_base.PickThenAgreeAndTurnsTests._opts

    def setUp(self) -> None:
        self._turns_setup()
        row = self.school.conn.execute(
            "SELECT offering_id FROM classes WHERE id = ?", (self.class_id,)
        ).fetchone()
        self.library_id = int(self.school.create_library("MCR3U", origin="upload")["id"])
        self.school.attach_library(int(row["offering_id"]), self.library_id)

    # helpers -------------------------------------------------------------
    def _search_rank(self) -> list[dict[str, Any]]:
        res = self.client.get(
            f"/api/staff/class/{self.class_id}/module-banks/M1/mc-search", query_string={"type": "rank"}
        )
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        return res.get_json()["items"]

    def _opinion_row(self) -> int:
        """A staff rank row with no answer order, in the M1 live bank."""
        res = self.client.post(
            f"/api/staff/class/{self.class_id}/live-bank/questions",
            json={"bank_scope": "M1", "type": "rank", "stem_text": "Rank by how fun", "options": ["Cards", "Chess", "Tag"]},
        )
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        return int(res.get_json()["question"]["id"])

    def _import(self, question_id: int, page: int) -> tuple[dict[str, Any], dict[str, Any]]:
        res = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/import-mc",
            json={"question_id": question_id, "page_number": page, "stage": "round"},
        )
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        placed = res.get_json()["placement"]["item"]
        self.school.ensure_live_session_items(self.session_id)
        live = next(
            r
            for r in self.school.list_live_session_items(self.session_id)
            if r["placement_key"] == placed["placement_key"]
        )
        return placed, live

    def _keyed_id(self) -> int:
        return int(next(r for r in self._search_rank() if r.get("rank_key"))["question_id"])

    def _publish(self, live: dict[str, Any], mode: str) -> dict[str, Any]:
        res = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{live['id']}/publish", json={"publish_mode": mode}
        )
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        return res.get_json()["item"]

    # S2 ------------------------------------------------------------------
    def test_answer_order_import_presets_group_take_turns(self) -> None:
        """The card starts on Group · take turns and plays the turn flow."""
        qid = self._keyed_id()
        placed, live = self._import(qid, 4)
        self.assertEqual(placed["group_rank_mode"], "turns")
        self.assertEqual(placed["import_group_preset"], "rank_turns")
        self.assertEqual((live["response_mode"], live["publish_mode"]), ("group_submit", "group_submit"))
        row = self.school.get_live_session_item(self.session_id, int(live["id"]))
        self.assertEqual(self.school._group_rank_mode(row), "turns")
        self.assertTrue(row["item"]["rank_key"])
        # A deck refresh keeps it.
        self.school.ensure_live_session_items(self.session_id)
        again = self.school.get_live_session_item(self.session_id, int(live["id"]))
        self.assertEqual((again["response_mode"], again["item"]["group_rank_mode"]), ("group_submit", "turns"))
        item = self._publish(live, "group_submit")
        card = self._card("Ava", item)
        self.assertEqual(card["rank_mode"], "turns")
        o = self._opts(item)
        self.assertEqual(self._post("Ava", item, "rank-turn", {"option_id": o[0]}).status_code, 200)
        # Take turns is stored per card, never on the bank row.
        stored = json.loads(
            self.school.conn.execute("SELECT payload_json FROM questions WHERE id = ?", (qid,)).fetchone()[0]
        )
        self.assertNotIn("group_rank_mode", stored)
        self.assertNotIn("import_group_preset", stored)

    def test_teacher_can_change_the_preset_before_publish(self) -> None:
        """Rank together, or Individual, sticks through a deck refresh and publishes."""
        _placed, live = self._import(self._keyed_id(), 4)
        res = self.client.patch(
            f"/api/live-sessions/{self.session_id}/items/{live['id']}/settings",
            json={"group_rank_mode": "together"},
        )
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        self.school.ensure_live_session_items(self.session_id)
        row = self.school.get_live_session_item(self.session_id, int(live["id"]))
        self.assertEqual(self.school._group_rank_mode(row), "together")
        item = self._publish(live, "group_submit")
        self.assertEqual(self._card("Ava", item)["rank_mode"], "together")
        self.assertEqual(self._post("Ava", item, "group-draft", {"tap": self._opts(item)[0]}).status_code, 200)

        # A second keyed card switched to Individual publishes individually.
        keyed = [r for r in self._search_rank() if r.get("rank_key")]
        _p2, live2 = self._import(int(keyed[1]["question_id"]), 5)
        res = self.client.patch(
            f"/api/live-sessions/{self.session_id}/items/{live2['id']}/settings",
            json={"response_mode": "individual", "publish_mode": "individual"},
        )
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        self.school.ensure_live_session_items(self.session_id)
        row2 = self.school.get_live_session_item(self.session_id, int(live2["id"]))
        self.assertEqual((row2["response_mode"], row2["publish_mode"]), ("individual", "individual"))
        item2 = self._publish(live2, "individual")
        self.assertEqual(item2["response_mode"], "individual")
        # Locked once published, as before.
        res = self.client.patch(
            f"/api/live-sessions/{self.session_id}/items/{item['id']}/settings",
            json={"group_rank_mode": "turns"},
        )
        self.assertEqual(res.status_code, 400)

    def test_opinion_import_keeps_todays_default(self) -> None:
        """No answer order: Individual, Rank together when switched to Group."""
        placed, live = self._import(self._opinion_row(), 4)
        self.assertNotIn("group_rank_mode", placed)
        self.assertNotIn("import_group_preset", placed)
        self.assertEqual((live["response_mode"], live["publish_mode"]), ("individual", "individual"))
        item = self._publish(live, "group_submit")
        self.assertEqual(self._card("Ava", item)["rank_mode"], "together")

    def test_reimport_adds_no_bank_rows_and_keeps_the_preset(self) -> None:
        """Importing the same item again is fine: no new bank rows, card still preset."""
        qid = self._keyed_id()
        before = self.school.conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        _a, first = self._import(qid, 4)
        res = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/import-mc",
            json={"question_id": qid, "page_number": 5, "stage": "round"},
        )
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        self.assertEqual(res.get_json()["placement"]["item"]["group_rank_mode"], "turns")
        self.school.ensure_live_session_items(self.session_id)
        row = self.school.get_live_session_item(self.session_id, int(first["id"]))
        self.assertEqual((row["response_mode"], row["item"]["group_rank_mode"]), ("group_submit", "turns"))
        self.assertEqual(self.school.conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0], before)

    def _student_texts(self) -> dict[str, str]:
        """Every student-facing payload for the live session, as text."""
        out: dict[str, str] = {}
        for name in ("Ava", "Ben"):
            client = self.students[name]
            out[f"{name} state"] = client.get("/api/student/state").get_data(as_text=True)
            out[f"{name} live-prompt"] = client.get("/api/student/live-prompt").get_data(as_text=True)
            out[f"{name} page"] = client.get("/student").get_data(as_text=True)
        guest = self.school.assemble_student_live_payload(
            self.session_id, self.class_id, None, participant_uuid="guest-zed", codename="Zed", unmatched=True
        )
        out["guest state"] = json.dumps(guest, default=str)
        metadata = self.school.student_live_class_metadata_for_session(self.session_id)
        out["student metadata"] = json.dumps(metadata, default=str)
        items = self.school.student_live_items_payload(self.session_id, self.ids["Ava"])
        out["student items"] = json.dumps(items, default=str)
        return out

    def _assert_no_preset_marker(self, texts: dict[str, str]) -> None:
        for where, text in texts.items():
            for field in ("import_group_preset", "group_rank_mode", "rank_turns", "rank_key"):
                self.assertNotIn(field, text, where)

    def test_students_never_see_the_preset_marker(self) -> None:
        """Ops LOW-1: the preset fields would reveal an answer order.

        Neither ``import_group_preset`` nor ``group_rank_mode`` reaches
        student state, live-prompt or /student, before or after publish.
        Take turns still plays: students learn it from the published group
        card (``rank_mode``), not from the preset fields.
        """
        _placed, live = self._import(self._keyed_id(), 4)
        row = self.school.get_live_session_item(self.session_id, int(live["id"]))
        self.assertEqual(row["item"]["import_group_preset"], "rank_turns")
        self._assert_no_preset_marker(self._student_texts())
        item = self._publish(live, "group_submit")
        self._assert_no_preset_marker(self._student_texts())
        card = self._card("Ava", item)
        self.assertEqual(card["rank_mode"], "turns")
        o = self._opts(item)
        # MCK-176 with MCK-177: the preset card is an Answer order rank, so
        # students also see per-item aliases, never the real option ids.
        items = self.school.student_live_items_payload(self.session_id, self.ids["Ava"])
        mine = next(
            q for q in items["active_questions"] if int(q["id"]) == int(item["id"])
        )
        shown = [row["id"] for row in mine["content"]["rank_options"]]
        self.assertEqual(len(shown), len(o))
        self.assertFalse(set(shown) & set(o), (shown, o))
        aliases = self.school.student_rank_aliases(
            self.school.get_live_session_item(self.session_id, int(item["id"]))
        )
        self.assertIsNotNone(aliases)
        self.assertEqual(sorted(aliases.back_list(shown)), sorted(o))
        texts = self._student_texts()
        for real in o:
            self.assertNotIn(f'"{real}"', texts["Ava state"], real)
        self.assertEqual(
            self._post("Ava", item, "rank-turn", {"option_id": shown[0]}).status_code, 200
        )
        self._assert_no_preset_marker(self._student_texts())

    def test_shared_strip_drops_the_preset_fields(self) -> None:
        """The shared student strip drops both fields, top level and nested."""
        from live_prompt_feedback import TEACHER_ONLY_FIELDS, strip_teacher_prompt_fields

        for field in ("import_group_preset", "group_rank_mode"):
            self.assertIn(field, TEACHER_ONLY_FIELDS)
        out = strip_teacher_prompt_fields(
            {
                "type": "rank",
                "group_rank_mode": "turns",
                "import_group_preset": "rank_turns",
                "items": [{"group_rank_mode": "turns", "import_group_preset": "rank_turns", "text": "x"}],
            }
        )
        for field in ("import_group_preset", "group_rank_mode"):
            self.assertNotIn(field, out)
            self.assertNotIn(field, out["items"][0])

    # S1 ------------------------------------------------------------------
    def test_search_rows_carry_what_the_chip_needs(self) -> None:
        """Seeded rows have rank_key (Answer order); staff opinion rows have none."""
        self._opinion_row()
        rows = self._search_rank()
        keyed = [r for r in rows if r.get("rank_key")]
        opinion = [r for r in rows if not r.get("rank_key")]
        self.assertEqual(len(keyed), 4)
        self.assertEqual([r["text"] for r in opinion], ["Rank by how fun"])
        for row in rows:
            self.assertGreaterEqual(len(row["rank_options"]), 3)
            self.assertEqual(row["options"], [o["label"] for o in row["rank_options"]])


class RankRowsClientTests(unittest.TestCase):
    """Chip, preview, sort and hint helpers, and the picker wiring."""

    @unittest.skipUnless(NODE, "node not installed")
    def test_node_cases(self) -> None:
        proc = subprocess.run(
            [NODE, str(LMS_DIR / "static" / "bank_rank_rows.test.mjs")],
            cwd=str(LMS_DIR), capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_picker_wiring(self) -> None:
        src = (LMS_DIR / "static" / "bank_mc_picker.js").read_text(encoding="utf-8")
        self.assertIn('from "/static/bank_rank_rows.js"', src)
        self.assertIn("const rows = sortRankRows(search.items, typeValue);", src)
        self.assertIn("paintList(rows, hints.noneKeyed);", src)
        self.assertIn("rankHintEl.textContent = hints.importDefault;", src)
        self.assertIn('data-bank-mc-rank-hint hidden></p>', src)
        self.assertIn("? rankMetaHtml(item, optionList.length)", src)
        self.assertIn("const preview = rank ? rankPreviewHtml(item) : \"\";", src)
        self.assertNotIn("answer order set", src)
        # The chip's help text is its tooltip and accessible name.
        meta = src.split("function rankMetaHtml(item, n) {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn('title="${escapeHtml(\n    chip.help\n  )}"', meta)
        self.assertIn("aria-label=", meta)
        # MCK-175: the hint hides with the bank list in the Contest view.
        apply = src.split("function applyKindView(kindValue) {", 1)[1].split("\n  }\n", 1)[0]
        self.assertIn("rankHintEl.hidden = true", apply)
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        for sel in (".bank-rank-chip.is-key {", ".bank-rank-chip.is-opinion {", ".bank-rank-preview {"):
            self.assertIn(sel, css)
        rule = css.split("body.staff-shell .bank-rank-preview {", 1)[1].split("}", 1)[0]
        self.assertIn("display: flex;", rule)
        # Ops LOW-2: the option text truncates; "+{k} more" stays visible.
        text_rule = css.split("body.staff-shell .bank-rank-preview-text {", 1)[1].split("}", 1)[0]
        self.assertIn("text-overflow: ellipsis;", text_rule)
        self.assertIn("min-width: 0;", text_rule)
        more_rule = css.split("body.staff-shell .bank-rank-preview-more {", 1)[1].split("}", 1)[0]
        self.assertIn("flex: none;", more_rule)
        preview_fn = src.split("function rankPreviewHtml(item) {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn('<span class="bank-rank-preview-text">', preview_fn)
        self.assertIn("</span>${more}</p>", preview_fn)


if __name__ == "__main__":
    unittest.main()

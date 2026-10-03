#!/usr/bin/env python3
"""MCK-169: bank search finds rank items and imports them as live rank prompts."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

import test_live_bank_tab as bank_base  # noqa: E402
from bank_mc_normalize import is_bank_rank_payload, normalize_bank_rank  # noqa: E402
from school_db import _now  # noqa: E402


# Shape of one reviewed MCK-167 candidate as part B would store it
# (payload + rank_key from proposed_answer_key + teacher fields).
CANDIDATE_PAYLOAD = {
    "stem_html": "<p>If x = \u22122 and y = 3, put these from least to greatest.</p>",
    "points_possible": 1.0,
    "type": "rank",
    "kind": "rank",
    "options": ["B: x\u00b2/y\u00b2", "A: y/x", "C: x\u00b2/y\u00b3"],
    "choices": ["B: x\u00b2/y\u00b2", "A: y/x", "C: x\u00b2/y\u00b3"],
    "rank_options": [
        {"id": "o1", "label": "B: x\u00b2/y\u00b2"},
        {"id": "o2", "label": "A: y/x"},
        {"id": "o3", "label": "C: x\u00b2/y\u00b3"},
    ],
    "rank_key": ["o2", "o3", "o1"],
    "teacher_key": {"simplified": {"A": "y/x = \u22123/2", "C": "4/27", "B": "4/9"}},
    "teacher_note": "SECRET-NOTE moves commute",
    "live_class": {"slot": "C3", "md_basis": "from files"},
    "bank_kind": "",
}


class NormalizeBankRankTests(unittest.TestCase):
    """The pure normalizer reads both stored rank shapes."""

    def test_add_new_shape_keeps_options_key_and_equation(self) -> None:
        """Save-to-bank payloads carry text, rank_options, rank_key, equation."""
        payload = {
            "type": "rank",
            "text": "Order the steps.",
            "rank_options": [
                {"id": "o1", "label": "Two points"},
                {"id": "o2", "label": "A graph"},
                {"id": "o3", "label": "A table"},
            ],
            "options": ["Two points", "A graph", "A table"],
            "rank_key": ["o3", "o1", "o2"],
            "equation": "y=mx+b",
            "bank_kind": "standard",
        }
        live, reason = normalize_bank_rank(
            question_id=9, bank_id=4, title="Order the steps.", payload=payload, bank_title="Staff"
        )
        self.assertIsNone(reason)
        assert live is not None
        self.assertEqual(live["type"], "rank")
        self.assertEqual(live["text"], "Order the steps.")
        self.assertEqual([r["id"] for r in live["rank_options"]], ["o1", "o2", "o3"])
        self.assertEqual(live["options"], ["Two points", "A graph", "A table"])
        self.assertEqual(live["rank_key"], ["o3", "o1", "o2"])
        self.assertEqual(live["equation"], "y=mx+b")
        self.assertEqual((live["question_id"], live["source_question_id"], live["bank_id"]), (9, 9, 4))
        self.assertNotIn("kind", live)
        self.assertNotIn("correct_answer", live)

    def test_live_bank_shape_reads_stem_html(self) -> None:
        """Live-bank rows store stem_html and labels; ids come from the labels."""
        payload = {"type": "rank", "kind": "rank", "stem_html": "<p>Rank <b>these</b></p>", "options": ["a", "b", "c"]}
        live, reason = normalize_bank_rank(question_id=1, bank_id=1, title="", payload=payload)
        assert live is not None, reason
        self.assertEqual(live["text"], "Rank these")
        self.assertEqual(len(live["rank_options"]), 3)
        self.assertNotIn("rank_key", live)

    def test_candidate_shape_keeps_teacher_fields_drops_slot(self) -> None:
        """teacher_key flattens to text, teacher_note carries; live_class is ignored on purpose."""
        live, reason = normalize_bank_rank(question_id=3, bank_id=2, title="If x", payload=CANDIDATE_PAYLOAD)
        assert live is not None, reason
        self.assertEqual(live["text"], "If x = \u22122 and y = 3, put these from least to greatest.")
        self.assertEqual(live["rank_key"], ["o2", "o3", "o1"])
        self.assertEqual(live["teacher_key"], "A: y/x = \u22123/2; C: 4/27; B: 4/9")
        self.assertEqual(live["teacher_note"], "SECRET-NOTE moves commute")
        self.assertNotIn("live_class", live)

    def test_choice_html_becomes_labels(self) -> None:
        """A rank payload whose items only live in ingest choices still works."""
        payload = {
            "type": "rank",
            "stem_html": "<p>Order</p>",
            "choices": [{"id": "A", "html": "<p>Common</p>"}, {"id": "B", "html": "Difference"}, {"id": "C", "html": "Check"}],
        }
        live, reason = normalize_bank_rank(question_id=1, bank_id=1, title="", payload=payload)
        assert live is not None, reason
        self.assertEqual(live["options"], ["Common", "Difference", "Check"])

    def test_stale_key_dropped_and_short_lists_skipped(self) -> None:
        """A key that no longer lists every option once is dropped, not repaired."""
        rows = [{"id": "o1", "label": "a"}, {"id": "o2", "label": "b"}, {"id": "o3", "label": "c"}]
        live, _ = normalize_bank_rank(
            question_id=1, bank_id=1, title="t", payload={"type": "rank", "text": "t", "rank_options": rows, "rank_key": ["o1", "o9", "o2"]}
        )
        assert live is not None
        self.assertNotIn("rank_key", live)
        live, reason = normalize_bank_rank(
            question_id=1, bank_id=1, title="t", payload={"type": "rank", "text": "t", "options": ["a", "b"]}
        )
        self.assertIsNone(live)
        self.assertEqual(reason, "need_three_options")
        self.assertEqual(
            normalize_bank_rank(question_id=1, bank_id=1, title="t", payload={"type": "poll", "text": "t"}),
            (None, "not_rank"),
        )

    def test_only_payload_type_rank_counts(self) -> None:
        """Polls and junk are not rank."""
        self.assertTrue(is_bank_rank_payload({"type": "Rank"}))
        self.assertFalse(is_bank_rank_payload({"type": "poll"}))
        self.assertFalse(is_bank_rank_payload({"kind": "warmup"}))
        self.assertFalse(is_bank_rank_payload("rank"))


class BankRankSearchTests(unittest.TestCase):
    """Real staff routes: live-bank add, mc-search, import-mc, publish."""

    setUp = bank_base.LiveBankTabTests.setUp
    tearDown = bank_base.LiveBankTabTests.tearDown
    _login_staff = bank_base.LiveBankTabTests._login_staff

    # helpers -------------------------------------------------------------
    def _bank(self, body: dict[str, Any]) -> int:
        rv = self.client.post(f"/api/staff/class/{self.class_id}/live-bank/questions", json=body)
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        return int(rv.get_json()["question"]["id"])

    def _rank(self, stem: str, scope: str = "M1", **extra: Any) -> int:
        body = {"bank_scope": scope, "type": "rank", "stem_text": stem, "options": ["Slope", "Intercept", "Vertex", "Axis"]}
        body.update(extra)
        return self._bank(body)

    def _ids(self, result: dict[str, Any]) -> set[int]:
        return {int(row["question_id"]) for row in result["items"]}

    def _search(self, module: str, **params: str) -> dict[str, Any]:
        rv = self.client.get(f"/api/staff/class/{self.class_id}/module-banks/{module}/mc-search", query_string=params)
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        return rv.get_json()

    def _raw_question(self, item_type: str, payload_json: str, key: str) -> int:
        bank_id = int(self.school._ensure_staff_authored_bank(self.library_id))
        self.school._ensure_module_bank_link(self.library_id, 1, bank_id)
        cur = self.school.conn.execute(
            "INSERT INTO questions (bank_id, import_key, item_type, title, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (bank_id, key, item_type, key, payload_json, _now()),
        )
        self.school.conn.commit()
        return int(cur.lastrowid)

    # tests ---------------------------------------------------------------
    def test_rank_found_by_module_and_course_search_polls_stay_out(self) -> None:
        """M1 and course-wide search list rank rows; staff polls stay out."""
        rank_m1 = self._rank("Order the features of a parabola", rank_key=[3, 2, 1, 0])
        rank_m3 = self._rank("Order the transformations", scope="M3")
        poll = self._bank({"bank_scope": "M1", "type": "poll", "stem_text": "Which feature is hardest?"})
        m1 = self._search("M1")
        self.assertIn(rank_m1, self._ids(m1))
        self.assertNotIn(poll, self._ids(m1))
        row = next(r for r in m1["items"] if int(r["question_id"]) == rank_m1)
        self.assertEqual(row["type"], "rank")
        self.assertEqual(row["text"], "Order the features of a parabola")
        self.assertEqual(row["options"], ["Slope", "Intercept", "Vertex", "Axis"])
        self.assertEqual(row["rank_key"], ["o4", "o3", "o2", "o1"])
        self.assertEqual(len(row["rank_options"]), 4)
        self.assertEqual(row["question_type"], "rank")
        # Staff-authored rows share one bank, so module scope is per bank
        # (same as MC today): the M3 rank shows wherever that bank is linked.
        m3 = self.school.search_module_bank_mcs(self.library_id, 3, "")
        self.assertIn(rank_m3, self._ids(m3))
        course = self._search("course")
        self.assertTrue({rank_m1, rank_m3} <= self._ids(course))
        self.assertNotIn(poll, self._ids(course))
        scope = self.school.search_bank_scope_mcs(self.library_id, "course", 1, "")
        self.assertTrue({rank_m1, rank_m3} <= self._ids(scope))

    def test_kind_and_text_search(self) -> None:
        """Custom-tagged rank shows only under Custom; stem and labels match q."""
        core = self._rank("Order the parabola features")
        custom = self._rank("Order the custom steps", bank_kind="standard")
        warm = self._rank("Order your favourite snacks", scope="course", bank_kind="warmup")
        default = self._ids(self._search("M1"))
        self.assertIn(core, default)
        self.assertNotIn(custom, default)
        self.assertNotIn(warm, default)
        standard = self._ids(self._search("M1", kind="standard"))
        self.assertIn(custom, standard)
        self.assertNotIn(core, standard)
        warmups = self._ids(self._search("course", kind="warmup"))
        self.assertIn(warm, warmups)
        self.assertNotIn(core, warmups)
        hit = self._search("M1", q="parabola")
        self.assertEqual(self._ids(hit) & {core, custom}, {core})
        by_label = self._ids(self._search("M1", q="intercept"))
        self.assertIn(core, by_label)
        self.assertEqual(self._ids(self._search("M1", q="zzznomatch")) & {core}, set())

    def test_import_keeps_key_and_publishes_a_live_rank_prompt(self) -> None:
        """import-mc of a bank rank item gives a rank prompt with its key kept."""
        qid = self._rank("Order the steps to graph a line", rank_key=["o2", "o1", "o4", "o3"])
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/import-mc",
            json={"question_id": qid, "page_number": 4, "stage": "round"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        item = rv.get_json()["placement"]["item"]
        self.assertEqual(item["id"], f"bank-import-{qid}")
        self.assertEqual(item["type"], "rank")
        self.assertEqual(item["rank_key"], ["o2", "o1", "o4", "o3"])
        self.assertEqual([r["label"] for r in item["rank_options"]], ["Slope", "Intercept", "Vertex", "Axis"])
        self.assertEqual(item["import_source"], "module_bank")
        self.assertFalse(self.school._bank_item_needs_rehydrate(item))

        teacher = self.school.get_user_by_email("teacher@gmail.com")
        live = self.school.start_live_class_session(self.class_id, int(teacher["id"]))
        session_id = int(live["id"])
        self.client.post(
            f"/api/live-sessions/{session_id}/teacher-state",
            json={"live_module": "M1", "live_slot": "C2", "stage": "round", "page_id": "round_1"},
        )
        self.school.ensure_live_session_items(session_id)
        row = next(r for r in self.school.list_live_session_items(session_id) if r["item_id"] == item["id"])
        rv = self.client.post(
            f"/api/live-sessions/{session_id}/items/{row['id']}/publish",
            json={"publish_mode": "individual"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        published = self.school.get_live_session_item(session_id, int(row["id"]))
        self.assertEqual(published["item"]["rank_key"], ["o2", "o1", "o4", "o3"])
        prompt = self.school._prompt_for_live_item(published)
        assert prompt is not None
        self.assertEqual(prompt["kind"], "rank")
        payload = prompt["payload"]
        self.assertEqual(payload["rank_key"], ["o2", "o1", "o4", "o3"])
        self.assertEqual(len(payload["rank_options"]), 4)
        guest = self.school.assemble_student_live_payload(
            session_id, self.class_id, None, participant_uuid="guest-zed", codename="Zed", unmatched=True
        )
        text = json.dumps(guest, default=str)
        self.assertIn("Order the steps to graph a line", text)
        self.assertNotIn("rank_key", text)

    def test_stale_key_dropped_on_search_and_import(self) -> None:
        """A stored key that no longer fits the options is not carried forward."""
        payload = {
            "type": "rank",
            "text": "Stale ranking",
            "rank_options": [{"id": "o1", "label": "a"}, {"id": "o2", "label": "b"}, {"id": "o3", "label": "c"}],
            "rank_key": ["o1", "o2"],
        }
        qid = self._raw_question("essay_question", json.dumps(payload), "rank-stale")
        row = next(r for r in self._search("M1")["items"] if int(r["question_id"]) == qid)
        self.assertNotIn("rank_key", row)
        placement = self.school.import_mc_to_class_playlist(
            self.class_id, "M1", "C1", qid, library_id=self.library_id, page_number=1
        )
        self.assertEqual(placement["item"]["type"], "rank")
        self.assertNotIn("rank_key", placement["item"])

    def test_candidate_import_hides_teacher_fields_from_students(self) -> None:
        """A stored candidate imports with teacher_key / teacher_note for staff only."""
        qid = self._raw_question("essay_question", json.dumps(CANDIDATE_PAYLOAD), "rank:MCR3U:M3:candidate")
        row = next(r for r in self._search("M1", q="least to greatest")["items"] if int(r["question_id"]) == qid)
        self.assertEqual(row["question_type"], "rank")
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C3/import-mc",
            json={"question_id": qid, "page_number": 1, "stage": "round"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        item = rv.get_json()["placement"]["item"]
        self.assertEqual(item["rank_key"], ["o2", "o3", "o1"])
        self.assertIn("4/27", item["teacher_key"])
        self.assertEqual(item["teacher_note"], "SECRET-NOTE moves commute")
        teacher = self.school.get_user_by_email("teacher@gmail.com")
        session_id = int(self.school.start_live_class_session(self.class_id, int(teacher["id"]))["id"])
        self.client.post(
            f"/api/live-sessions/{session_id}/teacher-state",
            json={"live_module": "M1", "live_slot": "C3", "stage": "round", "page_id": "round_1"},
        )
        self.school.ensure_live_session_items(session_id)
        live_row = next(r for r in self.school.list_live_session_items(session_id) if r["item_id"] == item["id"])
        rv = self.client.post(
            f"/api/live-sessions/{session_id}/items/{live_row['id']}/publish",
            json={"publish_mode": "individual"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        guest = json.dumps(
            self.school.assemble_student_live_payload(
                session_id, self.class_id, None, participant_uuid="guest-zed", codename="Zed", unmatched=True
            ),
            default=str,
        )
        self.assertIn("least to greatest", guest)
        for secret in ("rank_key", "teacher_key", "4/27", "SECRET-NOTE"):
            self.assertNotIn(secret, guest)

    def test_bad_rows_never_break_search(self) -> None:
        """Malformed essay JSON and two-option ranks are skipped quietly."""
        self._raw_question("essay_question", "not json {", "essay-bad")
        short = self._raw_question("essay_question", json.dumps({"type": "rank", "text": "x", "options": ["a", "b"]}), "rank-short")
        numeric = self._bank({"bank_scope": "M1", "type": "numeric", "stem_text": "2+2", "correct_answer": "4"})
        ids = self._ids(self._search("M1"))
        self.assertNotIn(short, ids)
        self.assertNotIn(numeric, ids)
        with self.assertRaises(ValueError):
            self.school.import_mc_to_class_playlist(
                self.class_id, "M1", "C1", short, library_id=self.library_id, page_number=1
            )

    def test_default_results_otherwise_unchanged(self) -> None:
        """Adding rank rows only adds rank rows; every other result is identical."""
        mc = self._bank({"bank_scope": "M1", "type": "mc", "stem_text": "Slope of y=2x?", "options": ["2", "1", "0", "-2"], "correct_answer": "A"})

        def snap() -> dict[str, list[dict[str, Any]]]:
            out = {}
            for kind in ("", "standard", "contest", "warmup"):
                for scope in ("M1", "course"):
                    rows = self.school.search_bank_scope_mcs(self.library_id, scope, 1, "", kind=kind)["items"]
                    out[f"{scope}:{kind}"] = [r for r in rows if r.get("type") != "rank"]
            return out

        before = snap()
        self.assertIn(mc, {int(r["question_id"]) for r in before["M1:"]})
        self._rank("Order these A")
        self._rank("Order these B", scope="course", bank_kind="warmup")
        self._rank("Order these C", bank_kind="standard")
        self.assertEqual(json.dumps(snap(), sort_keys=True, default=str), json.dumps(before, sort_keys=True, default=str))


    def test_ui_labels_rank_rows(self) -> None:
        """Import picker shows a Rank meta; the banks tab keeps rank rows read-only."""
        picker = (LMS_DIR / "static" / "bank_mc_picker.js").read_text(encoding="utf-8")
        # MCK-169 S1: the meta carries an Answer order / Opinion chip.
        self.assertIn("rankMetaHtml(item, optionList.length)", picker)
        banks = (LMS_DIR / "static" / "course_question_banks.js").read_text(encoding="utf-8")
        self.assertIn("function rankPeekHtml", banks)
        self.assertIn("editMode && isRank(selected)", banks)


if __name__ == "__main__":
    unittest.main()

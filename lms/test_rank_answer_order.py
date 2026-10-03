#!/usr/bin/env python3
"""MCK-172 (rank race R1): optional answer order on rank items.

``item_json.rank_key`` is authored in Add New and the live bank, validated
as a permutation of the options, kept on bank rows, never sent to students,
and ignored by the Borda class order. ``rank_race_score`` scores an order
spot by spot.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))

import test_group_mc_pick_turns_mck155 as turns_base  # noqa: E402
from live_prompt_feedback import strip_teacher_prompt_fields  # noqa: E402
from live_rank import (  # noqa: E402
    borda_class_order,
    build_rank_options,
    build_rank_tally,
    parse_rank_key,
    rank_race_score,
    safe_rank_key,
)

OPTIONS = build_rank_options(["Two points", "A graph", "An equation", "A table"])


class RankRaceScoreTests(unittest.TestCase):
    """Pure spot-by-spot scoring."""

    KEY = ["o3", "o1", "o4", "o2"]

    def test_full_and_none_right(self) -> None:
        """Exact order is all right; a rotation is none right."""
        self.assertEqual(
            rank_race_score(self.KEY, self.KEY),
            {"right": 4, "total": 4, "spots": [True, True, True, True]},
        )
        self.assertEqual(
            rank_race_score(["o1", "o4", "o2", "o3"], self.KEY),
            {"right": 0, "total": 4, "spots": [False, False, False, False]},
        )

    def test_right_only_on_exact_position(self) -> None:
        """A swapped pair loses both spots; the rest still count."""
        self.assertEqual(
            rank_race_score(["o3", "o4", "o1", "o2"], self.KEY),
            {"right": 2, "total": 4, "spots": [True, False, False, True]},
        )

    def test_partial_unsent_draft_with_empty_spots(self) -> None:
        """A draft scores its filled spots; holes and missing spots are not right."""
        self.assertEqual(
            rank_race_score(["o3", None, "o4"], self.KEY),
            {"right": 2, "total": 4, "spots": [True, False, True, False]},
        )
        self.assertEqual(
            rank_race_score(["o3", "", "", ""], self.KEY),
            {"right": 1, "total": 4, "spots": [True, False, False, False]},
        )
        self.assertEqual(
            rank_race_score([], self.KEY),
            {"right": 0, "total": 4, "spots": [False] * 4},
        )

    def test_bad_inputs_never_raise(self) -> None:
        """No key scores nothing; junk order reads as empty; extras ignored."""
        self.assertEqual(rank_race_score(self.KEY, None), {"right": 0, "total": 0, "spots": []})
        self.assertEqual(rank_race_score(self.KEY, []), {"right": 0, "total": 0, "spots": []})
        self.assertEqual(rank_race_score("o3", self.KEY)["right"], 0)
        self.assertEqual(rank_race_score([*self.KEY, "o9"], self.KEY)["right"], 4)


class RankKeyParseTests(unittest.TestCase):
    """Authored answer order must be a permutation of the options."""

    def test_ids_and_indices(self) -> None:
        """Option ids, int indices and digit strings all store as ids."""
        self.assertEqual(parse_rank_key(["o2", "o1", "o4", "o3"], OPTIONS), ["o2", "o1", "o4", "o3"])
        self.assertEqual(parse_rank_key([1, 0, 3, 2], OPTIONS), ["o2", "o1", "o4", "o3"])
        self.assertEqual(parse_rank_key(["3", "2", "1", "0"], OPTIONS), ["o4", "o3", "o2", "o1"])

    def test_blank_means_no_key(self) -> None:
        """Leaving it blank keeps today's behaviour."""
        for raw in (None, "", []):
            self.assertIsNone(parse_rank_key(raw, OPTIONS))

    def test_rejects_non_permutations(self) -> None:
        """Short, duplicated, unknown, out-of-range or non-list orders fail."""
        for raw in (
            ["o1", "o2", "o3"],
            ["o1", "o1", "o2", "o3"],
            ["o1", "o2", "o3", "o9"],
            [0, 1, 2, 4],
            "o1,o2,o3,o4",
            {"o1": 1},
        ):
            with self.assertRaises(ValueError, msg=repr(raw)):
                parse_rank_key(raw, OPTIONS)

    def test_safe_read_drops_a_stale_key(self) -> None:
        """A key left over from different options reads as none."""
        self.assertIsNone(safe_rank_key(["o1", "o2", "o3"], OPTIONS))
        self.assertEqual(safe_rank_key(["o4", "o3", "o2", "o1"], OPTIONS), ["o4", "o3", "o2", "o1"])

    def test_teacher_only_field(self) -> None:
        """The shared student strip drops rank_key, top level and nested."""
        out = strip_teacher_prompt_fields(
            {"type": "rank", "rank_key": ["o1"], "items": [{"rank_key": ["o2"], "text": "x"}]}
        )
        self.assertNotIn("rank_key", out)
        self.assertNotIn("rank_key", out["items"][0])

    def test_borda_ignores_the_key(self) -> None:
        """Class order is identical with or without an answer order."""
        votes = [
            {"response": {"order": ["o2", "o1", "o3", "o4"]}},
            {"response": {"order": ["o2", "o3", "o1", "o4"]}},
            {"response": {"order": ["o4", "o2", "o1", "o3"]}},
        ]
        base = {"id": 7, "kind": "rank", "payload": {"type": "rank", "rank_options": OPTIONS}}
        keyed = json.loads(json.dumps(base))
        keyed["payload"]["rank_key"] = ["o1", "o2", "o3", "o4"]
        self.assertEqual(
            build_rank_tally(base, responses=votes, present=3),
            build_rank_tally(keyed, responses=votes, present=3),
        )
        self.assertEqual(
            borda_class_order([v["response"]["order"] for v in votes], OPTIONS)[0]["option_id"], "o2"
        )


class RankKeyLiveTests(unittest.TestCase):
    """Real teacher and student routes on MCR3U M1 C2 (two teams of two)."""

    setUp = turns_base.PickThenAgreeAndTurnsTests.setUp
    tearDown = turns_base.PickThenAgreeAndTurnsTests.tearDown
    _add = turns_base.PickThenAgreeAndTurnsTests._add
    _publish = turns_base.PickThenAgreeAndTurnsTests._publish
    _post = turns_base.PickThenAgreeAndTurnsTests._post
    _view = turns_base.PickThenAgreeAndTurnsTests._view

    BODY = {"type": "rank", "text": "Order the steps.", "options": ["Two points", "A graph", "An equation", "A table"]}

    def _keyed_row(self, mode: str = "together") -> dict[str, Any]:
        row = self._add({**self.BODY, "rank_key": [2, 0, 3, 1]})
        if mode == "turns":
            rv = self.client.patch(
                f"/api/live-sessions/{self.session_id}/items/{row['id']}/settings",
                json={"group_rank_mode": "turns"},
            )
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        return row

    def _ids(self, row: dict[str, Any]) -> list[str]:
        """Real option ids in typed order (MCK-176: minted, not o1…).

        The live row lists the shown (shuffled) order, so map by label.
        """
        by_label = {opt["label"]: opt["id"] for opt in row["item"]["rank_options"]}
        return [by_label[label] for label in self.BODY["options"]]

    def _key(self, row: dict[str, Any]) -> list[str]:
        ids = self._ids(row)
        return [ids[2], ids[0], ids[3], ids[1]]

    def _student_texts(self, item: dict[str, Any]) -> dict[str, str]:
        """Every student-facing response for a live rank item, as text."""
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

    def _assert_no_key(self, texts: dict[str, str]) -> None:
        for where, text in texts.items():
            self.assertNotIn("rank_key", text, where)

    def test_add_new_stores_the_key_as_option_ids(self) -> None:
        """Indices from Add New store as ids; blank keeps no key; bad order is 400."""
        row = self._keyed_row()
        self.assertEqual(row["item"]["rank_key"], self._key(row))
        plain = self._add(dict(self.BODY))
        self.assertNotIn("rank_key", plain["item"])
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/add-question",
            json={"page_number": 4, "stage": "round", **self.BODY, "rank_key": [0, 0, 1, 2]},
        )
        self.assertEqual(rv.status_code, 400)
        self.assertEqual(rv.get_json()["error"], "answer order must list every option once")

    def test_save_to_bank_and_live_bank_keep_the_key(self) -> None:
        """Bank rank rows carry rank_key (what MCK-169 will read)."""
        library = self.school.create_library("MCR3U", origin="upload")
        offering = self.school.conn.execute(
            "SELECT offering_id FROM classes WHERE id = ?", (self.class_id,)
        ).fetchone()
        self.school.attach_library(int(offering["offering_id"]), int(library["id"]))
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/add-question",
            json={"page_number": 4, "stage": "round", **self.BODY, "rank_key": ["o4", "o3", "o2", "o1"], "save_to_bank": True},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        placed = rv.get_json()["placement"]["item"]
        qid = int(placed["source_question_id"])
        stored = json.loads(
            self.school.conn.execute("SELECT payload_json FROM questions WHERE id = ?", (qid,)).fetchone()[0]
        )
        # MCK-176: a key sent as o1… still maps by position onto minted ids.
        self.assertEqual(
            stored["rank_key"], list(reversed([opt["id"] for opt in placed["rank_options"]]))
        )
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-bank/questions",
            json={"bank_scope": "M1", "type": "rank", "stem_text": "Bank rank", "options": ["a", "b", "c"], "rank_key": [2, 1, 0]},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        bank_payload = rv.get_json()["question"]["payload"]
        self.assertEqual(
            bank_payload["rank_key"], list(reversed([opt["id"] for opt in bank_payload["rank_options"]]))
        )
        bad = self.client.post(
            f"/api/staff/class/{self.class_id}/live-bank/questions",
            json={"bank_scope": "M1", "type": "rank", "stem_text": "Bad", "options": ["a", "b", "c"], "rank_key": [2, 1]},
        )
        self.assertEqual(bad.status_code, 400)

    def test_students_never_see_the_key_rank_together(self) -> None:
        """Group rank (together): state, prompt, page, draft, send, metadata."""
        row = self._keyed_row()
        item = self._publish(row)
        staff = self.school.get_live_session_item(self.session_id, int(item["id"]))
        self.assertEqual(staff["item"]["rank_key"], self._key(row))
        ids = self._ids(row)
        texts = self._student_texts(item)
        self.assertIn("Order the steps.", texts["Ava state"])
        for opt in ids[:2]:
            rv = self._post("Ava", item, "group-draft", {"tap": opt})
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
            texts[f"draft {opt}"] = rv.get_data(as_text=True)
        rv = self._post("Ava", item, "group-submit", {"order": ids})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        texts["submit"] = rv.get_data(as_text=True)
        texts.update({f"after {k}": v for k, v in self._student_texts(item).items()})
        self._assert_no_key(texts)

    def test_students_never_see_the_key_take_turns(self) -> None:
        """Take turns placements never echo the key."""
        row = self._keyed_row("turns")
        item = self._publish(row)
        texts = self._student_texts(item)
        rv = self._post("Ava", item, "rank-turn", {"option_id": self._ids(row)[2]})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        texts["turn"] = rv.get_data(as_text=True)
        texts.update({f"after {k}": v for k, v in self._student_texts(item).items()})
        self._assert_no_key(texts)

    def test_students_never_see_the_key_individual(self) -> None:
        """Individual rank: prompt and submit response stay keyless."""
        row = self._keyed_row()
        rv = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{row['id']}/publish",
            json={"publish_mode": "individual"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        item = rv.get_json()["item"]
        texts = self._student_texts(item)
        prompt = self.school._prompt_for_live_item(self.school.get_live_session_item(self.session_id, int(item["id"])))
        self.assertEqual(prompt["payload"].get("rank_key"), self._key(row))
        ids = self._ids(row)
        rv = self.students["Ava"].post(
            "/api/student/live-prompt/response",
            json={"prompt_id": prompt["id"], "response": {"order": [ids[2], ids[0], ids[1], ids[3]]}},
        )
        texts["response"] = rv.get_data(as_text=True)
        self._assert_no_key(texts)

    def test_borda_results_unchanged_with_a_key(self) -> None:
        """Same team orders give the same class order, keyed or not."""
        orders = ([1, 0, 2, 3], [1, 2, 3, 0])
        results = []
        for row in (self._add(dict(self.BODY)), self._keyed_row()):
            item = self._publish(row)
            ids = self._ids(row)
            labels = {opt["id"]: opt["label"] for opt in row["item"]["rank_options"]}
            for name, order in zip(("Ava", "Ben"), orders):
                rv = self._post(name, item, "group-submit", {"order": [ids[i] for i in order]})
                self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
            rank = self._view(item)["rank"]
            results.append([(labels[r["option_id"]], r["points"], r["rank"]) for r in rank["class_order"]])
            self.client.post(f"/api/live-sessions/{self.session_id}/items/{item['id']}/close")
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0][0][0], "A graph")


class AnswerOrderAuthorUiTests(unittest.TestCase):
    """Add New: optional "Answer order" drag list under the rank options."""

    def test_author_ui_wiring(self) -> None:
        course = (LMS_DIR / "templates" / "staff" / "course.html").read_text(encoding="utf-8")
        staff = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn('id="live-add-q-rank-key-on"> Answer order</label>', course)
        self.assertIn(">Drag the items into the right order.</p>", course)
        self.assertNotIn("no answer key</p>", course)
        submit = staff.split("async function submitAddQuestion()")[1].split("\n}\n")[0]
        self.assertIn("rankKeyForSubmit(body.options)", submit)
        self.assertIn("if (rankKey) body.rank_key = rankKey;", submit)
        unset = turns_base._function_source(staff, "rankKeyForSubmit")
        self.assertIn("!on.checked) return null", unset)


if __name__ == "__main__":
    unittest.main()

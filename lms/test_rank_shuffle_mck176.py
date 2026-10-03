#!/usr/bin/env python3
"""MCK-176: answer-order ranks are never shown in the key's order.

Pure shuffle rules, then real teacher and student routes on MCR3U M1 C2
(two teams of two: Ava + Cy, Ben + Dee), borrowed from the MCK-155 suite.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

import test_group_mc_pick_turns_mck155 as turns_base  # noqa: E402
from live_rank import (  # noqa: E402
    RANK_DISPLAY_ORDER_FIELD,
    apply_rank_display_order,
    rank_race_score,
    shuffled_rank_order,
    valid_display_order,
    with_rank_display_order,
)

KEY = ["o3", "o1", "o4", "o2"]


class ShuffleRuleTests(unittest.TestCase):
    """Pure helpers in live_rank."""

    def test_never_the_key_over_many_seeds(self) -> None:
        for n in range(2, 7):
            ids = [f"o{i}" for i in range(1, n + 1)]
            keys = [ids, list(reversed(ids)), ids[1:] + ids[:1]]
            for key in keys:
                for seed in range(400):
                    order = shuffled_rank_order(ids, key, seed)
                    self.assertEqual(sorted(order), sorted(ids))
                    self.assertNotEqual(order, key, (n, key, seed))

    def test_two_items_always_swap(self) -> None:
        for seed in range(200):
            self.assertEqual(shuffled_rank_order(["a", "b"], ["a", "b"], seed), ["b", "a"])
            self.assertEqual(shuffled_rank_order(["a", "b"], ["b", "a"], f"s{seed}"), ["a", "b"])

    def test_one_item_and_same_seed(self) -> None:
        self.assertEqual(shuffled_rank_order(["a"], ["a"], 1), ["a"])
        ids = ["o1", "o2", "o3", "o4", "o5"]
        self.assertEqual(shuffled_rank_order(ids, ids, "x"), shuffled_rank_order(ids, ids, "x"))

    def test_stored_order_must_be_current_and_not_the_key(self) -> None:
        ids = ["o1", "o2", "o3", "o4"]
        self.assertEqual(valid_display_order(["o2", "o1", "o3", "o4"], ids, KEY), ["o2", "o1", "o3", "o4"])
        self.assertIsNone(valid_display_order(KEY, ids, KEY))
        self.assertIsNone(valid_display_order(["o1", "o2", "o3"], ids, KEY))
        self.assertIsNone(valid_display_order("o1", ids, KEY))

    def test_ids_move_with_labels(self) -> None:
        payload = {
            "type": "rank",
            "options": ["A", "B", "C", "D"],
            "rank_options": [{"id": f"o{i}", "label": x} for i, x in enumerate("ABCD", 1)],
            "rank_key": KEY,
        }
        out = apply_rank_display_order(payload, ["o2", "o4", "o1", "o3"])
        self.assertEqual([r["id"] for r in out["rank_options"]], ["o2", "o4", "o1", "o3"])
        self.assertEqual(out["options"], ["B", "D", "A", "C"])
        self.assertEqual(out[RANK_DISPLAY_ORDER_FIELD], ["o2", "o4", "o1", "o3"])
        self.assertEqual(payload["options"], ["A", "B", "C", "D"])  # copy, not in place

    def test_opinion_payload_is_the_same_object(self) -> None:
        opinion = {"type": "rank", "options": ["A", "B", "C"], "rank_options": [{"id": "o1", "label": "A"}]}
        self.assertIs(with_rank_display_order(opinion, "seed"), opinion)
        mc = {"type": "mc", "options": ["A", "B"], "rank_key": ["o1", "o2"]}
        self.assertIs(with_rank_display_order(mc, "seed"), mc)


def _rank_lists(node: Any, stem: str, out: list[list[str]]) -> list[list[str]]:
    """Every ``rank_options`` id list on a dict whose text is ``stem``."""
    if isinstance(node, dict):
        text = str(node.get("text") or node.get("question") or node.get("prompt") or "")
        rows = node.get("rank_options")
        if stem in text and isinstance(rows, list) and rows:
            out.append([str(r.get("id")) for r in rows if isinstance(r, dict)])
        for value in node.values():
            _rank_lists(value, stem, out)
    elif isinstance(node, list):
        for value in node:
            _rank_lists(value, stem, out)
    return out


class RankShuffleLiveTests(unittest.TestCase):
    """Real routes: shuffled, stable, shared, scored by id, key never sent."""

    setUp = turns_base.PickThenAgreeAndTurnsTests.setUp
    tearDown = turns_base.PickThenAgreeAndTurnsTests.tearDown
    _add = turns_base.PickThenAgreeAndTurnsTests._add
    _publish = turns_base.PickThenAgreeAndTurnsTests._publish
    _post = turns_base.PickThenAgreeAndTurnsTests._post
    _view = turns_base.PickThenAgreeAndTurnsTests._view
    _card = turns_base.PickThenAgreeAndTurnsTests._card

    STEM = "Order the steps."
    BODY = {"type": "rank", "text": STEM, "options": ["Two points", "A graph", "An equation", "A table"]}

    def _keyed(self, mode: str = "together") -> dict[str, Any]:
        row = self._add({**self.BODY, "rank_key": [2, 0, 3, 1]})
        self.assertEqual(row["item"]["rank_key"], KEY)
        if mode == "turns":
            rv = self.client.patch(
                f"/api/live-sessions/{self.session_id}/items/{row['id']}/settings",
                json={"group_rank_mode": "turns"},
            )
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        return row

    def _stored(self, item: dict[str, Any]) -> list[str]:
        live = self.school.get_live_session_item(self.session_id, int(item["id"]))
        order = self.school.rank_display_order(live)
        self.assertIsNotNone(order)
        return list(order or [])

    def _student_views(self, names: tuple[str, ...] = ("Ava", "Cy", "Ben")) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in names:
            client = self.students[name]
            out[f"{name} state"] = client.get("/api/student/state").get_json()
            out[f"{name} live-prompt"] = client.get("/api/student/live-prompt").get_json()
        out["metadata"] = self.school.student_live_class_metadata_for_session(self.session_id)
        out["items"] = self.school.student_live_items_payload(self.session_id, self.ids["Ava"])
        return out

    def _assert_all_shown(self, views: dict[str, Any], order: list[str]) -> None:
        seen = 0
        for where, body in views.items():
            for ids in _rank_lists(body, self.STEM, []):
                seen += 1
                self.assertEqual(ids, order, where)
        self.assertGreater(seen, 0)

    def test_group_card_shuffled_stable_and_shared(self) -> None:
        item = self._publish(self._keyed())
        order = self._stored(item)
        self.assertNotEqual(order, KEY)
        self.assertEqual(sorted(order), sorted(KEY))
        first = self._student_views()
        self._assert_all_shown(first, order)
        # Ava and Cy (one team) and Ben (other team) see one order.
        ava = _rank_lists(first["Ava state"], self.STEM, [])
        cy = _rank_lists(first["Cy state"], self.STEM, [])
        self.assertTrue(ava and cy)
        self.assertEqual(ava[0], cy[0])
        # Stable across reloads and a deck refresh.
        for _ in range(3):
            self.school.ensure_live_session_items(self.session_id)
            self._assert_all_shown(self._student_views(), order)
        self.assertEqual(self._stored(item), order)

    def test_teacher_card_lists_the_shown_order(self) -> None:
        row = self._keyed()
        before = self.school.get_live_session_item(self.session_id, int(row["id"]))
        self.assertNotEqual([o["id"] for o in before["item"]["rank_options"]], KEY)
        item = self._publish(row)
        live = self.school.get_live_session_item(self.session_id, int(item["id"]))
        labels = {o["id"]: o["label"] for o in live["item"]["rank_options"]}
        self.assertEqual([o["id"] for o in live["item"]["rank_options"]], self._stored(item))
        self.assertEqual(live["item"]["options"], [labels[i] for i in self._stored(item)])
        self.assertEqual(live["item"]["rank_key"], KEY)  # staff still has the key

    def test_individual_prompt_shuffled_and_scored_by_id(self) -> None:
        row = self._keyed()
        rv = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{row['id']}/publish",
            json={"publish_mode": "individual"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        item = rv.get_json()["item"]
        order = self._stored(item)
        prompt = self.school._prompt_for_live_item(self.school.get_live_session_item(self.session_id, int(item["id"])))
        self.assertEqual([o["id"] for o in prompt["payload"]["rank_options"]], order)
        self._assert_all_shown(self._student_views(), order)
        rv = self.students["Ava"].post(
            "/api/student/live-prompt/response",
            json={"prompt_id": prompt["id"], "response": {"order": KEY}},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        self.assertEqual(rank_race_score(KEY, prompt["payload"]["rank_key"])["right"], 4)

    def test_rank_together_submission_scores_against_the_key(self) -> None:
        item = self._publish(self._keyed())
        live = self.school.get_live_session_item(self.session_id, int(item["id"]))
        key = live["item"]["rank_key"]
        self.assertEqual(self._post("Ava", item, "group-submit", {"order": KEY}).status_code, 200)
        wrong = ["o1", "o2", "o3", "o4"]
        self.assertEqual(self._post("Ben", item, "group-submit", {"order": wrong}).status_code, 200)
        teams = self._view(item)["rank"]["teams"]
        orders = sorted((t["order"] for t in teams if t["status"] == "submitted"), key=lambda o: o != KEY)
        self.assertEqual(orders, [KEY, wrong])
        self.assertEqual(rank_race_score(orders[0], key), {"right": 4, "total": 4, "spots": [True] * 4})
        self.assertEqual(rank_race_score(orders[1], key)["right"], 0)

    def test_take_turns_placements_score_against_the_key(self) -> None:
        item = self._publish(self._keyed("turns"))
        order = self._stored(item)
        self._assert_all_shown(self._student_views(), order)
        for name, opt in zip(("Ava", "Cy", "Ava", "Cy"), KEY):
            rv = self._post(name, item, "rank-turn", {"option_id": opt})
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        card = self._card("Ava", item)
        self.assertTrue(card["submitted"])
        self.assertEqual(card["submitted_order"], KEY)
        live = self.school.get_live_session_item(self.session_id, int(item["id"]))
        self.assertEqual(rank_race_score(card["submitted_order"], live["item"]["rank_key"])["right"], 4)

    def test_no_key_or_order_field_reaches_students(self) -> None:
        for mode in ("together", "turns"):
            item = self._publish(self._keyed(mode))
            texts = {k: json.dumps(v, default=str) for k, v in self._student_views().items()}
            texts["page"] = self.students["Ava"].get("/student").get_data(as_text=True)
            for where, text in texts.items():
                self.assertNotIn("rank_key", text, where)
                self.assertNotIn("teacher_note", text, where)
                self.assertNotIn(RANK_DISPLAY_ORDER_FIELD, text, where)
            self.client.post(f"/api/live-sessions/{self.session_id}/items/{item['id']}/close")

    def test_republish_gets_a_fresh_shuffle(self) -> None:
        seen = set()
        for _ in range(6):
            row = self._keyed()
            item = self._publish(row)
            order = self._stored(item)
            self.assertNotEqual(order, KEY)
            seen.add(tuple(order))
            self.client.post(f"/api/live-sessions/{self.session_id}/items/{item['id']}/close")
        # Independent random seeds per publish: not one fixed order.
        self.assertGreater(len(seen), 1)

    def test_opinion_rank_unchanged(self) -> None:
        row = self._add(dict(self.BODY))
        before = self.school.get_live_session_item(self.session_id, int(row["id"]))["item"]
        item = self._publish(row)
        live = self.school.get_live_session_item(self.session_id, int(item["id"]))
        for field in ("options", "rank_options"):
            self.assertEqual(live["item"][field], before[field])  # publish only adds mode fields
        self.assertNotIn(RANK_DISPLAY_ORDER_FIELD, live["item"])
        self.assertIsNone(self.school.rank_display_order(live))
        authored = [o["id"] for o in live["item"]["rank_options"]]
        self.assertEqual(authored, ["o1", "o2", "o3", "o4"])
        self._assert_all_shown(self._student_views(), authored)
        self.assertIs(self.school._student_rank_display(live["item"]), live["item"])


if __name__ == "__main__":
    unittest.main()

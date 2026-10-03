#!/usr/bin/env python3
"""MCK-176: answer-order ranks are never shown in the key's order.

Pure shuffle rules, then real teacher and student routes on MCR3U M1 C2
(two teams of two: Ava + Cy, Ben + Dee), borrowed from the MCK-155 suite.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

import test_group_mc_pick_turns_mck155 as turns_base  # noqa: E402
from rank_alias import rank_option_alias  # noqa: E402
from live_rank import (  # noqa: E402
    RANK_DISPLAY_ORDER_FIELD,
    RANK_DISPLAY_PENDING_FIELD,
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

    def _ids(self, row: dict[str, Any]) -> list[str]:
        """Real ids in typed order (minted, MCK-176); rows list the shown order."""
        by_label = {o["label"]: o["id"] for o in row["item"]["rank_options"]}
        return [by_label[label] for label in self.BODY["options"]]

    def _key(self, row: dict[str, Any]) -> list[str]:
        ids = self._ids(row)
        return [ids[2], ids[0], ids[3], ids[1]]

    def _aliases(self, item: dict[str, Any]):
        live = self.school.get_live_session_item(self.session_id, int(item["id"]))
        aliases = self.school.student_rank_aliases(live)
        self.assertIsNotNone(aliases)
        return aliases

    def _from_student(self, item: dict[str, Any], ids: list[str]) -> list[str]:
        """Student ids back to real ids; a real id on a student screen fails."""
        aliases = self._aliases(item)
        for opt in ids:
            self.assertNotIn(opt, aliases.ids, "real option id reached a student")
        return [aliases.back(opt) for opt in ids]

    def _to_student(self, item: dict[str, Any], ids: list[str]) -> list[str]:
        aliases = self._aliases(item)
        return [aliases.out(opt) for opt in ids]

    def _keyed(self, mode: str = "together") -> dict[str, Any]:
        row = self._add({**self.BODY, "rank_key": [2, 0, 3, 1]})
        self.assertEqual(row["item"]["rank_key"], self._key(row))
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

    def _assert_all_shown(
        self, views: dict[str, Any], order: list[str], item: dict[str, Any] | None = None
    ) -> None:
        """Every student view shows ``order`` (as aliases when ``item`` is keyed)."""
        seen = 0
        for where, body in views.items():
            for ids in _rank_lists(body, self.STEM, []):
                seen += 1
                shown = self._from_student(item, ids) if item is not None else ids
                self.assertEqual(shown, order, where)
        self.assertGreater(seen, 0)

    def test_group_card_shuffled_stable_and_shared(self) -> None:
        item = self._publish(self._keyed())
        key = self._key(item)
        order = self._stored(item)
        self.assertNotEqual(order, key)
        self.assertEqual(sorted(order), sorted(key))
        first = self._student_views()
        self._assert_all_shown(first, order, item)
        # Ava and Cy (one team) and Ben (other team) see one order.
        ava = _rank_lists(first["Ava state"], self.STEM, [])
        cy = _rank_lists(first["Cy state"], self.STEM, [])
        self.assertTrue(ava and cy)
        self.assertEqual(ava[0], cy[0])
        # Stable across reloads and a deck refresh.
        for _ in range(3):
            self.school.ensure_live_session_items(self.session_id)
            self._assert_all_shown(self._student_views(), order, item)
        self.assertEqual(self._stored(item), order)

    def test_teacher_card_lists_the_shown_order(self) -> None:
        row = self._keyed()
        before = self.school.get_live_session_item(self.session_id, int(row["id"]))
        key = self._key(row)
        self.assertNotEqual([o["id"] for o in before["item"]["rank_options"]], key)
        item = self._publish(row)
        live = self.school.get_live_session_item(self.session_id, int(item["id"]))
        labels = {o["id"]: o["label"] for o in live["item"]["rank_options"]}
        self.assertEqual([o["id"] for o in live["item"]["rank_options"]], self._stored(item))
        self.assertEqual(live["item"]["options"], [labels[i] for i in self._stored(item)])
        self.assertEqual(live["item"]["rank_key"], key)  # staff still has the key

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
        self._assert_all_shown(self._student_views(), order, item)
        key = self._key(item)
        rv = self.students["Ava"].post(
            "/api/student/live-prompt/response",
            json={"prompt_id": prompt["id"], "response": {"order": self._to_student(item, key)}},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        saved = self.school.get_live_prompt_response(int(prompt["id"]), self.ids["Ava"])
        self.assertEqual(saved["response"]["order"], key)
        self.assertEqual(rank_race_score(saved["response"]["order"], prompt["payload"]["rank_key"])["right"], 4)

    def test_rank_together_submission_scores_against_the_key(self) -> None:
        item = self._publish(self._keyed())
        live = self.school.get_live_session_item(self.session_id, int(item["id"]))
        key = live["item"]["rank_key"]
        self.assertEqual(key, self._key(item))
        rv = self._post("Ava", item, "group-submit", {"order": self._to_student(item, key)})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        wrong = self._ids(item)
        rv = self._post("Ben", item, "group-submit", {"order": self._to_student(item, wrong)})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        teams = self._view(item)["rank"]["teams"]
        orders = sorted((t["order"] for t in teams if t["status"] == "submitted"), key=lambda o: o != key)
        self.assertEqual(orders, [key, wrong])
        self.assertEqual(rank_race_score(orders[0], key), {"right": 4, "total": 4, "spots": [True] * 4})
        self.assertEqual(rank_race_score(orders[1], key)["right"], 0)

    def test_take_turns_placements_score_against_the_key(self) -> None:
        item = self._publish(self._keyed("turns"))
        order = self._stored(item)
        self._assert_all_shown(self._student_views(), order, item)
        key = self._key(item)
        for name, opt in zip(("Ava", "Cy", "Ava", "Cy"), self._to_student(item, key)):
            rv = self._post(name, item, "rank-turn", {"option_id": opt})
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        card = self._card("Ava", item)
        self.assertTrue(card["submitted"])
        submitted = self._from_student(item, card["submitted_order"])
        self.assertEqual(submitted, key)
        live = self.school.get_live_session_item(self.session_id, int(item["id"]))
        self.assertEqual(rank_race_score(submitted, live["item"]["rank_key"])["right"], 4)

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
            self.assertNotEqual(order, self._key(row))
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
        self.assertEqual([o["label"] for o in live["item"]["rank_options"]], self.BODY["options"])
        self.assertIsNone(self.school.student_rank_aliases(live))
        # Opinion ranks keep their real ids and authored order for students.
        self._assert_all_shown(self._student_views(), authored)
        self.assertIs(self.school._student_rank_display(live["item"]), live["item"])



_PUBLISH_SCRIPT = r"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from school_db import SchoolDB
root = Path(sys.argv[2])
args = json.loads(sys.argv[3])
school = SchoolDB(root / "lloves.sqlite", root)
go = root / "go"
deadline = time.time() + 60
while not go.exists() and time.time() < deadline:
    time.sleep(0.005)
start = float(go.read_text())
while time.time() < start:
    pass
try:
    item = school.publish_live_session_item(args["session"], args["item"], publish_mode=args["mode"])
    print("RESULT ok " + json.dumps(item["item"].get("rank_display_order")))
except Exception as exc:
    print("RESULT error " + repr(exc))
"""


class GateFixTests(unittest.TestCase):
    """Ops gate on 02f9652: publish again (HIGH-1), racing publishes (MED-1),
    items opened before the deploy (LOW-1)."""

    setUp = RankShuffleLiveTests.setUp
    tearDown = RankShuffleLiveTests.tearDown
    _add = RankShuffleLiveTests._add
    _publish = RankShuffleLiveTests._publish
    _keyed = RankShuffleLiveTests._keyed
    _ids = RankShuffleLiveTests._ids
    _key = RankShuffleLiveTests._key
    _aliases = RankShuffleLiveTests._aliases
    _from_student = RankShuffleLiveTests._from_student
    _student_views = RankShuffleLiveTests._student_views
    STEM = RankShuffleLiveTests.STEM
    BODY = RankShuffleLiveTests.BODY

    # helpers -------------------------------------------------------------
    def _row(self, live_id: int) -> dict[str, Any]:
        row = self.school.conn.execute("SELECT * FROM live_session_items WHERE id = ?", (live_id,)).fetchone()
        return dict(row)

    def _db_orders(self, live_id: int) -> tuple[list[str], list[str]]:
        """(item_json order, prompt order) straight from the DB."""
        row = self._row(live_id)
        item = json.loads(row["item_json"])
        prompt = self.school.conn.execute(
            "SELECT payload FROM live_session_prompts WHERE id = ?", (row["prompt_id"],)
        ).fetchone()
        payload = json.loads(prompt["payload"]) if prompt else {}
        return (
            [o["id"] for o in item.get("rank_options") or []],
            [o["id"] for o in payload.get("rank_options") or []],
        )

    def _every_view(self, live_id: int, stem: str) -> dict[str, set[str]]:
        """Order(s) per surface: DB rows, students, teacher state (card/projector/slides)."""
        item_order, prompt_order = self._db_orders(live_id)
        out = {"item_json": {",".join(item_order)}, "prompt": {",".join(prompt_order)}}
        ref = {"id": live_id}
        for name in ("Ava", "Cy", "Ben"):
            body = self.students[name].get("/api/student/state").get_json()
            out[f"{name} state"] = {
                ",".join(self._from_student(ref, ids)) for ids in _rank_lists(body, stem, [])
            }
        meta = self.school.student_live_class_metadata_for_session(self.session_id)
        out["student metadata"] = {
            ",".join(self._from_student(ref, ids)) for ids in _rank_lists(meta, stem, [])
        }
        teacher = self.client.get(f"/api/live-sessions/{self.session_id}/state").get_json()
        lifecycle = [
            r for r in (teacher.get("live_items") or teacher.get("items") or [])
            if isinstance(r, dict) and int(r.get("id") or 0) == live_id
        ]
        out["teacher state"] = {",".join(ids) for ids in _rank_lists(lifecycle or teacher, stem, [])}
        return out

    def _assert_one_order(self, live_id: int, stem: str) -> list[str]:
        views = self._every_view(live_id, stem)
        for where, orders in views.items():
            self.assertTrue(orders, where)
        everything = set().union(*views.values())
        self.assertEqual(len(everything), 1, views)
        order = next(iter(everything)).split(",")
        live = self.school.get_live_session_item(self.session_id, live_id)
        self.assertNotEqual(order, self.school._keyed_rank_ids(live)[1])
        return order

    def _move(self, row: dict[str, Any], target: int) -> None:
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/playlist-item",
            json={"item_id": row["item_id"], "action": "move", "target_page_index": target},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))

    def _publish_mode(self, live_id: int, mode: str):
        return self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{live_id}/publish", json={"publish_mode": mode}
        )

    # HIGH-1 --------------------------------------------------------------
    def test_publish_again_after_a_move_is_200_with_a_new_order(self) -> None:
        for mode in ("individual", "group_submit"):
            stem = f"{mode}: order the steps."
            row = self._add({**self.BODY, "text": stem, "rank_key": [2, 0, 3, 1]})
            live_id = int(row["id"])
            rv = self._publish_mode(live_id, mode)
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
            seen = [self._assert_one_order(live_id, stem)]
            for attempt, target in enumerate((2, 3, 2, 3, 2, 3)):
                self._move(row, target)
                self.assertEqual(self._row(live_id)["status"], "inactive", mode)
                rv = self._publish_mode(live_id, mode)
                self.assertEqual(rv.status_code, 200, f"{mode} #{attempt}: {rv.get_data(as_text=True)[:300]}")
                self.assertNotIn(RANK_DISPLAY_PENDING_FIELD, self._row(live_id)["item_json"])
                seen.append(self._assert_one_order(live_id, stem))
            # A fresh draw per publish (23 non-key orders of 4; 7 draws).
            self.assertGreater(len({tuple(o) for o in seen}), 1, (mode, seen))
            self.client.post(f"/api/live-sessions/{self.session_id}/items/{live_id}/close")

    def _make_legacy(self, live_id: int) -> list[str]:
        """Rewrite a published row and its prompt as base 86ae6ce stored them.

        Old ids were ``o1``… in typed order (the leak MCK-176 aliases away).
        """
        row = self._row(live_id)
        item = json.loads(row["item_json"])
        by_label = {o["label"]: o for o in item["rank_options"]}
        typed = [by_label[label] for label in self.BODY["options"]]
        rename = {o["id"]: f"o{i}" for i, o in enumerate(typed, 1)}
        authored = [{"id": rename[o["id"]], "label": o["label"]} for o in typed]
        legacy_key = [rename[opt] for opt in item["rank_key"]]
        self.assertEqual(legacy_key, KEY)
        for raw, table, column, rid in (
            (item, "live_session_items", "item_json", live_id),
        ):
            raw.pop(RANK_DISPLAY_ORDER_FIELD, None)
            raw.pop(RANK_DISPLAY_PENDING_FIELD, None)
            raw["rank_options"] = authored
            raw["rank_key"] = legacy_key
            raw["options"] = raw["choices"] = [o["label"] for o in authored]
            self.school.conn.execute(f"UPDATE {table} SET {column} = ? WHERE id = ?", (json.dumps(raw), rid))
        placed = self.school.conn.execute(
            "SELECT id, item_json FROM class_live_playlist_placements WHERE placement_key = ?",
            (row["placement_key"],),
        ).fetchone()
        if placed is not None:
            source = json.loads(placed["item_json"])
            source["rank_options"] = [
                {**o, "id": rename.get(o["id"], o["id"])} for o in source.get("rank_options") or []
            ]
            source["rank_key"] = [rename.get(opt, opt) for opt in source.get("rank_key") or []]
            self.school.conn.execute(
                "UPDATE class_live_playlist_placements SET item_json = ? WHERE id = ?",
                (json.dumps(source), placed["id"]),
            )
            self.school._live_metadata_cache.clear()  # the playlist is cached in-process
        if row["prompt_id"]:
            prompt = self.school.conn.execute(
                "SELECT payload FROM live_session_prompts WHERE id = ?", (row["prompt_id"],)
            ).fetchone()
            payload = json.loads(prompt["payload"])
            payload.pop(RANK_DISPLAY_ORDER_FIELD, None)
            payload["rank_options"] = authored
            payload["rank_key"] = legacy_key
            payload["options"] = payload["choices"] = [o["label"] for o in authored]
            self.school.conn.execute(
                "UPDATE live_session_prompts SET payload = ? WHERE id = ?", (json.dumps(payload), row["prompt_id"])
            )
        return [o["id"] for o in authored]

    # HIGH-1 deploy case + LOW-1 --------------------------------------------
    def test_item_open_before_the_deploy(self) -> None:
        """Teacher views get the students' order saved; publishing again works."""
        for mode in ("individual", "group_submit"):
            stem = f"{mode}: order the steps."
            row = self._add({**self.BODY, "text": stem, "rank_key": [2, 0, 3, 1]})
            live_id = int(row["id"])
            self.assertEqual(self._publish_mode(live_id, mode).status_code, 200)
            authored = self._make_legacy(live_id)
            self.assertEqual(self._db_orders(live_id)[0], authored)
            # What students saw before any teacher view ran (placement-key shuffle).
            shown = _rank_lists(self.students["Ava"].get("/api/student/state").get_json(), stem, [])
            self.assertTrue(shown)
            before = [self._from_student({"id": live_id}, ids) for ids in shown]
            self.assertNotEqual(before[0], KEY)
            # A teacher / projector read stores that same order on the row.
            self.client.get(f"/api/live-sessions/{self.session_id}/state")
            listed = [r for r in self.school.list_live_session_items(self.session_id) if int(r["id"]) == live_id][0]
            self.assertEqual([o["id"] for o in listed["item"]["rank_options"]], before[0])
            self.assertEqual(self.school.rank_display_order(listed), before[0])
            self.assertEqual(self._assert_one_order(live_id, stem), before[0])
            # Publish again on the open item: 200, the order students have stays.
            rv = self._publish_mode(live_id, mode)
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True)[:300])
            self.assertEqual(self._assert_one_order(live_id, stem), before[0])
            # Moved and published again: 200 and a fresh order everywhere.
            self._move(row, 2)
            rv = self._publish_mode(live_id, mode)
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True)[:300])
            self._assert_one_order(live_id, stem)
            self.client.post(f"/api/live-sessions/{self.session_id}/items/{live_id}/close")

    def test_unpublished_legacy_row_teacher_card_never_lists_the_key(self) -> None:
        row = self._keyed()
        key = self._key(row)
        live_id = int(row["id"])
        item = json.loads(self._row(live_id)["item_json"])
        by_id = {o["id"]: o for o in item["rank_options"]}
        item.pop(RANK_DISPLAY_ORDER_FIELD, None)
        item["rank_options"] = [by_id[i] for i in key]
        item["options"] = item["choices"] = [by_id[i]["label"] for i in key]
        self.school.conn.execute("UPDATE live_session_items SET item_json = ? WHERE id = ?", (json.dumps(item), live_id))
        listed = [r for r in self.school.list_live_session_items(self.session_id) if int(r["id"]) == live_id][0]
        self.assertNotEqual([o["id"] for o in listed["item"]["rank_options"]], key)
        self.assertEqual(self._db_orders(live_id)[0], [o["id"] for o in listed["item"]["rank_options"]])

    # MED-1 ---------------------------------------------------------------
    def test_six_processes_publish_at_once_one_order_everywhere(self) -> None:
        root = Path(self.tmp.name)
        for round_no, mode in enumerate(("individual", "group_submit", "individual")):
            stem = f"Race {round_no}: order the steps."
            row = self._add({**self.BODY, "text": stem, "rank_key": [2, 0, 3, 1]})
            live_id = int(row["id"])
            go = root / "go"
            go.unlink(missing_ok=True)
            args = json.dumps({"session": self.session_id, "item": live_id, "mode": mode})
            procs = [
                subprocess.Popen(
                    [sys.executable, "-c", _PUBLISH_SCRIPT, str(LMS_DIR), str(root), args],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=str(LMS_DIR),
                )
                for _ in range(6)
            ]
            time.sleep(3.0)
            go.write_text(str(time.time() + 1.0))
            replies = set()
            for proc in procs:
                stdout, stderr = proc.communicate(timeout=180)
                line = next((ln for ln in stdout.splitlines() if ln.startswith("RESULT ")), "")
                self.assertTrue(line.startswith("RESULT ok "), (line, stderr[-2000:]))
                replies.add(line[len("RESULT ok "):])
            order = self._assert_one_order(live_id, stem)
            # Every racing publish returned the order that stuck.
            self.assertEqual(replies, {json.dumps(order)})
            self.assertNotIn(RANK_DISPLAY_PENDING_FIELD, self._row(live_id)["item_json"])
            self.client.post(f"/api/live-sessions/{self.session_id}/items/{live_id}/close")


def _strings(node: Any, path: str = "", out: list[tuple[str, str]] | None = None) -> list[tuple[str, str]]:
    """Every ``(path, string)`` in a JSON tree."""
    out = [] if out is None else out
    if isinstance(node, dict):
        for key, value in node.items():
            _strings(value, f"{path}.{key}", out)
    elif isinstance(node, list):
        for value in node:
            _strings(value, f"{path}[]", out)
    elif isinstance(node, str):
        out.append((path, node))
    return out


class OptionAliasTests(unittest.TestCase):
    """Ops MED (from #233): students never see option ids that reveal typed order."""

    setUp = RankShuffleLiveTests.setUp
    tearDown = RankShuffleLiveTests.tearDown
    _add = RankShuffleLiveTests._add
    _post = RankShuffleLiveTests._post
    _card = RankShuffleLiveTests._card
    _ids = RankShuffleLiveTests._ids
    _key = RankShuffleLiveTests._key
    _aliases = RankShuffleLiveTests._aliases
    _to_student = RankShuffleLiveTests._to_student
    _from_student = RankShuffleLiveTests._from_student
    _row = GateFixTests._row
    _make_legacy = GateFixTests._make_legacy
    STEM = RankShuffleLiveTests.STEM
    BODY = RankShuffleLiveTests.BODY

    def _item(self, mode: str, stem: str, *, legacy: bool = True) -> tuple[dict[str, Any], int | None]:
        """A published keyed rank; ``legacy`` rewrites it with o1… ids first.

        Returns:
            ``(lifecycle row, prompt id or None)``.
        """
        row = self._add({**self.BODY, "text": stem, "rank_key": [2, 0, 3, 1]})
        live_id = int(row["id"])
        if legacy:
            self.assertEqual(self._make_legacy(live_id), ["o1", "o2", "o3", "o4"])
        settings = {"show_live_results": True}
        if mode == "turns":
            settings["group_rank_mode"] = "turns"
        rv = self.client.patch(f"/api/live-sessions/{self.session_id}/items/{live_id}/settings", json=settings)
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        publish_mode = "individual" if mode == "individual" else "group_submit"
        rv = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{live_id}/publish", json={"publish_mode": publish_mode}
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        live = self.school.get_live_session_item(self.session_id, live_id)
        prompt = self.school._prompt_for_live_item(live) if mode == "individual" else None
        return live, (int(prompt["id"]) if prompt else None)

    def _real_ids(self, live: dict[str, Any]) -> list[str]:
        fresh = self.school.get_live_session_item(self.session_id, int(live["id"]))
        keyed = self.school._keyed_rank_ids(fresh)
        self.assertIsNotNone(keyed)
        return list(keyed[0])

    def _student_bodies(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in ("Ava", "Cy", "Ben"):
            client = self.students[name]
            out[f"{name} state"] = client.get("/api/student/state").get_json()
            out[f"{name} live-prompt"] = client.get("/api/student/live-prompt").get_json()
            out[f"{name} page"] = client.get("/student").get_data(as_text=True)
        out["metadata"] = self.school.student_live_class_metadata_for_session(self.session_id)
        out["items"] = self.school.student_live_items_payload(self.session_id, self.ids["Ava"])
        return out

    def _assert_no_real_ids(self, bodies: dict[str, Any], real: list[str]) -> None:
        for where, body in bodies.items():
            if isinstance(body, str):
                for opt in real:
                    self.assertIsNone(re.search(rf"[\"'\s=]{re.escape(opt)}[\"'\s,\]]", body), (where, opt))
                continue
            leaks = [(path, text) for path, text in _strings(body) if text in real]
            self.assertEqual(leaks, [], where)

    def _answer(self, mode: str, live: dict[str, Any], prompt_id: int | None, order: list[str]) -> dict[str, Any]:
        """Send ``order`` (student ids) on the mode's own route(s); return every reply."""
        replies: dict[str, Any] = {}
        if mode == "individual":
            rv = self.students["Ava"].post(
                "/api/student/live-prompt/response", json={"prompt_id": prompt_id, "response": {"order": order}}
            )
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
            replies["response"] = rv.get_json()
        elif mode == "together":
            for opt in order[:2]:
                rv = self._post("Ava", live, "group-draft", {"tap": opt})
                self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
                replies[f"tap {opt}"] = rv.get_json()
            rv = self._post("Cy", live, "group-submit", {"order": order})
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
            replies["submit"] = rv.get_json()
        else:
            for name, opt in zip(("Ava", "Cy", "Ava", "Cy"), order):
                rv = self._post(name, live, "rank-turn", {"option_id": opt})
                self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
                replies[f"turn {opt}"] = rv.get_json()
        return replies

    def _saved_order(self, mode: str, live: dict[str, Any], prompt_id: int | None) -> list[str]:
        if mode == "individual":
            saved = self.school.get_live_prompt_response(int(prompt_id), self.ids["Ava"])
            return list(saved["response"]["order"])
        team_id = self._card("Ava", live)["team_id"]
        row = self.school._group_response_row(int(live["id"]), int(team_id))
        return list((row.get("final_answer") or {}).get("order") or [])

    def test_no_student_payload_or_page_shows_real_option_ids(self) -> None:
        """Open and closed, every mode, legacy o1… ids and new minted ids."""
        for legacy in (True, False):
            for mode in ("together", "turns", "individual"):
                live, prompt_id = self._item(mode, f"Leak {mode} {legacy}: order.", legacy=legacy)
                real = self._real_ids(live)
                if legacy:
                    self.assertEqual(sorted(real), ["o1", "o2", "o3", "o4"])
                bodies = self._student_bodies()
                key = list(live["item"]["rank_key"])
                replies = self._answer(mode, live, prompt_id, self._to_student(live, key))
                bodies.update({f"reply {k}": v for k, v in replies.items()})
                bodies.update({f"answered {k}": v for k, v in self._student_bodies().items()})
                self.client.post(f"/api/live-sessions/{self.session_id}/items/{live['id']}/close")
                bodies.update({f"closed {k}": v for k, v in self._student_bodies().items()})
                self._assert_no_real_ids(bodies, real)
                # The closed results do reach students, as aliases.
                closed = json.dumps(bodies["closed Ava state"])
                self.assertIn(self._to_student(live, key)[0], closed)

    def test_aliases_stable_across_reloads_workers_and_the_group(self) -> None:
        live, _ = self._item("together", "Stable: order the steps.")
        views = []
        for _ in range(3):
            for name in ("Ava", "Cy", "Ben"):
                body = self.students[name].get("/api/student/state").get_json()
                views.extend(_rank_lists(body, "Stable: order the steps.", []))
        self.assertTrue(views)
        self.assertEqual({tuple(v) for v in views}, {tuple(views[0])})
        self.assertEqual(sorted(self._from_student(live, views[0])), ["o1", "o2", "o3", "o4"])
        # Another worker (fresh SchoolDB, same app secret) mints the same aliases.
        aliases = self._aliases(live)
        mapping = {opt: aliases.out(opt) for opt in aliases.ids}
        secret = str(self.school.rank_alias_secret)
        for opt, alias in mapping.items():
            self.assertEqual(rank_option_alias(aliases.scope, opt, secret=secret), alias)
            self.assertRegex(alias, r"^r[0-9a-f]{10}$")
        # Aliases do not sort in typed order the way o1… did, and are per item.
        other, _ = self._item("together", "Other: order the steps.")
        self.assertNotEqual(self._to_student(other, ["o1"]), self._to_student(live, ["o1"]))
        self.assertNotEqual(rank_option_alias(aliases.scope, "o1", secret="another"), mapping["o1"])

    def test_submissions_via_aliases_score_4_of_4(self) -> None:
        for mode in ("individual", "together", "turns"):
            live, prompt_id = self._item(mode, f"Score {mode}: order the steps.")
            key = list(live["item"]["rank_key"])
            self.assertEqual(key, KEY)
            self._answer(mode, live, prompt_id, self._to_student(live, key))
            saved = self._saved_order(mode, live, prompt_id)
            self.assertEqual(saved, KEY, mode)
            self.assertEqual(rank_race_score(saved, key), {"right": 4, "total": 4, "spots": [True] * 4})
            self.client.post(f"/api/live-sessions/{self.session_id}/items/{live['id']}/close")

    def test_unknown_alias_is_rejected(self) -> None:
        fake = "r0123456789"
        for mode in ("individual", "together", "turns"):
            live, prompt_id = self._item(mode, f"Reject {mode}: order the steps.")
            shown = self._to_student(live, KEY)
            other, _ = self._item("together", f"Elsewhere {mode}: order the steps.")
            foreign = self._to_student(other, ["o1"])[0]  # an alias from another item
            for bad in (fake, foreign):
                order = [bad] + shown[1:]
                if mode == "individual":
                    rv = self.students["Ava"].post(
                        "/api/student/live-prompt/response",
                        json={"prompt_id": prompt_id, "response": {"order": order}},
                    )
                    self.assertEqual(rv.status_code, 400, rv.get_data(as_text=True))
                    self.assertIsNone(self.school.get_live_prompt_response(int(prompt_id), self.ids["Ava"]))
                elif mode == "together":
                    self.assertEqual(self._post("Ava", live, "group-draft", {"tap": bad}).status_code, 400)
                    self.assertEqual(self._post("Ava", live, "group-draft", {"order": order}).status_code, 400)
                    self.assertEqual(self._post("Ava", live, "group-submit", {"order": order}).status_code, 400)
                    self.assertFalse(self._card("Ava", live)["submitted"])
                else:
                    self.assertEqual(self._post("Ava", live, "rank-turn", {"option_id": bad}).status_code, 400)
                    self.assertEqual(self._card("Ava", live)["order"], [])
            self.client.post(f"/api/live-sessions/{self.session_id}/items/{other['id']}/close")
            self.client.post(f"/api/live-sessions/{self.session_id}/items/{live['id']}/close")

    def test_new_items_get_opaque_ids(self) -> None:
        row = self._add({**self.BODY, "text": "Minted: order the steps.", "rank_key": [2, 0, 3, 1]})
        ids = self._ids(row)
        self.assertEqual(len(set(ids)), 4)
        for opt in ids:
            self.assertRegex(opt, r"^o[0-9a-f]{6}$")
        self.assertEqual(row["item"]["rank_key"], [ids[2], ids[0], ids[3], ids[1]])


if __name__ == "__main__":
    unittest.main()

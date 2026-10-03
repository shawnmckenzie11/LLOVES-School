#!/usr/bin/env python3
"""MCK-176: answer-order ranks are never shown in the key's order.

Pure shuffle rules, then real teacher and student routes on MCR3U M1 C2
(two teams of two: Ava + Cy, Ben + Dee), borrowed from the MCK-155 suite.
"""

from __future__ import annotations

import json
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
        for name in ("Ava", "Cy", "Ben"):
            body = self.students[name].get("/api/student/state").get_json()
            out[f"{name} state"] = {",".join(ids) for ids in _rank_lists(body, stem, [])}
        meta = self.school.student_live_class_metadata_for_session(self.session_id)
        out["student metadata"] = {",".join(ids) for ids in _rank_lists(meta, stem, [])}
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
        self.assertNotEqual(order, KEY)
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
        """Rewrite a published row and its prompt as base 86ae6ce stored them."""
        row = self._row(live_id)
        item = json.loads(row["item_json"])
        by_id = {o["id"]: o for o in item["rank_options"]}
        authored = [by_id[f"o{i}"] for i in range(1, 5)]
        for raw, table, column, rid in (
            (item, "live_session_items", "item_json", live_id),
        ):
            raw.pop(RANK_DISPLAY_ORDER_FIELD, None)
            raw.pop(RANK_DISPLAY_PENDING_FIELD, None)
            raw["rank_options"] = authored
            raw["options"] = raw["choices"] = [o["label"] for o in authored]
            self.school.conn.execute(f"UPDATE {table} SET {column} = ? WHERE id = ?", (json.dumps(raw), rid))
        if row["prompt_id"]:
            prompt = self.school.conn.execute(
                "SELECT payload FROM live_session_prompts WHERE id = ?", (row["prompt_id"],)
            ).fetchone()
            payload = json.loads(prompt["payload"])
            payload.pop(RANK_DISPLAY_ORDER_FIELD, None)
            payload["rank_options"] = authored
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
            before = _rank_lists(self.students["Ava"].get("/api/student/state").get_json(), stem, [])
            self.assertTrue(before)
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
        live_id = int(row["id"])
        item = json.loads(self._row(live_id)["item_json"])
        by_id = {o["id"]: o for o in item["rank_options"]}
        item.pop(RANK_DISPLAY_ORDER_FIELD, None)
        item["rank_options"] = [by_id[i] for i in KEY]
        item["options"] = item["choices"] = [by_id[i]["label"] for i in KEY]
        self.school.conn.execute("UPDATE live_session_items SET item_json = ? WHERE id = ?", (json.dumps(item), live_id))
        listed = [r for r in self.school.list_live_session_items(self.session_id) if int(r["id"]) == live_id][0]
        self.assertNotEqual([o["id"] for o in listed["item"]["rank_options"]], KEY)
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


if __name__ == "__main__":
    unittest.main()

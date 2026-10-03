#!/usr/bin/env python3
"""MCK-171 Team challenge (d): scoring at Close, the award, results blocks.

2 points per right spot, nothing else (Wonder v3, Shawn 9:41 ET: no bonus).
A full order can never have exactly one wrong spot, so with the 4-option
item here a full order totals 8 / 4 / 2 / 0 (5 options: 10 / 6 / 4 / 2 / 0).
A partial draft scores its filled spots.

Two teams of three: Ava + Cy + Eli, Ben + Dee + Fay. Answer order:
o3, o1, o4, o2.
"""

from __future__ import annotations

import itertools
import json
import subprocess
import unittest
from pathlib import Path
from typing import Any

import rank_challenge
from live_rank import rank_race_score
from test_rank_challenge_mck171 import KEY_IDS, ChallengeHarness

LMS_DIR = Path(__file__).resolve().parent

#: First two swapped: 2 right -> 4 points.
SWAP = ["o1", "o3", "o4", "o2"]


class ScoringRuleTests(unittest.TestCase):
    """Pure points and podium rules."""

    def test_two_points_per_right_spot_only(self) -> None:
        self.assertEqual(rank_challenge.BASE_PER_SPOT, 2)
        self.assertEqual([rank_challenge.team_points(n) for n in range(6)], [0, 2, 4, 6, 8, 10])

    def test_full_order_totals_are_10_6_4_2_0(self) -> None:
        key = ["a", "b", "c", "d", "e"]
        totals = {
            rank_challenge.team_points(rank_race_score(list(p), key)["right"])
            for p in itertools.permutations(key)
        }
        self.assertEqual(totals, {10, 6, 4, 2, 0})
        four = {
            rank_challenge.team_points(rank_race_score(list(p), key[:4])["right"])
            for p in itertools.permutations(key[:4])
        }
        self.assertEqual(four, {8, 4, 2, 0})

    def test_partial_draft_scores_filled_spots(self) -> None:
        self.assertEqual(rank_race_score(["o3", "o1"], KEY_IDS)["right"], 2)
        self.assertEqual(rank_race_score(["o3", "o1", "o4"], KEY_IDS)["right"], 3)  # 6 points

    def _team(self, tid: int, name: str, pts: int, scored: bool = True) -> dict[str, Any]:
        return {"team_id": tid, "team_name": name, "points": pts, "scored": scored}

    def test_podium_dense_ties_share_a_step(self) -> None:
        board = rank_challenge.podium(
            [
                self._team(1, "Tangents", 6),
                self._team(2, "Secants", 10),
                self._team(3, "Cosines", 6),
                self._team(4, "Radians", 4),
                self._team(5, "Vectors", 2),
                self._team(6, "Sines", 0, scored=False),
            ]
        )
        self.assertEqual(
            board["steps"],
            [
                {"step": 1, "points": 10, "team_ids": [2]},
                {"step": 2, "points": 6, "team_ids": [3, 1]},
                {"step": 3, "points": 4, "team_ids": [4]},
            ],
        )
        self.assertEqual(board["others"], [6, 5])  # alphabetical, no places

    def test_podium_all_tied_and_zero(self) -> None:
        board = rank_challenge.podium([self._team(1, "B", 8), self._team(2, "A", 8)])
        self.assertEqual(board["steps"], [{"step": 1, "points": 8, "team_ids": [2, 1]}])
        self.assertEqual(board["others"], [])
        none = rank_challenge.podium([self._team(1, "B", 0), self._team(2, "A", 0, scored=False)])
        self.assertEqual(none["steps"], [])
        self.assertEqual(none["others"], [2, 1])

    def test_no_bonus_code_left(self) -> None:
        for path in ("rank_challenge.py", "school_db.py", "app.py", "static/rank_challenge_view.js",
                     "static/rank_challenge_phone.js", "static/group_setup.js", "static/staff_ap.js",
                     "static/student-portal.js"):
            text = (LMS_DIR / path).read_text(encoding="utf-8")
            for word in ("hardest", "group_rank_race_bonus", "best_move", "Best move", "fastest"):
                self.assertNotIn(word, text, f"{word} in {path}")


class AwardTests(ChallengeHarness):
    """Close scores every team once and awards equal points per member."""

    def _points(self, name: str) -> int:
        with self.school.game._lock:
            row = self.school.game.conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS n FROM point_events "
                "WHERE to_kind = 'student' AND to_id = ?",
                (self.ids[name],),
            ).fetchone()
        return int(row["n"] or 0)

    def _lock_team(self, item: dict[str, Any], names: tuple[str, ...], order: list[str]) -> None:
        self.assertEqual(self._order(names[0], item, order).status_code, 200)
        for name in names:
            rv = self._post(name, item, "rank-agree", {"agree": True, "order": order})
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))

    def test_locked_and_draft_teams_score_at_close(self) -> None:
        item = self._challenge()
        self._lock_team(item, ("Ava", "Cy", "Eli"), KEY_IDS)
        self._order("Ben", item, SWAP)  # never locks: scored on its draft
        self.assertEqual(self._points("Ava"), 0)  # nothing before Close
        self._close(item)
        for name in ("Ava", "Cy", "Eli"):
            self.assertEqual(self._points(name), 8)
        for name in ("Ben", "Dee", "Fay"):
            self.assertEqual(self._points(name), 4)
        rows = {self._team(n, item): self._row(item, n) for n in ("Ava", "Ben")}
        self.assertEqual(rows[self._team("Ava", item)]["awarded_points"], 8)
        self.assertEqual(rows[self._team("Ben", item)]["awarded_points"], 4)

    def test_absent_members_get_the_team_total_too(self) -> None:
        item = self._challenge()
        self._leave("Eli")
        self._lock_team(item, ("Ava", "Cy"), KEY_IDS)
        self._close(item)
        self.assertEqual(self._points("Eli"), 8)

    def test_award_is_once_and_replace_safe(self) -> None:
        item = self._challenge()
        self._order("Ava", item, SWAP)
        closed = self._close(item)
        self.assertEqual(self._points("Ava"), 4)
        # Running the award again changes nothing.
        self.school._award_rank_race_points(self.session_id, closed)
        self.assertEqual(self._points("Ava"), 4)
        # A changed result replaces the old award (difference only).
        team = self._team("Ava", item)
        with self.school._lock:
            self.school.conn.execute(
                "UPDATE live_group_responses SET proposed_answer_json = ? "
                "WHERE live_item_id = ? AND team_id = ?",
                (json.dumps({"kind": "rank", "order": KEY_IDS, "complete": True}), int(item["id"]), team),
            )
            self.school.conn.commit()
        self.school._award_rank_race_points(self.session_id, closed)
        self.assertEqual(self._points("Ava"), 8)
        self.school._award_rank_race_points(self.session_id, closed)
        self.assertEqual(self._points("Ava"), 8)

    def test_team_that_placed_nothing_scores_nothing(self) -> None:
        item = self._challenge()
        self._order("Ava", item, KEY_IDS)
        self._close(item)
        self.assertEqual(self._points("Ben"), 0)
        results = self._view(item)["race"]["results"]
        ben = next(t for t in results["teams"] if t["team_id"] == self._team("Ben", item))
        self.assertFalse(ben["scored"])
        self.assertNotIn(ben["team_id"], [tid for s in results["podium"]["steps"] for tid in s["team_ids"]])

    def test_plain_rank_awards_nothing(self) -> None:
        row = self._rank_row()
        item = self._publish(row)
        self._order("Ava", item, KEY_IDS)
        self._post("Ava", item, "group-submit", {"order": KEY_IDS})
        self._close(item)
        self.assertEqual(self._points("Ava"), 0)
        self.assertIsNone(self._row(item, "Ava").get("awarded_points"))

    def test_ties_share_a_step_and_no_lock_time_is_read(self) -> None:
        item = self._challenge()
        self._lock_team(item, ("Ben", "Dee", "Fay"), SWAP)
        self._lock_team(item, ("Ava", "Cy", "Eli"), SWAP)
        self._close(item)
        steps = self._view(item)["race"]["results"]["podium"]["steps"]
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["points"], 4)
        self.assertEqual(len(steps[0]["team_ids"]), 2)
        self.assertNotIn("finalized_at", json.dumps(self._view(item)["race"]))


class ResultsBlockTests(ChallengeHarness):
    """Teacher results after Close only; phones get their own team only."""

    def test_teacher_results_only_after_close(self) -> None:
        item = self._challenge()
        self._order("Ava", item, KEY_IDS)
        self.assertNotIn("results", self._view(item)["race"])
        self._close(item)
        results = self._view(item)["race"]["results"]
        self.assertEqual([s["n"] for s in results["spots"]], [1, 2, 3, 4])
        self.assertEqual([s["item"] for s in results["spots"]], ["An equation", "Two points", "A table", "A graph"])
        ava = next(t for t in results["teams"] if t["team_id"] == self._team("Ava", item))
        self.assertEqual((ava["right"], ava["points"], ava["locked"]), (4, 8, False))

    def test_phone_results_own_team_only_with_results_on(self) -> None:
        item = self._challenge()
        self._order("Ava", item, SWAP)
        self._order("Ben", item, KEY_IDS)
        self.school.update_live_session_item_settings(self.session_id, int(item["id"]), show_live_results=False)
        self._close(item)
        self.assertNotIn("results", self._card("Ava", item)["race"])
        self.school.update_live_session_item_settings(self.session_id, int(item["id"]), show_live_results=True)
        res = self._card("Ava", item)["race"]["results"]
        self.assertEqual((res["right"], res["total"], res["points"]), (2, 4, 4))
        self.assertEqual(
            [(s["item"], s["right"], s["answer"]) for s in res["spots"]],
            [("Two points", False, "An equation"), ("An equation", False, "Two points"),
             ("A table", True, ""), ("A graph", True, "")],
        )
        self.assertFalse(res["on_podium"] and res["podium"][0]["teams"][0]["mine"])
        # Ben's team is on step 1 with 8; Ava's on step 2 with 4.
        self.assertEqual([s["points"] for s in res["podium"]], [8, 4])
        self.assertTrue(res["on_podium"])
        body = json.dumps(self._card("Ava", item))
        self.assertNotIn("rank_key", body)
        self.assertNotIn('"order": ["o3", "o1", "o4", "o2"]', body)  # Ben's order never leaks
        # The public results carry no class stack / other team orders.
        rv = self.students["Ava"].get("/api/student/state")
        state = rv.get_data(as_text=True)
        self.assertNotIn("class_order", state)
        self.assertNotIn("rank_key", state)


class ResultsClientTests(unittest.TestCase):
    """Node harness for the results views (projector + phone)."""

    def test_results_node_harness(self) -> None:
        proc = subprocess.run(
            ["node", str(LMS_DIR / "static" / "rank_challenge_results.test.mjs")],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("ok", proc.stdout)


if __name__ == "__main__":
    unittest.main()

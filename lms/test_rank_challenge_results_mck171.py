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


#: 4 spots: Next 1..4 reveals the spots, 5 is points, 6 is the podium.
POINTS_STEP = rank_challenge.points_step(4)
PODIUM_STEP = rank_challenge.podium_step(4)


class StepMixin:
    """Projector reveal steps through the teacher route."""

    def _step(self, item: dict[str, Any], step: int):
        return self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{item['id']}/race-step", json={"step": step}
        )

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


class AwardTests(StepMixin, ChallengeHarness):
    """Points land once, at the projector points step, to members seen present."""

    def test_points_land_at_the_points_step_not_at_close(self) -> None:
        item = self._challenge()
        self._lock_team(item, ("Ava", "Cy", "Eli"), KEY_IDS)
        self._order("Ben", item, SWAP)  # never locks: scored on its draft
        self._close(item)
        self.assertEqual(self._points("Ava"), 0)  # Close spoils nothing
        self.assertEqual(self._step(item, POINTS_STEP - 1).status_code, 200)
        self.assertEqual(self._points("Ava"), 0)  # spots only
        rv = self._step(item, POINTS_STEP)
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        self.assertEqual(rv.get_json()["step"], POINTS_STEP)
        for name in ("Ava", "Cy", "Eli"):
            self.assertEqual(self._points(name), 8)
        for name in ("Ben", "Dee", "Fay"):
            self.assertEqual(self._points(name), 4)
        rows = {self._team(n, item): self._row(item, n) for n in ("Ava", "Ben")}
        self.assertEqual(rows[self._team("Ava", item)]["awarded_points"], 8)
        self.assertEqual(rows[self._team("Ben", item)]["awarded_points"], 4)
        self._step(item, PODIUM_STEP)  # the podium step pays nothing more
        self.assertEqual(self._points("Ava"), 8)

    def test_never_present_member_gets_nothing(self) -> None:
        self._leave("Eli")
        item = self._challenge()
        self._lock_team(item, ("Ava", "Cy"), KEY_IDS)
        self._close(item)
        self._step(item, POINTS_STEP)
        self.assertEqual((self._points("Ava"), self._points("Cy"), self._points("Eli")), (8, 8, 0))

    def test_briefly_present_member_gets_the_team_total(self) -> None:
        self._leave("Eli")
        item = self._challenge()
        self._view(item)
        code = self.school.get_live_session(self.session_id)["session_code"]
        self.students["Eli"].post("/auth/student-code", data={"code": str(code), "name": "Eli"})
        self._view(item)  # Eli seen mid-question
        self._leave("Eli")  # gone again before Close
        self._lock_team(item, ("Ava", "Cy"), KEY_IDS)
        self._close(item)
        self._step(item, POINTS_STEP)
        self.assertEqual(self._points("Eli"), 8)

    def test_end_of_class_awards_a_challenge_never_stepped(self) -> None:
        item = self._challenge()
        self._order("Ava", item, KEY_IDS)
        self._close(item)
        self.assertEqual(self._points("Ava"), 0)
        live = self.client.post(
            f"/api/classes/{self.class_id}/game/start-rounds",
            json={"rounds": [{"kind": "challenge", "minutes": 10}]},
        )
        self.assertEqual(live.status_code, 200, live.get_data(as_text=True)[:300])
        rv = self.client.post(f"/api/classes/{self.class_id}/game/end", json={})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True)[:300])
        self.assertEqual(self._points("Ava"), 8)
        self.assertEqual(rv.get_json()["live_sessions_ended"], [self.session_id])

    def test_session_end_awards_too(self) -> None:
        item = self._challenge()
        self._order("Ava", item, KEY_IDS)
        self._close(item)
        self.school.end_live_class_session(self.session_id)
        self.assertEqual(self._points("Ava"), 8)

    def test_award_is_once_and_replace_safe(self) -> None:
        item = self._challenge()
        self._order("Ava", item, SWAP)
        closed = self._close(item)
        self._step(item, POINTS_STEP)
        self.assertEqual(self._points("Ava"), 4)
        # Stepping again, or running the award again, changes nothing.
        self._step(item, POINTS_STEP)
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
        self._step(item, POINTS_STEP)
        self.assertEqual(self._points("Ava"), 8)
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


class StepRouteTests(StepMixin, ChallengeHarness):
    """The reveal step: closed challenges only, never backwards, clamped."""

    def test_open_challenge_refuses_a_step(self) -> None:
        item = self._challenge()
        self.assertEqual(self._step(item, 1).status_code, 400)

    def test_plain_rank_refuses_a_step(self) -> None:
        row = self._rank_row()
        item = self._publish(row)
        self._close(item)
        self.assertEqual(self._step(item, 1).status_code, 400)

    def test_step_is_monotonic_and_clamped(self) -> None:
        item = self._challenge()
        self._close(item)
        self.assertEqual(self._step(item, 3).get_json()["step"], 3)
        self.assertEqual(self._step(item, 1).get_json()["step"], 3)  # never backwards
        self.assertEqual(self._step(item, 99).get_json()["step"], PODIUM_STEP)
        self.assertEqual(self._view(item)["race"]["results"]["step"], PODIUM_STEP)

    def test_missing_step_is_rejected(self) -> None:
        item = self._challenge()
        self._close(item)
        rv = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{item['id']}/race-step", json={}
        )
        self.assertEqual(rv.status_code, 400)


class ResultsBlockTests(StepMixin, ChallengeHarness):
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
        # Close shows the spots only: points and podium wait for the projector.
        self.assertEqual((res["right"], res["total"], res["points"]), (2, 4, None))
        self.assertEqual((res["podium"], res["on_podium"]), ([], False))
        self.assertEqual((res["show_points"], res["show_podium"]), (False, False))
        self._step(item, POINTS_STEP)
        res = self._card("Ava", item)["race"]["results"]
        self.assertEqual((res["points"], res["podium"], res["show_points"]), (4, [], True))
        self._step(item, PODIUM_STEP)
        res = self._card("Ava", item)["race"]["results"]
        self.assertTrue(res["show_podium"])
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


class FromDraftTests(StepMixin, ChallengeHarness):
    """Teams not locked in at Close are marked as scored from their draft."""

    def test_from_draft_only_for_unlocked_scored_teams(self) -> None:
        item = self._challenge()
        self._lock_team(item, ("Ava", "Cy", "Eli"), KEY_IDS)
        self._order("Ben", item, SWAP)
        self._close(item)
        ava = self._card("Ava", item)["race"]["results"]
        ben = self._card("Ben", item)["race"]["results"]
        self.assertEqual((ava["locked"], ava["from_draft"]), (True, False))
        self.assertEqual((ben["locked"], ben["from_draft"]), (False, True))
        teams = {t["team_id"]: t for t in self._view(item)["race"]["results"]["teams"]}
        self.assertTrue(teams[self._team("Ava", item)]["locked"])
        self.assertFalse(teams[self._team("Ben", item)]["locked"])

    def test_team_that_placed_nothing_is_not_from_draft(self) -> None:
        item = self._challenge()
        self._order("Ava", item, KEY_IDS)
        self._close(item)
        ben = self._card("Ben", item)["race"]["results"]
        self.assertFalse(ben["from_draft"])


class ResultsClientTests(unittest.TestCase):
    """Node harness for the results views (projector + phone)."""

    def test_teacher_next_tells_the_server_the_step(self) -> None:
        text = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("/race-step`", text)
        self.assertIn("void postRankRaceStep(itemId, next);", text)
        # A reload resumes at the server's step rather than at row 0.
        self.assertIn("step: raceStepFor(liveItemId, race.results),", text)

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

#!/usr/bin/env python3
"""MCK-185: teacher Scoring and coloured results on group answer-order ranks.

A group rank with an answer order (3+ spots) that is not a Team challenge,
Rank together or Take turns, gets the MCK-171 results at Close: spot rows,
then a podium ranked by right spots (no Points step, nothing paid). The
teacher awards each group by hand with the Class list team chips; the award
is the same ``game.award_points`` team award, so it reaches the tally the
same way. A Team challenge keeps its own 2-per-spot payout and refuses a
manual award (no double scoring).

Two teams of three: Ava + Cy + Eli, Ben + Dee + Fay. Answer order:
o3, o1, o4, o2.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from typing import Any

import rank_challenge
from test_rank_challenge_mck171 import KEY_IDS, ChallengeHarness
from test_rank_challenge_results_mck171 import StepMixin

LMS_DIR = Path(__file__).resolve().parent
STATIC = LMS_DIR / "static"
#: First two swapped: 2 of 4 right.
SWAP = ["o1", "o3", "o4", "o2"]
TEAM_A = ("Ava", "Cy", "Eli")
TEAM_B = ("Ben", "Dee", "Fay")


class SpotsRuleTests(unittest.TestCase):
    """Pure step and podium rules for the spots results."""

    def test_steps_skip_points(self) -> None:
        self.assertEqual(rank_challenge.points_step(4), 5)
        self.assertEqual(rank_challenge.podium_step(4), 6)
        self.assertIsNone(rank_challenge.points_step(4, with_points=False))
        self.assertEqual(rank_challenge.podium_step(4, with_points=False), 5)

    def test_podium_by_right_spots_drops_groups_that_sent_nothing(self) -> None:
        def team(tid: int, name: str, right: int, scored: bool = True) -> dict[str, Any]:
            return {"team_id": tid, "team_name": name, "right": right, "points": 0, "scored": scored}

        board = rank_challenge.podium(
            [
                team(1, "Tangents", 2),
                team(2, "Secants", 4),
                team(3, "Cosines", 2),
                team(4, "Radians", 1),
                team(5, "Vectors", 0),
                team(6, "Sines", 0, scored=False),
            ],
            by="right",
            drop_unscored=True,
        )
        self.assertEqual(
            board["steps"],
            [
                {"step": 1, "points": 4, "right": 4, "team_ids": [2]},
                {"step": 2, "points": 2, "right": 2, "team_ids": [3, 1]},
                {"step": 3, "points": 1, "right": 1, "team_ids": [4]},
            ],
        )
        self.assertEqual(board["others"], [5])  # Sines sent nothing: off the board

    def test_challenge_payout_switch_is_one_constant(self) -> None:
        self.assertTrue(rank_challenge.CHALLENGE_AUTO_PAYS)
        self.assertEqual(rank_challenge.MIN_KEY_SPOTS, 3)


class SpotsHarness(StepMixin, ChallengeHarness):
    """Publish answer-order ranks without the Team challenge toggle."""

    def _spots(self, *, mode: str = "together") -> dict[str, Any]:
        row = self._rank_row()
        if mode != "together":
            rv = self._settings(row, {"group_rank_mode": mode})
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        return self._publish(row)

    def _submit(self, item: dict[str, Any], name: str, order: list[str]) -> None:
        rv = self._post(name, item, "group-submit", {"order": order})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))

    def _turns(self, item: dict[str, Any], names: tuple[str, ...], order: list[str]) -> None:
        """Place ``order`` one spot at a time; whoever's turn it is places."""
        for token in order:
            for name in names:
                rv = self._post(name, item, "rank-turn", {"option_id": token})
                if rv.status_code == 200:
                    break
            else:
                self.fail(f"no one could place {token}: {rv.get_data(as_text=True)[:200]}")

    def _team_score(self, team_id: int) -> float:
        state = self.client.get(f"/api/classes/{self.class_id}/game").get_json()
        team = next(t for t in state["teams"] if int(t["id"]) == int(team_id))
        return float(team["score"])

    def _session_points(self, name: str) -> float:
        """The student's credited game points (what the Class list shows)."""
        state = self.client.get(f"/api/classes/{self.class_id}/game").get_json()
        for team in state.get("teams") or []:
            for member in team.get("members") or []:
                if int(member["id"]) == self.ids[name]:
                    return float(member.get("session_points") or 0)
        return 0.0

    def _award(self, item: dict[str, Any], team_id: int, amount: int, rule: str | None):
        return self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{item['id']}/rank-points",
            json={"team_id": team_id, "amount": amount, "team_rule": rule},
        )

    def _results(self, item: dict[str, Any]) -> dict[str, Any]:
        return self._view(item)["race"]["results"]

    def _student_texts(self, item: dict[str, Any]) -> dict[str, str]:
        out = {}
        for name in ("Ava", "Ben"):
            out[name] = json.dumps(self._card(name, item), default=str)
            state = self.students[name].get("/api/student/live-state")
            if state.status_code == 200:
                out[f"{name}-state"] = state.get_data(as_text=True)
        return out


class SpotsResultsTests(SpotsHarness):
    """Coloured results for a non-challenge answer-order rank."""

    def test_take_turns_gets_rows_then_podium_and_pays_nothing(self) -> None:
        item = self._spots(mode="turns")
        self.assertNotIn("race", self._view(item))  # open card unchanged
        self._turns(item, TEAM_A, KEY_IDS)
        self._turns(item, TEAM_B, SWAP)
        self._close(item)
        view = self._view(item)
        race = view["race"]
        self.assertFalse(race["challenge"])
        self.assertEqual(race["mode"], "turns")
        res = race["results"]
        self.assertFalse(res["challenge"])
        self.assertFalse(res["pays"])
        self.assertIsNone(res["points_step"])
        self.assertEqual(res["podium_step"], 5)
        by_team = {t["team_id"]: t for t in res["teams"]}
        a, b = self._team("Ava", item), self._team("Ben", item)
        # Hint source: right spots against the group's sent order.
        self.assertEqual((by_team[a]["right"], by_team[a]["total"]), (4, 4))
        self.assertEqual((by_team[b]["right"], by_team[b]["total"]), (2, 4))
        self.assertEqual(by_team[a]["points"], 0)
        self.assertEqual(
            res["podium"]["steps"],
            [
                {"step": 1, "points": 4, "right": 4, "team_ids": [a]},
                {"step": 2, "points": 2, "right": 2, "team_ids": [b]},
            ],
        )
        # Rows 1..4, then the podium at 5; there is no step 6.
        rv = self._step(item, 5)
        self.assertEqual(rv.get_json()["step"], 5)
        self.assertEqual(self._post_step(item, 6).status_code, 409)
        # No auto-award at any step.
        for name in TEAM_A + TEAM_B:
            self.assertEqual(self._points(name), 0, name)
        self.assertIsNone(self._row(item, "Ava").get("awarded_points"))

    def test_rank_together_without_challenge_scores_the_sent_order(self) -> None:
        item = self._spots()
        self._submit(item, "Ava", SWAP)
        self._close(item)
        res = self._results(item)
        by_team = {t["team_id"]: t for t in res["teams"]}
        a, b = self._team("Ava", item), self._team("Ben", item)
        self.assertTrue(by_team[a]["scored"])
        self.assertEqual(by_team[a]["right"], 2)
        self.assertFalse(by_team[b]["scored"])  # sent nothing: "No order sent"
        self.assertEqual(res["podium"]["others"], [])  # and off the board

    def test_opinion_rank_unchanged(self) -> None:
        item = self._publish(self._rank_row(key=False))
        self._submit(item, "Ava", SWAP)
        self._close(item)
        self.assertNotIn("race", self._view(item))


class ManualAwardTests(SpotsHarness):
    """The teacher's award: same team award as the Class list, manual only."""

    def _check_award_matches_class_list(self, item: dict[str, Any]) -> None:
        a, b = self._team("Ava", item), self._team("Ben", item)
        before = self._team_score(a)
        rv = self._award(item, a, 5, "each_member")
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        body = rv.get_json()
        self.assertEqual(body["awarded_points"], 5)
        self.assertIn("teams", body["game"])  # the game state the tally paints
        rank_delta = self._team_score(a) - before
        rank_points = [self._session_points(n) for n in TEAM_A]
        # The Class list chip (used for an MC award too): same rule, other team.
        before_b = self._team_score(b)
        rv = self.client.post(
            f"/api/classes/{self.class_id}/game/score",
            json={"kind": "team", "id": b, "amount": 5, "team_rule": "each_member"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        self.assertEqual(rank_delta, self._team_score(b) - before_b)
        self.assertEqual(rank_points, [self._session_points(n) for n in TEAM_B])
        self.assertEqual(rank_points, [5, 5, 5])
        # "+n" on the row, and a second award adds up (like Responses).
        self.assertEqual(self._award(item, a, -5, "team_only").status_code, 200)
        team = next(t for t in self._results(item)["teams"] if t["team_id"] == a)
        self.assertEqual(team["awarded_points"], 0)
        self.assertEqual(self._award(item, a, 1, "split_members").status_code, 200)
        team = next(t for t in self._results(item)["teams"] if t["team_id"] == a)
        self.assertEqual(team["awarded_points"], 1)

    def test_take_turns_award_updates_the_tally_like_the_class_list(self) -> None:
        item = self._spots(mode="turns")
        self._turns(item, TEAM_A, KEY_IDS)
        self._turns(item, TEAM_B, SWAP)
        self._close(item)
        self._step(item, 5)
        self._check_award_matches_class_list(item)

    def test_rank_together_award_updates_the_tally_like_the_class_list(self) -> None:
        item = self._spots()
        self._submit(item, "Ava", KEY_IDS)
        self._submit(item, "Ben", SWAP)
        self._close(item)
        self._check_award_matches_class_list(item)

    def test_award_refused_before_reveal_without_order_or_rule(self) -> None:
        item = self._spots()
        self._submit(item, "Ava", KEY_IDS)
        a, b = self._team("Ava", item), self._team("Ben", item)
        self.assertEqual(self._award(item, a, 5, "each_member").status_code, 400)  # still open
        self._close(item)
        rv = self._award(item, b, 5, "each_member")
        self.assertEqual(rv.status_code, 400)
        self.assertEqual(rv.get_json()["error"], "No order sent")
        self.assertEqual(self._award(item, a, 5, None).status_code, 400)  # chips always pick a rule
        self.assertEqual(sum(self._session_points(n) for n in TEAM_A + TEAM_B), 0)

    def test_team_challenge_keeps_its_payout_and_refuses_a_manual_award(self) -> None:
        for mode in ("together", "turns"):
            with self.subTest(mode=mode):
                item = self._challenge(mode=mode)
                if mode == "turns":
                    self._turns(item, TEAM_A, KEY_IDS)
                else:
                    self._lock_team(item, TEAM_A, KEY_IDS)
                self._close(item)
                res = self._results(item)
                self.assertTrue(res["challenge"])
                self.assertTrue(res["pays"])
                self.assertEqual(res["points_step"], 5)
                self.assertNotIn("awarded_points", res["teams"][0])
                a = self._team("Ava", item)
                rv = self._award(item, a, 5, "each_member")
                self.assertEqual(rv.status_code, 400)
                self.assertEqual(rv.get_json()["error"], "A Team challenge pays its own points.")
                before = self._points("Ava")
                self._step(item, 5)  # 171's own points step still pays 2 per spot
                self.assertEqual(self._points("Ava") - before, 8)

    def test_opinion_rank_refuses(self) -> None:
        item = self._publish(self._rank_row(key=False))
        self._submit(item, "Ava", SWAP)
        self._close(item)
        rv = self._award(item, self._team("Ava", item), 5, "each_member")
        self.assertEqual(rv.status_code, 400)


class StudentLeakTests(SpotsHarness):
    """Phones: no key, score or other team's order before Close."""

    def test_no_key_or_results_before_close_then_own_results_without_points(self) -> None:
        for mode in ("together", "turns"):
            with self.subTest(mode=mode):
                item = self._spots(mode=mode)
                if mode == "turns":
                    self._turns(item, TEAM_A, KEY_IDS)
                    self._turns(item, TEAM_B, SWAP)
                else:
                    self._submit(item, "Ava", KEY_IDS)
                    self._submit(item, "Ben", SWAP)
                for where, text in self._student_texts(item).items():
                    self.assertNotIn("rank_key", text, where)
                    self.assertNotIn('"race"', text, where)
                    self.assertNotIn("spots right", text, where)
                    self.assertNotIn('"results"', text.replace('"results": null', ""), where)
                card = self._card("Ava", item)
                self.assertNotIn("race", card)
                self._close(item)
                card = self._card("Ben", item)
                res = card["race"]["results"]
                self.assertFalse(res["challenge"])
                self.assertEqual((res["right"], res["total"]), (2, 4))
                self.assertIsNone(res["points"])
                self.assertFalse(res["show_points"])
                self.assertFalse(res["from_draft"])
                self.assertEqual(res["podium"], [])  # podium waits for the projector
                text = json.dumps(card, default=str)
                self.assertNotIn("awarded_points", text)
                self.assertNotIn("order", json.dumps(card["race"], default=str))  # no other team's order
                self._step(item, 5)
                res = self._card("Ben", item)["race"]["results"]
                self.assertTrue(res["show_podium"])
                self.assertEqual([s["step"] for s in res["podium"]], [1, 2])
                self.assertIsNone(res["points"])

    def test_results_off_keeps_the_plain_closed_card(self) -> None:
        item = self._spots()
        rv = self.client.patch(
            f"/api/live-sessions/{self.session_id}/items/{item['id']}/settings",
            json={"show_live_results": False},
        )
        self.assertIn(rv.status_code, (200, 400))
        if rv.status_code != 200:
            self.skipTest("show_live_results is not a settings field here")
        self._submit(item, "Ava", KEY_IDS)
        self._close(item)
        self.assertNotIn("race", self._card("Ava", item))


class ViewTests(unittest.TestCase):
    """Node harness for the view builders, and the staff shell wiring."""

    def test_view_node_harness(self) -> None:
        proc = subprocess.run(
            ["node", str(STATIC / "rank_scoring_mck185.test.mjs")],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("ok", proc.stdout)

    def test_staff_shell_reuses_team_controls(self) -> None:
        staff = (STATIC / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("function teamControls(teamId, pending = pendingTeam)", staff)
        self.assertIn("controls: (teamId) => teamControls(teamId, pending)", staff)
        self.assertIn("/rank-points`", staff)
        self.assertIn("hideKey: hideKeyOn()", staff)
        self.assertIn("scoringHtml: rankScoringRowsHtml(race.results, liveItemId)", staff)
        css = (STATIC / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn(".live-question-list.is-key-hidden .rank-scoring-hint:not(.is-none)", css)


if __name__ == "__main__":
    unittest.main()

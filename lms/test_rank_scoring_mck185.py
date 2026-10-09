#!/usr/bin/env python3
"""MCK-185: coloured results, "Score teams" and the full-order line on
group answer-order ranks.

Every group rank with an answer order (3+ spots), Team challenge, Rank
together or Take turns, gets the MCK-171 results at Close: spot rows, then a
podium ranked by right spots. Nothing is awarded automatically (Shawn,
option B). The teacher scores teams in the "Score teams" pop-up (default 2
per right spot); each Assign is a normal team award (``game.award_points``,
the "Give as" rule) to members seen while the question was open, and
re-assigning replaces the earlier award. After Close & reveal, every phone
shows "{team} put every item in the right order." when a team's final order
was all right.

Two teams of three: Ava + Cy + Eli, Ben + Dee + Fay. Answer order:
o3, o1, o4, o2.
"""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

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
        self.assertFalse(rank_challenge.CHALLENGE_AUTO_PAYS)  # option B: all manual
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


class ScoreTeamsTests(SpotsHarness):
    """The "Score teams" pop-up: defaults, Assign, Assign all, replace."""

    def _assign(self, item: dict[str, Any], awards: list[tuple[int, int]], rule: str | None = "each_member"):
        return self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{item['id']}/rank-points",
            json={"awards": [{"team_id": t, "points": p} for t, p in awards], "team_rule": rule},
        )

    def _events(self) -> int:
        with self.school.game._lock:
            row = self.school.game.conn.execute("SELECT COUNT(*) AS n FROM point_events").fetchone()
        return int(row["n"])

    def _turns_two_teams(self) -> dict[str, Any]:
        item = self._spots(mode="turns")
        self._turns(item, TEAM_A, KEY_IDS)
        self._turns(item, TEAM_B, SWAP)
        self._close(item)
        return item

    def test_defaults_are_two_per_spot_and_nothing_is_given(self) -> None:
        item = self._turns_two_teams()
        res = self._results(item)
        teams = {t["team_id"]: t for t in res["teams"]}
        a, b = self._team("Ava", item), self._team("Ben", item)
        self.assertEqual((teams[a]["auto"], teams[b]["auto"]), (8, 4))
        self.assertEqual((teams[a]["assigned"], teams[b]["assigned"]), (None, None))
        self.assertTrue(teams[a]["present"] and teams[b]["present"])
        self._step(item, 5)  # the podium: still nothing given
        self.assertEqual(sum(self._session_points(n) for n in TEAM_A + TEAM_B), 0)
        self.assertEqual(self._events(), 0)

    def test_assign_writes_once_and_a_second_assign_is_a_no_op(self) -> None:
        item = self._turns_two_teams()
        a = self._team("Ava", item)
        before = self._team_score(a)
        rv = self._assign(item, [(a, 8)])
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        body = rv.get_json()
        self.assertEqual(body["teams"], [{"team_id": a, "points": 8, "team_rule": "each_member", "changed": True}])
        self.assertIn("teams", body["game"])  # the game state the tally paints
        self.assertEqual([self._session_points(n) for n in TEAM_A], [8, 8, 8])
        team = next(t for t in body["results"]["teams"] if t["team_id"] == a)
        self.assertEqual(team["assigned"], {"points": 8, "team_rule": "each_member"})
        delta, events = self._team_score(a) - before, self._events()
        # Same as the Class list chip (Each member +8) on the other team.
        b = self._team("Ben", item)
        before_b = self._team_score(b)
        self.client.post(
            f"/api/classes/{self.class_id}/game/score",
            json={"kind": "team", "id": b, "amount": 8, "team_rule": "each_member"},
        )
        self.assertEqual(delta, self._team_score(b) - before_b)
        events = self._events()
        rv = self._assign(item, [(a, 8)])
        self.assertEqual(rv.get_json()["teams"][0]["changed"], False)
        self.assertEqual(self._events(), events)
        self.assertEqual([self._session_points(n) for n in TEAM_A], [8, 8, 8])

    def test_member_who_moves_mid_award_is_never_debited(self) -> None:
        """MCK-192 (a): the stored members are the ones actually credited."""
        item = self._turns_two_teams()
        a, b = self._team("Ava", item), self._team("Ben", item)
        mover = TEAM_A[-1]
        school = self.school
        real = school._teammate_ids_for_class
        fired = []

        def racy(class_id: int, team_id: int):
            out = real(class_id, team_id)
            if team_id == a and not fired:
                fired.append(1)
                game_id = int(school.game._game_row(self.class_id)["id"])
                with school.game._lock:
                    school.game.conn.execute(
                        "UPDATE game_memberships SET team_id = ? WHERE game_id = ? AND student_id = ?",
                        (b, game_id, self.ids[mover]),
                    )
                    school.game.conn.commit()
                cache = getattr(school.game, "_team_index_cache", None)
                if cache is not None:
                    cache.clear()
            return out

        school._teammate_ids_for_class = racy
        try:
            self.assertEqual(self._assign(item, [(a, 5)]).status_code, 200)
        finally:
            school._teammate_ids_for_class = real
        self.assertTrue(fired)
        self.assertEqual(self._session_points(mover), 0)
        self._assign(item, [(a, 7)])
        self.assertEqual(self._session_points(mover), 0)
        self._assign(item, [(a, 0)])
        self.assertEqual(self._session_points(mover), 0)
        self.assertEqual([self._session_points(n) for n in TEAM_A[:-1]], [0] * (len(TEAM_A) - 1))

    def test_reassign_replaces_and_never_stacks(self) -> None:
        item = self._turns_two_teams()
        a = self._team("Ava", item)
        start = self._team_score(a)
        self.assertEqual(self._assign(item, [(a, 8)]).status_code, 200)
        first = self._team_score(a) - start
        self.assertEqual(self._assign(item, [(a, 5)]).status_code, 200)  # Update
        self.assertEqual([self._session_points(n) for n in TEAM_A], [5, 5, 5])
        self.assertAlmostEqual(self._team_score(a) - start, first * 5 / 8)
        self.assertEqual(self._assign(item, [(a, 6)], "split_members").status_code, 200)
        self.assertEqual([self._session_points(n) for n in TEAM_A], [2, 2, 2])
        self.assertAlmostEqual(self._team_score(a) - start, 6)
        self.assertEqual(self._assign(item, [(a, 3)], "team_only").status_code, 200)
        self.assertEqual([self._session_points(n) for n in TEAM_A], [0, 0, 0])
        self.assertAlmostEqual(self._team_score(a) - start, 3)
        self.assertEqual(self._assign(item, [(a, 0)]).status_code, 200)  # back to nothing
        self.assertEqual([self._session_points(n) for n in TEAM_A], [0, 0, 0])
        self.assertAlmostEqual(self._team_score(a) - start, 0)
        team = next(t for t in self._results(item)["teams"] if t["team_id"] == a)
        self.assertEqual(team["assigned"], {"points": 0, "team_rule": "each_member"})

    def test_assign_all_gives_every_listed_team_once(self) -> None:
        item = self._turns_two_teams()
        a, b = self._team("Ava", item), self._team("Ben", item)
        self.assertEqual(self._assign(item, [(a, 8)]).status_code, 200)
        rv = self._assign(item, [(a, 8), (b, 4)])
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        self.assertEqual([t["changed"] for t in rv.get_json()["teams"]], [False, True])
        self.assertEqual([self._session_points(n) for n in TEAM_A], [8, 8, 8])
        self.assertEqual([self._session_points(n) for n in TEAM_B], [4, 4, 4])

    def test_member_not_seen_while_open_gets_nothing(self) -> None:
        self._leave("Eli")
        item = self._spots()
        self._submit(item, "Ava", KEY_IDS)
        self._view(item)
        self._card("Cy", item)
        self._close(item)
        a = self._team("Ava", item)
        self.assertEqual(self._assign(item, [(a, 8)]).status_code, 200)
        self.assertEqual(
            (self._session_points("Ava"), self._session_points("Cy"), self._session_points("Eli")), (8, 8, 0)
        )

    def test_refusals(self) -> None:
        item = self._spots()
        self._submit(item, "Ava", KEY_IDS)
        a, b = self._team("Ava", item), self._team("Ben", item)
        self.assertEqual(self._assign(item, [(a, 8)]).status_code, 400)  # still open
        self._close(item)
        rv = self._assign(item, [(b, 4)])
        self.assertEqual((rv.status_code, rv.get_json()["error"]), (400, "No order sent"))
        self.assertEqual(self._assign(item, [(a, 0)]).status_code, 200)  # 0 gives nothing
        self.assertEqual(self._assign(item, [(a, 8)], "bonus").status_code, 400)
        self.assertEqual(self._assign(item, []).status_code, 400)
        self.assertEqual(sum(self._session_points(n) for n in TEAM_A + TEAM_B), 0)
        opinion = self._publish(self._rank_row(key=False))
        self._submit(opinion, "Ava", SWAP)
        self._close(opinion)
        self.assertEqual(self._assign(opinion, [(a, 5)]).status_code, 400)

    def _post_raw(self, item: dict[str, Any], body: Any):
        return self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{item['id']}/rank-points", json=body
        )

    def test_malformed_bodies_are_400_never_500(self) -> None:
        """Gate LOW on f0a15be: objects or ints for awards used to 500."""
        item = self._turns_two_teams()
        a = self._team("Ava", item)
        for body in (
            {"awards": {"team_id": a, "points": 4}},
            {"awards": [a, 4]},
            {"awards": [{"team_id": a, "points": 4}], "team_rule": 3},
            [{"team_id": a, "points": 4}],
            "awards",
            {"awards": [{"team_id": True, "points": 4}]},
            {"awards": [{"team_id": a, "points": True}]},
            {"awards": [{"team_id": a, "points": "lots"}]},
            {"awards": [{"team_id": a}]},
        ):
            with self.subTest(body=body):
                self.assertEqual(self._post_raw(item, body).status_code, 400)
        self.assertEqual(self._events(), 0)

    def test_points_are_rounded_and_capped_at_999(self) -> None:
        item = self._turns_two_teams()
        a = self._team("Ava", item)
        for sent, kept in ((5.7, 6), ("7", 7), (0, 0), (5000, 999), (4.0, 4)):
            with self.subTest(sent=sent):
                rv = self._post_raw(item, {"awards": [{"team_id": a, "points": sent}], "team_rule": "each_member"})
                self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
                self.assertEqual(rv.get_json()["teams"][0]["points"], kept)
                self.assertEqual([self._session_points(n) for n in TEAM_A], [kept] * 3)
        self.assertEqual(rank_challenge.clamp_award_points(float("nan")), None)

    def test_negative_points_are_refused_and_keep_the_award(self) -> None:
        """Gate LOW-2 on 2cc6a9e: a negative used to clamp to 0 and wipe the award."""
        item = self._turns_two_teams()
        a = self._team("Ava", item)
        self.assertEqual(self._assign(item, [(a, 8)]).status_code, 200)
        events = self._events()
        for sent in (-3, "-5", -0.4, -1000):
            with self.subTest(sent=sent):
                rv = self._post_raw(item, {"awards": [{"team_id": a, "points": sent}], "team_rule": "each_member"})
                self.assertEqual(rv.status_code, 400, rv.get_data(as_text=True))
                self.assertEqual(rv.get_json()["error"], "Points can't be negative")
        self.assertEqual(self._events(), events)
        self.assertEqual([self._session_points(n) for n in TEAM_A], [8, 8, 8])
        team = next(t for t in self._results(item)["teams"] if t["team_id"] == a)
        self.assertEqual(team["assigned"], {"points": 8, "team_rule": "each_member"})
        self.assertIsNone(rank_challenge.clamp_award_points(-1))
        self.assertEqual(rank_challenge.award_points_problem("abc"), "Points must be a number")
        self.assertIsNone(rank_challenge.award_points_problem(12))

    def test_assign_all_is_all_or_nothing(self) -> None:
        """Gate LOW-1 on 2cc6a9e: if one team's award fails, no team changes."""
        item = self._turns_two_teams()
        a, b = self._team("Ava", item), self._team("Ben", item)
        self.assertEqual(self._assign(item, [(a, 8)]).status_code, 200)
        start_a, start_b, events = self._team_score(a), self._team_score(b), self._events()
        real = self.school.game.award_points

        def fail_team_b(*args: Any, **kwargs: Any):
            if int(kwargs.get("target_id") or 0) == b:
                raise ValueError("Scoring is closed")
            return real(*args, **kwargs)

        with mock.patch.object(self.school.game, "award_points", side_effect=fail_team_b):
            rv = self._assign(item, [(a, 5), (b, 4)])
        self.assertEqual(rv.status_code, 400, rv.get_data(as_text=True))
        self.assertFalse(self.school.game.conn.in_transaction)
        self.assertEqual((self._team_score(a), self._team_score(b)), (start_a, start_b))
        self.assertEqual(self._events(), events)
        self.assertEqual([self._session_points(n) for n in TEAM_A], [8, 8, 8])
        self.assertEqual([self._session_points(n) for n in TEAM_B], [0, 0, 0])
        teams = {t["team_id"]: t for t in self._results(item)["teams"]}
        self.assertEqual(teams[a]["assigned"], {"points": 8, "team_rule": "each_member"})
        self.assertIsNone(teams[b]["assigned"])
        # Nothing was left half-done: the same request now goes through.
        self.assertEqual(self._assign(item, [(a, 5), (b, 4)]).status_code, 200)
        self.assertEqual([self._session_points(n) for n in TEAM_A], [5, 5, 5])
        self.assertEqual([self._session_points(n) for n in TEAM_B], [4, 4, 4])

    def test_reassign_is_one_transaction(self) -> None:
        """Gate LOW on f0a15be: the reversal used to commit before the new
        award. If the new award fails, the claim and the reversal roll back."""
        item = self._turns_two_teams()
        a = self._team("Ava", item)
        self.assertEqual(self._assign(item, [(a, 8)]).status_code, 200)
        start, events = self._team_score(a), self._events()
        real = self.school.game.award_points

        def fail_forward(*args: Any, **kwargs: Any):
            if not kwargs.get("reverse"):
                raise ValueError("Scoring is closed")
            return real(*args, **kwargs)

        with mock.patch.object(self.school.game, "award_points", side_effect=fail_forward):
            self.assertEqual(self._assign(item, [(a, 5)]).status_code, 400)
        self.assertEqual(self._team_score(a), start)
        self.assertEqual(self._events(), events)
        self.assertEqual([self._session_points(n) for n in TEAM_A], [8, 8, 8])
        team = next(t for t in self._results(item)["teams"] if t["team_id"] == a)
        self.assertEqual(team["assigned"], {"points": 8, "team_rule": "each_member"})
        self.assertEqual(self._assign(item, [(a, 5)]).status_code, 200)  # and it still works
        self.assertEqual([self._session_points(n) for n in TEAM_A], [5, 5, 5])

    def test_staff_state_totals_carry_a_game_version(self) -> None:
        """Gate LOW-1: the page drops a full poll older than an Assign."""
        item = self._turns_two_teams()
        a = self._team("Ava", item)
        before = self.school._assemble_live_session_state(self.session_id)
        ver = before["game_points_ver"]
        self.assertEqual(set(ver), {"game_id", "game_key", "seq"})
        self.assertEqual(self._assign(item, [(a, 8)]).status_code, 200)
        after = self.school._assemble_live_session_state(self.session_id)
        self.assertEqual(after["game_points_ver"]["game_id"], ver["game_id"])
        self.assertGreater(after["game_points_ver"]["seq"], ver["seq"])
        self.assertEqual(after["game_points"][str(self.ids["Ava"])], 8)
        self.assertNotIn("game_points_ver", self.school._assemble_live_session_state(self.session_id, light=True))

    def test_a_new_game_after_quit_gets_a_new_version_and_zeroed_totals(self) -> None:
        """Gate LOW-3 on 2cc6a9e: Quit (keep the live class) + Begin re-uses
        the game id with seq 0. The version's game_key changes, and every
        student is listed (zeros too), so the page takes the new totals."""
        item = self._turns_two_teams()
        a = self._team("Ava", item)
        self.assertEqual(self._assign(item, [(a, 8)]).status_code, 200)
        old = self.school._assemble_live_session_state(self.session_id)
        self.assertEqual(old["game_points"][str(self.ids["Ava"])], 8)
        self.assertEqual(old["game_points"][str(self.ids["Ben"])], 0)  # zeros are listed
        quit_rv = self.client.post(
            f"/api/classes/{self.class_id}/game/cancel", json={"preserve_live_session": True}
        )
        self.assertEqual(quit_rv.status_code, 200, quit_rv.get_data(as_text=True))
        between = self.school._assemble_live_session_state(self.session_id)
        self.assertIn("game_points_ver", between)
        self.assertIsNone(between["game_points_ver"])  # no open game: the page forgets the old one
        # MCK-192 (e): no wait. Quit + Begin in the same second still gets a
        # new game_key (games.created_at keeps microseconds).
        begun = self.client.post(f"/api/classes/{self.class_id}/begin", json={})
        self.assertEqual(begun.status_code, 200, begun.get_data(as_text=True))
        new = self.school._assemble_live_session_state(self.session_id)
        ver_old, ver_new = old["game_points_ver"], new["game_points_ver"]
        self.assertNotEqual(ver_new["game_key"], ver_old["game_key"])
        self.assertLess(ver_new["seq"], ver_old["seq"])
        self.assertEqual(new["game_points"][str(self.ids["Ava"])], 0)
        # The key matches what the page builds from a game state's game block.
        game = self.client.get(f"/api/classes/{self.class_id}/game").get_json()["game"]
        self.assertEqual(ver_new["game_key"], f"{game['id']}|{game['session_id']}|{game['created_at']}")

    def test_a_failed_points_read_sends_no_totals(self) -> None:
        """A failed read must not look like "no game" (that resets the page)."""
        with mock.patch.object(self.school.game, "game_state", side_effect=RuntimeError("busy")):
            state = self.school._assemble_live_session_state(self.session_id)
        self.assertNotIn("game_points", state)
        self.assertNotIn("game_points_ver", state)

    def test_no_auto_award_through_end_of_class(self) -> None:
        """Closing the pop-up (or never opening it) gives nothing."""
        item = self._turns_two_teams()
        self._step(item, 5)
        self.client.post(f"/staff/class/{self.class_id}/end-live", data={})
        self.assertEqual(self._events(), 0)

    def test_team_challenge_is_scored_the_same_way(self) -> None:
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
                self.assertFalse(res["pays"])
                # No Points step any more: rows, then the podium by spots.
                self.assertEqual((res["points_step"], res["podium_step"]), (None, 5))
                a = self._team("Ava", item)
                team_a = next(t for t in res["teams"] if t["team_id"] == a)
                self.assertEqual((team_a["right"], team_a["auto"], team_a["points"]), (4, 8, 0))
                before = self._points("Ava")
                self._step(item, 5)
                self.assertEqual(self._post_step(item, 6).status_code, 409)
                self.assertEqual(self._points("Ava"), before)  # nothing automatic
                card = self._card("Ava", item)["race"]["results"]
                self.assertEqual((card["points"], card["show_points"]), (None, False))
                rv = self._assign(item, [(a, 8)])
                self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
                self.assertEqual(self._session_points("Ava") - 0, self._session_points("Cy"))
                # Phones: "8 points each" once assigned (as in MCK-171).
                card = self._card("Ava", item)["race"]["results"]
                self.assertEqual((card["points"], card["show_points"]), (8, True))
                self._assign(item, [(a, 6)], "split_members")
                card = self._card("Ava", item)["race"]["results"]
                self.assertEqual(card["points"], 2)
                b = self._team("Ben", item)
                rv = self._assign(item, [(b, 5)])
                self.assertEqual((rv.status_code, rv.get_json()["error"]), (400, "No order sent"))


class FullOrderTests(SpotsHarness):
    """Class-wide "put every item in the right order" notice."""

    def _notice(self, name: str, item: dict[str, Any]) -> Any:
        return self._card(name, item).get("full_order")

    def test_default_timing_is_lock(self) -> None:
        self.assertEqual(rank_challenge.FULL_ORDER_WHEN, "lock")  # Shawn, Oct 4

    @mock.patch.object(rank_challenge, "FULL_ORDER_WHEN", "reveal")
    def test_reveal_timing_shows_only_after_close_and_only_names(self) -> None:
        item = self._spots(mode="turns")
        self._turns(item, TEAM_A, KEY_IDS)
        self._turns(item, TEAM_B, SWAP)
        self.assertIsNone(self._notice("Ava", item))
        self.assertIsNone(self._notice("Ben", item))
        self.assertNotIn("full_order", self._view(item))
        self._close(item)
        a_name = self._row(item, "Ava").get("team_name") or next(
            t["team_name"] for t in self._results(item)["teams"] if t["team_id"] == self._team("Ava", item)
        )
        mine = self._notice("Ava", item)
        self.assertEqual((mine["you"], mine["teams"]), (True, []))
        other = self._notice("Ben", item)
        self.assertFalse(other["you"])
        self.assertEqual([t["name"] for t in other["teams"]], [a_name])
        self.assertEqual(set(other["teams"][0]), {"name", "slot"})  # no order, no spots
        self.assertEqual([t["name"] for t in self._view(item)["full_order"]["teams"]], [a_name])

    def test_lock_timing_take_turns_counts_only_the_last_spot(self) -> None:
        item = self._spots(mode="turns")
        self._turns(item, TEAM_A, KEY_IDS[:-1])  # 3 of 4 placed: a draft
        self.assertIsNone(self._notice("Ben", item))
        self._turns(item, TEAM_A, KEY_IDS[-1:])  # last spot: final, all right
        self.assertEqual(len(self._notice("Ben", item)["teams"]), 1)
        self.assertTrue(self._notice("Ava", item)["you"])

    def _all_phone_states(self) -> str:
        out = []
        for name in TEAM_A + TEAM_B:
            rv = self.students[name].get("/api/student/state")
            self.assertEqual(rv.status_code, 200)
            out.append(rv.get_data(as_text=True))
        return "\n".join(out)

    def test_rank_together_resend_loop_cannot_reveal_correctness_before_close(self) -> None:
        """Gate MED-1 on f0a15be: a sent Rank together order can be changed
        and resent, so under "lock" a team could resend until the line came.
        Plain Rank together now waits for Close: no line in any phone's
        state, or the teacher view, however often a team resends."""
        self.assertEqual(rank_challenge.FULL_ORDER_WHEN, "lock")
        item = self._spots()  # Rank together, not a Team challenge
        tries = [SWAP, ["o2", "o1", "o3", "o4"], KEY_IDS, SWAP, KEY_IDS]
        for n, order in enumerate(tries):
            sender = TEAM_A[n % len(TEAM_A)]
            self._submit(item, sender, order)
            self._submit(item, "Ben", KEY_IDS if n % 2 else SWAP)
            with self.subTest(send=n):
                self.assertNotIn("full_order", self._all_phone_states())
                for name in TEAM_A + TEAM_B:
                    self.assertIsNone(self._notice(name, item))
                self.assertNotIn("full_order", self._view(item))
        self._close(item)  # the last sends were right: the line comes at Close
        self.assertTrue(self._notice("Ava", item)["you"])
        self.assertIn("full_order", self._all_phone_states())

    def test_lock_waits_for_close_with_live_results_off(self) -> None:
        row = self._rank_row()
        rv = self._settings(row, {"group_rank_mode": "turns", "show_live_results": False})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        item = self._publish(row)
        self._turns(item, TEAM_A, KEY_IDS)  # final and right
        self.assertNotIn("full_order", self._all_phone_states())
        self.assertIsNone(self._notice("Ben", item))
        self._close(item)
        self.assertEqual(len(self._notice("Ben", item)["teams"]), 1)

    def test_lock_timing_per_mode(self) -> None:
        timing = rank_challenge.full_order_timing
        self.assertEqual(timing(challenge=True, rank_mode="together", results_on=True), "lock")
        self.assertEqual(timing(challenge=False, rank_mode="turns", results_on=True), "lock")
        self.assertEqual(timing(challenge=False, rank_mode="together", results_on=True), "reveal")
        self.assertEqual(timing(challenge=True, rank_mode="turns", results_on=False), "reveal")
        self.assertEqual(timing(challenge=True, rank_mode="turns", results_on=True, when="reveal"), "reveal")

    def test_nothing_when_no_team_got_it_all(self) -> None:
        item = self._spots()
        self._submit(item, "Ava", SWAP)
        self._close(item)
        self.assertIsNone(self._notice("Ava", item))
        self.assertNotIn("full_order", self._view(item))

    def test_a_draft_never_counts(self) -> None:
        item = self._challenge()
        self._order("Ava", item, KEY_IDS)  # right, but never locked in
        self._close(item)
        self.assertIsNone(self._notice("Ben", item))
        self.assertNotIn("full_order", self._view(item))

    def test_lock_timing_shows_as_soon_as_a_final_order_is_right(self) -> None:
        with mock.patch.object(rank_challenge, "FULL_ORDER_WHEN", "lock"):
            item = self._challenge()
            self._order("Ben", item, KEY_IDS)  # a right draft: not yet
            self.assertIsNone(self._notice("Ava", item))
            self._lock_team(item, TEAM_A, KEY_IDS)
            other = self._notice("Ben", item)
            self.assertEqual(len(other["teams"]), 1)
            self.assertTrue(self._notice("Ava", item)["you"])
            self.assertIn("full_order", self._view(item))
            self._close(item)
            self.assertTrue(self._notice("Ava", item)["you"])

    def test_timing_rule(self) -> None:
        self.assertFalse(rank_challenge.full_order_visible("active", "reveal"))
        self.assertTrue(rank_challenge.full_order_visible("closed", "reveal"))
        self.assertTrue(rank_challenge.full_order_visible("active", "lock"))
        self.assertFalse(rank_challenge.full_order_visible("ended", "lock"))
        self.assertEqual(rank_challenge.FULL_ORDER_TIMINGS, ("reveal", "lock"))


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


class Mck192StaffPollTests(unittest.TestCase):
    """MCK-192 (b)(c)(d): staff_ap.js source checks."""

    src = (Path(__file__).resolve().parent / "static" / "staff_ap.js").read_text(encoding="utf-8")

    def test_state_poll_has_a_timeout_and_retry_cuts_a_hung_poll(self) -> None:
        self.assertIn("const STAFF_STATE_TIMEOUT_MS = 15000;", self.src)
        self.assertIn("window.setTimeout(() => pollAbort.abort(), STAFF_STATE_TIMEOUT_MS)", self.src)
        self.assertIn("api(`/api/live-sessions/${id}/state${qs}`, fetchOpts)", self.src)
        retry = self.src[self.src.index("function reissueLiveStateOnce()"):]
        retry = retry[: retry.index("\n}\n")]
        self.assertIn("sessionPollAbort.abort();", retry)
        self.assertIn("sessionPollQueued = {", retry)

    def test_quit_copy_and_no_dead_helper(self) -> None:
        self.assertNotIn("Scores already logged stay registered", self.src)
        self.assertIn("Quit scoring? This game's points won't be saved.", self.src)
        self.assertNotIn("clearStuckGameForLiveSession", self.src)


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

    def test_staff_shell_wires_score_teams(self) -> None:
        staff = (STATIC / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("/rank-points`", staff)
        self.assertIn("openRankScoreDialog(Number(scoreOpen.dataset.rankScoreOpen)", staff)
        self.assertIn("// Closing awards nothing.", staff)
        self.assertNotIn("dialog.showModal();\n  rankScore", staff)
        # Never auto-opened: only the Score teams button opens it.
        self.assertEqual(staff.count("openRankScoreDialog("), 2)  # definition + the button
        self.assertNotIn("rankScoringRowsHtml", staff)
        self.assertIn("function teamControls(teamId) {", staff)  # Class list chips untouched
        template = (LMS_DIR / "templates" / "staff" / "course.html").read_text(encoding="utf-8")
        self.assertIn('<dialog id="rank-score-dialog" class="live-responses-dialog rank-score-dialog"', template)
        portal = (STATIC / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn("fullOrderHtml(item?.group_submit?.full_order)", portal)
        css = (STATIC / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn(".rank-score-dialog", css)
        self.assertNotIn(".rank-scoring", css)

    def test_totals_refresh_right_after_an_award(self) -> None:
        """Mobbin: Class list and Options-strip scoreboard move on Assign, not next poll."""
        staff = (STATIC / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("applyRankScoreGame(payload.game)", staff)
        self.assertIn("window.setTimeout(() => void refreshRaceGamePoints(), 1500)", staff)
        self.assertIn("  paintDivisionMeter();\n  paintScoreboardPreviewTotals();\n}", staff)
        self.assertIn("function paintScoreboardPreviewTotals() {", staff)
        css = (STATIC / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn("auto auto 9.6rem", css)  # fixed action column
        # Gate LOW-1: an older full poll never paints totals back.
        self.assertIn("const pointsFresh = adoptGamePointsVer(payload?.game_points_ver);", staff)
        self.assertIn("if (!adoptGamePointsVer(state.game)) return;", staff)
        self.assertIn("seq < gamePointsVer.seq", staff)
        # Gate LOW-3 on 2cc6a9e: forget the version whenever a game ends,
        # is quit or begins, and when a full poll says there is no game.
        self.assertEqual(staff.count("resetGamePointsVer();"), 5, "begin x2, cancel, end, poll (MCK-192 removed the unused stuck-game helper)")
        self.assertIn('hasOwnProperty.call(payload || {}, "game_points_ver") && !payload.game_points_ver', staff)
        # Gate LOW-3: at ~390px the rows wrap and the dialog scrolls.
        self.assertIn('"stepper stepper action"', css)
        self.assertIn("overflow-y: auto", css.split("body.staff-shell .rank-score-dialog {")[2])


    def test_points_version_rules_in_node(self) -> None:
        """Run the page's own adoptGamePointsVer through a Quit + Begin."""
        staff = (STATIC / "staff_ap.js").read_text(encoding="utf-8")
        parts = []
        for name in ("adoptGamePointsVer", "gamePointsKey", "resetGamePointsVer"):
            match = re.search(rf"^function {name}\(.*?^\}}$", staff, re.S | re.M)
            self.assertIsNotNone(match, name)
            parts.append(match.group(0))
        script = "let gamePointsVer = null; let sessionGamePoints = {};\n" + "\n".join(parts) + r"""
const out = [];
const adopt = (v) => { const ok = adoptGamePointsVer(v); out.push([ok, JSON.stringify(sessionGamePoints)]); };
adopt({ game_id: 1, game_key: "1|5|t1", seq: 4 });
sessionGamePoints = { "11": 7 };
adopt({ game_id: 1, game_key: "1|5|t1", seq: 3 });          // older poll, same game: ignored
adopt({ id: 1, session_id: 5, created_at: "t1", event_seq: 5 });  // game-state block, same game
adopt({ game_id: 1, game_key: "1|5|t2", seq: 0 });          // new game, same id: accepted, totals dropped
sessionGamePoints = { "11": 0 };
adopt({ game_id: 1, game_key: "1|5|t2", seq: 2 });          // new game's refresh
adopt({ id: 1, session_id: 5, created_at: "t2", event_seq: 1 });  // older, new game: ignored
resetGamePointsVer();
adopt({ game_id: 1, game_key: "1|5|t2", seq: 0 });          // after a reset anything is taken
adopt(null);
console.log(JSON.stringify(out));
"""
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        got = json.loads(proc.stdout.strip())
        self.assertEqual(
            got,
            [
                [True, "{}"],
                [False, '{"11":7}'],
                [True, '{"11":7}'],
                [True, "{}"],
                [True, '{"11":0}'],
                [False, '{"11":0}'],
                [True, '{"11":0}'],
                [True, '{"11":0}'],
            ],
        )


if __name__ == "__main__":
    unittest.main()

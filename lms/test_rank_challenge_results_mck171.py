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
from unittest import mock
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

    def _post_step(self, item: dict[str, Any], step: int):
        """One Next: the route only takes exactly the current step + 1."""
        return self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{item['id']}/race-step", json={"step": step}
        )

    def _step(self, item: dict[str, Any], step: int):
        """Press Next until the projector is on ``step`` (last reply)."""
        rv = None
        current = self.school.rank_race_step_view(self.session_id, int(item["id"]))["step"]
        for want in range(current + 1, step + 1):
            rv = self._post_step(item, want)
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True)[:300])
        return rv

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


class PaysOnMixin:
    """MCK-185 option B turned the automatic payout off
    (``rank_challenge.CHALLENGE_AUTO_PAYS = False``). The ledger code stays,
    so these suites switch it back on to keep it covered."""

    def setUp(self) -> None:
        super().setUp()
        switch = mock.patch.object(rank_challenge, "CHALLENGE_AUTO_PAYS", True)
        switch.start()
        self.addCleanup(switch.stop)


class NoAutoAwardTests(StepMixin, ChallengeHarness):
    """MCK-185 option B: a Team challenge awards nothing by itself."""

    def _ledger(self, item: dict[str, Any]) -> int:
        with self.school._lock:
            row = self.school.conn.execute(
                "SELECT COUNT(*) AS n FROM live_rank_race_awards WHERE live_item_id = ?",
                (int(item["id"]),),
            ).fetchone()
        return int(row["n"])

    def test_points_step_podium_end_game_and_end_live_class_pay_nothing(self) -> None:
        self.assertFalse(rank_challenge.CHALLENGE_AUTO_PAYS)
        item = self._challenge()
        self._lock_team(item, ("Ava", "Cy", "Eli"), KEY_IDS)
        self._order("Ben", item, SWAP)
        closed = self._close(item)
        # No Points step any more: the last row, then the podium (n+1).
        rv = self._step(item, POINTS_STEP)
        payload = rv.get_json()
        self.assertFalse(payload["results"]["pays"])
        self.assertEqual(payload["results"]["podium_step"], POINTS_STEP)
        self.assertEqual(self._post_step(item, PODIUM_STEP).status_code, 409)
        teams = {t["team_id"]: t for t in payload["results"]["teams"]}
        # The "Score teams" default (2 per right spot); nothing given.
        self.assertEqual(teams[self._team("Ava", item)]["auto"], 8)
        self.assertEqual(teams[self._team("Ben", item)]["auto"], 4)
        self.school._award_rank_race_points(self.session_id, closed)
        self.school._award_pending_rank_races(self.session_id)
        self.assertEqual(int(self._row(item, "Ava").get("awarded_points") or 0), 0)
        live = self.client.post(
            f"/api/classes/{self.class_id}/game/start-rounds",
            json={"rounds": [{"kind": "challenge", "minutes": 10}]},
        )
        self.assertEqual(live.status_code, 200, live.get_data(as_text=True)[:300])
        self.client.post(f"/api/classes/{self.class_id}/game/end", json={"preserve_live_session": True})
        self.client.post(f"/staff/class/{self.class_id}/end-live", data={})
        self.assertEqual(self.school.get_live_session(self.session_id)["status"], "ended")
        for name in ("Ava", "Ben", "Cy", "Dee", "Eli", "Fay"):
            self.assertEqual(self._points(name), 0, name)
        self.assertEqual(self._ledger(item), 0)


class AwardTests(PaysOnMixin, StepMixin, ChallengeHarness):
    """Points land once, at the projector points step, to members seen present
    (only with ``CHALLENGE_AUTO_PAYS`` on; off since MCK-185 option B)."""

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

    def test_award_is_paid_once_and_final(self) -> None:
        item = self._challenge()
        self._order("Ava", item, SWAP)
        closed = self._close(item)
        self._step(item, POINTS_STEP)
        self.assertEqual(self._points("Ava"), 4)
        # Repeating the points step is a 409; running the award again
        # (End Game, End Live Class, session end) pays nothing more.
        self.assertEqual(self._post_step(item, POINTS_STEP).status_code, 409)
        self.school._award_rank_race_points(self.session_id, closed)
        self.school._award_pending_rank_races(self.session_id)
        self.assertEqual(self._points("Ava"), 4)
        # One ledger row per (challenge, student): a second row is refused
        # by the primary key, not just by the code path.
        with self.school._lock:
            ledger = self.school.conn.execute(
                "SELECT student_id, points, paid_at FROM live_rank_race_awards WHERE live_item_id = ?",
                (int(item["id"]),),
            ).fetchall()
        paid = {int(r["student_id"]): int(r["points"]) for r in ledger if r["paid_at"]}
        self.assertEqual(paid, {self.ids[n]: 4 for n in ("Ava", "Cy", "Eli")})
        import sqlite3

        with self.assertRaises(sqlite3.IntegrityError):
            with self.school._lock:
                self.school.conn.execute(
                    "INSERT INTO live_rank_race_awards (live_item_id, student_id, team_id, points, created_at) "
                    "VALUES (?, ?, 0, 4, 'x')",
                    (int(item["id"]), self.ids["Ava"]),
                )
        with self.school._lock:
            if self.school.conn.in_transaction:
                self.school.conn.execute("ROLLBACK")
        # A payout is final: a later change to the stored answer is not
        # re-paid as a difference.
        team = self._team("Ava", item)
        with self.school._lock:
            self.school.conn.execute(
                "UPDATE live_group_responses SET proposed_answer_json = ? "
                "WHERE live_item_id = ? AND team_id = ?",
                (json.dumps({"kind": "rank", "order": self._as_real(item, KEY_IDS), "complete": True}), int(item["id"]), team),
            )
            self.school.conn.commit()
        self.school._award_rank_race_points(self.session_id, closed)
        self.assertEqual(self._points("Ava"), 4)
        with self.school.game._lock:
            n = self.school.game.conn.execute(
                "SELECT COUNT(*) FROM point_events WHERE to_kind = 'student' AND to_id = ? AND amount != 0",
                (self.ids["Ava"],),
            ).fetchone()[0]
        self.assertEqual(n, 1)

    def test_end_live_class_button_pays_a_challenge_never_stepped(self) -> None:
        """HIGH-2: the real End Live Class route pays through the ledger."""
        item = self._challenge()
        self._order("Ava", item, KEY_IDS)
        self._close(item)
        self._step(item, POINTS_STEP - 1)  # spots shown, points never reached
        self.assertEqual(self._points("Ava"), 0)
        rv = self.client.post(f"/staff/class/{self.class_id}/end-live", data={})
        self.assertIn(rv.status_code, (302, 303), rv.get_data(as_text=True)[:300])
        self.assertEqual(self.school.get_live_session(self.session_id)["status"], "ended")
        for name in ("Ava", "Cy", "Eli"):
            self.assertEqual(self._points(name), 8)
        self.assertEqual(self._points("Ben"), 0)
        # Pressing it again (a stale tab) pays nothing more.
        self.client.post(f"/staff/class/{self.class_id}/end-live", data={})
        self.assertEqual(self._points("Ava"), 8)

    def test_end_live_class_after_the_points_step_pays_nothing_more(self) -> None:
        item = self._challenge()
        self._order("Ava", item, KEY_IDS)
        self._close(item)
        self._step(item, POINTS_STEP)
        self.assertEqual(self._points("Ava"), 8)
        rv = self.client.post(f"/staff/class/{self.class_id}/end-live", data={})
        self.assertIn(rv.status_code, (302, 303))
        self.assertEqual(self._points("Ava"), 8)

    def test_end_game_twice_pays_once(self) -> None:
        """HIGH-2: the real End Game route, pressed twice."""
        item = self._challenge()
        self._order("Ava", item, KEY_IDS)
        self._close(item)
        live = self.client.post(
            f"/api/classes/{self.class_id}/game/start-rounds",
            json={"rounds": [{"kind": "challenge", "minutes": 10}]},
        )
        self.assertEqual(live.status_code, 200, live.get_data(as_text=True)[:300])
        for _ in range(2):
            rv = self.client.post(
                f"/api/classes/{self.class_id}/game/end", json={"preserve_live_session": True}
            )
            self.assertLess(rv.status_code, 500, rv.get_data(as_text=True)[:300])
        self.assertEqual(self._points("Ava"), 8)

    def test_late_joiner_after_close_is_credited_zero(self) -> None:
        """LOW-1: joined after Close = 0, and the phone says no team total."""
        self._leave("Eli")
        item = self._challenge()
        self._lock_team(item, ("Ava", "Cy"), KEY_IDS)
        self._close(item)
        code = self.school.get_live_session(self.session_id)["session_code"]
        self.students["Eli"].post("/auth/student-code", data={"code": str(code), "name": "Eli"})
        self._step(item, POINTS_STEP)
        self.assertEqual((self._points("Ava"), self._points("Eli")), (8, 0))
        eli = self._card("Eli", item)["race"]["results"]
        ava = self._card("Ava", item)["race"]["results"]
        self.assertEqual((eli["credited"], eli["points"], eli["show_points"]), (False, None, True))
        self.assertEqual((ava["credited"], ava["points"]), (True, 8))
        self.assertFalse(self.school.rank_race_credited(int(item["id"]), self.ids["Eli"]))
        self.assertTrue(self.school.rank_race_credited(int(item["id"]), self.ids["Ava"]))

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


class StepRouteTests(PaysOnMixin, StepMixin, ChallengeHarness):
    """The reveal step: closed challenges only, exactly one screen at a time."""

    def test_open_challenge_refuses_a_step(self) -> None:
        item = self._challenge()
        self.assertEqual(self._post_step(item, 1).status_code, 400)

    def test_plain_rank_refuses_a_step(self) -> None:
        # MCK-185: a keyed group rank now gets the spots reveal (its own
        # steps), so "plain" here is an opinion rank with no answer order.
        row = self._rank_row(key=False)
        item = self._publish(row)
        self._close(item)
        self.assertEqual(self._post_step(item, 1).status_code, 400)

    def test_only_the_exact_next_step_is_accepted(self) -> None:
        """LOW-3: skip, back, repeat and past-the-podium are 409 + resync."""
        item = self._challenge()
        self._close(item)
        self.assertEqual(self._post_step(item, 1).get_json()["step"], 1)
        for bad in (3, 1, 0, -1, 99):
            rv = self._post_step(item, bad)
            self.assertEqual(rv.status_code, 409, (bad, rv.get_data(as_text=True)[:200]))
            body = rv.get_json()
            self.assertEqual((body["ok"], body["step"]), (False, 1))
            self.assertEqual(body["results"]["step"], 1)  # quiet client resync
        self._step(item, PODIUM_STEP)
        self.assertEqual(self._post_step(item, PODIUM_STEP + 1).status_code, 409)
        self.assertEqual(self._view(item)["race"]["results"]["step"], PODIUM_STEP)

    def test_missing_step_is_rejected(self) -> None:
        item = self._challenge()
        self._close(item)
        rv = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{item['id']}/race-step", json={}
        )
        self.assertEqual(rv.status_code, 400)

    def test_step_reaches_phones(self) -> None:
        """MED-2: each step is a postcard, and the phone poll is not unchanged."""
        from live_news_wire import log_for

        item = self._challenge()
        self._order("Ava", item, KEY_IDS)
        self._close(item)
        self._step(item, POINTS_STEP)
        seq = int(self.school.live_session_teacher_state_payload(self.session_id).get("state_seq") or 0)
        stamp = self.school.live_student_poll_stamp(self.session_id, self.class_id)
        self.assertIsNotNone(
            self.school.student_live_poll_unchanged(self.session_id, self.class_id, seq, stamp)
        )
        news = log_for(self.school.data_dir)
        before = news.since(self.session_id, 0, limit=1000)
        rv = self._post_step(item, PODIUM_STEP)
        self.assertEqual(rv.status_code, 200)
        added = news.since(self.session_id, max([int(e.get("id") or 0) for e in before] or [0]), limit=1000)
        self.assertIn("state_seq", [e.get("type") for e in added])
        self.assertIn(("flag_work", "race_step"), [(e.get("type"), e.get("kind")) for e in added])
        # Same seq, old stamp: the poll rebuilds instead of "unchanged".
        self.assertNotEqual(self.school.live_student_poll_stamp(self.session_id, self.class_id), stamp)
        self.assertIsNone(
            self.school.student_live_poll_unchanged(self.session_id, self.class_id, seq, stamp)
        )
        self.assertTrue(self._card("Ava", item)["race"]["results"]["show_podium"])

    def test_rejected_step_sends_no_postcard(self) -> None:
        from live_news_wire import log_for

        item = self._challenge()
        self._close(item)
        news = log_for(self.school.data_dir)
        before = len(news.since(self.session_id, 0, limit=1000))
        self.assertEqual(self._post_step(item, 2).status_code, 409)
        self.assertEqual(len(news.since(self.session_id, 0, limit=1000)), before)


class ResultsBlockTests(PaysOnMixin, StepMixin, ChallengeHarness):
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
        # Ben's order never leaks, as aliases or as real ids.
        for ids in (self._as_student(item, KEY_IDS), self._as_real(item, KEY_IDS)):
            self.assertNotIn(json.dumps({"order": ids})[1:-1], body)
        # The public results carry no class stack / other team orders.
        rv = self.students["Ava"].get("/api/student/state")
        state = rv.get_data(as_text=True)
        self.assertNotIn("class_order", state)
        self.assertNotIn("rank_key", state)
        # MCK-176: no real option id in the results payloads either.
        for real in self._real_ids(item):
            self.assertNotIn(f'"{real}"', body)
            self.assertNotIn(f'"{real}"', state)


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
        # LOW-3: no optimistic advance; Next is disabled while the POST is
        # out, and a 409 resyncs to the server's step without an error.
        self.assertIn("busy: raceStepInFlight.has(liveItemId),", text)
        self.assertIn("response.status !== 409", text)
        self.assertNotIn("raceStep.set(itemId, next);", text)
        # MED-1: the class list and scoreboard refresh at the points step.
        self.assertIn("await refreshRaceGamePoints();", text)
        self.assertIn("api(`/api/classes/${classId}/game`)", text)

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

#!/usr/bin/env python3
"""MCK-155 PR C: group MC "pick alone, then agree" and rank "Take turns".

Real MCR3U M1 C2 deck, real teacher routes (Add New, settings, Publish,
Start group step, Skip) and real student routes (group-pick, group-submit,
rank-turn) with one logged-in client per student.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

import app as app_module  # noqa: E402
from app import create_app  # noqa: E402
from school_db import GroupAnswerLocked  # noqa: E402
from live_rank import (  # noqa: E402
    TurnConflict,
    apply_turn_place,
    apply_turn_skip,
    apply_turn_undo,
    empty_turns_state,
    turn_next_ids,
)

NAMES = ["Ava", "Ben", "Cy", "Dee"]


def _function_source(js: str, name: str) -> str:
    """Return the source of a top-level ``function name(...) {...}``."""
    start = js.index(f"\nfunction {name}(") + 1
    open_at = js.index("{", js.index(")", start))
    depth = 0
    for index in range(open_at, len(js)):
        if js[index] == "{":
            depth += 1
        elif js[index] == "}":
            depth -= 1
            if depth == 0:
                return js[start : index + 1]
    raise AssertionError(f"unbalanced {name}")


class TurnRuleTests(unittest.TestCase):
    """Pure first-come, no-repeat rule."""

    def _run(self, members: list[int], options: list[str], tries: list[int]) -> list[Any]:
        state = empty_turns_state()
        out: list[Any] = []
        for who in tries:
            free = [o for o in options if o not in state["order"]]
            try:
                state, _ = apply_turn_place(state, who, free[0], options, members)
                out.append(who)
            except TurnConflict as exc:
                out.append(f"x{who}:{exc.reason}")
        return out

    def test_three_members_five_options_cycle(self) -> None:
        got = self._run([1, 2, 3], ["a", "b", "c", "d", "e"], [2, 1, 3, 1, 2, 1])
        # First come (2, 1, 3), then the order repeats: 2 next, never 1 twice.
        self.assertEqual(got, [2, 1, 3, "x1:not_your_turn", 2, 1])

    def test_five_members_three_options_all_different(self) -> None:
        got = self._run([1, 2, 3, 4, 5], ["a", "b", "c"], [4, 4, 2, 5])
        self.assertEqual(got, [4, "x4:not_your_turn", 2, 5])

    def test_absent_member_is_not_waited_on(self) -> None:
        # 3 left the class: only 1 and 2 are present.
        got = self._run([1, 2], ["a", "b", "c", "d"], [1, 2, 1, 2])
        self.assertEqual(got, [1, 2, 1, 2])

    def test_skip_and_undo(self) -> None:
        state = empty_turns_state()
        state, _ = apply_turn_place(state, 1, "a", ["a", "b", "c"], [1, 2])
        self.assertEqual(turn_next_ids(state, [1, 2]), [2])
        state, skipped = apply_turn_skip(state, [1, 2])
        self.assertEqual(skipped, [2])
        # 2's turn passed, so 1 goes next and 2 waits for 1.
        self.assertEqual(turn_next_ids(state, [1, 2]), [1])
        state, _ = apply_turn_place(state, 1, "b", ["a", "b", "c"], [1, 2])
        state = apply_turn_undo(state, 1)
        self.assertEqual(state["order"], ["a"])
        with self.assertRaises(TurnConflict):
            apply_turn_undo(state, 1)  # the last move is the skip now
        self.assertEqual(apply_turn_skip(empty_turns_state(), [1, 2])[1], [])


class PickThenAgreeAndTurnsTests(unittest.TestCase):
    """Two teams of two: Ava + Cy, Ben + Dee. Each student has a client."""

    def setUp(self) -> None:
        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(teacher_user_id=int(teacher["id"]), ontario_code="MCR3U")
        self.class_id = int(
            self.school.game.create_class(
                year="2026/27",
                semester="Semester 1",
                course_code="MCR3U",
                days_preset="M/W/F",
                time_label="2:00pm",
                codenames=NAMES,
                offering_id=int(offering["id"]),
                teacher_user_id=int(teacher["id"]),
            )["id"]
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        code = self.school.get_user_by_email("teacher@gmail.com")["verification_code"]
        self.client.post("/verify-email", data={"code": code})
        live = self.school.start_live_class_session(self.class_id, int(teacher["id"]))
        self.session_id = int(live["id"])
        self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"live_module": "M1", "live_slot": "C2", "stage": "round", "page_id": "round_1"},
        )
        self.school.game.begin_game(self.class_id)
        with self.school.game._lock:
            rows = self.school.game.conn.execute(
                "SELECT id, codename FROM students WHERE class_id = ? ORDER BY id", (self.class_id,)
            ).fetchall()
        self.ids = {str(r["codename"]): int(r["id"]) for r in rows}
        self.students: dict[str, Any] = {}
        for name in NAMES:
            student = self.app.test_client()
            rv = student.post(
                "/auth/student-code", data={"code": str(live["session_code"]), "name": name}
            )
            self.assertEqual(rv.status_code, 302, rv.get_data(as_text=True)[:300])
            self.students[name] = student
        order = [self.ids[n] for n in NAMES]
        self.school.setup_live_session_groups(
            self.session_id,
            n_teams=2,
            mode="manual",
            present_ids=order,
            assignments=[{"student_id": sid, "team_index": i % 2} for i, sid in enumerate(order)],
        )
        self.school.ensure_live_session_items(self.session_id)

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)
        self.school.close()
        self.tmp.cleanup()

    # helpers -------------------------------------------------------------
    def _add(self, body: dict[str, Any]) -> dict[str, Any]:
        body = {"page_number": 4, "stage": "round", **body}
        rv = self.client.post(f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/add-question", json=body)
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        item_id = rv.get_json()["placement"]["item"]["id"]
        return next(r for r in self.school.list_live_session_items(self.session_id) if r["item_id"] == item_id)

    def _publish(self, row: dict[str, Any]) -> dict[str, Any]:
        rv = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{row['id']}/publish",
            json={"publish_mode": "group_submit"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        return rv.get_json()["item"]

    def _mc(self) -> dict[str, Any]:
        return self._publish(
            self._add({"type": "mc", "text": "Slope of y = 2x + 1?", "options": ["2", "1", "-2", "0"], "correct_index": 0})
        )

    def _rank_row(self) -> dict[str, Any]:
        return self._add({"type": "rank", "text": "Rank these.", "options": ["Two points", "A graph", "An equation", "A table"]})

    def _post(self, name: str, item: dict[str, Any], path: str, body: dict[str, Any]):
        return self.students[name].post(f"/api/student/live-items/{item['id']}/{path}", json=body)

    def _card(self, name: str, item: dict[str, Any]) -> dict[str, Any]:
        live = self.school.get_live_session_item(self.session_id, int(item["id"]))
        return self.school.student_group_submit_state(live, self.ids[name]) or {}

    def _view(self, item: dict[str, Any]) -> dict[str, Any]:
        return self.school.live_session_item_results(self.session_id, int(item["id"]))

    def _leave(self, name: str) -> None:
        self.school.mark_live_session_attendee_left(self.session_id, student_id=self.ids[name])

    # MC option B ---------------------------------------------------------
    def test_pick_step_shows_still_choosing_then_named_picks(self) -> None:
        item = self._mc()
        rv = self._post("Ava", item, "group-pick", {"choice": "2"})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        card = rv.get_json()["group_submit"]
        self.assertEqual(card["flow"], "pick_then_agree")
        self.assertTrue(card["pick_step"])
        self.assertEqual(card["my_pick"], "2")
        self.assertEqual(card["waiting_names"], ["Cy"])
        self.assertEqual(card["waiting_count"], 1)
        self.assertFalse(card["can_submit"])
        self.assertNotIn("member_picks", card)
        # No shared draft or send until everyone has picked.
        # Ava has picked: wait for the rest. Cy has not: send yours first.
        rv = self._post("Ava", item, "group-draft", {"choice": "2", "why": "x"})
        self.assertEqual(rv.status_code, 400)
        self.assertEqual(rv.get_json()["error"], "Wait until everyone has picked.")
        rv = self._post("Cy", item, "group-draft", {"choice": "2", "why": "x"})
        self.assertEqual(rv.status_code, 400)
        self.assertEqual(rv.get_json()["error"], "Send your own pick first.")
        rv = self._post("Cy", item, "group-pick", {"choice": "1"})
        card = rv.get_json()["group_submit"]
        self.assertFalse(card["pick_step"])
        self.assertEqual(
            card["member_picks"],
            [{"name": "Ava", "value": "2", "mine": False}, {"name": "Cy", "value": "1", "mine": True}],
        )
        # Students never see marks or the other team's picks.
        self.assertNotIn("correct", str(card["member_picks"]))
        ben = self._card("Ben", item)
        self.assertTrue(ben["pick_step"])
        self.assertNotIn("member_picks", ben)
        # Staff see each pick with a right/wrong mark on keyed MC.
        team = next(r for r in self._view(item)["status_board"] if r.get("picks"))
        self.assertEqual(
            team["picks"],
            [{"name": "Ava", "value": "2", "correct": True}, {"name": "Cy", "value": "1", "correct": False}],
        )
        self.assertFalse(team["pick_step"])

    def test_sending_locks_the_answer_for_the_team(self) -> None:
        item = self._mc()
        self._post("Ava", item, "group-pick", {"choice": "2"})
        self._post("Cy", item, "group-pick", {"choice": "1"})
        rv = self._post("Cy", item, "group-submit", {"choice": "1", "why": ""})
        self.assertEqual(rv.status_code, 400)
        self.assertIn("why", rv.get_json()["error"])
        rv = self._post("Cy", item, "group-submit", {"choice": "2", "why": "Ava convinced me"})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        self.assertTrue(rv.get_json()["group_submit"]["locked"])
        # Ava sends something else: 409 and the locked card, nothing changes.
        rv = self._post("Ava", item, "group-submit", {"choice": "-2", "why": "no"})
        self.assertEqual(rv.status_code, 409)
        body = rv.get_json()
        self.assertTrue(body["locked"])
        self.assertEqual(body["group_submit"]["submitted_choice"], "2")
        self.assertEqual(body["group_submit"]["last_submitter"], "Cy")
        rv = self._post("Ava", item, "group-draft", {"choice": "-2", "why": "no"})
        self.assertEqual(rv.status_code, 409)
        # Cy's repeat tap of the same answer is a safe no-op.
        rv = self._post("Cy", item, "group-submit", {"choice": "2", "why": "Ava convinced me"})
        self.assertEqual(rv.status_code, 200)
        view = self._view(item)
        sent = next(r for r in view["status_board"] if r["submitted"])
        self.assertTrue(sent["correct"])
        log = next(r for r in view["submitter_log"] if r["last_submitter"] == "Cy")
        self.assertEqual(log["resubmit_count"], 0)

    def test_absent_teammate_does_not_block_the_pick_step(self) -> None:
        item = self._mc()
        self._leave("Cy")
        rv = self._post("Ava", item, "group-pick", {"choice": "2"})
        card = rv.get_json()["group_submit"]
        self.assertFalse(card["pick_step"])
        self.assertEqual([p["name"] for p in card["member_picks"]], ["Ava"])

    def test_teacher_move_on_ends_the_pick_step(self) -> None:
        item = self._mc()
        self._post("Ava", item, "group-pick", {"choice": "2"})
        self.assertTrue(self._view(item)["any_picking"])
        rv = self.client.post(f"/api/live-sessions/{self.session_id}/items/{item['id']}/end-voting")
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        self.assertFalse(rv.get_json()["group_submit"]["any_picking"])
        self.assertFalse(self._card("Cy", item)["pick_step"])
        self.assertFalse(self._card("Ben", item)["pick_step"])
        rv = self._post("Cy", item, "group-submit", {"choice": "2", "why": "Ava's pick"})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))

    def test_light_poll_carries_picks_for_staff(self) -> None:
        item = self._mc()
        before = self.school.light_group_results(self.session_id)[str(item["id"])]["response_seq"]
        self._post("Ava", item, "group-pick", {"choice": "2"})
        light = self.school.light_group_results(self.session_id)[str(item["id"])]
        self.assertNotEqual(light["response_seq"], before)
        team = next(t for t in light["teams"] if t.get("picks"))
        self.assertEqual(team["picks"][0]["name"], "Ava")
        self.assertTrue(team["pick_step"])

    # Gate fixes (189dd07) ---------------------------------------------------
    def _picked_mc(self) -> dict[str, Any]:
        """Keyed MC with Ava and Cy (team 1) past the pick step."""
        item = self._mc()
        self._post("Ava", item, "group-pick", {"choice": "2"})
        self._post("Cy", item, "group-pick", {"choice": "1"})
        self.assertFalse(self._card("Ava", item)["pick_step"])
        return item

    def _team_row(self, item: dict[str, Any], name: str) -> dict[str, Any]:
        team_id = int(self._card(name, item)["team_id"])
        return self.school._group_response_row(int(item["id"]), team_id) or {}

    def _assert_one_winner(self, item: dict[str, Any], results: dict[str, Any], sent: dict[str, tuple[str, str]]) -> str:
        """Exactly one send won; the lock holds the winner's own text."""
        codes = sorted(results.values())
        self.assertEqual(codes, [200, 409], results)
        winner = next(n for n, code in results.items() if code == 200)
        row = self._team_row(item, winner)
        self.assertEqual(int(row["submit_count"]), 1)
        self.assertEqual(int(row["last_submitter_student_id"]), self.ids[winner])
        final = row["final_answer"]
        self.assertEqual((final["value"], final["why"]), sent[winner])
        return winner

    def test_two_teammates_send_at_once_one_wins(self) -> None:
        """Gate MED-1: both Send at the same instant -> one 200, one 409."""
        winners = set()
        for round_no in range(6):
            item = self._picked_mc()
            sent = {"Ava": ("2", f"ava why {round_no}"), "Cy": ("1", f"cy why {round_no}")}
            barrier = threading.Barrier(2)
            results: dict[str, int] = {}

            def send(name: str) -> None:
                choice, why = sent[name]
                barrier.wait()
                rv = self._post(name, item, "group-submit", {"choice": choice, "why": why})
                results[name] = rv.status_code

            threads = [threading.Thread(target=send, args=(n,)) for n in sent]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
            winners.add(self._assert_one_winner(item, results, sent))
            # The loser's card shows the winner's locked answer.
            loser = next(n for n in sent if n not in results or results[n] == 409)
            card = self._card(loser, item)
            self.assertTrue(card["locked"])
            self.assertEqual(card["submitted_choice"], self._team_row(item, loser)["final_answer"]["value"])
        self.assertTrue(winners)

    def test_one_student_two_tabs_send_at_once(self) -> None:
        """Gate MED-1: the same student in two tabs; different whys race."""
        item = self._picked_mc()
        live = self.school.get_live_session_item(self.session_id, int(item["id"]))
        ava = self.ids["Ava"]
        barrier = threading.Barrier(2)
        out: dict[str, str] = {}

        def tab(name: str, why: str) -> None:
            barrier.wait()
            try:
                self.school.submit_group_mc_answer(self.session_id, int(live["id"]), ava, choice="2", why=why)
                out[name] = "ok"
            except GroupAnswerLocked:
                out[name] = "locked"

        threads = [threading.Thread(target=tab, args=(n, f"tab {n}")) for n in ("A", "B")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(sorted(out.values()), ["locked", "ok"], out)
        won = next(n for n, v in out.items() if v == "ok")
        self.assertEqual(self._team_row(item, "Ava")["final_answer"]["why"], f"tab {won}")
        # The winning tab's repeat tap is still a safe no-op.
        self.school.submit_group_mc_answer(self.session_id, int(live["id"]), ava, choice="2", why=f"tab {won}")
        self.assertEqual(int(self._team_row(item, "Ava")["submit_count"]), 1)

    def test_a_draft_never_lands_on_a_locked_answer(self) -> None:
        """Gate MED-1: a late shared-draft save cannot rewrite the lock."""
        item = self._picked_mc()
        self.assertEqual(self._post("Ava", item, "group-submit", {"choice": "2", "why": "slope"}).status_code, 200)
        rv = self._post("Cy", item, "group-draft", {"choice": "1", "why": "late"})
        self.assertEqual(rv.status_code, 409)
        row = self._team_row(item, "Cy")
        self.assertEqual(row["why_text"], "slope")
        self.assertEqual(row["final_answer"]["why"], "slope")

    def test_two_processes_send_at_once_one_wins(self) -> None:
        """Gate MED-1 across processes: two SchoolDB connections, one DB file."""
        item = self._picked_mc()
        live_id = int(item["id"])
        root = Path(self.tmp.name)
        go = root / "go"
        script = r"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from school_db import SchoolDB, GroupAnswerLocked
root = Path(sys.argv[2])
school = SchoolDB(root / "lloves.sqlite", root)
args = json.loads(sys.argv[3])
go = root / "go"
deadline = time.time() + 60
while not go.exists() and time.time() < deadline:
    time.sleep(0.005)
start = float(go.read_text())
while time.time() < start:
    pass
try:
    school.submit_group_mc_answer(args["session"], args["item"], args["student"], choice=args["choice"], why=args["why"])
    print("RESULT ok")
except GroupAnswerLocked:
    print("RESULT locked")
"""
        sent = {"Ava": ("2", "ava xproc"), "Cy": ("1", "cy xproc")}
        procs = {}
        for name, (choice, why) in sent.items():
            args = {"session": self.session_id, "item": live_id, "student": self.ids[name], "choice": choice, "why": why}
            procs[name] = subprocess.Popen(
                [sys.executable, "-c", script, str(LMS_DIR), str(root), json.dumps(args)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(LMS_DIR),
            )
        # Both children open the DB first; then they start on the same clock.
        time.sleep(3.0)
        go.write_text(str(time.time() + 1.0))
        results: dict[str, int] = {}
        for name, proc in procs.items():
            stdout, stderr = proc.communicate(timeout=120)
            line = next((ln for ln in stdout.splitlines() if ln.startswith("RESULT ")), "")
            self.assertTrue(line, stderr[-2000:])
            results[name] = 200 if line == "RESULT ok" else 409
        self._assert_one_winner(item, results, sent)

    def test_move_on_sends_a_live_update(self) -> None:
        """Gate MED-3: Start group step emits the same postcard as Skip."""
        item = self._mc()
        self._post("Ava", item, "group-pick", {"choice": "2"})
        with mock.patch.object(app_module, "emit_answer_landed") as emit:
            rv = self.client.post(f"/api/live-sessions/{self.session_id}/items/{item['id']}/end-voting")
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        self.assertEqual(emit.call_count, 1)
        self.assertEqual(emit.call_args.kwargs.get("scope"), "group")
        self.assertEqual(int(emit.call_args.args[1]), self.session_id)

    def test_item_published_before_option_b_keeps_one_step_flow(self) -> None:
        """Gate MED-2: a deploy mid-question never strands open tabs."""
        item = self._mc()
        live = self.school.get_live_session_item(self.session_id, int(item["id"]))
        self.assertEqual(live["item"].get("group_mc_flow"), "pick_then_agree")
        # A deck refresh keeps the stamp.
        self.school.ensure_live_session_items(self.session_id)
        live = self.school.get_live_session_item(self.session_id, int(item["id"]))
        self.assertEqual(live["item"].get("group_mc_flow"), "pick_then_agree")
        # Now an item that was published by the old code (no stamp).
        old = self._mc()
        question = dict(self.school.get_live_session_item(self.session_id, int(old["id"]))["item"])
        question.pop("group_mc_flow", None)
        with self.school._lock:
            self.school.conn.execute(
                "UPDATE live_session_items SET item_json = ? WHERE id = ?",
                (json.dumps(question), int(old["id"])),
            )
            self.school.conn.commit()
        self.school.ensure_live_session_items(self.session_id)
        card = self._card("Ava", old)
        self.assertNotIn("flow", card)
        self.assertNotIn("pick_step", card)
        # The old tab's shared draft and send work with no pick step.
        rv = self._post("Ava", old, "group-draft", {"choice": "2", "why": "old tab"})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        rv = self._post("Cy", old, "group-submit", {"choice": "2", "why": "old tab"})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        self.assertTrue(rv.get_json()["group_submit"]["submitted"])
        view = self._view(old)
        self.assertFalse(view.get("any_picking"))
        self.assertFalse(any(r.get("picks") for r in view["status_board"]))
        # The old one-step flow still lets the group re-send, as it did.
        rv = self._post("Ava", old, "group-submit", {"choice": "1", "why": "changed our minds"})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        self.assertEqual(self._team_row(old, "Ava")["final_answer"]["value"], "1")
        # The new item next to it still runs option B.
        self.assertTrue(self._card("Ava", item)["pick_step"])

    def test_skip_without_a_team_is_a_400(self) -> None:
        """Gate LOW-3."""
        item = self._turns()
        url = f"/api/live-sessions/{self.session_id}/items/{item['id']}/rank-turn/skip"
        for body in ({}, {"team_id": None}, {"team_id": "x"}):
            rv = self.client.post(url, json=body)
            self.assertEqual(rv.status_code, 400, (body, rv.get_data(as_text=True)))
            self.assertFalse(rv.get_json()["ok"])

    def test_skipped_student_copy_and_done_conflict(self) -> None:
        """Gate LOW-2: "Your turn was skipped.", no "placed one", done 409."""
        item = self._turns()
        o = self._opts(item)
        self._post("Ava", item, "rank-turn", {"option_id": o[0]})
        team_id = self._card("Ava", item)["team_id"]
        rv = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{item['id']}/rank-turn/skip",
            json={"team_id": team_id},
        )
        self.assertEqual(rv.get_json()["skipped"], ["Cy"])
        cy = self._card("Cy", item)["turns"]
        self.assertTrue(cy["skipped_me"])
        self.assertEqual(cy["skipped_names"], [])
        self.assertFalse(cy["placed_last"])
        ava = self._card("Ava", item)["turns"]
        self.assertFalse(ava["skipped_me"])
        self.assertEqual(ava["skipped_names"], ["Cy"])
        self.assertTrue(ava["placed_last"])
        # Fill the order (Ava, then Cy, then Ava), then a late place.
        self.assertEqual(self._post("Ava", item, "rank-turn", {"option_id": o[1]}).status_code, 200)
        self.assertEqual(self._post("Cy", item, "rank-turn", {"option_id": o[2]}).status_code, 200)
        self.assertEqual(self._post("Ava", item, "rank-turn", {"option_id": o[3]}).status_code, 200)
        self.assertTrue(self._card("Cy", item)["turns"]["done"])
        rv = self._post("Cy", item, "rank-turn", {"option_id": o[0]})
        self.assertEqual(rv.status_code, 409)
        self.assertEqual(rv.get_json()["reason"], "done")
        self.assertEqual(rv.get_json()["error"], "Your group's order is already in.")
        # The last placer's own retry stays a safe no-op.
        self.assertEqual(self._post("Ava", item, "rank-turn", {"option_id": o[3]}).status_code, 200)

    # Take turns ----------------------------------------------------------
    def _turns(self) -> dict[str, Any]:
        row = self._rank_row()
        rv = self.client.patch(
            f"/api/live-sessions/{self.session_id}/items/{row['id']}/settings",
            json={"group_rank_mode": "turns"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        self.assertEqual(rv.get_json()["item"]["item"]["group_rank_mode"], "turns")
        return self._publish(row)

    def _opts(self, item: dict[str, Any]) -> list[str]:
        live = self.school.get_live_session_item(self.session_id, int(item["id"]))
        return [o["id"] for o in self.school._rank_option_rows(live)]

    def test_rank_mode_is_a_setup_setting(self) -> None:
        item = self._turns()
        rv = self.client.patch(
            f"/api/live-sessions/{self.session_id}/items/{item['id']}/settings",
            json={"group_rank_mode": "together"},
        )
        self.assertEqual(rv.status_code, 400)
        self.assertEqual(rv.get_json()["error"], "Group mode is locked once published.")
        mc = self._add({"type": "mc", "text": "Pick", "options": ["a", "b", "c", "d"], "correct_index": 0})
        rv = self.client.patch(
            f"/api/live-sessions/{self.session_id}/items/{mc['id']}/settings",
            json={"group_rank_mode": "turns"},
        )
        self.assertEqual(rv.status_code, 400)
        # A deck refresh keeps the setting.
        self.school.ensure_live_session_items(self.session_id)
        again = self.school.get_live_session_item(self.session_id, int(item["id"]))
        self.assertEqual(again["item"]["group_rank_mode"], "turns")

    def test_take_turns_first_come_no_repeat_then_auto_send(self) -> None:
        item = self._turns()
        o = self._opts(item)
        ava = self._card("Ava", item)
        self.assertEqual(ava["rank_mode"], "turns")
        self.assertTrue(ava["turns"]["can_place"])
        rv = self._post("Cy", item, "rank-turn", {"option_id": o[1]})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        turns = rv.get_json()["group_submit"]["turns"]
        self.assertEqual(turns["placed_spot"], 1)
        self.assertTrue(turns["can_undo"])
        self.assertFalse(turns["can_place"])
        self.assertEqual(turns["waiting_names"], ["Ava"])
        rv = self._post("Cy", item, "rank-turn", {"option_id": o[2]})
        self.assertEqual(rv.status_code, 409)
        self.assertEqual(rv.get_json()["reason"], "not_your_turn")
        self.assertEqual(rv.get_json()["error"], "Not your turn yet.")
        # The together path is refused for a take-turns question.
        rv = self._post("Ava", item, "group-draft", {"tap": o[2]})
        self.assertEqual(rv.status_code, 400)
        self.assertEqual(self._post("Ava", item, "rank-turn", {"option_id": o[0]}).status_code, 200)
        self.assertEqual(self._post("Cy", item, "rank-turn", {"option_id": o[3]}).status_code, 200)
        view = self._view(item)
        progress = next(r for r in view["turns"] if r["placed"])
        self.assertEqual((progress["placed"], progress["total"]), (3, 4))
        self.assertEqual(progress["next_names"], ["Ava"])
        rv = self._post("Ava", item, "rank-turn", {"option_id": o[2]})
        card = rv.get_json()["group_submit"]
        self.assertTrue(card["turns"]["done"])
        self.assertTrue(card["submitted"])
        self.assertEqual(card["submitted_order"], [o[1], o[0], o[3], o[2]])
        self.assertEqual([s["by_name"] for s in card["turns"]["spots"]], ["Cy", "Ava", "Cy", "Ava"])
        view = self._view(item)
        team = next(t for t in view["rank"]["teams"] if t["status"] == "submitted")
        self.assertEqual(team["order"], [o[1], o[0], o[3], o[2]])
        self.assertTrue(next(r for r in view["turns"] if r["placed"])["done"])

    def test_same_option_conflict_names_the_placer(self) -> None:
        item = self._turns()
        o = self._opts(item)
        self._post("Ava", item, "rank-turn", {"option_id": o[0]})
        rv = self._post("Cy", item, "rank-turn", {"option_id": o[0]})
        self.assertEqual(rv.status_code, 409)
        body = rv.get_json()
        self.assertEqual(body["reason"], "turn_taken")
        self.assertEqual(body["error"], "Ava just placed that one. Pick another.")
        # Ava's retry of her own placement is a safe no-op.
        self.assertEqual(self._post("Ava", item, "rank-turn", {"option_id": o[0]}).status_code, 200)

    def test_undo_only_before_the_next_pick(self) -> None:
        item = self._turns()
        o = self._opts(item)
        self._post("Ava", item, "rank-turn", {"option_id": o[0]})
        rv = self._post("Ava", item, "rank-turn", {"undo": True})
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(rv.get_json()["group_submit"]["turns"]["spots"], [])
        self._post("Ava", item, "rank-turn", {"option_id": o[0]})
        self._post("Cy", item, "rank-turn", {"option_id": o[1]})
        rv = self._post("Ava", item, "rank-turn", {"undo": True})
        self.assertEqual(rv.status_code, 409)
        self.assertEqual(rv.get_json()["reason"], "no_undo")

    def test_teacher_skip_and_absent_auto_skip(self) -> None:
        item = self._turns()
        o = self._opts(item)
        self._post("Ava", item, "rank-turn", {"option_id": o[0]})
        team_id = self._card("Ava", item)["team_id"]
        row = next(r for r in self._view(item)["turns"] if r["team_id"] == team_id)
        self.assertTrue(row["can_skip"])
        self.assertEqual(row["next_names"], ["Cy"])
        rv = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{item['id']}/rank-turn/skip",
            json={"team_id": team_id},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        self.assertEqual(rv.get_json()["skipped"], ["Cy"])
        ava = self._card("Ava", item)["turns"]
        self.assertTrue(ava["can_place"])
        self.assertEqual(ava["skipped_names"], ["Cy"])
        # Cy leaves: Ava is never blocked on Cy again.
        self._leave("Cy")
        self.assertEqual(self._post("Ava", item, "rank-turn", {"option_id": o[1]}).status_code, 200)
        self.assertEqual(self._post("Ava", item, "rank-turn", {"option_id": o[2]}).status_code, 200)

    def test_light_poll_carries_turns_progress_and_skip(self) -> None:
        item = self._turns()
        o = self._opts(item)
        before = self.school.light_group_results(self.session_id)[str(item["id"])]
        self.assertEqual(before["rank_mode"], "turns")
        self._post("Ava", item, "rank-turn", {"option_id": o[0]})
        after = self.school.light_group_results(self.session_id)[str(item["id"])]
        self.assertNotEqual(before["response_seq"], after["response_seq"])
        team_id = self._card("Ava", item)["team_id"]
        row = next(r for r in after["turns"] if r["team_id"] == team_id)
        self.assertEqual((row["placed"], row["next_names"], row["can_skip"]), (1, ["Cy"], True))
        # Progress only: no option labels on the light poll.
        self.assertNotIn("Two points", str(after))

    def test_teammate_turn_changes_the_student_poll_stamp(self) -> None:
        item = self._turns()
        o = self._opts(item)
        stamp = lambda: self.school.live_student_poll_stamp(self.session_id, self.class_id)  # noqa: E731
        before = stamp()
        self._post("Ava", item, "rank-turn", {"option_id": o[0]})
        mid = stamp()
        self.assertNotEqual(before, mid)
        self._post("Cy", item, "rank-turn", {"option_id": o[1]})
        self.assertNotEqual(mid, stamp())

    def test_write_during_state_build_is_not_unchanged_next_poll(self) -> None:
        """Gate MED-4: the /state stamp is taken before the payload build.

        Ava places while Cy's /state payload is being built. Cy's next poll
        (with the stamp from that reply) must rebuild and show Ava's spot,
        not answer ``unchanged`` on the stale payload.
        """
        item = self._turns()
        o = self._opts(item)
        real = self.school.assemble_student_live_payload
        wrote: list[bool] = []

        def build_with_a_write_mid_way(*args: Any, **kwargs: Any) -> dict[str, Any]:
            payload = real(*args, **kwargs)
            if not wrote:
                wrote.append(True)
                # Lands after the payload read, before the reply goes out.
                self.school.rank_turn_place(
                    self.session_id, int(item["id"]), self.ids["Ava"], option_id=o[0]
                )
            return payload

        def spots(payload: dict[str, Any]) -> list[Any] | None:
            for row in payload.get("live_items") or []:
                if int(row.get("id") or 0) == int(item["id"]):
                    return ((row.get("group_submit") or {}).get("turns") or {}).get("spots")
            return None

        cy = self.students["Cy"]
        with mock.patch.object(self.school, "assemble_student_live_payload", build_with_a_write_mid_way):
            first = cy.get("/api/student/state").get_json()
        self.assertTrue(wrote)
        self.assertEqual(spots(first), [], "the payload was built before Ava placed")
        second = cy.get(
            f"/api/student/state?seq={first['state_seq']}&stamp={first['stamp']}"
        ).get_json()
        self.assertNotIn("unchanged", second, "a write mid-build must not hide behind the new stamp")
        self.assertEqual([s["by_name"] for s in spots(second)], ["Ava"])
        # With nothing new, the poll after that is unchanged again.
        third = cy.get(
            f"/api/student/state?seq={second['state_seq']}&stamp={second['stamp']}"
        ).get_json()
        self.assertTrue(third.get("unchanged"), third)

    def test_rank_together_is_unchanged(self) -> None:
        row = self._rank_row()
        item = self._publish(row)
        o = self._opts(item)
        card = self._card("Ava", item)
        self.assertEqual(card["rank_mode"], "together")
        self.assertNotIn("turns", card)
        rv = self._post("Ava", item, "group-draft", {"tap": o[0]})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        rv = self._post("Cy", item, "rank-turn", {"option_id": o[1]})
        self.assertEqual(rv.status_code, 400)


class GroupFlowsClientTests(unittest.TestCase):
    """Option B and take-turns renderers, wiring and Wonder copy."""

    def test_group_flows_node_harness(self) -> None:
        proc = subprocess.run(
            ["node", str(LMS_DIR / "static" / "group_flows.test.mjs")],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("ok", proc.stdout)

    def test_clients_wire_new_routes(self) -> None:
        student = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        staff = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn('from "/static/group_flows.js"', student)
        self.assertIn('"group-pick", { choice', student)
        self.assertIn('"rank-turn", { option_id', student)
        self.assertIn("/rank-turn/skip", staff)
        self.assertIn("group_rank_mode", staff)
        self.assertIn("groupSubmitVariant", staff)
        # Wonder review: teacher labels for option B.
        self.assertIn('moveOn: "Start group step"', staff)
        self.assertIn('label: "Each student\'s pick"', staff)
        self.assertNotIn('"Move on"', staff)
        self.assertNotIn('"Own picks"', staff)

    def test_group_card_has_one_left_edge(self) -> None:
        """Gate LOW-7: option B and take-turns blocks share the LOW-1 inset
        (measured 20px at 390 and 1280: lc-qa/screenshots/mck-155-c-low7)."""
        css = (LMS_DIR / "static" / "student-portal.css").read_text(encoding="utf-8")
        for cls in ("student-group-wait", "student-group-picks", "student-group-locked", "student-group-sent", "rank-turns"):
            self.assertIn(f".student-live-card .student-group-card > .{cls}", css)

    def test_teacher_skip_names_everyone_and_confirms(self) -> None:
        """Gate LOW-1: rows name who Skip passes; 2+ asks first; says who."""
        staff = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        start = staff.index("const TURNS_COPY = Object.freeze(")
        copy = staff[start : staff.index("});", start) + 3]
        src = "\n".join(
            [copy, "const turnSkipNotes = new Map();", _function_source(staff, "skipNameList"), _function_source(staff, "rankTurnsTeacherHtml")]
        )
        harness = r"""
const vm = require("vm");
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
const ctx = { escapeHtml: (s) => String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;"), out: {} };
vm.createContext(ctx);
vm.runInContext(input.src + `
out.many = rankTurnsTeacherHtml({ turns: [{ team_id: 1, team_name: "Team 1", total: 5, placed: 2, next_names: ["Eli", "Gus"], can_skip: true }] }, 7);
out.one = rankTurnsTeacherHtml({ turns: [{ team_id: 1, team_name: "Team 1", total: 5, placed: 2, next_names: ["Eli"], can_skip: true }] }, 7);
turnSkipNotes.set("7:1", { placed: 2, text: TURNS_COPY.skipped.replace("{names}", skipNameList(["Eli", "Gus"])) });
out.note = rankTurnsTeacherHtml({ turns: [{ team_id: 1, team_name: "Team 1", total: 5, placed: 2, next_names: ["Ava"], can_skip: true }] }, 7);
out.after = rankTurnsTeacherHtml({ turns: [{ team_id: 1, team_name: "Team 1", total: 5, placed: 3, next_names: ["Ben"], can_skip: true }] }, 7);
out.confirm = TURNS_COPY.skipConfirm.replace("{names}", skipNameList(["Eli", "Gus"]));
`, ctx);
console.log(JSON.stringify(ctx.out));
"""
        done = subprocess.run(
            ["node", "-e", harness], input=json.dumps({"src": src}), capture_output=True, text=True, timeout=60, check=False
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertIn("2 of 5 placed · Waiting on Eli and Gus", out["many"])
        self.assertIn('data-skip-count="2"', out["many"])
        self.assertIn('aria-label="Skip Eli and Gus"', out["many"])
        self.assertIn("2 of 5 placed · Eli&#39;s turn", out["one"].replace("'", "&#39;"))
        self.assertIn('data-skip-count="1"', out["one"])
        self.assertIn("Skipped Eli and Gus.", out["note"])
        self.assertNotIn("Skipped", out["after"])
        self.assertEqual(out["confirm"], "Skip Eli and Gus? Each of them loses this turn.")
        self.assertIn("Number(turnSkip.dataset.skipCount) > 1", staff)
        self.assertIn("window.confirm(TURNS_COPY.skipConfirm", staff)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""MCK-155 PR C: group MC "pick alone, then agree" and rank "Take turns".

Real MCR3U M1 C2 deck, real teacher routes (Add New, settings, Publish,
Move on, Skip) and real student routes (group-pick, group-submit,
rank-turn) with one logged-in client per student.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from live_rank import (  # noqa: E402
    TurnConflict,
    apply_turn_place,
    apply_turn_skip,
    apply_turn_undo,
    empty_turns_state,
    turn_next_ids,
)

NAMES = ["Ava", "Ben", "Cy", "Dee"]


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
        rv = self._post("Ava", item, "group-draft", {"choice": "2", "why": "x"})
        self.assertEqual(rv.status_code, 400)
        self.assertIn("own pick first", rv.get_json()["error"])
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
        self.assertIn("before you publish", rv.get_json()["error"])
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


if __name__ == "__main__":
    unittest.main()

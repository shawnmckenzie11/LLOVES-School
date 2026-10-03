#!/usr/bin/env python3
"""MCK-171 Team challenge (a): settings, lock-in rules, I agree, teacher
Lock in, and the teacher/student race blocks.

Real MCR3U M1 C2 deck, real teacher routes (Add New, settings, Publish,
Close, Lock in, Skip) and real student routes (group-draft, group-submit,
rank-agree, rank-turn) with one logged-in client per student.

Two teams of three: Ava + Cy + Eli, Ben + Dee + Fay.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
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
import rank_challenge  # noqa: E402

NAMES = ["Ava", "Ben", "Cy", "Dee", "Eli", "Fay"]
OPTIONS = ["Two points", "A graph", "An equation", "A table"]
# Answer order: o3, o1, o4, o2. Since MCK-176 (#240) new items get opaque
# option ids and students see per-item aliases, so in these tests "oN" is
# a positional token for the Nth authored option. The harness turns it into
# the student alias on every student post, and ``_pos`` maps ids back.
KEY = [2, 0, 3, 1]
KEY_IDS = ["o3", "o1", "o4", "o2"]
TOKEN = re.compile(r"^o[1-6]$")


class AgreeRuleTests(unittest.TestCase):
    """Pure agree helpers."""

    def test_agree_and_clear(self) -> None:
        body = {"kind": "rank", "order": ["o1"], "complete": False}
        body = rank_challenge.with_agree(body, 4, True)
        body = rank_challenge.with_agree(body, 2, True)
        self.assertEqual(rank_challenge.agree_ids(body), [4, 2])
        body = rank_challenge.with_agree(body, 4, False)
        self.assertEqual(rank_challenge.agree_ids(body), [2])
        cleared, had = rank_challenge.clear_agrees(body)
        self.assertTrue(had)
        self.assertEqual(rank_challenge.agree_ids(cleared), [])
        self.assertTrue(cleared["agree_reset"])
        # An agree ends the reset notice.
        self.assertNotIn("agree_reset", rank_challenge.with_agree(cleared, 9, True))
        _, had = rank_challenge.clear_agrees({"kind": "rank"})
        self.assertFalse(had)

    def test_absent_members_never_block(self) -> None:
        self.assertTrue(rank_challenge.all_agreed([1, 2], [1, 2]))
        self.assertTrue(rank_challenge.all_agreed([1, 2, 3], [1, 2]))  # 3 left
        self.assertFalse(rank_challenge.all_agreed([1], [1, 2]))
        self.assertFalse(rank_challenge.all_agreed([1], []))
        self.assertEqual(rank_challenge.agree_counts([1, 3], [1, 2]), (1, 2))
        self.assertEqual(rank_challenge.present_with([2, 1], 5), [1, 2, 5])

    def test_flags(self) -> None:
        self.assertFalse(rank_challenge.flag_on({}, "group_rank_race"))
        self.assertFalse(rank_challenge.flag_on({"group_rank_race": False}, "group_rank_race"))
        self.assertTrue(rank_challenge.flag_on({"group_rank_race": "on"}, "group_rank_race"))


class ChallengeHarness(unittest.TestCase):
    """Class, live session, two teams of three, one client per student."""

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
            rv = student.post("/auth/student-code", data={"code": str(live["session_code"]), "name": name})
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

    def _settings(self, row: dict[str, Any], body: dict[str, Any]):
        return self.client.patch(f"/api/live-sessions/{self.session_id}/items/{row['id']}/settings", json=body)

    def _publish(self, row: dict[str, Any]) -> dict[str, Any]:
        rv = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{row['id']}/publish",
            json={"publish_mode": "group_submit"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        return rv.get_json()["item"]

    def _close(self, item: dict[str, Any]) -> dict[str, Any]:
        rv = self.client.post(f"/api/live-sessions/{self.session_id}/items/{item['id']}/close")
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        return self.school.get_live_session_item(self.session_id, int(item["id"]))

    def _rank_row(self, *, key: bool = True) -> dict[str, Any]:
        body: dict[str, Any] = {"type": "rank", "text": "Rank these.", "options": OPTIONS}
        if key:
            body["rank_key"] = KEY
        return self._add(body)

    def _challenge(self, *, mode: str = "together") -> dict[str, Any]:
        row = self._rank_row()
        body: dict[str, Any] = {"group_rank_race": True}
        if mode != "together":
            body["group_rank_mode"] = mode
        rv = self._settings(row, body)
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        return self._publish(row)

    def _post(self, name: str, item: dict[str, Any], path: str, body: dict[str, Any]):
        """Student POST; positional ``oN`` tokens go out as student aliases."""
        body = dict(body)
        if isinstance(body.get("order"), list):
            body["order"] = self._as_student(item, body["order"])
        for key in ("tap", "option_id"):
            if isinstance(body.get(key), str):
                body[key] = self._as_student(item, [body[key]])[0]
        return self.students[name].post(f"/api/student/live-items/{item['id']}/{path}", json=body)

    # MCK-176 ids --------------------------------------------------------
    def _live(self, item: dict[str, Any]) -> dict[str, Any]:
        return self.school.get_live_session_item(self.session_id, int(item["id"]))

    def _real_ids(self, item: dict[str, Any]) -> list[str]:
        """Real option ids in authored (``OPTIONS``) order, matched by label.

        #240 stores new options shuffled with opaque ids, so neither the id
        nor the stored position says which option was typed first.
        """
        rows = self.school._rank_option_rows(self._live(item))
        by_label = {row["label"]: row["id"] for row in rows}
        if set(by_label) == set(OPTIONS):
            return [by_label[label] for label in OPTIONS]
        return [row["id"] for row in rows]

    def _as_real(self, item: dict[str, Any], tokens: list[Any]) -> list[Any]:
        real = self._real_ids(item)
        return [real[int(t[1:]) - 1] if isinstance(t, str) and TOKEN.match(t) else t for t in tokens]

    def _as_student(self, item: dict[str, Any], tokens: list[Any]) -> list[Any]:
        aliases = self.school.student_rank_aliases(self._live(item))
        real = self._as_real(item, tokens)
        return [aliases.out(t) for t in real] if aliases is not None else real

    def _pos(self, item: dict[str, Any], ids: Any) -> Any:
        """Real ids or student aliases back to positional ``oN`` tokens."""
        if ids is None:
            return None
        live = self._live(item)
        real = self._real_ids(item)
        aliases = self.school.student_rank_aliases(live)
        out = []
        for value in ids:
            opt = value
            if aliases is not None:
                try:
                    opt = aliases.back(value)
                except ValueError:
                    opt = value
            out.append(f"o{real.index(opt) + 1}" if opt in real else value)
        return out

    def _card(self, name: str, item: dict[str, Any]) -> dict[str, Any]:
        live = self.school.get_live_session_item(self.session_id, int(item["id"]))
        return self.school.student_group_submit_state(live, self.ids[name]) or {}

    def _view(self, item: dict[str, Any]) -> dict[str, Any]:
        return self.school.live_session_item_results(self.session_id, int(item["id"]))

    def _row(self, item: dict[str, Any], name: str) -> dict[str, Any]:
        team = self._card(name, item)["team_id"]
        return self.school._group_response_row(int(item["id"]), int(team)) or {}

    def _leave(self, name: str) -> None:
        self.school.mark_live_session_attendee_left(self.session_id, student_id=self.ids[name])

    def _order(self, name: str, item: dict[str, Any], order: list[str]):
        return self._post(name, item, "group-draft", {"order": order})

    def _team(self, name: str, item: dict[str, Any]) -> int:
        return int(self._card(name, item)["team_id"])


class SettingsTests(ChallengeHarness):
    def test_team_challenge_needs_an_answer_order(self) -> None:
        row = self._rank_row(key=False)
        rv = self._settings(row, {"group_rank_race": True})
        self.assertEqual(rv.status_code, 400)
        self.assertEqual(rv.get_json()["error"], "Add an answer order to use Team challenge.")
        # Turning it off never needs a key.
        self.assertEqual(self._settings(row, {"group_rank_race": False}).status_code, 200)

    def test_toggle_and_publish_lock(self) -> None:
        row = self._rank_row()
        rv = self._settings(row, {"group_rank_race": True})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        item = rv.get_json()["item"]["item"]
        self.assertIs(item["group_rank_race"], True)
        self.assertEqual(self._settings(row, {"group_rank_race": "maybe"}).status_code, 400)
        # Shawn 9:41 ET: there is no bonus setting. An old client's key is
        # not a setting at all (400, nothing stored).
        rv = self._settings(row, {"group_rank_race_bonus": False})
        self.assertEqual(rv.status_code, 400)
        live = self.school.get_live_session_item(self.session_id, row["id"])
        self.assertNotIn("group_rank_race_bonus", live["item"])
        published = self._publish(row)
        rv = self._settings(published, {"group_rank_race": False})
        self.assertEqual(rv.status_code, 400)
        self.assertEqual(rv.get_json()["error"], "Team challenge is locked once published.")

    def test_rank_only(self) -> None:
        row = self._add({"type": "mc", "text": "Slope?", "options": ["2", "1", "0", "-1"], "correct_index": 0})
        rv = self._settings(row, {"group_rank_race": True})
        self.assertEqual(rv.status_code, 400)
        self.assertEqual(rv.get_json()["error"], "Team challenge is for rank questions.")

    def test_teacher_list_carries_key_and_flag_for_the_toggle(self) -> None:
        row = self._rank_row()
        self._settings(row, {"group_rank_race": True})
        listed = next(r for r in self.school.list_live_session_items(self.session_id) if int(r["id"]) == int(row["id"]))
        self.assertEqual(self._pos(row, listed["item"]["rank_key"]), KEY_IDS)
        self.assertTrue(listed["item"]["group_rank_race"])

    def test_stale_key_turns_the_challenge_off(self) -> None:
        item = self._challenge()
        live = self.school.get_live_session_item(self.session_id, int(item["id"]))
        live["item"] = {**live["item"], "rank_key": ["o1", "o2"]}
        self.assertFalse(self.school._rank_race_on(live))


class LockInTests(ChallengeHarness):
    def test_agree_resets_on_any_order_change_then_locks(self) -> None:
        item = self._challenge()
        self.assertEqual(self._order("Ava", item, KEY_IDS).status_code, 200)
        card = self._card("Ava", item)
        self.assertEqual(card["race"]["agree"]["count"], 0)
        self.assertEqual(card["race"]["agree"]["of"], 3)
        self.assertTrue(card["race"]["agree"]["can_agree"])
        self.assertFalse(card["can_submit"])
        with mock.patch.object(app_module, "emit_answer_landed") as emit:
            rv = self._post("Ava", item, "rank-agree", {"agree": True, "order": KEY_IDS})
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
            self.assertEqual(emit.call_count, 1)
            self.assertEqual(emit.call_args.kwargs["scope"], "group")
        agree = rv.get_json()["group_submit"]["race"]["agree"]
        self.assertEqual((agree["count"], agree["of"], agree["mine"]), (1, 3, True))
        self.assertEqual(agree["waiting_names"], ["Cy", "Eli"])
        self._post("Cy", item, "rank-agree", {"agree": True, "order": KEY_IDS})
        self.assertEqual(self._card("Eli", item)["race"]["agree"]["count"], 2)
        # Eli moves an item: every agree clears, teammates see the reset,
        # and that one draft emits a postcard.
        moved = ["o1", "o3", "o4", "o2"]
        with mock.patch.object(app_module, "emit_answer_landed") as emit:
            rv = self._order("Eli", item, moved)
            self.assertEqual(rv.status_code, 200)
            self.assertEqual(emit.call_count, 1)
        self.assertNotIn("agrees_cleared", rv.get_json()["group_submit"]["race"])
        ava = self._card("Ava", item)["race"]["agree"]
        self.assertEqual(ava["count"], 0)
        self.assertFalse(ava["mine"])
        self.assertTrue(ava["reset"])
        # A tap (toggle) or a clear is an order change too.
        self._post("Ava", item, "rank-agree", {"agree": True, "order": moved})
        self._post("Ava", item, "group-draft", {"tap": "o2"})
        self.assertEqual(self._card("Cy", item)["race"]["agree"]["count"], 0)
        # A same-order write keeps the agrees and emits nothing.
        self._order("Ava", item, moved)
        self._post("Ava", item, "rank-agree", {"agree": True, "order": moved})
        with mock.patch.object(app_module, "emit_answer_landed") as emit:
            self._order("Cy", item, moved)
            self.assertEqual(emit.call_count, 0)
        self.assertEqual(self._card("Cy", item)["race"]["agree"]["count"], 1)
        self.assertFalse(self._card("Cy", item)["race"]["agree"]["reset"])
        # Not yet takes my agree back.
        self._post("Ava", item, "rank-agree", {"agree": False, "order": moved})
        self.assertEqual(self._card("Cy", item)["race"]["agree"]["count"], 0)
        for name in ("Ava", "Cy"):
            self._post(name, item, "rank-agree", {"agree": True, "order": moved})
        self.assertEqual(self._row(item, "Ava")["submit_count"], 0)
        rv = self._post("Eli", item, "rank-agree", {"agree": True, "order": moved})
        self.assertEqual(rv.status_code, 200)
        card = rv.get_json()["group_submit"]
        self.assertTrue(card["race"]["locked"])
        self.assertEqual(card["race"]["locked_by"], "team")
        self.assertTrue(card["locked"])
        row = self._row(item, "Ava")
        self.assertEqual(row["submit_count"], 1)
        self.assertTrue(row["finalized_at"])
        self.assertEqual(self._pos(item, row["final_answer"]["order"]), moved)
        self.assertEqual(row["final_answer"]["locked_by"], "team")
        self.assertEqual(sorted(json.loads(row["submitter_ids_json"])), sorted(self.ids[n] for n in ("Ava", "Cy", "Eli")))

    def test_sends_are_final_once_locked(self) -> None:
        item = self._challenge()
        self._order("Ava", item, KEY_IDS)
        for name in ("Ava", "Cy", "Eli"):
            self._post(name, item, "rank-agree", {"agree": True, "order": KEY_IDS})
        stamp = self._row(item, "Ava")["finalized_at"]
        rv = self._order("Cy", item, ["o1", "o2", "o3", "o4"])
        self.assertEqual(rv.status_code, 409)
        body = rv.get_json()
        self.assertTrue(body["locked"])
        self.assertEqual(body["error"], "Your group's order is in.")
        self.assertTrue(body["group_submit"]["race"]["locked"])
        rv = self._post("Cy", item, "group-draft", {"clear": True})
        self.assertEqual(rv.status_code, 409)
        rv = self._post("Eli", item, "group-submit", {"order": ["o1", "o2", "o3", "o4"]})
        self.assertEqual(rv.status_code, 409)
        rv = self._post("Eli", item, "rank-agree", {"agree": False})
        self.assertEqual(rv.status_code, 409)
        # Agreeing again to the locked order is a quiet no-op.
        rv = self._post("Eli", item, "rank-agree", {"agree": True, "order": KEY_IDS})
        self.assertEqual(rv.status_code, 200)
        rv = self._post("Eli", item, "group-submit", {"order": KEY_IDS})
        self.assertEqual(rv.status_code, 200)
        row = self._row(item, "Ava")
        self.assertEqual(row["submit_count"], 1)
        self.assertEqual(row["finalized_at"], stamp)
        self.assertEqual(self._pos(item, row["final_answer"]["order"]), KEY_IDS)

    def test_send_is_my_agree(self) -> None:
        item = self._challenge()
        self._order("Ava", item, KEY_IDS)
        rv = self._post("Ava", item, "group-submit", {"order": KEY_IDS})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        card = rv.get_json()["group_submit"]
        self.assertFalse(card["race"]["locked"])
        self.assertEqual(card["race"]["agree"]["count"], 1)
        self.assertEqual(self._row(item, "Ava")["submit_count"], 0)

    def test_stale_or_incomplete_agree_writes_nothing(self) -> None:
        item = self._challenge()
        self._order("Ava", item, KEY_IDS[:2])
        rv = self._post("Cy", item, "rank-agree", {"agree": True})
        self.assertEqual(rv.status_code, 400)
        self.assertFalse(self._card("Cy", item)["race"]["agree"]["can_agree"])
        self._order("Ava", item, KEY_IDS)
        rv = self._post("Cy", item, "rank-agree", {"agree": True, "order": ["o1", "o2", "o3", "o4"]})
        self.assertEqual(rv.status_code, 409)
        body = rv.get_json()
        self.assertEqual(body["reason"], "order_changed")
        self.assertEqual(body["error"], "The order changed. Agree again when you're ready.")
        self.assertEqual(self._pos(item, body["group_submit"]["order"]), KEY_IDS)
        self.assertEqual(self._card("Cy", item)["race"]["agree"]["count"], 0)
        rv = self._post("Cy", item, "rank-agree", {"agree": True, "order": ["o9"]})
        self.assertEqual(rv.status_code, 400)

    def test_absent_members_do_not_block(self) -> None:
        item = self._challenge()
        self._order("Ava", item, KEY_IDS)
        self._leave("Eli")
        card = self._card("Ava", item)
        self.assertEqual(card["race"]["agree"]["of"], 2)
        self._post("Ava", item, "rank-agree", {"agree": True, "order": KEY_IDS})
        rv = self._post("Cy", item, "rank-agree", {"agree": True, "order": KEY_IDS})
        self.assertTrue(rv.get_json()["group_submit"]["race"]["locked"])

    def test_last_holdout_leaving_locks_on_the_next_read(self) -> None:
        item = self._challenge()
        self._order("Ben", item, KEY_IDS)
        self._post("Ben", item, "rank-agree", {"agree": True, "order": KEY_IDS})
        self._post("Dee", item, "rank-agree", {"agree": True, "order": KEY_IDS})
        self.assertEqual(self._row(item, "Ben")["submit_count"], 0)
        self._leave("Fay")
        lane = next(t for t in self._view(item)["race"]["teams"] if t["team_id"] == self._team("Ben", item))
        self.assertTrue(lane["locked"])
        self.assertTrue(self._row(item, "Ben")["finalized_at"])

    def test_teacher_lock_in_for_team(self) -> None:
        item = self._challenge()
        team = self._team("Ava", item)
        url = f"/api/live-sessions/{self.session_id}/items/{item['id']}/rank-lock"
        self._order("Ava", item, KEY_IDS[:3])
        lane = next(t for t in self._view(item)["race"]["teams"] if t["team_id"] == team)
        self.assertFalse(lane["can_lock"])
        rv = self.client.post(url, json={"team_id": team})
        self.assertEqual(rv.status_code, 400)
        self.assertEqual(rv.get_json()["error"], "This group hasn't placed every item yet.")
        self.assertEqual(self.client.post(url, json={}).status_code, 400)
        self._order("Ava", item, KEY_IDS)
        self._post("Ava", item, "rank-agree", {"agree": True, "order": KEY_IDS})
        lane = next(t for t in self._view(item)["race"]["teams"] if t["team_id"] == team)
        self.assertTrue(lane["can_lock"])
        with mock.patch.object(app_module, "emit_answer_landed") as emit:
            rv = self.client.post(url, json={"team_id": team})
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
            self.assertEqual(emit.call_count, 1)
        self.assertTrue(rv.get_json()["locked"])
        row = self._row(item, "Ava")
        self.assertEqual(row["final_answer"]["locked_by"], "teacher")
        self.assertTrue(row["finalized_at"])
        self.assertIsNone(row["last_submitter_student_id"])
        self.assertEqual(self._card("Cy", item)["race"]["locked_by"], "teacher")
        with mock.patch.object(app_module, "emit_answer_landed") as emit:
            rv = self.client.post(url, json={"team_id": team})
            self.assertEqual(rv.status_code, 200)
            self.assertFalse(rv.get_json()["locked"])
            self.assertEqual(emit.call_count, 0)

    def test_teacher_lock_is_for_rank_together_challenges_only(self) -> None:
        turns = self._challenge(mode="turns")
        team = self._team("Ava", turns)
        rv = self.client.post(f"/api/live-sessions/{self.session_id}/items/{turns['id']}/rank-lock", json={"team_id": team})
        self.assertEqual(rv.status_code, 400)

    def test_take_turns_auto_locks_and_stamps(self) -> None:
        item = self._challenge(mode="turns")
        order = ["Ava", "Cy", "Eli", "Ava"]
        for name, opt in zip(order, KEY_IDS):
            rv = self._post(name, item, "rank-turn", {"option_id": opt})
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        card = rv.get_json()["group_submit"]
        self.assertTrue(card["turns"]["done"])
        self.assertTrue(card["race"]["locked"])
        self.assertEqual(card["race"]["locked_by"], "turns")
        self.assertNotIn("agree", card["race"])
        row = self._row(item, "Ava")
        self.assertTrue(row["finalized_at"])
        rv = self._post("Ava", item, "rank-turn", {"undo": True})
        self.assertEqual(rv.status_code, 409)
        # Rank together's agree route is not for Take turns.
        self.assertEqual(self._post("Ben", item, "rank-agree", {"agree": True}).status_code, 400)
        lane = next(t for t in self._view(item)["race"]["teams"] if t["team_id"] == self._team("Ava", item))
        self.assertEqual((lane["placed"], lane["total"], lane["locked"]), (4, 4, True))


class AsTodayTests(ChallengeHarness):
    """Without Team challenge, group rank is byte-for-byte today's flow."""

    def test_plain_rank_together_resends_and_has_no_race(self) -> None:
        item = self._publish(self._rank_row())  # key, but no Team challenge
        self._order("Ava", item, KEY_IDS)
        self.assertEqual(self._post("Ava", item, "group-submit", {"order": KEY_IDS}).status_code, 200)
        rv = self._post("Cy", item, "group-submit", {"order": ["o1", "o2", "o3", "o4"]})
        self.assertEqual(rv.status_code, 200)
        card = rv.get_json()["group_submit"]
        self.assertNotIn("race", card)
        self.assertNotIn("locked", card)
        row = self._row(item, "Ava")
        self.assertEqual(row["submit_count"], 2)
        self.assertIsNone(row["finalized_at"])
        self.assertEqual(self._post("Ava", item, "rank-agree", {"agree": True}).status_code, 400)
        view = self._view(item)
        self.assertNotIn("race", view)
        team = next(t for t in view["rank"]["teams"] if t["team_id"] == self._team("Ava", item))
        self.assertEqual(self._pos(item, team["order"]), ["o1", "o2", "o3", "o4"])  # teacher keeps orders live
        self.assertEqual(
            self.client.post(f"/api/live-sessions/{self.session_id}/items/{item['id']}/rank-lock", json={"team_id": 1}).status_code,
            400,
        )

    def test_plain_take_turns_does_not_stamp(self) -> None:
        row = self._rank_row()
        self._settings(row, {"group_rank_mode": "turns"})
        item = self._publish(row)
        for name, opt in zip(["Ava", "Cy", "Eli", "Ava"], KEY_IDS):
            rv = self._post(name, item, "rank-turn", {"option_id": opt})
        self.assertTrue(rv.get_json()["group_submit"]["turns"]["done"])
        self.assertNotIn("race", rv.get_json()["group_submit"])
        self.assertIsNone(self._row(item, "Ava")["finalized_at"])


class NoLeakTests(ChallengeHarness):
    """The key and team orders never reach a student (or the lanes) early."""

    def _student_payload(self, name: str) -> str:
        rv = self.students[name].get("/api/student/state")
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True)[:300])
        return rv.get_data(as_text=True)

    def test_no_key_or_other_orders_before_close(self) -> None:
        item = self._challenge()
        self._order("Ava", item, KEY_IDS)
        for name in ("Ava", "Cy", "Eli"):
            self._post(name, item, "rank-agree", {"agree": True, "order": KEY_IDS})
        self._order("Ben", item, ["o4", "o2", "o1", "o3"])
        ben = self._student_payload("Ben")
        self.assertNotIn("rank_key", ben)
        data = json.loads(ben)
        rows = [r for r in (data.get("active_questions") or []) + (data.get("live_items") or []) if int(r.get("id") or 0) == int(item["id"])]
        self.assertTrue(rows)
        for row in rows:
            card = row["group_submit"]
            self.assertEqual(self._pos(item, card["order"]), ["o4", "o2", "o1", "o3"])  # own draft only
            self.assertEqual(card["race"]["teams_locked"], 1)
            self.assertEqual(card["race"]["teams_total"], 2)
            self.assertNotIn("results", card["race"])
            self.assertIsNone(row.get("results"))
            self.assertNotIn("rank_key", json.dumps(row["content"]))
        # Ava's locked order appears nowhere in Ben's payload.
        compact = ben.replace(" ", "")
        for ids in (self._as_student(item, KEY_IDS), self._as_real(item, KEY_IDS)):
            self.assertNotIn(json.dumps(ids).replace(" ", ""), compact)
        # No real option id reaches the phone at all (MCK-176 aliases).
        for real in self._real_ids(item):
            self.assertNotIn(f'"{real}"', ben)
        # Students never read the word "race" in copy (keys aside).
        self.assertNotIn("Race", ben)

    def test_teacher_lanes_and_stack_carry_no_orders_until_close(self) -> None:
        item = self._challenge()
        self._order("Ava", item, KEY_IDS)
        for name in ("Ava", "Cy", "Eli"):
            self._post(name, item, "rank-agree", {"agree": True, "order": KEY_IDS})
        view = self._view(item)
        race = view["race"]
        self.assertEqual(race["teams_locked"], 1)
        self.assertEqual(race["teams_total"], 2)
        self.assertNotIn("bonus", json.dumps(race))
        self.assertNotIn("order", json.dumps(race))
        self.assertNotIn("finalized", json.dumps(race))
        self.assertEqual([lane["slot"] for lane in race["teams"]], [0, 1])
        self.assertTrue(all(team["order"] is None for team in view["rank"]["teams"]))
        self.assertEqual(view["rank"]["class_order"], [])
        self.assertEqual(view["rank"]["teams"][0]["status"], "submitted")
        # Light poll carries the lanes and repaints on an agree.
        light = self.school.light_group_results(self.session_id)[str(item["id"])]
        self.assertEqual(light["race"]["teams_locked"], 1)
        seq = light["response_seq"]
        self._order("Ben", item, KEY_IDS)
        self._post("Ben", item, "rank-agree", {"agree": True, "order": KEY_IDS})
        light = self.school.light_group_results(self.session_id)[str(item["id"])]
        self.assertNotEqual(light["response_seq"], seq)
        lane = next(t for t in light["race"]["teams"] if t["team_id"] == self._team("Ben", item))
        self.assertEqual((lane["agree"], lane["agree_of"]), (1, 3))
        closed = self._close(item)
        view = self._view(closed)
        team = next(t for t in view["rank"]["teams"] if t["team_id"] == self._team("Ava", item))
        self.assertEqual(self._pos(item, team["order"]), KEY_IDS)

    def test_teacher_close_reads_closed_not_timesup(self) -> None:
        """Teacher Close with no timer: the unlocked team reads race.closed."""
        item = self._challenge()
        self._order("Ava", item, KEY_IDS)
        closed = self._close(item)
        self.assertEqual(closed["item"][rank_challenge.RACE_CLOSED_BY], "teacher")
        race = self._card("Ava", item)["race"]
        self.assertEqual(race["closed_unlocked"], "teacher")
        self.assertFalse(race["locked"])

    def test_timer_zero_reads_timesup(self) -> None:
        """The SessionTimer's 0:00 close: the unlocked team reads race.timesup."""
        from datetime import datetime, timedelta

        item = self._challenge()
        self._order("Ava", item, KEY_IDS)
        stamp = (datetime.now() - timedelta(seconds=120)).replace(microsecond=0)
        with self.school._lock:
            self.school.conn.execute(
                "UPDATE live_session_items SET published_at = ? WHERE id = ?",
                (stamp.isoformat(), int(item["id"])),
            )
            self.school.conn.commit()
        self.school.set_live_session_teacher_state(self.session_id, timer_closes_answers=True)
        self.school.game.start_session_timer(self.class_id, 1)
        started = (datetime.now() - timedelta(seconds=61)).replace(microsecond=0)
        with self.school.game._lock:
            self.school.game.conn.execute(
                "UPDATE games SET round_started_at = ? WHERE class_id = ? AND status != 'ended'",
                (started.isoformat(), self.class_id),
            )
            self.school.game.conn.commit()
        self.assertIn(int(item["id"]), self.school.close_answers_if_timer_expired(self.session_id))
        live = self.school.get_live_session_item(self.session_id, int(item["id"]))
        self.assertEqual(live["status"], "closed")
        self.assertEqual(live["item"][rank_challenge.RACE_CLOSED_BY], "timer")
        self.assertEqual(self._card("Ava", item)["race"]["closed_unlocked"], "timer")

    def test_locked_team_has_no_closed_line(self) -> None:
        item = self._challenge()
        self._order("Ava", item, KEY_IDS)
        for name in ("Ava", "Cy", "Eli"):
            rv = self._post(name, item, "rank-agree", {"agree": True, "order": KEY_IDS})
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        self._close(item)
        race = self._card("Ava", item)["race"]
        self.assertTrue(race["locked"])
        self.assertNotIn("closed_unlocked", race)

    def test_absent_team_lane(self) -> None:
        item = self._challenge()
        for name in ("Ben", "Dee", "Fay"):
            self._leave(name)
        lanes = {t["team_id"]: t for t in self._view(item)["race"]["teams"]}
        ben = lanes[next(k for k in lanes if k != self._team("Ava", item))]
        self.assertTrue(ben["absent"])
        self.assertEqual(ben["present"], 0)
        self.assertEqual(ben["members"], 3)  # away pips (dashed) on the projector
        self.assertFalse(ben["can_lock"])
        self.assertFalse(lanes[self._team("Ava", item)]["absent"])

    def test_count_is_present_teams_only(self) -> None:
        """'{k} of {n} teams locked in' leaves out a team with nobody here."""
        item = self._challenge()
        for name in ("Ben", "Dee", "Fay"):
            self._leave(name)
        race = self._view(item)["race"]
        self.assertEqual((race["teams_locked"], race["teams_total"]), (0, 1))
        card = self._card("Ava", item)["race"]
        self.assertEqual((card["teams_locked"], card["teams_total"]), (0, 1))


class PresenceCreditTests(ChallengeHarness):
    """Who was seen present while the challenge was open (credit list)."""

    def _rejoin(self, name: str) -> None:
        code = self.school.get_live_session(self.session_id)["session_code"]
        rv = self.students[name].post("/auth/student-code", data={"code": str(code), "name": name})
        self.assertEqual(rv.status_code, 302, rv.get_data(as_text=True)[:300])

    def _seen(self, item: dict[str, Any], name: str) -> set[int]:
        return self.school._rank_race_seen_ids(int(item["id"]), self._team(name, item))

    def test_present_all_along_is_seen(self) -> None:
        item = self._challenge()
        self._close(item)
        self.assertIn(self.ids["Ava"], self._seen(item, "Ava"))

    def test_present_briefly_mid_question_is_seen(self) -> None:
        self._leave("Eli")
        item = self._challenge()
        team = self._team("Ava", item)
        self._view(item)  # lanes poll while Eli is away
        self.assertNotIn(self.ids["Eli"], self.school._rank_race_seen_ids(int(item["id"]), team))
        self._rejoin("Eli")
        self._view(item)  # Eli shows up mid-question
        self._leave("Eli")  # and drops before Close
        self._close(item)
        self.assertIn(self.ids["Eli"], self.school._rank_race_seen_ids(int(item["id"]), team))

    def test_never_present_is_not_seen(self) -> None:
        self._leave("Eli")
        item = self._challenge()
        team = self._team("Ava", item)
        self._view(item)
        self._close(item)
        self.assertNotIn(self.ids["Eli"], self.school._rank_race_seen_ids(int(item["id"]), team))


class SetupClientTests(unittest.TestCase):
    """Group line toggle, help and live chips (Wonder v3 copy)."""

    def test_group_setup_node_harness(self) -> None:
        import subprocess

        proc = subprocess.run(
            ["node", str(LMS_DIR / "static" / "rank_challenge_setup.test.mjs")],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("ok", proc.stdout)

    def test_staff_wires_the_patch(self) -> None:
        staff = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("JSON.stringify({ group_rank_race: on })", staff)
        self.assertIn("rankRaceSettings(item, card)", staff)
        self.assertNotIn("group_rank_race_bonus", staff)


class DisplayOrderHookTests(ChallengeHarness):
    """MCK-176: lanes and phone read one shown order; phones see aliases only."""

    def test_race_options_follow_the_display_order(self) -> None:
        item = self._challenge()
        live = self._live(item)
        shown = self.school.rank_display_order(live)
        self.assertTrue(shown)  # stamped at publish (#240)
        self.assertEqual([o["id"] for o in self._view(item)["race"]["options"]], shown)
        aliases = self.school.student_rank_aliases(live)
        self.assertEqual(
            [o["id"] for o in self._card("Ava", item)["race"]["options"]],
            [aliases.out(opt) for opt in shown],
        )
        labels = {row["id"]: row["label"] for row in self.school._rank_option_rows(live)}
        self.assertEqual([o["label"] for o in self._view(item)["race"]["options"]], [labels[o] for o in shown])

    def test_reads_rank_display_order_and_falls_back(self) -> None:
        from unittest import mock

        item = self._challenge()
        shown = self._as_real(item, ["o4", "o2", "o1", "o3"])
        with mock.patch.object(type(self.school), "rank_display_order", return_value=shown):
            teacher = [o["id"] for o in self._view(item)["race"]["options"]]
            student = [o["id"] for o in self._card("Ava", item)["race"]["options"]]
        self.assertEqual(teacher, shown)
        self.assertEqual(self._pos(item, student), ["o4", "o2", "o1", "o3"])
        # None (no valid stored order) falls back to the stored option order.
        with mock.patch.object(type(self.school), "rank_display_order", return_value=None):
            stored = [row["id"] for row in self.school._rank_option_rows(self._live(item))]
            self.assertEqual([o["id"] for o in self._view(item)["race"]["options"]], stored)

    def test_scoring_ignores_the_display_order(self) -> None:
        """Score by option id against the key, whatever order was shown."""
        item = self._challenge()
        self._order("Ava", item, KEY_IDS)
        for name in ("Ava", "Cy", "Eli"):
            rv = self._post(name, item, "rank-agree", {"agree": True, "order": KEY_IDS})
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        live = self._live(item)
        row = self.school._group_response_row(int(live["id"]), self._team("Ava", item)) or {}
        stored = self.school._rank_race_draft_order(live, row)
        self.assertEqual(stored, self._as_real(item, KEY_IDS))  # real ids on the server
        from live_rank import rank_race_score

        self.assertEqual(rank_race_score(stored, self._as_real(item, KEY_IDS))["right"], 4)

    def test_options_carry_no_key(self) -> None:
        item = self._challenge()
        race = self._card("Ava", item)["race"]
        self.assertEqual(set(self._pos(item, [o["id"] for o in race["options"]])), set(KEY_IDS))
        self.assertTrue(all(set(o) == {"id", "label"} for o in race["options"]))


class AliasTests(ChallengeHarness):
    """MCK-176 aliases on every Team challenge student path."""

    def _payloads(self, name: str, item: dict[str, Any]) -> list[str]:
        state = self.students[name].get("/api/student/state").get_data(as_text=True)
        card = json.dumps(self._card(name, item))
        return [state, card]

    def _assert_no_real_ids(self, item: dict[str, Any], *payloads: str) -> None:
        for real in self._real_ids(item):
            for payload in payloads:
                self.assertNotIn(f'"{real}"', payload)

    def test_no_real_option_id_reaches_a_student_payload(self) -> None:
        item = self._challenge()
        rv = self._order("Ava", item, KEY_IDS)
        draft = rv.get_data(as_text=True)
        agrees = [
            self._post(n, item, "rank-agree", {"agree": True, "order": KEY_IDS}).get_data(as_text=True)
            for n in ("Ava", "Cy")
        ]
        self._assert_no_real_ids(item, draft, *agrees, *self._payloads("Ava", item), *self._payloads("Ben", item))
        # Locked, conflict (409) and closed cards too.
        locked = self._post("Eli", item, "rank-agree", {"agree": True, "order": KEY_IDS})
        late = self._post("Eli", item, "rank-agree", {"agree": False, "order": KEY_IDS})
        self.assertEqual(late.status_code, 409)
        self._order("Ben", item, ["o1", "o2", "o3", "o4"])
        stale = self._post("Dee", item, "rank-agree", {"agree": True, "order": KEY_IDS})
        self.assertEqual(stale.status_code, 409)
        self._close(item)
        self._assert_no_real_ids(
            item, locked.get_data(as_text=True), late.get_data(as_text=True), stale.get_data(as_text=True),
            *self._payloads("Ava", item), *self._payloads("Ben", item),
        )

    def test_take_turns_payloads_carry_aliases_only(self) -> None:
        item = self._challenge(mode="turns")
        bodies = []
        for name, opt in zip(["Ava", "Cy", "Eli", "Ava"], KEY_IDS):
            rv = self._post(name, item, "rank-turn", {"option_id": opt})
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
            bodies.append(rv.get_data(as_text=True))
        self._assert_no_real_ids(item, *bodies, *self._payloads("Ava", item))
        row = self._row(item, "Ava")
        self.assertEqual(self._pos(item, row["final_answer"]["order"]), KEY_IDS)

    def test_unknown_or_foreign_alias_is_400_and_saves_nothing(self) -> None:
        item = self._challenge()
        other = self._publish(self._rank_row())  # another keyed rank item
        self._order("Ava", item, KEY_IDS)
        before = self._row(item, "Ava")
        foreign = self._as_student(other, KEY_IDS)
        for path, body in (
            ("group-draft", {"order": foreign}),
            ("group-draft", {"order": ["rdeadbeef00", *self._as_student(item, KEY_IDS[1:])]}),
            ("rank-agree", {"agree": True, "order": foreign}),
            ("group-submit", {"order": foreign}),
        ):
            rv = self.students["Ava"].post(f"/api/student/live-items/{item['id']}/{path}", json=body)
            self.assertEqual(rv.status_code, 400, (path, rv.get_data(as_text=True)))
        after = self._row(item, "Ava")
        self.assertEqual(after["proposed_answer"], before["proposed_answer"])
        self.assertEqual(after["submit_count"], 0)
        self.assertEqual(self._card("Ava", item)["race"]["agree"]["count"], 0)

    def test_real_ids_from_an_old_tab_still_work(self) -> None:
        item = self._challenge()
        real = self._as_real(item, KEY_IDS)
        rv = self.students["Ava"].post(f"/api/student/live-items/{item['id']}/group-draft", json={"order": real})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        for name in ("Ava", "Cy", "Eli"):
            rv = self.students[name].post(
                f"/api/student/live-items/{item['id']}/rank-agree", json={"agree": True, "order": real}
            )
            self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        self.assertTrue(rv.get_json()["group_submit"]["race"]["locked"])
        self.assertEqual(self._row(item, "Ava")["final_answer"]["order"], real)


if __name__ == "__main__":
    unittest.main()

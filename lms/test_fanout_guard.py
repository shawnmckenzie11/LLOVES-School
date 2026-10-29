#!/usr/bin/env python3
"""One student's action must not rebuild every other phone.

Two teams of three and two students with no team share one live session.
After each student action the other team's stamps and a solo stamp stay
put. The actor and that actor's teammates move. Publish, close, and end
still move every joined phone.

``INDIVIDUAL_ROWS`` is the staff question-type list. A new type fails
this module until a row is added.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Callable

LMS_DIR = Path(__file__).resolve().parent
REPO = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from live_news_wire import reset_logs  # noqa: E402
from school_db import LIVE_QUESTION_TYPES  # noqa: E402
from test_team_draw_mck242 import _INKED_PNG, _png_b64  # noqa: E402

# Whole-second ``updated_at`` values need a gap past one second.
GAP_S = 1.15

TEAM_ACTIONS = (
    "group_mc_pick",
    "group_mc_send",
    "rank_place",
    "rank_check",
    "rank_send",
    "rank_lock",
    "join",
    "rejoin",
    "team_draw",
)

INDIVIDUAL_ROWS = (
    "mc",
    "numeric",
    "poll",
    "rank",
    "team_draw",
)

_MC = ["Red", "Blue", "Green", "Gold"]


class FanoutGuardTests(unittest.TestCase):
    """Live session: team A, team B, and two solos."""

    def setUp(self) -> None:
        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "school.sqlite",
            data_dir=root,
            testing=True,
            live_database_url=None,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.staff = self.app.test_client()
        self.school.activate_from_semester_json()
        course = self.school.conn.execute(
            "SELECT code FROM ontario_courses ORDER BY code LIMIT 1"
        ).fetchone()
        self.assertIsNotNone(course)
        code = str(course["code"])
        teacher = self.school.register_staff("staff@example.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code=code
        )
        self.seats = [f"seat{n:02d}" for n in range(1, 9)]
        self.class_id = int(
            self.school.game.create_class(
                year="2026/27",
                semester="Semester 1",
                course_code=code,
                days_preset="M/W/F",
                time_label="2:00pm",
                codenames=self.seats,
                offering_id=int(offering["id"]),
                teacher_user_id=int(teacher["id"]),
            )["id"]
        )
        self.staff.get("/auth/google?portal=staff")
        self.staff.get("/auth/google/callback?email=staff@example.com&name=Staff")
        verify = self.school.get_user_by_email("staff@example.com")["verification_code"]
        self.staff.post("/verify-email", data={"code": verify})
        live = self.school.start_live_class_session(self.class_id, int(teacher["id"]))
        self.session_id = int(live["id"])
        self.session_code = str(live["session_code"])
        self.staff.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={
                "live_module": "M1",
                "live_slot": "C2",
                "stage": "round",
                "page_id": "round_1",
            },
        )
        self.school.game.begin_game(self.class_id)
        with self.school.game._lock:
            rows = self.school.game.conn.execute(
                "SELECT id, codename FROM students WHERE class_id = ? ORDER BY id",
                (self.class_id,),
            ).fetchall()
        by_name = {str(row["codename"]): int(row["id"]) for row in rows}
        self.ids = [by_name[seat] for seat in self.seats]
        self.phones: dict[int, Any] = {}
        self.present: set[int] = set()
        for index in (0, 1, 3, 4, 5, 6, 7):
            self._enter(index)
        team_ids = self.ids[:6]
        self.school.setup_live_session_groups(
            self.session_id,
            n_teams=2,
            mode="manual",
            present_ids=team_ids,
            assignments=[
                {"student_id": sid, "team_index": 0 if i < 3 else 1}
                for i, sid in enumerate(team_ids)
            ],
        )
        self.school.ensure_live_session_items(self.session_id)
        self.rank_id = 0
        self._q = 0

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)
        reset_logs()
        from bank_image_mirror import clear_bank_image_refusals
        clear_bank_image_refusals()
        self.school.close()
        self.tmp.cleanup()

    def _enter(self, index: int) -> None:
        """Join one seat and keep the phone."""
        client = self.app.test_client()
        rv = client.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": self.seats[index]},
        )
        self._ok_status(rv, 302)
        self.phones[index] = client
        self.present.add(index)

    def _ok_status(self, rv: Any, code: int) -> None:
        """Fail with the status and a short error, never a session code."""
        if rv.status_code == code:
            return
        err = ""
        body = rv.get_json(silent=True)
        if isinstance(body, dict):
            err = str(body.get("error") or "")[:160]
        self.fail(f"status {rv.status_code} {err}")

    def _spec(self, kind: str, group: bool) -> dict[str, Any]:
        """Staff add-question body for one live type."""
        self._q += 1
        stem = f"Question {self._q}"
        if kind == "mc":
            return {
                "type": "mc",
                "text": stem,
                "options": list(_MC),
                "correct_index": 0,
            }
        if kind == "numeric":
            return {
                "type": "numeric",
                "text": stem,
                "correct_answer": "4",
                "tolerance": 0,
            }
        if kind == "poll":
            return {"type": "poll", "text": stem}
        if kind == "rank":
            body: dict[str, Any] = {
                "type": "rank",
                "text": stem,
                "options": list(_MC),
            }
            if group:
                body["rank_key"] = [0, 1, 2, 3]
            return body
        if kind == "team_draw":
            return {"type": "team_draw", "text": stem}
        self.fail(f"unknown type {kind}")
        return {}

    def _add(self, kind: str, group: bool) -> dict[str, Any]:
        """Add one question and return its lifecycle row."""
        spec = self._spec(kind, group)
        rv = self.staff.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/add-question",
            json={"page_number": 4, "stage": "round", **spec},
        )
        self._ok_status(rv, 200)
        item_id = rv.get_json()["placement"]["item"]["id"]
        matches = [
            row
            for row in self.school.list_live_session_items(self.session_id)
            if str(row.get("item_id") or "") == str(item_id)
            and str(row.get("status") or "") != "closed"
            and self._row_type(row) == kind
        ]
        self.assertTrue(matches, "new question did not mint a lifecycle row")
        return matches[-1]

    def _row_type(self, row: dict[str, Any]) -> str:
        """Question type stored on one lifecycle row."""
        item = row.get("item") if isinstance(row.get("item"), dict) else {}
        return str(item.get("type") or row.get("kind") or "")

    def _publish(self, row: dict[str, Any], group: bool) -> dict[str, Any]:
        """Publish one lifecycle row and return the published item."""
        mode = "group_submit" if group else "individual"
        rv = self.staff.post(
            f"/api/live-sessions/{self.session_id}/items/{row['id']}/publish",
            json={"publish_mode": mode},
        )
        self._ok_status(rv, 200)
        item = dict((rv.get_json() or {}).get("item") or {})
        item["id"] = int(item.get("id") or row["id"])
        if item.get("prompt_id") in (None, ""):
            stored = self.school.get_live_session_item(self.session_id, int(row["id"]))
            item["prompt_id"] = stored.get("prompt_id")
        return item

    def _close(self, item: dict[str, Any]) -> None:
        """Close one lifecycle row."""
        rv = self.staff.post(
            f"/api/live-sessions/{self.session_id}/items/{item['id']}/close"
        )
        self._ok_status(rv, 200)

    def _close_open(self) -> None:
        """Close every active item. Not part of a student guard."""
        for row in self.school.list_live_session_items(self.session_id):
            if str(row.get("status") or "") != "active":
                continue
            if str(row.get("kind") or "") == "whiteboard":
                continue
            self._close(row)

    def _full(self, index: int) -> dict[str, Any]:
        """One full student poll."""
        rv = self.phones[index].get("/api/student/state")
        self._ok_status(rv, 200)
        body = rv.get_json()
        self.assertIsInstance(body, dict)
        return body

    def _card(self, index: int, item_id: int) -> dict[str, Any]:
        """The active card for one lifecycle id, or an empty dict."""
        for row in self._full(index).get("active_questions") or []:
            if int(row.get("id") or 0) == int(item_id):
                return row
        return {}

    def _rank_ids(self, index: int, item_id: int) -> list[str]:
        """Option ids exactly as this phone lists them."""
        card = self._card(index, item_id)
        content = card.get("content") if isinstance(card.get("content"), dict) else {}
        opts = content.get("rank_options") or card.get("rank_options") or []
        found = [str(opt.get("id")) for opt in opts if isinstance(opt, dict) and opt.get("id")]
        self.assertTrue(found, "rank list missing")
        return found

    def _turns(self, index: int, item_id: int) -> dict[str, Any]:
        """Take-turns block on this phone, or an empty dict."""
        card = self._card(index, item_id)
        turns = (card.get("group_submit") or {}).get("turns") or {}
        return turns if isinstance(turns, dict) else {}

    def _holder(self, item_id: int) -> int | None:
        """Joined teammate whose phone says it can place, if any."""
        for index in (0, 1, 2):
            if index not in self.present:
                continue
            if self._turns(index, item_id).get("can_place"):
                return index
        return None

    def _post_item(self, index: int, item_id: int, path: str, body: dict[str, Any]) -> Any:
        """Student POST against one live item."""
        return self.phones[index].post(
            f"/api/student/live-items/{item_id}/{path}",
            json=body,
        )

    def _snap(self, index: int) -> tuple[Any, Any, str]:
        """Poll seq, poll stamp, and the direct per-student stamp."""
        body = self._full(index)
        direct = self.school.live_student_poll_stamp(
            self.session_id, self.class_id, self.ids[index]
        )
        return body.get("state_seq"), body.get("stamp"), direct

    def _assert_same(self, index: int, snap: tuple[Any, Any, str], label: str) -> None:
        """This phone's poll and direct stamp did not move."""
        seq, stamp, direct = snap
        rv = self.phones[index].get(
            "/api/student/state",
            query_string={"seq": seq, "stamp": stamp},
        )
        self._ok_status(rv, 200)
        body = rv.get_json() or {}
        self.assertTrue(body.get("unchanged"), label)
        now = self.school.live_student_poll_stamp(
            self.session_id, self.class_id, self.ids[index]
        )
        self.assertEqual(now, direct, label)

    def _assert_moved(self, index: int, snap: tuple[Any, Any, str], label: str) -> None:
        """This phone rebuilt and its direct stamp changed."""
        seq, stamp, direct = snap
        rv = self.phones[index].get(
            "/api/student/state",
            query_string={"seq": seq, "stamp": stamp},
        )
        self._ok_status(rv, 200)
        body = rv.get_json() or {}
        self.assertFalse(body.get("unchanged"), label)
        now = self.school.live_student_poll_stamp(
            self.session_id, self.class_id, self.ids[index]
        )
        self.assertNotEqual(now, direct, label)

    def _guard_team(self, actor: int, action: Callable[[], None], label: str) -> None:
        """Teammates move. The other team and solos stay."""
        mates = [i for i in (0, 1, 2) if i != actor and i in self.present]
        watchers = [i for i in (3, 4, 5, 6, 7) if i in self.present]
        before = {i: self._snap(i) for i in [actor, *mates, *watchers]}
        time.sleep(GAP_S)
        action()
        self._assert_moved(actor, before[actor], label)
        for index in mates:
            self._assert_moved(index, before[index], f"{label} mate {index}")
        for index in watchers:
            self._assert_same(index, before[index], f"{label} watcher {index}")

    def _guard_join(self, action: Callable[[], None], label: str) -> None:
        """A join moves the team already in the room and leaves everyone else."""
        mates = [i for i in (0, 1) if i in self.present]
        watchers = [i for i in (3, 4, 5, 6, 7) if i in self.present]
        before = {i: self._snap(i) for i in [*mates, *watchers]}
        time.sleep(GAP_S)
        action()
        for index in mates:
            self._assert_moved(index, before[index], f"{label} mate {index}")
        for index in watchers:
            self._assert_same(index, before[index], f"{label} watcher {index}")
        body = self._full(2)
        self.assertTrue(body.get("stamp"), label)
        self.assertFalse(body.get("unchanged"), label)

    def _guard_self(self, actor: int, action: Callable[[], None], label: str) -> None:
        """Only the actor moves."""
        others = [i for i in sorted(self.present) if i != actor]
        before = {i: self._snap(i) for i in [actor, *others]}
        time.sleep(GAP_S)
        action()
        self._assert_moved(actor, before[actor], label)
        for index in others:
            self._assert_same(index, before[index], f"{label} other {index}")

    def _guard_all(self, action: Callable[[], None], label: str) -> None:
        """Every joined phone moves."""
        before = {i: self._snap(i) for i in sorted(self.present)}
        time.sleep(GAP_S)
        action()
        for index in sorted(self.present):
            self._assert_moved(index, before[index], f"{label} seat {index}")

    def group_mc_pick(self) -> None:
        """Each present teammate's pick stays on that team."""
        row = self._add("mc", True)
        self.mc = self._publish(row, True)
        choice = _MC[0]

        def pick(index: int) -> None:
            rv = self._post_item(index, int(self.mc["id"]), "group-pick", {"choice": choice})
            self._ok_status(rv, 200)

        self._guard_team(0, lambda: pick(0), "group_mc_pick")
        self._guard_team(1, lambda: pick(1), "group_mc_pick_mate")

    def group_mc_send(self) -> None:
        """The shared send stays on that team."""

        def send() -> None:
            """Send the pick both present teammates already share."""
            rv = self._post_item(0, int(self.mc["id"]), "group-submit", {})
            self._ok_status(rv, 200)

        self._guard_team(0, send, "group_mc_send")

    def rank_place(self) -> None:
        """The first take-turns placement stays on that team."""
        self._close_open()
        row = self._add("rank", True)
        rv = self.staff.patch(
            f"/api/live-sessions/{self.session_id}/items/{row['id']}/settings",
            json={"group_rank_mode": "turns"},
        )
        self._ok_status(rv, 200)
        item = self._publish(row, True)
        self.rank_id = int(item["id"])
        holder = self._holder(self.rank_id)
        self.assertIsNotNone(holder)
        assert holder is not None
        choice = self._rank_ids(holder, self.rank_id)[0]

        def place() -> None:
            """Place the first card."""
            rv = self._post_item(holder, self.rank_id, "rank-turn", {"option_id": choice})
            self._ok_status(rv, 200)

        self._guard_team(holder, place, "rank_place")

    def rank_check(self) -> None:
        """One check of the partial order stays on that team."""

        def check() -> None:
            """Check the partial order once."""
            rv = self._post_item(0, self.rank_id, "rank-check", {})
            self._ok_status(rv, 200)

        self._guard_team(0, check, "rank_check")

    def rank_send(self) -> None:
        """Each remaining placement, then the send, stays on that team."""
        for _ in range(6):
            holder = self._holder(self.rank_id)
            if holder is None:
                break
            opts = self._rank_ids(holder, self.rank_id)
            used = {
                str(spot.get("option_id"))
                for spot in (self._turns(holder, self.rank_id).get("spots") or [])
                if isinstance(spot, dict)
            }
            left = [opt for opt in opts if opt not in used]
            self.assertTrue(left, "rank send stalled")
            choice = left[0]

            def place(index: int = holder, option_id: str = choice) -> None:
                """Place one remaining card."""
                rv = self._post_item(index, self.rank_id, "rank-turn", {"option_id": option_id})
                self._ok_status(rv, 200)

            self._guard_team(holder, place, "rank_send_place")
        self.assertIsNone(self._holder(self.rank_id))

        def send() -> None:
            """Send the finished order."""
            rv = self._post_item(0, self.rank_id, "group-submit", {})
            self._ok_status(rv, 200)

        self._guard_team(0, send, "rank_send")

    def rank_lock(self) -> None:
        """One Lock in our order tap stays on that team.

        Placements finish the take-turns order first. Only the rank-agree
        tap is inside the guard.
        """
        self._close_open()
        row = self._add("rank", True)
        rv = self.staff.patch(
            f"/api/live-sessions/{self.session_id}/items/{row['id']}/settings",
            json={"group_rank_mode": "turns", "group_rank_race": True},
        )
        self._ok_status(rv, 200)
        item = self._publish(row, True)
        item_id = int(item["id"])
        for _ in range(8):
            holder = self._holder(item_id)
            if holder is None:
                break
            opts = self._rank_ids(holder, item_id)
            used = {
                str(spot.get("option_id"))
                for spot in (self._turns(holder, item_id).get("spots") or [])
                if isinstance(spot, dict)
            }
            left = [opt for opt in opts if opt not in used]
            self.assertTrue(left, "rank lock stalled")
            placed = self._post_item(
                holder, item_id, "rank-turn", {"option_id": left[0]}
            )
            self._ok_status(placed, 200)
        self.assertIsNone(self._holder(item_id), "rank lock order is not complete")
        order = [
            str(spot.get("option_id"))
            for spot in (self._turns(0, item_id).get("spots") or [])
            if isinstance(spot, dict) and spot.get("option_id")
        ]
        self.assertEqual(len(order), 4, "rank lock order is short")

        def lock() -> None:
            """Lock the finished order."""
            locked = self._post_item(
                0, item_id, "rank-agree", {"agree": True, "order": order}
            )
            self._ok_status(locked, 200)

        self._guard_team(0, lock, "rank_lock")

    def join(self) -> None:
        """A first join moves that team only."""

        def act() -> None:
            """Join the late seat."""
            self._enter(2)

        self._guard_join(act, "join")

    def rejoin(self) -> None:
        """A rejoin moves that team only. The leave is outside the guard."""
        self.school.mark_live_session_attendee_left(
            self.session_id, student_id=self.ids[2]
        )
        self.present.discard(2)

        def act() -> None:
            """Rejoin the same phone."""
            rv = self.phones[2].post(
                "/auth/student-code",
                data={"code": self.session_code, "name": self.seats[2]},
            )
            self._ok_status(rv, 302)
            self.present.add(2)

        self._guard_join(act, "rejoin")

    def team_draw(self) -> None:
        """A team drawing stays on that team."""
        self._close_open()
        row = self._add("team_draw", True)
        item = self._publish(row, True)

        def draw() -> None:
            """Submit the team drawing."""
            rv = self._post_item(
                0,
                int(item["id"]),
                "team-draw",
                {"image": _png_b64(_INKED_PNG)},
            )
            self._ok_status(rv, 200)

        self._guard_team(0, draw, "team_draw")

    def _answer(self, index: int, item: dict[str, Any], kind: str) -> None:
        """One individual answer for this question type."""
        if kind == "team_draw":
            rv = self._post_item(
                index,
                int(item["id"]),
                "team-draw",
                {"image": _png_b64(_INKED_PNG)},
            )
            self._ok_status(rv, 200)
            return
        if kind == "rank":
            order = self._rank_ids(index, int(item["id"]))
            response: dict[str, Any] = {"order": order}
        elif kind == "numeric":
            response = {"value": 4}
        elif kind == "mc":
            response = {"choice": _MC[0]}
        else:
            response = {"choice": "yes"}
        rv = self.phones[index].post(
            "/api/student/live-prompt/response",
            json={"prompt_id": int(item["prompt_id"]), "response": response},
        )
        self._ok_status(rv, 200)

    def _individual(self, kind: str) -> None:
        """Publish outside the guard, then one solo answer."""
        row = self._add(kind, False)
        item = self._publish(row, False)
        self._guard_self(6, lambda: self._answer(6, item, kind), kind)
        self._close(item)

    def test_whiteboard_clear_and_show_do_not_reload_the_class(self) -> None:
        """Clear and Show update the board channel, not every phone's /state.

        Clear taps only readers of that shared board. Show taps joined
        students with the board key and does not bump ``state_seq``.
        Group pick and rank placement stay on their own checks.
        """
        import json

        from live_news_wire import NewsAudience, event_visible, log_for, reset_ink_taps

        self._close_open()
        whiteboard = next(
            (
                row
                for row in self.school.list_live_session_items(self.session_id)
                if str(row.get("kind") or "") == "whiteboard"
            ),
            None,
        )
        self.assertIsNotNone(whiteboard)
        assert whiteboard is not None
        published = self.staff.post(
            f"/api/live-sessions/{self.session_id}/items/{int(whiteboard['id'])}/publish",
            json={"publish_mode": "group_shared"},
        )
        self._ok_status(published, 200)
        key = str(self.school.student_live_board_key(self.session_id, self.ids[0]))
        self.school.append_live_board_ops(
            self.session_id,
            key,
            owner=str(self.ids[0]),
            ops=[{"type": "stroke_add", "id": "fan-pen", "points": [[0.2, 0.3], [0.4, 0.5]], "color": "#2563eb"}],
        )
        reset_ink_taps()
        tape = log_for(self.app.config["SCHOOL_DB"].data_dir)

        def clear() -> None:
            """Clear the shared board for the first team."""
            rv = self.staff.post(
                f"/api/live-sessions/{self.session_id}/whiteboard-gallery/{key}/clear"
            )
            self._ok_status(rv, 200)

        before = {i: self._snap(i) for i in sorted(self.present)}
        cursor = max((int(row.get("id") or 0) for row in tape.since(self.session_id, 0)), default=0)
        time.sleep(GAP_S)
        clear()
        for index in sorted(self.present):
            self._assert_same(index, before[index], f"clear seat {index}")
        ink = [
            row
            for row in tape.since(self.session_id, cursor)
            if row.get("type") == "board_ink" and row.get("board_key") == key
        ]
        self.assertTrue(ink, "clear did not tap the shared board")
        actor = NewsAudience(
            "student",
            student_id=self.ids[0],
            read_keys=list(self.school.student_board_access(self.session_id, self.ids[0])["read"]),
        )
        watcher = NewsAudience(
            "student",
            student_id=self.ids[3],
            read_keys=list(self.school.student_board_access(self.session_id, self.ids[3])["read"]),
        )
        self.assertTrue(event_visible(ink[-1], actor))
        self.assertFalse(event_visible(ink[-1], watcher))
        self.assertFalse(
            any(row.get("type") == "state_seq" for row in tape.since(self.session_id, cursor))
        )
        seen = self.phones[0].get(f"/api/student/board/{key}?since=0")
        self._ok_status(seen, 200)
        self.assertNotIn("fan-pen", str(seen.get_json()))

        def show() -> None:
            """Show that board without a class-wide state bump."""
            rv = self.staff.post(
                f"/api/live-sessions/{self.session_id}/whiteboard-gallery/{key}/show"
            )
            self._ok_status(rv, 200)
            self._shown = rv.get_json() or {}

        before = {i: self._snap(i) for i in sorted(self.present)}
        cursor = max((int(row.get("id") or 0) for row in tape.since(self.session_id, 0)), default=0)
        time.sleep(GAP_S)
        show()
        for index in sorted(self.present):
            self._assert_same(index, before[index], f"show seat {index}")
        cards = [
            row
            for row in tape.since(self.session_id, cursor)
            if row.get("type") == "board_show"
        ]
        self.assertTrue(cards, "show did not tap students")
        self.assertFalse(
            any(row.get("type") == "state_seq" for row in tape.since(self.session_id, cursor))
        )
        self.assertTrue(event_visible(cards[-1], actor))
        self.assertTrue(event_visible(cards[-1], watcher))
        self.assertFalse(event_visible(cards[-1], NewsAudience("staff")))
        teacher_names = [
            str(row.get("name") or "").strip()
            for row in (self._shown.get("legend") or [])
            if str(row.get("name") or "").strip()
        ]
        self.assertTrue(teacher_names)
        student = self.phones[3].get("/api/student/board/mine?since=0&teacher_since=0")
        self._ok_status(student, 200)
        board = (student.get_json() or {}).get("class_board") or {}
        dumped = json.dumps(board)
        self.assertNotIn('"name"', dumped)
        for label in teacher_names:
            self.assertNotIn(label, dumped)
        portal = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        wire = portal.split('event.type === "board_ink"')[1].split("scheduleNewsState")[0]
        self.assertIn("board_show", wire)
        self.assertIn("return;", wire)
        self.assertNotIn("void tick()", wire)

    def test_student_actions_do_not_fan_out(self) -> None:
        """Each student action, then one answer of every live question type."""
        self.assertEqual(set(INDIVIDUAL_ROWS), set(LIVE_QUESTION_TYPES))
        self.assertEqual(len(INDIVIDUAL_ROWS), len(LIVE_QUESTION_TYPES))
        for name in TEAM_ACTIONS:
            getattr(self, name)()
        self._close_open()
        for kind in INDIVIDUAL_ROWS:
            self._individual(kind)

    def _news_tip(self) -> int:
        """Highest postcard id stored for this session, or 0."""
        from live_news_wire import log_for

        rows = log_for(self.school.data_dir).since(self.session_id, 0)
        if not rows:
            return 0
        return int(rows[-1]["id"])

    def _slide_updates(self, after: int) -> list[dict[str, Any]]:
        """Slide postcards stored after ``after``.

        Args:
            after: Last postcard id already seen.
        """
        from live_news_wire import log_for

        return [
            row
            for row in log_for(self.school.data_dir).since(self.session_id, after)
            if row.get("type") == "google_slide"
        ]

    def test_slide_publish_and_page_change_do_not_fan_out(self) -> None:
        """Publishing or moving a slide leaves every phone stamp unchanged.

        Each move stores exactly one slide postcard. Group pick and rank
        place stay on their own guards.
        """
        import fitz

        pngs: list[bytes] = []
        document = fitz.open()
        try:
            for label in ("A", "B"):
                page = document.new_page(width=64, height=36)
                page.insert_text((8, 24), label)
                pngs.append(page.get_pixmap(alpha=False).tobytes("png"))
        finally:
            document.close()
        self.school.import_google_slides_pages(
            self.session_id,
            presentation_id="DeckFanout01",
            page_pngs=pngs,
        )

        def _same(label: str) -> None:
            for index, snap in before.items():
                self._assert_same(index, snap, label)

        before = {index: self._snap(index) for index in sorted(self.present)}
        stamps = {
            index: self.school.live_student_poll_stamp(
                self.session_id, self.class_id, self.ids[index]
            )
            for index in sorted(self.present)
        }
        time.sleep(GAP_S)
        tip = self._news_tip()
        published = self.staff.post(
            f"/api/live-sessions/{self.session_id}/google-slides/pages/0/publish"
        )
        self._ok_status(published, 200)
        _same("slide publish")
        self.assertEqual(len(self._slide_updates(tip)), 1)
        for index, stamp in stamps.items():
            self.assertEqual(
                self.school.live_student_poll_stamp(
                    self.session_id, self.class_id, self.ids[index]
                ),
                stamp,
                "slide publish",
            )

        before = {index: self._snap(index) for index in sorted(self.present)}
        stamps = {
            index: self.school.live_student_poll_stamp(
                self.session_id, self.class_id, self.ids[index]
            )
            for index in sorted(self.present)
        }
        time.sleep(GAP_S)
        tip = self._news_tip()
        moved = self.staff.post(
            f"/api/live-sessions/{self.session_id}/google-slides/current",
            json={"index": 1},
        )
        self._ok_status(moved, 200)
        _same("slide page")
        self.assertEqual(len(self._slide_updates(tip)), 1)
        for index, stamp in stamps.items():
            self.assertEqual(
                self.school.live_student_poll_stamp(
                    self.session_id, self.class_id, self.ids[index]
                ),
                stamp,
                "slide page",
            )

        before = {index: self._snap(index) for index in sorted(self.present)}
        time.sleep(GAP_S)
        hidden = self.staff.post(
            f"/api/live-sessions/{self.session_id}/google-slides/pages/0/unpublish"
        )
        self._ok_status(hidden, 200)
        _same("slide unpublish")

    def test_unpublish_current_slide_does_not_fan_out(self) -> None:
        """Unpublish hides only the current page and does not reload every phone.

        The other page stays published. Students get one slide postcard
        with an empty image, which paints the waiting line and hides the
        iframe. Group pick and rank stay on their own guards.
        """
        import fitz

        pngs: list[bytes] = []
        document = fitz.open()
        try:
            for label in ("A", "B"):
                page = document.new_page(width=64, height=36)
                page.insert_text((8, 24), label)
                pngs.append(page.get_pixmap(alpha=False).tobytes("png"))
        finally:
            document.close()
        self.school.import_google_slides_pages(
            self.session_id,
            presentation_id="DeckFanoutUnpublish",
            page_pngs=pngs,
        )
        for index in (0, 1):
            published = self.staff.post(
                f"/api/live-sessions/{self.session_id}/google-slides/pages/{index}/publish"
            )
            self._ok_status(published, 200)
        shown = self._full(0).get("google_slide") or {}
        self.assertTrue(shown.get("image_url"))
        self.assertEqual(int(shown.get("index") or 0), 1)

        before = {index: self._snap(index) for index in sorted(self.present)}
        time.sleep(GAP_S)
        tip = self._news_tip()
        hidden = self.staff.post(
            f"/api/live-sessions/{self.session_id}/google-slides/pages/1/unpublish"
        )
        self._ok_status(hidden, 200)
        for index, snap in before.items():
            self._assert_same(index, snap, "current slide unpublish")
        from live_news_wire import log_for

        rows = list(log_for(self.school.data_dir).since(self.session_id, tip))
        self.assertEqual([row.get("type") for row in rows], ["google_slide"])
        self.assertLess(len(rows), len(self.present))
        deck = (hidden.get_json() or {}).get("google_slides") or {}
        pages = deck.get("pages") or []
        self.assertEqual(int(deck.get("current_index") or 0), 1)
        self.assertTrue(pages[0].get("published"))
        self.assertFalse(pages[1].get("published"))
        card = (hidden.get_json() or {}).get("google_slide") or {}
        self.assertEqual(card.get("image_url"), "")
        self.assertFalse(card.get("shared"))
        student = self._full(0).get("google_slide") or {}
        self.assertEqual(student.get("image_url"), "")
        self.assertFalse(student.get("shared"))

        page = self.staff.get(f"/staff/class/{self.class_id}?tab=live")
        self._ok_status(page, 200)
        html = page.get_data(as_text=True)
        self.assertEqual(html.count('id="live-slides-unpublish"'), 1)
        self.assertIn(">Unpublish<", html)
        self.assertNotIn("unpublish-all", html)
        self.assertNotIn("Unpublish deck", html)

        staff_js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        fn = staff_js.split("async function unpublishCurrentSlide")[1].split(
            "function slidesReadConnectUrl"
        )[0]
        self.assertIn('postGoogleSlideAction("unpublish", index)', fn)
        self.assertIn("current_index", fn)
        self.assertNotIn("unpublish-all", fn)
        self.assertNotIn("pages.forEach", fn)

        portal = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        placeholder = portal.split("function showUnsharedSlidePlaceholder")[1].split(
            "function applyStudentGoogleSlide"
        )[0]
        self.assertIn("slidesFrame.hidden = true", placeholder)
        self.assertIn('removeAttribute("src")', placeholder)
        self.assertIn("SLIDES_WAITING_COPY", placeholder)
        projection = portal.split("if (slidesPane)")[1].split("syncTeamDrawChrome")[0]
        self.assertIn("showUnsharedSlidePlaceholder()", projection)
        paint = portal.split("function paintStudentSlides")[1].split(
            "function paneIsBeingGrabbed"
        )[0]
        self.assertIn("if (!url)", paint)
        self.assertIn("showUnsharedSlidePlaceholder()", paint)

    def test_teacher_actions_update_everyone(self) -> None:
        """Publish, close, and end each rebuild every joined phone."""
        self._enter(2)
        row = self._add("mc", False)
        self._guard_all(lambda: self._publish(row, False), "publish")
        published = self.school.list_live_session_items(self.session_id)
        active = next(item for item in published if str(item.get("status") or "") == "active")
        self._guard_all(lambda: self._close(active), "close")
        self._guard_all(self._end, "end")

    def _end(self) -> None:
        """End the live class."""
        rv = self.staff.post(
            f"/staff/class/{self.class_id}/end-live",
            data={"end_options": "1"},
            follow_redirects=False,
        )
        self._ok_status(rv, 302)

    def _news_rows(self, after: int) -> list[dict[str, Any]]:
        """Postcards stored for this session after ``after``.

        Args:
            after: Last postcard id already seen.
        """
        from live_news_wire import log_for

        return list(log_for(self.school.data_dir).since(self.session_id, after))

    def test_quit_join_note_does_not_fan_out(self) -> None:
        """Quit reuses one end signal. Phones are not each told to reload.

        The join note is the ``ended=1`` flag on the redirect the student
        poll already returns. Quit stores one postcard for the session,
        not one per phone, and the phones' follow-up polls add none.
        """
        from urllib.parse import parse_qs, urlsplit

        from auth import CLASS_ENDED_JOIN_NOTE

        self.assertEqual(
            CLASS_ENDED_JOIN_NOTE,
            "This class has ended. Your teacher will share a new code for the next class.",
        )
        landing = (LMS_DIR / "templates" / "landing.html").read_text(encoding="utf-8")
        portal = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertNotIn(CLASS_ENDED_JOIN_NOTE, landing)
        self.assertNotIn(CLASS_ENDED_JOIN_NOTE, portal)
        self.assertIn("{{ class_ended_note }}", landing)
        self.assertNotIn("setInterval", landing)

        tip = self._news_tip()
        quit_rv = self.staff.post(
            f"/staff/class/{self.class_id}/quit-live",
            follow_redirects=False,
        )
        self._ok_status(quit_rv, 302)
        signaled = self._news_rows(tip)
        self.assertEqual(len(signaled), 1)
        self.assertLess(len(signaled), len(self.present))
        self.assertEqual(signaled[0].get("type"), "state_seq")

        redirects: list[str] = []
        for index in sorted(self.present):
            rv = self.phones[index].get("/api/student/state")
            self._ok_status(rv, 200)
            body = rv.get_json() or {}
            self.assertEqual(body.get("status"), "ended")
            self.assertFalse(body.get("celebrate"))
            loc = str(body.get("redirect") or "")
            redirects.append(loc)
            parts = urlsplit(loc)
            self.assertEqual(parts.path, "/")
            self.assertEqual(parse_qs(parts.query).get("ended"), ["1"])
            self.assertNotIn(self.session_code, loc)
            for seat in self.seats:
                self.assertNotIn(seat, loc)
        self.assertEqual(len(set(redirects)), 1)
        self.assertEqual(len(self._news_rows(tip)), 1)

        noted = self.phones[0].get(redirects[0])
        self._ok_status(noted, 200)
        html = noted.get_data(as_text=True)
        self.assertEqual(html.count(CLASS_ENDED_JOIN_NOTE), 1)
        self.assertIn('id="class-ended-note"', html)
        plain = self.phones[0].get("/")
        self._ok_status(plain, 200)
        self.assertNotIn(CLASS_ENDED_JOIN_NOTE, plain.get_data(as_text=True))
        self.assertNotIn('id="class-ended-note"', plain.get_data(as_text=True))


    def test_dead_bank_image_student_refresh_fetches_once(self) -> None:
        """One refused bank image is fetched on the first student view only."""
        from unittest.mock import patch

        from bank_image_mirror import clear_bank_image_refusals
        from test_module_bank_import import (
            _LOGIN_HTML,
            _UrlOpenBody,
            _insert_bank,
            _insert_mc_question,
        )

        offering = self.school.conn.execute(
            "SELECT offering_id FROM classes WHERE id = ?",
            (self.class_id,),
        ).fetchone()
        course = self.school.conn.execute(
            "SELECT code FROM ontario_courses ORDER BY code LIMIT 1"
        ).fetchone()
        library = self.school.create_library(str(course["code"]), origin="upload")
        library_id = int(library["id"])
        self.school.attach_library(int(offering["offering_id"]), library_id)
        remote = (
            "https://virtuallearning.instructure.com/assessment_questions/"
            "1/files/9/download?verifier=fanout"
        )
        bank_id = _insert_bank(
            self.school,
            library_id,
            title="Live bank",
            import_key="bank:fanout",
        )
        question_id = _insert_mc_question(
            self.school,
            bank_id,
            import_key="q-fanout",
            title="Graph",
            stem=f'<p>Graph</p><img src="{remote}">',
        )
        self.school.confirm_module_bank_links(library_id, 1, [bank_id])
        calls = {"n": 0}

        def opener(*_args: object, **_kwargs: object) -> _UrlOpenBody:
            """Count outbound image downloads and return a login page."""
            calls["n"] += 1
            return _UrlOpenBody(_LOGIN_HTML, "text/html")

        with patch("bank_image_mirror._open_remote", side_effect=opener):
            page_number = int(
                self.school._current_student_page_number(self.session_id) or 1
            )
            self.school.import_mc_to_class_playlist(
                self.class_id,
                "M1",
                "C2",
                question_id,
                library_id=library_id,
                page_number=page_number,
                stage="round",
            )
            items = self.school.ensure_live_session_items(self.session_id)
            imported = next(
                row
                for row in items
                if str(row.get("item_id") or "") == f"bank-import-{question_id}"
            )
            self.school.publish_live_session_item(
                self.session_id, int(imported["id"])
            )
            clear_bank_image_refusals()
            calls["n"] = 0
            self.school.student_live_items_payload(self.session_id, self.ids[0])
            first = calls["n"]
            calls["n"] = 0
            for student_id in self.ids:
                self.school.student_live_items_payload(self.session_id, student_id)
            self.assertGreaterEqual(first, 1)
            self.assertEqual(calls["n"], 0)

    def test_twenty_two_page_deck_and_one_group_tap_do_not_fan_out(self) -> None:
        """A 22-page deck plus one group pick rebuilds only that team.

        Each phone is polled once after the tap. The slides client does
        not reload the page from a slide postcard or a ``/state`` tick.
        """
        import fitz

        document = fitz.open()
        try:
            document.new_page(width=32, height=18)
            png = document[0].get_pixmap(alpha=False).tobytes("png")
        finally:
            document.close()
        self.school.import_google_slides_pages(
            self.session_id,
            presentation_id="DeckFanout22",
            page_pngs=[png] * 22,
        )
        deck = self.school.google_slides_deck(self.session_id)
        assert deck is not None
        self.assertEqual(len(deck["pages"]), 22)
        self._close_open()
        row = self._add("mc", True)
        item = self._publish(row, True)

        def pick() -> None:
            """One teammate's pick. It must not fetch ``/state`` itself."""
            rv = self._post_item(
                0, int(item["id"]), "group-pick", {"choice": _MC[0]}
            )
            self._ok_status(rv, 200)

        self._guard_team(0, pick, "22-page deck group pick")
        js = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        slides = js.split("function applyStudentGoogleSlide")[1].split(
            "function paintStudentSlides"
        )[0]
        wire = js.split('event.type === "google_slide"')[1].split(
            'event.type === "board_ink"'
        )[0]
        self.assertIn("showUnsharedSlidePlaceholder()", slides)
        self.assertIn("Teacher hasn't shared this slide yet", js)
        self.assertNotIn("location.reload", js)
        self.assertNotIn("location.reload", wire)
        self.assertIn("return;", wire)

    def test_sentry_group_pick_adds_no_per_tap_load(self) -> None:
        """MCK-135: with Sentry on, one team pick still rebuilds only that team.

        The pick and the polls after it emit no error event and no
        WARNING-or-above log. Traces are forced off so a 5% sample cannot
        fail this guard. Production still samples non-poll routes at 5%
        and live polls at 0.
        """
        import sentry_sdk
        from sentry_sdk.transport import Transport

        from sentry_wire import sentry_init_options

        class CountingTransport(Transport):
            """Count error events and logs. Do not send them."""

            def __init__(self, options: Any = None) -> None:
                """Start the counters at zero."""
                super().__init__(options)
                self.events = 0
                self.logs = 0

            def capture_envelope(self, envelope: Any) -> None:
                """Count error events and log batches."""
                for item in envelope.items:
                    kind = item.headers.get("type")
                    if kind == "event":
                        self.events += 1
                    elif kind == "log":
                        self.logs += 1

        logging.disable(logging.NOTSET)
        transport = CountingTransport()
        options = sentry_init_options("https://public@o0.ingest.sentry.io/1")
        options["traces_sampler"] = lambda _sampling_context: 0.0
        sentry_sdk.init(transport=transport, **options)
        try:
            row = self._add("mc", True)
            item = self._publish(row, True)
            sentry_sdk.flush(timeout=2)
            before = (transport.events, transport.logs)

            def pick() -> None:
                """One teammate's pick."""
                rv = self._post_item(
                    0, int(item["id"]), "group-pick", {"choice": _MC[0]}
                )
                self._ok_status(rv, 200)

            self._guard_team(0, pick, "sentry group pick")
            sentry_sdk.flush(timeout=2)
            self.assertEqual(transport.events, before[0], "sentry error on a tap")
            self.assertEqual(transport.logs, before[1], "sentry log on a tap")
        finally:
            logging.disable(logging.WARNING)
            sentry_sdk.get_client().close()
            sentry_sdk.init()


class FanoutGuardTableTests(unittest.TestCase):
    """The action table and the required CI job names."""

    def test_table_lists_every_live_question_type(self) -> None:
        """A new staff question type has to be added to the individual rows."""
        self.assertEqual(set(INDIVIDUAL_ROWS), set(LIVE_QUESTION_TYPES))
        self.assertEqual(len(set(INDIVIDUAL_ROWS)), len(INDIVIDUAL_ROWS))
        for name in TEAM_ACTIONS:
            self.assertTrue(callable(getattr(FanoutGuardTests, name)), name)

    def test_ci_job_names(self) -> None:
        """The workflow names are the ones branch protection can require."""
        text = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("name: fan-out guard", text)
        self.assertIn("name: 30-seat load budget", text)
        self.assertIn("test_fanout_guard.py", text)
        self.assertIn("--sqlite-budget", text)


if __name__ == "__main__":
    unittest.main()

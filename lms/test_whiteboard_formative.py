#!/usr/bin/env python3
"""MCK-218 formative whiteboard helpers and board-read presence guard."""

from __future__ import annotations

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

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from live_presence import LivePresenceUnavailable  # noqa: E402
from whiteboard_formative import (  # noqa: E402
    FORMATIVE_MEMBER_PALETTE,
    formative_board_members,
    formative_session_slot_map,
    formative_slot_map_from_rows,
    formative_whiteboard_enabled,
    first_name_only,
    member_color,
    assign_board_colors,
    drawing_owner_ids,
    roster_board_bucket,
    student_ink_color,
)


class FormativeHelperTests(unittest.TestCase):
    """Palette and feature flag."""

    def test_member_palette_matches_spec(self) -> None:
        """Six slot colours from the MCK-218 spec."""
        self.assertEqual(FORMATIVE_MEMBER_PALETTE[0].lower(), "#2dd4bf")
        self.assertEqual(len(FORMATIVE_MEMBER_PALETTE), 6)

    def test_seventh_member_reuses_palette(self) -> None:
        """Slot six wraps with the same hex as slot zero."""
        self.assertEqual(member_color(6), member_color(0))

    def test_first_name_only(self) -> None:
        """Chips use the first token only."""
        self.assertEqual(first_name_only("Noah Maple"), "Noah")

    def test_sort_order_zero_is_one_colour_sequence(self) -> None:
        """Sort order 0 is a real board, even when team ids differ."""
        rows = [
            {"student_id": 1, "team_sort_order": 0, "team_id": 10},
            {"student_id": 2, "team_sort_order": 0, "team_id": 16},
            {"student_id": 3, "team_sort_order": 1, "team_id": 22},
        ]
        self.assertEqual(roster_board_bucket(rows[0]), 0)
        self.assertEqual(roster_board_bucket({"team_id": 0}), 0)
        slots = formative_slot_map_from_rows(rows)
        self.assertEqual(slots[1], 0)
        self.assertEqual(slots[2], 1)
        self.assertEqual(slots[3], 0)

    def test_board_colours_bump_past_a_hash_clash(self) -> None:
        """A hash clash takes the next free colour. The first id stays put."""
        from live_canvas import CURSOR_COLORS, cursor_color_for

        first = 1
        target = cursor_color_for(first)
        second = None
        for candidate in range(2, 20000):
            if cursor_color_for(candidate) == target:
                second = candidate
                break
        self.assertIsNotNone(second)
        assert second is not None
        assigned = assign_board_colors([first, second])
        self.assertEqual(assigned[str(first)], target)
        self.assertNotEqual(assigned[str(second)], target)
        self.assertEqual(len(set(assigned.values())), 2)
        self.assertIn(assigned[str(second)], CURSOR_COLORS)
        again = assign_board_colors([first, second, second + 1])
        self.assertEqual(again[str(first)], assigned[str(first)])
        self.assertEqual(again[str(second)], assigned[str(second)])
        owners = drawing_owner_ids(
            [
                {"owner": "teacher", "op": "{\"type\":\"text_upsert\",\"text\":\"note\"}"},
                {"owner": str(second), "op": {"type": "stroke_add", "id": "s"}},
                {"owner": str(first), "op": "{\"type\":\"text_upsert\",\"text\":\"\"}"},
                {"owner": "9", "op": "{\"type\":\"text_upsert\",\"text\":\"kept\"}"},
            ]
        )
        self.assertEqual(owners, {str(second), "9"})

    def test_collab_js_ignores_projector_names_and_darkens_cursor_chips(self) -> None:
        """Collab name chips ignore a saved projector flag; cursor text is dark."""
        script = r"""
import { boardNameChipsEnabled, classBoardPaintMode, collabForcesNames, cursorChipInkStyle } from './lms/static/whiteboard_formative.js';
if (collabForcesNames(false, true)) process.exit(1);
if (!collabForcesNames(true, true)) process.exit(2);
if (boardNameChipsEnabled()) process.exit(7);
if (classBoardPaintMode('team:0', 'team:0') !== 'show-own') process.exit(8);
if (classBoardPaintMode('team:1', 'team:0') !== 'show-class') process.exit(9);
if (classBoardPaintMode('', 'team:0') !== 'show-class') process.exit(10);
const style = cursorChipInkStyle('#2dd4bf');
if (!style.includes('color:#0f172a')) process.exit(3);
if (!style.includes('background:#fff')) process.exit(4);
if (style.includes('color:#2dd4bf')) process.exit(5);
if (!style.includes('box-shadow:0 0 0 1px #0f172a')) process.exit(6);
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_flag_defaults_on(self) -> None:
        """Formative UI is on unless LLOVES_FORMATIVE_WHITEBOARD=0."""
        prior = os.environ.pop("LLOVES_FORMATIVE_WHITEBOARD", None)
        try:
            self.assertTrue(formative_whiteboard_enabled())
            os.environ["LLOVES_FORMATIVE_WHITEBOARD"] = "0"
            self.assertFalse(formative_whiteboard_enabled())
        finally:
            if prior is None:
                os.environ.pop("LLOVES_FORMATIVE_WHITEBOARD", None)
            else:
                os.environ["LLOVES_FORMATIVE_WHITEBOARD"] = prior


class FormativeIntegrationTests(unittest.TestCase):
    """Teacher toggles, student gate, slots, and own-only erase."""

    def setUp(self) -> None:
        """One group whiteboard live session with Maple and Cedar on one team."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
            live_database_url="",
        )
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.staff = self.app.test_client()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        self.staff.get("/auth/google?portal=staff")
        self.staff.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.staff.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )
        created = self.staff.post(
            "/api/staff/classes",
            json={
                "offering_id": int(offering["id"]),
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple", "Cedar", "Pine"],
            },
        )
        self.class_id = int(created.get_json()["class"]["id"])
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])
        maple = self.school.game.find_student_by_codename(self.class_id, "Maple")
        cedar = self.school.game.find_student_by_codename(self.class_id, "Cedar")
        pine = self.school.game.find_student_by_codename(self.class_id, "Pine")
        assert maple is not None and cedar is not None and pine is not None
        self.maple_id = int(maple["id"])
        self.cedar_id = int(cedar["id"])
        self.pine_id = int(pine["id"])
        self.school.game.begin_game(self.class_id)
        for row in (maple, cedar, pine):
            self.school.join_live_class_session(
                self.session_id, int(row["id"]), codename=str(row["codename"])
            )
        self.school.setup_live_session_groups(
            self.session_id,
            n_teams=2,
            mode="manual",
            present_ids=[self.maple_id, self.cedar_id, self.pine_id],
            assignments=[
                {"student_id": self.maple_id, "team_index": 0},
                {"student_id": self.cedar_id, "team_index": 0},
                {"student_id": self.pine_id, "team_index": 1},
            ],
        )
        self.school.ensure_live_session_items(self.session_id)
        wb = next(
            row
            for row in self.school.list_live_session_items(self.session_id)
            if str(row.get("kind") or "") == "whiteboard"
        )
        self.school.publish_live_session_item(
            self.session_id, int(wb["id"]), publish_mode="group_shared"
        )
        self.school.set_live_session_teacher_state(
            self.session_id,
            canvas_align="team",
            run_as_group=True,
            groups_configured=True,
        )
        self.team_id = self.school.student_team_id_for_class(
            self.class_id, self.maple_id
        )
        assert self.team_id is not None
        self.team_sort_order = self.school._team_sort_order_for_team_id(
            self.class_id, int(self.team_id)
        )
        assert self.team_sort_order is not None
        self.student = self.app.test_client()
        row = self.school.conn.execute(
            """
            SELECT visit_token FROM live_session_attendees
            WHERE live_session_id = ? AND student_id = ?
            """,
            (self.session_id, self.maple_id),
        ).fetchone()
        self.visit_token = str(row["visit_token"] if row else "")

    def tearDown(self) -> None:
        """Close sqlite and remove the temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def test_teacher_name_toggles_are_ignored(self) -> None:
        """The removed name checkboxes are not stored on teacher state."""
        rv = self.staff.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={
                "whiteboard_always_show_names": True,
                "whiteboard_projector_names": True,
            },
        )
        body = rv.get_json()
        self.assertEqual(rv.status_code, 200, body)
        state = body.get("teacher_state") or {}
        self.assertNotIn("whiteboard_always_show_names", state)
        self.assertNotIn("whiteboard_projector_names", state)

    def test_student_cannot_post_teacher_state_toggles(self) -> None:
        """Students cannot patch teacher-only whiteboard state."""
        rv = self.student.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"eyes_up": True},
        )
        self.assertIn(rv.status_code, (302, 401, 403))

    def test_member_slot_stable_when_teammate_leaves(self) -> None:
        """Departed students keep their slot index for remaining chip colours."""
        before = formative_board_members(self.school, self.session_id, self.team_sort_order)
        self.assertEqual(len(before), 2)
        maple_color = next(m["color"] for m in before if m["id"] == str(self.maple_id))
        self.school.conn.execute(
            """
            UPDATE live_session_attendees
            SET left_at = '2026-10-15T12:00:00'
            WHERE live_session_id = ? AND student_id = ?
            """,
            (self.session_id, self.cedar_id),
        )
        self.school.conn.commit()
        after = formative_board_members(self.school, self.session_id, self.team_sort_order)
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0]["id"], str(self.maple_id))
        self.assertEqual(after[0]["color"], maple_color)
        self.assertEqual(after[0]["slot"], 0)

    def test_member_slot_stable_when_middle_joiner_leaves(self) -> None:
        """Middle join order keeps later slot colours when they leave."""
        game_id = int(self.school.game.game_state(self.class_id)["game"]["id"])
        self.school.conn.execute(
            """
            UPDATE game_memberships
            SET team_id = ?
            WHERE game_id = ? AND student_id = ?
            """,
            (self.team_id, game_id, self.pine_id),
        )
        self.school.conn.execute(
            """
            UPDATE live_board_assignments
            SET team_sort_order = ?, board_key = ?
            WHERE class_id = ? AND student_id = ?
            """,
            (
                int(self.team_sort_order),
                f"team:{int(self.team_sort_order)}",
                self.class_id,
                self.pine_id,
            ),
        )
        self.school.conn.commit()
        self.school.game._team_index_cache.pop(int(self.class_id), None)
        before = formative_board_members(self.school, self.session_id, self.team_sort_order)
        self.assertEqual(len(before), 3)
        pine_color = next(m["color"] for m in before if m["id"] == str(self.pine_id))
        pine_slot = next(m["slot"] for m in before if m["id"] == str(self.pine_id))
        self.assertEqual(pine_slot, 2)
        self.school.conn.execute(
            """
            UPDATE live_session_attendees
            SET left_at = '2026-10-15T12:05:00'
            WHERE live_session_id = ? AND student_id = ?
            """,
            (self.session_id, self.cedar_id),
        )
        self.school.conn.commit()
        after = formative_board_members(self.school, self.session_id, self.team_sort_order)
        self.assertEqual(len(after), 2)
        pine_after = next(m for m in after if m["id"] == str(self.pine_id))
        self.assertEqual(pine_after["slot"], 2)
        self.assertEqual(pine_after["color"], pine_color)

    def test_formative_meta_on_incremental_board_poll(self) -> None:
        """Board polls attach formative meta even when no snapshot is sent."""
        access = self.school.student_board_access(self.session_id, self.maple_id)
        board_key = str(access["collab_key"])
        first = self.school.read_board(
            self.session_id,
            board_key,
            0,
            student_id=self.maple_id,
            teacher_since=0,
        )
        self.assertIn("formative", first)
        members = (first.get("formative") or {}).get("members") or []
        self.assertGreaterEqual(len(members), 2)
        seq = int(first.get("board_seq") or 0)
        inc = self.school.read_board(
            self.session_id,
            board_key,
            seq,
            student_id=self.maple_id,
            teacher_since=int(first.get("teacher_board_seq") or 0),
        )
        self.assertFalse(inc.get("snapshot"))
        self.assertIn("formative", inc)
        inc_ids = {str(m.get("id")) for m in (inc["formative"].get("members") or [])}
        self.assertIn(str(self.maple_id), inc_ids)
        self.assertIn(str(self.cedar_id), inc_ids)
        self.school.mark_live_session_attendee_left(
            self.session_id, student_id=self.cedar_id
        )
        inc2 = self.school.read_board(
            self.session_id,
            board_key,
            seq,
            student_id=self.maple_id,
            teacher_since=int(first.get("teacher_board_seq") or 0),
        )
        left_ids = {str(m.get("id")) for m in (inc2["formative"].get("members") or [])}
        self.assertNotIn(str(self.cedar_id), left_ids)

    def test_student_board_visit_gate_unavailable_is_503(self) -> None:
        """Board reads return 503 when the visit-token presence gate is down."""

        def boom(*_args: Any, **_kwargs: Any) -> None:
            raise LivePresenceUnavailable("live presence store unavailable")

        self.school.resolve_student_visit_token = boom  # type: ignore[method-assign]
        rv = self.student.get(
            "/api/student/board/mine?since=0&teacher_since=0",
            headers={"X-Student-Visit-Token": self.visit_token},
        )
        self.assertEqual(rv.status_code, 503)
        self.assertEqual(rv.headers.get("Retry-After"), "2")
        self.assertTrue((rv.get_json() or {}).get("retry"))



    def test_formative_colour_matches_session_slot_on_each_team(self) -> None:
        """Legend colour follows the student id, and the slot stays put."""
        slots = formative_session_slot_map(self.school, self.session_id)
        pine_slot = slots[self.pine_id]
        team0 = formative_board_members(
            self.school, self.session_id, self.team_sort_order
        )
        pine_team_id = self.school.student_team_id_for_class(self.class_id, self.pine_id)
        assert pine_team_id is not None
        pine_order = self.school._team_sort_order_for_team_id(
            self.class_id, int(pine_team_id)
        )
        self.assertIsNotNone(pine_order)
        team1_members = formative_board_members(self.school, self.session_id, pine_order)
        all_members = formative_board_members(self.school, self.session_id, None)
        self.assertTrue(team0)
        pine_team1 = next(m for m in team1_members if m["id"] == str(self.pine_id))
        pine_all = next(m for m in all_members if m["id"] == str(self.pine_id))
        self.assertEqual(pine_team1["slot"], pine_slot)
        team_rows = self.school.list_live_session_team_members(self.session_id, pine_order)
        all_rows = self.school.list_live_session_team_members(self.session_id, None)
        team_colors = assign_board_colors(
            [int(row["student_id"]) for row in team_rows if row.get("student_id") not in (None, "")]
        )
        all_colors = assign_board_colors(
            [int(row["student_id"]) for row in all_rows if row.get("student_id") not in (None, "")]
        )
        self.assertEqual(pine_team1["color"], team_colors[str(self.pine_id)])
        self.assertEqual(pine_all["color"], all_colors[str(self.pine_id)])
        self.assertNotEqual(pine_team1["color"], "")

    def test_teacher_board_formative_drops_departed_student(self) -> None:
        """Teacher board polls refresh legend membership when someone leaves."""
        before = self.school.read_board(
            self.session_id,
            "teacher",
            0,
            as_teacher=True,
        )
        self.assertIn("formative", before)
        ids_before = {m["id"] for m in (before["formative"].get("members") or [])}
        self.assertIn(str(self.cedar_id), ids_before)
        self.school.mark_live_session_attendee_left(
            self.session_id, student_id=self.cedar_id
        )
        after = self.school.read_board(
            self.session_id,
            "teacher",
            int(before.get("board_seq") or 0),
            as_teacher=True,
        )
        ids_after = {m["id"] for m in (after["formative"].get("members") or [])}
        self.assertNotIn(str(self.cedar_id), ids_after)

    def test_mid_board_joiner_can_poll_team_board(self) -> None:
        """A student who joins after ink exists can read the board with a visit token."""
        self.school.conn.execute(
            """
            DELETE FROM live_session_attendees
            WHERE live_session_id = ? AND student_id = ?
            """,
            (self.session_id, self.pine_id),
        )
        self.school.conn.commit()
        self.school.apply_live_canvas_presence(
            self.session_id,
            owner=str(self.maple_id),
            name="Maple",
            team_id=self.team_id,
            x=0.2,
            y=0.2,
            stroke_id="mid-join-line",
            point=[0.2, 0.2],
            as_teacher=False,
        )
        joined = self.school.join_live_class_session(
            self.session_id, self.pine_id, codename="Pine"
        )
        token = str((joined.get("attendee") or {}).get("visit_token") or "")
        self.assertTrue(token)
        late = self.app.test_client()
        rv = late.get(
            "/api/student/board/mine?since=0&teacher_since=0",
            headers={"X-Student-Visit-Token": token},
        )
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True)[:400])
        body = rv.get_json() or {}
        self.assertIn("formative", body)
        self.assertIn(str(self.pine_id), {m["id"] for m in body["formative"].get("members") or []})

    def test_formative_erase_rejects_other_students_stroke(self) -> None:
        """stroke_remove is refused when the stroke belongs to a teammate."""
        owner = str(self.cedar_id)
        self.school.apply_live_canvas_presence(
            self.session_id,
            owner=owner,
            name="Cedar",
            team_id=self.team_id,
            x=0.4,
            y=0.4,
            stroke_id="cedar-line",
            point=[0.4, 0.4],
            as_teacher=False,
        )
        rv = self.student.post(
            "/api/student/board/mine/ops",
            json={
                "run_key": self.school.live_board_run_key(self.session_id),
                "ops": [{"type": "stroke_remove", "id": "cedar-line"}],
            },
            headers={"X-Student-Visit-Token": self.visit_token},
        )
        self.assertEqual(rv.status_code, 403, rv.get_data(as_text=True)[:300])

    def _enroll(self, codename: str, team_id: int) -> int:
        """Add one roster student onto a team and join the live session.

        Args:
            codename: Unique class codename.
            team_id: ``game_teams.id`` for the board they draw on.
        """
        sid = self.school.game._insert_student_row(
            self.class_id,
            canvas_id=f"codename:{codename}",
            last_display="",
            first_name=codename,
            codename=codename,
        )
        game_id = int(self.school.game.game_state(self.class_id)["game"]["id"])
        self.school.conn.execute(
            """
            INSERT INTO game_memberships (game_id, team_id, student_id)
            VALUES (?, ?, ?)
            """,
            (game_id, int(team_id), int(sid)),
        )
        self.school.conn.commit()
        self.school.game._team_index_cache.pop(int(self.class_id), None)
        self.school.join_live_class_session(
            self.session_id, int(sid), codename=codename
        )
        return int(sid)

    def test_team_buckets_keep_distinct_ink_with_leaver_and_late_joiner(self) -> None:
        """Slots stay put when session join order is six apart.

        Seven or more students sit on two teams. A leaver keeps their
        slot, and a late joiner takes the lowest index nobody on that
        board, present or departed, has used. Ink colour is unique on that board. A late joiner bumps
        to a free colour and does not shift anyone already there.
        """
        team_b = self.school.student_team_id_for_class(self.class_id, self.pine_id)
        self.assertIsNotNone(team_b)
        assert team_b is not None
        plan = ["B", "B", "B", "A", "A", "A", "B", "A", "B", "A"]
        enrolled: list[int] = []
        for index, side in enumerate(plan):
            team = self.team_id if side == "A" else int(team_b)
            enrolled.append(self._enroll(f"N{index}{side}", team))
        self.school.game._team_index_cache.pop(int(self.class_id), None)
        everyone = self.school.list_live_session_team_members(self.session_id, None)
        maple = next(row for row in everyone if int(row["student_id"]) == self.maple_id)
        self.assertEqual(maple.get("team_sort_order"), 0)
        order = [int(row["student_id"]) for row in everyone]
        session_slot = {sid: index for index, sid in enumerate(order)}
        team_a_ids = [
            int(row["student_id"])
            for row in everyone
            if roster_board_bucket(row) == 0
        ]
        self.assertGreaterEqual(len(team_a_ids), 7)
        collided = False
        for left in team_a_ids:
            for right in team_a_ids:
                if left >= right:
                    continue
                a = session_slot[left]
                b = session_slot[right]
                if a >= 6 and b >= 6 and a % 6 == b % 6:
                    collided = True
        self.assertTrue(collided)

        order_b = self.school._team_sort_order_for_team_id(self.class_id, int(team_b))
        self.assertIsNotNone(order_b)
        team_a = formative_board_members(
            self.school, self.session_id, self.team_sort_order
        )
        team_b_members = formative_board_members(self.school, self.session_id, order_b)
        legend = formative_board_members(self.school, self.session_id, None)
        slots = formative_session_slot_map(self.school, self.session_id)
        self.assertGreaterEqual(len(team_a), 7)
        by_id = {str(m["id"]): m for m in legend}
        a_colors = assign_board_colors(
            [
                int(row["student_id"])
                for row in self.school.list_live_session_team_members(
                    self.session_id, self.team_sort_order
                )
                if row.get("student_id") not in (None, "")
            ]
        )
        b_colors = assign_board_colors(
            [
                int(row["student_id"])
                for row in self.school.list_live_session_team_members(self.session_id, order_b)
                if row.get("student_id") not in (None, "")
            ]
        )
        legend_colors = assign_board_colors(
            [int(row["student_id"]) for row in everyone if row.get("student_id") not in (None, "")]
        )
        board_colors = {}
        for member in team_a:
            board_colors[member["id"]] = a_colors[member["id"]]
        for member in team_b_members:
            board_colors[member["id"]] = b_colors[member["id"]]
        for member in team_a + team_b_members:
            other = by_id[str(member["id"])]
            self.assertEqual(other["dashed"], member["dashed"])
            self.assertEqual(other["slot"], member["slot"])
            self.assertEqual(member["slot"], slots[int(member["id"])])
            self.assertEqual(member["color"], board_colors[member["id"]])
            self.assertEqual(other["color"], legend_colors[member["id"]])
        self.assertEqual(len({member["color"] for member in team_a}), min(len(team_a), 10))
        access = self.school.student_board_access(self.session_id, self.maple_id)
        student = self.school.read_board(
            self.session_id,
            str(access["collab_key"]),
            0,
            student_id=self.maple_id,
        )
        teacher = self.school.read_board(
            self.session_id,
            "teacher",
            0,
            as_teacher=True,
        )
        student_maple = next(
            m for m in student["formative"]["members"] if m["id"] == str(self.maple_id)
        )
        teacher_maple = next(
            m for m in teacher["formative"]["members"] if m["id"] == str(self.maple_id)
        )
        self.assertEqual(student_maple["color"], a_colors[str(self.maple_id)])
        self.assertEqual(teacher_maple["color"], legend_colors[str(self.maple_id)])
        self.assertEqual(student_maple["dashed"], teacher_maple["dashed"])
        self.assertEqual(student_maple["slot"], by_id[str(self.maple_id)]["slot"])

        before = {m["id"]: m for m in team_a}
        leaver = next(m for m in team_a if m["slot"] == 3)
        self.school.mark_live_session_attendee_left(
            self.session_id, student_id=int(leaver["id"])
        )
        after_leave = formative_board_members(self.school, self.session_id, self.team_sort_order)
        self.assertNotIn(leaver["id"], {m["id"] for m in after_leave})
        for member in after_leave:
            prior = before[member["id"]]
            self.assertEqual(member["slot"], prior["slot"])
            self.assertEqual(member["color"], prior["color"])
            self.assertEqual(member["dashed"], prior["dashed"])
        late_id = self._enroll("LateA", self.team_id)
        present = formative_board_members(self.school, self.session_id, self.team_sort_order)
        late = next(m for m in present if m["id"] == str(late_id))
        used = {int(m["slot"]) for m in before.values()}
        self.assertEqual(late["slot"], min(i for i in range(len(used) + 1) if i not in used))
        self.assertNotEqual(late["slot"], int(leaver["slot"]))
        for member in present:
            if member["id"] == str(late_id):
                late_rows = self.school.list_live_session_team_members(
                    self.session_id, self.team_sort_order
                )
                late_colors = assign_board_colors(
                    [
                        int(row["student_id"])
                        for row in late_rows
                        if row.get("student_id") not in (None, "")
                    ]
                )
                self.assertEqual(member["color"], late_colors[str(late_id)])
                continue
            self.assertEqual(member["slot"], before[member["id"]]["slot"])
            self.assertEqual(member["color"], before[member["id"]]["color"])
        self.assertEqual(len(enrolled), 10)

    def test_collab_always_shows_names(self) -> None:
        """Collab boards keep the name flag. Labels live on the legend."""
        access = self.school.student_board_access(self.session_id, self.maple_id)
        student = self.school.read_board(
            self.session_id,
            str(access["collab_key"]),
            0,
            student_id=self.maple_id,
        )
        teacher = self.school.read_board(
            self.session_id,
            "teacher",
            0,
            as_teacher=True,
        )
        self.assertTrue(student["formative"].get("always_show_names"))
        self.assertFalse(student["formative"].get("projector_show_names"))
        self.assertTrue(teacher["formative"].get("always_show_names"))
        self.assertFalse(teacher["formative"].get("projector_show_names"))

    def test_team_board_poll_does_not_load_the_whole_session(self) -> None:
        """A team board read lists that team, and the teacher board lists once."""
        seen: list[int | None] = []
        original = self.school.list_live_session_team_members

        def wrapped(session_id: int, team_sort_order: int | None = None) -> list[dict[str, Any]]:
            seen.append(None if team_sort_order is None else int(team_sort_order))
            return original(session_id, team_sort_order)

        self.school.list_live_session_team_members = wrapped  # type: ignore[method-assign]
        access = self.school.student_board_access(self.session_id, self.maple_id)
        self.school.read_board(
            self.session_id,
            str(access["collab_key"]),
            0,
            student_id=self.maple_id,
        )
        self.assertTrue(seen)
        self.assertEqual(set(seen), {int(self.team_sort_order)})
        seen.clear()
        self.school.read_board(
            self.session_id,
            "teacher",
            0,
            as_teacher=True,
        )
        self.assertEqual(seen, [None])

    def test_idle_poll_skips_the_class_board_lookup(self) -> None:
        """No projected board means no session or gallery read for it."""
        state = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertFalse(str(state.get("whiteboard_projected_board") or "").strip())
        self.school._teacher_state_for_poll = (int(self.session_id), state)

        def boom(*_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("class board looked up a session")

        self.school.get_live_session = boom  # type: ignore[method-assign]
        self.school.live_session_teacher_state_payload = boom  # type: ignore[method-assign]
        self.school._require_gallery_card = boom  # type: ignore[method-assign]
        self.assertIsNone(self.school.whiteboard_class_board(self.session_id))

    def test_student_poll_loads_teacher_state_once_when_nothing_is_shown(self) -> None:
        """Formative meta reuses its teacher state. An idle class board does not load another."""
        calls = {"n": 0}
        original = self.school.live_session_teacher_state_payload

        def wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
            calls["n"] += 1
            return original(*args, **kwargs)

        self.school.live_session_teacher_state_payload = wrapped  # type: ignore[method-assign]
        access = self.school.student_board_access(self.session_id, self.maple_id)
        self.school.read_board(
            self.session_id,
            str(access["collab_key"]),
            0,
            student_id=self.maple_id,
        )
        self.assertEqual(calls["n"], 1)

    def test_shown_own_board_keeps_the_open_stroke_and_tools(self) -> None:
        """Show to class on the viewer's board does not reset or disable tools."""
        portal = (REPO_ROOT / "lms" / "static" / "student-portal.js").read_text(encoding="utf-8")
        start = portal.index("function syncClassBoard")
        chunk = portal[start:portal.index("function bindStudentCanvas")]
        own = chunk.index('=== "show-own"')
        reset = chunk.index("resetRun")
        self.assertLess(own, reset)
        own_branch = chunk[own:chunk.index('return "show-own"')]
        self.assertNotIn("resetRun", own_branch)
        self.assertNotIn("setViewOnly", own_branch)
        home = (REPO_ROOT / "lms" / "templates" / "student" / "home.html").read_text(encoding="utf-8")
        for token in (
            "student-canvas-pen",
            "student-canvas-text",
            "student-canvas-erase",
            "Name your masterpiece",
        ):
            self.assertIn(token, home)


if __name__ == "__main__":
    unittest.main()

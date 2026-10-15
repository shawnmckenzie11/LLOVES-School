#!/usr/bin/env python3
"""MCK-218 formative whiteboard helpers and board-read presence guard."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from live_presence import LivePresenceUnavailable  # noqa: E402
from whiteboard_formative import (  # noqa: E402
    FORMATIVE_MEMBER_PALETTE,
    formative_board_members,
    formative_whiteboard_enabled,
    member_color,
    first_name_only,
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

    def test_teacher_can_set_whiteboard_name_toggles(self) -> None:
        """Staff POST persists formative projector / always-show toggles."""
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
        self.assertTrue(state.get("whiteboard_always_show_names"))
        self.assertTrue(state.get("whiteboard_projector_names"))

    def test_student_cannot_post_teacher_state_toggles(self) -> None:
        """Students cannot patch teacher-only whiteboard toggles."""
        rv = self.student.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"whiteboard_always_show_names": True},
        )
        self.assertIn(rv.status_code, (302, 401, 403))

    def test_member_slot_stable_when_teammate_leaves(self) -> None:
        """Departed students keep their slot index for remaining chip colours."""
        before = formative_board_members(self.school, self.session_id, self.team_id)
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
        after = formative_board_members(self.school, self.session_id, self.team_id)
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0]["id"], str(self.maple_id))
        self.assertEqual(after[0]["color"], maple_color)
        self.assertEqual(after[0]["slot"], 0)

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


if __name__ == "__main__":
    unittest.main()

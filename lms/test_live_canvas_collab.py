#!/usr/bin/env python3
"""Group whiteboard collab and session text labels."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402


class WhiteboardCollabTests(unittest.TestCase):
    """Publishing the whiteboard to the group turns collab on."""

    def setUp(self) -> None:
        """Staff session with one live class."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.client.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Aspen", "Birch"],
            },
        )
        self.class_id = int(created.get_json()["class"]["id"])
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _whiteboard(self) -> dict:
        """Return the seeded whiteboard lifecycle row."""
        self.school.ensure_live_session_items(self.session_id)
        for row in self.school.list_live_session_items(self.session_id):
            if str(row.get("kind") or "") == "whiteboard":
                return row
        self.fail("whiteboard lifecycle row missing")
        return {}

    def _team_for(self, _class_id: int, student_id: int) -> int:
        """Map the two collab writers onto one group and a third onto another."""
        return 2 if int(student_id) == 13 else 1

    def test_shared_group_choice_survives_refresh_and_uses_team_lock(self) -> None:
        """Whiteboard Shared within Group stays set and shares one locked team.

        A deck refresh and a page change must not put the mode back on
        Individual. Teammates then see each other's strokes and cursors.
        """

        self.school.game.add_student(self.class_id, codename="Cedar")
        students = [
            dict(row)
            for row in self.school.game.conn.execute(
                "SELECT id, codename FROM students WHERE class_id = ? ORDER BY id",
                (self.class_id,),
            ).fetchall()
        ]
        self.assertGreaterEqual(len(students), 3)
        mates = students[:2]
        other = students[2]
        self.school.game.begin_game(self.class_id)
        for student in students:
            self.school.join_live_class_session(
                self.session_id,
                int(student["id"]),
                codename=str(student["codename"]),
            )
        self.school.setup_live_session_groups(
            self.session_id,
            n_teams=2,
            mode="manual",
            present_ids=[int(student["id"]) for student in students],
            assignments=[
                {"student_id": int(student["id"]), "team_index": 0}
                for student in mates
            ]
            + [{"student_id": int(other["id"]), "team_index": 1}],
        )
        row = self._whiteboard()
        saved = self.client.patch(
            f"/api/live-sessions/{self.session_id}/items/{int(row['id'])}/settings",
            json={"publish_mode": "group_shared"},
        )
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual(saved.get_json()["item"]["publish_mode"], "group_shared")
        self.school.ensure_live_session_items(self.session_id)
        self.school.set_live_session_teacher_state(
            self.session_id, stage="play", page_id="later"
        )
        self.school.set_live_session_teacher_state(
            self.session_id, stage="meet", page_id="meet"
        )
        state = self.school.get_live_session_state(self.session_id)
        board = next(
            item
            for item in state["live_items"]
            if str(item.get("kind") or "") == "whiteboard"
        )
        self.assertEqual(board["publish_mode"], "group_shared")
        self.assertEqual(board["item"]["publish_mode"], "group_shared")
        published = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{int(row['id'])}/publish",
            json={"publish_mode": "group_shared"},
        )
        self.assertEqual(published.status_code, 200, published.get_json())
        teacher = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertEqual(teacher["canvas_align"], "team")
        self.assertEqual(teacher["student_view"]["canvas"], "team")
        for student, x in (
            (mates[0], 0.2),
            (mates[1], 0.55),
            (other, 0.8),
        ):
            team_id = self.school.student_team_id_for_class(
                self.class_id, int(student["id"])
            )
            self.assertIsNotNone(team_id)
            self.school.apply_live_canvas_presence(
                self.session_id,
                owner=str(int(student["id"])),
                name=str(student["codename"]),
                team_id=team_id,
                x=x,
                y=0.4,
                stroke_id=f"s-{student['id']}",
                point=[x, 0.4],
                as_teacher=False,
            )
        first = int(mates[0]["id"])
        view = self.school.live_session_canvas_view(
            self.session_id, student_id=first
        )
        self.assertEqual(
            {stroke["owner"] for stroke in view["strokes"]},
            {str(int(student["id"])) for student in mates},
        )
        self.assertEqual(
            {cursor["name"] for cursor in view["cursors"]},
            {str(student["codename"]) for student in mates},
        )
        self.assertNotIn(str(int(other["id"])), {stroke["owner"] for stroke in view["strokes"]})

    def test_group_publish_shares_strokes_and_named_cursors(self) -> None:
        """group_shared publish stores every writer's stroke and cursor name."""
        row = self._whiteboard()
        self.assertIn("group_shared", row["item"].get("publish_modes") or [])
        published = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{int(row['id'])}/publish",
            json={"publish_mode": "group_shared"},
        )
        self.assertEqual(published.status_code, 200, published.get_json())
        self.assertEqual(published.get_json()["item"]["publish_mode"], "group_shared")
        state = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertEqual(state["canvas_align"], "team")
        self.assertEqual(state["student_view"]["canvas"], "team")
        self.school.student_team_id_for_class = self._team_for  # type: ignore[method-assign]
        for owner, name, x in (("11", "Aspen", 0.2), ("12", "Birch", 0.55)):
            self.school.apply_live_canvas_presence(
                self.session_id,
                owner=owner,
                name=name,
                team_id=1,
                x=x,
                y=0.4,
                stroke_id=f"s-{owner}",
                point=[x, 0.4],
                as_teacher=False,
            )
        self.school.apply_live_canvas_presence(
            self.session_id,
            owner="teacher",
            name="Teacher",
            x=0.4,
            y=0.7,
            stroke_id="s-teacher",
            point=[0.4, 0.7],
            as_teacher=True,
        )
        view = self.school.live_session_canvas_view(
            self.session_id, student_id=11
        )
        self.assertEqual(
            {stroke["owner"] for stroke in view["strokes"]},
            {"11", "12", "teacher"},
        )
        self.assertEqual(
            {cursor["name"] for cursor in view["cursors"]},
            {"Aspen", "Birch", "Teacher"},
        )
        other = self.school.live_session_canvas_view(
            self.session_id, student_id=13
        )
        other_owners = {stroke["owner"] for stroke in other["strokes"]}
        self.assertNotIn("11", other_owners)
        self.assertNotIn("12", other_owners)
        self.assertIn("teacher", other_owners)

    def test_text_tool_persists_for_the_session_and_edits(self) -> None:
        """Text labels survive a reload, can be edited, and can be cleared."""
        before = self.school.live_student_poll_stamp(self.session_id, self.class_id)
        saved = self.school.apply_live_canvas_text(
            self.session_id,
            owner="11",
            name="Aspen",
            text_id="tx-1",
            text="slope",
            x=0.25,
            y=0.3,
            as_teacher=False,
        )
        self.assertEqual(saved["texts"][0]["text"], "slope")
        reloaded = self.school.live_session_canvas_sync(self.session_id)
        self.assertEqual(reloaded["texts"][0]["text"], "slope")
        self.school.set_live_session_teacher_state(
            self.session_id, student_view={"canvas": "student"}
        )
        view = self.school.live_session_canvas_view(
            self.session_id, student_id=11
        )
        self.assertEqual(view["texts"][0]["text"], "slope")
        self.assertTrue(view["texts"][0]["mine"])
        hidden = self.school.live_session_canvas_view(
            self.session_id, student_id=12
        )
        self.assertEqual(hidden["texts"], [])
        edited = self.school.apply_live_canvas_text(
            self.session_id,
            owner="11",
            name="Aspen",
            text_id="tx-1",
            text="slope!",
            x=0.25,
            y=0.3,
            as_teacher=False,
        )
        self.assertEqual(edited["texts"][0]["text"], "slope!")
        after = self.school.live_student_poll_stamp(self.session_id, self.class_id)
        self.assertNotEqual(before, after)
        cleared = self.school.apply_live_canvas_text(
            self.session_id,
            owner="11",
            name="Aspen",
            text_id="tx-1",
            text="",
            x=0.25,
            y=0.3,
            as_teacher=False,
        )
        self.assertEqual(cleared["texts"], [])

    def test_scoreboard_preview_matches_option_row_and_text_tool_is_wired(self) -> None:
        """Preview height is the options row, and both boards expose Text."""
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn(
            "body.staff-shell .live-unlocks-strip > #ap-scoreboard-preview-wrap",
            css,
        )
        self.assertIn("height: var(--live-options-row-h)", css)
        staff = (LMS_DIR / "templates" / "staff" / "course.html").read_text(
            encoding="utf-8"
        )
        student = (LMS_DIR / "templates" / "student" / "home.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('id="live-canvas-text"', staff)
        self.assertIn('id="student-canvas-text"', student)
        script = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("text_id", script)
        self.assertIn("whiteboardCollabOn", script)

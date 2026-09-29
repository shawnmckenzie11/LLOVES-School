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

    def test_teacher_shell_does_not_fan_in_group_boards(self) -> None:
        """Staff polls echo the teacher board, not every group's strokes.

        Publishing Shared within Group still stores teammate strokes for
        students. The teacher ``/state`` payload and canvas tick omit them.
        """
        row = self._whiteboard()
        published = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{int(row['id'])}/publish",
            json={"publish_mode": "group_shared"},
        )
        self.assertEqual(published.status_code, 200, published.get_json())
        self.school.student_team_id_for_class = self._team_for  # type: ignore[method-assign]
        for owner, name, team_id, x in (
            ("11", "Aspen", 1, 0.2),
            ("12", "Birch", 1, 0.4),
            ("13", "Cedar", 2, 0.8),
        ):
            self.school.apply_live_canvas_presence(
                self.session_id,
                owner=owner,
                name=name,
                team_id=team_id,
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
            x=0.5,
            y=0.6,
            stroke_id="s-teacher",
            point=[0.5, 0.6],
            as_teacher=True,
        )
        staff = self.school.live_session_canvas_view(
            self.session_id, as_teacher=True
        )
        self.assertEqual(
            {stroke["owner"] for stroke in staff["strokes"]},
            {"teacher"},
        )
        self.assertEqual(
            {cursor["owner"] for cursor in staff["cursors"]},
            {"teacher"},
        )
        aspen = self.school.live_session_canvas_view(
            self.session_id, student_id=11
        )
        self.assertEqual(
            {stroke["owner"] for stroke in aspen["strokes"]},
            {"11", "12", "teacher"},
        )
        cedar = self.school.live_session_canvas_view(
            self.session_id, student_id=13
        )
        self.assertEqual(
            {stroke["owner"] for stroke in cedar["strokes"]},
            {"13", "teacher"},
        )
        for light in ("", "?light=1"):
            polled = self.client.get(
                f"/api/live-sessions/{self.session_id}/state{light}"
            )
            self.assertEqual(polled.status_code, 200, polled.get_json())
            body = polled.get_json()
            painted = {stroke["owner"] for stroke in body["canvas_sync"]["strokes"]}
            self.assertEqual(painted, {"teacher"})
            teams = body["session"]["canvas_sync"]["strokes"]["teams"]
            self.assertEqual(teams, {})
            self.assertNotIn("canvas_sync_json", body["session"])
        tick = self.client.post(
            f"/api/live-sessions/{self.session_id}/canvas-presence",
            json={
                "x": 0.15,
                "y": 0.25,
                "point": [0.15, 0.25],
                "stroke_id": "s-teacher-2",
            },
        )
        self.assertEqual(tick.status_code, 200, tick.get_json())
        tick_body = tick.get_json()
        for key in ("canvas_view", "canvas_sync"):
            owners = {
                stroke["owner"] for stroke in tick_body[key]["strokes"]
            }
            self.assertEqual(owners, {"teacher"}, key)
            self.assertNotIsInstance(tick_body[key]["strokes"], dict)

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
        self.assertIn("teacherCanvasWithoutGroupBoards", script)
        self.assertIn("teacherCalmFrameHeld", script)
        self.assertIn("must not white-wipe", script)
        self.assertIn('paintTeacherCanvas(payload.canvas_sync, { source: "poll" })', script)

    def _stroke_by_id(self, stroke_id: str) -> dict:
        """Return one stored stroke by id, from either bucket."""
        blob = self.school.live_session_canvas_sync(self.session_id)
        rows = list(blob["strokes"]["teacher"])
        for team_rows in blob["strokes"]["teams"].values():
            rows.extend(team_rows)
        for row in rows:
            if row.get("id") == stroke_id:
                return row
        self.fail(f"stroke {stroke_id} was not stored")
        return {}

    def test_presence_accepts_point_batch_and_keeps_single_point(self) -> None:
        """``points`` appends a batch; a lone ``point`` still appends one.

        A legacy lift (``ended`` without ``points``) does not add another
        sample. A batch marked ended keeps every sample in that batch.
        The per-stroke cap still bounds a long batch.
        """
        from live_canvas import MAX_POINTS

        row = self._whiteboard()
        published = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{int(row['id'])}/publish",
            json={"publish_mode": "group_shared"},
        )
        self.assertEqual(published.status_code, 200, published.get_json())
        batch = [[round(0.05 + i * 0.004, 4), 0.4] for i in range(12)]
        self.school.apply_live_canvas_presence(
            self.session_id,
            owner="11",
            name="Aspen",
            team_id=1,
            x=batch[-1][0],
            y=batch[-1][1],
            stroke_id="batch-1",
            points=batch,
            ended=True,
            as_teacher=False,
        )
        stored = self._stroke_by_id("batch-1")
        self.assertEqual(stored["points"], batch)
        self.school.apply_live_canvas_presence(
            self.session_id,
            owner="11",
            name="Aspen",
            team_id=1,
            stroke_id="legacy-1",
            point=[0.2, 0.3],
            as_teacher=False,
        )
        self.school.apply_live_canvas_presence(
            self.session_id,
            owner="11",
            name="Aspen",
            team_id=1,
            stroke_id="legacy-1",
            point=[0.8, 0.8],
            ended=True,
            as_teacher=False,
        )
        legacy = self._stroke_by_id("legacy-1")
        self.assertEqual(legacy["points"], [[0.2, 0.3]])
        self.school.apply_live_canvas_presence(
            self.session_id,
            owner="11",
            name="Aspen",
            team_id=1,
            stroke_id="deduped",
            points=[[0.1, 0.1], [0.1, 0.1], [0.3, 0.3]],
            as_teacher=False,
        )
        self.assertEqual(
            self._stroke_by_id("deduped")["points"],
            [[0.1, 0.1], [0.3, 0.3]],
        )
        flood = [[i / 10000, 0.2] for i in range(MAX_POINTS + 40)]
        self.school.apply_live_canvas_presence(
            self.session_id,
            owner="11",
            name="Aspen",
            team_id=1,
            stroke_id="capped",
            points=flood,
            as_teacher=False,
        )
        capped = self._stroke_by_id("capped")
        self.assertEqual(len(capped["points"]), MAX_POINTS)
        self.assertGreater(MAX_POINTS, 80)

    def _publish_and_join_two_groups(self) -> dict[str, str]:
        """Publish the group board and return visit tokens keyed by codename."""
        self.school.game.add_student(self.class_id, codename="Cedar")
        students = [
            dict(row)
            for row in self.school.game.conn.execute(
                "SELECT id, codename FROM students WHERE class_id = ? ORDER BY id",
                (self.class_id,),
            ).fetchall()
        ]
        mates = [row for row in students if row["codename"] in ("Aspen", "Birch")]
        other = next(row for row in students if row["codename"] == "Cedar")
        self.school.game.begin_game(self.class_id)
        tokens: dict[str, str] = {}
        for student in students:
            joined = self.school.join_live_class_session(
                self.session_id,
                int(student["id"]),
                codename=str(student["codename"]),
            )
            tokens[str(student["codename"])] = str(joined["attendee"]["visit_token"])
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
        published = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{int(row['id'])}/publish",
            json={"publish_mode": "group_shared"},
        )
        self.assertEqual(published.status_code, 200, published.get_json())
        return tokens

    def _post_as_student(self, token: str, path: str, body: dict) -> dict:
        """POST canvas presence as one joined student and return JSON."""
        client = self.app.test_client()
        response = client.post(
            path,
            json=body,
            headers={"X-Student-Visit-Token": token},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def test_student_presence_reply_omits_other_teams(self) -> None:
        """A student reply is own team plus teacher, on both presence routes.

        The stored blob still has every group. Neither ``canvas_view`` nor
        ``canvas_sync`` in the student reply carries another team's stroke.
        """
        tokens = self._publish_and_join_two_groups()
        teacher = self.client.post(
            f"/api/live-sessions/{self.session_id}/canvas-presence",
            json={
                "x": 0.5,
                "y": 0.5,
                "point": [0.5, 0.5],
                "stroke_id": "teacher-ink",
            },
        )
        self.assertEqual(teacher.status_code, 200, teacher.get_json())
        aspen_batch = [[0.12, 0.22], [0.18, 0.28], [0.24, 0.3]]
        aspen = self._post_as_student(
            tokens["Aspen"],
            "/api/student/canvas-presence",
            {
                "x": 0.24,
                "y": 0.3,
                "points": aspen_batch,
                "stroke_id": "aspen-batch",
                "ended": True,
            },
        )
        cedar = self._post_as_student(
            tokens["Cedar"],
            "/api/student/canvas-presence",
            {
                "x": 0.7,
                "y": 0.3,
                "point": [0.7, 0.3],
                "stroke_id": "cedar-one",
            },
        )
        self.assertEqual(self._stroke_by_id("aspen-batch")["points"], aspen_batch)
        self.assertEqual(self._stroke_by_id("cedar-one")["points"], [[0.7, 0.3]])

        def owners(payload: dict) -> set[str]:
            found: set[str] = set()
            for key in ("canvas_view", "canvas_sync"):
                node = payload.get(key) or {}
                strokes = node.get("strokes")
                self.assertIsInstance(strokes, list, key)
                for stroke in strokes:
                    found.add(str(stroke.get("owner") or ""))
            return found

        aspen_id = str(
            self.school.game.find_student_by_codename(self.class_id, "Aspen")["id"]
        )
        cedar_id = str(
            self.school.game.find_student_by_codename(self.class_id, "Cedar")["id"]
        )
        self.assertEqual(owners(aspen), {aspen_id, "teacher"})
        self.assertEqual(owners(cedar), {cedar_id, "teacher"})
        self.assertNotIn("cedar-one", str(aspen))
        self.assertNotIn("aspen-batch", str(cedar))

        session_reply = self._post_as_student(
            tokens["Aspen"],
            f"/api/live-sessions/{self.session_id}/canvas-presence",
            {
                "x": 0.15,
                "y": 0.22,
                "points": [[0.15, 0.22]],
                "stroke_id": "aspen-more",
            },
        )
        self.assertNotIn("cedar-one", str(session_reply))
        self.assertIn("aspen-batch", str(session_reply))
        self.assertIn("teacher-ink", str(session_reply))
        self.assertNotIn("aspen-batch", str(cedar))

    def test_client_batches_ink_without_hover_posts(self) -> None:
        """The board flushes on a timer and does not POST a hover cursor."""
        wb = (LMS_DIR / "static" / "live_whiteboard.js").read_text(encoding="utf-8")
        student = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        staff = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("export const PRESENCE_FLUSH_MS = 80", wb)
        self.assertIn("requestAnimationFrame", wb)
        self.assertIn("devicePixelRatio", wb)
        self.assertIn("createPresenceQueue", wb)
        self.assertNotIn("wb-${Date.now()}", wb)
        self.assertIn("onPoints:", student)
        self.assertIn("onPoints:", staff)
        self.assertNotIn("cursorOnly: true", student)
        self.assertNotIn("cursorOnly: true", staff)
        self.assertIn("normalizeBoardPoint", student)
        self.assertIn("normalizeBoardPoint", staff)
        self.assertIn('if (node !== studentCanvas) return;', student)

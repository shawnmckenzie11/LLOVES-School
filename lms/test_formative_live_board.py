#!/usr/bin/env python3
"""One live whiteboard: gallery on team and solo boards.

Strokes stay in board_ops. Teacher feedback is a layer on that same board
and stays hidden until the teacher sends it. These tests cover spoofed
ids, hidden feedback, test-class delete (including team boards), and
roster names.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from board_ops import MAX_OPS_PER_BATCH  # noqa: E402
from live_canvas import MAX_POINTS, MAX_STROKES  # noqa: E402


class FormativeLimitTests(unittest.TestCase):
    """Caps the live board already uses stay put."""

    def test_point_stroke_and_batch_caps(self) -> None:
        """MAX_POINTS, MAX_STROKES, and MAX_OPS_PER_BATCH are unchanged."""
        self.assertEqual(MAX_POINTS, 360)
        self.assertEqual(MAX_STROKES, 80)
        self.assertEqual(MAX_OPS_PER_BATCH, 32)

    def test_client_module_parses(self) -> None:
        """The grid script is valid JavaScript."""
        completed = subprocess.run(
            ["node", "--check", "lms/static/formative_live_board.js"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_stale_run_refetches_and_retries_once(self) -> None:
        """A stale-run 409 is posted again with the fetched key, not the old one."""
        script = """
globalThis.HTMLElement = class HTMLElement {};
globalThis.document = { getElementById() { return null; } };
const { postOpsWithFreshRun } = await import('./lms/static/formative_live_board.js');
const sent = [];
const first = await postOpsWithFreshRun({
  runKey: 'old-run',
  post: async (key) => {
    sent.push(key);
    if (key === 'old-run') return { status: 409, data: { ok: false, stale_run: true } };
    return { status: 200, data: { ok: true, run_key: key } };
  },
  refetch: async () => 'current-run',
});
if (sent.join(',') !== 'old-run,current-run') {
  console.error(JSON.stringify(sent));
  process.exit(1);
}
if (first.status !== 200) process.exit(2);
const same = [];
const stuck = await postOpsWithFreshRun({
  runKey: 'old-run',
  post: async (key) => {
    same.push(key);
    return { status: 409, data: { stale_run: true } };
  },
  refetch: async () => 'old-run',
});
if (same.join(',') !== 'old-run' || stuck.status !== 409) {
  console.error(JSON.stringify(same));
  process.exit(3);
}
let fetches = 0;
await postOpsWithFreshRun({
  runKey: 'current-run',
  post: async () => ({ status: 200, data: { ok: true } }),
  refetch: async () => {
    fetches += 1;
    return 'other-run';
  },
});
if (fetches !== 0) process.exit(4);
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)


class FormativeLiveBoardTests(unittest.TestCase):
    """Teacher grid and the signed-in student's own board."""

    def setUp(self) -> None:
        """One live class with Maple, Cedar, and Pine joined."""
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
        self.session_code = str(live.get("session_code") or "")
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
        self.maple = self.app.test_client()
        self.cedar_client = self.app.test_client()
        self.maple_token = self._visit_token(self.maple_id)
        self.cedar_token = self._visit_token(self.cedar_id)

    def tearDown(self) -> None:
        """Close sqlite and remove the temp dir."""
        db = self.app.config.get("SCHOOL_DB")
        if db is not None:
            db.close()
        self.tmp.cleanup()

    def _visit_token(self, student_id: int) -> str:
        """Return the attendee visit token for one roster id.

        Args:
            student_id: Roster id.
        """
        row = self.school.conn.execute(
            """
            SELECT visit_token FROM live_session_attendees
            WHERE live_session_id = ? AND student_id = ?
            """,
            (self.session_id, int(student_id)),
        ).fetchone()
        return str(row["visit_token"] if row else "")

    def _student_headers(self, token: str) -> dict[str, str]:
        """Header map for one joined student. The token is not logged.

        Args:
            token: Visit token for that attendee.
        """
        return {"X-Student-Visit-Token": token}

    def _text_op(self, text: str, op_id: str) -> dict[str, object]:
        """One text label op with no name and no secret fields.

        Args:
            text: Label body.
            op_id: Client id.
        """
        return {
            "type": "text_upsert",
            "id": op_id,
            "text": text,
            "x": 0.2,
            "y": 0.3,
            "color": "#2563eb",
        }

    def _publish(self, mode: str) -> None:
        """Publish the session whiteboard in ``individual`` or ``group_shared``.

        Args:
            mode: Publish mode stored on the whiteboard row.
        """
        self.school.ensure_live_session_items(self.session_id)
        whiteboard = next(
            row
            for row in self.school.list_live_session_items(self.session_id)
            if str(row.get("kind") or "") == "whiteboard"
        )
        self.school.publish_live_session_item(
            self.session_id, int(whiteboard["id"]), publish_mode=mode
        )

    def _participation_rows(self) -> int:
        """Count gradebook participation rows. Boards must not add any."""
        total = 0
        for table in (
            "participation_answers",
            "participation_items",
            "participation_module_marks",
        ):
            row = self.school.conn.execute(
                f"SELECT COUNT(*) AS n FROM {table}"
            ).fetchone()
            total += int(row["n"] or 0)
        return total

    def test_student_id_in_the_query_is_ignored(self) -> None:
        """A caller cannot read or write another student's solo board."""
        self._publish("individual")
        cedar_key = f"solo:{self.cedar_id}"
        maple_key = f"solo:{self.maple_id}"
        run_key = self.school.live_board_run_key(self.session_id)
        cedar_ink = self.cedar_client.post(
            f"/api/student/board/{cedar_key}/ops",
            json={
                "student_id": self.maple_id,
                "run_key": run_key,
                "ops": [self._text_op("cedar-secret-ink", "cedar-ink")],
            },
            headers=self._student_headers(self.cedar_token),
        )
        self.assertEqual(cedar_ink.status_code, 200, cedar_ink.get_data(as_text=True))
        spoofed = self.maple.get(
            f"/api/student/board/{cedar_key}",
            headers=self._student_headers(self.maple_token),
        )
        self.assertEqual(spoofed.status_code, 403, spoofed.get_data(as_text=True))
        own = self.maple.get(
            f"/api/student/board/{maple_key}",
            headers=self._student_headers(self.maple_token),
        )
        self.assertEqual(own.status_code, 200, own.get_data(as_text=True))
        self.assertNotIn("cedar-secret-ink", json.dumps(own.get_json()))
        denied_write = self.maple.post(
            f"/api/student/board/{cedar_key}/ops",
            json={
                "run_key": run_key,
                "ops": [self._text_op("maple-only-ink", "maple-ink")],
            },
            headers=self._student_headers(self.maple_token),
        )
        self.assertEqual(denied_write.status_code, 403)
        for key in (f"fb:{self.cedar_id}", f"student:{self.cedar_id}"):
            denied = self.maple.get(
                f"/api/student/board/{key}",
                headers=self._student_headers(self.maple_token),
            )
            self.assertEqual(denied.status_code, 403, key)

    def test_feedback_stays_hidden_until_release(self) -> None:
        """Send feedback is what makes teacher ink visible on that board."""
        self._publish("individual")
        key = f"solo:{self.maple_id}"
        posted = self.staff.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/{key}/feedback",
            json={"ops": [self._text_op("check vertex", "fb-vertex")]},
        )
        self.assertEqual(posted.status_code, 200, posted.get_data(as_text=True))
        hidden = self.maple.get(
            f"/api/student/board/{key}",
            headers=self._student_headers(self.maple_token),
        )
        self.assertEqual(hidden.status_code, 200, hidden.get_data(as_text=True))
        self.assertNotIn("check vertex", json.dumps(hidden.get_json()))
        released = self.staff.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/{key}/release"
        )
        self.assertEqual(released.status_code, 200, released.get_data(as_text=True))
        shown = self.maple.get(
            f"/api/student/board/{key}",
            headers=self._student_headers(self.maple_token),
        )
        self.assertEqual(shown.status_code, 200, shown.get_data(as_text=True))
        self.assertIn("check vertex", json.dumps(shown.get_json()))

    def test_board_ink_does_not_write_the_gradebook(self) -> None:
        """Drawing on the whiteboard does not add participation rows."""
        self._publish("individual")
        before = self._participation_rows()
        posted = self.maple.post(
            f"/api/student/board/solo:{self.maple_id}/ops",
            json={
                "run_key": self.school.live_board_run_key(self.session_id),
                "ops": [self._text_op("ungraded", "grade-ink")],
            },
            headers=self._student_headers(self.maple_token),
        )
        self.assertEqual(posted.status_code, 200, posted.get_data(as_text=True))
        self.assertEqual(self._participation_rows(), before)
        gone = self.maple.post(
            "/api/student/formative-board/done",
            headers=self._student_headers(self.maple_token),
        )
        self.assertEqual(gone.status_code, 404)

    def test_gallery_uses_roster_name_not_first_name(self) -> None:
        """Cards show the roster name column, even when first_name differs."""
        self.school.conn.execute(
            """
            UPDATE students
            SET first_name = ?, last_display = ?
            WHERE id = ?
            """,
            ("Secretfirst", "NotThis", self.maple_id),
        )
        self.school.conn.commit()
        hidden = self.staff.get(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery"
        )
        self.assertEqual(hidden.status_code, 200, hidden.get_data(as_text=True))
        hidden_body = hidden.get_json()
        self.assertFalse(hidden_body["visible"])
        self.assertEqual(hidden_body["tiles"], [])
        self._publish("individual")
        named = self.staff.get(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery"
        )
        self.assertEqual(named.status_code, 200, named.get_data(as_text=True))
        body = named.get_json()
        self.assertTrue(body["visible"])
        maple = next(
            tile
            for tile in body["tiles"]
            if int(tile.get("student_id") or 0) == self.maple_id
        )
        self.assertEqual(maple["name"], "Maple")
        blob = json.dumps(body)
        self.assertNotIn("Secretfirst", blob)
        self.assertNotIn("NotThis", blob)
        self.assertNotIn("Student 1", blob)
        self.assertNotIn("first_name", blob)
        if self.session_code:
            self.assertNotIn(self.session_code, blob)
        self.school.conn.execute(
            "UPDATE students SET codename = ? WHERE id = ?",
            ("Renamed", self.maple_id),
        )
        self.school.conn.commit()
        renamed = self.staff.get(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery"
        ).get_json()
        maple_now = next(
            tile
            for tile in renamed["tiles"]
            if int(tile.get("student_id") or 0) == self.maple_id
        )
        self.assertEqual(maple_now["name"], "Renamed")
        self.assertNotIn("Secretfirst", json.dumps(renamed))
        self.school.conn.execute(
            "UPDATE students SET codename = '' WHERE id = ?",
            (self.cedar_id,),
        )
        self.school.conn.commit()
        blanked = self.staff.get(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery"
        ).get_json()
        cedar = next(
            tile
            for tile in blanked["tiles"]
            if int(tile.get("student_id") or 0) == self.cedar_id
        )
        self.assertEqual(cedar["name"], "")
        self.assertNotIn("Student ", json.dumps(blanked))

    def test_group_cards_use_team_names(self) -> None:
        """A group whiteboard is one card per team, labeled with the team name."""
        self._publish("group_shared")
        body = self.staff.get(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery"
        ).get_json()
        self.assertTrue(body["visible"])
        self.assertEqual(len(body["tiles"]), 2)
        names = sorted(str(tile["name"]) for tile in body["tiles"])
        self.assertTrue(all(names))
        self.assertNotIn("Maple", names)
        self.assertNotIn("Secretfirst", json.dumps(body))
        blob = json.dumps(body)
        self.assertNotIn("Student 1", blob)
        self.assertNotIn("Student 2", blob)
        for tile in body["tiles"]:
            self.assertTrue(str(tile["board_key"]).startswith("team:"))

    def test_tile_poll_and_show_are_teacher_only(self) -> None:
        """A joined student cannot poll the gallery or project a board."""
        self._publish("individual")
        tiles = self.maple.get(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery",
            headers=self._student_headers(self.maple_token),
        )
        show = self.maple.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/solo:{self.cedar_id}/show",
            headers=self._student_headers(self.maple_token),
        )
        self.assertIn(tiles.status_code, (401, 403))
        self.assertIn(show.status_code, (401, 403))

    def test_tile_poll_reads_every_board_in_one_query(self) -> None:
        """The gallery delta is one since_many call and one UNION statement."""
        self._publish("individual")
        calls = {"fn": 0, "sql": 0}
        original = self.school.boards.since_many
        real_exec = self.school.boards._exec

        def counting(sql: str, params: tuple = ()) -> object:
            """Count the combined delta statement."""
            if "UNION ALL" in str(sql):
                calls["sql"] += 1
            return real_exec(sql, params)

        def wrapped(run_key: str, cursors: dict) -> dict:
            """Count since_many and watch its statements."""
            calls["fn"] += 1
            self.school.boards._exec = counting
            try:
                return original(run_key, cursors)
            finally:
                self.school.boards._exec = real_exec

        self.school.boards.since_many = wrapped
        try:
            response = self.staff.get(
                f"/api/live-sessions/{self.session_id}/whiteboard-gallery"
            )
        finally:
            self.school.boards.since_many = original
            self.school.boards._exec = real_exec
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(calls["fn"], 1)
        self.assertEqual(calls["sql"], 1)
        self.assertEqual(len(response.get_json()["tiles"]), 3)

    def test_show_hides_feedback_until_release(self) -> None:
        """Show to class includes the feedback layer only after release."""
        self._publish("individual")
        key = f"solo:{self.maple_id}"
        mark = self._text_op("check vertex", "fb-show")
        posted = self.staff.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/{key}/feedback",
            json={"ops": [mark]},
        )
        self.assertEqual(posted.status_code, 200, posted.get_data(as_text=True))
        panel = self.staff.get(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/{key}"
        )
        self.assertIn("check vertex", json.dumps(panel.get_json()))
        hidden = self.staff.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/{key}/show"
        )
        self.assertEqual(hidden.status_code, 200, hidden.get_data(as_text=True))
        hidden_body = hidden.get_json()
        self.assertNotIn("check vertex", json.dumps(hidden_body))
        self.assertNotIn("feedback_view", hidden_body)
        self.assertEqual(hidden_body["name"], "Maple")
        self.school.release_whiteboard_feedback(self.session_id, key)
        shown = self.staff.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/{key}/show"
        )
        self.assertEqual(shown.status_code, 200, shown.get_data(as_text=True))
        shown_body = shown.get_json()
        self.assertIn("check vertex", json.dumps(shown_body))
        self.assertIn("feedback_view", shown_body)

    def test_delete_test_run_drops_team_boards(self) -> None:
        """Delete-test removes solo ink, team ink, and assignment rows."""
        self.school.end_live_class_session(self.session_id)
        test = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=True
        )
        sid = int(test["id"])
        self.school.join_live_class_session(sid, self.maple_id, codename="Maple")
        self.school.ensure_live_session_items(sid)
        whiteboard = next(
            row
            for row in self.school.list_live_session_items(sid)
            if str(row.get("kind") or "") == "whiteboard"
        )
        self.school.publish_live_session_item(
            sid, int(whiteboard["id"]), publish_mode="individual"
        )
        self.school.append_live_board_ops(
            sid,
            f"solo:{self.maple_id}",
            owner=str(self.maple_id),
            ops=[self._text_op("keep-until-delete", "del-ink")],
        )
        self.school.append_whiteboard_feedback(
            sid,
            f"solo:{self.maple_id}",
            [self._text_op("released mark", "fb-del")],
        )
        self.school.release_whiteboard_feedback(sid, f"solo:{self.maple_id}")
        self.school.record_live_board_assignment_for_student(sid, self.maple_id, 0)
        self.school.append_live_board_ops(
            sid,
            "team:0",
            owner=str(self.maple_id),
            ops=[self._text_op("team ink", "team-del")],
        )
        run_key = self.school.live_board_run_key(sid)
        self.assertGreater(self.school.boards.count_run_ops(run_key), 0)
        assigned = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM live_board_assignments WHERE live_session_id = ?",
            (sid,),
        ).fetchone()
        self.assertGreater(int(assigned["n"] or 0), 0)
        with self.assertRaises(KeyError):
            self.school.delete_test_live_class(self.class_id + 99, sid)
        deleted = self.school.delete_test_live_class(self.class_id, sid)
        self.assertEqual(deleted["deleted_session_id"], sid)
        left = self.school.conn.execute(
            """
            SELECT COUNT(*) AS n FROM live_board_assignments
            WHERE live_session_id = ? OR run_key = ?
            """,
            (sid, run_key),
        ).fetchone()
        self.assertEqual(int(left["n"] or 0), 0)
        self.assertEqual(self.school.boards.count_run_ops(run_key), 0)

    def test_cleanup_removes_board_rows(self) -> None:
        """End deletes board ink and keeps the team-board assignment rows."""
        self._publish("individual")
        self.school.append_live_board_ops(
            self.session_id,
            f"solo:{self.maple_id}",
            owner=str(self.maple_id),
            ops=[self._text_op("gone-on-cleanup", "clean-ink")],
        )
        self.school.record_live_board_assignment_for_student(
            self.session_id, self.maple_id, 0
        )
        run_key = self.school.live_board_run_key(self.session_id)
        self.school.cleanup_live_session_response_data(self.session_id)
        left = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM live_board_assignments WHERE live_session_id = ?",
            (self.session_id,),
        ).fetchone()
        self.assertGreater(int(left["n"] or 0), 0)
        self.assertEqual(self.school.boards.count_run_ops(run_key), 0)


if __name__ == "__main__":
    unittest.main()

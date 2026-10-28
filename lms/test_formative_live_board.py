#!/usr/bin/env python3
"""One live whiteboard: gallery on team and solo boards.

Strokes stay in board_ops. New teacher ink is live. Rows already stored
as a feedback layer stay hidden until a release, and those rows are not
deleted. These tests cover spoofed ids, that split, test-class delete
(including team boards), and roster names.
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
from whiteboard_formative import assign_board_colors, student_ink_color  # noqa: E402


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
const { foldGalleryInk, galleryRunChanged, showControlState } = await import('./lms/static/formative_live_board.js');
const bag = foldGalleryInk(
  { strokes: { old: { id: 'old', points: [[0.1, 0.1]] } }, texts: { t: { id: 't', text: 'old' } } },
  { snapshot: true, canvas_view: { strokes: [], texts: [] } },
  '#2563eb',
);
if (Object.keys(bag.strokes).length !== 0 || Object.keys(bag.texts).length !== 0) {
  console.error(JSON.stringify(bag));
  process.exit(5);
}
const kept = foldGalleryInk(bag, {
  snapshot: false,
  ops: [{ type: 'stroke_add', id: 'fresh', points: [[0.2, 0.2]], color: '#2563eb' }],
});
if (!kept.strokes.fresh) process.exit(6);
if (!galleryRunChanged('run-a', 'run-a~g1')) process.exit(7);
if (galleryRunChanged('', 'run-a')) process.exit(8);
if (galleryRunChanged('run-a', 'run-a')) process.exit(9);
const showing = showControlState('team:0', 'team:0');
const idle = showControlState('team:0', '');
if (showing.label !== 'Stop showing' || !showing.pressed) process.exit(10);
if (idle.label !== 'Show to class' || idle.pressed) process.exit(11);
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
        """New teacher ink is live. A stored feedback layer stays until release."""
        self._publish("individual")
        key = f"solo:{self.maple_id}"
        posted = self.staff.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/{key}/feedback",
            json={"ops": [self._text_op("check vertex", "fb-vertex")]},
        )
        self.assertEqual(posted.status_code, 200, posted.get_data(as_text=True))
        live = self.maple.get(
            f"/api/student/board/{key}",
            headers=self._student_headers(self.maple_token),
        )
        self.assertEqual(live.status_code, 200, live.get_data(as_text=True))
        self.assertIn("check vertex", json.dumps(live.get_json()))
        stored = dict(self._text_op("stored mark", "fb-stored"))
        stored["layer"] = "feedback"
        self.school.append_live_board_ops(
            self.session_id,
            key,
            owner="teacher",
            ops=[stored],
        )
        hidden = self.maple.get(
            f"/api/student/board/{key}",
            headers=self._student_headers(self.maple_token),
        )
        hidden_body = json.dumps(hidden.get_json())
        self.assertIn("check vertex", hidden_body)
        self.assertNotIn("stored mark", hidden_body)
        rows = json.dumps(self.school._board_rows(self.session_id, key))
        self.assertIn("stored mark", rows)
        self.assertIn("feedback", rows)
        released = self.staff.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/{key}/release"
        )
        self.assertEqual(released.status_code, 200, released.get_data(as_text=True))
        shown = self.maple.get(
            f"/api/student/board/{key}",
            headers=self._student_headers(self.maple_token),
        )
        self.assertEqual(shown.status_code, 200, shown.get_data(as_text=True))
        self.assertIn("stored mark", json.dumps(shown.get_json()))

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

    def _board_keys(self, run_key: str) -> list[str]:
        """Distinct board keys stored for one run.

        Args:
            run_key: Board partition key for the live session.
        """

        def read(exec_) -> list[str]:
            rows = exec_(
                "SELECT DISTINCT board_key FROM board_ops WHERE run_key = ?",
                (run_key,),
            ).fetchall()
            found: list[str] = []
            for row in rows:
                found.append(str(row["board_key"] if hasattr(row, "keys") else row[0]))
            return found

        return list(self.school.boards._read(read))

    def test_individual_pen_lands_on_solo_and_survives_migration(self) -> None:
        """An Individual pen stroke is stored on ``solo:<id>`` only.

        The client posts that stroke through canvas presence. Boot deletes
        ``student:`` and ``fb:`` keys, including when the migration runs
        again, and leaves the solo stroke in place. The teacher gallery
        tile for that student carries the stroke.
        """
        source = (LMS_DIR / "static" / "student-portal.js").read_text()
        self.assertIn(
            'if (align !== "team" && align !== "student") return;',
            source,
        )
        self._publish("individual")
        run_key = self.school.live_board_run_key(self.session_id)
        posted = self.maple.post(
            "/api/student/canvas-presence",
            json={
                "x": 0.22,
                "y": 0.41,
                "points": [[0.22, 0.41], [0.34, 0.52]],
                "stroke_id": "maple-solo-pen",
                "ended": True,
                "run_key": run_key,
            },
            headers=self._student_headers(self.maple_token),
        )
        self.assertEqual(posted.status_code, 200, posted.get_data(as_text=True))
        keys = self._board_keys(run_key)
        solo = f"solo:{self.maple_id}"
        self.assertIn(solo, keys)
        self.assertFalse(any(key.startswith("student:") or key.startswith("fb:") for key in keys))
        gallery = self.staff.get(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery"
        )
        self.assertEqual(gallery.status_code, 200, gallery.get_data(as_text=True))
        body = gallery.get_json()
        maple = next(
            tile
            for tile in body["tiles"]
            if int(tile.get("student_id") or 0) == self.maple_id
        )
        self.assertEqual(maple["board_key"], solo)
        ink = json.dumps(
            {
                "canvas_view": maple.get("canvas_view"),
                "ops": maple.get("ops"),
            }
        )
        self.assertIn("maple-solo-pen", ink)
        self.assertGreater(int(maple.get("board_seq") or 0), 0)
        self.school.boards._transaction(
            lambda exec_: exec_(
                "DELETE FROM board_flags WHERE name = ?",
                ("mck272_removed_keys",),
            )
        )
        self.school._migrate_mck272_whiteboard()
        self.assertIn(solo, self._board_keys(run_key))
        self.assertFalse(
            any(
                key.startswith("student:") or key.startswith("fb:")
                for key in self._board_keys(run_key)
            )
        )
        self.school._migrate_mck272_whiteboard()
        self.assertIn(solo, self._board_keys(run_key))

    def test_individual_pen_persists_during_round(self) -> None:
        """An Individual board stores solo strokes while ROUND hides the canvas.

        ROUND sets ``canvas_align`` to teacher, which used to accept the
        stroke and save nothing. Group already overrides that. Individual
        publish does too, and the rows stay on ``solo:<id>``.
        """
        self.school.set_live_session_teacher_state(self.session_id, stage="round")
        self._publish("individual")
        teacher = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertEqual(str(teacher.get("stage") or ""), "round")
        self.assertEqual(str(teacher.get("canvas_align") or ""), "teacher")
        run_key = self.school.live_board_run_key(self.session_id)
        pine = self.app.test_client()
        pine_token = self._visit_token(self.pine_id)
        writers = (
            (self.maple, self.maple_token, self.maple_id, "maple-round-pen"),
            (self.cedar_client, self.cedar_token, self.cedar_id, "cedar-round-pen"),
            (pine, pine_token, self.pine_id, "pine-round-pen"),
        )
        for client, token, _student_id, stroke_id in writers:
            posted = client.post(
                "/api/student/canvas-presence",
                json={
                    "x": 0.22,
                    "y": 0.41,
                    "points": [[0.22, 0.41], [0.34, 0.52]],
                    "stroke_id": stroke_id,
                    "ended": True,
                    "run_key": run_key,
                },
                headers=self._student_headers(token),
            )
            self.assertEqual(posted.status_code, 200, posted.get_data(as_text=True))
        keys = self._board_keys(run_key)
        self.assertFalse(
            any(key.startswith("student:") or key.startswith("fb:") for key in keys)
        )
        gallery = self.staff.get(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery"
        )
        self.assertEqual(gallery.status_code, 200, gallery.get_data(as_text=True))
        tiles = gallery.get_json()["tiles"]
        for _client, _token, student_id, stroke_id in writers:
            solo = f"solo:{student_id}"
            self.assertIn(solo, keys)
            self.assertNotIn(f"team:{student_id}", keys)
            tile = next(
                row for row in tiles if int(row.get("student_id") or 0) == student_id
            )
            self.assertEqual(tile["board_key"], solo)
            ink = json.dumps({"canvas_view": tile.get("canvas_view"), "ops": tile.get("ops")})
            self.assertIn(stroke_id, ink)
            self.assertGreater(int(tile.get("board_seq") or 0), 0)

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
        """Show includes live ink and the legend. Old feedback waits."""
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
        panel_body = panel.get_json()
        self.assertIn("check vertex", json.dumps(panel_body))
        self.assertTrue(panel_body.get("legend"))
        self.assertEqual(
            panel_body["legend"][0]["color"], student_ink_color(self.maple_id)
        )
        hidden = self.staff.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/{key}/show"
        )
        self.assertEqual(hidden.status_code, 200, hidden.get_data(as_text=True))
        hidden_body = hidden.get_json()
        self.assertIn("check vertex", json.dumps(hidden_body))
        self.assertNotIn("feedback_view", hidden_body)
        self.assertEqual(hidden_body["legend"][0]["color"], student_ink_color(self.maple_id))
        stored = dict(self._text_op("stored mark", "fb-show-old"))
        stored["layer"] = "feedback"
        self.school.append_live_board_ops(
            self.session_id, key, owner="teacher", ops=[stored]
        )
        still = self.staff.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/{key}/show"
        )
        still_body = still.get_json()
        self.assertNotIn("stored mark", json.dumps(still_body))
        self.assertNotIn("feedback_view", still_body)
        self.school.release_whiteboard_feedback(self.session_id, key)
        shown = self.staff.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/{key}/show"
        )
        self.assertEqual(shown.status_code, 200, shown.get_data(as_text=True))
        shown_body = shown.get_json()
        self.assertIn("stored mark", json.dumps(shown_body))
        self.assertIn("feedback_view", shown_body)

    def test_group_show_reaches_the_other_team(self) -> None:
        """Another team's student sees the projected board and its legend."""
        self._publish("group_shared")
        self.school.append_live_board_ops(
            self.session_id,
            "team:0",
            owner=str(self.maple_id),
            ops=[self._text_op("team sketch", "team-sketch")],
        )
        posted = self.staff.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/team:0/feedback",
            json={"ops": [self._text_op("teacher note", "t-note")]},
        )
        self.assertEqual(posted.status_code, 200, posted.get_data(as_text=True))
        shown = self.staff.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/team:0/show"
        )
        self.assertEqual(shown.status_code, 200, shown.get_data(as_text=True))
        pine = self.app.test_client()
        seen = pine.get(
            "/api/student/board/mine?since=0&teacher_since=0",
            headers=self._student_headers(self._visit_token(self.pine_id)),
        )
        self.assertEqual(seen.status_code, 200, seen.get_data(as_text=True))
        body = seen.get_json()
        board = body.get("class_board") or {}
        self.assertEqual(board.get("board_key"), "team:0")
        self.assertNotEqual(body.get("board_key"), "team:0")
        dumped = json.dumps(board)
        self.assertIn("teacher note", dumped)
        self.assertIn("team sketch", dumped)
        colors = {row["id"]: row["color"] for row in board.get("legend") or []}
        roster = self.school.list_live_session_team_members(self.session_id, 0)
        expected = assign_board_colors(
            [int(row["student_id"]) for row in roster if row.get("student_id") not in (None, "")]
        )
        self.assertEqual(colors[str(self.maple_id)], expected[str(self.maple_id)])
        self.assertEqual(colors[str(self.cedar_id)], expected[str(self.cedar_id)])
        panel = self.staff.get(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/team:0"
        )
        panel_legend = panel.get_json().get("legend") or []
        self.assertEqual(
            [(row.get("id"), row.get("color")) for row in panel_legend],
            [(row.get("id"), row.get("color")) for row in board.get("legend") or []],
        )
        self.assertTrue(any(str(row.get("name") or "").strip() for row in panel_legend))
        self.assertFalse(
            any(str(row.get("name") or "").strip() for row in board.get("legend") or [])
        )
        denied = pine.post(
            "/api/student/board/team:0/ops",
            json={
                "run_key": self.school.live_board_run_key(self.session_id),
                "ops": [self._text_op("nope", "pine-write")],
            },
            headers=self._student_headers(self._visit_token(self.pine_id)),
        )
        self.assertEqual(denied.status_code, 403)

    def test_shown_board_lists_a_teammate_who_drew_and_left(self) -> None:
        """Team 1 keeps both students on the shown legend after one draws and leaves."""
        self._publish("group_shared")
        self.school.append_live_board_ops(
            self.session_id,
            "team:0",
            owner=str(self.cedar_id),
            ops=[self._text_op("kept", "cedar-kept")],
        )
        self.school.mark_live_session_attendee_left(
            self.session_id, student_id=self.cedar_id
        )
        panel = self.staff.get(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/team:0"
        )
        self.assertEqual(panel.status_code, 200, panel.get_data(as_text=True))
        shown = self.staff.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/team:0/show"
        )
        self.assertEqual(shown.status_code, 200, shown.get_data(as_text=True))
        panel_legend = panel.get_json().get("legend") or []
        shown_legend = shown.get_json().get("legend") or []
        self.assertEqual(panel_legend, shown_legend)
        ids = [row["id"] for row in shown_legend]
        self.assertIn(str(self.maple_id), ids)
        self.assertIn(str(self.cedar_id), ids)
        self.assertEqual(len({row["color"] for row in shown_legend}), len(shown_legend))
        pine = self.app.test_client()
        seen = pine.get(
            "/api/student/board/mine?since=0&teacher_since=0",
            headers=self._student_headers(self._visit_token(self.pine_id)),
        )
        self.assertEqual(seen.status_code, 200, seen.get_data(as_text=True))
        board = (seen.get_json() or {}).get("class_board") or {}
        self.assertEqual(
            [(row.get("id"), row.get("color")) for row in board.get("legend") or []],
            [(row.get("id"), row.get("color")) for row in shown_legend],
        )
        self.assertFalse(
            any(str(row.get("name") or "").strip() for row in board.get("legend") or [])
        )
        self.assertTrue(any(str(row.get("name") or "").strip() for row in shown_legend))

    def test_teacher_wrote_and_clear_keeps_live_ink(self) -> None:
        """The chip follows teacher rows. Clear leaves that ink in place."""
        self._publish("individual")
        key = f"solo:{self.maple_id}"
        before = self.staff.get(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery"
        )
        before_body = before.get_json()
        self.assertIn(
            "teacher_wrote",
            [chip["id"] for chip in before_body["filters"]],
        )
        self.assertIn(
            "Teacher wrote",
            [chip["label"] for chip in before_body["filters"]],
        )
        tile = next(row for row in before_body["tiles"] if row["board_key"] == key)
        self.assertFalse(tile["teacher_wrote"])
        self.assertNotIn("status", tile)
        self.school.append_live_board_ops(
            self.session_id,
            key,
            owner=str(self.maple_id),
            ops=[self._text_op("student sketch", "stu-sketch")],
        )
        posted = self.staff.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/{key}/feedback",
            json={"ops": [self._text_op("teacher note", "t-clear")]},
        )
        self.assertEqual(posted.status_code, 200, posted.get_data(as_text=True))
        after = self.staff.get(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery"
        )
        marked = next(row for row in after.get_json()["tiles"] if row["board_key"] == key)
        self.assertTrue(marked["teacher_wrote"])
        self.assertEqual(marked["status"], "Teacher wrote")
        cleared = self.staff.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/{key}/clear"
        )
        self.assertEqual(cleared.status_code, 200, cleared.get_data(as_text=True))
        student = self.maple.get(
            f"/api/student/board/{key}",
            headers=self._student_headers(self.maple_token),
        )
        dumped = json.dumps(student.get_json())
        self.assertIn("teacher note", dumped)
        self.assertNotIn("student sketch", dumped)

    def test_student_stroke_colour_matches_the_legend(self) -> None:
        """A pen stroke stores the same colour the legend shows for that id."""
        self._publish("individual")
        key = f"solo:{self.maple_id}"
        run_key = self.school.live_board_run_key(self.session_id)
        posted = self.maple.post(
            "/api/student/canvas-presence",
            json={
                "x": 0.22,
                "y": 0.41,
                "points": [[0.22, 0.41], [0.34, 0.52]],
                "stroke_id": "maple-colour",
                "ended": True,
                "run_key": run_key,
            },
            headers=self._student_headers(self.maple_token),
        )
        self.assertEqual(posted.status_code, 200, posted.get_data(as_text=True))
        gallery = self.staff.get(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery"
        )
        tile = next(row for row in gallery.get_json()["tiles"] if row["board_key"] == key)
        expected = student_ink_color(self.maple_id)
        found: list[str] = []
        view = tile.get("canvas_view") or {}
        for stroke in view.get("strokes") or []:
            if isinstance(stroke, dict) and stroke.get("color"):
                found.append(str(stroke["color"]))
        for op in tile.get("ops") or []:
            if isinstance(op, dict) and op.get("color"):
                found.append(str(op["color"]))
        self.assertIn(expected, found)
        panel = self.staff.get(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/{key}"
        )
        legend = panel.get_json()["legend"]
        self.assertEqual(legend[0]["color"], expected)
        page = (LMS_DIR / "templates" / "staff" / "course.html").read_text()
        self.assertNotIn("wb-live-send", page)
        self.assertNotIn("Send feedback", page)
        self.assertIn('id="wb-live-show"', page)
        self.assertIn('id="wb-live-legend"', page)
        home = (LMS_DIR / "templates" / "student" / "home.html").read_text()
        self.assertIn('id="class-board-legend"', home)

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

    def _stroke(self, stroke_id: str, *, points: list[list[float]] | None = None) -> dict[str, object]:
        """One finished pen stroke with no name and no secret fields.

        Args:
            stroke_id: Client stroke id.
            points: 0–1 samples. A short segment is used when omitted.
        """
        return {
            "type": "stroke_add",
            "id": stroke_id,
            "points": points or [[0.2, 0.3], [0.4, 0.5]],
            "color": "#2563eb",
        }

    def _whiteboard_id(self) -> int:
        """Lifecycle id of the session whiteboard."""
        self.school.ensure_live_session_items(self.session_id)
        whiteboard = next(
            row
            for row in self.school.list_live_session_items(self.session_id)
            if str(row.get("kind") or "") == "whiteboard"
        )
        return int(whiteboard["id"])

    def test_clear_team_board_after_teacher_undo(self) -> None:
        """Clear on a team board stays 200 when the teacher has undone a stroke.

        Replaying that stroke_remove after a purge used to raise
        'stroke is not on this board' and the route returned 500.
        Student ink goes. The undone teacher stroke stays gone. The
        teacher stroke that was still on the board stays.
        """
        self._publish("group_shared")
        key = "team:0"
        self.school.append_live_board_ops(
            self.session_id,
            key,
            owner=str(self.maple_id),
            ops=[self._stroke("student-pen")],
        )
        self.school.append_live_board_ops(
            self.session_id,
            key,
            owner="teacher",
            ops=[self._stroke("kept-pen"), self._stroke("undone-pen")],
        )
        self.school.append_live_board_ops(
            self.session_id,
            key,
            owner="teacher",
            ops=[{"type": "stroke_remove", "id": "undone-pen"}],
        )
        cleared = self.staff.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/{key}/clear"
        )
        self.assertEqual(cleared.status_code, 200, cleared.get_data(as_text=True))
        body = cleared.get_json()
        self.assertTrue(body.get("cleared"))
        self.assertTrue(body.get("snapshot"))
        ids = {
            str(stroke.get("id") or "")
            for stroke in (body.get("canvas_view") or {}).get("strokes") or []
        }
        self.assertEqual(ids, {"kept-pen"})
        student = self.maple.get(
            f"/api/student/board/{key}?since=0",
            headers=self._student_headers(self.maple_token),
        )
        self.assertEqual(student.status_code, 200, student.get_data(as_text=True))
        dumped = json.dumps(student.get_json())
        self.assertIn("kept-pen", dumped)
        self.assertNotIn("student-pen", dumped)
        self.assertNotIn("undone-pen", dumped)

    def test_fresh_reopen_gallery_shows_empty_boards(self) -> None:
        """A stale gallery cursor after Fresh board snapshots to empty ink.

        The next read, from that new head, carries only ink drawn after
        the reopen.
        """
        from urllib.parse import quote

        self._publish("group_shared")
        key = "team:0"
        self.school.append_live_board_ops(
            self.session_id,
            key,
            owner=str(self.maple_id),
            ops=[self._stroke("old-ink")],
        )
        before = self.staff.get(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery"
        )
        self.assertEqual(before.status_code, 200, before.get_data(as_text=True))
        tile = next(row for row in before.get_json()["tiles"] if row["board_key"] == key)
        seq = int(tile["board_seq"])
        self.assertGreater(seq, 0)
        self.assertIn("old-ink", json.dumps(tile))
        wb_id = self._whiteboard_id()
        closed = self.staff.post(
            f"/api/live-sessions/{self.session_id}/items/{wb_id}/close",
            json={},
        )
        self.assertEqual(closed.status_code, 200, closed.get_data(as_text=True))
        reopened = self.staff.post(
            f"/api/live-sessions/{self.session_id}/items/{wb_id}/reopen",
            json={"start": "fresh"},
        )
        self.assertEqual(reopened.status_code, 200, reopened.get_data(as_text=True))
        cursors = quote(json.dumps({key: seq}))
        stale = self.staff.get(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery?cursors={cursors}"
        )
        self.assertEqual(stale.status_code, 200, stale.get_data(as_text=True))
        fresh_tile = next(
            row for row in stale.get_json()["tiles"] if row["board_key"] == key
        )
        self.assertTrue(fresh_tile["snapshot"])
        self.assertEqual((fresh_tile.get("canvas_view") or {}).get("strokes") or [], [])
        self.assertNotIn("old-ink", json.dumps(fresh_tile.get("ops") or []))
        self.assertNotEqual(stale.get_json().get("run_key"), before.get_json().get("run_key"))
        self.school.append_live_board_ops(
            self.session_id,
            key,
            owner=str(self.maple_id),
            ops=[self._stroke("fresh-ink")],
        )
        head = int(fresh_tile.get("board_seq") or 0)
        follow_cursors = quote(json.dumps({key: head}))
        follow = self.staff.get(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery?cursors={follow_cursors}"
        )
        self.assertEqual(follow.status_code, 200, follow.get_data(as_text=True))
        follow_dump = json.dumps(follow.get_json())
        self.assertIn("fresh-ink", follow_dump)
        self.assertNotIn("old-ink", follow_dump)

    def test_show_to_class_toggles_off(self) -> None:
        """Show to class projects a board, and the same call stops it.

        Students' projected view is cleared when the projection stops.
        Showing a second board switches rather than stacking.
        """
        self._publish("group_shared")
        first = self.staff.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/team:0/show"
        )
        self.assertEqual(first.status_code, 200, first.get_data(as_text=True))
        self.assertTrue(first.get_json().get("projected"))
        pine = self.app.test_client()
        pine_token = self._visit_token(self.pine_id)
        seen = pine.get(
            "/api/student/board/mine?since=0&teacher_since=0",
            headers=self._student_headers(pine_token),
        )
        self.assertEqual(seen.status_code, 200, seen.get_data(as_text=True))
        self.assertEqual((seen.get_json().get("class_board") or {}).get("board_key"), "team:0")
        switched = self.staff.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/team:1/show"
        )
        self.assertEqual(switched.status_code, 200, switched.get_data(as_text=True))
        self.assertTrue(switched.get_json().get("projected"))
        self.assertEqual(switched.get_json().get("board_key"), "team:1")
        stopped = self.staff.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/team:1/show"
        )
        self.assertEqual(stopped.status_code, 200, stopped.get_data(as_text=True))
        self.assertFalse(stopped.get_json().get("projected"))
        gallery = self.staff.get(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery"
        )
        self.assertEqual(gallery.get_json().get("projected_board") or "", "")
        after = pine.get(
            "/api/student/board/mine?since=0&teacher_since=0",
            headers=self._student_headers(pine_token),
        )
        self.assertEqual(after.status_code, 200, after.get_data(as_text=True))
        self.assertIsNone(after.get_json().get("class_board"))
        page = (LMS_DIR / "templates" / "staff" / "course.html").read_text()
        self.assertIn('class="live-canvas-stage wb-live-panel-stage"', page)
        script = (LMS_DIR / "static" / "formative_live_board.js").read_text()
        self.assertIn("Stop showing", script)
        self.assertIn("applyDelta", script)

    def test_teacher_stroke_reaches_the_student_board_feed(self) -> None:
        """A teacher pen stroke on a team board is in that team's student feed.

        Another team's student cannot read the board. The stroke is a
        normal board op, not a hidden feedback layer.
        """
        self._publish("group_shared")
        key = "team:0"
        posted = self.staff.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/{key}/feedback",
            json={"ops": [self._stroke("teacher-pen", points=[[0.15, 0.25], [0.45, 0.55]])]},
        )
        self.assertEqual(posted.status_code, 200, posted.get_data(as_text=True))
        seen = self.maple.get(
            f"/api/student/board/{key}?since=0",
            headers=self._student_headers(self.maple_token),
        )
        self.assertEqual(seen.status_code, 200, seen.get_data(as_text=True))
        body = seen.get_json()
        ops = [
            op
            for op in (body.get("ops") or [])
            if isinstance(op, dict) and op.get("id") == "teacher-pen"
        ]
        view = [
            stroke
            for stroke in ((body.get("canvas_view") or {}).get("strokes") or [])
            if isinstance(stroke, dict) and stroke.get("id") == "teacher-pen"
        ]
        found = ops or view
        self.assertTrue(found, body)
        self.assertEqual(str(found[0].get("owner") or ""), "teacher")
        self.assertNotEqual(str(found[0].get("layer") or ""), "feedback")
        pine = self.app.test_client()
        denied = pine.get(
            f"/api/student/board/{key}?since=0",
            headers=self._student_headers(self._visit_token(self.pine_id)),
        )
        self.assertEqual(denied.status_code, 403)


    def test_clear_wipes_the_shared_team_copy_and_a_solo_board(self) -> None:
        """Clear deletes the saved board, including one shared team copy.

        A later read from the previous cursor is a snapshot of what
        remains. Both teammates read that same copy. A solo board is
        that student's copy. The tap names the board and not the class.
        """
        from live_news_wire import NewsAudience, event_visible, log_for, reset_ink_taps

        self._publish("group_shared")
        key = "team:0"
        self.school.append_live_board_ops(
            self.session_id,
            key,
            owner=str(self.maple_id),
            ops=[self._stroke("shared-pen")],
        )
        self.school.append_live_board_ops(
            self.session_id,
            key,
            owner=str(self.cedar_id),
            ops=[self._text_op("shared label", "shared-label")],
        )
        maple_before = self.maple.get(
            f"/api/student/board/{key}?since=0",
            headers=self._student_headers(self.maple_token),
        )
        self.assertEqual(maple_before.status_code, 200, maple_before.get_data(as_text=True))
        held = int(maple_before.get_json().get("board_seq") or 0)
        self.assertGreater(held, 0)
        tape = log_for(self.school.data_dir)
        cursor = max((int(row.get("id") or 0) for row in tape.since(self.session_id, 0)), default=0)
        reset_ink_taps()
        seq_before = int(
            self.school.live_session_teacher_state_payload(self.session_id).get("state_seq") or 0
        )
        cleared = self.staff.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/{key}/clear"
        )
        self.assertEqual(cleared.status_code, 200, cleared.get_data(as_text=True))
        self.assertTrue(cleared.get_json().get("cleared"))
        self.assertEqual(
            int(self.school.live_session_teacher_state_payload(self.session_id).get("state_seq") or 0),
            seq_before,
        )
        for client, token in (
            (self.maple, self.maple_token),
            (self.cedar_client, self.cedar_token),
        ):
            seen = client.get(
                f"/api/student/board/{key}?since={held}",
                headers=self._student_headers(token),
            )
            self.assertEqual(seen.status_code, 200, seen.get_data(as_text=True))
            body = seen.get_json()
            self.assertTrue(body.get("snapshot"))
            self.assertLess(int(body.get("board_seq") or 0), held)
            dumped = json.dumps(body.get("canvas_view") or {})
            self.assertNotIn("shared-pen", dumped)
            self.assertNotIn("shared label", dumped)
        fresh = self.maple.get(
            f"/api/student/board/{key}?since=0",
            headers=self._student_headers(self.maple_token),
        )
        self.assertNotIn("shared-pen", json.dumps(fresh.get_json()))
        ink = [
            row
            for row in tape.since(self.session_id, cursor)
            if row.get("type") == "board_ink"
        ]
        self.assertTrue(ink, "clear did not tap the shared board")
        self.assertEqual(ink[-1].get("board_key"), key)
        self.assertNotIn("name", ink[-1])
        mine = NewsAudience(
            "student",
            student_id=self.maple_id,
            read_keys=list(self.school.student_board_access(self.session_id, self.maple_id)["read"]),
        )
        other = NewsAudience(
            "student",
            student_id=self.pine_id,
            read_keys=list(self.school.student_board_access(self.session_id, self.pine_id)["read"]),
        )
        self.assertTrue(event_visible(ink[-1], mine))
        self.assertFalse(event_visible(ink[-1], other))

        self._publish("individual")
        solo = f"solo:{self.maple_id}"
        self.school.append_live_board_ops(
            self.session_id,
            solo,
            owner=str(self.maple_id),
            ops=[self._stroke("solo-pen")],
        )
        solo_before = self.maple.get(
            f"/api/student/board/{solo}?since=0",
            headers=self._student_headers(self.maple_token),
        )
        solo_held = int(solo_before.get_json().get("board_seq") or 0)
        self.assertGreater(solo_held, 0)
        reset_ink_taps()
        solo_clear = self.staff.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/{solo}/clear"
        )
        self.assertEqual(solo_clear.status_code, 200, solo_clear.get_data(as_text=True))
        solo_after = self.maple.get(
            f"/api/student/board/{solo}?since={solo_held}",
            headers=self._student_headers(self.maple_token),
        )
        self.assertTrue(solo_after.get_json().get("snapshot"))
        self.assertNotIn("solo-pen", json.dumps(solo_after.get_json().get("canvas_view") or {}))

    def test_show_hides_names_on_the_student_board_and_skips_state_seq(self) -> None:
        """Student shown-board payload has no name. Teacher and projector do.

        Show does not bump ``state_seq``. The postcard is the board key.
        """
        from live_news_wire import NewsAudience, event_visible, log_for, reset_logs

        self._publish("group_shared")
        seq_before = int(
            self.school.live_session_teacher_state_payload(self.session_id).get("state_seq") or 0
        )
        tape = log_for(self.school.data_dir)
        cursor = max((int(row.get("id") or 0) for row in tape.since(self.session_id, 0)), default=0)
        shown = self.staff.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/team:0/show"
        )
        self.assertEqual(shown.status_code, 200, shown.get_data(as_text=True))
        teacher = shown.get_json()
        self.assertTrue(any(str(row.get("name") or "").strip() for row in teacher.get("legend") or []))
        self.assertEqual(
            int(self.school.live_session_teacher_state_payload(self.session_id).get("state_seq") or 0),
            seq_before,
        )
        panel = self.staff.get(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/team:0"
        )
        self.assertTrue(
            any(str(row.get("name") or "").strip() for row in panel.get_json().get("legend") or [])
        )
        seen = self.maple.get(
            "/api/student/board/mine?since=0&teacher_since=0",
            headers=self._student_headers(self.maple_token),
        )
        self.assertEqual(seen.status_code, 200, seen.get_data(as_text=True))
        board = (seen.get_json() or {}).get("class_board") or {}
        self.assertEqual(board.get("board_key"), "team:0")
        dumped = json.dumps(board)
        self.assertNotIn('"name"', dumped)
        for row in teacher.get("legend") or []:
            label = str(row.get("name") or "").strip()
            if label:
                self.assertNotIn(label, dumped)
        news = [
            row
            for row in tape.since(self.session_id, cursor)
            if row.get("type") == "board_show"
        ]
        self.assertTrue(news)
        self.assertEqual(set(news[-1]) - {"id"}, {"type", "board_key"})
        self.assertEqual(news[-1].get("board_key"), "team:0")
        self.assertFalse(
            any(row.get("type") == "state_seq" for row in tape.since(self.session_id, cursor))
        )
        student = NewsAudience("student", student_id=self.pine_id, read_keys=["solo:1"])
        staff = NewsAudience("staff")
        self.assertTrue(event_visible(news[-1], student))
        self.assertFalse(event_visible(news[-1], staff))
        reset_logs()

    def _nested_strings(self, value: object) -> list[str]:
        """Return every string in a JSON tree, keys and values included.

        Args:
            value: Decoded JSON object, list, or scalar.
        """
        found: list[str] = []

        def walk(node: object) -> None:
            """Collect strings from one node."""
            if isinstance(node, str):
                found.append(node)
                return
            if isinstance(node, dict):
                for key, item in node.items():
                    walk(str(key))
                    walk(item)
                return
            if isinstance(node, list):
                for item in node:
                    walk(item)

        walk(value)
        return found

    def test_student_shown_board_has_no_name_in_any_field(self) -> None:
        """A shown board sent to a student contains no roster name.

        The scan covers labels, member lists, titles, and nested objects,
        not only keys named name or codename. The teacher Show body and
        the projector panel still carry those names.
        """
        self._publish("group_shared")
        key = "team:0"
        self.school.append_live_board_ops(
            self.session_id,
            key,
            owner=str(self.maple_id),
            ops=[
                self._stroke("nested-pen"),
                self._text_op("axis sketch", "nested-label"),
            ],
        )
        shown = self.staff.post(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/{key}/show"
        )
        self.assertEqual(shown.status_code, 200, shown.get_data(as_text=True))
        teacher = shown.get_json()
        panel = self.staff.get(
            f"/api/live-sessions/{self.session_id}/whiteboard-gallery/{key}"
        )
        self.assertEqual(panel.status_code, 200, panel.get_data(as_text=True))
        projector = panel.get_json()
        names = [
            str(row.get("name") or "").strip()
            for row in (teacher.get("legend") or [])
            if str(row.get("name") or "").strip()
        ]
        self.assertGreaterEqual(len(names), 2)
        teacher_dump = json.dumps(teacher)
        projector_dump = json.dumps(projector)
        for label in names:
            self.assertIn(label, teacher_dump)
            self.assertIn(label, projector_dump)
        for student_id, token in (
            (self.maple_id, self.maple_token),
            (self.pine_id, self._visit_token(self.pine_id)),
        ):
            client = self.maple if student_id == self.maple_id else self.app.test_client()
            seen = client.get(
                "/api/student/board/mine?since=0&teacher_since=0",
                headers=self._student_headers(token),
            )
            self.assertEqual(seen.status_code, 200, seen.get_data(as_text=True))
            board = (seen.get_json() or {}).get("class_board") or {}
            self.assertEqual(board.get("board_key"), key)
            self.assertIn("axis sketch", json.dumps(board))
            blob = "\n".join(self._nested_strings(board)).casefold()
            for label in names:
                self.assertNotIn(label.casefold(), blob)

    def test_student_shown_legend_dom_has_no_name(self) -> None:
        """The student shown-board legend paints no name. The teacher legend does."""
        script = r"""
import { legendChipLabels, studentShownMembers, wireFormativeWhiteboard } from './lms/static/whiteboard_formative.js';
import { snapshotReplacesInk } from './lms/static/live_whiteboard.js';

class HTMLElement {}
class HTMLCanvasElement extends HTMLElement {}
globalThis.HTMLElement = HTMLElement;
globalThis.HTMLCanvasElement = HTMLCanvasElement;
globalThis.Element = class Element {};

function makeEl(id) {
  const node = {
    id: id || '',
    hidden: false,
    style: {},
    dataset: {},
    className: '',
    classList: { add() {}, remove() {}, toggle() {} },
    textContent: '',
    children: [],
    parentElement: null,
    append(...kids) { for (const kid of kids) this.children.push(kid); },
    appendChild(child) { child.parentElement = this; this.children.push(child); return child; },
    replaceChildren() { this.children = []; this.textContent = ''; },
    addEventListener() {},
    setAttribute() {},
    querySelector(sel) {
      if (String(sel).includes('class-board-legend')) return this._legend || null;
      return null;
    },
    closest() { return this._root || null; },
    getBoundingClientRect() {
      return { width: 320, height: 180, left: 0, top: 0, right: 320, bottom: 180 };
    },
    getContext() {
      return { setTransform() {}, clearRect() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}, fillRect() {} };
    },
  };
  return node;
}

const label = 'Ink label';
const members = [{ id: '7', name: label, color: '#2563eb' }];
if (legendChipLabels(studentShownMembers(members)).length !== 0) process.exit(2);
if (legendChipLabels(members).join('|') !== label) process.exit(3);
if (!snapshotReplacesInk(1, 8, 8)) process.exit(4);
if (snapshotReplacesInk(9, 2, 2)) process.exit(5);

const canvas = makeEl('canvas');
Object.setPrototypeOf(canvas, HTMLCanvasElement.prototype);
const stage = makeEl('stage');
Object.setPrototypeOf(stage, HTMLElement.prototype);
const legend = makeEl('class-board-legend');
Object.setPrototypeOf(legend, HTMLElement.prototype);
const root = makeEl('canvas-pane');
root._legend = legend;
root.insertBefore = (node) => root.appendChild(node);
canvas._root = root;
stage.parentElement = root;
canvas.parentElement = stage;
globalThis.document = {
  body: makeEl('body'),
  createElement() { return makeEl(); },
  getElementById() { return null; },
  addEventListener() {},
};
document.createTextNode = (text) => ({ textContent: String(text) });
globalThis.window = globalThis;
window.addEventListener = () => {};
window.setTimeout = setTimeout;
window.clearTimeout = clearTimeout;
window.matchMedia = () => ({ matches: false });

function labels(host) {
  const found = [];
  const walk = (node) => {
    if (!node) return;
    if (node.textContent) found.push(String(node.textContent));
    for (const child of node.children || []) walk(child);
  };
  walk(host);
  return found.join(' ');
}

wireFormativeWhiteboard({ setFormative() {}, getInk: () => ({ strokes: [], texts: [] }) }, {
  canvas,
  stage,
  members: studentShownMembers(members),
  ownerId: () => '7',
  alwaysShowNames: false,
  getInk: () => ({ strokes: [], texts: [] }),
});
if (labels(legend).includes(label)) {
  console.error('student legend ' + labels(legend));
  process.exit(6);
}
legend.replaceChildren();
wireFormativeWhiteboard({ setFormative() {}, getInk: () => ({ strokes: [], texts: [] }) }, {
  canvas,
  stage,
  members,
  ownerId: () => 'teacher',
  isTeacher: true,
  alwaysShowNames: true,
  getInk: () => ({ strokes: [], texts: [] }),
});
if (!labels(legend).includes(label)) {
  console.error('teacher legend ' + labels(legend));
  process.exit(7);
}
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)


if __name__ == "__main__":
    unittest.main()

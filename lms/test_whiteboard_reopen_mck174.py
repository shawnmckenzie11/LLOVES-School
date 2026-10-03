#!/usr/bin/env python3
"""MCK-174: reopen a closed whiteboard with Last board or Fresh board."""

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
from live_news_wire import (  # noqa: E402
    LiveNewsLog,
    NewsAudience,
    event_visible,
    news_db_path,
)

# Wonder copy, word for word (alc-three-fixes-copy-wonder-v1.md §MCK-174).
COPY = {
    "wb.reopen": "Reopen board",
    "wb.reopen.last": "Last board",
    "wb.reopen.last.help": "Brings back all the ink from when you closed it.",
    "wb.reopen.fresh": "Fresh board",
    "wb.reopen.fresh.help": "A blank board. The last one is kept until class ends.",
    "wb.reopen.toast.last": "Board reopened with the last ink.",
    "wb.reopen.toast.fresh": "Fresh board is open.",
    "wb.student.last": "The board is back. Keep going.",
    "wb.student.fresh": "Fresh board. Start again here.",
}


class WhiteboardReopenTests(unittest.TestCase):
    """Close, then Reopen with Last board or Fresh board."""

    def setUp(self) -> None:
        """Staff session with one live class and a logged-in teacher."""
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
        """Close the school db and the temp directory."""
        self.school.close()
        self.tmp.cleanup()

    # -- helpers ---------------------------------------------------------

    def _whiteboard(self) -> dict:
        """Return the seeded whiteboard lifecycle row."""
        self.school.ensure_live_session_items(self.session_id)
        for row in self.school.list_live_session_items(self.session_id):
            if str(row.get("kind") or "") == "whiteboard":
                return row
        self.fail("whiteboard lifecycle row missing")
        return {}

    def _join_two_groups(self, publish_mode: str) -> dict[str, str]:
        """Join Aspen + Birch (group 1) and Cedar (group 2), then publish.

        Args:
            publish_mode: ``group_shared`` or ``individual``.

        Returns:
            Visit tokens keyed by codename.
        """
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
        published = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{self._wb_id()}/publish",
            json={"publish_mode": publish_mode},
        )
        self.assertEqual(published.status_code, 200, published.get_json())
        return tokens

    def _wb_id(self) -> int:
        """Lifecycle id of the whiteboard row."""
        return int(self._whiteboard()["id"])

    def _student(self, token: str):
        """Test client bound to one student visit token."""
        client = self.app.test_client()
        client.environ_base["HTTP_X_STUDENT_VISIT_TOKEN"] = token
        return client

    def _draw(self, client, stroke_id: str, *, run_key: str | None = None):
        """Post one finished student stroke."""
        return client.post(
            "/api/student/canvas-presence",
            json={
                "run_key": run_key
                if run_key is not None
                else self.school.live_board_run_key(self.session_id),
                "x": 0.2,
                "y": 0.3,
                "points": [[0.2, 0.3], [0.25, 0.35]],
                "stroke_id": stroke_id,
                "ended": True,
            },
        )

    def _teacher_draw(self, stroke_id: str):
        """Post one finished teacher stroke."""
        drawn = self.client.post(
            f"/api/live-sessions/{self.session_id}/canvas-presence",
            json={
                "run_key": self.school.live_board_run_key(self.session_id),
                "x": 0.5,
                "y": 0.5,
                "points": [[0.5, 0.5], [0.55, 0.5]],
                "stroke_id": stroke_id,
                "ended": True,
            },
        )
        self.assertEqual(drawn.status_code, 200, drawn.get_json())

    def _close(self):
        closed = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{self._wb_id()}/close",
            json={},
        )
        self.assertEqual(closed.status_code, 200, closed.get_json())
        self.assertEqual(closed.get_json()["item"]["status"], "closed")
        return closed

    def _reopen(self, start: str):
        return self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{self._wb_id()}/reopen",
            json={"start": start},
        )

    def _mine(self, client) -> dict:
        got = client.get("/api/student/board/mine?since=0&teacher_since=0")
        self.assertEqual(got.status_code, 200, got.get_json())
        return got.get_json()

    @staticmethod
    def _ink(reply: dict) -> set[str]:
        """Stroke ids in a board read, from ops or a snapshot view."""
        found: set[str] = set()
        for key in ("ops", "teacher_ops", "ops_since"):
            for op in reply.get(key) or []:
                stroke = op.get("stroke_id") or op.get("id")
                if stroke:
                    found.add(str(stroke))
        view = reply.get("canvas_view") or {}
        for stroke in view.get("strokes") or []:
            found.add(str(stroke.get("id") or ""))
        found.discard("")
        return found

    def _op_count(self, run_key: str) -> int:
        row = self.school.boards.conn.execute(
            "SELECT COUNT(*) FROM board_ops WHERE run_key = ?",
            (run_key,),
        ).fetchone()
        return int(row[0])

    def _base_key(self) -> str:
        row = self.school.get_live_session(self.session_id)
        return str(row["run_key"])

    # -- tests -----------------------------------------------------------

    def test_reopen_last_brings_every_board_back(self) -> None:
        """Last board: group ink and teacher ink return; run key is unchanged."""
        tokens = self._join_two_groups("group_shared")
        aspen = self._student(tokens["Aspen"])
        cedar = self._student(tokens["Cedar"])
        self.assertEqual(self._draw(aspen, "aspen-ink").status_code, 200)
        self.assertEqual(self._draw(cedar, "cedar-ink").status_code, 200)
        self._teacher_draw("teacher-ink")
        key_before = self.school.live_board_run_key(self.session_id)
        self._close()
        teacher = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertEqual(teacher["student_view"]["canvas"], "none")

        reopened = self._reopen("last")
        self.assertEqual(reopened.status_code, 200, reopened.get_json())
        body = reopened.get_json()
        self.assertEqual(body["item"]["status"], "active")
        self.assertIsNone(body["item"]["closed_at"])
        self.assertEqual(body["item"]["publish_mode"], "group_shared")
        self.assertEqual(body["item"]["item"]["reopen"], {"start": "last", "n": 1})
        self.assertEqual(body["run_key"], key_before)
        self.assertEqual(self.school.live_board_run_key(self.session_id), key_before)
        teacher = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertEqual(teacher["student_view"]["canvas"], "team")

        aspen_ink = self._ink(self._mine(aspen))
        self.assertIn("aspen-ink", aspen_ink)
        self.assertIn("teacher-ink", aspen_ink)
        self.assertNotIn("cedar-ink", aspen_ink)
        cedar_ink = self._ink(self._mine(cedar))
        self.assertIn("cedar-ink", cedar_ink)
        self.assertIn("teacher-ink", cedar_ink)
        staff = self.client.get(
            f"/api/live-sessions/{self.session_id}/board/teacher?since=0"
        )
        self.assertEqual(staff.status_code, 200, staff.get_json())
        self.assertIn("teacher-ink", self._ink(staff.get_json()))
        # Students keep drawing on the same board.
        self.assertEqual(self._draw(aspen, "aspen-after").status_code, 200)

    def test_reopen_fresh_starts_an_empty_generation_and_keeps_old_ops(self) -> None:
        """Fresh board: new generation is blank, earlier ops stay in the DB."""
        tokens = self._join_two_groups("group_shared")
        aspen = self._student(tokens["Aspen"])
        self.assertEqual(self._draw(aspen, "gen0-ink").status_code, 200)
        self._teacher_draw("gen0-teacher")
        base = self._base_key()
        self.assertEqual(self.school.live_board_run_key(self.session_id), base)
        kept = self._op_count(base)
        self.assertGreater(kept, 0)
        self._close()

        reopened = self._reopen("fresh")
        self.assertEqual(reopened.status_code, 200, reopened.get_json())
        body = reopened.get_json()
        self.assertEqual(body["item"]["status"], "active")
        self.assertEqual(body["item"]["item"]["reopen"], {"start": "fresh", "n": 1})
        fresh_key = f"{base}~g1"
        self.assertEqual(body["run_key"], fresh_key)
        self.assertEqual(self.school.live_board_run_key(self.session_id), fresh_key)
        self.assertEqual(self._base_key(), base, "base run key never changes")

        mine = self._mine(aspen)
        self.assertEqual(mine["run_key"], fresh_key)
        self.assertEqual(self._ink(mine), set())
        staff = self.client.get(
            f"/api/live-sessions/{self.session_id}/board/teacher?since=0"
        )
        self.assertEqual(self._ink(staff.get_json()), set())
        view = self.school.live_session_canvas_view(
            self.session_id, student_id=int(
                self.school.game.find_student_by_codename(self.class_id, "Aspen")["id"]
            )
        )
        self.assertEqual(view["strokes"], [])
        # Hidden, never deleted.
        self.assertEqual(self._op_count(base), kept)
        self.assertEqual(
            self.school.live_board_generation_keys(self.session_id),
            [base, fresh_key],
        )
        # A tab still on the old generation is told its run is stale.
        stale = self._draw(aspen, "late-old-ink", run_key=base)
        self.assertEqual(stale.status_code, 409, stale.get_json())
        self.assertTrue(stale.get_json().get("stale_run"))
        self.assertEqual(self._op_count(base), kept)
        # New ink lands on the fresh generation.
        self.assertEqual(self._draw(aspen, "gen1-ink").status_code, 200)
        self.assertGreater(self._op_count(fresh_key), 0)

        # Close, then Last board shows the fresh generation's ink only.
        self._close()
        again = self._reopen("last")
        self.assertEqual(again.status_code, 200, again.get_json())
        self.assertEqual(again.get_json()["item"]["item"]["reopen"], {"start": "last", "n": 2})
        ink = self._ink(self._mine(aspen))
        self.assertIn("gen1-ink", ink)
        self.assertNotIn("gen0-ink", ink)
        self.assertNotIn("gen0-teacher", ink)

    def test_fresh_is_unavailable_on_an_individual_whiteboard(self) -> None:
        """Individual pen ink is client-only until S3, so Fresh is refused."""
        tokens = self._join_two_groups("individual")
        self.assertTrue(tokens)
        base = self.school.live_board_run_key(self.session_id)
        self._close()
        refused = self._reopen("fresh")
        self.assertEqual(refused.status_code, 400, refused.get_json())
        self.assertIn("Individual", refused.get_json()["error"])
        row = self._whiteboard()
        self.assertEqual(row["status"], "closed")
        self.assertEqual(self.school.live_board_run_key(self.session_id), base)
        last = self._reopen("last")
        self.assertEqual(last.status_code, 200, last.get_json())
        self.assertEqual(last.get_json()["item"]["publish_mode"], "individual")
        teacher = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertEqual(teacher["student_view"]["canvas"], "student")
        # The teacher popover hides the Fresh row for Individual boards.
        staff_js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("reopenFreshAllowed", staff_js)
        self.assertIn('publish_mode || "") === "group_shared"', staff_js)

    def test_only_a_closed_whiteboard_reopens(self) -> None:
        """Active boards, unknown starts, and ended sessions are refused."""
        self._join_two_groups("group_shared")
        active = self._reopen("last")
        self.assertEqual(active.status_code, 400, active.get_json())
        self.assertEqual(
            active.get_json()["error"], "Only a closed whiteboard can be reopened."
        )
        self._close()
        bogus = self._reopen("sideways")
        self.assertEqual(bogus.status_code, 400, bogus.get_json())
        first = self._reopen("last")
        self.assertEqual(first.status_code, 200, first.get_json())
        second = self._reopen("last")
        self.assertEqual(second.status_code, 400, "a second tab gets 400")
        self._close()
        self.school.end_live_class_session(self.session_id)
        with self.assertRaises(ValueError):
            self.school.reopen_live_whiteboard(self.session_id, self._wb_id_any())

    def _wb_id_any(self) -> int:
        """Whiteboard row id without re-seeding an ended session."""
        for row in self.school.list_live_session_items(self.session_id):
            if str(row.get("kind") or "") == "whiteboard":
                return int(row["id"])
        self.fail("whiteboard lifecycle row missing")
        return 0

    def test_end_live_class_purges_every_generation(self) -> None:
        """End still deletes all ink: the base run and every ~g generation."""
        tokens = self._join_two_groups("group_shared")
        aspen = self._student(tokens["Aspen"])
        base = self._base_key()
        self.assertEqual(self._draw(aspen, "g0").status_code, 200)
        for start, stroke in (("fresh", "g1"), ("fresh", "g2")):
            self._close()
            self.assertEqual(self._reopen(start).status_code, 200)
            self.assertEqual(self._draw(aspen, stroke).status_code, 200)
        keys = [base, f"{base}~g1", f"{base}~g2"]
        self.assertEqual(self.school.live_board_generation_keys(self.session_id), keys)
        for key in keys:
            self.assertGreater(self._op_count(key), 0, key)
        ended = self.school.end_live_class_session(self.session_id)
        self.assertEqual(ended["status"], "ended")
        for key in keys:
            self.assertEqual(self._op_count(key), 0, key)
            self.assertEqual(self.school.boards.load_keys(key, None), [], key)
        leftover = self.school.boards.conn.execute(
            "SELECT COUNT(*) FROM board_ops WHERE run_key LIKE ?",
            (f"{base}%",),
        ).fetchone()
        self.assertEqual(int(leftover[0]), 0)

    def test_student_cue_rides_the_existing_stream(self) -> None:
        """Reopen sends the student-visible state_seq postcard and marker.

        The student tab picks the cue line from ``live_items[].reopen``
        on its next /state, with no reload.
        """
        tokens = self._join_two_groups("group_shared")
        aspen = self._student(tokens["Aspen"])
        aspen_id = int(
            self.school.game.find_student_by_codename(self.class_id, "Aspen")["id"]
        )
        self._close()
        before = aspen.get("/api/student/state").get_json()
        seq_before = int(before["teacher_state"]["state_seq"])
        tape = LiveNewsLog(news_db_path(self.school.data_dir))
        last_id = max([int(row["id"]) for row in tape.since(self.session_id, 0)] or [0])

        reopened = self._reopen("last")
        self.assertEqual(reopened.status_code, 200, reopened.get_json())
        news = tape.since(self.session_id, last_id)
        audience = NewsAudience("student", student_id=aspen_id)
        seen = [row for row in news if event_visible(row, audience)]
        self.assertTrue(
            any(row.get("type") == "state_seq" for row in seen), news
        )
        self.assertTrue(
            any(row.get("type") == "flag_work" and row.get("kind") == "reopen" for row in news)
        )

        after = aspen.get("/api/student/state").get_json()
        self.assertGreater(int(after["teacher_state"]["state_seq"]), seq_before)
        board = next(
            row for row in after["live_items"]
            if str((row.get("content") or {}).get("item_type") or row.get("kind") or "")
            == "whiteboard"
            or row.get("reopen") is not None
        )
        self.assertEqual(board["status"], "active")
        self.assertEqual(board["reopen"], {"start": "last", "n": 1})
        # A deck refresh rewrites item_json; the marker survives it.
        self.school.ensure_live_session_items(self.session_id)
        self.assertEqual(self._whiteboard()["item"]["reopen"], {"start": "last", "n": 1})

        self._close()
        self.assertEqual(self._reopen("fresh").status_code, 200)
        fresh = aspen.get("/api/student/state").get_json()
        board = next(row for row in fresh["live_items"] if row.get("reopen"))
        self.assertEqual(board["reopen"], {"start": "fresh", "n": 2})

        student_js = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn("wbseen:", student_js)
        self.assertIn(COPY["wb.student.last"], student_js)
        self.assertIn(COPY["wb.student.fresh"], student_js)
        wb_js = (LMS_DIR / "static" / "live_whiteboard.js").read_text(encoding="utf-8")
        self.assertIn("board-refresh-cue", wb_js)

    def test_teacher_copy_is_word_for_word(self) -> None:
        """Every Wonder string ships verbatim in the staff bundle."""
        staff_js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        for key in (
            "wb.reopen",
            "wb.reopen.last",
            "wb.reopen.last.help",
            "wb.reopen.fresh",
            "wb.reopen.fresh.help",
            "wb.reopen.toast.last",
            "wb.reopen.toast.fresh",
        ):
            self.assertIn(COPY[key], staff_js, key)
        course = (LMS_DIR / "templates" / "staff" / "course.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('data-surface-reopen="canvas"', course)


if __name__ == "__main__":
    unittest.main()

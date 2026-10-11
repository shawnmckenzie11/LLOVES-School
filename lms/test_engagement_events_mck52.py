#!/usr/bin/env python3
"""MCK-52 P0: engagement event log, draft editors, notes/mood kept across runs."""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

import serve_capacity  # noqa: E402
from app import create_app  # noqa: E402
from engagement_events import MoveThrottle, clean_payload, emits, first_name_only  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self) -> None:
        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
        )
        self.class_id = int(
            self.school.game.create_class(
                year="2026/27", semester="Semester 1", course_code="MCR3U",
                days_preset="M/W/F", time_label="2:00pm",
                codenames=["Ava Smith", "Ben Jones"],
                offering_id=int(offering["id"]), teacher_user_id=int(self.teacher["id"]),
            )["id"]
        )
        rows = self.school.game.conn.execute(
            "SELECT id FROM students WHERE class_id = ? ORDER BY id", (self.class_id,)
        ).fetchall()
        self.students = [int(r[0]) for r in rows]
        self.school.game.conn.execute(
            "UPDATE students SET first_name = 'Ava Marie' WHERE id = ?", (self.students[0],)
        )
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        self.session_id = int(live["id"])

    def tearDown(self) -> None:
        serve_capacity._poll_budget.deadline = None
        logging.disable(logging.NOTSET)
        self.tmp.cleanup()

    def events(self, kind: str | None = None) -> list[dict]:
        rows = self.school.list_engagement_events(self.class_id)
        return [r for r in rows if kind is None or r["kind"] == kind]


class StoreTests(Base):
    """P0.1: store, privacy, poll budget, survives End/Quit."""

    def test_first_name_only_and_no_answer_text(self) -> None:
        ok = self.school.record_engagement_event(
            "group.vote", session_id=self.session_id, student_id=self.students[0],
            item_ref=7, payload={"why": "secret", "text": "x", "changed": True},
        )
        self.assertTrue(ok)
        row = self.events("group.vote")[0]
        self.assertEqual(row["first_name"], "Ava")
        self.assertEqual(row["payload"], {"changed": True})
        self.assertEqual(row["class_id"], self.class_id)

    def test_unknown_kind_is_refused(self) -> None:
        self.assertFalse(self.school.record_engagement_event("grade.set", session_id=self.session_id))

    def test_spent_poll_budget_skips_the_write(self) -> None:
        serve_capacity._poll_budget.deadline = time.monotonic() - 1
        self.assertFalse(
            self.school.record_engagement_event("present.stale", session_id=self.session_id)
        )
        serve_capacity._poll_budget.deadline = None
        self.assertEqual(self.events(), [])

    def test_log_survives_end_and_quit(self) -> None:
        self.school.record_engagement_event(
            "answer.submit", session_id=self.session_id, student_id=self.students[1]
        )
        self.school.cleanup_live_session_response_data(self.session_id)
        self.school.wipe_live_sessions_for_class(self.class_id)
        self.assertEqual(len(self.events("answer.submit")), 1)

    def test_helpers(self) -> None:
        self.assertEqual(first_name_only("  Ava  Marie Smith "), "Ava")
        self.assertEqual(clean_payload({"comment": "hi", "n": 2, "big": "x" * 99}), '{"n":2}')
        throttle = MoveThrottle(10)
        self.assertTrue(throttle.allow(("a",), now=0))
        self.assertFalse(throttle.allow(("a",), now=5))
        self.assertTrue(throttle.allow(("a",), now=11))


class EmitTests(Base):
    """P0.2/P0.3: routes emit after their own write succeeds."""

    def test_decorator_skips_on_failure_and_never_raises(self) -> None:
        calls: list[str] = []

        class Fake:
            def record_engagement_event(self, kind: str, **kw: object) -> None:
                calls.append(kind)
                raise RuntimeError("store down")

            @emits("group.submit")
            def ok(self, session_id: int, live_item_id: int, student_id: int) -> str:
                return "done"

            @emits("group.submit")
            def bad(self, session_id: int, live_item_id: int, student_id: int) -> str:
                raise ValueError("nope")

        self.assertEqual(Fake().ok(1, 2, 3), "done")
        with self.assertRaises(ValueError):
            Fake().bad(1, 2, 3)
        self.assertEqual(calls, ["group.submit"])

    def test_note_and_lookfor(self) -> None:
        base = dict(
            live_session_id=self.session_id, class_id=self.class_id,
            observer_user_id=int(self.teacher["id"]), scope="student", note="zebra private",
            student_ids=[self.students[0]],
        )
        self.school.create_observation(**base)
        self.assertEqual(len(self.events("note")), 1)
        self.assertNotIn("zebra", str(self.events()))

    def test_join_and_board_ink(self) -> None:
        self.school.join_live_class_session(self.session_id, self.students[0])
        self.assertEqual([e["student_id"] for e in self.events("present.join")], [self.students[0]])

    def test_artifact_move_is_throttled(self) -> None:
        a = {"session_id": self.session_id, "student_id": self.students[0], "prompt_id": 5}
        from school_db import _ev_artifact_move

        self.assertIsNotNone(_ev_artifact_move(self.school, a, None))
        self.assertIsNone(_ev_artifact_move(self.school, a, None))

    def test_board_extractor_counts_ops_only(self) -> None:
        from school_db import _ev_board

        ev = _ev_board(self.school, {
            "session_id": 1, "board_key": "shared", "owner": str(self.students[1]),
            "ops": [{"type": "stroke_add", "pts": [[1, 2]]}, {"type": "pts_append"}],
        }, None)
        self.assertEqual(ev["kind"], "board.ink_batch")
        self.assertEqual(ev["payload"]["ops"], 2)
        self.assertIsNone(_ev_board(self.school, {"owner": "teacher", "ops": []}, None))

    def test_staff_only_route(self) -> None:
        client = self.app.test_client()
        url = f"/api/classes/{self.class_id}/engagement-events"
        self.assertIn(client.get(url).status_code, (302, 401, 403))
        client.get("/auth/google?portal=staff")
        client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        code = self.school.get_user_by_email("teacher@gmail.com")["verification_code"]
        client.post("/verify-email", data={"code": code})
        self.school.record_engagement_event("lookfor", session_id=self.session_id)
        body = client.get(url).get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["events"][0]["kind"], "lookfor")


class DraftEditorTests(Base):
    """P0.4: group draft edits store the editor."""

    def test_editor_ids_accumulate(self) -> None:
        conn = self.school.conn
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "INSERT INTO live_group_responses (live_item_id, team_id, created_at, updated_at) "
            "VALUES (999, 1, 'x', 'x')"
        )
        conn.execute("PRAGMA foreign_keys = ON")
        self.school._note_group_draft_editor(999, 1, self.students[0])
        self.school._note_group_draft_editor(999, 1, self.students[1])
        self.school._note_group_draft_editor(999, 1, self.students[0])
        got = self.school.group_draft_editors(999, 1)
        self.assertEqual(got["last_editor_student_id"], self.students[0])
        self.assertEqual(got["editor_ids"], self.students[:2])

    def test_both_draft_savers_record_the_editor(self) -> None:
        src = (LMS_DIR / "school_db.py").read_text(encoding="utf-8")
        for name in ("def save_group_mc_draft(", "def _save_group_rank_draft("):
            body = src[src.index(name):]
            body = body[: body.index("\n    def ", 10)]
            if "save_group_mc_draft" in name:
                body = body.split("_save_group_rank_draft(")[-1]
            self.assertIn("_note_group_draft_editor(", body, name)


class KeepAcrossRunsTests(Base):
    """P0.5: Run / Quit keep earlier notes and mood history."""

    def test_notes_kept_after_quit(self) -> None:
        self.school.create_observation(
            live_session_id=self.session_id, class_id=self.class_id,
            observer_user_id=int(self.teacher["id"]), scope="student",
            note="kept note", student_ids=[self.students[0]],
        )
        self.school.wipe_live_sessions_for_class(self.class_id)
        self.school.wipe_live_sessions_for_class(self.class_id)
        kept = self.school.list_observation_history(self.class_id)
        self.assertEqual([k["note"] for k in kept], ["kept note"])
        self.assertIn(str(self.students[0]), kept[0]["student_ids_json"])

    def test_subset_wipe_keeps_other_sessions(self) -> None:
        other = self.session_id
        self.school.wipe_live_sessions_for_class(self.class_id, session_ids=[other + 999])
        self.assertIsNotNone(self.school.get_live_session(other))

    def test_mood_history_survives_next_run(self) -> None:
        game = self.school.game
        mod = sys.modules[type(game).__module__]
        ontario_today = mod.ontario_today
        mood = sorted(mod.STUDENT_MOODS)[0]
        yesterday = (ontario_today() - timedelta(days=1)).isoformat()
        game.set_mood(self.class_id, self.students[0], mood, meeting_date=yesterday)
        game.set_mood(self.class_id, self.students[0], mood)
        game.clear_class_moods_and_characters(self.class_id)
        game.clear_students_live_presence(self.class_id, [self.students[0]])
        days = [r[0] for r in game.conn.execute(
            "SELECT meeting_date FROM student_moods WHERE class_id = ?", (self.class_id,)
        ).fetchall()]
        self.assertEqual(days, [yesterday])


if __name__ == "__main__":
    unittest.main()

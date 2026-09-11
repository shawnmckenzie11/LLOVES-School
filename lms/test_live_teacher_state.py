#!/usr/bin/env python3
"""Thin LiveTeacherState channel: helpers, staff API, student projection."""

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
from live_media import DEFAULT_LIVE_MEDIA_URL  # noqa: E402
from live_teacher_state import (  # noqa: E402
    LAYOUT_PRESETS,
    adjacent_stage,
    apply_teacher_state_update,
    default_teacher_state,
    public_teacher_state,
)


class LiveTeacherStateHelperTests(unittest.TestCase):
    """Pure channel helpers (no Flask)."""

    def test_default_is_join_media_and_ephemeral(self) -> None:
        """Fresh state starts on join with media_full and canvas_ephemeral."""
        state = default_teacher_state()
        self.assertEqual(state["stage"], "join")
        self.assertEqual(state["teams_mode"], "individual")
        self.assertEqual(state["layout_preset"], "media_full")
        self.assertEqual(state["frames"], {"A": "media"})
        self.assertEqual(state["active_tab"], "media")
        self.assertTrue(state["canvas_ephemeral"])
        self.assertIsNone(state["round"])
        self.assertIsNone(state["active_media_ref"])
        self.assertIsNone(state["prompt_ref"])
        self.assertNotIn("url", state)
        self.assertNotIn("stem", state)

    def test_advance_moves_stage_only(self) -> None:
        """Prev/Next walk join → teams → meet → round → play without wrapping."""
        state = default_teacher_state()
        nxt = apply_teacher_state_update(state, advance="next")
        self.assertEqual(nxt["stage"], "teams")
        self.assertEqual(nxt["layout_preset"], "media_full")
        self.assertTrue(nxt["canvas_ephemeral"])
        play = apply_teacher_state_update(nxt, stage="play")
        still = apply_teacher_state_update(play, advance="next")
        self.assertEqual(still["stage"], "play")
        back = apply_teacher_state_update(still, advance="prev")
        self.assertEqual(back["stage"], "round")
        self.assertEqual(adjacent_stage("join", -1), "join")

    def test_preset_and_frames_are_content_ids(self) -> None:
        """Presets fill A/B/C; invalid frames raise."""
        state = apply_teacher_state_update(None, layout_preset="questions_full")
        self.assertEqual(state["frames"], {"A": "questions"})
        three = apply_teacher_state_update(state, layout_preset="three_up")
        self.assertEqual(three["frames"], LAYOUT_PRESETS["three_up"])
        dragged = apply_teacher_state_update(
            three, frames={"A": "questions", "B": "media"}
        )
        self.assertEqual(dragged["frames"]["A"], "questions")
        self.assertEqual(dragged["frames"]["B"], "media")
        with self.assertRaises(ValueError):
            apply_teacher_state_update(None, stage="freeze")
        with self.assertRaises(ValueError):
            apply_teacher_state_update(None, frames={"A": "unknown"})

    def test_canvas_ephemeral_cannot_be_disabled(self) -> None:
        """Persisted blobs cannot flip canvas_ephemeral off."""
        stored = public_teacher_state({"canvas_ephemeral": False, "stage": "play"})
        self.assertTrue(stored["canvas_ephemeral"])
        patched = apply_teacher_state_update(stored, canvas_ephemeral=False)
        self.assertTrue(patched["canvas_ephemeral"])

    def test_refs_do_not_copy_payloads(self) -> None:
        """active_media_ref / prompt_ref are ids, not nested blobs."""
        state = apply_teacher_state_update(
            None,
            active_media_ref=DEFAULT_LIVE_MEDIA_URL,
            prompt_ref="meet-math",
        )
        self.assertEqual(state["active_media_ref"], DEFAULT_LIVE_MEDIA_URL)
        self.assertEqual(state["prompt_ref"], "meet-math")
        self.assertNotIn("params", state)
        self.assertNotIn("payload", state)


class LiveTeacherStateApiTests(unittest.TestCase):
    """Staff API + student projection for the thin channel."""

    def setUp(self) -> None:
        """Isolated app with one teacher, rostered class, and live session."""
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
        self.class_id = created.get_json()["class"]["id"]
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def test_get_defaults_then_advance_and_preset(self) -> None:
        """GET seeds join; POST advance/preset/tab stay thin."""
        got = self.client.get(f"/api/live-sessions/{self.session_id}/teacher-state")
        self.assertEqual(got.status_code, 200, got.get_json())
        state = got.get_json()["teacher_state"]
        self.assertEqual(state["stage"], "join")
        self.assertTrue(state["canvas_ephemeral"])
        nxt = self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"advance": "next"},
        )
        self.assertEqual(nxt.status_code, 200, nxt.get_json())
        self.assertEqual(nxt.get_json()["teacher_state"]["stage"], "teams")
        laid = self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={
                "layout_preset": "questions_full",
                "active_tab": "questions",
                "active_media_ref": DEFAULT_LIVE_MEDIA_URL,
            },
        )
        body = laid.get_json()["teacher_state"]
        self.assertEqual(body["frames"], {"A": "questions"})
        self.assertEqual(body["active_tab"], "questions")
        self.assertEqual(body["active_media_ref"], DEFAULT_LIVE_MEDIA_URL)
        self.assertNotIn("params", body)
        session_state = self.client.get(
            f"/api/live-sessions/{self.session_id}/state"
        ).get_json()
        self.assertEqual(session_state["teacher_state"]["stage"], "teams")
        self.assertIn("active_media", session_state)

    def test_student_state_projects_teacher_state(self) -> None:
        """Student poll includes teacher_state beside active_media."""
        self.school.set_live_session_teacher_state(
            self.session_id, stage="play", layout_preset="three_up"
        )
        self.school.set_live_session_active_media(
            self.session_id, url=DEFAULT_LIVE_MEDIA_URL
        )
        live = self.school.get_live_session(self.session_id)
        student = self.app.test_client()
        student.post(
            "/auth/student-code",
            data={"code": live["session_code"], "name": "Aspen"},
            follow_redirects=False,
        )
        student.post("/student/mood", data={"mood": "good"})
        student.post("/student/character", data={"character": "fox"})
        state = student.get("/api/student/state").get_json()
        self.assertEqual(state.get("teacher_state", {}).get("stage"), "play", state)
        self.assertEqual(state["teacher_state"]["layout_preset"], "three_up")
        self.assertTrue(state["teacher_state"]["canvas_ephemeral"])
        self.assertEqual(state["active_media"]["url"], DEFAULT_LIVE_MEDIA_URL)

    def test_rejects_unknown_stage(self) -> None:
        """Legacy Challenge/Freeze tokens are not stages."""
        bad = self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"stage": "challenge"},
        )
        self.assertEqual(bad.status_code, 400)


if __name__ == "__main__":
    unittest.main()

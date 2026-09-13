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
    MINDS_ON_PROMPT_REF,
    adjacent_stage,
    apply_teacher_state_update,
    bind_meet_student_projection,
    clear_join_prompt_bindings,
    default_teacher_state,
    public_mc_ui,
    public_teacher_state,
    student_should_mount_canvas,
    student_should_mount_media,
)
from minds_on import is_minds_on_payload  # noqa: E402
from teams_spark import (  # noqa: E402
    CUE_TEAMS_SPARK,
    TEAMS_SPARK_PROMPT,
    TEAMS_SPARK_PROMPT_REF,
    is_teams_spark_payload,
)


class LiveTeacherStateHelperTests(unittest.TestCase):
    """Pure channel helpers (no Flask)."""

    def test_default_is_join_questions_and_ephemeral(self) -> None:
        """Fresh state starts on JOIN Questions + Minds-On; students hide media."""
        state = default_teacher_state()
        self.assertEqual(state["stage"], "join")
        self.assertEqual(state["teams_mode"], "individual")
        self.assertEqual(state["layout_preset"], "questions_full")
        self.assertEqual(state["frames"], {"A": "questions"})
        self.assertEqual(state["active_tab"], "questions")
        self.assertEqual(state["prompt_ref"], MINDS_ON_PROMPT_REF)
        self.assertEqual(state["state_seq"], 0)
        self.assertEqual(
            state["student_frames"],
            {"questions": True, "media": False, "canvas": False},
        )
        self.assertEqual(state["unlocks"], {"media": False, "canvas": False})
        self.assertEqual(state["canvas_align"], "student")
        self.assertTrue(state["canvas_ephemeral"])
        self.assertEqual(
            state["round_flags"],
            {"minds_on": False, "action": False, "consolidation": False},
        )
        self.assertIsNone(state["round"])
        self.assertIsNone(state["active_media_ref"])
        self.assertIsNone(state["meet_chain"])
        self.assertIsNone(state["cue_id"])
        self.assertFalse(student_should_mount_media(state))
        self.assertFalse(student_should_mount_canvas(state))
        self.assertNotIn("url", state)
        self.assertNotIn("stem", state)

    def test_advance_moves_stage_and_bumps_seq(self) -> None:
        """Prev/Next walk stages, increment state_seq, and project student frames."""
        state = default_teacher_state()
        nxt = apply_teacher_state_update(state, advance="next")
        self.assertEqual(nxt["stage"], "teams")
        self.assertEqual(nxt["layout_preset"], "questions_full")
        self.assertEqual(nxt["state_seq"], 1)
        self.assertEqual(nxt["prompt_ref"], TEAMS_SPARK_PROMPT_REF)
        self.assertFalse(student_should_mount_media(nxt))
        self.assertTrue(nxt["canvas_ephemeral"])
        play = apply_teacher_state_update(nxt, stage="play")
        self.assertEqual(play["state_seq"], 2)
        self.assertEqual(
            play["student_frames"],
            {"questions": True, "media": False, "canvas": False},
        )
        self.assertEqual(play["unlocks"], {"media": False, "canvas": False})
        self.assertFalse(student_should_mount_media(play))
        self.assertFalse(student_should_mount_canvas(play))
        still = apply_teacher_state_update(play, advance="next")
        self.assertEqual(still["stage"], "play")
        self.assertEqual(still["state_seq"], 3)
        back = apply_teacher_state_update(still, advance="prev")
        self.assertEqual(back["stage"], "round")
        self.assertEqual(back["state_seq"], 4)
        self.assertFalse(student_should_mount_media(back))
        self.assertEqual(adjacent_stage("join", -1), "join")

    def test_join_to_teams_clears_prompt_ref_and_mc_ui(self) -> None:
        """JOIN→TEAMS drops Minds-On reveal and binds the shared spark."""
        revealed = apply_teacher_state_update(
            default_teacher_state(),
            mc_ui={"prompt_ref": MINDS_ON_PROMPT_REF, "reveal": True},
        )
        self.assertEqual(revealed["prompt_ref"], MINDS_ON_PROMPT_REF)
        self.assertTrue(revealed["mc_ui"]["reveal"])
        nxt = apply_teacher_state_update(revealed, advance="next")
        self.assertEqual(nxt["stage"], "teams")
        self.assertEqual(nxt["prompt_ref"], TEAMS_SPARK_PROMPT_REF)
        self.assertEqual(nxt["mc_ui"]["prompt_ref"], TEAMS_SPARK_PROMPT_REF)
        self.assertFalse(nxt["mc_ui"]["reveal"])
        self.assertEqual(nxt["state_seq"], revealed["state_seq"] + 1)
        self.assertTrue(nxt["student_frames"]["questions"])
        self.assertFalse(nxt["student_frames"]["media"])
        self.assertEqual(nxt["layout_preset"], "questions_full")
        self.assertEqual(nxt["frames"], {"A": "questions"})
        self.assertIsNone(nxt.get("cue_id"))
        parked = public_teacher_state({"stage": "teams"})
        self.assertEqual(parked["stage"], "teams")
        self.assertEqual(parked["prompt_ref"], TEAMS_SPARK_PROMPT_REF)
        wiped = clear_join_prompt_bindings(
            {
                "prompt_ref": MINDS_ON_PROMPT_REF,
                "mc_ui": {"prompt_ref": MINDS_ON_PROMPT_REF, "reveal": True},
            }
        )
        self.assertIsNone(wiped["prompt_ref"])
        self.assertNotIn("mc_ui", wiped)

    def test_meet_stage_binds_question_prompt_ref(self) -> None:
        """MEET projects Question-only frames and meet-team prompt_ref."""
        meet = apply_teacher_state_update(None, stage="meet")
        self.assertEqual(meet["stage"], "meet")
        self.assertEqual(meet["prompt_ref"], "meet-team")
        self.assertEqual(
            meet["student_frames"],
            {"questions": True, "media": False, "canvas": False},
        )
        self.assertEqual(meet["active_tab"], "questions")
        self.assertEqual(meet["layout_preset"], "questions_full")
        self.assertFalse(student_should_mount_media(meet))
        leftover = public_teacher_state(
            {
                "stage": "meet",
                "prompt_ref": MINDS_ON_PROMPT_REF,
                "student_frames": {"questions": False, "media": True, "canvas": False},
            }
        )
        self.assertEqual(leftover["prompt_ref"], "meet-team")
        self.assertTrue(leftover["student_frames"]["questions"])
        self.assertFalse(leftover["student_frames"]["media"])
        bound = bind_meet_student_projection(
            {
                "stage": "meet",
                "meet_chain": {
                    "stage": "meet",
                    "chain": ["A", "C", "B"],
                    "index": 1,
                    "a_picks": {},
                    "c_reacts": {},
                    "b_picks": {},
                    "spark_id": "ops-spark-stub-1",
                    "need_prompt_id": "ops-need-stub-1",
                    "rotated": ["notices details", "brings the calm"],
                },
            }
        )
        self.assertEqual(bound["prompt_ref"], "meet-c")
        self.assertTrue(bound["student_frames"]["questions"])

    def test_unlocks_bump_seq_without_flicker_only(self) -> None:
        """PLAY unlock toggles are a teacher commit."""
        play = apply_teacher_state_update(None, stage="play")
        seq = play["state_seq"]
        unlocked = apply_teacher_state_update(play, unlocks={"media": True})
        self.assertEqual(unlocked["state_seq"], seq + 1)
        self.assertTrue(unlocked["unlocks"]["media"])
        self.assertFalse(unlocked["unlocks"]["canvas"])
        self.assertTrue(student_should_mount_media(unlocked))

    def test_unlocks_project_media_on_any_stage(self) -> None:
        """Beat 23: unlock flags mount media/canvas even on JOIN/MEET."""
        join = apply_teacher_state_update(
            None, unlocks={"media": True, "canvas": True}
        )
        self.assertEqual(join["stage"], "join")
        self.assertTrue(join["student_frames"]["media"])
        self.assertTrue(join["student_frames"]["canvas"])
        self.assertTrue(student_should_mount_media(join))
        self.assertTrue(student_should_mount_canvas(join))
        meet = apply_teacher_state_update(join, stage="meet")
        self.assertEqual(meet["stage"], "meet")
        self.assertTrue(meet["unlocks"]["media"])
        self.assertTrue(meet["unlocks"]["canvas"])
        self.assertTrue(student_should_mount_media(meet))
        self.assertTrue(student_should_mount_canvas(meet))
        self.assertTrue(meet["student_frames"]["questions"])
        aligned = apply_teacher_state_update(meet, canvas_align="team")
        self.assertEqual(aligned["canvas_align"], "team")
        frozen = apply_teacher_state_update(aligned, canvas_align="teacher")
        self.assertEqual(frozen["canvas_align"], "teacher")

    def test_uncheck_collapses_media_and_canvas_frames(self) -> None:
        """Beat 29: uncheck removes the student frame; PLAY has no locked pane."""
        play = apply_teacher_state_update(None, stage="play")
        self.assertFalse(student_should_mount_media(play))
        self.assertFalse(student_should_mount_canvas(play))
        opened = apply_teacher_state_update(
            play, unlocks={"media": True, "canvas": True}
        )
        self.assertTrue(opened["student_frames"]["media"])
        self.assertTrue(opened["student_frames"]["canvas"])
        closed = apply_teacher_state_update(
            opened, unlocks={"media": False, "canvas": False}
        )
        self.assertFalse(closed["unlocks"]["media"])
        self.assertFalse(closed["unlocks"]["canvas"])
        self.assertFalse(closed["student_frames"]["media"])
        self.assertFalse(closed["student_frames"]["canvas"])
        self.assertFalse(student_should_mount_media(closed))
        self.assertFalse(student_should_mount_canvas(closed))
        stored = public_teacher_state(
            {
                "stage": "play",
                "student_frames": {"questions": True, "media": True, "canvas": True},
                "unlocks": {"media": False, "canvas": False},
            }
        )
        self.assertFalse(stored["student_frames"]["media"])
        self.assertFalse(stored["student_frames"]["canvas"])

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

    def test_mc_ui_reveal_is_additive_and_bumps_seq(self) -> None:
        """JOIN Reveal shares + closes the poll; Hide unshares but stays closed."""
        from live_teacher_state import bind_join_share_on_reveal, mc_poll_closed

        state = default_teacher_state()
        self.assertNotIn("mc_ui", state)
        self.assertIsNone(public_mc_ui(None))
        shown = apply_teacher_state_update(
            state,
            mc_ui={"prompt_ref": MINDS_ON_PROMPT_REF, "reveal": True},
        )
        self.assertEqual(shown["state_seq"], 1)
        self.assertEqual(shown["mc_ui"]["prompt_ref"], MINDS_ON_PROMPT_REF)
        self.assertTrue(shown["mc_ui"]["reveal"])
        self.assertTrue(shown["mc_ui"]["reveal_to_students"])
        self.assertTrue(shown["mc_ui"]["poll_closed"])
        self.assertTrue(mc_poll_closed(shown))
        hidden = apply_teacher_state_update(
            shown,
            mc_ui={
                "prompt_ref": MINDS_ON_PROMPT_REF,
                "reveal": False,
                "reveal_to_students": False,
            },
        )
        self.assertEqual(hidden["state_seq"], 2)
        self.assertFalse(hidden["mc_ui"]["reveal"])
        self.assertFalse(hidden["mc_ui"]["reveal_to_students"])
        self.assertTrue(hidden["mc_ui"]["poll_closed"])
        self.assertTrue(mc_poll_closed(hidden))
        moved = apply_teacher_state_update(hidden, prompt_ref="C1-CONS-1")
        self.assertNotIn("mc_ui", moved)
        kept = public_teacher_state(
            {
                "prompt_ref": MINDS_ON_PROMPT_REF,
                "mc_ui": {"prompt_ref": "C1-CONS-1", "reveal": True},
            }
        )
        self.assertEqual(kept["mc_ui"]["prompt_ref"], "C1-CONS-1")
        self.assertTrue(kept["mc_ui"]["reveal"])
        self.assertTrue(kept["mc_ui"]["reveal_to_students"])
        play = apply_teacher_state_update(
            {"stage": "play", "prompt_ref": "C1-CONS-1"},
            mc_ui={"prompt_ref": "C1-CONS-1", "reveal": True},
        )
        self.assertTrue(play["mc_ui"]["reveal"])
        self.assertFalse(play["mc_ui"]["reveal_to_students"])
        self.assertFalse(play["mc_ui"]["poll_closed"])
        forced = bind_join_share_on_reveal(
            {
                "stage": "join",
                "mc_ui": {
                    "prompt_ref": MINDS_ON_PROMPT_REF,
                    "reveal": True,
                    "reveal_to_students": False,
                    "poll_closed": False,
                },
            }
        )
        self.assertTrue(forced["mc_ui"]["reveal_to_students"])
        self.assertTrue(forced["mc_ui"]["poll_closed"])
        with self.assertRaises(ValueError):
            apply_teacher_state_update(None, mc_ui="yes")

    def test_round_flags_set_bumps_state_seq(self) -> None:
        """SET-style round_flags write is a teacher commit (state_seq++)."""
        state = default_teacher_state()
        self.assertEqual(state["state_seq"], 0)
        self.assertFalse(state["round_flags"]["action"])
        set_flags = apply_teacher_state_update(
            state,
            round_flags={"minds_on": True, "action": True, "consolidation": False},
        )
        self.assertEqual(set_flags["state_seq"], 1)
        self.assertTrue(set_flags["round_flags"]["minds_on"])
        self.assertTrue(set_flags["round_flags"]["action"])
        self.assertFalse(set_flags["round_flags"]["consolidation"])
        self.assertIsNone(set_flags.get("cue_id"))
        again = apply_teacher_state_update(
            set_flags, round_flags=["minds_on", "consolidation"]
        )
        self.assertEqual(again["state_seq"], 2)
        self.assertTrue(again["round_flags"]["minds_on"])
        self.assertFalse(again["round_flags"]["action"])
        self.assertTrue(again["round_flags"]["consolidation"])
        with self.assertRaises(ValueError):
            apply_teacher_state_update(None, round_flags="minds_on")


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
        self.assertEqual(state["active_tab"], "questions")
        self.assertEqual(state["prompt_ref"], MINDS_ON_PROMPT_REF)
        self.assertEqual(state["state_seq"], 0)
        self.assertFalse(state["student_frames"]["media"])
        self.assertTrue(state["canvas_ephemeral"])
        nxt = self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"advance": "next"},
        )
        self.assertEqual(nxt.status_code, 200, nxt.get_json())
        nxt_state = nxt.get_json()["teacher_state"]
        self.assertEqual(nxt_state["stage"], "teams")
        self.assertEqual(nxt_state["state_seq"], 1)
        self.assertEqual(nxt_state["prompt_ref"], TEAMS_SPARK_PROMPT_REF)
        self.assertEqual(nxt_state.get("cue_id"), CUE_TEAMS_SPARK)
        self.assertFalse(nxt_state["student_frames"]["media"])
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
        self.assertTrue(state["teacher_state"]["student_frames"]["media"])
        self.assertTrue(state["teacher_state"]["student_frames"]["canvas"])
        self.assertFalse(state["teacher_state"]["unlocks"]["media"])
        self.assertGreaterEqual(state["teacher_state"]["state_seq"], 1)
        self.assertEqual(state["active_media"]["url"], DEFAULT_LIVE_MEDIA_URL)

    def test_join_to_teams_clears_teacher_and_student_questions(self) -> None:
        """Beat 12 + 25: JOIN→TEAMS closes Minds-On and binds the shared spark."""
        live = self.school.get_live_session(self.session_id)
        student = self.app.test_client()
        student.post(
            "/auth/student-code",
            data={"code": live["session_code"], "name": "Aspen"},
            follow_redirects=False,
        )
        student.post("/student/mood", data={"mood": "good"})
        student.post("/student/character", data={"character": "fox"})
        join = self.client.get(f"/api/live-sessions/{self.session_id}/teacher-state")
        self.assertEqual(join.status_code, 200, join.get_json())
        join_state = join.get_json()["teacher_state"]
        self.assertEqual(join_state["stage"], "join")
        self.assertEqual(join_state["prompt_ref"], MINDS_ON_PROMPT_REF)
        seq = int(join_state["state_seq"])
        join_live = self.client.get(f"/api/live-sessions/{self.session_id}/state")
        self.assertEqual(join_live.status_code, 200, join_live.get_json())
        tally = join_live.get_json().get("mc_tally")
        self.assertIsNotNone(tally)
        self.assertEqual(tally["prompt_ref"], MINDS_ON_PROMPT_REF)
        student_join = student.get("/api/student/state").get_json()
        self.assertTrue(student_join.get("waiting_room"), student_join)
        prompt = student_join.get("prompt") or {}
        self.assertTrue(is_minds_on_payload(prompt.get("payload")), student_join)
        shown = self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={
                "mc_ui": {
                    "prompt_ref": MINDS_ON_PROMPT_REF,
                    "reveal": True,
                    "reveal_to_students": False,
                }
            },
        )
        self.assertEqual(shown.status_code, 200, shown.get_json())
        join_ui = shown.get_json()["teacher_state"]["mc_ui"]
        self.assertTrue(join_ui["reveal"])
        self.assertTrue(join_ui["reveal_to_students"])
        self.assertTrue(join_ui["poll_closed"])
        nxt = self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"advance": "next"},
        )
        self.assertEqual(nxt.status_code, 200, nxt.get_json())
        nxt_state = nxt.get_json()["teacher_state"]
        self.assertEqual(nxt_state["stage"], "teams")
        self.assertEqual(nxt_state["prompt_ref"], TEAMS_SPARK_PROMPT_REF)
        self.assertEqual(nxt_state.get("cue_id"), CUE_TEAMS_SPARK)
        self.assertEqual(nxt_state.get("mc_ui", {}).get("prompt_ref"), TEAMS_SPARK_PROMPT_REF)
        self.assertFalse(nxt_state.get("mc_ui", {}).get("reveal"))
        self.assertGreater(nxt_state["state_seq"], seq)
        self.assertTrue(nxt_state["student_frames"]["questions"])
        self.assertFalse(nxt_state["student_frames"]["media"])
        self.assertEqual(nxt_state["layout_preset"], "questions_full")
        self.assertEqual(nxt_state["frames"], {"A": "questions"})
        staff_live = self.client.get(f"/api/live-sessions/{self.session_id}/state")
        self.assertEqual(staff_live.status_code, 200, staff_live.get_json())
        staff_body = staff_live.get_json()
        self.assertIsNone(staff_body.get("mc_tally"))
        spark = staff_body.get("teams_spark") or {}
        self.assertEqual(spark.get("prompt"), TEAMS_SPARK_PROMPT)
        self.assertIn("9", str(spark.get("teacher_key") or ""))
        self.assertFalse(spark.get("reveal"))
        student_teams = student.get("/api/student/state").get_json()
        self.assertEqual(student_teams.get("teacher_state", {}).get("stage"), "teams")
        self.assertEqual(
            student_teams.get("teacher_state", {}).get("prompt_ref"),
            TEAMS_SPARK_PROMPT_REF,
        )
        self.assertFalse(student_teams.get("waiting_room"), student_teams)
        prompt = student_teams.get("prompt") or {}
        payload = prompt.get("payload") or {}
        self.assertTrue(is_teams_spark_payload(payload), student_teams)
        self.assertFalse(is_minds_on_payload(payload), student_teams)
        self.assertEqual(payload.get("prompt"), TEAMS_SPARK_PROMPT)
        self.assertEqual(payload.get("choices"), ["8", "9", "17", "0"])
        self.assertNotIn("key", payload)
        self.assertNotIn("teacher_key", payload)
        self.assertNotIn("student_feedback_after_reveal", payload)
        self.assertIsNone(student_teams.get("my_response"))
        self.assertNotIn("mc_tally", student_teams)
        live_prompt = student.get("/api/student/live-prompt").get_json()
        self.assertTrue(is_teams_spark_payload((live_prompt.get("prompt") or {}).get("payload")))
        self.assertFalse(live_prompt.get("waiting_room"), live_prompt)
        if live_prompt.get("prompt") is not None:
            self.assertFalse(
                is_minds_on_payload((live_prompt["prompt"] or {}).get("payload"))
            )

    def test_beat25_teams_spark_reveals_then_clears_on_meet(self) -> None:
        """Beat 25: spark stay-line is teacher-gated; TEAMS→MEET clears it."""
        live = self.school.get_live_session(self.session_id)
        student = self.app.test_client()
        student.post(
            "/auth/student-code",
            data={"code": live["session_code"], "name": "Aspen"},
            follow_redirects=False,
        )
        student.post("/student/mood", data={"mood": "good"})
        student.post("/student/character", data={"character": "fox"})
        teams = self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"advance": "next"},
        )
        self.assertEqual(teams.status_code, 200, teams.get_json())
        first = teams.get_json()["teacher_state"]
        self.assertEqual(first["cue_id"], CUE_TEAMS_SPARK)
        mid = self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"teams_mode": "teams"},
        )
        self.assertEqual(mid.status_code, 200, mid.get_json())
        self.assertEqual(mid.get_json()["teacher_state"]["cue_id"], CUE_TEAMS_SPARK)
        self.assertEqual(
            mid.get_json()["teacher_state"]["prompt_ref"], TEAMS_SPARK_PROMPT_REF
        )
        shown = self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={
                "mc_ui": {
                    "prompt_ref": TEAMS_SPARK_PROMPT_REF,
                    "reveal": True,
                    "reveal_to_students": True,
                }
            },
        )
        self.assertEqual(shown.status_code, 200, shown.get_json())
        ui = shown.get_json()["teacher_state"]["mc_ui"]
        self.assertTrue(ui["reveal"])
        self.assertTrue(ui["reveal_to_students"])
        self.assertFalse(ui.get("poll_closed"))
        after = student.get("/api/student/state").get_json()
        stay = (after.get("prompt") or {}).get("payload") or {}
        self.assertEqual(
            stay.get("student_feedback_after_reveal"),
            "All but nine means nine stay.",
        )
        self.assertNotIn("teacher_key", stay)
        self.assertNotIn("mc_tally", after)
        meet = self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"advance": "next"},
        )
        self.assertEqual(meet.status_code, 200, meet.get_json())
        meet_state = meet.get_json()["teacher_state"]
        self.assertEqual(meet_state["stage"], "meet")
        self.assertEqual(meet_state["prompt_ref"], "meet-team")
        self.assertNotEqual(meet_state.get("cue_id"), CUE_TEAMS_SPARK)
        staff_meet = self.client.get(
            f"/api/live-sessions/{self.session_id}/state"
        ).get_json()
        self.assertIsNone(staff_meet.get("teams_spark"))
        student_meet = student.get("/api/student/state").get_json()
        meet_payload = (student_meet.get("prompt") or {}).get("payload") or {}
        self.assertFalse(is_teams_spark_payload(meet_payload), student_meet)

    def test_join_does_not_project_teacher_preview_media(self) -> None:
        """JOIN keeps Question-only frames after a teacher Real-slice seed."""
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
        self.assertEqual(state["teacher_state"]["stage"], "join")
        self.assertFalse(state["teacher_state"]["student_frames"]["media"])
        self.assertFalse(state["teacher_state"]["student_frames"]["canvas"])
        self.assertTrue(state["teacher_state"]["student_frames"]["questions"])
        self.assertEqual(state["teacher_state"]["prompt_ref"], MINDS_ON_PROMPT_REF)
        self.assertTrue(state.get("waiting_room"), state)
        prompt = state.get("prompt") or {}
        self.assertEqual((prompt.get("payload") or {}).get("item_id"), "minds_on")

    def test_rejects_unknown_stage(self) -> None:
        """Legacy Challenge/Freeze tokens are not stages."""
        bad = self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"stage": "challenge"},
        )
        self.assertEqual(bad.status_code, 400)

    def test_mc_ui_patch_does_not_set_wonder_cue(self) -> None:
        """Reveal/Hide is mc_ui only — Wonder stays silent."""
        shown = self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={
                "mc_ui": {
                    "prompt_ref": MINDS_ON_PROMPT_REF,
                    "reveal": True,
                    "reveal_to_students": False,
                }
            },
        )
        self.assertEqual(shown.status_code, 200, shown.get_json())
        body = shown.get_json()["teacher_state"]
        self.assertTrue(body["mc_ui"]["reveal"])
        self.assertTrue(body["mc_ui"]["reveal_to_students"])
        self.assertTrue(body["mc_ui"]["poll_closed"])
        self.assertIsNone(body.get("cue_id"))
        self.assertGreaterEqual(body["state_seq"], 1)

    def test_round_flags_patch_increments_state_seq(self) -> None:
        """Staff SET posts round_flags; Wonder stays silent."""
        before = self.client.get(f"/api/live-sessions/{self.session_id}/teacher-state")
        self.assertEqual(before.status_code, 200, before.get_json())
        seq = int(before.get_json()["teacher_state"]["state_seq"])
        posted = self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={
                "round_flags": {
                    "minds_on": True,
                    "action": False,
                    "consolidation": True,
                }
            },
        )
        self.assertEqual(posted.status_code, 200, posted.get_json())
        body = posted.get_json()["teacher_state"]
        self.assertEqual(body["state_seq"], seq + 1)
        self.assertTrue(body["round_flags"]["minds_on"])
        self.assertFalse(body["round_flags"]["action"])
        self.assertTrue(body["round_flags"]["consolidation"])
        self.assertIsNone(body.get("cue_id"))

    def test_canvas_presence_follows_alignment(self) -> None:
        """Frozen-to-teacher publishes teacher strokes; unique stays empty."""
        frozen = self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"unlocks": {"canvas": True}, "canvas_align": "teacher"},
        )
        self.assertEqual(frozen.status_code, 200, frozen.get_json())
        self.assertEqual(frozen.get_json()["teacher_state"]["canvas_align"], "teacher")
        posted = self.client.post(
            f"/api/live-sessions/{self.session_id}/canvas-presence",
            json={
                "x": 0.2,
                "y": 0.3,
                "point": [0.2, 0.3],
                "stroke_id": "t-1",
            },
        )
        self.assertEqual(posted.status_code, 200, posted.get_json())
        view = posted.get_json()["canvas_view"]
        self.assertEqual(len(view["strokes"]), 1)
        self.assertEqual(view["cursors"][0]["name"], "Teacher")
        unique = self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"canvas_align": "student"},
        )
        self.assertEqual(unique.status_code, 200, unique.get_json())
        empty = self.school.live_session_canvas_view(
            self.session_id, as_teacher=True
        )
        self.assertEqual(empty["strokes"], [])
        self.assertEqual(empty["cursors"], [])


if __name__ == "__main__":
    unittest.main()

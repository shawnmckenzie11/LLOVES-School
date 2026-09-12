#!/usr/bin/env python3
"""Source-agnostic MC live tally + Reveal for the teacher Questions card."""

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
from live_mc import build_mc_tally, is_mc_prompt  # noqa: E402
from live_media import DEFAULT_LIVE_MEDIA_URL  # noqa: E402
from live_teacher_state import MINDS_ON_PROMPT_REF  # noqa: E402
from minds_on import MINDS_ON_CHOICES, minds_on_prompt_payload  # noqa: E402


class LiveMcHelperTests(unittest.TestCase):
    """Pure tally helpers (no Flask)."""

    def test_tally_maps_letter_or_text_and_fingerprints(self) -> None:
        """Choices accept A or full text; seq changes when a bar moves."""
        prompt = {
            "id": 9,
            "kind": "mc",
            "payload": minds_on_prompt_payload(),
        }
        self.assertTrue(is_mc_prompt(prompt))
        empty = build_mc_tally(prompt, responses=[], present=2)
        assert empty is not None
        self.assertEqual(empty["prompt_ref"], "minds_on")
        self.assertEqual(empty["responded"], 0)
        self.assertEqual(empty["present"], 2)
        self.assertEqual(empty["source"], "live_prompt")
        first = build_mc_tally(
            prompt,
            responses=[{"response": {"choice": "A"}}],
            present=2,
        )
        via_text = build_mc_tally(
            prompt,
            responses=[{"response": {"choice": MINDS_ON_CHOICES[0]}}],
            present=2,
        )
        assert first is not None and via_text is not None
        self.assertEqual(first["choices"][0]["count"], 1)
        self.assertEqual(via_text["choices"][0]["count"], 1)
        self.assertEqual(first["response_seq"], via_text["response_seq"])
        other = build_mc_tally(
            prompt,
            responses=[{"response": {"choice": "B"}}],
            present=2,
        )
        assert other is not None
        self.assertNotEqual(first["response_seq"], other["response_seq"])
        self.assertIsNone(build_mc_tally({"kind": "share", "payload": {"prompt": "x"}}))

    def test_meet_chain_is_a_first_class_mc_source(self) -> None:
        """MEET soft MC tallies ephemeral picks, not response rows."""
        prompt = {
            "id": 3,
            "kind": "mc",
            "payload": {
                "item_id": "meet-team",
                "artifact_id": "quick-hitter-question-chain",
                "ride": "meet_team",
                "step": "A",
                "kind": "mc",
                "prompt": "Today I’m the teammate who…",
                "choices": ["keeps us kind", "Not sure"],
            },
        }
        tally = build_mc_tally(
            prompt,
            responses=[{"response": {"choice": "A"}}],
            meet_chain={
                "stage": "meet",
                "chain": ["A", "C", "B"],
                "index": 0,
                "a_picks": {"p1": "keeps us kind", "p2": "Not sure"},
                "c_reacts": {},
                "b_picks": {},
                "rotated": ["asks the good question", "brings the calm"],
            },
            present=2,
        )
        assert tally is not None
        self.assertEqual(tally["source"], "meet_chain")
        self.assertEqual(tally["responded"], 2)
        self.assertEqual(tally["choices"][0]["count"], 1)
        self.assertEqual(tally["choices"][1]["count"], 1)


class LiveMcApiTests(unittest.TestCase):
    """Staff /state tally + reveal for JOIN Minds-On and CONS MC."""

    def setUp(self) -> None:
        """Isolated app with staff, rostered class, live session, and student."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.staff = self.app.test_client()
        self.student = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
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
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple"],
            },
        )
        self.class_id = created.get_json()["class"]["id"]
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])
        self.student.post(
            "/auth/student-code",
            data={"code": live["session_code"], "name": "Maple"},
            follow_redirects=False,
        )
        self.student.post("/student/mood", data={"mood": "good"})
        self.student.post("/student/character", data={"character": "fox"})

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def test_join_minds_on_populates_and_reveal_stays_in_teacher_state(self) -> None:
        """JOIN Minds-On: live tally is staff-only until Reveal shares + closes."""
        idle = self.staff.get(f"/api/live-sessions/{self.session_id}/state")
        self.assertEqual(idle.status_code, 200, idle.get_json())
        idle_tally = idle.get_json()["mc_tally"]
        self.assertEqual(idle_tally["prompt_ref"], "minds_on")
        self.assertEqual(idle_tally["responded"], 0)
        self.assertGreaterEqual(idle_tally["present"], 1)
        self.assertEqual(idle_tally["source"], "live_prompt")
        student = self.student.get("/api/student/state").get_json()
        self.assertNotIn("mc_tally", student)
        self.assertFalse(student.get("poll_closed"))
        prompt = student["prompt"]
        self.assertEqual(prompt["payload"]["item_id"], "minds_on")
        submit = self.student.post(
            "/api/student/live-prompt/response",
            json={
                "prompt_id": prompt["id"],
                "response": {"choice": MINDS_ON_CHOICES[0]},
            },
        )
        self.assertEqual(submit.status_code, 200, submit.get_json())
        live = self.staff.get(f"/api/live-sessions/{self.session_id}/state").get_json()
        tally = live["mc_tally"]
        self.assertEqual(tally["responded"], 1)
        self.assertEqual(tally["choices"][0]["count"], 1)
        self.assertGreater(tally["response_seq"], idle_tally["response_seq"])
        self.assertIsNone((live.get("teacher_state") or {}).get("mc_ui"))
        shown = self.staff.post(
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
        ui = shown.get_json()["teacher_state"]["mc_ui"]
        self.assertTrue(ui["reveal"])
        self.assertTrue(ui["reveal_to_students"])
        self.assertTrue(ui["poll_closed"])
        self.assertIsNone(shown.get_json()["teacher_state"].get("cue_id"))
        student_after = self.student.get("/api/student/state").get_json()
        shared = student_after.get("mc_tally")
        self.assertIsNotNone(shared)
        self.assertEqual(shared["prompt_ref"], "minds_on")
        self.assertEqual(shared["responded"], 1)
        self.assertEqual(shared["choices"][0]["count"], 1)
        self.assertEqual(shared["choices"][0]["pct"], 100)
        self.assertTrue(student_after.get("poll_closed"))
        self.assertTrue(
            (student_after.get("teacher_state") or {})
            .get("mc_ui", {})
            .get("reveal_to_students")
        )
        blocked = self.student.post(
            "/api/student/live-prompt/response",
            json={
                "prompt_id": prompt["id"],
                "response": {"choice": MINDS_ON_CHOICES[1]},
            },
        )
        self.assertEqual(blocked.status_code, 409, blocked.get_json())
        self.assertTrue(blocked.get_json().get("poll_closed"))
        self.assertEqual(blocked.get_json().get("error"), "Poll is closed.")

    def test_cons_mc_uses_the_same_tally_pipeline(self) -> None:
        """CONS-1 (not Minds-On) fills the same staff mc_tally shape."""
        self.staff.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"stage": "play"},
        )
        self.staff.post(
            f"/api/live-sessions/{self.session_id}/active-media",
            json={"url": DEFAULT_LIVE_MEDIA_URL, "frozen": True},
        )
        cons = self.staff.post(
            f"/api/live-sessions/{self.session_id}/active-media",
            json={"cons_item": "C1-CONS-1"},
        )
        self.assertEqual(cons.status_code, 200, cons.get_json())
        student = self.student.get("/api/student/state").get_json()
        self.assertEqual(student["prompt"]["payload"]["item_id"], "C1-CONS-1")
        self.assertEqual(student["prompt"]["kind"], "mc")
        submit = self.student.post(
            "/api/student/live-prompt/response",
            json={
                "prompt_id": student["prompt"]["id"],
                "response": {"choice": "a > 0"},
            },
        )
        self.assertEqual(submit.status_code, 200, submit.get_json())
        tally = self.staff.get(
            f"/api/live-sessions/{self.session_id}/state"
        ).get_json()["mc_tally"]
        self.assertEqual(tally["prompt_ref"], "C1-CONS-1")
        self.assertEqual(tally["item_id"], "C1-CONS-1")
        self.assertEqual(tally["source"], "live_prompt")
        self.assertEqual(tally["responded"], 1)
        self.assertEqual(tally["choices"][1]["id"], "B")
        self.assertEqual(tally["choices"][1]["count"], 1)
        revealed = self.staff.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"mc_ui": {"prompt_ref": "C1-CONS-1", "reveal": True}},
        )
        self.assertEqual(revealed.status_code, 200, revealed.get_json())
        self.assertTrue(revealed.get_json()["teacher_state"]["mc_ui"]["reveal"])
        self.assertFalse(
            revealed.get_json()["teacher_state"]["mc_ui"]["reveal_to_students"]
        )


if __name__ == "__main__":
    unittest.main()

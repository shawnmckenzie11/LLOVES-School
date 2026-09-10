#!/usr/bin/env python3
"""Meet Your Team warm-up payload helpers and assign/meet-teams seed/clear."""

from __future__ import annotations

import os
import random
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
from meet_team import (  # noqa: E402
    MEET_TEAM_FIXED_CHOICES,
    MEET_TEAM_ITEM_ID,
    MEET_TEAM_LABEL,
    MEET_TEAM_PROMPT,
    MEET_TEAM_WARMUP_POOL,
    is_meet_team_payload,
    meet_team_choices,
    meet_team_prompt_payload,
)
from minds_on import is_minds_on_payload  # noqa: E402
from quick_hitter import (  # noqa: E402
    CLEAR_ON_TEAM_CHALLENGE,
    QUICK_HITTER_ARTIFACT_ID,
    QUICK_HITTER_CHANNEL,
    RIDE_MEET_TEAM,
)


class MeetTeamHelperTests(unittest.TestCase):
    """Universal teammate-poll copy, fixed options, and rotated pool."""

    def test_payload_is_universal_teammate_mc(self) -> None:
        """Stem, packaging, and identity stay course-agnostic."""
        payload = meet_team_prompt_payload(rng=random.Random(0))
        self.assertEqual(payload["item_id"], MEET_TEAM_ITEM_ID)
        self.assertEqual(payload["item_id"], "meet-team")
        self.assertEqual(payload["label"], MEET_TEAM_LABEL)
        self.assertEqual(payload["prompt"], MEET_TEAM_PROMPT)
        self.assertEqual(payload["prompt"], "Today I’m the teammate who…")
        self.assertEqual(payload["artifact_id"], QUICK_HITTER_ARTIFACT_ID)
        self.assertEqual(payload["ride"], RIDE_MEET_TEAM)
        self.assertEqual(payload["channel"], QUICK_HITTER_CHANNEL)
        self.assertTrue(payload["ephemeral"])
        self.assertFalse(payload["durable_store"])
        self.assertEqual(payload["clear_on"], CLEAR_ON_TEAM_CHALLENGE)
        self.assertEqual(payload["chain_length"], 1)
        self.assertEqual(len(payload["items"]), 1)
        self.assertNotIn("key", payload)
        self.assertTrue(is_meet_team_payload(payload))
        self.assertFalse(is_minds_on_payload(payload))
        self.assertFalse(is_meet_team_payload({"item_id": "minds_on"}))
        self.assertFalse(is_meet_team_payload(None))

    def test_fixed_three_always_present(self) -> None:
        """keeps us kind, team leader, and Not sure land on every draw."""
        for seed in range(16):
            choices = meet_team_prompt_payload(rng=random.Random(seed))["choices"]
            self.assertEqual(len(choices), 5)
            for fixed in MEET_TEAM_FIXED_CHOICES:
                self.assertIn(fixed, choices)
            self.assertEqual(choices[-1], "Not sure")
            self.assertIn("keeps us kind", choices)
            self.assertIn("wants to try being team leader", choices)

    def test_rotated_two_come_from_pool(self) -> None:
        """The two non-fixed lines are distinct members of the warmup pool."""
        seen: set[str] = set()
        for seed in range(24):
            payload = meet_team_prompt_payload(rng=random.Random(seed))
            extra = [
                choice
                for choice in payload["choices"]
                if choice not in MEET_TEAM_FIXED_CHOICES
            ]
            self.assertEqual(len(extra), 2)
            self.assertEqual(len(set(extra)), 2)
            for choice in extra:
                self.assertIn(choice, MEET_TEAM_WARMUP_POOL)
                seen.add(choice)
        self.assertGreaterEqual(len(seen), 3)

    def test_explicit_rotated_pair(self) -> None:
        """Callers can pin the two rotated lines."""
        choices = meet_team_choices(
            ["notices details", "brings the calm"]
        )
        self.assertEqual(
            choices,
            [
                "notices details",
                "brings the calm",
                "keeps us kind",
                "wants to try being team leader",
                "Not sure",
            ],
        )


class MeetTeamLivePromptTests(unittest.TestCase):
    """Assign / Meet Teams seed the warmup; Team Challenge clears it."""

    def setUp(self) -> None:
        """Isolated app with one rostered class and staff/student clients."""
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
                "codenames": ["Maple", "Aspen"],
            },
        )
        self.assertEqual(created.status_code, 200)
        self.class_id = int(created.get_json()["class"]["id"])
        run = self.staff.post(
            f"/staff/class/{self.class_id}/run-live",
            follow_redirects=False,
        )
        self.assertEqual(run.status_code, 302)
        live = self.school.get_active_live_session_for_class(self.class_id)
        self.assertIsNotNone(live)
        assert live is not None
        self.session_code = str(live["session_code"])
        self.live_session_id = int(live["id"])
        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.student.post("/student/mood", data={"mood": "good"})

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _staff_assign_two_teams(self) -> None:
        """Mark the roster present and Generate teams (n=2, random)."""
        begin = self.staff.post(
            f"/api/classes/{self.class_id}/begin",
            json={"meeting_date": "2026-09-09"},
        )
        self.assertEqual(begin.status_code, 200, begin.get_json())
        ids = [int(student["id"]) for student in begin.get_json()["students"]]
        self.staff.post(
            f"/api/classes/{self.class_id}/game/attendance",
            json={"present_ids": ids, "meeting_date": "2026-09-09"},
        )
        assigned = self.staff.post(
            f"/api/classes/{self.class_id}/game/assign",
            json={"n_teams": 2, "mode": "random"},
        )
        self.assertEqual(assigned.status_code, 200, assigned.get_json())

    def _assert_meet_team_prompt(self, body: dict) -> dict:
        """Require the universal teammate MC and return its payload."""
        self.assertFalse(body.get("waiting_room"), body)
        prompt = body.get("prompt") or {}
        payload = prompt.get("payload") or {}
        self.assertEqual(payload.get("item_id"), "meet-team")
        self.assertEqual(payload.get("ride"), "meet_team")
        self.assertEqual(payload.get("prompt"), MEET_TEAM_PROMPT)
        self.assertTrue(payload.get("ephemeral"))
        self.assertFalse(payload.get("durable_store"))
        self.assertEqual(payload.get("clear_on"), CLEAR_ON_TEAM_CHALLENGE)
        choices = list(payload.get("choices") or [])
        self.assertEqual(len(choices), 5)
        for fixed in MEET_TEAM_FIXED_CHOICES:
            self.assertIn(fixed, choices)
        extra = [choice for choice in choices if choice not in MEET_TEAM_FIXED_CHOICES]
        self.assertEqual(len(extra), 2)
        for choice in extra:
            self.assertIn(choice, MEET_TEAM_WARMUP_POOL)
        self.assertFalse(is_minds_on_payload(payload))
        self.assertNotIn("key", payload)
        return payload

    def test_assign_clears_minds_on_and_seeds_warmup(self) -> None:
        """Generate teams drops waiting-room Minds-On and shows the teammate poll."""
        idle = self.student.get("/api/student/live-prompt").get_json()
        self.assertTrue(idle["waiting_room"])
        self.assertEqual(idle["prompt"]["payload"]["item_id"], "minds_on")

        self._staff_assign_two_teams()
        live_prompt = self.student.get("/api/student/live-prompt").get_json()
        self._assert_meet_team_prompt(live_prompt)
        state = self.student.get("/api/student/state").get_json()
        self._assert_meet_team_prompt(state)

    def test_meet_teams_clears_minds_on_and_seeds_warmup(self) -> None:
        """Meet Teams after assign keeps the warmup (does not resurrect Minds-On)."""
        self._staff_assign_two_teams()
        first = self.student.get("/api/student/live-prompt").get_json()
        first_choices = list(first["prompt"]["payload"]["choices"])
        meet = self.staff.post(
            f"/api/classes/{self.class_id}/game/meet-teams",
            json={"minutes": 3},
        )
        self.assertEqual(meet.status_code, 200, meet.get_json())
        self.assertEqual(meet.get_json()["game"]["overlay_phase"], "meet_teams")
        again = self.student.get("/api/student/live-prompt").get_json()
        payload = self._assert_meet_team_prompt(again)
        self.assertEqual(payload["choices"], first_choices)

    def test_start_rounds_clears_meet_team_warmup(self) -> None:
        """Team Challenge start drops the ephemeral teammate poll."""
        self._staff_assign_two_teams()
        assigned = self.school.game.game_state(self.class_id)
        teams = [{"id": team["id"], "name": team["name"]} for team in assigned["teams"]]
        self.staff.post(
            f"/api/classes/{self.class_id}/game/rename",
            json={"teams": teams, "go_live": False},
        )
        self.staff.post(
            f"/api/classes/{self.class_id}/game/meet-teams",
            json={"minutes": 3},
        )
        warmed = self.student.get("/api/student/live-prompt").get_json()
        self._assert_meet_team_prompt(warmed)

        live = self.staff.post(
            f"/api/classes/{self.class_id}/game/start-rounds",
            json={"rounds": [{"kind": "challenge", "minutes": 15}]},
        )
        self.assertEqual(live.status_code, 200, live.get_json())
        self.assertEqual(live.get_json()["game"]["status"], "live")
        state = self.student.get("/api/student/state").get_json()
        self.assertTrue(state.get("scoring"))
        self.assertFalse(state.get("waiting_room"))
        prompt = state.get("prompt")
        if prompt is not None:
            payload = prompt.get("payload") or {}
            self.assertFalse(is_meet_team_payload(payload))
            self.assertFalse(is_minds_on_payload(payload))
        live_prompt = self.student.get("/api/student/live-prompt").get_json()
        self.assertFalse(live_prompt.get("waiting_room"))
        if live_prompt.get("prompt") is not None:
            self.assertFalse(
                is_meet_team_payload((live_prompt["prompt"] or {}).get("payload"))
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

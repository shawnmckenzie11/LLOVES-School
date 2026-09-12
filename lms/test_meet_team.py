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
    CUE_MEET_CLEAR,
    CUE_MEET_OPEN,
    MEET_CHAIN_DEFAULT,
    MEET_NEED_POOL,
    MEET_SPARK_POOL,
    MEET_TEAM_FIXED_CHOICES,
    MEET_TEAM_ITEM_ID,
    MEET_TEAM_LABEL,
    MEET_TEAM_PROMPT,
    MEET_TEAM_WARMUP_POOL,
    advance_meet_chain,
    is_meet_team_payload,
    meet_step_payload,
    meet_team_choices,
    meet_team_prompt_payload,
    new_meet_chain_state,
    skip_meet_c,
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
        self.assertEqual(payload["step"], "A")
        self.assertEqual(payload["chain"], ["A", "C", "B"])
        self.assertEqual(payload["chain_index"], 1)
        self.assertEqual(payload["chain_length"], 3)
        self.assertEqual(len(payload["items"]), 1)
        self.assertNotIn("key", payload)
        self.assertTrue(is_meet_team_payload(payload))
        self.assertFalse(is_minds_on_payload(payload))
        self.assertFalse(is_meet_team_payload({"item_id": "minds_on"}))
        self.assertFalse(is_meet_team_payload(None))
        self.assertEqual(CUE_MEET_OPEN, "cue.meet_open")
        self.assertEqual(CUE_MEET_CLEAR, "cue.meet_clear")
        self.assertNotIn("cue.meet_a", (CUE_MEET_OPEN, CUE_MEET_CLEAR))
        self.assertNotIn("meet_a", (CUE_MEET_OPEN, CUE_MEET_CLEAR))

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

    def test_chain_order_and_skip_c(self) -> None:
        """Default A→C→B; density escape drops C first."""
        self.assertEqual(list(MEET_CHAIN_DEFAULT), ["A", "C", "B"])
        state = new_meet_chain_state(rng=random.Random(1))
        self.assertEqual(state["chain"], ["A", "C", "B"])
        self.assertEqual(state["index"], 0)
        nxt = advance_meet_chain(state)
        self.assertEqual(nxt["chain"][nxt["index"]], "C")
        last = advance_meet_chain(nxt)
        self.assertEqual(last["chain"][last["index"]], "B")
        still = advance_meet_chain(last)
        self.assertEqual(still["index"], last["index"])
        skipped = skip_meet_c(new_meet_chain_state(rng=random.Random(2)))
        self.assertEqual(skipped["chain"], ["A", "B"])
        self.assertEqual(skipped["chain"][skipped["index"]], "A")
        mid = advance_meet_chain(new_meet_chain_state(rng=random.Random(3)))
        self.assertEqual(mid["chain"][mid["index"]], "C")
        dropped = skip_meet_c(mid)
        self.assertEqual(dropped["chain"], ["A", "B"])
        self.assertEqual(dropped["chain"][dropped["index"]], "B")

    def test_spark_and_need_are_ops_stubs(self) -> None:
        """C/B pool ids stay course-agnostic Ops stubs."""
        spark = meet_step_payload("C", rng=random.Random(0))
        self.assertEqual(spark["step"], "C")
        self.assertEqual(spark["item_id"], "meet-c")
        self.assertEqual(spark["chain_index"], 2)
        self.assertTrue(any(spark["spark_id"] == row["id"] for row in MEET_SPARK_POOL))
        self.assertIn("Ops stub", spark["prompt"])
        need = meet_step_payload("B", rng=random.Random(0))
        self.assertEqual(need["step"], "B")
        self.assertEqual(need["item_id"], "meet-b")
        self.assertTrue(need["prompt"].startswith("One thing our team might need"))
        self.assertIn("Not sure", need["choices"])
        self.assertTrue(any(need["need_prompt_id"] == row["id"] for row in MEET_NEED_POOL))
        short = meet_team_prompt_payload(rng=random.Random(0), include_c=False)
        self.assertEqual(short["chain"], ["A", "B"])
        self.assertEqual(short["chain_length"], 2)


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

    def _assert_meet_step(self, body: dict, step: str) -> dict:
        """Require one visible Meet chain item and return its payload."""
        self.assertFalse(body.get("waiting_room"), body)
        prompt = body.get("prompt") or {}
        payload = prompt.get("payload") or {}
        self.assertEqual(payload.get("ride"), "meet_team")
        self.assertEqual(payload.get("step"), step)
        self.assertTrue(payload.get("ephemeral"))
        self.assertFalse(payload.get("durable_store"))
        self.assertEqual(payload.get("clear_on"), CLEAR_ON_TEAM_CHALLENGE)
        self.assertFalse(is_minds_on_payload(payload))
        self.assertNotIn("key", payload)
        return payload

    def _assert_meet_team_prompt(self, body: dict) -> dict:
        """Require Meet step A and return its payload."""
        payload = self._assert_meet_step(body, "A")
        self.assertEqual(payload.get("item_id"), "meet-team")
        self.assertEqual(payload.get("prompt"), MEET_TEAM_PROMPT)
        choices = list(payload.get("choices") or [])
        self.assertEqual(len(choices), 5)
        for fixed in MEET_TEAM_FIXED_CHOICES:
            self.assertIn(fixed, choices)
        extra = [choice for choice in choices if choice not in MEET_TEAM_FIXED_CHOICES]
        self.assertEqual(len(extra), 2)
        for choice in extra:
            self.assertIn(choice, MEET_TEAM_WARMUP_POOL)
        return payload

    def test_assign_keeps_minds_on_without_mounting_meet(self) -> None:
        """Generate teams keeps JOIN Minds-On; Meet waits for MEET enter."""
        idle = self.student.get("/api/student/live-prompt").get_json()
        self.assertTrue(idle["waiting_room"])
        self.assertEqual(idle["prompt"]["payload"]["item_id"], "minds_on")

        self._staff_assign_two_teams()
        live_prompt = self.student.get("/api/student/live-prompt").get_json()
        self.assertTrue(live_prompt.get("waiting_room"), live_prompt)
        prompt = live_prompt.get("prompt")
        self.assertIsNotNone(prompt)
        payload = (prompt or {}).get("payload") or {}
        self.assertTrue(is_minds_on_payload(payload))
        self.assertFalse(is_meet_team_payload(payload))
        state = self.student.get("/api/student/state").get_json()
        self.assertNotEqual(state.get("teacher_state", {}).get("cue_id"), CUE_MEET_OPEN)
        self.assertFalse(state.get("teacher_state", {}).get("student_frames", {}).get("media"))

    def test_meet_teams_clears_minds_on_and_seeds_warmup(self) -> None:
        """Start Meet after assign mounts A and fires cue.meet_open once."""
        self._staff_assign_two_teams()
        meet = self.staff.post(
            f"/api/classes/{self.class_id}/game/meet-teams",
            json={"minutes": 3},
        )
        self.assertEqual(meet.status_code, 200, meet.get_json())
        self.assertEqual(meet.get_json()["game"]["overlay_phase"], "meet_teams")
        again = self.student.get("/api/student/live-prompt").get_json()
        payload = self._assert_meet_team_prompt(again)
        self.assertEqual(payload["chain"], ["A", "C", "B"])
        teacher = self.staff.get(
            f"/api/live-sessions/{self.live_session_id}/teacher-state"
        ).get_json()["teacher_state"]
        self.assertEqual(teacher["stage"], "meet")
        self.assertEqual(teacher["cue_id"], CUE_MEET_OPEN)
        self.assertEqual(teacher["active_tab"], "questions")
        self.assertEqual(teacher["layout_preset"], "questions_full")
        self.assertEqual(teacher["meet_chain"]["index"], 0)
        again_state = self.student.get("/api/student/state").get_json()
        self.assertEqual(again_state["teacher_state"]["cue_id"], CUE_MEET_OPEN)
        self.assertNotIn("meet_a", str(again_state["teacher_state"].get("cue_id")))

    def test_meet_timer_start_does_not_remount_existing_meet(self) -> None:
        """Timer start on an already-mounted MEET stage does not bump chrome."""
        self._staff_assign_two_teams()
        entered = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/teacher-state",
            json={"stage": "meet"},
        )
        self.assertEqual(entered.status_code, 200, entered.get_json())
        before = entered.get_json()["teacher_state"]
        self.assertEqual(before["stage"], "meet")
        self.assertEqual(before["cue_id"], CUE_MEET_OPEN)
        seq = before["state_seq"]
        meet = self.staff.post(
            f"/api/classes/{self.class_id}/game/meet-teams",
            json={"minutes": 3},
        )
        self.assertEqual(meet.status_code, 200, meet.get_json())
        self.assertEqual(meet.get_json()["game"]["overlay_phase"], "meet_teams")
        after = self.staff.get(
            f"/api/live-sessions/{self.live_session_id}/teacher-state"
        ).get_json()["teacher_state"]
        self.assertEqual(after["stage"], "meet")
        self.assertEqual(after["state_seq"], seq)
        self.assertEqual(after["cue_id"], before["cue_id"])
        self.assertEqual(after["meet_chain"]["index"], before["meet_chain"]["index"])

    def test_session_timer_starts_on_join_without_teams(self) -> None:
        """Beat 14: JOIN timer start does not require teams or change stage."""
        self.staff.post(
            f"/api/classes/{self.class_id}/begin",
            json={"meeting_date": "2026-09-08"},
        )
        before = self.staff.get(
            f"/api/live-sessions/{self.live_session_id}/teacher-state"
        ).get_json()["teacher_state"]
        self.assertEqual(before["stage"], "join")
        seq = before["state_seq"]
        started = self.staff.post(
            f"/api/classes/{self.class_id}/game/timer/start",
            json={"minutes": 5},
        )
        self.assertEqual(started.status_code, 200, started.get_json())
        game = started.get_json()["game"]
        self.assertNotEqual(game.get("overlay_phase"), "meet_teams")
        self.assertFalse(game.get("timer_paused"))
        self.assertIsInstance(game.get("round_ends_at_ms"), int)
        after = self.staff.get(
            f"/api/live-sessions/{self.live_session_id}/teacher-state"
        ).get_json()["teacher_state"]
        self.assertEqual(after["stage"], "join")
        self.assertEqual(after["state_seq"], seq)

    def test_teacher_next_walks_a_c_b_then_clear_wipes(self) -> None:
        """Next advances A→C→B; End Meet → ROUND wipes picks and fires meet_clear."""
        self._staff_assign_two_teams()
        self.staff.post(
            f"/api/classes/{self.class_id}/game/meet-teams",
            json={"minutes": 3},
        )
        first = self._assert_meet_team_prompt(
            self.student.get("/api/student/live-prompt").get_json()
        )
        prompt_id = int(
            self.student.get("/api/student/live-prompt").get_json()["prompt"]["id"]
        )
        pick = self.student.post(
            "/api/student/live-prompt/response",
            json={
                "prompt_id": prompt_id,
                "response": {"choice": "keeps us kind"},
            },
        )
        self.assertEqual(pick.status_code, 200, pick.get_json())
        self.assertTrue(pick.get_json().get("my_response", {}).get("ephemeral"))
        self.assertEqual(pick.get_json().get("meet_chip"), "keeps us kind")
        self.assertNotIn("feedback", pick.get_json())
        self.assertNotIn("feedback", pick.get_json().get("my_response") or {})
        self.assertNotIn("lead", pick.get_json())
        teacher_after = self.staff.get(
            f"/api/live-sessions/{self.live_session_id}/teacher-state"
        ).get_json()["teacher_state"]
        self.assertIn("keeps us kind", teacher_after["meet_chain"]["a_picks"].values())
        with self.school._lock:
            stored = self.school.conn.execute(
                "SELECT COUNT(*) AS n FROM live_session_responses WHERE prompt_id = ?",
                (prompt_id,),
            ).fetchone()
        self.assertEqual(int(stored["n"]), 0)

        nxt = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/teacher-state",
            json={"meet_action": "next"},
        )
        self.assertEqual(nxt.status_code, 200, nxt.get_json())
        spark = self._assert_meet_step(
            self.student.get("/api/student/live-prompt").get_json(), "C"
        )
        self.assertEqual(spark["item_id"], "meet-c")
        self.assertEqual(spark["chain_index"], 2)
        self.assertNotEqual(nxt.get_json()["teacher_state"]["cue_id"], "cue.meet_c")

        nxt2 = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/teacher-state",
            json={"meet_action": "next"},
        )
        need = self._assert_meet_step(
            self.student.get("/api/student/live-prompt").get_json(), "B"
        )
        self.assertEqual(need["item_id"], "meet-b")
        self.assertTrue(need["prompt"].startswith("One thing our team might need"))
        self.assertEqual(first["chain"], ["A", "C", "B"])

        ended = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/teacher-state",
            json={"meet_action": "clear"},
        )
        self.assertEqual(ended.status_code, 200, ended.get_json())
        body = ended.get_json()["teacher_state"]
        self.assertEqual(body["stage"], "round")
        self.assertEqual(body["cue_id"], CUE_MEET_CLEAR)
        self.assertIsNone(body.get("meet_chain"))
        cleared = self.student.get("/api/student/live-prompt").get_json()
        if cleared.get("prompt") is not None:
            self.assertFalse(
                is_meet_team_payload((cleared["prompt"] or {}).get("payload"))
            )
        self.assertIsNone(cleared.get("meet_chip") or None)
        state = self.student.get("/api/student/state").get_json()
        self.assertEqual(state["teacher_state"]["cue_id"], CUE_MEET_CLEAR)
        self.assertFalse(state.get("meet_chip"))

    def test_skip_c_makes_a_then_b(self) -> None:
        """Skip C density escape: A → B, no spark card."""
        self._staff_assign_two_teams()
        self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/teacher-state",
            json={"stage": "meet"},
        )
        self._assert_meet_team_prompt(
            self.student.get("/api/student/live-prompt").get_json()
        )
        skipped = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/teacher-state",
            json={"meet_action": "skip_c"},
        )
        self.assertEqual(skipped.status_code, 200, skipped.get_json())
        chain = skipped.get_json()["teacher_state"]["meet_chain"]
        self.assertEqual(chain["chain"], ["A", "B"])
        self.assertEqual(chain["index"], 0)
        still_a = self._assert_meet_team_prompt(
            self.student.get("/api/student/live-prompt").get_json()
        )
        self.assertEqual(still_a["chain"], ["A", "B"])
        self.assertEqual(still_a["chain_length"], 2)
        self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/teacher-state",
            json={"meet_action": "next"},
        )
        need = self._assert_meet_step(
            self.student.get("/api/student/live-prompt").get_json(), "B"
        )
        self.assertEqual(need["chain_index"], 2)
        self.assertEqual(need["chain_length"], 2)

    def _staff_mark_present(self) -> list[int]:
        """Begin the meeting and persist present flags without assigning."""
        begin = self.staff.post(
            f"/api/classes/{self.class_id}/begin",
            json={"meeting_date": "2026-09-09"},
        )
        self.assertEqual(begin.status_code, 200, begin.get_json())
        ids = [int(student["id"]) for student in begin.get_json()["students"]]
        att = self.staff.post(
            f"/api/classes/{self.class_id}/game/attendance",
            json={"present_ids": ids, "meeting_date": "2026-09-09"},
        )
        self.assertEqual(att.status_code, 200, att.get_json())
        return ids

    def test_teams_next_assigns_and_meets_in_one_seq(self) -> None:
        """TEAMS→Meet Next commits assign + stage=meet in one state_seq."""
        ids = self._staff_mark_present()
        teams_stage = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/teacher-state",
            json={"stage": "teams"},
        )
        self.assertEqual(teams_stage.status_code, 200, teams_stage.get_json())
        before = teams_stage.get_json()["teacher_state"]
        self.assertEqual(before["stage"], "teams")
        seq = int(before["state_seq"])
        game_before = self.school.game.game_state(self.class_id)
        self.assertLess(len(game_before.get("teams") or []), 2)

        nxt = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/teacher-state",
            json={
                "advance": "next",
                "teams_mode": "teams",
                "assign": {
                    "n_teams": 2,
                    "mode": "random",
                    "present_ids": ids,
                },
            },
        )
        self.assertEqual(nxt.status_code, 200, nxt.get_json())
        body = nxt.get_json()
        teacher = body["teacher_state"]
        self.assertEqual(teacher["stage"], "meet")
        self.assertEqual(teacher["state_seq"], seq + 1)
        self.assertEqual(teacher["teams_mode"], "teams")
        assigned = body.get("game") or self.school.game.game_state(self.class_id)
        teams = [team for team in assigned.get("teams") or [] if team.get("name") != "Class"]
        self.assertGreaterEqual(len(teams), 2)
        members = [member for team in teams for member in team.get("members") or []]
        self.assertGreaterEqual(len(members), 1)
        again = self.staff.get(
            f"/api/live-sessions/{self.live_session_id}/teacher-state"
        ).get_json()["teacher_state"]
        self.assertEqual(again["stage"], "meet")
        self.assertEqual(again["state_seq"], seq + 1)

    def test_teams_next_count_one_skips_assign_without_crash(self) -> None:
        """Count=1 Next still enters MEET and does not call assign_teams."""
        ids = self._staff_mark_present()[:1]
        self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/teacher-state",
            json={"stage": "teams"},
        )
        nxt = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/teacher-state",
            json={
                "advance": "next",
                "teams_mode": "individual",
                "assign": {"n_teams": 1, "mode": "balanced", "present_ids": ids},
            },
        )
        self.assertEqual(nxt.status_code, 200, nxt.get_json())
        teacher = nxt.get_json()["teacher_state"]
        self.assertEqual(teacher["stage"], "meet")
        self.assertEqual(teacher["teams_mode"], "individual")
        game = self.school.game.game_state(self.class_id)
        teams = [team for team in game.get("teams") or [] if team.get("name") != "Class"]
        self.assertLess(len(teams), 2)

    def test_teams_next_assign_failure_stays_on_teams(self) -> None:
        """Failed assign returns 400 and does not commit stage=meet."""
        ids = self._staff_mark_present()
        parked = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/teacher-state",
            json={"stage": "teams"},
        )
        seq = int(parked.get_json()["teacher_state"]["state_seq"])
        bad = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/teacher-state",
            json={
                "advance": "next",
                "assign": {
                    "n_teams": 99,
                    "mode": "balanced",
                    "present_ids": ids,
                },
            },
        )
        self.assertEqual(bad.status_code, 400, bad.get_json())
        after = self.staff.get(
            f"/api/live-sessions/{self.live_session_id}/teacher-state"
        ).get_json()["teacher_state"]
        self.assertEqual(after["stage"], "teams")
        self.assertEqual(after["state_seq"], seq)

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

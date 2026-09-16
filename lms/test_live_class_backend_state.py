#!/usr/bin/env python3
"""Focused backend tests for global live controls, publishing, and groups."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from live_class_metadata import load_live_class_metadata
from minds_on import is_minds_on_payload  # noqa: E402


def metadata_fixture() -> dict[str, Any]:
    """Return v2-shaped resolved metadata with two individual/group items."""

    questions = [
        {
            "id": "q-one",
            "ref": "test/question/q-one",
            "item_type": "question",
            "stage": "round",
            "page_number": 1,
            "order": 1,
            "type": "mc",
            "text": "Pick one",
            "options": ["A", "B"],
            "correct_answer": "A",
            "default_status": "inactive",
            "publish_modes": ["individual", "group_consensus"],
            "response_mode": "individual",
        },
        {
            "id": "q-two",
            "ref": "test/question/q-two",
            "item_type": "question",
            "stage": "round",
            "page_number": 1,
            "order": 2,
            "type": "mc",
            "text": "Pick another",
            "options": ["A", "B"],
            "correct_answer": "B",
            "default_status": "inactive",
            "publish_modes": ["individual", "group_consensus"],
            "response_mode": "group_consensus",
        },
    ]
    media = {
        "id": "media-one",
        "ref": "test/media/one",
        "item_type": "media",
        "stage": "play",
        "page_number": 1,
        "order": 1,
        "title": "Media",
        "default_status": "inactive",
        "publish_modes": ["individual"],
        "response_mode": "individual",
    }
    return {
        "schema_version": 2,
        "course": "MCF3M",
        "module": "M1",
        "live_class": "C1",
        "questions": questions,
        "items": [*questions, media],
        "media": None,
        "slides": {"deck_ref": None, "page_numbers": []},
        "round_defaults": {},
    }


class LiveBackendStateTests(unittest.TestCase):
    """Exercise session state directly against an isolated school database."""

    def setUp(self) -> None:
        """Create one teacher, four-student class, and active live session."""

        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCF3M",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Aspen", "Birch", "Cedar", "Maple"],
            offering_id=int(self.offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )
        self.class_id = int(created["id"])
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])
        with self.school.game._lock:
            self.students = [
                dict(row)
                for row in self.school.game.conn.execute(
                    """
                    SELECT * FROM students
                    WHERE class_id = ?
                    ORDER BY id ASC
                    """,
                    (self.class_id,),
                ).fetchall()
            ]
        self.student_ids = [int(row["id"]) for row in self.students]
        self.original_metadata = self.school.live_class_metadata_for_session
        self.school.live_class_metadata_for_session = lambda _session_id: (
            metadata_fixture()
        )

    def tearDown(self) -> None:
        """Close sqlite handles and remove temporary files."""

        self.school.close()
        self.tmp.cleanup()

    def _begin_and_join(self, count: int = 4) -> None:
        """Open attendance and record the requested active attendees."""

        self.school.game.begin_game(self.class_id)
        for student in self.students[:count]:
            self.school.join_live_class_session(
                self.session_id,
                int(student["id"]),
                codename=str(student["codename"]),
            )

    def _setup_groups(self) -> list[dict[str, Any]]:
        """Create deterministic two-person teams and return their state."""

        assignments = [
            {"student_id": student_id, "team_index": index % 2}
            for index, student_id in enumerate(self.student_ids)
        ]
        result = self.school.setup_live_session_groups(
            self.session_id,
            n_teams=2,
            mode="manual",
            present_ids=self.student_ids,
            assignments=assignments,
        )
        return result["game"]["teams"]

    def test_v2_join_question_requires_explicit_publish(self) -> None:
        """An inactive v2 JOIN card stays hidden until its lifecycle publish."""

        self.school.live_class_metadata_for_session = self.original_metadata
        self.assertTrue(
            self.school.schema_v2_owns_live_stage_questions(
                self.session_id, "join"
            )
        )
        cards = self.school.live_session_question_cards(self.session_id)
        self.assertEqual([row["id"] for row in cards], ["minds_on"])
        self.assertEqual(cards[0]["status"], "inactive")
        before = self.school.student_live_prompt_payload(
            self.session_id, self.student_ids[0]
        )
        self.assertEqual(before["active_questions"], [])

        item = next(
            row
            for row in self.school.ensure_live_session_items(self.session_id)
            if row["item_id"] == "minds_on"
        )
        self.school.publish_live_session_item(
            self.session_id, int(item["id"]), publish_mode="individual"
        )
        after = self.school.student_live_prompt_payload(
            self.session_id, self.student_ids[0]
        )
        self.assertEqual(len(after["active_questions"]), 1)
        self.assertEqual(after["active_questions"][0]["item_id"], "minds_on")
        active_cards = self.school.live_session_question_cards(self.session_id)
        self.assertEqual(active_cards[0]["status"], "active")

    def test_join_cards_exclude_teams_spark_fallback(self) -> None:
        """A legacy TEAMS spark row never creates a second JOIN card."""

        self.school.live_class_metadata_for_session = self.original_metadata
        self.school.ensure_teams_spark(self.session_id)
        cards = self.school.live_session_question_cards(self.session_id)
        self.assertEqual([row["id"] for row in cards], ["minds_on"])
        self.assertNotIn("teams-spark", {row["id"] for row in cards})

    def test_global_group_state_is_fixed_and_stage_persistent(self) -> None:
        """New sessions are individual; setup is one-time and toggles persist."""

        initial = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertFalse(initial["groups_configured"])
        self.assertFalse(initial["run_as_group"])
        self.assertFalse(initial["scoreboard_visible"])
        self.assertFalse(initial["hide_absent"])
        self._begin_and_join(2)
        teams = self._setup_groups()
        configured = self.school.live_session_teacher_state_payload(
            self.session_id
        )
        self.assertTrue(configured["groups_configured"])
        self.assertTrue(configured["run_as_group"])
        self.assertTrue(configured["scoreboard_visible"])
        self.assertEqual(
            len(self.school.live_class_roster_projection(self.session_id)), 4
        )
        with self.assertRaisesRegex(ValueError, "already set up"):
            self.school.setup_live_session_groups(
                self.session_id,
                n_teams=2,
                mode="balanced",
                present_ids=self.student_ids,
            )
        before_members = {
            int(team["id"]): [int(row["id"]) for row in team["members"]]
            for team in teams
        }
        changed = self.school.set_live_session_teacher_state(
            self.session_id,
            stage="play",
            run_as_group=False,
            scoreboard_visible=False,
            hide_absent=True,
        )
        self.assertEqual(changed["stage"], "play")
        self.assertTrue(changed["groups_configured"])
        self.assertFalse(changed["run_as_group"])
        self.assertFalse(changed["scoreboard_visible"])
        self.assertTrue(changed["hide_absent"])
        after = self.school.game.game_state(self.class_id)["teams"]
        self.assertEqual(
            {
                int(team["id"]): [int(row["id"]) for row in team["members"]]
                for team in after
            },
            before_members,
        )
        roster = self.school.live_class_roster_projection(self.session_id)
        self.assertEqual(len(roster), 2)
        self.assertTrue(all(row["present"] for row in roster))
        self.assertEqual(self.school.live_group_projection(self.session_id), [])
        self.assertIsNone(
            self.school.live_scoreboard_projection(self.session_id)
        )

    def test_multiple_publish_close_results_and_cleanup(self) -> None:
        """Independent questions stay active, close locks, and end cleans data."""

        items = self.school.ensure_live_session_items(self.session_id)
        self.assertGreaterEqual(len(items), 3)
        self.assertTrue(
            any(str(row.get("kind") or "") == "whiteboard" for row in items)
        )
        self.assertTrue(all(row["status"] == "inactive" for row in items))
        self.assertTrue(all(row["show_live_results"] for row in items))
        q1 = next(row for row in items if row["item_id"] == "q-one")
        q2 = next(row for row in items if row["item_id"] == "q-two")
        media = next(row for row in items if row["item_id"] == "media-one")
        self.school.publish_live_session_item(
            self.session_id, int(q1["id"]), publish_mode="individual"
        )
        self.school.publish_live_session_item(
            self.session_id, int(q2["id"]), publish_mode="individual"
        )
        published_media = self.school.publish_live_session_item(
            self.session_id, int(media["id"]), publish_mode="individual"
        )
        self.assertIsNone(published_media["prompt_id"])
        active = self.school.list_active_live_questions(self.session_id)
        self.assertEqual(
            [row["payload"]["item_id"] for row in active],
            ["q-one", "q-two"],
        )
        prompt = active[0]
        student_id = self.student_ids[0]
        self.school.submit_live_prompt_response(
            int(prompt["id"]), student_id, {"choice": "A"}
        )
        student_payload = self.school.student_live_items_payload(
            self.session_id, student_id
        )
        self.assertEqual(len(student_payload["active_questions"]), 2)
        first = student_payload["active_questions"][0]
        self.assertIsNotNone(first["my_response"])
        self.assertIsNotNone(first["results"])
        self.school.update_live_session_item_settings(
            self.session_id,
            int(q2["id"]),
            show_live_results=False,
        )
        closed = self.school.close_live_session_item(
            self.session_id, int(q2["id"])
        )
        self.assertEqual(closed["status"], "closed")
        with self.assertRaisesRegex(ValueError, "not accepting"):
            second_prompt = active[1]
            self.school.submit_live_prompt_response(
                int(second_prompt["id"]), student_id, {"choice": "B"}
            )
        closed_payload = self.school.student_live_items_payload(
            self.session_id, student_id
        )
        self.assertEqual(len(closed_payload["closed_results"]), 1)
        self.assertEqual(
            closed_payload["closed_results"][0]["results_phase"], "final"
        )
        self.school.end_live_class_session(self.session_id, clear_moods=False)
        with self.school._lock:
            response_count = self.school.conn.execute(
                "SELECT COUNT(*) AS n FROM live_session_responses"
            ).fetchone()["n"]
        self.assertEqual(int(response_count), 0)

    def test_group_consensus_privacy_tie_finalization_and_award(self) -> None:
        """Votes stay private, ties need a choice, and finalization is atomic."""

        self._begin_and_join(4)
        teams = self._setup_groups()
        self.school.game.rename_teams(
            self.class_id,
            [{"id": team["id"], "name": team["name"]} for team in teams],
        )
        items = self.school.ensure_live_session_items(self.session_id)
        group_item = next(row for row in items if row["item_id"] == "q-two")
        group_item = self.school.publish_live_session_item(
            self.session_id,
            int(group_item["id"]),
            publish_mode="group_consensus",
        )
        teams = self.school.game.game_state(self.class_id)["teams"]
        first_ids = [int(row["id"]) for row in teams[0]["members"]]
        second_ids = [int(row["id"]) for row in teams[1]["members"]]
        first_vote = self.school.submit_group_consensus_vote(
            self.session_id,
            int(group_item["id"]),
            first_ids[0],
            {"choice": "A"},
        )
        self.assertEqual(
            first_vote["team"]["status"], "collecting_votes"
        )
        self.assertNotIn("vote_summary", first_vote["team"])
        advanced = self.school.submit_group_consensus_vote(
            self.session_id,
            int(group_item["id"]),
            first_ids[1],
            {"choice": "A"},
        )
        self.assertEqual(
            advanced["team"]["status"], "awaiting_team_answer"
        )
        self.assertEqual(
            advanced["team"]["proposed_answer"],
            {"kind": "choice", "value": "A"},
        )
        self.school.submit_group_consensus_vote(
            self.session_id,
            int(group_item["id"]),
            second_ids[0],
            {"choice": "A"},
        )
        tied = self.school.submit_group_consensus_vote(
            self.session_id,
            int(group_item["id"]),
            second_ids[1],
            {"choice": "B"},
        )
        self.assertEqual(tied["team"]["status"], "discussion")
        self.assertIsNone(tied["team"]["proposed_answer"])
        finalized = self.school.finalize_group_consensus_answer(
            self.session_id,
            int(group_item["id"]),
            second_ids[0],
            {"choice": "B"},
        )
        self.assertEqual(finalized["status"], "finalized")
        with self.assertRaisesRegex(ValueError, "already finalized"):
            self.school.finalize_group_consensus_answer(
                self.session_id,
                int(group_item["id"]),
                second_ids[1],
                {"choice": "A"},
            )
        first_final = self.school.finalize_group_consensus_answer(
            self.session_id,
            int(group_item["id"]),
            first_ids[0],
            {"choice": "A"},
        )
        self.assertEqual(first_final["status"], "finalized")
        awarded = self.school.award_group_consensus_points(
            self.session_id,
            int(group_item["id"]),
            team_id=int(teams[0]["id"]),
            amount=2,
        )
        self.assertEqual(awarded["awarded_student_ids"], sorted(first_ids))
        summary = self.school.teacher_group_consensus_summary(
            self.session_id, int(group_item["id"])
        )
        self.assertEqual(len(summary["teams"]), 2)
        own = self.school.student_group_consensus_state(
            group_item, first_ids[0]
        )
        self.assertEqual(own["team_id"], int(teams[0]["id"]))
        self.assertNotEqual(own["team_id"], int(teams[1]["id"]))

    def test_manual_end_voting_and_late_join_scope(self) -> None:
        """End Voting advances partial teams; late join never reopens discussion."""

        self._begin_and_join(3)
        assignments = [
            {"student_id": self.student_ids[0], "team_index": 0},
            {"student_id": self.student_ids[1], "team_index": 1},
            {"student_id": self.student_ids[2], "team_index": 1},
        ]
        self.school.setup_live_session_groups(
            self.session_id,
            n_teams=2,
            mode="manual",
            present_ids=self.student_ids[:3],
            assignments=assignments,
        )
        group_item = next(
            row
            for row in self.school.ensure_live_session_items(self.session_id)
            if row["item_id"] == "q-two"
        )
        group_item = self.school.publish_live_session_item(
            self.session_id,
            int(group_item["id"]),
            publish_mode="group_consensus",
        )
        self.school.join_live_class_session(
            self.session_id,
            self.student_ids[3],
            codename=str(self.students[3]["codename"]),
        )
        collecting_team_id = self.school.student_team_id_for_class(
            self.class_id, self.student_ids[3]
        )
        with self.school._lock:
            collecting_member = self.school.conn.execute(
                """
                SELECT 1 FROM live_group_members
                WHERE live_item_id = ? AND team_id = ? AND student_id = ?
                """,
                (
                    int(group_item["id"]),
                    int(collecting_team_id),
                    self.student_ids[3],
                ),
            ).fetchone()
        self.assertIsNotNone(collecting_member)
        self.school.submit_group_consensus_vote(
            self.session_id,
            int(group_item["id"]),
            self.student_ids[1],
            {"choice": "A"},
        )
        ended = self.school.end_group_consensus_voting(
            self.session_id, int(group_item["id"])
        )
        self.assertTrue(
            all(
                row["status"] in {"discussion", "awaiting_team_answer"}
                for row in ended["teams"]
            )
        )
        self.school.game.add_student(self.class_id, codename="Elm")
        elm = self.school.game.find_student_by_codename(self.class_id, "Elm")
        assert elm is not None
        self.school.join_live_class_session(
            self.session_id,
            int(elm["id"]),
            codename="Elm",
        )
        team_id = self.school.student_team_id_for_class(
            self.class_id, int(elm["id"])
        )
        with self.school._lock:
            membership = self.school.conn.execute(
                """
                SELECT 1 FROM live_group_members
                WHERE live_item_id = ? AND team_id = ? AND student_id = ?
                """,
                (int(group_item["id"]), int(team_id), int(elm["id"])),
            ).fetchone()
        self.assertIsNone(membership)
        late_state = self.school.student_group_consensus_state(
            group_item, int(elm["id"])
        )
        self.assertFalse(late_state["eligible"])
        self.assertFalse(late_state["can_finalize"])

    def test_late_join_uses_summed_course_scores_after_size(self) -> None:
        """Equal-size teams place a late joiner with the lower career sum."""

        first_two = self.student_ids[:2]
        self.school.game.begin_game(self.class_id)
        self.school.game.save_attendance(self.class_id, first_two)
        prior = self.school.game.assign_teams(
            self.class_id,
            2,
            "manual",
            assignments=[
                {"student_id": first_two[0], "team_index": 0},
                {"student_id": first_two[1], "team_index": 1},
            ],
        )
        self.school.game.rename_teams(
            self.class_id,
            [{"id": team["id"], "name": team["name"]} for team in prior["teams"]],
        )
        self.school.game.award_points(
            self.class_id,
            kind="student",
            target_id=first_two[0],
            amount=20,
        )
        self.school.game.award_points(
            self.class_id,
            kind="student",
            target_id=first_two[1],
            amount=5,
        )
        self.school.game.end_game(self.class_id)
        self.school.game.begin_game(self.class_id)
        for student in self.students[:2]:
            self.school.join_live_class_session(
                self.session_id,
                int(student["id"]),
                codename=str(student["codename"]),
            )
        setup = self.school.setup_live_session_groups(
            self.session_id,
            n_teams=2,
            mode="manual",
            present_ids=first_two,
            assignments=[
                {"student_id": first_two[0], "team_index": 0},
                {"student_id": first_two[1], "team_index": 1},
            ],
        )
        teams = setup["game"]["teams"]
        weaker_team = next(
            team
            for team in teams
            if any(int(member["id"]) == first_two[1] for member in team["members"])
        )
        self.school.game.rename_teams(
            self.class_id,
            [{"id": team["id"], "name": team["name"]} for team in teams],
        )
        self.school.join_live_class_session(
            self.session_id,
            self.student_ids[2],
            codename=str(self.students[2]["codename"]),
        )
        self.assertEqual(
            self.school.student_team_id_for_class(
                self.class_id, self.student_ids[2]
            ),
            int(weaker_team["id"]),
        )

    def test_numeric_catalogue_item_keeps_answer_kind(self) -> None:
        """Copied numeric questions publish a numeric prompt, not an empty MC."""

        numeric = {
            "id": "evaluate-f2",
            "ref": "test/question/evaluate-f2",
            "item_type": "question",
            "stage": "join",
            "page_number": 1,
            "order": 1,
            "type": "numeric",
            "text": "Type the integer",
            "options": [],
            "correct_answer": "-2",
            "integer_only": True,
            "placeholder": "Enter a number",
            "default_status": "inactive",
            "publish_modes": ["individual"],
            "response_mode": "individual",
        }
        self.school.live_class_metadata_for_session = lambda _sid: {
            **metadata_fixture(),
            "questions": [numeric],
            "items": [numeric],
        }
        items = self.school.ensure_live_session_items(self.session_id)
        row = next(item for item in items if item["item_id"] == "evaluate-f2")
        published = self.school.publish_live_session_item(
            self.session_id, int(row["id"]), publish_mode="individual"
        )
        prompt = self.school._prompt_for_live_item(published)
        self.assertIsNotNone(prompt)
        self.assertEqual(prompt["kind"], "numeric")
        payload = prompt.get("payload") or {}
        self.assertEqual(payload.get("kind"), "numeric")
        self.assertTrue(payload.get("integer_only"))
        self.assertEqual(payload.get("choices") or payload.get("options") or [], [])
        self.assertEqual(payload.get("key"), "-2")
        self.assertEqual(payload.get("correct_answer"), "-2")
        cards = self.school.live_session_question_cards(self.session_id)
        card = next(row for row in cards if row["id"] == "evaluate-f2")
        self.assertEqual(card["type"], "numeric")
        self.assertEqual(card["correct_answer"], "-2")

    def test_keyed_numeric_marks_matches_and_awards_correct(self) -> None:
        """A singular numeric key marks equivalents and awards only matches."""

        numeric = {
            "id": "evaluate-f2",
            "ref": "test/question/evaluate-f2",
            "item_type": "question",
            "stage": "join",
            "page_number": 1,
            "order": 1,
            "type": "numeric",
            "text": "Type the integer",
            "options": [],
            "correct_answer": "-2",
            "integer_only": True,
            "placeholder": "Enter a number",
            "default_status": "inactive",
            "publish_modes": ["individual"],
            "response_mode": "individual",
        }
        self.school.live_class_metadata_for_session = lambda _sid: {
            **metadata_fixture(),
            "questions": [numeric],
            "items": [numeric],
        }
        self.school.game.begin_game(self.class_id)
        for student in self.students:
            self.school.join_live_class_session(
                self.session_id,
                int(student["id"]),
                codename=str(student["codename"]),
            )
        items = self.school.ensure_live_session_items(self.session_id)
        row = next(item for item in items if item["item_id"] == "evaluate-f2")
        published = self.school.publish_live_session_item(
            self.session_id, int(row["id"]), publish_mode="individual"
        )
        prompt = self.school._prompt_for_live_item(published)
        self.assertIsNotNone(prompt)
        assert prompt is not None
        prompt_id = int(prompt["id"])
        payload = prompt.get("payload") or {}
        self.assertEqual(payload.get("key"), "-2")
        self.school.submit_live_prompt_response(
            prompt_id, self.student_ids[0], {"value": -2}
        )
        self.school.submit_live_prompt_response(
            prompt_id, self.student_ids[1], {"value": 3}
        )
        self.school.submit_live_prompt_response(
            prompt_id, self.student_ids[2], {"value": " -2 "}
        )
        self.school.submit_live_prompt_response(
            prompt_id, self.student_ids[3], {"value": -2.0}
        )
        roster = {
            int(row["student_id"]): row
            for row in self.school.live_prompt_response_roster(
                self.session_id, prompt_id
            )
            if row.get("student_id") not in (None, "")
        }
        self.assertTrue(roster[self.student_ids[0]]["correct"])
        self.assertFalse(roster[self.student_ids[1]]["correct"])
        self.assertTrue(roster[self.student_ids[2]]["correct"])
        self.assertTrue(roster[self.student_ids[3]]["correct"])
        awarded = self.school.award_live_prompt_points(
            self.session_id, prompt_id, mode="correct"
        )
        self.assertEqual(
            awarded["awarded_student_ids"],
            sorted(
                [self.student_ids[0], self.student_ids[2], self.student_ids[3]]
            ),
        )
        student_payload = self.school.student_live_items_payload(
            self.session_id, self.student_ids[0]
        )
        for item in student_payload.get("active_questions") or []:
            content = item.get("content") or {}
            prompt_body = (item.get("prompt") or {}).get("payload") or {}
            self.assertNotIn("correct_answer", content)
            self.assertNotIn("key", content)
            self.assertNotIn("correct_answer", prompt_body)
            self.assertNotIn("key", prompt_body)


    def test_light_state_skips_seed_and_heavy_fields(self) -> None:
        """Light /state lists attendees without seeding lifecycle rows."""

        calls = {"n": 0}
        original = self.school.ensure_live_session_items

        def wrapped(session_id: int):
            calls["n"] += 1
            return original(session_id)

        self.school.ensure_live_session_items = wrapped  # type: ignore[method-assign]
        light = self.school.get_live_session_state(self.session_id, light=True)
        self.assertEqual(calls["n"], 0)
        self.assertTrue(light["light"])
        self.assertIn("attendees", light)
        self.assertIn("teacher_state", light)
        self.assertIn("mc_tally", light)
        self.assertIn("state_seq", light)
        self.assertNotIn("question_cards", light)
        self.assertNotIn("live_metadata", light)
        self.assertNotIn("career_totals", light)
        self.assertNotIn("class_list", light)
        self.assertEqual(self.school.list_live_session_items(self.session_id), [])
        full = self.school.get_live_session_state(self.session_id)
        self.assertEqual(calls["n"], 1)
        self.assertFalse(full["light"])
        self.assertIn("question_cards", full)
        self.assertIn("class_list", full)
        second = self.school.get_live_session_state(self.session_id)
        self.assertEqual(calls["n"], 1)
        self.assertIn("question_cards", second)

    def test_full_state_seeds_publishable_page_cards(self) -> None:
        """First full snapshot gives Publish ids, options, and page numbers."""

        self.school.set_live_session_teacher_state(self.session_id, stage="round")
        self.assertEqual(self.school.list_live_session_items(self.session_id), [])
        full = self.school.get_live_session_state(self.session_id)
        cards = full["question_cards"]
        self.assertEqual([row["id"] for row in cards], ["q-one", "q-two"])
        for card in cards:
            self.assertIsNotNone(card.get("live_item_id"), card)
            self.assertEqual(card.get("page_number"), 1)
            self.assertEqual(card.get("options"), ["A", "B"])
            self.assertEqual(card.get("stage"), "round")
        self.assertTrue(full["live_items"])

    def test_round_question_submit_after_meet_is_not_meet_card(self) -> None:
        """Page-4 MCs stay submittable after Meet; leftover Meet cards do not."""

        self.school.live_class_metadata_for_session = self.original_metadata
        self.school.set_live_session_teacher_state(
            self.session_id, live_module="M1", live_slot="C2"
        )
        maple = self.student_ids[0]
        self.school.join_live_class_session(
            self.session_id, maple, codename="Aspen"
        )
        self.school.set_live_session_teacher_state(self.session_id, stage="meet")
        self.school.activate_meet_team_question(self.session_id)
        meet_state = self.school.student_live_items_payload(
            self.session_id, maple
        )
        self.assertTrue(
            any(
                str(row.get("item_id") or "").replace("_", "-") == "meet-team"
                for row in meet_state.get("active_questions") or []
            ),
            meet_state,
        )
        self.school.set_live_session_teacher_state(self.session_id, stage="round")
        items = self.school.ensure_live_session_items(self.session_id)
        parabola = next(
            row for row in items if str(row.get("item_id") or "") == "parabola-a"
        )
        self.school.publish_live_session_item(
            self.session_id, int(parabola["id"]), publish_mode="individual"
        )
        payload = self.school.student_live_items_payload(self.session_id, maple)
        active_ids = [
            str(row.get("item_id") or "")
            for row in payload.get("active_questions") or []
        ]
        self.assertNotIn("meet-team", active_ids)
        self.assertNotIn("meet_team", active_ids)
        self.assertIn("parabola-a", active_ids)
        card = next(
            row
            for row in payload["active_questions"]
            if row.get("item_id") == "parabola-a"
        )
        self.assertTrue(card.get("can_submit"), card)
        prompt_id = int((card.get("prompt") or {})["id"])
        saved = self.school.submit_live_prompt_response(
            prompt_id, maple, {"choice": "It's positive"}
        )
        self.assertEqual((saved.get("response") or {}).get("choice"), "It's positive")
        after = self.school.student_live_items_payload(self.session_id, maple)
        answered = next(
            row
            for row in after.get("active_questions") or []
            if row.get("item_id") == "parabola-a"
        )
        self.assertEqual(
            (answered.get("my_response") or {}).get("response", {}).get("choice"),
            "It's positive",
        )

    def test_join_question_submit_after_minds_on_is_not_minds_on_card(self) -> None:
        """Page-1 MCs stay submittable after leftover waiting-room minds-on."""

        self.school.live_class_metadata_for_session = self.original_metadata
        maple = self.student_ids[0]
        self.school.join_live_class_session(
            self.session_id, maple, codename="Aspen"
        )
        idle = self.school.student_live_prompt_payload(self.session_id, maple)
        self.assertEqual(
            ((idle.get("prompt") or {}).get("payload") or {}).get("item_id"),
            "minds_on",
            idle,
        )
        self.school.set_live_session_teacher_state(
            self.session_id, live_module="M1", live_slot="C2"
        )
        items = self.school.ensure_live_session_items(self.session_id)
        notation = next(
            row
            for row in items
            if str(row.get("item_id") or "") == "function-notation"
        )
        self.school.publish_live_session_item(
            self.session_id, int(notation["id"]), publish_mode="individual"
        )
        payload = self.school.student_live_items_payload(self.session_id, maple)
        active_ids = [
            str(row.get("item_id") or "")
            for row in payload.get("active_questions") or []
        ]
        self.assertNotIn("minds_on", active_ids)
        self.assertNotIn("minds-on", active_ids)
        self.assertIn("function-notation", active_ids)
        legacy = self.school.get_active_live_prompt(self.session_id)
        self.assertTrue(
            legacy is None
            or not is_minds_on_payload(legacy.get("payload")),
            legacy,
        )
        card = next(
            row
            for row in payload["active_questions"]
            if row.get("item_id") == "function-notation"
        )
        self.assertTrue(card.get("can_submit"), card)
        facing = self.school.student_live_prompt_payload(self.session_id, maple)
        facing_id = str(
            ((facing.get("prompt") or {}).get("payload") or {}).get("item_id") or ""
        )
        self.assertEqual(facing_id, "function-notation", facing)
        prompt_id = int((card.get("prompt") or {})["id"])
        choice = "the output of rule f when the input is x"
        saved = self.school.submit_live_prompt_response(
            prompt_id, maple, {"choice": choice}
        )
        self.assertEqual((saved.get("response") or {}).get("choice"), choice)
        after = self.school.student_live_items_payload(self.session_id, maple)
        answered = next(
            row
            for row in after.get("active_questions") or []
            if row.get("item_id") == "function-notation"
        )
        self.assertEqual(
            (answered.get("my_response") or {}).get("response", {}).get("choice"),
            choice,
        )
        numeric = next(
            row
            for row in items
            if str(row.get("item_id") or "") == "evaluate-f2"
        )
        self.school.publish_live_session_item(
            self.session_id, int(numeric["id"]), publish_mode="individual"
        )
        numeric_payload = self.school.student_live_items_payload(
            self.session_id, maple
        )
        numeric_card = next(
            row
            for row in numeric_payload["active_questions"]
            if row.get("item_id") == "evaluate-f2"
        )
        self.assertTrue(numeric_card.get("can_submit"), numeric_card)
        numeric_prompt_id = int((numeric_card.get("prompt") or {})["id"])
        self.assertNotEqual(numeric_prompt_id, prompt_id)
        saved_numeric = self.school.submit_live_prompt_response(
            numeric_prompt_id, maple, {"value": -2}
        )
        self.assertEqual((saved_numeric.get("response") or {}).get("value"), -2)
        after_numeric = self.school.student_live_items_payload(
            self.session_id, maple
        )
        answered_numeric = next(
            row
            for row in after_numeric.get("active_questions") or []
            if row.get("item_id") == "evaluate-f2"
        )
        self.assertEqual(
            (answered_numeric.get("my_response") or {})
            .get("response", {})
            .get("value"),
            -2,
        )

    def _sample_response_for_live_item(self, item: dict[str, Any]) -> dict[str, Any]:
        """Build a student-submittable payload for one playlist item."""

        content = item.get("item") if isinstance(item.get("item"), dict) else {}
        prompt = item.get("prompt") if isinstance(item.get("prompt"), dict) else {}
        payload = (
            prompt.get("payload") if isinstance(prompt.get("payload"), dict) else {}
        )
        kind = str(
            prompt.get("kind") or content.get("type") or payload.get("kind") or "mc"
        ).lower()
        options = (
            payload.get("choices")
            or payload.get("options")
            or content.get("options")
            or content.get("choices")
            or []
        )
        if (
            kind == "numeric"
            or content.get("integer_only")
            or payload.get("integer_only")
        ):
            return {"value": -2}
        if options:
            first = options[0]
            if isinstance(first, str):
                return {"choice": first}
            if isinstance(first, dict):
                return {
                    "choice": str(
                        first.get("label") or first.get("text") or first.get("choice") or ""
                    )
                }
        return {"text": "sample"}

    def _walk_playlist_submits(self, course: str, module: str, slot: str) -> None:
        """Publish every student-answerable playlist item and record its prompt."""

        maple = self.student_ids[0]
        self.school.join_live_class_session(
            self.session_id, maple, codename="Aspen"
        )
        self.school.student_live_prompt_payload(self.session_id, maple)
        self.school.live_class_metadata_for_session = lambda _sid: (
            load_live_class_metadata(course, module, slot)
        )
        self.school.set_live_session_teacher_state(
            self.session_id, live_module=module, live_slot=slot
        )
        items = [
            row
            for row in self.school.ensure_live_session_items(self.session_id)
            if str(row.get("kind") or "").strip().lower()
            not in {"media", "whiteboard", "slides"}
            and str((row.get("item") or {}).get("item_type") or "").strip().lower()
            not in {"media", "whiteboard", "slides"}
        ]
        self.assertTrue(items, f"{course} {module} {slot} has no answerable items")
        leftover = {"minds_on", "minds-on", "meet_team", "meet-team"}
        recorded: list[str] = []
        for row in items:
            item_id = str(row.get("item_id") or "")
            stage = str(row.get("stage") or "join").strip().lower() or "join"
            self.school.set_live_session_teacher_state(self.session_id, stage=stage)
            published = self.school.publish_live_session_item(
                self.session_id, int(row["id"]), publish_mode="individual"
            )
            payload = self.school.student_live_items_payload(self.session_id, maple)
            card = next(
                (
                    item
                    for item in payload.get("active_questions") or []
                    if str(item.get("item_id") or "") == item_id
                ),
                None,
            )
            self.assertIsNotNone(card, f"{item_id} missing from {payload}")
            assert card is not None
            self.assertTrue(card.get("can_submit"), card)
            prompt_id = int((card.get("prompt") or {})["id"])
            facing = self.school.student_live_prompt_payload(self.session_id, maple)
            facing_id = str(
                ((facing.get("prompt") or {}).get("payload") or {}).get("item_id")
                or ""
            ).replace("-", "_")
            if item_id.replace("-", "_") not in leftover:
                self.assertNotEqual(facing_id, "minds_on", facing)
            response = self._sample_response_for_live_item({**published, **card})
            if item_id.replace("-", "_") in {"meet_team"}:
                self.school.record_meet_chain_pick(
                    self.session_id,
                    student_id=maple,
                    choice=str(response.get("choice") or ""),
                )
            saved = self.school.submit_live_prompt_response(
                prompt_id, maple, response
            )
            after = self.school.student_live_items_payload(self.session_id, maple)
            answered = next(
                item
                for item in after.get("active_questions") or []
                if str(item.get("item_id") or "") == item_id
            )
            mine = (answered.get("my_response") or {}).get("response") or {}
            if "value" in response:
                self.assertEqual(mine.get("value"), response["value"], answered)
                self.assertEqual((saved.get("response") or {}).get("value"), response["value"])
            elif "choice" in response:
                self.assertEqual(mine.get("choice"), response["choice"], answered)
            recorded.append(item_id)
        self.assertEqual(recorded, [str(row.get("item_id") or "") for row in items])

    def test_playlist_submit_records_facing_item_mcf3m_m1_c2(self) -> None:
        """Every MCF3M M1 C2 student-answerable item records its own prompt."""

        self._walk_playlist_submits("MCF3M", "M1", "C2")

    def test_playlist_submit_records_facing_item_mcr3u_m1_c2(self) -> None:
        """Every MCR3U M1 C2 student-answerable item records its own prompt."""

        self._walk_playlist_submits("MCR3U", "M1", "C2")

    def test_student_poll_unchanged_when_seq_and_stamp_match(self) -> None:
        """Matching seq/stamp skips the heavy student payload."""

        stamp = self.school.live_student_poll_stamp(
            self.session_id, self.class_id
        )
        teacher = self.school.live_session_teacher_state_payload(self.session_id)
        seq = int(teacher.get("state_seq") or 0)
        hit = self.school.student_live_poll_unchanged(
            self.session_id, self.class_id, seq, stamp
        )
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertTrue(hit["unchanged"])
        self.assertEqual(hit["state_seq"], seq)
        self.assertEqual(hit["stamp"], stamp)
        missed = self.school.student_live_poll_unchanged(
            self.session_id, self.class_id, seq + 1, stamp
        )
        self.assertIsNone(missed)

    def test_light_poll_reports_lifecycle_response_counts(self) -> None:
        """Light staff polls expose per-item answer counts after student submit."""

        self.school.live_class_metadata_for_session = self.original_metadata
        maple = self.student_ids[0]
        self.school.join_live_class_session(
            self.session_id, maple, codename="Aspen"
        )
        self.school.set_live_session_teacher_state(
            self.session_id, live_module="M1", live_slot="C2", stage="join"
        )
        items = self.school.ensure_live_session_items(self.session_id)
        notation = next(
            row
            for row in items
            if str(row.get("item_id") or "") == "function-notation"
        )
        self.school.publish_live_session_item(
            self.session_id, int(notation["id"]), publish_mode="individual"
        )
        before = self.school.get_live_session_state(self.session_id, light=True)
        self.assertEqual(
            before.get("lifecycle_response_counts", {}).get(int(notation["id"])),
            0,
        )
        card = self.school.student_live_items_payload(self.session_id, maple)
        prompt_id = int((card["active_questions"][0].get("prompt") or {})["id"])
        self.school.submit_live_prompt_response(
            prompt_id, maple, {"choice": "the output of rule f when the input is x"}
        )
        after = self.school.get_live_session_state(self.session_id, light=True)
        self.assertEqual(
            after.get("lifecycle_response_counts", {}).get(int(notation["id"])),
            1,
            after,
        )

    def test_student_payload_includes_results_after_submit(self) -> None:
        """Lifecycle cards receive class results once the student has answered."""

        self.school.live_class_metadata_for_session = self.original_metadata
        maple = self.student_ids[0]
        self.school.join_live_class_session(
            self.session_id, maple, codename="Aspen"
        )
        self.school.set_live_session_teacher_state(
            self.session_id, live_module="M1", live_slot="C2", stage="join"
        )
        items = self.school.ensure_live_session_items(self.session_id)
        notation = next(
            row
            for row in items
            if str(row.get("item_id") or "") == "function-notation"
        )
        self.school.publish_live_session_item(
            self.session_id, int(notation["id"]), publish_mode="individual"
        )
        stamp_before = self.school.live_student_poll_stamp(
            self.session_id, self.class_id
        )
        card = self.school.student_live_items_payload(self.session_id, maple)
        question = card["active_questions"][0]
        self.assertIsNone(question.get("my_response"))
        self.assertIsNone(question.get("results"))
        prompt_id = int(question["prompt"]["id"])
        self.school.submit_live_prompt_response(
            prompt_id,
            maple,
            {"choice": "the output of rule f when the input is x"},
        )
        stamp_after = self.school.live_student_poll_stamp(
            self.session_id, self.class_id
        )
        self.assertNotEqual(stamp_before, stamp_after)
        after = self.school.student_live_items_payload(self.session_id, maple)
        row = after["active_questions"][0]
        self.assertIsNotNone(row.get("my_response"))
        results = row.get("results") or {}
        choices = results.get("choices") or []
        self.assertTrue(choices, results)
        self.assertGreaterEqual(
            sum(int(c.get("count") or 0) for c in choices), 1
        )


class LiveBackendApiGuardTests(unittest.TestCase):
    """Verify ownership and active-session guards on new publish APIs."""

    def setUp(self) -> None:
        """Create an authenticated owner and a second unauthorized teacher."""

        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.owner = self.school.register_staff("owner@gmail.com")
        self.other = self.school.register_staff("other@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(self.owner["id"]), ontario_code="MCF3M"
        )
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCF3M",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Aspen", "Birch"],
            offering_id=int(offering["id"]),
            teacher_user_id=int(self.owner["id"]),
        )
        self.class_id = int(created["id"])
        live = self.school.start_live_class_session(
            self.class_id, int(self.owner["id"])
        )
        self.session_id = int(live["id"])
        self.school.live_class_metadata_for_session = lambda _session_id: (
            metadata_fixture()
        )
        self.item_id = int(
            next(
                row
                for row in self.school.ensure_live_session_items(self.session_id)
                if row["item_id"] == "q-one"
            )["id"]
        )
        self.owner_client = self.app.test_client()
        self.other_client = self.app.test_client()
        self._login(self.owner_client, "owner@gmail.com")
        self._login(self.other_client, "other@gmail.com")

    def tearDown(self) -> None:
        """Close the database and temporary directory."""

        self.school.close()
        self.tmp.cleanup()

    def _login(self, client: Any, email: str) -> None:
        """Complete the local staff login flow for one test client."""

        client.get("/auth/google?portal=staff")
        client.get(f"/auth/google/callback?email={email}&name=T")
        client.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email(email)[
                    "verification_code"
                ]
            },
        )

    def test_publish_permissions_and_ended_session_guard(self) -> None:
        """Non-owner receives 403 and ended sessions receive 409."""

        path = (
            f"/api/live-sessions/{self.session_id}/items/"
            f"{self.item_id}/publish"
        )
        forbidden = self.other_client.post(
            path, json={"publish_mode": "individual"}
        )
        self.assertEqual(forbidden.status_code, 403)
        published = self.owner_client.post(
            path, json={"publish_mode": "individual"}
        )
        self.assertEqual(published.status_code, 200, published.get_json())
        self.school.end_live_class_session(
            self.session_id, clear_moods=False
        )
        ended = self.owner_client.post(
            path, json={"publish_mode": "individual"}
        )
        self.assertEqual(ended.status_code, 409, ended.get_json())


class LiveBackendSchemaTests(unittest.TestCase):
    """Verify new lifecycle schema creation is migration-safe and idempotent."""

    def test_schema_recreates_missing_tables_and_reopens_twice(self) -> None:
        """Dropping new tables simulates an old DB and repeated boot repairs it."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "lloves.sqlite"
            app = create_app(db_path=path, data_dir=root, testing=True)
            app.config["SCHOOL_DB"].close()
            conn = sqlite3.connect(path)
            conn.executescript(
                """
                DROP TABLE live_group_votes;
                DROP TABLE live_group_members;
                DROP TABLE live_group_responses;
                DROP TABLE live_session_items;
                """
            )
            conn.close()
            for _ in range(2):
                reopened = create_app(
                    db_path=path, data_dir=root, testing=True
                )
                school = reopened.config["SCHOOL_DB"]
                names = {
                    row["name"]
                    for row in school.conn.execute(
                        """
                        SELECT name FROM sqlite_master
                        WHERE type = 'table' AND name LIKE 'live_%'
                        """
                    ).fetchall()
                }
                self.assertIn("live_session_items", names)
                self.assertIn("live_group_votes", names)
                self.assertIn("live_group_members", names)
                self.assertIn("live_group_responses", names)
                school.close()



if __name__ == "__main__":
    unittest.main(verbosity=2)

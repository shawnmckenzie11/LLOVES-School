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
        self.assertIsNone(before["prompt"])
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
        self.assertIsNone(after["prompt"])
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

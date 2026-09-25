#!/usr/bin/env python3
"""Focused backend tests for global live controls, publishing, and groups."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from collections import Counter
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
from school_db import json_safe  # noqa: E402
from teams_spark import TEAMS_SPARK_PROMPT, TEAMS_SPARK_SLIDE_INDEX  # noqa: E402


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
        self.assertIsNone(before.get("prompt"))

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
        self.assertIsNotNone(after.get("prompt"))
        active_cards = self.school.live_session_question_cards(self.session_id)
        self.assertEqual(active_cards[0]["status"], "active")

    def test_unpublished_teams_spark_stays_off_student_until_publish(self) -> None:
        """TEAMS spark is seeded inactive and hidden until staff Publish."""

        self.school.live_class_metadata_for_session = self.original_metadata
        self.school.set_live_session_teacher_state(self.session_id, stage="teams")
        self.school.ensure_teams_spark(self.session_id)
        spark_row = self.school._prompt_at_slide(
            self.session_id, int(TEAMS_SPARK_SLIDE_INDEX)
        )
        if spark_row is not None:
            self.assertFalse(bool(spark_row.get("active")))
        before = self.school.student_live_prompt_payload(
            self.session_id, self.student_ids[0]
        )
        self.assertIsNone(before.get("prompt"))
        self.assertEqual(before["active_questions"], [])
        item = next(
            row
            for row in self.school.ensure_live_session_items(self.session_id)
            if str(row.get("item_id") or "").replace("_", "-") == "teams-spark"
        )
        self.school.publish_live_session_item(
            self.session_id, int(item["id"]), publish_mode="individual"
        )
        after = self.school.student_live_prompt_payload(
            self.session_id, self.student_ids[0]
        )
        self.assertIsNotNone(after.get("prompt"))
        self.assertEqual(
            str(after["prompt"]["payload"].get("item_id") or "").replace("_", "-"),
            "teams-spark",
        )
        self.assertTrue(
            any(
                str(row.get("item_id") or "").replace("_", "-") == "teams-spark"
                for row in after["active_questions"]
            )
        )

    def test_mcr3u_c4_page2_spark_follows_teacher_publish(self) -> None:
        """MCR3U M1 C4 page 2 does not leave a typeable integer prompt behind.

        Closing the spark, or leaving Welcome while it is still active,
        drops the facing student prompt. Save to card still keeps the
        right-panel card when the teacher turns that flag on.
        """

        def mcr3u_c4(_session_id: int) -> dict[str, Any]:
            """Bind the MCR3U M1 C4 deck even though the fixture class is MCF3M."""

            return self.school._merged_live_class_metadata(
                self.class_id, "MCR3U", "M1", "C4", fresh=True
            )

        self.school.live_class_metadata_for_session = mcr3u_c4
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M1",
            live_slot="C4",
            stage="teams",
            page_id="welcome",
            student_view={
                "questions": "student",
                "media": "none",
                "canvas": "none",
                "slides": "none",
            },
        )
        meta = self.school.live_class_metadata_for_session(self.session_id)
        self.assertEqual(str(meta.get("course") or ""), "MCR3U")
        self.school.ensure_live_session_items(self.session_id)
        student_id = self.student_ids[0]
        hidden = self.school.student_live_prompt_payload(self.session_id, student_id)
        self.assertIsNone(hidden.get("prompt"))
        self.assertNotIn(TEAMS_SPARK_PROMPT, json.dumps(hidden.get("prompt")))
        item = next(
            row
            for row in self.school.list_live_session_items(self.session_id)
            if str(row.get("item_id") or "").replace("_", "-") == "teams-spark"
        )
        self.school.publish_live_session_item(
            self.session_id, int(item["id"]), publish_mode="individual"
        )
        shown = self.school.student_live_prompt_payload(self.session_id, student_id)
        self.assertEqual(
            (shown.get("prompt") or {}).get("payload", {}).get("prompt"),
            TEAMS_SPARK_PROMPT,
        )
        self.school.update_live_session_item_settings(
            self.session_id, int(item["id"]), save_to_card=True
        )
        self.school.set_live_session_teacher_state(self.session_id, page_id="round_1")
        off_page = self.school.student_live_prompt_payload(self.session_id, student_id)
        self.assertIsNone(off_page.get("prompt"))
        self.assertFalse(
            any(
                str(row.get("item_id") or "").replace("_", "-") == "teams-spark"
                for row in off_page.get("active_questions") or []
            )
        )
        parked_live = next(
            row
            for row in off_page.get("saved_cards") or []
            if str(row.get("item_id") or "").replace("_", "-") == "teams-spark"
        )
        self.assertTrue(parked_live.get("parked"))
        self.assertFalse(parked_live.get("can_submit"))
        self.assertFalse(
            any(
                str(row.get("id") or "").replace("_", "-") == "teams-spark"
                for row in self.school.live_session_question_cards(self.session_id)
            )
        )
        self.school.set_live_session_teacher_state(self.session_id, page_id="welcome")
        self.school.close_live_session_item(self.session_id, int(item["id"]))
        closed = self.school.student_live_prompt_payload(self.session_id, student_id)
        self.assertIsNone(closed.get("prompt"))
        self.assertFalse(
            any(
                str(row.get("item_id") or "").replace("_", "-") == "teams-spark"
                and row.get("can_submit")
                for row in (closed.get("active_questions") or [])
                + (closed.get("closed_results") or [])
            )
        )
        self.school.set_live_session_teacher_state(
            self.session_id, stage="teams", page_id="welcome"
        )
        cards = self.school.live_session_question_cards(self.session_id)
        spark_cards = [
            row
            for row in cards
            if str(row.get("id") or "").replace("_", "-") == "teams-spark"
        ]
        self.assertTrue(spark_cards)
        self.assertEqual(spark_cards[0]["status"], "closed")
        self.school.update_live_session_item_settings(
            self.session_id, int(item["id"]), save_to_card=True
        )
        self.school.set_live_session_teacher_state(
            self.session_id, stage="meet", page_id="meet"
        )
        parked = self.school.student_live_prompt_payload(self.session_id, student_id)
        self.assertNotEqual(
            (parked.get("prompt") or {}).get("payload", {}).get("item_id"),
            "teams-spark",
        )
        self.assertFalse(
            any(
                str(row.get("item_id") or "").replace("_", "-") == "teams-spark"
                for row in parked.get("active_questions") or []
            )
        )
        saved_spark = next(
            row
            for row in parked.get("saved_cards") or []
            if str(row.get("item_id") or "").replace("_", "-") == "teams-spark"
        )
        self.assertTrue(saved_spark.get("save_to_card"))
        self.assertFalse(saved_spark.get("can_submit"))
        teacher_off_page = [
            row
            for row in self.school.live_session_question_cards(self.session_id)
            if str(row.get("id") or "").replace("_", "-") == "teams-spark"
        ]
        self.assertEqual(teacher_off_page, [])

    def test_unpublished_catalogue_item_stays_off_student_until_publish(self) -> None:
        """A fixture catalogue question stays off the student prompt until Publish."""

        items = self.school.ensure_live_session_items(self.session_id)
        q1 = next(row for row in items if row["item_id"] == "q-one")
        self.school.set_live_session_teacher_state(
            self.session_id,
            stage="round",
            student_view={"questions": "student"},
        )
        before = self.school.student_live_prompt_payload(
            self.session_id, self.student_ids[0]
        )
        self.assertIsNone(before.get("prompt"))
        self.assertEqual(before["active_questions"], [])
        self.school.publish_live_session_item(
            self.session_id, int(q1["id"]), publish_mode="individual"
        )
        after = self.school.student_live_prompt_payload(
            self.session_id, self.student_ids[0]
        )
        self.assertIsNotNone(after.get("prompt"))
        self.assertEqual(after["active_questions"][0]["item_id"], "q-one")

    def test_show_live_results_off_hides_student_tally_until_closed(self) -> None:
        """Unchecked Show live results omits student results and mc_tally."""

        items = self.school.ensure_live_session_items(self.session_id)
        q1 = next(row for row in items if row["item_id"] == "q-one")
        self.school.set_live_session_teacher_state(
            self.session_id,
            stage="round",
            student_view={"questions": "student"},
        )
        published = self.school.publish_live_session_item(
            self.session_id, int(q1["id"]), publish_mode="individual"
        )
        prompt = self.school._prompt_for_live_item(published)
        assert prompt is not None
        self.school.submit_live_prompt_response(
            int(prompt["id"]), self.student_ids[0], {"choice": "A"}
        )
        visible = self.school.student_live_prompt_payload(
            self.session_id, self.student_ids[0]
        )
        self.assertIsNotNone(visible.get("mc_tally"))
        self.assertIsNotNone(visible["active_questions"][0].get("results"))
        self.school.update_live_session_item_settings(
            self.session_id, int(q1["id"]), show_live_results=False
        )
        hidden = self.school.student_live_prompt_payload(
            self.session_id, self.student_ids[0]
        )
        self.assertNotIn("mc_tally", hidden)
        self.assertIsNone(hidden["active_questions"][0].get("results"))
        self.school.update_live_session_item_settings(
            self.session_id, int(q1["id"]), show_live_results=True
        )
        shown = self.school.student_live_prompt_payload(
            self.session_id, self.student_ids[0]
        )
        self.assertIsNotNone(shown.get("mc_tally"))
        self.assertIsNotNone(shown["active_questions"][0].get("results"))

    def test_move_resets_unpublished_and_clears_responses(self) -> None:
        """Moving a published question unpublishes it and clears answers, not points."""

        self.school.live_class_metadata_for_session = self.original_metadata
        maple = self.student_ids[0]
        self.school.game.begin_game(self.class_id)
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
        published = self.school.publish_live_session_item(
            self.session_id, int(notation["id"]), publish_mode="individual"
        )
        prompt = self.school._prompt_for_live_item(published)
        assert prompt is not None
        self.school.submit_live_prompt_response(
            int(prompt["id"]),
            maple,
            {"choice": "the output of rule f when the input is x"},
        )
        self.school.award_live_prompt_points(
            self.session_id,
            int(prompt["id"]),
            mode="manual",
            student_ids=[maple],
            amount=2,
        )
        before_points = self.school.live_awarded_session_points(self.class_id)
        self.assertEqual(before_points.get(maple), 2)
        moved = self.school.move_class_playlist_item(
            self.class_id,
            "M1",
            "C2",
            "function-notation",
            target_page_index=2,
        )
        self.assertEqual(moved["action"], "moved")
        after_points = self.school.live_awarded_session_points(self.class_id)
        self.assertEqual(after_points.get(maple), 2)
        lifecycle = next(
            row
            for row in self.school.list_live_session_items(self.session_id)
            if str(row.get("item_id") or "") == "function-notation"
        )
        self.assertEqual(lifecycle["status"], "inactive")
        self.assertIsNone(lifecycle.get("published_at"))
        linked = self.school._prompt_for_live_item(lifecycle)
        if linked is not None:
            self.assertFalse(bool(linked.get("active")))
            self.assertEqual(
                self.school.list_live_prompt_responses(int(linked["id"])),
                [],
            )
        student = self.school.student_live_prompt_payload(self.session_id, maple)
        self.assertIsNone(student.get("prompt"))
        self.assertFalse(
            any(
                str(row.get("item_id") or "") == "function-notation"
                for row in student.get("active_questions") or []
            )
        )

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
        self.assertEqual(len(roster), 4)
        self.assertEqual(sum(1 for row in roster if row["present"]), 2)
        self.assertEqual(self.school.live_group_projection(self.session_id), [])
        self.assertIsNone(
            self.school.live_scoreboard_projection(self.session_id)
        )

    def test_scoreboard_visible_survives_normalize_before_groups(self) -> None:
        """Stored and patched scoreboard_visible stay on before groups exist."""

        from live_teacher_state import apply_teacher_state_update, public_teacher_state

        stored = public_teacher_state(
            {"groups_configured": False, "scoreboard_visible": True}
        )
        self.assertFalse(stored["groups_configured"])
        self.assertTrue(stored["scoreboard_visible"])
        patched = apply_teacher_state_update(None, scoreboard_visible=True)
        self.assertFalse(patched["groups_configured"])
        self.assertTrue(patched["scoreboard_visible"])
        self.school.set_live_session_teacher_state(
            self.session_id, scoreboard_visible=True
        )
        before = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertFalse(before["groups_configured"])
        self.assertTrue(before["scoreboard_visible"])

    def test_setup_groups_keeps_explicit_scoreboard_false(self) -> None:
        """Set Up / assign leaves scoreboard_visible false when staff unchecks it."""

        self._begin_and_join(2)
        result = self.school.setup_live_session_groups(
            self.session_id,
            n_teams=2,
            mode="balanced",
            present_ids=self.student_ids,
            scoreboard_visible=False,
        )
        self.assertTrue(result["teacher_state"]["groups_configured"])
        self.assertFalse(result["teacher_state"]["scoreboard_visible"])
        self.assertIsNone(self.school.live_scoreboard_projection(self.session_id))

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
        self.assertEqual(tied["team"]["member_answers"], ["A", "B"])
        own_mid = self.school.student_group_consensus_state(
            group_item, first_ids[0]
        )
        self.assertEqual(own_mid["member_answers"], ["A", "A"])
        self.assertNotIn("B", own_mid["member_answers"])
        live_payload = self.school.student_live_items_payload(
            self.session_id, first_ids[0]
        )
        live_card = next(
            row
            for row in live_payload["active_questions"]
            if row["item_id"] == "q-two"
        )
        self.assertEqual(
            [row["team_id"] for row in live_card["results"]["team_answers"]],
            [int(teams[0]["id"])],
        )
        self.assertEqual(live_card["results"]["class_distribution"], [])
        self.assertEqual(live_card["results"]["member_answers"], ["A", "A"])
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
        for team in summary["teams"]:
            self.assertGreaterEqual(int(team["eligible_count"]), 1)
            self.assertTrue(team["member_answers"])
        own = self.school.student_group_consensus_state(
            group_item, first_ids[0]
        )
        self.assertEqual(own["team_id"], int(teams[0]["id"]))
        self.assertNotEqual(own["team_id"], int(teams[1]["id"]))
        self.assertEqual(own["member_answers"], ["A", "A"])
        self.assertNotIn("B", own["member_answers"])

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

    def test_numeric_item_accepts_publish_mode_group_alias(self) -> None:
        """Open-ended numeric items accept publish_mode=group as group_consensus."""

        def numeric_fixture() -> dict[str, Any]:
            """Return the shared fixture plus an integer-only numeric item."""

            data = metadata_fixture()
            numeric = {
                "id": "q-numeric",
                "ref": "test/question/q-numeric",
                "item_type": "question",
                "stage": "round",
                "page_number": 1,
                "order": 3,
                "type": "numeric",
                "text": "Type the integer you think most students will answer",
                "options": [],
                "integer_only": True,
                "default_status": "inactive",
                "publish_modes": ["individual"],
                "response_mode": "individual",
            }
            questions = [*data["questions"], numeric]
            media = next(
                row for row in data["items"] if row.get("item_type") == "media"
            )
            return {
                **data,
                "questions": questions,
                "items": [*questions, media],
            }

        self.school.live_class_metadata_for_session = (
            lambda _session_id: numeric_fixture()
        )
        self._begin_and_join(4)
        self._setup_groups()
        numeric_item = next(
            row
            for row in self.school.ensure_live_session_items(self.session_id)
            if row["item_id"] == "q-numeric"
        )
        published = self.school.publish_live_session_item(
            self.session_id,
            int(numeric_item["id"]),
            publish_mode="group",
        )
        self.assertEqual(published["response_mode"], "group_consensus")
        self.assertEqual(published["publish_mode"], "group_consensus")

    def test_numeric_individual_in_group_privacy_reveal_and_atomic_team_answer(
        self,
    ) -> None:
        """Walk a numeric item through private votes, reveal, and one team answer."""

        def numeric_fixture() -> dict[str, Any]:
            """Return the shared fixture plus an integer-only numeric item."""

            data = metadata_fixture()
            numeric = {
                "id": "q-numeric",
                "ref": "test/question/q-numeric",
                "item_type": "question",
                "stage": "round",
                "page_number": 1,
                "order": 3,
                "type": "numeric",
                "text": "Type the integer you think most students will answer",
                "options": [],
                "integer_only": True,
                "default_status": "inactive",
                "publish_modes": ["individual"],
                "response_mode": "individual",
            }
            questions = [*data["questions"], numeric]
            media = next(
                row for row in data["items"] if row.get("item_type") == "media"
            )
            return {
                **data,
                "questions": questions,
                "items": [*questions, media],
            }

        self.school.live_class_metadata_for_session = (
            lambda _session_id: numeric_fixture()
        )
        self._begin_and_join(4)
        self._setup_groups()
        numeric_item = next(
            row
            for row in self.school.ensure_live_session_items(self.session_id)
            if row["item_id"] == "q-numeric"
        )
        published = self.school.publish_live_session_item(
            self.session_id,
            int(numeric_item["id"]),
            publish_mode="individual_in_group",
        )
        self.assertEqual(published["response_mode"], "group_consensus")
        self.assertEqual(published["publish_mode"], "group_consensus")
        teams = self.school.game.game_state(self.class_id)["teams"]
        first_ids = [int(row["id"]) for row in teams[0]["members"]]
        second_ids = [int(row["id"]) for row in teams[1]["members"]]
        first_vote = self.school.submit_group_consensus_vote(
            self.session_id,
            int(published["id"]),
            first_ids[0],
            {"value": 12},
        )
        self.assertEqual(first_vote["team"]["status"], "collecting_votes")
        self.assertNotIn("member_answers", first_vote["team"])
        self.assertNotIn("vote_summary", first_vote["team"])
        advanced = self.school.submit_group_consensus_vote(
            self.session_id,
            int(published["id"]),
            first_ids[1],
            {"value": 16},
        )
        self.assertIn(
            advanced["team"]["status"],
            {"discussion", "awaiting_team_answer"},
        )
        self.assertEqual(advanced["team"]["member_answers"], [12, 16])
        self.assertNotIn(20, advanced["team"]["member_answers"])
        self.assertNotIn(7, advanced["team"]["member_answers"])
        self.school.submit_group_consensus_vote(
            self.session_id,
            int(published["id"]),
            second_ids[0],
            {"value": 20},
        )
        team_two = self.school.submit_group_consensus_vote(
            self.session_id,
            int(published["id"]),
            second_ids[1],
            {"value": 7},
        )
        self.assertEqual(team_two["team"]["member_answers"], [20, 7])
        self.assertNotIn(12, team_two["team"]["member_answers"])
        self.assertNotIn(16, team_two["team"]["member_answers"])
        own = self.school.student_group_consensus_state(
            published, first_ids[0]
        )
        self.assertEqual(own["member_answers"], [12, 16])
        self.assertNotIn(20, own["member_answers"])
        self.assertNotIn(7, own["member_answers"])
        live_payload = self.school.student_live_items_payload(
            self.session_id, first_ids[0]
        )
        live_card = next(
            row
            for row in live_payload["active_questions"]
            if row["item_id"] == "q-numeric"
        )
        self.assertEqual(
            [row["team_id"] for row in live_card["results"]["team_answers"]],
            [int(teams[0]["id"])],
        )
        self.assertEqual(live_card["results"]["class_distribution"], [])
        self.assertEqual(live_card["results"]["member_answers"], [12, 16])
        finalized = self.school.finalize_group_consensus_answer(
            self.session_id,
            int(published["id"]),
            first_ids[0],
            {"value": 16},
        )
        self.assertEqual(finalized["status"], "finalized")
        self.assertEqual(finalized["final_answer"], {"kind": "value", "value": 16})
        with self.assertRaisesRegex(ValueError, "already finalized"):
            self.school.finalize_group_consensus_answer(
                self.session_id,
                int(published["id"]),
                first_ids[1],
                {"value": 12},
            )
        team_two_final = self.school.finalize_group_consensus_answer(
            self.session_id,
            int(published["id"]),
            second_ids[0],
            {"value": 20},
        )
        self.assertEqual(team_two_final["status"], "finalized")
        summary = self.school.teacher_group_consensus_summary(
            self.session_id, int(published["id"])
        )
        self.assertEqual(len(summary["teams"]), 2)
        by_id = {int(row["team_id"]): row for row in summary["teams"]}
        self.assertEqual(by_id[int(teams[0]["id"])]["member_answers"], [12, 16])
        self.assertEqual(by_id[int(teams[1]["id"])]["member_answers"], [20, 7])
        for team in summary["teams"]:
            self.assertGreaterEqual(int(team["eligible_count"]), 1)

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
        self.assertIn("groups", light)
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
        self.assertIsNone(idle.get("prompt"), idle)
        self.assertEqual(idle.get("active_questions"), [])
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

    def test_show_live_results_and_save_to_card_before_publish(self) -> None:
        """Teacher can set both flags on an inactive card, and Save to card sticks."""

        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M1",
            live_slot="C1",
            stage="round",
            student_view={"questions": "student"},
        )
        items = self.school.ensure_live_session_items(self.session_id)
        q1 = next(row for row in items if row["item_id"] == "q-one")
        self.assertEqual(q1["status"], "inactive")
        self.assertFalse(q1["save_to_card"])
        hidden = self.school.update_live_session_item_settings(
            self.session_id, int(q1["id"]), show_live_results=False
        )
        self.assertEqual(hidden["status"], "inactive")
        self.assertFalse(hidden["show_live_results"])
        saved = self.school.update_live_session_item_settings(
            self.session_id, int(q1["id"]), save_to_card=True
        )
        self.assertTrue(saved["save_to_card"])
        self.assertFalse(saved["show_live_results"])
        overrides = self.school.list_class_playlist_item_overrides(
            self.class_id, "M1", "C1"
        )
        self.assertTrue(
            any(
                row.get("item_id") == "q-one" and int(row.get("save_to_card") or 0) == 1
                for row in overrides
            )
        )
        published = self.school.publish_live_session_item(
            self.session_id, int(q1["id"]), publish_mode="individual"
        )
        self.assertTrue(published["save_to_card"])
        self.assertFalse(published["show_live_results"])
        self.school.set_live_session_teacher_state(
            self.session_id, stage="summary"
        )
        payload = self.school.student_live_items_payload(
            self.session_id, self.student_ids[0]
        )
        card = next(
            row for row in payload["saved_cards"] if row["item_id"] == "q-one"
        )
        self.assertTrue(card["save_to_card"])
        self.assertFalse(card["can_submit"])
        self.assertTrue(card.get("parked"))
        self.assertFalse(
            any(row.get("item_id") == "q-one" for row in payload["active_questions"])
        )

    def test_teacher_settings_survive_deck_refresh_and_page_change(self) -> None:
        """Catalogue defaults must not wipe teacher settings on /state or page changes.

        Save to card, Show Live Results, Individual vs Group, and whiteboard
        Shared within Group stay on the lifecycle row for bank and seed items.
        """

        bank = {
            "id": "bank-import-41",
            "ref": "bank/import/41",
            "item_type": "question",
            "stage": "round",
            "page_number": 1,
            "order": 3,
            "type": "mc",
            "text": "Bank pick",
            "options": ["A", "B"],
            "correct_answer": "A",
            "publish_modes": ["individual"],
            "response_mode": "individual",
            "import_source": "module_bank",
        }

        def catalogue(_session_id: int) -> dict[str, Any]:
            meta = metadata_fixture()
            meta["questions"] = [*meta["questions"], bank]
            meta["items"] = [*meta["items"], bank]
            return meta

        self.school.live_class_metadata_for_session = catalogue
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M1",
            live_slot="C1",
            stage="round",
            page_id="round",
            student_view={"questions": "student"},
        )
        items = self.school.ensure_live_session_items(self.session_id)
        by_item = {str(row["item_id"]): row for row in items}
        q1 = by_item["q-one"]
        q2 = by_item["q-two"]
        imported = by_item["bank-import-41"]
        board = next(row for row in items if str(row.get("kind")) == "whiteboard")
        self.school.update_live_session_item_settings(
            self.session_id,
            int(q1["id"]),
            save_to_card=True,
            show_live_results=False,
        )
        self.school.update_live_session_item_settings(
            self.session_id,
            int(q2["id"]),
            publish_mode="individual",
            response_mode="individual",
        )
        self.school.update_live_session_item_settings(
            self.session_id,
            int(imported["id"]),
            save_to_card=True,
            publish_mode="group_submit",
            response_mode="group_submit",
        )
        self.school.update_live_session_item_settings(
            self.session_id,
            int(board["id"]),
            publish_mode="group_shared",
        )

        def clobber(_session_id: int) -> dict[str, Any]:
            meta = catalogue(self.session_id)
            for row in list(meta["questions"]) + list(meta["items"]):
                if not isinstance(row, dict):
                    continue
                row["save_to_card"] = False
                row["show_live_results"] = True
                if str(row.get("id")) == "q-two":
                    row["response_mode"] = "group_consensus"
                    row["publish_mode"] = "group_consensus"
                if str(row.get("id")) == "bank-import-41":
                    row["response_mode"] = "individual"
                    row["publish_mode"] = "individual"
                if str(row.get("item_type") or row.get("type")) == "whiteboard":
                    row["publish_mode"] = "individual"
            return meta

        self.school.live_class_metadata_for_session = clobber
        self.school.ensure_live_session_items(self.session_id)
        self.school.student_live_items_payload(self.session_id, self.student_ids[0])
        self.school.set_live_session_teacher_state(
            self.session_id, stage="play", page_id="later"
        )
        self.school.set_live_session_teacher_state(
            self.session_id, stage="round", page_id="round"
        )
        state = self.school.get_live_session_state(self.session_id)
        live = {str(row["item_id"]): row for row in state["live_items"]}
        self.assertTrue(live["q-one"]["save_to_card"])
        self.assertFalse(live["q-one"]["show_live_results"])
        self.assertTrue(live["q-one"]["item"]["save_to_card"])
        self.assertEqual(live["q-two"]["response_mode"], "individual")
        self.assertEqual(live["q-two"]["publish_mode"], "individual")
        self.assertTrue(live["bank-import-41"]["save_to_card"])
        self.assertEqual(live["bank-import-41"]["response_mode"], "group_submit")
        board_after = next(
            row for row in state["live_items"] if str(row.get("kind")) == "whiteboard"
        )
        self.assertEqual(board_after["publish_mode"], "group_shared")
        cards = {str(row["id"]): row for row in state["question_cards"]}
        self.assertTrue(cards["q-one"]["save_to_card"])
        self.assertFalse(cards["q-one"]["show_live_results"])
        self.assertEqual(cards["q-two"]["response_mode"], "individual")
        self.assertEqual(cards["bank-import-41"]["response_mode"], "group_submit")
        self.assertTrue(cards["bank-import-41"]["save_to_card"])

    def _preloaded_deck_metadata(self, page: int = 4) -> dict[str, Any]:
        """Return a stripped desk item plus a bank item on one round page.

        The desk projection uses an underscore id and omits the answer key,
        matching ``items`` after teacher-field stripping. The catalogue
        question keeps the hyphenated id and the key.
        """

        question = {
            "id": "parabola-a",
            "ref": "test/parabola-a",
            "item_type": "question",
            "stage": "round",
            "page_number": page,
            "order": 1,
            "type": "mc",
            "text": "Which graph opens upward?",
            "options": ["down", "up"],
            "correct_answer": "B",
            "publish_modes": ["individual", "group_consensus"],
            "response_mode": "individual",
        }
        stripped = {
            key: value
            for key, value in question.items()
            if key not in {"correct_answer", "key"}
        }
        stripped["id"] = "parabola_a"
        bank = {
            "id": "bank-import-41",
            "ref": "bank/import/41",
            "item_type": "question",
            "stage": "round",
            "page_number": page,
            "order": 2,
            "type": "mc",
            "text": "Bank pick",
            "options": ["left", "right"],
            "correct_answer": "A",
            "placement_key": f"class:{self.class_id}:import:parabola",
            "publish_modes": ["individual"],
            "response_mode": "individual",
            "import_source": "module_bank",
        }
        return {
            "schema_version": 2,
            "course": "MCF3M",
            "module": "M2",
            "live_class": "C1",
            "questions": [question, bank],
            "items": [stripped, bank],
            "media": None,
            "slides": {"deck_ref": None, "page_numbers": []},
            "round_defaults": {},
            "pages": [
                {"id": "round", "stage": "round", "page_number": page, "name": "Round 1"}
            ],
        }

    def _drift_preloaded_lifecycle_id(self, row_id: int) -> None:
        """Store the underscore spelling the desk projection used to fork."""

        with self.school._lock:
            self.school.conn.execute(
                """
                UPDATE live_session_items
                SET item_id = ?, placement_key = ?
                WHERE id = ?
                """,
                ("parabola_a", "round:4:9:parabola_a", int(row_id)),
            )
            self.school.conn.commit()

    def test_preloaded_save_to_card_survives_alias_refresh(self) -> None:
        """Save to card on a deck item survives a hyphen/underscore refresh.

        A later /state poll must keep one published row and the student
        saved card, including after the lifecycle id drifts to underscores.
        """

        self.school.live_class_metadata_for_session = (
            lambda _session_id: self._preloaded_deck_metadata()
        )
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M2",
            live_slot="C1",
            stage="round",
            page_id="round",
            student_view={"questions": "student"},
        )
        items = self.school.ensure_live_session_items(self.session_id)
        deck_rows = [
            row
            for row in items
            if str(row.get("item_id") or "") in {"parabola-a", "parabola_a"}
        ]
        self.assertEqual(len(deck_rows), 1)
        deck = deck_rows[0]
        self.assertEqual(deck["item_id"], "parabola-a")
        self.assertEqual((deck.get("item") or {}).get("correct_answer"), "B")
        row_id = int(deck["id"])
        self._drift_preloaded_lifecycle_id(row_id)
        saved = self.school.update_live_session_item_settings(
            self.session_id, row_id, save_to_card=True
        )
        self.assertEqual(int(saved["id"]), row_id)
        self.assertTrue(saved["save_to_card"])
        self.assertEqual(saved["item_id"], "parabola-a")
        published = self.school.publish_live_session_item(
            self.session_id, row_id, publish_mode="individual"
        )
        self.assertEqual(published["status"], "active")
        self.assertTrue(published["save_to_card"])
        self._drift_preloaded_lifecycle_id(row_id)
        state = self.school.get_live_session_state(self.session_id)
        live = [
            row
            for row in state["live_items"]
            if str(row.get("item_id") or "") in {"parabola-a", "parabola_a"}
        ]
        self.assertEqual(len(live), 1)
        self.assertEqual(int(live[0]["id"]), row_id)
        self.assertEqual(live[0]["status"], "active")
        self.assertTrue(live[0]["save_to_card"])
        cards = {
            str(row["id"]): row for row in state["question_cards"]
        }
        self.assertEqual(cards["parabola-a"]["live_item_id"], row_id)
        self.assertTrue(cards["parabola-a"]["save_to_card"])
        self.assertEqual(cards["parabola-a"]["correct_answer"], "B")
        self.school.set_live_session_teacher_state(self.session_id, stage="summary")
        payload = self.school.student_live_items_payload(
            self.session_id, self.student_ids[0]
        )
        card = next(
            row
            for row in payload["saved_cards"]
            if str(row.get("item_id") or "") in {"parabola-a", "parabola_a"}
        )
        self.assertTrue(card["save_to_card"])
        self.assertTrue(card.get("parked"))

    def test_preloaded_reveal_marks_answer_and_bank_still_works(self) -> None:
        """Reveal marks a deck item's key, and a bank add still marks its own.

        A keyless prompt linked to the preloaded row is repaired from the
        catalogue question. The bank placement keeps its own key.
        """

        self.school.live_class_metadata_for_session = (
            lambda _session_id: self._preloaded_deck_metadata()
        )
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M2",
            live_slot="C1",
            stage="round",
            page_id="round",
            student_view={"questions": "student"},
        )
        items = {
            str(row["item_id"]): row
            for row in self.school.ensure_live_session_items(self.session_id)
        }
        deck = items["parabola-a"]
        bank = items["bank-import-41"]
        published = self.school.publish_live_session_item(
            self.session_id, int(deck["id"]), publish_mode="individual"
        )
        prompt = self.school._prompt_for_live_item(published)
        assert prompt is not None
        self.assertEqual(str((prompt.get("payload") or {}).get("key") or ""), "B")
        payload = dict(prompt.get("payload") or {})
        payload.pop("key", None)
        payload.pop("correct_answer", None)
        with self.school._lock:
            self.school.conn.execute(
                """
                UPDATE live_session_prompts
                SET payload = ?
                WHERE id = ?
                """,
                (json.dumps(payload), int(prompt["id"])),
            )
            self.school.conn.commit()
        self._drift_preloaded_lifecycle_id(int(deck["id"]))
        results = self.school.live_session_item_results(
            self.session_id, int(deck["id"])
        )
        tally = results.get("tally") or {}
        marked = [
            row for row in tally.get("choices") or [] if row.get("correct")
        ]
        self.assertEqual([row.get("id") for row in marked], ["B"])
        healed = self.school.get_live_session_item(self.session_id, int(deck["id"]))
        self.assertEqual(healed["item_id"], "parabola-a")
        self.assertEqual(healed["status"], "active")
        bank_published = self.school.publish_live_session_item(
            self.session_id, int(bank["id"]), publish_mode="individual"
        )
        bank_prompt = self.school._prompt_for_live_item(bank_published)
        assert bank_prompt is not None
        self.assertEqual(str((bank_prompt.get("payload") or {}).get("key") or ""), "A")
        bank_results = self.school.live_session_item_results(
            self.session_id, int(bank["id"])
        )
        bank_marked = [
            row
            for row in (bank_results.get("tally") or {}).get("choices") or []
            if row.get("correct")
        ]
        self.assertEqual([row.get("id") for row in bank_marked], ["A"])

    def _positional_preloaded_metadata(self) -> dict[str, Any]:
        """Desk item whose id is the stage:page:order placement key.

        Teacher sqlite can keep that key on the lifecycle row and on an
        older override, while the catalogue question stays ``parabola-a``.
        The stripped desk copy still omits the answer key.
        """

        base = self._preloaded_deck_metadata()
        question = next(row for row in base["questions"] if row["id"] == "parabola-a")
        stripped = {
            key: value
            for key, value in question.items()
            if key not in {"correct_answer", "key"}
        }
        stripped["id"] = "round:4:1:parabola_a"
        stripped["item_id"] = "round:4:1:parabola_a"
        bank = next(
            row for row in base["items"] if str(row.get("id") or "") == "bank-import-41"
        )
        base["items"] = [stripped, bank]
        return base

    def test_preloaded_save_to_card_follows_positional_override(self) -> None:
        """Save to card survives a stage:page:order id after a teacher deck edit.

        The class already has a move override on ``parabola-a`` (flag off)
        and a stale override on ``round:4:9:parabola_a``. The live row uses
        that positional id and Group submission. Toggling Save to card must
        update the catalogue override, keep the same published row, and
        still be on after a fresh deck seed.
        """

        stamp = "2026-09-25T12:00:00"
        with self.school._lock:
            self.school.conn.execute(
                """
                INSERT INTO class_live_playlist_item_overrides (
                    class_id, module, slot, item_id, removed, save_to_card,
                    page_number, stage, sort_order, created_at, updated_at
                ) VALUES (?, 'M2', 'C1', 'parabola-a', 0, 0, 4, 'round', 1, ?, ?)
                """,
                (self.class_id, stamp, stamp),
            )
            self.school.conn.execute(
                """
                INSERT INTO class_live_playlist_item_overrides (
                    class_id, module, slot, item_id, removed, save_to_card,
                    page_number, stage, sort_order, created_at, updated_at
                ) VALUES (
                    ?, 'M2', 'C1', 'round:4:9:parabola_a', 0, 0,
                    NULL, NULL, NULL, ?, ?
                )
                """,
                (self.class_id, stamp, stamp),
            )
            self.school.conn.commit()

        def metadata(_session_id: int) -> dict[str, Any]:
            overrides = self.school.list_class_playlist_item_overrides(
                self.class_id, "M2", "C1"
            )
            return self.school._apply_class_playlist_item_overrides(
                self._positional_preloaded_metadata(), overrides
            )

        self.school.live_class_metadata_for_session = metadata
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M2",
            live_slot="C1",
            stage="round",
            page_id="round",
            student_view={"questions": "student"},
        )
        with self.school._lock:
            cur = self.school.conn.execute(
                """
                INSERT INTO live_session_items (
                    live_session_id, placement_key, item_id, stage, page_number,
                    sort_order, kind, item_json, status, publish_mode,
                    response_mode, show_live_results, save_to_card, published_at,
                    created_at, updated_at
                ) VALUES (
                    ?, 'round:4:9:parabola_a', 'round:4:9:parabola_a', 'round', 4,
                    4004, 'question', ?, 'active', 'group_submit',
                    'group_submit', 1, 0, ?, ?, ?
                )
                """,
                (
                    self.session_id,
                    json.dumps(
                        {
                            "id": "round:4:9:parabola_a",
                            "type": "mc",
                            "text": "Which graph opens upward?",
                            "options": ["down", "up"],
                            "stage": "round",
                            "page_number": 4,
                            "order": 9,
                        }
                    ),
                    stamp,
                    stamp,
                    stamp,
                ),
            )
            self.school.conn.commit()
            row_id = int(cur.lastrowid)
        saved = self.school.update_live_session_item_settings(
            self.session_id, row_id, save_to_card=True
        )
        self.assertEqual(int(saved["id"]), row_id)
        self.assertTrue(saved["save_to_card"])
        self.assertEqual(saved["item_id"], "parabola-a")
        self.assertEqual(saved["response_mode"], "group_submit")
        self.assertEqual((saved.get("item") or {}).get("correct_answer"), "B")
        state = self.school.get_live_session_state(self.session_id)
        live = [
            row
            for row in state["live_items"]
            if "parabola" in str(row.get("item_id") or "")
        ]
        self.assertEqual(len(live), 1)
        self.assertEqual(int(live[0]["id"]), row_id)
        self.assertEqual(live[0]["status"], "active")
        self.assertTrue(live[0]["save_to_card"])
        self.assertEqual(live[0]["response_mode"], "group_submit")
        cards = {str(row["id"]): row for row in state["question_cards"]}
        self.assertEqual(cards["parabola-a"]["live_item_id"], row_id)
        self.assertTrue(cards["parabola-a"]["save_to_card"])
        self.assertEqual(cards["parabola-a"]["correct_answer"], "B")
        self.assertEqual(cards["parabola-a"]["response_mode"], "group_submit")
        overrides = self.school.list_class_playlist_item_overrides(
            self.class_id, "M2", "C1"
        )
        catalogue = next(row for row in overrides if row["item_id"] == "parabola-a")
        self.assertEqual(int(catalogue["save_to_card"]), 1)
        merged = self.school._apply_class_playlist_item_overrides(
            self._positional_preloaded_metadata(), overrides
        )
        question = next(row for row in merged["questions"] if row["id"] == "parabola-a")
        self.assertTrue(question.get("save_to_card"))
        with self.school._lock:
            self.school.conn.execute(
                "DELETE FROM live_session_items WHERE live_session_id = ?",
                (self.session_id,),
            )
            self.school.conn.commit()
        fresh = [
            row
            for row in self.school.ensure_live_session_items(self.session_id)
            if "parabola" in str(row.get("item_id") or "")
        ]
        self.assertEqual(len(fresh), 1)
        self.assertEqual(fresh[0]["item_id"], "parabola-a")
        self.assertTrue(fresh[0]["save_to_card"])

    def test_save_to_card_binds_ref_and_bank_keys_on_every_page(self) -> None:
        """Save to Card persists when lifecycle ids are refs or import keys.

        #149 only aliased hyphen versus underscore. Live rows stored as the
        seed ref (``live-class/.../parabola-a``) or a bank row stored as
        ``class:N:import:...`` never match the short catalogue id, so every
        course and page ships ``live_item_id`` null and the checkbox cannot
        persist. Reveal uses that same row for the answer key.
        """

        self.school.live_class_metadata_for_session = self.original_metadata
        self._begin_and_join(1)
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M1",
            live_slot="C2",
            stage="round",
            page_id="round_1",
            student_view={"questions": "student"},
        )
        self.school.ensure_live_session_items(self.session_id)
        meta = self.school.live_class_metadata_for_session(self.session_id)
        ref_by_id = {
            str(row.get("id") or ""): str(row.get("ref") or row.get("item_ref") or "")
            for row in meta.get("questions") or []
            if isinstance(row, dict)
        }
        with self.school._lock:
            rows = self.school.conn.execute(
                """
                SELECT id, item_id, kind FROM live_session_items
                WHERE live_session_id = ?
                """,
                (self.session_id,),
            ).fetchall()
            for row in rows:
                if str(row["kind"] or "") in {"media", "whiteboard", "slides"}:
                    continue
                ref = ref_by_id.get(str(row["item_id"] or ""))
                if not ref:
                    continue
                self.school.conn.execute(
                    """
                    UPDATE live_session_items
                    SET item_id = ?, save_to_card = 0
                    WHERE id = ?
                    """,
                    (ref, int(row["id"])),
                )
            self.school.conn.commit()
        import_key = f"class:{self.class_id}:import:savecard"
        bank_payload = {
            "id": "bank-import-41",
            "item_type": "question",
            "type": "mc",
            "stage": "round",
            "page_number": 4,
            "order": 9,
            "text": "Bank question on the same page",
            "options": ["A", "B"],
            "correct_answer": "A",
            "placement_key": import_key,
            "publish_modes": ["individual"],
            "response_mode": "individual",
            "import_source": "module_bank",
        }
        with self.school._lock:
            self.school.conn.execute(
                """
                INSERT INTO class_live_playlist_placements (
                    class_id, module, slot, page_number, stage, sort_order,
                    placement_key, item_id, item_json, source_question_id,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    self.class_id,
                    "M1",
                    "C2",
                    4,
                    "round",
                    9,
                    import_key,
                    "bank-import-41",
                    json.dumps(bank_payload),
                    "t",
                ),
            )
            self.school.conn.commit()
        self.school.invalidate_live_metadata_cache(class_id=self.class_id)
        self.school.ensure_live_session_items(self.session_id)
        with self.school._lock:
            self.school.conn.execute(
                """
                UPDATE live_session_items
                SET item_id = ?, save_to_card = 0
                WHERE live_session_id = ? AND item_id = ?
                """,
                (import_key, self.session_id, "bank-import-41"),
            )
            self.school.conn.commit()
        before = self.school.get_live_session_state(self.session_id)
        cards = {
            str(card.get("id")): card for card in before.get("question_cards") or []
        }
        self.assertIn("parabola-a", cards)
        self.assertIn("parabola-c", cards)
        self.assertIn("bank-import-41", cards)
        for card_id, expected_key in (
            ("parabola-a", "B"),
            ("parabola-c", "D"),
            ("bank-import-41", "A"),
        ):
            card = cards[card_id]
            self.assertTrue(card.get("live_item_id"), card_id)
            self.assertFalse(card.get("save_to_card"))
            self.assertEqual(card.get("correct_answer"), expected_key)
            self.school.update_live_session_item_settings(
                self.session_id, int(card["live_item_id"]), save_to_card=True
            )
        after = self.school.get_live_session_state(self.session_id)
        after_cards = {
            str(card.get("id")): card for card in after.get("question_cards") or []
        }
        for card_id, expected_key in (
            ("parabola-a", "B"),
            ("parabola-c", "D"),
            ("bank-import-41", "A"),
        ):
            card = after_cards[card_id]
            self.assertTrue(card.get("save_to_card"), card_id)
            self.assertEqual(card.get("correct_answer"), expected_key)
            self.assertEqual(
                int(card.get("live_item_id")),
                int(cards[card_id]["live_item_id"]),
            )
        published = self.school.publish_live_session_item(
            self.session_id,
            int(after_cards["parabola-a"]["live_item_id"]),
            publish_mode="individual",
        )
        self.assertTrue(published.get("save_to_card"))
        self.assertEqual(
            (published.get("item") or {}).get("correct_answer"), "B"
        )
        self.school.set_live_session_teacher_state(
            self.session_id, stage="join", page_id="join"
        )
        parked = self.school.student_live_items_payload(
            self.session_id, self.student_ids[0]
        )
        saved = [
            row
            for row in parked.get("saved_cards") or []
            if row.get("item_id") == "parabola-a"
        ]
        self.assertEqual(len(saved), 1)
        self.assertTrue(saved[0].get("save_to_card"))
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M1",
            live_slot="C1",
            stage="join",
            page_id="join",
        )
        self.school.ensure_live_session_items(self.session_id)
        join_meta = self.school.live_class_metadata_for_session(self.session_id)
        join_refs = {
            str(row.get("id") or ""): str(row.get("ref") or row.get("item_ref") or "")
            for row in join_meta.get("questions") or []
            if isinstance(row, dict)
        }
        with self.school._lock:
            join_rows = self.school.conn.execute(
                """
                SELECT id, item_id, kind FROM live_session_items
                WHERE live_session_id = ?
                """,
                (self.session_id,),
            ).fetchall()
            for row in join_rows:
                if str(row["kind"] or "") in {"media", "whiteboard", "slides"}:
                    continue
                ref = join_refs.get(str(row["item_id"] or ""))
                if not ref:
                    continue
                self.school.conn.execute(
                    """
                    UPDATE live_session_items
                    SET item_id = ?, save_to_card = 0
                    WHERE id = ?
                    """,
                    (ref, int(row["id"])),
                )
            self.school.conn.commit()
        join_state = self.school.get_live_session_state(self.session_id)
        join_cards = [
            card
            for card in join_state.get("question_cards") or []
            if card.get("live_item_id")
        ]
        self.assertGreaterEqual(len(join_cards), 1)
        for card in join_cards:
            self.school.update_live_session_item_settings(
                self.session_id, int(card["live_item_id"]), save_to_card=True
            )
        join_after = self.school.get_live_session_state(self.session_id)
        for card in join_after.get("question_cards") or []:
            self.assertTrue(card.get("live_item_id"))
            self.assertTrue(card.get("save_to_card"), card.get("id"))
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M1",
            live_slot="C2",
            stage="round",
            page_id="round_1",
        )
        back = self.school.get_live_session_state(self.session_id)
        back_cards = {
            str(card.get("id")): card for card in back.get("question_cards") or []
        }
        self.assertTrue(back_cards["parabola-a"].get("save_to_card"))
        self.assertTrue(back_cards["bank-import-41"].get("save_to_card"))
        self.assertEqual(back_cards["parabola-a"].get("correct_answer"), "B")
        self.assertEqual(back_cards["bank-import-41"].get("correct_answer"), "A")

    def test_preloaded_reveal_answers_keeps_the_published_row(self) -> None:
        """Reveal answers advances the published deck row after an id drift.

        Group voting is stored on the lifecycle id. A deck refresh that only
        changes hyphen versus underscore must not point the button at a new
        inactive row.
        """

        self.school.live_class_metadata_for_session = (
            lambda _session_id: self._preloaded_deck_metadata()
        )
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M2",
            live_slot="C1",
            stage="round",
            page_id="round",
            student_view={"questions": "student"},
        )
        self._begin_and_join(4)
        self._setup_groups()
        deck = next(
            row
            for row in self.school.ensure_live_session_items(self.session_id)
            if row["item_id"] == "parabola-a"
        )
        published = self.school.publish_live_session_item(
            self.session_id, int(deck["id"]), publish_mode="group_consensus"
        )
        self.assertEqual(published["response_mode"], "group_consensus")
        self._drift_preloaded_lifecycle_id(int(deck["id"]))
        summary = self.school.end_group_consensus_voting(
            self.session_id, int(deck["id"])
        )
        self.assertGreaterEqual(len(summary.get("teams") or []), 1)
        healed = self.school.get_live_session_item(self.session_id, int(deck["id"]))
        self.assertEqual(int(healed["id"]), int(deck["id"]))
        self.assertEqual(healed["status"], "active")
        self.assertEqual(healed["item_id"], "parabola-a")

    def test_removed_meet_team_does_not_return_for_students(self) -> None:
        """Removing Meet from M1 C4 drops the teammate prompt for students."""

        self.school.live_class_metadata_for_session = self.original_metadata
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M1",
            live_slot="C4",
            stage="join",
            student_view={"questions": "student"},
        )
        self.school.ensure_live_session_items(self.session_id)
        self.school.remove_class_playlist_item(
            self.class_id, "M1", "C4", "meet-team"
        )
        self.school.set_live_session_teacher_state(self.session_id, stage="meet")
        payload = self.school.student_live_prompt_payload(
            self.session_id, self.student_ids[0]
        )
        blob = json.dumps(payload)
        self.assertNotIn("teammate who", blob.lower())
        for row in payload.get("live_items") or []:
            self.assertNotEqual(
                str(row.get("item_id") or "").replace("_", "-"), "meet-team"
            )

    def test_c4_join_submit_stores_a_response(self) -> None:
        """M1 C4 page 1 (Join) Submit Answer stores the waiting-room choice."""

        self.school.live_class_metadata_for_session = self.original_metadata
        self.school.set_live_session_teacher_state(
            self.session_id,
            live_module="M1",
            live_slot="C4",
            stage="join",
            student_view={"questions": "student"},
        )
        payload = self.school.student_live_prompt_payload(
            self.session_id, self.student_ids[0]
        )
        prompt = payload.get("prompt") or {}
        self.assertTrue(prompt.get("id"))
        choices = (prompt.get("payload") or {}).get("choices") or []
        self.assertTrue(choices)
        self.school.submit_live_prompt_response(
            int(prompt["id"]),
            self.student_ids[0],
            {"choice": choices[0]},
        )
        stored = self.school.get_live_prompt_response(
            int(prompt["id"]), self.student_ids[0]
        )
        self.assertIsNotNone(stored)
        self.assertEqual((stored or {}).get("response", {}).get("choice"), choices[0])

    def test_poll_prompt_keeps_authored_choices(self) -> None:
        """A poll publish keeps its options so Submit Answer has a choice to send."""

        deck = metadata_fixture()
        poll = {
            "id": "q-poll",
            "ref": "test/question/q-poll",
            "item_type": "question",
            "stage": "round",
            "page_number": 1,
            "order": 3,
            "type": "poll",
            "text": "Pick a colour",
            "options": ["Red", "Blue"],
            "default_status": "inactive",
            "publish_modes": ["individual"],
            "response_mode": "individual",
        }
        deck["questions"] = [*deck["questions"], poll]
        deck["items"] = [*deck["questions"], deck["items"][-1]]
        self.school.live_class_metadata_for_session = lambda _session_id: deck
        self.school.set_live_session_teacher_state(
            self.session_id,
            stage="round",
            student_view={"questions": "student"},
        )
        items = self.school.ensure_live_session_items(self.session_id)
        row = next(item for item in items if item["item_id"] == "q-poll")
        published = self.school.publish_live_session_item(
            self.session_id, int(row["id"]), publish_mode="individual"
        )
        prompt = self.school._prompt_for_live_item(published)
        assert prompt is not None
        self.assertEqual(prompt["payload"].get("choices"), ["Red", "Blue"])

    def test_group_mc_ready_gate_one_switch_and_blank_miss(self) -> None:
        """Group MC is one switch, gated on why, and a miss stays blank."""

        self._begin_and_join(4)
        self._setup_groups()
        self.school.set_live_session_teacher_state(
            self.session_id, run_as_group=False
        )
        self.assertFalse(
            self.school.live_session_teacher_state_payload(self.session_id)[
                "run_as_group"
            ]
        )
        items = self.school.ensure_live_session_items(self.session_id)
        group_item = next(row for row in items if row["item_id"] == "q-one")
        published = self.school.publish_live_session_item(
            self.session_id,
            int(group_item["id"]),
            publish_mode="group_submit",
        )
        self.assertEqual(published["response_mode"], "group_submit")
        self.assertEqual(published["publish_mode"], "group_submit")
        self.assertTrue(
            self.school.live_session_teacher_state_payload(self.session_id)[
                "run_as_group"
            ]
        )
        prompt = self.school._prompt_for_live_item(published)
        assert prompt is not None
        with self.assertRaisesRegex(ValueError, "group submit"):
            self.school.submit_live_prompt_response(
                int(prompt["id"]), self.student_ids[0], {"choice": "A"}
            )
        drafting = self.school.save_group_mc_draft(
            self.session_id,
            int(published["id"]),
            self.student_ids[0],
            choice="A",
            why="  ",
        )
        self.assertEqual(drafting["phase"], "drafting")
        self.assertFalse(drafting["can_submit"])
        with self.assertRaisesRegex(ValueError, "why"):
            self.school.submit_group_mc_answer(
                self.session_id,
                int(published["id"]),
                self.student_ids[0],
                choice="A",
                why="",
            )
        submitted = self.school.submit_group_mc_answer(
            self.session_id,
            int(published["id"]),
            self.student_ids[0],
            choice="A",
            why="because the graph rises",
        )
        self.assertEqual(submitted["phase"], "submitted")
        self.assertFalse(submitted["can_submit"])
        self.assertIn("Aspen", submitted["members"])
        self.assertTrue(submitted["team_name"])
        self.assertEqual(submitted["last_submitter"], "Aspen")
        self.assertEqual(submitted["submitted_choice"], "A")
        self.assertNotIn("celebrate", submitted)
        self.assertNotIn("submitter_log", submitted)
        self.assertFalse(
            self.school.live_session_teacher_state_payload(self.session_id).get(
                "celebrate"
            )
        )
        self.school.submit_group_mc_answer(
            self.session_id,
            int(published["id"]),
            self.student_ids[0],
            choice="A",
            why="because the graph rises",
        )
        open_view = self.school.live_session_item_results(
            self.session_id, int(published["id"])
        )
        self.assertNotIn("reveal", open_view)
        self.assertTrue(open_view["status_board"])
        for row in open_view["status_board"]:
            self.assertEqual(set(row), {"team_id", "team_name", "submitted"})
        aspen_open = next(
            row
            for row in open_view["submitter_log"]
            if row["last_submitter"] == "Aspen"
        )
        self.assertEqual(aspen_open["resubmit_count"], 0)
        self.assertFalse(aspen_open["repeat_submitter"])
        with self.school._lock:
            self.school.conn.execute(
                """
                DELETE FROM live_group_members
                WHERE live_item_id = ? AND student_id = ?
                """,
                (int(published["id"]), self.student_ids[0]),
            )
            self.school.conn.commit()
        late = self.school.student_group_submit_state(
            published, self.student_ids[0]
        )
        assert late is not None
        self.assertEqual(late["choice"], "A")
        self.assertEqual(late["phase"], "submitted")
        edited = self.school.save_group_mc_draft(
            self.session_id,
            int(published["id"]),
            self.student_ids[0],
            choice="A",
            why="",
        )
        self.assertEqual(edited["phase"], "drafting")
        self.assertFalse(edited["can_submit"])
        still_in = self.school.live_session_item_results(
            self.session_id, int(published["id"])
        )
        aspen_team = next(
            row
            for row in still_in["status_board"]
            if row["team_name"] == submitted["team_name"]
        )
        self.assertTrue(aspen_team["submitted"])
        self.assertNotIn("answer", aspen_team)
        revised = self.school.submit_group_mc_answer(
            self.session_id,
            int(published["id"]),
            self.student_ids[0],
            choice="A",
            why="because the graph still rises",
        )
        self.assertEqual(revised["phase"], "submitted")
        revised_view = self.school.live_session_item_results(
            self.session_id, int(published["id"])
        )
        aspen_revised = next(
            row
            for row in revised_view["submitter_log"]
            if row["last_submitter"] == "Aspen"
        )
        self.assertEqual(aspen_revised["resubmit_count"], 1)
        self.assertFalse(aspen_revised["repeat_submitter"])
        q_two = next(
            row
            for row in self.school.ensure_live_session_items(self.session_id)
            if row["item_id"] == "q-two"
        )
        second = self.school.publish_live_session_item(
            self.session_id,
            int(q_two["id"]),
            publish_mode="group_submit",
        )
        self.school.submit_group_mc_answer(
            self.session_id,
            int(second["id"]),
            self.student_ids[0],
            choice="B",
            why="a second round",
        )
        second_view = self.school.live_session_item_results(
            self.session_id, int(second["id"])
        )
        aspen_second = next(
            row
            for row in second_view["submitter_log"]
            if row["last_submitter"] == "Aspen"
        )
        self.assertTrue(aspen_second["repeat_submitter"])
        self.assertEqual(aspen_second["resubmit_count"], 0)
        student_payload = self.school.student_live_items_payload(
            self.session_id, self.student_ids[0]
        )
        card = next(
            row
            for row in student_payload["active_questions"]
            if row["item_id"] == "q-one"
        )
        self.assertIsNone(card["results"])
        self.assertNotIn("submitter_log", card)
        self.assertNotIn("resubmit_count", card["group_submit"])
        self.assertEqual(card["group_submit"]["phase"], "submitted")
        self.assertIn("Aspen", card["group_submit"]["members"])
        self.school.close_live_session_item(self.session_id, int(published["id"]))
        closed_view = self.school.live_session_item_results(
            self.session_id, int(published["id"])
        )
        reveal = closed_view["reveal"]
        misses = [row for row in reveal if row["missed"]]
        hits = [row for row in reveal if not row["missed"]]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["answer"], "A")
        self.assertIn("still rises", hits[0]["why"])
        self.assertTrue(misses)
        for row in misses:
            self.assertEqual(row["answer"], "")
            self.assertEqual(row["why"], "")
        aspen = next(
            row
            for row in closed_view["submitter_log"]
            if row["last_submitter"] == "Aspen"
        )
        self.assertEqual(aspen["resubmit_count"], 1)
        self.assertTrue(aspen["repeat_submitter"])
        self.assertTrue(aspen["why_present"])
        missed_log = next(
            row for row in closed_view["submitter_log"] if row["no_submit_at_reveal"]
        )
        self.assertFalse(missed_log["why_present"])
        closed_student = self.school.student_live_items_payload(
            self.session_id, self.student_ids[0]
        )
        closed_card = next(
            row
            for row in closed_student["closed_results"]
            if row["item_id"] == "q-one"
        )
        self.assertNotIn("submitter_log", closed_card)
        self.assertTrue(closed_card["results"]["reveal"])
        blank = next(row for row in closed_card["results"]["reveal"] if row["missed"])
        self.assertEqual(blank["answer"], "")
        self.assertEqual(blank["why"], "")
        staff_js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        student_js = (LMS_DIR / "static" / "student-portal.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('aria-label="Submission"', staff_js)
        self.assertIn("group_submit", staff_js)
        self.assertIn("Group submission is multiple choice only.", staff_js)
        self.assertIn("Submit for team", student_js)
        self.assertIn("data-group-phase", student_js)
        self.assertIn("Submitted — waiting for other teams", student_js)
        self.assertIn("Last submitter:", student_js)
        self.assertIn("student-group-strip", student_js)
        self.assertNotIn("Last submitted by", student_js)
        self.assertNotIn("data-group-agree", student_js)
        self.assertNotIn("huddle-timer", student_js)

    def test_group_submit_rejects_numeric(self) -> None:
        """Numeric items keep the consensus alias and cannot use group submit."""

        def numeric_fixture() -> dict[str, Any]:
            """Return the shared fixture plus an integer-only numeric item."""

            data = metadata_fixture()
            numeric = {
                "id": "q-numeric",
                "ref": "test/question/q-numeric",
                "item_type": "question",
                "stage": "round",
                "page_number": 1,
                "order": 3,
                "type": "numeric",
                "text": "Type an integer",
                "options": [],
                "integer_only": True,
                "default_status": "inactive",
                "publish_modes": ["individual"],
                "response_mode": "individual",
            }
            questions = [*data["questions"], numeric]
            media = next(
                row for row in data["items"] if row.get("item_type") == "media"
            )
            return {**data, "questions": questions, "items": [*questions, media]}

        self.school.live_class_metadata_for_session = (
            lambda _session_id: numeric_fixture()
        )
        self._begin_and_join(4)
        self._setup_groups()
        numeric_item = next(
            row
            for row in self.school.ensure_live_session_items(self.session_id)
            if row["item_id"] == "q-numeric"
        )
        with self.assertRaisesRegex(ValueError, "multiple choice only"):
            self.school.publish_live_session_item(
                self.session_id,
                int(numeric_item["id"]),
                publish_mode="group_submit",
            )
        with self.assertRaisesRegex(ValueError, "multiple choice only"):
            self.school.publish_live_session_item(
                self.session_id,
                int(numeric_item["id"]),
                publish_mode="group submit",
            )

    def test_group_submit_spaced_alias_publishes(self) -> None:
        """``group submit`` is the same Group MC publish as ``group_submit``.

        The page error ``publish mode is not supported: group submit`` was
        the whitelist rejecting the spaced token before the group-submit
        branch. Groups already configured must publish and turn run-as-groups
        on.
        """

        self._begin_and_join(4)
        self._setup_groups()
        self.school.set_live_session_teacher_state(
            self.session_id, run_as_group=False
        )
        items = self.school.ensure_live_session_items(self.session_id)
        group_item = next(row for row in items if row["item_id"] == "q-one")
        published = self.school.publish_live_session_item(
            self.session_id,
            int(group_item["id"]),
            publish_mode="group submit",
        )
        self.assertEqual(published["response_mode"], "group_submit")
        self.assertEqual(published["publish_mode"], "group_submit")
        self.assertEqual(published["status"], "active")
        self.assertTrue(
            self.school.live_session_teacher_state_payload(self.session_id)[
                "run_as_group"
            ]
        )
        q_two = next(row for row in items if row["item_id"] == "q-two")
        hyphen = self.school.publish_live_session_item(
            self.session_id,
            int(q_two["id"]),
            publish_mode="group-submit",
        )
        self.assertEqual(hyphen["response_mode"], "group_submit")
        self.assertEqual(hyphen["publish_mode"], "group_submit")
        client = self.app.test_client()
        client.get("/auth/google?portal=staff")
        client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        client.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )
        again = client.post(
            f"/api/live-sessions/{self.session_id}/items/{int(group_item['id'])}/publish",
            json={"publish_mode": "Group Submit"},
        )
        self.assertEqual(again.status_code, 200, again.get_json())
        body = again.get_json()
        self.assertEqual(body["item"]["publish_mode"], "group_submit")
        self.assertEqual(body["item"]["response_mode"], "group_submit")
        staff_js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        publish_fn = staff_js.split("async function publishLifecycleItem(")[1].split(
            "async function closeLifecycleItem("
        )[0]
        self.assertIn("selectedPublishMode(liveItemId)", publish_fn)
        self.assertIn('return "group_submit"', staff_js)


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


LOAD_CODENAMES = [
    "Aspen",
    "Birch",
    "Cedar",
    "Maple",
    "Oak",
    "Pine",
    "Spruce",
    "Willow",
    "Elm",
    "Ash",
    "Beech",
    "Fir",
    "Hemlock",
    "Larch",
    "Poplar",
    "Redwood",
    "Sequoia",
]


class LiveSessionStateLoadTests(unittest.TestCase):
    """Heavy /state stays 200 under a full class and builder failures."""

    def setUp(self) -> None:
        """Create a 17-student MCF3M class, live session, and staff login."""

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
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCF3M",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=LOAD_CODENAMES,
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
        self.school.game.begin_game(self.class_id)
        for student in self.students:
            self.school.join_live_class_session(
                self.session_id,
                int(student["id"]),
                codename=str(student["codename"]),
            )

    def tearDown(self) -> None:
        """Close sqlite handles and remove temporary files."""

        self.school.close()
        self.tmp.cleanup()

    def test_json_safe_encodes_sets_paths_and_nan(self) -> None:
        """Non-JSON leftovers become lists, strings, or null."""

        cleaned = json_safe(
            {
                "tags": {"a", "b"},
                "path": Path("/tmp/deck"),
                "score": float("nan"),
                "nested": [{"raw": b"xyz"}],
                7: 0,
            }
        )
        json.dumps(cleaned)
        self.assertEqual(sorted(cleaned["tags"]), ["a", "b"])
        self.assertEqual(cleaned["path"], "/tmp/deck")
        self.assertIsNone(cleaned["score"])
        self.assertIsNone(cleaned["nested"][0]["raw"])
        self.assertEqual(cleaned[7], 0)

    def test_heavy_state_http_with_seventeen_attendees(self) -> None:
        """Full /state stays 200 and JSON when N≈17 are present."""

        light = self.client.get(f"/api/live-sessions/{self.session_id}/state?light=1")
        self.assertEqual(light.status_code, 200, light.get_data(as_text=True)[:500])
        light_body = light.get_json()
        self.assertTrue(light_body["ok"])
        self.assertTrue(light_body["light"])
        self.assertEqual(light_body["count"], 17)
        self.assertEqual(len(light_body["attendees"]), 17)

        for _ in range(5):
            heavy = self.client.get(f"/api/live-sessions/{self.session_id}/state")
            self.assertEqual(heavy.status_code, 200, heavy.get_data(as_text=True)[:800])
            body = heavy.get_json()
            self.assertTrue(body["ok"])
            self.assertFalse(body["light"])
            self.assertEqual(body["count"], 17)
            self.assertIn("question_cards", body)
            self.assertIn("class_list", body)
            self.assertIn("live_metadata", body)
            self.assertEqual(len(body["class_list"]), 17)
            json.dumps(body)

    def test_heavy_state_stays_200_when_cards_raise(self) -> None:
        """A question-card crash degrades that slice instead of 500ing."""

        def boom(_session_id: int) -> list[dict[str, Any]]:
            raise RuntimeError("cards exploded")

        self.school.live_session_question_cards = boom  # type: ignore[method-assign]
        rv = self.client.get(f"/api/live-sessions/{self.session_id}/state")
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True)[:800])
        body = rv.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["question_cards"], [])
        self.assertEqual(body["count"], 17)
        self.assertIn("class_list", body)

    def test_heavy_state_stays_200_when_field_is_not_json(self) -> None:
        """A Path/set heavy field is sanitized so jsonify cannot 500."""

        self.school.class_deck_revision = lambda *_args, **_kwargs: {  # type: ignore[method-assign]
            Path("/tmp/rev"),
            float("inf"),
        }
        rv = self.client.get(f"/api/live-sessions/{self.session_id}/state")
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True)[:800])
        body = rv.get_json()
        self.assertTrue(body["ok"])
        self.assertIsInstance(body["deck_revision"], list)
        json.dumps(body)


class StudentStateLoadTests(unittest.TestCase):
    """Student ``/api/student/state`` stays 200 under a full class."""

    def setUp(self) -> None:
        """Create a 17-student class and one logged-in client per attendee."""

        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        self.school = self.app.config["SCHOOL_DB"]
        self.staff = self.app.test_client()
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
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCF3M",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=LOAD_CODENAMES,
            offering_id=int(self.offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )
        self.class_id = int(created["id"])
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])
        self.session_code = str(live["session_code"])
        self.school.game.begin_game(self.class_id)
        self.clients: list[tuple[str, Any]] = []
        for name in LOAD_CODENAMES:
            client = self.app.test_client()
            joined = client.post(
                "/auth/student-code",
                data={"code": self.session_code, "name": name},
                follow_redirects=False,
            )
            self.assertEqual(joined.status_code, 302, joined.get_data(as_text=True)[:400])
            client.post("/student/mood", data={"mood": "good"})
            client.post("/student/character", data={"character": "fox"})
            self.clients.append((name, client))

    def tearDown(self) -> None:
        """Close sqlite handles and remove temporary files."""

        self.school.close()
        self.tmp.cleanup()

    def test_concurrent_student_state_stays_200(self) -> None:
        """N≈17 student polls and a staff heavy poll stay 200 with a real deck."""

        codes: Counter[int] = Counter()
        errors: list[str] = []
        lock = threading.Lock()

        def hit_student(name: str, client: Any) -> None:
            """Poll one attendee several times and record non-200 bodies."""

            for _ in range(4):
                rv = client.get("/api/student/state")
                body = rv.get_json(silent=True) or {}
                with lock:
                    codes[rv.status_code] += 1
                if rv.status_code != 200 or body.get("error") == "state unavailable":
                    with lock:
                        errors.append(
                            f"{name} {rv.status_code} {rv.get_data(as_text=True)[:500]}"
                        )
                    continue
                if body.get("unchanged"):
                    continue
                if (body.get("me") or {}).get("codename") != name:
                    with lock:
                        errors.append(f"{name} me={body.get('me')}")
                if "live_metadata" not in body or "teacher_state" not in body:
                    with lock:
                        errors.append(f"{name} missing deck keys {sorted(body)[:12]}")

        def hit_staff() -> None:
            """Staff heavy /state must stay 200 while students poll."""

            for _ in range(6):
                rv = self.staff.get(f"/api/live-sessions/{self.session_id}/state")
                body = rv.get_json(silent=True) or {}
                with lock:
                    codes[1000 + rv.status_code] += 1
                if rv.status_code != 200 or not body.get("ok"):
                    with lock:
                        errors.append(
                            f"staff {rv.status_code} {rv.get_data(as_text=True)[:400]}"
                        )
                    continue
                if body.get("error") == "state unavailable":
                    with lock:
                        errors.append("staff degraded to stub")
                    continue
                if body.get("count") != 17:
                    with lock:
                        errors.append(f"staff count {body.get('count')}")

        threads = [threading.Thread(target=hit_staff)]
        threads.extend(
            threading.Thread(target=hit_student, args=(name, client))
            for name, client in self.clients
        )
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [], errors[:4])
        self.assertEqual(codes[200], 17 * 4)
        self.assertEqual(codes[1200], 6)
        self.assertNotIn(500, codes)

    def test_metadata_failure_returns_reconnect_stub(self) -> None:
        """A metadata crash is a 200 stub, not a partial deck wipe."""

        def boom(_session_id: int) -> dict[str, Any]:
            raise TypeError("'NoneType' object is not subscriptable")

        self.school.student_live_class_metadata_for_session = boom  # type: ignore[method-assign]
        name, client = self.clients[0]
        rv = client.get("/api/student/state")
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True)[:500])
        body = rv.get_json()
        self.assertEqual(body.get("error"), "state unavailable")
        self.assertNotIn("me", body)
        self.assertNotIn("live_metadata", body)
        self.assertNotIn("prompt", body)
        json.dumps(body)
        self.assertTrue(name)

    def test_student_count_cursor_survives_concurrent_get_class(self) -> None:
        """Roster counts stay readable while other threads use the game connection."""

        errors: list[str] = []
        lock = threading.Lock()

        def hammer() -> None:
            """Call get_class, which counts students after releasing its own query."""

            try:
                for _ in range(40):
                    row = self.school.game.get_class(self.class_id)
                    if int(row["student_count"]) != 17:
                        raise AssertionError(row.get("student_count"))
            except Exception as exc:  # noqa: BLE001 — record the race
                with lock:
                    errors.append(repr(exc))

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])

    def test_idle_sixteen_by_twenty_waves_zero_500(self) -> None:
        """N=16 idle waves match the ops bar: 0/320 HTTP 500s.

        Baseline on tip ``01392ce`` was 12/320 (3.75%). Each wave is one
        simultaneous poll from every attendee, with no seq/stamp so the
        server builds a full snapshot instead of the unchanged short-circuit.
        A staff heavy ``/state`` rides along and must stay a real 200.
        """

        cohort = self.clients[:16]
        self.assertEqual(len(cohort), 16)
        tokens = {
            str(row.get("codename") or ""): str(row.get("visit_token") or "")
            for row in self.school.list_live_session_attendees(self.session_id)
        }
        failures: list[str] = []
        stubs = 0
        student_200 = 0
        staff_bad = 0
        lock = threading.Lock()

        for wave in range(20):
            barrier = threading.Barrier(17)

            def hit_student(name: str, client: Any) -> None:
                """One idle full snapshot for this attendee."""

                barrier.wait(timeout=30)
                rv = client.get(
                    "/api/student/state",
                    headers={"X-Student-Visit-Token": tokens.get(name, "")},
                )
                body = rv.get_json(silent=True) or {}
                with lock:
                    nonlocal student_200, stubs
                    if rv.status_code == 200:
                        student_200 += 1
                    if body.get("error") == "state unavailable":
                        stubs += 1
                    if rv.status_code != 200:
                        failures.append(
                            f"wave {wave} {name} {rv.status_code} "
                            f"{rv.get_data(as_text=True)[:240]}"
                        )

            def hit_staff() -> None:
                """Staff heavy poll in the same wave must not 500 or stub."""

                barrier.wait(timeout=30)
                rv = self.staff.get(f"/api/live-sessions/{self.session_id}/state")
                body = rv.get_json(silent=True) or {}
                with lock:
                    nonlocal staff_bad
                    if (
                        rv.status_code != 200
                        or not body.get("ok")
                        or body.get("error") == "state unavailable"
                        or body.get("light")
                        or int(body.get("count") or 0) < 16
                    ):
                        staff_bad += 1
                        failures.append(
                            f"wave {wave} staff {rv.status_code} "
                            f"count={body.get('count')} error={body.get('error')}"
                        )

            threads = [threading.Thread(target=hit_staff)]
            threads.extend(
                threading.Thread(target=hit_student, args=(name, client))
                for name, client in cohort
            )
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(student_200, 320, failures[:6])
        self.assertEqual(failures, [], failures[:6])
        self.assertEqual(stubs, 0)
        self.assertEqual(staff_bad, 0)


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

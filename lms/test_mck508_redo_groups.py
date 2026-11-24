#!/usr/bin/env python3
"""MCK-508: Redo groups and live dashboard checkbox removal."""

from __future__ import annotations

import os
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
from test_live_class_backend_state import (  # noqa: E402
    LiveBackendStateTests,
    metadata_fixture,
)


class Mck508RedoGroupsTests(LiveBackendStateTests):
    """Redo groups gating and server enforcement."""

    def test_groups_redo_allowed_only_before_first_group_publish(self) -> None:
        """``groups_redo_allowed`` is true after setup and false after a group Q."""
        self._begin_and_join(4)
        before = self.school.staff_teacher_state_view(self.session_id)
        self.assertFalse(before.get("groups_redo_allowed"))

        self._setup_groups()
        mid = self.school.staff_teacher_state_view(self.session_id)
        self.assertTrue(mid.get("groups_configured"))
        self.assertTrue(mid.get("groups_redo_allowed"))

        self.school.live_class_metadata_for_session = lambda _sid: metadata_fixture()
        self.school.set_live_session_teacher_state(self.session_id, stage="round")
        items = self.school.ensure_live_session_items(self.session_id)
        group_item = next(row for row in items if row["item_id"] == "q-two")
        self.school.publish_live_session_item(
            self.session_id,
            int(group_item["id"]),
            publish_mode="group_consensus",
        )
        after = self.school.staff_teacher_state_view(self.session_id)
        self.assertFalse(after.get("groups_redo_allowed"))

    def test_student_teacher_state_payload_omits_groups_redo(self) -> None:
        """``/state`` reads must not compute redo gating SQL."""
        self._begin_and_join(4)
        self._setup_groups()
        bare = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertNotIn("groups_redo_allowed", bare)

    def test_redo_clears_teams_and_reopens_setup(self) -> None:
        """Redo removes named teams and clears ``groups_configured``."""
        self._begin_and_join(4)
        self._setup_groups()
        self.assertTrue(self.school.game.class_has_named_teams(self.class_id))

        result = self.school.redo_live_session_groups(self.session_id)
        teacher = result["teacher_state"]
        self.assertFalse(teacher.get("groups_configured"))
        self.assertFalse(teacher.get("groups_redo_allowed"))
        self.assertFalse(self.school.game.class_has_named_teams(self.class_id))
        setup = teacher.get("group_setup") or {}
        self.assertEqual(int(setup.get("n_teams") or 0), 2)
        self.assertEqual(str(setup.get("mode") or ""), "manual")

    def test_redo_refused_after_group_question_publish(self) -> None:
        """Server rejects redo once a group question is published."""
        self._begin_and_join(4)
        self._setup_groups()
        self.school.live_class_metadata_for_session = lambda _sid: metadata_fixture()
        self.school.set_live_session_teacher_state(self.session_id, stage="round")
        items = self.school.ensure_live_session_items(self.session_id)
        group_item = next(row for row in items if row["item_id"] == "q-two")
        self.school.publish_live_session_item(
            self.session_id,
            int(group_item["id"]),
            publish_mode="group_consensus",
        )
        with self.assertRaisesRegex(ValueError, "group question"):
            self.school.redo_live_session_groups(self.session_id)
        self.assertTrue(self.school.game.class_has_named_teams(self.class_id))

    def test_redo_never_touches_teams_with_group_responses(self) -> None:
        """Protected team rows remain when redo is blocked by group responses."""
        self._begin_and_join(4)
        game = self._setup_groups()
        named = [team for team in game if str(team.get("name")) != "Class"]
        team_id = int(named[0]["id"])
        items = self.school.ensure_live_session_items(self.session_id)
        group_item = next(row for row in items if row["item_id"] == "q-two")
        with self.school._lock:
            self.school.conn.execute(
                """
                INSERT INTO live_group_responses (
                    live_item_id, team_id, status, vote_summary_json,
                    proposed_answer_json, final_answer_json,
                    finalizer_student_id, awarded_points, voting_ended_at,
                    finalized_at, created_at, updated_at
                ) VALUES (?, ?, 'collecting_votes', '[]', NULL, NULL,
                          NULL, NULL, NULL, NULL, datetime('now'), datetime('now'))
                """,
                (int(group_item["id"]), team_id),
            )
            self.school.conn.commit()
        before = self.school.game.game_state(self.class_id)
        before_ids = {int(team["id"]) for team in before.get("teams") or []}
        with self.assertRaisesRegex(ValueError, "group responses"):
            self.school.redo_live_session_groups(self.session_id)
        after = self.school.game.game_state(self.class_id)
        after_ids = {int(team["id"]) for team in after.get("teams") or []}
        self.assertEqual(before_ids, after_ids)

    def test_clear_named_teams_respects_skip_ids(self) -> None:
        """``clear_named_teams`` deletes only teams outside ``skip_team_ids``."""
        self._begin_and_join(4)
        self._setup_groups()
        state = self.school.game.game_state(self.class_id)
        teams = [team for team in state.get("teams") or [] if team.get("name") != "Class"]
        self.assertGreaterEqual(len(teams), 2)
        keep = int(teams[0]["id"])
        self.school.game.clear_named_teams(self.class_id, skip_team_ids=frozenset({keep}))
        state = self.school.game.game_state(self.class_id)
        remaining = [
            int(team["id"])
            for team in state.get("teams") or []
            if str(team.get("name")) != "Class"
        ]
        self.assertEqual(remaining, [keep])


class Mck508LiveShellTests(unittest.TestCase):
    """Template and client wiring for MCK-508 dashboard changes."""

    def test_live_tab_omits_removed_checkboxes_and_wires_redo(self) -> None:
        """Scoreboard, Winner page, and Show teams controls are gone; Redo groups stays."""
        html = (LMS_DIR / "templates" / "staff" / "course.html").read_text(encoding="utf-8")
        self.assertNotIn('id="ap-scoreboard-toggle"', html)
        self.assertNotIn('id="ap-gamification-toggle"', html)
        self.assertNotIn('id="live-run-as-group"', html)
        self.assertNotIn(">Show teams to students<", html)
        self.assertNotIn(">Winner page<", html)
        self.assertIn('id="live-redo-groups"', html)
        self.assertIn(">Redo groups<", html)
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("groups_redo_allowed", js)
        self.assertIn("redoLiveGroups", js)
        self.assertIn("/groups/redo", js)


if __name__ == "__main__":
    unittest.main()
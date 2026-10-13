#!/usr/bin/env python3
"""MCK-62: quiet-member strip on the teacher light poll and shell."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from test_live_class_backend_state import LiveBackendStateTests  # noqa: E402


class QuietMemberStripTests(unittest.TestCase):
    """Light poll carries quiet_dots; teacher JS paints the strip."""

    def test_light_poll_quiet_dots_follow_engagement(self) -> None:
        harness = LiveBackendStateTests()
        harness.setUp()
        try:
            harness._begin_and_join(4)
            harness._setup_groups()
            harness.school.set_live_session_teacher_state(harness.session_id, stage="round")
            items = harness.school.ensure_live_session_items(harness.session_id)
            group_item = next(row for row in items if row["item_id"] == "q-one")
            published = harness.school.publish_live_session_item(
                harness.session_id,
                int(group_item["id"]),
                publish_mode="group_submit",
            )
            live_item_id = int(published["id"])
            bag = harness.school.get_live_session_state(harness.session_id, light=True)[
                "group_results"
            ][str(live_item_id)]
            teams = bag["teams"]
            self.assertGreaterEqual(len(teams), 1)
            first_team = teams[0]
            self.assertIn("quiet_dots", first_team)
            roster_len = len(first_team["quiet_dots"])
            self.assertGreaterEqual(roster_len, 1)
            self.assertTrue(all(first_team["quiet_dots"]), first_team)

            team_id = int(first_team["team_id"])
            roster = harness.school._team_roster_member_ids(harness.session_id, team_id)
            voter = roster[0]
            harness.school.record_engagement_event(
                "group.vote",
                session_id=harness.session_id,
                student_id=voter,
                item_ref=live_item_id,
            )
            after = harness.school.get_live_session_state(harness.session_id, light=True)[
                "group_results"
            ][str(live_item_id)]
            self.assertNotEqual(after["response_seq"], bag["response_seq"])
            team_row = next(row for row in after["teams"] if int(row["team_id"]) == team_id)
            for index, student_id in enumerate(roster):
                self.assertEqual(
                    team_row["quiet_dots"][index],
                    student_id != voter,
                )
        finally:
            harness.tearDown()

    def test_teacher_js_wires_quiet_strip(self) -> None:
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("function quietMemberStripHtml(", js)
        self.assertIn("quiet-member-strip", js)
        self.assertIn("studentViewOn()", js)
        self.assertIn("quiet_dots", js)
        self.assertIn("paintGroupResultsInPlace(true)", js)
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn(".quiet-member-strip", css)
        self.assertIn("is-student-view .quiet-member-strip", css)

    def test_full_item_results_include_quiet_dots(self) -> None:
        """Initial /results payload must carry quiet_dots (MCK-62 MED)."""
        harness = LiveBackendStateTests()
        harness.setUp()
        try:
            harness._begin_and_join(4)
            harness._setup_groups()
            harness.school.set_live_session_teacher_state(harness.session_id, stage="round")
            items = harness.school.ensure_live_session_items(harness.session_id)
            group_item = next(row for row in items if row["item_id"] == "q-one")
            published = harness.school.publish_live_session_item(
                harness.session_id,
                int(group_item["id"]),
                publish_mode="group_submit",
            )
            live_item_id = int(published["id"])
            full = harness.school.live_session_item_results(
                harness.session_id, live_item_id
            )
            self.assertEqual(full["response_mode"], "group_submit")
            board = full.get("status_board") or []
            self.assertGreaterEqual(len(board), 1)
            self.assertIn("quiet_dots", board[0])
            self.assertTrue(all(board[0]["quiet_dots"]))
            self.assertTrue(str(full.get("group_response_seq") or ""))
        finally:
            harness.tearDown()


if __name__ == "__main__":
    unittest.main()

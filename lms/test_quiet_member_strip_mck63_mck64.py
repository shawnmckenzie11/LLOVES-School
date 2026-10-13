#!/usr/bin/env python3
"""MCK-63 / MCK-64: quiet window on the light poll and hide strip while projecting."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from engagement_events import QUIET_MEMBER_WINDOW_MINUTES, SqliteEngagementEvents  # noqa: E402
from test_live_class_backend_state import LiveBackendStateTests  # noqa: E402

_TZ = ZoneInfo("America/Toronto")


class QuietWindowLogicTests(unittest.TestCase):
    """MCK-63: quiet is relative to the group's latest activity."""

    def test_quiet_dots_use_group_activity_window(self) -> None:
        anchor = datetime(2026, 10, 1, 12, 0, tzinfo=_TZ)
        roster = [1, 2, 3]
        touch = {
            1: anchor,
            2: anchor - timedelta(minutes=QUIET_MEMBER_WINDOW_MINUTES - 1),
            3: anchor - timedelta(minutes=QUIET_MEMBER_WINDOW_MINUTES + 1),
        }
        dots = SqliteEngagementEvents.quiet_dots_for_roster(roster, touch)
        self.assertEqual(dots, [False, False, True])

    def test_all_quiet_when_group_has_no_touches(self) -> None:
        dots = SqliteEngagementEvents.quiet_dots_for_roster([4, 5], {})
        self.assertEqual(dots, [True, True])


class QuietWindowPollTests(unittest.TestCase):
    """Light poll quiet_dots follow the engagement window."""

    def test_stale_vote_can_be_quiet_again(self) -> None:
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
            run_key = harness.school.get_live_session(harness.session_id)["run_key"]
            team_id = int(
                harness.school.get_live_session_state(harness.session_id, light=True)[
                    "group_results"
                ][str(live_item_id)]["teams"][0]["team_id"]
            )
            roster = harness.school._team_roster_member_ids(harness.session_id, team_id)
            voter, quiet_one = roster[0], roster[1]
            old = (datetime.now(_TZ) - timedelta(minutes=QUIET_MEMBER_WINDOW_MINUTES + 2)).isoformat(
                timespec="seconds"
            )
            fresh = datetime.now(_TZ).isoformat(timespec="seconds")
            store = harness.school.engagement
            harness.school.record_engagement_event(
                "group.vote",
                session_id=harness.session_id,
                student_id=voter,
                item_ref=live_item_id,
            )
            with store.lock:
                store.conn.execute(
                    "UPDATE engagement_events SET ts = ? WHERE student_id = ? AND kind = ?",
                    (old, voter, "group.vote"),
                )
                store.conn.commit()
            harness.school.record_engagement_event(
                "group.vote",
                session_id=harness.session_id,
                student_id=quiet_one,
                item_ref=live_item_id,
            )
            with store.lock:
                store.conn.execute(
                    "UPDATE engagement_events SET ts = ? WHERE student_id = ? AND kind = ?",
                    (fresh, quiet_one, "group.vote"),
                )
                store.conn.commit()
            bag = harness.school.get_live_session_state(harness.session_id, light=True)[
                "group_results"
            ][str(live_item_id)]
            team_row = bag["teams"][0]
            by_id = dict(zip(roster, team_row["quiet_dots"], strict=True))
            self.assertTrue(by_id[voter], by_id)
            self.assertFalse(by_id[quiet_one], by_id)
        finally:
            harness.tearDown()


class QuietStripProjectingTests(unittest.TestCase):
    """MCK-64: teacher shell hides the strip while projecting or Hide names is on."""

    def test_teacher_js_and_css_guard_projecting(self) -> None:
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("function liveProjectingForQuietStrip(", js)
        self.assertIn("function syncQuietStripSuppressed(", js)
        self.assertIn("hideResponseNames", js.split("function quietMemberStripHtml(")[1][:600])
        self.assertIn("liveProjectingForQuietStrip()", js.split("function quietMemberStripHtml(")[1][:600])
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn("is-quiet-strip-suppressed .quiet-member-strip", css)
        self.assertIn("is-student-view .quiet-member-strip", css)

    def test_student_portal_has_no_quiet_strip(self) -> None:
        portal = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertNotIn("quiet-member-strip", portal)
        self.assertNotIn("quiet_dots", portal)


if __name__ == "__main__":
    unittest.main()

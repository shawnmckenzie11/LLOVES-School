#!/usr/bin/env python3
"""Teacher Run Live Class dual-pane shell markup and existing control IDs."""

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


class LiveShellTests(unittest.TestCase):
    """Staff live tab is a persistent header + dual pane, not an accordion."""

    def setUp(self) -> None:
        """Isolated app with one assigned teacher and rostered class."""
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

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def test_live_tab_shell_has_header_and_dual_panes(self) -> None:
        """IA v0 shell IDs are present; track accordion is gone."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn('class="track-live-root live-shell-root"', html)
        self.assertIn('id="live-header"', html)
        self.assertIn('id="live-stage-rail"', html)
        self.assertIn("Join", html)
        self.assertIn("Teams", html)
        self.assertIn("Meet", html)
        self.assertIn("Challenge", html)
        self.assertIn("Freeze/CONS", html)
        self.assertIn('id="live-advance"', html)
        self.assertIn('id="live-start"', html)
        self.assertIn('id="class-list-pane"', html)
        self.assertIn('id="team-assign-pane"', html)
        self.assertIn('id="round-slide-settings"', html)
        self.assertIn('id="media-artifact-zone"', html)
        self.assertIn('id="question-artifact-zone"', html)
        self.assertIn('id="results-strip"', html)
        self.assertNotIn('id="track-accordion"', html)
        self.assertNotIn("data-accordion-toggle", html)

    def test_live_tab_preserves_existing_control_ids(self) -> None:
        """Attendance, teams, meet, rounds, media, and scoring IDs stay wired."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        html = page.get_data(as_text=True)
        for control_id in (
            "ap-att-list",
            "ap-att-log",
            "ap-allow-guests",
            "ap-gamify-no",
            "ap-gamify-yes",
            "ap-gamify-next",
            "ap-n-teams",
            "ap-assign-balanced",
            "ap-teams-next",
            "ap-meet-start",
            "ap-meet-minutes",
            "ap-name-list",
            "ap-start-game",
            "ap-rounds-start",
            "ap-rounds-list",
            "ap-active-media",
            "ap-media-preview",
            "ap-join-billboard",
            "ap-join-billboard-code",
            "ap-join-strip",
            "ap-score-end",
            "ap-score-list",
        ):
            self.assertIn(f'id="{control_id}"', html)

    def test_live_tab_does_not_host_end_live_class_wipe(self) -> None:
        """Hard-clear End Live Class stays on the dashboard, not this shell."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        html = page.get_data(as_text=True)
        self.assertNotIn("All session data will be lost", html)
        self.assertNotIn("staff_end_live_class", html)
        home = self.client.get("/staff")
        self.assertEqual(home.status_code, 200)
        self.assertIn("Run Live Class", home.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Teacher Run Live Class IA v1 shell markup and existing control IDs."""

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
    """Staff live tab is IA v1 stage rail + condensed roster + Active Content."""

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
        """IA v1 shell IDs are present; track accordion is gone."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("live-shell-ia-v1", html)
        self.assertIn('id="live-header"', html)
        self.assertIn('id="live-stage-rail"', html)
        self.assertIn('id="live-stage-prev"', html)
        self.assertIn('id="live-stage-next"', html)
        self.assertIn('data-stage="join"', html)
        self.assertIn('data-stage="teams"', html)
        self.assertIn('data-stage="meet"', html)
        self.assertIn('data-stage="round"', html)
        self.assertIn('data-stage="play"', html)
        self.assertNotIn('data-stage="challenge"', html)
        self.assertNotIn('data-stage="freeze"', html)
        self.assertIn('id="live-end-class"', html)
        self.assertIn('id="live-option-card"', html)
        self.assertIn('id="teams-option-card"', html)
        self.assertIn('id="round-option-card"', html)
        self.assertIn("Minds on", html)
        self.assertIn("Consolidation", html)
        self.assertIn("Keep teams", html)
        self.assertIn('id="class-list-pane"', html)
        self.assertIn('id="team-assign-pane"', html)
        self.assertIn('id="live-active-content"', html)
        self.assertIn('id="live-content-tabs"', html)
        self.assertIn("Active Media", html)
        self.assertIn("Question(s)", html)
        self.assertIn("Canvas/Slides", html)
        self.assertIn('id="live-edit-layout"', html)
        self.assertIn('id="live-layout-presets"', html)
        self.assertIn('id="live-frames"', html)
        self.assertIn('data-frame="A"', html)
        self.assertIn('data-frame="B"', html)
        self.assertIn('data-frame="C"', html)
        self.assertIn('id="live-canvas-stub"', html)
        self.assertIn("canvas_ephemeral: true", html)
        self.assertIn('id="round-slide-settings"', html)
        self.assertIn('id="media-artifact-zone"', html)
        self.assertIn('id="question-artifact-zone"', html)
        self.assertIn('id="results-strip"', html)
        self.assertNotIn('id="track-accordion"', html)
        self.assertNotIn("data-accordion-toggle", html)
        self.assertEqual(html.count('id="ap-media-preview"'), 1)
        self.assertIn("/static/live-media/m1c1-c1-real-slice.html?role=teacher", html)

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

    def test_live_tab_end_class_is_placement_only(self) -> None:
        """Header End Class posts to the dashboard wipe route; semantics stay #49."""
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        html = page.get_data(as_text=True)
        self.assertIn("All session data will be lost", html)
        self.assertIn(f"/staff/class/{self.class_id}/end-live", html)
        self.assertIn('id="live-end-class"', html)
        self.assertIn('aria-label="End Live Class"', html)
        self.assertIn("live-end-class-form", html)
        self.assertNotIn('class="live-header-end danger live-legacy-control"', html)
        self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        home = self.client.get("/staff")
        self.assertEqual(home.status_code, 200)
        home_html = home.get_data(as_text=True)
        self.assertIn("End Live Class", home_html)
        self.assertIn("All session data will be lost", home_html)
        self.assertIn(f"/staff/class/{self.class_id}/end-live", home_html)
        self.assertIn("course-action-live-row", home_html)

    def test_class_list_pane_has_readable_min_width(self) -> None:
        """ClassListPane stays condensed but wide enough for names + mood chips."""
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn("--live-left-min: 18rem", css)
        self.assertIn("--live-left-max: 25%", css)
        self.assertIn(
            "minmax(var(--live-left-min), var(--live-left-max))", css
        )
        self.assertNotIn("--live-left-min: 140px", css)
        self.assertNotIn("minmax(var(--live-left-min), 15%)", css)
        self.assertNotIn("max-width: var(--live-left-max)", css)
        self.assertIn("grid-template-columns: 1.25rem minmax(6rem, 1fr) auto", css)


if __name__ == "__main__":
    unittest.main()

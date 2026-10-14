#!/usr/bin/env python3
"""MCK-230: deck denominator, merged filters, finish module, session grid."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from local_dev_seed import LOCAL_DEV_SHAWN_EMAIL, seed_local_dev_school  # noqa: E402
from participation_weight import count_participation_opportunities  # noqa: E402
class Mck230ParticipationUiTests(unittest.TestCase):
    """Participation tab chrome and grid payload."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env = mock.patch.dict(
            os.environ,
            {"LOCAL_DEV_LOGIN": "1", "FLASK_ENV": "development"},
            clear=False,
        )
        self.env.start()
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root)
        self.school = self.app.config["SCHOOL_DB"]
        first = seed_local_dev_school(self.school)
        self.class_id = int(first["class_id"])
        self.client = self.app.test_client()
        self.client.get(
            f"/auth/google/callback?portal=staff&email={LOCAL_DEV_SHAWN_EMAIL}&name=Shawn",
            follow_redirects=False,
        )

    def tearDown(self) -> None:
        self.school.close()
        self.env.stop()
        self.tmp.cleanup()

    def test_participation_tab_has_merged_filters(self) -> None:
        html = self.client.get(
            f"/staff/class/{self.class_id}?tab=ap&view=participation"
        ).get_data(as_text=True)
        self.assertIn('id="ap-include-individual"', html)
        self.assertIn('id="ap-include-group"', html)
        self.assertIn('id="ap-display-mode-select"', html)
        self.assertNotIn('id="ap-data-mode-select"', html)
        self.assertNotIn('id="ap-group-slice-select"', html)

    def test_grid_returns_dashboard_columns_and_session_meta(self) -> None:
        body = self.client.get(
            f"/api/classes/{self.class_id}/participation-grid?sort=az"
        ).get_json()
        self.assertTrue(body.get("ok"))
        self.assertIn("columns", body)
        self.assertIn("session_meta", body)
        self.assertIn("individual_cells", body)
        self.assertNotIn("weeks", body)

    def test_deck_denominator_uses_mc_only(self) -> None:
        offered = self.school.participation_module_offered(
            self.class_id, "M1", include_individual=True, include_group=False
        )
        self.assertEqual(offered, 6)


    def test_finish_module_creates_subtotal_and_marks_complete(self) -> None:
        """Seeded M1 live columns can be finished into a locked module total."""
        self.assertFalse(self.school.module_complete(self.class_id, "M1"))
        rv = self.client.post(
            f"/api/classes/{self.class_id}/participation/modules/M1/finish",
            json={"sort": "az"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        self.assertTrue(self.school.module_complete(self.class_id, "M1"))
        dash = self.school.game.dashboard(self.class_id)
        names = [c.get("name") for c in dash["columns"] if c.get("kind") == "subtotal"]
        self.assertIn("Module 1 total", names)


if __name__ == "__main__":
    unittest.main()

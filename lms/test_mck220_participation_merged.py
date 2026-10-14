#!/usr/bin/env python3
"""MCK-220: module participation merged into Participation tab with filters."""

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
from test_participation_mck198 import Base  # noqa: E402


class Mck220ParticipationTabTests(unittest.TestCase):
    """Participation tab shell and participation-grid payload."""

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

    def test_participation_tab_has_filters_not_legacy_link(self) -> None:
        html = self.client.get(
            f"/staff/class/{self.class_id}?tab=ap&view=participation"
        ).get_data(as_text=True)
        self.assertIn('id="ap-data-mode-select"', html)
        self.assertIn('id="ap-individual-metric-select"', html)
        self.assertIn('id="ap-group-slice-select"', html)
        self.assertNotIn("participation-catchup-link", html)
        self.assertNotIn("Catch-up links", html)

    def test_legacy_staff_participation_url_redirects(self) -> None:
        rv = self.client.get(f"/staff/classes/{self.class_id}/participation")
        self.assertEqual(rv.status_code, 302)
        self.assertIn(f"/staff/class/{self.class_id}", rv.headers.get("Location", ""))
        self.assertIn("view=participation", rv.headers.get("Location", ""))

    def test_participation_grid_includes_module_participation(self) -> None:
        body = self.client.get(
            f"/api/classes/{self.class_id}/participation-grid?sort=az"
        ).get_json()
        self.assertTrue(body.get("ok"))
        part = body.get("module_participation") or {}
        modules = part.get("modules") or []
        self.assertTrue(any(m.get("module") == "M1" for m in modules))
        m1 = next(m for m in modules if m["module"] == "M1")
        self.assertEqual(m1.get("offered"), 8)
        students = part.get("students") or []
        maple = next(s for s in students if s["modules"]["M1"]["answered"] == 5)
        m1_cell = maple["modules"]["M1"]
        self.assertEqual(m1_cell.get("answered"), 5)
        self.assertEqual(m1_cell.get("percent"), 62)
        self.assertEqual(m1_cell.get("level"), 2)


class Mck220RealRunGridCountsTests(Base):
    """Non-discarded real runs only — same denominators as class_participation."""

    def test_test_run_excluded_from_grid_module_participation(self) -> None:
        self.run_class("M3", "C1", 2, 0, {})
        test_key = self.run_class("M3", "C2", 3, 0, {}, ended_iso="2026-10-20T15:00:00")
        self.school.conn.execute(
            """
            UPDATE participation_runs
            SET discarded = 0, is_real = 0
            WHERE class_id = ? AND run_key = ?
            """,
            (self.class_id, test_key),
        )
        self.school.conn.commit()
        client = self.app.test_client()
        client.get("/auth/google?portal=staff")
        client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        code = self.school.get_user_by_email("teacher@gmail.com")["verification_code"]
        client.post("/verify-email", data={"code": code})
        body = client.get(
            f"/api/classes/{self.class_id}/participation-grid?sort=az"
        ).get_json()
        m3 = next(
            m
            for m in body["module_participation"]["modules"]
            if m["module"] == "M3"
        )
        self.assertEqual(m3["offered"], 2)


if __name__ == "__main__":
    unittest.main()

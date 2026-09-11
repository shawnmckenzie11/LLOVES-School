#!/usr/bin/env python3
"""LOCAL_DEV_LOGIN seed: picker accounts, semester, one MCF3M demo class."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from local_dev_seed import (  # noqa: E402
    LOCAL_DEV_IT_EMAIL,
    LOCAL_DEV_PICKER_EMAILS,
    LOCAL_DEV_SHAWN_EMAIL,
    LOCAL_DEV_STAFF_EMAIL,
    seed_local_dev_school,
)


class LocalDevSeedTests(unittest.TestCase):
    """Cloud/laptop empty sqlite becomes a clickable school under the flag."""

    def setUp(self) -> None:
        """Isolated sqlite with LOCAL_DEV_LOGIN on at create_app time."""
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
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.env.stop()
        self.tmp.cleanup()

    def test_seed_is_idempotent(self) -> None:
        """Second seed keeps the same users, offering, and class."""
        first = seed_local_dev_school(self.school)
        second = seed_local_dev_school(self.school)
        self.assertEqual(first["staff_ids"], second["staff_ids"])
        self.assertEqual(first["offering_id"], second["offering_id"])
        self.assertEqual(first["class_id"], second["class_id"])
        self.assertFalse(second["created_class"])
        emails = {
            str(row["email"]).lower()
            for row in self.school.list_staff()
        }
        for email in LOCAL_DEV_PICKER_EMAILS:
            self.assertIn(email, emails)

    def test_both_pickers_list_seeded_emails(self) -> None:
        """Admin and Teacher pickers show IT + both staff; no empty-db copy."""
        for portal in ("it", "staff"):
            rv = self.client.get(f"/auth/google?portal={portal}")
            self.assertEqual(rv.status_code, 200)
            html = rv.get_data(as_text=True)
            self.assertNotIn("No accounts in this local database yet", html)
            self.assertNotIn("Add another Google email", html)
            for email in LOCAL_DEV_PICKER_EMAILS:
                self.assertIn(email, html)

    def test_unknown_picker_email_stays_403(self) -> None:
        """Typing an unseeded email is still a login, not self-register."""
        rv = self.client.get(
            "/auth/google/callback?portal=staff&email=stranger@gmail.com&name=X"
        )
        self.assertEqual(rv.status_code, 403)
        self.assertIn("not registered", rv.get_data(as_text=True).lower())

    def test_one_click_skips_2sv(self) -> None:
        """Picker click lands in the portal with no verify-email hop."""
        rv = self.client.get(
            f"/auth/google/callback?portal=it&email={LOCAL_DEV_IT_EMAIL}&name=Shawn",
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302)
        self.assertTrue(rv.headers["Location"].endswith("/it"))
        dash = self.client.get("/it", follow_redirects=False)
        self.assertEqual(dash.status_code, 200)

        staff = self.app.test_client()
        landed = staff.get(
            f"/auth/google/callback?portal=staff&email={LOCAL_DEV_SHAWN_EMAIL}&name=Shawn",
            follow_redirects=False,
        )
        self.assertEqual(landed.status_code, 302)
        self.assertNotIn("verify-email", landed.headers["Location"])
        home = staff.get("/staff", follow_redirects=False)
        self.assertEqual(home.status_code, 200)

    def test_staff_on_admin_picker_lands_in_staff_shell(self) -> None:
        """Percival on the padlock picker is staff home, not a 403."""
        rv = self.client.get(
            f"/auth/google/callback?portal=it&email={LOCAL_DEV_STAFF_EMAIL}&name=R",
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302)
        self.assertIn("/staff", rv.headers["Location"])

    def test_demo_class_owned_by_shawn(self) -> None:
        """Run Live Class has an MCF3M section with Maple on Shawn's account."""
        shawn = self.school.get_user_by_email(LOCAL_DEV_SHAWN_EMAIL)
        assert shawn is not None
        classes = self.school.list_staff_classes(int(shawn["id"]))
        self.assertTrue(classes)
        row = classes[0]
        self.assertEqual(str(row.get("offering_code") or row.get("course_code")), "MCF3M")
        maple = self.school.game.find_student_by_codename(int(row["id"]), "Maple")
        self.assertIsNotNone(maple)


if __name__ == "__main__":
    unittest.main(verbosity=2)

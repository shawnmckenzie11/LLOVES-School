#!/usr/bin/env python3
"""Local dev login picker vs real Google OAuth, and the production guard."""

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

FAKE_OAUTH = {
    "GOOGLE_CLIENT_ID": "fake-client-id.apps.googleusercontent.com",
    "GOOGLE_CLIENT_SECRET": "fake-secret",
    "GOOGLE_REDIRECT_URI": "https://alc.mckenzian.com/auth/google/callback",
}


class LocalLoginTests(unittest.TestCase):
    """A laptop can sign in without bouncing to the production callback."""

    def setUp(self) -> None:
        """Create an isolated app with production OAuth credentials present."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, FAKE_OAUTH, clear=False)
        self.env.start()
        # ``testing=False`` so the mock picker is driven only by the dev flag.
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        """Close db, stop env patches, and remove temp files."""
        self.school.close()
        self.env.stop()
        self.tmp.cleanup()

    def test_without_flag_login_uses_real_google(self) -> None:
        """Default behavior still redirects to Google's production callback."""
        with mock.patch.dict(os.environ, {"LOCAL_DEV_LOGIN": ""}, clear=False):
            rv = self.client.get("/auth/google?portal=it")
        self.assertEqual(rv.status_code, 302)
        target = rv.headers["Location"]
        self.assertIn("accounts.google.com", target)
        self.assertIn("alc.mckenzian.com", target)

    def test_with_flag_login_stays_local(self) -> None:
        """The dev flag serves the offline picker instead of redirecting."""
        with mock.patch.dict(os.environ, {"LOCAL_DEV_LOGIN": "1"}, clear=False):
            rv = self.client.get("/auth/google?portal=it")
            self.assertEqual(rv.status_code, 200)
            html = rv.get_data(as_text=True)
            self.assertIn("Simulated Google Sign-In", html)
            self.assertNotIn("accounts.google.com", html)
            self.assertIn("solutions@mckenzian.com", html)

    def test_picker_completes_a_local_it_session(self) -> None:
        """Choosing the IT account lands on the local IT dashboard."""
        with mock.patch.dict(os.environ, {"LOCAL_DEV_LOGIN": "1"}, clear=False):
            self.client.get("/auth/google?portal=it")
            rv = self.client.get(
                "/auth/google/callback?portal=it"
                "&email=solutions@mckenzian.com&name=Shawn"
            )
            self.assertEqual(rv.status_code, 302)
            self.assertNotIn("alc.mckenzian.com", rv.headers["Location"])
            user = self.school.get_user_by_email("solutions@mckenzian.com")
            assert user is not None
            code = user.get("verification_code")
            if code:
                self.client.post("/verify-email", data={"code": code})
            dash = self.client.get("/it", follow_redirects=False)
            self.assertIn(dash.status_code, (200, 302))

    def test_staff_picker_excludes_nothing_but_lists_staff(self) -> None:
        """The staff portal picker lists staff accounts for one-click entry."""
        self.school.register_staff("teacher@gmail.com", "Teacher One")
        with mock.patch.dict(os.environ, {"LOCAL_DEV_LOGIN": "1"}, clear=False):
            rv = self.client.get("/auth/google?portal=staff")
        self.assertIn("teacher@gmail.com", rv.get_data(as_text=True))


class ProductionGuardTests(unittest.TestCase):
    """Production refuses to boot with the offline picker enabled."""

    def test_production_with_dev_login_refuses_to_start(self) -> None:
        """FLASK_ENV=production plus LOCAL_DEV_LOGIN is a hard failure."""
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        try:
            with mock.patch.dict(
                os.environ,
                {"FLASK_ENV": "production", "LOCAL_DEV_LOGIN": "1"},
                clear=False,
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    create_app(db_path=root / "l.sqlite", data_dir=root)
            self.assertIn("LOCAL_DEV_LOGIN", str(ctx.exception))
        finally:
            tmp.cleanup()

    def test_production_without_dev_login_starts(self) -> None:
        """Production boots normally when the flag is absent."""
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        try:
            with mock.patch.dict(
                os.environ,
                {"FLASK_ENV": "production", "LOCAL_DEV_LOGIN": ""},
                clear=False,
            ):
                app = create_app(db_path=root / "l.sqlite", data_dir=root)
            app.config["SCHOOL_DB"].close()
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)

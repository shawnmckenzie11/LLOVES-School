#!/usr/bin/env python3
"""MCK-183 I3: /invite/<token> states, login_hint, wrong account, accept → Welcome."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")
os.environ.pop("GOOGLE_CLIENT_ID", None)

import staff_invites  # noqa: E402
from app import create_app  # noqa: E402

NEW = "rae.teacher@gmail.com"


class InviteAcceptTests(unittest.TestCase):
    """The invite link from the email."""

    def setUp(self) -> None:
        """School with one invite."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.invite, self.token = staff_invites.create_or_refresh_invite(
            self.school, email=NEW, first_name="Rae",
            preset=("SBI4U", "M/W/F", "2:00pm"),
        )
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        """Close."""
        self.school.close()
        self.tmp.cleanup()

    def _google(self, email: str):
        """Mock Google callback, then the email code if asked."""
        rv = self.client.get(f"/auth/google/callback?email={email}&name=R")
        if "/verify-email" in rv.headers.get("Location", ""):
            user = self.school.get_user_by_email(email)
            rv = self.client.post("/verify-email", data={"code": user["verification_code"]})
        return rv

    def test_valid_link_signs_in_and_lands_on_welcome(self) -> None:
        """Valid → Continue with Google (login_hint) → code → /staff/welcome; single use."""
        page = self.client.get(f"/invite/{self.token}")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn("You're invited to ALC", html)
        self.assertIn(f"Sign in with {NEW} to set up your class.", html)
        self.assertIn("Continue with Google", html)
        self.assertIn('name="referrer" content="no-referrer"', html)
        href = html.split('class="btn" href="')[1].split('"')[0].replace("&amp;", "&")
        query = parse_qs(urlsplit(href).query)
        self.assertEqual(query["login_hint"], [NEW])
        self.assertEqual(query["next"], ["/staff/welcome"])
        self.client.get(href)
        done = self._google(NEW)
        self.assertTrue(done.headers["Location"].endswith("/staff/welcome"), done.headers["Location"])
        self.assertEqual(staff_invites.resolve_invite(self.school, self.token)[0], "used")
        actions = [r[0] for r in self.school.conn.execute("SELECT action FROM access_audit_log")]
        self.assertIn("invite.accept", actions)
        again = self.client.get(f"/invite/{self.token}").get_data(as_text=True)
        self.assertIn("You've already joined", again)
        self.assertIn("This invite has been used. Sign in to get to your classes.", again)

    def test_wrong_google_account_does_not_use_the_invite(self) -> None:
        """Another (even allowlisted) account sees the mismatch page and isn't signed in."""
        self.school.register_staff("other@gmail.com")
        self.client.get(f"/invite/{self.token}")
        self.client.get("/auth/google?portal=staff")
        rv = self._google("other@gmail.com")
        html = rv.get_data(as_text=True)
        self.assertIn("That's a different Google account", html)
        self.assertIn(
            f"This invite is for {NEW}, but you signed in as other@gmail.com. Your invite is still waiting.",
            html,
        )
        self.assertIn("Use another Google account", html)
        self.assertEqual(staff_invites.resolve_invite(self.school, self.token)[0], "valid")
        self.assertEqual(self.client.get("/staff").status_code, 302)
        with self.client.session_transaction() as sess:
            self.assertFalse(sess.get("logged_in"))
        # Retry with the right account works.
        done = self._google(NEW)
        self.assertTrue(done.headers["Location"].endswith("/staff/welcome"))

    def test_signed_in_as_someone_else(self) -> None:
        """Already signed in as another teacher: mismatch, then switch."""
        self.school.register_staff("other@gmail.com")
        self.client.get("/auth/google?portal=staff")
        self._google("other@gmail.com")
        html = self.client.get(f"/invite/{self.token}").get_data(as_text=True)
        self.assertIn("That's a different Google account", html)
        self.assertEqual(staff_invites.resolve_invite(self.school, self.token)[0], "valid")
        self.assertIn(f'<form method="post" action="/invite/{self.token}/switch"', html)
        # GET must not sign anyone out (#269 gate: logout CSRF).
        self.assertEqual(self.client.get(f"/invite/{self.token}/switch").status_code, 405)
        with self.client.session_transaction() as sess:
            self.assertTrue(sess.get("logged_in"))
        rv = self.client.post(f"/invite/{self.token}/switch")
        self.assertTrue(rv.headers["Location"].endswith(f"/invite/{self.token}"))
        with self.client.session_transaction() as sess:
            self.assertFalse(sess.get("logged_in"))

    def test_expired_revoked_unknown_pages(self) -> None:
        """Wonder v1.4 copy for each dead link."""
        past = (datetime.now() - timedelta(minutes=1)).isoformat()
        self.school.conn.execute("UPDATE staff_invites SET expires_at = ?", (past,))
        html = self.client.get(f"/invite/{self.token}").get_data(as_text=True)
        self.assertIn("This invite has expired", html)
        self.assertIn("Invite links last 7 days. Ask Shawn to send you a new one.", html)
        unknown = self.client.get("/invite/not-a-token").get_data(as_text=True)
        self.assertIn("This invite link doesn't work", unknown)
        self.assertIn("It may have been replaced or cancelled. Ask Shawn for a new one.", unknown)
        it_user = self.school.get_user_by_email("solutions@mckenzian.com")
        future = (datetime.now() + timedelta(days=1)).isoformat()
        self.school.conn.execute("UPDATE staff_invites SET expires_at = ?", (future,))
        staff_invites.revoke_invite(self.school, self.invite["id"], int(it_user["id"]))
        revoked = self.client.get(f"/invite/{self.token}").get_data(as_text=True)
        self.assertIn("This invite link doesn't work", revoked)

    def test_expired_invite_in_session_does_not_block_sign_in(self) -> None:
        """A stale pending invite is dropped; normal sign-in carries on."""
        self.client.get(f"/invite/{self.token}")
        past = (datetime.now() - timedelta(minutes=1)).isoformat()
        self.school.conn.execute("UPDATE staff_invites SET expires_at = ?", (past,))
        rv = self._google(NEW)
        self.assertEqual(rv.status_code, 302)
        self.assertEqual(staff_invites.resolve_invite(self.school, self.token)[0], "expired")

    def test_login_hint_reaches_google(self) -> None:
        """Real OAuth URL carries login_hint for the invited email."""
        env = {"GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "sec"}
        with patch.dict(os.environ, env, clear=False), patch("auth.mock_login_enabled", return_value=False):
            rv = self.client.get(f"/auth/google?portal=staff&login_hint={NEW}")
        query = parse_qs(urlsplit(rv.headers["Location"]).query)
        self.assertEqual(query["login_hint"], [NEW])
        self.assertEqual(query["scope"], ["openid email profile"])
        with patch.dict(os.environ, env, clear=False), patch("auth.mock_login_enabled", return_value=False):
            rv = self.client.get("/auth/google?portal=staff&login_hint=nope")
        self.assertNotIn("login_hint", parse_qs(urlsplit(rv.headers["Location"]).query))


if __name__ == "__main__":
    unittest.main()

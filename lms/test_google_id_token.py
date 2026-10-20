#!/usr/bin/env python3
"""MCK-306: Google sign-in credential must be verified before a session exists."""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from google.auth.transport import requests as google_requests

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)

from app import create_app  # noqa: E402

CLIENT_ID = "fake-client-id"
EMAIL = "staff@example.com"
CSRF = "fake-csrf-token"
STORED_SUB = "fake-sub-stored"
TOKEN_SUB = "fake-sub-one"
FAILED = "Google One Tap authentication failed."


def _b64(raw: bytes) -> str:
    """URL-safe base64 without padding, for a synthetic unsigned credential."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unsigned_credential(claims: dict) -> str:
    """Build an unsigned credential. It is not a Google-signed token."""
    header = _b64(b'{"alg":"none","typ":"JWT"}')
    body = _b64(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    return f"{header}.{body}."


class GoogleIdTokenTests(unittest.TestCase):
    """Posted Google credentials are verified before any session or user write."""

    def setUp(self) -> None:
        """Isolated sqlite app with a fake Google client id."""
        self._env_prev = {
            key: os.environ.get(key)
            for key in (
                "FLASK_ENV",
                "REQUIRE_PRIVILEGED_MFA",
                "GOOGLE_CLIENT_ID",
                "LOCAL_DEV_LOGIN",
            )
        }
        os.environ.pop("FLASK_ENV", None)
        os.environ.pop("REQUIRE_PRIVILEGED_MFA", None)
        os.environ.pop("LOCAL_DEV_LOGIN", None)
        os.environ["GOOGLE_CLIENT_ID"] = CLIENT_ID
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        """Close the database and restore environment variables."""
        self.school.close()
        self.tmp.cleanup()
        for key, value in self._env_prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _claims(self, **overrides: object) -> dict:
        """Claim set that passes every check except the fields a test overrides."""
        now = int(datetime.now(timezone.utc).timestamp())
        claims: dict = {
            "iss": "https://accounts.google.com",
            "aud": CLIENT_ID,
            "sub": TOKEN_SUB,
            "email": EMAIL,
            "email_verified": True,
            "name": "Example",
            "iat": now,
            "exp": now + 3600,
        }
        claims.update(overrides)
        return claims

    def _allow(self, *, stored_sub: str | None) -> dict:
        """Allowlist one verified account, optionally with a stored subject."""
        user = self.school.register_staff(EMAIL, "Example")
        user = self.school.mark_verified(int(user["id"]))
        if stored_sub:
            user = self.school.link_google(int(user["id"]), stored_sub, "Example")
        return user

    def _row(self) -> dict:
        """Return the allowlisted account row."""
        row = self.school.get_user_by_email(EMAIL)
        self.assertIsNotNone(row)
        return row

    def _post(
        self,
        credential: str,
        *,
        csrf_body: str | None = CSRF,
        csrf_cookie: str | None = CSRF,
    ):
        """POST a GIS credential with the double-submit cookie values given."""
        if csrf_cookie is not None:
            self.client.set_cookie("g_csrf_token", csrf_cookie)
        data = {"credential": credential}
        if csrf_body is not None:
            data["g_csrf_token"] = csrf_body
        return self.client.post(
            "/auth/google/callback",
            data=data,
            follow_redirects=False,
        )

    def _assert_rejected(self, response, before: dict) -> None:
        """The generic failure page, with no session and no account write."""
        self.assertEqual(response.status_code, 401)
        body = response.get_data(as_text=True)
        self.assertIn(FAILED, body)
        after = self._row()
        self.assertEqual(after.get("google_sub"), before.get("google_sub"))
        self.assertEqual(after.get("last_login_at"), before.get("last_login_at"))
        self.assertEqual(after.get("verified_at"), before.get("verified_at"))
        with self.client.session_transaction() as sess:
            self.assertFalse(sess.get("logged_in"))
            self.assertFalse(sess.get("pending_2sv"))
            self.assertIsNone(sess.get("user_id"))

    def test_forged_unsigned_token_is_rejected(self) -> None:
        """An unsigned credential for an allowlisted account does not sign in."""
        from google.oauth2 import id_token as google_id_token

        self._allow(stored_sub=TOKEN_SUB)
        before = self._row()
        credential = _unsigned_credential(self._claims())
        with patch(
            "google.oauth2.id_token.verify_oauth2_token",
            wraps=google_id_token.verify_oauth2_token,
        ) as verify:
            response = self._post(credential)
        verify.assert_called_once()
        self.assertIsInstance(verify.call_args.args[1], google_requests.Request)
        self.assertEqual(verify.call_args.args[2], CLIENT_ID)
        self.assertNotIn(credential, response.get_data(as_text=True))
        self._assert_rejected(response, before)

    def test_wrong_audience_is_rejected(self) -> None:
        """A token whose audience is not the configured client id is rejected."""
        self._allow(stored_sub=TOKEN_SUB)
        before = self._row()
        claims = self._claims(aud="other-client-id")
        with patch(
            "google.oauth2.id_token.verify_oauth2_token", return_value=claims
        ):
            response = self._post("fake-signed-token")
        self._assert_rejected(response, before)

    def test_wrong_issuer_is_rejected(self) -> None:
        """A token from any issuer other than Google accounts is rejected."""
        self._allow(stored_sub=TOKEN_SUB)
        before = self._row()
        claims = self._claims(iss="https://not-google.example")
        with patch(
            "google.oauth2.id_token.verify_oauth2_token", return_value=claims
        ):
            response = self._post("fake-signed-token")
        self._assert_rejected(response, before)

    def test_expired_token_is_rejected(self) -> None:
        """A token whose exp is in the past is rejected."""
        self._allow(stored_sub=TOKEN_SUB)
        before = self._row()
        now = int(datetime.now(timezone.utc).timestamp())
        claims = self._claims(exp=now - 60)
        with patch(
            "google.oauth2.id_token.verify_oauth2_token", return_value=claims
        ):
            response = self._post("fake-signed-token")
        self._assert_rejected(response, before)

    def test_email_verified_false_is_rejected(self) -> None:
        """email_verified false never opens a session."""
        self._allow(stored_sub=TOKEN_SUB)
        before = self._row()
        claims = self._claims(email_verified=False)
        with patch(
            "google.oauth2.id_token.verify_oauth2_token", return_value=claims
        ):
            response = self._post("fake-signed-token")
        self._assert_rejected(response, before)

    def test_sub_mismatch_does_not_overwrite(self) -> None:
        """A different Google subject is a failed login and leaves the row unchanged."""
        self._allow(stored_sub=STORED_SUB)
        before = self._row()
        claims = self._claims(sub="fake-sub-other")
        with patch(
            "google.oauth2.id_token.verify_oauth2_token", return_value=claims
        ):
            with patch.object(self.school, "link_google", wraps=self.school.link_google) as link:
                response = self._post("fake-signed-token")
        link.assert_not_called()
        self.assertEqual(self._row().get("google_sub"), STORED_SUB)
        self._assert_rejected(response, before)

    def test_gis_csrf_mismatch_is_rejected(self) -> None:
        """The GIS double-submit cookie must match the posted value."""
        self._allow(stored_sub=TOKEN_SUB)
        before = self._row()
        with patch(
            "google.oauth2.id_token.verify_oauth2_token",
            return_value=self._claims(),
        ) as verify:
            response = self._post("fake-signed-token", csrf_body="other-csrf-token")
        verify.assert_not_called()
        self._assert_rejected(response, before)

    def test_valid_token_opens_staff_session(self) -> None:
        """A verified token links an empty subject once and opens a staff session."""
        self._allow(stored_sub=None)
        for issuer in ("https://accounts.google.com", "accounts.google.com"):
            claims = self._claims(iss=issuer, sub=TOKEN_SUB)
            with patch(
                "google.oauth2.id_token.verify_oauth2_token", return_value=claims
            ) as verify:
                response = self._post("fake-signed-token")
            self.assertEqual(response.status_code, 302)
            self.assertIn("/staff", response.headers.get("Location", ""))
            self.assertEqual(verify.call_args.args[0], "fake-signed-token")
            self.assertIsInstance(verify.call_args.args[1], google_requests.Request)
            self.assertEqual(verify.call_args.args[2], CLIENT_ID)
            self.assertEqual(self._row().get("google_sub"), TOKEN_SUB)
            with self.client.session_transaction() as sess:
                self.assertTrue(sess.get("logged_in"))
                self.assertEqual(sess.get("user_id"), self._row()["id"])
            self.client.get("/logout")

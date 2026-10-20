#!/usr/bin/env python3
"""MCK-306: loopback test staff login stays off the Google ID token path."""

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

import auth  # noqa: E402
from app import create_app  # noqa: E402
from local_test_login import TEST_STAFF_EMAIL  # noqa: E402

CLIENT_ID = "fake-client-id"
OTHER_EMAIL = "other-staff@example.com"
ENV_KEYS = (
    "FLASK_ENV",
    "FLY_APP_NAME",
    "HOST",
    "LOCAL_DEV_LOGIN",
    "LLOVES_TEST_LOGIN",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
)


def _b64(raw: bytes) -> str:
    """URL-safe base64 without padding, for a synthetic unsigned credential."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unsigned_credential(email: str) -> str:
    """Build an unsigned credential. It is not a Google-signed token."""
    now = int(datetime.now(timezone.utc).timestamp())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "fake-sub-unsigned",
        "email": email,
        "email_verified": True,
        "iat": now,
        "exp": now + 3600,
    }
    header = _b64(b'{"alg":"none","typ":"JWT"}')
    body = _b64(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    return f"{header}.{body}."


class _AppCase(unittest.TestCase):
    """Isolated app with the caller's environment restored afterward."""

    def setUp(self) -> None:
        """Snapshot env vars the gate reads and clear Google credentials."""
        self._env_prev = {key: os.environ.get(key) for key in ENV_KEYS}
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        """Close the database, remove the temp dir, and restore the environment."""
        school = getattr(self, "school", None)
        if school is not None:
            school.close()
        self.tmp.cleanup()
        for key, value in self._env_prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _boot(self, *, testing: bool = True):
        """Create an app against the temp database.

        Args:
            testing: Flask testing flag. The login gate does not treat it as on.
        """
        self.app = create_app(
            db_path=self.root / "lloves.sqlite",
            data_dir=self.root,
            testing=testing,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()


class TestLoginOffTests(_AppCase):
    """The route does nothing unless the flag is explicitly on."""

    def test_off_by_default(self) -> None:
        """With the flag unset, the route is a 404 and creates no user."""
        self._boot()
        response = self.client.post("/auth/test-staff")
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(self.school.get_user_by_email(TEST_STAFF_EMAIL))
        with self.client.session_transaction() as sess:
            self.assertFalse(sess.get("logged_in"))

    def test_zero_and_empty_are_off(self) -> None:
        """``0`` and a blank value do not enable the route."""
        for value in ("0", "", "false", "no"):
            os.environ["LLOVES_TEST_LOGIN"] = value
            self._boot()
            response = self.client.get("/auth/test-staff")
            self.assertEqual(response.status_code, 404, value)
            self.school.close()
            self.school = None


class TestLoginProductionGuardTests(unittest.TestCase):
    """Production and Fly refuse to boot when the flag is exported."""

    def setUp(self) -> None:
        """Snapshot and clear the gate's environment."""
        self._env_prev = {key: os.environ.get(key) for key in ENV_KEYS}
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        """Remove the temp dir and restore the environment."""
        self.tmp.cleanup()
        for key, value in self._env_prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _expect_refuse(self, env: dict[str, str], needle: str) -> None:
        """Boot must raise, and the message must name the blocking condition.

        Args:
            env: Variables to set before ``create_app``.
            needle: Text the refusal has to include.
        """
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaises(RuntimeError) as ctx:
                create_app(db_path=self.root / "l.sqlite", data_dir=self.root)
        message = str(ctx.exception)
        self.assertIn("LLOVES_TEST_LOGIN", message)
        self.assertIn(needle, message)

    def test_production_with_flag_refuses_to_start(self) -> None:
        """``FLASK_ENV=production`` plus the flag does not boot."""
        self._expect_refuse(
            {"FLASK_ENV": "production", "LLOVES_TEST_LOGIN": "1"},
            "FLASK_ENV=production",
        )

    def test_fly_app_name_with_flag_refuses_to_start(self) -> None:
        """A Fly process plus the flag does not boot, even outside production."""
        self._expect_refuse(
            {"FLY_APP_NAME": "lloves-lms", "LLOVES_TEST_LOGIN": "1"},
            "FLY_APP_NAME",
        )

    def test_public_bind_with_flag_refuses_to_start(self) -> None:
        """An explicit non-loopback bind plus the flag does not boot."""
        self._expect_refuse(
            {"HOST": "0.0.0.0", "LLOVES_TEST_LOGIN": "1"},
            "127.0.0.1",
        )

    def test_production_without_flag_still_starts(self) -> None:
        """Production boots when the flag is absent and a key is set."""
        with patch.dict(
            os.environ,
            {"FLASK_ENV": "production", "FLASK_SECRET_KEY": "prod-test-secret"},
            clear=False,
        ):
            app = create_app(db_path=self.root / "l.sqlite", data_dir=self.root)
        self.assertEqual(app.secret_key, "prod-test-secret")
        client = app.test_client()
        self.assertEqual(client.get("/auth/test-staff").status_code, 404)
        app.config["SCHOOL_DB"].close()


class TestLoginLocalTests(_AppCase):
    """The flag signs in one fixed staff account on localhost only."""

    def setUp(self) -> None:
        """Turn the flag on for a loopback bind and boot a temp app."""
        super().setUp()
        os.environ["LLOVES_TEST_LOGIN"] = "1"
        os.environ["HOST"] = "127.0.0.1"
        with self.assertLogs("lloves.local_test_login", level="WARNING") as logs:
            self._boot()
        self.boot_logs = "\n".join(logs.output)

    def test_startup_warning_names_the_flag_and_not_a_secret(self) -> None:
        """Boot logs a warning that the test login is on."""
        self.assertIn("LLOVES_TEST_LOGIN is on", self.boot_logs)
        self.assertIn(TEST_STAFF_EMAIL, self.boot_logs)
        lowered = self.boot_logs.lower()
        self.assertNotIn("client_secret", lowered)
        self.assertNotIn("password", lowered)
        self.assertNotIn("eyj", lowered)

    def test_local_flag_signs_in_fixed_staff(self) -> None:
        """POST on localhost opens a staff session for the fixed account only."""
        response = self.client.post("/auth/test-staff", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/staff"))
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        user = self.school.get_user_by_email(TEST_STAFF_EMAIL)
        self.assertIsNotNone(user)
        self.assertEqual(user["role"], "staff")
        self.assertTrue(user.get("verified_at"))
        self.assertFalse(user.get("google_sub"))
        with self.client.session_transaction() as sess:
            self.assertTrue(sess.get("logged_in"))
            self.assertEqual(sess.get("email"), TEST_STAFF_EMAIL)
            self.assertEqual(sess.get("portal"), "staff")
            self.assertEqual(sess.get("user_id"), int(user["id"]))

    def test_get_signs_in_the_same_account(self) -> None:
        """A browser on the test server can open the route with GET."""
        first = self.client.get("/auth/test-staff")
        second = self.client.get("/auth/test-staff")
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        rows = self.school.conn.execute(
            "SELECT COUNT(*) FROM users WHERE email = ?",
            (TEST_STAFF_EMAIL,),
        ).fetchone()
        self.assertEqual(int(rows[0]), 1)

    def test_posted_credential_does_not_choose_the_account(self) -> None:
        """A body email or credential cannot sign in as someone else."""
        from google.oauth2 import id_token as google_id_token

        os.environ["GOOGLE_CLIENT_ID"] = CLIENT_ID
        credential = _unsigned_credential(OTHER_EMAIL)
        with patch(
            "google.oauth2.id_token.verify_oauth2_token",
            wraps=google_id_token.verify_oauth2_token,
        ) as verify:
            response = self.client.post(
                "/auth/test-staff",
                data={"credential": credential, "email": OTHER_EMAIL},
            )
        verify.assert_not_called()
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.school.get_user_by_email(OTHER_EMAIL))
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get("email"), TEST_STAFF_EMAIL)

    def test_staff_next_is_kept_and_external_next_is_dropped(self) -> None:
        """``next`` may land on a staff path and nowhere else."""
        kept = self.client.get("/auth/test-staff?next=/staff/class/1")
        self.assertIn("/staff/class/1", kept.headers["Location"])
        self.client.get("/logout")
        dropped = self.client.get("/auth/test-staff?next=https://evil.example/staff")
        self.assertTrue(dropped.headers["Location"].endswith("/staff"))
        self.assertNotIn("evil.example", dropped.headers["Location"])

    def test_non_local_host_is_ignored(self) -> None:
        """A request whose host is not localhost or 127.0.0.1 gets 404."""
        response = self.client.get(
            "/auth/test-staff",
            headers={"Host": "evil.example"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(self.school.get_user_by_email(TEST_STAFF_EMAIL))
        with self.client.session_transaction() as sess:
            self.assertFalse(sess.get("logged_in"))

    def test_non_loopback_peer_is_ignored(self) -> None:
        """A loopback Host with a remote peer does not sign in."""
        response = self.client.get(
            "/auth/test-staff",
            base_url="http://127.0.0.1",
            environ_base={"REMOTE_ADDR": "203.0.113.5"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(self.school.get_user_by_email(TEST_STAFF_EMAIL))

    def test_flag_stays_off_in_production_after_boot(self) -> None:
        """Flipping the process to production after boot still disables the route."""
        with patch.dict(os.environ, {"FLASK_ENV": "production"}, clear=False):
            response = self.client.post("/auth/test-staff")
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(self.school.get_user_by_email(TEST_STAFF_EMAIL))

    def test_flag_stays_off_when_fly_app_name_appears(self) -> None:
        """A Fly app name disables the route even if the process already booted."""
        with patch.dict(os.environ, {"FLY_APP_NAME": "lloves-lms"}, clear=False):
            response = self.client.post("/auth/test-staff")
        self.assertEqual(response.status_code, 404)

    def test_archived_or_non_staff_row_is_refused(self) -> None:
        """A hijacked fixed-account row does not get a session."""
        user = self.school.register_staff(TEST_STAFF_EMAIL, "Test Staff")
        self.school.conn.execute(
            "UPDATE users SET role = 'it' WHERE id = ?",
            (int(user["id"]),),
        )
        self.school.conn.commit()
        response = self.client.post("/auth/test-staff")
        self.assertEqual(response.status_code, 403)
        with self.client.session_transaction() as sess:
            self.assertFalse(sess.get("logged_in"))


class GooglePathUnchangedTests(_AppCase):
    """The real Google callback still rejects an unsigned credential."""

    def test_unsigned_token_rejected_while_test_login_is_on(self) -> None:
        """The test flag does not let an unsigned credential through Google sign-in."""
        from google.oauth2 import id_token as google_id_token

        os.environ["LLOVES_TEST_LOGIN"] = "1"
        os.environ["HOST"] = "127.0.0.1"
        os.environ["GOOGLE_CLIENT_ID"] = CLIENT_ID
        self._boot()
        auth._clear_google_cert_cache()
        self.school.register_staff(OTHER_EMAIL, "Other")
        self.school.mark_verified(
            int(self.school.get_user_by_email(OTHER_EMAIL)["id"])
        )
        before = self.school.get_user_by_email(OTHER_EMAIL)
        credential = _unsigned_credential(OTHER_EMAIL)
        self.client.set_cookie("g_csrf_token", "csrf-value")
        with patch(
            "google.oauth2.id_token.verify_oauth2_token",
            wraps=google_id_token.verify_oauth2_token,
        ) as verify:
            response = self.client.post(
                "/auth/google/callback",
                data={"credential": credential, "g_csrf_token": "csrf-value"},
            )
        verify.assert_called_once()
        self.assertIsInstance(verify.call_args.args[1], google_requests.Request)
        self.assertEqual(verify.call_args.args[2], CLIENT_ID)
        self.assertEqual(response.status_code, 401)
        self.assertIn("Google One Tap authentication failed.", response.get_data(as_text=True))
        self.assertNotIn(credential, response.get_data(as_text=True))
        after = self.school.get_user_by_email(OTHER_EMAIL)
        self.assertEqual(after.get("google_sub"), before.get("google_sub"))
        self.assertEqual(after.get("last_login_at"), before.get("last_login_at"))
        with self.client.session_transaction() as sess:
            self.assertFalse(sess.get("logged_in"))
            self.assertIsNone(sess.get("user_id"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

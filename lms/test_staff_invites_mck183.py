#!/usr/bin/env python3
"""MCK-183 I1 + I2: invite tokens, is_test, Admin Invite a teacher, invite email."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")
os.environ.pop("GOOGLE_CLIENT_ID", None)

import email_service  # noqa: E402
import staff_invites  # noqa: E402
from app import create_app  # noqa: E402

NEW = "rae.teacher@gmail.com"


class _Base(unittest.TestCase):
    """Fresh school + signed-in Admin."""

    def setUp(self) -> None:
        """Isolated sqlite and an IT session."""
        self._env = patch.dict(
            os.environ,
            {"RESEND_API_KEY": "", "SMTP_SERVER": "", "INVITE_REPLY_TO": "", "PUBLIC_APP_URL": ""},
            clear=False,
        )
        self._env.start()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        """Close and restore env."""
        self.school.close()
        self.tmp.cleanup()
        self._env.stop()

    def _login_it(self) -> None:
        """Sign in the seeded Admin."""
        self.client.get("/auth/google?portal=it")
        self.client.get("/auth/google/callback?email=solutions@mckenzian.com&name=Shawn")
        user = self.school.get_user_by_email("solutions@mckenzian.com")
        self.client.post("/verify-email", data={"code": user["verification_code"]})


class InviteTokenTests(_Base):
    """I1: hashed single-use tokens with a 7-day expiry."""

    def test_schema_and_is_test_column(self) -> None:
        """The table and users.is_test exist after boot."""
        cols = {r[1] for r in self.school.conn.execute("PRAGMA table_info(users)")}
        self.assertIn("is_test", cols)
        tables = {r[0] for r in self.school.conn.execute("SELECT name FROM sqlite_master")}
        self.assertIn("staff_invites", tables)

    def test_only_the_hash_is_stored(self) -> None:
        """The raw token is nowhere in the database."""
        invite, token = staff_invites.create_or_refresh_invite(
            self.school, email=NEW, first_name="Rae"
        )
        self.assertGreaterEqual(len(token), 40)
        self.assertEqual(invite["token_sha256"], staff_invites.token_hash(token))
        dump = "\n".join(self.school.conn.iterdump())
        self.assertNotIn(token, dump)
        state, row = staff_invites.resolve_invite(self.school, token)
        self.assertEqual(state, "valid")
        self.assertEqual(row["email"], NEW)
        expires = datetime.fromisoformat(invite["expires_at"])
        self.assertAlmostEqual((expires - datetime.now()).total_seconds(), 7 * 86400, delta=120)

    def test_expired_used_revoked_unknown(self) -> None:
        """Every link state resolves."""
        invite, token = staff_invites.create_or_refresh_invite(self.school, email=NEW, first_name="Rae")
        self.assertEqual(staff_invites.resolve_invite(self.school, "nope")[0], "unknown")
        self.assertEqual(staff_invites.resolve_invite(self.school, "")[0], "unknown")
        past = (datetime.now() - timedelta(minutes=1)).isoformat()
        self.school.conn.execute("UPDATE staff_invites SET expires_at = ? WHERE id = ?", (past, invite["id"]))
        self.assertEqual(staff_invites.resolve_invite(self.school, token)[0], "expired")
        future = (datetime.now() + timedelta(days=1)).isoformat()
        self.school.conn.execute("UPDATE staff_invites SET expires_at = ? WHERE id = ?", (future, invite["id"]))
        staff_invites.mark_accepted(self.school, invite["id"])
        self.assertEqual(staff_invites.resolve_invite(self.school, token)[0], "used")
        other, token2 = staff_invites.create_or_refresh_invite(self.school, email="x@gmail.com", first_name="X")
        it_user = self.school.get_user_by_email("solutions@mckenzian.com")
        staff_invites.revoke_invite(self.school, other["id"], int(it_user["id"]))
        self.assertEqual(staff_invites.resolve_invite(self.school, token2)[0], "revoked")
        self.assertIsNotNone(self.school.get_user_by_email("x@gmail.com")["archived_at"])

    def test_reinvite_rotates_and_old_link_stops(self) -> None:
        """Same email: same invite row, new token; the old token is dead."""
        first, old = staff_invites.create_or_refresh_invite(self.school, email=NEW, first_name="Rae")
        again, new = staff_invites.create_or_refresh_invite(self.school, email=NEW.upper(), first_name="Rae")
        self.assertEqual(first["id"], again["id"])
        self.assertNotEqual(old, new)
        self.assertEqual(staff_invites.resolve_invite(self.school, old)[0], "unknown")
        self.assertEqual(staff_invites.resolve_invite(self.school, new)[0], "valid")
        rotated, newer = staff_invites.rotate_token(self.school, first["id"])
        self.assertEqual(staff_invites.resolve_invite(self.school, new)[0], "unknown")
        self.assertEqual(staff_invites.resolve_invite(self.school, newer)[0], "valid")
        del rotated

    def test_test_user_flag_and_admin_refused(self) -> None:
        """kind=test sets users.is_test; an Admin address is refused."""
        staff_invites.create_or_refresh_invite(self.school, email=NEW, first_name="Rae", kind="test")
        user = self.school.get_user_by_email(NEW)
        self.assertEqual(int(user["is_test"]), 1)
        self.assertEqual(user["role"], "staff")
        self.assertIsNone(user["verified_at"])
        with self.assertRaises(ValueError):
            staff_invites.create_or_refresh_invite(self.school, email="solutions@mckenzian.com", first_name="S")
        with self.assertRaises(ValueError):
            staff_invites.create_or_refresh_invite(self.school, email=NEW, first_name="R", kind="boss")

    def test_days_left_copy(self) -> None:
        """Wonder v1.4 inv.list.left."""
        self.assertEqual(staff_invites.days_left_label(7 * 86400 - 30), "7 days left")
        self.assertEqual(staff_invites.days_left_label(86400 + 5), "1 day left")
        self.assertEqual(staff_invites.days_left_label(3600), "Expires today")


class InviteFormTests(_Base):
    """I2: POST /it/invites."""

    def setUp(self) -> None:
        """Signed-in Admin."""
        super().setUp()
        self._login_it()

    def test_send_invite_creates_teacher_and_emails_the_link(self) -> None:
        """One step: allowlist + invite + email (From, Reply-To, Wonder copy)."""
        sent = {}

        def fake_send(to, subject, text, html, *, reply_to=None, from_name=None):
            """Capture the outgoing message."""
            sent.update(to=to, subject=subject, text=text, html=html, reply_to=reply_to, from_name=from_name)
            return True

        with patch.dict(os.environ, {"PUBLIC_APP_URL": "https://alc.mckenzian.com"}, clear=False):
            with patch.object(email_service, "send_email", side_effect=fake_send):
                rv = self.client.post(
                    "/it/invites",
                    data={"email": NEW, "first_name": "Rae", "kind": "teacher",
                          "preset_code": "SBI4U", "preset_days": "M/W/F", "preset_time": "2:00pm"},
                )
        self.assertEqual(rv.status_code, 302)
        user = self.school.get_user_by_email(NEW)
        self.assertEqual(user["role"], "staff")
        self.assertEqual(int(user["is_test"]), 0)
        invite = staff_invites.list_invites(self.school)[0]
        self.assertEqual((invite["preset_code"], invite["preset_days"], invite["preset_time"]), ("SBI4U", "M/W/F", "2:00pm"))
        self.assertEqual(invite["send_count"], 1)
        self.assertEqual(sent["to"], NEW)
        self.assertEqual(sent["subject"], "Shawn invited you to ALC")
        self.assertEqual(sent["from_name"], "Shawn McKenzie via ALC")
        self.assertEqual(sent["reply_to"], "solutions@mckenzian.com")
        self.assertIn("https://alc.mckenzian.com/invite/", sent["text"])
        self.assertIn("Accept invite:\nhttps://alc.mckenzian.com/invite/", sent["text"])
        self.assertIn("Hi Rae, Shawn McKenzie invited you to ALC, a simple way to take attendance and track participation in your live classes. Setup takes about 3 minutes.", sent["text"])
        self.assertIn(f"This link works for 7 days and only for {NEW}. Questions? Just reply to this email.", sent["text"])
        self.assertIn(">Accept invite</a>", sent["html"])
        # No offering is created until she presses Next on Your class.
        self.assertEqual(self.school.list_offerings(teacher_user_id=int(user["id"])), [])
        page = self.client.get("/it").get_data(as_text=True)
        self.assertIn(f"Invite sent to {NEW}.", page)
        self.assertIn("Invite a teacher", page)

    def test_failed_email_still_saves_and_offers_copy_link(self) -> None:
        """Email off: invite saved, toast offers Copy link once."""
        rv = self.client.post("/it/invites", data={"email": NEW, "first_name": "Rae", "kind": "test"})
        self.assertEqual(rv.status_code, 302)
        self.assertEqual(len(staff_invites.list_invites(self.school)), 1)
        self.assertEqual(int(self.school.get_user_by_email(NEW)["is_test"]), 1)
        page = self.client.get("/it").get_data(as_text=True)
        self.assertIn("The invite is saved, but the email didn't send. Copy the link and send it yourself.", page)
        self.assertIn("data-copy-invite-link=", page)
        self.assertIn('<span class="badge badge-test">Test</span>', page)
        again = self.client.get("/it").get_data(as_text=True)
        self.assertNotIn("data-copy-invite-link=", again)

    def test_reinvite_is_idempotent_and_rate_limited(self) -> None:
        """Same email again: one invite row; a double click is refused."""
        self.client.post("/it/invites", data={"email": NEW, "first_name": "Rae"})
        self.client.post("/it/invites", data={"email": NEW, "first_name": "Rae"})
        invites = staff_invites.list_invites(self.school)
        self.assertEqual(len(invites), 1)
        self.assertEqual(invites[0]["send_count"], 1)
        page = self.client.get("/it").get_data(as_text=True)
        self.assertIn("That invite was just sent.", page)
        self.school.conn.execute("UPDATE staff_invites SET sent_at = '2000-01-01T00:00:00'")
        self.client.post("/it/invites", data={"email": NEW, "first_name": "Rae"})
        self.assertEqual(staff_invites.list_invites(self.school)[0]["send_count"], 2)

    def test_hourly_admin_cap(self) -> None:
        """More than 20 sends an hour from one Admin are refused."""
        with patch.object(staff_invites, "SENDS_PER_ADMIN_PER_HOUR", 2):
            for n in range(3):
                self.client.post("/it/invites", data={"email": f"t{n}@gmail.com", "first_name": "T"})
        self.assertIsNone(self.school.get_user_by_email("t2@gmail.com"))
        self.assertIn("Too many invites in the last hour.", self.client.get("/it").get_data(as_text=True))

    def test_preset_must_come_from_the_lists(self) -> None:
        """Half a preset or an off-list value is refused, nothing is created."""
        for data in (
            {"preset_code": "SBI4U"},
            {"preset_code": "ZZZ9Z", "preset_days": "M/W/F", "preset_time": "2:00pm"},
            {"preset_code": "SBI4U", "preset_days": "M/T", "preset_time": "2:00pm"},
            {"preset_code": "SBI4U", "preset_days": "M/W/F", "preset_time": "1:00pm"},
        ):
            self.client.post("/it/invites", data={"email": NEW, "first_name": "R", **data})
            self.assertIsNone(self.school.get_user_by_email(NEW), data)
        self.assertIn("Pick a course, days and a start time to continue.", self.client.get("/it").get_data(as_text=True))

    def test_staff_cannot_send_invites(self) -> None:
        """Only Admin."""
        teacher = self.app.test_client()
        self.school.register_staff("t@gmail.com")
        teacher.get("/auth/google?portal=staff")
        teacher.get("/auth/google/callback?email=t@gmail.com&name=T")
        teacher.post("/verify-email", data={"code": self.school.get_user_by_email("t@gmail.com")["verification_code"]})
        rv = teacher.post("/it/invites", data={"email": NEW})
        self.assertEqual(rv.status_code, 403)
        self.assertIsNone(self.school.get_user_by_email(NEW))


class EmailServiceTests(unittest.TestCase):
    """Reply-To on both transports, and escaped HTML."""

    def test_resend_carries_reply_to_and_from_name(self) -> None:
        """Resend JSON has reply_to and the display name on EMAIL_FROM's address."""
        captured = {}

        class _Resp:
            status_code = 200
            text = "{}"

        def fake_post(url, headers=None, json=None, timeout=None):
            """Capture the Resend payload."""
            captured.update(json)
            return _Resp()

        with patch.dict(os.environ, {"RESEND_API_KEY": "k", "EMAIL_FROM": "ALC <noreply@mckenzian.com>"}, clear=False):
            with patch.object(email_service.requests, "post", side_effect=fake_post):
                ok = email_service.send_staff_invite(NEW, "Rae", "https://x/invite/t", reply_to="shawn@example.com")
        self.assertTrue(ok)
        self.assertEqual(captured["reply_to"], "shawn@example.com")
        self.assertEqual(captured["from"], "Shawn McKenzie via ALC <noreply@mckenzian.com>")

    def test_smtp_sets_reply_to_header(self) -> None:
        """SMTP message has Reply-To."""
        sent = {}

        class _SMTP:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def ehlo(self):
                pass

            def starttls(self, context=None):
                pass

            def login(self, u, p):
                pass

            def sendmail(self, frm, to, msg):
                sent["msg"] = msg

        env = {"RESEND_API_KEY": "", "SMTP_SERVER": "s", "SMTP_USERNAME": "u@x.com", "SMTP_PASSWORD": "p"}
        with patch.dict(os.environ, env, clear=False):
            with patch.object(email_service.smtplib, "SMTP", _SMTP):
                ok = email_service.send_staff_invite(NEW, "Rae", "https://x/invite/t", reply_to="shawn@example.com")
        self.assertTrue(ok)
        self.assertIn("Reply-To: shawn@example.com", sent["msg"])

    def test_invite_and_access_request_html_are_escaped(self) -> None:
        """Names never go into HTML raw."""
        _s, _t, html = email_service.build_staff_invite("<b>Rae</b>", "a@b.c", "https://x/invite/t\"><script>")
        self.assertNotIn("<b>Rae</b>", html)
        self.assertIn("&lt;b&gt;Rae&lt;/b&gt;", html)
        self.assertNotIn('"><script>', html)
        captured = {}
        with patch.object(email_service, "send_email", side_effect=lambda to, s, t, h, **k: captured.setdefault("html", h) and True):
            email_service.send_access_request_notice(
                {"name": "<img src=x onerror=alert(1)>", "email": "e@x.com", "role": "teacher", "context": "<script>x</script>"}
            )
        self.assertNotIn("<img src=x", captured["html"])
        self.assertNotIn("<script>x</script>", captured["html"])


if __name__ == "__main__":
    unittest.main()

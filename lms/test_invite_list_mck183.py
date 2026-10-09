#!/usr/bin/env python3
"""MCK-183 I4: Admin Invites card (Pending / Joined / Expired, Resend, Copy link, Revoke)."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

import staff_invites  # noqa: E402
from test_staff_invites_mck183 import NEW, _Base  # noqa: E402


class InviteListTests(_Base):
    """The Invites card on Admin → Staff."""

    def setUp(self) -> None:
        """Admin signed in, one pending invite."""
        super().setUp()
        self.invite, self.token = staff_invites.create_or_refresh_invite(
            self.school, email=NEW, first_name="Rae", kind="test",
            preset=("SBI4U", "M/W/F", "2:00pm"),
        )
        self._login_it()

    def _expire(self) -> None:
        """Push the invite past its expiry."""
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        self.school.conn.execute("UPDATE staff_invites SET expires_at = ?", (past,))
        self.school.conn.commit()

    def test_card_tabs_rows_and_empty_copy(self) -> None:
        """Pending row with Test tag, preset, days left; empty tabs say so."""
        html = self.client.get("/it").get_data(as_text=True)
        card = html.split('id="invites"')[1].split("</section>")[0]
        for text in ("Invites", ">Pending", ">Joined", ">Expired", "Rae", NEW,
                     "SBI4U · MWF · 2:00pm", "days left", "Resend", "Copy link", "Revoke"):
            self.assertIn(text, card)
        self.assertIn('<span class="badge badge-test">Test</span>', card)
        self.assertEqual(card.count("No invites here."), 2)
        self.assertIn("Their link stops working and they won't be able to sign in. "
                      "You can invite them again later.", card)

    def test_days_left_labels(self) -> None:
        """Wonder's inv.list.left."""
        self.assertEqual(staff_invites.days_left_label(7 * 86400), "7 days left")
        self.assertEqual(staff_invites.days_left_label(86400 + 60), "1 day left")
        self.assertEqual(staff_invites.days_left_label(3600), "Expires today")

    def test_expired_and_joined_tabs(self) -> None:
        """Expired shows in Expired; a joined invite moves to Joined without actions."""
        self._expire()
        tabs = staff_invites.invite_tabs(self.school)
        self.assertEqual([r["email"] for r in tabs["expired"]], [NEW])
        self.assertEqual(tabs["pending"], [])
        staff_invites.mark_accepted(self.school, int(self.invite["id"]))
        tabs = staff_invites.invite_tabs(self.school)
        self.assertEqual([r["email"] for r in tabs["joined"]], [NEW])
        rv = self.client.post(f"/it/invites/{self.invite['id']}/link")
        self.assertEqual(rv.status_code, 409)

    def test_resend_resets_expiry_and_voids_old_link(self) -> None:
        """Resend: new 7-day link, email sent, old link void; double click is limited."""
        self._expire()
        with patch("email_service.send_email", return_value=True) as send:
            rv = self.client.post(f"/it/invites/{self.invite['id']}/resend")
        self.assertEqual(rv.status_code, 302)
        self.assertTrue(rv.headers["Location"].endswith("/it#invites"))
        send.assert_called_once()
        body = send.call_args.kwargs.get("text_body") or str(send.call_args)
        self.assertIn("/invite/", body)
        row = staff_invites.get_invite(self.school, int(self.invite["id"]))
        self.assertEqual(staff_invites.invite_status(row), "pending")
        left = datetime.fromisoformat(row["expires_at"]) - datetime.now()
        self.assertGreater(left, timedelta(days=6, hours=23))
        self.assertEqual(staff_invites.resolve_invite(self.school, self.token)[0], "unknown")
        with patch("email_service.send_email", return_value=True) as again:
            self.client.post(f"/it/invites/{self.invite['id']}/resend")
        again.assert_not_called()
        html = self.client.get("/it").get_data(as_text=True)
        self.assertIn("it-invite-toast is-error", html)

    def test_copy_link_mints_new_link_without_email(self) -> None:
        """Copy link returns a fresh working link; the old one stops working."""
        with patch("email_service.send_email") as send:
            rv = self.client.post(f"/it/invites/{self.invite['id']}/link")
        send.assert_not_called()
        link = rv.get_json()["link"]
        new_token = link.rsplit("/invite/", 1)[1]
        self.assertEqual(staff_invites.resolve_invite(self.school, new_token)[0], "valid")
        self.assertEqual(staff_invites.resolve_invite(self.school, self.token)[0], "unknown")
        html = self.client.get("/it").get_data(as_text=True)
        self.assertIn("Link copied. Older links for this invite stop working.", html)

    def test_revoke_archives_user_and_blocks_sign_in(self) -> None:
        """Revoke: link dead, allowlist row archived, Google sign-in refused."""
        rv = self.client.post(f"/it/invites/{self.invite['id']}/revoke")
        self.assertEqual(rv.status_code, 302)
        self.assertEqual(staff_invites.resolve_invite(self.school, self.token)[0], "revoked")
        user = self.school.get_user_by_email(NEW)
        self.assertTrue(user.get("archived_at"))
        tabs = staff_invites.invite_tabs(self.school)
        self.assertFalse(any(tabs.values()))
        teacher = self.app.test_client()
        page = teacher.get(f"/invite/{self.token}").get_data(as_text=True)
        self.assertIn("This invite link doesn't work", page)
        teacher.get("/auth/google?portal=staff")
        out = teacher.get(f"/auth/google/callback?email={NEW}&name=Rae")
        self.assertNotIn("/staff", out.headers.get("Location", ""))
        self.assertEqual(teacher.get("/staff").status_code, 302)
        with teacher.session_transaction() as sess:
            self.assertFalse(sess.get("logged_in"))

    def test_existing_teacher_is_never_changed_by_invite_or_revoke(self) -> None:
        """#269 gate MED: is_test stays 0 and Revoke leaves her account alone."""
        real = self.school.register_staff("percival.real@gmail.com")
        uid = int(real["id"])
        self.school.assign_course(teacher_user_id=uid, ontario_code="MCF3M")
        inv, _tok = staff_invites.create_or_refresh_invite(
            self.school, email="percival.real@gmail.com", first_name="P", kind="test",
        )
        self.assertEqual(int(self.school.get_user(uid).get("is_test") or 0), 0)
        self.assertEqual(int(inv.get("owns_account") or 0), 0)
        # Resend refreshes the same invite and still owns nothing.
        inv2, _ = staff_invites.create_or_refresh_invite(
            self.school, email="percival.real@gmail.com", first_name="P", kind="test",
        )
        self.assertEqual(int(inv2["id"]), int(inv["id"]))
        self.assertEqual(int(self.school.get_user(uid).get("is_test") or 0), 0)
        rv = self.client.post(f"/it/invites/{inv['id']}/revoke")
        self.assertEqual(rv.status_code, 302)
        user = self.school.get_user(uid)
        self.assertFalse(user.get("archived_at"))
        self.assertEqual(int(user.get("is_test") or 0), 0)
        self.assertEqual(staff_invites.get_invite(self.school, int(inv["id"]))["revoked_at"] is not None, True)

    def test_admin_send_refuses_existing_teacher(self) -> None:
        """Admin → Send invite to an active teacher: refused, nothing changes."""
        real = self.school.register_staff("percival.real@gmail.com")
        with patch("app.send_email", return_value=True, create=True):
            rv = self.client.post("/it/invites", data={
                "email": "percival.real@gmail.com", "first_name": "P", "kind": "test",
            })
        self.assertEqual(rv.status_code, 302)
        self.assertIsNone(staff_invites.open_invite_for(self.school, 1, "percival.real@gmail.com"))
        self.assertEqual(int(self.school.get_user(int(real["id"])).get("is_test") or 0), 0)
        page = self.client.get("/it").get_data(as_text=True)
        self.assertIn("That teacher already has an account and can sign in.", page)

    def test_invite_owned_account_refresh_and_reactivation(self) -> None:
        """An invite-made account stays owned on resend; a revoked one reactivates and is owned."""
        self.assertEqual(int(self.invite.get("owns_account") or 0), 1)
        again, _ = staff_invites.create_or_refresh_invite(
            self.school, email=NEW, first_name="Rae", kind="teacher",
        )
        self.assertEqual(int(again["owns_account"]), 1)
        user = self.school.get_user_by_email(NEW)
        self.assertEqual(int(user.get("is_test") or 0), 0)
        staff_invites.revoke_invite(self.school, int(again["id"]), int(self.it["id"]) if hasattr(self, "it") else 1)
        self.assertTrue(self.school.get_user_by_email(NEW).get("archived_at"))
        back, _ = staff_invites.create_or_refresh_invite(
            self.school, email=NEW, first_name="Rae", kind="test",
        )
        self.assertEqual(int(back["owns_account"]), 1)
        self.assertFalse(self.school.get_user_by_email(NEW).get("archived_at"))
        self.assertEqual(int(self.school.get_user_by_email(NEW).get("is_test") or 0), 1)

    def test_staff_only_and_other_school(self) -> None:
        """Teachers can't use the invite actions; unknown ids 404."""
        self.assertEqual(self.client.post("/it/invites/9999/link").status_code, 404)
        teacher = self.app.test_client()
        rv = teacher.post(f"/it/invites/{self.invite['id']}/revoke")
        self.assertIn(rv.status_code, (302, 401, 403))
        self.assertIsNone(staff_invites.get_invite(self.school, int(self.invite["id"]))["revoked_at"])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""MCK-183 slice A: the /welcome link, ``next`` through sign-in, first-run gate."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)

from app import create_app  # noqa: E402
from auth import _safe_next_url  # noqa: E402

NEW = "new.teacher@gmail.com"


class WelcomeGateTests(unittest.TestCase):
    """A brand-new teacher follows the link and lands on Welcome."""

    def setUp(self) -> None:
        """Fresh school with an active semester and one allowlisted teacher."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff(NEW)
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _sign_in(self, email: str, start: str):
        """Follow ``start`` to Google, then mock callback and the email code."""
        first = self.client.get(start)
        location = first.headers.get("Location", "")
        if location.startswith("/auth/google") or "/auth/google?" in location:
            self.client.get(location)
        callback = self.client.get(f"/auth/google/callback?email={email}&name=T")
        if "/verify-email" not in callback.headers.get("Location", ""):
            return callback
        user = self.school.get_user_by_email(email)
        return self.client.post("/verify-email", data={"code": user["verification_code"]})

    def test_welcome_link_survives_the_first_login_code(self) -> None:
        """/welcome → Google → email code → /staff/welcome (not the Dashboard)."""
        start = self.client.get("/welcome")
        self.assertEqual(start.status_code, 302)
        parsed = urlsplit(start.headers["Location"])
        self.assertEqual(parsed.path, "/auth/google")
        self.assertEqual(parse_qs(parsed.query)["next"], ["/staff/welcome"])
        done = self._sign_in(NEW, "/welcome")
        self.assertEqual(done.status_code, 302)
        self.assertTrue(done.headers["Location"].endswith("/staff/welcome"), done.headers["Location"])
        page = self.client.get("/staff/welcome").get_data(as_text=True)
        self.assertIn("Welcome to ALC", page)
        self.assertIn(
            "ALC takes attendance and tracks participation while you teach. "
            "Students join on their own phones or laptops. This setup takes about 3 minutes.",
            page,
        )
        self.assertIn("Get started", page)

    def test_staff_required_keeps_next(self) -> None:
        """A signed-out deep link comes back to the same page after sign-in."""
        rv = self.client.get("/staff/welcome?step=class")
        parsed = urlsplit(rv.headers["Location"])
        self.assertEqual(parsed.path, "/auth/google")
        self.assertEqual(parse_qs(parsed.query)["next"], ["/staff/welcome?step=class"])
        done = self._sign_in(NEW, "/staff/welcome?step=class")
        self.assertTrue(done.headers["Location"].endswith("/staff/welcome?step=class"))

    def test_api_routes_still_401_without_redirect(self) -> None:
        """JSON callers keep the 401, not a Google redirect."""
        rv = self.client.get("/api/staff/classes")
        self.assertEqual(rv.status_code, 401)

    def test_staff_sends_a_teacher_with_nothing_to_welcome(self) -> None:
        """No course and no class: /staff → /staff/welcome."""
        self._sign_in(NEW, "/staff")
        rv = self.client.get("/staff")
        self.assertEqual(rv.status_code, 302)
        self.assertTrue(rv.headers["Location"].endswith("/staff/welcome"))

    def test_teacher_with_a_course_keeps_the_dashboard(self) -> None:
        """An assigned course (Populate Class path) or a class: no gate."""
        self.school.assign_course(teacher_user_id=int(self.teacher["id"]), ontario_code="SBI4U")
        self._sign_in(NEW, "/staff")
        rv = self.client.get("/staff")
        self.assertEqual(rv.status_code, 200)

    def test_teacher_with_a_class_skips_welcome(self) -> None:
        """Welcome itself sends a set-up teacher to the Dashboard."""
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="SBI4U"
        )
        self._sign_in(NEW, "/staff")
        self.client.post(
            "/api/staff/classes",
            json={"offering_id": offering["id"], "days": "M/W/F", "time": "2:00pm", "codenames": ["Ava"]},
        )
        rv = self.client.get("/staff/welcome")
        self.assertEqual(rv.status_code, 302)
        self.assertTrue(rv.headers["Location"].endswith("/staff"))

    def test_unlisted_email_gets_the_same_403(self) -> None:
        """The link is not a sign-up: an unknown Google account is refused."""
        self.client.get("/welcome")
        self.client.get("/auth/google?portal=staff&next=/staff/welcome")
        rv = self.client.get("/auth/google/callback?email=stranger@gmail.com&name=S")
        self.assertEqual(rv.status_code, 403)
        self.assertIn("This Google account is not registered.", rv.get_data(as_text=True))

    def test_signed_in_teacher_link_goes_straight_to_welcome(self) -> None:
        """Already signed in: /welcome and /auth/google honour next."""
        self._sign_in(NEW, "/staff")
        self.assertTrue(self.client.get("/welcome").headers["Location"].endswith("/staff/welcome"))
        rv = self.client.get("/auth/google?portal=staff&next=/staff/welcome")
        self.assertTrue(rv.headers["Location"].endswith("/staff/welcome"))

    def test_safe_next_rejects_offsite_targets(self) -> None:
        """Only same-site paths survive."""
        self.assertEqual(_safe_next_url("/staff/welcome"), "/staff/welcome")
        for bad in ("//evil.com", "https://evil.com", "/\\evil.com", "/x\r\nSet-Cookie:a", None, ""):
            self.assertIsNone(_safe_next_url(bad), bad)
        start = self.client.get("/auth/google?portal=staff&next=//evil.com")
        self.assertNotIn("google_oauth_next", dict(self._session()))
        del start

    def _session(self):
        """Read the test client's session."""
        with self.client.session_transaction() as sess:
            return dict(sess)

    def test_it_account_keeps_the_dashboard(self) -> None:
        """Admin (IT) with no class is not sent to Welcome."""
        it_email = self.school.get_user_by_id(1)["email"] if self.school.get_user_by_id(1) else None
        if not it_email:
            self.skipTest("no seeded IT user")
        user = self.school.get_user_by_id(1)
        if user.get("role") != "it":
            self.skipTest("first user is not IT")
        self._sign_in(it_email, "/staff")
        with self.client.session_transaction() as sess:
            sess["portal"] = "staff"
        rv = self.client.get("/staff")
        self.assertEqual(rv.status_code, 200)


if __name__ == "__main__":
    unittest.main()

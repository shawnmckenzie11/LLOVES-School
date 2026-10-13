#!/usr/bin/env python3
"""MCK-195: ``/`` is the student join; staff routes and links are unchanged."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)

import staff_invites  # noqa: E402
from app import create_app  # noqa: E402


def _href(body: str, label: str) -> str:
    """The href of the top-bar link whose text is ``label``."""
    head = body.split(f">{label}</a>")[0]
    return head.rsplit('href="', 1)[1].split('"')[0].replace("&amp;", "&")


class RootJoinTests(unittest.TestCase):
    """Root URL shows the join form with Teacher/Admin links."""

    def setUp(self) -> None:
        """Fresh school."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        """Close."""
        self.school.close()
        self.tmp.cleanup()

    def test_root_renders_join_form_and_both_links(self) -> None:
        """Code then first name, Join, Teacher/Admin links, #celebrations."""
        body = self.client.get("/").get_data(as_text=True)
        self.assertLess(body.index('id="code"'), body.index('id="student-name"'))
        self.assertIn(">Join</button>", body)
        self.assertNotIn("student-role-btn", body)
        self.assertEqual(_href(body, "Teacher login"), "/auth/google?portal=staff")
        self.assertEqual(_href(body, "Admin login"), "/auth/google?portal=it")
        self.assertIn('id="celebrations"', body)
        self.assertIn('href="#celebrations"', body)
        # MCK-197: no logo in the top bar; the links stay right-aligned
        # and at least 24px tall.
        self.assertNotIn("alc-logo.png", body)
        self.assertNotIn('aria-label="ALC home"', body)
        self.assertIn("justify-content:flex-end", body)
        link_rule = body.split("body.landing-page .join-topbar nav a {", 1)[1].split("}", 1)[0]
        self.assertIn("display:inline-flex", link_rule)
        self.assertIn("min-height:24px", link_rule)

    def test_root_next_staff_reaches_staff_sign_in(self) -> None:
        """``/?next=/staff/...`` → Teacher login carries next → staff sign-in."""
        body = self.client.get("/?next=/staff/welcome").get_data(as_text=True)
        href = _href(body, "Teacher login")
        parsed = urlsplit(href)
        self.assertEqual(parsed.path, "/auth/google")
        q = parse_qs(parsed.query)
        self.assertEqual(q["portal"], ["staff"])
        self.assertEqual(q["next"], ["/staff/welcome"])
        self.assertNotEqual(self.client.get(href).status_code, 404)
        evil = self.client.get("/?next=//evil.example/staff").get_data(as_text=True)
        self.assertEqual(_href(evil, "Teacher login"), "/auth/google?portal=staff")
        for bad in ("/staff/../it", "/staff/%2e%2e/x", "/it/home"):
            page = self.client.get("/", query_string={"next": bad}).get_data(as_text=True)
            self.assertEqual(_href(page, "Teacher login"), "/auth/google?portal=staff", bad)
        self.assertIn("No live class is running right now.", body)

    def test_signed_out_staff_route_goes_to_staff_sign_in(self) -> None:
        """Never to ``/``."""
        rv = self.client.get("/staff/welcome?step=class")
        self.assertEqual(rv.status_code, 302)
        parsed = urlsplit(rv.headers["Location"])
        self.assertEqual(parsed.path, "/auth/google")
        self.assertEqual(parse_qs(parsed.query)["portal"], ["staff"])

    def test_welcome_and_invite_still_work(self) -> None:
        """/welcome → staff sign-in with next; /invite/<token> renders."""
        rv = self.client.get("/welcome")
        parsed = urlsplit(rv.headers["Location"])
        self.assertEqual(parsed.path, "/auth/google")
        self.assertEqual(parse_qs(parsed.query)["next"], ["/staff/welcome"])
        _, token = staff_invites.create_or_refresh_invite(
            self.school, email="rae.teacher@gmail.com", first_name="Rae",
            preset=("SBI4U", "M/W/F", "2:00pm"),
        )
        page = self.client.get(f"/invite/{token}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("You're invited to ALC", page.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()

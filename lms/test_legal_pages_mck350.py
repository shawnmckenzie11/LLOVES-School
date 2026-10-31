"""MCK-350: published privacy, terms, and support pages.

Public pages carry the approved text, noindex, and a shared footer whose
legal name is ALC. The same footer is on the school home, the unknown-account
page, and the staff shell.
"""

from __future__ import annotations

import logging
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
os.environ.pop("GOOGLE_CLIENT_SECRET", None)

import staff_invites  # noqa: E402
from app import create_app  # noqa: E402

LEGAL_HREFS = ('href="/privacy"', 'href="/terms"', 'href="/support"')
PUBLISHED = "October 29, 2026"
# Substring bans from the placeholder pass. "@" is the published contact
# address, so it is not banned. "whiteboard" is approved product language.
BANNED = ("celebration", "slides", "import")


class LegalPageTests(unittest.TestCase):
    """Published legal pages and the shared footer."""

    def setUp(self) -> None:
        """Isolated sqlite and a quiet test client."""
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        """Close the database before removing the temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _assert_footer(self, body: str) -> None:
        """The shared footer names ALC and links the three pages.

        Args:
            body: Rendered HTML.
        """
        self.assertIn('id="legal-footer"', body)
        self.assertIn('class="legal-footer-name">ALC<', body)
        for href in LEGAL_HREFS:
            self.assertIn(href, body)

    def _assert_published(self, path: str, title: str) -> str:
        """One legal page is noindex and shows the approved text.

        Args:
            path: Public path such as ``/privacy``.
            title: Expected document title.

        Returns:
            The rendered HTML.
        """
        rv = self.client.get(path)
        self.assertEqual(rv.status_code, 200)
        self.assertIn("noindex", (rv.headers.get("X-Robots-Tag") or ""))
        body = rv.get_data(as_text=True)
        self.assertIn('<meta name="robots" content="noindex">', body)
        self.assertIn(f"<title>{title} · ALC</title>", body)
        self.assertNotIn('id="legal-pending-banner"', body)
        self.assertNotIn("Text pending Shawn + legal approval", body)
        self.assertIn(f"Last updated: {PUBLISHED}", body)
        self._assert_footer(body)
        lowered = body.lower()
        for word in BANNED:
            self.assertNotIn(word, lowered)
        return body

    def test_privacy_terms_and_support_publish_the_approved_text(self) -> None:
        """Each page shows the approved copy, dated, with no pending banner."""
        privacy = self._assert_published("/privacy", "Privacy Notice")
        terms = self._assert_published("/terms", "Terms of Use")
        support = self._assert_published("/support", "Support")
        self.assertIn("The only student identifier ALC stores is the student's first name", privacy)
        self.assertIn("whiteboard work", privacy)
        self.assertIn("kept with no automatic deletion", privacy)
        self.assertIn(
            'Privacy contact: <a href="mailto:solutions@mckenzian.com">solutions@mckenzian.com</a>',
            privacy,
        )
        self.assertNotIn("not ALC's privacy notice", privacy)
        self.assertIn("Teachers run classes with whiteboards", terms)
        self.assertIn("Prices include applicable taxes.", terms)
        self.assertIn("laws of Ontario", terms)
        self.assertNotIn("not a contract", terms)
        self.assertIn('id="support-request-access"', support)
        self.assertIn('href="/request-access"', support)
        self.assertIn("Request access", support)
        self.assertIn("We reply within 48 hours.", support)
        self.assertIn("How do students join a class?", support)
        self.assertIn('href="/privacy"', support)
        self.assertIn("mailto:solutions@mckenzian.com", support)
        self.assertNotIn("personal contact address", support)
        self.assertNotIn("HELP_CONTACT_EMAIL", support)

    def test_landing_and_request_form_use_the_footer(self) -> None:
        """The school home and the request form link the three pages."""
        for path in ("/", "/request-access"):
            rv = self.client.get(path)
            self.assertEqual(rv.status_code, 200)
            body = rv.get_data(as_text=True)
            self._assert_footer(body)
            self.assertNotIn("noindex", body.lower())
            self.assertNotIn("noindex", (rv.headers.get("X-Robots-Tag") or "").lower())

    def test_unknown_account_page_links_the_footer(self) -> None:
        """The not-registered page keeps Request access and adds the footer."""
        with patch("email_service.send_verification_email", return_value=True):
            self.client.get("/auth/google?portal=staff")
            rv = self.client.get(
                "/auth/google/callback?email=visitor@example.com&portal=staff",
                follow_redirects=False,
            )
        self.assertEqual(rv.status_code, 403)
        body = rv.get_data(as_text=True)
        self.assertIn('id="forbidden-request-access"', body)
        self._assert_footer(body)

    def test_other_forbidden_pages_keep_the_footer_without_the_request_button(self) -> None:
        """A staff account blocked from Admin still gets the legal links."""
        self.school.register_staff("teacher@example.com", "Teacher")
        with patch("email_service.send_verification_email", return_value=True):
            self.client.get("/auth/google?portal=staff")
            self.client.get(
                "/auth/google/callback?email=teacher@example.com&name=Teacher",
                follow_redirects=False,
            )
        user = self.school.get_user_by_email("teacher@example.com")
        self.assertIsNotNone(user)
        code = str(user["verification_code"])
        posted = self.client.post("/verify-email", data={"code": code}, follow_redirects=False)
        self.assertEqual(posted.status_code, 302)
        blocked = self.client.get("/it")
        self.assertEqual(blocked.status_code, 403)
        body = blocked.get_data(as_text=True)
        self.assertNotIn("forbidden-request-access", body)
        self.assertNotIn('href="/request-access"', body)
        self._assert_footer(body)

    def test_staff_shell_renders_the_footer(self) -> None:
        """A signed-in teacher sees the ALC footer on the staff shell."""
        self.school.register_staff("teacher@example.com", "Teacher")
        with patch("email_service.send_verification_email", return_value=True):
            self.client.get("/auth/google?portal=staff")
            self.client.get(
                "/auth/google/callback?email=teacher@example.com&name=Teacher",
                follow_redirects=False,
            )
        user = self.school.get_user_by_email("teacher@example.com")
        self.assertIsNotNone(user)
        code = str(user["verification_code"])
        self.client.post("/verify-email", data={"code": code}, follow_redirects=False)
        page = self.client.get("/staff", follow_redirects=True)
        self.assertEqual(page.status_code, 200)
        self._assert_footer(page.get_data(as_text=True))

    def test_public_pages_omit_the_owner_name(self) -> None:
        """Legal, landing, invite, and forbidden pages omit the owner's name.

        The company name and the solutions@ address stay. The surname check
        is the word followed by a space, so "McKenzian" is not a hit.
        """
        bodies: list[str] = []
        for path in ("/privacy", "/terms", "/support", "/"):
            rv = self.client.get(path)
            self.assertEqual(rv.status_code, 200, path)
            bodies.append(rv.get_data(as_text=True))

        unknown = self.client.get("/invite/not-a-token")
        self.assertEqual(unknown.status_code, 200)
        bodies.append(unknown.get_data(as_text=True))

        self.school.activate_from_semester_json()
        _invite, token = staff_invites.create_or_refresh_invite(
            self.school, email="guest@example.com", first_name="Guest",
        )
        past = (datetime.now() - timedelta(minutes=1)).isoformat()
        self.school.conn.execute("UPDATE staff_invites SET expires_at = ?", (past,))
        expired = self.client.get(f"/invite/{token}")
        self.assertEqual(expired.status_code, 200)
        expired_html = expired.get_data(as_text=True)
        self.assertIn("This invite has expired", expired_html)
        bodies.append(expired_html)

        with patch("email_service.send_verification_email", return_value=True):
            self.client.get("/auth/google?portal=staff")
            forbidden = self.client.get(
                "/auth/google/callback?email=visitor@example.com&portal=staff",
                follow_redirects=False,
            )
        self.assertEqual(forbidden.status_code, 403)
        bodies.append(forbidden.get_data(as_text=True))

        for body in bodies:
            self.assertNotIn("Shawn", body)
            self.assertNotIn("McKenzie ", body)


if __name__ == "__main__":
    unittest.main()

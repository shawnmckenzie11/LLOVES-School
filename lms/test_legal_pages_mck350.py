"""MCK-350: privacy, terms, and support placeholders.

Public pages carry the pending-approval banner, noindex, and a shared
footer whose legal name is ALC. The same footer is on the school home,
the unknown-account page, and the staff shell.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)

from app import create_app  # noqa: E402

BANNER = "Text pending Shawn + legal approval"
LEGAL_HREFS = ('href="/privacy"', 'href="/terms"', 'href="/support"')
BANNED = ("celebration", "slides", "import", "@")


class LegalPageTests(unittest.TestCase):
    """Placeholder legal pages and the shared footer."""

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

    def _assert_placeholder(self, path: str, title: str) -> str:
        """One legal page is noindex and shows the pending banner.

        Args:
            path: Public path such as ``/privacy``.
            title: Expected document title stem.

        Returns:
            The rendered HTML.
        """
        rv = self.client.get(path)
        self.assertEqual(rv.status_code, 200)
        self.assertIn("noindex", (rv.headers.get("X-Robots-Tag") or ""))
        body = rv.get_data(as_text=True)
        self.assertIn('<meta name="robots" content="noindex">', body)
        self.assertIn(f"<title>{title} · ALC</title>", body)
        self.assertIn(f'id="legal-pending-banner"', body)
        self.assertIn(BANNER, body)
        self._assert_footer(body)
        lowered = body.lower()
        for word in BANNED:
            self.assertNotIn(word, lowered)
        return body

    def test_privacy_terms_and_support_are_noindex_placeholders(self) -> None:
        """Each page shows the banner and does not invent a contact address."""
        privacy = self._assert_placeholder("/privacy", "Privacy")
        terms = self._assert_placeholder("/terms", "Terms")
        support = self._assert_placeholder("/support", "Support")
        self.assertIn("not ALC's privacy notice", privacy)
        self.assertIn("not a contract", terms)
        self.assertIn('id="support-request-access"', support)
        self.assertIn('href="/request-access"', support)
        self.assertIn("Request access", support)
        self.assertIn("personal contact address", support)
        self.assertNotIn("mailto:", support.lower())

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


if __name__ == "__main__":
    unittest.main()

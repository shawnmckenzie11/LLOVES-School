"""MCK-539: read-only teacher Settings page and Account menu."""

from __future__ import annotations

import html as html_module
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from features import BASIC_OFF, PLAN_BASIC, PLAN_PRO, set_feature_override  # noqa: E402

_TOKEN_MARKERS = (
    "refresh_token",
    "access_token",
    "google_sub",
    "connect_url",
)


def _sign_in_staff(client, user_id: int) -> None:
    """Sign in as staff without the launch feature grant."""
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_id"] = int(user_id)
        sess["portal"] = "staff"
        sess["role"] = "staff"


class StaffSettingsTests(unittest.TestCase):
    """Settings renders for Basic and Pro; no secrets; students denied."""

    def setUp(self) -> None:
        self._prev_local = os.environ.get("LOCAL_DEV_LOGIN")
        os.environ.pop("LOCAL_DEV_LOGIN", None)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
            live_database_url="",
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.client = self.app.test_client()
        self.basic = self.school.register_staff("basic-settings@example.com")
        self.pro_email = "pro-settings@example.com"
        self.pro = self.school.register_staff(self.pro_email)
        self.school.conn.execute(
            "UPDATE users SET plan = ? WHERE id = ?",
            (PLAN_PRO, int(self.pro["id"])),
        )

    def tearDown(self) -> None:
        self.school.close()
        self.tmp.cleanup()
        if self._prev_local is None:
            os.environ.pop("LOCAL_DEV_LOGIN", None)
        else:
            os.environ["LOCAL_DEV_LOGIN"] = self._prev_local

    def _html(self, user_id: int, section: str = "plan") -> str:
        _sign_in_staff(self.client, user_id)
        resp = self.client.get(f"/staff/settings?section={section}")
        self.assertEqual(resp.status_code, 200)
        return html_module.unescape(resp.get_data(as_text=True))

    def test_basic_teacher_plan_copy(self) -> None:
        """Basic plan card shows Wonder lines and a Pro-gated row."""
        html = self._html(int(self.basic["id"]))
        self.assertIn("You're on Basic", html)
        self.assertIn("Managed by your admin", html)
        self.assertIn("To get Pro features, ask your admin.", html)
        self.assertIn("Whiteboards", html)
        self.assertIn("staff-plan-pro-badge", html)
        self.assertIn("Catch-up (all classes)", html)

    def test_pro_teacher_plan_copy(self) -> None:
        """Pro teachers see the Pro headline without the upsell line."""
        html = self._html(int(self.pro["id"]))
        self.assertIn("You're on Pro", html)
        self.assertNotIn("To get Pro features, ask your admin.", html)

    def test_override_note_only_when_differs_from_plan(self) -> None:
        """Notes appear only when an override changes plan defaults."""
        uid = int(self.basic["id"])
        set_feature_override(self.school, uid, "whiteboard", True)
        html = self._html(uid)
        self.assertIn("Whiteboards is on for your account", html)
        set_feature_override(self.school, uid, "mc_questions", True)
        html2 = self._html(uid)
        self.assertNotIn("Multiple choice is on for your account", html2)

    def test_grandfathered_overrides_summarized(self) -> None:
        """Many plan-differing on rows collapse to one summary line."""
        uid = int(self.basic["id"])
        for key in BASIC_OFF:
            set_feature_override(self.school, uid, key, True)
        html = self._html(uid)
        self.assertIn("features beyond your Basic plan defaults", html)
        self.assertNotIn("Whiteboards is on for your account", html)

    def test_effective_access_in_you_column(self) -> None:
        """Overrides show included for the teacher without a Pro badge."""
        uid = int(self.basic["id"])
        set_feature_override(self.school, uid, "whiteboard", True)
        html = self._html(uid)
        idx = html.find("Whiteboards")
        self.assertGreater(idx, -1)
        snippet = html[idx : idx + 220]
        self.assertNotIn("staff-plan-pro-badge", snippet)
        self.assertIn('aria-label="Included for you"', html)

    def test_no_token_strings_in_response(self) -> None:
        """Connected apps never echo OAuth material."""
        operator = self.school.register_staff("operator-settings@example.com")
        self.school.conn.execute(
            "UPDATE users SET plan = ? WHERE id = ?",
            (PLAN_PRO, int(operator["id"])),
        )
        self.school.store_google_api_token(
            int(operator["id"]),
            refresh_token="SECRET_REFRESH_SHOULD_NOT_APPEAR",
            scopes="https://www.googleapis.com/auth/presentations.readonly",
            access_token="SECRET_ACCESS_SHOULD_NOT_APPEAR",
            expires_in=3500,
        )
        html = self._html(int(operator["id"]), section="connected")
        self.assertIn("Connected", html)
        for marker in _TOKEN_MARKERS:
            self.assertNotIn(marker, html, msg=marker)
        self.assertNotIn("SECRET_REFRESH_SHOULD_NOT_APPEAR", html)
        self.assertNotIn("SECRET_ACCESS_SHOULD_NOT_APPEAR", html)

    def test_non_staff_denied(self) -> None:
        """Student sessions cannot open Settings."""
        tenant = self.school.default_tenant_id()
        self.school.conn.execute(
            """
            INSERT INTO users (email, display_name, role, tenant_id, created_at, plan)
            VALUES (?, ?, 'student', ?, datetime('now'), 'basic')
            """,
            ("student-settings@example.com", "Student Example", tenant),
        )
        student_id = int(
            self.school.conn.execute(
                "SELECT id FROM users WHERE email = ?",
                ("student-settings@example.com",),
            ).fetchone()["id"]
        )
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["user_id"] = student_id
            sess["portal"] = "staff"
            sess["role"] = "student"
        resp = self.client.get("/staff/settings")
        self.assertEqual(resp.status_code, 403)

    def test_billing_placeholder_copy(self) -> None:
        """Billing section is non-interactive Wonder copy."""
        html = self._html(int(self.basic["id"]), section="billing")
        self.assertIn(
            "Billing isn't set up yet. Your plan is managed by your admin.",
            html,
        )
        self.assertNotRegex(html, re.compile(r'next class Start', re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()
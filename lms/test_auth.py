#!/usr/bin/env python3
"""Auth tests for LLOVES: Google allowlist, portals, 2SV, student code."""

from __future__ import annotations

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


class AuthTests(unittest.TestCase):
    """Google mock login, portal gating, student-code rate limit."""

    def setUp(self) -> None:
        """Isolated sqlite + Flask test client."""
        self._env_prev = {
            key: os.environ.get(key)
            for key in (
                "FLASK_ENV",
                "RESEND_API_KEY",
                "SMTP_SERVER",
                "SMTP_USERNAME",
                "SMTP_PASSWORD",
            )
        }
        for key in self._env_prev:
            if key != "FLASK_ENV":
                os.environ.pop(key, None)
        os.environ.pop("FLASK_ENV", None)
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
        """Close db and temp dir; restore env."""
        self.school.close()
        self.tmp.cleanup()
        for key, value in self._env_prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _callback(self, email: str, portal: str = "staff"):
        """Mock Google OAuth: start then callback with an email."""
        self.client.get(f"/auth/google?portal={portal}")
        return self.client.get(
            f"/auth/google/callback?email={email}&name=Test",
            follow_redirects=False,
        )

    def _complete_2sv(self, email: str) -> None:
        """Submit the stored first-login email code."""
        user = self.school.get_user_by_email(email)
        assert user is not None
        code = user["verification_code"]
        self.assertTrue(code)
        rv = self.client.post("/verify-email", data={"code": code}, follow_redirects=False)
        self.assertEqual(rv.status_code, 302)

    def test_landing_has_teacher_student_and_admin_lock(self) -> None:
        """Public landing shows Teacher/Student roles and Admin lock."""
        rv = self.client.get("/")
        self.assertEqual(rv.status_code, 200)
        body = rv.get_data(as_text=True)
        self.assertIn("Teacher", body)
        self.assertIn("Student", body)
        self.assertIn("Select your role:", body)
        self.assertIn("Admin login", body)
        self.assertIn("auth/google?portal=it", body)
        self.assertIn("Learning Live Online Virtually", body)
        self.assertIn('class="brand-edge"', body)
        self.assertIn("Take attendance and log participation", body)
        self.assertNotIn("Staff Login", body)
        self.assertNotIn(">ELC<", body)

    def test_unknown_google_403(self) -> None:
        """Unknown Google accounts are not auto-created."""
        rv = self._callback("stranger@gmail.com", "staff")
        self.assertEqual(rv.status_code, 403)
        self.assertIn("not registered", rv.get_data(as_text=True))
        self.assertIsNone(self.school.get_user_by_email("stranger@gmail.com"))

    def test_it_email_may_use_staff_portal(self) -> None:
        """Shawn's IT email can complete Staff Login after 2SV."""
        rv = self._callback("solutions@mckenzian.com", "staff")
        self.assertEqual(rv.status_code, 302)
        self.assertIn("/verify-email", rv.headers.get("Location", ""))
        self._complete_2sv("solutions@mckenzian.com")
        home = self.client.get("/staff")
        self.assertEqual(home.status_code, 200)

    def test_staff_cannot_use_it_portal(self) -> None:
        """Registered staff clicking IT get 403."""
        self.school.register_staff("teacher@gmail.com")
        rv = self._callback("teacher@gmail.com", "it")
        self.assertEqual(rv.status_code, 403)
        self.assertIn("Ask Admin", rv.get_data(as_text=True))

    def test_first_login_requires_email_code_second_skips(self) -> None:
        """First LLOVES login is 2SV; later Google logins skip the email code."""
        self.school.register_staff("teacher@gmail.com")
        with patch("email_service.send_verification_email", return_value=True) as mocked:
            first = self._callback("teacher@gmail.com", "staff")
            mocked.assert_called_once()
            self.assertEqual(mocked.call_args.args[0], "teacher@gmail.com")
            self.assertRegex(str(mocked.call_args.args[2]), r"^\d{6}$")
        self.assertIn("/verify-email", first.headers.get("Location", ""))
        self.assertIn("sent=1", first.headers.get("Location", ""))
        self._complete_2sv("teacher@gmail.com")
        self.client.get("/logout")
        second = self._callback("teacher@gmail.com", "staff")
        self.assertEqual(second.status_code, 302)
        self.assertIn("/staff", second.headers.get("Location", ""))

    def test_production_never_shows_verification_code(self) -> None:
        """FLASK_ENV=production hides the on-page code even if ALLOW_DEV is on."""
        self.school.register_staff("teacher@gmail.com")
        self._callback("teacher@gmail.com", "staff")
        user = self.school.get_user_by_email("teacher@gmail.com")
        assert user is not None
        code = str(user["verification_code"])
        os.environ["FLASK_ENV"] = "production"
        os.environ["ALLOW_DEV_VERIFICATION_CODE"] = "1"
        rv = self.client.get("/verify-email")
        body = rv.get_data(as_text=True)
        self.assertEqual(rv.status_code, 200)
        self.assertNotIn(code, body)
        self.assertNotIn("Dev code", body)

    def test_configured_email_hides_on_page_code(self) -> None:
        """When Resend is configured, the verify page does not print the code."""
        os.environ["RESEND_API_KEY"] = "re_test_not_a_real_key"
        self.school.register_staff("teacher@gmail.com")
        with patch("email_service.send_verification_email", return_value=True):
            self._callback("teacher@gmail.com", "staff")
        user = self.school.get_user_by_email("teacher@gmail.com")
        assert user is not None
        code = str(user["verification_code"])
        rv = self.client.get("/verify-email")
        body = rv.get_data(as_text=True)
        self.assertNotIn(code, body)
        self.assertNotIn("Dev code", body)

    def test_dev_page_shows_code_when_email_is_off(self) -> None:
        """Local ALLOW_DEV + no Resend/SMTP still prints the code for developers."""
        self.school.register_staff("teacher@gmail.com")
        self._callback("teacher@gmail.com", "staff")
        user = self.school.get_user_by_email("teacher@gmail.com")
        assert user is not None
        code = str(user["verification_code"])
        rv = self.client.get("/verify-email")
        self.assertIn(code, rv.get_data(as_text=True))

    def test_wrong_student_code_401(self) -> None:
        """Unknown codes are rejected (no active session → idle message)."""
        rv = self.client.post("/auth/student-code", data={"code": "ABCD2345", "name": "Maple"})
        self.assertEqual(rv.status_code, 401)
        self.assertIn(b"No live class is running", rv.data)

    def test_unknown_student_name_401(self) -> None:
        """A valid session code with an unknown roster name is rejected."""
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCF3M"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.client.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple"],
            },
        )
        self.assertEqual(created.status_code, 200)
        class_id = int(created.get_json()["class"]["id"])
        self.client.post(f"/staff/class/{class_id}/run-live")
        live = self.school.get_active_live_session_for_class(class_id)
        assert live is not None
        self.client.get("/logout")
        rv = self.client.post(
            "/auth/student-code",
            data={"code": live["session_code"], "name": "Birch"},
        )
        self.assertEqual(rv.status_code, 401)
        self.assertIn(b"Double-check the code", rv.data)

    def test_student_code_joins_waiting_room(self) -> None:
        """An active session code and roster name land on the mood check-in."""
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCF3M"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.client.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple"],
            },
        )
        self.assertEqual(created.status_code, 200)
        class_id = int(created.get_json()["class"]["id"])
        self.client.post(f"/staff/class/{class_id}/run-live")
        live = self.school.get_active_live_session_for_class(class_id)
        assert live is not None
        self.client.get("/logout")
        available = self.client.get("/api/student/live-available")
        self.assertEqual(available.status_code, 200)
        self.assertTrue(available.get_json()["active"])
        rv = self.client.post(
            "/auth/student-code",
            data={"code": live["session_code"], "name": "Maple"},
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302)
        self.assertIn("/student/mood", rv.headers.get("Location", ""))
        attendees = self.school.list_live_session_attendees(
            int(live["id"]), present_only=True
        )
        self.assertEqual(len(attendees), 1)

    def test_student_code_rate_limit(self) -> None:
        """More than 5 attempts per IP in 10 minutes is 429."""
        last = None
        for _ in range(6):
            last = self.client.post(
                "/auth/student-code", data={"code": "ZZZZZZZZ", "name": "X"}
            )
        self.assertEqual(last.status_code, 429)

    def test_durable_offering_code_rejected_when_idle(self) -> None:
        """Offering codes no longer admit students without an active session."""
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCF3M"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.client.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple"],
            },
        )
        self.assertEqual(created.status_code, 200)
        self.client.get("/logout")
        idle = self.client.get("/api/student/live-available")
        self.assertFalse(idle.get_json()["active"])
        rv = self.client.post(
            "/auth/student-code",
            data={"code": offering["live_access_code"], "name": "Maple"},
        )
        self.assertEqual(rv.status_code, 401)
        self.assertIn(b"No live class is running", rv.data)


if __name__ == "__main__":
    unittest.main(verbosity=2)

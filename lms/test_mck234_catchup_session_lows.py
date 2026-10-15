#!/usr/bin/env python3
"""MCK-234: Test Student display name, staff role, and stale PIN authorize handling."""

from __future__ import annotations

import logging
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from catchup_landing import signed_in_line  # noqa: E402
from catchup_session import codename_for_student, establish_catchup_identity  # noqa: E402
from participation import KEY_PIN_SIGN_IN_AGAIN, copy  # noqa: E402
from test_participation_mck198 import Base  # noqa: E402


def staff_client(test: Base, email: str = "teacher@gmail.com"):
    """Logged-in staff test client."""
    client = test.app.test_client()
    client.get("/auth/google?portal=staff")
    client.get(f"/auth/google/callback?email={email}&name=T")
    code = test.school.get_user_by_email(email)["verification_code"]
    client.post("/verify-email", data={"code": code})
    return client


class Mck234CatchupSessionLowsTest(Base):
    """Follow-up fixes for catch-up identity and authorize error handling."""

    def setUp(self) -> None:
        super().setUp()
        self.run_class("M1", "C1", 2, 0, {self.students[0]: 1})
        self.token = self.school.list_catchup_links(self.class_id)[0]["token"]
        info = self.school.ensure_class_test_student(self.class_id)
        self.assertIsNotNone(info)
        self.test_student_id = int(info["student_id"])
        self.school.set_student_pin(self.class_id, self.test_student_id, "9876")

    def test_codename_for_test_student(self) -> None:
        """Test Student must resolve even though roster pickers omit them."""
        label = codename_for_student(
            self.school, self.class_id, self.test_student_id
        )
        self.assertEqual(label, self.school.TEST_STUDENT_CODENAME)

    def test_landing_signed_in_line_shows_test_student(self) -> None:
        """Catch-up landing shows Signed in as Test Student after PIN sign-in."""
        from catchup_landing import set_landing_session

        client = self.app.test_client()
        stamp = self.school.catchup_pin_stamp(self.class_id, self.test_student_id)
        with client.session_transaction() as sess:
            set_landing_session(
                sess,
                class_id=self.class_id,
                student_id=self.test_student_id,
                matched_token=self.token,
                portal_code="R3K1X",
                display_name="ignored",
            )
            establish_catchup_identity(
                sess, self.school, self.class_id, self.test_student_id, stamp
            )
        html = client.get("/student/catchup/landing").get_data(as_text=True)
        want = signed_in_line(self.school.TEST_STUDENT_CODENAME)
        self.assertIn(want, html)

    def test_staff_role_survives_catchup_identity(self) -> None:
        """Teachers previewing catch-up keep their staff session role."""
        client = staff_client(self)
        sid = self.students[0]
        self.school.set_student_pin(self.class_id, sid, "4321")
        stamp = self.school.catchup_pin_stamp(self.class_id, sid)
        self.assertTrue(stamp)
        with client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["role"] = "teacher"
            establish_catchup_identity(sess, self.school, self.class_id, sid, stamp)
            self.assertEqual(sess.get("role"), "teacher")
            self.assertTrue(sess.get("logged_in"))

    def test_authorize_denied_on_catchup_bind_shows_pin_screen_not_500(self) -> None:
        """go_student_dashboard must show the PIN screen when authorize fails."""
        client = self.app.test_client()
        sid = self.students[0]
        self.school.set_student_pin(self.class_id, sid, "4321")
        stamp = self.school.catchup_pin_stamp(self.class_id, sid)
        with client.session_transaction() as sess:
            establish_catchup_identity(sess, self.school, self.class_id, sid, stamp)
        path = self.catchup_url(self.token)
        with patch.object(
            self.school,
            "authorize_student_pin_change",
            side_effect=PermissionError("catch-up portal sign-in required"),
        ):
            res = client.post(path, data={"action": "pin"})
        self.assertEqual(res.status_code, 200)
        self.assertIn(copy(KEY_PIN_SIGN_IN_AGAIN)["text"], res.get_data(as_text=True))

    def test_authorize_denied_on_catchup_pin_save_shows_message_not_500(self) -> None:
        """Catch-up PIN save must show a friendly message when authorize fails."""
        client = self.app.test_client()
        sid = self.students[0]
        self.school.set_student_pin(self.class_id, sid, "4321")
        stamp = self.school.catchup_pin_stamp(self.class_id, sid)
        with client.session_transaction() as sess:
            establish_catchup_identity(sess, self.school, self.class_id, sid, stamp)
        path = self.catchup_url(self.token)
        with patch.object(
            self.school,
            "authorize_student_pin_change",
            side_effect=PermissionError("catch-up portal sign-in required"),
        ):
            res = client.post(
                path,
                data={
                    "action": "set_catchup_pin",
                    "pin": "2468",
                    "confirm": "2468",
                },
            )
        self.assertEqual(res.status_code, 200)
        self.assertIn(copy(KEY_PIN_SIGN_IN_AGAIN)["text"], res.get_data(as_text=True))


if __name__ == "__main__":
    logging.disable(logging.NOTSET)
    unittest.main()

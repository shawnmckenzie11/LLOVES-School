#!/usr/bin/env python3
"""MCK-234: roster PIN API auth and catch-up portal PIN authorize wiring."""

from __future__ import annotations

import logging
import os
import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from test_participation_mck198 import Base  # noqa: E402
from participation import check_pin  # noqa: E402
from student_portal import bind_pin_portal_session  # noqa: E402


def staff_client(test: Base, email: str = "teacher@gmail.com"):
    """Logged-in staff test client for ``email``."""
    client = test.app.test_client()
    client.get("/auth/google?portal=staff")
    client.get(f"/auth/google/callback?email={email}&name=T")
    code = test.school.get_user_by_email(email)["verification_code"]
    client.post("/verify-email", data={"code": code})
    return client


class Mck234RosterPinAuthorizeTest(Base):
    """Staff roster PIN route and student self-service PIN changes."""

    def setUp(self) -> None:
        super().setUp()
        self.run_class("M1", "C1", 2, 0, {self.students[0]: 1})

    def test_bind_pin_portal_session_requires_portal_identity(self) -> None:
        """Invalid portal sessions must raise PermissionError, not TypeError (MCK-208)."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            with self.assertRaises(PermissionError):
                bind_pin_portal_session(sess, self.school, self.class_id, self.students[0])

    def test_roster_pin_staff_success(self) -> None:
        client = staff_client(self)
        sid = self.students[0]
        res = client.put(
            f"/api/classes/{self.class_id}/students/{sid}/pin",
            json={"pin": "2468"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json().get("ok"))
        stored = self.school.game.conn.execute(
            "SELECT pin_hash, pin_set_by FROM students WHERE id = ?", (sid,)
        ).fetchone()
        self.assertEqual(stored[1], "teacher")
        self.assertTrue(check_pin("2468", stored[0]))

    def test_roster_pin_wrong_class_staff_denied(self) -> None:
        other = self.school.register_staff("other@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(other["id"]), ontario_code="MCF3M"
        )
        other_class = int(
            self.school.game.create_class(
                year="2026/27",
                semester="Semester 1",
                course_code="MCF3M",
                days_preset="M/W/F",
                time_label="2:00pm",
                codenames=["Zed"],
                offering_id=int(offering["id"]),
                teacher_user_id=int(other["id"]),
            )["id"]
        )
        client = staff_client(self, "other@gmail.com")
        res = client.put(
            f"/api/classes/{self.class_id}/students/{self.students[0]}/pin",
            json={"pin": "2468"},
        )
        self.assertEqual(res.status_code, 403)
        self.assertNotEqual(other_class, self.class_id)

    def test_student_self_change_with_catchup_portal(self) -> None:
        sid = self.students[0]
        self.school.set_student_pin(self.class_id, sid, "5678", pin_set_by="default")
        client = self.app.test_client()
        stamp = self.school.catchup_pin_stamp(self.class_id, sid)
        with client.session_transaction() as sess:
            from catchup_session import establish_catchup_identity

            establish_catchup_identity(sess, self.school, self.class_id, sid, stamp)
        res = client.post(
            "/api/student/catchup-pin",
            json={"pin": "1357", "confirm": "1357", "current_pin": "5678"},
        )
        self.assertEqual(res.status_code, 200)
        stored = self.school.game.conn.execute(
            "SELECT pin_hash, pin_set_by FROM students WHERE id = ?", (sid,)
        ).fetchone()
        self.assertEqual(stored[1], "student")
        self.assertTrue(check_pin("1357", stored[0]))


if __name__ == "__main__":
    logging.disable(logging.NOTSET)
    unittest.main()

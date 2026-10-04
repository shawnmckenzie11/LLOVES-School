#!/usr/bin/env python3
"""MCK-183 slice C: the "Who's in {code}?" names step and its gate."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

import test_welcome_gate_mck183 as gate  # noqa: E402

NEW = gate.NEW
API = "/api/staff/onboarding/course"


class ClassListTests(unittest.TestCase):
    """Screen 3 (names)."""

    setUp = gate.WelcomeGateTests.setUp
    tearDown = gate.WelcomeGateTests.tearDown
    _sign_in = gate.WelcomeGateTests._sign_in

    def _setup_courses(self, *codes: str) -> list[int]:
        """Sign in and create courses through screen 2."""
        self._sign_in(NEW, "/welcome")
        rv = self.client.post(API, json={"classes": [
            {"ontario_code": c, "live_days": "M/W/F", "live_time": "2:00pm"} for c in codes
        ]})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        return [int(o["id"]) for o in rv.get_json()["offerings"]]

    def test_course_without_class_goes_to_names(self) -> None:
        """/staff → names step with Wonder's v1.2 copy."""
        self._setup_courses("SBI4U")
        rv = self.client.get("/staff")
        self.assertTrue(rv.headers["Location"].endswith("/staff/welcome?step=names"))
        html = self.client.get("/staff/welcome?step=names").get_data(as_text=True)
        self.assertIn("Who's in SBI4U?", html)
        self.assertIn("Type each student's first name, one per line. "
                      "Students type the same name to join.", html)
        self.assertIn("First names", html)
        self.assertIn("You can add or change names any time.", html)
        self.assertIn("Two students with the same first name? Add a number so each "
                      "can find theirs, like Sam 2.", html)
        self.assertNotIn("Add names later", html)
        self.assertIn('id="ob-names-next" disabled', html)

    def test_names_save_through_classes_api_then_next_course_then_dashboard(self) -> None:
        """Two courses: names for each in turn, then the Dashboard."""
        first, second = self._setup_courses("SBI4U", "SCH4U")
        rv = self.client.post("/api/staff/classes", json={
            "offering_id": first, "codenames": ["Ana", "Sam", "Sam 2"],
        })
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        self.assertEqual(rv.get_json()["class"].get("days"), "Mon/Wed/Fri")
        html = self.client.get("/staff/welcome?step=names").get_data(as_text=True)
        self.assertIn("Who's in SCH4U?", html)
        self.client.post("/api/staff/classes", json={"offering_id": second, "codenames": ["Lee"]})
        done = self.client.get("/staff/welcome?step=names")
        self.assertTrue(done.headers["Location"].endswith("/staff"))
        self.assertEqual(self.client.get("/staff").status_code, 200)

    def test_offering_param_picks_a_pending_course_only(self) -> None:
        """?offering= must be one of hers without a class."""
        first, second = self._setup_courses("SBI4U", "SCH4U")
        html = self.client.get(f"/staff/welcome?step=names&offering={second}").get_data(as_text=True)
        self.assertIn("Who's in SCH4U?", html)
        html = self.client.get("/staff/welcome?step=names&offering=99999").get_data(as_text=True)
        self.assertIn("Who's in SBI4U?", html)

    def test_names_step_without_course_goes_back_to_screen_two(self) -> None:
        """No course yet → screen 2."""
        self._sign_in(NEW, "/welcome")
        rv = self.client.get("/staff/welcome?step=names")
        self.assertTrue(rv.headers["Location"].endswith("/staff/welcome?step=class"))

    def test_existing_teacher_with_classes_never_sees_names(self) -> None:
        """A teacher with a class keeps the Dashboard."""
        first, = self._setup_courses("SBI4U")
        self.client.post("/api/staff/classes", json={"offering_id": first, "codenames": ["Ana"]})
        self.assertEqual(self.client.get("/staff").status_code, 200)
        rv = self.client.get("/staff/welcome?step=names")
        self.assertTrue(rv.headers["Location"].endswith("/staff"))


    def _rollover(self) -> None:
        """A new active semester (her old classes stay in the last one)."""
        from datetime import datetime

        conn = self.school.conn
        conn.execute("UPDATE semesters SET is_active = 0")
        conn.execute(
            "INSERT INTO semesters (label, year_display, term, is_active, payload_json, created_at)"
            " VALUES ('Next term', '2027', 'S2', 1, '{}', ?)",
            (datetime.now().isoformat(),),
        )
        conn.commit()

    def test_rollover_with_new_course_keeps_the_dashboard(self) -> None:
        """Old classes last term, a new Admin course this term: Dashboard, not names."""
        first, = self._setup_courses("SBI4U")
        self.client.post("/api/staff/classes", json={"offering_id": first, "codenames": ["Ana"]})
        self._rollover()
        self.school.assign_course(teacher_user_id=int(self.teacher["id"]), ontario_code="SCH4U")
        self.assertEqual(self.client.get("/staff").status_code, 200)

    def test_live_session_keeps_the_dashboard(self) -> None:
        """Mid-live-class she is never sent to the names step."""
        first, = self._setup_courses("SBI4U")
        cls = self.client.post(
            "/api/staff/classes", json={"offering_id": first, "codenames": ["Ana"]}
        ).get_json()["class"]
        self.school.start_live_class_session(int(cls["id"]), int(self.teacher["id"]))
        self._rollover()
        self.school.assign_course(teacher_user_id=int(self.teacher["id"]), ontario_code="SCH4U")
        self.assertEqual(self.client.get("/staff").status_code, 200)


if __name__ == "__main__":
    unittest.main()

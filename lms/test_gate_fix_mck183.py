#!/usr/bin/env python3
"""MCK-183 #250 gate follow-up: never trap a set-up teacher; tighter ``next``."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

import test_welcome_gate_mck183 as gate  # noqa: E402
from auth import _safe_next_url  # noqa: E402

NEW = gate.NEW


class GateFixTests(unittest.TestCase):
    """First-run gate edges from the #250 gate (MED + LOWs)."""

    setUp = gate.WelcomeGateTests.setUp
    tearDown = gate.WelcomeGateTests.tearDown
    _sign_in = gate.WelcomeGateTests._sign_in

    def _session(self) -> dict:
        """Test client session."""
        with self.client.session_transaction() as sess:
            return dict(sess)

    def _class(self) -> dict:
        """Her SBI4U course with one student, in the active semester."""
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="SBI4U"
        )
        rv = self.client.post("/api/staff/classes", json={
            "offering_id": offering["id"], "days": "M/W/F", "time": "2:00pm", "codenames": ["Ana"],
        })
        return rv.get_json()["class"]

    def _new_active_semester(self) -> None:
        """Rollover: a new, empty active semester."""
        conn = self.school.conn
        conn.execute("UPDATE semesters SET is_active = 0")
        conn.execute(
            "INSERT INTO semesters (label, year_display, term, is_active, payload_json, created_at)"
            " VALUES ('Next term', '2027', 'S2', 1, '{}', ?)",
            (datetime.now().isoformat(),),
        )
        conn.commit()

    def test_no_active_semester_keeps_the_dashboard(self) -> None:
        """No active semester: /staff renders, no Welcome."""
        self._sign_in(NEW, "/staff")
        self._class()
        self.school.conn.execute("UPDATE semesters SET is_active = 0")
        self.school.conn.commit()
        self.assertEqual(self.client.get("/staff").status_code, 200)

    def test_rollover_with_old_classes_keeps_the_dashboard(self) -> None:
        """Classes only in last semester: still set up."""
        self._sign_in(NEW, "/staff")
        self._class()
        self._new_active_semester()
        self.assertEqual(self.client.get("/staff").status_code, 200)

    def test_course_in_another_semester_keeps_the_dashboard(self) -> None:
        """A course (no class) in another semester: no Welcome either."""
        self._sign_in(NEW, "/staff")
        self.school.assign_course(teacher_user_id=int(self.teacher["id"]), ontario_code="SBI4U")
        self._new_active_semester()
        self.assertEqual(self.client.get("/staff").status_code, 200)

    def test_live_session_keeps_the_dashboard(self) -> None:
        """Mid-live-class (even with no class this semester): never gated."""
        self._sign_in(NEW, "/staff")
        cls = self._class()
        self.school.start_live_class_session(int(cls["id"]), int(self.teacher["id"]))
        self._new_active_semester()
        rv = self.client.get("/staff")
        self.assertEqual(rv.status_code, 200)

    def test_archived_course_and_class_do_not_count(self) -> None:
        """Only an archived course (and its class) elsewhere: still Welcome."""
        self._sign_in(NEW, "/staff")
        cls = self._class()
        self.school.archive_offering(int(cls["offering_id"]))
        self._new_active_semester()
        self.assertTrue(self.client.get("/staff").headers["Location"].endswith("/staff/welcome"))

    def test_brand_new_teacher_still_gated(self) -> None:
        """Control: no course, no class anywhere → Welcome."""
        self._sign_in(NEW, "/staff")
        self.assertTrue(self.client.get("/staff").headers["Location"].endswith("/staff/welcome"))

    def test_next_refuses_path_traversal(self) -> None:
        """#269 gate LOW: decode, refuse dot segments and backslashes, then allowlist."""
        for bad in (
            "/staff/../logout", "/staff/welcome/../../logout", "/staff/./welcome",
            "/staff/%2e%2e/logout", "/staff/%2E%2E/logout", "/staff/.%2E/logout",
            "/staff/%2e./logout", "/staff/%252e%252e/logout", "/staff/%5c..%5clogout",
            "/staff\\..\\logout", "/staff/%2f%2fevil.example", "/it/%2e%2e/staff/../logout",
        ):
            self.assertIsNone(_safe_next_url(bad), bad)
        self.assertEqual(_safe_next_url("/staff/welcome?step=names"), "/staff/welcome?step=names")

    def test_next_allowlist_and_characters(self) -> None:
        """Only /staff and /it; printable ASCII only."""
        for good in ("/staff", "/staff/welcome", "/staff/class/3?tab=live", "/it", "/it/?x=1", "/staff?"):
            self.assertEqual(_safe_next_url(good), good)
        for bad in (
            "/logout", "/auth/google/slides", "/", "/welcome", "/staffing", "/itx",
            "/staff/\x7f", "/staff/\u2028x", "/staff/\u202ex", "/staff/\u200bx",
            "/staff/\u00a0x", "/staff\uff0fevil", "/staff\uff3cevil", "/staff x",
        ):
            self.assertIsNone(_safe_next_url(bad), repr(bad))

    def test_stale_next_cleared(self) -> None:
        """403, an abandoned start, and the IT already-signed-in branch drop next."""
        self.client.get("/auth/google?portal=staff&next=/staff/welcome")
        self.client.get("/auth/google/callback?email=unknown.person@gmail.com&name=U")
        self.assertNotIn("google_oauth_next", self._session())
        self.client.get("/auth/google?portal=staff&next=/staff/welcome")
        self.assertIn("google_oauth_next", self._session())
        self.client.get("/auth/google?portal=staff")
        self.assertNotIn("google_oauth_next", self._session())

    def test_it_already_signed_in_drops_next(self) -> None:
        """IT branch of auth_google pops next before redirecting."""
        it_user = self.school.get_user_by_email("solutions@mckenzian.com")
        if not it_user:
            self.skipTest("no seeded Admin")
        self._sign_in("solutions@mckenzian.com", "/it")
        with self.client.session_transaction() as sess:
            sess["google_oauth_next"] = "/staff/welcome"
        rv = self.client.get("/auth/google?portal=it&next=/it")
        self.assertTrue(rv.headers["Location"].endswith("/it"))
        self.assertNotIn("google_oauth_next", self._session())


if __name__ == "__main__":
    unittest.main()

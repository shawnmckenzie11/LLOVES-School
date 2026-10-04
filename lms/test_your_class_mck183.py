#!/usr/bin/env python3
"""MCK-183 slice B: screen 2 (Your class), three preset dropdowns, teacher-created course."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

import staff_invites  # noqa: E402
import test_welcome_gate_mck183 as gate  # noqa: E402

NEW = gate.NEW

ERROR = "Pick a course, days and a start time to continue."
API = "/api/staff/onboarding/course"


class YourClassTests(unittest.TestCase):
    """Screen 2 and its endpoint."""

    setUp = gate.WelcomeGateTests.setUp
    tearDown = gate.WelcomeGateTests.tearDown
    _sign_in = gate.WelcomeGateTests._sign_in

    def _signed_in(self) -> None:
        """New teacher, signed in."""
        self._sign_in(NEW, "/welcome")

    def _offerings(self) -> list[dict]:
        """Her active-semester offerings."""
        active = self.school.get_active_semester()
        return self.school.list_offerings(
            teacher_user_id=int(self.teacher["id"]),
            semester_id=int(active["id"]),
            include_archived=False,
        )

    def test_screen_two_has_three_choose_dropdowns(self) -> None:
        """Course code / Days / Start time, all 'Choose…', with Wonder's help."""
        self._signed_in()
        html = self.client.get("/staff/welcome?step=class").get_data(as_text=True)
        self.assertIn("Tell us about your class", html)
        for label in ("Course code", "Days", "Start time", "+ Add another class", "Next", "Back"):
            self.assertIn(label, html)
        self.assertEqual(html.count('<option value="">Choose…</option>'), 3)
        self.assertIn("Don't see your course? Ask Shawn to add it.", html)
        self.assertIn(ERROR, html)
        self.assertIn('<option value="M/W/F">MWF</option>', html)
        self.assertIn('<option value="2:00pm">2:00pm</option>', html)
        self.assertIn('value="SBI4U"', html)
        self.assertNotIn(" selected", html)
        self.assertIn("/static/onboarding.js", html)

    def test_next_creates_her_course_with_schedule_and_no_pack(self) -> None:
        """SBI4U + M/W/F + 2:00pm → one offering, no library, schedule set."""
        self._signed_in()
        rv = self.client.post(API, json={"classes": [
            {"ontario_code": "SBI4U", "live_days": "M/W/F", "live_time": "2:00pm"},
        ]})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        body = rv.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["next"], "/staff")
        rows = self._offerings()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ontario_code"], "SBI4U")
        self.assertEqual(rows[0]["live_days"], "M/W/F")
        self.assertEqual(rows[0]["live_time"], "2:00pm")
        self.assertFalse(rows[0].get("library_id"))
        self.assertEqual(self.client.get("/staff").status_code, 200)

    def test_repeat_code_is_a_second_section_and_back_next_is_idempotent(self) -> None:
        """Two SPH3U rows → SPH3U and SPH3U-2; pressing Next again adds nothing."""
        self._signed_in()
        rows = [
            {"ontario_code": "SPH3U", "live_days": "M/W/F", "live_time": "9:15am"},
            {"ontario_code": "SPH3U", "live_days": "T/Th/F", "live_time": "12:35pm"},
        ]
        self.assertEqual(self.client.post(API, json={"classes": rows}).status_code, 200)
        self.assertEqual(len(self._offerings()), 2)
        rows[1]["live_time"] = "3:15pm"
        self.assertEqual(self.client.post(API, json={"classes": rows}).status_code, 200)
        offerings = self._offerings()
        self.assertEqual(len(offerings), 2)
        self.assertEqual(sorted(o["live_time"] for o in offerings), ["3:15pm", "9:15am"])
        html = self.client.get("/staff/welcome?step=class").get_data(as_text=True)
        self.assertEqual(html.count('value="SPH3U" selected'), 2)

    def test_missing_or_off_list_values_get_the_one_error(self) -> None:
        """Empty, unknown course, off-preset days or time → 400, nothing created."""
        self._signed_in()
        for row in (
            {"ontario_code": "SBI4U", "live_days": "", "live_time": "2:00pm"},
            {"ontario_code": "ZZZ9Z", "live_days": "M/W/F", "live_time": "2:00pm"},
            {"ontario_code": "SBI4U", "live_days": "M/T", "live_time": "2:00pm"},
            {"ontario_code": "SBI4U", "live_days": "M/W/F", "live_time": "2:05pm"},
        ):
            rv = self.client.post(API, json={"classes": [row]})
            self.assertEqual(rv.status_code, 400, row)
            self.assertEqual(rv.get_json()["error"], ERROR)
        self.assertEqual(self.client.post(API, json={}).status_code, 400)
        self.assertEqual(self._offerings(), [])

    def test_only_during_setup_and_only_for_staff(self) -> None:
        """Signed out → redirect; a teacher with a class can't add courses here."""
        anon = self.app.test_client().post(API, json={"classes": []})
        self.assertIn(anon.status_code, (302, 401, 403))
        self._signed_in()
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="SBI4U"
        )
        self.client.post("/api/staff/classes", json={
            "offering_id": offering["id"], "days": "M/W/F", "time": "2:00pm", "codenames": ["Ana"],
        })
        rv = self.client.post(API, json={"classes": [
            {"ontario_code": "SCH4U", "live_days": "M/W/F", "live_time": "2:00pm"},
        ]})
        self.assertEqual(rv.status_code, 409)

    def test_invite_preset_prefills_screen_two(self) -> None:
        """The accepted invite's course/days/time arrive pre-selected; no course yet."""
        invite, _token = staff_invites.create_or_refresh_invite(
            self.school, email=NEW, first_name="Rae", preset=("SBI4U", "M/W/F", "2:00pm"),
        )
        staff_invites.mark_accepted(self.school, int(invite["id"]))
        self._signed_in()
        self.assertEqual(self._offerings(), [])
        html = self.client.get("/staff/welcome?step=class").get_data(as_text=True)
        self.assertIn('value="SBI4U" selected', html)
        self.assertIn('value="M/W/F" selected', html)
        self.assertIn('value="2:00pm" selected', html)

    def test_catalog_titles_keep_their_grade(self) -> None:
        """Seeding no longer drops the grade number ('Physics, Grade, ...')."""
        rows = {r["code"]: r["title"] for r in self.school.search_ontario_courses("", limit=1000)}
        self.assertFalse([c for c, t in rows.items() if ", Grade," in t], rows)
        if "SPH3U" in rows:
            self.assertEqual(rows["SPH3U"], "Physics, Grade 11, University Preparation")


if __name__ == "__main__":
    unittest.main()

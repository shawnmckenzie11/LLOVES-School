#!/usr/bin/env python3
"""MCK-249: primary course tabs, More menu, masked Roster & PINs."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

from app import _format_last_taken  # noqa: E402
from test_participation_mck198 import Base  # noqa: E402


class LastTakenFormatTests(unittest.TestCase):
    """Course-card date wording."""

    def test_blank_and_iso(self) -> None:
        self.assertEqual(_format_last_taken(""), "")
        self.assertEqual(_format_last_taken("not-a-date"), "")
        self.assertEqual(_format_last_taken("2026-10-19"), "Oct 19, 2026")


class TeacherUiTests(Base):
    """Approved teacher IA. PIN plaintext stays off the page and out of assertions."""

    def setUp(self) -> None:
        super().setUp()
        self.client = self.app.test_client()
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        code = self.school.get_user_by_email("teacher@gmail.com")["verification_code"]
        self.client.post("/verify-email", data={"code": code})
        self.school.ensure_default_pins_for_class(self.class_id, str(self.app.secret_key or ""))

    def test_default_course_page_shows_the_tab_bar(self) -> None:
        """Open course stays on idle Set Class, with the course tabs visible."""
        html = self.client.get(f"/staff/class/{self.class_id}").get_data(as_text=True)
        self.assertIn('id="course-tabs"', html)
        self.assertIn('class="staff-top-menu"', html)
        self.assertIn('class="topbar"', html)
        self.assertIn("is-set-class", html)
        css = Path(__file__).resolve().parent.joinpath("static", "staff-shell.css").read_text(
            encoding="utf-8"
        )
        hide = (
            "body.staff-shell.course-live:not(:has(.live-shell-ia-v2.is-set-class)) "
            ".tabs.course-tabs {\n  display: none !important;"
        )
        self.assertIn(hide, css)
        self.assertNotIn(
            "body.staff-shell.course-live .tabs.course-tabs {\n  display: none !important;",
            css,
        )

    def test_teacher_set_pin_is_custom(self) -> None:
        """A teacher-set PIN reads Custom. A student-set PIN reads Student set."""
        teacher_sid = self.students[0]
        student_sid = self.students[1]
        self.school.set_student_pin(
            self.class_id,
            teacher_sid,
            "".join(str(n) for n in (4, 1, 8, 2)),
            pin_set_by="teacher",
        )
        self.school.set_student_pin(
            self.class_id,
            student_sid,
            "".join(str(n) for n in (6, 3, 9, 1)),
            pin_set_by="student",
        )
        page = self.client.get(
            f"/staff/class/{self.class_id}?tab=ap&view=roster"
        ).get_data(as_text=True)
        self.assertIn(">Custom<", page)
        self.assertIn(">Student set<", page)
        self.assertNotIn("Your catch-up PIN:", page)

    def test_default_tab_is_live_class(self) -> None:
        html = self.client.get(f"/staff/class/{self.class_id}").get_data(as_text=True)
        self.assertIn("course-live", html)
        self.assertIn('<span class="course-tabs-toggle-label">Live Class</span>', html)
        self.assertIn(">Question banks<", html)
        self.assertIn(">Lesson Slides<", html)
        self.assertNotIn(">Content library<", html)
        self.assertNotIn(">Grades</a>", html)
        self.assertNotIn(">Engagement</a>", html)

    def test_old_catalog_urls_open_the_library_prefiltered(self) -> None:
        for tab, kind in (
            ("library", "all"),
            ("pages", "pages"),
            ("assignments", "assignments"),
            ("quizzes", "quizzes"),
        ):
            html = self.client.get(
                f"/staff/class/{self.class_id}?tab={tab}"
            ).get_data(as_text=True)
            self.assertIn(f'data-kind="{kind}"', html, tab)
            self.assertIn("More: Content library", html, tab)
            self.assertIn('id="catalog-root"', html, tab)

    def test_more_label_names_the_open_section(self) -> None:
        html = self.client.get(
            f"/staff/class/{self.class_id}?tab=syllabus"
        ).get_data(as_text=True)
        self.assertIn("More: Syllabus", html)
        self.assertNotIn('class="course-more-summary', html)
        self.assertIn('title="Syllabus"', html)

    def test_engagement_route_still_renders(self) -> None:
        html = self.client.get(
            f"/staff/class/{self.class_id}?tab=ap&view=engagement"
        ).get_data(as_text=True)
        self.assertIn("engagement-summary-root", html)
        self.assertNotIn(">Engagement</a>", html)
        self.assertIn(">Roster &amp; PINs<", html)

    def test_roster_masks_pins_and_keeps_the_route(self) -> None:
        page = self.client.get(
            f"/staff/class/{self.class_id}?tab=ap&view=roster"
        ).get_data(as_text=True)
        self.assertIn("Require students to change default PINs", page)
        self.assertIn("Print all default slips", page)
        self.assertIn(">Show<", page)
        self.assertIn("Set new PIN", page)
        self.assertIn(" pin-set", page)
        self.assertNotIn("Your catch-up PIN:", page)
        cells = re.findall(r'class="ap-roster-pin"[^>]*>([^<]*)', page)
        self.assertTrue(cells)
        for cell in cells:
            self.assertIn("••••", cell)
            self.assertIsNone(re.search(r"\d", cell))

    def test_show_slip_is_not_on_the_page(self) -> None:
        """The reveal call succeeds for a default PIN and is absent from HTML."""
        sid = self.students[0]
        page = self.client.get(
            f"/staff/class/{self.class_id}?tab=ap&view=roster"
        ).get_data(as_text=True)
        revealed = self.client.get(
            f"/api/classes/{self.class_id}/students/{sid}/default-pin-slip"
        )
        self.assertEqual(revealed.status_code, 200)
        self.assertEqual(revealed.headers.get("Cache-Control"), "no-store")
        payload = revealed.get_json()
        slip = str((payload or {}).get("slip") or "")
        shaped = slip.startswith("Your catch-up PIN:") and slip.endswith("(not the join code)")
        self.assertTrue(shaped, "default slip has the expected wording")
        self.assertFalse(bool(slip) and slip in page, "slip leaked onto the roster page")
        chosen = "".join(str(n) for n in (9, 0, 8, 7))
        self.school.set_student_pin(self.class_id, sid, chosen, pin_set_by="student")
        hidden = self.client.get(
            f"/api/classes/{self.class_id}/students/{sid}/default-pin-slip"
        )
        self.assertEqual(hidden.status_code, 404)
        self.assertNotIn("slip", hidden.get_json())

    def test_dashboard_card_copy(self) -> None:
        home = self.client.get("/staff").get_data(as_text=True)
        self.assertNotIn("Records who's here today.", home)
        self.assertEqual(home.count(">Take attendance<"), 1)
        self.assertIn(">Open course<", home)
        self.assertIn("<span>Edit roster</span>", home)
        self.assertIn("Run Live Class", home)
        self.assertNotIn("Explore Course", home)
        self.assertNotIn("Last taken:", home)
        self.assertNotIn("<summary>Roster</summary>", home)


if __name__ == "__main__":
    unittest.main()

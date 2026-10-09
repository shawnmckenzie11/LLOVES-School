#!/usr/bin/env python3
"""MCK-183 slice E: plain view for pack-less non-math courses; Join code."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

import test_welcome_gate_mck183 as gate  # noqa: E402

NEW = gate.NEW
API = "/api/staff/onboarding/course"
HIDDEN_TABS = (
    "lesson-slides", "modules", "gradebook", "pages", "assignments", "quizzes",
    "question-banks", "syllabus", "expectations", "profiles",
)


class NonMathTests(unittest.TestCase):
    """Pack-less non-math (SBI4U) vs math (MCF3M) on the same school."""

    setUp = gate.WelcomeGateTests.setUp
    tearDown = gate.WelcomeGateTests.tearDown
    _sign_in = gate.WelcomeGateTests._sign_in

    def _class_for(self, code: str) -> int:
        self._sign_in(NEW, "/welcome")
        rv = self.client.post(API, json={"classes": [
            {"ontario_code": code, "live_days": "M/W/F", "live_time": "2:00pm"}
        ]})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        oid = int(rv.get_json()["offerings"][0]["id"])
        cls = self.client.post(
            "/api/staff/classes", json={"offering_id": oid, "codenames": ["Ana", "Sam"]}
        ).get_json()["class"]
        return int(cls["id"])

    def _tabs(self, html: str) -> str:
        return re.search(r'<div class="course-tabs-panel".*?</div>', html, re.S).group(0)

    def test_non_math_course_opens_on_ap_with_two_tabs(self) -> None:
        class_id = self._class_for("SBI4U")
        html = self.client.get(f"/staff/class/{class_id}").get_data(as_text=True)
        tabs = self._tabs(html)
        self.assertIn("Run Live Class", tabs)
        self.assertIn("Attendance &amp; Participation", tabs)
        for tab in HIDDEN_TABS:
            self.assertNotIn(f"tab={tab}", tabs, tab)
        self.assertIn('<span class="course-tabs-toggle-label">Attendance &amp; Participation</span>', html)
        # Unknown tab falls back to A&P too.
        other = self.client.get(f"/staff/class/{class_id}?tab=nope").get_data(as_text=True)
        self.assertIn('<span class="course-tabs-toggle-label">Attendance &amp; Participation</span>', other)

    def test_non_math_set_class_and_kind_labels(self) -> None:
        class_id = self._class_for("SBI4U")
        html = self.client.get(f"/staff/class/{class_id}?tab=live").get_data(as_text=True)
        self.assertIn('<label class="live-pack-field" for="live-module-select" hidden>', html)
        self.assertIn('<label class="live-pack-field" for="live-class-select" hidden>', html)
        self.assertIn('id="live-module-select"', html)  # still there for staff_ap.js (M1/C1)
        self.assertIn('<option value="" selected>Course</option>', html)
        self.assertNotIn(">Core Math</option>", html)

    def test_non_math_dashboard_card(self) -> None:
        self._class_for("SBI4U")
        html = self.client.get("/staff").get_data(as_text=True)
        self.assertNotIn("Ask Admin to attach a module pack", html)
        self.school.assign_course(teacher_user_id=int(self.teacher["id"]), ontario_code="SCH4U")
        html = self.client.get("/staff").get_data(as_text=True)
        card = html[html.index('data-ontario-code="SCH4U"'):]
        card = card[: card.index("</article>")]
        self.assertIn("No class list yet. Add your students' first names.", card)
        self.assertIn("<span>Class list</span>", card)
        self.assertNotIn("Populate Class", card)
        self.assertNotIn("Ask Admin to attach a module pack", card)

    def test_math_course_without_pack_is_unchanged(self) -> None:
        """Shawn's math courses keep every tab, Core Math, the pack strip and hints."""
        class_id = self._class_for("MCF3M")
        html = self.client.get(f"/staff/class/{class_id}").get_data(as_text=True)
        self.assertIn('<span class="course-tabs-toggle-label">Modules</span>', html)
        tabs = self._tabs(html)
        for tab in HIDDEN_TABS:
            self.assertIn(f"tab={tab}", tabs, tab)
        live = self.client.get(f"/staff/class/{class_id}?tab=live").get_data(as_text=True)
        self.assertIn('<label class="live-pack-field" for="live-module-select">', live)
        self.assertIn('<option value="" selected>Core Math</option>', live)
        self.school.assign_course(teacher_user_id=int(self.teacher["id"]), ontario_code="MHF4U")
        home = self.client.get("/staff").get_data(as_text=True)
        card = home[home.index('data-ontario-code="MHF4U"'):]
        card = card[: card.index("</article>")]
        self.assertIn("<span>Populate Class</span>", card)
        self.assertIn("Ask Admin to attach a module pack", card)

    def test_course_with_a_pack_is_unchanged(self) -> None:
        """A non-math course that has a pack keeps the full view."""
        app_simple = self.app.jinja_env.globals["simple_course"]
        self.assertTrue(app_simple("SBI4U", None))
        self.assertTrue(app_simple("eng2d", 0))
        self.assertFalse(app_simple("SBI4U", 3))
        self.assertFalse(app_simple("MCF3M", None))
        self.assertFalse(app_simple("mpm2d", None))
        self.assertFalse(app_simple("", None))

    def test_student_landing_says_join_code(self) -> None:
        html = self.app.test_client().get("/").get_data(as_text=True)
        self.assertIn('<label class="field" for="code">Join code</label>', html)
        self.assertIn("Enter the join code and your first name to join the live lesson.", html)
        self.assertNotIn(">Course code</label>", html)
        self.assertIn('placeholder="Code on the screen"', html)


if __name__ == "__main__":
    unittest.main()

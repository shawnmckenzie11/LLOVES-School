#!/usr/bin/env python3
"""MCK-199: teacher Student view (client-side mask for screen sharing)."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent


class StudentViewTests(unittest.TestCase):
    """Markup, wiring and the node harness."""

    def test_node_harness(self) -> None:
        """Masking, stable numbering and persistence."""
        node = LMS_DIR / "static" / "student_view.test.mjs"
        result = subprocess.run(
            ["node", str(node)], cwd=str(node.parent),
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("ok", result.stdout)

    def test_toggle_next_to_eyes_up(self) -> None:
        html = (LMS_DIR / "templates" / "staff" / "course.html").read_text(encoding="utf-8")
        eyes = html.index('id="live-eyes-up"')
        sv = html.index('id="live-student-view"')
        self.assertLess(eyes, sv)
        self.assertLess(sv - eyes, 900)
        self.assertIn('title="Hide names and points so you can share your screen"', html)
        self.assertIn("Student view</button>", html)
        self.assertIn('class="student-view-icon"', html)

    def test_hide_names_checkbox_removed(self) -> None:
        html = (LMS_DIR / "templates" / "staff" / "course.html").read_text(encoding="utf-8")
        self.assertNotIn('id="live-hide-names"', html)
        self.assertNotIn("<span>Hide names</span>", html)
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("let hideResponseNames = false;", js)
        self.assertIn('import { mountStudentView } from "/static/student_view.js";', js)
        self.assertIn("roster: () => lastClassListFull", js)

    def test_client_side_only(self) -> None:
        """No request and no student-facing code."""
        js = (LMS_DIR / "static" / "student_view.js").read_text(encoding="utf-8")
        for token in ("fetch(", "XMLHttpRequest", "api(", "WebSocket"):
            self.assertNotIn(token, js)
        portal = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertNotIn("student_view.js", portal)
        self.assertIn('STUDENT_VIEW_KEY = "lloves-student-view"', js)


if __name__ == "__main__":
    unittest.main()

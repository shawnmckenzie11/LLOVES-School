"""MCK-503: class list drawer, student-only minimize, drop wb filters."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

LMS = Path(__file__).resolve().parent
ROOT = LMS.parent
sys.path.insert(0, str(LMS))


class ClassListDrawerTests(unittest.TestCase):
    """Overlay drawer wiring and accessible labels."""

    def test_drawer_controls_and_storage(self) -> None:
        """Toggle, scrim, and Esc share one open flag."""
        html = (LMS / "templates" / "staff" / "course.html").read_text(encoding="utf-8")
        js = (LMS / "static" / "staff_ap.js").read_text(encoding="utf-8")
        css = (LMS / "static" / "staff-shell.css").read_text(encoding="utf-8")
        rail = (LMS / "static" / "class_list_rail.js").read_text(encoding="utf-8")
        self.assertIn('id="class-list-scrim"', html)
        self.assertNotIn('id="class-list-close"', html)
        self.assertIn('aria-label="Show class list"', html)
        self.assertIn("class-list-drawer", html)
        self.assertIn("classListToggleAriaLabel", js)
        self.assertIn('setAttribute("aria-label"', js)
        self.assertNotIn('btn.textContent = "Hide class list"', html)
        self.assertIn("class-list-scrim", css)
        self.assertIn("translateX", css)
        self.assertNotIn("class-list-open .class-list-toggle {\n  font-size", css)
        self.assertNotIn("class-list-collapsed .class-list-toggle {\n  font-size: 0", css)
        self.assertIn(
            "body.staff-shell #class-list-pane .ap-att-row.has-team-color .ap-att-name",
            css,
        )
        self.assertNotIn('id="live-shell-left" aria-hidden="true"', html)
        self.assertNotIn("if (open) return", rail.split("classListToggleLabel")[1].split("}")[0])

    def test_class_list_esc_yields_to_dialog(self) -> None:
        """Esc closes the drawer only when no modal dialog is open."""
        js = (LMS / "static" / "staff_ap.js").read_text(encoding="utf-8")
        esc = js.split('if (event.key !== "Escape" || !classListIsOpen()) return;')[1].split(
            "setClassListOpen(false)"
        )[0]
        self.assertIn("dialog[open]", esc)

    def test_toggle_label_keeps_present_count(self) -> None:
        """Class (N) stays on the button; title and aria flip Show/Hide."""
        script = r"""
import {
  classListToggleLabel,
  classListToggleTitle,
  classListToggleAriaLabel,
} from "./lms/static/class_list_rail.js";
if (classListToggleLabel(false, 3) !== "Class (3)") throw new Error("closed");
if (classListToggleLabel(true, 3) !== "Class (3)") throw new Error("open count");
if (classListToggleTitle(true) !== "Hide class list") throw new Error("open title");
if (classListToggleAriaLabel(false) !== "Show class list") throw new Error("closed aria");
console.log("ok");
"""
        node = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(node.returncode, 0, node.stderr or node.stdout)


class TeacherMinimizePanelTests(unittest.TestCase):
    """Minimize affects students only; teacher cards stay in the main list."""

    def test_no_teacher_minimize_dock(self) -> None:
        """The teacher shell no longer ships a right-hand Minimized panel."""
        html = (LMS / "templates" / "staff" / "course.html").read_text(encoding="utf-8")
        js = (LMS / "static" / "staff_ap.js").read_text(encoding="utf-8")
        css = (LMS / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertNotIn('id="teacher-minimized-panel"', html)
        self.assertNotIn('id="teacher-minimized-list"', html)
        self.assertIn('id="live-questions-layout"', html)
        self.assertIn("studentMinimizeButtonHtml", js)
        self.assertIn("handleLiveQuestionCardClick", js)
        self.assertNotIn("teacherQuestionInMinimizedPanel", js)
        self.assertNotIn("teacher-minimized-panel", css)
        self.assertNotIn("has-teacher-minimized", css)

    def test_hide_key_covers_main_list(self) -> None:
        """MCK-155: Hide key hides Answer chips and marks on the main list."""
        js = (LMS / "static" / "staff_ap.js").read_text(encoding="utf-8")
        css = (LMS / "static" / "staff-shell.css").read_text(encoding="utf-8")
        apply = js.split("function applyHideKey()")[1].split("\n}\n")[0]
        self.assertIn("live-questions-layout", apply)
        self.assertIn("live-question-list", apply)
        self.assertNotIn("teacher-minimized-list", apply)
        self.assertIn(".live-questions-layout.is-key-hidden", css)
        self.assertIn(".live-question-list.is-key-hidden", css)
        self.assertNotIn(".teacher-minimized-list.is-key-hidden", css)


class StudentMinimizeSidebarTests(unittest.TestCase):
    """Teacher-minimized questions live in the student side panel."""

    def test_minimized_host_in_chrome(self) -> None:
        """Minimized bars render in chrome-top, not over the main stack."""
        html = (LMS / "templates" / "student" / "home.html").read_text(encoding="utf-8")
        js = (LMS / "static" / "student-portal.js").read_text(encoding="utf-8")
        css = (LMS / "static" / "student-portal.css").read_text(encoding="utf-8")
        chrome = html.split('class="student-chrome student-chrome-top"')[1].split(
            "</header>"
        )[0]
        self.assertIn('id="student-minimized-host"', chrome)
        self.assertNotIn(
            'id="student-minimized-host"',
            html.split("<main ")[1].split("</main>")[0],
        )
        paint = js.split("function paintStudentMinimizedBars(")[1].split(
            "function paintLifecycleQuestionStack("
        )[0]
        self.assertIn("students_minimized", js)
        self.assertNotIn("student-minimized-offset", paint)
        self.assertNotIn("student-minimized-offset", css)


class WhiteboardFilterRemovalTests(unittest.TestCase):
    """Teacher whiteboard gallery no longer ships filter chips."""

    def test_filters_removed_from_shell_and_board(self) -> None:
        """Template, CSS, gallery JS, and payloads drop the filter row."""
        html = (LMS / "templates" / "staff" / "course.html").read_text(encoding="utf-8")
        js = (LMS / "static" / "formative_live_board.js").read_text(encoding="utf-8")
        css = (LMS / "static" / "whiteboard_formative.css").read_text(encoding="utf-8")
        qb = (LMS / "question_board.py").read_text(encoding="utf-8")
        self.assertNotIn("wb-live-filters", html)
        self.assertNotIn("paintFilters", js)
        self.assertNotIn("wb-live-filter", css)
        self.assertNotIn('"filters":', qb)


if __name__ == "__main__":
    unittest.main()
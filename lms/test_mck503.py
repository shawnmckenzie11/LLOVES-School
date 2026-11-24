"""MCK-503: class list overlay drawer, teacher minimize panel, drop wb filters."""

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
        """Toggle, scrim, close, and Esc share one open flag."""
        html = (LMS / "templates" / "staff" / "course.html").read_text(encoding="utf-8")
        js = (LMS / "static" / "staff_ap.js").read_text(encoding="utf-8")
        css = (LMS / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn('id="class-list-scrim"', html)
        self.assertIn('id="class-list-close"', html)
        self.assertIn('aria-label="Show class list"', html)
        self.assertIn("class-list-drawer", html)
        self.assertIn("classListToggleAriaLabel", js)
        self.assertIn('setAttribute("aria-label"', js)
        self.assertIn("class-list-scrim", css)
        self.assertIn("translateX", css)
        self.assertIn(
            "body.staff-shell #class-list-pane .ap-att-row.has-team-color .ap-att-name",
            css,
        )
        self.assertNotIn('id="live-shell-left" aria-hidden="true"', html)

    def test_class_list_esc_yields_to_dialog(self) -> None:
        """Esc closes the drawer only when no modal dialog is open."""
        js = (LMS / "static" / "staff_ap.js").read_text(encoding="utf-8")
        esc = js.split('if (event.key !== "Escape" || !classListIsOpen()) return;')[1].split(
            "setClassListOpen(false)"
        )[0]
        self.assertIn("dialog[open]", esc)


class TeacherMinimizePanelTests(unittest.TestCase):
    """Minimized questions render in the right-hand panel."""

    def test_minimized_panel_wiring(self) -> None:
        """Closed + students_minimized cards are painted outside the main list."""
        html = (LMS / "templates" / "staff" / "course.html").read_text(encoding="utf-8")
        js = (LMS / "static" / "staff_ap.js").read_text(encoding="utf-8")
        css = (LMS / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn('id="teacher-minimized-panel"', html)
        self.assertIn('id="teacher-minimized-list"', html)
        self.assertIn('id="live-questions-layout"', html)
        self.assertIn("teacherQuestionInMinimizedPanel", js)
        self.assertIn("handleLiveQuestionCardClick", js)
        self.assertIn("liveQuestionsLayout.addEventListener", js)
        self.assertIn("liveQuestionCardForItem", js)
        self.assertIn("has-teacher-minimized", css)
        self.assertIn("teacher-minimized-panel", css)

    def test_hide_key_covers_minimize_dock(self) -> None:
        """MCK-155: Hide key hides Answer chips and marks in the minimize dock too."""
        js = (LMS / "static" / "staff_ap.js").read_text(encoding="utf-8")
        css = (LMS / "static" / "staff-shell.css").read_text(encoding="utf-8")
        apply = js.split("function applyHideKey()")[1].split("\n}\n")[0]
        self.assertIn("live-questions-layout", apply)
        self.assertIn("teacher-minimized-list", apply)
        self.assertIn(".live-questions-layout.is-key-hidden", css)
        self.assertIn(".teacher-minimized-list.is-key-hidden", css)


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
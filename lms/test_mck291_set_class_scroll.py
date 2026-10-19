#!/usr/bin/env python3
"""MCK-291: Set Class scrolls to the day calendar. Live session layout stays fixed."""

from __future__ import annotations

import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
CSS_PATH = LMS_DIR / "static" / "staff-shell.css"


class SetClassScrollCssTests(unittest.TestCase):
    """The setup shell scrolls. A running class keeps the clipped live shell."""

    def setUp(self) -> None:
        """Load the staff shell stylesheet once per test."""
        self.css = CSS_PATH.read_text(encoding="utf-8")

    def _setup_block(self) -> str:
        """Return the MCK-291 rules and nothing from the live-session shell."""
        start = self.css.index("/* MCK-291:")
        end = self.css.index("/* MCK-291 end */", start)
        return self.css[start:end]

    def test_setup_view_scrolls(self) -> None:
        """Set Class releases the viewport clip and lets the page scroll."""
        block = self._setup_block()
        self.assertIn(".is-set-class", block)
        self.assertIn("overflow-y: auto", block)
        self.assertIn("overflow: visible", block)
        self.assertIn("height: auto", block)
        self.assertIn("grid-template-rows: auto auto auto", block)
        self.assertIn("#set-class-day-step", block)
        self.assertIn("position: sticky", block)

    def test_live_session_shell_stays_clipped(self) -> None:
        """Running-class geometry is the fixed three-row shell, not the setup override."""
        self.assertIn(
            "body.staff-shell.course-live {\n  height: 100vh;\n  overflow: hidden;",
            self.css,
        )
        self.assertIn(
            "body.staff-shell.course-live .track-live-root.live-shell-root {\n"
            "  flex: 1 1 0;\n"
            "  min-height: 0;\n"
            "  overflow: hidden;\n"
            "  grid-template-rows: auto auto minmax(0, 1fr);",
            self.css,
        )
        block = self._setup_block()
        self.assertNotIn("#live-frames", block)
        self.assertNotIn("#live-active-content", block)
        self.assertNotIn("#class-list-pane", block)

    def test_if_then_forward_actions_stay_hidden(self) -> None:
        """MCK-271 still hides header Start and Advance while Set Class is open."""
        hide = self.css[
            self.css.index("/* MCK-271: Set Class keeps one forward action. */") : self.css.index(
                "/* MCK-271 end */"
            )
        ]
        self.assertIn(".is-set-class", hide)
        for token in (
            "#live-stage-prev",
            "#live-eyes-up",
            "#live-end-class",
            "#live-start",
            "#live-advance",
        ):
            self.assertIn(token, hide)
        self.assertNotIn("#live-quit-class", hide)


if __name__ == "__main__":
    unittest.main()

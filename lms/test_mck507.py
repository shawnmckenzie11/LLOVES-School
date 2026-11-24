"""MCK-507: class list header chip, clickable drawer, no X close."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

LMS = Path(__file__).resolve().parent
ROOT = LMS.parent
sys.path.insert(0, str(LMS))


class ClassListHeaderTests(unittest.TestCase):
    """Permanent Class (N) chip sits left of JOIN CODE on the live header."""

    def test_toggle_in_header_before_join_code(self) -> None:
        """The toggle is the first control in live-header-join, before JOIN CODE."""
        html = (LMS / "templates" / "staff" / "course.html").read_text(encoding="utf-8")
        css = (LMS / "static" / "staff-shell.css").read_text(encoding="utf-8")
        header = html.split('id="live-header"')[1].split("</header>")[0]
        self.assertIn('class="live-header-join"', header)
        self.assertIn('id="class-list-toggle"', header)
        self.assertIn("JOIN CODE", header)
        self.assertLess(header.index("class-list-toggle"), header.index("JOIN CODE"))
        self.assertIn("live-header-join .class-list-toggle", css)
        self.assertNotIn("class-list-collapsed .class-list-toggle::before", css)

    def test_drawer_outside_shell_left(self) -> None:
        """Pane and scrim mount on the live root, not under pointer-events:none left rail."""
        html = (LMS / "templates" / "staff" / "course.html").read_text(encoding="utf-8")
        header_end = html.index("</header>")
        body_start = html.index('class="live-shell-body"')
        between = html[header_end:body_start]
        self.assertIn('id="class-list-pane"', between)
        self.assertIn('id="class-list-scrim"', between)
        left = html.split('id="live-shell-left"')[1].split("live-shell-right")[0]
        self.assertNotIn("class-list-pane", left)
        self.assertNotIn("class-list-toggle", left)


class ClassListInteractionTests(unittest.TestCase):
    """Drawer controls receive clicks; only toggle and Esc close."""

    def test_no_x_or_scrim_close(self) -> None:
        """No X button; scrim is visual-only; Hide class list is the click-to-close path."""
        html = (LMS / "templates" / "staff" / "course.html").read_text(encoding="utf-8")
        js = (LMS / "static" / "staff_ap.js").read_text(encoding="utf-8")
        css = (LMS / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertNotIn('id="class-list-close"', html)
        self.assertNotIn("class-list-close", css)
        self.assertNotIn('getElementById("class-list-close")', html)
        self.assertNotIn("class-list-close", js)
        bind = js.split("function bindClassListToggle()")[1].split("let classListEscBound")[0]
        self.assertNotIn("scrim.addEventListener", bind)
        self.assertIn("pointer-events: none", css.split(".class-list-scrim")[1].split("}")[0])
        self.assertIn("pointer-events: auto", css.split("#class-list-pane.class-list-drawer")[1][:400])

    def test_esc_and_hide_class_list_close(self) -> None:
        """Esc closes when no dialog is open; toggle aria flips Show/Hide."""
        js = (LMS / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn('event.key !== "Escape"', js)
        self.assertIn("syncClassListDrawerTop", js)
        self.assertIn("--class-list-drawer-top", js)
        esc = js.split('if (event.key !== "Escape" || !classListIsOpen()) return;')[1].split(
            "setClassListOpen(false)"
        )[0]
        self.assertIn("dialog[open]", esc)
        script = r"""
import { classListToggleTitle, classListToggleAriaLabel } from "./lms/static/class_list_rail.js";
if (classListToggleTitle(true) !== "Hide class list") throw new Error("open title");
if (classListToggleAriaLabel(true) !== "Hide class list") throw new Error("open aria");
if (classListToggleTitle(false) !== "Show class list") throw new Error("closed title");
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


if __name__ == "__main__":
    unittest.main()
#!/usr/bin/env python3
"""MCK-529: enlarge inline stem and answer images on the student MC card."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
STATIC = LMS_DIR / "static"
NODE = "node"


class Mck529SourceTests(unittest.TestCase):
    """The student card opens the shared lightbox from inline images."""

    def test_card_script(self) -> None:
        """Stem tap, option magnifier, and option-image select pass the node check."""
        node = STATIC / "live_question_card.test.mjs"
        result = subprocess.run(
            [NODE, str(node)],
            cwd=str(node.parent),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("ok", result.stdout)

    def test_close_button_is_labelled_close(self) -> None:
        """The enlarged view's close control reads Close for sighted and AT users."""
        js = (STATIC / "live_question_card.js").read_text(encoding="utf-8")
        block = js.split("function ensureQuestionLightbox")[1].split(
            "export function mountQuestionLightbox"
        )[0]
        self.assertIn('aria-label="Close"', block)
        self.assertIn(">Close</button>", block)

    def test_magnifier_matches_the_placement_spec(self) -> None:
        """Top-right 28px icon on 80% white, with a 44px tap target."""
        css = (STATIC / "student-portal.css").read_text(encoding="utf-8")
        self.assertIn(".live-q-enlarge-option {", css)
        control = css.split(".live-q-enlarge-option {")[1].split(".live-q-enlarge-option-icon {")[0]
        self.assertIn("width: 44px;", control)
        self.assertIn("height: 44px;", control)
        self.assertIn("top: 0;", control)
        self.assertIn("right: 0;", control)
        icon = css.split(".live-q-enlarge-option-icon {")[1].split(
            ".live-q-enlarge-option-icon svg"
        )[0]
        self.assertIn("width: 28px;", icon)
        self.assertIn("height: 28px;", icon)
        self.assertIn("border-radius: 50%;", icon)
        self.assertIn("background: rgba(255, 255, 255, 0.8);", icon)

    def test_option_magnifier_copy(self) -> None:
        """Wonder's tooltip and aria-label stay Enlarge image."""
        js = (STATIC / "live_question_card.js").read_text(encoding="utf-8")
        self.assertIn('const ENLARGE_IMAGE_LABEL = "Enlarge image";', js)
        decorate = js.split("function decorateOptionImage")[1].split("export function enhanceQuestionImages")[0]
        self.assertIn('setAttribute("title", ENLARGE_IMAGE_LABEL)', decorate)
        self.assertIn('setAttribute("aria-label", ENLARGE_IMAGE_LABEL)', decorate)


if __name__ == "__main__":
    unittest.main()

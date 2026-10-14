#!/usr/bin/env python3
"""MCK-218 formative whiteboard helpers and board-read presence guard."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from whiteboard_formative import (  # noqa: E402
    FORMATIVE_MEMBER_PALETTE,
    formative_whiteboard_enabled,
    member_color,
    first_name_only,
)


class FormativeHelperTests(unittest.TestCase):
    """Palette and feature flag."""

    def test_member_palette_matches_spec(self) -> None:
        """Six slot colours from the MCK-218 spec."""
        self.assertEqual(FORMATIVE_MEMBER_PALETTE[0].lower(), "#2dd4bf")
        self.assertEqual(len(FORMATIVE_MEMBER_PALETTE), 6)

    def test_seventh_member_reuses_palette(self) -> None:
        """Slot six wraps with the same hex as slot zero."""
        self.assertEqual(member_color(6), member_color(0))

    def test_first_name_only(self) -> None:
        """Chips use the first token only."""
        self.assertEqual(first_name_only("Noah Maple"), "Noah")

    def test_flag_defaults_on(self) -> None:
        """Formative UI is on unless LLOVES_FORMATIVE_WHITEBOARD=0."""
        prior = os.environ.pop("LLOVES_FORMATIVE_WHITEBOARD", None)
        try:
            self.assertTrue(formative_whiteboard_enabled())
            os.environ["LLOVES_FORMATIVE_WHITEBOARD"] = "0"
            self.assertFalse(formative_whiteboard_enabled())
        finally:
            if prior is None:
                os.environ.pop("LLOVES_FORMATIVE_WHITEBOARD", None)
            else:
                os.environ["LLOVES_FORMATIVE_WHITEBOARD"] = prior


if __name__ == "__main__":
    unittest.main()

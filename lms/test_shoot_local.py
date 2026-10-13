#!/usr/bin/env python3
"""scripts/shoot.py stays on loopback and never opens the live alc site."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "shoot.py"
_SPEC = importlib.util.spec_from_file_location("lloves_shoot", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
shoot = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(shoot)


class ShootGuardTests(unittest.TestCase):
    """URL guard for the headless screenshot helper."""

    def test_loopback_is_allowed(self) -> None:
        """127.0.0.1 and localhost pass the guard."""
        self.assertTrue(shoot.assert_loopback("http://127.0.0.1:8787/staff").endswith("/staff"))
        self.assertIn("localhost", shoot.assert_loopback("http://localhost:8787/"))

    def test_live_alc_is_refused(self) -> None:
        """The public LMS host is not a screenshot target."""
        with self.assertRaises(SystemExit):
            shoot.assert_loopback("https://alc.mckenzian.com/staff/class/1?tab=lesson-slides")

    def test_slug_keeps_the_lesson_slides_tab(self) -> None:
        """A query string is part of the filename slug."""
        slug = shoot.slug_for("http://127.0.0.1:8787/staff/class/1?tab=lesson-slides")
        self.assertIn("lesson-slides", slug)
        self.assertNotIn("/", slug)


if __name__ == "__main__":
    unittest.main()

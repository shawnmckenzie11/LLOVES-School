#!/usr/bin/env python3
"""Interim Module + Live class catalogue registry."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)

from live_class_packs import (  # noqa: E402
    live_class_registry,
    minds_on_brief_relpath,
    normalize_live_module,
)


class LiveClassPacksTests(unittest.TestCase):
    """Catalogue scan finds the wired MCF3M M1 C1/C2/C3 packs."""

    def test_mcf3m_m1_c1_c2_c3(self) -> None:
        """Beat 30 interim: MCF3M exposes Module 1 live classes C1–C3."""
        registry = live_class_registry("MCF3M")
        self.assertEqual(registry["course"], "MCF3M")
        self.assertTrue(registry["interim"])
        self.assertIn("M1", registry["modules"])
        self.assertEqual(registry["slots_by_module"]["M1"], ["C1", "C2", "C3"])
        self.assertEqual(normalize_live_module("m2"), "M2")
        self.assertEqual(normalize_live_module("nope"), "M1")
        self.assertIn(
            "minds-on/MCF3M-M1-C1-minds-on-student.md",
            minds_on_brief_relpath("MCF3M", "M1", "C1"),
        )


if __name__ == "__main__":
    unittest.main()

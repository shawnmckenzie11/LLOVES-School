#!/usr/bin/env python3
"""File-backed Module + Live class metadata registry."""

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
    """Metadata scan finds four live classes in every MCF3M module."""

    def test_mcf3m_has_four_live_classes_per_module(self) -> None:
        """MCF3M exposes C1–C4 from each of its eight metadata modules."""
        registry = live_class_registry("MCF3M")
        self.assertEqual(registry["course"], "MCF3M")
        self.assertFalse(registry["interim"])
        self.assertEqual(registry["modules"], [f"M{i}" for i in range(1, 9)])
        self.assertEqual(
            registry["slots_by_module"]["M1"], ["C1", "C2", "C3", "C4"]
        )
        self.assertTrue(
            all(
                slots == ["C1", "C2", "C3", "C4"]
                for slots in registry["slots_by_module"].values()
            )
        )
        self.assertEqual(normalize_live_module("m2"), "M2")
        self.assertEqual(normalize_live_module("nope"), "M1")
        self.assertIn(
            "minds-on/MCF3M-M1-C1-minds-on-student.md",
            minds_on_brief_relpath("MCF3M", "M1", "C1"),
        )


if __name__ == "__main__":
    unittest.main()

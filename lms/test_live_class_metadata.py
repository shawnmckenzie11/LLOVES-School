#!/usr/bin/env python3
"""Tests for file-backed Run Live Class metadata."""

from __future__ import annotations

import unittest

from lms.live_class_metadata import (
    load_live_class_metadata,
    metadata_path,
    questions_for_stage,
)
from scripts.generate_live_class_metadata import COURSES, LIVE_CLASSES, MODULES


class LiveClassMetadataTests(unittest.TestCase):
    """Every math course ships four complete metadata files per module."""

    def test_every_course_module_has_four_metadata_files(self) -> None:
        """All ten courses expose M1–M8 and C1–C4 on disk."""

        paths = [
            metadata_path(course, module, live_class)
            for course in COURSES
            for module in MODULES
            for live_class in LIVE_CLASSES
        ]
        self.assertEqual(len(paths), 320)
        self.assertTrue(all(path.is_file() for path in paths))

    def test_mcf3m_m1c1_keeps_media_and_typed_questions(self) -> None:
        """Known MCF3M metadata includes the 3D media and ordered Join cards."""

        metadata = load_live_class_metadata("MCF3M", "M1", "C1")
        self.assertEqual(
            metadata["media"]["file"],
            "/static/live-media/m1c1-c1-real-slice.html",
        )
        join = questions_for_stage(metadata, "join")
        self.assertEqual([row["type"] for row in join], ["mc", "numeric"])
        self.assertEqual(join[0]["correct_answer"], "A")
        self.assertTrue(join[0]["default_visibility"])
        self.assertFalse(join[1]["default_visibility"])

    def test_numeric_questions_drop_options(self) -> None:
        """Numeric metadata normalizes to integer-entry without choices."""

        metadata = load_live_class_metadata("MHF4U", "M8", "C4")
        join = questions_for_stage(metadata, "join")
        self.assertEqual(join[0]["type"], "numeric")
        self.assertEqual(join[0]["options"], [])
        self.assertIsNone(join[0]["correct_answer"])


if __name__ == "__main__":
    unittest.main()

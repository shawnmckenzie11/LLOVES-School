#!/usr/bin/env python3
"""VLC Math Game Show welcome copy and payload helper."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))

os.environ.pop("GOOGLE_CLIENT_ID", None)

from game_show_welcome import (  # noqa: E402
    GAME_SHOW_TITLE,
    game_show_welcome_payload,
)


class GameShowWelcomeTests(unittest.TestCase):
    """Locked title, three rounds, and class/lesson codes."""

    def test_payload_has_banner_codes_and_round_blurbs(self) -> None:
        """Welcome card carries title, codes, the three rounds, and avatars."""
        payload = game_show_welcome_payload(
            class_code="MCF3M",
            lesson_code="M1-C2",
            participants=[{"codename": "Maple", "character": "fox"}],
        )
        self.assertEqual(payload["title"], GAME_SHOW_TITLE)
        self.assertEqual(payload["title"], "VLC Math Game Show")
        self.assertEqual(payload["class_code"], "MCF3M")
        self.assertEqual(payload["lesson_code"], "M1-C2")
        titles = [row["title"] for row in payload["rounds"]]
        self.assertEqual(
            titles,
            [
                "Open Question Round",
                "Team Challenge Round",
                "Consolidation Round",
            ],
        )
        for row in payload["rounds"]:
            self.assertTrue(row["blurb"])
        self.assertEqual(payload["participants"][0]["character"], "fox")


if __name__ == "__main__":
    unittest.main(verbosity=2)

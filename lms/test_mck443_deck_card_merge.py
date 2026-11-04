#!/usr/bin/env python3
"""MCK-443: run the deck_card_merge node checks and guard the staff wiring."""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent


class DeckCardMergeTests(unittest.TestCase):
    """A removed deck card stays gone after (Re)move."""

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_node_harness(self) -> None:
        """``node static/deck_card_merge.test.mjs`` prints ok."""

        proc = subprocess.run(
            ["node", str(LMS_DIR / "static" / "deck_card_merge.test.mjs")],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("ok", proc.stdout)

    def test_staff_panel_uses_the_filter(self) -> None:
        """Remove records the id and the card merge drops it."""

        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        merge = js.split("function questionCardsFromMetadata(")[1].split(
            "function applyLocalPlaylistCardChange("
        )[0]
        self.assertIn("withoutRemovedCards(", merge)
        relocate = js.split("async function relocatePlaylistItem(")[1].split(
            "function playlistPageLabel("
        )[0]
        self.assertIn('applyLocalPlaylistCardChange(token, "remove")', relocate)


if __name__ == "__main__":
    unittest.main()

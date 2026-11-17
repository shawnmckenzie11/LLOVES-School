#!/usr/bin/env python3
"""MCK-473: poll question boards are not redrawn on an unchanged live sync.

Group and individual boards sit inside question cards. A live-sync paint
replaces those cards. The whiteboard nodes have to move with the new card.
Several paints with the same ink must keep the same canvases, leave an
opened board open, and not draw again.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO = LMS_DIR.parent
SCRIPT = LMS_DIR / "static" / "question_board_flicker_mck473.test.mjs"


class QuestionBoardFlickerTests(unittest.TestCase):
    """Unchanged sync polls keep group and individual poll boards still."""

    def test_sync_polls_do_not_redraw_poll_boards(self) -> None:
        """Three card paints keep the same canvases and do not draw again."""
        proc = subprocess.run(
            ["node", str(SCRIPT)],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(
            proc.returncode,
            0,
            (proc.stdout or "")[-4000:] + (proc.stderr or "")[-4000:],
        )
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        failed = [name for name, ok in payload.items() if ok is False]
        self.assertEqual(failed, [], payload)


if __name__ == "__main__":
    unittest.main()

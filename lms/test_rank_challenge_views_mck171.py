#!/usr/bin/env python3
"""MCK-171 Team challenge (b): projector lanes on the teacher's rank card.

Runs the node harness for ``static/rank_challenge_view.js`` and checks the
teacher shell wiring (lanes replace the stack until Close, the light poll
carries the lanes, Lock in / Skip confirm by team name, reduced motion).
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
STATIC = LMS_DIR / "static"


class LaneViewTests(unittest.TestCase):
    """Pure lane builder (node) and the staff shell wiring."""

    def test_view_node_harness(self) -> None:
        proc = subprocess.run(
            ["node", str(STATIC / "rank_challenge_view.test.mjs")],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("ok", proc.stdout)

    def test_staff_shell_wiring(self) -> None:
        staff = (STATIC / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn('from "/static/rank_challenge_view.js"', staff)
        # Lanes replace the stack while open; the light poll carries them.
        self.assertIn("rankRaceTeacherHtml(result, revealed, itemId)", staff)
        self.assertIn("{ race: entry.race }", staff)
        # Teacher controls confirm by team, never by student.
        self.assertIn("RACE_COPY.lockForConfirm, { team }", staff)
        self.assertIn("RACE_COPY.skipConfirm, { team }", staff)
        self.assertIn("/rank-lock`", staff)
        self.assertIn("RACE_COPY.view", staff)

    def test_projector_css(self) -> None:
        css = (STATIC / "staff-shell.css").read_text(encoding="utf-8")
        start = css.index("MCK-171 Team challenge: projector lanes")
        end = css.find("\n/* MCK-", start)
        block = css[start : end if end > 0 else len(css)]
        self.assertIn("font-size: 28px", block)  # team names
        self.assertIn("font-size: 22px", block)  # status
        self.assertIn("font-size: 40px", block)  # icons
        self.assertIn("race-check-pop 300ms", block)
        # Absent lanes: muted text + dashed outline, never a fade.
        self.assertNotIn("opacity", block)
        self.assertIn("outline: 2px dashed", block)
        self.assertIn("color: #5b6779", block)  # 5.9:1 on white
        self.assertIn("min-height: 44px", block)
        reduced = block[block.index("prefers-reduced-motion") :]
        self.assertIn("animation: none", reduced)


if __name__ == "__main__":
    unittest.main()

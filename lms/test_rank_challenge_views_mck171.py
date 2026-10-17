#!/usr/bin/env python3
"""MCK-171 Team challenge (b): projector lane builder and teacher shell.

Runs the node harness for ``static/rank_challenge_view.js`` and checks the
teacher shell wiring. MCK-274: the open teacher card is the compact stack,
not the projector lanes. Lock in / Skip confirm by team name, and reduced
motion on the lane CSS, stay covered here.
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
        # Open card falls through to the compact stack; the light poll
        # still carries the race block.
        self.assertIn("rankRaceTeacherHtml(result, revealed, itemId)", staff)
        self.assertIn("{ race: entry.race }", staff)
        fn = staff.split("function rankRaceTeacherHtml")[1].split("\nfunction ")[0]
        self.assertIn("if (!revealed) return", fn)
        self.assertNotIn("raceLanesHtml", fn)
        # Teacher controls confirm by team, never by student.
        self.assertIn("RACE_COPY.lockForConfirm, { team }", staff)
        self.assertIn("RACE_COPY.skipConfirm, { team }", staff)
        self.assertIn("/rank-lock`", staff)
        self.assertIn("RACE_COPY.view", staff)
        # MCK-274: a race block does not hide the strip Reveal.
        showing = staff.split("const raceLanesShowing")[1].split(";")[0]
        self.assertNotIn("result?.race", showing)
        self.assertIn("? raceLanesShowing\n            ? moveOnHtml", staff)

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

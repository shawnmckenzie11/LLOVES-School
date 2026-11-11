#!/usr/bin/env python3
"""MCK-469: teacher board tiles fill the width; an open board keeps aspect.

The gallery script owns the column count and the open-board box. CSS
keeps the same contract when the script has not measured yet, and it
does not restyle the student drawing surface.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent

NODE_CHECKS = r"""
const {
  galleryColumnCount,
  expandedBoardBox,
  tileBitmapSize,
  BOARD_LOGICAL,
} = await import('./lms/static/formative_live_board.js');

const wide = galleryColumnCount(1400, 8, 4, 200, 10);
const two = galleryColumnCount(1400, 2, 4, 200, 10);
const cap3 = galleryColumnCount(1400, 9, 3, 200, 10);
const narrow = galleryColumnCount(520, 8, 4, 200, 10);
const phone = galleryColumnCount(360, 8, 4, 200, 10);
const one = galleryColumnCount(1400, 1, 5, 200, 10);
if (wide !== 4 || two !== 2 || cap3 !== 3 || narrow !== 2 || phone !== 1 || one !== 1) {
  console.error(JSON.stringify({ wide, two, cap3, narrow, phone, one }));
  process.exit(1);
}

const box = expandedBoardBox(960);
if (box.width !== 960 || box.height !== 480) {
  console.error(JSON.stringify(box));
  process.exit(2);
}
if (Math.abs(box.scale - 960 / BOARD_LOGICAL.width) > 1e-9) process.exit(3);

const odd = expandedBoardBox(1000);
const nx = 0.3;
const ny = 0.8;
const logicalX = nx * BOARD_LOGICAL.width;
const logicalY = ny * BOARD_LOGICAL.height;
const drawnX = logicalX * odd.scale;
const drawnY = logicalY * odd.scale;
if (Math.abs(drawnX - nx * odd.width) > 1e-6) process.exit(4);
if (Math.abs(drawnY - ny * odd.height) > 1e-6) process.exit(5);
if (Math.abs(odd.width / odd.height - BOARD_LOGICAL.width / BOARD_LOGICAL.height) > 1e-9) {
  process.exit(6);
}

const tile = tileBitmapSize(320);
if (tile.width !== 320 || tile.height !== 160) {
  console.error(JSON.stringify(tile));
  process.exit(7);
}
if (tile.width / tile.height !== BOARD_LOGICAL.width / BOARD_LOGICAL.height) process.exit(8);

const hidden = galleryColumnCount(0, 4, 4, 200, 10);
if (hidden !== 1) process.exit(9);
"""


class WhiteboardGridLayoutTests(unittest.TestCase):
    """Column fill and uniform scale for the teacher gallery."""

    def test_column_count_and_uniform_scale(self) -> None:
        """Wide rows fill, narrow rows drop columns, and both axes share one scale."""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", NODE_CHECKS],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_gallery_css_fills_width_and_keeps_aspect(self) -> None:
        """The teacher grid and the open board are full width at 2:1, not a side drawer."""
        css = (LMS_DIR / "static" / "whiteboard_formative.css").read_text(encoding="utf-8")
        self.assertIn("grid-template-columns: minmax(0, 1fr)", css)
        self.assertNotIn("minmax(0, 1fr) 360px", css)
        self.assertNotIn("min(560px, 46vw)", css)
        self.assertIn("aspect-ratio: 2 / 1", css)
        self.assertIn("repeat(auto-fit, minmax(min(100%, 12.5rem), 1fr))", css)
        panel = css.split(".wb-live .wb-live-panel-stage > .wb-live-panel-canvas", 1)[1]
        panel_rule = panel.split("}", 1)[0]
        self.assertIn("width: 100%", panel_rule)
        self.assertIn("aspect-ratio: 2 / 1", panel_rule)
        self.assertNotIn("height: 100%", panel_rule)

    def test_submission_dialog_uses_the_same_full_width_grid(self) -> None:
        """Lesson Slides submissions are not locked to two columns or a 720px floor."""
        shell = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        start = shell.index("dialog.ls-board-dialog {")
        chunk = shell[start : start + 700]
        self.assertIn("width: min(96vw, 100%)", chunk)
        self.assertIn("max-width: none", chunk)
        self.assertNotIn("minmax(220px, 1fr)", chunk)
        self.assertNotIn("min-width: 720px", chunk)
        self.assertNotIn("min-width: 480px", chunk)

    def test_student_board_css_is_unchanged(self) -> None:
        """The student drawing surface is not given the teacher panel's aspect rule."""
        student = (LMS_DIR / "static" / "student-portal.css").read_text(encoding="utf-8")
        self.assertNotIn(".wb-live-panel-canvas", student)
        block = student.split("\n.student-canvas {", 1)[1].split("}", 1)[0]
        self.assertIn("width: 100%", block)
        self.assertIn("height: auto", block)
        self.assertNotIn("aspect-ratio: 2 / 1", block)
        formative = (LMS_DIR / "static" / "whiteboard_formative.css").read_text(encoding="utf-8")
        live = formative.split(".canvas-pane .student-canvas {", 1)[1].split("}", 1)[0]
        self.assertIn("height: 100%", live)
        self.assertNotIn("aspect-ratio", live)

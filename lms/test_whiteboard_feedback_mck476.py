#!/usr/bin/env python3
"""MCK-476: feedback sits beside an opened board, and Prev/Next leaves it open.

The fit reuses expandedBoardBox. The column takes 336px only while the
board would still be at least 640px wide.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO = LMS_DIR.parent
SCRIPT = LMS_DIR / "e2e" / "whiteboard_feedback_mck476.mjs"

NODE_CHECKS = r"""
const {
  openedBoardSlot,
  openedBoardHeight,
  fitOpenedBoard,
  expandedBoardBox,
  panelHeading,
  panelStatusLabel,
  typedAnswerVisible,
  BOARD_LOGICAL,
} = await import('./lms/static/formative_live_board.js');

const wide = openedBoardSlot(1280);
if (!wide.beside || wide.width !== 1280 - 336) {
  console.error(JSON.stringify(wide));
  process.exit(1);
}
const about = openedBoardSlot(1236);
if (!about.beside || about.width !== 900) process.exit(2);

const edge = openedBoardSlot(976);
if (!edge.beside || edge.width !== 640) process.exit(3);
const narrow = openedBoardSlot(975);
if (narrow.beside || narrow.width !== 975) process.exit(4);
const small = openedBoardSlot(880);
if (small.beside || small.width !== 880) process.exit(5);

const fitted = fitOpenedBoard(wide.width, 700);
const plain = expandedBoardBox(wide.width);
if (fitted.width !== plain.width || fitted.height !== plain.height) process.exit(6);
if (Math.abs(fitted.width / fitted.height - BOARD_LOGICAL.width / BOARD_LOGICAL.height) > 1e-9) {
  process.exit(7);
}

const short = fitOpenedBoard(944, 300);
if (short.height > 300.01) process.exit(8);
if (Math.abs(short.width / short.height - 2) > 1e-6) process.exit(9);
if (short.width >= 944) process.exit(10);

const wideScreen = fitOpenedBoard(900, 800, { width: 16, height: 9 });
if (wideScreen.width !== 900) process.exit(11);
if (Math.abs(wideScreen.width / wideScreen.height - 16 / 9) > 1e-6) process.exit(12);

if (openedBoardHeight(500, 700) !== 500) process.exit(13);
if (openedBoardHeight(900, 700) !== 700) process.exit(14);
if (openedBoardHeight(40, 700) !== 700) process.exit(15);

const solo = panelHeading({ name: 'Board A', status: 'Drawing' });
if (solo.title !== 'Board A' || solo.members || solo.status !== 'Drawing') process.exit(16);
const team = panelHeading({
  name: 'Team 1',
  team_sort_order: 1,
  member_names: ['Seat A', 'Seat B'],
  status: 'Submitted',
});
if (team.title !== 'Team 1' || team.members !== 'Seat A, Seat B' || team.status !== 'Submitted') {
  process.exit(17);
}
if (panelStatusLabel({ teacher_wrote: true }) !== 'Teacher wrote') process.exit(18);
if (panelStatusLabel({ drawing: true }) !== 'Drawing') process.exit(19);
if (!typedAnswerVisible('both', 'The line crosses at 2.')) process.exit(20);
if (typedAnswerVisible('board', 'The line crosses at 2.')) process.exit(21);
if (typedAnswerVisible('both', '   ')) process.exit(22);
"""


def _chrome() -> str:
    """Return a Chrome binary, or an empty string when this machine has none."""
    for path in (
        os.environ.get("LLOVES_E2E_CHROME", ""),
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
    ):
        if path and Path(path).is_file():
            return path
    return ""


def _ready() -> bool:
    """True when node, Chrome, and playwright-core can launch."""
    if not (shutil.which("node") and _chrome()):
        return False
    env = {**os.environ}
    node_path = os.environ.get("LLOVES_E2E_NODE_PATH", "").strip()
    if node_path:
        env["NODE_PATH"] = node_path
    probe = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "import { createRequire } from 'node:module';"
                "createRequire(process.cwd() + '/lms/e2e/whiteboard_feedback_mck476.mjs')"
                ".resolve('playwright-core');"
            ),
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


class WhiteboardFeedbackLayoutTests(unittest.TestCase):
    """Fit math and the markup the opened board uses."""

    def test_fit_reuses_the_narrower_width(self) -> None:
        """Side by side above 640px, overlay below, aspect unchanged."""
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", NODE_CHECKS],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_panel_markup_sits_beside_the_board(self) -> None:
        """The column is 320px on the right, and the canvas rule is unchanged."""
        css = (LMS_DIR / "static" / "whiteboard_formative.css").read_text(encoding="utf-8")
        self.assertIn("grid-template-columns: minmax(0, max-content) 320px", css)
        self.assertIn("gap: 16px", css)
        self.assertIn("background: #12202e", css)
        self.assertIn("rgba(255, 255, 255, 0.12)", css)
        self.assertIn("animation: wb-panel-fade 120ms linear", css)
        self.assertIn("width: 40px", css)
        panel = css.split(".wb-live .wb-live-panel-stage > .wb-live-panel-canvas", 1)[1]
        panel_rule = panel.split("}", 1)[0]
        self.assertIn("width: 100%", panel_rule)
        self.assertIn("aspect-ratio: 2 / 1", panel_rule)
        self.assertNotIn("height: 100%", panel_rule)
        page = (LMS_DIR / "templates" / "staff" / "course.html").read_text(encoding="utf-8")
        board = page.index('class="wb-live-board"')
        feedback = page.index('id="wb-live-feedback"')
        self.assertLess(board, feedback)
        self.assertIn("Clear my ink", page)
        self.assertIn("Show on board", page)
        self.assertNotIn("Clear board", page)
        question = (LMS_DIR / "static" / "question_board_ui.js").read_text(encoding="utf-8")
        self.assertIn("Clear my ink", question)
        self.assertNotIn("Save feedback", question)
        self.assertIn('rows="2"', question)
        script = (LMS_DIR / "static" / "formative_live_board.js").read_text(encoding="utf-8")
        self.assertIn("fitOpenedBoard", script)
        self.assertNotIn("canvas.style.width", script)


@unittest.skipUnless(
    _ready(),
    "node + Chrome + playwright-core (LLOVES_E2E_NODE_PATH) not available",
)
class WhiteboardFeedbackOpenTests(unittest.TestCase):
    """Prev/Next keeps the column open, and a narrow window overlays it."""

    def test_prev_next_keeps_the_panel_open(self) -> None:
        """1280 side by side, then the overlay collapses without cropping."""
        out = Path("/opt/cursor/artifacts")
        if not out.is_dir():
            out = Path(os.environ.get("TMPDIR", "/tmp"))
        env = {**os.environ, "CHROME_PATH": _chrome()}
        node_path = os.environ.get("LLOVES_E2E_NODE_PATH", "").strip()
        if node_path:
            env["NODE_PATH"] = node_path
        proc = subprocess.run(
            ["node", str(SCRIPT), str(out)],
            cwd=str(REPO),
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, (proc.stdout or "")[-4000:] + (proc.stderr or "")[-4000:])
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        failed = [name for name, ok in payload.items() if name != "details" and not ok]
        self.assertEqual(failed, [], payload)

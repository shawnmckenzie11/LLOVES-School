#!/usr/bin/env python3
"""MCK-473: an opened teacher board survives live polls, and the grid does not rebuild.

The gallery used to replace every tile canvas on each poll and to size the
open board from the element a resize observer was watching. This drives
Chrome against that page: several sync updates must leave the same tile
nodes in place and the opened board at full width.
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
SCRIPT = LMS_DIR / "e2e" / "whiteboard_grid_mck473.mjs"


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
                "createRequire(process.cwd() + '/lms/e2e/whiteboard_grid_mck473.mjs')"
                ".resolve('playwright-core');"
            ),
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


@unittest.skipUnless(
    _ready(),
    "node + Chrome + playwright-core (LLOVES_E2E_NODE_PATH) not available",
)
class WhiteboardGridFlickerTests(unittest.TestCase):
    """Opened board stays up, and tile nodes survive several polls."""

    def test_open_board_survives_sync_and_grid_does_not_rebuild(self) -> None:
        """Pins on the tiles remain, the panel stays open, and resize stays quiet."""
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

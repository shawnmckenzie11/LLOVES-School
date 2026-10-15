#!/usr/bin/env python3
"""MCK-218 formative whiteboard headless acceptance (dr eggbot)."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
SCRIPT = LMS_DIR / "e2e" / "whiteboard_mck218.mjs"


def _chrome() -> str:
    for path in (
        os.environ.get("LLOVES_E2E_CHROME", ""),
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
    ):
        if path and Path(path).exists():
            return path
    return ""


def _node_path() -> str:
    return os.environ.get("LLOVES_E2E_NODE_PATH", "").strip()


def _ready() -> bool:
    if not (shutil.which("node") and _chrome() and _node_path()):
        return False
    env = {**os.environ, "NODE_PATH": _node_path()}
    probe = subprocess.run(
        ["node", "-e", "require.resolve('playwright-core')"],
        env=env,
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


@unittest.skipUnless(
    _ready(), "node + Chrome + playwright-core (LLOVES_E2E_NODE_PATH) not available"
)
class WhiteboardMck218BrowserTests(unittest.TestCase):
    """Seven dr eggbot checks for formative chips and text tool."""

    def setUp(self) -> None:
        logging.disable(logging.WARNING)
        os.environ["ALLOW_DEV_VERIFICATION_CODE"] = "1"
        os.environ["LOCAL_DEV_LOGIN"] = "1"
        from app import create_app
        from werkzeug.serving import make_server

        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.server = make_server("127.0.0.1", 0, self.app, threaded=True)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        try:
            self.server.shutdown()
            self.server.server_close()
            self.app.config["SCHOOL_DB"].close()
        finally:
            logging.disable(logging.NOTSET)
            self.tmp.cleanup()

    def test_formative_acceptance_checks(self) -> None:
        """Run the seven headless acceptance checks against the dev formative shell."""
        out = Path(self.tmp.name) / "mck218"
        env = {
            **os.environ,
            "NODE_PATH": _node_path(),
            "CHROME_PATH": _chrome(),
        }
        proc = subprocess.run(
            ["node", str(SCRIPT), self.base, str(out)],
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-4000:] + proc.stdout[-4000:])
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        failed = [name for name, ok in payload.get("checks", {}).items() if not ok]
        self.assertEqual(failed, [], payload)


if __name__ == "__main__":
    unittest.main()

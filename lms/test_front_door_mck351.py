"""MCK-351: a visitor reaches the request form in one click.

Needs node, Chrome, and playwright-core. The check is skipped when those
are not installed. Desktop is 1280×800. Mobile is 375×667.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO))
SCRIPT = LMS_DIR / "e2e" / "front_door_mck351.mjs"
SHOTS = (
    "p0-6-landing-desktop.png",
    "p0-6-landing-mobile.png",
    "p0-6-403-desktop.png",
    "p0-6-403-mobile.png",
    "p0-6-confirm-desktop.png",
    "p0-6-confirm-mobile.png",
)


def _chrome() -> str:
    """Return a Chrome binary, or \"\" when this machine has none."""
    for path in (
        os.environ.get("LLOVES_E2E_CHROME", ""),
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/local/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ):
        if path and Path(path).exists():
            return path
    return ""


def _node_path() -> str:
    """Directory node uses to resolve playwright-core."""
    return os.environ.get("LLOVES_E2E_NODE_PATH", "").strip()


def _ready() -> bool:
    """True when node, Chrome, and playwright-core can launch.

    A package next to the repo is enough. ``LLOVES_E2E_NODE_PATH`` is optional
    so a checkout that installed playwright-core at the repo root still runs.
    """
    if not (shutil.which("node") and _chrome()):
        return False
    env = {**os.environ}
    if _node_path():
        env["NODE_PATH"] = _node_path()
    probe = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                "import { createRequire } from 'node:module';"
                "createRequire(process.cwd() + '/lms/e2e/front_door_mck351.mjs')"
                ".resolve('playwright-core');"
            ),
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


def _shot_dir(fallback: Path) -> Path:
    """Prefer the artifact directory, and fall back to the test temp dir.

    Args:
        fallback: Directory that is always writable for this test.
    """
    preferred = Path("/opt/cursor/artifacts")
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        probe = preferred / ".mck351-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        return fallback
    return preferred


@unittest.skipUnless(
    _ready(),
    "node + Chrome + playwright-core (LLOVES_E2E_NODE_PATH) not available",
)
class FrontDoorBrowserTests(unittest.TestCase):
    """Landing, the unknown-account page, and the confirmation at two sizes."""

    def setUp(self) -> None:
        """One offline app. No roster and no live class."""
        logging.disable(logging.WARNING)
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        os.environ["ALLOW_DEV_VERIFICATION_CODE"] = "1"
        from app import create_app

        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.server = None

    def tearDown(self) -> None:
        """Stop the server and close the database before removing the temp dir."""
        try:
            server = getattr(self, "server", None)
            if server is not None:
                server.shutdown()
                server.server_close()
            self.school.close()
        finally:
            logging.disable(logging.NOTSET)
            self.tmp.cleanup()

    def test_request_access_is_one_click_at_both_sizes(self) -> None:
        """Each viewport can open the form from home and from the unknown-account page."""
        from werkzeug.serving import make_server

        self.server = make_server("127.0.0.1", 0, self.app, threaded=True)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        out_dir = _shot_dir(Path(self.tmp.name) / "shots")
        info = {
            "port": self.server.server_port,
            "chrome": _chrome(),
            "outDir": str(out_dir),
            "forbiddenPath": "/auth/google/callback?email=visitor@example.com&portal=staff",
            "applicant": {
                "name": "Sample Applicant",
                "email": "sample.applicant@example.com",
                "context": "One senior class.",
            },
        }
        cfg = Path(self.tmp.name) / "e2e.json"
        cfg.write_text(json.dumps(info), encoding="utf-8")
        env = {**os.environ}
        if _node_path():
            env["NODE_PATH"] = _node_path()
        proc = subprocess.run(
            ["node", str(SCRIPT), str(cfg)],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        lines = [line for line in proc.stdout.splitlines() if line.startswith("{")]
        self.assertTrue(lines, (proc.stderr or "")[-2000:] + (proc.stdout or "")[-2000:])
        out = json.loads(lines[-1])
        self.assertEqual(out["errors"], [], out)
        failed = [step for step in out["steps"] if not step["ok"]]
        self.assertEqual(failed, [], json.dumps(out, ensure_ascii=False)[:4000])
        self.assertEqual(len(out["steps"]), 6, out["steps"])
        for name in SHOTS:
            self.assertTrue((out_dir / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()

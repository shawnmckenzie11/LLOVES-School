#!/usr/bin/env python3
"""MCK-180: Fly stops the machine with SIGTERM and waits 35 s.

Fly's default is SIGINT with a 5 s wait. gunicorn treats SIGINT as a quick
shutdown, and 5 s is shorter than its 30 s ``--graceful-timeout``, so the
v216 machine swap dropped in-flight requests (about 14 proxy errors during
a live class). These tests lock the fly.toml keys, check that the signal
reaches gunicorn (exec-form CMD, no shell), and show that gunicorn finishes
an in-flight request on SIGTERM.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import tomllib
import unittest
import urllib.request
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
if str(LMS_DIR) not in sys.path:
    sys.path.insert(0, str(LMS_DIR))

import serve_capacity as cap  # noqa: E402

FLY_TOML = REPO_ROOT / "fly.toml"
DOCKERFILE = LMS_DIR / "Dockerfile"


class FlyShutdownConfigTests(unittest.TestCase):
    """fly.toml asks for gunicorn's graceful stop and waits long enough."""

    def setUp(self) -> None:
        self.text = FLY_TOML.read_text(encoding="utf-8")
        self.fly = tomllib.loads(self.text)

    def test_kill_signal_and_timeout_are_top_level(self) -> None:
        """Both keys parse at the top level, not inside a table."""
        self.assertEqual(self.fly.get("kill_signal"), "SIGTERM")
        self.assertEqual(self.fly.get("kill_signal"), cap.FLY_KILL_SIGNAL)
        self.assertEqual(self.fly.get("kill_timeout"), 35)
        self.assertEqual(self.fly.get("kill_timeout"), cap.FLY_KILL_TIMEOUT_SECONDS)
        for name, table in self.fly.items():
            if isinstance(table, dict):
                self.assertNotIn("kill_signal", table, name)
                self.assertNotIn("kill_timeout", table, name)

    def test_timeout_covers_gunicorn_graceful_window(self) -> None:
        """Fly waits longer than gunicorn's graceful timeout, within Fly's max."""
        timeout = int(self.fly["kill_timeout"])
        self.assertGreater(timeout, cap.GRACEFUL_TIMEOUT_SECONDS)
        self.assertLessEqual(timeout, cap.FLY_KILL_TIMEOUT_MAX_SECONDS)
        self.assertIn(
            f"--graceful-timeout {cap.GRACEFUL_TIMEOUT_SECONDS}",
            self.fly["processes"]["app"],
        )

    def test_signal_reaches_gunicorn_directly(self) -> None:
        """No shell or wrapper sits between Fly's init and gunicorn.

        The image CMD is exec form and there is no ENTRYPOINT. The fly.toml
        process command starts with gunicorn and has no shell syntax, so it
        runs as an argv list and gunicorn is the process Fly signals.
        """
        docker = DOCKERFILE.read_text(encoding="utf-8")
        lines = [ln.strip() for ln in docker.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        self.assertFalse([ln for ln in lines if ln.upper().startswith(("ENTRYPOINT", "SHELL"))])
        cmds = [ln for ln in lines if ln.upper().startswith("CMD")]
        self.assertEqual(len(cmds), 1, cmds)
        argv = json.loads(cmds[0][3:].strip())
        self.assertIsInstance(argv, list)
        self.assertEqual(argv[0], "gunicorn")
        self.assertNotIn("STOPSIGNAL SIGINT", docker)
        command = self.fly["processes"]["app"]
        self.assertEqual(shlex.split(command)[0], "gunicorn")
        for token in ("sh -c", "bash", "&&", "||", ";", "|", "$(", "`"):
            self.assertNotIn(token, command, token)
        self.assertEqual(shlex.split(command), argv)


TINY_APP = textwrap.dedent(
    """
    import time

    def app(environ, start_response):
        if environ.get("PATH_INFO") == "/slow":
            time.sleep(2.5)
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"done"]
    """
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@unittest.skipUnless(
    shutil.which("gunicorn") or (Path(sys.executable).parent / "gunicorn").exists(),
    "gunicorn is not installed",
)
class GunicornSigtermTests(unittest.TestCase):
    """gunicorn finishes an in-flight request on SIGTERM, then exits 0."""

    def test_sigterm_finishes_in_flight_request(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        Path(tmp.name, "tiny_app.py").write_text(TINY_APP, encoding="utf-8")
        port = _free_port()
        exe = Path(sys.executable).parent / "gunicorn"
        gunicorn = str(exe) if exe.exists() else shutil.which("gunicorn")
        proc = subprocess.Popen(
            [
                gunicorn,
                "--worker-class", "gthread",
                "--workers", "1",
                "--threads", "2",
                "--graceful-timeout", str(cap.GRACEFUL_TIMEOUT_SECONDS),
                "--bind", f"127.0.0.1:{port}",
                "--chdir", tmp.name,
                "tiny_app:app",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 20
        while True:
            try:
                urllib.request.urlopen(f"{base}/", timeout=1).read()
                break
            except OSError:
                if time.monotonic() > deadline or proc.poll() is not None:
                    self.fail("gunicorn did not start")
                time.sleep(0.2)
        result: dict[str, object] = {}

        def slow() -> None:
            try:
                with urllib.request.urlopen(f"{base}/slow", timeout=15) as res:
                    result["status"] = res.status
                    result["body"] = res.read()
            except Exception as exc:  # noqa: BLE001 - reported below
                result["error"] = repr(exc)

        worker = threading.Thread(target=slow)
        worker.start()
        time.sleep(0.8)  # the request is now in flight
        proc.send_signal(signal.SIGTERM)
        worker.join(15)
        code = proc.wait(timeout=cap.GRACEFUL_TIMEOUT_SECONDS + 5)
        stderr = proc.stderr.read() if proc.stderr else ""
        self.assertEqual(result.get("status"), 200, (result, stderr[-2000:]))
        self.assertEqual(result.get("body"), b"done")
        self.assertEqual(code, 0, stderr[-2000:])


if __name__ == "__main__":
    unittest.main()

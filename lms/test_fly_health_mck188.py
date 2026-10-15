#!/usr/bin/env python3
"""MCK-188: Fly routes only after GET /health, on the same one machine.

v219's proxy opened connections while gunicorn was not listening yet.
``[[http_service.checks]]`` is the service check that holds traffic until
a worker answers. It is not a second machine, a bigger VM, a top-level
machine check, or an ephemeral ``machine_checks`` runner.
"""

from __future__ import annotations

import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
if str(LMS_DIR) not in sys.path:
    sys.path.insert(0, str(LMS_DIR))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

import serve_capacity as cap  # noqa: E402

FLY_TOML = REPO_ROOT / "fly.toml"


class FlyHealthCheckConfigTests(unittest.TestCase):
    """fly.toml withholds proxy traffic until /health, without new machines."""

    def setUp(self) -> None:
        self.fly = tomllib.loads(FLY_TOML.read_text(encoding="utf-8"))

    def test_http_service_check_targets_health(self) -> None:
        """One GET /health service check, with the serve_capacity timings."""
        checks = self.fly["http_service"]["checks"]
        self.assertEqual(len(checks), 1, checks)
        check = checks[0]
        self.assertEqual(check["path"], cap.FLY_HEALTH_CHECK_PATH)
        self.assertEqual(check["path"], "/health")
        self.assertEqual(check["method"], cap.FLY_HEALTH_CHECK_METHOD)
        self.assertEqual(check["protocol"], cap.FLY_HEALTH_CHECK_PROTOCOL)
        self.assertEqual(check["grace_period"], cap.FLY_HEALTH_CHECK_GRACE_PERIOD)
        self.assertEqual(check["interval"], cap.FLY_HEALTH_CHECK_INTERVAL)
        self.assertEqual(check["timeout"], cap.FLY_HEALTH_CHECK_TIMEOUT)
        self.assertEqual(check["grace_period"], "10s")
        self.assertEqual(check["timeout"], "5s")
        self.assertEqual(check["interval"], "5s")

    def test_check_does_not_add_a_machine_or_a_restart_probe(self) -> None:
        """Same one VM. No top-level checks and no ephemeral machine check."""
        self.assertNotIn("checks", self.fly)
        http = self.fly["http_service"]
        self.assertNotIn("machine_checks", http)
        self.assertEqual(http["min_machines_running"], 1)
        self.assertEqual(http["auto_stop_machines"], "off")
        self.assertEqual(http["internal_port"], 8080)
        vms = self.fly["vm"]
        self.assertEqual(len(vms), 1, vms)
        self.assertEqual(vms[0]["memory"], "1gb")
        self.assertEqual(vms[0]["cpu_kind"], "shared")
        self.assertEqual(vms[0]["cpus"], 1)
        self.assertEqual(list(self.fly["processes"]), ["app"])
        self.assertEqual(len(self.fly["mounts"]), 1)
        self.assertEqual(self.fly["mounts"][0]["source"], "lloves_data")

    def test_health_route_is_public_200(self) -> None:
        """The check path answers 200 with no login and no presence ping."""
        from app import create_app

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = create_app(
            db_path=Path(tmp.name) / "lloves.sqlite",
            data_dir=Path(tmp.name),
            testing=True,
            live_database_url="",
        )
        rv = app.test_client().get("/health")
        self.assertEqual(rv.status_code, 200)
        body = rv.get_json()
        self.assertTrue(body["ok"])
        self.assertIn("live_presence", body)


if __name__ == "__main__":
    unittest.main()

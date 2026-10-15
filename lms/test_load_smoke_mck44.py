#!/usr/bin/env python3
"""MCK-44: deploy-gate limits for scripts/load_class.py.

The full 30-seat run is the deploy workflow, against a local gunicorn.
These tests lock the pass/fail rules and the refusal to point at production.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load():
    """Import scripts/load_class.py without running it."""
    path = REPO / "scripts" / "load_class.py"
    spec = importlib.util.spec_from_file_location("load_class_script", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LoadSmokeGateTests(unittest.TestCase):
    """Gate math and the local-only presence check."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load()

    def test_two_classes_of_fifteen_are_thirty_disjoint_seats(self) -> None:
        """The deploy smoke's roster is 15 + 15, with no shared codename."""
        a = self.mod.class_codenames(0, 15)
        b = self.mod.class_codenames(1, 15)
        self.assertEqual(len(a), 15)
        self.assertEqual(len(b), 15)
        self.assertEqual(len(set(a) | set(b)), 30)
        self.assertTrue(all(name.startswith("A") for name in a))
        self.assertTrue(all(name.startswith("B") for name in b))

    def test_presence_url_must_be_loopback(self) -> None:
        """Production hosts are refused. The smoke never dials alc."""
        self.assertTrue(self.mod.presence_is_local("postgresql://lloves:lloves@127.0.0.1:5432/lloves_live"))
        self.assertTrue(self.mod.presence_is_local("postgresql://lloves@localhost/lloves_live"))
        self.assertFalse(self.mod.presence_is_local("postgresql://lloves@alc.mckenzian.com/lloves"))
        self.assertFalse(self.mod.presence_is_local("postgresql://lloves@lloves-lms.fly.dev/lloves"))
        self.assertFalse(self.mod.presence_is_local("postgresql://lloves@example.com/lloves"))

    def _summary(self, **overrides: object) -> dict:
        base = {
            "requests": 400,
            "http_p95_ms": 40.0,
            "errors_5xx": 0,
            "worker_restarts": 0,
            "log_worker_timeouts": 0,
            "log_autorestarts": 0,
            "max_workers": 4,
            "classes": 2,
            "kinds": {"student_state": {"n": 300}, "sse_first_byte": {"n": 20}},
        }
        base.update(overrides)
        return base

    def test_gate_passes_a_clean_two_class_run(self) -> None:
        """0 errors, p95 under 250, 0 restarts, both classes, all workers."""
        errors = self.mod.smoke_gate_errors(
            self._summary(),
            max_p95_ms=250,
            min_workers=4,
            min_classes=2,
        )
        self.assertEqual(errors, [])

    def test_gate_blocks_errors_p95_restarts_and_one_class(self) -> None:
        """Each Ops limit is its own failure, including p95 of exactly 250."""
        cases = [
            {"errors_5xx": 1},
            {"http_p95_ms": 250.0},
            {"worker_restarts": 1},
            {"log_autorestarts": 1},
            {"max_workers": 3},
            {"classes": 1},
            {"requests": 0, "kinds": {}},
        ]
        for overrides in cases:
            errors = self.mod.smoke_gate_errors(
                self._summary(**overrides),
                max_p95_ms=250,
                min_workers=4,
                min_classes=2,
            )
            self.assertTrue(errors, overrides)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""MCK-498: four LMS shards, green-SHA reuse, and the 599-only smoke retry.

These checks read the workflows and the shard scripts. They do not import
the rest of the suite, so they cannot drop or double-run those tests.
"""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    """Import a script by path without running it.

    Args:
        name: Module name to register.
        path: Script file.

    Returns:
        The loaded module.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ShardPlanTests(unittest.TestCase):
    """The four shards cover the serial suite once, minus the fan-out file."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load("lms_test_shards_mck498", REPO / ".github" / "scripts" / "lms_test_shards.py")

    def test_plan_drops_nothing_and_keeps_schema_tests(self) -> None:
        """File parity with discover, fan-out excluded, mandatory modules kept."""
        on_disk = sorted(path.name for path in (REPO / "lms").glob("test_*.py"))
        suite = self.mod.suite_modules()
        buckets = self.mod.assign_shards()
        flat = [name for bucket in buckets for name in bucket]
        self.assertEqual(len(buckets), 4)
        self.assertEqual(sorted(flat), suite)
        self.assertEqual(set(on_disk) - set(suite), {"test_fanout_guard.py"})
        self.assertNotIn("test_fanout_guard.py", flat)
        mandatory = self.mod.mandatory_modules()
        self.assertIn("test_boot_schema_race.py", mandatory)
        self.assertGreaterEqual(sum(name.startswith("test_mck365_") for name in mandatory), 3)
        self.assertTrue(set(mandatory) <= set(flat))
        self.mod.check_plan()

    def test_weights_stay_in_the_same_band(self) -> None:
        """Longest-processing-time packing does not leave a shard at half weight."""
        buckets = self.mod.assign_shards()
        weights = [
            sum(self.mod.module_weight(REPO / "lms" / name) for name in bucket)
            for bucket in buckets
        ]
        self.assertGreater(min(weights), 0)
        self.assertLessEqual(max(weights) / min(weights), 1.15)

    def test_sqlite_urls_are_refused(self) -> None:
        """LIVE_DATABASE_URL and DATABASE_URL must not reach the shard runner."""
        previous = {key: os.environ.get(key) for key in ("LIVE_DATABASE_URL", "DATABASE_URL")}
        os.environ["DATABASE_URL"] = "postgresql://lloves@127.0.0.1/lloves_live"
        try:
            with self.assertRaises(SystemExit):
                self.mod.assert_sqlite_heartbeat_env()
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_report_names_dropped_modules_and_warns_softly(self) -> None:
        """A missing module fails. A slow module is a warning, not an error."""
        docs = [
            {
                "shard": 1,
                "seconds": 10,
                "modules": [{"module": "test_boot_schema_race.py", "tests": 1, "seconds": 1, "failed": False}],
            }
        ]
        _text, errors = self.mod.render_report(docs, lms_touched_flag="true")
        self.assertTrue(any(item.startswith("dropped modules:") for item in errors))
        self.assertTrue(any("mandatory" in item for item in errors))
        slow = [
            {
                "shard": 1,
                "seconds": 100,
                "modules": [
                    {"module": name, "tests": 1, "seconds": 1, "failed": False}
                    for name in self.mod.suite_modules()
                ],
            }
        ]
        slow[0]["modules"][0]["seconds"] = 120
        text, errors = self.mod.render_report(slow, lms_touched_flag="false")
        self.assertEqual(errors, [])
        self.assertIn("::warning::", text)
        self.assertIn("dropped=0", text)

    def test_reuse_skip_is_green_without_timings(self) -> None:
        """A verified reuse does not need shard artifacts on the deploy run."""
        with tempfile.TemporaryDirectory() as tmp:
            code = self.mod.aggregate(Path(tmp), "skipped", "true", "true")
        self.assertEqual(code, 0)


class ReuseDecisionTests(unittest.TestCase):
    """Copy-only and partial CI runs must not skip the deploy shards."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load("reuse_green_ci_mck498", REPO / ".github" / "scripts" / "reuse_green_ci.py")

    def test_self_test_refuses_skipped_jobs(self) -> None:
        """The script's own fixtures cover a full green run and a copy-only run."""
        self.mod._self_test()

    def test_success_workflow_with_skipped_load_jobs_runs_tests(self) -> None:
        """A green conclusion is not enough when fan-out or slide load was skipped."""
        jobs = [{"name": name, "conclusion": "success"} for name in self.mod.REQUIRED_JOBS]
        for name in ("fan-out guard", "30-seat load budget", "slide forward load"):
            for job in jobs:
                if job["name"] == name:
                    job["conclusion"] = "skipped"
        runs = [{"id": 11, "status": "completed", "conclusion": "success", "jobs": jobs}]
        skip, reason = self.mod.choose_reuse(runs)
        self.assertFalse(skip)
        self.assertIn("skipped", reason)

    def test_missing_shard_is_not_a_full_run(self) -> None:
        """The reuse list names every shard. A missing shard is not success."""
        jobs = [
            {"name": name, "conclusion": "success"}
            for name in self.mod.REQUIRED_JOBS
            if name != "LMS tests shard 2 of 4"
        ]
        ok, reason = self.mod.full_green(jobs)
        self.assertFalse(ok)
        self.assertIn("shard 2", reason)


class LoadRetryTests(unittest.TestCase):
    """The 599 retry matches the deploy run that failed on client timeouts only."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load("load_class_mck498", REPO / "scripts" / "load_class.py")

    def _summary(self, **status_extra: int) -> dict:
        """Build the 5x 599 shape from deploy run 45250408429 attempt 1.

        Args:
            status_extra: Extra status counts merged into student_state.

        Returns:
            A summarize-shaped dict. No names or codes.
        """
        student = {"200": 422, "599": 2}
        student.update(status_extra)
        sse_bad = 3
        student_bad = sum(count for code, count in student.items() if int(code) >= 500)
        return {
            "requests": 1019,
            "http_p95_ms": 104.4,
            "errors_5xx": sse_bad + student_bad,
            "worker_restarts": 0,
            "log_worker_timeouts": 0,
            "log_autorestarts": 0,
            "max_workers": 4,
            "classes": 2,
            "kinds": {
                "sse_first_byte": {"n": 84, "errors_5xx": sse_bad, "status": {"200": 81, "599": 3}},
                "student_state": {"n": 424, "errors_5xx": student_bad, "status": {str(k): v for k, v in student.items()}},
            },
        }

    def test_five_client_timeouts_may_retry_once(self) -> None:
        """http_p95 104 ms and exactly five 599s is the retryable gate failure."""
        summary = self._summary()
        self.assertEqual(summary["errors_5xx"], 5)
        failures = self.mod.smoke_gate_errors(summary, max_p95_ms=400, min_workers=4, min_classes=2)
        self.assertEqual(failures, ["errors_5xx=5, want 0"])
        decision = self.mod.client_timeout_retry(summary, failures)
        self.assertTrue(decision["retry"])
        self.assertIn("599", decision["reason"])

    def test_server_status_and_latency_do_not_retry(self) -> None:
        """500-504 and a p95 miss fail hard. An empty failure list does not retry."""
        summary = self._summary(**{"500": 1})
        failures = self.mod.smoke_gate_errors(summary, max_p95_ms=400, min_workers=4, min_classes=2)
        decision = self.mod.client_timeout_retry(summary, failures)
        self.assertFalse(decision["retry"])
        self.assertIn("500", decision["reason"])
        slow = self._summary()
        slow["http_p95_ms"] = 400.0
        slow_failures = self.mod.smoke_gate_errors(slow, max_p95_ms=400, min_workers=4, min_classes=2)
        self.assertFalse(self.mod.client_timeout_retry(slow, slow_failures)["retry"])
        self.assertFalse(self.mod.client_timeout_retry(summary, [])["retry"])

    def test_summary_line_on_the_metrics_we_have(self) -> None:
        """Smoke omits slide metrics. Slide forward includes them. One line either way."""
        smoke = self.mod.format_load_summary_line({"http_p95_ms": 104.4, "errors_5xx": 5, "state_shed": 0})
        self.assertEqual(
            smoke,
            "load-summary state_p95_ms=n/a http_p95_ms=104.4 state_shed=0 errors_5xx=5 slide_reload_peak=n/a",
        )
        slide = self.mod.format_load_summary_line(
            {
                "state_p95_ms": 701.1,
                "http_p95_ms": 40.0,
                "state_shed": 0,
                "errors_5xx": 0,
                "slide_reload_peak": 2,
            }
        )
        self.assertNotIn("\n", slide)
        self.assertIn("state_p95_ms=701.1", slide)
        self.assertIn("slide_reload_peak=2", slide)

    def test_slide_gate_stays_at_800ms(self) -> None:
        """The slide-forward latency gate is not loosened."""
        text = (REPO / "scripts" / "load_class.py").read_text(encoding="utf-8")
        self.assertIn("if state_p95 >= 800:", text)


class WorkflowRailTests(unittest.TestCase):
    """ci.yml and deploy.yml keep the required check names and the deploy rails."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        cls.deploy = (REPO / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    def test_both_workflows_shard_the_suite(self) -> None:
        """Four shards, presence URL only, and the aggregate is named LMS test suite."""
        for text in (self.ci, self.deploy):
            self.assertEqual(text.count("shard: [1, 2, 3, 4]"), 1)
            self.assertIn("name: LMS test suite", text)
            self.assertIn("name: LMS tests shard ${{ matrix.shard }} of 4", text)
            self.assertIn("LIVE_PRESENCE_TEST_URL:", text)
            self.assertNotRegex(text, r"(?m)^\s+LIVE_DATABASE_URL:")
            self.assertNotRegex(text, r"(?m)^\s+DATABASE_URL:")
            self.assertIn("lms_test_shards.py run --shard", text)
            self.assertNotIn("discover -s lms -p 'test_*.py'", text)
        self.assertIn("name: fan-out guard", self.ci)
        self.assertIn("test_fanout_guard.py", self.ci)
        self.assertIn("--sqlite-budget", self.ci)
        self.assertIn("name: 30-seat load budget", self.ci)
        self.assertIn("name: slide forward load", self.ci)

    def test_slide_forward_is_not_retried(self) -> None:
        """The 599 retry is the deploy smoke only. Slide load keeps one command."""
        slide = self.ci[self.ci.index("  slide-load:\n"):]
        self.assertNotIn("retry.json", slide)
        self.assertNotIn("retrying once", slide)
        self.assertEqual(self.deploy.count("retrying once"), 1)
        self.assertIn("database or setup errors are not retried", self.deploy)

    def test_deploy_rails_stay(self) -> None:
        """SHA guard, dev-login guard, class hours twice, one approval, no cancel of flyctl."""
        deploy = self.deploy[self.deploy.index("  deploy:\n"):]
        self.assertIn("origin/production", deploy)
        self.assertIn("LOCAL_DEV_LOGIN", deploy)
        self.assertIn("class_hours_guard.py", deploy)
        self.assertEqual(self.deploy.count("class_hours_guard.py"), 2)
        self.assertIn("--plain", self.deploy)
        self.assertEqual(self.deploy.count("environment:\n      name: production"), 1)
        self.assertIn("cancel-in-progress: false", self.deploy)
        self.assertIn("flyctl deploy --remote-only", deploy)
        self.assertNotIn("actions: write", self.deploy)
        smoke = self.deploy[self.deploy.index("  load-smoke:\n"):self.deploy.index("  deploy:\n")]
        self.assertNotIn("needs: test", smoke)
        self.assertIn("--max-p95-ms 400", smoke)
        self.assertNotIn("--max-p95-ms 250", self.deploy)
        drop = smoke[smoke.index("name: Drop load smoke databases"):]
        self.assertIn("if: always()", drop[:200])


if __name__ == "__main__":
    unittest.main()

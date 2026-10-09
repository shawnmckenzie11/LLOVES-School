#!/usr/bin/env python3
"""MCK-183 follow-up: refused student steps (400/409 from rank-turn and similar) reach Sentry.

Server: one throttled Sentry Logs line (``alc.action = student.step_rejected``)
with endpoint, status, the API's short error text and class/course tags.
Browser: a ``student.step`` warning breadcrumb, sent only with a later error.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import sentry_sdk

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

import sentry_wire  # noqa: E402
from app import create_app  # noqa: E402
from sentry_wire import (  # noqa: E402
    ACTION_LOG_ATTRIBUTE,
    StepLogThrottle,
    before_send_log,
    sentry_init_options,
    step_rejection,
)
from test_sentry_teacher_mck183 import CaptureTransport, _all_text, _items  # noqa: E402


class StepRejectionHelperTests(unittest.TestCase):
    """Which responses count, and what the line carries."""

    def test_student_writes_with_4xx_only(self) -> None:
        """POST rank-turn 409 counts; GET, 5xx, staff, ops and presence don't."""
        attrs = step_rejection(
            "/api/student/live-items/5/rank-turn", "POST", "api_student_rank_turn", 409,
            {"ok": False, "error": "Not your turn (3 left)"},
        )
        self.assertEqual(attrs[ACTION_LOG_ATTRIBUTE], "student.step_rejected")
        self.assertEqual(attrs["endpoint"], "api_student_rank_turn")
        self.assertEqual(attrs["http.status_code"], 409)
        self.assertEqual(attrs["error"], "Not your turn (# left)")
        self.assertIsNotNone(before_send_log({"attributes": attrs}))
        for args in (
            ("/api/student/state", "GET", "x", 409, None),
            ("/api/student/live-items/5/rank-turn", "POST", "x", 500, None),
            ("/api/student/live-items/5/rank-turn", "POST", "x", 401, None),
            ("/api/live-sessions/1/items/2/publish", "POST", "x", 409, None),
            ("/api/student/board/main/ops", "POST", "x", 409, None),
            ("/api/student/canvas-presence", "POST", "x", 400, None),
        ):
            self.assertIsNone(step_rejection(*args), args)

    def test_error_text_masks_emails_and_numbers(self) -> None:
        """No emails or ids in the error attribute; long text is cut."""
        attrs = step_rejection(
            "/api/student/vote", "POST", "e", 400,
            {"error": "rae@gmail.com tried 12345 " + "x" * 200},
        )
        self.assertNotIn("rae@", attrs["error"])
        self.assertNotIn("12345", attrs["error"])
        self.assertLessEqual(len(attrs["error"]), 80)

    def test_throttle(self) -> None:
        """Same key once per window; a per-minute cap across keys."""
        throttle = StepLogThrottle(window=10, per_minute=3)
        self.assertTrue(throttle.allow(("a",), 0.0))
        self.assertFalse(throttle.allow(("a",), 5.0))
        self.assertTrue(throttle.allow(("a",), 11.0))
        self.assertTrue(throttle.allow(("b",), 12.0))
        self.assertFalse(throttle.allow(("c",), 13.0))
        self.assertTrue(throttle.allow(("c",), 61.0))


class StepRejectionServerTests(unittest.TestCase):
    """A real SDK client with an in-memory transport."""

    def setUp(self) -> None:
        """Sentry on, fresh app, a student-only failing route."""
        self.transport = CaptureTransport()
        options = sentry_init_options("https://public@o0.ingest.sentry.io/1")
        options["traces_sampler"] = lambda _ctx: 0.0
        sentry_sdk.init(transport=self.transport, **options)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)

        def refuse(live_item_id: int):
            """Test-only refused student step."""
            from flask import jsonify
            return jsonify({"ok": False, "error": "Not your turn"}), 409

        self.app.add_url_rule(
            "/api/student/live-items/<int:live_item_id>/test-step", "test_step", refuse,
            methods=["POST"],
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        sentry_wire._STEP_THROTTLE = StepLogThrottle()

    def tearDown(self) -> None:
        """SDK off again."""
        sentry_sdk.get_client().close()
        sentry_sdk.init()
        self.school.close()
        self.tmp.cleanup()

    def test_refused_step_writes_one_log_line(self) -> None:
        """409 → one warning line with endpoint, status, item id; repeats throttled."""
        for _ in range(3):
            rv = self.client.post("/api/student/live-items/42/test-step", json={"name": "Quillon"})
            self.assertEqual(rv.status_code, 409)
        sentry_sdk.flush()
        logs = [
            line for line in _items(self.transport, "log")
            if (line.get("attributes") or {}).get(ACTION_LOG_ATTRIBUTE, {}).get("value")
            == "student.step_rejected"
        ]
        self.assertEqual(len(logs), 1)
        line = logs[0]
        self.assertEqual(line["level"], "warn")
        attrs = {k: v.get("value") for k, v in line["attributes"].items()}
        self.assertEqual(attrs["endpoint"], "test_step")
        self.assertEqual(attrs["http.status_code"], 409)
        self.assertEqual(attrs["live_item_id"], "42")
        self.assertEqual(attrs["error"], "Not your turn")
        self.assertEqual([e for e in _items(self.transport, "event") if e.get("exception")], [])
        self.assertNotIn("Quillon", _all_text(self.transport))


class StepRejectionBrowserTests(unittest.TestCase):
    """sentry-live.js leaves a breadcrumb for a refused student step."""

    def test_breadcrumb_for_refused_step(self) -> None:
        """Node sandbox run of the browser file."""
        script = r"""const fs = require("fs");
const vm = require("vm");
const src = fs.readFileSync(process.argv[1], "utf8");
function meta(content) { return { content, getAttribute() { return this.content; } }; }
const metas = {
  'meta[name="lloves-sentry-dsn"]': meta("https://public@o0.ingest.sentry.io/1"),
  'meta[name="lloves-sentry-environment"]': meta("tip"),
  'meta[name="lloves-sentry-user"]': meta("7"),
  'meta[name="lloves-sentry-tags"]': meta('{"teacher_id":"7","teacher_kind":"other","portal":"staff","tool":"attendance","class_id":"2","course_code":"SBI4U"}'),
};
const calls = { user: null, tags: null, messages: [], exceptions: [], crumbs: [] };
let fetchStatus = 500;
const sandbox = {
  console,
  location: { origin: "https://alc.mckenzian.com" },
  document: { querySelector(sel) { return metas[sel] || null; } },
  fetch: function () { return Promise.resolve({ status: fetchStatus, ok: fetchStatus < 400 }); },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.Sentry = {
  init(opts) { sandbox.__opts = opts; },
  browserTracingIntegration() { return { name: "BrowserTracing" }; },
  setUser(u) { calls.user = u; },
  setTags(t) { calls.tags = t; },
  captureMessage(m, ctx) { calls.messages.push([m, ctx]); },
  captureException(e, ctx) { calls.exceptions.push([e, ctx]); },
  addBreadcrumb(c) { calls.crumbs.push(c); },
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const opts = sandbox.__opts;
function assert(cond, msg) { if (!cond) throw new Error(msg); }
(async () => {
  fetchStatus = 409;
  await sandbox.fetch("/api/student/live-items/5/rank-turn?n=Quillon", { method: "POST" });
  assert(calls.crumbs.length === 1, "no crumb for 409 rank-turn");
  const crumb = calls.crumbs[0];
  assert(crumb.category === "student.step" && crumb.level === "warning", JSON.stringify(crumb));
  assert(crumb.message === "Step refused: POST /api/student/live-items/:id/rank-turn 409", crumb.message);
  assert(!JSON.stringify(crumb).includes("Quillon"), "query leaked");
  assert(calls.messages.length === 0, "4xx must not raise an event");
  // GETs, board ops, presence and staff paths add nothing.
  await sandbox.fetch("/api/student/state");
  await sandbox.fetch("/api/student/board/main/ops", { method: "POST" });
  await sandbox.fetch("/api/student/canvas-presence", { method: "POST" });
  await sandbox.fetch("/api/live-sessions/1/items/2/publish", { method: "POST" });
  assert(calls.crumbs.length === 1, "extra crumbs " + calls.crumbs.length);
  console.log("ok");
})().catch((err) => { console.error(err.message); process.exit(1); });
"""
        completed = subprocess.run(
            ["node", "-e", script, str(LMS_DIR / "static" / "sentry-live.js")],
            check=False, capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertIn("ok", completed.stdout)


if __name__ == "__main__":
    unittest.main()

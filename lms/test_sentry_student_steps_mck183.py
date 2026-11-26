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


FRAMES_SCRIPT = r"""const fs = require("fs");
const vm = require("vm");
const src = fs.readFileSync(process.argv[1], "utf8");
function meta(content) { return { content, getAttribute() { return this.content; } }; }
const metas = {
  'meta[name="lloves-sentry-dsn"]': meta("https://public@o0.ingest.sentry.io/1"),
  'meta[name="lloves-sentry-environment"]': meta("tip"),
  'meta[name="lloves-sentry-tags"]': meta('{"portal":"student"}'),
};
const sandbox = {
  console,
  location: { origin: "https://alc.mckenzian.com" },
  document: { querySelector(sel) { return metas[sel] || null; } },
  fetch: function () { return Promise.resolve({ status: 200, ok: true }); },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.Sentry = {
  init(opts) { sandbox.__opts = opts; },
  browserTracingIntegration() { return { name: "BrowserTracing" }; },
  setUser() {}, setTags() {}, captureMessage() {}, captureException() {}, addBreadcrumb() {},
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const TOK = "VISITtok123secret";
const frame = (n) => ({
  filename: `https://alc.mckenzian.com/static/student_${n}.js?v=${TOK}`,
  abs_path: `https://alc.mckenzian.com/student/home?v=${TOK}#x`,
  function: "f" + n,
});
const event = {
  message: `Load failed https://alc.mckenzian.com/student/waiting?v=${TOK}`,
  exception: { values: [
    { type: "TypeError", value: `bad at /static/a.js?v=${TOK}:1:2`, stacktrace: { frames: [frame(1), frame(2)] } },
    { type: "Error", value: "x", stacktrace: { frames: [frame(3)] } },
  ] },
  threads: { values: [{ stacktrace: { frames: [frame(4)] } }] },
  request: { url: `https://alc.mckenzian.com/student/s/SEAT${TOK}?v=${TOK}` },
  breadcrumbs: [
    { category: "navigation", data: { from: `/student/waiting?v=${TOK}`, to: `/student/s/SEAT${TOK}` } },
    { category: "sentry.event", message: `Error at https://alc.mckenzian.com/static/b.js?v=${TOK}` },
    { category: "xhr", data: { url: `/api/student/x?v=${TOK}`, method: "POST", status_code: 500 } },
  ],
};
const out = sandbox.__opts.beforeSend(event);
const text = JSON.stringify(out);
if (text.includes(TOK)) { console.error("token leaked: " + text); process.exit(1); }
if (out.exception.values[0].stacktrace.frames[0].filename !== "https://alc.mckenzian.com/static/student_1.js") {
  console.error("frame filename: " + out.exception.values[0].stacktrace.frames[0].filename); process.exit(1);
}
if (out.exception.values[0].stacktrace.frames[1].abs_path !== "https://alc.mckenzian.com/student/home") {
  console.error("abs_path: " + out.exception.values[0].stacktrace.frames[1].abs_path); process.exit(1);
}
if (!text.includes("/student/s/[token]")) { console.error("seat path not masked"); process.exit(1); }
console.log("ok");
"""


class StackFrameScrubTests(unittest.TestCase):
    """#269 gate MED: no visit token in frame filename/abs_path, values or crumbs."""

    def test_frames_values_and_breadcrumbs_lose_the_token(self) -> None:
        completed = subprocess.run(
            ["node", "-e", FRAMES_SCRIPT, str(LMS_DIR / "static" / "sentry-live.js")],
            check=False, capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertIn("ok", completed.stdout)

    def test_server_masks_student_seat_path(self) -> None:
        self.assertEqual(
            sentry_wire._strip_url_query("https://alc.mckenzian.com/student/s/AbC123?v=x"),
            "https://alc.mckenzian.com/student/s/[token]",
        )
        self.assertEqual(sentry_wire._strip_url_query("/invite/tok/switch"), "/invite/[token]/switch")


# 32-char urlsafe stand-in; not a real catch-up token.
_FAKE_CATCHUP_TOKEN = "abcdefghijklmnopqrstuv012345_-xy"

CATCHUP_MASK_SCRIPT = r"""
const fs = require("fs");
const vm = require("vm");
const src = fs.readFileSync(process.argv[1], "utf8");
function meta(content) { return { content, getAttribute() { return this.content; } }; }
const metas = {
  'meta[name="lloves-sentry-dsn"]': meta("https://public@o0.ingest.sentry.io/1"),
  'meta[name="lloves-sentry-environment"]': meta("tip"),
  'meta[name="lloves-sentry-tags"]': meta('{"portal":"student"}'),
};
const sandbox = {
  console,
  location: { origin: "https://alc.mckenzian.com" },
  document: { querySelector(sel) { return metas[sel] || null; } },
  fetch: function () { return Promise.resolve({ status: 200, ok: true }); },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.Sentry = {
  init(opts) { sandbox.__opts = opts; },
  browserTracingIntegration() { return { name: "BrowserTracing" }; },
  setUser() {}, setTags() {}, captureMessage() {}, captureException() {}, addBreadcrumb() {},
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const opts = sandbox.__opts;
const TOK = "abcdefghijklmnopqrstuv012345_-xy";
const shapes = [
  [
    `https://alc.mckenzian.com/catchup/MCR3U/m3-c2/${TOK}`,
    "https://alc.mckenzian.com/catchup/MCR3U/m3-c2/[token]",
  ],
  [
    `https://alc.mckenzian.com/catchup/${TOK}`,
    "https://alc.mckenzian.com/catchup/[token]",
  ],
  [
    `https://alc.mckenzian.com/api/catchup/${TOK}/questions/1/board`,
    "https://alc.mckenzian.com/api/catchup/[token]/questions/1/board",
  ],
  [
    `https://alc.mckenzian.com/api/classes/12/catchup-links/${TOK}`,
    "https://alc.mckenzian.com/api/classes/12/catchup-links/[token]",
  ],
];
const fixed = [
  "https://alc.mckenzian.com/student/catchup/landing",
  "/student/catchup/landing",
  "/student/catchup/sign-out",
  "/api/student/catchup-pin/later",
  "https://alc.mckenzian.com/api/student/catchup-pin/verify",
  "/api/classes/12/catchup-links",
];
function fail(msg) { console.error(msg); process.exit(1); }
function clone(value) { return JSON.parse(JSON.stringify(value)); }
for (const [raw, masked] of shapes) {
  const event = {
    message: `failed at ${raw}`,
    transaction: raw,
    request: { url: `${raw}?v=visit`, headers: { Referer: raw } },
    breadcrumbs: [
      { category: "navigation", data: { from: raw, to: `${raw}?play=1` } },
      { category: "fetch", data: { url: raw, method: "GET", status_code: 500 } },
      { category: "xhr", data: { url: raw, method: "GET", status_code: 500 } },
    ],
  };
  const out = opts.beforeSend(clone(event));
  const text = JSON.stringify(out);
  if (text.includes(TOK)) fail(`token leaked in beforeSend: ${text}`);
  if (out.request.url !== masked) fail(`request.url: ${out.request.url}`);
  if (out.request.headers.Referer !== masked) fail(`Referer: ${out.request.headers.Referer}`);
  if (out.transaction !== masked) fail(`transaction: ${out.transaction}`);
  if (out.message !== `failed at ${masked}`) fail(`message: ${out.message}`);
  if (out.breadcrumbs[0].data.from !== masked) fail(`nav from: ${out.breadcrumbs[0].data.from}`);
  if (out.breadcrumbs[0].data.to !== masked) fail(`nav to: ${out.breadcrumbs[0].data.to}`);
  if (out.breadcrumbs[1].data.url !== masked) fail(`fetch url: ${out.breadcrumbs[1].data.url}`);
  if (out.breadcrumbs[2].data.url !== masked) fail(`xhr url: ${out.breadcrumbs[2].data.url}`);

  const span = opts.beforeSendSpan({
    description: `GET ${raw}`,
    name: raw,
    data: { "http.url": raw, "url.full": raw, "url.path": raw, "http.target": raw },
  });
  const spanText = JSON.stringify(span);
  if (spanText.includes(TOK)) fail(`token leaked in beforeSendSpan: ${spanText}`);
  if (span.description !== `GET ${masked}`) fail(`span description: ${span.description}`);
  if (span.name !== masked) fail(`span name: ${span.name}`);
  if (span.data["http.url"] !== masked) fail(`span http.url: ${span.data["http.url"]}`);
  if (span.data["url.full"] !== masked) fail(`span url.full: ${span.data["url.full"]}`);

  const nav = opts.beforeBreadcrumb({ category: "navigation", data: { from: raw, to: raw } });
  if (nav.data.from !== masked) fail(`beforeBreadcrumb nav from: ${nav.data.from}`);
  if (nav.data.to !== masked) fail(`beforeBreadcrumb nav to: ${nav.data.to}`);
  const fetchCrumb = opts.beforeBreadcrumb({ category: "fetch", data: { url: raw } });
  if (fetchCrumb.data.url !== masked) fail(`beforeBreadcrumb fetch url: ${fetchCrumb.data.url}`);
}
const trailing = ["/", ".", ":", ","];
for (const mark of trailing) {
  const raw = `https://alc.mckenzian.com/catchup/${TOK}${mark}`;
  const masked = `https://alc.mckenzian.com/catchup/[token]${mark}`;
  const out = opts.beforeSend(clone({
    message: `open ${raw}`,
    request: { url: raw },
    breadcrumbs: [{ category: "fetch", data: { url: raw } }],
  }));
  if (JSON.stringify(out).includes(TOK)) fail(`legacy trailing ${mark} leaked: ${JSON.stringify(out)}`);
  if (out.request.url !== masked) fail(`legacy trailing ${mark} url: ${out.request.url}`);
  if (out.message !== `open ${masked}`) fail(`legacy trailing ${mark} message: ${out.message}`);
  if (out.breadcrumbs[0].data.url !== masked) fail(`legacy trailing ${mark} fetch: ${out.breadcrumbs[0].data.url}`);
  const span = opts.beforeSendSpan({ description: `GET ${raw}`, data: { "http.url": raw } });
  if (span.description !== `GET ${masked}`) fail(`legacy trailing ${mark} span: ${span.description}`);
}
for (const path of fixed) {
  const event = {
    message: path,
    transaction: path,
    request: { url: path, headers: { Referer: path } },
    breadcrumbs: [
      { category: "navigation", data: { from: path, to: path } },
      { category: "fetch", data: { url: path } },
    ],
  };
  const out = opts.beforeSend(clone(event));
  if (out.request.url !== path) fail(`fixed request.url changed: ${out.request.url}`);
  if (out.request.headers.Referer !== path) fail(`fixed Referer changed: ${out.request.headers.Referer}`);
  if (out.transaction !== path) fail(`fixed transaction changed: ${out.transaction}`);
  if (out.message !== path) fail(`fixed message changed: ${out.message}`);
  if (out.breadcrumbs[0].data.from !== path) fail(`fixed nav from changed: ${out.breadcrumbs[0].data.from}`);
  if (out.breadcrumbs[0].data.to !== path) fail(`fixed nav to changed: ${out.breadcrumbs[0].data.to}`);
  if (out.breadcrumbs[1].data.url !== path) fail(`fixed fetch url changed: ${out.breadcrumbs[1].data.url}`);
  const span = opts.beforeSendSpan({ description: `GET ${path}`, data: { "http.url": path } });
  if (span.description !== `GET ${path}`) fail(`fixed span description changed: ${span.description}`);
  if (span.data["http.url"] !== path) fail(`fixed span http.url changed: ${span.data["http.url"]}`);
}
console.log("ok");
"""


class CatchupPathTokenMaskTests(unittest.TestCase):
    """MCK-530: catch-up path tokens are masked in every Sentry URL field."""

    TOKEN = _FAKE_CATCHUP_TOKEN
    SHAPES = (
        (
            f"https://alc.mckenzian.com/catchup/MCR3U/m3-c2/{_FAKE_CATCHUP_TOKEN}",
            "https://alc.mckenzian.com/catchup/MCR3U/m3-c2/[token]",
        ),
        (
            f"https://alc.mckenzian.com/catchup/{_FAKE_CATCHUP_TOKEN}",
            "https://alc.mckenzian.com/catchup/[token]",
        ),
        (
            f"https://alc.mckenzian.com/api/catchup/{_FAKE_CATCHUP_TOKEN}/questions/1/board",
            "https://alc.mckenzian.com/api/catchup/[token]/questions/1/board",
        ),
        (
            f"https://alc.mckenzian.com/api/classes/12/catchup-links/{_FAKE_CATCHUP_TOKEN}",
            "https://alc.mckenzian.com/api/classes/12/catchup-links/[token]",
        ),
    )
    FIXED = (
        "https://alc.mckenzian.com/student/catchup/landing",
        "/student/catchup/landing",
        "/student/catchup/sign-out",
        "/api/student/catchup-pin/later",
        "https://alc.mckenzian.com/api/student/catchup-pin/verify",
        "/api/classes/12/catchup-links",
    )

    def test_request_url(self) -> None:
        for raw, masked in self.SHAPES:
            event = {"request": {"url": raw + "?v=visit"}}
            out = sentry_wire.before_send(event, {})
            self.assertEqual(out["request"]["url"], masked)
            self.assertNotIn(self.TOKEN, out["request"]["url"])

    def test_http_url_and_url_full(self) -> None:
        for raw, masked in self.SHAPES:
            event = {
                "transaction": "catchup_page_canonical",
                "contexts": {"trace": {"data": {"http.url": raw, "url.full": raw}}},
                "spans": [{"data": {"http.url": raw, "url.full": raw, "url.path": raw, "http.target": raw}}],
            }
            out = sentry_wire.before_send_transaction(event, {})
            self.assertEqual(out["contexts"]["trace"]["data"]["http.url"], masked)
            self.assertEqual(out["contexts"]["trace"]["data"]["url.full"], masked)
            self.assertEqual(out["spans"][0]["data"]["http.url"], masked)
            self.assertEqual(out["spans"][0]["data"]["url.full"], masked)
            self.assertEqual(out["spans"][0]["data"]["url.path"], masked)
            self.assertEqual(out["spans"][0]["data"]["http.target"], masked)

    def test_transaction_name(self) -> None:
        for raw, masked in self.SHAPES:
            event = {"transaction": raw, "request": {"url": "https://alc.mckenzian.com/"}}
            out = sentry_wire.before_send(event, {})
            self.assertEqual(out["transaction"], masked)
            tx = sentry_wire.before_send_transaction({"transaction": raw}, {})
            self.assertEqual(tx["transaction"], masked)

    def test_span_description(self) -> None:
        for raw, masked in self.SHAPES:
            event = {
                "transaction": "catchup_page_canonical",
                "spans": [{"description": f"GET {raw}", "name": raw}],
            }
            out = sentry_wire.before_send_transaction(event, {})
            self.assertEqual(out["spans"][0]["description"], f"GET {masked}")
            # Span ``name`` is a dropped identity key, so it is removed
            # before _strip_span_queries runs. Description is the surface
            # that still carries a path on the transaction payload.

    def test_referer(self) -> None:
        for raw, masked in self.SHAPES:
            event = {
                "request": {
                    "url": "https://alc.mckenzian.com/student/home",
                    "headers": {"Referer": raw + "?play=1"},
                }
            }
            out = sentry_wire.before_send(event, {})
            self.assertEqual(out["request"]["headers"]["Referer"], masked)
            self.assertNotIn(self.TOKEN, out["request"]["headers"]["Referer"])

    def test_nav_breadcrumb_from_to(self) -> None:
        for raw, masked in self.SHAPES:
            crumb = sentry_wire.before_breadcrumb(
                {"category": "navigation", "data": {"from": raw, "to": raw}},
            )
            self.assertEqual(crumb["data"]["from"], masked)
            self.assertEqual(crumb["data"]["to"], masked)

    def test_fetch_breadcrumb_url(self) -> None:
        for raw, masked in self.SHAPES:
            crumb = sentry_wire.before_breadcrumb(
                {"category": "fetch", "data": {"url": raw}},
            )
            self.assertEqual(crumb["data"]["url"], masked)

    def test_message_text(self) -> None:
        for raw, masked in self.SHAPES:
            event = {"message": f"failed at {raw}"}
            out = sentry_wire.before_send(event, {})
            self.assertEqual(out["message"], f"failed at {masked}")
            self.assertNotIn(self.TOKEN, out["message"])
            crumb = sentry_wire.before_breadcrumb({"message": f"open {raw}"})
            self.assertEqual(crumb["message"], f"open {masked}")

    def test_fixed_catchup_routes_unchanged(self) -> None:
        for path in self.FIXED:
            self.assertEqual(sentry_wire._strip_url_query(path), path)
            self.assertEqual(sentry_wire.mask_path_tokens(path), path)
            self.assertEqual(sentry_wire._strip_url_query(path + "?x=1"), path)
            event = {
                "transaction": path,
                "message": path,
                "request": {"url": path, "headers": {"Referer": path}},
                "spans": [{"description": f"GET {path}", "data": {"http.url": path}}],
            }
            sent = sentry_wire.before_send(
                {"transaction": path, "message": path, "request": {"url": path, "headers": {"Referer": path}}},
                {},
            )
            self.assertEqual(sent["transaction"], path)
            self.assertEqual(sent["message"], path)
            self.assertEqual(sent["request"]["url"], path)
            self.assertEqual(sent["request"]["headers"]["Referer"], path)
            tx = sentry_wire.before_send_transaction(event, {})
            self.assertEqual(tx["spans"][0]["description"], f"GET {path}")
            self.assertEqual(tx["spans"][0]["data"]["http.url"], path)
            crumb = sentry_wire.before_breadcrumb(
                {"category": "navigation", "data": {"from": path, "to": path}},
            )
            self.assertEqual(crumb["data"]["from"], path)
            self.assertEqual(crumb["data"]["to"], path)

    def test_catchup_links_teacher_route(self) -> None:
        raw = f"https://alc.mckenzian.com/api/classes/12/catchup-links/{self.TOKEN}"
        masked = "https://alc.mckenzian.com/api/classes/12/catchup-links/[token]"
        self.assertEqual(sentry_wire._strip_url_query(raw), masked)
        event = {"request": {"url": raw + "?x=1"}}
        out = sentry_wire.before_send(event, {})
        self.assertEqual(out["request"]["url"], masked)
        crumb = sentry_wire.before_breadcrumb(
            {"category": "fetch", "data": {"url": raw}},
        )
        self.assertEqual(crumb["data"]["url"], masked)
        xhr = sentry_wire.before_breadcrumb(
            {"category": "xhr", "data": {"url": raw}},
        )
        self.assertEqual(xhr["data"]["url"], masked)
        tx = sentry_wire.before_send_transaction(
            {"transaction": "staff_catchup_links", "spans": [{"description": f"POST {raw}"}]},
            {},
        )
        self.assertEqual(tx["spans"][0]["description"], f"POST {masked}")

    def test_legacy_catchup_token_boundary(self) -> None:
        for mark in ("/", ".", ":", ","):
            raw = f"https://alc.mckenzian.com/catchup/{self.TOKEN}{mark}"
            masked = f"https://alc.mckenzian.com/catchup/[token]{mark}"
            self.assertEqual(sentry_wire._strip_url_query(raw), masked, mark)
            self.assertEqual(sentry_wire.mask_path_tokens(f"open {raw}"), f"open {masked}", mark)
            event = {"message": f"open {raw}", "request": {"url": raw}}
            out = sentry_wire.before_send(event, {})
            self.assertEqual(out["request"]["url"], masked, mark)
            self.assertEqual(out["message"], f"open {masked}", mark)
            crumb = sentry_wire.before_breadcrumb(
                {"category": "fetch", "data": {"url": raw}},
            )
            self.assertEqual(crumb["data"]["url"], masked, mark)
            tx = sentry_wire.before_send_transaction(
                {"spans": [{"description": f"GET {raw}"}]},
                {},
            )
            self.assertEqual(tx["spans"][0]["description"], f"GET {masked}", mark)

    def test_browser_masks_catchup_paths(self) -> None:
        completed = subprocess.run(
            ["node", "-e", CATCHUP_MASK_SCRIPT, str(LMS_DIR / "static" / "sentry-live.js")],
            check=False, capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertIn("ok", completed.stdout)




if __name__ == "__main__":
    unittest.main()

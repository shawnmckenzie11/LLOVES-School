"""Teacher reconnect strip: one Sentry payload, allowlisted keys only.

The yellow bar on /staff/class/<id> is a client episode. The object sent
to Sentry may contain cause, duration_ms, timestamp, and class_id. A URL,
query, header, body, name, email, PIN, or join code must not be copied
in, even when the fetch error text contains them.
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
NODE = shutil.which("node")

PAYLOAD_KEYS = ("cause", "duration_ms", "timestamp", "class_id")


@unittest.skipUnless(NODE, "node is required")
class StaffReconnectPayloadTests(unittest.TestCase):
    """staff_reconnect_log.js builds and times the episode."""

    def test_payload_keys_and_episode_rules(self) -> None:
        """The logged object has only the four allowed keys."""
        script = r"""
import { pathToFileURL } from "url";
const mod = await import(pathToFileURL(process.argv[1]).href);
const {
  classifyStaffReconnectCause,
  createStaffReconnectLog,
  staffReconnectPayload,
  STAFF_RECONNECT_CAUSE_SOURCE,
  STAFF_RECONNECT_KEYS,
  STAFF_RECONNECT_LOG_AFTER_MS,
} = mod;

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

assert(JSON.stringify(STAFF_RECONNECT_KEYS) === JSON.stringify(["cause","duration_ms","timestamp","class_id"]), "key order");
assert(STAFF_RECONNECT_LOG_AFTER_MS > 15000, "log-after must sit past the 15s abort");

const leaked = staffReconnectPayload({
  cause: "http_503",
  durationMs: 1200,
  timestamp: 1700000000000,
  classId: 9,
  url: "https://alc.example/api/live-sessions/9/state?pin=1234&join=AB12CD",
  pin: "1234",
  email: "student@example.com",
  name: "Maple Student",
  headers: { Authorization: "seat-token", Cookie: "pin=1234" },
  body: "{\"pin\":\"1234\",\"join_code\":\"AB12CD\"}",
  query: "pin=1234",
});
const leakedText = JSON.stringify(leaked);
assert(JSON.stringify(Object.keys(leaked)) === JSON.stringify(["cause","duration_ms","timestamp","class_id"]), leakedText);
assert(leaked.cause === "http_503", leaked.cause);
assert(leaked.duration_ms === 1200, "duration");
assert(leaked.timestamp === 1700000000000, "timestamp");
assert(leaked.class_id === 9, "class");
for (const secret of ["https://", "pin", "1234", "@", "Maple", "AB12", "Authorization", "Cookie", "join_code", "?"]) {
  assert(!leakedText.includes(secret), "leaked " + secret + " in " + leakedText);
}

assert(staffReconnectPayload({ cause: "https://x?pin=1234", durationMs: 1, timestamp: 2, classId: 3 }) === null, "bad cause sent");
assert(staffReconnectPayload({ cause: "fetch_timeout", durationMs: 1, timestamp: 2, classId: 0 }) === null, "class 0 sent");
assert(staffReconnectPayload({ cause: "not a cause", durationMs: 1, timestamp: 2, classId: 3 }) === null, "free text cause");

const nasty = new Error("HTTP 503 https://alc.example/api/live-sessions/9/state?pin=1234&code=AB12CD");
assert(classifyStaffReconnectCause(nasty, {}) === "http_503", "status");
assert(classifyStaffReconnectCause(new TypeError("Failed to fetch"), {}) === "network_error", "network");
assert(classifyStaffReconnectCause(Object.assign(new Error("The operation was aborted."), { name: "AbortError" }), { timeout: true }) === "fetch_timeout", "timeout");
assert(classifyStaffReconnectCause(Object.assign(new Error("The operation was aborted."), { name: "AbortError" }), { timeout: true, visibilityResume: true }) === "visibility_resume", "wake");
assert(classifyStaffReconnectCause(null, { slowPoll: true, streamClosed: true }) === "stream_closed", "stream");
assert(classifyStaffReconnectCause(null, { slowPoll: true }) === "slow_poll", "slow");
assert(classifyStaffReconnectCause(new Error("state unavailable"), { busy: true }) === "busy", "busy");
assert(classifyStaffReconnectCause(Object.assign(new Error("aborted"), { name: "AbortError" }), {}) === "unknown", "retry abort");
const classified = classifyStaffReconnectCause(nasty, {});
assert(!String(classified).includes("pin") && !String(classified).includes("http://") && !String(classified).includes("https"), classified);

let clock = 5000;
const sent = [];
const timers = [];
const cancelled = [];
const log = createStaffReconnectLog({
  log(payload) { sent.push(payload); },
  now() { return clock; },
  after(ms, fn) { timers.push({ ms, fn }); return timers.length; },
  cancel(id) { cancelled.push(id); },
});
log.shown("slow_poll", 9);
log.shown("http_503", 9);
clock = 5400;
log.hidden();
assert(sent.length === 1, "clear logged " + sent.length);
assert(sent[0].cause === "http_503", "upgraded " + sent[0].cause);
assert(sent[0].duration_ms === 400, "duration " + sent[0].duration_ms);
assert(sent[0].class_id === 9 && sent[0].timestamp === 5400, JSON.stringify(sent[0]));
assert(cancelled.length === 1, "timer cancelled on clear");

sent.length = 0;
cancelled.length = 0;
timers.length = 0;
const openAt = clock;
log.shown("http_503", 9);
log.shown("slow_poll", 9);
clock = openAt + 16000;
timers[0].fn();
assert(sent.length === 1 && sent[0].cause === "http_503", "timer kept specific cause " + JSON.stringify(sent));
assert(sent[0].duration_ms === 16000, "timer duration " + sent[0].duration_ms);
log.hidden();
assert(sent.length === 1, "clear after timer logged again");

sent.length = 0;
log.shown("slow_poll", 0);
log.hidden();
assert(sent.length === 0, "missing class id logged");

log.shown("fetch_timeout", 4);
clock += 100;
log.hidden();
log.shown("network_error", 4);
clock += 50;
log.hidden();
assert(sent.length === 2, "two episodes " + sent.length);
assert(sent[0].cause === "fetch_timeout" && sent[1].cause === "network_error", JSON.stringify(sent));
assert(new RegExp(STAFF_RECONNECT_CAUSE_SOURCE).test("http_503"), "shared pattern");
assert(!new RegExp(STAFF_RECONNECT_CAUSE_SOURCE).test("https://x"), "pattern rejects a url");
console.log("ok");
"""
        completed = subprocess.run(
            [NODE, "--input-type=module", "-e", script, str(LMS_DIR / "static" / "staff_reconnect_log.js")],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertIn("ok", completed.stdout)

    def test_sentry_sender_keeps_only_allowed_keys(self) -> None:
        """llovesSentryStaffReconnect copies the four keys and survives the poll filter."""
        script = r"""
import fs from "fs";
import vm from "vm";
import { pathToFileURL } from "url";
const causeMod = await import(pathToFileURL(process.argv[2]).href);
const src = fs.readFileSync(process.argv[1], "utf8");
function meta(content) { return { content, getAttribute() { return this.content; } }; }
const metas = {
  'meta[name="lloves-sentry-dsn"]': meta("https://public@o0.ingest.sentry.io/1"),
  'meta[name="lloves-sentry-environment"]': meta("tip"),
  'meta[name="lloves-sentry-user"]': meta("7"),
  'meta[name="lloves-sentry-tags"]': meta('{"portal":"staff","class_id":"9","course_code":"MCF3M"}'),
};
const calls = { messages: [] };
const sandbox = {
  console,
  location: { origin: "https://alc.mckenzian.com" },
  document: { querySelector(sel) { return metas[sel] || null; } },
  fetch: function () { return Promise.resolve({ status: 200, ok: true }); },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.LLOVES_STAFF_RECONNECT_CAUSE_SOURCE = causeMod.STAFF_RECONNECT_CAUSE_SOURCE;
sandbox.Sentry = {
  init(opts) { sandbox.__opts = opts; },
  browserTracingIntegration() { return { name: "BrowserTracing" }; },
  setUser() {},
  setTags() {},
  captureMessage(message, ctx) { calls.messages.push([message, ctx]); },
  captureException() {},
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const opts = sandbox.__opts;
function assert(cond, msg) { if (!cond) throw new Error(msg); }

sandbox.llovesSentryStaffReconnect({
  cause: "http_503",
  duration_ms: 1800,
  timestamp: 1700000000000,
  class_id: 9,
  url: "https://alc.example/staff/class/9?pin=1234",
  pin: "1234",
  email: "student@example.com",
  name: "Maple Student",
  headers: { Cookie: "join=AB12CD" },
  body: "pin=1234",
});
assert(calls.messages.length === 1, "not sent");
const [message, ctx] = calls.messages[0];
assert(message === "Teacher live strip", message);
const extra = ctx.extra;
assert(JSON.stringify(Object.keys(extra)) === JSON.stringify(["cause","duration_ms","timestamp","class_id"]), JSON.stringify(extra));
const blob = JSON.stringify(ctx);
assert(!blob.includes("https://alc.example"), blob);
assert(!blob.includes("pin"), blob);
assert(!blob.includes("1234"), blob);
assert(!blob.includes("@"), blob);
assert(!blob.includes("Maple"), blob);
assert(!blob.includes("AB12"), blob);
assert(!blob.includes("Cookie"), blob);
assert(ctx.tags["lloves.staff_reconnect"] === "1", "tag");

sandbox.llovesSentryStaffReconnect({
  cause: "https://evil.example/state?pin=9999",
  duration_ms: 1,
  timestamp: 2,
  class_id: 9,
});
assert(calls.messages.length === 1, "bad cause sent");

const kept = opts.beforeSend({
  message: "Reconnecting… HTTP 503",
  tags: { "lloves.staff_reconnect": "1" },
  extra: { cause: "http_503", duration_ms: 1800, timestamp: 1700000000000, class_id: 9 },
  breadcrumbs: [{ category: "fetch", data: { url: "/api/live-sessions/9/state?pin=1234", status_code: 503 } }],
});
assert(kept, "staff reconnect dropped");
const keptText = JSON.stringify(kept);
assert(!keptText.includes("1234"), keptText);
assert(!keptText.includes("?pin"), keptText);
assert(kept.extra.cause === "http_503" && kept.extra.class_id === 9, keptText);

const dropped = opts.beforeSend({
  message: "Reconnecting… HTTP 503",
  breadcrumbs: [{ category: "fetch", data: { url: "/api/live-sessions/9/state", status_code: 503 } }],
});
assert(dropped === null, "untagged reconnect kept");
console.log("ok");
"""
        completed = subprocess.run(
            [
                NODE,
                "--input-type=module",
                "-e",
                script,
                str(LMS_DIR / "static" / "sentry-live.js"),
                str(LMS_DIR / "static" / "staff_reconnect_log.js"),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertIn("ok", completed.stdout)

    def test_teacher_page_wires_the_episode(self) -> None:
        """staff_ap.js reports the yellow strip and does not log the fetch URL."""
        staff = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn('from "/static/staff_reconnect_log.js"', staff)
        self.assertIn("createStaffReconnectLog", staff)
        self.assertIn("classifyStaffReconnectCause", staff)
        self.assertIn("llovesSentryStaffReconnect", staff)
        self.assertIn('staffPollAbortReason = "timeout"', staff)
        self.assertIn('staffPollAbortReason = "retry"', staff)
        self.assertIn("visibilitychange", staff)
        self.assertIn("staffStreamClosedHint", staff)
        self.assertNotIn("fetch_timeout|network_error", (LMS_DIR / "static" / "sentry-live.js").read_text(encoding="utf-8"))
        paint = staff.split("function paintStaffReconnect(")[1].split("function clearReconnectFeelTimers(")[0]
        self.assertIn("staffReconnectLog.shown", paint)
        self.assertIn("staffReconnectLog.hidden", paint)
        self.assertNotIn("err.message", paint)
        self.assertNotIn("http://", paint)
        self.assertNotIn("pin", paint.lower())


if __name__ == "__main__":
    unittest.main()

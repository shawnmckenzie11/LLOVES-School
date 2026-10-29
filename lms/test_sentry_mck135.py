"""MCK-135: WARNING logs, secret scrubbing, swallowed errors, new-issue alert."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

import sentry_sdk  # noqa: E402
from sentry_sdk.transport import Transport  # noqa: E402

from app import _json_error, create_app  # noqa: E402
from sentry_wire import (  # noqa: E402
    EXPECTED_MONTHLY_LOG_LINES,
    before_breadcrumb,
    before_send,
    before_send_log,
    note_busy_shed,
    report_swallowed,
    reset_busy_shed_counts,
    sentry_init_options,
)

# Obvious fakes. Not a roster codename, a course PIN, or a real address.
FAKE_NAME = "Zorblax"
FAKE_LAST = "Quuxley"
FAKE_EMAIL = "zorblax.quuxley@example.test"
FAKE_PIN = "9876"
FAKE_JOIN = "Q7Z9K"
FAKE_CATCH = "W4N8P2"
ONLY_COOKIE = "onlycookieSECRET"
ONLY_QUERY = "onlyquerySECRET"
ONLY_HEADER = "onlyheaderSECRET"
ONLY_LOCAL = "onlylocalSECRET"
ONLY_REFER = "onlyreferSECRET"
_BANNED = (
    FAKE_NAME,
    FAKE_LAST,
    FAKE_NAME.lower(),
    FAKE_LAST.lower(),
    f"{FAKE_NAME} {FAKE_LAST}",
    FAKE_EMAIL,
    FAKE_PIN,
    FAKE_JOIN,
    FAKE_CATCH,
)

JOIN = "R3K1X"
CATCHUP = "F2K14"
PIN = "4321"
EMAIL = "maple.student@school.ca"
FIRST = "Maple"
LAST = "Chen"


class CaptureTransport(Transport):
    """Keep envelopes in memory."""

    def __init__(self, options=None) -> None:
        """Start empty."""
        super().__init__(options)
        self.envelopes: list = []

    def capture_envelope(self, envelope) -> None:
        """Store one envelope."""
        self.envelopes.append(envelope)

    def flush(self, timeout, callback=None) -> None:
        """Nothing is on the network."""
        return None

    def kill(self) -> None:
        """Nothing to stop."""
        return None


def _blob(transport: CaptureTransport) -> str:
    """Every envelope as text."""
    chunks = []
    for envelope in transport.envelopes:
        for item in envelope.items:
            chunks.append(item.payload.get_bytes().decode("utf-8", "ignore"))
    return "\n".join(chunks)


class ScrubTests(unittest.TestCase):
    """before_send never ships join codes, catch-up codes, PINs, names, or emails."""

    def test_structured_secrets_and_labeled_text_are_removed(self) -> None:
        """Fields and labeled text lose secrets. Course codes and error slugs stay."""
        event = {
            "message": (
                f"course MCF3M student {FIRST} {LAST} PIN {PIN} "
                f"join code {JOIN} catch-up code {CATCHUP} email {EMAIL} "
                f"name={FIRST}"
            ),
            "extra": {
                "course_code": "MCF3M",
                "first_name": FIRST,
                "last_name": LAST,
                "pin": PIN,
                "session_code": JOIN,
                "portal_join_code": CATCHUP,
                "email": EMAIL,
                "code": "pin_not_set",
            },
            "exception": {
                "values": [
                    {
                        "type": "RuntimeError",
                        "value": f"student {FIRST} PIN {PIN} join code {JOIN} {EMAIL}",
                    }
                ]
            },
            "user": {"id": "2", "email": EMAIL, "username": f"{FIRST} {LAST}"},
            "breadcrumbs": {
                "values": [{"message": f"typed join code {JOIN} pin={PIN}"}]
            },
            "request": {"url": "https://alc.example/staff", "data": {"name": FIRST}},
        }
        event["contexts"] = {"code": JOIN}
        sent = before_send(event, None)
        self.assertIs(sent, event)
        blob = json.dumps(sent)
        for secret in (JOIN, CATCHUP, PIN, EMAIL, FIRST, LAST, f"{FIRST} {LAST}"):
            self.assertNotIn(secret, blob, secret)
        self.assertIn("MCF3M", blob)
        self.assertIn("pin_not_set", blob)
        self.assertIn("[redacted]", blob)
        self.assertEqual(sent["user"], {"id": "2"})

    def test_warning_log_scrubs_parameters(self) -> None:
        """A WARNING log ships, without the PIN or the join code argument."""
        log = {
            "severity_text": "warn",
            "severity_number": 13,
            "body": f"student {FIRST} PIN {PIN} join code {JOIN}",
            "attributes": {
                "sentry.message.template": "student %s PIN %s join code %s",
                "sentry.message.parameter.0": FIRST,
                "sentry.message.parameter.1": PIN,
                "sentry.message.parameter.2": JOIN,
                "course_code": "MCF3M",
            },
        }
        sent = before_send_log(log)
        self.assertIsNotNone(sent)
        blob = json.dumps(sent)
        for secret in (FIRST, PIN, JOIN):
            self.assertNotIn(secret, blob, secret)
        self.assertIn("MCF3M", blob)
        self.assertIn("[redacted]", blob)

    def test_info_log_is_dropped(self) -> None:
        """Ordinary INFO, including a poll line, is not a Sentry log."""
        self.assertIsNone(
            before_send_log(
                {
                    "severity_text": "info",
                    "severity_number": 9,
                    "body": "live /state duration_ms=4 payload_bytes=1200",
                    "attributes": {},
                }
            )
        )


class SwallowedAndShedTests(unittest.TestCase):
    """Silent faults become issues. Busy sheds are one WARNING a minute."""

    def setUp(self) -> None:
        """In-memory Sentry client."""
        self.transport = CaptureTransport()
        options = sentry_init_options("https://public@o0.ingest.sentry.io/1")
        options["traces_sampler"] = lambda _ctx: 0.0
        sentry_sdk.init(transport=self.transport, **options)

    def tearDown(self) -> None:
        """Drop the client so later tests are uninstrumented."""
        sentry_sdk.get_client().close()
        sentry_sdk.init()

    def test_report_swallowed_sends_an_issue_without_the_secret(self) -> None:
        """capture_exception runs, and the message is scrubbed first."""
        try:
            raise RuntimeError(f"student {FIRST} PIN {PIN} join code {JOIN} {EMAIL}")
        except RuntimeError:
            report_swallowed("unit")
        sentry_sdk.flush(timeout=2)
        blob = _blob(self.transport)
        self.assertIn("swallowed", blob)
        for secret in (FIRST, PIN, JOIN, EMAIL):
            self.assertNotIn(secret, blob, secret)

    def test_json_500_reports_and_value_error_does_not(self) -> None:
        """Unexpected JSON failures are issues. ValueError stays a 400."""
        app = create_app(testing=True)
        with app.test_request_context():
            try:
                raise RuntimeError(f"student {FIRST} broke the sheet")
            except RuntimeError as exc:
                _body, status = _json_error(exc)
            self.assertEqual(status, 500)
            try:
                raise ValueError("missing field")
            except ValueError as exc:
                _body, status = _json_error(exc)
            self.assertEqual(status, 400)
        sentry_sdk.flush(timeout=2)
        blob = _blob(self.transport)
        self.assertIn("json api", blob)
        self.assertIn("student [redacted] broke the sheet", blob)
        self.assertNotIn(FIRST, blob)

    def test_busy_shed_logs_once_per_minute(self) -> None:
        """A class of artifact 503s becomes one counter line, not one each."""
        reset_busy_shed_counts()
        with self.assertLogs("sentry_wire", level="WARNING") as first:
            note_busy_shed("mint")
        self.assertIn("action=mint", "\n".join(first.output))
        self.assertIn("count=1", "\n".join(first.output))
        with self.assertNoLogs("sentry_wire", level="WARNING"):
            note_busy_shed("mint")
            note_busy_shed("mint")


class AlertAndQuotaTests(unittest.TestCase):
    """The new-issue alert is config, and the log volume is written down."""

    def test_alert_is_a_new_issue_rule_for_both_projects(self) -> None:
        """Slack placeholders only. No webhook secret in the repo."""
        path = LMS_DIR / "ops" / "sentry_new_issue_alert.json"
        rule = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            rule["conditions"][0]["id"],
            "sentry.rules.conditions.first_seen_event.FirstSeenEventCondition",
        )
        action = rule["actions"][0]
        self.assertIn("Slack", action["id"])
        self.assertEqual(action["channel"], "CONFIGURE_IN_SENTRY")
        self.assertNotIn("hooks.slack.com", path.read_text(encoding="utf-8"))
        deploy = (LMS_DIR / "DEPLOY.md").read_text(encoding="utf-8")
        self.assertIn("lloves-lms", deploy)
        self.assertIn("lloves-live", deploy)
        self.assertIn("new issue", deploy.lower())
        self.assertIn("1,000", EXPECTED_MONTHLY_LOG_LINES)
        self.assertIn(EXPECTED_MONTHLY_LOG_LINES, deploy)

    def test_logging_integration_is_warning_and_above(self) -> None:
        """Stdlib INFO is not a Sentry log. logger.error is not auto-an issue."""
        options = sentry_init_options("https://public@o0.ingest.sentry.io/1")
        integration = options["integrations"][0]
        self.assertEqual(integration._sentry_logs_handler.level, logging.WARNING)
        self.assertIsNone(integration._handler)
        self.assertTrue(options["enable_logs"])


class UnlabelledScrubTests(unittest.TestCase):
    """Messages and breadcrumbs lose codes, PINs, and names with no label."""

    def _assert_banned(self, payload: object, label: str) -> str:
        """The serialized payload contains none of the fake secrets."""
        blob = json.dumps(payload)
        for secret in _BANNED:
            self.assertNotIn(secret, blob, f"{label} leaked {secret}")
        return blob

    def test_unlabelled_secrets_are_absent_from_every_payload(self) -> None:
        """``tap by <name>`` and ``code <token>`` never survive a send."""
        prose = (
            f"Roster edited. tap by {FAKE_NAME} {FAKE_LAST} code {FAKE_JOIN} "
            f"catch-up code {FAKE_CATCH} typed {FAKE_PIN} ping {FAKE_EMAIL} "
            f"course code MCF3M student portal due by Monday in 2026 "
            f"code pin_not_set"
        )
        event = {
            "message": prose,
            "extra": {
                "note": f"tap by {FAKE_NAME} code {FAKE_CATCH} typed {FAKE_PIN}",
                "course_code": "MCF3M",
                "code": "pin_not_set",
                "email": FAKE_EMAIL,
                "pin": FAKE_PIN,
            },
            "exception": {
                "values": [{"type": "RuntimeError", "value": prose}]
            },
            "breadcrumbs": {
                "values": [
                    {"message": f"tap by {FAKE_NAME}", "data": {"note": f"code {FAKE_JOIN}"}},
                    {"message": f"typed {FAKE_PIN} ping {FAKE_EMAIL}"},
                ]
            },
        }
        sent = before_send(event, None)
        self.assertIs(sent, event)
        blob = self._assert_banned(sent, "event")
        self.assertIn("tap by [redacted]", blob)
        self.assertIn("course code MCF3M", blob)
        self.assertIn("student portal", blob)
        self.assertIn("due by Monday", blob)
        self.assertIn("2026", blob)
        self.assertIn("pin_not_set", blob)
        self.assertIn("MCF3M", sent["extra"]["course_code"])

        log = {
            "severity_text": "warning",
            "severity_number": 13,
            "body": prose,
            "attributes": {
                "course_code": "MCF3M",
                "code": "pin_not_set",
                "sentry.message.template": "tap by %s code %s typed %s",
                "sentry.message.parameter.0": f"{FAKE_NAME} {FAKE_LAST}",
                "sentry.message.parameter.1": FAKE_JOIN,
                "sentry.message.parameter.2": FAKE_PIN,
            },
        }
        logged = before_send_log(log)
        self.assertIsNotNone(logged)
        log_blob = self._assert_banned(logged, "log")
        self.assertIn("MCF3M", log_blob)
        self.assertIn("pin_not_set", log_blob)
        self.assertIn("2026", log_blob)
        self.assertEqual(logged["attributes"]["course_code"], "MCF3M")

        crumb = before_breadcrumb(
            {
                "category": "custom",
                "message": f"tap by {FAKE_NAME} code {FAKE_CATCH} typed {FAKE_PIN} {FAKE_EMAIL}",
                "data": {"note": f"code {FAKE_JOIN} tap by {FAKE_LAST}"},
            },
            None,
        )
        self.assertIsNotNone(crumb)
        crumb_blob = self._assert_banned(crumb, "breadcrumb")
        self.assertIn("by [redacted]", crumb_blob)
        self.assertIn("[redacted]", crumb_blob)


class RequestSurfaceTests(unittest.TestCase):
    """Headers, cookies, query strings, and exception locals never ship."""

    def test_headers_cookies_query_and_locals_are_removed(self) -> None:
        """Opaque secrets in those four places are absent from the payload."""
        event = {
            "message": "handler failed in 2026 course code MCF3M",
            "request": {
                "method": "POST",
                "url": (
                    f"https://alc.example/student/join?blob={ONLY_QUERY}"
                    f"&pin={FAKE_PIN}&code={FAKE_JOIN}"
                ),
                "query_string": (
                    f"blob={ONLY_QUERY}&code={FAKE_JOIN}&email={FAKE_EMAIL}"
                ),
                "cookies": {"session": ONLY_COOKIE, "pin": FAKE_PIN},
                "env": {"RAW": ONLY_HEADER},
                "data": {"name": FAKE_NAME, "note": ONLY_LOCAL},
                "headers": {
                    "Cookie": f"session={ONLY_COOKIE}",
                    "Authorization": f"Bearer {ONLY_HEADER}",
                    "X-Student-Visit-Token": ONLY_HEADER,
                    "Referer": f"https://alc.example/student/pick?blob={ONLY_REFER}",
                    "User-Agent": f"TestAgent/{FAKE_PIN}",
                    "Host": "alc.example",
                },
            },
            "exception": {
                "values": [
                    {
                        "type": "RuntimeError",
                        "value": f"tap by {FAKE_NAME} code {FAKE_JOIN} typed {FAKE_PIN}",
                        "stacktrace": {
                            "frames": [
                                {
                                    "filename": "app.py",
                                    "lineno": 10,
                                    "vars": {
                                        "pin": FAKE_PIN,
                                        "email": FAKE_EMAIL,
                                        "session_code": FAKE_JOIN,
                                        "first_name": FAKE_NAME,
                                        "scratch": ONLY_LOCAL,
                                        "course_code": "MCF3M",
                                    },
                                }
                            ]
                        },
                    }
                ]
            },
        }
        sent = before_send(event, None)
        self.assertIs(sent, event)
        blob = json.dumps(sent)
        for secret in _BANNED + (
            ONLY_COOKIE,
            ONLY_QUERY,
            ONLY_HEADER,
            ONLY_LOCAL,
            ONLY_REFER,
        ):
            self.assertNotIn(secret, blob, secret)
        request = sent["request"]
        self.assertNotIn("query_string", request)
        self.assertNotIn("cookies", request)
        self.assertNotIn("env", request)
        self.assertNotIn("data", request)
        self.assertEqual(request["url"], "https://alc.example/student/join")
        self.assertEqual(request["method"], "POST")
        headers = request["headers"]
        self.assertNotIn("Cookie", headers)
        self.assertNotIn("Authorization", headers)
        self.assertNotIn("X-Student-Visit-Token", headers)
        self.assertEqual(headers["Host"], "alc.example")
        self.assertEqual(headers["User-Agent"], "TestAgent/[redacted]")
        self.assertEqual(headers["Referer"], "https://alc.example/student/pick")
        frame = sent["exception"]["values"][0]["stacktrace"]["frames"][0]
        self.assertNotIn("vars", frame)
        self.assertEqual(frame["filename"], "app.py")
        self.assertEqual(frame["lineno"], 10)
        self.assertIn("course code MCF3M", blob)
        self.assertIn("2026", blob)
        self.assertIn("tap by [redacted]", blob)


class BrowserScrubTests(unittest.TestCase):
    """sentry-live.js applies the server scrub rules and drops a failed scrub."""

    def test_browser_scrubs_secrets_and_drops_a_failed_scrub(self) -> None:
        """Node run: messages, extras, breadcrumbs, and a cyclic event."""
        script = r"""
const fs = require("fs");
const vm = require("vm");
const src = fs.readFileSync(process.argv[1], "utf8");
function meta(content) { return { content, getAttribute() { return this.content; } }; }
const metas = {
  'meta[name="lloves-sentry-dsn"]': meta("https://public@o0.ingest.sentry.io/1"),
  'meta[name="lloves-sentry-environment"]': meta("tip"),
  'meta[name="lloves-sentry-tags"]': meta('{"portal":"student","course_code":"MCF3M"}'),
};
const sandbox = {
  console,
  location: { origin: "https://alc.example" },
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
function assert(cond, msg) { if (!cond) throw new Error(msg || "assert"); }
const NAME = "Zorblax";
const LAST = "Quuxley";
const EMAIL = "zorblax.quuxley@example.test";
const PIN = "9876";
const JOIN = "Q7Z9K";
const CATCH = "W4N8P2";
const prose = [
  "Roster edited. tap by " + NAME + " " + LAST,
  "code " + JOIN,
  "catch-up code " + CATCH,
  "typed " + PIN,
  "ping " + EMAIL,
  "course code MCF3M student portal due by Monday in 2026 code pin_not_set",
].join(" ");
const event = {
  message: prose,
  extra: {
    note: "tap by " + NAME + " code " + CATCH + " typed " + PIN,
    course_code: "MCF3M",
    code: "pin_not_set",
    email: EMAIL,
    pin: PIN,
    join_code: JOIN,
  },
  exception: { values: [{ type: "Error", value: prose, stacktrace: { frames: [{
    filename: "app.js",
    vars: { scratch: "onlylocalSECRET", pin: PIN, email: EMAIL },
  }] } }] },
  request: {
    url: "https://alc.example/student/join?blob=onlyquerySECRET&pin=" + PIN,
    query_string: "blob=onlyquerySECRET&code=" + JOIN,
    cookies: { session: "onlycookieSECRET" },
    data: { name: NAME },
    headers: {
      Cookie: "session=onlycookieSECRET",
      Authorization: "Bearer onlyheaderSECRET",
      Referer: "https://alc.example/student/pick?blob=onlyreferSECRET",
      "User-Agent": "TestAgent/" + PIN,
      Host: "alc.example",
    },
  },
  breadcrumbs: [
    { category: "custom", message: "tap by " + NAME + " code " + JOIN, data: { note: "typed " + PIN } },
    { category: "console", message: NAME },
  ],
  user: { id: "7", username: NAME, email: EMAIL },
};
const sent = opts.beforeSend(event);
assert(sent === event, "event identity");
const blob = JSON.stringify(sent);
for (const secret of [NAME, LAST, NAME.toLowerCase(), LAST.toLowerCase(), EMAIL, PIN, JOIN, CATCH, "onlylocalSECRET", "onlyquerySECRET", "onlycookieSECRET", "onlyheaderSECRET", "onlyreferSECRET"]) {
  assert(!blob.includes(secret), "leaked " + secret + " in " + blob);
}
assert(blob.includes("tap by [redacted]"), blob);
assert(blob.includes("course code MCF3M"), blob);
assert(blob.includes("student portal"), blob);
assert(blob.includes("due by Monday"), blob);
assert(blob.includes("2026"), blob);
assert(blob.includes("pin_not_set"), blob);
assert(sent.extra.course_code === "MCF3M", "course code field");
assert(!sent.extra.email && !sent.extra.pin && !sent.extra.join_code, "secret keys");
assert(sent.request.url === "https://alc.example/student/join", sent.request.url);
assert(!sent.request.query_string && !sent.request.cookies && !sent.request.data, "request body");
assert(!sent.request.headers.Cookie && !sent.request.headers.Authorization, "secret headers");
assert(sent.request.headers.Host === "alc.example", "host");
assert(sent.request.headers["User-Agent"] === "TestAgent/[redacted]", sent.request.headers["User-Agent"]);
assert(sent.request.headers.Referer === "https://alc.example/student/pick", sent.request.headers.Referer);
assert(!sent.exception.values[0].stacktrace.frames[0].vars, "frame locals");
assert(JSON.stringify(sent.user) === '{"id":"7"}', "user");
assert(sent.breadcrumbs.length === 1, "console crumb dropped");
const cyclic = { message: "boom" };
cyclic.extra = { loop: cyclic };
assert(opts.beforeSend(cyclic) === null, "scrub failure must drop the event");
const bad = { category: "custom", message: "x", data: {} };
bad.data.loop = bad.data;
assert(opts.beforeBreadcrumb(bad) === null, "scrub failure must drop the breadcrumb");
const kept = opts.beforeBreadcrumb({
  category: "custom",
  message: "tap by " + NAME + " code " + JOIN + " typed " + PIN + " " + EMAIL,
});
const keptBlob = JSON.stringify(kept);
for (const secret of [NAME, PIN, JOIN, EMAIL]) {
  assert(!keptBlob.includes(secret), "crumb leaked " + secret);
}
assert(keptBlob.includes("by [redacted]"), keptBlob);
console.log("ok");
"""
        completed = subprocess.run(
            ["node", "-e", script, str(LMS_DIR / "static" / "sentry-live.js")],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertIn("ok", completed.stdout)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""MCK-183 Sentry: teacher identity, tags, key-action logs, browser meta.

Students never get a Sentry user and no student name is ever sent.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

import sentry_sdk  # noqa: E402
from sentry_sdk.transport import Transport  # noqa: E402

from app import create_app  # noqa: E402
import sentry_wire  # noqa: E402
from sentry_wire import (  # noqa: E402
    ACTION_ENDPOINTS,
    ACTION_LOG_ATTRIBUTE,
    action_for,
    before_send_log,
    email_hash,
    init_flask_sentry,
    owner_emails,
    sentry_init_options,
    teacher_kind,
    tool_for_request,
)

TEACHER = "teacher.other@gmail.com"
OWNER = "owner@gmail.com"
ROSTER = ["Zephyrine", "Quillon"]


class CaptureTransport(Transport):
    """Keep every envelope in memory instead of sending it."""

    def __init__(self, options=None) -> None:
        """Start with no envelopes."""
        super().__init__(options)
        self.envelopes: list = []

    def capture_envelope(self, envelope) -> None:
        """Store one envelope."""
        self.envelopes.append(envelope)

    def flush(self, timeout, callback=None) -> None:
        """Nothing is queued."""
        return None

    def kill(self) -> None:
        """Nothing to stop."""
        return None


def _items(transport: CaptureTransport, kind: str) -> list[dict]:
    """Decoded payloads of one item type (``event`` or ``log``)."""
    out: list[dict] = []
    for envelope in transport.envelopes:
        for item in envelope.items:
            if item.headers.get("type") != kind:
                continue
            payload = item.payload.json
            if kind == "log":
                out.extend(payload.get("items") or [])
            else:
                out.append(payload)
    return out


def _all_text(transport: CaptureTransport) -> str:
    """Every envelope serialised, to search for leaked names or emails."""
    chunks = []
    for envelope in transport.envelopes:
        for item in envelope.items:
            chunks.append(item.payload.get_bytes().decode("utf-8", "ignore"))
    return "\n".join(chunks)


class PureHelperTests(unittest.TestCase):
    """Owner list, email hash, tool map, action map, log filter."""

    def test_owner_emails_default_is_empty_so_everyone_is_other(self) -> None:
        """Unset SENTRY_OWNER_EMAILS never hides a teacher under owner."""
        with patch.dict(os.environ, {"SENTRY_OWNER_EMAILS": ""}, clear=False):
            self.assertEqual(owner_emails(), frozenset())
            self.assertEqual(teacher_kind(OWNER), "other")
        os.environ.pop("SENTRY_OWNER_EMAILS", None)
        self.assertEqual(teacher_kind(OWNER), "other")
        with patch.dict(
            os.environ, {"SENTRY_OWNER_EMAILS": " Owner@Gmail.com , x@y.z "}, clear=False
        ):
            self.assertEqual(teacher_kind("owner@gmail.com"), "owner")
            self.assertEqual(teacher_kind(TEACHER), "other")
            self.assertEqual(teacher_kind(""), "other")

    def test_email_hash_is_short_and_not_the_email(self) -> None:
        """12 hex chars, case-insensitive, never the raw address."""
        digest = email_hash("A@B.com")
        self.assertEqual(len(digest), 12)
        self.assertEqual(digest, email_hash(" a@b.com "))
        self.assertNotIn("@", digest)
        self.assertEqual(email_hash(""), "")

    def test_email_hash_is_keyed_with_the_server_secret(self) -> None:
        """HMAC, not a bare sha256: a guessed address can't be confirmed in Sentry."""
        import hashlib

        plain = hashlib.sha256(b"a@b.com").hexdigest()[:12]
        with patch.dict(os.environ, {"FLASK_SECRET_KEY": "one"}, clear=False):
            first = email_hash("a@b.com")
        with patch.dict(os.environ, {"FLASK_SECRET_KEY": "two"}, clear=False):
            second = email_hash("a@b.com")
        self.assertNotEqual(first, plain)
        self.assertNotEqual(first, second)

    def test_transaction_and_spans_lose_query_strings(self) -> None:
        """before_send_transaction strips ?v= from name, request and spans."""
        event = {
            "type": "transaction",
            "transaction": "/student/home?v=VISITTOKEN1",
            "request": {"url": "https://alc.mckenzian.com/student/home?v=VISITTOKEN1",
                        "query_string": "v=VISITTOKEN1"},
            "contexts": {"trace": {"data": {"url.full": "https://x/student/home?v=VISITTOKEN1"}}},
            "spans": [{"description": "GET /api/student/live-prompt/response?v=VISITTOKEN1",
                       "data": {"http.url": "https://x/api/student/a?v=VISITTOKEN1",
                                "http.query": "v=VISITTOKEN1", "url": "/api/b?v=VISITTOKEN1#f"}}],
        }
        out = sentry_wire.before_send_transaction(event)
        text = json.dumps(out)
        self.assertNotIn("VISITTOKEN1", text)
        self.assertNotIn("?v=", text)
        self.assertEqual(out["spans"][0]["description"], "GET /api/student/live-prompt/response")

    def test_tool_from_route_group(self) -> None:
        """Each route group maps to one tool tag."""
        cases = {
            "/staff": "dashboard",
            "/it": "admin",
            "/it/staff": "admin",
            "/api/staff/classes": "roster",
            "/api/staff/classes/4/roster": "roster",
            "/api/classes/4/students": "roster",
            "/api/classes/4/game/finalize-attendance": "attendance",
            "/api/classes/4/attendance-grid": "attendance",
            "/api/classes/4/participation-grid": "participation",
            "/api/classes/4/gradebook": "gradebook",
            "/api/staff/class/4/question-bank/2": "banks",
            "/api/staff/class/4/live-lessons/M1/C1/import-mc": "banks",
            "/api/staff/class/4/live-lessons/M1/C1/add-question": "live",
            "/staff/class/4/run-live": "live",
            "/api/live-sessions/9/items/3/publish": "live",
            "/auth/student-code": "student-join",
            "/student/pick": "student-join",
            "/api/student/live-prompt/response": "live",
            "/student/home": "live",
            "/auth/google": "auth",
            "/staff/class/4/syllabus": "course",
        }
        for path, tool in cases.items():
            self.assertEqual(tool_for_request(path, {}), tool, path)
        self.assertEqual(tool_for_request("/staff/class/4", {"tab": "ap"}), "attendance")
        self.assertEqual(
            tool_for_request("/staff/class/4", {"tab": "ap", "view": "participation"}),
            "participation",
        )
        self.assertEqual(tool_for_request("/staff/class/4", {"tab": "live"}), "live")
        self.assertEqual(tool_for_request("/staff/class/4", {}), "course")

    def test_nine_key_actions_only(self) -> None:
        """The action lines are the approved nine, and GET classes is not one."""
        self.assertEqual(
            set(ACTION_ENDPOINTS.values()),
            {
                "Dashboard opened",
                "Run Live Class",
                "Publish",
                "End Live Class",
                "Quit",
                "Take Attendance saved",
                "Roster edited",
                "Import from bank",
                "Add New",
            },
        )
        self.assertIsNone(action_for("staff_classes", "GET"))
        self.assertEqual(action_for("staff_classes", "POST"), "Roster edited")
        self.assertIsNone(action_for("api_student_state", "GET"))

    def test_before_send_log_keeps_action_lines_only(self) -> None:
        """Ordinary Python logging never becomes a Sentry log."""
        self.assertIsNone(before_send_log({"body": "joined", "attributes": {}}))
        line = {"body": "Publish", "attributes": {ACTION_LOG_ATTRIBUTE: "Publish"}}
        self.assertIs(before_send_log(line), line)

    def test_init_turns_on_logs_and_keeps_replay_off(self) -> None:
        """enable_logs with the filter. No replay or PII options."""
        with patch.dict(
            os.environ, {"SENTRY_DSN": "https://public@o0.ingest.sentry.io/1"}, clear=False
        ):
            with patch.object(sentry_sdk, "init", return_value=None) as mocked:
                self.assertTrue(init_flask_sentry())
        kwargs = mocked.call_args.kwargs
        self.assertIs(kwargs["enable_logs"], True)
        self.assertIs(kwargs["before_send_log"], before_send_log)
        self.assertIs(kwargs["send_default_pii"], False)
        self.assertEqual(kwargs["max_request_body_size"], "never")
        self.assertIn("before_send_transaction", kwargs)
        self.assertNotIn("replays_session_sample_rate", json.dumps(sorted(kwargs)))


    def test_invite_token_and_login_hint_scrubbed(self) -> None:
        """Invite links and emailed sign-in hints never reach Sentry."""
        event = {
            "transaction": "/invite/abc123SECRET",
            "request": {
                "url": "https://alc.mckenzian.com/invite/abc123SECRET",
                "query_string": "portal=staff&login_hint=rae%40gmail.com",
                "data": {"names": "Sam"},
            },
        }
        out = sentry_wire.before_send(event, {})
        self.assertNotIn("SECRET", json.dumps(out))
        self.assertNotIn("rae", json.dumps(out))
        self.assertEqual(out["request"]["url"], "https://alc.mckenzian.com/invite/[token]")
        tx = sentry_wire.before_send_transaction(
            {"transaction": "/invite/abc123SECRET/switch", "request": {"url": "/invite/abc123SECRET"}}
        )
        self.assertNotIn("SECRET", json.dumps(tx))


class RequestScopeTests(unittest.TestCase):
    """A real SDK client with an in-memory transport."""

    def setUp(self) -> None:
        """Init Sentry, build the app, sign in a non-owner teacher."""
        self.transport = CaptureTransport()
        self.env = patch.dict(os.environ, {"SENTRY_OWNER_EMAILS": OWNER}, clear=False)
        self.env.start()
        # The production options, plus every request traced so a sampled
        # transaction can't hide a body leak behind the 5% rate.
        options = sentry_init_options("https://public@o0.ingest.sentry.io/1")
        options["traces_sampler"] = lambda _ctx: 1.0
        sentry_sdk.init(transport=self.transport, **options)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.app.config["PROPAGATE_EXCEPTIONS"] = False

        def boom(class_id: int):
            """Test-only failing staff route."""
            raise RuntimeError("boom for class")

        def student_boom():
            """Test-only failing student route."""
            raise RuntimeError("student boom")

        self.app.add_url_rule("/staff/class/<int:class_id>/boom", "boom", boom)
        self.app.add_url_rule("/student/boom", "student_boom", student_boom)
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff(TEACHER)
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="SPH3U"
        )
        self.client = self.app.test_client()
        self.client.get("/auth/google?portal=staff")
        self.client.get(f"/auth/google/callback?email={TEACHER}&name=Teacher")
        self.client.post(
            "/verify-email",
            data={"code": self.school.get_user_by_email(TEACHER)["verification_code"]},
        )
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ROSTER,
            },
        )
        self.class_id = int(created.get_json()["class"]["id"])
        sentry_sdk.flush()
        self.transport.envelopes.clear()

    def tearDown(self) -> None:
        """Shut the SDK down so other tests run uninstrumented."""
        sentry_sdk.get_client().close()
        sentry_sdk.init()
        self.env.stop()
        self.school.close()
        self.tmp.cleanup()

    def test_student_error_event_has_no_visit_token(self) -> None:
        """A server error on a student request drops the query string too."""
        self.app.test_client().get("/student/boom?v=VISITTOKEN8")
        sentry_sdk.flush()
        self.assertTrue([e for e in _items(self.transport, "event") if e.get("exception")])
        self.assertNotIn("VISITTOKEN8", _all_text(self.transport))

    def test_forced_sampled_student_trace_has_no_visit_token(self) -> None:
        """Every request traced: a student ?v= visit token never reaches Sentry."""
        anon = self.app.test_client()
        anon.get("/student/home?v=VISITTOKEN9")
        anon.get("/api/student/live-prompt?v=VISITTOKEN9")
        anon.get("/?v=VISITTOKEN9")
        sentry_sdk.flush()
        self.assertTrue(_items(self.transport, "transaction"))
        text = _all_text(self.transport)
        self.assertNotIn("VISITTOKEN9", text)
        self.assertNotIn("?v=", text)

    def test_staff_error_carries_teacher_identity_and_tags(self) -> None:
        """user id + email hash, teacher_kind other, class and course tags."""
        response = self.client.get(f"/staff/class/{self.class_id}/boom")
        self.assertEqual(response.status_code, 500)
        sentry_sdk.flush()
        events = [e for e in _items(self.transport, "event") if e.get("exception")]
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(
            event["user"],
            {"id": str(self.teacher["id"]), "email_hash": email_hash(TEACHER)},
        )
        tags = event["tags"]
        self.assertEqual(tags["teacher_id"], str(self.teacher["id"]))
        self.assertEqual(tags["teacher_kind"], "other")
        self.assertEqual(tags["portal"], "staff")
        self.assertEqual(tags["class_id"], str(self.class_id))
        self.assertEqual(tags["course_code"], "SPH3U")
        self.assertEqual(tags["tool"], "course")
        text = _all_text(self.transport)
        self.assertNotIn(TEACHER, text)
        for name in ROSTER:
            self.assertNotIn(name, text)

    def test_owner_email_is_tagged_owner(self) -> None:
        """An address in SENTRY_OWNER_EMAILS is teacher_kind=owner."""
        with patch.dict(os.environ, {"SENTRY_OWNER_EMAILS": TEACHER}, clear=False):
            self.client.get(f"/staff/class/{self.class_id}/boom")
        sentry_sdk.flush()
        event = [e for e in _items(self.transport, "event") if e.get("exception")][0]
        self.assertEqual(event["tags"]["teacher_kind"], "owner")

    def test_class_lookup_runs_only_when_an_event_is_sent(self) -> None:
        """A normal staff request never reads class or course for Sentry."""
        from sentry_wire import make_event_processor

        calls: list[dict] = []

        def counting(**kwargs):
            """Count resolver calls."""
            calls.append(kwargs)
            return self.school.telemetry_class_course(**kwargs)

        processor = make_event_processor(counting, {"class_id": self.class_id})
        self.assertEqual(calls, [])
        event = processor({"type": "transaction", "tags": {}}, {})
        self.assertEqual(calls, [])
        self.assertNotIn("course_code", event["tags"])
        event = processor({"tags": {}}, {})
        self.assertEqual(len(calls), 1)
        self.assertEqual(event["tags"]["course_code"], "SPH3U")
        ok = self.client.get(f"/api/classes/{self.class_id}/attendance-grid")
        self.assertLess(ok.status_code, 500)
        sentry_sdk.flush()
        self.assertEqual(
            [e for e in _items(self.transport, "event") if e.get("exception")], []
        )

    def test_student_error_has_no_user_and_class_tags_only(self) -> None:
        """Students never get a Sentry user and their name is never sent."""
        student = self.app.test_client()
        with student.session_transaction() as sess:
            sess["student_class_id"] = self.class_id
            sess["student_course"] = "SPH3U"
            sess["student_codename"] = ROSTER[0]
        response = student.get("/student/boom")
        self.assertEqual(response.status_code, 500)
        sentry_sdk.flush()
        event = [e for e in _items(self.transport, "event") if e.get("exception")][0]
        self.assertNotIn("user", event)
        tags = event["tags"]
        self.assertEqual(tags["portal"], "student")
        self.assertEqual(tags["class_id"], str(self.class_id))
        self.assertEqual(tags["course_code"], "SPH3U")
        self.assertNotIn("teacher_id", tags)
        self.assertNotIn(ROSTER[0], _all_text(self.transport))

    def test_key_actions_write_one_log_line_without_student_data(self) -> None:
        """Dashboard opened and Roster edited log. Failures and polls do not."""
        self.client.get("/staff")
        self.client.put(
            f"/api/staff/classes/{self.class_id}/roster",
            json={"codenames": ROSTER + ["Maribel"]},
        )
        self.client.post("/api/staff/classes", json={"offering_id": 999999})
        self.client.get(f"/api/classes/{self.class_id}/attendance-grid")
        sentry_sdk.flush()
        logs = _items(self.transport, "log")
        bodies = [row["body"] for row in logs]
        self.assertIn("Dashboard opened", bodies)
        self.assertIn("Roster edited", bodies)
        self.assertEqual(bodies.count("Roster edited"), 1, bodies)
        for row in logs:
            attrs = row["attributes"]
            self.assertIn(ACTION_LOG_ATTRIBUTE, attrs)
            self.assertEqual(attrs["teacher_kind"]["value"], "other")
            self.assertEqual(attrs["teacher_id"]["value"], str(self.teacher["id"]))
        roster_line = next(r for r in logs if r["body"] == "Roster edited")
        self.assertEqual(roster_line["attributes"]["course_code"]["value"], "SPH3U")
        self.assertEqual(roster_line["attributes"]["tool"]["value"], "roster")
        text = _all_text(self.transport)
        for name in ROSTER + ["Maribel"]:
            self.assertNotIn(name, text)
        self.assertNotIn(TEACHER, text)

    def test_python_logging_does_not_become_sentry_logs(self) -> None:
        """App logger lines are filtered out of Sentry Logs."""
        import logging

        logging.getLogger("lloves.test").warning("joined as %s", ROSTER[0])
        sentry_sdk.flush()
        self.assertEqual(_items(self.transport, "log"), [])


class BrowserMetaTests(unittest.TestCase):
    """Browser Sentry on every staff page, with the teacher id and tags."""

    def setUp(self) -> None:
        """App with a teacher, one class, and a browser DSN."""
        self.env = patch.dict(
            os.environ,
            {"SENTRY_DSN_LIVE": "https://public@o0.ingest.sentry.io/1", "SENTRY_OWNER_EMAILS": ""},
            clear=False,
        )
        self.env.start()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff(TEACHER)
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="SPH3U"
        )
        self.client = self.app.test_client()
        self.client.get("/auth/google?portal=staff")
        self.client.get(f"/auth/google/callback?email={TEACHER}&name=Teacher")
        self.client.post(
            "/verify-email",
            data={"code": self.school.get_user_by_email(TEACHER)["verification_code"]},
        )
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ROSTER,
            },
        )
        self.class_id = int(created.get_json()["class"]["id"])

    def tearDown(self) -> None:
        """Restore env and close the db."""
        self.env.stop()
        self.school.close()
        self.tmp.cleanup()

    @staticmethod
    def _meta(html: str, name: str) -> str | None:
        """Return one meta tag's unescaped content, or None."""
        import html as html_lib
        import re

        match = re.search(rf'<meta name="{re.escape(name)}" content="([^"]*)">', html)
        return html_lib.unescape(match.group(1)) if match else None

    def test_dashboard_and_attendance_load_browser_sentry_with_tags(self) -> None:
        """Dashboard and A&P carry the SDK, setUser id, and tags."""
        for url, tool in (
            ("/staff", "dashboard"),
            (f"/staff/class/{self.class_id}?tab=ap&view=attendance", "attendance"),
            (f"/staff/class/{self.class_id}?tab=live", "live"),
        ):
            html = self.client.get(url).get_data(as_text=True)
            self.assertIn("/static/sentry-live.js", html, url)
            self.assertEqual(self._meta(html, "lloves-sentry-user"), str(self.teacher["id"]))
            tags = json.loads(self._meta(html, "lloves-sentry-tags") or "{}")
            self.assertEqual(tags["teacher_kind"], "other", url)
            self.assertEqual(tags["portal"], "staff", url)
            self.assertEqual(tags["tool"], tool, url)
            self.assertEqual(tags["teacher_id"], str(self.teacher["id"]))
            if "class" in url:
                self.assertEqual(tags["class_id"], str(self.class_id))
                self.assertEqual(tags["course_code"], "SPH3U")
            self.assertNotIn(TEACHER, self._meta(html, "lloves-sentry-tags") or "")

    def test_student_page_meta_has_no_user(self) -> None:
        """The partial on a student page renders an empty user."""
        with self.app.test_request_context("/student/home"):
            from flask import render_template, session

            session["student_class_id"] = self.class_id
            session["student_course"] = "SPH3U"
            session["student_codename"] = ROSTER[0]
            html = render_template("_sentry_browser.html")
        self.assertEqual(self._meta(html, "lloves-sentry-user"), "")
        tags = json.loads(self._meta(html, "lloves-sentry-tags") or "{}")
        self.assertEqual(tags["portal"], "student")
        self.assertEqual(tags["course_code"], "SPH3U")
        self.assertNotIn("teacher_id", tags)
        self.assertNotIn(ROSTER[0], html)

    def test_every_staff_and_student_template_includes_the_partial(self) -> None:
        """Dashboard, course (every tab), Admin, and the student pages."""
        names = [
            "staff/home.html",
            "staff/course.html",
            "staff/bots.html",
            "it/dashboard.html",
            "landing.html",
            "student/home.html",
            "student/waiting.html",
            "student/game.html",
            "student/pick.html",
        ]
        for name in names:
            text = (LMS_DIR / "templates" / name).read_text(encoding="utf-8")
            head = text.partition("</head>")[0]
            self.assertIn('{% include "_sentry_browser.html" %}', head, name)
        course_head = (LMS_DIR / "templates/staff/course.html").read_text().partition("</head>")[0]
        self.assertNotIn("tab == 'live' %}{% include", course_head)


class BrowserScriptTests(unittest.TestCase):
    """sentry-live.js: setUser/setTags, scrubbing, fetch failures."""

    def test_browser_identity_scrub_and_fetch_failures(self) -> None:
        """Node sandbox run of the browser file."""
        script = r"""
const fs = require("fs");
const vm = require("vm");
const src = fs.readFileSync(process.argv[1], "utf8");
function meta(content) { return { content, getAttribute() { return this.content; } }; }
const metas = {
  'meta[name="lloves-sentry-dsn"]': meta("https://public@o0.ingest.sentry.io/1"),
  'meta[name="lloves-sentry-environment"]': meta("tip"),
  'meta[name="lloves-sentry-user"]': meta("7"),
  'meta[name="lloves-sentry-tags"]': meta('{"teacher_id":"7","teacher_kind":"other","portal":"staff","tool":"attendance","class_id":"2","course_code":"SBI4U"}'),
};
const calls = { user: null, tags: null, messages: [], exceptions: [] };
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
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const opts = sandbox.__opts;
function assert(cond, msg) { if (!cond) throw new Error(msg); }
assert(JSON.stringify(calls.user) === '{"id":"7"}', "setUser " + JSON.stringify(calls.user));
assert(calls.tags.teacher_kind === "other" && calls.tags.course_code === "SBI4U", "tags");
assert(!opts.replaysSessionSampleRate && !opts.replaysOnErrorSampleRate, "replay must stay off");
// Breadcrumb scrubbing: roster names never leave.
assert(opts.beforeBreadcrumb({ category: "console", message: "Zephyrine joined" }) === null, "console kept");
const click = opts.beforeBreadcrumb({ category: "ui.click", message: 'button.roster-name[aria-label="Zephyrine"]' });
assert(click.message === "button.roster-name", "click " + click.message);
const nav = opts.beforeBreadcrumb({ category: "navigation", data: { from: "/student/pick?name=Zephyrine", to: "/student/home" } });
assert(nav.data.from === "/student/pick", "nav");
const ev = opts.beforeSend({
  message: "x",
  request: { url: "https://alc.mckenzian.com/student/pick?name=Quillon", headers: { Referer: "https://alc.mckenzian.com/?n=Quillon" } },
  user: { id: "7", username: "Quillon", ip_address: "1.2.3.4" },
  breadcrumbs: [{ category: "console", message: "Quillon" }],
});
const evText = JSON.stringify(ev);
assert(!evText.includes("Quillon"), "event leaked a name: " + evText);
assert(JSON.stringify(ev.user) === '{"id":"7"}', "event user");
(async () => {
  // A failed live action (500) reports once per method + path + status.
  await sandbox.fetch("/api/live-sessions/12/items/3/publish", { method: "POST" });
  await sandbox.fetch("/api/live-sessions/12/items/4/publish", { method: "POST" });
  assert(calls.messages.length === 1, "dedupe " + calls.messages.length);
  const [msg, ctx] = calls.messages[0];
  assert(msg === "Live fetch failed: POST /api/live-sessions/:id/items/:id/publish 500", msg);
  // Our own fetch-failure event is not dropped by the poll filter.
  const own = { message: "Live fetch failed: GET /api/student/state 500", tags: ctx.tags,
    breadcrumbs: [{ category: "fetch", data: { url: "/api/student/state", status_code: 500 } }] };
  assert(opts.beforeSend(own) === own, "own fetch failure dropped");
  // A student-side 500 reports too.
  await sandbox.fetch("/api/student/live-items/5/rank-turn?x=Quillon", { method: "POST" });
  assert(calls.messages.length === 2, "student 500");
  assert(!calls.messages[1][0].includes("Quillon"), "query leaked");
  // Busy / shed statuses stay quiet. So do other sites.
  fetchStatus = 503;
  await sandbox.fetch("/api/student/state");
  fetchStatus = 500;
  await sandbox.fetch("https://accounts.google.com/gsi/client");
  assert(calls.messages.length === 2, "reported a soft status or a foreign host");
  // Swallowed poll paint errors report, at most 3 per page load.
  sandbox.llovesSentryReport(new TypeError("x is undefined"), "student-state-paint");
  assert(calls.exceptions.length === 1, "llovesSentryReport");
  assert(calls.exceptions[0][1].tags["lloves.where"] === "student-state-paint", "where tag");
  for (let i = 0; i < 5; i += 1) sandbox.llovesSentryReport(new TypeError("again"), "student-state-paint");
  assert(calls.exceptions.length === 3, "report cap " + calls.exceptions.length);
  // Spans and transactions lose query strings (student ?v= visit token).
  const span = opts.beforeSendSpan({
    description: "GET /api/student/live-prompt/response?v=VISITTOKEN1",
    data: { "url.full": "https://alc.mckenzian.com/api/student/live-prompt/response?v=VISITTOKEN1", "url.query": "?v=VISITTOKEN1", "http.url": "/api/student/x?v=VISITTOKEN1" },
  });
  assert(span && !JSON.stringify(span).includes("VISITTOKEN1"), "span leaked " + JSON.stringify(span));
  const tx = opts.beforeSendTransaction({
    transaction: "/student/home?v=VISITTOKEN1",
    request: { url: "https://alc.mckenzian.com/student/home?v=VISITTOKEN1", query_string: "v=VISITTOKEN1" },
    contexts: { trace: { data: { "url.full": "https://alc.mckenzian.com/student/home?v=VISITTOKEN1" } } },
    spans: [{ description: "GET /student/mood?v=VISITTOKEN1", data: { "http.url": "/student/mood?v=VISITTOKEN1", "http.query": "v=VISITTOKEN1" } }],
  });
  const txText = JSON.stringify(tx);
  assert(!txText.includes("VISITTOKEN1") && !txText.includes("?v="), "transaction leaked " + txText);
  console.log("ok");
})().catch((err) => { console.error(err.message); process.exit(1); });
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

    def test_poll_loops_report_paint_errors(self) -> None:
        """Student and staff /state loops report a throw after the response."""
        student = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        staff = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn('window.llovesSentryReport?.(_err, "student-state-paint")', student)
        self.assertIn('window.llovesSentryReport?.(_, "staff-state-paint")', staff)


if __name__ == "__main__":
    unittest.main()

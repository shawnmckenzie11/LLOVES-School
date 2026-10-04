"""Sentry stays off without a DSN, and live polls are not fatal events."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sentry_sdk

from app import create_app
from sentry_wire import (
    DEFAULT_TRACES_SAMPLE_RATE,
    SENTRY_CONNECT_SRC,
    before_send,
    environment_name,
    init_flask_sentry,
    release_name,
    traces_sampler,
)

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
DSN_RE = re.compile(r"https://[0-9a-f]{16,}@")


class SentryWireTests(unittest.TestCase):
    """Env-only Flask init, poll sampling, and the browser shell snippet."""

    def test_unset_dsn_does_not_init(self) -> None:
        """An empty SENTRY_DSN leaves the SDK untouched."""
        with patch.dict(os.environ, {"SENTRY_DSN": ""}, clear=False):
            with patch.object(sentry_sdk, "init") as mocked:
                self.assertFalse(init_flask_sentry())
        mocked.assert_not_called()

    def test_bad_dsn_does_not_break_boot(self) -> None:
        """A rejected DSN is swallowed so create_app can continue."""
        with patch.dict(os.environ, {"SENTRY_DSN": "not-a-dsn"}, clear=False):
            self.assertFalse(init_flask_sentry())

    def test_init_tags_environment_and_release(self) -> None:
        """GH_SHA and FLASK_ENV are the release and environment tags."""
        with patch.dict(
            os.environ,
            {
                "SENTRY_DSN": "https://public@o0.ingest.sentry.io/1",
                "FLASK_ENV": "production",
                "GH_SHA": "abc123",
                "SENTRY_ENVIRONMENT": "",
                "SENTRY_RELEASE": "",
            },
            clear=False,
        ):
            with patch.object(sentry_sdk, "init", return_value=None) as mocked:
                self.assertTrue(init_flask_sentry())
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["environment"], "production")
        self.assertEqual(kwargs["release"], "abc123")
        self.assertIs(kwargs["send_default_pii"], False)
        self.assertIs(kwargs["include_local_variables"], False)
        self.assertEqual(kwargs["max_request_body_size"], "never")
        self.assertIs(kwargs["traces_sampler"], traces_sampler)
        self.assertIs(kwargs["before_send"], before_send)
        self.assertNotIn("integrations", kwargs)

    def test_environment_override_and_release_fallback(self) -> None:
        """SENTRY_ENVIRONMENT and SENTRY_RELEASE override the deploy tags."""
        with patch.dict(
            os.environ,
            {
                "FLASK_ENV": "production",
                "SENTRY_ENVIRONMENT": "tip",
                "GH_SHA": "",
                "SENTRY_RELEASE": "local",
            },
            clear=False,
        ):
            self.assertEqual(environment_name(), "tip")
            self.assertEqual(release_name(), "local")
        with patch.dict(
            os.environ,
            {"FLASK_ENV": "", "SENTRY_ENVIRONMENT": "", "GH_SHA": "", "SENTRY_RELEASE": ""},
            clear=False,
        ):
            self.assertEqual(environment_name(), "development")
            self.assertIsNone(release_name())

    def test_sampler_skips_polls_even_when_parent_sampled(self) -> None:
        """Health and live polls stay at 0. Other routes use the small rate."""
        self.assertEqual(
            traces_sampler({"wsgi_environ": {"PATH_INFO": "/health"}}),
            0.0,
        )
        self.assertEqual(
            traces_sampler({"wsgi_environ": {"PATH_INFO": "/api/student/state"}}),
            0.0,
        )
        self.assertEqual(
            traces_sampler(
                {"wsgi_environ": {"PATH_INFO": "/api/student/heartbeat"}}
            ),
            0.0,
        )
        self.assertEqual(
            traces_sampler(
                {"wsgi_environ": {"PATH_INFO": "/api/live-sessions/12/state"}}
            ),
            0.0,
        )
        self.assertEqual(
            traces_sampler(
                {
                    "wsgi_environ": {"PATH_INFO": "/api/student/state"},
                    "parent_sampled": True,
                }
            ),
            0.0,
        )
        self.assertEqual(
            traces_sampler({"transaction_context": {"name": "GET /health"}}),
            0.0,
        )
        self.assertEqual(
            traces_sampler({"wsgi_environ": {"PATH_INFO": "/staff/class/1"}}),
            DEFAULT_TRACES_SAMPLE_RATE,
        )
        self.assertEqual(
            traces_sampler(
                {
                    "wsgi_environ": {"PATH_INFO": "/api/classes/1/game"},
                    "parent_sampled": True,
                }
            ),
            1.0,
        )
        self.assertEqual(
            traces_sampler(
                {
                    "wsgi_environ": {"PATH_INFO": "/staff"},
                    "parent_sampled": False,
                }
            ),
            0.0,
        )

    def test_before_send_drops_poll_disconnects_only(self) -> None:
        """Client disconnects on /state are noise. App errors still send."""
        disconnect = {
            "request": {
                "url": "https://alc.mckenzian.com/api/student/state?seq=1"
            },
            "exception": {"values": [{"type": "ConnectionResetError"}]},
        }
        self.assertIsNone(before_send(disconnect, None))
        staff_disconnect = {
            "request": {
                "url": "https://alc.mckenzian.com/api/live-sessions/4/state"
            },
            "exception": {"values": [{"type": "ClientDisconnected"}]},
        }
        self.assertIsNone(before_send(staff_disconnect, None))
        locked = {
            "request": {"url": "https://alc.mckenzian.com/api/student/heartbeat"},
            "exception": {
                "values": [{"type": "OperationalError", "value": "database is locked"}]
            },
        }
        self.assertIs(before_send(locked, None), locked)
        other_page = {
            "request": {"url": "https://alc.mckenzian.com/staff/class/1"},
            "exception": {"values": [{"type": "BrokenPipeError"}]},
        }
        self.assertIs(before_send(other_page, None), other_page)

    def test_testing_factory_skips_sentry_and_csp_allows_ingest(self) -> None:
        """Unit-test apps do not phone home. Pages may post envelopes to Sentry."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("app.init_flask_sentry") as mocked:
                app = create_app(
                    db_path=root / "lloves.sqlite",
                    data_dir=root,
                    testing=True,
                )
            mocked.assert_not_called()
            response = app.test_client().get("/health")
        self.assertEqual(response.status_code, 200)
        csp = response.headers.get("Content-Security-Policy", "")
        self.assertIn(SENTRY_CONNECT_SRC, csp)
        self.assertIn("https://accounts.google.com", csp)

    def test_browser_partial_injects_env_dsn_only(self) -> None:
        """The meta tag is the env value. A blank env emits no script."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = create_app(
                db_path=root / "lloves.sqlite",
                data_dir=root,
                testing=True,
            )
            with app.app_context():
                from flask import render_template

                with patch.dict(os.environ, {"SENTRY_DSN_LIVE": ""}, clear=False):
                    blank = render_template("_sentry_browser.html")
                with patch.dict(
                    os.environ,
                    {
                        "SENTRY_DSN_LIVE": "https://public@o0.ingest.sentry.io/1",
                        "SENTRY_ENVIRONMENT": "tip",
                        "GH_SHA": "deadbeef",
                    },
                    clear=False,
                ):
                    filled = render_template("_sentry_browser.html")
        self.assertNotIn("lloves-sentry-dsn", blank)
        self.assertNotIn("bundle.tracing.min.js", blank)
        self.assertIn("https://public@o0.ingest.sentry.io/1", filled)
        self.assertIn('content="tip"', filled)
        self.assertIn('content="deadbeef"', filled)
        self.assertIn("/static/sentry-live.js", filled)

    def test_live_shell_templates_include_the_partial(self) -> None:
        """Run Live Class and the student portal are the browser surfaces."""
        course = (LMS_DIR / "templates" / "staff" / "course.html").read_text(
            encoding="utf-8"
        )
        head, _, _rest = course.partition("</head>")
        # MCK-183: every course tab loads browser Sentry, not only Live.
        self.assertNotIn("{% if tab == 'live' %}", head)
        self.assertIn('{% include "_sentry_browser.html" %}', head)
        for name in ("home.html", "mood.html", "exit.html", "pick.html", "character.html"):
            text = (LMS_DIR / "templates" / "student" / name).read_text(
                encoding="utf-8"
            )
            self.assertIn('{% include "_sentry_browser.html" %}', text, name)

    def test_no_dsn_string_in_the_tree(self) -> None:
        """A real ingest key must not land in git."""
        skip_dirs = {".git", "__pycache__", "node_modules", ".local-data"}
        skip_suffixes = {".png", ".pdf", ".woff", ".woff2", ".sqlite", ".zip"}
        hits: list[str] = []
        for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
            dirnames[:] = [name for name in dirnames if name not in skip_dirs]
            for name in filenames:
                path = Path(dirpath) / name
                if path.suffix.lower() in skip_suffixes:
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if DSN_RE.search(text):
                    hits.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(hits, [])

    def test_bundle_is_the_tracing_build(self) -> None:
        """The vendored file is the performance bundle, without a missing map."""
        bundle = LMS_DIR / "static" / "vendor" / "sentry" / "bundle.tracing.min.js"
        head = bundle.read_text(encoding="utf-8")[:180]
        self.assertIn("@sentry/browser", head)
        self.assertIn("Performance Monitoring", head)
        self.assertIn("11.1.0", head)
        self.assertGreater(bundle.stat().st_size, 100_000)
        self.assertNotIn("sourceMappingURL", bundle.read_text(encoding="utf-8")[-200:])

    def test_deploy_wires_release_sha(self) -> None:
        """The image receives GH_SHA and the SDK is a runtime dependency."""
        dockerfile = (LMS_DIR / "Dockerfile").read_text(encoding="utf-8")
        workflow = (REPO_ROOT / ".github" / "workflows" / "deploy.yml").read_text(
            encoding="utf-8"
        )
        requirements = (LMS_DIR / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("ARG GH_SHA=", dockerfile)
        self.assertIn("ENV GH_SHA=${GH_SHA}", dockerfile)
        self.assertIn("--build-arg GH_SHA=${{ github.sha }}", workflow)
        self.assertIn("sentry-sdk>=2.71.0", requirements)

    def test_browser_filter_keeps_paint_bugs_and_drops_poll_failures(self) -> None:
        """Unhandled poll transport errors drop. Paint exceptions report."""
        script = r"""
const fs = require("fs");
const vm = require("vm");
const src = fs.readFileSync(process.argv[1], "utf8");
const metas = {
  'meta[name="lloves-sentry-dsn"]': { content: "https://public@o0.ingest.sentry.io/1", getAttribute() { return this.content; } },
  'meta[name="lloves-sentry-environment"]': { content: "tip", getAttribute() { return this.content; } },
  'meta[name="lloves-sentry-release"]': { content: "abc", getAttribute() { return this.content; } },
};
const sandbox = {
  console,
  document: {
    querySelector(sel) { return metas[sel] || null; },
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.Sentry = {
  init(opts) { sandbox.__opts = opts; },
  browserTracingIntegration(cfg) {
    sandbox.__trace = cfg;
    return { name: "BrowserTracing" };
  },
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const opts = sandbox.__opts;
if (!opts) throw new Error("init was not called");
if (opts.dsn !== metas['meta[name="lloves-sentry-dsn"]'].content) throw new Error("dsn");
if (opts.environment !== "tip") throw new Error("env");
if (opts.release !== "abc") throw new Error("release");
if (opts.sendDefaultPii !== false) throw new Error("pii");
const integrations = opts.integrations([]);
if (!integrations.some((item) => item && item.name === "BrowserTracing")) {
  throw new Error("missing BrowserTracing");
}
const span = sandbox.__trace.shouldCreateSpanForRequest;
if (span("/api/student/state?seq=1") !== false) throw new Error("span state");
if (span("/api/student/heartbeat") !== false) throw new Error("span beat");
if (span("/api/live-sessions/4/state?light=1") !== false) throw new Error("span staff");
if (span("/api/classes/1/game") !== true) throw new Error("span game");
const re = opts.tracePropagationTargets[0];
if (re.test("/api/student/state")) throw new Error("prop state");
if (re.test("/api/live-sessions/4/state")) throw new Error("prop staff");
if (re.test("/health")) throw new Error("prop health");
if (!re.test("/api/classes/1/game")) throw new Error("prop game");
const pollFail = {
  exception: { values: [{ type: "TypeError", value: "Failed to fetch" }] },
  breadcrumbs: [{ category: "fetch", data: { url: "/api/student/state", status_code: 0 } }],
};
if (opts.beforeSend(pollFail) !== null) throw new Error("kept poll fail");
const http503 = {
  exception: { values: [{ type: "Error", value: "HTTP 503" }] },
  breadcrumbs: [{ category: "fetch", data: { url: "/api/live-sessions/9/state", status_code: 503 } }],
};
if (opts.beforeSend(http503) !== null) throw new Error("kept 503");
const paint = {
  exception: { values: [{ type: "TypeError", value: "Cannot read properties of undefined (reading 'map')" }] },
  breadcrumbs: [{ category: "fetch", data: { url: "/api/student/state", status_code: 200 } }],
};
if (opts.beforeSend(paint) !== paint) throw new Error("dropped paint");
const slides = {
  exception: { values: [{ type: "TypeError", value: "Failed to fetch" }] },
  breadcrumbs: [
    { category: "fetch", data: { url: "/api/student/heartbeat", status_code: 200 } },
    { category: "fetch", data: { url: "https://accounts.google.com/gsi/client", status_code: 0 } },
  ],
};
if (opts.beforeSend(slides) !== slides) throw new Error("dropped slides");
const pollSpan = { description: "GET /api/student/state", data: { url: "/api/student/state" } };
if (opts.beforeSendSpan(pollSpan) !== null) throw new Error("kept poll span");
const gameSpan = { description: "GET /api/classes/1/game", data: { url: "/api/classes/1/game" } };
if (opts.beforeSendSpan(gameSpan) !== gameSpan) throw new Error("dropped game span");
const okCrumb = { category: "fetch", data: { url: "/api/student/heartbeat", status_code: 200 } };
if (opts.beforeBreadcrumb(okCrumb) !== null) throw new Error("kept ok crumb");
const badCrumb = { category: "fetch", data: { url: "/api/student/state", status_code: 503 } };
if (opts.beforeBreadcrumb(badCrumb) !== badCrumb) throw new Error("dropped bad crumb");
console.log("ok");
"""
        completed = subprocess.run(
            ["node", "-e", script, str(LMS_DIR / "static" / "sentry-live.js")],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stderr or completed.stdout,
        )
        self.assertIn("ok", completed.stdout)


if __name__ == "__main__":
    unittest.main()

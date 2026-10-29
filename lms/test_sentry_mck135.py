"""MCK-135: WARNING logs, secret scrubbing, swallowed errors, new-issue alert."""

from __future__ import annotations

import json
import logging
import os
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
    before_send,
    before_send_log,
    note_busy_shed,
    report_swallowed,
    reset_busy_shed_counts,
    sentry_init_options,
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


if __name__ == "__main__":
    unittest.main()

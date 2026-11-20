#!/usr/bin/env python3
"""MCK-492: read-only bearer token for the End Class report GETs.

The token opens only the three report reads. A staff session still sees
the stored report. The token response drops synthetic names, join codes,
and PINs and keeps an opaque reporter id.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
import live_fix  # noqa: E402
from sentry_wire import before_send  # noqa: E402


TOKEN = "test-report-token-492"
STAFF_EMAIL = "staff-report-token@example.com"
ROSTER_NAME = "Pebble"
STUDENT_NAME = "Zorblax Quuxley"
JOIN_CODE = "Q7Z9K2"
PIN = "4821"
STUDENT_EMAIL = "zorblax.quuxley@example.com"
ANON_ID = "student-73ab12cd34ef"
SESSION_ID = 910492
_SECRET_KEYS = {
    "pin",
    "course_pin",
    "student_pin",
    "join_code",
    "session_code",
    "portal_join_code",
    "student_name",
    "first_name",
    "last_name",
    "codename",
    "email",
    "name",
    "reporter_name",
}
_SECRET_VALUES = (
    STUDENT_NAME,
    "Zorblax",
    "Quuxley",
    "zorblax",
    "quuxley",
    JOIN_CODE,
    JOIN_CODE.lower(),
    PIN,
    STUDENT_EMAIL,
    STAFF_EMAIL,
)


def _keys(node: object, found: list[str]) -> None:
    """Collect dict keys from a JSON tree.

    Args:
        node: Parsed JSON value.
        found: Key names in lower case.
    """
    if isinstance(node, dict):
        for key, child in node.items():
            found.append(str(key).lower())
            _keys(child, found)
    elif isinstance(node, list):
        for child in node:
            _keys(child, found)


class LiveFixReportTokenTests(unittest.TestCase):
    """Token scope, redaction, and the unchanged staff view."""

    def setUp(self) -> None:
        """One staff class and a stored report that still contains secrets."""
        logging.disable(logging.WARNING)
        self._env = patch.dict(
            os.environ,
            {
                "LIVE_FIX_REPORT_TOKEN": TOKEN,
                "ANTHROPIC_API_KEY": "",
                "SENTRY_AUTH_TOKEN": "",
                "SENTRY_ORG": "",
            },
            clear=False,
        )
        self._env.start()
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("SENTRY_AUTH_TOKEN", None)
        os.environ.pop("SENTRY_ORG", None)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.outsider = self.app.test_client()
        self.school.activate_from_semester_json()
        self.school.register_staff(STAFF_EMAIL)
        offering = self.school.assign_course(
            teacher_user_id=int(self.school.get_user_by_email(STAFF_EMAIL)["id"]),
            ontario_code="MCR3U",
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get(f"/auth/google/callback?email={STAFF_EMAIL}&name=Teacher")
        code = self.school.get_user_by_email(STAFF_EMAIL)["verification_code"]
        self.client.post("/verify-email", data={"code": code})
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": int(offering["id"]),
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": [ROSTER_NAME],
            },
        )
        self.class_id = int(created.get_json()["class"]["id"])
        self.report = {
            "session_id": SESSION_ID,
            "class_id": self.class_id,
            "course_code": "MCR3U",
            "section": "MCR3U",
            "lesson": "M1 C2",
            "student_name": STUDENT_NAME,
            "first_name": "Zorblax",
            "last_name": "Quuxley",
            "codename": ROSTER_NAME,
            "email": STUDENT_EMAIL,
            "join_code": JOIN_CODE,
            "session_code": JOIN_CODE,
            "portal_join_code": JOIN_CODE,
            "pin": PIN,
            "course_pin": PIN,
            "copy_text": (
                f"End Class report\nCourse: MCR3U\n"
                f"Student {STUDENT_NAME} join code {JOIN_CODE} PIN {PIN}\n"
                f"zorblax typed {JOIN_CODE.lower()} and {PIN} near {ROSTER_NAME}\n"
                f"Reporter: student {ANON_ID}\n"
                f"{STUDENT_EMAIL}\n"
            ),
            "flags_by_slide": [
                {
                    "slide": 1,
                    "flags": [
                        {
                            "reporter_role": "student",
                            "reporter_anon": ANON_ID,
                            "note": (
                                f"{STUDENT_NAME} and {ROSTER_NAME} saw code "
                                f"{JOIN_CODE} pin {PIN}"
                            ),
                            "question_id": "eq-image-1",
                        }
                    ],
                }
            ],
            "sentry": {"configured": False, "issues": []},
        }
        self.school.conn.execute(
            """
            INSERT INTO live_fix_reports (
                live_session_id, class_id, created_at, started_at, ended_at, report_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                SESSION_ID,
                self.class_id,
                "2026-11-16T10:00:00",
                "2026-11-16T10:00:00",
                "2026-11-16T11:00:00",
                json.dumps(self.report),
            ),
        )
        self.school.conn.commit()
        self.latest = f"/api/staff/class/{self.class_id}/live-fix-reports/latest"
        self.by_id = (
            f"/api/staff/class/{self.class_id}/live-fix-reports/{SESSION_ID}"
        )
        self.listing = f"/api/staff/class/{self.class_id}/live-fix-reports"
        self.auth = {"Authorization": f"Bearer {TOKEN}"}

    def tearDown(self) -> None:
        """Close the temp database and restore the process environment."""
        logging.disable(logging.NOTSET)
        self.school.close()
        self.tmp.cleanup()
        self._env.stop()

    def _stored_report(self) -> str:
        """Return the report row exactly as it is stored."""
        row = self.school.conn.execute(
            "SELECT report_json FROM live_fix_reports WHERE live_session_id = ?",
            (SESSION_ID,),
        ).fetchone()
        self.assertIsNotNone(row)
        return str(row["report_json"])

    def test_token_is_rejected_on_other_staff_routes(self) -> None:
        """The token is not a staff session. Reads and writes elsewhere are 401."""
        before_flags = self.school.conn.execute(
            "SELECT COUNT(*) FROM live_fix_flags"
        ).fetchone()[0]
        before_reports = self._stored_report()
        listing = self.outsider.get("/api/staff/classes", headers=self.auth)
        flags = self.outsider.get(
            "/api/live-sessions/1/flags",
            headers=self.auth,
        )
        posted = self.outsider.post(
            "/api/live-sessions/1/flags",
            json={"note": "should not store"},
            headers=self.auth,
        )
        created = self.outsider.post(
            "/api/staff/classes",
            json={"offering_id": 1, "days": "M/W/F", "time": "2:00pm", "codenames": ["Pebble"]},
            headers=self.auth,
        )
        self.assertEqual(listing.status_code, 401, listing.get_json())
        self.assertEqual(flags.status_code, 401, flags.get_json())
        self.assertEqual(posted.status_code, 401, posted.get_json())
        self.assertEqual(created.status_code, 401, created.get_json())
        after_flags = self.school.conn.execute(
            "SELECT COUNT(*) FROM live_fix_flags"
        ).fetchone()[0]
        self.assertEqual(after_flags, before_flags)
        self.assertEqual(self._stored_report(), before_reports)

    def test_latest_token_response_drops_names_codes_and_pins(self) -> None:
        """A token read of /latest has no name, join code, or PIN, and keeps the id."""
        before = self._stored_report()
        latest = self.outsider.get(self.latest, headers=self.auth)
        by_id = self.outsider.get(self.by_id, headers=self.auth)
        listing = self.outsider.get(self.listing, headers=self.auth)
        self.assertEqual(latest.status_code, 200, latest.get_json())
        self.assertEqual(by_id.status_code, 200, by_id.get_json())
        self.assertEqual(listing.status_code, 200, listing.get_json())
        for response in (latest, by_id, listing):
            blob = response.get_data(as_text=True)
            for secret in _SECRET_VALUES:
                self.assertNotIn(secret, blob, secret)
            keys: list[str] = []
            _keys(response.get_json(), keys)
            for key in _SECRET_KEYS:
                self.assertNotIn(key, keys, key)
        report = latest.get_json()["report"]
        self.assertEqual(report["course_code"], "MCR3U")
        self.assertIn(ANON_ID, report["copy_text"])
        self.assertEqual(report["flags_by_slide"][0]["flags"][0]["reporter_anon"], ANON_ID)
        self.assertIn("eq-image-1", json.dumps(report))
        self.assertNotIn(ROSTER_NAME, json.dumps(report))
        self.assertEqual(self._stored_report(), before)
        self.assertIn(str(SESSION_ID), listing.get_data(as_text=True))

    def test_bad_or_missing_token_is_401(self) -> None:
        """A wrong token, a missing header, and a blank env value do not unlock the report."""
        missing = self.outsider.get(self.latest)
        wrong = self.outsider.get(
            self.latest,
            headers={"Authorization": "Bearer not-the-token"},
        )
        self.assertEqual(missing.status_code, 401, missing.get_json())
        self.assertEqual(wrong.status_code, 401, wrong.get_json())
        with patch.dict(os.environ, {"LIVE_FIX_REPORT_TOKEN": ""}):
            disabled = self.outsider.get(self.latest, headers=self.auth)
        self.assertEqual(disabled.status_code, 401, disabled.get_json())
        with patch.dict(os.environ, {"LIVE_FIX_REPORT_TOKEN": "   "}):
            blank = self.outsider.get(self.latest, headers=self.auth)
        self.assertEqual(blank.status_code, 401, blank.get_json())

    def test_staff_session_keeps_the_full_report(self) -> None:
        """Teacher sign-in still returns the stored names, join code, and PIN."""
        latest = self.client.get(self.latest)
        self.assertEqual(latest.status_code, 200, latest.get_json())
        report = latest.get_json()["report"]
        self.assertEqual(report["student_name"], STUDENT_NAME)
        self.assertEqual(report["join_code"], JOIN_CODE)
        self.assertEqual(report["pin"], PIN)
        self.assertEqual(report["email"], STUDENT_EMAIL)
        self.assertEqual(report["codename"], ROSTER_NAME)
        self.assertIn(ROSTER_NAME, report["copy_text"])
        self.assertIn(STUDENT_NAME, report["copy_text"])
        self.assertIn(JOIN_CODE, report["copy_text"])
        self.assertIn(PIN, report["copy_text"])
        self.assertIn(ANON_ID, report["copy_text"])
        both = self.client.get(self.latest, headers=self.auth)
        self.assertEqual(both.status_code, 200, both.get_json())
        self.assertEqual(both.get_json()["report"]["student_name"], STUDENT_NAME)
        self.assertEqual(both.get_json()["report"]["pin"], PIN)

    def test_compare_digest_and_logs_omit_the_token(self) -> None:
        """Matches use hmac.compare_digest. Log lines do not keep the bearer value."""
        lines: list[str] = []

        class Grab(logging.Handler):
            """Collect formatted log lines."""

            def emit(self, record: logging.LogRecord) -> None:
                """Store one formatted line."""
                lines.append(record.getMessage())

        handler = Grab()
        root = logging.getLogger()
        previous = root.level
        root.setLevel(logging.INFO)
        root.addHandler(handler)
        logging.disable(logging.NOTSET)
        try:
            with patch("live_fix.hmac.compare_digest", wraps=hmac.compare_digest) as cmp:
                bad = self.outsider.get(
                    self.latest,
                    headers={"Authorization": "Bearer not-the-token"},
                )
                good = self.outsider.get(self.latest, headers=self.auth)
            logging.getLogger().info(
                "inbound Authorization: Bearer %s",
                TOKEN,
            )
        finally:
            root.removeHandler(handler)
            root.setLevel(previous)
            logging.disable(logging.WARNING)
        self.assertEqual(bad.status_code, 401)
        self.assertEqual(good.status_code, 200)
        self.assertGreaterEqual(cmp.call_count, 2)
        self.assertEqual([line for line in lines if TOKEN in line], [])
        self.assertTrue(any("[redacted]" in line for line in lines))

    def test_sentry_before_send_drops_the_authorization_header(self) -> None:
        """The existing Sentry scrubber removes the report token's header."""
        event = {
            "message": "report fetch failed",
            "request": {
                "method": "GET",
                "url": "https://alc.example/api/staff/class/1/live-fix-reports/latest",
                "headers": {
                    "Authorization": f"Bearer {TOKEN}",
                    "Host": "alc.example",
                },
            },
        }
        sent = before_send(event, None)
        self.assertIs(sent, event)
        blob = json.dumps(sent)
        self.assertNotIn(TOKEN, blob)
        self.assertNotIn("Authorization", sent["request"]["headers"])
        self.assertEqual(sent["request"]["headers"]["Host"], "alc.example")


if __name__ == "__main__":
    unittest.main()

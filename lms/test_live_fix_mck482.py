#!/usr/bin/env python3
"""MCK-482 and MCK-480: in-class flags and the End Class report.

Flags store slide context and an anonymized reporter. Student polling
does not read the flag table. End Class writes a report with or without
Sentry and without calling Claude on the request.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
import live_fix  # noqa: E402


SECRET_NAME = "Maple"
STAFF_EMAIL = "staff-fix@example.com"


class LiveFixReportTests(unittest.TestCase):
    """Flag capture, student notes, and the End Class report."""

    def setUp(self) -> None:
        """One staff class, one roster student, and a live session on slide 1."""
        logging.disable(logging.WARNING)
        self._env = patch.dict(
            os.environ,
            {"ANTHROPIC_API_KEY": "", "SENTRY_AUTH_TOKEN": "", "SENTRY_ORG": ""},
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
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff(STAFF_EMAIL)
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
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
                "codenames": [SECRET_NAME],
            },
        )
        self.class_id = int(created.get_json()["class"]["id"])
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=True
        )
        self.session_id = int(live["id"])
        self.session_code = str(live["session_code"])
        self.school.set_live_session_teacher_state(
            self.session_id,
            page_number=1,
            page_id="round_1",
            stage="round",
            live_module="M1",
            live_slot="C2",
        )
        now = "2026-11-16T10:00:00"
        self.school.conn.execute(
            """
            INSERT INTO live_session_items (
                live_session_id, placement_key, item_id, stage, page_number,
                sort_order, kind, item_json, status, created_at, updated_at
            ) VALUES (?, 'fix-q', 'eq-image-1', 'round', 1, 1, 'mc', '{}', 'active', ?, ?)
            """,
            (self.session_id, now, now),
        )
        self.school.conn.commit()
        student = self.school.game.find_student_by_codename(self.class_id, SECRET_NAME)
        self.assertIsNotNone(student)
        self.student_id = int(student["id"])
        joined = self.school.join_live_class_session(
            self.session_id, self.student_id, codename=SECRET_NAME
        )
        self.visit_token = str(joined["attendee"]["visit_token"])
        self.student = self.app.test_client()

    def tearDown(self) -> None:
        """Close the temp database and restore the process environment."""
        logging.disable(logging.NOTSET)
        self.school.close()
        self.tmp.cleanup()
        self._env.stop()

    def _stored_fix_text(self) -> str:
        """Dump flag and report rows. Roster tables are not included."""
        chunks = []
        for table in ("live_fix_flags", "live_fix_reports"):
            rows = self.school.conn.execute(f"SELECT * FROM {table}").fetchall()
            for row in rows:
                chunks.append(json.dumps(dict(row), default=str))
        return "\n".join(chunks)

    def _assert_secrets_absent(self, text: str) -> None:
        """Fail when a roster name, staff email, or join code is present."""
        self.assertNotIn(SECRET_NAME, text)
        self.assertNotIn(STAFF_EMAIL, text)
        self.assertNotIn(self.session_code, text)

    def test_flag_captures_slide_and_hides_the_reporter(self) -> None:
        """A teacher flag keeps the slide and drops names, email, and the code."""
        note = f"{SECRET_NAME} {STAFF_EMAIL} {self.session_code} can't see the image"
        response = self.client.post(
            f"/api/live-sessions/{self.session_id}/flags",
            json={
                "note": note,
                "reporter_name": SECRET_NAME,
                "actions": ["live-stage-next", f"{SECRET_NAME} said hi", SECRET_NAME, "save-work"],
            },
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        flag = response.get_json()["flag"]
        self.assertEqual(flag["slide_number"], 1)
        self.assertEqual(flag["page_id"], "round_1")
        self.assertEqual(flag["question_id"], "eq-image-1")
        self.assertEqual(flag["content_type"], "mc")
        self.assertEqual(flag["actions"], ["live-stage-next", "save-work"])
        self.assertEqual(flag["reporter_role"], "teacher")
        self.assertTrue(str(flag["reporter_anon"]).startswith("teacher-"))
        self.assertIn("can't see the image", flag["note"])
        self._assert_secrets_absent(json.dumps(flag))
        self._assert_secrets_absent(self._stored_fix_text())
        listed = self.client.get(f"/api/live-sessions/{self.session_id}/flags")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.get_json()["flags"]), 1)

    def test_student_flag_and_poll_does_not_read_flags(self) -> None:
        """A joined student can file a note. /api/student/state does not query it."""
        seen: list[str] = []

        def tracer(sql: str) -> None:
            """Record statements that touch the fix tables."""
            if "live_fix" in sql.lower():
                seen.append(sql)

        self.school.conn.set_trace_callback(tracer)
        state = self.student.get(
            "/api/student/state",
            headers={"X-Student-Visit-Token": self.visit_token},
        )
        self.school.conn.set_trace_callback(None)
        self.assertEqual(state.status_code, 200, state.get_data(as_text=True)[:500])
        self.assertEqual(seen, [])
        note = f"{SECRET_NAME} can't see the image"
        filed = self.student.post(
            "/api/student/flags",
            json={"note": note, "actions": ["save-work"], "codename": SECRET_NAME},
            headers={"X-Student-Visit-Token": self.visit_token},
        )
        self.assertEqual(filed.status_code, 200, filed.get_json())
        self.assertIn("id", filed.get_json())
        self.assertNotIn("flag", filed.get_json())
        rows = self.school.conn.execute(
            "SELECT reporter_role, reporter_anon, note, content_type, question_id FROM live_fix_flags"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reporter_role"], "student")
        self.assertTrue(str(rows[0]["reporter_anon"]).startswith("student-"))
        self.assertIn("image", rows[0]["note"])
        self.assertEqual(rows[0]["question_id"], "eq-image-1")
        self._assert_secrets_absent(self._stored_fix_text())
        stranger = self.app.test_client()
        denied = stranger.get(f"/api/live-sessions/{self.session_id}/flags")
        self.assertIn(denied.status_code, (401, 403))

    def test_teacher_can_delete_a_flag_while_class_is_live(self) -> None:
        """Delete removes one note and leaves the other."""
        first = self.client.post(
            f"/api/live-sessions/{self.session_id}/flags",
            json={"note": "first", "actions": ["live-stage-next"]},
        )
        second = self.client.post(
            f"/api/live-sessions/{self.session_id}/flags",
            json={"note": "second", "actions": ["live-stage-prev"]},
        )
        flag_id = int(first.get_json()["flag"]["id"])
        removed = self.client.delete(f"/api/live-sessions/{self.session_id}/flags/{flag_id}")
        self.assertEqual(removed.status_code, 200, removed.get_json())
        listed = self.client.get(f"/api/live-sessions/{self.session_id}/flags").get_json()
        self.assertEqual([row["id"] for row in listed["flags"]], [int(second.get_json()["flag"]["id"])])

    def test_end_live_class_report_without_sentry_or_claude(self) -> None:
        """End Class saves a report. Missing keys skip Sentry and Claude."""
        self.student.post(
            "/api/student/flags",
            json={"note": f"{SECRET_NAME} can't see the image", "actions": ["save-work"]},
            headers={"X-Student-Visit-Token": self.visit_token},
        )
        calls: list[str] = []

        def explode(*_args, **_kwargs):
            """Fail the test if End Class reaches the network."""
            calls.append("http")
            raise AssertionError("network")

        with patch.object(live_fix, "_http_json", explode):
            ended = self.client.post(f"/staff/class/{self.class_id}/end-live")
        self.assertEqual(ended.status_code, 302)
        self.assertIn("fix_report=", ended.headers["Location"])
        self.assertIn("tab=ap", ended.headers["Location"])
        self.assertEqual(calls, [])
        latest = self.client.get(f"/api/staff/class/{self.class_id}/live-fix-reports/latest")
        self.assertEqual(latest.status_code, 200, latest.get_json())
        report = latest.get_json()["report"]
        self.assertEqual(report["flag_count"], 1)
        self.assertEqual(report["flags_by_slide"][0]["slide"], 1)
        self.assertEqual(report["sentry"]["configured"], False)
        self.assertEqual(report["tidy"]["status"], "skipped")
        self.assertIn("image", report["flags_by_slide"][0]["flags"][0]["note"])
        self._assert_secrets_absent(json.dumps(report))
        self._assert_secrets_absent(self._stored_fix_text())
        page = self.client.get(ended.headers["Location"])
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn('id="live-fix-history"', html)
        self.assertIn('id="live-fix-report"', html)

    def test_sentry_issues_are_optional_and_drop_user_fields(self) -> None:
        """A configured token adds counts. A user name on the issue is not stored."""
        os.environ["SENTRY_AUTH_TOKEN"] = "test-token"
        os.environ["SENTRY_ORG"] = "mckenzian"

        def fake_http(url, **_kwargs):
            """Return one issue that still carries a user object."""
            if "/lloves-live/" in url:
                return []
            self.assertIn("/lloves-lms/", url)
            return [
                {
                    "shortId": "LLOVES-LMS-9",
                    "title": f"slide error {SECRET_NAME}",
                    "count": "3",
                    "firstSeen": "2020-01-01T00:00:00",
                    "lastSeen": datetime.now().replace(microsecond=0).isoformat(),
                    "permalink": "https://sentry.io/issues/9",
                    "user": {"name": SECRET_NAME, "email": STAFF_EMAIL},
                }
            ]

        with patch.object(live_fix, "_http_json", fake_http):
            self.school.finish_live_class(self.class_id, celebrate=True, live_run=True)
        report = live_fix.latest_report(self.school, self.class_id)
        self.assertIsNotNone(report)
        issue = report["sentry"]["issues"][0]
        self.assertEqual(issue["id"], "LLOVES-LMS-9")
        self.assertEqual(issue["count"], 3)
        self.assertIn("first_seen", issue)
        self.assertIn("last_seen", issue)
        self.assertNotIn("user", issue)
        self._assert_secrets_absent(json.dumps(report))
        self._assert_secrets_absent(self._stored_fix_text())

    def test_claude_tidy_is_off_the_request_and_sends_no_names(self) -> None:
        """The key turns a note into a title only when tidy runs on its own."""
        self.client.post(
            f"/api/live-sessions/{self.session_id}/flags",
            json={"note": f"{SECRET_NAME} can't see the image", "actions": ["live-eyes-up"]},
        )
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        calls: list[dict] = []

        def fake_http(url, **kwargs):
            """Capture the outbound body and return a one-line title."""
            calls.append({"url": url, "payload": kwargs.get("payload")})
            return {"content": [{"text": '{"title": "Image missing on slide 1", "description": "The equation image did not show."}'}]}

        with patch.object(live_fix, "_http_json", fake_http):
            self.school.finish_live_class(self.class_id, celebrate=True, live_run=True)
            self.assertEqual(calls, [])
            result = live_fix.tidy_session_flags(self.school, self.session_id)
        self.assertEqual(result["status"], "done")
        self.assertEqual(len(calls), 1)
        sent = json.dumps(calls[0]["payload"])
        self._assert_secrets_absent(sent)
        self.assertIn("can't see the image", sent)
        self.assertNotIn(SECRET_NAME, sent)
        report = live_fix.latest_report(self.school, self.class_id)
        flag = report["flags_by_slide"][0]["flags"][0]
        self.assertEqual(flag["tidy_title"], "Image missing on slide 1")
        self.assertIn("equation image", flag["tidy_description"])
        self._assert_secrets_absent(self._stored_fix_text())

    def test_live_and_student_pages_offer_the_note_box(self) -> None:
        """The teacher shell has Flag and the student phone has Report a problem."""
        live = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        self.assertEqual(live.status_code, 200)
        html = live.get_data(as_text=True)
        self.assertIn('id="live-fix-flag"', html)
        self.assertIn("Shift+F", html)
        self.assertIn("/static/live_fix.js", html)
        script = (LMS_DIR / "static" / "live_fix.js").read_text(encoding="utf-8")
        self.assertIn("SpeechRecognition", script)
        self.assertNotIn("api.anthropic.com", script)
        self.assertNotIn("sentry.io", script)
        home = (LMS_DIR / "templates" / "student" / "home.html").read_text(encoding="utf-8")
        self.assertIn('id="live-fix-student"', home)
        self.assertIn("Report a problem", home)


if __name__ == "__main__":
    unittest.main()

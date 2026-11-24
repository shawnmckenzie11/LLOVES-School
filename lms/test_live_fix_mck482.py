#!/usr/bin/env python3
"""MCK-482 and MCK-480: in-class flags and the End Class report.

Flags store slide context and an anonymized reporter. Student polling
does not read the flag table. End Class writes a report with or without
Sentry and without calling Claude on the request.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from werkzeug.test import EnvironBuilder

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
            {
                "ANTHROPIC_API_KEY": "",
                "SENTRY_AUTH_TOKEN": "",
                "SENTRY_ORG": "",
                "LIVE_FIX_ANON_PEPPER": "test-pepper",
            },
            clear=False,
        )
        self._env.start()
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("SENTRY_AUTH_TOKEN", None)
        os.environ.pop("SENTRY_ORG", None)
        live_fix.clear_note_rates()
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
        self.assertNotIn(self.visit_token, text)

    def test_flag_captures_slide_and_hides_the_reporter(self) -> None:
        """A teacher flag keeps the slide and drops names, email, and the code."""
        note = (
            f"{SECRET_NAME} {STAFF_EMAIL} {self.session_code} "
            f"{self.visit_token} can't see the image"
        )
        response = self.client.post(
            f"/api/live-sessions/{self.session_id}/flags",
            json={
                "note": note,
                "reporter_name": SECRET_NAME,
                "actions": [
                    "live-stage-next",
                    f"{SECRET_NAME} said hi",
                    SECRET_NAME,
                    self.visit_token,
                    "save-work",
                ],
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
        self.assertIn('id="live-fix-report-copy"', html)

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

    def test_copy_report_is_plain_text_without_secrets(self) -> None:
        """Copy report carries the class, the debug label, Sentry, and the release."""
        self.addCleanup(os.environ.pop, "SENTRY_AUTH_TOKEN", None)
        self.addCleanup(os.environ.pop, "SENTRY_ORG", None)
        self.addCleanup(os.environ.pop, "GH_SHA", None)
        os.environ["SENTRY_AUTH_TOKEN"] = "test-token"
        os.environ["SENTRY_ORG"] = "mckenzian"
        os.environ["GH_SHA"] = "abc1234deadbeef"
        self.student.post(
            "/api/student/flags",
            json={
                "note": f"{SECRET_NAME} saw the question twice",
                "actions": ["save-work"],
            },
            headers={"X-Student-Visit-Token": self.visit_token},
        )

        def fake_http(url, **_kwargs):
            """Return one issue whose title still names the student."""
            if "/lloves-live/" in url:
                return []
            return [
                {
                    "shortId": "LLOVES-LMS-9",
                    "title": f"duplicate card {SECRET_NAME}",
                    "count": "2",
                    "firstSeen": "2020-01-01T00:00:00Z",
                    "lastSeen": datetime.now().replace(microsecond=0).isoformat(),
                    "permalink": "https://sentry.io/issues/9",
                    "user": {"email": STAFF_EMAIL, "name": SECRET_NAME},
                }
            ]

        with patch.object(live_fix, "_http_json", fake_http):
            self.school.finish_live_class(self.class_id, celebrate=True, live_run=True)
        report = live_fix.latest_report(self.school, self.class_id)
        self.assertIsNotNone(report)
        text = str(report["copy_text"])
        self.assertIn("Course: MCR3U", text)
        self.assertIn("Section: MCR3U", text)
        self.assertIn("Lesson: M1 C2", text)
        self.assertIn("Deck: M1 C2", text)
        self.assertIn(" ET", text)
        self.assertIn("Release: abc1234deadbeef", text)
        self.assertIn("Slide 1", text)
        self.assertIn("eq-image-1", text)
        self.assertIn("save-work", text)
        self.assertRegex(
            text,
            r"Debug: student · student-[0-9a-f]{12} · mc · eq-image-1 · save-work",
        )
        self.assertRegex(text, r"Reporter: student student-[0-9a-f]{12}")
        self.assertIn("LLOVES-LMS-9", text)
        self.assertIn("https://sentry.io/issues/9", text)
        self.assertNotIn(self.session_code, text)
        self._assert_secrets_absent(text)
        self._assert_secrets_absent(json.dumps(report))
        script = (LMS_DIR / "static" / "live_fix.js").read_text(encoding="utf-8")
        self.assertIn("copy_text", script)
        self.assertIn("live-fix-report-copy", script)

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
        script_ids = set(re.findall(r'"([a-z][a-z0-9-]*)"', script[script.index("const ACTION_IDS"):script.index("function rememberAction")]))
        self.assertEqual(script_ids, set(live_fix.ACTION_IDS))

    def test_missing_pepper_stores_role_only(self) -> None:
        """No pepper means no reporter hash, and the warning has no secrets."""
        live_fix._pepper_warned = False
        logging.disable(logging.NOTSET)
        try:
            with patch.dict(os.environ, {"LIVE_FIX_ANON_PEPPER": ""}, clear=False):
                with self.assertLogs("live_fix", level="WARNING") as logs:
                    response = self.client.post(
                        f"/api/live-sessions/{self.session_id}/flags",
                        json={"note": "can't see the image", "actions": ["live-stage-next"]},
                    )
        finally:
            logging.disable(logging.WARNING)
        self.assertEqual(response.status_code, 200, response.get_json())
        flag = response.get_json()["flag"]
        self.assertEqual(flag["reporter_role"], "teacher")
        self.assertEqual(flag["reporter_anon"], "")
        warning = "\n".join(logs.output)
        self.assertIn("LIVE_FIX_ANON_PEPPER is unset", warning)
        self.assertNotIn(SECRET_NAME, warning)
        self.assertNotIn(self.visit_token, warning)
        self.assertNotIn(STAFF_EMAIL, warning)

    def test_other_tenant_it_cannot_read_notes(self) -> None:
        """IT in another school gets 403 on the note list and the report."""
        tenant = self.school.create_tenant("other-school", "Other School")
        outsider = self.school.register_staff(
            "it-other@gmail.com", tenant_id=int(tenant["id"])
        )
        self.school.conn.execute(
            "UPDATE users SET role = 'it' WHERE id = ?",
            (int(outsider["id"]),),
        )
        self.school.conn.commit()
        foreign = self.app.test_client()
        foreign.get("/auth/google?portal=it")
        foreign.get(
            "/auth/google/callback?portal=it&email=it-other@gmail.com&name=Other"
        )
        code = self.school.get_user_by_email("it-other@gmail.com")["verification_code"]
        foreign.post("/verify-email", data={"code": code})
        notes = foreign.get(f"/api/live-sessions/{self.session_id}/flags")
        report = foreign.get(
            f"/api/staff/class/{self.class_id}/live-fix-reports/latest"
        )
        self.assertEqual(notes.status_code, 403)
        self.assertEqual(report.status_code, 403)

    def test_student_notes_are_rate_limited_and_oversized_bodies_are_refused(self) -> None:
        """A student is limited per person and per session. A huge body is 413."""
        headers = {
            "X-Student-Visit-Token": self.visit_token,
            "Content-Type": "application/json",
        }
        huge = self.student.post(
            "/api/student/flags",
            data=b"{}",
            headers=headers,
            environ_overrides={"CONTENT_LENGTH": "99999"},
        )
        self.assertEqual(huge.status_code, 413, huge.get_data(as_text=True)[:300])
        self.assertEqual(
            self.school.conn.execute("SELECT COUNT(*) FROM live_fix_flags").fetchone()[0],
            0,
        )
        with patch.object(live_fix, "STUDENT_NOTE_LIMIT", 2), patch.object(
            live_fix, "SESSION_NOTE_LIMIT", 100
        ):
            for _ in range(2):
                ok = self.student.post(
                    "/api/student/flags",
                    json={"note": "can't see the image", "actions": ["save-work"]},
                    headers={"X-Student-Visit-Token": self.visit_token},
                )
                self.assertEqual(ok.status_code, 200, ok.get_json())
            limited = self.student.post(
                "/api/student/flags",
                json={"note": "again", "actions": ["save-work"]},
                headers={"X-Student-Visit-Token": self.visit_token},
            )
        self.assertEqual(limited.status_code, 429, limited.get_json())
        live_fix.clear_note_rates()
        with patch.object(live_fix, "STUDENT_NOTE_LIMIT", 100), patch.object(
            live_fix, "SESSION_NOTE_LIMIT", 2
        ):
            for _ in range(2):
                ok = self.student.post(
                    "/api/student/flags",
                    json={"note": "session cap", "actions": ["save-work"]},
                    headers={"X-Student-Visit-Token": self.visit_token},
                )
                self.assertEqual(ok.status_code, 200, ok.get_json())
            limited = self.student.post(
                "/api/student/flags",
                json={"note": "over session", "actions": ["save-work"]},
                headers={"X-Student-Visit-Token": self.visit_token},
            )
        self.assertEqual(limited.status_code, 429, limited.get_json())

    def test_split_visit_token_is_scrubbed(self) -> None:
        """A visit token with a space after the first 12 characters is removed."""
        token = self.visit_token
        split = f"{token[:12]} {token[12:]}"
        response = self.client.post(
            f"/api/live-sessions/{self.session_id}/flags",
            json={"note": f"see {split}", "actions": ["live-stage-next"]},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        stored = response.get_json()["flag"]["note"]
        self.assertNotIn(token[:12], stored)
        self.assertNotIn(token[12:], stored)
        self.assertNotIn(token, stored)
        self.assertLessEqual(len(stored), 280)

    def test_chunked_oversize_note_stops_at_the_cap(self) -> None:
        """A chunked body with no Content-Length is refused after 4 KB."""
        payload = json.dumps({"note": "x" * 80000, "actions": ["save-work"]}).encode()

        class CountingStream(io.RawIOBase):
            """Count bytes pulled from the note body."""

            def __init__(self, raw: bytes) -> None:
                self._raw = io.BytesIO(raw)
                self.read_bytes = 0

            def read(self, size: int = -1) -> bytes:
                if size is None or size < 0:
                    size = len(payload)
                chunk = self._raw.read(size)
                self.read_bytes += len(chunk)
                return chunk

            def readable(self) -> bool:
                return True

        stream = CountingStream(payload)
        builder = EnvironBuilder(
            path="/api/student/flags",
            method="POST",
            content_type="application/json",
            headers={"X-Student-Visit-Token": self.visit_token},
        )
        built = builder.get_request()
        built.environ.pop("CONTENT_LENGTH", None)
        built.environ["wsgi.input_terminated"] = True
        built.environ["wsgi.input"] = stream
        response = self.student.open(built)
        self.assertEqual(response.status_code, 413, response.get_data(as_text=True)[:300])
        self.assertLess(stream.read_bytes, live_fix.FLAG_BODY_MAX_BYTES + 64)
        self.assertEqual(
            self.school.conn.execute("SELECT COUNT(*) FROM live_fix_flags").fetchone()[0],
            0,
        )


if __name__ == "__main__":
    unittest.main()

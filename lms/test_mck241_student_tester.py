#!/usr/bin/env python3
"""MCK-241: hidden test students, STMTG tester feedback, dry-run seed."""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from celebration import celebration_candidates  # noqa: E402
from show_test_students import show_test_students_requested  # noqa: E402
from stmtg_tester import apply_stmtg_tester, assert_tester_course  # noqa: E402
from tester_feedback import (  # noqa: E402
    feedback_issue_input,
    post_tester_feedback,
    prepare_feedback_text,
    redact_real_roster,
)

_PIN = re.compile(r"(?<!\d)\d{4}(?!\d)")


def _load_seed():
    """Import the STMTG seed script without running it."""
    path = REPO_ROOT / "scripts" / "seed_stmtg_student_tester.py"
    spec = importlib.util.spec_from_file_location("seed_stmtg_student_tester", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ShowSwitchTests(unittest.TestCase):
    """The teacher switch defaults to hidden and an explicit off wins."""

    def test_query_wins_over_cookie(self) -> None:
        """``show_test_students=0`` hides rows even when the cookie is on."""
        self.assertFalse(show_test_students_requested({}, {}))
        self.assertTrue(
            show_test_students_requested({}, {"lloves_show_test_students": "1"})
        )
        self.assertTrue(
            show_test_students_requested(
                {"show_test_students": "1"},
                {"lloves_show_test_students": "0"},
            )
        )
        self.assertFalse(
            show_test_students_requested(
                {"show_test_students": "0"},
                {"lloves_show_test_students": "1"},
            )
        )


class HiddenTestStudentTests(unittest.TestCase):
    """Attendance, participation, roster, and exports stay on real students."""

    def setUp(self) -> None:
        """One MCF3M class with Maple and a flagged Test Student."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.client.post(
            "/verify-email",
            data={"code": self.school.get_user_by_email("teacher@gmail.com")["verification_code"]},
        )
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple"],
            },
        )
        self.assertEqual(created.status_code, 200, created.get_data(as_text=True))
        self.class_id = int(created.get_json()["class"]["id"])
        flagged = self.school.ensure_class_test_student(self.class_id)
        assert flagged is not None
        self.test_id = int(flagged["student_id"])
        maple = self.school.game.find_student_by_codename(self.class_id, "Maple")
        assert maple is not None
        self.maple_id = int(maple["id"])
        self._mark_both_present()

    def tearDown(self) -> None:
        """Close the temp database."""
        self.school.close()
        self.tmp.cleanup()

    def _mark_both_present(self) -> None:
        """Give Maple and the test student a present mark on the class session."""
        game = self.school.game
        with game._lock:
            session = game.conn.execute(
                "SELECT id FROM sessions WHERE class_id = ? ORDER BY id LIMIT 1",
                (self.class_id,),
            ).fetchone()
            self.session_id = int(session["id"])
            game.conn.execute(
                "UPDATE sessions SET status = 'ended' WHERE id = ?",
                (self.session_id,),
            )
            game.conn.execute(
                """
                UPDATE session_scores SET present = 1, points = 1
                WHERE session_id = ? AND student_id = ?
                """,
                (self.session_id, self.maple_id),
            )
            game.conn.execute(
                """
                INSERT INTO session_scores (session_id, student_id, present, points)
                VALUES (?, ?, 1, 4)
                """,
                (self.session_id, self.test_id),
            )
            game.conn.commit()

    def _student_ids(self, payload: dict) -> list[int]:
        """Roster ids from a grid or gradebook payload."""
        return [int(row["id"]) for row in payload.get("students") or []]

    def test_views_hide_test_student_until_asked(self) -> None:
        """Default grids, roster, and CSV omit the test student. The query shows them."""
        hidden_att = self.client.get(
            f"/api/classes/{self.class_id}/attendance-grid"
        ).get_json()
        shown_att = self.client.get(
            f"/api/classes/{self.class_id}/attendance-grid?show_test_students=1"
        ).get_json()
        self.assertEqual(self._student_ids(hidden_att), [self.maple_id])
        self.assertCountEqual(self._student_ids(shown_att), [self.maple_id, self.test_id])
        hidden_total = sum(int(n) for n in (hidden_att.get("day_totals") or {}).values())
        shown_total = sum(int(n) for n in (shown_att.get("day_totals") or {}).values())
        self.assertGreaterEqual(shown_total, hidden_total)
        if shown_total != hidden_total:
            self.assertEqual(shown_total, hidden_total + 1)

        hidden_part = self.client.get(
            f"/api/classes/{self.class_id}/participation-grid"
        ).get_json()
        shown_part = self.client.get(
            f"/api/classes/{self.class_id}/participation-grid?show_test_students=1"
        ).get_json()
        self.assertEqual(self._student_ids(hidden_part), [self.maple_id])
        self.assertIn(self.test_id, self._student_ids(shown_part))
        hidden_points = hidden_part.get("totals") or {}
        self.assertNotIn(str(self.test_id), hidden_points)

        hidden_book = self.client.get(f"/api/classes/{self.class_id}/gradebook").get_json()
        shown_book = self.client.get(
            f"/api/classes/{self.class_id}/gradebook?show_test_students=1"
        ).get_json()
        self.assertEqual(self._student_ids(hidden_book), [self.maple_id])
        self.assertIn(self.test_id, self._student_ids(shown_book))

        home = self.client.get("/staff").get_data(as_text=True)
        self.assertIn('id="show-test-students"', home)
        self.assertNotIn("Test Student", home)
        roster = self.client.get(
            f"/staff/class/{self.class_id}?tab=ap&view=roster"
        ).get_data(as_text=True)
        self.assertIn("Show test students", roster)
        self.assertNotIn("Test Student", roster)
        participation = self.client.get(
            f"/staff/class/{self.class_id}?tab=ap&view=participation"
        ).get_data(as_text=True)
        self.assertIn("show_test_students=0", participation)
        shown_roster = self.client.get(
            f"/staff/class/{self.class_id}?tab=ap&view=roster&show_test_students=1"
        ).get_data(as_text=True)
        self.assertIn("Test Student", shown_roster)
        shown_home = self.client.get("/staff?show_test_students=1").get_data(as_text=True)
        self.assertIn('id="show-test-students"', shown_home)
        self.assertNotIn("Test Student", shown_home)

        self.school.conn.execute(
            """
            INSERT INTO live_result_snapshots (
                live_session_id, class_id, session_code, run_key, live_module, live_slot,
                source, question_key, question_json, student_id, codename, answer_json,
                snapshot_at
            ) VALUES (0, ?, 'CODE', 'run-241', 'M1', 'C1', 'test', 'q', '{}', ?, ?, '{}',
                      '2026-10-01T12:00:00')
            """,
            (self.class_id, self.maple_id, "Maple"),
        )
        self.school.conn.execute(
            """
            INSERT INTO live_result_snapshots (
                live_session_id, class_id, session_code, run_key, live_module, live_slot,
                source, question_key, question_json, student_id, codename, answer_json,
                snapshot_at
            ) VALUES (0, ?, 'CODE', 'run-241', 'M1', 'C1', 'test', 'q2', '{}', ?, ?, '{}',
                      '2026-10-01T12:00:01')
            """,
            (self.class_id, self.test_id, "Test Student"),
        )
        self.school.conn.commit()
        hidden_csv = self.client.get(
            f"/staff/class/{self.class_id}/results.csv?show_test_students=0"
        ).get_data(as_text=True)
        shown_csv = self.client.get(
            f"/staff/class/{self.class_id}/results.csv?show_test_students=1"
        ).get_data(as_text=True)
        self.assertIn("Maple", hidden_csv)
        self.assertNotIn("Test Student", hidden_csv)
        self.assertIn("Test Student", shown_csv)

    def test_cookie_shows_roster_and_explicit_off_hides_it(self) -> None:
        """The staff cookie follows the teacher across subtabs until the query says off."""
        self.client.set_cookie("lloves_show_test_students", "1", domain="localhost")
        shown = self.client.get(
            f"/api/classes/{self.class_id}/attendance-grid"
        ).get_json()
        self.assertIn(self.test_id, self._student_ids(shown))
        forced = self.client.get(
            f"/api/classes/{self.class_id}/attendance-grid?show_test_students=0"
        ).get_json()
        self.assertEqual(self._student_ids(forced), [self.maple_id])

    def test_celebrations_and_live_list_stay_hidden(self) -> None:
        """The teacher switch does not put a test student on celebrations or the live list."""
        featured = celebration_candidates(self.school, int(self.teacher["id"]))
        featured_ids = {int(row["student_id"]) for row in featured}
        self.assertIn(self.maple_id, featured_ids)
        self.assertNotIn(self.test_id, featured_ids)
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        projected = self.school.live_class_roster_projection(int(live["id"]))
        projected_ids = {int(row["student_id"]) for row in projected}
        self.assertIn(self.maple_id, projected_ids)
        self.assertNotIn(self.test_id, projected_ids)

    def test_flag_survives_rename_and_roster_replace(self) -> None:
        """Renaming or saving the roster without the test name keeps the flag and the row."""
        self.school.rename_roster_student(
            self.class_id, self.test_id, display_name="Test S"
        )
        self.assertTrue(self.school.is_test_student(self.test_id))
        self.school.game.replace_codename_roster(self.class_id, ["Maple"])
        self.assertTrue(self.school.is_test_student(self.test_id))
        row = self.school.game.conn.execute(
            "SELECT id FROM students WHERE id = ? AND class_id = ?",
            (self.test_id, self.class_id),
        ).fetchone()
        self.assertIsNotNone(row)


class TesterFeedbackTests(unittest.TestCase):
    """Feedback rejects PINs and never puts them in logs or Linear."""

    def test_prepare_and_redact(self) -> None:
        """A PIN, a course code, or a real name is refused. Exact names are blanked."""
        with self.assertRaises(ValueError):
            prepare_feedback_text("The door code is 4821 today")
        with self.assertRaises(ValueError):
            prepare_feedback_text("Look at MCR3U")
        with self.assertRaises(ValueError):
            prepare_feedback_text("Maple could not hear", forbidden_names={"Maple"})
        clean = prepare_feedback_text("The join screen was slow on my phone.")
        issue = feedback_issue_input(clean, "team-from-env")
        self.assertEqual(issue["input"]["description"], clean)
        self.assertNotIn("student", json.dumps(issue).lower())
        redacted = redact_real_roster(
            {"prompt": "Maple", "stem": "Maple syrup is not a name here"},
            {"Maple"},
        )
        self.assertEqual(redacted["prompt"], "")
        self.assertIn("Maple syrup", redacted["stem"])

    def test_route_hides_pin_from_response_and_logs(self) -> None:
        """A note that contains a PIN is a generic 400 and is not logged."""
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        school = app.config["SCHOOL_DB"]
        school.activate_from_semester_json()
        teacher = school.register_staff("teacher@gmail.com")
        summary = apply_stmtg_tester(school, teacher["email"], str(app.secret_key or "secret"))
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["student_offering_id"] = int(summary["offering_id"])
            sess["student_class_id"] = int(summary["class_id"])
            sess["student_id"] = int(summary["student_id"])
        records: list[str] = []

        class _Grab(logging.Handler):
            """Collect log messages without printing them."""

            def emit(self, record: logging.LogRecord) -> None:
                records.append(record.getMessage())

        handler = _Grab()
        logger = logging.getLogger("tester_feedback")
        logger.addHandler(handler)
        try:
            rv = client.post(
                "/student/tester-feedback",
                json={"text": "Please reset 4821 for me"},
            )
        finally:
            logger.removeHandler(handler)
            school.close()
            tmp.cleanup()
        self.assertEqual(rv.status_code, 400)
        body = rv.get_data(as_text=True)
        self.assertNotIn("4821", body)
        self.assertNotIn("4821", "\n".join(records))
        self.assertIn("Leave out PINs", body)

    def test_linear_payload_is_only_the_note(self) -> None:
        """A clean note is the issue description. A missing key does not log the note."""
        note = "The join screen was slow on my phone."
        seen: dict[str, object] = {}

        def transport(url: str, body: bytes, headers: dict[str, str]) -> dict:
            """Capture the GraphQL body and pretend Linear accepted it."""
            seen["url"] = url
            seen["body"] = json.loads(body.decode("utf-8"))
            self.assertNotIn(note, " ".join(headers.values()))
            return {"data": {"issueCreate": {"success": True}}}

        old_key = os.environ.get("LINEAR_API_KEY")
        old_token = os.environ.get("LINEAR_API_TOKEN")
        old_team = os.environ.get("LINEAR_TEAM_ID")
        os.environ["LINEAR_API_KEY"] = "test-key-not-a-pin"
        os.environ["LINEAR_TEAM_ID"] = "team-from-env"
        os.environ.pop("LINEAR_API_TOKEN", None)
        try:
            result = post_tester_feedback(note, forbidden_names={"Maple"}, transport=transport)
            self.assertEqual(result, {"ok": True})
            description = seen["body"]["variables"]["input"]["description"]
            self.assertEqual(description, note)
            dumped = json.dumps(seen["body"])
            self.assertNotIn("Maple", dumped)
            self.assertNotIn("MCR3U", dumped)
            self.assertNotIn("MCF3M", dumped)
            os.environ.pop("LINEAR_API_KEY", None)
            with self.assertLogs("tester_feedback", level="INFO") as caught:
                with self.assertRaises(ValueError):
                    post_tester_feedback(note, transport=transport)
            self.assertTrue(any("chars=" in line for line in caught.output))
            self.assertNotIn(note, "\n".join(caught.output))
        finally:
            if old_key is None:
                os.environ.pop("LINEAR_API_KEY", None)
            else:
                os.environ["LINEAR_API_KEY"] = old_key
            if old_token is None:
                os.environ.pop("LINEAR_API_TOKEN", None)
            else:
                os.environ["LINEAR_API_TOKEN"] = old_token
            if old_team is None:
                os.environ.pop("LINEAR_TEAM_ID", None)
            else:
                os.environ["LINEAR_TEAM_ID"] = old_team


class SeedScriptTests(unittest.TestCase):
    """The seed script is dry-run by default and never prints a PIN."""

    def test_dry_run_writes_nothing_and_apply_stays_on_stmtg(self) -> None:
        """Dry-run leaves STMTG unbuilt. Apply flags Tester and leaves MCF3M alone."""
        seed = _load_seed()
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        db_path = root / "lloves.sqlite"
        app = create_app(db_path=db_path, data_dir=root, testing=True)
        school = app.config["SCHOOL_DB"]
        school.activate_from_semester_json()
        teacher = school.register_staff("teacher@gmail.com")
        offering = school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="MCF3M"
        )
        school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCF3M",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Maple"],
            offering_id=int(offering["id"]),
            teacher_user_id=int(teacher["id"]),
        )
        school.close()

        dry = seed.main(["--teacher-email", "teacher@gmail.com", "--db", str(db_path)])
        self.assertEqual(dry, 0)
        app = create_app(db_path=db_path, data_dir=root, testing=True)
        school = app.config["SCHOOL_DB"]
        self.assertIsNone(school.get_course("STMTG"))
        school.close()

        with self.assertRaises(ValueError):
            assert_tester_course("MCR3U")

        old = sys.stdout
        from io import StringIO

        buf = StringIO()
        sys.stdout = buf
        try:
            code = seed.main(
                ["--apply", "--teacher-email", "teacher@gmail.com", "--db", str(db_path)]
            )
        finally:
            sys.stdout = old
        text = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIsNone(_PIN.search(text))
        app = create_app(db_path=db_path, data_dir=root, testing=True)
        school = app.config["SCHOOL_DB"]
        try:
            semester = school.get_active_semester()
            offering = school.get_offering_for(
                int(semester["id"]), "STMTG", int(teacher["id"])
            )
            self.assertIsNotNone(offering)
            classes = school.classes_for_offering(int(offering["id"]))
            self.assertEqual(len(classes), 1)
            class_id = int(classes[0]["id"])
            ids = school.test_student_ids(class_id)
            self.assertEqual(len(ids), 1)
            self.assertTrue(school.is_test_student(ids[0]))
            mcf = school.game.conn.execute(
                """
                SELECT COUNT(*) FROM students s
                JOIN classes c ON c.id = s.class_id
                WHERE c.course_code = 'MCF3M' AND COALESCE(s.is_test_student, 0) = 1
                """
            ).fetchone()
            self.assertEqual(int(mcf[0]), 0)
            names = school.class_codenames(class_id, include_test_students=True)
            self.assertEqual(set(names.values()), {"Tester"})
        finally:
            school.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()

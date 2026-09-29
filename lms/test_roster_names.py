#!/usr/bin/env python3
"""Staff can rename a student on any course roster they can open."""

from __future__ import annotations

import os
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


class _Presence:
    """In-memory stand-in for the Postgres presence store."""

    def __init__(self) -> None:
        """Start with no mirrored rows."""
        self.sessions: list[dict] = []
        self.by_id: dict[int, dict] = {}

    def upsert_session(self, row: dict) -> None:
        """Record a session mirror.

        Args:
            row: ``live_class_sessions`` mapping.
        """
        self.sessions.append(dict(row))

    def upsert_attendee(self, row: dict) -> None:
        """Record an attendee mirror.

        Args:
            row: ``live_session_attendees`` mapping.
        """
        self.by_id[int(row["id"])] = dict(row)

    def get_by_id(self, attendee_id: int) -> dict | None:
        """Return one mirrored attendee.

        Args:
            attendee_id: ``live_session_attendees.id``.
        """
        found = self.by_id.get(int(attendee_id))
        return dict(found) if found else None

    def sweep(self, session_id: int, **_kwargs: object) -> int:
        """Skip the heartbeat sweep. The rename test does not age anyone out.

        Args:
            session_id: ``live_class_sessions.id``.
            **_kwargs: Stale-window arguments the real store accepts.
        """
        _ = session_id
        return 0

    def close(self) -> None:
        """Match SchoolDB.close, which closes the presence pool."""


class RosterNameEditTests(unittest.TestCase):
    """Rename route, roster HTML, and live-class name propagation."""

    def setUp(self) -> None:
        """Isolated app with one teacher and an MCF3M roster."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        self._login(self.client, "teacher@gmail.com")
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple", "Aspen"],
            },
        )
        self.assertEqual(created.status_code, 200, created.get_json())
        self.class_id = int(created.get_json()["class"]["id"])
        self.maple = self.school.game.find_student_by_codename(self.class_id, "Maple")
        assert self.maple is not None
        self.maple_id = int(self.maple["id"])

    def tearDown(self) -> None:
        """Close the database and remove the temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _login(self, client, email: str) -> None:
        """Sign in through the offline staff Google callback.

        Args:
            client: Flask test client.
            email: Staff address already registered.
        """
        client.get("/auth/google?portal=staff")
        client.get(f"/auth/google/callback?email={email}&name=T")
        client.post(
            "/verify-email",
            data={"code": self.school.get_user_by_email(email)["verification_code"]},
        )

    def _rename(self, client, class_id: int, student_id: int, body: dict):
        """POST a roster name edit.

        Args:
            client: Flask test client.
            class_id: Class to edit.
            student_id: Roster row.
            body: JSON name fields.
        """
        return client.post(
            f"/api/classes/{class_id}/students/{student_id}/name",
            json=body,
        )

    def test_authorized_staff_renames_and_leaves_identity_and_scores(self) -> None:
        """A teacher can rename; email, points, and attendance stay put."""
        self.school.game.conn.execute(
            """
            UPDATE session_scores
            SET points = 4, present = 1
            WHERE student_id = ?
            """,
            (self.maple_id,),
        )
        self.school.game.conn.commit()
        before_scores = [
            tuple(row)
            for row in self.school.game.conn.execute(
                """
                SELECT session_id, points, present
                FROM session_scores WHERE student_id = ?
                ORDER BY session_id
                """,
                (self.maple_id,),
            ).fetchall()
        ]
        self.assertTrue(before_scores)
        teacher_before = dict(self.school.get_user_by_email("teacher@gmail.com"))
        canvas_before = self.maple["canvas_id"]
        rv = self._rename(
            self.client,
            self.class_id,
            self.maple_id,
            {"first_name": "  River  ", "display_name": "  River  "},
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        body = rv.get_json()
        self.assertEqual(body["name"], "River")
        self.assertEqual(body["first_name"], "River")
        self.assertEqual(body["display_name"], "River")
        self.assertEqual(body["last_name"], "")
        updated = self.school.game.get_student(self.class_id, self.maple_id)
        self.assertEqual(updated["codename"], "River")
        self.assertEqual(updated["canvas_id"], canvas_before)
        after_scores = [
            tuple(row)
            for row in self.school.game.conn.execute(
                """
                SELECT session_id, points, present
                FROM session_scores WHERE student_id = ?
                ORDER BY session_id
                """,
                (self.maple_id,),
            ).fetchall()
        ]
        self.assertEqual(after_scores, before_scores)
        teacher_after = self.school.get_user_by_email("teacher@gmail.com")
        self.assertEqual(teacher_after["email"], teacher_before["email"])
        self.assertEqual(teacher_after["display_name"], teacher_before["display_name"])
        self.assertEqual(teacher_after.get("google_sub"), teacher_before.get("google_sub"))

    def test_rename_works_on_a_second_course(self) -> None:
        """The same route edits MCR3U, not only the first course."""
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
        )
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": offering["id"],
                "days": "T/Th/F",
                "time": "2:00pm",
                "codenames": ["Birch"],
            },
        )
        self.assertEqual(created.status_code, 200, created.get_json())
        class_id = int(created.get_json()["class"]["id"])
        student = self.school.game.find_student_by_codename(class_id, "Birch")
        assert student is not None
        rv = self._rename(
            self.client,
            class_id,
            int(student["id"]),
            {"first_name": "Cedar", "display_name": "Cedar"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        self.assertEqual(rv.get_json()["name"], "Cedar")
        self.assertEqual(
            self.school.game.get_student(class_id, int(student["id"]))["codename"],
            "Cedar",
        )

    def test_staff_without_course_access_is_forbidden(self) -> None:
        """A teacher who cannot open the class cannot rename its students."""
        self.school.register_staff("other@gmail.com")
        outsider = self.app.test_client()
        self._login(outsider, "other@gmail.com")
        rv = self._rename(
            outsider,
            self.class_id,
            self.maple_id,
            {"display_name": "River"},
        )
        self.assertEqual(rv.status_code, 403)
        self.assertEqual(rv.get_json()["error"], "Forbidden")
        self.assertEqual(
            self.school.game.get_student(self.class_id, self.maple_id)["codename"],
            "Maple",
        )

    def test_student_and_logged_out_cannot_rename(self) -> None:
        """A joined student and an anonymous client are not staff."""
        run = self.client.post(
            f"/staff/class/{self.class_id}/run-live", follow_redirects=False
        )
        self.assertEqual(run.status_code, 302)
        live = self.school.get_active_live_session_for_class(self.class_id)
        assert live is not None
        student = self.app.test_client()
        joined = student.post(
            "/auth/student-code",
            data={"code": live["session_code"], "name": "Maple"},
            follow_redirects=False,
        )
        self.assertEqual(joined.status_code, 302)
        denied = self._rename(
            student,
            self.class_id,
            self.maple_id,
            {"display_name": "River"},
        )
        self.assertEqual(denied.status_code, 401)
        self.assertIn("Authentication", denied.get_json()["error"])
        anonymous = self.app.test_client()
        logged_out = self._rename(
            anonymous,
            self.class_id,
            self.maple_id,
            {"display_name": "River"},
        )
        self.assertEqual(logged_out.status_code, 401)
        self.assertEqual(
            self.school.game.get_student(self.class_id, self.maple_id)["codename"],
            "Maple",
        )

    def test_rejects_empty_whitespace_and_too_long_names(self) -> None:
        """Blank, whitespace-only, and over-long names are 400."""
        empty = self._rename(
            self.client,
            self.class_id,
            self.maple_id,
            {"first_name": "", "display_name": ""},
        )
        self.assertEqual(empty.status_code, 400)
        self.assertIn("Enter", empty.get_json()["error"])
        blank = self._rename(
            self.client,
            self.class_id,
            self.maple_id,
            {"display_name": "   "},
        )
        self.assertEqual(blank.status_code, 400)
        self.assertIn("Enter", blank.get_json()["error"])
        whitespace_first = self._rename(
            self.client,
            self.class_id,
            self.maple_id,
            {"first_name": " \t "},
        )
        self.assertEqual(whitespace_first.status_code, 400)
        self.assertIn("first name", whitespace_first.get_json()["error"])
        too_long = self._rename(
            self.client,
            self.class_id,
            self.maple_id,
            {"display_name": "R" * 81},
        )
        self.assertEqual(too_long.status_code, 400)
        self.assertIn("80", too_long.get_json()["error"])
        last_long = self._rename(
            self.client,
            self.class_id,
            self.maple_id,
            {"first_name": "Ada", "last_name": "L" * 81},
        )
        self.assertEqual(last_long.status_code, 400)
        self.assertIn("80", last_long.get_json()["error"])
        self.assertEqual(
            self.school.game.get_student(self.class_id, self.maple_id)["codename"],
            "Maple",
        )

    def test_duplicate_name_is_rejected(self) -> None:
        """A display name already on the roster is not saved."""
        rv = self._rename(
            self.client,
            self.class_id,
            self.maple_id,
            {"display_name": "aspen"},
        )
        self.assertEqual(rv.status_code, 400)
        self.assertIn("already", rv.get_json()["error"])
        self.assertEqual(
            self.school.game.get_student(self.class_id, self.maple_id)["codename"],
            "Maple",
        )

    def test_roster_html_shows_the_control_and_the_saved_name(self) -> None:
        """The course roster renders a pencil, then the new name after save."""
        home = self.client.get("/staff").get_data(as_text=True)
        self.assertIn("roster-name-pencil", home)
        self.assertIn("Edit name for Maple", home)
        self.assertIn(">Maple<", home)
        attendance = self.client.get(
            f"/staff/class/{self.class_id}?tab=ap&view=attendance"
        )
        self.assertEqual(attendance.status_code, 200)
        att_html = attendance.get_data(as_text=True)
        self.assertIn("roster-name-pencil", att_html)
        self.assertIn("Edit name for Maple", att_html)
        self.assertIn('name="first_name"', att_html)
        self.assertIn('name="display_name"', att_html)
        self.assertIn("roster-name-save", att_html)
        saved = self._rename(
            self.client,
            self.class_id,
            self.maple_id,
            {"first_name": "River", "display_name": "River"},
        )
        self.assertEqual(saved.status_code, 200)
        again = self.client.get(
            f"/staff/class/{self.class_id}?tab=ap&view=attendance"
        ).get_data(as_text=True)
        self.assertIn("Edit name for River", again)
        self.assertIn(">River<", again)
        self.assertNotIn("Edit name for Maple", again)
        home_again = self.client.get("/staff").get_data(as_text=True)
        self.assertIn("Edit name for River", home_again)
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        pencil = css.split("body.staff-shell .roster-name-pencil", 1)[1]
        self.assertIn("min-height: 44px", css)
        self.assertIn("min-width: 44px", pencil[:400])
        self.assertIn(":focus-visible", css[css.find(".roster-name-label") :])

    def test_rename_propagates_to_class_list_attendee_and_student_view(self) -> None:
        """Live class reads the new name, including a stale presence copy."""
        run = self.client.post(
            f"/staff/class/{self.class_id}/run-live", follow_redirects=False
        )
        self.assertEqual(run.status_code, 302)
        live = self.school.get_active_live_session_for_class(self.class_id)
        assert live is not None
        session_id = int(live["id"])
        student = self.app.test_client()
        joined = student.post(
            "/auth/student-code",
            data={"code": live["session_code"], "name": "Maple"},
            follow_redirects=False,
        )
        self.assertEqual(joined.status_code, 302)
        people = self.school.list_live_session_attendees(session_id)
        maple = next(row for row in people if int(row["student_id"]) == self.maple_id)
        presence = _Presence()
        presence.by_id[int(maple["id"])] = {**maple, "codename": "Maple"}
        self.school.presence = presence
        rv = self._rename(
            self.client,
            self.class_id,
            self.maple_id,
            {"first_name": "River", "display_name": "River"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        roster = self.school.live_class_roster_projection(session_id)
        maple_row = next(
            row for row in roster if int(row["student_id"]) == self.maple_id
        )
        self.assertEqual(maple_row["codename"], "River")
        state = self.client.get(f"/api/live-sessions/{session_id}/state")
        self.assertEqual(state.status_code, 200, state.get_json())
        listed = state.get_json().get("class_list") or []
        listed_name = next(
            row["codename"]
            for row in listed
            if int(row["student_id"]) == self.maple_id
        )
        self.assertEqual(listed_name, "River")
        attendees = state.get_json().get("attendees") or []
        attendee_name = next(
            row["codename"]
            for row in attendees
            if int(row.get("student_id") or 0) == self.maple_id
        )
        self.assertEqual(attendee_name, "River")
        self.assertEqual(presence.by_id[int(maple["id"])]["codename"], "River")
        self.school.presence = None
        own = student.get("/api/student/state")
        self.assertEqual(own.status_code, 200, own.get_json())
        self.assertEqual((own.get_json().get("me") or {})["codename"], "River")


if __name__ == "__main__":
    unittest.main()

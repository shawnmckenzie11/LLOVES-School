#!/usr/bin/env python3
"""Join and leave still reach the right phones, and only those phones.

A take-turns item is open, so presence is part of the student poll stamp.
One student joining or leaving moves that student's teammates and the
teacher's present count. The other team stays on its old stamp.

``present_rev_for_students`` is the Postgres form of that same token. It
is checked against Postgres, not only the sqlite fallback.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

LMS_DIR = Path(__file__).resolve().parent
REPO = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402

# Whole-second timestamps need a gap past one second before the action.
GAP_S = 1.15


def _pg_admin_url() -> str:
    """Postgres admin URL for presence tests, or empty when unset."""
    return os.environ.get("LIVE_PRESENCE_TEST_URL", "").strip()


class JoinLeaveProbe(unittest.TestCase):
    """Two teams. A late teammate joins, then leaves."""

    def _presence_url(self) -> str | None:
        """Presence DSN. Sqlite when this returns None."""
        return None

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "school.sqlite",
            data_dir=root,
            testing=True,
            live_database_url=self._presence_url(),
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.staff = self.app.test_client()
        self.school.activate_from_semester_json()
        course = self.school.conn.execute(
            "SELECT code FROM ontario_courses ORDER BY code LIMIT 1"
        ).fetchone()
        self.assertIsNotNone(course)
        code = str(course["code"])
        teacher = self.school.register_staff("staff@example.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code=code
        )
        self.seats = [f"seat{n:02d}" for n in range(1, 7)]
        self.class_id = int(
            self.school.game.create_class(
                year="2026/27",
                semester="Semester 1",
                course_code=code,
                days_preset="M/W/F",
                time_label="2:00pm",
                codenames=self.seats,
                offering_id=int(offering["id"]),
                teacher_user_id=int(teacher["id"]),
            )["id"]
        )
        self.staff.get("/auth/google?portal=staff")
        self.staff.get("/auth/google/callback?email=staff@example.com&name=Staff")
        verify = self.school.get_user_by_email("staff@example.com")["verification_code"]
        self.staff.post("/verify-email", data={"code": verify})
        live = self.school.start_live_class_session(self.class_id, int(teacher["id"]))
        self.session_id = int(live["id"])
        self.session_code = str(live["session_code"])
        self.staff.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={
                "live_module": "M1",
                "live_slot": "C2",
                "stage": "round",
                "page_id": "round_1",
            },
        )
        self.school.game.begin_game(self.class_id)
        with self.school.game._lock:
            rows = self.school.game.conn.execute(
                "SELECT id, codename FROM students WHERE class_id = ? ORDER BY id",
                (self.class_id,),
            ).fetchall()
        by_name = {str(row["codename"]): int(row["id"]) for row in rows}
        self.ids = [by_name[seat] for seat in self.seats]
        self.phones: dict[int, Any] = {}
        # Seat 2 is on team A but has not joined yet.
        for index in (0, 1, 3, 4, 5):
            self._enter(index)
        self.school.setup_live_session_groups(
            self.session_id,
            n_teams=2,
            mode="manual",
            present_ids=self.ids,
            assignments=[
                {"student_id": sid, "team_index": 0 if i < 3 else 1}
                for i, sid in enumerate(self.ids)
            ],
        )
        self.school.ensure_live_session_items(self.session_id)
        self._open_turns()

    def tearDown(self) -> None:
        self.school.close()
        self.tmp.cleanup()

    def _enter(self, index: int) -> None:
        """Join one seat and keep that phone."""
        client = self.app.test_client()
        rv = client.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": self.seats[index]},
        )
        self._ok(rv, 302)
        self.phones[index] = client

    def _ok(self, rv: Any, code: int) -> None:
        """Fail with the status only. Do not include a session code."""
        if rv.status_code == code:
            return
        err = ""
        body = rv.get_json(silent=True)
        if isinstance(body, dict):
            err = str(body.get("error") or "")[:160]
        self.fail(f"status {rv.status_code} {err}")

    def _open_turns(self) -> None:
        """Publish one take-turns rank so presence is in the poll stamp."""
        rv = self.staff.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/add-question",
            json={
                "page_number": 4,
                "stage": "round",
                "type": "rank",
                "text": "Order these",
                "options": ["Red", "Blue", "Green", "Gold"],
                "rank_key": [0, 1, 2, 3],
            },
        )
        self._ok(rv, 200)
        item_id = rv.get_json()["placement"]["item"]["id"]
        row = next(
            row
            for row in self.school.list_live_session_items(self.session_id)
            if str(row.get("item_id") or "") == str(item_id)
            and str(row.get("status") or "") != "closed"
        )
        rv = self.staff.patch(
            f"/api/live-sessions/{self.session_id}/items/{row['id']}/settings",
            json={"group_rank_mode": "turns"},
        )
        self._ok(rv, 200)
        rv = self.staff.post(
            f"/api/live-sessions/{self.session_id}/items/{row['id']}/publish",
            json={"publish_mode": "group_submit"},
        )
        self._ok(rv, 200)

    def _snap(self, index: int) -> tuple[Any, Any, str]:
        """Poll seq, poll stamp, and the direct per-student stamp."""
        rv = self.phones[index].get("/api/student/state")
        self._ok(rv, 200)
        body = rv.get_json() or {}
        direct = self.school.live_student_poll_stamp(
            self.session_id, self.class_id, self.ids[index]
        )
        return body.get("state_seq"), body.get("stamp"), direct

    def _assert_moved(self, index: int, snap: tuple[Any, Any, str], label: str) -> None:
        """This phone rebuilt and its direct stamp changed."""
        seq, stamp, direct = snap
        rv = self.phones[index].get(
            "/api/student/state",
            query_string={"seq": seq, "stamp": stamp},
        )
        self._ok(rv, 200)
        body = rv.get_json() or {}
        self.assertFalse(body.get("unchanged"), label)
        now = self.school.live_student_poll_stamp(
            self.session_id, self.class_id, self.ids[index]
        )
        self.assertNotEqual(now, direct, label)

    def _assert_same(self, index: int, snap: tuple[Any, Any, str], label: str) -> None:
        """This phone's poll and direct stamp did not move."""
        seq, stamp, direct = snap
        rv = self.phones[index].get(
            "/api/student/state",
            query_string={"seq": seq, "stamp": stamp},
        )
        self._ok(rv, 200)
        body = rv.get_json() or {}
        self.assertTrue(body.get("unchanged"), label)
        now = self.school.live_student_poll_stamp(
            self.session_id, self.class_id, self.ids[index]
        )
        self.assertEqual(now, direct, label)

    def _teacher_present(self) -> tuple[int, set[int], str]:
        """Present count, present roster ids, and the class-wide stamp.

        The class-wide stamp is what a poll with no student id sees. The
        teacher roster is the present count on staff state.
        """
        rv = self.staff.get(f"/api/live-sessions/{self.session_id}/state")
        self._ok(rv, 200)
        body = rv.get_json() or {}
        present = {
            int(row["student_id"])
            for row in body.get("attendees") or []
            if isinstance(row, dict)
            and not row.get("left_at")
            and row.get("student_id") not in (None, "")
        }
        stamp = self.school.live_student_poll_stamp(self.session_id, self.class_id)
        return int(body.get("count") or 0), present, stamp

    def test_join_and_leave_update_teammates_and_teacher(self) -> None:
        """A join and a leave move team A and the teacher. Team B stays."""
        mates = (0, 1)
        others = (3, 4, 5)
        before = {index: self._snap(index) for index in (*mates, *others)}
        teacher_before = self._teacher_present()
        self.assertNotIn(self.ids[2], teacher_before[1])
        time.sleep(GAP_S)
        self._enter(2)
        for index in mates:
            self._assert_moved(index, before[index], "join mate")
        for index in others:
            self._assert_same(index, before[index], "join other")
        count, present, stamp = self._teacher_present()
        self.assertIn(self.ids[2], present)
        self.assertEqual(count, teacher_before[0] + 1)
        self.assertNotEqual(stamp, teacher_before[2])

        before = {index: self._snap(index) for index in (*mates, *others)}
        teacher_before = self._teacher_present()
        time.sleep(GAP_S)
        self.school.mark_live_session_attendee_left(
            self.session_id, student_id=self.ids[2]
        )
        for index in mates:
            self._assert_moved(index, before[index], "leave mate")
        for index in others:
            self._assert_same(index, before[index], "leave other")
        count, present, stamp = self._teacher_present()
        self.assertNotIn(self.ids[2], present)
        self.assertEqual(count, teacher_before[0] - 1)
        self.assertNotEqual(stamp, teacher_before[2])


@unittest.skipUnless(_pg_admin_url(), "LIVE_PRESENCE_TEST_URL is not set")
class JoinLeavePostgresProbe(JoinLeaveProbe):
    """The same join and leave with Postgres as the presence store."""

    def _presence_url(self) -> str | None:
        """Throwaway database on the presence admin URL."""
        import psycopg

        admin = _pg_admin_url()
        self.pg_db = f"lloves_t308_{uuid.uuid4().hex[:8]}"
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{self.pg_db}"')
        self.addCleanup(self._drop_pg, admin)
        parts = urlsplit(admin)
        return urlunsplit((parts.scheme, parts.netloc, f"/{self.pg_db}", parts.query, ""))

    def _drop_pg(self, admin: str) -> None:
        """Drop the throwaway presence database."""
        import psycopg

        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{self.pg_db}" WITH (FORCE)')

    def test_presence_store_is_postgres(self) -> None:
        """This probe is not silently falling back to sqlite."""
        self.assertEqual(self.school.live_presence_status(), "postgres")


@unittest.skipUnless(_pg_admin_url(), "LIVE_PRESENCE_TEST_URL is not set")
class PresenceRevPostgresTests(unittest.TestCase):
    """``present_rev_for_students`` against Postgres."""

    def setUp(self) -> None:
        import psycopg

        from live_presence import LivePresenceStore

        admin = _pg_admin_url()
        self.pg_db = f"lloves_t308q_{uuid.uuid4().hex[:8]}"
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{self.pg_db}"')
        parts = urlsplit(admin)
        dsn = urlunsplit((parts.scheme, parts.netloc, f"/{self.pg_db}", parts.query, ""))
        self.store = LivePresenceStore(dsn, pool_size=2)
        self.addCleanup(self._close, admin)
        self.session_id = 910308
        self.store.upsert_session(
            {
                "id": self.session_id,
                "class_id": 1,
                "status": "active",
                "started_at": "2026-10-21T12:00:00",
                "ended_at": None,
            }
        )
        # Two roster ids on one side, one on the other.
        self.side_a = (11, 12)
        self.side_b = (21,)
        for attendee_id, student_id in ((101, 11), (102, 12), (103, 21)):
            self.store.upsert_attendee(
                {
                    "id": attendee_id,
                    "live_session_id": self.session_id,
                    "student_id": student_id,
                    "participant_uuid": f"p{attendee_id}",
                    "visit_token": f"v{attendee_id}",
                    "codename": f"s{student_id}",
                    "unmatched": 0,
                    "joined_at": "2026-10-21T12:00:00",
                    "left_at": None,
                    "last_heartbeat_at": "2026-10-21T12:00:00",
                }
            )

    def _close(self, admin: str) -> None:
        """Close the pool and drop the throwaway database."""
        import psycopg

        self.store.close()
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{self.pg_db}" WITH (FORCE)')

    def test_listed_students_ignore_another_side(self) -> None:
        """A leave on the other side does not move this side's token."""
        before = self.store.present_rev_for_students(self.session_id, self.side_a)
        whole = self.store.present_rev(self.session_id)
        self.assertEqual(
            self.store.present_rev_for_students(
                self.session_id, (*self.side_a, *self.side_b)
            ),
            whole,
        )
        self.store.mark_left(103, "2026-10-21T12:05:00")
        self.assertEqual(
            self.store.present_rev_for_students(self.session_id, self.side_a),
            before,
        )
        self.assertNotEqual(self.store.present_rev(self.session_id), whole)
        self.store.mark_left(101, "2026-10-21T12:06:00")
        self.assertNotEqual(
            self.store.present_rev_for_students(self.session_id, self.side_a),
            before,
        )

    def test_empty_list_is_a_stable_zero(self) -> None:
        """No roster ids means an empty present set, not the whole class."""
        self.assertEqual(self.store.present_rev_for_students(self.session_id, []), "0.0.0")
        self.assertNotEqual(self.store.present_rev(self.session_id), "0.0.0")

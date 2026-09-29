"""Presence pool recycle and poll degrade when Postgres idle-times out.

Fly Managed Postgres (PgBouncer) closes an idle client with
``ProtocolViolation: client_idle_timeout``. The student visit-token gate
runs before the view, so that error used to become Flask's HTML 500 on
``GET /api/student/state`` and stall the gunicorn threads.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
if str(LMS_DIR) not in sys.path:
    sys.path.insert(0, str(LMS_DIR))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

import psycopg  # noqa: E402
from live_presence import (  # noqa: E402
    LivePresenceStore,
    LivePresenceUnavailable,
    _POOL_WAIT_SECONDS,
)
from app import create_app  # noqa: E402


class _Cursor:
    """Stand-in for the cursor ``execute`` returns."""

    def __init__(self, name: str) -> None:
        self.name = name

    def fetchone(self) -> dict[str, Any]:
        """Return one fake row."""
        return {"name": self.name}

    def fetchall(self) -> list[dict[str, Any]]:
        """Return no rows."""
        return []


class _Conn:
    """In-memory connection with a controllable ``execute``."""

    def __init__(
        self,
        name: str,
        *,
        error: BaseException | None = None,
    ) -> None:
        self.name = name
        self.error = error
        self.closed = False
        self.calls = 0

    def execute(self, query: str, params: Any = None, **kwargs: Any) -> _Cursor:
        """Record the call or raise the scripted driver error.

        Args:
            query: Ignored SQL.
            params: Ignored binds.
            **kwargs: Ignored psycopg options.
        """
        del query, params, kwargs
        self.calls += 1
        if self.error is not None:
            raise self.error
        return _Cursor(self.name)

    def close(self) -> None:
        """Mark the socket closed."""
        self.closed = True


def _bare_store(connect: Any) -> LivePresenceStore:
    """Build a store that never dials Postgres.

    Args:
        connect: Replacement for ``LivePresenceStore._connect``.
    """
    store = LivePresenceStore.__new__(LivePresenceStore)
    store.dsn = "postgresql://unused"
    store.schema = None
    store.heartbeat_writes = 0
    store._pool_size = 2
    store._sem = threading.BoundedSemaphore(2)
    store._idle = []
    store._idle_lock = threading.Lock()
    store._sweep_at = {}
    store._sweep_lock = threading.Lock()
    store._ping_at = 0.0
    store._ping_ok = False
    store._closed = False
    store._connect = connect  # type: ignore[method-assign]
    return store


class PresencePoolTests(unittest.TestCase):
    """Idle sockets are dropped. A protocol error is retried once."""

    def test_idle_connection_is_replaced_before_use(self) -> None:
        """A sibling idle longer than the pooler window is not checked out."""
        stale = _Conn("stale")
        fresh = _Conn("fresh")
        opened: list[str] = []

        def connect() -> _Conn:
            """Open the replacement the pool should use."""
            opened.append(fresh.name)
            return fresh

        store = _bare_store(connect)
        store._idle.append((stale, time.monotonic() - 120))
        with store._conn() as conn:
            row = conn.execute("SELECT 1").fetchone()
        self.assertEqual(row["name"], "fresh")
        self.assertEqual(opened, ["fresh"])
        self.assertTrue(stale.closed)
        self.assertFalse(fresh.closed)
        self.assertEqual(fresh.calls, 1)
        with store._conn() as conn:
            again = conn.execute("SELECT 1").fetchone()
        self.assertEqual(again["name"], "fresh")
        self.assertEqual(opened, ["fresh"])
        self.assertEqual(fresh.calls, 2)

    def test_client_idle_timeout_retries_on_a_new_connection(self) -> None:
        """``ProtocolViolation: client_idle_timeout`` runs the statement again."""
        dead = _Conn(
            "dead",
            error=psycopg.errors.ProtocolViolation("client_idle_timeout"),
        )
        live = _Conn("live")
        opened: list[str] = []

        def connect() -> _Conn:
            """Supply the connection used after the dead one is discarded."""
            opened.append("live")
            return live

        store = _bare_store(connect)
        store._idle.append((dead, time.monotonic()))
        with store._conn() as conn:
            row = conn.execute("SELECT 1").fetchone()
        self.assertEqual(row["name"], "live")
        self.assertEqual(opened, ["live"])
        self.assertTrue(dead.closed)
        self.assertEqual(live.calls, 1)
        self.assertFalse(live.closed)

    def test_constraint_error_is_not_retried(self) -> None:
        """A unique violation is a real failure, not a dead socket."""
        bad = _Conn("bad", error=psycopg.errors.UniqueViolation("dup"))
        opened: list[str] = []

        def connect() -> _Conn:
            """Must not run. The statement is not retried."""
            opened.append("nope")
            return _Conn("nope")

        store = _bare_store(connect)
        store._idle.append((bad, time.monotonic()))
        with self.assertRaises(LivePresenceUnavailable):
            with store._conn() as conn:
                conn.execute("INSERT INTO live_presence_attendees DEFAULT VALUES")
        self.assertEqual(opened, [])
        self.assertTrue(bad.closed)

    def test_pool_wait_fails_fast(self) -> None:
        """A stuck checkout does not hold the caller for the worker timeout."""
        store = _bare_store(lambda: _Conn("unused"))
        store._sem = threading.BoundedSemaphore(1)
        self.assertTrue(store._sem.acquire(timeout=0.1))
        started = time.monotonic()
        import live_presence as presence_mod

        previous = presence_mod._POOL_WAIT_SECONDS
        presence_mod._POOL_WAIT_SECONDS = 0.05
        try:
            with self.assertRaises(LivePresenceUnavailable) as caught:
                with store._conn() as conn:
                    conn.execute("SELECT 1")
        finally:
            presence_mod._POOL_WAIT_SECONDS = previous
            store._sem.release()
        self.assertIn("pool busy", str(caught.exception))
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertGreater(_POOL_WAIT_SECONDS, 0)


class _DownPresence:
    """Presence store whose every call is the idle-timeout failure."""

    def get_by_id(self, attendee_id: int) -> dict[str, Any]:
        """Fail the read overlay.

        Args:
            attendee_id: Ignored primary key.
        """
        del attendee_id
        raise LivePresenceUnavailable("live presence store unavailable")

    def get_by_token(self, token: str) -> dict[str, Any]:
        """Fail the heartbeat lookup.

        Args:
            token: Ignored visit token.
        """
        del token
        raise LivePresenceUnavailable("live presence store unavailable")

    def resume_if_stale(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Fail the heartbeat write."""
        del args, kwargs
        raise LivePresenceUnavailable("live presence store unavailable")

    def sweep(self, *args: Any, **kwargs: Any) -> int:
        """Fail the stale sweep."""
        del args, kwargs
        raise LivePresenceUnavailable("live presence store unavailable")

    def present_count(self, session_id: int) -> int:
        """Fail the headcount.

        Args:
            session_id: Ignored session id.
        """
        del session_id
        raise LivePresenceUnavailable("live presence store unavailable")

    def close(self) -> None:
        """Match the real store so app teardown can call this."""


class PresencePollDegradeTests(unittest.TestCase):
    """Student and staff polls stay JSON 200 when presence is down."""

    def setUp(self) -> None:
        """One joined student on sqlite, then a presence store that fails."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
            live_database_url="",
        )
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        self.school = self.app.config["SCHOOL_DB"]
        self.staff = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        self.staff.get("/auth/google?portal=staff")
        self.staff.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.staff.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCF3M",
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=["Maple"],
            offering_id=int(self.offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )
        self.class_id = int(created["id"])
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])
        self.student = self.app.test_client()
        joined = self.student.post(
            "/auth/student-code",
            data={"code": live["session_code"], "name": "Maple"},
            follow_redirects=False,
        )
        self.assertEqual(joined.status_code, 302, joined.get_data(as_text=True)[:300])
        self.student.post("/student/mood", data={"mood": "good"})
        self.student.post("/student/character", data={"character": "fox"})
        self.school.presence = _DownPresence()

    def tearDown(self) -> None:
        """Close sqlite and the temp dir."""
        school = getattr(self, "school", None)
        if school is not None:
            school.presence = None
            school.close()
        tmp = getattr(self, "tmp", None)
        if tmp is not None:
            tmp.cleanup()

    def test_student_state_is_json_retry_not_html_500(self) -> None:
        """The visit-token gate must not turn a presence blip into an HTML 500."""
        started = time.monotonic()
        rv = self.student.get("/api/student/state")
        elapsed = time.monotonic() - started
        body_text = rv.get_data(as_text=True)
        self.assertLess(elapsed, 1.0, body_text[:200])
        self.assertEqual(rv.status_code, 200, body_text[:400])
        self.assertIn("application/json", rv.content_type)
        self.assertNotIn("Internal Server Error", body_text)
        body = rv.get_json()
        self.assertEqual(body.get("error"), "state unavailable")
        self.assertTrue(body.get("retry"))

    def test_student_gate_blip_is_json_when_resolve_raises(self) -> None:
        """A raise in the decorator itself is the same JSON retry."""

        def boom(*_args: Any, **_kwargs: Any) -> None:
            """Simulate the pre-fix uncaught gate failure."""
            raise LivePresenceUnavailable("live presence store unavailable")

        self.school.resolve_student_visit_token = boom  # type: ignore[method-assign]
        rv = self.student.get("/api/student/state")
        body_text = rv.get_data(as_text=True)
        self.assertEqual(rv.status_code, 200, body_text[:400])
        self.assertIn("application/json", rv.content_type)
        self.assertNotIn("Internal Server Error", body_text)
        self.assertEqual(rv.get_json().get("retry"), True)

    def test_student_home_reload_stays_200(self) -> None:
        """A page reload renders. It does not 500 because presence blinked."""
        rv = self.student.get("/student/home")
        body_text = rv.get_data(as_text=True)
        self.assertEqual(rv.status_code, 200, body_text[:400])
        self.assertNotIn("Internal Server Error", body_text)
        self.assertIn("student-home", body_text)

    def test_landing_reload_keeps_rejoin_cookie(self) -> None:
        """Auto-resume failure shows landing and leaves the rejoin cookie."""
        from student_portal import REJOIN_COOKIE_NAME

        self.assertIsNotNone(self.student.get_cookie(REJOIN_COOKIE_NAME))
        rv = self.student.get("/")
        body_text = rv.get_data(as_text=True)
        self.assertEqual(rv.status_code, 200, body_text[:300])
        self.assertNotIn("Internal Server Error", body_text)
        self.assertIsNotNone(self.student.get_cookie(REJOIN_COOKIE_NAME))

    def test_staff_state_keeps_sqlite_roster(self) -> None:
        """Staff /state stays a real roster when sweep and overlay fail."""
        rv = self.staff.get(f"/api/live-sessions/{self.session_id}/state")
        body_text = rv.get_data(as_text=True)
        self.assertEqual(rv.status_code, 200, body_text[:400])
        body = rv.get_json()
        self.assertTrue(body.get("ok"), body)
        self.assertNotEqual(body.get("error"), "state unavailable")
        self.assertGreaterEqual(int(body.get("count") or 0), 1)


if __name__ == "__main__":
    unittest.main()


class PresenceBackfillRaceTests(unittest.TestCase):
    """Boot copies one attendee set without crashing on a duplicate token."""

    def test_parallel_backfill_serializes_on_the_advisory_lock(self) -> None:
        """Four threads copying the same attendee never insert at once."""
        shared = {"inserts": 0, "max": 0, "guard": threading.Lock(), "advisory": threading.Lock()}

        class Cursor:
            """One scripted result."""

            def __init__(self, row: dict[str, Any] | None) -> None:
                self.row = row

            def fetchone(self) -> dict[str, Any] | None:
                """Return the scripted row."""
                return self.row

        class Raw:
            """Postgres stand-in whose advisory lock is a thread lock."""

            def __init__(self) -> None:
                self.holding = False

            def transaction(self):
                """Act as psycopg's transaction context."""
                return self

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                if self.holding:
                    shared["advisory"].release()
                    self.holding = False
                return False

            def execute(self, query: str, params: Any = None, **kwargs: Any) -> Cursor:
                """Record inserts and hold the lock across the copy."""
                del params, kwargs
                if "pg_advisory_xact_lock" in query:
                    shared["advisory"].acquire()
                    self.holding = True
                    return Cursor(None)
                if "INSERT INTO live_presence_attendees" in query:
                    with shared["guard"]:
                        shared["inserts"] += 1
                        shared["max"] = max(shared["max"], shared["inserts"])
                    time.sleep(0.05)
                    with shared["guard"]:
                        shared["inserts"] -= 1
                    return Cursor({"id": 1, "visit_token": "tok", "live_session_id": 1, "student_id": 1, "unmatched": 0, "participant_uuid": "", "codename": "Aspen", "joined_at": "", "left_at": None, "last_heartbeat_at": None})
                if "INSERT INTO live_presence_sessions" in query:
                    return Cursor(None)
                if query.strip().startswith(("SAVEPOINT", "RELEASE", "ROLLBACK")):
                    return Cursor(None)
                raise AssertionError(query)

        def connect() -> Raw:
            """Open one fake connection."""
            return Raw()

        store = _bare_store(connect)
        db = sqlite3.connect(":memory:", check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute(
            """
            CREATE TABLE live_class_sessions (
                id INTEGER PRIMARY KEY, class_id INTEGER, status TEXT,
                started_at TEXT, ended_at TEXT
            )
            """
        )
        db.execute(
            """
            CREATE TABLE live_session_attendees (
                id INTEGER PRIMARY KEY, live_session_id INTEGER, student_id INTEGER,
                participant_uuid TEXT, visit_token TEXT, codename TEXT,
                unmatched INTEGER, joined_at TEXT, left_at TEXT, last_heartbeat_at TEXT
            )
            """
        )
        db.execute(
            "INSERT INTO live_class_sessions VALUES (1, 2, 'active', 't', NULL)"
        )
        db.execute(
            "INSERT INTO live_session_attendees VALUES (5, 1, 9, '', 'tok', 'Aspen', 0, 't', NULL, NULL)"
        )
        errors: list[BaseException] = []

        def copy() -> None:
            """Run the boot copy and remember unexpected errors."""
            try:
                store.backfill_active(db)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=copy) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertEqual(shared["max"], 1)
        self.assertFalse(shared["advisory"].locked())

    def test_visit_token_conflict_updates_instead_of_raising(self) -> None:
        """A duplicate visit token becomes an update, not a worker crash."""

        class Cursor:
            """One scripted result."""

            def __init__(self, row: dict[str, Any] | None) -> None:
                self.row = row

            def fetchone(self) -> dict[str, Any] | None:
                """Return the scripted row."""
                return self.row

        class Raw:
            """First attendee insert hits the visit_token unique index."""

            def __init__(self) -> None:
                self.inserts = 0

            def execute(self, query: str, params: Any = None, **kwargs: Any) -> Cursor:
                """Raise once, then accept the token update."""
                del kwargs
                if "INSERT INTO live_presence_attendees" in query:
                    self.inserts += 1
                    raise psycopg.errors.UniqueViolation("live_presence_attendees_token")
                if "UPDATE live_presence_attendees" in query:
                    return Cursor(
                        {
                            "id": 5,
                            "live_session_id": 1,
                            "student_id": 9,
                            "participant_uuid": "",
                            "visit_token": "tok",
                            "codename": "Aspen",
                            "unmatched": 0,
                            "joined_at": "t",
                            "left_at": None,
                            "last_heartbeat_at": None,
                        }
                    )
                raise AssertionError(query)

        raw = Raw()
        store = _bare_store(lambda: raw)
        stored = store.upsert_attendee(
            {
                "id": 5,
                "live_session_id": 1,
                "student_id": 9,
                "participant_uuid": "",
                "visit_token": "tok",
                "codename": "Aspen",
                "unmatched": 0,
                "joined_at": "t",
                "left_at": None,
                "last_heartbeat_at": None,
            }
        )
        self.assertEqual(stored["visit_token"], "tok")
        self.assertEqual(raw.inserts, 1)



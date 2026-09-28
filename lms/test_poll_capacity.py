"""Dead-session polls, poll budget, and the 1 GB gunicorn cap.

Alc hung with ~1000 ESTABLISHED sockets on one gunicorn worker (2 threads,
timeout 600) so ``/health`` returned zero bytes. These tests lock the tip
fix: one worker and eight threads, a small connection cap, gone Meets
return ended JSON without a traceback per field, and ``/health`` does not
ping presence.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
if str(LMS_DIR) not in sys.path:
    sys.path.insert(0, str(LMS_DIR))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

import gunicorn_conf  # noqa: E402
import serve_capacity as cap  # noqa: E402
from app import create_app  # noqa: E402
from live_presence import LivePresenceStore  # noqa: E402
from school_db import live_state_field  # noqa: E402
from serve_capacity import (  # noqa: E402
    POLL_IN_FLIGHT,
    THREADS,
    WORKER_CONNECTIONS,
    WORKERS,
    PollBudgetExceeded,
    is_live_state_poll,
    poll_slot,
)


class ServeCapacityTests(unittest.TestCase):
    """The image stays one worker on the 1 GB machine."""

    def test_one_worker_and_more_threads(self) -> None:
        """Eight threads, still one worker. Two workers do not fit this box."""
        self.assertEqual(WORKERS, 1)
        self.assertEqual(THREADS, 8)
        self.assertEqual(gunicorn_conf.workers, 1)
        self.assertEqual(gunicorn_conf.threads, 8)
        self.assertEqual(gunicorn_conf.worker_class, "gthread")
        self.assertLess(POLL_IN_FLIGHT, THREADS)
        self.assertEqual(gunicorn_conf.timeout, 600)

    def test_keepalive_and_connections_are_bounded(self) -> None:
        """Dead clients cannot accumulate gunicorn's default 1000 sockets."""
        self.assertLessEqual(WORKER_CONNECTIONS, 64)
        self.assertEqual(gunicorn_conf.worker_connections, WORKER_CONNECTIONS)
        self.assertGreater(WORKER_CONNECTIONS, THREADS)
        self.assertLessEqual(gunicorn_conf.keepalive, 2)
        self.assertEqual(gunicorn_conf.keepalive, cap.KEEPALIVE_SECONDS)
        self.assertLessEqual(gunicorn_conf.backlog, 128)
        dockerfile = (LMS_DIR / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("lms/gunicorn_conf.py", dockerfile)
        self.assertNotIn('"--threads", "2"', dockerfile)

    def test_poll_paths_and_budget_helpers(self) -> None:
        """Only the live ``/state`` polls close the socket."""
        self.assertTrue(is_live_state_poll("/api/student/state"))
        self.assertTrue(is_live_state_poll("/api/live-sessions/20/state"))
        self.assertFalse(is_live_state_poll("/health"))
        self.assertFalse(is_live_state_poll("/api/live-sessions/20/prompts"))
        self.assertFalse(cap.poll_budget_expired())
        cap._poll_budget.deadline = time.monotonic() - 1
        try:
            self.assertTrue(cap.poll_budget_expired())
            with self.assertRaises(PollBudgetExceeded):
                cap.ensure_poll_budget()
        finally:
            cap._poll_budget.deadline = None

    def test_poll_slot_sheds_when_full(self) -> None:
        """A full cap refuses another heavy poll without blocking."""
        held = 0
        try:
            for _ in range(POLL_IN_FLIGHT):
                self.assertTrue(cap._POLL_SLOTS.acquire(blocking=False))
                held += 1
            with poll_slot(enabled=True) as extra:
                self.assertFalse(extra)
            with poll_slot(enabled=False) as always:
                self.assertTrue(always)
        finally:
            for _ in range(held):
                cap._POLL_SLOTS.release()

    def test_field_reraises_gone_session_without_traceback(self) -> None:
        """``KeyError: live session`` aborts the field. It does not log a stack."""

        def boom() -> list[object]:
            """The builder the pre-v189 logs showed on canvas and groups."""
            raise KeyError("live session 20")

        with self.assertNoLogs("school_db", level="WARNING"):
            with self.assertRaises(KeyError) as caught:
                live_state_field(20, "groups", boom, [])
        self.assertIn("live session", str(caught.exception))

    def test_field_failure_logs_once_and_returns_default(self) -> None:
        """A real builder bug degrades that slice and does not stack per poll."""

        def boom() -> list[object]:
            """Non-session failure."""
            raise RuntimeError("cards exploded")

        with self.assertLogs("school_db", level="WARNING") as caught:
            first = live_state_field(91, "cards", boom, [])
            second = live_state_field(91, "cards", boom, [])
        self.assertEqual(first, [])
        self.assertEqual(second, [])
        self.assertEqual(len(caught.records), 1)
        self.assertFalse(any(record.exc_info for record in caught.records))

    def test_field_skips_builder_after_budget(self) -> None:
        """A spent poll budget does not start another slice."""
        called: list[int] = []

        def boom() -> int:
            """Must not run."""
            called.append(1)
            return 1

        cap._poll_budget.deadline = time.monotonic() - 1
        try:
            self.assertEqual(live_state_field(3, "groups", boom, []), [])
        finally:
            cap._poll_budget.deadline = None
        self.assertEqual(called, [])

    def test_cached_presence_label_does_not_need_a_ping(self) -> None:
        """Checkout notes flip the health label. No ``SELECT 1``."""
        store = LivePresenceStore.__new__(LivePresenceStore)
        store._ping_at = 0.0
        store._ping_ok = False
        self.assertEqual(store.cached_label(), "postgres")
        store._note_checkout(ok=False)
        self.assertEqual(store.cached_label(), "postgres-down")
        store._note_checkout(ok=True)
        self.assertEqual(store.cached_label(), "postgres")


class LivePollHardenTests(unittest.TestCase):
    """Staff and student ``/state`` when the Meet row disappears."""

    def setUp(self) -> None:
        """One teacher, one student, one active live session."""
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

    def tearDown(self) -> None:
        """Close sqlite and drop a leftover poll budget on this thread."""
        cap._poll_budget.deadline = None
        school = getattr(self, "school", None)
        if school is not None:
            school.close()
        tmp = getattr(self, "tmp", None)
        if tmp is not None:
            tmp.cleanup()

    def test_health_does_not_ping_presence(self) -> None:
        """``/health`` reads the cached label and stays off the presence pool."""

        class _LabelOnly:
            """Presence stand-in whose ping must not run."""

            def ping(self) -> bool:
                """Fail the test if liveness queries Postgres."""
                raise AssertionError("health pinged presence")

            def cached_label(self) -> str:
                """Cached mode with no query."""
                return "postgres-down"

            def close(self) -> None:
                """Match the real store."""

        self.school.presence = _LabelOnly()
        try:
            health = self.staff.get("/health")
        finally:
            self.school.presence = None
        self.assertEqual(health.status_code, 200)
        body = health.get_json()
        self.assertEqual(body["school"], "LLOVES")
        self.assertEqual(body["live_presence"], "postgres-down")
        self.assertNotEqual(health.headers.get("Connection"), "close")

    def test_gone_session_state_is_ended_json(self) -> None:
        """A deleted Meet is HTTP 200 ended JSON for staff and the student."""
        with self.school._lock:
            self.school.conn.execute(
                "DELETE FROM live_class_sessions WHERE id = ?",
                (self.session_id,),
            )
        with self.assertNoLogs("school_db", level="ERROR"):
            staff = self.staff.get(
                f"/api/live-sessions/{self.session_id}/state?light=1"
            )
            student = self.student.get("/api/student/state")
        for rv in (staff, student):
            text = rv.get_data(as_text=True)
            self.assertEqual(rv.status_code, 200, text[:400])
            self.assertIn("application/json", rv.content_type)
            self.assertNotIn("Internal Server Error", text)
            self.assertNotIn("<html", text.lower())
            self.assertEqual(rv.headers.get("Connection"), "close")
            body = rv.get_json()
            self.assertEqual(body.get("phase") or body.get("status"), "ended")

    def test_field_keyerror_while_session_vanishes_is_ended_json(self) -> None:
        """canvas/group ``KeyError: live session`` does not 500 or traceback."""

        def vanish(_session_id: int) -> list[dict]:
            """Drop the row the way End Live can, then raise the old KeyError."""
            with self.school._lock:
                self.school.conn.execute(
                    "DELETE FROM live_class_sessions WHERE id = ?",
                    (self.session_id,),
                )
            raise KeyError(f"live session {self.session_id}")

        self.school.live_group_projection = vanish  # type: ignore[method-assign]
        with self.assertNoLogs("school_db", level="ERROR"):
            rv = self.staff.get(f"/api/live-sessions/{self.session_id}/state?light=1")
        text = rv.get_data(as_text=True)
        self.assertEqual(rv.status_code, 200, text[:500])
        self.assertNotIn("Internal Server Error", text)
        self.assertNotIn("Traceback", text)
        body = rv.get_json()
        self.assertEqual(body.get("phase"), "ended")
        self.assertEqual(rv.headers.get("Connection"), "close")

    def test_student_budget_returns_retry_json(self) -> None:
        """A spent student budget is retry JSON, not an HTML 500."""
        cap._poll_budget.deadline = time.monotonic() - 1
        try:
            with self.assertNoLogs("app", level="ERROR"):
                rv = self.student.get("/api/student/state")
        finally:
            cap._poll_budget.deadline = None
        text = rv.get_data(as_text=True)
        self.assertEqual(rv.status_code, 200, text[:400])
        self.assertNotIn("Internal Server Error", text)
        body = rv.get_json()
        self.assertEqual(body.get("error"), "state unavailable")
        self.assertTrue(body.get("retry"))
        self.assertEqual(rv.headers.get("Connection"), "close")

    def test_full_poll_cap_sheds_without_touching_health(self) -> None:
        """When every poll slot is taken, ``/state`` retries and ``/health`` answers."""
        self.app.config["TESTING"] = False
        held = 0
        try:
            for _ in range(POLL_IN_FLIGHT):
                self.assertTrue(cap._POLL_SLOTS.acquire(blocking=False))
                held += 1
            rv = self.staff.get(f"/api/live-sessions/{self.session_id}/state?light=1")
            text = rv.get_data(as_text=True)
            self.assertEqual(rv.status_code, 200, text[:400])
            self.assertTrue(rv.get_json().get("retry"))
            self.assertEqual(rv.headers.get("Connection"), "close")
            health = self.staff.get("/health")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.get_json()["ok"], True)
            self.assertNotEqual(health.headers.get("Connection"), "close")
        finally:
            self.app.config["TESTING"] = True
            for _ in range(held):
                cap._POLL_SLOTS.release()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    unittest.main()

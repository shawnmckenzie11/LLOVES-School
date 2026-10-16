#!/usr/bin/env python3
"""MCK-43: a full class of 30 students + the teacher stays on the push line.

The per-worker seat cap used to be 4, so a real class fell through to the
20 s poll. These tests seat 31 concurrent clients, check the postcard
schema still carries ids rather than names, and check a waiting stream
does not hold a sqlite read open.
"""

from __future__ import annotations

import os
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

LMS_DIR = Path(__file__).resolve().parent
if str(LMS_DIR) not in sys.path:
    sys.path.insert(0, str(LMS_DIR))

from flask import request, session  # noqa: E402

from app import create_app
import live_news_wire
from live_news_wire import (
    CLASS_ON_STREAM,
    RING_PER_SESSION,
    STREAM_HOLD_TESTING_S,
    STREAMS_PER_WORKER,
    clear_stream_shutdown,
    detach_gunicorn_client,
    live_news_response,
    log_for,
    reset_logs,
    reset_stream_budget,
)


class ClassStreamTests(unittest.TestCase):
    """One live session and 31 overlapping LiveNewsWire clients."""

    def setUp(self) -> None:
        """Teacher, class, and a live session on a fresh data dir."""
        clear_stream_shutdown()
        reset_stream_budget()
        reset_logs()
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
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.client.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Aspen", "Birch"],
            },
        )
        class_id = created.get_json()["class"]["id"]
        live = self.school.start_live_class_session(
            class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])
        self.log = log_for(root)

    def tearDown(self) -> None:
        """Close the tape and the school db."""
        clear_stream_shutdown()
        reset_stream_budget()
        reset_logs()
        self.school.close()
        self.tmp.cleanup()

    def test_cap_covers_a_full_class_and_the_ring_stays_200(self) -> None:
        """31 seats fit, and the per-session ring was not raised."""
        self.assertEqual(CLASS_ON_STREAM, 31)
        self.assertGreaterEqual(STREAMS_PER_WORKER, CLASS_ON_STREAM)
        self.assertEqual(RING_PER_SESSION, 200)

    def test_schema_and_postcards_keep_ids_and_reads_commit(self) -> None:
        """Tape tables have no name columns, and a tail read is not left open."""
        conn = sqlite3.connect(self.log.path)
        try:
            events_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'live_news_events'"
            ).fetchone()[0]
            coalesce_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'board_ink_coalesce'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertIn("session_id INTEGER NOT NULL", events_sql)
        self.assertIn("event_type TEXT NOT NULL", events_sql)
        self.assertIn("payload_json TEXT NOT NULL", events_sql)
        self.assertNotIn("codename", events_sql.lower())
        self.assertNotIn("student_name", events_sql.lower())
        self.assertIn("PRIMARY KEY (session_id, board_key)", coalesce_sql)
        stored = self.log.append(
            self.session_id,
            [
                {
                    "type": "response_landed",
                    "scope": "self",
                    "student_id": 9,
                    "team_id": 4,
                    "state_seq": 2,
                    "codename": "Aspen",
                    "name": "Shawn",
                }
            ],
        )
        self.assertEqual(stored[0]["student_id"], 9)
        self.assertEqual(stored[0]["team_id"], 4)
        self.assertNotIn("codename", stored[0])
        self.assertNotIn("name", stored[0])
        self.log.since(self.session_id, 0)
        self.assertFalse(self.log._conn.in_transaction)

    def test_thirty_one_clients_stay_on_the_stream(self) -> None:
        """31 concurrent event streams each receive a live postcard, none busy."""
        cookie = self.client.get_cookie("session")
        self.assertIsNotNone(cookie)
        clients = []
        for _ in range(CLASS_ON_STREAM):
            client = self.app.test_client()
            client.set_cookie("session", cookie.value)
            clients.append(client)
        barrier = threading.Barrier(CLASS_ON_STREAM)
        bodies: list[str | None] = [None] * CLASS_ON_STREAM
        errors: list[str] = []

        def pull(index: int) -> None:
            """Open one stream after every client is ready to connect."""
            try:
                barrier.wait(timeout=10)
                response = clients[index].get(
                    f"/api/live/session/{self.session_id}/events"
                )
                bodies[index] = response.get_data(as_text=True)
            except Exception as exc:
                errors.append(f"{index}: {exc!r}")

        with mock.patch.object(live_news_wire, "STREAM_HOLD_TESTING_S", 2.0):
            threads = [
                threading.Thread(target=pull, args=(index,))
                for index in range(CLASS_ON_STREAM)
            ]
            for thread in threads:
                thread.start()
            time.sleep(0.35)
            stored = self.log.append(
                self.session_id,
                [
                    {
                        "type": "stage",
                        "stage": "action",
                        "state_seq": 3,
                        "codename": "Aspen",
                    }
                ],
            )
            self.assertNotIn("codename", stored[0])
            for thread in threads:
                thread.join(timeout=10)
                self.assertFalse(thread.is_alive())

        self.assertEqual(errors, [])
        for body in bodies:
            self.assertIsNotNone(body)
            assert body is not None
            self.assertIn("event: hello", body)
            self.assertIn("event: stage", body)
            self.assertIn('"stage":"action"', body)
            self.assertNotIn("event: busy", body)
            self.assertNotIn("Aspen", body)
            self.assertNotIn("codename", body)
            self.assertIn("event: ping", body)

    def test_waiting_seats_release_the_tape_and_the_school_lock(self) -> None:
        """A class sitting on the stream does not block the next write."""
        cookie = self.client.get_cookie("session")
        self.assertIsNotNone(cookie)
        clients = []
        for _ in range(CLASS_ON_STREAM):
            client = self.app.test_client()
            client.set_cookie("session", cookie.value)
            clients.append(client)
        barrier = threading.Barrier(CLASS_ON_STREAM)
        errors: list[str] = []

        def pull(index: int) -> None:
            """Hold one stream open for the write probe."""
            try:
                barrier.wait(timeout=10)
                clients[index].get(f"/api/live/session/{self.session_id}/events")
            except Exception as exc:
                errors.append(repr(exc))

        with mock.patch.object(live_news_wire, "STREAM_HOLD_TESTING_S", 1.5):
            threads = [
                threading.Thread(target=pull, args=(index,))
                for index in range(CLASS_ON_STREAM)
            ]
            for thread in threads:
                thread.start()
            time.sleep(0.35)
            other = sqlite3.connect(self.log.path, timeout=0.2)
            try:
                other.execute("PRAGMA busy_timeout=200")
                started = time.monotonic()
                other.execute("BEGIN IMMEDIATE")
                other.execute("COMMIT")
                tape_wait = time.monotonic() - started
            finally:
                other.close()
            started = time.monotonic()
            with self.school._lock:
                self.school.conn.execute("BEGIN IMMEDIATE")
                self.school.conn.execute("ROLLBACK")
            school_wait = time.monotonic() - started
            for thread in threads:
                thread.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertLess(tape_wait, 0.5, "tape write blocked behind a stream read")
        self.assertLess(
            school_wait, 0.5, "school write blocked behind a stream read"
        )
        self.assertFalse(self.log._conn.in_transaction)

    def test_detach_frees_the_caller_and_the_browser_gets_the_stream(self) -> None:
        """gunicorn's socket is lifted; the view returns before the hold ends."""
        browser, worker = socket.socketpair()
        browser.settimeout(2)
        self.addCleanup(browser.close)
        self.addCleanup(worker.close)
        with self.app.test_request_context(
            f"/api/live/session/{self.session_id}/events"
        ):
            session["logged_in"] = True
            session["user_id"] = int(self.teacher["id"])
            request.environ["gunicorn.socket"] = worker
            started = time.monotonic()
            response = live_news_response(
                self.school,
                self.session_id,
                testing=True,
                last_event_header=None,
                last_event_arg=None,
            )
            elapsed = time.monotonic() - started
            self.assertEqual(response.status_code, 200)
            # The seat is still held by the detached writer, not this caller.
            self.assertEqual(live_news_wire._stream_count, 1)
        worker.close()
        self.assertLess(elapsed, STREAM_HOLD_TESTING_S + 0.05)
        chunks = b""
        while b"event: ping" not in chunks:
            part = browser.recv(4096)
            if not part:
                break
            chunks += part
        self.assertIn(b"HTTP/1.1 200 OK", chunks)
        self.assertEqual(chunks.count(b"HTTP/1.1"), 1)
        self.assertIn(b"text/event-stream", chunks)
        self.assertIn(b"event: hello", chunks)
        self.assertIn(b"event: ping", chunks)
        self.assertNotIn(b"busy", chunks)
        deadline = time.monotonic() + 2
        while live_news_wire._stream_count and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(live_news_wire._stream_count, 0)

    def test_gunicorn_shutdown_does_not_reach_the_browser(self) -> None:
        """Bytes gunicorn writes after detach stay on the drain."""
        browser, worker = socket.socketpair()
        browser.settimeout(1)
        self.addCleanup(browser.close)
        self.addCleanup(worker.close)
        lifted = detach_gunicorn_client(worker)
        self.assertIsNotNone(lifted)
        assert lifted is not None
        self.addCleanup(lifted.close)
        worker.sendall(b"GUNICORN-FIN")
        lifted.sendall(b"BROWSER-SEES")
        data = browser.recv(64)
        self.assertEqual(data, b"BROWSER-SEES")
        self.assertNotIn(b"GUNICORN", data)


if __name__ == "__main__":
    unittest.main()

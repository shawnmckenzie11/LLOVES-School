"""N=28 Artifact open on Postgres presence: 0 sqlite lock, 0 HTML 500.

Thursday classes are MCR3U 9:15, MCR3U-2 10:40, and MCF3M 2:00 ET at
24–28 students. The alc failure was ``database is locked`` on student
``/state`` and heartbeat while an Artifact was open, at about N=12.
This run puts heartbeat writes on Postgres and keeps sqlite as the
catalogue. It writes ``lc-qa/artifact-load-postgres.md`` and ``.log``.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
import unittest
import uuid
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
if str(LMS_DIR) not in sys.path:
    sys.path.insert(0, str(LMS_DIR))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

import school_db as school_db_mod  # noqa: E402
from app import create_app  # noqa: E402
from artifact import C2_TRANSFORM_MEDIA_URL, TRANSFORMATIONS_ARTIFACT_ID  # noqa: E402

# Thursday bar. The teacher is the extra party on staff /state.
CLASS_SIZE = 28
WAVES = 4
GUNICORN_THREADS = 2
CODENAMES = [f"S{index:02d}" for index in range(CLASS_SIZE)]
REPORT_MD = REPO_ROOT / "lc-qa" / "artifact-load-postgres.md"
REPORT_LOG = REPO_ROOT / "lc-qa" / "artifact-load-postgres.log"
DEFAULT_URL = "postgresql://lloves:lloves@127.0.0.1:5432/lloves_live"


def _postgres_admin_url() -> str:
    """Return the admin URL the load test uses to create a database.

    Returns:
        ``LIVE_PRESENCE_TEST_URL`` or the local default.
    """
    return os.environ.get("LIVE_PRESENCE_TEST_URL", DEFAULT_URL).strip()


def _database_url(admin_url: str, name: str) -> str:
    """Swap the database name on a postgres URL.

    Args:
        admin_url: URL whose database already exists.
        name: Database to select.

    Returns:
        URL pointing at ``name``.
    """
    parts = urlsplit(admin_url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, ""))


def _postgres_up(admin_url: str) -> bool:
    """True when the admin database accepts a connection.

    Args:
        admin_url: Postgres URL.
    """
    try:
        import psycopg
    except ImportError:
        return False
    try:
        with psycopg.connect(admin_url, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception:
        return False
    return True


def _write_report(markdown: str, log_text: str) -> None:
    """Write the ops log next to the repo.

    Args:
        markdown: Human report.
        log_text: Line-oriented timing log.
    """
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text(markdown, encoding="utf-8")
    REPORT_LOG.write_text(log_text, encoding="utf-8")


class LivePresenceDefaultTests(unittest.TestCase):
    """Machines with no URL keep sqlite and the existing health alias."""

    def test_health_reports_sqlite_when_url_forced_off(self) -> None:
        """``live_database_url=''`` does not attach Postgres."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = create_app(
            db_path=Path(tmp.name) / "lloves.sqlite",
            data_dir=Path(tmp.name),
            testing=True,
            live_database_url="",
        )
        self.addCleanup(app.config["SCHOOL_DB"].close)
        health = app.test_client().get("/health")
        self.assertEqual(health.status_code, 200)
        body = health.get_json()
        self.assertEqual(body["school"], "LLOVES")
        self.assertEqual(body["live_presence"], "sqlite")
        self.assertIsNone(app.config["SCHOOL_DB"].presence)


class PostgresArtifactLoadTests(unittest.TestCase):
    """Artifact open at N=28 with presence writes on Postgres."""

    def setUp(self) -> None:
        """Twenty-eight joined students and a private Postgres database."""
        admin = _postgres_admin_url()
        if not _postgres_up(admin):
            if os.environ.get("CI"):
                self.fail(f"CI requires Postgres at {admin}")
            self.skipTest(f"Postgres is not reachable at {admin}")
        import psycopg

        self.db_name = f"lloves_p_{uuid.uuid4().hex[:8]}"
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{self.db_name}"')
        self.presence_url = _database_url(admin, self.db_name)
        self.admin_url = admin
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
            live_database_url=self.presence_url,
        )
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        self.school = self.app.config["SCHOOL_DB"]
        self.assertIsNotNone(self.school.presence)
        self.assertEqual(self.school.live_presence_status(), "postgres")
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
            codenames=CODENAMES,
            offering_id=int(self.offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )
        self.class_id = int(created["id"])
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.session_id = int(live["id"])
        self.session_code = str(live["session_code"])
        self.school.game.begin_game(self.class_id)
        with self.school.game._lock:
            rows = self.school.game.conn.execute(
                """
                SELECT id FROM students
                WHERE class_id = ?
                ORDER BY id ASC
                """,
                (self.class_id,),
            ).fetchall()
        self.student_ids = [int(row["id"]) for row in rows]
        self.school.setup_live_session_groups(
            self.session_id,
            n_teams=4,
            mode="manual",
            present_ids=self.student_ids,
            assignments=[
                {"student_id": sid, "team_index": index % 4}
                for index, sid in enumerate(self.student_ids)
            ],
        )
        self.clients: list[tuple[str, Any]] = []
        for name in CODENAMES:
            client = self.app.test_client()
            joined = client.post(
                "/auth/student-code",
                data={"code": self.session_code, "name": name},
                follow_redirects=False,
            )
            self.assertEqual(joined.status_code, 302, joined.get_data(as_text=True)[:300])
            client.post("/student/mood", data={"mood": "good"})
            client.post("/student/character", data={"character": "fox"})
            self.clients.append((name, client))

    def tearDown(self) -> None:
        """Close pools and drop the private database."""
        school = getattr(self, "school", None)
        if school is not None:
            school.close()
        tmp = getattr(self, "tmp", None)
        if tmp is not None:
            tmp.cleanup()
        name = getattr(self, "db_name", "")
        admin = getattr(self, "admin_url", "")
        if name and admin:
            import psycopg

            with psycopg.connect(admin, autocommit=True) as conn:
                conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')

    def test_n28_artifact_open_writes_postgres_not_sqlite(self) -> None:
        """N=28 state, heartbeat, media, and Group Q preview: 0 sqlite locks.

        The 20s freshness skip is forced to 0 so every poll would have been
        an ``UPDATE live_session_attendees`` on the sqlite path. Those
        updates must stay at 0. Heartbeat rows in Postgres must move.
        A second connection hammers the game-show file the way the alc
        lock did.
        """
        self.assertEqual(self.school.live_presence_status(), "postgres")
        health = self.staff.get("/health").get_json()
        self.assertEqual(health["live_presence"], "postgres")
        self.assertEqual(health["school"], "LLOVES")
        minted = self.staff.post(
            f"/api/live-sessions/{self.session_id}/artifacts",
            json={
                "artifact_id": TRANSFORMATIONS_ARTIFACT_ID,
                "snapshot": {"a": 2, "h": 1, "k": -1},
                "target_mode": "graph",
                "group_q": True,
                "hot_cold_visible": True,
            },
        )
        self.assertEqual(minted.status_code, 200, minted.get_data(as_text=True)[:400])
        minted_body = minted.get_json()
        prompt_id = int(minted_body["prompt"]["id"])
        self.assertIs(minted_body["prompt"]["payload"].get("group_q"), True)
        self.assertEqual(
            minted_body["prompt"]["payload"].get("artifact_id"),
            TRANSFORMATIONS_ARTIFACT_ID,
        )
        self.assertEqual(
            (minted_body.get("active_media") or {}).get("url"),
            C2_TRANSFORM_MEDIA_URL,
        )
        before_beats = {
            int(row["id"]): row["last_heartbeat_at"]
            for row in self.school.conn.execute(
                """
                SELECT id, last_heartbeat_at
                FROM live_session_attendees
                WHERE live_session_id = ?
                """,
                (self.session_id,),
            )
        }
        self.assertEqual(len(before_beats), CLASS_SIZE)
        self.school.hot_sqlite_writes = 0
        self.school.presence.heartbeat_writes = 0
        self.school.conn.execute("PRAGMA busy_timeout=400")
        self.school.game.conn.execute("PRAGMA busy_timeout=400")
        sqlite_attendee_writes = {"n": 0}

        def _trace_attendee_write(sql: str) -> None:
            """Count attendee writes that still hit the sqlite file."""
            text = " ".join(str(sql).split()).upper()
            if "LIVE_SESSION_ATTENDEES" in text and (
                text.startswith("UPDATE") or text.startswith("INSERT")
            ):
                sqlite_attendee_writes["n"] += 1

        self.school.conn.set_trace_callback(_trace_attendee_write)
        previous_window = school_db_mod.LIVE_HEARTBEAT_WRITE_SECONDS
        school_db_mod.LIVE_HEARTBEAT_WRITE_SECONDS = 0
        gate = threading.Semaphore(GUNICORN_THREADS)
        lock = threading.Lock()
        errors: list[str] = []
        rows: list[str] = []
        codes: Counter[str] = Counter()
        latencies: dict[str, list[float]] = {}
        lock_logs: list[str] = []
        stop_hammer = threading.Event()
        hammer_ok = {"n": 0}

        class _LockCatch(logging.Handler):
            """Record log lines that still say the sqlite file is locked."""

            def emit(self, record: logging.LogRecord) -> None:
                """Keep a locked-database line, including its traceback."""
                text = record.getMessage()
                exc = record.exc_info[1] if record.exc_info else None
                blob = f"{text} {exc or ''}"
                if "locked" in blob.lower() or "server overloaded" in blob.lower():
                    lock_logs.append(blob[:240])

        handler = _LockCatch()
        loggers = [self.app.logger, logging.getLogger()]
        for target in loggers:
            target.addHandler(handler)
        original_level = self.app.logger.level
        self.app.logger.setLevel(logging.ERROR)

        def hammer() -> None:
            """Write the game-show connection while students poll."""
            while not stop_hammer.is_set():
                try:
                    self.school.game.conn.execute(
                        """
                        UPDATE classes
                        SET course_code = course_code
                        WHERE id = ?
                        """,
                        (self.class_id,),
                    )
                    hammer_ok["n"] += 1
                except sqlite3.OperationalError as exc:
                    with lock:
                        errors.append(f"game-write {exc}")
                        if "locked" in str(exc).lower():
                            lock_logs.append(repr(exc))
                    return

        hammer_thread = threading.Thread(target=hammer, name="game-hammer")

        def record(label: str, rv: Any, started: float) -> None:
            """Store one timed response. Locks, 5xx, and HTML 500s fail the bar."""
            elapsed_ms = (time.perf_counter() - started) * 1000
            text = rv.get_data(as_text=True)
            body = rv.get_json(silent=True) or {}
            line = f"{label}\t{rv.status_code}\t{elapsed_ms:.1f}ms"
            with lock:
                codes[f"{label}:{rv.status_code}"] += 1
                latencies.setdefault(label, []).append(elapsed_ms)
                rows.append(line)
                if (
                    rv.status_code >= 500
                    or "Internal Server Error" in text
                    or "server overloaded" in text.lower()
                    or "database is locked" in text.lower()
                    or body.get("error") in {"state unavailable", "Reconnecting…"}
                ):
                    errors.append(f"{line} {text[:240]}")

        def run(label: str, fn: Any) -> None:
            """Hold one of the two gunicorn slots, then call the route."""
            gate.acquire()
            started = time.perf_counter()
            try:
                record(label, fn(), started)
            except Exception as exc:  # noqa: BLE001 — the log must show the crash
                with lock:
                    errors.append(f"{label} EXC {exc!r}")
                    if "locked" in str(exc).lower():
                        lock_logs.append(repr(exc))
            finally:
                gate.release()

        wall_started = time.perf_counter()
        hammer_thread.start()
        try:
            for _wave in range(WAVES):
                threads: list[threading.Thread] = []
                for _name, client in self.clients:
                    threads.append(
                        threading.Thread(
                            target=run,
                            args=(
                                "state",
                                lambda client=client: client.get("/api/student/state"),
                            ),
                        )
                    )
                    threads.append(
                        threading.Thread(
                            target=run,
                            args=(
                                "heartbeat",
                                lambda client=client: client.post(
                                    "/api/student/heartbeat"
                                ),
                            ),
                        )
                    )
                    threads.append(
                        threading.Thread(
                            target=run,
                            args=(
                                "media",
                                lambda client=client: client.get(C2_TRANSFORM_MEDIA_URL),
                            ),
                        )
                    )
                    threads.append(
                        threading.Thread(
                            target=run,
                            args=(
                                "preview",
                                lambda client=client: client.post(
                                    "/api/student/live-prompt/artifact-preview",
                                    json={
                                        "prompt_id": prompt_id,
                                        "params": {"a": 2.0, "h": 1.0, "k": -1.0},
                                    },
                                ),
                            ),
                        )
                    )
                threads.append(
                    threading.Thread(
                        target=run,
                        args=(
                            "staff-state",
                            lambda: self.staff.get(
                                f"/api/live-sessions/{self.session_id}/state"
                            ),
                        ),
                    )
                )
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
        finally:
            stop_hammer.set()
            hammer_thread.join(timeout=5)
            school_db_mod.LIVE_HEARTBEAT_WRITE_SECONDS = previous_window
            self.school.conn.set_trace_callback(None)
            self.school.conn.execute("PRAGMA busy_timeout=30000")
            self.school.game.conn.execute("PRAGMA busy_timeout=30000")
            for target in loggers:
                target.removeHandler(handler)
            self.app.logger.setLevel(original_level)
        wall_ms = (time.perf_counter() - wall_started) * 1000
        after_beats = {
            int(row["id"]): row["last_heartbeat_at"]
            for row in self.school.conn.execute(
                """
                SELECT id, last_heartbeat_at
                FROM live_session_attendees
                WHERE live_session_id = ?
                """,
                (self.session_id,),
            )
        }
        if after_beats != before_beats:
            errors.append("sqlite last_heartbeat_at changed during the poll storm")
        presence_writes = int(self.school.presence.heartbeat_writes)
        polls = int(codes.get("state:200", 0) + codes.get("heartbeat:200", 0))
        if presence_writes < polls:
            errors.append(
                f"postgres heartbeat writes {presence_writes} < "
                f"state+heartbeat 200s {polls}"
            )
        if self.school.hot_sqlite_writes:
            errors.append(f"hot_sqlite_writes={self.school.hot_sqlite_writes}")
        if sqlite_attendee_writes["n"]:
            errors.append(
                f"sqlite attendee INSERT/UPDATE={sqlite_attendee_writes['n']}"
            )
        school_txn = bool(self.school.conn.in_transaction)
        game_txn = bool(self.school.game.conn.in_transaction)
        if school_txn or game_txn:
            errors.append(f"open transaction school={school_txn} game={game_txn}")
        errors.extend(f"sqlite lock log: {line}" for line in lock_logs)
        if hammer_ok["n"] < 1:
            errors.append("game-show writer never completed")
        passed = not errors and not lock_logs and presence_writes >= polls
        per_wave = CLASS_SIZE
        expected = {
            "state:200": per_wave * WAVES,
            "heartbeat:200": per_wave * WAVES,
            "media:200": per_wave * WAVES,
            "preview:200": per_wave * WAVES,
            "staff-state:200": WAVES,
        }
        for key, count in expected.items():
            if codes.get(key) != count:
                errors.append(f"{key}={codes.get(key, 0)} expected {count}")
                passed = False
        for bad in ("state:500", "heartbeat:500", "heartbeat:503", "preview:500"):
            if codes.get(bad):
                errors.append(f"{bad}={codes[bad]}")
                passed = False
        markdown = _report(
            wall_ms=wall_ms,
            codes=codes,
            latencies=latencies,
            errors=errors,
            lock_logs=lock_logs,
            passed=passed,
            school_txn=school_txn,
            game_txn=game_txn,
            presence_writes=presence_writes,
            sqlite_attendee_writes=sqlite_attendee_writes["n"],
            hot_sqlite_writes=self.school.hot_sqlite_writes,
            hammer_writes=hammer_ok["n"],
        )
        summary = [
            f"verdict={'PASS' if passed else 'FAIL'}",
            "store=postgres",
            "stack=GET /api/student/state -> touch_live_session_heartbeat -> "
            "live_presence.resume_if_stale",
            "stack=POST /api/student/heartbeat -> touch_live_session_heartbeat -> "
            "live_presence.resume_if_stale",
            f"artifact={TRANSFORMATIONS_ARTIFACT_ID} media={C2_TRANSFORM_MEDIA_URL} "
            "group_q=true",
            f"repro=N={CLASS_SIZE} x {WAVES} waves, in-flight={GUNICORN_THREADS}, "
            "heartbeat write window forced to 0s, game-show connection hammering",
            f"postgres_heartbeat_writes={presence_writes}",
            f"sqlite_attendee_writes={sqlite_attendee_writes['n']}",
            f"hot_sqlite_writes={self.school.hot_sqlite_writes}",
            f"game_hammer_writes={hammer_ok['n']}",
            f"wall_ms={wall_ms:.1f}",
            f"sqlite_lock_logs={len(lock_logs)}",
            f"errors={len(errors)}",
            f"in_transaction school={school_txn} game={game_txn}",
            "codes " + " ".join(
                f"{key}={count}" for key, count in sorted(codes.items())
            ),
        ]
        for label, values in sorted(latencies.items()):
            ordered = sorted(values)
            p95 = ordered[max(0, int(len(ordered) * 0.95) - 1)]
            summary.append(
                f"{label} n={len(values)} med={statistics.median(values):.1f}ms "
                f"p95={p95:.1f}ms max={max(values):.1f}ms"
            )
        log_text = "\n".join(summary + ["---"] + rows) + "\n"
        _write_report(markdown, log_text)
        self.assertEqual(errors, [], errors[:8])
        self.assertEqual(lock_logs, [])
        self.assertGreaterEqual(presence_writes, polls)
        self.assertEqual(sqlite_attendee_writes["n"], 0)
        self.assertEqual(self.school.hot_sqlite_writes, 0)
        self.assertEqual(after_beats, before_beats)
        self.assertFalse(school_txn)
        self.assertFalse(game_txn)


def _report(
    *,
    wall_ms: float,
    codes: Counter[str],
    latencies: dict[str, list[float]],
    errors: list[str],
    lock_logs: list[str],
    passed: bool,
    school_txn: bool,
    game_txn: bool,
    presence_writes: int,
    sqlite_attendee_writes: int,
    hot_sqlite_writes: int,
    hammer_writes: int,
) -> str:
    """Build the N=28 Postgres ops log.

    Args:
        wall_ms: Wall time for every wave.
        codes: ``label:status`` counts.
        latencies: Milliseconds by label.
        errors: Failure lines.
        lock_logs: Logged lock / overloaded lines.
        passed: True when the bar was met.
        school_txn: School connection left inside a transaction.
        game_txn: Game connection left inside a transaction.
        presence_writes: Postgres heartbeat updates.
        sqlite_attendee_writes: ``INSERT``/``UPDATE`` on ``live_session_attendees``.
        hot_sqlite_writes: Sqlite resume/sweep counter.
        hammer_writes: Game-show file writes during the storm.

    Returns:
        Markdown report with repro, N, timings, and PASS/FAIL counts.
    """
    latency_rows = []
    for label, values in sorted(latencies.items()):
        ordered = sorted(values)
        p95 = ordered[max(0, int(len(ordered) * 0.95) - 1)]
        latency_rows.append(
            f"| {label} | {len(values)} | {statistics.median(values):.0f} | "
            f"{p95:.0f} | {max(values):.0f} |"
        )
    code_line = ", ".join(f"{key}={count}" for key, count in sorted(codes.items()))
    error_block = "\n".join(f"- {line}" for line in errors[:12]) or "- none"
    lock_block = "\n".join(f"- {line}" for line in lock_logs[:8]) or "- none"
    verdict = "PASS" if passed else "FAIL"
    return f"""# Artifact Postgres presence ({verdict})

Re-run:

```bash
python3 -m unittest lms.test_live_presence_load -v
```

Writes this file and `lc-qa/artifact-load-postgres.log`.

## Repro

Thursday bar: **N={CLASS_SIZE}** (MCR3U / MCR3U-2 / MCF3M headcount, not the N=12 alc sample). Artifact `{TRANSFORMATIONS_ARTIFACT_ID}` at `{C2_TRANSFORM_MEDIA_URL}` with **`group_q: true`**. In-flight cap {GUNICORN_THREADS} (gunicorn threads). Heartbeat write window forced to 0 so every state and heartbeat poll takes the presence UPDATE. A second connection hammers `classes` on the sqlite file for the whole storm.

Fly stack this replaces:

1. `GET /api/student/state` → `touch_live_session_heartbeat` → `UPDATE live_session_attendees`
2. `POST /api/student/heartbeat` → same UPDATE

Pass bar: those updates are Postgres, sqlite attendee writes stay 0, 0 `database is locked`, 0 HTTP 500, 0 “Internal Server Error” / “server overloaded”, 0 open transactions.

## This run

| | |
|---|---|
| Verdict | **{verdict}** |
| Store | postgres |
| Class | {CLASS_SIZE} students |
| Waves | {WAVES} |
| In-flight cap | {GUNICORN_THREADS} |
| Wall | {wall_ms:.0f} ms |
| Postgres heartbeat writes | {presence_writes} (state + heartbeat + preview gates) |
| Sqlite attendee INSERT/UPDATE | {sqlite_attendee_writes} |
| `hot_sqlite_writes` | {hot_sqlite_writes} |
| Game-show hammer writes | {hammer_writes} |
| School in_transaction | {school_txn} |
| Game in_transaction | {game_txn} |
| HTTP | {code_line} |

| Path | n | med ms | p95 ms | max ms |
|---|---:|---:|---:|---:|
{chr(10).join(latency_rows)}

### Errors

{error_block}

### Lock logs

{lock_block}

## alc gap

`fly.toml` does not set `LIVE_DATABASE_URL` or `DATABASE_URL`. `/health` on this process reports `live_presence=postgres` only because the test passed a URL. Production reports `sqlite` until Shawn runs `fly mpg attach` (that command restarts the app — not done here). See `lms/LIVE_PRESENCE.md`.

## Residual

- Thursday still runs on sqlite if this branch is not merged **and** the secret is not attached. Both are required. Attach alone on the current image does nothing; the image alone without the secret stays on sqlite.
- Staff `/state` still reads prompts, scores, and roster from sqlite. Those are reads. A sqlite writer outside this process (an SSH `sqlite3` with no busy timeout) can still stall the catalogue.
- Fly machine size is unchanged (shared-cpu-1x, 1 GB, 2 threads). Postgres moves the lock. It does not add CPU.
- Basic Managed Postgres is a paid cluster. WAL remains the local/dev belt when no URL is set.
"""

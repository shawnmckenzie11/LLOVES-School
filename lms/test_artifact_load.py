"""Class-size Artifact open: Group Q must not 500 or rebuild the game.

Production serves live class on one gunicorn worker with two threads
(``lms/Dockerfile``) and a shared-cpu-1x Fly machine. Opening an Artifact
for group work used to call ``game_state`` on every teacher-state read.
Sixteen students plus the teacher then rebuilt the full roster hundreds of
times in one wave. That pegged the machine (Fly "server overloaded") and
any escaped error became an HTML 500.

This test is the ops re-run. It writes ``lc-qa/artifact-load-latest.md``
and ``lc-qa/artifact-load-latest.log``.
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
from collections import Counter
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
if str(LMS_DIR) not in sys.path:
    sys.path.insert(0, str(LMS_DIR))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

import school_db as school_db_mod  # noqa: E402
from app import create_app  # noqa: E402
from artifact import C2_TRANSFORM_MEDIA_URL, TRANSFORMATIONS_ARTIFACT_ID  # noqa: E402

# N=16 matches the #116 idle bar. The teacher is the 17th party.
CLASS_SIZE = 16
# gunicorn ``--threads 2``. Extra requests wait, the way Fly queues them.
GUNICORN_THREADS = 2
# One student poll may still build the live game once for points and once
# for the group list. The old path was ~25 ``game_state`` calls.
STUDENT_GAME_STATE_BUDGET = 4
# Whole open wave. The pre-fix fanout was ~600.
WAVE_GAME_STATE_BUDGET = 80
REPORT_MD = REPO_ROOT / "lc-qa" / "artifact-load-latest.md"
REPORT_LOG = REPO_ROOT / "lc-qa" / "artifact-load-latest.log"
SQLITE_MD = REPO_ROOT / "lc-qa" / "artifact-load-sqlite.md"
SQLITE_LOG = REPO_ROOT / "lc-qa" / "artifact-load-sqlite.log"
# Ops incident on alc: SID 11, about 12 students, Artifact m1c2-transforms open.
INCIDENT_CLASS = 12
INCIDENT_WAVES = 4
CODENAMES = [f"S{index:02d}" for index in range(CLASS_SIZE)]


def _write_report(markdown: str, log_text: str) -> None:
    """Write the ops log next to the repo so a re-run leaves a file.

    Args:
        markdown: Human report.
        log_text: Line-oriented timing log.
    """
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text(markdown, encoding="utf-8")
    REPORT_LOG.write_text(log_text, encoding="utf-8")


class ArtifactGroupLoadTests(unittest.TestCase):
    """Artifact mint, media, and Group Q previews under a full class."""

    def setUp(self) -> None:
        """Sixteen joined students, four teams, Run as Group on."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        self.school = self.app.config["SCHOOL_DB"]
        self.game_state_calls = 0
        original = self.school.game.game_state

        def _counted(*args: Any, **kwargs: Any) -> Any:
            """Count full game rebuilds. The load bar fails if this spikes."""
            self.game_state_calls += 1
            return original(*args, **kwargs)

        self.school.game.game_state = _counted  # type: ignore[method-assign]
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
        """Close sqlite and drop the temp database."""
        self.school.close()
        self.tmp.cleanup()

    def _mint(
        self,
        *,
        snapshot: dict[str, float],
        mode: str = "graph",
        group_q: bool = True,
    ) -> Any:
        """POST one Artifact mint.

        Args:
            snapshot: Teacher slider values.
            mode: ``graph`` or ``equation``.
            group_q: When True, submit waits for teammates. The alc incident
                minted Match challenges with this flag false.
        """
        return self.staff.post(
            f"/api/live-sessions/{self.session_id}/artifacts",
            json={
                "artifact_id": TRANSFORMATIONS_ARTIFACT_ID,
                "snapshot": snapshot,
                "target_mode": mode,
                "group_q": group_q,
                "hot_cold_visible": True,
            },
        )

    def test_student_poll_does_not_rebuild_game_per_read(self) -> None:
        """One student snapshot after Group Q mint stays within the budget."""
        minted = self._mint(snapshot={"a": 2, "h": 1, "k": -1})
        self.assertEqual(minted.status_code, 200, minted.get_data(as_text=True)[:500])
        body = minted.get_json()
        self.assertTrue(body["prompt"]["payload"]["group_q"])
        self.assertEqual(body["active_media"]["url"], C2_TRANSFORM_MEDIA_URL)
        self.game_state_calls = 0
        student = self.clients[0][1].get("/api/student/state")
        self.assertEqual(student.status_code, 200, student.get_data(as_text=True)[:500])
        payload = student.get_json()
        self.assertNotEqual(payload.get("error"), "state unavailable")
        self.assertIn("group_q_ready", payload)
        self.assertEqual(
            (payload.get("active_media") or {}).get("url"),
            C2_TRANSFORM_MEDIA_URL,
        )
        self.assertLessEqual(
            self.game_state_calls,
            STUDENT_GAME_STATE_BUDGET,
            f"student /state rebuilt game_state {self.game_state_calls} times",
        )

    def test_mint_failure_is_json_retry_not_html_500(self) -> None:
        """A mint crash is a JSON 503, not Internal Server Error HTML."""

        def boom(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("artifact store locked")

        self.school.mint_live_artifact = boom  # type: ignore[method-assign]
        rv = self._mint(snapshot={"a": 1, "h": 0, "k": 0})
        text = rv.get_data(as_text=True)
        self.assertEqual(rv.status_code, 503, text[:400])
        self.assertNotIn("Internal Server Error", text)
        body = rv.get_json()
        self.assertFalse(body["ok"])
        self.assertTrue(body["retry"])

    def test_class_open_wave_stays_healthy(self) -> None:
        """Teacher mint plus N=16 media, state, and previews: 0×500.

        Requests are gated to two at a time, matching production gunicorn.
        """
        seeded = self._mint(snapshot={"a": 2, "h": 1, "k": -1})
        self.assertEqual(seeded.status_code, 200, seeded.get_data(as_text=True)[:400])
        prompt_id = int(seeded.get_json()["prompt"]["id"])
        self.game_state_calls = 0
        gate = threading.Semaphore(GUNICORN_THREADS)
        lock = threading.Lock()
        errors: list[str] = []
        rows: list[str] = []
        codes: Counter[str] = Counter()
        latencies: dict[str, list[float]] = {}

        def record(label: str, rv: Any, started: float) -> None:
            """Store one timed response. 500s and reconnect stubs are failures."""
            elapsed_ms = (time.perf_counter() - started) * 1000
            text = rv.get_data(as_text=True)
            body = rv.get_json(silent=True) or {}
            line = f"{label}\t{rv.status_code}\t{elapsed_ms:.1f}ms"
            with lock:
                codes[f"{label}:{rv.status_code}"] += 1
                latencies.setdefault(label, []).append(elapsed_ms)
                rows.append(line)
                if rv.status_code >= 500 or "Internal Server Error" in text:
                    errors.append(f"{line} {text[:240]}")
                elif body.get("error") == "state unavailable":
                    errors.append(f"{line} reconnect stub")
                elif rv.status_code != 200 or (
                    label != "media" and body.get("ok") is False
                ):
                    errors.append(f"{line} {text[:240]}")

        def run(label: str, fn: Any) -> None:
            """Hold a worker slot, then call the route."""
            gate.acquire()
            started = time.perf_counter()
            try:
                record(label, fn(), started)
            except Exception as exc:  # noqa: BLE001 — the log must show the crash
                with lock:
                    errors.append(f"{label} EXC {exc!r}")
            finally:
                gate.release()

        threads = [
            threading.Thread(
                target=run,
                args=(
                    "mint",
                    lambda: self._mint(
                        snapshot={"a": 3, "h": -1, "k": 2}, mode="equation"
                    ),
                ),
            ),
            threading.Thread(
                target=run,
                args=(
                    "staff-state",
                    lambda: self.staff.get(
                        f"/api/live-sessions/{self.session_id}/state"
                    ),
                ),
            ),
            threading.Thread(
                target=run,
                args=(
                    "staff-media",
                    lambda: self.staff.get(
                        f"/api/live-sessions/{self.session_id}/active-media"
                    ),
                ),
            ),
        ]
        for name, client in self.clients:
            threads.append(
                threading.Thread(
                    target=run,
                    args=("state", lambda client=client: client.get("/api/student/state")),
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
        wall_started = time.perf_counter()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        wall_ms = (time.perf_counter() - wall_started) * 1000
        summary_lines = [
            f"wall_ms={wall_ms:.1f}",
            f"game_state_calls={self.game_state_calls}",
            f"gunicorn_threads={GUNICORN_THREADS}",
            f"class_size={CLASS_SIZE}",
            f"errors={len(errors)}",
        ]
        for label, values in sorted(latencies.items()):
            ordered = sorted(values)
            p95 = ordered[max(0, int(len(ordered) * 0.95) - 1)]
            summary_lines.append(
                f"{label} n={len(values)} med={statistics.median(values):.1f}ms "
                f"p95={p95:.1f}ms max={max(values):.1f}ms"
            )
        summary_lines.append("codes " + " ".join(
            f"{key}={count}" for key, count in sorted(codes.items())
        ))
        log_text = "\n".join(summary_lines + ["---"] + rows) + "\n"
        passed = not errors and self.game_state_calls <= WAVE_GAME_STATE_BUDGET
        markdown = _report_markdown(
            wall_ms=wall_ms,
            game_state_calls=self.game_state_calls,
            codes=codes,
            latencies=latencies,
            errors=errors,
            passed=passed,
        )
        _write_report(markdown, log_text)
        self.assertEqual(errors, [], errors[:4])
        self.assertLessEqual(self.game_state_calls, WAVE_GAME_STATE_BUDGET)
        self.assertLess(wall_ms, 3000)
        self.assertEqual(codes["state:200"], CLASS_SIZE)
        self.assertEqual(codes["media:200"], CLASS_SIZE)
        self.assertEqual(codes["preview:200"], CLASS_SIZE)
        self.assertEqual(codes["mint:200"], 1)
        self.assertEqual(codes["staff-state:200"], 1)
        self.assertEqual(codes["staff-media:200"], 1)
        self.assertNotIn("state:500", codes)
        self.assertNotIn("mint:500", codes)

    def test_school_write_does_not_reserve_the_game_connection(self) -> None:
        """A school UPDATE must not leave the reserved lock for the game connection.

        That pair is ``database is locked`` on student ``/state`` and heartbeat.
        Legacy isolation kept the lock until ``commit()``. Autocommit ends it
        with the statement. The busy timeout is shortened so a regression
        fails in under a second instead of hanging for 30s.
        """
        self.school.conn.execute("PRAGMA busy_timeout=400")
        self.school.game.conn.execute("PRAGMA busy_timeout=400")
        self.school.conn.execute(
            """
            UPDATE live_class_sessions
            SET session_code = session_code
            WHERE id = ?
            """,
            (self.session_id,),
        )
        self.assertFalse(
            self.school.conn.in_transaction,
            "school write still holds a transaction",
        )
        started = time.perf_counter()
        self.school.game.conn.execute(
            """
            UPDATE classes
            SET course_code = course_code
            WHERE id = ?
            """,
            (self.class_id,),
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.assertFalse(self.school.game.conn.in_transaction)
        self.assertLess(elapsed_ms, 1000, "game write waited on a school lock")
        self.school.conn.execute("PRAGMA busy_timeout=30000")
        self.school.game.conn.execute("PRAGMA busy_timeout=30000")

    def test_artifact_open_state_and_heartbeat_have_zero_sqlite_locks(self) -> None:
        """N=12 Artifact polls hit ``_resume_live_attendee`` with 0 sqlite locks.

        Ops stack (session 11, v148): ``GET /api/student/state`` and
        ``POST /api/student/heartbeat`` both call ``touch_live_session_heartbeat``
        → ``_resume_live_attendee`` while ``m1c2-transforms`` is on screen.
        The 20s freshness skip is turned off so every poll takes that UPDATE.
        """
        self.assertIs(self.school._lock, self.school.game._lock)
        journal = self.school.conn.execute("PRAGMA journal_mode").fetchone()
        self.assertEqual(str(journal[0]).lower(), "wal")
        minted = self._mint(snapshot={"a": 2, "h": 1, "k": -1}, group_q=False)
        self.assertEqual(minted.status_code, 200, minted.get_data(as_text=True)[:400])
        minted_body = minted.get_json()
        prompt_payload = (minted_body.get("prompt") or {}).get("payload") or {}
        self.assertIs(prompt_payload.get("group_q"), False)
        self.assertEqual(
            prompt_payload.get("artifact_id"), TRANSFORMATIONS_ARTIFACT_ID
        )
        media_url = (minted_body.get("active_media") or {}).get("url")
        self.assertEqual(media_url, C2_TRANSFORM_MEDIA_URL)
        resume_calls = {"n": 0}
        resume_lock = threading.Lock()
        original_resume = self.school._resume_live_attendee

        def _count_resume(existing: dict[str, Any], name: str = "") -> dict[str, Any]:
            """Count the UPDATE Fly logged, then run the real resume."""
            with resume_lock:
                resume_calls["n"] += 1
            return original_resume(existing, name=name)

        self.school._resume_live_attendee = _count_resume  # type: ignore[method-assign]
        previous_write_window = school_db_mod.LIVE_HEARTBEAT_WRITE_SECONDS
        school_db_mod.LIVE_HEARTBEAT_WRITE_SECONDS = 0
        cohort = self.clients[:INCIDENT_CLASS]
        gate = threading.Semaphore(GUNICORN_THREADS)
        lock = threading.Lock()
        errors: list[str] = []
        rows: list[str] = []
        codes: Counter[str] = Counter()
        latencies: dict[str, list[float]] = {}
        lock_logs: list[str] = []

        class _LockCatch(logging.Handler):
            """Record log lines that still say the sqlite file is locked."""

            def emit(self, record: logging.LogRecord) -> None:
                """Keep a locked-database line, including its traceback."""
                text = record.getMessage()
                exc = record.exc_info[1] if record.exc_info else None
                blob = f"{text} {exc or ''}"
                if "locked" in blob.lower():
                    lock_logs.append(blob[:240])

        handler = _LockCatch()
        # Flask's app.logger name is the import name. Catch both.
        loggers = [self.app.logger, logging.getLogger()]
        for target in loggers:
            target.addHandler(handler)
        original_level = self.app.logger.level
        self.app.logger.setLevel(logging.ERROR)

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
        try:
            for _wave in range(INCIDENT_WAVES):
                threads: list[threading.Thread] = []
                for _name, client in cohort:
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
            school_db_mod.LIVE_HEARTBEAT_WRITE_SECONDS = previous_write_window
            self.school._resume_live_attendee = original_resume  # type: ignore[method-assign]
            for target in loggers:
                target.removeHandler(handler)
            self.app.logger.setLevel(original_level)
        wall_ms = (time.perf_counter() - wall_started) * 1000
        resume_writes = int(resume_calls["n"])
        school_txn = bool(self.school.conn.in_transaction)
        game_txn = bool(self.school.game.conn.in_transaction)
        if school_txn or game_txn:
            errors.append(f"open transaction school={school_txn} game={game_txn}")
        errors.extend(f"sqlite lock log: {line}" for line in lock_logs)
        polls = int(codes.get("state:200", 0) + codes.get("heartbeat:200", 0))
        if resume_writes < polls:
            errors.append(
                f"resume writes {resume_writes} < state+heartbeat 200s {polls}"
            )
        passed = not errors and not lock_logs and resume_writes >= polls
        markdown = _sqlite_report(
            wall_ms=wall_ms,
            codes=codes,
            latencies=latencies,
            errors=errors,
            lock_logs=lock_logs,
            passed=passed,
            school_txn=school_txn,
            game_txn=game_txn,
            resume_writes=resume_writes,
        )
        summary = [
            f"verdict={'PASS' if passed else 'FAIL'}",
            "stack=GET /api/student/state -> student_state -> "
            "_require_active_live_attendee -> touch_live_session_heartbeat -> "
            "_resume_live_attendee",
            "stack=POST /api/student/heartbeat -> touch_live_session_heartbeat -> "
            "_resume_live_attendee",
            f"artifact={TRANSFORMATIONS_ARTIFACT_ID} media={C2_TRANSFORM_MEDIA_URL} "
            "group_q=false",
            f"repro=N={INCIDENT_CLASS} x {INCIDENT_WAVES} waves, "
            f"in-flight={GUNICORN_THREADS}, heartbeat write window forced to 0s",
            f"resume_writes={resume_writes}",
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
        SQLITE_MD.parent.mkdir(parents=True, exist_ok=True)
        SQLITE_MD.write_text(markdown, encoding="utf-8")
        SQLITE_LOG.write_text(log_text, encoding="utf-8")
        self.assertEqual(errors, [], errors[:6])
        self.assertEqual(lock_logs, [])
        self.assertFalse(school_txn)
        self.assertFalse(game_txn)
        per_wave = INCIDENT_CLASS
        self.assertEqual(codes["state:200"], per_wave * INCIDENT_WAVES)
        self.assertEqual(codes["heartbeat:200"], per_wave * INCIDENT_WAVES)
        self.assertEqual(codes["media:200"], per_wave * INCIDENT_WAVES)
        self.assertEqual(codes["staff-state:200"], INCIDENT_WAVES)
        self.assertNotIn("state:500", codes)
        self.assertNotIn("heartbeat:500", codes)
        self.assertNotIn("heartbeat:503", codes)
        self.assertGreaterEqual(resume_writes, polls)


def _report_markdown(
    *,
    wall_ms: float,
    game_state_calls: int,
    codes: Counter[str],
    latencies: dict[str, list[float]],
    errors: list[str],
    passed: bool,
) -> str:
    """Build the ops report for one Artifact open wave.

    Args:
        wall_ms: Wall time for the gated wave.
        game_state_calls: Full ``game_state`` rebuilds during the wave.
        codes: ``label:status`` counts.
        latencies: Milliseconds by label.
        errors: Failure lines. Empty when the wave stayed healthy.
        passed: True when the bar was met.

    Returns:
        Markdown report.
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
    verdict = "PASS" if passed else "FAIL"
    return f"""# Artifact group-work load ({verdict})

Re-run:

```bash
python3 -m unittest discover -s lms -p 'test_artifact_load.py' -v
```

Writes this file and `lc-qa/artifact-load-latest.log`.

## What failed in class

On alc, opening an **Artifact for group work** returned Internal Server Error / server overloaded for the teacher and students. #115 and #116 kept idle `/state` at 200. They did not cover Artifact mint, active media, or Group Q preview fanout.

Production is gunicorn **1 worker, 2 threads** on a **shared-cpu-1x / 1gb** Fly machine (`fly.toml`, `lms/Dockerfile`). No Fly deploy in this change.

## Cause

`live_session_teacher_state_payload` called `_named_teams_for_live_session` → full `game_state` (roster, scores, career totals, moods) on **every** teacher-state read once groups existed.

One student `/api/student/state` after a Group Q mint did that about **25 times**. A class of 16, plus previews and the teacher mint, did it about **600 times** in one wave. That pegs the shared CPU on the 2-thread worker. An exception then renders Flask's default HTML 500, whose text is: "Internal Server Error … Either the server is overloaded or there is an error in the application." That is the page teacher and students saw. It is not the #115/#116 idle `/state` path.

Group Q team checks and the session timer used the same full rebuild.

## Fix

- Teacher-state only asks whether a named team exists, via one locked membership read (cached 0.5s for the poll wave).
- Group Q teammate checks use that index. They do not rebuild the game.
- The student SessionTimer reads the open game's clock columns.
- The student prompt builds the question list once per poll.
- Mint, active-media, and artifact-preview failures return JSON `503` `retry: true` instead of an HTML 500.

## This run

| | |
|---|---|
| Verdict | **{verdict}** |
| Class | {CLASS_SIZE} students + teacher |
| In-flight cap | {GUNICORN_THREADS} (gunicorn threads) |
| Wall | {wall_ms:.0f} ms |
| `game_state` calls | {game_state_calls} (budget {WAVE_GAME_STATE_BUDGET}) |
| HTTP | {code_line} |

| Path | n | med ms | p95 ms | max ms |
|---|---:|---:|---:|---:|
{chr(10).join(latency_rows)}

### Errors

{error_block}

## Residual

- Fly machine size is unchanged: shared-cpu-1x, 1 GB, 2 threads. A different heavy path can still saturate that VM. This wave no longer rebuilds the game per teacher-state read.
- Staff heavy `/state` is still the #115 path (field isolation, 200). This test rides one heavy staff poll in the same wave and expects 200.
- The 0.5s membership cache can lag a team edit by one student poll. Artifact open does not edit teams.
- Not smoked on Fly. Re-run this test on tip `:8787` only if you want the same protocol against the dev server; the in-process bar above is the regression lock.
- The sqlite lock bar is `lc-qa/artifact-load-sqlite.md` (N=12 state + heartbeat).
"""


def _sqlite_report(
    *,
    wall_ms: float,
    codes: Counter[str],
    latencies: dict[str, list[float]],
    errors: list[str],
    lock_logs: list[str],
    passed: bool,
    school_txn: bool,
    game_txn: bool,
    resume_writes: int,
) -> str:
    """Build the ops log for the sqlite-lock incident shape.

    Args:
        wall_ms: Wall time for every wave.
        codes: ``label:status`` counts.
        latencies: Milliseconds by label.
        errors: Failure lines.
        lock_logs: Logged ``database is locked`` lines.
        passed: True when the bar was met.
        school_txn: School connection left inside a transaction.
        game_txn: Game connection left inside a transaction.
        resume_writes: Calls to ``_resume_live_attendee`` during the waves.

    Returns:
        Markdown report with repro steps, N, timings, and PASS counts.
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
    return f"""# Artifact sqlite lock ({verdict})

Re-run:

```bash
python3 -m unittest discover -s lms -p 'test_artifact_load.py' -v
```

Writes this file and `lc-qa/artifact-load-sqlite.log`.

## Repro (alc incident note)

Source: `lc-qa/incident-alc-artifact-overload-2026-09-21.md`. Session **11**, class 8, v148 / `1849339`, N=12 still in, Artifact prompts 16–18.

Fly stack, both routes, same write:

1. `GET /api/student/state` → `student_state` → `_require_active_live_attendee` → `touch_live_session_heartbeat` → `_resume_live_attendee` (`UPDATE live_session_attendees`)
2. `POST /api/student/heartbeat` → `touch_live_session_heartbeat` → `_resume_live_attendee`

Artifact on screen: `{TRANSFORMATIONS_ARTIFACT_ID}` at `{C2_TRANSFORM_MEDIA_URL}`. Incident payloads had **`group_q: false`** (individual Match challenges). `live_group_members` / `live_group_responses` were 0. This run mints the same way.

The 20s heartbeat skip is forced to 0 so every poll takes that UPDATE. Production only writes when the last beat is older than 20s; the class still reached this line.

Pass bar: resume writes cover every state and heartbeat 200, 0 `database is locked`, 0 HTTP 500, 0 “Internal Server Error” / “server overloaded”, 0 open transactions.

## Why not Postgres

Postgres would be a second source of truth and a service this app does not run. The file is already the live-class store, mounted on one machine. The durable fix is to stop the two connections from holding a reserved lock across the resume UPDATE.

## Cause

`_resume_live_attendee` writes `last_heartbeat_at` on the school connection. Student `/state` and heartbeat both call it while other work uses the game-show connection on the same file. Legacy isolation kept that UPDATE’s reserved lock until `commit()`, so the other connection raised `sqlite3.OperationalError: database is locked`. Flask rendered Internal Server Error / “the server is overloaded”. Not OOM. #115 and #116 did not cover this write.

## Fix

- Both connections use autocommit, so a statement releases the write lock when it returns.
- Both connections share one process lock, so the two gunicorn threads do not interleave one connection.
- WAL + `busy_timeout` stay. `synchronous=NORMAL` is the WAL companion so a heartbeat fsync does not sit on the lock.
- If a lock still escapes, heartbeat returns JSON 503 `retry: true` and student `/state` returns the reconnect stub. The student page keeps the last Artifact frame and shows Reconnecting… / Retry. This run expects those branches not to fire.

## This run

| | |
|---|---|
| Verdict | **{verdict}** |
| Class | {INCIDENT_CLASS} students |
| Waves | {INCIDENT_WAVES} |
| In-flight cap | {GUNICORN_THREADS} |
| Wall | {wall_ms:.0f} ms |
| sqlite lock logs | {len(lock_logs)} |
| `_resume_live_attendee` writes | {resume_writes} |
| school in_transaction | {school_txn} |
| game in_transaction | {game_txn} |
| HTTP | {code_line} |

| Path | n | med ms | p95 ms | max ms |
|---|---:|---:|---:|---:|
{chr(10).join(latency_rows)}

### Errors

{error_block}

### Lock logs

{lock_block}

## Residual

- Fly machine size is unchanged (shared-cpu-1x, 1 GB, 2 threads). Not smoked on Fly.
- A second process on the same file can still wait on `busy_timeout` (30s). Production runs one gunicorn worker.
- The 0.5s team-membership cache can lag a team edit by one poll.
"""


if __name__ == "__main__":
    unittest.main()

"""Teacher-only engagement event log (MCK-52 P0.1-P0.3).

One append-only table, ``engagement_events``, that records who did what in
a live class: votes, submits, draft edits, ink batches, presence changes and
so on. End Live Class and Quit purge the live tables; this log is kept.

Rules the code enforces:

* Rows carry counts and flags only. Never answer text or ink points.
* Students are named by first name only (plus the roster id).
* Writes are server-side, after the route's own write succeeded. A failed
  event write is logged and swallowed; it never fails the student action.
* A write never extends a live poll: when the thread's poll budget
  (``serve_capacity.ensure_poll_budget``) is spent, the event is dropped.
* There is no student-facing read. Only staff routes call ``list_for_class``.

Postgres is the store when live presence is attached, sqlite otherwise,
the same split as ``board_ops``.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable, Sequence
from zoneinfo import ZoneInfo

from engagement_rollup import rollup_ddl_postgres_parts, rollup_ddl_sqlite

logger = logging.getLogger(__name__)

KINDS = frozenset(
    {
        "present.join", "present.leave", "present.stale", "present.resume",
        "answer.submit", "answer.change",
        "group.vote", "group.draft_edit", "group.submit", "group.finalize",
        "board.ink_batch", "board.text",
        "artifact.move", "artifact.answer",
        "points.student", "points.team",
        "lookfor", "note", "checkin.mood", "checkin.avatar", "exit.feedback",
        "item.offered",
    }
)
MAX_PAYLOAD_BYTES = 500
# Placeholder until a 30-seat smoke: one artifact.move per student per item per window.
ARTIFACT_MOVE_WINDOW_S = 10.0
# Payload keys that could carry answer text or ink. Always stripped.
_BANNED_KEYS = frozenset(
    {"text", "answer", "why", "why_text", "response", "points", "pts", "ink", "body", "comment", "snapshot"}
)
_DDL_LOCK = 87421352
_TZ = ZoneInfo("America/Toronto")

_COLUMNS = (
    "class_id, live_session, run_key, student_id, first_name, team_id, item_ref, kind, ts, payload"
)
_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS engagement_events (
    id INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL,
    live_session INTEGER,
    run_key TEXT NOT NULL DEFAULT '',
    student_id INTEGER,
    first_name TEXT NOT NULL DEFAULT '',
    team_id INTEGER,
    item_ref TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    ts TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS engagement_events_class_ts
    ON engagement_events (class_id, ts);
CREATE INDEX IF NOT EXISTS engagement_events_session
    ON engagement_events (live_session, kind);
"""
# Session ids are reused after a wipe; ``run_key`` (live_class_sessions.run_key,
# a uuid minted per run) is what tells runs apart. Added after the first cut.
_RUN_KEY_INDEX = (
    "CREATE INDEX IF NOT EXISTS engagement_events_run "
    "ON engagement_events (run_key, student_id, item_ref, kind)"
)
_POSTGRES_DDL = _SQLITE_DDL.replace("id INTEGER PRIMARY KEY", "id BIGSERIAL PRIMARY KEY")


def now_iso() -> str:
    """Server time in Toronto, ISO seconds."""
    return datetime.now(_TZ).isoformat(timespec="seconds")


def clean_payload(payload: dict[str, Any] | None) -> str:
    """Return small JSON with answer-like keys removed.

    Args:
        payload: Counts and flags.

    Returns:
        JSON text no longer than ``MAX_PAYLOAD_BYTES`` (``{}`` when too big).
    """
    safe: dict[str, Any] = {}
    for key, value in (payload or {}).items():
        name = str(key)
        if name in _BANNED_KEYS:
            continue
        if isinstance(value, (bool, int, float)) or value is None:
            safe[name] = value
        elif isinstance(value, str) and len(value) <= 40:
            safe[name] = value
    raw = json.dumps(safe, sort_keys=True, separators=(",", ":"))
    return raw if len(raw.encode("utf-8")) <= MAX_PAYLOAD_BYTES else "{}"


def first_name_only(name: Any) -> str:
    """First word of a name, capped. Never a surname."""
    parts = str(name or "").strip().split()
    return parts[0][:40] if parts else ""


class _Store:
    """Shared append/read logic over an ``exec`` callable."""

    def _write(self, sql: str, params: tuple[Any, ...]) -> None:
        raise NotImplementedError

    def _fetch(self, sql: str, params: tuple[Any, ...]) -> list[Any]:
        raise NotImplementedError

    def append(self, row: dict[str, Any]) -> None:
        """Insert one event row (already validated)."""
        self._write(
            f"INSERT INTO engagement_events ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(row["class_id"]),
                row.get("live_session"),
                str(row.get("run_key") or "")[:80],
                row.get("student_id"),
                row.get("first_name") or "",
                row.get("team_id"),
                str(row.get("item_ref") or "")[:120],
                str(row["kind"]),
                row.get("ts") or now_iso(),
                row.get("payload") or "{}",
            ),
        )

    def has_event(self, run_key: str, student_id: int, item_ref: Any, kinds: tuple[str, ...]) -> bool:
        """True when this run already has one of ``kinds`` for the student and item."""
        if not run_key:
            return False
        marks = ",".join("?" * len(kinds))
        rows = self._fetch(
            "SELECT 1 FROM engagement_events WHERE run_key = ? AND student_id = ? "
            f"AND item_ref = ? AND kind IN ({marks}) LIMIT 1",
            (str(run_key), int(student_id), str(item_ref), *kinds),
        )
        return bool(rows)

    _ROLLUP_COLS = (
        "class_id, live_session, run_key, rolled_at, scope, student_id, team_id, "
        "signal, count, offered, share_of_group, share_vs_even, payload"
    )

    def _rollup_insert_params(self, row: dict[str, Any]) -> tuple[Any, ...]:
        """Bind values for one ``engagement_rollup`` insert."""
        return (
            int(row["class_id"]),
            row.get("live_session"),
            str(row["run_key"]),
            row.get("rolled_at"),
            str(row["scope"]),
            row.get("student_id"),
            row.get("team_id"),
            str(row["signal"]),
            int(row.get("count") or 0),
            row.get("offered"),
            row.get("share_of_group"),
            row.get("share_vs_even"),
            row.get("payload") or "{}",
        )

    def _rollup_insert_sql(self) -> str:
        """``INSERT`` with ``ON CONFLICT DO NOTHING`` for rollup idempotency."""
        return (
            f"INSERT INTO engagement_rollup ({self._ROLLUP_COLS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT DO NOTHING"
        )

    def insert_rollup_batch(self, rows: Sequence[dict[str, Any]]) -> int:
        """Insert a full rollup in one transaction; ignore duplicate cells.

        Returns:
            Number of rows actually inserted (0 when this run was already rolled up).
        """
        raise NotImplementedError

    def list_rollup(
        self,
        class_id: int,
        *,
        run_key: str | None = None,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        """Rollup rows for one class, oldest insert first."""
        sql = (
            "SELECT id, class_id, live_session, run_key, rolled_at, scope, student_id, team_id, "
            "signal, count, offered, share_of_group, share_vs_even, payload "
            "FROM engagement_rollup WHERE class_id = ?"
        )
        params: list[Any] = [int(class_id)]
        if run_key is not None:
            sql += " AND run_key = ?"
            params.append(str(run_key))
        sql += " ORDER BY id LIMIT ?"
        params.append(max(1, min(int(limit), 20000)))
        out = []
        for raw in self._fetch(sql, tuple(params)):
            row = dict(raw) if not isinstance(raw, dict) else raw
            try:
                row["payload"] = json.loads(row.get("payload") or "{}")
            except (TypeError, ValueError):
                row["payload"] = {}
            out.append(row)
        return out

    def list_for_class(
        self,
        class_id: int,
        *,
        live_session: int | None = None,
        run_key: str | None = None,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        """Events for one class, oldest first. Staff use only.

        ``run_key`` picks one run. ``live_session`` alone can span several
        runs because sqlite reuses session ids; callers resolve it to a key.
        """
        sql = f"SELECT id, {_COLUMNS} FROM engagement_events WHERE class_id = ?"
        params: list[Any] = [int(class_id)]
        if run_key is not None:
            sql += " AND run_key = ?"
            params.append(str(run_key))
        if live_session is not None:
            sql += " AND live_session = ?"
            params.append(int(live_session))
        sql += " ORDER BY id LIMIT ?"
        params.append(max(1, min(int(limit), 10000)))
        out = []
        for raw in self._fetch(sql, tuple(params)):
            row = dict(raw) if not isinstance(raw, dict) else raw
            try:
                row["payload"] = json.loads(row.get("payload") or "{}")
            except (TypeError, ValueError):
                row["payload"] = {}
            out.append(row)
        return out


class SqliteEngagementEvents(_Store):
    """Event log on a sqlite connection (local dev, CI)."""

    def __init__(self, conn: Any, lock: threading.Lock | None = None) -> None:
        self.conn = conn
        self.lock = lock or threading.Lock()

    def ensure_schema(self) -> None:
        with self.lock:
            self.conn.executescript(_SQLITE_DDL)
            self.conn.executescript(rollup_ddl_sqlite())
            cols = {str(r[1]) for r in self.conn.execute("PRAGMA table_info(engagement_events)")}
            if "run_key" not in cols:
                self.conn.execute(
                    "ALTER TABLE engagement_events ADD COLUMN run_key TEXT NOT NULL DEFAULT ''"
                )
            self.conn.execute(_RUN_KEY_INDEX)

    def _write(self, sql: str, params: tuple[Any, ...]) -> None:
        with self.lock:
            self.conn.execute(sql, params)
            if self.conn.in_transaction:
                self.conn.commit()

    def _fetch(self, sql: str, params: tuple[Any, ...]) -> list[Any]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def insert_rollup_batch(self, rows: Sequence[dict[str, Any]]) -> int:
        """Insert rollup cells on sqlite (autocommit per statement).

        The school connection uses ``isolation_level = None`` so an explicit
        ``BEGIN`` would hold the file write lock across the whole batch and
        starve the game-show connection on the same file. Each
        ``INSERT … ON CONFLICT DO NOTHING`` is still atomic; the unique
        index makes a concurrent End safe.
        """
        if not rows:
            return 0
        sql = self._rollup_insert_sql()
        with self.lock:
            inserted = 0
            for row in rows:
                cur = self.conn.execute(sql, self._rollup_insert_params(row))
                inserted += int(cur.rowcount or 0)
        return inserted


class PostgresEngagementEvents(_Store):
    """Event log on the live-presence Postgres pool."""

    def __init__(self, presence: Any) -> None:
        self.presence = presence

    def ensure_schema(self) -> None:
        from live_presence import ddl_race

        with self.presence._conn() as wrapped:
            raw = wrapped.connection
            with raw.transaction():
                raw.execute("SELECT pg_advisory_xact_lock(%s)", (_DDL_LOCK,))
                ddl = _POSTGRES_DDL.split(";") + [
                    "ALTER TABLE engagement_events ADD COLUMN IF NOT EXISTS "
                    "run_key TEXT NOT NULL DEFAULT ''",
                    _RUN_KEY_INDEX,
                ]
                for part in ddl + rollup_ddl_postgres_parts():
                    sql = part.strip()
                    if not sql:
                        continue
                    raw.execute("SAVEPOINT ddl_step")
                    try:
                        raw.execute(sql)
                    except Exception as exc:
                        raw.execute("ROLLBACK TO SAVEPOINT ddl_step")
                        if not ddl_race(exc):
                            raise
                    else:
                        raw.execute("RELEASE SAVEPOINT ddl_step")

    def _write(self, sql: str, params: tuple[Any, ...]) -> None:
        with self.presence._conn() as wrapped:
            wrapped.connection.execute(sql.replace("?", "%s"), params)

    def _fetch(self, sql: str, params: tuple[Any, ...]) -> list[Any]:
        with self.presence._conn() as wrapped:
            return wrapped.connection.execute(sql.replace("?", "%s"), params).fetchall()

    def insert_rollup_batch(self, rows: Sequence[dict[str, Any]]) -> int:
        """Insert every rollup cell atomically on Postgres."""
        if not rows:
            return 0
        sql = self._rollup_insert_sql().replace("?", "%s")
        inserted = 0
        with self.presence._conn() as wrapped:
            with wrapped.connection.transaction():
                for row in rows:
                    cur = wrapped.connection.execute(sql, self._rollup_insert_params(row))
                    inserted += int(cur.rowcount or 0)
        return inserted


class MoveThrottle:
    """At most one event per key per window, in process memory."""

    def __init__(self, window_s: float = ARTIFACT_MOVE_WINDOW_S) -> None:
        self.window_s = window_s
        self._last: dict[tuple[Any, ...], float] = {}
        self._lock = threading.Lock()

    def allow(self, key: tuple[Any, ...], now: float | None = None) -> bool:
        t = time.monotonic() if now is None else now
        with self._lock:
            last = self._last.get(key)
            if last is not None and t - last < self.window_s:
                return False
            self._last[key] = t
            if len(self._last) > 20000:
                cutoff = t - self.window_s
                self._last = {k: v for k, v in self._last.items() if v >= cutoff}
            return True


Extractor = Callable[[Any, dict[str, Any], Any], "dict[str, Any] | list[dict[str, Any]] | None"]


def emits(kind: str, extract: Extractor | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorate a ``SchoolDB`` write so it logs an engagement event on success.

    The wrapped method runs first. Only when it returns normally is the
    event written, and any failure in the event path is logged, never raised.

    Args:
        kind: Default event kind.
        extract: ``(self, bound_args, result) -> event dict(s) | None``. The
            dict may set ``session_id``, ``student_id``, ``team_id``,
            ``item_ref``, ``kind`` and ``payload``. Default reads
            ``session_id``, ``student_id`` and ``live_item_id`` arguments.
    """

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            result = fn(self, *args, **kwargs)
            try:
                bound = sig.bind(self, *args, **kwargs)
                bound.apply_defaults()
                params = dict(bound.arguments)
                if extract is None:
                    events: Any = {
                        "session_id": params.get("session_id"),
                        "student_id": params.get("student_id"),
                        "item_ref": params.get("live_item_id"),
                    }
                else:
                    events = extract(self, params, result)
                for event in events if isinstance(events, list) else [events]:
                    if event:
                        self.record_engagement_event(
                            event.pop("kind", kind), **event
                        )
            except Exception:  # noqa: BLE001 - logging must never break a route
                logger.exception("engagement event %s failed", kind)
            return result

        return wrapper

    return deco

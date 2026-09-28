"""Postgres source of truth for live-class presence.

Student ``/state`` and ``/heartbeat`` refresh ``last_heartbeat_at`` while an
Artifact is open. On Fly alc that UPDATE shares ``lloves.sqlite`` with the
Math Game Show connection and raises ``database is locked``. When
``LIVE_DATABASE_URL`` or a postgres ``DATABASE_URL`` is set, heartbeat
writes, stale sweeps, and presence reads for that hot path go here.

Sqlite remains the catalogue (users, rosters, prompts, grades). Machines
without a URL keep the WAL / autocommit path in ``school_db``.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_SCHEMA_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# PgBouncer ``client_idle_timeout`` on Fly Managed Postgres closes a client
# that has not issued a query. This pool is LIFO inside each worker.
# Production is 4 gunicorn workers × 8 threads. This pool stays at four
# connections per worker, so one connection stays hot on 1–4s polls while a
# sibling sits until the next burst and then raises
# ``ProtocolViolation: client_idle_timeout``. Extra threads wait on the
# semaphore and the poll returns retry JSON instead of opening more sockets.
# Recycle anything idle this long before the pooler does.
_MAX_IDLE_SECONDS = 15.0
# Milliseconds. A silent dead socket must not hold a gunicorn thread until
# the 120s worker timeout.
_TCP_USER_TIMEOUT_MS = 8000
_POOL_WAIT_SECONDS = 2.0
_PRESENCE_LOG_INTERVAL_S = 10.0
_RECONNECTABLE_ERRORS = frozenset(
    {
        "ProtocolViolation",
        "OperationalError",
        "InterfaceError",
        "AdminShutdown",
        "ConnectionDoesNotExist",
        "ConnectionFailure",
        "ConnectionException",
        "SqlclientUnableToEstablishSqlconnection",
    }
)

_presence_log_at = 0.0
_presence_log_lock = threading.Lock()

_DDL = """
CREATE TABLE IF NOT EXISTS live_presence_sessions (
    id BIGINT PRIMARY KEY,
    class_id BIGINT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS live_presence_attendees (
    id BIGINT PRIMARY KEY,
    live_session_id BIGINT NOT NULL
        REFERENCES live_presence_sessions(id) ON DELETE CASCADE,
    student_id BIGINT,
    participant_uuid TEXT NOT NULL DEFAULT '',
    visit_token TEXT NOT NULL,
    codename TEXT NOT NULL DEFAULT '',
    unmatched INTEGER NOT NULL DEFAULT 0,
    joined_at TEXT NOT NULL,
    left_at TEXT,
    last_heartbeat_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS live_presence_attendees_token
    ON live_presence_attendees (visit_token);
CREATE INDEX IF NOT EXISTS live_presence_attendees_session
    ON live_presence_attendees (live_session_id);
"""


class LivePresenceUnavailable(Exception):
    """Postgres could not serve a live poll.

    Callers on student ``/state`` and heartbeat turn this into the same
    JSON retry the sqlite lock path already uses, so the browser keeps
    the last Artifact frame.
    """


def note_presence_blip(detail: str) -> None:
    """Log a presence blip at most once every few seconds.

    A full traceback on every student poll pegged the shared-cpu worker.
    Callers keep the last frame and retry. This records that the blip
    happened without writing a stack per request.

    Args:
        detail: Short reason. No traceback.
    """
    global _presence_log_at
    now = time.monotonic()
    with _presence_log_lock:
        if now - _presence_log_at < _PRESENCE_LOG_INTERVAL_S:
            return
        _presence_log_at = now
    logger.warning("live presence unavailable: %s", detail)


class _PresenceConnection:
    """Checked-out connection that opens a fresh one after an idle drop.

    PgBouncer answers the next query on a killed client with
    ``ProtocolViolation: client_idle_timeout``. One retry uses a new
    connection so the poll succeeds. A second failure propagates.
    """

    def __init__(self, store: "LivePresenceStore", connection: Any) -> None:
        """Wrap one autocommit connection.

        Args:
            store: Pool that can open a replacement.
            connection: psycopg connection.
        """
        self._store = store
        self.connection = connection
        self._retried = False

    def execute(self, query: str, params: Any = None, **kwargs: Any) -> Any:
        """Run one statement, reconnecting once if the pooler dropped us.

        Args:
            query: SQL text.
            params: Bind values, or ``None``.
            **kwargs: Passed to psycopg ``Connection.execute``.

        Returns:
            The cursor from the attempt that ran.

        Raises:
            Exception: The driver error when the fresh connection also fails,
            or when the error is not a dropped connection.
        """
        try:
            return self.connection.execute(query, params, **kwargs)
        except Exception as exc:
            if self._retried or not _is_reconnectable(exc):
                raise
            self._retried = True
            note_presence_blip(f"recycled dead connection ({type(exc).__name__})")
            self._store._close_quietly(self.connection)
            self.connection = None
            self.connection = self._store._connect()
            return self.connection.execute(query, params, **kwargs)


def resolve_live_database_url(explicit: str | None = None) -> str:
    """Return the Postgres URL for live presence, or ``""`` when unset.

    An explicit string wins, including ``""`` which forces sqlite even if
    the environment has a URL. Otherwise ``LIVE_DATABASE_URL`` is preferred.
    A generic ``DATABASE_URL`` counts only when it is a postgres URL, which
    is what ``fly postgres attach`` injects.

    Args:
        explicit: Caller override. ``None`` reads the environment.

    Returns:
        Connection string, or empty when the hot path stays on sqlite.
    """
    import os

    if explicit is not None:
        return str(explicit).strip()
    live = os.getenv("LIVE_DATABASE_URL", "").strip()
    if live:
        return live
    generic = os.getenv("DATABASE_URL", "").strip()
    if generic.startswith(("postgres://", "postgresql://")):
        return generic
    return ""


def _parse_iso(raw: Any) -> datetime | None:
    """Parse an ISO timestamp stored beside sqlite ``_now()`` values.

    Args:
        raw: ISO datetime string or blank.

    Returns:
        Naive datetime, or ``None`` when unparseable.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _attendee_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Copy one presence row without the joined session status.

    Args:
        row: ``live_presence_attendees`` mapping, possibly joined.

    Returns:
        Attendee dict shaped like ``live_session_attendees``.
    """
    item = dict(row)
    item.pop("session_status", None)
    sid = item.get("student_id")
    item["student_id"] = int(sid) if sid not in (None, "") else None
    item["id"] = int(item["id"])
    item["live_session_id"] = int(item["live_session_id"])
    item["unmatched"] = int(item.get("unmatched") or 0)
    item["participant_uuid"] = str(item.get("participant_uuid") or "")
    item["visit_token"] = str(item.get("visit_token") or "")
    item["codename"] = str(item.get("codename") or "")
    return item


class LivePresenceStore:
    """Postgres rows for live session status and attendee heartbeats.

    One process checks out a connection per call. Production is four gunicorn
    workers and eight threads each; this pool stays at four connections per
    worker so a burst cannot open a socket per thread. Statements are autocommit so a heartbeat
    does not hold a transaction open across the poll. Idle connections are
    closed before Fly's pooler ``client_idle_timeout`` can turn the next poll
    into a protocol error. Successful and failed checkouts update
    ``cached_label`` for ``/health``, which does not ping.
    """

    def __init__(self, dsn: str, *, schema: str | None = None, pool_size: int = 4) -> None:
        """Open a pool and ensure the presence tables exist.

        Args:
            dsn: Postgres connection string.
            schema: Optional schema (tests). Production uses ``public``.
            pool_size: Max checked-out connections.

        Raises:
            LivePresenceUnavailable: Connect or DDL failed.
            ValueError: ``schema`` is not a plain identifier.
        """
        cleaned = (dsn or "").strip()
        if not cleaned:
            raise ValueError("live presence DSN is empty")
        if schema is not None and not _SCHEMA_NAME.fullmatch(schema):
            raise ValueError(f"invalid presence schema: {schema!r}")
        self.dsn = cleaned
        self.schema = schema
        self.heartbeat_writes = 0
        self._pool_size = max(1, int(pool_size))
        self._sem = threading.BoundedSemaphore(self._pool_size)
        self._idle: list[Any] = []
        self._idle_lock = threading.Lock()
        self._sweep_at: dict[int, float] = {}
        self._sweep_lock = threading.Lock()
        self._ping_at = 0.0
        self._ping_ok = False
        self._closed = False
        self.ensure_schema()

    def close(self) -> None:
        """Close pooled connections."""
        self._closed = True
        with self._idle_lock:
            idle = list(self._idle)
            self._idle.clear()
        for conn, _returned_at in idle:
            self._close_quietly(conn)

    def cached_label(self) -> str:
        """Return ``postgres`` or ``postgres-down`` without a query.

        ``/health`` reads this. A checkout updates it. A store that has not
        checked out yet reports ``postgres`` — boot runs DDL through
        ``_conn`` before the app serves.

        Returns:
            Cached presence mode.
        """
        if self._ping_at and not self._ping_ok:
            return "postgres-down"
        return "postgres"

    def _note_checkout(self, *, ok: bool) -> None:
        """Remember the last checkout for ``cached_label``.

        Args:
            ok: True when the statement finished on a live connection.
        """
        self._ping_ok = bool(ok)
        self._ping_at = time.monotonic()

    def ping(self) -> bool:
        """True when ``SELECT 1`` succeeds. Cached for 30 seconds.

        ``/health`` must not call this. Poll traffic and this method share
        ``cached_label``.

        Returns:
            Whether the store answered.
        """
        now = time.monotonic()
        if self._ping_ok and now - self._ping_at < 30:
            return True
        try:
            with self._conn() as conn:
                conn.execute("SELECT 1")
        except LivePresenceUnavailable:
            self._ping_ok = False
        else:
            self._ping_ok = True
        self._ping_at = now
        return self._ping_ok

    def ensure_schema(self) -> None:
        """Create presence tables if this database does not have them yet."""
        statements = [
            part.strip()
            for part in _DDL.split(";")
            if part.strip()
        ]
        with self._conn() as conn:
            for statement in statements:
                conn.execute(statement)

    def upsert_session(self, session_row: dict[str, Any]) -> None:
        """Insert or update one live session's status.

        Args:
            session_row: ``live_class_sessions`` mapping.
        """
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO live_presence_sessions (
                    id, class_id, status, started_at, ended_at
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    class_id = EXCLUDED.class_id,
                    status = EXCLUDED.status,
                    started_at = EXCLUDED.started_at,
                    ended_at = EXCLUDED.ended_at
                """,
                (
                    int(session_row["id"]),
                    int(session_row["class_id"]),
                    str(session_row.get("status") or "active"),
                    session_row.get("started_at"),
                    session_row.get("ended_at"),
                ),
            )

    def upsert_attendee(self, row: dict[str, Any]) -> dict[str, Any]:
        """Insert or replace one attendee presence row.

        Args:
            row: Attendee mapping (sqlite or presence shaped).

        Returns:
            The stored attendee row.
        """
        token = str(row.get("visit_token") or "").strip()
        if not token:
            raise LivePresenceUnavailable("attendee is missing a visit token")
        sid = row.get("student_id")
        student_id = int(sid) if sid not in (None, "") else None
        with self._conn() as conn:
            stored = conn.execute(
                """
                INSERT INTO live_presence_attendees (
                    id, live_session_id, student_id, participant_uuid,
                    visit_token, codename, unmatched, joined_at, left_at,
                    last_heartbeat_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    live_session_id = EXCLUDED.live_session_id,
                    student_id = EXCLUDED.student_id,
                    participant_uuid = EXCLUDED.participant_uuid,
                    visit_token = EXCLUDED.visit_token,
                    codename = EXCLUDED.codename,
                    unmatched = EXCLUDED.unmatched,
                    joined_at = EXCLUDED.joined_at,
                    left_at = EXCLUDED.left_at,
                    last_heartbeat_at = EXCLUDED.last_heartbeat_at
                RETURNING *
                """,
                (
                    int(row["id"]),
                    int(row["live_session_id"]),
                    student_id,
                    str(row.get("participant_uuid") or ""),
                    token,
                    str(row.get("codename") or ""),
                    1 if int(row.get("unmatched") or 0) else 0,
                    str(row.get("joined_at") or ""),
                    row.get("left_at"),
                    row.get("last_heartbeat_at"),
                ),
            ).fetchone()
        if stored is None:
            raise LivePresenceUnavailable("presence upsert returned no row")
        return _attendee_payload(stored)

    def get_by_token(self, token: str) -> dict[str, Any] | None:
        """Return one attendee plus ``session_status``, or ``None``.

        Args:
            token: ``visit_token``.
        """
        cleaned = (token or "").strip()
        if not cleaned:
            return None
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT a.*, s.status AS session_status
                FROM live_presence_attendees a
                JOIN live_presence_sessions s ON s.id = a.live_session_id
                WHERE a.visit_token = %s
                LIMIT 1
                """,
                (cleaned,),
            ).fetchone()
        if row is None:
            return None
        item = _attendee_payload(row)
        item["session_status"] = str(row.get("session_status") or "")
        return item

    def get_by_id(self, attendee_id: int) -> dict[str, Any] | None:
        """Return one attendee by primary key.

        Args:
            attendee_id: ``live_presence_attendees.id``.
        """
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM live_presence_attendees
                WHERE id = %s
                """,
                (int(attendee_id),),
            ).fetchone()
        return _attendee_payload(row) if row else None

    def present_count(self, session_id: int) -> int:
        """Count attendees with no ``left_at`` in this session.

        Args:
            session_id: ``live_presence_sessions.id``.
        """
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM live_presence_attendees
                WHERE live_session_id = %s AND left_at IS NULL
                """,
                (int(session_id),),
            ).fetchone()
        return int(row["count"] if row else 0)

    def resume_if_stale(
        self,
        attendee: dict[str, Any],
        *,
        write_window_s: float,
        stale_s: float,
        sweep_interval_s: float,
    ) -> dict[str, Any]:
        """Write ``last_heartbeat_at`` when the last beat is older than the window.

        Args:
            attendee: Row from ``get_by_token`` (includes session status).
            write_window_s: Skip the write when the last beat is newer.
            stale_s: Heartbeat age that marks someone else left.
            sweep_interval_s: Minimum seconds between sweeps for this session.

        Returns:
            Attendee row after the optional write. ``left_at`` is cleared.
        """
        last = _parse_iso(attendee.get("last_heartbeat_at"))
        fresh = (
            last is not None
            and not attendee.get("left_at")
            and write_window_s > 0
            and (datetime.now() - last).total_seconds() < write_window_s
        )
        if fresh:
            return _attendee_payload(attendee)
        session_id = int(attendee["live_session_id"])
        self.sweep(
            session_id,
            except_token=str(attendee.get("visit_token") or ""),
            stale_s=stale_s,
            min_interval_s=sweep_interval_s,
        )
        now = datetime.now().replace(microsecond=0).isoformat()
        token = str(attendee.get("visit_token") or "").strip()
        with self._conn() as conn:
            stored = conn.execute(
                """
                UPDATE live_presence_attendees
                SET left_at = NULL,
                    last_heartbeat_at = %s
                WHERE id = %s AND visit_token = %s
                RETURNING *
                """,
                (now, int(attendee["id"]), token),
            ).fetchone()
        self.heartbeat_writes += 1
        if stored is None:
            raise LivePresenceUnavailable("heartbeat update missed the attendee row")
        return _attendee_payload(stored)

    def sweep(
        self,
        session_id: int,
        *,
        except_token: str = "",
        stale_s: float,
        min_interval_s: float,
    ) -> int:
        """Set ``left_at`` when a heartbeat is older than ``stale_s``.

        Args:
            session_id: Live session id.
            except_token: Rejoin token to leave present (the caller).
            stale_s: Age in seconds.
            min_interval_s: Skip when this session was swept recently.

        Returns:
            How many attendees were marked left.
        """
        now_m = time.monotonic()
        with self._sweep_lock:
            last_sweep = self._sweep_at.get(int(session_id), 0.0)
            if now_m - last_sweep < min_interval_s:
                return 0
            self._sweep_at[int(session_id)] = now_m
        skip = (except_token or "").strip()
        stamp = datetime.now().replace(microsecond=0).isoformat()
        now = datetime.now().replace(microsecond=0)
        marked = 0
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, visit_token, last_heartbeat_at, joined_at
                FROM live_presence_attendees
                WHERE live_session_id = %s AND left_at IS NULL
                """,
                (int(session_id),),
            ).fetchall()
            for row in rows:
                token = str(row.get("visit_token") or "")
                if skip and token == skip:
                    continue
                last = _parse_iso(row.get("last_heartbeat_at") or row.get("joined_at"))
                if last is None:
                    continue
                if (now - last).total_seconds() < stale_s:
                    continue
                conn.execute(
                    """
                    UPDATE live_presence_attendees
                    SET left_at = %s
                    WHERE id = %s AND left_at IS NULL
                    """,
                    (stamp, int(row["id"])),
                )
                marked += 1
        return marked

    def mark_left(self, attendee_id: int, left_at: str) -> None:
        """Set ``left_at`` for one attendee (explicit leave).

        Args:
            attendee_id: Presence primary key.
            left_at: ISO timestamp.
        """
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE live_presence_attendees
                SET left_at = %s
                WHERE id = %s AND left_at IS NULL
                """,
                (left_at, int(attendee_id)),
            )

    def mark_session_ended(self, session_id: int, ended_at: str) -> None:
        """Mark the session ended and close still-present attendees.

        Args:
            session_id: Live session id.
            ended_at: ISO timestamp.
        """
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE live_presence_sessions
                SET status = 'ended', ended_at = %s
                WHERE id = %s
                """,
                (ended_at, int(session_id)),
            )
            conn.execute(
                """
                UPDATE live_presence_attendees
                SET left_at = COALESCE(left_at, %s)
                WHERE live_session_id = %s AND left_at IS NULL
                """,
                (ended_at, int(session_id)),
            )

    def backfill_active(self, sqlite_conn: Any) -> tuple[int, int]:
        """Copy active sqlite sessions and their attendees into Postgres.

        Used once at process start so a class already on the volume is
        present after the cutover. Heartbeats after this do not write sqlite.

        Args:
            sqlite_conn: Open school sqlite connection.

        Returns:
            ``(sessions, attendees)`` copied.
        """
        sessions = sqlite_conn.execute(
            """
            SELECT id, class_id, status, started_at, ended_at
            FROM live_class_sessions
            WHERE status = 'active'
            """
        ).fetchall()
        session_n = 0
        attendee_n = 0
        for session in sessions:
            item = dict(session)
            self.upsert_session(item)
            session_n += 1
            people = sqlite_conn.execute(
                """
                SELECT * FROM live_session_attendees
                WHERE live_session_id = ?
                """,
                (int(item["id"]),),
            ).fetchall()
            for person in people:
                self.upsert_attendee(dict(person))
                attendee_n += 1
        if session_n or attendee_n:
            logger.info(
                "live presence backfill sessions=%s attendees=%s",
                session_n,
                attendee_n,
            )
        return session_n, attendee_n

    @contextmanager
    def _conn(self) -> Iterator[_PresenceConnection]:
        """Check out one autocommit connection.

        A checkout that has been idle past ``_MAX_IDLE_SECONDS`` is closed
        and replaced. The wrapper retries one statement when the pooler has
        already dropped the socket.

        Yields:
            A connection wrapper with ``execute``.

        Raises:
            LivePresenceUnavailable: Connect, pool wait, or a server error.
        """
        if not self._sem.acquire(timeout=_POOL_WAIT_SECONDS):
            raise LivePresenceUnavailable("live presence pool busy")
        wrapped: _PresenceConnection | None = None
        try:
            raw = self._take_idle()
            if raw is None:
                raw = self._connect()
            wrapped = _PresenceConnection(self, raw)
            try:
                yield wrapped
            except Exception:
                self._close_quietly(wrapped.connection)
                wrapped.connection = None
                raise
            else:
                self._park(wrapped.connection)
                wrapped.connection = None
                self._note_checkout(ok=True)
        except LivePresenceUnavailable as exc:
            # Pool wait is a full semaphore, not a dead server. Constraint
            # failures are converted below and are not marked down either.
            if "pool busy" not in str(exc):
                self._note_checkout(ok=False)
            raise
        except Exception as exc:
            if _is_db_error(exc):
                if _is_reconnectable(exc):
                    self._note_checkout(ok=False)
                raise LivePresenceUnavailable(
                    "live presence store unavailable"
                ) from exc
            raise
        finally:
            self._sem.release()

    def _take_idle(self) -> Any:
        """Return a recently used connection, closing stale siblings.

        Returns:
            A psycopg connection, or ``None`` when the pool needs a new one.
        """
        now = time.monotonic()
        stale: list[Any] = []
        chosen: Any = None
        with self._idle_lock:
            while self._idle:
                conn, returned_at = self._idle.pop()
                if getattr(conn, "closed", False):
                    stale.append(conn)
                    continue
                if now - returned_at >= _MAX_IDLE_SECONDS:
                    stale.append(conn)
                    continue
                chosen = conn
                break
        for conn in stale:
            self._close_quietly(conn)
        return chosen

    def _park(self, conn: Any) -> None:
        """Return a live connection to the idle list.

        Args:
            conn: Connection to keep, or ``None``.
        """
        if conn is None or getattr(conn, "closed", False):
            self._close_quietly(conn)
            return
        with self._idle_lock:
            self._idle.append((conn, time.monotonic()))

    def _connect(self) -> Any:
        """Open one autocommit connection on the presence schema.

        Returns:
            psycopg connection.
        """
        import psycopg
        from psycopg.rows import dict_row

        conn = psycopg.connect(
            self.dsn,
            autocommit=True,
            row_factory=dict_row,
            connect_timeout=5,
            # Fly's pooled DATABASE_URL goes through PgBouncer (transaction
            # mode). Prepared statements are not safe on that pool.
            prepare_threshold=None,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=5,
            keepalives_count=3,
            tcp_user_timeout=_TCP_USER_TIMEOUT_MS,
        )
        if self.schema:
            conn.execute(f'SET search_path TO "{self.schema}"')
        return conn

    @staticmethod
    def _close_quietly(conn: Any) -> None:
        """Close a connection and ignore a second failure.

        Args:
            conn: psycopg connection or ``None``.
        """
        try:
            conn.close()
        except Exception:
            return


def _is_db_error(exc: BaseException) -> bool:
    """True when ``exc`` is a psycopg or connection failure.

    Args:
        exc: Exception from a presence call.
    """
    try:
        import psycopg
    except ImportError:
        return False
    return isinstance(exc, psycopg.Error)


def _is_reconnectable(exc: BaseException) -> bool:
    """True when a new connection can safely retry this statement.

    Constraint failures stay put. Idle-timeout and dropped sockets do not.

    Args:
        exc: Exception from ``execute``.
    """
    if not _is_db_error(exc):
        return False
    if type(exc).__name__ in _RECONNECTABLE_ERRORS:
        return True
    text = str(exc).lower()
    return (
        "client_idle_timeout" in text
        or "server closed the connection" in text
    )

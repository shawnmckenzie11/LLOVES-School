"""LiveNewsWire — thin SSE postcards for one live session.

Mutations stay ordinary HTTP. After a successful write the route emits a
postcard (ids, ``state_seq``, a short label). Staff and student tabs share
one ``GET /api/live/session/<sid>/events`` stream. The log is its own
sqlite file so the four gunicorn workers on the 1 GB machine see the same
tape without taking the school catalogue lock.

Each worker keeps at most ``STREAMS_PER_WORKER`` streams so half of the
eight ``gthread`` threads stay free for join, submit, and the slow
``/state`` fallback. Extra tabs get one ``busy`` event and the browser
waits. That event never means reload.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from flask import Response, jsonify, session

from auth import current_user
from student_portal import resolve_student_live_context

logger = logging.getLogger(__name__)

# Half of gunicorn's 8 threads per worker. The other half stays on HTTP.
STREAMS_PER_WORKER = 4
# Slow safety-net poll. The wire is the tap; this is not a 2s/4s stampede.
FALLBACK_POLL_MS = 20000
# How long one stream holds its thread before the browser reconnects.
STREAM_HOLD_S = 25.0
STREAM_HOLD_TESTING_S = 0.2
# Sqlite tail while this process waits for another worker's insert.
STREAM_POLL_S = 0.35
# Browser reconnect after a healthy stream ends or drops.
SSE_RETRY_MS = 1000
# Browser reconnect after a shed. Matches the slow fallback, not a stampede.
SSE_BUSY_RETRY_MS = FALLBACK_POLL_MS
RING_PER_SESSION = 200

NEWS_TYPES = frozenset(
    {
        "hello",
        "state_seq",
        "stage",
        "slide",
        "active_media",
        "flags",
        "prompt",
        "response_landed",
        "flag_work",
        "busy",
        "ping",
    }
)

# Keys allowed on the wire. Anything else (stem, roster, Artifact HTML) drops.
_FIELDS: dict[str, frozenset[str]] = {
    "hello": frozenset({"type", "state_seq", "session_id"}),
    "state_seq": frozenset({"type", "state_seq"}),
    "stage": frozenset({"type", "stage", "state_seq"}),
    "slide": frozenset({"type", "slide_index", "deck_version", "state_seq"}),
    "active_media": frozenset(
        {"type", "media_version", "artifact_id", "state_seq"}
    ),
    "flags": frozenset({"type", "flag_mask", "state_seq"}),
    "prompt": frozenset({"type", "prompt_id", "state_seq"}),
    "response_landed": frozenset(
        {"type", "scope", "count", "state_seq", "student_id", "team_id"}
    ),
    "flag_work": frozenset(
        {"type", "student_id", "team_id", "kind", "state_seq"}
    ),
    "busy": frozenset({"type", "retry"}),
    "ping": frozenset({"type"}),
}

_FLAG_KEYS = ("minds_on", "action", "consolidation")

_stream_lock = threading.Lock()
_stream_count = 0


class NewsAudience:
    """Who is holding the stream. Staff sees the teacher board; students do not."""

    def __init__(
        self,
        role: str,
        *,
        student_id: int | None = None,
    ) -> None:
        """Record the caller's role and optional roster id.

        Args:
            role: ``staff`` or ``student``.
            student_id: Roster id for a student stream. ``None`` for staff
                and unmatched guests.
        """
        self.role = role
        self.student_id = student_id


def reset_stream_budget() -> None:
    """Drop the in-process stream count. Tests call this between cases."""
    global _stream_count
    with _stream_lock:
        _stream_count = 0


def _acquire_stream() -> bool:
    """Take one stream slot, or refuse when this worker is at the cap.

    Returns:
        True when the caller owns a slot it must release.
    """
    global _stream_count
    with _stream_lock:
        if _stream_count >= STREAMS_PER_WORKER:
            return False
        _stream_count += 1
        return True


def _release_stream() -> None:
    """Return one stream slot to the worker budget."""
    global _stream_count
    with _stream_lock:
        _stream_count = max(0, _stream_count - 1)


#: ``busy_timeout`` (ms) on the tape connection once it is open.
NEWS_BUSY_TIMEOUT_MS = 2_000


def news_db_path(data_dir: Path | str) -> Path:
    """Return the sqlite tape path beside the school data directory.

    Args:
        data_dir: School ``data_dir`` (temp dir in tests, ``/data`` on Fly).
    """
    return Path(data_dir) / "live_news_wire.sqlite"


def _connect(path: Path) -> sqlite3.Connection:
    """Open the tape with WAL so four workers can append and tail.

    Args:
        path: ``live_news_wire.sqlite``.
    """
    from school_db import boot_schema_lock, enable_sqlite_wal

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=2.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        # A fresh data dir: workers race the WAL switch, and SQLite can answer
        # BUSY at once. Same lock + retry as lloves.sqlite (MCK-104, MCK-109).
        with boot_schema_lock(path):
            enable_sqlite_wal(conn, restore_timeout_ms=NEWS_BUSY_TIMEOUT_MS)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS live_news_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS live_news_session_id
                ON live_news_events(session_id, id)
                """
            )
            conn.commit()
    except BaseException:
        conn.close()
        raise
    return conn


def _clean_event(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Keep one postcard. Drop unknown types and fat keys.

    Args:
        raw: Caller event. Extra keys are ignored, not stored.

    Returns:
        A catalogue event, or ``None`` when ``type`` is not in the catalogue.
    """
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("type") or "").strip()
    allowed = _FIELDS.get(kind)
    if allowed is None:
        return None
    event: dict[str, Any] = {"type": kind}
    for key in allowed:
        if key == "type" or key not in raw:
            continue
        event[key] = raw[key]
    if kind == "busy":
        event["retry"] = True
    return event


def _as_int(value: Any, default: int = 0) -> int:
    """Parse an integer postcard field.

    Args:
        value: Raw JSON value.
        default: Used when the value is not an integer.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class LiveNewsLog:
    """Append-only postcard tape shared by every gunicorn worker."""

    def __init__(self, path: Path | str) -> None:
        """Open or create the tape.

        Args:
            path: Sqlite file. Parent directories are created.
        """
        self.path = Path(path)
        self._lock = threading.Lock()
        self._conn = _connect(self.path)

    def close(self) -> None:
        """Close the tape connection."""
        with self._lock:
            self._conn.close()

    def append(self, session_id: int, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Store one mutation's postcards and prune the session ring.

        Args:
            session_id: ``live_class_sessions.id``.
            events: Catalogue events. Fat or unknown rows are skipped.

        Returns:
            Stored events, each with an integer ``id`` for ``Last-Event-ID``.
        """
        cleaned = []
        for item in events:
            event = _clean_event(item)
            # hello, ping, and busy belong to one socket, not the resume tape.
            if event is None or event["type"] in {"hello", "ping", "busy"}:
                continue
            cleaned.append(event)
        if not cleaned:
            return []
        stored: list[dict[str, Any]] = []
        sid = int(session_id)
        with self._lock:
            for event in cleaned:
                payload = json.dumps(event, separators=(",", ":"), default=str)
                if len(payload) > 500:
                    logger.warning("live news postcard dropped; too large")
                    continue
                cursor = self._conn.execute(
                    """
                    INSERT INTO live_news_events (session_id, event_type, payload_json)
                    VALUES (?, ?, ?)
                    """,
                    (sid, event["type"], payload),
                )
                event_id = int(cursor.lastrowid or 0)
                stored.append({"id": event_id, **event})
            self._conn.execute(
                """
                DELETE FROM live_news_events
                WHERE session_id = ?
                  AND id < COALESCE((
                    SELECT id FROM live_news_events
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT 1 OFFSET ?
                  ), 0)
                """,
                (sid, sid, RING_PER_SESSION - 1),
            )
            self._conn.commit()
        return stored

    def since(self, session_id: int, after_id: int, limit: int = 50) -> list[dict[str, Any]]:
        """Return postcards newer than ``after_id``, oldest first.

        Args:
            session_id: ``live_class_sessions.id``.
            after_id: Last SSE id the client already applied. ``0`` reads the ring.
            limit: Cap so one tail cannot dump the ring into one chunk.
        """
        cap = max(1, min(int(limit), 50))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, payload_json
                FROM live_news_events
                WHERE session_id = ? AND id > ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (int(session_id), int(after_id), cap),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            payload["id"] = int(row["id"])
            out.append(payload)
        return out


_logs: dict[str, LiveNewsLog] = {}
_logs_lock = threading.Lock()


def log_for(data_dir: Path | str) -> LiveNewsLog:
    """Return the process-wide tape for one data directory.

    Args:
        data_dir: School data directory.
    """
    path = str(news_db_path(data_dir))
    with _logs_lock:
        existing = _logs.get(path)
        if existing is None:
            existing = LiveNewsLog(path)
            _logs[path] = existing
        return existing


def reset_logs() -> None:
    """Close cached tapes. Tests call this so temp dirs can disappear."""
    with _logs_lock:
        logs = list(_logs.values())
        _logs.clear()
    for log in logs:
        try:
            log.close()
        except sqlite3.Error:
            pass


def emit_session_news(
    school: Any,
    session_id: int,
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Append postcards after a successful mutation.

    Wire faults are logged and swallowed. The HTTP write already committed.

    Args:
        school: SchoolDB. Only ``data_dir`` is read.
        session_id: ``live_class_sessions.id``.
        events: Catalogue postcards for this mutation.

    Returns:
        Stored rows (may be empty when every event was rejected).
    """
    if not events:
        return []
    try:
        return log_for(school.data_dir).append(int(session_id), events)
    except Exception:
        logger.warning("live news emit failed session=%s", session_id)
        return []


def teacher_state_seq(school: Any, session_id: int) -> int:
    """Read ``state_seq`` for a postcard. Missing sessions report ``0``.

    Args:
        school: SchoolDB.
        session_id: ``live_class_sessions.id``.
    """
    try:
        state = school.live_session_teacher_state_payload(int(session_id))
    except Exception:
        return 0
    if not isinstance(state, dict):
        return 0
    return _as_int(state.get("state_seq"), 0)


def prompt_response_count(school: Any, prompt_id: int) -> int | None:
    """Count stored answers for one prompt. No roster and no answer text.

    Args:
        school: SchoolDB.
        prompt_id: ``live_session_prompts.id``.
    """
    try:
        with school._lock:
            row = school.conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM live_session_responses
                WHERE prompt_id = ?
                """,
                (int(prompt_id),),
            ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    return _as_int(row["n"], 0)


def flag_mask(state: dict[str, Any] | None) -> str:
    """Opaque bit string for round flags plus reveal/closed. Not pedagogy text.

    Args:
        state: Public teacher state. Missing flags are zeros.
    """
    body = state if isinstance(state, dict) else {}
    flags = body.get("round_flags") if isinstance(body.get("round_flags"), dict) else {}
    bits = ["1" if flags.get(key) else "0" for key in _FLAG_KEYS]
    mc = body.get("mc_ui") if isinstance(body.get("mc_ui"), dict) else {}
    bits.append("1" if mc.get("reveal") or mc.get("reveal_to_students") else "0")
    bits.append("1" if mc.get("poll_closed") else "0")
    return "".join(bits)


def events_for_teacher_patch(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Postcards for one teacher-state write. No media body and no roster.

    Args:
        before: Teacher state before the write.
        after: Teacher state the write returned.
    """
    prev = before if isinstance(before, dict) else {}
    nxt = after if isinstance(after, dict) else {}
    seq = _as_int(nxt.get("state_seq"), 0)
    events: list[dict[str, Any]] = []
    if _as_int(prev.get("state_seq"), 0) != seq:
        events.append({"type": "state_seq", "state_seq": seq})
    if str(prev.get("stage") or "") != str(nxt.get("stage") or ""):
        events.append(
            {
                "type": "stage",
                "stage": str(nxt.get("stage") or ""),
                "state_seq": seq,
            }
        )
    if str(prev.get("page_id") or "") != str(nxt.get("page_id") or ""):
        events.append(
            {
                "type": "slide",
                "slide_index": 0,
                "deck_version": seq,
                "state_seq": seq,
            }
        )
    if flag_mask(prev) != flag_mask(nxt):
        events.append(
            {"type": "flags", "flag_mask": flag_mask(nxt), "state_seq": seq}
        )
        events.append({"type": "flag_work", "kind": "flags", "state_seq": seq})
    prev_prompt = str(prev.get("prompt_ref") or "")
    next_prompt = str(nxt.get("prompt_ref") or "")
    if prev_prompt != next_prompt:
        events.append(
            {
                "type": "prompt",
                "prompt_id": next_prompt or None,
                "state_seq": seq,
            }
        )
    return events


def _version_token(value: Any) -> int | str:
    """Keep a media version as an int when it is numeric, else a short token.

    Args:
        value: ``media_version`` from active media. Often a path token.
    """
    if isinstance(value, bool) or value in (None, ""):
        return 0
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    return text[:120]


def artifact_id_from_media(media: dict[str, Any] | None) -> str | None:
    """Read an artifact id off active media. Never the document body.

    Args:
        media: Public active-media payload, or ``None`` when cleared.
    """
    if not isinstance(media, dict):
        return None
    raw = media.get("artifact_id")
    if raw not in (None, ""):
        token = str(raw).strip()
        return token[:80] or None
    art = media.get("artifact")
    if isinstance(art, dict):
        token = str(art.get("id") or art.get("artifact_id") or "").strip()
        return token[:80] or None
    if isinstance(art, str) and art.strip():
        return art.strip()[:80]
    return None


def active_media_event(
    media: dict[str, Any] | None,
    state_seq: int,
) -> dict[str, Any]:
    """Postcard for Push, Swap, or Clear. Clients remount only on a new version.

    Args:
        media: Public active media, or ``None`` after a clear.
        state_seq: Teacher ``state_seq`` after the write.
    """
    return {
        "type": "active_media",
        "media_version": _version_token(
            media.get("media_version") if isinstance(media, dict) else 0
        ),
        "artifact_id": artifact_id_from_media(media),
        "state_seq": int(state_seq),
    }


def response_landed_events(
    *,
    state_seq: int,
    student_id: int | None = None,
    team_id: int | None = None,
    count: int | None = None,
    scope: str = "class",
) -> list[dict[str, Any]]:
    """One class (or group) tap plus a self tap. No answer text.

    Args:
        state_seq: Current teacher sequence. A response may not bump it.
        student_id: Roster id for the self postcard.
        team_id: Group id when ``scope`` is ``group``.
        count: Optional answer or vote total.
        scope: ``class`` for an individual answer, ``group`` for a team write.
    """
    seq = int(state_seq)
    if scope == "group":
        event: dict[str, Any] = {
            "type": "response_landed",
            "scope": "group",
            "state_seq": seq,
        }
        if team_id is not None:
            event["team_id"] = int(team_id)
        if count is not None:
            event["count"] = int(count)
        return [event]
    class_event: dict[str, Any] = {
        "type": "response_landed",
        "scope": "class",
        "state_seq": seq,
    }
    if count is not None:
        class_event["count"] = int(count)
    events = [class_event]
    if student_id is not None:
        events.append(
            {
                "type": "response_landed",
                "scope": "self",
                "student_id": int(student_id),
                "state_seq": seq,
            }
        )
    return events


def emit_answer_landed(
    school: Any,
    session_id: int,
    *,
    student_id: int | None = None,
    team_id: int | None = None,
    count: int | None = None,
    scope: str = "class",
) -> None:
    """Tell the room an answer landed. The HTTP ack stays the student's truth.

    Args:
        school: SchoolDB.
        session_id: ``live_class_sessions.id``.
        student_id: Roster id for the self postcard.
        team_id: Group id when ``scope`` is ``group``.
        count: Optional total. Omit rather than sending answer text.
        scope: ``class`` or ``group``.
    """
    emit_session_news(
        school,
        int(session_id),
        response_landed_events(
            state_seq=teacher_state_seq(school, int(session_id)),
            student_id=student_id,
            team_id=team_id,
            count=count,
            scope=scope,
        ),
    )


def prompt_event(prompt_id: Any, state_seq: int) -> dict[str, Any]:
    """Postcard naming the active prompt id and nothing of its stem.

    Args:
        prompt_id: Prompt id or teacher ``prompt_ref``. Empty becomes null.
        state_seq: Teacher sequence after the write.
    """
    token = str(prompt_id or "").strip()
    return {
        "type": "prompt",
        "prompt_id": token or None,
        "state_seq": int(state_seq),
    }


def flag_work_event(kind: str, state_seq: int, **ids: Any) -> dict[str, Any]:
    """Teacher-board tap. Students do not receive this event.

    Args:
        kind: Short token such as ``publish``, ``close``, or ``flags``.
        state_seq: Teacher sequence after the write.
        **ids: Optional ``student_id`` or ``team_id``.
    """
    event: dict[str, Any] = {
        "type": "flag_work",
        "kind": str(kind or "flag")[:40],
        "state_seq": int(state_seq),
    }
    if ids.get("student_id") not in (None, ""):
        event["student_id"] = _as_int(ids.get("student_id"), 0)
    if ids.get("team_id") not in (None, ""):
        event["team_id"] = _as_int(ids.get("team_id"), 0)
    return event


def event_visible(event: dict[str, Any], audience: NewsAudience) -> bool:
    """Apply auth scope. Staff sees the board; students miss other people's self taps.

    Args:
        event: Stored postcard, including its SSE ``id``.
        audience: Caller holding this stream.
    """
    if audience.role == "staff":
        return True
    kind = str(event.get("type") or "")
    if kind == "flag_work":
        return False
    if kind == "response_landed" and event.get("scope") == "self":
        return _as_int(event.get("student_id"), -1) == _as_int(
            audience.student_id, -2
        )
    return True


def format_sse(
    event: dict[str, Any],
    *,
    event_id: int | None = None,
    retry_ms: int | None = None,
) -> str:
    """Encode one SSE frame. Ping and busy omit an id so resume stays put.

    Args:
        event: Postcard. ``id`` is not copied into the JSON body.
        event_id: ``Last-Event-ID`` value. ``None`` leaves the browser's id.
        retry_ms: Browser reconnect delay in milliseconds.
    """
    body = {key: value for key, value in event.items() if key != "id"}
    lines: list[str] = []
    if retry_ms is not None:
        lines.append(f"retry: {int(retry_ms)}")
    if event_id is not None:
        lines.append(f"id: {int(event_id)}")
    lines.append(f"event: {body.get('type') or 'ping'}")
    lines.append("data: " + json.dumps(body, separators=(",", ":"), default=str))
    return "\n".join(lines) + "\n\n"


def parse_last_event_id(header: str | None, arg: str | None) -> int:
    """Read ``Last-Event-ID`` or the ``last_event_id`` query.

    Args:
        header: ``Last-Event-ID`` request header.
        arg: Query fallback used by tests.
    """
    raw = header if header not in (None, "") else arg
    return max(0, _as_int(raw, 0))


def iter_sse(
    log: LiveNewsLog,
    session_id: int,
    *,
    audience: NewsAudience,
    after_id: int,
    hello_seq: int,
    hold_s: float,
    poll_s: float = STREAM_POLL_S,
) -> Iterator[str]:
    """Yield hello, a resume tail, live appends, and a ping. Then stop.

    The browser reconnects with ``Last-Event-ID``. Stopping the generator
    returns the gunicorn thread. ``busy`` is not produced here; the route
    sheds before calling this.

    Args:
        log: Shared tape.
        session_id: ``live_class_sessions.id``.
        audience: Staff or student scope.
        after_id: Resume cursor. Events at or below this id are not replayed.
        hello_seq: ``state_seq`` to put on the hello postcard.
        hold_s: How long to tail before ending the response.
        poll_s: Sqlite tail interval, for events written by another worker.
    """
    cursor = int(after_id)
    yield format_sse(
        {
            "type": "hello",
            "state_seq": int(hello_seq),
            "session_id": str(int(session_id)),
        },
        retry_ms=SSE_RETRY_MS,
    )
    deadline = time.monotonic() + max(0.0, float(hold_s))
    while True:
        batch = log.since(session_id, cursor)
        for event in batch:
            cursor = max(cursor, _as_int(event.get("id"), cursor))
            if not event_visible(event, audience):
                continue
            yield format_sse(event, event_id=cursor)
        if time.monotonic() >= deadline:
            break
        time.sleep(min(poll_s, max(0.0, deadline - time.monotonic())))
    yield format_sse({"type": "ping"}, retry_ms=SSE_RETRY_MS)


def _audience_for(
    school: Any,
    session_id: int,
    session_row: dict[str, Any],
) -> NewsAudience | None:
    """Resolve the caller, or ``None`` when this session is not theirs.

    Args:
        school: SchoolDB.
        session_id: ``live_class_sessions.id``.
        session_row: Session row already loaded.
    """
    user = current_user()
    if user is not None and school.teacher_owns_class(
        int(user["id"]), int(session_row["class_id"])
    ):
        return NewsAudience("staff")
    ctx = resolve_student_live_context(school, session)
    if ctx is None:
        return None
    if int(ctx.get("live_session_id") or 0) != int(session_id):
        return None
    student_id = ctx.get("student_id")
    return NewsAudience(
        "student",
        student_id=int(student_id) if student_id not in (None, "") else None,
    )


def live_news_response(
    school: Any,
    session_id: int,
    *,
    testing: bool,
    last_event_header: str | None,
    last_event_arg: str | None,
) -> Response:
    """Stream LiveNewsWire for one session, or a JSON/busy refusal.

    Cookie auth covers staff session and the student rejoin cookie.
    ``EventSource`` cannot set a custom header, so the visit token must
    already be on that cookie.

    Args:
        school: SchoolDB.
        session_id: ``live_class_sessions.id``.
        testing: Short hold so the Flask test client can finish the body.
        last_event_header: ``Last-Event-ID``.
        last_event_arg: Query fallback.
    """
    row = school.get_live_session(int(session_id))
    if row is None:
        return jsonify({"ok": False, "error": "Session not found"}), 404
    audience = _audience_for(school, int(session_id), row)
    if audience is None:
        if current_user() is None and resolve_student_live_context(school, session) is None:
            return jsonify({"ok": False, "error": "Authentication required."}), 401
        return jsonify({"ok": False, "error": "Forbidden"}), 403
    if not _acquire_stream():
        body = format_sse(
            {"type": "busy", "retry": True},
            retry_ms=SSE_BUSY_RETRY_MS,
        )
        return Response(
            body,
            mimetype="text/event-stream",
            headers=_sse_headers(),
        )
    try:
        after_id = parse_last_event_id(last_event_header, last_event_arg)
        hello_seq = teacher_state_seq(school, int(session_id))
        hold_s = STREAM_HOLD_TESTING_S if testing else STREAM_HOLD_S
        log = log_for(school.data_dir)
    except BaseException:
        # A tape that won't open (after its bounded wait) must not keep
        # this worker's stream slot until restart.
        _release_stream()
        raise

    def generate() -> Iterator[str]:
        """Yield frames and always return the thread slot."""
        try:
            yield from iter_sse(
                log,
                int(session_id),
                audience=audience,
                after_id=after_id,
                hello_seq=hello_seq,
                hold_s=hold_s,
            )
        finally:
            _release_stream()

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers=_sse_headers(),
    )


def _sse_headers() -> dict[str, str]:
    """Headers that keep a proxy from buffering or caching the tape."""
    return {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }

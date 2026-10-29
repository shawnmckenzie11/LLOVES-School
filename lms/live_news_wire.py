"""LiveNewsWire — thin SSE postcards for one live session.

Mutations stay ordinary HTTP. After a successful write the route emits a
postcard (ids, ``state_seq``, a short label). Staff and student tabs share
one ``GET /api/live/session/<sid>/events`` stream. The log is its own
sqlite file so the four gunicorn workers on the 1 GB machine see the same
tape without taking the school catalogue lock.

One tail thread reads the tape for every seat on this worker. Each read
is a deferred snapshot that is committed before anyone waits, so a hold
cannot block an answer write. On gunicorn the browser socket is lifted
off the request thread; the eight ``gthread`` threads stay free for
join, submit, and ``/state``. A seat past ``STREAMS_PER_WORKER`` gets
one ``busy`` event and the browser waits. That event never means reload.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import signal
import socket
import sqlite3
import ssl
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator

from flask import Response, has_request_context, jsonify, request, session

from auth import current_user
from student_portal import resolve_student_live_context

logger = logging.getLogger(__name__)

# 30 students + the teacher. MCK-43. A real class stays on the push line.
CLASS_ON_STREAM = 31
# EventSource opens the next connection as this hold closes, so a full
# class briefly needs a second seat per person while the old one releases.
# Four spare seats cover a second teacher tab. Seats are not gunicorn threads.
STREAMS_PER_WORKER = CLASS_ON_STREAM * 2 + 4
# Slow safety-net poll. The wire is the tap; this is not a 2s/4s stampede.
FALLBACK_POLL_MS = 20000
# How long one stream holds its thread before the browser reconnects.
# SIGTERM does not use this full hold: note_stream_shutdown ends it on the
# next poll so a deploy drain is not 25 s of open streams (v219 drained 21 s).
STREAM_HOLD_S = 25.0
STREAM_HOLD_TESTING_S = 0.2
# Other gunicorn workers do not share this process's wake. They notice a
# postcard on the next tail read. Same-process listeners are woken from
# ``append`` and do not wait this long.
STREAM_POLL_S = 0.05
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
        "board_ink",
        "board_show",
        "google_slide",
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
    "prompt": frozenset({"type", "prompt_id", "state_seq", "card"}),
    "response_landed": frozenset(
        {"type", "scope", "count", "state_seq", "student_id", "team_id"}
    ),
    "flag_work": frozenset(
        {"type", "student_id", "team_id", "kind", "state_seq"}
    ),
    # Board key only. No points, no names. Members fetch since-seq deltas.
    "board_ink": frozenset({"type", "board_key"}),
    # Shown-board key only. Empty means the projection stopped. No names.
    "board_show": frozenset({"type", "board_key"}),
    # Current slide only. No roster. image_url is empty until the page is shared.
    "google_slide": frozenset(
        {"type", "index", "shared", "page_count", "image_url", "rev"}
    ),
    "busy": frozenset({"type", "retry"}),
    "ping": frozenset({"type"}),
}

_SLIDE_IMAGE_URL = re.compile(
    r"^/api/classes/\d+/live-question-images/[a-f0-9]{64}\.png$"
)

_FLAG_KEYS = ("minds_on", "action", "consolidation")

_stream_lock = threading.Lock()
_stream_count = 0
_stream_shutdown = threading.Event()
_shutdown_hook_installed = False


def stream_shutting_down() -> bool:
    """True after this worker has been asked to stop.

    Returns:
        Whether in-flight LiveNewsWire holds should end.
    """
    return _stream_shutdown.is_set()


def note_stream_shutdown() -> None:
    """End in-flight LiveNewsWire holds on the next tail poll.

    gunicorn waits out the generator on SIGTERM. The steady hold stays
    25 s so a quiet class still reconnects on that cadence. A shutdown
    cuts the hold so the old machine can release ``lloves_data``.
    """
    _stream_shutdown.set()
    _fan_notify_shutdown()


def clear_stream_shutdown() -> None:
    """Forget a shutdown mark. Tests call this; a real stop does not."""
    _stream_shutdown.clear()


def reset_stream_shutdown_hook() -> None:
    """Allow ``install_stream_shutdown_hook`` to run again. Tests only."""
    global _shutdown_hook_installed
    _shutdown_hook_installed = False


def chain_sigterm(previous: Any) -> Callable[[int, Any], None]:
    """Return a SIGTERM handler that ends stream holds, then chains.

    Args:
        previous: Handler already installed, usually gunicorn ``handle_exit``.

    Returns:
        Handler for ``signal.signal``.
    """

    def _on_sigterm(signum: int, frame: Any) -> None:
        note_stream_shutdown()
        if callable(previous):
            previous(signum, frame)

    return _on_sigterm


def install_stream_shutdown_hook() -> bool:
    """Chain SIGTERM in a gunicorn worker so stream holds end on shutdown.

    ``create_app`` runs after gunicorn's ``init_signals``, so the previous
    handler is ``handle_exit``. Chaining keeps that: the worker stops
    accepting, and the SSE loop returns the thread within one poll.
    Outside gunicorn this is a no-op so unit tests do not replace SIGTERM.

    Returns:
        True when this call installed the hook.
    """
    global _shutdown_hook_installed
    if _shutdown_hook_installed:
        return False
    from serve_capacity import running_under_gunicorn

    if not running_under_gunicorn():
        return False
    previous = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, chain_sigterm(previous))
    _shutdown_hook_installed = True
    return True


class NewsAudience:
    """Who is holding the stream. Staff sees the teacher board; students do not."""

    def __init__(
        self,
        role: str,
        *,
        student_id: int | None = None,
        read_keys: frozenset[str] | set[str] | None = None,
        team_ids: frozenset[int] | set[int] | None = None,
    ) -> None:
        """Record the caller's role, roster id, boards, and open-group teams.

        ``read_keys`` starts as the boards visible when the stream opens.
        A ``board_ink`` postcard reloads them before it is filtered, so a
        team change applies on the next tap instead of the next reconnect.

        ``team_ids`` is the student's teams on the session's open group
        items. Empty means the team is not known yet. A group postcard
        then still arrives, so a late joiner cannot miss their own team.
        The first group tap that finds membership fills this set.

        Args:
            role: ``staff`` or ``student``.
            student_id: Roster id for a student stream. ``None`` for staff
                and unmatched guests.
            read_keys: Board keys this student may read. Staff ignore this.
            team_ids: Open-group team ids for this student. Staff ignore this.
        """
        self.role = role
        self.student_id = student_id
        self.read_keys = frozenset(str(key) for key in (read_keys or ()))
        parsed: set[int] = set()
        for raw in team_ids or ():
            try:
                parsed.add(int(raw))
            except (TypeError, ValueError):
                continue
        self.team_ids = frozenset(parsed)


def reset_stream_budget() -> None:
    """Drop the in-process stream count. Tests call this between cases."""
    global _stream_count
    with _stream_lock:
        _stream_count = 0
    _fan_drop_all()


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


class _NewsSeat:
    """One tab subscribed to the shared tail. The queue is the push line."""

    def __init__(
        self,
        log: LiveNewsLog,
        session_id: int,
        audience: NewsAudience,
        cursor: int,
        school: Any,
    ) -> None:
        """Remember whose tape to read and where this tab left off.

        Args:
            log: Shared postcard tape.
            session_id: ``live_class_sessions.id``.
            audience: Staff or student scope for this socket.
            cursor: Last event id already handed to the tab.
            school: SchoolDB for a board-key refresh, or ``None``.
        """
        self.log = log
        self.session_id = int(session_id)
        self.audience = audience
        self.cursor = int(cursor)
        self.school = school
        self.queue: queue.Queue[str | None] = queue.Queue()


_fan_lock = threading.Lock()
_fan_seats: list[_NewsSeat] = []
_fan_wake = threading.Event()
_fan_thread: threading.Thread | None = None


def _ensure_fan() -> None:
    """Start the process-wide tail thread the first time a seat subscribes."""
    global _fan_thread
    if _fan_thread is not None and _fan_thread.is_alive():
        return
    _fan_thread = threading.Thread(
        target=_fan_loop, name="live-news-tail", daemon=True
    )
    _fan_thread.start()


def _fan_subscribe(
    log: LiveNewsLog,
    session_id: int,
    audience: NewsAudience,
    cursor: int,
    school: Any,
) -> _NewsSeat:
    """Register one tab on the shared tail.

    Args:
        log: Shared postcard tape.
        session_id: ``live_class_sessions.id``.
        audience: Staff or student scope.
        cursor: Last event id this tab already applied.
        school: SchoolDB for board-key refresh, or ``None``.

    Returns:
        The seat whose queue receives later frames.
    """
    seat = _NewsSeat(log, session_id, audience, cursor, school)
    with _fan_lock:
        _fan_seats.append(seat)
        _ensure_fan()
    _fan_wake.set()
    return seat


def _fan_unsubscribe(seat: _NewsSeat) -> None:
    """Remove one tab from the tail. A late frame may still sit on its queue.

    Args:
        seat: Seat returned by :func:`_fan_subscribe`.
    """
    with _fan_lock:
        try:
            _fan_seats.remove(seat)
        except ValueError:
            return


def _fan_drop_all() -> None:
    """Wake every seat and forget it. Tests call this between cases."""
    with _fan_lock:
        seats = list(_fan_seats)
        _fan_seats.clear()
    for seat in seats:
        seat.queue.put(None)
    _fan_wake.set()


def _fan_notify_shutdown() -> None:
    """Wake holds so a SIGTERM does not wait out the 25 s tail."""
    with _fan_lock:
        seats = list(_fan_seats)
    for seat in seats:
        seat.queue.put(None)
    _fan_wake.set()


def _deliver_batch(seat: _NewsSeat, batch: list[dict[str, Any]]) -> bool:
    """Enqueue frames this seat has not applied yet.

    Args:
        seat: Subscriber whose cursor moves forward.
        batch: Postcards from one short read, oldest first.

    Returns:
        True when the seat's cursor advanced.
    """
    before = seat.cursor
    refreshed = False
    for event in batch:
        event_id = _as_int(event.get("id"), seat.cursor)
        if event_id <= seat.cursor:
            continue
        seat.cursor = event_id
        if (
            not refreshed
            and seat.school is not None
            and str(event.get("type") or "") == "board_ink"
        ):
            _sync_board_read_keys(seat.school, seat.session_id, seat.audience)
            refreshed = True
        _maybe_sync_group_teams(
            seat.school, seat.session_id, seat.audience, event
        )
        if event_visible(event, seat.audience):
            seat.queue.put(format_sse(event, event_id=event_id))
    return seat.cursor != before


def _fan_catch_up(group: list[_NewsSeat]) -> None:
    """Read one session until every seat in the group has the new rows.

    Each ``since`` commits before the next. The ring is 200 rows and each
    read returns at most 50, so four passes finish a cold seat.

    Args:
        group: Seats sharing one tape and one session id.
    """
    if not group:
        return
    log = group[0].log
    session_id = group[0].session_id
    for _ in range(4):
        cursor = min(seat.cursor for seat in group)
        try:
            batch = log.since(session_id, cursor)
        except sqlite3.Error:
            logger.warning(
                "live news tail read failed session=%s", session_id, exc_info=True
            )
            return
        if not batch:
            return
        moved = False
        for seat in group:
            if _deliver_batch(seat, batch):
                moved = True
        if not moved or len(batch) < 50:
            return


def _fan_loop() -> None:
    """Tail every subscribed session. One read serves every seat on it.

    The sleep sits after the read is committed. A quiet class does not
    hold a sqlite transaction open between polls. With no seats the
    thread waits until the next subscribe.
    """
    while True:
        with _fan_lock:
            seats = list(_fan_seats)
        if not seats:
            _fan_wake.wait()
            _fan_wake.clear()
            continue
        if not stream_shutting_down():
            groups: dict[tuple[str, int], list[_NewsSeat]] = {}
            for seat in seats:
                key = (str(seat.log.path), seat.session_id)
                groups.setdefault(key, []).append(seat)
            for group in groups.values():
                if stream_shutting_down():
                    break
                _fan_catch_up(group)
        # A postcard appended during this pass set the wake. Loop again
        # instead of sleeping it away. A wake that arrives after this
        # check and before ``wait`` still returns at once.
        if _fan_wake.is_set():
            _fan_wake.clear()
            continue
        _fan_wake.wait(STREAM_POLL_S)
        _fan_wake.clear()


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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS board_ink_coalesce (
                    session_id INTEGER NOT NULL,
                    board_key TEXT NOT NULL,
                    window_ends_at REAL NOT NULL,
                    trail_due INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (session_id, board_key)
                )
                """
            )
            conn.commit()
    except BaseException:
        conn.close()
        raise
    return conn


# Ordinary postcards stay under 500 bytes. A publish card may carry the
# stem and choices the phone paints before it reloads ``/state``.
PROMPT_CARD_MAX_BYTES = 4096
_CARD_TEXT_MAX = 600
_CARD_LABEL_MAX = 160
_CARD_OPTIONS_MAX = 8
_CARD_ID_MAX = 64
_CARD_KINDS = frozenset({"mc", "numeric", "poll", "rank", "text"})
_CARD_MODES = frozenset(
    {"individual", "group_submit", "group_consensus", "team_draw"}
)
_CARD_FLOWS = frozenset({"", "pick_then_agree"})
_CARD_RANK_MODES = frozenset({"", "turns", "together"})
_CARD_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SCRIPT_TAG = re.compile(r"(?is)<script\b[^>]*>.*?</script>")

_push_stages: dict[str, float] = {}


def record_push_stages(**stages: float) -> None:
    """Remember the last publish's server stages, in milliseconds.

    The load check reads this after the teacher POST. Values are durations
    only. No roster, names, or session codes.

    Args:
        **stages: Named millisecond timings, such as ``state_write_ms``.
    """
    for key, value in stages.items():
        try:
            _push_stages[str(key)] = round(float(value), 1)
        except (TypeError, ValueError):
            continue


def last_push_stages() -> dict[str, float]:
    """Return a copy of the timings from the last :func:`record_push_stages`."""
    return dict(_push_stages)


def reset_push_stages() -> None:
    """Drop remembered publish timings. Tests call this between cases."""
    _push_stages.clear()


def _postcard_limit(event: dict[str, Any]) -> int:
    """Byte cap for one stored postcard.

    Args:
        event: Cleaned catalogue event.

    Returns:
        ``PROMPT_CARD_MAX_BYTES`` for a prompt that still has a card,
        otherwise 500.
    """
    if event.get("type") == "prompt" and isinstance(event.get("card"), dict):
        return PROMPT_CARD_MAX_BYTES
    return 500


def _clip_text(value: Any, limit: int) -> str:
    """Return a short string with script tags removed.

    Args:
        value: Raw postcard text.
        limit: Maximum characters kept.
    """
    text = _SCRIPT_TAG.sub("", str(value or ""))
    text = text.replace("\x00", "").strip()
    return text[: int(limit)]


def _public_option(raw: Any) -> str | dict[str, str] | None:
    """One choice label, or an aliased rank id plus its label.

    Args:
        raw: A string label or ``{"id", "label"}``. Anything else is dropped.
    """
    if isinstance(raw, str):
        label = _clip_text(raw, _CARD_LABEL_MAX)
        return label or None
    if not isinstance(raw, dict):
        return None
    label = _clip_text(raw.get("label") or raw.get("text"), _CARD_LABEL_MAX)
    if not label:
        return None
    token = str(raw.get("id") or "").strip()
    if token and _CARD_ID.fullmatch(token) and len(token) <= _CARD_ID_MAX:
        return {"id": token, "label": label}
    return label


def _public_card(raw: Any) -> dict[str, Any] | None:
    """Keep a publish card's stem and choices. Drop keys, names, and roster.

    Args:
        raw: Caller ``card`` object. Unknown keys are ignored.

    Returns:
        A card with text or choices, or ``None`` when there is nothing to paint.
    """
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in _CARD_KINDS:
        return None
    mode = str(raw.get("mode") or "individual").strip().lower()
    if mode not in _CARD_MODES:
        mode = "individual"
    flow = str(raw.get("flow") or "").strip().lower()
    if flow not in _CARD_FLOWS:
        flow = ""
    rank_mode = str(raw.get("rank_mode") or "").strip().lower()
    if rank_mode not in _CARD_RANK_MODES:
        rank_mode = ""
    text = _clip_text(raw.get("text"), _CARD_TEXT_MAX)
    options: list[str | dict[str, str]] = []
    raw_options = raw.get("options")
    if isinstance(raw_options, list):
        for item in raw_options[:_CARD_OPTIONS_MAX]:
            option = _public_option(item)
            if option is not None:
                options.append(option)
    if not text and not options:
        return None
    card: dict[str, Any] = {
        "item_id": max(0, _as_int(raw.get("item_id"))),
        "prompt_id": max(0, _as_int(raw.get("prompt_id"))),
        "kind": kind,
        "text": text,
        "options": options,
        "mode": mode,
        "flow": flow,
        "rank_mode": rank_mode,
    }
    if card["item_id"] <= 0:
        return None
    if card["prompt_id"] <= 0:
        card["prompt_id"] = None
    return card


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
    if kind == "board_ink":
        key = str(event.get("board_key") or "").strip()[:80]
        if not key:
            return None
        event["board_key"] = key
    if kind == "board_show":
        event["board_key"] = str(event.get("board_key") or "").strip()[:80]
    if kind == "google_slide":
        event["index"] = max(0, min(40, _as_int(event.get("index"))))
        event["page_count"] = max(0, min(40, _as_int(event.get("page_count"))))
        event["rev"] = max(0, min(1_000_000, _as_int(event.get("rev"))))
        event["shared"] = 1 if _as_int(event.get("shared")) else 0
        url = str(event.get("image_url") or "")
        event["image_url"] = url if _SLIDE_IMAGE_URL.fullmatch(url) else ""
        if not event["shared"]:
            event["image_url"] = ""
    if kind == "prompt":
        card = _public_card(event.get("card"))
        if card is None:
            event.pop("card", None)
        else:
            event["card"] = card
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
                if len(payload) > _postcard_limit(event):
                    if event.get("type") == "prompt" and "card" in event:
                        event = {
                            key: value
                            for key, value in event.items()
                            if key != "card"
                        }
                        payload = json.dumps(
                            event, separators=(",", ":"), default=str
                        )
                    if len(payload) > _postcard_limit(event):
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
        if stored:
            # Same-process tails are waiting on this event. Other workers
            # still notice the row on their next poll.
            _fan_wake.set()
        return stored

    def purge_session(self, session_id: int) -> int:
        """Delete postcards and ink windows for one session id.

        ``id`` is AUTOINCREMENT, so a tab holding an old ``Last-Event-ID``
        still gets every later append. ``board_ink_coalesce`` is the
        shared tap for that session and goes with the postcards.

        Args:
            session_id: ``live_class_sessions.id``.

        Returns:
            Postcard rows deleted.
        """
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM live_news_events WHERE session_id = ?",
                (int(session_id),),
            )
            self._conn.execute(
                "DELETE FROM board_ink_coalesce WHERE session_id = ?",
                (int(session_id),),
            )
            self._conn.commit()
        return int(cursor.rowcount or 0)

    def since(self, session_id: int, after_id: int, limit: int = 50) -> list[dict[str, Any]]:
        """Return postcards newer than ``after_id``, oldest first.

        Args:
            session_id: ``live_class_sessions.id``.
            after_id: Last SSE id the client already applied. ``0`` reads the ring.
            limit: Cap so one tail cannot dump the ring into one chunk.
        """
        cap = max(1, min(int(limit), 50))
        with self._lock:
            # Deferred snapshot, committed before this returns. Callers wait
            # outside ``_lock``, so a 25 s hold cannot keep a read open and
            # the next postcard append is not stuck behind it.
            self._begin_read()
            try:
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
            finally:
                self._conn.commit()
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

    def claim_board_ink(
        self,
        session_id: int,
        board_key: str,
        now: float,
        window_s: float,
    ) -> tuple[str, float]:
        """Claim the shared tap for one board. Every worker sees the same row.

        The first batch in a window is ``lead``. Later batches in that
        window are ``trail`` and share one trailing tap when the window ends.

        Args:
            session_id: ``live_class_sessions.id``.
            board_key: Board that just stored ops.
            now: Wall clock seconds. Shared across processes on this machine.
            window_s: How long one lead covers a burst.

        Returns:
            ``("lead", 0)`` when the caller should emit now, or
            ``("trail", window_ends_at)`` when a trailing tap is due then.
        """
        sid = int(session_id)
        key = str(board_key)
        ends = float(now) + float(window_s)
        with self._lock:
            self._begin_immediate()
            row = self._conn.execute(
                """
                SELECT window_ends_at FROM board_ink_coalesce
                WHERE session_id = ? AND board_key = ?
                """,
                (sid, key),
            ).fetchone()
            if row is None or float(row["window_ends_at"]) <= float(now):
                self._conn.execute(
                    """
                    INSERT INTO board_ink_coalesce (
                        session_id, board_key, window_ends_at, trail_due
                    ) VALUES (?, ?, ?, 0)
                    ON CONFLICT(session_id, board_key) DO UPDATE SET
                        window_ends_at = excluded.window_ends_at,
                        trail_due = 0
                    """,
                    (sid, key, ends),
                )
                self._conn.commit()
                return "lead", 0.0
            window_ends = float(row["window_ends_at"])
            self._conn.execute(
                """
                UPDATE board_ink_coalesce
                SET trail_due = 1
                WHERE session_id = ? AND board_key = ?
                """,
                (sid, key),
            )
            self._conn.commit()
            return "trail", window_ends

    def claim_board_ink_trail(self, session_id: int, board_key: str, now: float) -> bool:
        """Take the one trailing tap for a burst, if it is still due.

        Args:
            session_id: ``live_class_sessions.id``.
            board_key: Board the burst was written on.
            now: Wall clock seconds. A little early is allowed for timer slack.

        Returns:
            True when this caller should emit the trailing postcard.
        """
        sid = int(session_id)
        key = str(board_key)
        with self._lock:
            self._begin_immediate()
            cursor = self._conn.execute(
                """
                UPDATE board_ink_coalesce
                SET trail_due = 0, window_ends_at = ?
                WHERE session_id = ? AND board_key = ?
                  AND trail_due = 1 AND window_ends_at <= ?
                """,
                (float(now), sid, key, float(now) + 0.02),
            )
            self._conn.commit()
            return int(cursor.rowcount or 0) == 1

    def clear_ink_coalesce(self) -> None:
        """Drop shared tap rows. Tests call this between cases."""
        with self._lock:
            self._conn.execute("DELETE FROM board_ink_coalesce")
            self._conn.commit()

    def _begin_read(self) -> None:
        """Start a deferred read. The caller commits before it waits.

        Deferred, not ``IMMEDIATE``: a tail must not take the tape's write
        lock. Other workers can still append postcards during the snapshot,
        and this connection commits before the stream sleeps.
        """
        try:
            self._conn.execute("BEGIN")
        except sqlite3.OperationalError:
            self._conn.commit()
            self._conn.execute("BEGIN")

    def _begin_immediate(self) -> None:
        """Start a write transaction that waits for other workers.

        A leftover read transaction is committed first so ``BEGIN`` can run.
        """
        try:
            self._conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            self._conn.commit()
            self._conn.execute("BEGIN IMMEDIATE")


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
    reset_ink_taps()
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
        from feature_gates import news_events_for_session
    except ImportError:
        from lms.feature_gates import news_events_for_session
    events = news_events_for_session(int(session_id), events)
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


# One lead tap covers a burst this long. The clock is a row in the news
# tape, so the four gunicorn workers share it.
INK_TAP_COALESCE_S = 0.25
_trail_lock = threading.Lock()
_trail_timers: dict[tuple[str, int, str], threading.Timer] = {}


def reset_ink_taps() -> None:
    """Cancel trailing taps and clear the shared coalesce rows.

    Tests call this between cases. Open tapes are cleared; a timer that
    fires later finds nothing to claim.
    """
    with _trail_lock:
        timers = list(_trail_timers.values())
        _trail_timers.clear()
    for timer in timers:
        timer.cancel()
    with _logs_lock:
        logs = list(_logs.values())
    for log in logs:
        try:
            log.clear_ink_coalesce()
        except sqlite3.Error:
            pass


def _schedule_board_ink_trail(
    school: Any,
    session_id: int,
    board_key: str,
    ready_at: float,
) -> None:
    """Emit one trailing tap at ``ready_at`` if this process wins the claim.

    Other workers may schedule the same moment. The shared row lets one
    of them send the postcard.

    Args:
        school: SchoolDB. Only ``data_dir`` is read.
        session_id: ``live_class_sessions.id``.
        board_key: Board the burst belongs to.
        ready_at: Wall-clock second when the coalesce window ends.
    """
    data_dir = str(getattr(school, "data_dir", "") or "")
    token = (data_dir, int(session_id), str(board_key))
    delay = max(0.0, float(ready_at) - time.time())

    def fire() -> None:
        """Claim the trail and emit it. A lost claim stays quiet."""
        with _trail_lock:
            current = _trail_timers.get(token)
            if current is timer:
                _trail_timers.pop(token, None)
        try:
            if log_for(data_dir).claim_board_ink_trail(
                int(session_id), str(board_key), time.time()
            ):
                emit_session_news(
                    school,
                    int(session_id),
                    [{"type": "board_ink", "board_key": str(board_key)}],
                )
        except Exception:
            logger.warning(
                "board ink trail failed session=%s",
                session_id,
                exc_info=True,
            )

    with _trail_lock:
        existing = _trail_timers.get(token)
        if existing is not None and existing.is_alive():
            return
        timer = threading.Timer(delay, fire)
        timer.daemon = True
        _trail_timers[token] = timer
        timer.start()


def emit_board_ink(school: Any, session_id: int, board_key: str) -> None:
    """Tell readers of one board that new ops are stored.

    The postcard is the board key and nothing else. Strokes stay in
    ``board_ops``. Workers share one clock per board: the first batch in
    a short window taps immediately, and one trailing tap follows the
    window when more batches landed.

    Args:
        school: SchoolDB. Only ``data_dir`` is read.
        session_id: ``live_class_sessions.id``.
        board_key: Board that just gained ops.
    """
    key = str(board_key or "").strip()[:80]
    if not key:
        return
    sid = int(session_id)
    try:
        kind, ready_at = log_for(school.data_dir).claim_board_ink(
            sid, key, time.time(), INK_TAP_COALESCE_S
        )
    except Exception:
        logger.warning("board ink claim failed session=%s", sid, exc_info=True)
        return
    if kind == "trail":
        _schedule_board_ink_trail(school, sid, key, ready_at)
        return
    emit_session_news(school, sid, [{"type": "board_ink", "board_key": key}])


def prompt_event(
    prompt_id: Any,
    state_seq: int,
    card: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Postcard naming the active prompt, plus a stem card when one is ready.

    The card is the question text and choices every phone can paint before
    it reloads ``/state``. Answer keys and roster fields are removed when
    the tape stores the event.

    Args:
        prompt_id: Prompt id or teacher ``prompt_ref``. Empty becomes null.
        state_seq: Teacher sequence after the write.
        card: Optional public question card from ``SchoolDB.public_push_card``.
    """
    token = str(prompt_id or "").strip()
    event: dict[str, Any] = {
        "type": "prompt",
        "prompt_id": token or None,
        "state_seq": int(state_seq),
    }
    if isinstance(card, dict):
        event["card"] = card
    return event


def open_push_watch(log: LiveNewsLog, session_id: int, seats: int) -> list[_NewsSeat]:
    """Listen for the next publish card on ``seats`` same-process tails.

    Each seat starts at the current end of the tape, so older postcards
    are not counted. Call this before the teacher POST.

    Args:
        log: Shared tape for this data directory.
        session_id: ``live_class_sessions.id``.
        seats: How many student tails to open. At least 1.

    Returns:
        Seats to pass to :func:`collect_push_cards`.
    """
    count = max(1, int(seats))
    existing = log.since(int(session_id), 0)
    cursor = 0
    for event in existing:
        cursor = max(cursor, _as_int(event.get("id"), cursor))
    watched: list[_NewsSeat] = []
    for index in range(count):
        watched.append(
            _fan_subscribe(
                log,
                int(session_id),
                NewsAudience("student", student_id=index + 1),
                cursor,
                None,
            )
        )
    return watched


def collect_push_cards(
    seats: list[_NewsSeat],
    timeout_s: float,
) -> dict[str, Any]:
    """Wait until each seat has a prompt card, or the timeout lands.

    Does not fetch ``/state``. The card on the wire is the visible question.

    Args:
        seats: Seats from :func:`open_push_watch`.
        timeout_s: How long to wait for the slowest seat.

    Returns:
        ``cards`` (how many seats saw a card), ``first_ms`` and ``all_ms``
        from the moment this function starts. ``all_ms`` is ``None`` when
        a seat is still waiting.
    """
    pending = set(range(len(seats)))
    started = time.perf_counter()
    deadline = started + max(0.0, float(timeout_s))
    first_ms: float | None = None
    while pending and time.perf_counter() < deadline:
        for index in list(pending):
            try:
                frame = seats[index].queue.get_nowait()
            except queue.Empty:
                continue
            if not isinstance(frame, str):
                continue
            if '"type":"prompt"' not in frame or '"card":' not in frame:
                continue
            pending.discard(index)
            now_ms = (time.perf_counter() - started) * 1000
            if first_ms is None:
                first_ms = now_ms
        if pending:
            time.sleep(0.002)
    all_ms = None if pending else (time.perf_counter() - started) * 1000
    for seat in seats:
        _fan_unsubscribe(seat)
    return {
        "seats": len(seats),
        "cards": len(seats) - len(pending),
        "first_ms": None if first_ms is None else round(first_ms, 1),
        "all_ms": None if all_ms is None else round(all_ms, 1),
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


def _group_response_visible(event: dict[str, Any], audience: NewsAudience) -> bool:
    """Whether this student should see one group ``response_landed`` postcard.

    Staff is decided before this runs. A postcard with no ``team_id`` stays
    class-wide (the teacher moved every group at once). A student whose
    team is not known yet still receives it, so they cannot miss their own
    team's update. Everyone else only hears their own team's write.

    Args:
        event: Stored ``response_landed`` with ``scope`` ``group``.
        audience: Student holding this stream.
    """
    raw = event.get("team_id")
    if raw in (None, ""):
        return True
    if not audience.team_ids:
        return True
    return _as_int(raw, -1) in audience.team_ids


def event_visible(event: dict[str, Any], audience: NewsAudience) -> bool:
    """Apply auth scope. Staff sees the board; students miss other people's self taps.

    ``board_ink`` reaches only students whose stream was opened on that
    board, plus staff when the ink is on the teacher board. Another
    group's key is not delivered.

    A group ``response_landed`` with a ``team_id`` reaches staff and the
    students on that team. Other teams stay on their 20 s safety poll.

    Args:
        event: Stored postcard, including its SSE ``id``.
        audience: Caller holding this stream.
    """
    kind = str(event.get("type") or "")
    if kind == "board_ink":
        key = str(event.get("board_key") or "")
        if audience.role == "staff":
            return key == "teacher"
        return key in audience.read_keys
    if kind == "board_show":
        # Every joined student paints the shown board. The postcard is
        # the key only, and the client fetches that board instead of
        # reloading /state. Staff already painted from the Show POST.
        return audience.role == "student"
    if audience.role == "staff":
        return True
    if kind == "flag_work":
        return False
    if kind == "response_landed" and event.get("scope") == "self":
        return _as_int(event.get("student_id"), -1) == _as_int(
            audience.student_id, -2
        )
    if kind == "response_landed" and str(event.get("scope") or "") == "group":
        return _group_response_visible(event, audience)
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


def purge_session_news(data_dir: Path | str, session_id: int) -> int:
    """Clear one session's tape at Start. Wire faults are logged.

    Args:
        data_dir: School data directory.
        session_id: ``live_class_sessions.id`` of the new run.

    Returns:
        Rows deleted, or ``0`` on a wire fault.
    """
    try:
        return log_for(data_dir).purge_session(int(session_id))
    except Exception:
        logger.warning("live news purge failed session=%s", session_id)
        return 0


def parse_last_event_id(header: str | None, arg: str | None) -> int:
    """Read ``Last-Event-ID`` or the ``last_event_id`` query.

    Args:
        header: ``Last-Event-ID`` request header.
        arg: Query fallback used by tests.
    """
    raw = header if header not in (None, "") else arg
    return max(0, _as_int(raw, 0))


def _sync_group_team_ids(
    school: Any,
    session_id: int,
    audience: NewsAudience,
) -> None:
    """Load this student's open-group teams when the stream does not have them.

    Read-only. A miss or a lookup failure leaves ``team_ids`` empty so the
    postcard is still delivered. A hit is cached on the audience for the
    rest of this hold.

    Args:
        school: SchoolDB.
        session_id: ``live_class_sessions.id``.
        audience: Student holding this stream.
    """
    if audience.role != "student" or audience.student_id is None:
        return
    if audience.team_ids:
        return
    try:
        found = school.student_open_group_team_ids(
            int(session_id), int(audience.student_id)
        )
    except Exception:
        logger.warning(
            "live news team scope failed session=%s student=%s",
            session_id,
            audience.student_id,
        )
        return
    parsed: set[int] = set()
    for raw in found or ():
        try:
            parsed.add(int(raw))
        except (TypeError, ValueError):
            continue
    if parsed:
        audience.team_ids = frozenset(parsed)


def _maybe_sync_group_teams(
    school: Any,
    session_id: int,
    audience: NewsAudience,
    event: dict[str, Any],
) -> None:
    """Fill team ids the first time a team-scoped group postcard arrives.

    Phones connect before the question is published, so the open-group
    membership rows do not exist yet. Retrying on each group tap until
    the set is known keeps the first real tap filtered.

    Args:
        school: SchoolDB, or ``None`` in a unit stream.
        session_id: ``live_class_sessions.id``.
        audience: Caller holding this stream.
        event: Postcard about to be filtered.
    """
    if school is None or audience.team_ids:
        return
    if str(event.get("type") or "") != "response_landed":
        return
    if str(event.get("scope") or "") != "group":
        return
    if event.get("team_id") in (None, ""):
        return
    _sync_group_team_ids(school, int(session_id), audience)


def _sync_board_read_keys(
    school: Any,
    session_id: int,
    audience: NewsAudience,
) -> None:
    """Reload the boards a student may read before a ``board_ink`` is filtered.

    A lookup failure keeps the keys already on the stream. Staff are unchanged.

    Args:
        school: SchoolDB.
        session_id: ``live_class_sessions.id``.
        audience: Caller holding this stream.
    """
    if audience.role != "student" or audience.student_id is None:
        return
    try:
        access = school.student_board_access(int(session_id), int(audience.student_id))
        keys = access.get("read") if isinstance(access, dict) else ()
        audience.read_keys = frozenset(str(key) for key in (keys or ()))
    except Exception:
        logger.warning(
            "live news board scope failed session=%s student=%s",
            session_id,
            audience.student_id,
        )


def iter_sse(
    log: LiveNewsLog,
    session_id: int,
    *,
    audience: NewsAudience,
    after_id: int,
    hello_seq: int,
    hold_s: float,
    poll_s: float = STREAM_POLL_S,
    school: Any = None,
) -> Iterator[str]:
    """Yield hello, a resume tail, live appends, and a ping. Then stop.

    The browser reconnects with ``Last-Event-ID``. The resume is read
    here, once, and committed. Later postcards come from the shared tail,
    so thirty seats do not each hold a read open. ``busy`` is not produced
    here; the route sheds before calling this.

    Args:
        log: Shared tape.
        session_id: ``live_class_sessions.id``.
        audience: Staff or student scope.
        after_id: Resume cursor. Events at or below this id are not replayed.
        hello_seq: ``state_seq`` to put on the hello postcard.
        hold_s: How long to tail before ending the response.
        poll_s: How often to notice a shutdown while waiting on the tail.
        school: SchoolDB. Used to refresh board read keys on ``board_ink``.
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
    # The ring is 200 and each read returns 50. Four committed reads
    # finish a cold resume without sleeping inside a transaction.
    for _ in range(4):
        batch = log.since(session_id, cursor)
        if not batch:
            break
        refreshed = False
        for event in batch:
            cursor = max(cursor, _as_int(event.get("id"), cursor))
            if (
                not refreshed
                and school is not None
                and str(event.get("type") or "") == "board_ink"
            ):
                _sync_board_read_keys(school, int(session_id), audience)
                refreshed = True
            _maybe_sync_group_teams(school, int(session_id), audience, event)
            if not event_visible(event, audience):
                continue
            yield format_sse(event, event_id=cursor)
        if len(batch) < 50:
            break
    if not stream_shutting_down() and time.monotonic() < deadline:
        seat = _fan_subscribe(log, int(session_id), audience, cursor, school)
        try:
            while not stream_shutting_down() and time.monotonic() < deadline:
                timeout = min(
                    float(poll_s), max(0.0, deadline - time.monotonic())
                )
                try:
                    frame = seat.queue.get(timeout=timeout)
                except queue.Empty:
                    continue
                if frame is None:
                    break
                yield frame
        finally:
            _fan_unsubscribe(seat)
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
    sid = int(student_id) if student_id not in (None, "") else None
    read_keys: frozenset[str] = frozenset()
    if sid is not None:
        try:
            access = school.student_board_access(int(session_id), sid)
            read_keys = frozenset(str(key) for key in (access.get("read") or ()))
        except Exception:
            logger.warning(
                "live news board scope failed session=%s student=%s",
                session_id,
                sid,
            )
            read_keys = frozenset()
    team_ids: frozenset[int] = frozenset()
    if sid is not None:
        try:
            found = school.student_open_group_team_ids(int(session_id), sid)
            team_ids = frozenset(int(team) for team in (found or ()))
        except Exception:
            logger.warning(
                "live news team scope failed session=%s student=%s",
                session_id,
                sid,
            )
            team_ids = frozenset()
    return NewsAudience(
        "student", student_id=sid, read_keys=read_keys, team_ids=team_ids
    )


def detach_gunicorn_client(sock: socket.socket) -> socket.socket | None:
    """Point gunicorn's fd at a drain and return the browser connection.

    gunicorn finishes the WSGI response with ``shutdown(SHUT_WR)``. That
    FIN would end the SSE body. ``dup2`` moves gunicorn's fd onto a
    socketpair first, so the FIN hits the drain and the eight ``gthread``
    threads are free while the class stays connected. A TLS socket is
    left in place: duplicating its raw fd is not the TLS session.

    Args:
        sock: ``environ["gunicorn.socket"]`` for this request.

    Returns:
        The browser socket to write the SSE body on, or ``None`` when the
        socket cannot be lifted and the caller should keep the generator.
    """
    if isinstance(sock, ssl.SSLSocket) or not isinstance(sock, socket.socket):
        return None
    try:
        fileno = sock.fileno()
    except OSError:
        return None
    if fileno < 0:
        return None
    try:
        held = os.dup(fileno)
    except OSError:
        return None
    drain_read, drain_write = socket.socketpair()
    try:
        os.dup2(drain_write.fileno(), fileno)
    except OSError:
        os.close(held)
        drain_read.close()
        drain_write.close()
        return None
    drain_write.close()
    drain_read.setblocking(True)

    def _drain() -> None:
        """Discard gunicorn's response so its write cannot fill the pair.

        Closing this end after EOF lets ``close_graceful`` finish its
        read instead of lingering on an open socketpair.
        """
        try:
            while True:
                chunk = drain_read.recv(65536)
                if not chunk:
                    break
        except OSError:
            pass
        finally:
            try:
                drain_read.close()
            except OSError:
                pass

    threading.Thread(target=_drain, name="live-news-drain", daemon=True).start()
    try:
        client = socket.socket(fileno=held)
    except OSError:
        os.close(held)
        return None
    client.setblocking(True)
    return client


def _request_gunicorn_socket() -> socket.socket | None:
    """Return this request's gunicorn socket, or ``None`` outside a worker.

    Returns:
        The client socket gunicorn stored on the WSGI environ.
    """
    if not has_request_context():
        return None
    sock = request.environ.get("gunicorn.socket")
    if isinstance(sock, socket.socket):
        return sock
    return None


_SSE_PREAMBLE = (
    "HTTP/1.1 200 OK\r\n"
    "Content-Type: text/event-stream\r\n"
    "Cache-Control: no-cache\r\n"
    "X-Accel-Buffering: no\r\n"
    "Transfer-Encoding: chunked\r\n"
    "Connection: close\r\n"
    "\r\n"
)


def _write_chunk(sock: socket.socket, payload: bytes) -> None:
    """Write one chunked block. An empty payload ends the HTTP body.

    Args:
        sock: Detached browser socket.
        payload: SSE bytes, or ``b""`` for the terminating chunk.
    """
    if not payload:
        sock.sendall(b"0\r\n\r\n")
        return
    header = b"%X\r\n" % len(payload)
    sock.sendall(header + payload + b"\r\n")


def _serve_detached(
    sock: socket.socket,
    log: LiveNewsLog,
    session_id: int,
    audience: NewsAudience,
    after_id: int,
    hello_seq: int,
    hold_s: float,
    school: Any,
) -> None:
    """Write one SSE hold on a socket the request thread has released.

    Args:
        sock: Browser connection lifted off gunicorn.
        log: Shared tape.
        session_id: ``live_class_sessions.id``.
        audience: Staff or student scope.
        after_id: Resume cursor.
        hello_seq: ``state_seq`` for the hello postcard.
        hold_s: How long to tail before the browser reconnects.
        school: SchoolDB for board-key refresh.
    """
    try:
        sock.sendall(_SSE_PREAMBLE.encode("ascii"))
        for frame in iter_sse(
            log,
            int(session_id),
            audience=audience,
            after_id=after_id,
            hello_seq=hello_seq,
            hold_s=hold_s,
            school=school,
        ):
            _write_chunk(sock, frame.encode("utf-8"))
        _write_chunk(sock, b"")
    except OSError:
        logger.debug("live news client closed session=%s", session_id)
    except Exception:
        logger.warning(
            "live news detached stream ended session=%s",
            session_id,
            exc_info=True,
        )
    finally:
        try:
            sock.close()
        except OSError:
            pass
        _release_stream()


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

    client_sock: socket.socket | None = None
    try:
        raw_sock = _request_gunicorn_socket()
        if raw_sock is not None:
            client_sock = detach_gunicorn_client(raw_sock)
    except Exception:
        logger.warning(
            "live news detach failed session=%s", session_id, exc_info=True
        )
        client_sock = None
    if client_sock is not None:
        try:
            threading.Thread(
                target=_serve_detached,
                args=(
                    client_sock,
                    log,
                    int(session_id),
                    audience,
                    after_id,
                    hello_seq,
                    hold_s,
                    school,
                ),
                name="live-news-seat",
                daemon=True,
            ).start()
        except RuntimeError:
            try:
                client_sock.close()
            except OSError:
                pass
            _release_stream()
            raise
        # gunicorn writes this empty body to the drain fd, then frees the
        # gthread. The browser is already reading ``client_sock``.
        return Response(
            "",
            status=200,
            mimetype="text/event-stream",
            headers=_sse_headers(),
        )

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
                school=school,
            )
        finally:
            _release_stream()

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers=_sse_headers(),
    )


def _sse_headers() -> dict[str, str]:
    """Headers that keep a proxy from buffering or caching the tape.

    ``Connection`` is not set here. gunicorn drops that hop-by-hop header
    and chooses close versus keep-alive from ``--keep-alive``. The finite
    25s stream must not be pooled: a client that reuses the socket races
    the idle close.
    """
    return {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }

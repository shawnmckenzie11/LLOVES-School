"""In-class fix notes and the End Live Class triage report (MCK-482, MCK-480).

Flags are written only when someone submits one. Student polling never
reads this table. The report is built once when End Class closes the
session. A Claude tidy, when ``ANTHROPIC_API_KEY`` is set, runs after
that request returns. Sentry is included only when ``SENTRY_AUTH_TOKEN``
and ``SENTRY_ORG`` are set.

Stored context and outbound payloads keep an anonymized reporter id
when ``LIVE_FIX_ANON_PEPPER`` is set. They do not keep student names,
teacher emails, join codes, or visit tokens.

``LIVE_FIX_REPORT_TOKEN`` is an optional read-only bearer for the three
End Class report GETs. When it is unset, that path stays off. A match
does not write and does not open any other route. The token response
drops student names, join codes, and PINs. Opaque ids such as
``student-73ab…`` stay. The token is compared in constant time and is
not logged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from functools import wraps
from typing import Any, Callable
from zoneinfo import ZoneInfo
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

NOTE_LIMIT = 280
ACTION_LIMIT = 10
QUESTION_LIMIT = 8
# A one-line note plus a short action list. Reject anything larger before parse.
FLAG_BODY_MAX_BYTES = 4096
STUDENT_NOTE_LIMIT = 6
SESSION_NOTE_LIMIT = 30
NOTE_WINDOW_SECONDS = 600
_SENTRY_BUDGET_SECONDS = 3.0
_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)
_SENTRY_PROJECTS_DEFAULT = "lloves-lms,lloves-live"
_ANTHROPIC_MODEL_DEFAULT = "claude-haiku-4-5"
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
# Button ids live_fix.js records. Anything else, including a visit token, is dropped.
ACTION_IDS = frozenset(
    {
        "ap-join-billboard-code",
        "ap-join-billboard-copy",
        "ap-meet-minutes-down",
        "ap-meet-minutes-up",
        "ap-meet-restart",
        "ap-meet-start",
        "ap-meet-stop",
        "exit-feedback-send",
        "exit-feedback-skip",
        "live-add-page",
        "live-advance",
        "live-delete-page",
        "live-end-class",
        "live-eyes-up",
        "live-mutation-retry",
        "live-quit-class",
        "live-reconnect-retry",
        "live-stage-next",
        "live-stage-prev",
        "live-start",
        "live-student-view",
        "prompt-dismiss",
        "prompt-feedback-close",
        "rw-chip",
        "save-work",
        "set-class-saved-override",
        "set-class-start",
        "student-canvas-erase",
        "student-canvas-pen",
        "student-canvas-text",
        "student-canvas-undo",
        "student-reconnect-retry",
        "team-draw-submit",
    }
)
_note_hits: dict[str, list[float]] = {}
_note_lock = threading.Lock()
_pepper_warned = False

HttpJson = Callable[..., Any]


def anonymize_reporter(session_id: int, role: str, raw_key: str) -> str:
    """Return a stable anonymous id for one person in one live session.

    With no ``LIVE_FIX_ANON_PEPPER``, nothing is hashed and the caller
    stores the role only.

    Args:
        session_id: ``live_class_sessions.id``.
        role: ``teacher`` or ``student``.
        raw_key: Server-side id used only as hash input. It is not stored.

    Returns:
        A short id such as ``student-`` plus 12 hex characters, or ``""``.
    """
    global _pepper_warned
    pepper = (os.environ.get("LIVE_FIX_ANON_PEPPER") or "").strip()
    if not pepper:
        if not _pepper_warned:
            logger.warning("live fix reporter id skipped; LIVE_FIX_ANON_PEPPER is unset")
            _pepper_warned = True
        return ""
    material = f"{int(session_id)}|{role}|{raw_key}".encode()
    digest = hmac.new(pepper.encode(), material, hashlib.sha256).hexdigest()[:12]
    return f"{role}-{digest}"


def clean_actions(raw: Any, secrets: list[str] | None = None) -> list[str]:
    """Keep the last allowlisted button ids.

    Args:
        raw: Client-supplied action list.
        secrets: Values that are dropped even when they match an allowlisted id.

    Returns:
        At most ten ids from :data:`ACTION_IDS`.
    """
    blocked = {secret for secret in (secrets or []) if secret}
    if not isinstance(raw, list):
        return []
    kept: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        token = item.strip()
        if token not in ACTION_IDS or token in blocked:
            continue
        kept.append(token)
    return kept[-ACTION_LIMIT:]


def clear_note_rates() -> None:
    """Drop in-memory note counters. Tests start from an empty window."""
    with _note_lock:
        _note_hits.clear()


def note_rate_limited(*, session_id: int, student_key: str) -> bool:
    """True when this student or this session has sent too many notes.

    Args:
        session_id: ``live_class_sessions.id``.
        student_key: Roster id or participant key. It is not logged.

    Returns:
        True when the note must be refused.
    """
    now = time.monotonic()
    student_bucket = f"student:{int(session_id)}:{student_key}"
    session_bucket = f"session:{int(session_id)}"
    with _note_lock:
        for bucket in (student_bucket, session_bucket):
            _note_hits[bucket] = [
                stamp
                for stamp in _note_hits.get(bucket, [])
                if now - stamp < NOTE_WINDOW_SECONDS
            ]
        over_student = len(_note_hits[student_bucket]) >= STUDENT_NOTE_LIMIT
        over_session = len(_note_hits[session_bucket]) >= SESSION_NOTE_LIMIT
        if over_student or over_session:
            return True
        _note_hits[student_bucket].append(now)
        _note_hits[session_bucket].append(now)
    return False


def flag_body_too_large(content_length: int | None) -> bool:
    """True when Content-Length is past the note cap.

    A missing length is not treated as small. Callers still bound the
    stream with :func:`read_capped_json` so a chunked body cannot grow
    past the cap.

    Args:
        content_length: Declared body size, or None when the header is absent.
    """
    if content_length is None:
        return False
    try:
        size = int(content_length)
    except (TypeError, ValueError):
        return False
    return size > FLAG_BODY_MAX_BYTES


def read_capped_json(environ: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Read one JSON object and stop at the note cap.

    The raw WSGI input is read for one byte past the cap. A longer body,
    including a chunked body with no Content-Length, returns ``too_large``
    without pulling the rest into the worker.

    Args:
        environ: The request environ. ``wsgi.input`` is read directly so a
            missing Content-Length cannot fall through to an unlimited read.

    Returns:
        ``(body, too_large)``. An empty or unreadable body is ``{}``.
    """
    stream = environ.get("wsgi.input") if isinstance(environ, dict) else None
    read = getattr(stream, "read", None)
    if not callable(read):
        return {}, False
    raw = read(FLAG_BODY_MAX_BYTES + 1)
    if raw is None:
        raw = b""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if len(raw) > FLAG_BODY_MAX_BYTES:
        return {}, True
    if not raw.strip():
        return {}, False
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}, False
    if not isinstance(parsed, dict):
        return {}, False
    return parsed, False


def roster_secrets(school: Any, class_id: int, session_row: dict[str, Any]) -> list[str]:
    """Names, emails, and the join code that must not be stored or sent.

    Args:
        school: Open school database.
        class_id: Game-show ``classes.id``.
        session_row: Live session columns, including ``session_code``.

    Returns:
        Secrets of at least three characters, longest first.

    Raises:
        Exception: A roster or visit-token lookup failed. Callers must not
            send free text when this list is incomplete.
    """
    found: list[str] = []
    code = str(session_row.get("session_code") or "").strip()
    if code:
        found.append(code)
    teacher_id = session_row.get("teacher_user_id")
    if teacher_id not in (None, ""):
        try:
            user = school.get_user(int(teacher_id))
        except (TypeError, ValueError):
            user = None
        if isinstance(user, dict):
            for key in ("email", "name"):
                text = str(user.get(key) or "").strip()
                if len(text) >= 3:
                    found.append(text)
    game = getattr(school, "game", None)
    conn = getattr(game, "conn", None)
    if conn is not None:
        columns = [str(row[1]) for row in conn.execute("PRAGMA table_info(students)")]
        wanted = [
            name
            for name in ("codename", "first_name", "last_name", "email", "student_raw")
            if name in columns
        ]
        if wanted:
            sql = (
                f"SELECT {', '.join(wanted)} FROM students WHERE class_id = ?"
            )
            rows = conn.execute(sql, (int(class_id),)).fetchall()
            for row in rows:
                for value in row:
                    text = str(value or "").strip()
                    if len(text) >= 3:
                        found.append(text)
    session_id = session_row.get("id")
    conn = getattr(school, "conn", None)
    if session_id not in (None, "") and conn is not None:
        with school._lock:
            token_rows = conn.execute(
                """
                SELECT visit_token FROM live_session_attendees
                WHERE live_session_id = ?
                """,
                (int(session_id),),
            ).fetchall()
        for row in token_rows:
            token = str(row["visit_token"] or "").strip()
            if len(token) >= 8:
                found.append(token)
    unique = list(dict.fromkeys(found))
    unique.sort(key=len, reverse=True)
    return unique


def _flexible_secret(secret: str) -> str | None:
    """Match a secret even when whitespace is inserted between characters.

    Args:
        secret: One roster value or visit token.

    Returns:
        A regex, or None when the value is too short to search for.
    """
    chars = [ch for ch in secret if not ch.isspace()]
    if len(chars) < 3 or secret.lower() in {"the", "and"}:
        return None
    body = r"\s*".join(re.escape(ch) for ch in chars)
    if all(ch.isalnum() or ch in " .'-_" for ch in secret):
        return rf"(?<!\w){body}(?!\w)"
    return body


def scrub_roster_values(text: str, secrets: list[str]) -> str:
    """Replace known names, emails, and join codes. Leave SHAs and dates.

    Unlike :func:`scrub_secrets`, this does not treat every 4-digit group
    or short code as a secret. A release SHA and a Sentry id must stay
    readable in the pasted report.

    Args:
        text: The pasteable report.
        secrets: Values from :func:`roster_secrets`.
    """
    cleaned = str(text or "")
    for secret in secrets:
        pattern = _flexible_secret(secret)
        if not pattern:
            continue
        cleaned = re.sub(pattern, "[redacted]", cleaned, flags=re.IGNORECASE)
    return _EMAIL_RE.sub("[redacted]", cleaned)


def scrub_secrets(text: str, secrets: list[str]) -> str:
    """Replace roster secrets and email addresses with ``[redacted]``.

    The text should already be one short line. Whitespace between the
    characters of a visit token still matches.

    Args:
        text: Note, title, or other free text.
        secrets: Values from :func:`roster_secrets`.

    Returns:
        Text with those values and email addresses removed.
    """
    cleaned = str(text or "")
    for secret in secrets:
        pattern = _flexible_secret(secret)
        if not pattern:
            continue
        cleaned = re.sub(pattern, "[redacted]", cleaned, flags=re.IGNORECASE)
    cleaned = _EMAIL_RE.sub("[redacted]", cleaned)
    try:
        from sentry_wire import _scrub_text
    except ImportError:
        from lms.sentry_wire import _scrub_text
    return _scrub_text(cleaned)


def clean_note(text: str, secrets: list[str]) -> str:
    """Turn a submitted note into one short stored line.

    Whitespace is collapsed and the line is cut to 280 characters before
    any secret scan, so a long note cannot make scrubbing slow.

    Args:
        text: Raw note from the teacher or student.
        secrets: Values that must not remain in the note.

    Returns:
        A single line of at most 280 characters.
    """
    cleaned = str(text or "").replace("\r", " ").replace("\n", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()[:NOTE_LIMIT]
    return scrub_secrets(cleaned, secrets).strip()[:NOTE_LIMIT]


def _session_columns(school: Any, session_id: int) -> dict[str, Any] | None:
    """Read the session columns a flag or report needs.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.

    Returns:
        A small column dict, or ``None`` when the session is missing.
    """
    with school._lock:
        row = school.conn.execute(
            """
            SELECT id, class_id, status, session_code, teacher_user_id,
                   started_at, ended_at, teacher_state_json
            FROM live_class_sessions
            WHERE id = ?
            """,
            (int(session_id),),
        ).fetchone()
    return dict(row) if row is not None else None


def _teacher_page(raw_state: Any) -> dict[str, Any]:
    """Pull slide fields out of teacher state without copying other keys.

    Args:
        raw_state: ``teacher_state_json`` text or a dict.

    Returns:
        ``page_number``, ``page_id``, and ``stage`` only.
    """
    parsed: dict[str, Any] = {}
    if isinstance(raw_state, str) and raw_state.strip():
        try:
            loaded = json.loads(raw_state)
        except json.JSONDecodeError:
            loaded = {}
        if isinstance(loaded, dict):
            parsed = loaded
    elif isinstance(raw_state, dict):
        parsed = raw_state
    page_number = None
    raw_number = parsed.get("page_number")
    if raw_number not in (None, ""):
        try:
            number = int(raw_number)
        except (TypeError, ValueError):
            number = 0
        if number > 0:
            page_number = number
    return {
        "page_number": page_number,
        "page_id": str(parsed.get("page_id") or "").strip()[:80],
        "stage": str(parsed.get("stage") or "").strip().lower()[:40],
    }


def slide_context(school: Any, session_id: int, session_row: dict[str, Any]) -> dict[str, Any]:
    """Describe the slide the class is on, without names.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        session_row: Row from :func:`_session_columns`.

    Returns:
        Slide number, page id, question ids, and content types.
    """
    page = _teacher_page(session_row.get("teacher_state_json"))
    page_number = page["page_number"]
    stage = page["stage"]
    with school._lock:
        rows = school.conn.execute(
            """
            SELECT item_id, kind, page_number, stage, status
            FROM live_session_items
            WHERE live_session_id = ?
            ORDER BY sort_order ASC, id ASC
            """,
            (int(session_id),),
        ).fetchall()
    matched: list[dict[str, str]] = []
    for row in rows:
        row_page = row["page_number"]
        row_stage = str(row["stage"] or "").strip().lower()
        if page_number is not None and row_page not in (None, ""):
            try:
                if int(row_page) != int(page_number):
                    continue
            except (TypeError, ValueError):
                continue
        elif page_number is not None and stage and row_stage and row_stage != stage:
            continue
        elif page_number is None and stage and row_stage and row_stage != stage:
            continue
        question_id = str(row["item_id"] or "").strip()[:80]
        content_type = str(row["kind"] or "").strip().lower()[:40]
        if not question_id and not content_type:
            continue
        matched.append(
            {
                "question_id": question_id,
                "content_type": content_type,
                "status": str(row["status"] or ""),
            }
        )
        if len(matched) >= QUESTION_LIMIT:
            break
    active = [item for item in matched if item["status"] == "active"] or matched
    primary = active[0] if active else {}
    questions = [
        {"question_id": item["question_id"], "content_type": item["content_type"]}
        for item in active
    ]
    content_type = primary.get("content_type") or stage or ""
    return {
        "slide_number": page_number,
        "page_id": page["page_id"],
        "question_id": primary.get("question_id") or "",
        "content_type": content_type,
        "questions": questions,
    }


def _public_flag(row: Any) -> dict[str, Any]:
    """Serialize one flag for the teacher list and the report.

    Args:
        row: ``live_fix_flags`` sqlite row.
    """
    try:
        actions = json.loads(row["actions_json"] or "[]")
    except json.JSONDecodeError:
        actions = []
    if not isinstance(actions, list):
        actions = []
    slide = row["slide_number"]
    questions: list[dict[str, str]] = []
    try:
        stored_context = json.loads(row["context_json"] or "{}")
    except json.JSONDecodeError:
        stored_context = {}
    if isinstance(stored_context, dict):
        raw_questions = stored_context.get("questions")
        if isinstance(raw_questions, list):
            for item in raw_questions:
                if not isinstance(item, dict):
                    continue
                questions.append(
                    {
                        "question_id": str(item.get("question_id") or "")[:80],
                        "content_type": str(item.get("content_type") or "")[:40],
                    }
                )
    return {
        "id": int(row["id"]),
        "created_at": str(row["created_at"] or ""),
        "reporter_role": str(row["reporter_role"] or ""),
        "reporter_anon": str(row["reporter_anon"] or ""),
        "slide_number": int(slide) if slide not in (None, "") else None,
        "page_id": str(row["page_id"] or ""),
        "question_id": str(row["question_id"] or ""),
        "content_type": str(row["content_type"] or ""),
        "note": str(row["note"] or ""),
        "actions": [str(item) for item in actions if isinstance(item, str)],
        "questions": questions,
        "tidy_title": str(row["tidy_title"] or ""),
        "tidy_description": str(row["tidy_description"] or ""),
    }


def add_flag(
    school: Any,
    session_id: int,
    *,
    role: str,
    raw_key: str,
    note: str,
    actions: Any,
) -> dict[str, Any]:
    """Store one in-class note and the slide context around it.

    The caller has already checked that this person may write for the
    session. ``raw_key`` is hashed and discarded.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        role: ``teacher`` or ``student``.
        raw_key: Staff user id or student participant key. Not stored.
        note: One-line note from the form.
        actions: Recent button ids from the browser.

    Returns:
        The stored flag, with no name, email, or join code.

    Raises:
        KeyError: The session does not exist.
        ValueError: The role is not teacher or student.
    """
    if role not in {"teacher", "student"}:
        raise ValueError("role must be teacher or student")
    session_row = _session_columns(school, int(session_id))
    if session_row is None:
        raise KeyError(f"live session {session_id}")
    secrets = roster_secrets(school, int(session_row["class_id"]), session_row)
    context = slide_context(school, int(session_id), session_row)
    stored_note = clean_note(note, secrets)
    stored_actions = clean_actions(actions, secrets)
    page_id = scrub_secrets(str(context.get("page_id") or ""), secrets)[:80]
    context_out = {
        "slide_number": context.get("slide_number"),
        "page_id": page_id,
        "question_id": str(context.get("question_id") or "")[:80],
        "content_type": str(context.get("content_type") or "")[:40],
        "questions": context.get("questions") or [],
        "actions": stored_actions,
        "reporter_role": role,
    }
    anon = anonymize_reporter(int(session_id), role, str(raw_key or "unknown"))
    created = _now()
    with school._lock:
        cur = school.conn.execute(
            """
            INSERT INTO live_fix_flags (
                live_session_id, class_id, created_at, reporter_role,
                reporter_anon, slide_number, page_id, question_id,
                content_type, note, actions_json, context_json,
                tidy_title, tidy_description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '')
            """,
            (
                int(session_id),
                int(session_row["class_id"]),
                created,
                role,
                anon,
                context_out["slide_number"],
                page_id,
                context_out["question_id"],
                context_out["content_type"],
                stored_note,
                json.dumps(stored_actions),
                json.dumps(context_out, ensure_ascii=True),
            ),
        )
        flag_id = int(cur.lastrowid)
        school.conn.commit()
        row = school.conn.execute(
            "SELECT * FROM live_fix_flags WHERE id = ?",
            (flag_id,),
        ).fetchone()
    return _public_flag(row)


def list_flags(school: Any, session_id: int) -> list[dict[str, Any]]:
    """Return this session's flags, oldest first.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
    """
    with school._lock:
        rows = school.conn.execute(
            """
            SELECT * FROM live_fix_flags
            WHERE live_session_id = ?
            ORDER BY id ASC
            """,
            (int(session_id),),
        ).fetchall()
    return [_public_flag(row) for row in rows]


def delete_flag(school: Any, session_id: int, flag_id: int) -> bool:
    """Delete one flag from an open session.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        flag_id: ``live_fix_flags.id``.

    Returns:
        True when a row was removed.
    """
    with school._lock:
        cur = school.conn.execute(
            """
            DELETE FROM live_fix_flags
            WHERE id = ? AND live_session_id = ?
            """,
            (int(flag_id), int(session_id)),
        )
        school.conn.commit()
        return int(cur.rowcount or 0) > 0


def delete_session_rows(school: Any, session_ids: list[int]) -> None:
    """Drop flags and reports for sessions that are being wiped.

    Caller holds ``school._lock`` and an open transaction. Missing tables
    are ignored so an older file can still quit.

    Args:
        school: Open school database.
        session_ids: ``live_class_sessions.id`` values.
    """
    if not session_ids:
        return
    present = {
        str(row[0])
        for row in school.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    marks = ",".join("?" * len(session_ids))
    params = tuple(int(sid) for sid in session_ids)
    for table in ("live_fix_flags", "live_fix_reports"):
        if table not in present:
            continue
        school.conn.execute(
            f"DELETE FROM {table} WHERE live_session_id IN ({marks})",
            params,
        )


def _group_flags(flags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group flags by slide number, unknown slides last.

    Args:
        flags: Public flag dicts.
    """
    buckets: dict[int | None, list[dict[str, Any]]] = {}
    for flag in flags:
        slide = flag.get("slide_number")
        key = slide if isinstance(slide, int) else None
        buckets.setdefault(key, []).append(flag)
    ordered = sorted(buckets, key=lambda key: (key is None, key or 0))
    return [{"slide": key, "flags": buckets[key]} for key in ordered]


def _parse_stamp(value: str) -> datetime | None:
    """Parse a stored or Sentry timestamp into naive local time.

    Args:
        value: ISO timestamp, with or without a trailing ``Z``.
    """
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone().replace(tzinfo=None)
    return parsed


def _http_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 2.5,
) -> Any:
    """POST or GET JSON and return the decoded body.

    Args:
        url: Absolute URL.
        headers: Extra request headers.
        payload: JSON body. ``None`` sends a GET.
        timeout: Socket timeout in seconds.

    Returns:
        Decoded JSON.

    Raises:
        URLError: The network call failed.
        HTTPError: The server returned an error status.
        json.JSONDecodeError: The body was not JSON.
    """
    data = None
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=req_headers, method="POST" if data else "GET")
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8"))


def sentry_config() -> dict[str, Any]:
    """Read Sentry report settings from the environment.

    Returns:
        ``configured`` is true only when a token and an org slug are set.
    """
    token = (os.environ.get("SENTRY_AUTH_TOKEN") or "").strip()
    org = (os.environ.get("SENTRY_ORG") or "").strip()
    projects = [
        part.strip()
        for part in (os.environ.get("SENTRY_PROJECTS") or _SENTRY_PROJECTS_DEFAULT).split(",")
        if part.strip()
    ]
    base = (os.environ.get("SENTRY_API_BASE") or "https://sentry.io").rstrip("/")
    if not token or not org:
        reason = "SENTRY_AUTH_TOKEN is not set" if not token else "SENTRY_ORG is not set"
        return {
            "configured": False,
            "reason": reason,
            "token": "",
            "org": org,
            "projects": projects,
            "base": base,
        }
    return {
        "configured": True,
        "reason": "",
        "token": token,
        "org": org,
        "projects": projects or ["lloves-lms", "lloves-live"],
        "base": base,
    }


def _class_window_minutes(started_at: str, ended_at: str) -> int:
    """Minutes to look back in Sentry for this class.

    Args:
        started_at: Session start timestamp.
        ended_at: Session end timestamp.
    """
    start = _parse_stamp(started_at)
    end = _parse_stamp(ended_at) or datetime.now()
    if start is None:
        return 90
    minutes = int((end - start).total_seconds() // 60) + 2
    return max(5, min(180, minutes))


def _issue_in_window(issue: dict[str, Any], started_at: str, ended_at: str) -> bool:
    """True when the issue's last-seen time overlaps the class.

    Args:
        issue: One Sentry issue object.
        started_at: Session start.
        ended_at: Session end.
    """
    last_seen = _parse_stamp(str(issue.get("lastSeen") or ""))
    start = _parse_stamp(started_at)
    end = _parse_stamp(ended_at)
    if last_seen is None or start is None:
        return True
    if last_seen < start:
        return False
    if end is not None and _parse_stamp(str(issue.get("firstSeen") or "")):
        first_seen = _parse_stamp(str(issue.get("firstSeen") or ""))
        if first_seen is not None and first_seen > end:
            return False
    return True


def _public_issue(project: str, issue: dict[str, Any], secrets: list[str]) -> dict[str, Any]:
    """Copy the issue fields the report shows, and nothing else.

    Args:
        project: Sentry project slug.
        issue: Raw issue object. User fields are ignored.
        secrets: Roster values to strip from the title.
    """
    try:
        count = int(issue.get("count") or 0)
    except (TypeError, ValueError):
        count = 0
    return {
        "project": project,
        "id": str(issue.get("shortId") or issue.get("id") or ""),
        "title": scrub_secrets(str(issue.get("title") or ""), secrets)[:180],
        "count": count,
        "first_seen": str(issue.get("firstSeen") or ""),
        "last_seen": str(issue.get("lastSeen") or ""),
        "url": str(issue.get("permalink") or ""),
    }


def fetch_sentry_issues(
    started_at: str,
    ended_at: str,
    secrets: list[str],
    *,
    http_json: HttpJson | None = None,
) -> dict[str, Any]:
    """Load ALC Sentry issues seen during the class window.

    Args:
        started_at: Session start timestamp.
        ended_at: Session end timestamp.
        secrets: Values that must not remain in issue titles.
        http_json: Optional HTTP stand-in for tests.

    Returns:
        ``configured`` false when auth is missing. Failures stay in
        ``error`` and do not raise.
    """
    config = sentry_config()
    if not config["configured"]:
        return {
            "configured": False,
            "reason": config["reason"],
            "projects": config["projects"],
            "issues": [],
        }
    call = http_json or _http_json
    minutes = _class_window_minutes(started_at, ended_at)
    issues: list[dict[str, Any]] = []
    errors: list[str] = []
    deadline = time.monotonic() + _SENTRY_BUDGET_SECONDS
    for project in config["projects"]:
        remaining = deadline - time.monotonic()
        if remaining < 0.2:
            errors.append(f"{project}: budget")
            break
        url = (
            f"{config['base']}/api/0/projects/{quote(str(config['org']))}/"
            f"{quote(project)}/issues/?query={quote(f'lastSeen:-{minutes}m')}"
            "&limit=25"
        )
        try:
            payload = call(
                url,
                headers={"Authorization": f"Bearer {config['token']}"},
                timeout=min(2.0, remaining),
            )
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            errors.append(f"{project}: {exc.__class__.__name__}")
            continue
        rows = payload if isinstance(payload, list) else []
        for issue in rows:
            if not isinstance(issue, dict):
                continue
            if not _issue_in_window(issue, started_at, ended_at):
                continue
            issues.append(_public_issue(project, issue, secrets))
    result: dict[str, Any] = {
        "configured": True,
        "projects": list(config["projects"]),
        "issues": issues,
        "window_minutes": minutes,
    }
    if errors:
        result["error"] = "; ".join(errors)
    return result


def _tidy_status(key_set: bool) -> str:
    """Report tidy state at the moment End Class saves the report.

    Args:
        key_set: Whether ``ANTHROPIC_API_KEY`` is present.
    """
    return "scheduled" if key_set else "skipped"


def anthropic_api_key() -> str:
    """Return the Claude API key, or an empty string when unset."""
    return (os.environ.get("ANTHROPIC_API_KEY") or "").strip()


def _parse_moment(value: str) -> datetime | None:
    """Parse a stored timestamp. Naive values are Eastern Time.

    The school runs in Ontario. A timestamp with no offset is the time
    the server stored locally, which is Eastern. A ``Z`` or offset is
    converted to Eastern.

    Args:
        value: ISO timestamp from a session or Sentry.
    """
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    eastern = ZoneInfo("America/Toronto")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=eastern)
    return parsed.astimezone(eastern)


def format_eastern(value: str) -> str:
    """Return one timestamp as ``YYYY-MM-DD HH:MM ET``.

    Args:
        value: ISO timestamp. Empty or unparseable text is returned trimmed.
    """
    parsed = _parse_moment(value)
    if parsed is None:
        return str(value or "").strip()
    return parsed.strftime("%Y-%m-%d %H:%M ET")


def flag_debug_label(flag: dict[str, Any]) -> str:
    """Return the same debug line the End Class report shows under a note.

    Order matches the on-screen meta: role, anonymized id, content type,
    question id, then recent button ids. Empty pieces are left out, so a
    student note on a bank import after Later reads
    ``student · question · bank-import-28920 · live-catchup-pin-later``.

    Args:
        flag: Public flag dict.
    """
    actions = flag.get("actions") if isinstance(flag.get("actions"), list) else []
    action_text = ", ".join(str(item) for item in actions if str(item).strip())
    bits = [
        str(flag.get("reporter_role") or "").strip(),
        str(flag.get("reporter_anon") or "").strip(),
        str(flag.get("content_type") or "").strip(),
        str(flag.get("question_id") or "").strip(),
    ]
    if action_text:
        bits.append(action_text)
    return " · ".join(bit for bit in bits if bit)


def _teacher_state_dict(raw_state: Any) -> dict[str, Any]:
    """Return teacher state as a dict, or an empty dict.

    Args:
        raw_state: ``teacher_state_json`` text or a dict.
    """
    if isinstance(raw_state, dict):
        return raw_state
    if isinstance(raw_state, str) and raw_state.strip():
        try:
            loaded = json.loads(raw_state)
        except json.JSONDecodeError:
            return {}
        if isinstance(loaded, dict):
            return loaded
    return {}


def _lesson_context(school: Any, session_row: dict[str, Any]) -> dict[str, str]:
    """Course, section, lesson, and deck for one ended class.

    Join codes and access codes are not copied. A missing offering leaves
    the course fields blank.

    Args:
        school: Open school database.
        session_row: Session columns, including teacher state.
    """
    course = ""
    section = ""
    class_id = session_row.get("class_id")
    game = getattr(school, "game", None)
    if class_id not in (None, "") and game is not None:
        try:
            cls = game.get_class(int(class_id))
        except (TypeError, ValueError, KeyError):
            cls = None
        offering_id = (cls or {}).get("offering_id") if isinstance(cls, dict) else None
        offering = None
        if offering_id not in (None, ""):
            try:
                offering = school.get_offering(int(offering_id))
            except (TypeError, ValueError, KeyError):
                offering = None
        if isinstance(offering, dict):
            course = str(offering.get("ontario_code") or "").strip().upper()
            try:
                from school_db import section_code
            except ImportError:
                from lms.school_db import section_code

            section = str(offering.get("section_code") or "").strip() or section_code(
                course, offering.get("section_index")
            )
    state = _teacher_state_dict(session_row.get("teacher_state_json"))
    module = str(state.get("live_module") or "").strip().upper()
    slot = str(state.get("live_slot") or "").strip().upper()
    lesson = " ".join(part for part in (module, slot) if part)
    title = ""
    presentation = ""
    try:
        deck = school.google_slides_deck(int(session_row["id"]))
    except (TypeError, ValueError, AttributeError):
        deck = None
    if isinstance(deck, dict):
        title = str(deck.get("title") or "").strip()
        presentation = str(deck.get("presentation_id") or "").strip()
    deck_bits = [lesson]
    if title:
        deck_bits.append(title)
    if presentation:
        deck_bits.append(presentation)
    try:
        from sentry_wire import release_name
    except ImportError:
        from lms.sentry_wire import release_name

    release = release_name() or ""
    return {
        "course_code": course,
        "section": section,
        "module": module,
        "slot": slot,
        "lesson": lesson,
        "deck": " · ".join(bit for bit in deck_bits if bit),
        "release": release,
    }


def report_copy_text(report: dict[str, Any]) -> str:
    """Plain text a developer can paste into a chat.

    Includes the course, section, lesson, deck, Eastern start and end,
    each note's slide and question ids, the debug label, Sentry issues,
    the release SHA, and the reporter role with an anonymized id.
    Names, emails, and join codes must already be gone from ``report``.

    Args:
        report: Dict from :func:`build_report`.
    """
    lines = ["End Class report"]
    course = str(report.get("course_code") or "").strip()
    section = str(report.get("section") or "").strip()
    lesson = str(report.get("lesson") or "").strip()
    deck = str(report.get("deck") or "").strip()
    if course:
        lines.append(f"Course: {course}")
    if section:
        lines.append(f"Section: {section}")
    if lesson:
        lines.append(f"Lesson: {lesson}")
    if deck:
        lines.append(f"Deck: {deck}")
    started = format_eastern(str(report.get("started_at") or ""))
    ended = format_eastern(str(report.get("ended_at") or ""))
    if started:
        lines.append(f"Date: {started[:10]}")
        lines.append(f"Started: {started}")
    if ended:
        lines.append(f"Ended: {ended}")
    release = str(report.get("release") or "").strip()
    lines.append(f"Release: {release or 'unknown'}")
    if report.get("session_id") not in (None, ""):
        lines.append(f"Session: {int(report['session_id'])}")
    if report.get("class_id") not in (None, ""):
        lines.append(f"Class: {int(report['class_id'])}")
    groups = report.get("flags_by_slide")
    if not isinstance(groups, list) or not groups:
        lines.append("")
        lines.append("No in-class notes.")
    else:
        for group in groups:
            if not isinstance(group, dict):
                continue
            slide = group.get("slide")
            lines.append("")
            lines.append("Slide unknown" if slide in (None, "") else f"Slide {int(slide)}")
            flags = group.get("flags") if isinstance(group.get("flags"), list) else []
            for flag in flags:
                if not isinstance(flag, dict):
                    continue
                page_id = str(flag.get("page_id") or "").strip()
                if page_id:
                    lines.append(f"Page: {page_id}")
                questions = flag.get("questions")
                if not isinstance(questions, list) or not questions:
                    question_id = str(flag.get("question_id") or "").strip()
                    content_type = str(flag.get("content_type") or "").strip()
                    questions = [
                        {"question_id": question_id, "content_type": content_type}
                    ]
                item_bits = []
                for item in questions:
                    if not isinstance(item, dict):
                        continue
                    question_id = str(item.get("question_id") or "").strip()
                    content_type = str(item.get("content_type") or "").strip()
                    if question_id and content_type:
                        item_bits.append(f"{question_id} ({content_type})")
                    elif question_id or content_type:
                        item_bits.append(question_id or content_type)
                if item_bits:
                    lines.append("Items: " + "; ".join(item_bits))
                title = str(flag.get("tidy_title") or "").strip()
                note = str(flag.get("note") or "").strip()
                description = str(flag.get("tidy_description") or "").strip()
                if title:
                    lines.append(f"Note: {title}")
                if note and note != title:
                    lines.append(f"Detail: {note}")
                if description and description not in {title, note}:
                    lines.append(f"Tidy: {description}")
                label = flag_debug_label(flag)
                if label:
                    lines.append(f"Debug: {label}")
                role = str(flag.get("reporter_role") or "").strip()
                anon = str(flag.get("reporter_anon") or "").strip()
                who = " ".join(part for part in (role, anon) if part)
                if who:
                    lines.append(f"Reporter: {who}")
                actions = flag.get("actions") if isinstance(flag.get("actions"), list) else []
                action_text = ", ".join(str(item) for item in actions if str(item).strip())
                if "live-catchup-pin-later" in action_text:
                    lines.append("Catch-up pin: later")
                elif action_text:
                    lines.append(f"Actions: {action_text}")
    sentry = report.get("sentry") if isinstance(report.get("sentry"), dict) else {}
    lines.append("")
    lines.append("Sentry")
    if not sentry.get("configured"):
        lines.append("Sentry was not checked.")
    else:
        issues = sentry.get("issues") if isinstance(sentry.get("issues"), list) else []
        if not issues:
            lines.append("No Sentry issues in this class window.")
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            lines.append(
                " ".join(
                    part
                    for part in (
                        str(issue.get("project") or "").strip(),
                        str(issue.get("id") or "").strip(),
                        str(issue.get("title") or "").strip(),
                    )
                    if part
                )
                + f" · count {issue.get('count') or 0}"
                + f" · {issue.get('first_seen') or ''} – {issue.get('last_seen') or ''}"
            )
            url = str(issue.get("url") or "").strip()
            if url:
                lines.append(url)
        if sentry.get("error"):
            lines.append(f"Sentry error: {sentry.get('error')}")
    return "\n".join(lines).strip() + "\n"


def build_report(
    school: Any,
    session_id: int,
    *,
    http_json: HttpJson | None = None,
) -> dict[str, Any]:
    """Build the post-class report for one session.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        http_json: Optional HTTP stand-in for the Sentry call.

    Returns:
        The report dict. Empty flags and a missing Sentry token are valid.

    Raises:
        KeyError: The session does not exist.
    """
    session_row = _session_columns(school, int(session_id))
    if session_row is None:
        raise KeyError(f"live session {session_id}")
    secrets = roster_secrets(school, int(session_row["class_id"]), session_row)
    flags = list_flags(school, int(session_id))
    ended_at = str(session_row.get("ended_at") or "") or _now()
    sentry = fetch_sentry_issues(
        str(session_row.get("started_at") or ""),
        ended_at,
        secrets,
        http_json=http_json,
    )
    lesson = _lesson_context(school, session_row)
    report = {
        "session_id": int(session_id),
        "class_id": int(session_row["class_id"]),
        "course_code": lesson["course_code"],
        "section": lesson["section"],
        "module": lesson["module"],
        "slot": lesson["slot"],
        "lesson": lesson["lesson"],
        "deck": scrub_roster_values(lesson["deck"], secrets),
        "release": lesson["release"],
        "started_at": str(session_row.get("started_at") or ""),
        "ended_at": ended_at,
        "flag_count": len(flags),
        "flags_by_slide": _group_flags(flags),
        "sentry": sentry,
        "tidy": {"status": _tidy_status(bool(anthropic_api_key()))},
    }
    report["copy_text"] = scrub_roster_values(report_copy_text(report), secrets)
    return report


def save_report(school: Any, report: dict[str, Any]) -> None:
    """Insert or replace the stored report for this session.

    Args:
        school: Open school database.
        report: Dict from :func:`build_report`.
    """
    now = _now()
    payload = json.dumps(report, ensure_ascii=True)
    with school._lock:
        school.conn.execute(
            """
            INSERT INTO live_fix_reports (
                live_session_id, class_id, created_at, started_at, ended_at, report_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(live_session_id) DO UPDATE SET
                class_id = excluded.class_id,
                created_at = excluded.created_at,
                started_at = excluded.started_at,
                ended_at = excluded.ended_at,
                report_json = excluded.report_json
            """,
            (
                int(report["session_id"]),
                int(report["class_id"]),
                now,
                str(report.get("started_at") or ""),
                str(report.get("ended_at") or ""),
                payload,
            ),
        )
        school.conn.commit()


def record_end_report(
    school: Any,
    session_id: int,
    *,
    http_json: HttpJson | None = None,
) -> dict[str, Any] | None:
    """Save the End Class report. A failure is logged and does not raise.

    Args:
        school: Open school database.
        session_id: Session that just ended.
        http_json: Optional HTTP stand-in for tests.

    Returns:
        The saved report, or ``None`` when it could not be saved.
    """
    try:
        report = build_report(school, int(session_id), http_json=http_json)
        save_report(school, report)
    except Exception:
        logger.exception("live fix report was not saved for session %s", session_id)
        return None
    return report


def report_row(school: Any, class_id: int, session_id: int) -> dict[str, Any] | None:
    """Return one stored report when it belongs to this class.

    Args:
        school: Open school database.
        class_id: Game-show ``classes.id``.
        session_id: ``live_class_sessions.id``.
    """
    with school._lock:
        row = school.conn.execute(
            """
            SELECT report_json FROM live_fix_reports
            WHERE class_id = ? AND live_session_id = ?
            """,
            (int(class_id), int(session_id)),
        ).fetchone()
    if row is None:
        return None
    try:
        parsed = json.loads(row["report_json"] or "{}")
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def latest_report(school: Any, class_id: int) -> dict[str, Any] | None:
    """Return the newest stored report for a class.

    Args:
        school: Open school database.
        class_id: Game-show ``classes.id``.
    """
    with school._lock:
        row = school.conn.execute(
            """
            SELECT report_json FROM live_fix_reports
            WHERE class_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(class_id),),
        ).fetchone()
    if row is None:
        return None
    try:
        parsed = json.loads(row["report_json"] or "{}")
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def report_summaries(school: Any, class_id: int) -> list[dict[str, Any]]:
    """List ended-class reports without their notes.

    Args:
        school: Open school database.
        class_id: Game-show ``classes.id``.
    """
    with school._lock:
        rows = school.conn.execute(
            """
            SELECT live_session_id, started_at, ended_at, report_json
            FROM live_fix_reports
            WHERE class_id = ?
            ORDER BY id DESC
            """,
            (int(class_id),),
        ).fetchall()
    summaries: list[dict[str, Any]] = []
    for row in rows:
        try:
            parsed = json.loads(row["report_json"] or "{}")
        except json.JSONDecodeError:
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        sentry = parsed.get("sentry") if isinstance(parsed.get("sentry"), dict) else {}
        issues = sentry.get("issues") if isinstance(sentry.get("issues"), list) else []
        summaries.append(
            {
                "session_id": int(row["live_session_id"]),
                "started_at": str(row["started_at"] or ""),
                "ended_at": str(row["ended_at"] or ""),
                "flag_count": int(parsed.get("flag_count") or 0),
                "sentry_count": len(issues),
                "sentry_configured": bool(sentry.get("configured")),
            }
        )
    return summaries


def _tidy_payload(flag: dict[str, Any], secrets: list[str]) -> dict[str, Any]:
    """Build the only object sent to Claude for one flag.

    Args:
        flag: Public flag dict.
        secrets: Roster values stripped again before the request.
    """
    note = clean_note(str(flag.get("note") or ""), secrets)
    questions = flag.get("questions")
    if not isinstance(questions, list):
        questions = []
    safe_questions = []
    for item in questions:
        if not isinstance(item, dict):
            continue
        safe_questions.append(
            {
                "question_id": str(item.get("question_id") or "")[:80],
                "content_type": str(item.get("content_type") or "")[:40],
            }
        )
    return {
        "note": note,
        "context": {
            "slide_number": flag.get("slide_number"),
            "page_id": scrub_secrets(str(flag.get("page_id") or ""), secrets)[:80],
            "question_id": str(flag.get("question_id") or "")[:80],
            "content_type": str(flag.get("content_type") or "")[:40],
            "questions": safe_questions,
            "actions": clean_actions(flag.get("actions"), secrets),
            "reporter_role": str(flag.get("reporter_role") or ""),
        },
    }


def _parse_tidy_text(text: str, secrets: list[str]) -> tuple[str, str]:
    """Read a title and description from a Claude reply.

    Args:
        text: Model text, ideally a JSON object.
        secrets: Values stripped from both fields.

    Returns:
        Title and description. Either may be empty.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return "", ""
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return "", ""
    if not isinstance(parsed, dict):
        return "", ""
    title = clean_note(str(parsed.get("title") or ""), secrets)[:140]
    description = clean_note(str(parsed.get("description") or ""), secrets)[:500]
    return title, description


def tidy_session_flags(
    school: Any,
    session_id: int,
    *,
    http_json: HttpJson | None = None,
) -> dict[str, Any]:
    """Rewrite each note into a title and description when a key is set.

    This is not called from the flag request. End Class starts it only
    after the response is ready to return, and tests call it directly.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
        http_json: Optional HTTP stand-in for tests.

    Returns:
        ``status`` of ``skipped``, ``done``, or ``error``.
    """
    key = anthropic_api_key()
    if not key:
        return {"status": "skipped"}
    session_row = _session_columns(school, int(session_id))
    if session_row is None:
        return {"status": "error"}
    secrets = roster_secrets(school, int(session_row["class_id"]), session_row)
    flags = list_flags(school, int(session_id))
    call = http_json or _http_json
    model = (os.environ.get("ANTHROPIC_MODEL") or _ANTHROPIC_MODEL_DEFAULT).strip()
    updated = 0
    try:
        for flag in flags[:40]:
            body = _tidy_payload(flag, secrets)
            payload = call(
                _ANTHROPIC_URL,
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                },
                payload={
                    "model": model,
                    "max_tokens": 300,
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                "Rewrite this class bug note as JSON with keys "
                                "title and description. title is one line. "
                                "description is at most two sentences. Use only "
                                "the note and context. Do not add names.\n"
                                + json.dumps(body, ensure_ascii=True)
                            ),
                        }
                    ],
                },
                timeout=8,
            )
            text = ""
            if isinstance(payload, dict):
                content = payload.get("content")
                if isinstance(content, list) and content:
                    first = content[0]
                    if isinstance(first, dict):
                        text = str(first.get("text") or "")
            title, description = _parse_tidy_text(text, secrets)
            if not title and not description:
                continue
            with school._lock:
                school.conn.execute(
                    """
                    UPDATE live_fix_flags
                    SET tidy_title = ?, tidy_description = ?
                    WHERE id = ? AND live_session_id = ?
                    """,
                    (title, description, int(flag["id"]), int(session_id)),
                )
                school.conn.commit()
            updated += 1
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        logger.warning("live fix tidy failed for session %s", session_id)
        _mark_tidy(school, int(session_row["class_id"]), int(session_id), "error")
        return {"status": "error", "updated": updated}
    _refresh_flags_in_report(school, int(session_row["class_id"]), int(session_id))
    return {"status": "done", "updated": updated}


def _mark_tidy(school: Any, class_id: int, session_id: int, status: str) -> None:
    """Set the tidy status on a stored report without rewriting Sentry.

    Args:
        school: Open school database.
        class_id: Game-show ``classes.id``.
        session_id: ``live_class_sessions.id``.
        status: ``done`` or ``error``.
    """
    report = report_row(school, int(class_id), int(session_id))
    if report is None:
        return
    report["tidy"] = {"status": status}
    report["flags_by_slide"] = _group_flags(list_flags(school, int(session_id)))
    report["flag_count"] = sum(len(group["flags"]) for group in report["flags_by_slide"])
    session_row = _session_columns(school, int(session_id))
    secrets = (
        roster_secrets(school, int(class_id), session_row) if session_row is not None else []
    )
    report["copy_text"] = scrub_roster_values(report_copy_text(report), secrets)
    save_report(school, report)


def _refresh_flags_in_report(school: Any, class_id: int, session_id: int) -> None:
    """Replace the report's flags with the tidied rows.

    Args:
        school: Open school database.
        class_id: Game-show ``classes.id``.
        session_id: ``live_class_sessions.id``.
    """
    _mark_tidy(school, class_id, session_id, "done")


def kick_tidy(school: Any, session_id: int) -> None:
    """Start the Claude tidy on a background thread.

    No-op when the key is unset, when Flask is in testing mode, or when
    there is no application context. The End Class response does not wait.

    Args:
        school: Open school database.
        session_id: Session whose report was just saved.
    """
    if not anthropic_api_key():
        return
    try:
        from flask import current_app

        if current_app.config.get("TESTING"):
            return
    except Exception:
        return
    thread = threading.Thread(
        target=_tidy_safe,
        args=(school, int(session_id)),
        name=f"live-fix-tidy-{int(session_id)}",
        daemon=True,
    )
    thread.start()


def _tidy_safe(school: Any, session_id: int) -> None:
    """Run tidy and log failures without the note text.

    Args:
        school: Open school database.
        session_id: ``live_class_sessions.id``.
    """
    try:
        tidy_session_flags(school, int(session_id))
    except Exception:
        logger.exception("live fix tidy crashed for session %s", session_id)


def _now() -> str:
    """Local ISO timestamp without microseconds."""
    return datetime.now().replace(microsecond=0).isoformat()


# Keys a token response must not include. Staff JSON is unchanged.
_REPORT_SECRET_KEYS = frozenset(
    {
        "pin",
        "course_pin",
        "student_pin",
        "catchup_pin",
        "catch_up_pin",
        "default_pin",
        "pin_hash",
        "default_pin_ciphertext",
        "join_code",
        "session_code",
        "portal_join_code",
        "catchup_code",
        "catch_up_code",
        "live_access_code",
        "access_code",
        "name",
        "student_name",
        "first_name",
        "last_name",
        "display_name",
        "full_name",
        "preferred_name",
        "roster_name",
        "given_name",
        "family_name",
        "codename",
        "last_display",
        "username",
        "email",
        "reporter_name",
        "teacher_email",
        "teacher_name",
        "student_email",
    }
)
# Opaque reporter ids stay intact while surrounding text is scrubbed.
_ANON_ID_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:student|teacher)-[0-9a-f]{12}(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_BEARER_LOG_RE = re.compile(r"(?i)\bBearer\s+\S+")
_AUTH_HEADER_LOG_RE = re.compile(
    r"(?i)((?:http_)?authorization['\"]?\s*[:=]\s*['\"]?)([^'\"\s,}&]+)"
)
_log_scrub_installed = False


class _AuthorizationLogFilter(logging.Filter):
    """Remove Authorization bearer values from log lines."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Keep the record, with any bearer token replaced.

        Args:
            record: Log record that may mention an Authorization header.

        Returns:
            True. The record is not dropped.
        """
        try:
            message = record.getMessage()
        except Exception:
            return True
        lowered = message.lower()
        if "authorization" not in lowered and "bearer " not in lowered:
            return True
        cleaned = _BEARER_LOG_RE.sub("Bearer [redacted]", message)
        cleaned = _AUTH_HEADER_LOG_RE.sub(r"\1[redacted]", cleaned)
        record.msg = cleaned
        record.args = ()
        return True


def install_authorization_log_scrub() -> None:
    """Redact Authorization bearer values on the process loggers.

    Werkzeug and gunicorn access lines do not include headers. A record
    that still contains an Authorization value, including the report
    token, loses that value before it is emitted. The token is not written.
    """
    global _log_scrub_installed
    if _log_scrub_installed:
        return
    scrub = _AuthorizationLogFilter()
    for name in ("", "werkzeug", "gunicorn", "gunicorn.access", "gunicorn.error"):
        logging.getLogger(name).addFilter(scrub)
    _log_scrub_installed = True


def _bearer_value(header: str) -> str:
    """Return the token from a Bearer header, or empty when it is not one.

    Args:
        header: Raw Authorization value. It is not logged.
    """
    text = str(header or "").strip()
    if not text:
        return ""
    scheme, sep, rest = text.partition(" ")
    if not sep or scheme.lower() != "bearer":
        return ""
    token = rest.strip()
    if not token or any(ch.isspace() for ch in token):
        return ""
    return token


def bearer_token_matches(header: str) -> bool:
    """True when ``header`` matches ``LIVE_FIX_REPORT_TOKEN``.

    An empty or missing env value disables the path. The compare is
    ``hmac.compare_digest``. Neither side is logged.

    Args:
        header: Raw Authorization value.
    """
    expected = (os.environ.get("LIVE_FIX_REPORT_TOKEN") or "").strip()
    if not expected or len(expected) > 256:
        return False
    presented = _bearer_value(header)
    if not presented or len(presented) > 256:
        return False
    try:
        return hmac.compare_digest(
            presented.encode("utf-8"),
            expected.encode("utf-8"),
        )
    except Exception:
        return False


_REPORT_TOKEN_ENDPOINTS = frozenset(
    {
        "staff_live_fix_reports",
        "staff_live_fix_report_latest",
        "staff_live_fix_report",
    }
)


def prepare_report_token_request() -> None:
    """Check the report bearer token, then drop Authorization.

    Only the three End Class report GETs are touched. Every other
    request keeps its Authorization header, and the token flag is left
    unset. The raw token is not stored.
    """
    from flask import g, request

    if request.endpoint not in _REPORT_TOKEN_ENDPOINTS:
        return
    raw = request.environ.pop("HTTP_AUTHORIZATION", None)
    header = raw if isinstance(raw, str) else ""
    g.live_fix_report_token = bearer_token_matches(header)


def live_fix_report_access(view: Callable) -> Callable:
    """Allow a staff session, or the read-only report token, on one view.

    The token flag is set only by :func:`prepare_report_token_request`.
    A signed-in teacher keeps the existing staff checks and the full
    report. With no session, a matching token calls the view; anything
    else is the usual 401. Other routes do not use this decorator.

    Args:
        view: One of the three End Class report GET views.
    """
    try:
        from auth import current_user, staff_required
    except ImportError:
        from lms.auth import current_user, staff_required

    guarded = staff_required(view)

    @wraps(view)
    def decorated(*args: Any, **kwargs: Any):
        """Dispatch staff auth or the report token."""
        from flask import g

        if current_user() is not None:
            return guarded(*args, **kwargs)
        if getattr(g, "live_fix_report_token", False):
            return view(*args, **kwargs)
        return guarded(*args, **kwargs)

    return decorated


def _is_report_secret_key(key: Any, child: Any) -> bool:
    """True when this JSON key is a name, join code, or PIN.

    Args:
        key: Dict key from a report payload.
        child: Value stored under that key.
    """
    lowered = str(key).strip().lower()
    if lowered in _REPORT_SECRET_KEYS or lowered.endswith("_pin") or lowered.endswith("_name"):
        return True
    if lowered != "code" or not isinstance(child, str):
        return False
    try:
        from sentry_wire import _is_ontario_course_code, _looks_like_join_code
    except ImportError:
        from lms.sentry_wire import _is_ontario_course_code, _looks_like_join_code
    if _is_ontario_course_code(child):
        return False
    return _looks_like_join_code(child)


def _gather_secret_strings(value: Any, found: list[str]) -> None:
    """Collect secret strings from a dropped name, code, or PIN field.

    Args:
        value: Field value that will not be returned.
        found: Secret list to extend. Opaque reporter ids are skipped.
    """
    if isinstance(value, str):
        text = value.strip()
        if _ANON_ID_RE.fullmatch(text):
            return
        if len(text) >= 3:
            found.append(text)
        for part in re.split(r"\s+", text):
            piece = part.strip(".,;:()[]{}")
            if len(piece) >= 3 and not _ANON_ID_RE.fullmatch(piece):
                found.append(piece)
        return
    if isinstance(value, dict):
        for child in value.values():
            _gather_secret_strings(child, found)
        return
    if isinstance(value, list):
        for child in value:
            _gather_secret_strings(child, found)


def _dropped_secret_strings(node: Any, found: list[str]) -> None:
    """Walk a payload and collect values of secret keys.

    Args:
        node: Report JSON node.
        found: Secret list to extend.
    """
    if isinstance(node, dict):
        for key, child in node.items():
            if _is_report_secret_key(key, child):
                _gather_secret_strings(child, found)
                continue
            _dropped_secret_strings(child, found)
        return
    if isinstance(node, list):
        for child in node:
            _dropped_secret_strings(child, found)


def _redact_report_text(text: str, secrets: list[str]) -> str:
    """Remove names, join codes, and PINs from one string.

    Opaque ids such as ``student-73ab…`` are held aside so a code or
    PIN scrub cannot split them. Ministry course codes and years are
    left to the same rules the Sentry scrubber already uses.

    Args:
        text: A note, title, or ``copy_text`` field.
        secrets: Known roster values, join codes, and PINs.
    """
    try:
        from sentry_wire import (
            _BY_NAME_RE,
            _CODE_TEXT_RE,
            _NAME_PAIR_RE,
            _NAME_TEXT_RE,
            _PIN_TEXT_RE,
            _STUDENT_LABEL_RE,
            _scrub_by_name,
            _scrub_name_pair,
            _scrub_student_label,
        )
    except ImportError:
        from lms.sentry_wire import (
            _BY_NAME_RE,
            _CODE_TEXT_RE,
            _NAME_PAIR_RE,
            _NAME_TEXT_RE,
            _PIN_TEXT_RE,
            _STUDENT_LABEL_RE,
            _scrub_by_name,
            _scrub_name_pair,
            _scrub_student_label,
        )

    held: list[str] = []

    def hold(match: re.Match[str]) -> str:
        """Replace one opaque id with a placeholder that scrubbing skips."""
        held.append(match.group(0))
        return "\uE000" + ("\uE001" * len(held)) + "\uE002"

    cleaned = scrub_roster_values(_ANON_ID_RE.sub(hold, str(text or "")), secrets)
    cleaned = _PIN_TEXT_RE.sub(r"\1\2[redacted]", cleaned)
    cleaned = _CODE_TEXT_RE.sub(r"\1\2[redacted]", cleaned)
    cleaned = _NAME_TEXT_RE.sub(r"\1\2[redacted]", cleaned)
    cleaned = _STUDENT_LABEL_RE.sub(_scrub_student_label, cleaned)
    cleaned = _BY_NAME_RE.sub(_scrub_by_name, cleaned)
    cleaned = _NAME_PAIR_RE.sub(_scrub_name_pair, cleaned)
    for index, original in enumerate(held):
        cleaned = cleaned.replace("\uE000" + ("\uE001" * (index + 1)) + "\uE002", original)
    return cleaned


def _redact_report_node(node: Any, secrets: list[str]) -> Any:
    """Copy one JSON node without secret keys, scrubbing remaining text.

    Args:
        node: Report JSON node.
        secrets: Values removed from strings.
    """
    if isinstance(node, dict):
        cleaned: dict[str, Any] = {}
        for key, child in node.items():
            if _is_report_secret_key(key, child):
                continue
            cleaned[str(key)] = _redact_report_node(child, secrets)
        return cleaned
    if isinstance(node, list):
        return [_redact_report_node(child, secrets) for child in node]
    if isinstance(node, str):
        return _redact_report_text(node, secrets)
    return node


def redact_report_for_token(payload: Any, secrets: list[str] | None = None) -> Any:
    """Return a token-safe copy of an End Class report payload.

    Secret keys (names, join codes, PINs) are omitted. Their values are
    also removed from remaining strings, including ``copy_text``. Opaque
    reporter ids stay. The stored report is not modified.

    Args:
        payload: JSON object the route would return to a teacher.
        secrets: Extra roster values, join codes, and PINs to strip.
    """
    found = [item for item in (secrets or []) if isinstance(item, str) and item.strip()]
    _dropped_secret_strings(payload, found)
    unique = list(dict.fromkeys(item.strip() for item in found if len(item.strip()) >= 3))
    unique.sort(key=len, reverse=True)
    return _redact_report_node(payload, unique)


def report_token_secrets(school: Any, class_id: int) -> list[str]:
    """Names, join codes, and default PINs for one class.

    Plaintext PINs are used only to find them in a token response. They
    are not logged and are not written back.

    Args:
        school: Open school database.
        class_id: Game-show ``classes.id``.

    Raises:
        Exception: A class, roster, or visit-token lookup failed. The
            token response must not be sent from a partial list.
    """
    found: list[str] = []
    session_row: dict[str, Any] = {"session_code": "", "teacher_user_id": None}
    pin_rows: list[Any] = []
    with school._lock:
        sessions = school.conn.execute(
            """
            SELECT id, session_code, teacher_user_id
            FROM live_class_sessions
            WHERE class_id = ?
            ORDER BY id DESC
            """,
            (int(class_id),),
        ).fetchall()
        portals = school.conn.execute(
            """
            SELECT portal_join_code FROM catchup_links
            WHERE class_id = ?
            """,
            (int(class_id),),
        ).fetchall()
        game = getattr(school, "game", None)
        game_conn = getattr(game, "conn", None)
        if game_conn is not None:
            pin_rows = game_conn.execute(
                """
                SELECT default_pin_ciphertext FROM students
                WHERE class_id = ?
                  AND TRIM(COALESCE(default_pin_ciphertext, '')) <> ''
                """,
                (int(class_id),),
            ).fetchall()
    if sessions:
        session_row = dict(sessions[0])
        for row in sessions:
            code = str(row["session_code"] or "").strip()
            if code:
                found.append(code)
    for row in portals:
        code = str(row["portal_join_code"] or "").strip()
        if code:
            found.append(code)
    found.extend(roster_secrets(school, int(class_id), session_row))
    found.extend(_default_pin_secrets(pin_rows))
    return found


def _default_pin_secrets(rows: list[Any]) -> list[str]:
    """Decrypt default PINs so the token response can drop those digits.

    Args:
        rows: Student rows with ``default_pin_ciphertext``. Failures are
            skipped and not logged, so a ciphertext is not written to a log.
    """
    if not rows:
        return []
    try:
        from default_pin import decrypt_pin
    except ImportError:
        from lms.default_pin import decrypt_pin
    secret = (os.environ.get("FLASK_SECRET_KEY") or os.environ.get("DEFAULT_PIN_KEY") or "")
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            secret = str(current_app.secret_key or secret)
    except Exception:
        pass
    if not secret:
        return []
    pins: list[str] = []
    for row in rows:
        raw = str(row["default_pin_ciphertext"] or "").strip()
        if not raw:
            continue
        try:
            pin = decrypt_pin(raw, secret)
        except Exception:
            continue
        if pin and pin not in pins:
            pins.append(pin)
    return pins

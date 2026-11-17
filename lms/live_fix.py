"""In-class fix notes and the End Live Class triage report (MCK-482, MCK-480).

Flags are written only when someone submits one. Student polling never
reads this table. The report is built once when End Class closes the
session. A Claude tidy, when ``ANTHROPIC_API_KEY`` is set, runs after
that request returns. Sentry is included only when ``SENTRY_AUTH_TOKEN``
and ``SENTRY_ORG`` are set.

Stored context and outbound payloads keep an anonymized reporter id.
They do not keep student names, teacher emails, or join codes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import threading
from datetime import datetime
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

NOTE_LIMIT = 280
ACTION_LIMIT = 10
QUESTION_LIMIT = 8
_ACTION_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")
_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)
_ANON_PEPPER_DEFAULT = "lloves-live-fix-v1"
_SENTRY_PROJECTS_DEFAULT = "lloves-lms,lloves-live"
_ANTHROPIC_MODEL_DEFAULT = "claude-haiku-4-5"
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

HttpJson = Callable[..., Any]


def anonymize_reporter(session_id: int, role: str, raw_key: str) -> str:
    """Return a stable anonymous id for one person in one live session.

    Args:
        session_id: ``live_class_sessions.id``.
        role: ``teacher`` or ``student``.
        raw_key: Server-side id used only as hash input. It is not stored.

    Returns:
        A short id such as ``student-`` plus 12 hex characters.
    """
    pepper = (os.environ.get("LIVE_FIX_ANON_PEPPER") or _ANON_PEPPER_DEFAULT).encode()
    material = f"{int(session_id)}|{role}|{raw_key}".encode()
    digest = hmac.new(pepper, material, hashlib.sha256).hexdigest()[:12]
    return f"{role}-{digest}"


def clean_actions(raw: Any, secrets: list[str] | None = None) -> list[str]:
    """Keep the last button ids and drop labels, names, and free text.

    Args:
        raw: Client-supplied action list.
        secrets: Roster values. An id that equals one of them is dropped.

    Returns:
        At most ten id-shaped strings.
    """
    blocked = {secret.casefold() for secret in (secrets or []) if len(secret) >= 3}
    if not isinstance(raw, list):
        return []
    kept: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        token = item.strip()
        if not _ACTION_RE.fullmatch(token):
            continue
        if token.startswith("live-fix"):
            continue
        if token.casefold() in blocked:
            continue
        kept.append(token)
    return kept[-ACTION_LIMIT:]


def roster_secrets(school: Any, class_id: int, session_row: dict[str, Any]) -> list[str]:
    """Names, emails, and the join code that must not be stored or sent.

    Args:
        school: Open school database.
        class_id: Game-show ``classes.id``.
        session_row: Live session columns, including ``session_code``.

    Returns:
        Secrets of at least three characters, longest first.
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
        try:
            columns = [str(row[1]) for row in conn.execute("PRAGMA table_info(students)")]
        except Exception:
            columns = []
        wanted = [
            name
            for name in ("codename", "first_name", "last_name", "email", "student_raw")
            if name in columns
        ]
        if wanted:
            sql = (
                f"SELECT {', '.join(wanted)} FROM students WHERE class_id = ?"
            )
            try:
                rows = conn.execute(sql, (int(class_id),)).fetchall()
            except Exception:
                rows = []
            for row in rows:
                for value in row:
                    text = str(value or "").strip()
                    if len(text) >= 3:
                        found.append(text)
    unique = list(dict.fromkeys(found))
    unique.sort(key=len, reverse=True)
    return unique


def scrub_secrets(text: str, secrets: list[str]) -> str:
    """Replace roster secrets and email addresses with ``[redacted]``.

    Args:
        text: Note, title, or other free text.
        secrets: Values from :func:`roster_secrets`.

    Returns:
        Text with those values and email addresses removed.
    """
    cleaned = str(text or "")
    for secret in secrets:
        if len(secret) < 3 or secret.lower() in {"the", "and"}:
            continue
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .'-]*", secret):
            pattern = rf"\b{re.escape(secret)}\b"
        else:
            pattern = re.escape(secret)
        cleaned = re.sub(pattern, "[redacted]", cleaned, flags=re.IGNORECASE)
    cleaned = _EMAIL_RE.sub("[redacted]", cleaned)
    try:
        from sentry_wire import _scrub_text
    except ImportError:
        from lms.sentry_wire import _scrub_text
    return _scrub_text(cleaned)


def clean_note(text: str, secrets: list[str]) -> str:
    """Turn a submitted note into one short stored line.

    Args:
        text: Raw note from the teacher or student.
        secrets: Values that must not remain in the note.

    Returns:
        A single line of at most 280 characters.
    """
    cleaned = scrub_secrets(text, secrets).replace("\n", " ").replace("\r", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:NOTE_LIMIT]


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
    for project in config["projects"]:
        url = (
            f"{config['base']}/api/0/projects/{quote(str(config['org']))}/"
            f"{quote(project)}/issues/?query={quote(f'lastSeen:-{minutes}m')}"
            "&limit=25"
        )
        try:
            payload = call(
                url,
                headers={"Authorization": f"Bearer {config['token']}"},
                timeout=2.5,
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
    return {
        "session_id": int(session_id),
        "class_id": int(session_row["class_id"]),
        "started_at": str(session_row.get("started_at") or ""),
        "ended_at": ended_at,
        "flag_count": len(flags),
        "flags_by_slide": _group_flags(flags),
        "sentry": sentry,
        "tidy": {"status": _tidy_status(bool(anthropic_api_key()))},
    }


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

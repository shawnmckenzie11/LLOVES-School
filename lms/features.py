"""Per-teacher feature switches (MCK-345).

The registry says which keys a basic-plan teacher has. ``user_features``
stores a per-teacher override. ``has_feature`` reads the override first
and otherwise the plan default. Students have no accounts: a live session
uses the teacher who owns it.

This module does not attach :func:`feature_required` to any production
route. Later tickets gate import, whiteboards, and the screens.
"""

from __future__ import annotations

import logging
from datetime import datetime
from functools import wraps
from typing import Any, Callable

logger = logging.getLogger(__name__)

PLAN_BASIC = "basic"

#: One registry. True means on for a basic-plan teacher.
#: Anything absent from this map is off.
FEATURE_REGISTRY: dict[str, bool] = {
    # Individual and group multiple choice.
    "mc_questions": True,
    # Individual and group ranking.
    "rank_questions": True,
    # Individual numeric answers.
    "numeric_questions": True,
    # Individual essay answers.
    "essay_questions": True,
    # Built-in live lesson decks.
    "live_slides": True,
    "catchup_links": True,
    "celebrations": True,
    # Automated gradebook, attendance, and participation.
    "gradebook_tracking": True,
    # Rank game show, the public scoreboard, and celebrations.
    "game_show": True,
    # Google Slides / PowerPoint import.
    "slides_import": False,
    "whiteboard": False,
    "media": False,
    "team_draw": False,
    "group_draw": False,
    "portfolio_gdoc": False,
    "news_wire_extras": False,
}

FEATURE_KEYS: tuple[str, ...] = tuple(FEATURE_REGISTRY)
BASIC_ON: tuple[str, ...] = tuple(
    key for key, enabled in FEATURE_REGISTRY.items() if enabled
)
BASIC_OFF: tuple[str, ...] = tuple(
    key for key, enabled in FEATURE_REGISTRY.items() if not enabled
)

# school_settings key. Set after the first grant so a later boot does not
# turn every feature on for teachers who joined in between.
GRANDFATHER_MARKER = "mck345_feature_grandfather_v1"

USER_PLAN_DDL = "ALTER TABLE users ADD COLUMN plan TEXT NOT NULL DEFAULT 'basic'"

USER_FEATURES_DDL = """
CREATE TABLE IF NOT EXISTS user_features (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    feature_key TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (user_id, feature_key)
)
"""


def _now() -> str:
    """Local ISO timestamp without microseconds, matching ``school_db._now``."""
    return datetime.now().replace(microsecond=0).isoformat()


def plan_default(plan: str | None, key: str) -> bool:
    """Return the plan default for ``key``.

    Only ``basic`` is defined. A missing plan is basic. Unknown keys are
    off on every plan. A per-teacher row still wins over this default.

    Args:
        plan: ``users.plan``, or None when the column is empty.
        key: Feature key.

    Returns:
        True when a teacher on this plan has the feature with no override.
    """
    normalized = (plan or PLAN_BASIC).strip().lower()
    if normalized != PLAN_BASIC:
        return False
    return bool(FEATURE_REGISTRY.get(str(key or ""), False))


def ensure_feature_schema(conn: Any) -> None:
    """Create ``user_features`` and add ``users.plan`` when it is missing.

    Safe to call on every boot. A second call does not rewrite the column.

    Args:
        conn: sqlite connection. The caller holds the boot lock.
    """
    conn.execute(USER_FEATURES_DDL)
    cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(users)")}
    if "plan" not in cols:
        conn.execute(USER_PLAN_DDL)


def _it_allowlist() -> set[str]:
    """Emails allowed on the IT portal, lower-cased.

    Imported inside the call so this module can load during schema boot
    without an import cycle through auth.
    """
    try:
        from auth import it_emails
    except ImportError:
        from lms.auth import it_emails
    return {str(email).strip().lower() for email in it_emails() if str(email).strip()}


def _eligible_user_ids(conn: Any, allowlist: set[str]) -> list[int]:
    """User ids who keep every feature: staff, IT, allowlist, test invites.

    Args:
        conn: sqlite connection.
        allowlist: Lower-cased IT portal emails.

    Returns:
        Distinct ``users.id`` values, ascending.
    """
    emails = sorted(allowlist)
    email_sql = ""
    params: list[Any] = []
    if emails:
        marks = ",".join("?" * len(emails))
        email_sql = f"OR lower(u.email) IN ({marks})"
        params.extend(emails)
    rows = conn.execute(
        f"""
        SELECT DISTINCT u.id
        FROM users u
        LEFT JOIN staff_invites i
            ON i.user_id = u.id AND lower(i.kind) = 'test'
        WHERE lower(u.role) IN ('staff', 'it')
           OR COALESCE(u.is_test, 0) = 1
           OR i.id IS NOT NULL
           {email_sql}
        ORDER BY u.id
        """,
        params,
    ).fetchall()
    return [int(row["id"]) for row in rows]


def _grant_all_features_locked(conn: Any, allowlist: set[str]) -> dict[str, int]:
    """Upsert ``enabled = 1`` for every registry key on the launch cohort.

    A row that is already on is not rewritten, so ``updated_at`` stays.
    The caller holds ``school._lock``.

    Args:
        conn: sqlite connection.
        allowlist: Lower-cased IT portal emails.

    Returns:
        ``users`` in the cohort and ``rows_written`` (inserts and flips).
    """
    user_ids = _eligible_user_ids(conn, allowlist)
    stamp = _now()
    written = 0
    for user_id in user_ids:
        for key in FEATURE_KEYS:
            before = int(conn.total_changes)
            conn.execute(
                """
                INSERT INTO user_features (user_id, feature_key, enabled, updated_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(user_id, feature_key) DO UPDATE SET
                    enabled = 1,
                    updated_at = excluded.updated_at
                WHERE user_features.enabled != 1
                """,
                (int(user_id), key, stamp),
            )
            if int(conn.total_changes) != before:
                written += 1
    return {"users": len(user_ids), "rows_written": written}


def migrate_existing_feature_access(school: Any) -> dict[str, int]:
    """Turn every registry feature on for the current launch cohort.

    Eligible accounts are staff, IT, the IT email allowlist, and
    test-invite accounts (``users.is_test`` or a test invite). Rows that
    are already on stay as they are, including ``updated_at``. A second
    call on the same cohort writes nothing.

    Args:
        school: Open ``SchoolDB`` or ``LovesDB``.

    Returns:
        ``users`` counted and ``rows_written``.
    """
    with school._lock:
        result = _grant_all_features_locked(school.conn, _it_allowlist())
    logger.info(
        "feature access migration users=%s rows_written=%s",
        result["users"],
        result["rows_written"],
    )
    return result


def grandfather_features_once(school: Any) -> dict[str, int]:
    """Grant the launch cohort once, then leave later teachers on basic.

    The marker lives in ``school_settings``. A second call writes no
    ``user_features`` rows and does not refresh the marker.

    Args:
        school: Open school database, after invite schema and any local seed.

    Returns:
        Migration counts plus ``applied`` (1 the first time, else 0).
    """
    with school._lock:
        school.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS school_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        existing = school.conn.execute(
            "SELECT value FROM school_settings WHERE key = ?",
            (GRANDFATHER_MARKER,),
        ).fetchone()
        if existing is not None:
            return {"applied": 0, "users": 0, "rows_written": 0}
        result = _grant_all_features_locked(school.conn, _it_allowlist())
        school.conn.execute(
            """
            INSERT OR IGNORE INTO school_settings (key, value, updated_at)
            VALUES (?, '1', ?)
            """,
            (GRANDFATHER_MARKER, _now()),
        )
    logger.info(
        "feature grandfather applied users=%s rows_written=%s",
        result["users"],
        result["rows_written"],
    )
    return {"applied": 1, **result}


def _school():
    """Return the Flask process school database.

    Imported lazily so schema boot does not cycle through auth.
    """
    try:
        from auth import school_db
    except ImportError:
        from lms.auth import school_db
    return school_db()


def _as_user(user: Any) -> dict[str, Any] | None:
    """Copy a user row into a dict, or None when there is no id."""
    if user is None:
        return None
    data = user if isinstance(user, dict) else dict(user)
    if data.get("id") in (None, ""):
        return None
    return data


def has_feature(user: Any, key: str) -> bool:
    """True when this teacher may use ``key``.

    A ``user_features`` row wins, including an override that turns a
    basic-on key off. With no row, the plan default applies. Unknown
    keys are off unless an override turns them on. A missing user is off.

    Args:
        user: Staff or IT user row (``users.id`` required).
        key: Feature key.

    Returns:
        Whether the feature is on for this teacher.
    """
    row = _as_user(user)
    if row is None:
        return False
    school = _school()
    with school._lock:
        override = school.conn.execute(
            """
            SELECT enabled FROM user_features
            WHERE user_id = ? AND feature_key = ?
            """,
            (int(row["id"]), str(key)),
        ).fetchone()
        if override is not None:
            return int(override["enabled"]) != 0
        plan = row.get("plan")
        if not plan:
            stored = school.conn.execute(
                "SELECT plan FROM users WHERE id = ?",
                (int(row["id"]),),
            ).fetchone()
            plan = stored["plan"] if stored is not None else PLAN_BASIC
    return plan_default(str(plan or PLAN_BASIC), str(key))


def set_feature_override(school: Any, user_id: int, key: str, enabled: bool) -> None:
    """Upsert one per-teacher override.

    Args:
        school: Open school database.
        user_id: ``users.id``.
        key: Feature key. Unknown keys are allowed so a later flag can
            be stored before it joins the registry.
        enabled: True turns the feature on for this teacher.
    """
    flag = 1 if enabled else 0
    with school._lock:
        school.conn.execute(
            """
            INSERT INTO user_features (user_id, feature_key, enabled, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, feature_key) DO UPDATE SET
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (int(user_id), str(key), flag, _now()),
        )


def feature_for_session(session_id: int, key: str) -> bool:
    """The owning teacher's flag for this live session.

    Students have no accounts and always follow that teacher. A missing
    session, or a session with no teacher, is off.

    Args:
        session_id: ``live_class_sessions.id``.
        key: Feature key.

    Returns:
        Whether the session's teacher has the feature.
    """
    try:
        sid = int(session_id)
    except (TypeError, ValueError):
        return False
    school = _school()
    with school._lock:
        row = school.conn.execute(
            "SELECT teacher_user_id FROM live_class_sessions WHERE id = ?",
            (sid,),
        ).fetchone()
    if row is None or row["teacher_user_id"] in (None, ""):
        return False
    teacher = school.get_user(int(row["teacher_user_id"]))
    if not teacher:
        return False
    return has_feature(teacher, key)


def _positive_int(raw: Any) -> int | None:
    """Parse a session id, or None when it is not a positive integer."""
    if raw in (None, ""):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return value


def _session_id_from_request(kwargs: dict[str, Any]) -> int | None:
    """Live session id from the view kwargs or the current request.

    Args:
        kwargs: Keyword arguments bound for the view.
    """
    for name in ("session_id", "live_session_id"):
        found = _positive_int(kwargs.get(name))
        if found is not None:
            return found
    try:
        from flask import has_request_context, request
    except ImportError:
        return None
    if not has_request_context():
        return None
    sources: list[Any] = [request.view_args or {}, request.args, request.form]
    payload = request.get_json(silent=True)
    if isinstance(payload, dict):
        sources.append(payload)
    for source in sources:
        if not source:
            continue
        for name in ("session_id", "live_session_id"):
            found = _positive_int(source.get(name))
            if found is not None:
                return found
    return None


def _caller_has_feature(key: str, kwargs: dict[str, Any]) -> bool:
    """Resolve the flag for the signed-in teacher, else the session owner.

    Args:
        key: Feature key.
        kwargs: View keyword arguments, used to find a live session.
    """
    try:
        from auth import current_user
    except ImportError:
        from lms.auth import current_user
    try:
        user = current_user()
    except RuntimeError:
        user = None
    if user is not None:
        return has_feature(user, key)
    session_id = _session_id_from_request(kwargs)
    if session_id is None:
        return False
    return feature_for_session(session_id, key)


def feature_required(key: str) -> Callable:
    """Decorate a view so a missing feature answers 404.

    Staff and IT are checked with :func:`has_feature`. A signed-out
    request uses :func:`feature_for_session` when the view names a live
    session. Off is 404, not 403. Do not put this on production routes
    in the registry PR; the gate tickets do that.

    Args:
        key: Feature key that must be on.

    Returns:
        A decorator for a Flask view.
    """

    def decorator(view: Callable) -> Callable:
        """Attach the feature check to ``view``."""

        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any):
            """Return 404 when ``key`` is off for this caller."""
            if not _caller_has_feature(key, kwargs):
                from flask import abort

                abort(404)
            return view(*args, **kwargs)

        return wrapped

    return decorator

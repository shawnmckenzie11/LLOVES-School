"""Optional Sentry reporting for the LLOVES LMS.

DSNs come from the environment only. An empty ``SENTRY_DSN`` leaves the
process uninstrumented so local runs, CI, and a Fly machine that does not
have the secret yet all boot the same way.

The browser key is a different variable, ``SENTRY_DSN_LIVE``. Templates
read it through :func:`sentry_browser_context`. It is never written into
static JavaScript.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any, Callable
from urllib.parse import urlparse

from serve_capacity import is_live_state_poll

logger = logging.getLogger(__name__)

# Browser CSP ``connect-src`` hosts for the Sentry ingest endpoint.
# Wildcards only — the DSN itself stays in the environment.
SENTRY_CONNECT_SRC = "https://*.ingest.sentry.io https://*.ingest.us.sentry.io"

# Non-poll transactions. Live polls are excluded in :func:`traces_sampler`.
DEFAULT_TRACES_SAMPLE_RATE = 0.05

# Client-gone during a poll. These are not application faults.
_DISCONNECT_TYPES = frozenset(
    {
        "ConnectionResetError",
        "BrokenPipeError",
        "ConnectionAbortedError",
        "ClientDisconnected",
    }
)

_QUIET_EXACT = frozenset(
    {
        "/health",
        "/api/student/heartbeat",
        "/api/student/state",
    }
)


def environment_name() -> str:
    """Return the Sentry environment tag.

    ``SENTRY_ENVIRONMENT`` wins when it is set, so a tip smoke can label
    events without ``FLASK_ENV=production`` (that flag refuses
    ``LOCAL_DEV_LOGIN``). Otherwise the tag is ``FLASK_ENV``. An unset
    ``FLASK_ENV`` is ``development``.

    Returns:
        A short environment name.
    """
    override = (os.getenv("SENTRY_ENVIRONMENT") or "").strip()
    if override:
        return override
    flask_env = (os.getenv("FLASK_ENV") or "").strip().lower()
    if flask_env:
        return flask_env
    return "development"


def release_name() -> str | None:
    """Return the deploy SHA used as the Sentry release.

    ``GH_SHA`` is the image build-arg from Actions. ``SENTRY_RELEASE`` is
    an optional override. Empty values omit the tag.

    Returns:
        The release string, or ``None`` when neither variable is set.
    """
    sha = (os.getenv("GH_SHA") or "").strip()
    if sha:
        return sha
    fallback = (os.getenv("SENTRY_RELEASE") or "").strip()
    return fallback or None


def sentry_browser_context() -> dict[str, str]:
    """Return template values for the public live-shell Sentry snippet.

    ``sentry_live_dsn`` is ``SENTRY_DSN_LIVE``. The browser key is public
    by design and is still read from the environment so it is not stored
    in git. An empty string means the partial renders no script tags.

    Returns:
        ``sentry_live_dsn``, ``sentry_environment``, and ``sentry_release``.
    """
    return {
        "sentry_live_dsn": (os.getenv("SENTRY_DSN_LIVE") or "").strip(),
        "sentry_environment": environment_name(),
        "sentry_release": release_name() or "",
        "sentry_user_id": "",
        "sentry_tags_json": "{}",
    }


def _path_only(value: str) -> str:
    """Reduce a URL or ``METHOD /path`` name to a path.

    Args:
        value: Full URL, path, or Sentry transaction name.

    Returns:
        The path without a query string or trailing slash.
    """
    text = str(value or "").strip()
    if "://" in text:
        text = urlparse(text).path or ""
    elif " " in text:
        tail = text.split()[-1]
        if tail.startswith("/"):
            text = tail
    text = text.split("?", 1)[0].split("#", 1)[0]
    if len(text) > 1:
        text = text.rstrip("/")
    return text


def is_quiet_telemetry_path(path: str) -> bool:
    """True for health checks and live-shell polls.

    Those routes fail soft in the staff and student shells (reconnect /
    busy strip). Sampling them would record one transaction per beat.

    Args:
        path: URL path or transaction name that may include the path.

    Returns:
        Whether tracing should drop the path and disconnects on it are noise.
    """
    cleaned = _path_only(path)
    if cleaned in _QUIET_EXACT:
        return True
    return is_live_state_poll(cleaned)


def _exception_type_names(
    hint: dict[str, Any] | None, event: dict[str, Any]
) -> set[str]:
    """Collect exception class names from a Sentry hint and event.

    Args:
        hint: SDK hint, possibly containing ``exc_info``.
        event: Event payload, possibly containing ``exception.values``.

    Returns:
        Class names. Empty when the event has no exception.
    """
    names: set[str] = set()
    info = (hint or {}).get("exc_info")
    if isinstance(info, tuple) and info and info[0] is not None:
        exc_type = info[0]
        names.add(str(getattr(exc_type, "__name__", "") or ""))
        for base in getattr(exc_type, "__mro__", ()):
            names.add(str(getattr(base, "__name__", "") or ""))
    values = ((event.get("exception") or {}).get("values") or [])
    if isinstance(values, list):
        for value in values:
            if isinstance(value, dict) and value.get("type"):
                names.add(str(value["type"]))
    names.discard("")
    return names


def before_send(
    event: dict[str, Any], hint: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Drop client disconnects on live polls and keep real exceptions.

    A browser that goes away mid-poll is the same noise as the reconnect
    strip. ``ValueError``, sqlite errors, and every non-poll failure
    still send.

    Args:
        event: Sentry event payload.
        hint: Optional SDK hint, including ``exc_info``.

    Returns:
        The event to send, or ``None`` to drop it.
    """
    try:
        paths: list[str] = []
        request = event.get("request") or {}
        if isinstance(request, dict) and request.get("url"):
            paths.append(str(request.get("url")))
        if event.get("transaction"):
            paths.append(str(event.get("transaction")))
        if not any(is_quiet_telemetry_path(path) for path in paths):
            return event
        if _exception_type_names(hint, event) & _DISCONNECT_TYPES:
            return None
    except Exception:
        logger.exception("Sentry before_send failed open")
        return event
    return event


def traces_sampler(sampling_context: dict[str, Any]) -> float:
    """Sample lightly, and never sample health or live-shell polls.

    A sampled browser pageload can still attach a ``sentry-trace`` header.
    Quiet paths return 0 even when the parent was sampled, so a class poll
    cannot open a transaction on every beat.

    Args:
        sampling_context: Sentry context with ``wsgi_environ``,
            ``transaction_context``, and ``parent_sampled``.

    Returns:
        A sample rate from 0 through 1.
    """
    try:
        path = ""
        environ = sampling_context.get("wsgi_environ") or {}
        if isinstance(environ, dict):
            path = str(environ.get("PATH_INFO") or "")
        if not path:
            ctx = sampling_context.get("transaction_context") or {}
            if isinstance(ctx, dict):
                path = str(ctx.get("name") or "")
        if is_quiet_telemetry_path(path):
            return 0.0
        parent = sampling_context.get("parent_sampled")
        if parent is True:
            return 1.0
        if parent is False:
            return 0.0
    except Exception:
        logger.exception("Sentry traces_sampler failed open")
        return DEFAULT_TRACES_SAMPLE_RATE
    return DEFAULT_TRACES_SAMPLE_RATE


def init_flask_sentry() -> bool:
    """Start the Flask Sentry SDK when ``SENTRY_DSN`` is set.

    Gunicorn's production command does not preload the application, so
    each worker calls this from ``create_app`` after fork. sentry-sdk
    also resets its transport in a forked child. An empty DSN, a missing
    package, or a rejected DSN is logged and swallowed. The LMS still boots.

    The SDK auto-enables the Flask integration. This function does not
    pass ``integrations``, because an explicit list replaces those
    defaults.

    Returns:
        True when ``sentry_sdk.init`` completed.
    """
    dsn = (os.getenv("SENTRY_DSN") or "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        logger.warning(
            "SENTRY_DSN is set but sentry-sdk is not installed; "
            "booting without Sentry"
        )
        return False
    options: dict[str, Any] = {
        "dsn": dsn,
        "environment": environment_name(),
        "send_default_pii": False,
        "include_local_variables": False,
        "max_request_body_size": "never",
        "traces_sampler": traces_sampler,
        "before_send": before_send,
        # MCK-183 S3: Sentry Logs carry one line per key teacher action.
        # ``before_send_log`` keeps those lines only, so ordinary Python
        # logging (which can mention anything) never becomes a Sentry log.
        "enable_logs": True,
        "before_send_log": before_send_log,
    }
    release = release_name()
    if release:
        options["release"] = release
    try:
        sentry_sdk.init(**options)
    except Exception as exc:
        # The DSN can show up in parser errors. Log the class only.
        logger.warning(
            "Sentry init failed (%s); booting without Sentry",
            type(exc).__name__,
        )
        return False
    logger.info("Sentry enabled (environment=%s)", options["environment"])
    return True


# ---------------------------------------------------------------------------
# MCK-183: who hit the error, and which tool they were in.
#
# Staff requests get ``set_user({"id", "email_hash"})``. Students never get a
# user. Every request gets ``portal`` and ``tool`` tags. ``class_id`` and
# ``course_code`` are resolved inside an event processor, so the database is
# only read when an error event is actually sent.
# ---------------------------------------------------------------------------

#: Attribute every MCK-183 action log line carries. ``before_send_log``
#: drops any Sentry log without it.
ACTION_LOG_ATTRIBUTE = "alc.action"

#: Flask endpoint -> Sentry Logs action line. One line per successful call.
ACTION_ENDPOINTS: dict[str, str] = {
    "staff_home": "Dashboard opened",
    "staff_run_live_class": "Run Live Class",
    "api_publish_live_item": "Publish",
    "staff_end_live_class": "End Live Class",
    "staff_quit_live_class": "Quit",
    "api_finalize_attendance": "Take Attendance saved",
    "staff_classes": "Roster edited",
    "staff_replace_roster": "Roster edited",
    "api_add_student": "Roster edited",
    "api_del_student": "Roster edited",
    "api_rename_roster_student": "Roster edited",
    "staff_import_live_mc": "Import from bank",
    "staff_import_live_content_questions": "Import from bank",
    "staff_add_live_question": "Add New",
    "staff_live_bank_add_question": "Add New",
}

#: ``staff_classes`` is GET and POST. Only the POST edits the roster.
_ACTION_METHODS: dict[str, frozenset[str]] = {
    "staff_classes": frozenset({"POST"}),
}

_EMAIL_HASH_LEN = 12

# (pattern, tool). First match wins. Paths are matched without a query.
_TOOL_RULES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), tool)
    for pattern, tool in (
        (r"^/(?:it|api/it)(?:/|$)", "admin"),
        (r"^/auth/student-code$", "student-join"),
        (r"^/student/(?:pick|s/|character|waiting)", "student-join"),
        (r"^/(?:auth|verify-email|resend-verification|logout|request-access)(?:/|$)", "auth"),
        (r"^/(?:student|api/student)(?:/|$)", "live"),
        (r"^/staff/?$", "dashboard"),
        (r"^/api/staff/defaults$", "dashboard"),
        (r"^/api/live-sessions/active$", "dashboard"),
        (r"^/api/staff/classes(?:/\d+/roster)?$", "roster"),
        (r"^/api/classes/\d+/(?:students|game/add-student|game/rename)", "roster"),
        (r"^/api/classes/\d+/(?:attendance|game/attendance|game/finalize-attendance|game/meeting|game/cancel|begin|log-context|sessions)", "attendance"),
        (r"^/api/classes/\d+/(?:participation-grid|mood-grid|ap-round-profiles|feedback-live)", "participation"),
        (r"^/api/classes/\d+/(?:gradebook|grade-scheme|grade-weights|subtotals|portfolio)", "gradebook"),
        (r"^/staff/class/\d+/results\.csv$", "gradebook"),
        (r"^/api/staff/class/\d+/(?:question-bank|module-banks|live-bank|components|question-overlays)", "banks"),
        (r"^/api/staff/class/\d+/live-lessons/.+/(?:import-mc|import-contest-questions)$", "banks"),
        (r"^/api/staff/class/\d+/live-lessons/contest-questions$", "banks"),
        (r"^/staff/class/\d+/(?:run-live|end-live|quit-live)$", "live"),
        (r"^/(?:api/live-sessions|api/live|live-overlay)(?:/|$)", "live"),
        (r"^/api/staff/class/\d+/(?:live-lessons|live-question-image)", "live"),
        (r"^/api/classes/\d+/(?:live-|lesson-slides|game|show-rank|stat-window|scoreboard)", "live"),
        (r"^/(?:staff|api/staff)/", "course"),
        (r"^/api/classes/", "course"),
    )
)

# ``/staff/class/<id>?tab=...`` is one route with many tools.
_COURSE_TAB_TOOLS = {
    "live": "live",
    "track-live": "live",
    "track_live": "live",
    "ap": "attendance",
    "track": "attendance",
    "attendance": "attendance",
    "attendance-participation": "attendance",
    "participation": "participation",
    "grades": "gradebook",
    "gradebook": "gradebook",
    "question-banks": "banks",
}


def owner_emails() -> frozenset[str]:
    """Return the lowercased owner emails from ``SENTRY_OWNER_EMAILS``.

    Unset or blank means no owners. Every teacher is then ``other``, so an
    unconfigured deploy over-reports rather than hiding another teacher's
    errors under ``owner``.

    Returns:
        Lowercased, stripped addresses. Empty when the variable is unset.
    """
    raw = os.getenv("SENTRY_OWNER_EMAILS") or ""
    return frozenset(
        part.strip().lower() for part in raw.split(",") if part.strip()
    )


def teacher_kind(email: str | None) -> str:
    """``owner`` for an address in ``SENTRY_OWNER_EMAILS``, else ``other``.

    Args:
        email: Signed-in teacher email.

    Returns:
        ``owner`` or ``other``.
    """
    cleaned = str(email or "").strip().lower()
    if cleaned and cleaned in owner_emails():
        return "owner"
    return "other"


def email_hash(email: str | None) -> str:
    """Short sha256 of a lowercased email. The raw address is never sent.

    Args:
        email: Teacher email.

    Returns:
        The first 12 hex characters, or an empty string for no email.
    """
    cleaned = str(email or "").strip().lower()
    if not cleaned:
        return ""
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:_EMAIL_HASH_LEN]


def tool_for_request(path: str, args: Any = None) -> str:
    """Map a request path (and course-page ``tab``) to a tool tag.

    Args:
        path: ``request.path``.
        args: ``request.args`` or any mapping with ``get``.

    Returns:
        One of live, attendance, participation, roster, banks, gradebook,
        dashboard, admin, student-join, auth, course, or other.
    """
    cleaned = _path_only(path)
    if re.match(r"^/staff/class/\d+$", cleaned):
        tab = ""
        view = ""
        if args is not None:
            tab = str(args.get("tab") or "").strip().lower()
            view = str(args.get("view") or "").strip().lower()
        tool = _COURSE_TAB_TOOLS.get(tab, "course")
        if tool == "attendance" and view == "participation":
            return "participation"
        return tool
    for pattern, tool in _TOOL_RULES:
        if pattern.search(cleaned):
            return tool
    return "other"


def portal_for_request(path: str, session_data: Any) -> str:
    """``staff``, ``it``, ``student``, or ``public`` for this request.

    Args:
        path: ``request.path``.
        session_data: Flask ``session`` (or a dict).

    Returns:
        The portal tag.
    """
    cleaned = _path_only(path)
    if session_data and session_data.get("logged_in") and session_data.get("user_id"):
        return "it" if cleaned.startswith(("/it", "/api/it")) else "staff"
    if cleaned.startswith(("/student", "/api/student", "/auth/student-code")):
        return "student"
    if session_data and session_data.get("student_class_id"):
        return "student"
    return "public"


def staff_identity(session_data: Any) -> dict[str, str] | None:
    """Sentry user for a signed-in teacher, from the session only.

    No database read, no name, no raw email.

    Args:
        session_data: Flask ``session`` (or a dict).

    Returns:
        ``{"id", "email_hash"}`` or ``None`` for students and guests.
    """
    if not session_data or not session_data.get("logged_in"):
        return None
    user_id = session_data.get("user_id")
    if not user_id:
        return None
    return {"id": str(int(user_id)), "email_hash": email_hash(session_data.get("email"))}


def request_ids(view_args: dict[str, Any] | None, session_data: Any) -> dict[str, int]:
    """Class, live-session, and offering ids this request names.

    Args:
        view_args: ``request.view_args``.
        session_data: Flask ``session``; a student session names its class.

    Returns:
        Any of ``class_id``, ``session_id``, ``offering_id``.
    """
    out: dict[str, int] = {}
    for key in ("class_id", "session_id", "offering_id"):
        value = (view_args or {}).get(key)
        if isinstance(value, int):
            out[key] = value
    if "class_id" not in out and session_data and not session_data.get("logged_in"):
        raw = session_data.get("student_class_id")
        if isinstance(raw, int):
            out["class_id"] = raw
    return out


def _clean_tags(tags: dict[str, Any]) -> dict[str, str]:
    """Drop empty tag values and stringify the rest."""
    return {key: str(value) for key, value in tags.items() if value not in (None, "")}


def resolve_class_course(
    resolver: Callable[..., dict[str, Any]] | None, ids: dict[str, int]
) -> dict[str, str]:
    """Run the lazy class/course lookup. Never raises.

    Args:
        resolver: ``SchoolDB.telemetry_class_course`` or a stand-in.
        ids: Output of :func:`request_ids`.

    Returns:
        ``class_id`` / ``course_code`` tags that could be resolved.
    """
    tags: dict[str, Any] = {}
    if ids.get("class_id") is not None:
        tags["class_id"] = ids["class_id"]
    if resolver is None or not ids:
        return _clean_tags(tags)
    try:
        found = resolver(**ids) or {}
        tags.update({k: found.get(k) for k in ("class_id", "course_code") if found.get(k)})
    except Exception:  # noqa: BLE001 - telemetry must never fail the event
        logger.debug("Sentry class/course lookup failed", exc_info=True)
    return _clean_tags(tags)


def request_tags(path: str, args: Any, session_data: Any) -> dict[str, str]:
    """Cheap per-request tags: teacher_id, teacher_kind, portal, tool.

    Args:
        path: ``request.path``.
        args: ``request.args``.
        session_data: Flask ``session``.

    Returns:
        Tag dict. Students get portal and tool only.
    """
    tags: dict[str, Any] = {
        "portal": portal_for_request(path, session_data),
        "tool": tool_for_request(path, args),
    }
    identity = staff_identity(session_data)
    if identity:
        tags["teacher_id"] = identity["id"]
        tags["teacher_kind"] = teacher_kind(session_data.get("email"))
    return _clean_tags(tags)


def make_event_processor(
    resolver: Callable[..., dict[str, Any]] | None, ids: dict[str, int]
) -> Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]:
    """Event processor that adds class_id/course_code when an error is sent.

    Transactions skip the lookup, so sampled traces never read the database.

    Args:
        resolver: Lazy class/course lookup.
        ids: Ids named by the request.

    Returns:
        A Sentry event processor.
    """

    def processor(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any]:
        """Attach class/course tags to error events only."""
        try:
            if event.get("type") == "transaction":
                return event
            tags = event.setdefault("tags", {})
            if isinstance(tags, dict):
                for key, value in resolve_class_course(resolver, ids).items():
                    tags.setdefault(key, value)
        except Exception:  # noqa: BLE001
            logger.debug("Sentry event processor failed open", exc_info=True)
        return event

    return processor


def before_send_log(log: dict[str, Any], _hint: Any = None) -> dict[str, Any] | None:
    """Keep only MCK-183 action lines in Sentry Logs.

    Args:
        log: Sentry log payload.
        _hint: SDK hint.

    Returns:
        The log, or ``None`` for anything that is not an action line.
    """
    try:
        attributes = log.get("attributes") or {}
        if ACTION_LOG_ATTRIBUTE in attributes:
            return log
    except Exception:  # noqa: BLE001
        return None
    return None


def action_for(endpoint: str | None, method: str) -> str | None:
    """The action line name for a successful request, if it is one of ours.

    Args:
        endpoint: ``request.endpoint``.
        method: HTTP method.

    Returns:
        Action name, or ``None``.
    """
    if not endpoint or endpoint not in ACTION_ENDPOINTS:
        return None
    allowed = _ACTION_METHODS.get(endpoint)
    if allowed is not None and method.upper() not in allowed:
        return None
    return ACTION_ENDPOINTS[endpoint]


def log_action(action: str, tags: dict[str, str]) -> None:
    """Write one Sentry Logs line for a teacher action. No student data.

    Attributes are the tag values only: teacher_kind, teacher_id,
    course_code, class_id, tool, portal.

    Args:
        action: One of :data:`ACTION_ENDPOINTS` values.
        tags: Request tags (already free of student data).
    """
    try:
        import sentry_sdk
        from sentry_sdk import logger as sentry_logger
    except ImportError:
        return
    if not sentry_sdk.get_client().is_active():
        return
    attributes: dict[str, Any] = {ACTION_LOG_ATTRIBUTE: action}
    for key in ("teacher_kind", "teacher_id", "course_code", "class_id", "tool", "portal"):
        if tags.get(key):
            attributes[key] = tags[key]
    try:
        sentry_logger.info(action, attributes=attributes)
    except Exception:  # noqa: BLE001
        logger.debug("Sentry action log failed", exc_info=True)


def browser_context(
    path: str,
    args: Any,
    session_data: Any,
    view_args: dict[str, Any] | None,
    resolver: Callable[..., dict[str, Any]] | None,
) -> dict[str, str]:
    """Browser meta values: user id (staff only) and the same tags as server.

    Only called when ``SENTRY_DSN_LIVE`` is set, so a page without browser
    Sentry never runs the class/course lookup.

    Args:
        path: ``request.path``.
        args: ``request.args``.
        session_data: Flask ``session``.
        view_args: ``request.view_args``.
        resolver: Class/course lookup.

    Returns:
        ``sentry_user_id`` and ``sentry_tags_json``.
    """
    tags = request_tags(path, args, session_data)
    ids = request_ids(view_args, session_data)
    if session_data and not session_data.get("logged_in") and session_data.get("student_course"):
        # Students carry their course in the session already: no lookup.
        tags.update(resolve_class_course(None, ids))
        tags["course_code"] = str(session_data.get("student_course"))
    else:
        tags.update(resolve_class_course(resolver, ids))
    identity = staff_identity(session_data)
    return {
        "sentry_user_id": identity["id"] if identity else "",
        "sentry_tags_json": json.dumps(tags, sort_keys=True),
    }


def install_request_scope(app: Any, resolver: Callable[..., dict[str, Any]] | None) -> None:
    """Register the Flask hooks that tag Sentry events and log key actions.

    No-op work when the SDK is not active (local runs, CI): the hooks return
    straight away.

    Args:
        app: Flask app.
        resolver: ``SchoolDB.telemetry_class_course``.
    """
    from flask import g, request, session

    def _active() -> bool:
        """True when a Sentry client is initialised in this process."""
        try:
            import sentry_sdk
        except ImportError:
            return False
        return sentry_sdk.get_client().is_active()

    @app.before_request
    def _sentry_tag_request() -> None:
        """Set the Sentry user (staff only) and the cheap tags."""
        if not _active() or is_quiet_telemetry_path(request.path):
            return None
        import sentry_sdk

        try:
            tags = request_tags(request.path, request.args, session)
            g.sentry_tags = tags
            scope = sentry_sdk.get_isolation_scope()
            identity = staff_identity(session)
            if identity:
                scope.set_user(identity)
            for key, value in tags.items():
                scope.set_tag(key, value)
            ids = request_ids(request.view_args, session)
            if "class_id" in ids:
                scope.set_tag("class_id", str(ids["class_id"]))
            g.sentry_ids = ids
            scope.add_event_processor(make_event_processor(resolver, ids))
        except Exception:  # noqa: BLE001
            logger.debug("Sentry request tagging failed open", exc_info=True)
        return None

    @app.after_request
    def _sentry_log_action(response: Any) -> Any:
        """One Sentry Logs line per successful key teacher action."""
        try:
            action = action_for(request.endpoint, request.method)
            if action is None or response.status_code >= 400 or not _active():
                return response
            if response.is_json:
                body = response.get_json(silent=True)
                if isinstance(body, dict) and body.get("ok") is False:
                    return response
            tags = dict(getattr(g, "sentry_tags", None) or {})
            tags.update(resolve_class_course(resolver, getattr(g, "sentry_ids", None) or {}))
            log_action(action, tags)
        except Exception:  # noqa: BLE001
            logger.debug("Sentry action log failed open", exc_info=True)
        return response

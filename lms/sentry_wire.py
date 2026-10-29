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
import hmac
import json
import logging
import os
import re
import threading
import time
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
        "sentry_defer": True,
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
        _drop_request_body(event)
        if not any(is_quiet_telemetry_path(path) for path in paths):
            return event
        if _exception_type_names(hint, event) & _DISCONNECT_TYPES:
            return None
    except Exception as exc:
        # Fail closed. The event can hold an address or a confirm field,
        # so a scrub error drops it. The log is the exception type only.
        logger.warning("Sentry before_send dropped the event (%s)", type(exc).__name__)
        return None
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
    options = sentry_init_options(dsn)
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


_INVITE_TOKEN_RE = re.compile(r"/invite/[^/?#]+")
_STUDENT_SEAT_RE = re.compile(r"/student/s/[^/?#]+")
# Free text (messages, exception values, breadcrumbs): a token ends at
# whitespace, quotes, brackets or a path/query separator.
_TEXT_INVITE_RE = re.compile(r"/invite/(?!\[token\])[^\s/?#\"'()<>\[\],;]+")
_TEXT_SEAT_RE = re.compile(r"/student/s/(?!\[token\])[^\s/?#\"'()<>\[\],;]+")


def mask_path_tokens(value: Any) -> Any:
    """Mask ``/student/s/<seat>`` and ``/invite/<token>`` anywhere in a string.

    MCK-183 Ops LOW-1: the old scrub only masked these paths inside a URL
    that had a ``?`` or ``#``. A bare seat or invite path in a message, an
    exception value or a breadcrumb now becomes ``.../[token]`` too.
    Non-strings are returned unchanged.
    """
    if not isinstance(value, str):
        return value
    if "/invite/" not in value and "/student/s/" not in value:
        return value
    value = _TEXT_INVITE_RE.sub("/invite/[token]", value)
    return _TEXT_SEAT_RE.sub("/student/s/[token]", value)


def _mask_nested(value: Any, depth: int = 0) -> Any:
    """Apply :func:`mask_path_tokens` through dicts and lists (bounded depth)."""
    if depth > 6:
        return value
    if isinstance(value, str):
        return mask_path_tokens(value)
    if isinstance(value, dict):
        return {k: _mask_nested(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_nested(v, depth + 1) for v in value]
    return value


def _mask_event_tokens(event: dict[str, Any]) -> None:
    """Mask seat and invite tokens in an event's text fields, in place.

    Covers ``message``, ``logentry``, exception and thread values and frame
    paths, breadcrumbs (message and data), ``transaction``, the request URL
    and headers, and ``extra``/``contexts`` strings.
    """
    for key in ("message", "transaction", "culprit"):
        if isinstance(event.get(key), str):
            event[key] = mask_path_tokens(event[key])
    for key in ("logentry", "extra", "contexts", "request", "tags"):
        if isinstance(event.get(key), (dict, list)):
            event[key] = _mask_nested(event[key])
    for group in ("exception", "threads"):
        holder = event.get(group)
        if isinstance(holder, dict) and isinstance(holder.get("values"), list):
            holder["values"] = _mask_nested(holder["values"])
    crumbs = event.get("breadcrumbs")
    if isinstance(crumbs, (dict, list)):
        event["breadcrumbs"] = _mask_nested(crumbs)


_IDENTITY_KEYS = frozenset({"email", "confirm_email", "login_hint"})
# Structured fields that hold a join code, catch-up code, PIN, or student name.
# Dropped entirely before send. ``course_code`` is not in this set.
_DROPPED_KEYS = _IDENTITY_KEYS | frozenset(
    {
        "pin",
        "course_pin",
        "student_pin",
        "catchup_pin",
        "catch_up_pin",
        "default_pin",
        "pin_hash",
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
    }
)
_EMAIL_TEXT_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# "PIN 4321" / "pin=4321". A bare 4-digit year or status is left alone.
_PIN_TEXT_RE = re.compile(r"(?i)\b(pin)\b(\s*[:=]?\s*)\d{4}\b")
# Labeled join / catch-up / session codes. "course code MCF3M" is not a label.
_CODE_TEXT_RE = re.compile(
    r"(?i)\b((?:join|catch-?up|session|portal|access)\s+code"
    r"|session_code|portal_join_code|join_code|live_access_code|catch-?up_code)"
    r"\b(\s*[:=]?\s*)[A-Za-z0-9]{4,8}\b"
)
# "name=Maple" / "first_name: Maple". Requires a separator so "name Maple" in
# prose is handled by the student-label pattern instead.
_NAME_TEXT_RE = re.compile(
    r"(?i)\b(student(?:'s)?\s+name|first_name|last_name|full_name|display_name"
    r"|codename|roster_name|name)\b(\s*[:=]\s*)([^\s,;\"'&=]{1,80})"
)
# The name must be capitalized. ``(?i)`` would turn "Roster edited" into a
# redacted name, because "edited" would match.
_STUDENT_LABEL_RE = re.compile(
    r"\b([Ss]tudent|[Rr]oster)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b"
)
# ``tap by Zorblax`` has no student/roster label. The name is capitalized;
# ``(?i)`` would also swallow ordinary words.
_BY_NAME_RE = re.compile(
    r"\b[Bb]y\s+([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?)\b"
)
# ``code Q7Z9K`` / ``code=Q7Z9K`` with no join/catch-up/session label.
# ``course code MCF3M`` is excluded in :func:`_scrub_bare_code`.
_BARE_CODE_RE = re.compile(
    r"(?i)\b(code)(\s*[:=]?\s*)([A-Za-z0-9]{4,8})\b"
)
# A 4-digit token with no ``PIN`` label. Years are put back by the replacer.
_BARE_PIN_RE = re.compile(r"(?<!\d)(\d{4})(?!\d)")
_PERSON_NAME_RE = re.compile(r"^[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?$")
# Words that follow "student" and are the product, not a person.
_NOT_A_STUDENT_NAME = frozenset(
    {
        "portal",
        "state",
        "home",
        "join",
        "code",
        "pin",
        "live",
        "class",
        "session",
        "error",
        "failed",
        "page",
        "view",
        "tab",
        "board",
        "phone",
        "card",
    }
)
# OTel severity number for warn. WARNING and above are at least this.
_LOG_WARNING_NUMBER = 13
_LOG_WARNING_TEXT = frozenset({"warn", "warning", "error", "fatal", "critical"})
# One sampled busy-shed line per action per minute, not one per student tap.
_SHED_INTERVAL_S = 60.0
_shed_lock = threading.Lock()
_shed_counts: dict[str, int] = {}
_shed_logged_at: dict[str, float] = {}
# Healthy month vs one noisy 75-minute period at the step-rejection cap.
# Confirm the org's log quota before production; this is the expected volume.
EXPECTED_MONTHLY_LOG_LINES = (
    "about 1,000 lines in a healthy month; under 15,000 in a noisy week"
)


# Product words that share the shape of a capitalized name.
_NOT_A_PERSON = frozenset(
    {
        "error",
        "warning",
        "failed",
        "false",
        "true",
        "none",
        "null",
        "graph",
        "publish",
        "roster",
        "student",
        "portal",
        "class",
        "dashboard",
        "attendance",
    }
)
# Words that follow "by" and are not a student. Weekdays and months stay
# so "due by Monday" is still readable. Keep this set in sync with
# NOT_A_BY_NAME in static/sentry-live.js.
_NOT_A_BY_NAME = _NOT_A_PERSON | _NOT_A_STUDENT_NAME | frozenset(
    {
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "staff",
        "teacher",
        "admin",
        "user",
        "system",
        "server",
        "module",
        "lesson",
        "question",
        "answer",
        "team",
        "group",
        "item",
        "step",
        "today",
        "tomorrow",
        "yesterday",
        "someone",
        "anyone",
    }
)


def _looks_like_person_name(value: str) -> bool:
    """True for one or two capitalized words, such as a roster codename.

    Args:
        value: One logging argument or field.

    Returns:
        Whether the value should be treated as a student name.
    """
    text = str(value or "").strip()
    if text.lower() in _NOT_A_PERSON:
        return False
    return bool(_PERSON_NAME_RE.fullmatch(text))


def _redact_secret_argument(text: str, argument: str) -> str:
    """Replace one secret argument wherever it stands as its own word.

    Args:
        text: Message that may contain the argument.
        argument: The raw argument.

    Returns:
        ``text`` with that argument replaced by ``[redacted]``.
    """
    if not argument or argument not in text:
        return text
    return re.sub(rf"\b{re.escape(argument)}\b", "[redacted]", text)


def _looks_like_join_code(value: str) -> bool:
    """True when a string is shaped like a live join or catch-up code.

    Lengths are 4, 5, 6, or 8, alphanumeric, with a letter. Underscored
    error slugs (``pin_not_set``) and all-digit PINs are not codes. Course
    codes such as ``MCF3M`` share this shape; they belong on ``course_code``,
    not on a ``code`` field.

    Args:
        value: Field value.

    Returns:
        Whether the value should be treated as a join or catch-up secret.
    """
    text = str(value or "").strip().upper().replace(" ", "")
    if len(text) not in (4, 5, 6, 8):
        return False
    if not text.isalnum() or text.isdigit():
        return False
    return any(ch.isalpha() for ch in text)


def _scrub_student_label(match: re.Match[str]) -> str:
    """Replace ``student Zorblax`` and leave ``student portal`` in place."""
    name = match.group(2)
    if name.lower() in _NOT_A_STUDENT_NAME:
        return match.group(0)
    return f"{match.group(1)} [redacted]"


def _is_unlabelled_pin(value: str) -> bool:
    """True for a 4-digit token that is not a calendar year.

    Args:
        value: One logging argument or a text token.

    Returns:
        Whether the value should be treated as a PIN.
    """
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4}", text):
        return False
    number = int(text)
    return not 1900 <= number <= 2099


def _scrub_by_name(match: re.Match[str]) -> str:
    """Redact a capitalized name after ``by``, such as ``tap by Zorblax``.

    Weekdays, months, and product words stay so ``due by Monday`` and
    ``by Staff`` are unchanged.

    Args:
        match: The ``by <Name>`` match. Group 1 is the name.

    Returns:
        ``by [redacted]`` or the original text.
    """
    words = match.group(1).split()
    if words and all(word.lower() in _NOT_A_BY_NAME for word in words):
        return match.group(0)
    return "by [redacted]"


def _scrub_bare_code(match: re.Match[str]) -> str:
    """Redact a join-shaped token after the word ``code``.

    ``course code MCF3M`` is a course code, not a join secret. A token
    that is not join-shaped (``pin_not_set``, an all-digit PIN) is left
    for the PIN pass or kept as-is.

    Args:
        match: The bare ``code <token>`` match.

    Returns:
        The label plus ``[redacted]``, or the original text.
    """
    token = match.group(3)
    if not _looks_like_join_code(token):
        return match.group(0)
    prefix = match.string[: match.start()]
    if re.search(r"(?i)course[_ \t]+$", prefix):
        return match.group(0)
    return f"{match.group(1)}{match.group(2)}[redacted]"


def _scrub_bare_pin(match: re.Match[str]) -> str:
    """Redact a 4-digit token that is not a calendar year.

    Labelled ``PIN 9876`` is already handled. This catches the same shape
    with no label, such as ``typed 9876``. Years 1900–2099 stay so a date
    in a message is still readable.

    Args:
        match: A 4-digit token.

    Returns:
        ``[redacted]`` or the original digits when they are a year.
    """
    digits = match.group(1)
    if _is_unlabelled_pin(digits):
        return "[redacted]"
    return digits


def _scrub_text(value: str) -> str:
    """Redact emails, PINs, join codes, and student names in text.

    Labelled forms (``PIN 9876``, ``join code Q7Z9K``, ``name=Zorblax``)
    and unlabelled forms (``tap by Zorblax``, ``code Q7Z9K``, a bare
    4-digit PIN) are both removed. Course codes and calendar years stay.

    Args:
        value: One string from an event, a log, or a breadcrumb.

    Returns:
        The same text with those secrets replaced by ``[redacted]``.
    """
    value = _EMAIL_TEXT_RE.sub("[redacted]", value)
    value = mask_path_tokens(value)
    value = _PIN_TEXT_RE.sub(r"\1\2[redacted]", value)
    value = _CODE_TEXT_RE.sub(r"\1\2[redacted]", value)
    value = _NAME_TEXT_RE.sub(r"\1\2[redacted]", value)
    value = _STUDENT_LABEL_RE.sub(_scrub_student_label, value)
    value = _BY_NAME_RE.sub(_scrub_by_name, value)
    value = _BARE_CODE_RE.sub(_scrub_bare_code, value)
    return _BARE_PIN_RE.sub(_scrub_bare_pin, value)


def _drop_secret_field(key: str, child: Any) -> bool:
    """True when this field must not be sent to Sentry.

    Args:
        key: Dict key.
        child: Field value.

    Returns:
        Whether to omit the field.
    """
    lowered = str(key).lower()
    if lowered in _DROPPED_KEYS or lowered.endswith("_pin"):
        return True
    if lowered == "code" and isinstance(child, str) and _looks_like_join_code(child):
        return True
    return False


def _scrub_identity(value: Any) -> Any:
    """Drop address, PIN, code, and name fields and redact them in text.

    Walks every nested dict and list with an explicit stack, so a deep
    payload is still scrubbed. A cycle raises and the caller drops the
    event instead of sending an unscrubbed node.

    Args:
        value: Event node.

    Returns:
        The same shape, with those fields removed.

    Raises:
        ValueError: The payload contains a cycle.
    """
    if isinstance(value, str):
        return _scrub_text(value)
    if not isinstance(value, (dict, list)):
        return value

    def fresh(node: Any) -> dict[str, Any] | list[Any]:
        """Return an empty container of the same kind as ``node``."""
        return [] if isinstance(node, list) else {}

    def children(node: Any):
        """Yield kept children. Address and confirm keys are skipped."""
        if isinstance(node, dict):
            for key, child in node.items():
                if _drop_secret_field(str(key), child):
                    continue
                yield str(key), child
            return
        for index, child in enumerate(node):
            yield index, child

    def put(clone: Any, key: Any, child: Any) -> None:
        """Store one scrubbed child on its new parent."""
        if isinstance(clone, list):
            clone.append(child)
            return
        clone[key] = child

    clones: dict[int, Any] = {id(value): fresh(value)}
    stack: list[tuple[Any, Any]] = [(value, iter(children(value)))]
    while stack:
        node, pending = stack[-1]
        try:
            key, child = next(pending)
        except StopIteration:
            stack.pop()
            continue
        if isinstance(child, str):
            put(clones[id(node)], key, _scrub_text(child))
            continue
        if isinstance(child, (dict, list)):
            child_id = id(child)
            if child_id in clones:
                raise ValueError("cyclic sentry payload")
            clones[child_id] = fresh(child)
            put(clones[id(node)], key, clones[child_id])
            stack.append((child, iter(children(child))))
            continue
        put(clones[id(node)], key, child)
    return clones[id(value)]


def _drop_request_body(event: dict[str, Any]) -> dict[str, Any]:
    """Remove request bodies, query strings, addresses, and invite tokens.

    Roster names live in bodies; ``/invite/<token>`` paths carry a sign-in
    secret; ``?v=`` carries a student visit token and ``login_hint`` an
    invited email. ``confirm_email`` is the permanent-delete confirmation.
    """
    cleaned = _scrub_identity(event)
    if isinstance(cleaned, dict):
        event.clear()
        event.update(cleaned)
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("data", None)
        request.pop("cookies", None)
        url = request.get("url")
        if isinstance(url, str):
            request["url"] = _strip_url_query(url)
        # No query strings at all: ``?v=`` is a student visit token and
        # ``login_hint`` an invited email (MCK-183 gate F2). Tags carry the tab.
        if request.get("query_string"):
            request.pop("query_string", None)
        request.pop("env", None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = _allowed_headers(headers)
    transaction = event.get("transaction")
    if isinstance(transaction, str):
        event["transaction"] = _INVITE_TOKEN_RE.sub("/invite/[token]", transaction)
    _mask_event_tokens(event)
    _drop_frame_locals(event)
    return event


def _drop_frame_locals(event: dict[str, Any]) -> None:
    """Remove exception and thread local-variable dicts.

    ``include_local_variables`` is off. A frame that still carries ``vars``
    can hold a PIN or a name under an arbitrary key, so the dict is removed
    rather than scrubbed key by key.

    Args:
        event: Sentry event payload, mutated in place.
    """
    for group in ("exception", "threads"):
        holder = event.get(group)
        if not isinstance(holder, dict):
            continue
        values = holder.get("values")
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            stack = value.get("stacktrace")
            if not isinstance(stack, dict):
                continue
            frames = stack.get("frames")
            if not isinstance(frames, list):
                continue
            for frame in frames:
                if isinstance(frame, dict):
                    frame.pop("vars", None)


# Request headers Sentry may keep (MCK-183 gate, #249 ccff8ef MED). Everything
# else is dropped: ``X-Student-Visit-Token`` is the student visit token itself,
# cookies and auth headers are secrets, and forwarding headers carry IPs.
# ``Referer`` stays with its query cut (``?v=`` on student pages).
_HEADER_ALLOWLIST = frozenset(
    {
        "accept",
        "accept-language",
        "content-length",
        "content-type",
        "host",
        "referer",
        "user-agent",
    }
)


def _allowed_headers(headers: dict[str, Any]) -> dict[str, Any]:
    """Allowlisted request headers, with any URL query cut."""
    kept: dict[str, Any] = {}
    for name, value in headers.items():
        if str(name).lower() not in _HEADER_ALLOWLIST:
            continue
        kept[name] = _strip_url_query(value) if isinstance(value, str) else ""
    return kept


_QUERY_DATA_KEYS = ("http.query", "url.query", "http.fragment")
_URL_DATA_KEYS = ("url", "http.url", "url.full", "http.target", "url.path")


def _strip_url_query(value: Any) -> Any:
    """Cut ``?query`` and ``#fragment`` from a URL-ish string."""
    if not isinstance(value, str):
        return value
    cut = min((i for i in (value.find("?"), value.find("#")) if i >= 0), default=-1)
    text = value[:cut] if cut >= 0 else value
    text = _INVITE_TOKEN_RE.sub("/invite/[token]", text)
    return _STUDENT_SEAT_RE.sub("/student/s/[token]", text)


_URLISH_QUERY_RE = re.compile(r"(?:^|\s)(?:/|https?://)\S*[?#]")


def _strip_urlish(value: Any) -> Any:
    """Cut the query from a string that is (or ends in) a URL or path.

    SQL span descriptions also use ``?`` placeholders; those are not URLs
    and are left alone.
    """
    if isinstance(value, str) and _URLISH_QUERY_RE.search(value):
        return _strip_url_query(value)
    return value


def _strip_span_queries(span: dict[str, Any]) -> None:
    """Remove queries (student ``?v=`` visit tokens) from one span in place."""
    if not isinstance(span, dict):
        return
    for key in ("description", "name"):
        if key in span:
            span[key] = _strip_urlish(span[key])
    data = span.get("data")
    if isinstance(data, dict):
        for key in _QUERY_DATA_KEYS:
            data.pop(key, None)
        for key in _URL_DATA_KEYS:
            if key in data:
                data[key] = _strip_url_query(data[key])
        for key, value in list(data.items()):
            if ".header." in str(key).lower() and "visit-token" in str(key).lower():
                data.pop(key, None)
            elif isinstance(value, str):
                data[key] = _strip_urlish(value)
            elif isinstance(value, list):
                data[key] = [_strip_urlish(v) for v in value]


def before_send_transaction(
    event: dict[str, Any], _hint: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Sampled transactions carry no request body and no query string.

    The student visit token rides in ``?v=``, so the transaction name, the
    request URL/query and every span URL/description lose their query here
    (MCK-183, gate F2).
    """
    try:
        _drop_request_body(event)
        request = event.get("request")
        if isinstance(request, dict):
            request["url"] = _strip_url_query(request.get("url"))
            request.pop("query_string", None)
        if "transaction" in event:
            event["transaction"] = _strip_url_query(event["transaction"])
        trace = ((event.get("contexts") or {}).get("trace")) or {}
        _strip_span_queries(trace)
        for span in event.get("spans") or []:
            _strip_span_queries(span)
        return event
    except Exception:  # noqa: BLE001
        # Same as before_send: a scrub error must not ship the transaction.
        return None


def sentry_init_options(dsn: str) -> dict[str, Any]:
    """The ``sentry_sdk.init`` options production uses (tests reuse them).

    Args:
        dsn: Server DSN.

    Returns:
        Keyword arguments for ``sentry_sdk.init``.
    """
    # WARNING and above become Sentry Logs. INFO stays out, including the
    # per-poll ``live /state`` line, so a class does not pay a log per beat.
    # Issues come from ``capture_exception`` (swallowed faults), not from
    # every ``logger.exception`` that already logs and continues.
    # An explicit LoggingIntegration replaces only the default logging one;
    # Flask and the other defaults still auto-enable.
    import logging as py_logging
    from sentry_sdk.integrations.logging import LoggingIntegration

    options: dict[str, Any] = {
        "dsn": dsn,
        "environment": environment_name(),
        "send_default_pii": False,
        "include_local_variables": False,
        "max_request_body_size": "never",
        "traces_sampler": traces_sampler,
        "before_send": before_send,
        "enable_logs": True,
        "before_send_log": before_send_log,
        "before_send_transaction": before_send_transaction,
        "integrations": [
            LoggingIntegration(
                level=py_logging.WARNING,
                event_level=None,
                sentry_logs_level=py_logging.WARNING,
            )
        ],
        "before_breadcrumb": before_breadcrumb,
    }
    release = release_name()
    if release:
        options["release"] = release
    return options


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
    """Short HMAC-sha256 of a lowercased email. The raw address is never sent.

    Args:
        email: Teacher email.

    Returns:
        The first 12 hex characters, or an empty string for no email.
    """
    cleaned = str(email or "").strip().lower()
    if not cleaned:
        return ""
    # Keyed with the server's Flask secret (same source as app.secret_key), so
    # someone with Sentry access can't confirm a guessed address.
    key = os.getenv("FLASK_SECRET_KEY", "lloves-dev-secret-change-me").encode("utf-8")
    return hmac.new(key, cleaned.encode("utf-8"), hashlib.sha256).hexdigest()[:_EMAIL_HASH_LEN]


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


def before_breadcrumb(
    crumb: dict[str, Any], hint: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Scrub a breadcrumb. Drop it when scrubbing fails.

    Logging arguments that are emails, PINs, join codes, or names are
    removed from the message. The breadcrumb level is WARNING and above,
    so an INFO poll line never becomes one.

    Args:
        crumb: Sentry breadcrumb.
        hint: SDK hint, sometimes including the log record.

    Returns:
        The scrubbed breadcrumb, or ``None`` to drop it.
    """
    try:
        if not isinstance(crumb, dict):
            return crumb
        message = crumb.get("message")
        record = (hint or {}).get("log_record") if isinstance(hint, dict) else None
        args = getattr(record, "args", None) if record is not None else None
        if isinstance(message, str):
            if isinstance(args, tuple):
                for arg in args:
                    if not isinstance(arg, str):
                        continue
                    mentions_code = bool(
                        re.search(r"\bcode\b|join|catch-?up", message, re.I)
                    )
                    pin_shaped = bool(re.fullmatch(r"\d{4}", arg.strip()))
                    mentions_pin = bool(re.search(r"\bpin\b", message, re.I))
                    secret = (
                        _looks_like_person_name(arg)
                        or bool(_EMAIL_TEXT_RE.search(arg))
                        or (mentions_code and _looks_like_join_code(arg))
                        or _is_unlabelled_pin(arg)
                        or (pin_shaped and mentions_pin)
                    )
                    if secret:
                        message = _redact_secret_argument(message, arg)
            crumb["message"] = _scrub_text(message)
        data = crumb.get("data")
        if isinstance(data, dict):
            cleaned = _scrub_identity(data)
            if isinstance(cleaned, dict):
                crumb["data"] = cleaned
        return crumb
    except Exception:  # noqa: BLE001
        return None


def _log_is_warning_or_above(log: dict[str, Any]) -> bool:
    """True when a Sentry log is WARNING, ERROR, or FATAL.

    Args:
        log: Sentry log payload.

    Returns:
        Whether the severity is warning or higher.
    """
    text = str(log.get("severity_text") or log.get("level") or "").strip().lower()
    if text in _LOG_WARNING_TEXT:
        return True
    number = log.get("severity_number")
    return isinstance(number, (int, float)) and number >= _LOG_WARNING_NUMBER


def _scrub_log_parameters(log: dict[str, Any]) -> None:
    """Redact stdlib logging arguments when the line is about a secret.

    Sentry copies ``logger.warning("pin %s", pin)`` into
    ``sentry.message.parameter.*`` as well as the formatted body. The body
    is already scrubbed. Arguments that are emails, person names, join
    codes on a line that mentions a code, or 4-digit PINs (including an
    unlabelled PIN that is not a calendar year) are replaced here.

    Args:
        log: Sentry log payload, mutated in place.
    """
    attributes = log.get("attributes")
    if not isinstance(attributes, dict):
        return
    template = str(attributes.get("sentry.message.template") or "")
    body = str(log.get("body") or "")
    haystack = f"{template}\n{body}".lower()
    mentions_pin = bool(re.search(r"\bpin\b", haystack))
    mentions_code = bool(
        re.search(r"\bcode\b|join|catch-?up|session_code|portal_join", haystack)
    )
    mentions_name = bool(re.search(r"\bname\b|\bstudent\b|\broster\b", haystack))
    for key, value in list(attributes.items()):
        if not str(key).startswith("sentry.message.parameter."):
            continue
        if not isinstance(value, str):
            continue
        secret = bool(_EMAIL_TEXT_RE.search(value)) or _looks_like_person_name(value)
        pin_shaped = bool(re.fullmatch(r"\d{4}", value.strip()))
        if _is_unlabelled_pin(value) or (mentions_pin and pin_shaped):
            secret = True
        if mentions_code and _looks_like_join_code(value):
            secret = True
        if mentions_name:
            secret = True
        if secret:
            attributes[key] = "[redacted]"
            body = _redact_secret_argument(body, value)
    if body != log.get("body"):
        log["body"] = body


def _apply_scrub(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Scrub one event or log in place. ``None`` when scrubbing fails closed.

    Args:
        payload: Mutable Sentry payload.

    Returns:
        The same dict, or ``None`` when the payload must not be sent.
    """
    cleaned = _scrub_identity(payload)
    if not isinstance(cleaned, dict):
        return None
    payload.clear()
    payload.update(cleaned)
    return payload


def before_send_log(log: dict[str, Any], _hint: Any = None) -> dict[str, Any] | None:
    """Send WARNING and above, plus MCK-183 action lines, after scrubbing.

    INFO python logs are dropped. A log marked ``lloves_sentry_skip`` is
    the per-request busy traceback; the sampled shed counter is the line
    that ships. Secrets are removed first. A scrub error drops the log.

    Args:
        log: Sentry log payload.
        _hint: SDK hint.

    Returns:
        The scrubbed log, or ``None`` when it must not be sent.
    """
    try:
        attributes = log.get("attributes") or {}
        if isinstance(attributes, dict) and attributes.get("lloves_sentry_skip"):
            return None
        scrubbed = _apply_scrub(log)
        if scrubbed is None:
            return None
        _scrub_log_parameters(scrubbed)
        attributes = scrubbed.get("attributes") or {}
        if isinstance(attributes, dict) and ACTION_LOG_ATTRIBUTE in attributes:
            return scrubbed
        if _log_is_warning_or_above(scrubbed):
            return scrubbed
    except Exception:  # noqa: BLE001
        return None
    return None


def report_swallowed(where: str) -> None:
    """Report an exception a handler would otherwise hide.

    Call from the ``except`` block. Writes one error log (Sentry Logs keep
    WARNING and above) and, when the SDK is active, one issue. ``where`` is
    a short static label, never request data. Blocks that already log and
    continue should not call this. Never raises, including when Sentry is off.

    Args:
        where: Static handler name, such as ``json api``.
    """
    label = str(where or "handler")[:80]
    logger.exception("swallowed error in %s", label)
    try:
        import sentry_sdk

        if sentry_sdk.get_client().is_active():
            sentry_sdk.capture_exception(tags={"lloves.swallowed": label})
    except Exception:  # noqa: BLE001
        return


def note_busy_shed(action: str) -> None:
    """Count a busy/503 shed and emit at most one WARNING a minute.

    The caller's per-request ``logger.exception`` should set
    ``extra={"lloves_sentry_skip": True}`` so Sentry does not store one
    traceback per student. This line carries the action and the count only.

    Args:
        action: Short label such as ``mint`` or ``preview``.
    """
    label = str(action or "busy")[:40]
    now = time.monotonic()
    emit = False
    count = 0
    with _shed_lock:
        _shed_counts[label] = _shed_counts.get(label, 0) + 1
        last = _shed_logged_at.get(label, 0.0)
        if now - last >= _SHED_INTERVAL_S:
            _shed_logged_at[label] = now
            count = _shed_counts[label]
            _shed_counts[label] = 0
            emit = True
    if emit:
        logger.warning("live busy shed action=%s count=%s", label, count)


def reset_busy_shed_counts() -> None:
    """Clear the in-process busy-shed counter. Used by tests."""
    with _shed_lock:
        _shed_counts.clear()
        _shed_logged_at.clear()


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


#: Student step failures worth one Sentry Logs line (MCK-183 follow-up).
STEP_REJECT_STATUSES = frozenset({400, 403, 404, 409, 422, 429})
STEP_REJECT_ACTION = "student.step_rejected"
#: Chatty student writes whose 4xx are normal (board sync, presence).
_STEP_QUIET_RE = re.compile(r"/(?:ops|canvas-presence)/?$")
_EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+")


class StepLogThrottle:
    """Keep student step-failure logs cheap.

    One line per (endpoint, status, student) per ``window`` seconds and at
    most ``per_minute`` lines a minute per process, so a class hammering
    the same 409 costs a handful of lines, not thousands.
    """

    def __init__(self, window: float = 10.0, per_minute: int = 120) -> None:
        """Set the limits."""
        self.window = float(window)
        self.per_minute = int(per_minute)
        self._seen: dict[tuple[str, ...], float] = {}
        self._minute = 0
        self._count = 0

    def allow(self, key: tuple[str, ...], now: float) -> bool:
        """True when this line should be written."""
        minute = int(now // 60)
        if minute != self._minute:
            self._minute, self._count = minute, 0
            if len(self._seen) > 2000:
                self._seen = {k: t for k, t in self._seen.items() if now - t < self.window}
        if self._count >= self.per_minute:
            return False
        last = self._seen.get(key)
        if last is not None and now - last < self.window:
            return False
        self._seen[key] = now
        self._count += 1
        return True


def _safe_error_text(body: Any) -> str:
    """The API's short error text, digits and emails masked, max 80 chars."""
    if not isinstance(body, dict):
        return ""
    raw = body.get("code") or body.get("error") or ""
    if not isinstance(raw, str):
        return ""
    text = _EMAIL_RE.sub("[email]", raw)
    text = re.sub(r"\d+", "#", text)
    return " ".join(text.split())[:80]


def step_rejection(
    path: str,
    method: str,
    endpoint: str | None,
    status: int,
    body: Any,
) -> dict[str, Any] | None:
    """Attributes for a failed student step request, or ``None``.

    Covers student writes under ``/api/student/`` (rank turn, vote, team
    answer, group pick, response, ...) that came back 400/403/404/409/422/429.
    Board ops and canvas presence are left out (their conflicts are normal).

    Args:
        path: ``request.path``.
        method: HTTP method.
        endpoint: Flask endpoint name (no ids).
        status: Response status.
        body: Parsed JSON body or ``None``.

    Returns:
        Log attributes (no student data) or ``None``.
    """
    if method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    if not str(path or "").startswith("/api/student/") or int(status) not in STEP_REJECT_STATUSES:
        return None
    if _STEP_QUIET_RE.search(path):
        return None
    attributes: dict[str, Any] = {
        ACTION_LOG_ATTRIBUTE: STEP_REJECT_ACTION,
        "endpoint": endpoint or "unknown",
        "http.status_code": int(status),
        "portal": "student",
    }
    error = _safe_error_text(body)
    if error:
        attributes["error"] = error
    return attributes


_STEP_THROTTLE = StepLogThrottle()


def log_step_rejection(attributes: dict[str, Any], tags: dict[str, str]) -> None:
    """Write one warning Logs line for a failed student step. Never raises."""
    try:
        import sentry_sdk
        from sentry_sdk import logger as sentry_logger
    except ImportError:
        return
    if not sentry_sdk.get_client().is_active():
        return
    merged = dict(attributes)
    for key in ("course_code", "class_id", "live_session_id", "live_item_id"):
        if tags.get(key):
            merged[key] = tags[key]
    try:
        sentry_logger.warning(
            "student step rejected {endpoint} {status}",
            endpoint=merged.get("endpoint"),
            status=merged.get("http.status_code"),
            attributes=merged,
        )
    except Exception:  # noqa: BLE001
        logger.debug("Sentry step log failed", exc_info=True)


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
        # Student and join pages run classic scripts (student-visit-token.js,
        # student-live-session.js, inline blocks) that must find Sentry ready,
        # so they load it without ``defer``. Staff pages keep ``defer``.
        "sentry_defer": portal_for_request(path, session_data) not in ("student", "public"),
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
    def _sentry_log_step_rejection(response: Any) -> Any:
        """One throttled Logs line when a student step request is refused."""
        try:
            status = int(response.status_code)
            if status < 400 or status >= 500 or not _active():
                return response
            body = response.get_json(silent=True) if response.is_json else None
            attributes = step_rejection(
                request.path, request.method, request.endpoint, status, body
            )
            if attributes is None:
                return response
            import time

            who = str(session.get("student_id") or session.get("student_visit_token") or "")[:16]
            key = (str(request.endpoint), str(status), who)
            if not _STEP_THROTTLE.allow(key, time.monotonic()):
                return response
            tags = dict(getattr(g, "sentry_tags", None) or {})
            tags.update(resolve_class_course(resolver, getattr(g, "sentry_ids", None) or {}))
            for key_name in ("session_id", "live_item_id"):
                value = (request.view_args or {}).get(key_name)
                if isinstance(value, int):
                    tags["live_session_id" if key_name == "session_id" else key_name] = str(value)
            log_step_rejection(attributes, tags)
        except Exception:  # noqa: BLE001
            logger.debug("Sentry step log failed open", exc_info=True)
        return response

    @app.after_request
    def _sentry_log_action(response: Any) -> Any:
        """One Sentry Logs line per successful key teacher action."""
        try:
            action = action_for(request.endpoint, request.method)
            if action is None or response.status_code >= 400 or not _active():
                return response
            if action == "Dashboard opened" and (
                not 200 <= response.status_code < 300 or staff_identity(session) is None
            ):
                # A signed-out 302 to sign-in, or a redirect away, is not an open.
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

"""Optional Sentry reporting for the LLOVES LMS.

DSNs come from the environment only. An empty ``SENTRY_DSN`` leaves the
process uninstrumented so local runs, CI, and a Fly machine that does not
have the secret yet all boot the same way.

The browser key is a different variable, ``SENTRY_DSN_LIVE``. Templates
read it through :func:`sentry_browser_context`. It is never written into
static JavaScript.
"""

from __future__ import annotations

import logging
import os
from typing import Any
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

"""Loopback-only staff sign-in for the local :8788 test server.

The route is a separate door from Google sign-in. It never reads a posted
``credential`` and never calls the ID token verifier. It stays off unless
``LLOVES_TEST_LOGIN`` is set, the process is not production and not Fly,
and ``HOST`` is explicitly ``localhost`` or ``127.0.0.1``. gunicorn
``--bind`` and ``flask run --host`` do not read ``HOST``, so an unset
variable is not treated as a loopback listen address. The per-request
loopback check is the guard for a process that still listens publicly.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import sys
import urllib.parse
from typing import Any

from flask import Flask, request, session

logger = logging.getLogger("lloves.local_test_login")

#: Env flag the Mac test server sets. Unset means the route does not exist.
TEST_LOGIN_ENV = "LLOVES_TEST_LOGIN"
#: Fixed staff account. Not a Google identity and not a password.
TEST_STAFF_EMAIL = "lloves-test-staff@localhost"
TEST_STAFF_NAME = "Test Staff"
_ON_VALUES = frozenset({"1", "true", "yes", "on"})
_LOCAL_BIND_HOSTS = frozenset({"localhost", "127.0.0.1"})
_WARNING = (
    "WARNING: LLOVES_TEST_LOGIN is on. Loopback test staff sign-in is active "
    f"for {TEST_STAFF_EMAIL}. Google ID token verification is unchanged. "
    "Do not set this flag when FLY_APP_NAME is set or FLASK_ENV=production."
)


class TestStaffRejected(Exception):
    """The fixed account exists but must not receive a session."""


def local_test_login_requested() -> bool:
    """True when ``LLOVES_TEST_LOGIN`` is an explicit on value.

    Empty, ``0``, and any other string are off. The flag is not a secret.
    """
    return (os.getenv(TEST_LOGIN_ENV) or "").strip().lower() in _ON_VALUES


def _production_env() -> bool:
    """True when this process is marked production."""
    return (os.getenv("FLASK_ENV") or "").strip().lower() == "production"


def _fly_app_name() -> str:
    """Return the Fly app name when this process is on Fly."""
    return (os.getenv("FLY_APP_NAME") or "").strip()


def explicit_loopback_host() -> str | None:
    """Return ``HOST`` when it is exactly ``localhost`` or ``127.0.0.1``.

    An empty ``HOST`` is not loopback. ``local_dev_bind_host`` fills that
    gap for the normal dev server, and that default would let
    ``gunicorn --bind 0.0.0.0`` and ``flask run --host 0.0.0.0`` boot with
    the test flag on.

    Returns:
        The lowercase host, or None when ``HOST`` is missing or public.
    """
    host = (os.getenv("HOST") or "").strip().lower()
    if host in _LOCAL_BIND_HOSTS:
        return host
    return None


def _cli_option(argv: list[str], name: str) -> str | None:
    """Return a ``--name value`` or ``--name=value`` argument.

    Args:
        argv: Process arguments, including the program name.
        name: Long option name without leading dashes.

    Returns:
        The option value, or None when the option is absent.
    """
    flag = f"--{name}"
    prefix = f"{flag}="
    for index, arg in enumerate(argv):
        if arg == flag and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith(prefix):
            return arg[len(prefix):]
    return None


def cli_bind_host(argv: list[str]) -> str:
    """Return the host ``python lms/app.py`` should pass to its listen call.

    With the test flag off, ``--host`` wins and otherwise the local-dev
    default is used. With the flag on, ``HOST`` must be explicitly
    ``localhost`` or ``127.0.0.1`` and ``--host`` must be that same value.
    The run script passes ``--host "$HOST"`` so the listen address cannot
    drift from the variable the boot check reads.

    Args:
        argv: Process arguments, including the program name.

    Returns:
        The host string to bind.

    Raises:
        RuntimeError: The test flag is on and ``--host`` is missing or is
            not the explicit ``HOST`` value.
    """
    chosen = (_cli_option(argv, "host") or "").strip()
    if not local_test_login_requested():
        if chosen:
            return chosen
        from local_dev_seed import local_dev_bind_host

        return local_dev_bind_host()
    required = explicit_loopback_host()
    if required is None:
        raise RuntimeError(
            "LLOVES_TEST_LOGIN requires HOST to be explicitly localhost "
            "or 127.0.0.1. Refusing to bind a public address."
        )
    if chosen.lower() != required:
        raise RuntimeError(
            "LLOVES_TEST_LOGIN requires --host to be the same value as HOST "
            f"({required}); refusing to bind {chosen or 'an unset --host'}."
        )
    return required


def cli_bind_port(argv: list[str]) -> int:
    """Return the port ``python lms/app.py`` should pass to its listen call.

    ``--port`` wins, then ``PORT``, then 8787.

    Args:
        argv: Process arguments, including the program name.

    Returns:
        TCP port number.
    """
    raw = (_cli_option(argv, "port") or os.getenv("PORT") or "8787").strip()
    return int(raw)


def local_test_login_boot_block() -> str | None:
    """Return why startup must refuse the flag, or None when boot may continue.

    Refusal wins over ignoring the flag: a production process, Fly, or a
    ``HOST`` that is not explicitly loopback must not stay up. This does not
    see a gunicorn ``--bind`` or ``flask run --host``. The per-request
    loopback check is the guard for a process that still listens publicly.

    Returns:
        A short reason, or None when the flag is off or ``HOST`` is an
        explicit loopback name.
    """
    if not local_test_login_requested():
        return None
    if _production_env():
        return (
            "LLOVES_TEST_LOGIN is set with FLASK_ENV=production; refusing to "
            "start with the test staff login enabled."
        )
    if _fly_app_name():
        return (
            "LLOVES_TEST_LOGIN is set with FLY_APP_NAME; refusing to start "
            "with the test staff login enabled."
        )
    if explicit_loopback_host() is None:
        return (
            "LLOVES_TEST_LOGIN is set but HOST is not explicitly localhost "
            "or 127.0.0.1; refusing to start."
        )
    return None


def enforce_local_test_login(app: Flask) -> None:
    """Refuse a production boot with the flag, or warn when the test login is on.

    Args:
        app: Flask application being created.

    Raises:
        RuntimeError: The flag is set on Fly, in production, or without
            ``HOST`` set explicitly to localhost or 127.0.0.1.
    """
    reason = local_test_login_boot_block()
    if reason:
        raise RuntimeError(reason)
    if not local_test_login_requested():
        return
    logger.warning(_WARNING)
    app.logger.warning(_WARNING)
    if not app.config.get("TESTING"):
        print(_WARNING, file=sys.stderr)


def _request_hostname() -> str:
    """Return the request hostname without a port.

    ``X-Forwarded-Host`` is not read. The value is the Host header Flask
    already stored on the request.
    """
    raw = (request.host or "").strip().lower()
    if raw.startswith("["):
        end = raw.find("]")
        return raw[1:end] if end > 1 else ""
    return raw.split(":", 1)[0]


def _peer_is_loopback() -> bool:
    """True when the TCP peer, before any proxy rewrite, is a loopback address.

    A client-supplied ``X-Forwarded-For`` is never consulted.
    """
    try:
        environ = request.environ
    except RuntimeError:
        return False
    orig = environ.get("werkzeug.proxy_fix.orig") or {}
    peer = str(orig.get("REMOTE_ADDR") or environ.get("REMOTE_ADDR") or "").strip()
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return bool(addr.is_loopback)


def _split_host_port(raw: str, scheme: str) -> tuple[str, int] | None:
    """Split a host[:port] value into a hostname and port.

    Args:
        raw: Host header or the host portion of an absolute URL.
        scheme: ``http`` or ``https``, used when the port is omitted.

    Returns:
        Lowercase hostname and port, or None when the value is unusable.
    """
    text = (raw or "").strip().lower()
    if not text or " " in text or "/" in text:
        return None
    if text.startswith("["):
        end = text.find("]")
        if end < 2:
            return None
        host = text[1:end]
        rest = text[end + 1:]
        port_text = rest[1:] if rest.startswith(":") else ""
    elif text.count(":") == 1:
        host, port_text = text.split(":", 1)
    else:
        host, port_text = text, ""
    if not host:
        return None
    if port_text:
        if not port_text.isdigit():
            return None
        return host, int(port_text)
    default = 443 if scheme == "https" else 80
    return host, default


def _request_host_port() -> tuple[str, int] | None:
    """Return this request's hostname and port from the Host header."""
    return _split_host_port(request.host or "", request.scheme or "http")


def _header_matches_request_host(value: str | None) -> bool:
    """True when an Origin or Referer is absent or names this request host.

    Args:
        value: Header value. Blank counts as absent so curl can omit both.

    Returns:
        True when the header is blank or its host and port match this
        loopback request. A public or different-port value is false.
    """
    text = (value or "").strip()
    if not text:
        return True
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    origin = _split_host_port(parsed.netloc, parsed.scheme)
    here = _request_host_port()
    if origin is None or here is None:
        return False
    host, port = origin
    if host not in _LOCAL_BIND_HOSTS:
        return False
    return (host, port) == here


def local_test_login_same_host() -> bool:
    """True when Origin and Referer are absent or match this loopback host.

    Curl sends neither header. A browser POST from another site sends an
    Origin that must name the same host and port as this request. A present
    Referer is held to the same rule.
    """
    if not _header_matches_request_host(request.headers.get("Origin")):
        return False
    referer = request.headers.get("Referer")
    if referer is None:
        referer = request.headers.get("Referrer")
    return _header_matches_request_host(referer)


def local_test_login_request_allowed() -> bool:
    """True when this request may use the test staff route.

    The flag, a non-production process, an unset ``FLY_APP_NAME``, an
    explicit loopback ``HOST``, a matching Host, and a loopback peer are
    all required. This per-request loopback check is the guard when a
    process listens on a public address anyway. Any miss is the route off.
    """
    if local_test_login_boot_block() is not None:
        return False
    if not local_test_login_requested():
        return False
    if _request_hostname() not in _LOCAL_BIND_HOSTS:
        return False
    return _peer_is_loopback()


def ensure_test_staff_user(db: Any) -> dict[str, Any]:
    """Return the fixed local test staff row, creating it when it is missing.

    An existing row is signed in only when it is an active staff account.
    The row is marked email-verified so the test server does not stop on the
    email code. Google ``sub`` is left untouched.

    Args:
        db: Open ``SchoolDB``.

    Returns:
        The staff user row.

    Raises:
        TestStaffRejected: The stored row is not an active staff account.
    """
    row = db.get_user_by_email(TEST_STAFF_EMAIL)
    if row is None:
        row = db.register_staff(TEST_STAFF_EMAIL, TEST_STAFF_NAME)
    if str(row.get("role") or "") != "staff" or row.get("archived_at"):
        raise TestStaffRejected("fixed test staff account cannot be used")
    if not row.get("verified_at"):
        row = db.mark_verified(int(row["id"]))
    return row


def register_local_test_login(app: Flask) -> None:
    """Attach ``/auth/test-staff``.

    The route accepts POST only. GET is 405. A POST answers 404 unless
    ``local_test_login_request_allowed`` is true, and 403 when Origin or
    Referer names a different host. The handler does not read a Google
    credential.

    Args:
        app: Flask application.
    """

    @app.route("/auth/test-staff", methods=["POST"])
    def auth_test_staff():
        """Sign in the fixed test staff account when the local flag allows it."""
        if not local_test_login_request_allowed():
            return ("Not found", 404)
        if not local_test_login_same_host():
            return ("Forbidden", 403)
        from auth import (
            _post_login_redirect,
            _safe_next_url,
            establish_user_session,
            school_db,
        )

        try:
            user = ensure_test_staff_user(school_db())
        except TestStaffRejected:
            return ("Forbidden", 403)
        except Exception:  # noqa: BLE001 - no session if the account cannot be prepared
            app.logger.exception("test staff login could not prepare the fixed account")
            return ("Unavailable", 503)
        nxt = _safe_next_url(request.values.get("next"))
        path = (nxt or "").split("?", 1)[0]
        if nxt and (path == "/staff" or path.startswith("/staff/")):
            session["google_oauth_next"] = nxt
        establish_user_session(user, portal="staff")
        response = _post_login_redirect("staff")
        response.headers["Cache-Control"] = "no-store"
        return response

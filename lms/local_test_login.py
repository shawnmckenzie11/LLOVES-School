"""Loopback-only staff sign-in for the local :8788 test server.

The route is a separate door from Google sign-in. It never reads a posted
``credential`` and never calls the ID token verifier. It stays off unless
``LLOVES_TEST_LOGIN`` is set and this process is not production, not Fly,
and bound to localhost or 127.0.0.1. A request that is not itself from
localhost or 127.0.0.1 is ignored.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import sys
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


def local_test_login_boot_block() -> str | None:
    """Return why startup must refuse the flag, or None when boot may continue.

    Refusal wins over ignoring the flag: a production or non-local process
    that exported the flag should not stay up.

    Returns:
        A short reason, or None when the flag is off or the process is a
        local test server.
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
    from local_dev_seed import local_dev_bind_host

    bind = local_dev_bind_host().strip().lower()
    if bind not in _LOCAL_BIND_HOSTS:
        return (
            "LLOVES_TEST_LOGIN is set but the server host is not localhost "
            "or 127.0.0.1; refusing to start."
        )
    return None


def enforce_local_test_login(app: Flask) -> None:
    """Refuse a production boot with the flag, or warn when the test login is on.

    Args:
        app: Flask application being created.

    Raises:
        RuntimeError: The flag is set on Fly, in production, or on a bind
            host other than localhost or 127.0.0.1.
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


def local_test_login_request_allowed() -> bool:
    """True when this request may use the test staff route.

    The flag, a non-production process, an unset ``FLY_APP_NAME``, a bind on
    localhost or 127.0.0.1, a matching Host, and a loopback peer are all
    required. Any miss is treated as the route being off.
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

    The route is always registered and answers 404 unless
    ``local_test_login_request_allowed`` is true. The handler does not read
    a Google credential.

    Args:
        app: Flask application.
    """

    @app.route("/auth/test-staff", methods=["GET", "POST"])
    def auth_test_staff():
        """Sign in the fixed test staff account when the local flag allows it."""
        if not local_test_login_request_allowed():
            return ("Not found", 404)
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

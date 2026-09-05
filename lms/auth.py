"""Google OAuth, first-login email 2-step, and student-code sessions."""

from __future__ import annotations

import base64
import json
import os
import random
import urllib.parse
from functools import wraps
from typing import Any, Callable

import requests
from flask import (
    Flask,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import email_service
from school_db import SchoolDB
from paths import DEFAULT_IT_EMAIL, SCHOOL_NAME, SCHOOL_SHORT
from student_portal import bind_student_session, next_student_endpoint


def google_client_id() -> str:
    """Return the configured Web OAuth client id, or empty."""
    return (os.getenv("GOOGLE_CLIENT_ID") or "").strip()


def google_client_secret() -> str:
    """Return the configured Web OAuth client secret, or empty."""
    return (os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()


def mock_login_enabled() -> bool:
    """True when the offline account picker replaces real Google OAuth.

    Two cases use it: Flask ``TESTING`` (so unit tests stay offline) and
    ``LOCAL_DEV_LOGIN=1`` in ``lms/.env`` (so a laptop can sign in without
    registering ``127.0.0.1`` as a Google redirect URI). Production never sets
    the flag, so the live site always uses real OAuth.
    """
    if current_app.config.get("TESTING"):
        return True
    return (os.getenv("LOCAL_DEV_LOGIN") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _local_dev_accounts(portal: str) -> list[dict[str, Any]]:
    """List existing users for one-click local sign-in.

    Args:
        portal: ``it`` or ``staff`` — filters to accounts that can enter it.

    Returns:
        User dicts, or an empty list if the database is unavailable.
    """
    try:
        users = school_db().list_staff()
    except Exception:  # noqa: BLE001 - picker must never break the login page
        return []
    allowed = it_emails()
    out = []
    for user in users:
        email = str(user.get("email") or "").lower()
        is_it = user.get("role") == "it" or email in allowed
        if portal == "it" and not is_it:
            continue
        out.append(user)
    return out


def google_oauth_ready() -> bool:
    """True when this process should redirect to real Google accounts.

    Flask ``TESTING`` and local dev login always use the mock picker, even
    when ``lms/.env`` carries production credentials.
    """
    if mock_login_enabled():
        return False
    return bool(google_client_id() and google_client_secret())


def landing_kwargs(**extra: Any) -> dict[str, Any]:
    """Template context for the public landing page."""
    ctx = {
        "school_name": SCHOOL_NAME,
        "school_short": SCHOOL_SHORT,
        "google_client_id": google_client_id() if google_oauth_ready() else "",
        "one_tap_auto": False,
        "student_error": None,
        "oauth_ready": google_oauth_ready(),
    }
    ctx.update(extra)
    return ctx


def it_emails() -> set[str]:
    """Return emails allowed on the IT portal (env ``IT_EMAILS`` plus default)."""
    emails = {DEFAULT_IT_EMAIL.lower()}
    extra = (os.getenv("IT_EMAILS") or "").strip()
    if extra:
        emails.update(part.strip().lower() for part in extra.split(",") if part.strip())
    return emails


def school_db() -> SchoolDB:
    """Return the process SchoolDB attached to the Flask app."""
    from flask import current_app

    db = current_app.config.get("SCHOOL_DB")
    if db is None:
        raise RuntimeError("School database is not initialized")
    return db


def _safe_next_url(next_url: str | None) -> str | None:
    """Return a same-site relative redirect target when safe."""
    if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
        return None
    return next_url


def _google_redirect_uri() -> str:
    """OAuth callback URL from env or the current host."""
    configured = (os.getenv("GOOGLE_REDIRECT_URI") or "").strip()
    if configured:
        return configured
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
    return f"{scheme}://{request.host}/auth/google/callback"


def establish_user_session(user: dict[str, Any], *, portal: str) -> None:
    """Persist a logged-in Flask session after Google + (optional) 2SV.

    Args:
        user: ``users`` row.
        portal: ``staff`` or ``it`` — which shell to land in.
    """
    session.clear()
    session["logged_in"] = True
    session["user_id"] = int(user["id"])
    session["email"] = user["email"]
    session["role"] = user["role"]
    session["portal"] = portal
    session["display_name"] = user.get("display_name") or user["email"]
    session.permanent = True
    school_db().record_login(int(user["id"]))


def begin_pending_2sv(user: dict[str, Any], *, portal: str) -> None:
    """Hold a pending first-login session until the email code matches."""
    session.clear()
    session["pending_2sv"] = True
    session["pending_user_id"] = int(user["id"])
    session["pending_portal"] = portal
    session["email"] = user["email"]
    session.permanent = True


def current_user() -> dict[str, Any] | None:
    """Return the signed-in staff/IT user, or None."""
    if not session.get("logged_in"):
        return None
    user_id = session.get("user_id")
    if not user_id:
        return None
    user = school_db().get_user_by_id(int(user_id))
    if not user:
        session.clear()
        return None
    if user.get("archived_at"):
        session.clear()
        return None
    return user


def _api_auth_error(message: str = "Authentication required.", status: int = 401):
    """Return a JSON auth failure for staff/IT API routes.

    Args:
        message: Error string shown in the UI.
        status: HTTP status (401 or 403).
    """
    return jsonify({"ok": False, "error": message}), status


def login_required(f: Callable) -> Callable:
    """Require a staff/IT Google session."""

    @wraps(f)
    def decorated(*args, **kwargs):
        if current_user() is None:
            if request.path.startswith("/api/"):
                return _api_auth_error()
            return redirect(url_for("landing"))
        return f(*args, **kwargs)

    return decorated


def staff_required(f: Callable) -> Callable:
    """Require Staff portal (IT email may use this portal too)."""

    @wraps(f)
    def decorated(*args, **kwargs):
        user = current_user()
        if user is None:
            if request.path.startswith("/api/"):
                return _api_auth_error()
            return redirect(url_for("auth_google", portal="staff"))
        portal = session.get("portal")
        if portal == "it" and user["role"] == "it":
            # Shawn clicked IT; staff routes still allowed for the IT email.
            return f(*args, **kwargs)
        if portal != "staff" and user["role"] != "it":
            if request.path.startswith("/api/"):
                return _api_auth_error("Ask Admin to grant access.", 403)
            return render_template(
                "forbidden.html",
                message="Ask Admin to grant access.",
            ), 403
        return f(*args, **kwargs)

    return decorated


def it_required(f: Callable) -> Callable:
    """Require the IT portal and an IT role / IT email."""

    @wraps(f)
    def decorated(*args, **kwargs):
        user = current_user()
        if user is None:
            if request.path.startswith("/api/"):
                return _api_auth_error()
            return redirect(url_for("auth_google", portal="it"))
        email = str(user.get("email") or "").lower()
        if user["role"] != "it" and email not in it_emails():
            return render_template(
                "forbidden.html",
                message="Ask Admin to grant access.",
            ), 403
        return f(*args, **kwargs)

    return decorated


def student_required(f: Callable) -> Callable:
    """Require a student-code session (no Google)."""

    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("student_offering_id") and not session.get("student_class_id"):
            return redirect(url_for("landing"))
        return f(*args, **kwargs)

    return decorated


def staff_or_student_scoreboard(f: Callable) -> Callable:
    """Allow staff session or a student-code session scoped to a course."""

    @wraps(f)
    def decorated(*args, **kwargs):
        if current_user() is not None:
            return f(*args, **kwargs)
        if session.get("student_offering_id") or session.get("student_class_id"):
            return f(*args, **kwargs)
        if request.path.startswith("/api/"):
            return _api_auth_error()
        return redirect(url_for("landing"))

    return decorated


def _send_first_login_code(user: dict[str, Any]) -> tuple[str, bool]:
    """Generate and store a 6-digit code; email it when delivery is configured.

    Args:
        user: Allowlisted user row.

    Returns:
        ``(code, emailed)``. ``emailed`` is True only when Resend or SMTP
        accepted the message. The code is stored on the user row; production
        never puts it on the verify page.
    """
    code = f"{random.randint(100000, 999999)}"
    school_db().set_verification_code(int(user["id"]), code)
    name = user.get("display_name") or user["email"].split("@")[0]
    emailed = email_service.send_verification_email(user["email"], name, code)
    return code, emailed


def _decode_google_jwt(jwt_token: str) -> dict[str, Any] | None:
    """Decode a Google Identity Services JWT payload without verifying.

    Signature verification is skipped only when used as a convenience parse
    after GIS posts to our callback; production OAuth code flow uses userinfo.
    """
    try:
        parts = jwt_token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload_json = base64.b64decode(payload_b64).decode("utf-8")
        payload = json.loads(payload_json)
        return payload if isinstance(payload, dict) else None
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _finish_google_identity(
    *,
    email: str,
    google_sub: str,
    name: str | None,
    portal: str,
):
    """Allowlist check, optional 2SV, then redirect to the right shell.

    Args:
        email: Google account email.
        google_sub: Google ``sub``.
        name: Display name.
        portal: Requested ``staff`` or ``it``.

    Returns:
        Flask redirect or 403 response.
    """
    db = school_db()
    email_key = (email or "").strip().lower()
    user = db.get_user_by_google_sub(google_sub) or db.get_user_by_email(email_key)
    if not user:
        return render_template(
            "forbidden.html",
            message="This Google account is not registered. Ask IT.",
        ), 403
    if user.get("archived_at"):
        session.clear()
        return redirect(url_for("landing"))
    if google_sub and user.get("google_sub") != google_sub:
        user = db.link_google(int(user["id"]), google_sub, name)
    elif name and not user.get("display_name"):
        user = db.link_google(int(user["id"]), user.get("google_sub") or google_sub, name)

    portal_key = "it" if portal == "it" else "staff"
    email_l = str(user["email"]).lower()
    is_it = user["role"] == "it" or email_l in it_emails()

    if portal_key == "it" and not is_it:
        return render_template(
            "forbidden.html",
            message="Ask Admin to grant access.",
        ), 403
    if portal_key == "staff" and user["role"] not in {"staff", "it"} and not is_it:
        return render_template(
            "forbidden.html",
            message="This Google account is not registered. Ask IT.",
        ), 403

    if not user.get("verified_at"):
        _code, emailed = _send_first_login_code(user)
        begin_pending_2sv(user, portal=portal_key)
        return redirect(url_for("verify_email", sent="1" if emailed else "0"))

    establish_user_session(user, portal=portal_key)
    return _post_login_redirect(portal_key)


def _post_login_redirect(portal: str):
    """Send a fully authenticated user to IT or staff home."""
    next_url = _safe_next_url(session.pop("google_oauth_next", None))
    if next_url:
        return redirect(next_url)
    if portal == "it":
        return redirect(url_for("it_dashboard"))
    return redirect(url_for("staff_home"))


def _disconnect_student_live_if_bound() -> None:
    """Best-effort: mark the bound student left and wipe mood/character.

    No-op when the Flask session is not a live-session student join.
    """
    live_session_id = session.get("student_live_session_id")
    student_id = session.get("student_id")
    if not live_session_id or not student_id:
        return
    try:
        school_db().disconnect_live_session_student(
            int(live_session_id),
            int(student_id),
            clear_mood=True,
        )
    except Exception:  # noqa: BLE001 - disconnect must never block logout
        return


def register_auth_routes(app: Flask) -> None:
    """Attach Google, verify, logout, and student-code routes."""

    @app.route("/auth/google")
    def auth_google():
        """Start Google OAuth, or the test mock / setup page when OAuth is off."""
        portal = (request.args.get("portal") or "staff").strip().lower()
        if portal not in {"staff", "it"}:
            portal = "staff"
        session["oauth_portal"] = portal
        next_url = _safe_next_url(request.args.get("next"))
        if next_url:
            session["google_oauth_next"] = next_url

        user = current_user()
        if user and user.get("verified_at"):
            email_l = str(user["email"]).lower()
            is_it = user["role"] == "it" or email_l in it_emails()
            if portal == "it" and is_it:
                session["portal"] = "it"
                return redirect(url_for("it_dashboard"))
            if portal == "staff" and (user["role"] in {"staff", "it"} or is_it):
                session["portal"] = "staff"
                return redirect(url_for("staff_home"))

        if not google_oauth_ready():
            if mock_login_enabled():
                app.logger.warning(
                    "Local dev login is on; using the offline account picker."
                )
                return render_template(
                    "google_auth.html",
                    portal=portal,
                    school=SCHOOL_SHORT,
                    known_accounts=_local_dev_accounts(portal),
                )
            app.logger.warning(
                "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set; OAuth is off."
            )
            return render_template(
                "google_setup.html",
                portal=portal,
                school=SCHOOL_SHORT,
                school_name=SCHOOL_NAME,
                redirect_uri=_google_redirect_uri(),
            ), 503

        state = f"{random.randint(100000, 999999)}"
        session["google_oauth_state"] = state
        params = {
            "client_id": google_client_id(),
            "redirect_uri": _google_redirect_uri(),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "prompt": "select_account",
        }
        google_auth_url = (
            "https://accounts.google.com/o/oauth2/v2/auth?"
            + urllib.parse.urlencode(params)
        )
        return redirect(google_auth_url)

    @app.route("/auth/google/callback", methods=["GET", "POST"])
    def auth_google_callback():
        """Complete OAuth, One Tap JWT, or mock Google login."""
        portal = session.get("oauth_portal") or request.args.get("portal") or "staff"
        client_id = google_client_id()
        client_secret = google_client_secret()
        oauth_ready = google_oauth_ready()

        jwt_token = request.form.get("credential")
        if jwt_token:
            payload = _decode_google_jwt(jwt_token)
            if not payload:
                return render_template(
                    "forbidden.html",
                    message="Google One Tap authentication failed.",
                ), 401
            email = payload.get("email")
            google_id = payload.get("sub")
            name = payload.get("name") or payload.get("given_name")
            if not email or not google_id:
                return render_template(
                    "forbidden.html",
                    message="Google One Tap authentication failed.",
                ), 401
            return _finish_google_identity(
                email=email, google_sub=str(google_id), name=name, portal=portal
            )

        if not oauth_ready:
            if not mock_login_enabled():
                return redirect(url_for("auth_google", portal=portal))
            email = request.args.get("email")
            name = request.args.get("name") or (email.split("@")[0] if email else None)
            if not email:
                return redirect(url_for("landing"))
            google_id = f"mock_google_{email.strip().lower()}"
            return _finish_google_identity(
                email=email, google_sub=google_id, name=name, portal=portal
            )

        code = request.args.get("code")
        state = request.args.get("state")
        stored_state = session.pop("google_oauth_state", None)
        if not code or (stored_state and state != stored_state):
            return render_template(
                "forbidden.html",
                message="Google authentication failed: state mismatch.",
            ), 401
        try:
            token_resp = requests.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": _google_redirect_uri(),
                    "grant_type": "authorization_code",
                },
                timeout=10,
            )
            token_resp.raise_for_status()
            access_token = token_resp.json().get("access_token")
            if not access_token:
                return render_template(
                    "forbidden.html",
                    message="Failed to fetch access token from Google.",
                ), 401
            userinfo_resp = requests.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            userinfo_resp.raise_for_status()
            info = userinfo_resp.json()
            email = info.get("email")
            google_id = info.get("sub")
            name = info.get("name") or info.get("given_name")
            if not email or not google_id:
                return render_template(
                    "forbidden.html",
                    message="Google profile info missing required fields.",
                ), 401
            return _finish_google_identity(
                email=email, google_sub=str(google_id), name=name, portal=portal
            )
        except requests.RequestException as exc:
            app.logger.exception("Google OAuth token exchange failed")
            return render_template(
                "forbidden.html",
                message=f"Google authentication failed: {exc}",
            ), 401

    @app.route("/verify-email", methods=["GET", "POST"])
    def verify_email():
        """First-login 6-digit code (skipped on later visits)."""
        if not session.get("pending_2sv"):
            if current_user():
                return _post_login_redirect(session.get("portal") or "staff")
            return redirect(url_for("landing"))
        db = school_db()
        user = db.get_user_by_id(int(session["pending_user_id"]))
        if not user:
            session.clear()
            return redirect(url_for("landing"))
        error = None
        info = None
        if request.method == "POST":
            entered = (request.form.get("code") or "").strip()
            if entered and entered == str(user.get("verification_code") or ""):
                user = db.mark_verified(int(user["id"]))
                portal = session.get("pending_portal") or "staff"
                establish_user_session(user, portal=portal)
                return _post_login_redirect(portal)
            error = "Invalid verification code. Please try again."
            user = db.get_user_by_id(int(user["id"])) or user

        sent_raw = request.args.get("sent")
        sent = None if sent_raw is None else sent_raw in {"1", "true", "yes"}
        if sent is True:
            info = email_service.delivery_status_message(True)
        elif sent is False:
            error = error or email_service.delivery_status_message(False)

        dev_code = None
        if email_service.show_on_page_verification_code():
            dev_code = user.get("verification_code")

        return render_template(
            "verify.html",
            email=user["email"],
            error=error,
            info=info,
            dev_code=dev_code,
            email_configured=email_service.is_email_delivery_configured(),
        )

    @app.route("/resend-verification", methods=["POST"])
    def resend_verification():
        """Email a fresh first-login code for a pending 2SV session."""
        if not session.get("pending_2sv"):
            return redirect(url_for("landing"))
        db = school_db()
        user = db.get_user_by_id(int(session["pending_user_id"]))
        if not user or user.get("verified_at"):
            return redirect(url_for("landing"))
        _code, emailed = _send_first_login_code(user)
        return redirect(url_for("verify_email", sent="1" if emailed else "0"))

    @app.route("/logout")
    def logout():
        """Clear staff, IT, and student sessions."""
        _disconnect_student_live_if_bound()
        session.clear()
        return redirect(url_for("landing"))

    @app.route("/api/student/live-available")
    def api_student_live_available():
        """Public check: whether any live class session is currently joinable."""
        from flask import jsonify

        active = school_db().has_active_live_sessions()
        return jsonify({"ok": True, "active": active})

    @app.route("/api/student/leave", methods=["POST"])
    def api_student_leave():
        """Best-effort student disconnect (tab close / End Game client path).

        Sets ``left_at`` and wipes mood/character for the bound live session
        attendee. Safe to call with sendBeacon; always returns 204 when the
        cookie session is incomplete.

        Only clears student session keys so a same-browser staff login (used
        during local testing) is not wiped by a student tab close/beacon.
        """
        from flask import Response

        _disconnect_student_live_if_bound()
        for key in list(session.keys()):
            if key.startswith("student_") or key == "role" and session.get("role") == "student":
                session.pop(key, None)
        if session.get("role") == "student":
            session.pop("role", None)
        return Response(status=204)

    @app.route("/auth/student-code", methods=["POST"])
    def auth_student_code():
        """Join an active live class session via ephemeral code + roster name.

        Landing Student join is active-session-only: durable offering/class
        codes do not admit students when idle.
        """
        from flask import jsonify

        db = school_db()
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
        ip = ip.split(",")[0].strip()
        attempts = db.record_code_attempt(ip)
        if attempts > 5:
            body = {"ok": False, "error": "Too many attempts. Try again in a few minutes."}
            if request.is_json:
                return jsonify(body), 429
            return render_template(
                "landing.html", **landing_kwargs(student_error=body["error"])
            ), 429

        payload = request.get_json(silent=True) or {}
        raw = request.form.get("code") or payload.get("code") or ""
        name = (request.form.get("name") or payload.get("name") or "").strip()
        code = str(raw).strip().upper()

        no_session_msg = (
            "No live class is running right now. Ask your teacher to start "
            "class, then try again."
        )
        mismatch_msg = (
            "Double-check the code with your teacher, or make sure your "
            "username matches the roster for this course."
        )

        def _fail(msg: str, status: int = 401):
            """Return a join error for JSON or landing form posts."""
            if request.is_json:
                return jsonify({"ok": False, "error": msg}), status
            return render_template(
                "landing.html", **landing_kwargs(student_error=msg)
            ), status

        if not db.has_active_live_sessions():
            return _fail(no_session_msg)

        if not name:
            return _fail("Enter the name on your class roster.")

        live_session = db.get_active_live_session_by_code(code)
        if live_session is None:
            return _fail(mismatch_msg)

        student = db.find_roster_student_for_live_session(
            int(live_session["id"]), name
        )
        if student is None:
            return _fail(mismatch_msg)

        class_id = int(live_session["class_id"])
        try:
            cls = db.game.get_class(class_id)
        except KeyError:
            return _fail(mismatch_msg)

        offering = None
        if cls.get("offering_id"):
            try:
                offering = db.get_offering(int(cls["offering_id"]))
            except KeyError:
                offering = None
        if offering is None:
            return _fail(mismatch_msg)

        db.join_live_class_session(
            int(live_session["id"]),
            int(student["id"]),
            codename=str(student.get("codename") or name),
        )
        bind_student_session(
            session,
            offering,
            cls,
            student,
            live_session_id=int(live_session["id"]),
            session_code=str(live_session.get("session_code") or code),
        )
        return redirect(
            url_for(
                next_student_endpoint(
                    db,
                    class_id,
                    int(student["id"]),
                )
            )
        )

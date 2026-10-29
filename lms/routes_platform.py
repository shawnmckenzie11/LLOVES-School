"""Health, static files, invites, course-code rotation, and PIN routes.

Split from ``app.py`` for MCK-364. URLs, methods, decorators, and
endpoint names match the routes that were registered on the app.
"""

from __future__ import annotations

from routes_common import LaneBlueprint
from app import (
    LMS_DIR,
    MGS_STATIC,
    SCHOOL_NAME,
    SCHOOL_SHORT,
    _include_test_students,
    _same_origin_post,
    current_user,
    it_required,
    join_limit_ip_key,
    jsonify,
    login_required,
    redirect,
    render_template,
    request,
    request_client_ip,
    resolve_student_live_context,
    send_from_directory,
    session,
    staff_invites,
    student_required,
    url_for,
)

def register_platform(app, school, shared):
    """Register platform routes on the platform blueprint.

    Auth and login routes that already live in ``auth.py`` stay there.
    This module holds the platform routes that used to be declared in
    ``app.py``. Endpoint names stay bare.

    Args:
        app: Flask application.
        school: Open school database for this process.
        shared: Helpers used by more than one lane.
    """
    bp = LaneBlueprint('platform', __name__)
    _audit = shared._audit
    _require_class_staff = shared._require_class_staff


    @bp.route("/static/<path:filename>")
    def static_files(filename: str):
        """Serve LLOVES assets first, then Math Game Show static files."""
        lms_file = LMS_DIR / "static" / filename
        if lms_file.is_file():
            response = send_from_directory(LMS_DIR / "static", filename)
        else:
            response = send_from_directory(MGS_STATIC, filename)
        if filename.startswith("live-media/"):
            # Student home iframes these pages; keep them same-origin only.
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "connect-src 'self'; "
                "frame-ancestors 'self'; "
                "base-uri 'self'; "
                "form-action 'self'"
            )
        return response

    @bp.route("/health")
    def health():
        """Fly / DNS liveness — no auth, no presence ping, no live session."""
        return jsonify(
            {
                "ok": True,
                "school": SCHOOL_SHORT,
                "live_presence": school.live_presence_label(),
            }
        )

    @bp.route("/it/offerings/<int:offering_id>/rotate", methods=["POST"])
    @it_required
    def it_rotate_code(offering_id: int):
        """Rotate the shared student key for a (semester, course) pair."""
        school.rotate_live_access_code(offering_id)
        return redirect(url_for("it_dashboard"))

    @bp.route("/invite/<token>")
    def invite_landing(token: str):
        """MCK-183 I3: the invite link. Valid, expired, used, revoked, unknown.

        Valid: remember the invite in the session and offer Continue with
        Google (``login_hint`` = invited email). The invite is only used up
        when the matching Google account signs in. Already signed in as the
        invited teacher: accept and go to Welcome. Signed in as someone
        else: the mismatch page, invite untouched.
        """
        state, row = staff_invites.resolve_invite(school, token)
        if state == "revoked":
            state = "void"
        if state == "unknown":
            state = "void"
        context: dict[str, Any] = {"state": state, "school_name": SCHOOL_NAME}
        if state == "used":
            context["signin_url"] = url_for("auth_google", portal="staff")
        if state != "valid" or row is None:
            return render_template("invite.html", **context)
        invite_email = str(row["email"]).lower()
        user = current_user()
        if user is not None:
            if str(user.get("email") or "").lower() == invite_email:
                staff_invites.mark_accepted(school, int(row["id"]))
                _audit("invite.accept", "staff", resource_id=row.get("user_id"),
                       detail={"invite_id": row.get("id")})
                return redirect(url_for("staff_welcome"))
            return render_template(
                "invite.html",
                state="mismatch",
                invite_email=invite_email,
                google_email=str(user.get("email") or "").lower(),
                retry_url=url_for("logout_to_invite", token=token),
                school_name=SCHOOL_NAME,
            )
        session["pending_invite_id"] = int(row["id"])
        context.update(
            email=invite_email,
            continue_url=url_for(
                "auth_google",
                portal="staff",
                next=url_for("staff_welcome"),
                login_hint=invite_email,
            ),
        )
        return render_template("invite.html", **context)

    @bp.route("/invite/<token>/switch", methods=["POST"])
    def logout_to_invite(token: str):
        """Sign out the other account, then reopen the same invite link.

        POST only (#269 gate): a GET link could sign anyone out from another
        site. MCK-183 Ops LOW-2: a cross-site auto-submitted form still
        reached here, so a POST whose ``Sec-Fetch-Site`` is ``cross-site``
        or whose ``Origin`` (or, without one, ``Referer``) is another host
        is refused, and the session is only cleared for a valid open invite.
        """
        if not _same_origin_post():
            return "Forbidden", 403
        state, _row = staff_invites.resolve_invite(school, token)
        if state == "valid":
            session.clear()
        return redirect(url_for("invite_landing", token=token))

    @bp.route("/welcome")
    def welcome_link():
        """MCK-183: the link Shawn sends a new teacher.

        Signed in already: straight to the Welcome screen. Otherwise Google
        sign-in (and the first-login email code) with ``next`` kept, so she
        lands on Welcome. An unlisted Google account still gets the usual 403.
        """
        if current_user() is not None:
            return redirect(url_for("staff_welcome"))
        return redirect(url_for("auth_google", portal="staff", next=url_for("staff_welcome")))

    @bp.route("/api/classes/<int:class_id>/students/<int:student_id>/pin", methods=["PUT"])
    @login_required
    def api_set_student_pin(class_id: int, student_id: int):
        """Set or reset a student's 4-digit catch-up PIN (stored hashed)."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        owned = school.game.conn.execute(
            "SELECT 1 FROM students WHERE id = ? AND class_id = ?", (student_id, class_id)
        ).fetchone()
        if owned is None:
            return jsonify({"ok": False, "error": "Not found."}), 404
        try:
            school.set_student_pin(class_id, student_id, str(body.get("pin") or ""), pin_set_by="teacher")
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True})

    def _default_pin_slips_for_teacher(class_id: int) -> tuple[list[dict[str, Any]], Any]:
        """Default-PIN slips for the owning teacher, or a JSON error response.

        The slip text is returned to the caller and is not logged.

        Args:
            class_id: Class primary key.

        Returns:
            ``(slips, None)`` when the teacher may see them, otherwise
            ``([], response)``.
        """
        denied = _require_class_staff(class_id)
        if denied:
            return [], denied
        slips = school.teacher_default_pin_slips(
            class_id,
            str(app.secret_key or ""),
            include_test_students=_include_test_students(),
        )
        return slips, None

    @bp.route("/api/classes/<int:class_id>/default-pin-slips", methods=["GET"])
    @login_required
    def api_default_pin_slips(class_id: int):
        """Printable default-PIN slips. Not included in the roster HTML."""
        slips, denied = _default_pin_slips_for_teacher(class_id)
        if denied:
            return denied
        resp = jsonify(
            {
                "ok": True,
                "slips": [
                    {
                        "student_id": int(row["student_id"]),
                        "name": str(row["name"]),
                        "slip": str(row["slip"]),
                    }
                    for row in slips
                ],
            }
        )
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @bp.route(
        "/api/classes/<int:class_id>/students/<int:student_id>/default-pin-slip",
        methods=["GET"],
    )
    @login_required
    def api_default_pin_slip(class_id: int, student_id: int):
        """One default-PIN slip for Show or Print. 404 when the student set their own."""
        slips, denied = _default_pin_slips_for_teacher(class_id)
        if denied:
            return denied
        match = next((row for row in slips if int(row["student_id"]) == int(student_id)), None)
        if match is None:
            resp = jsonify({"ok": False, "error": "No default PIN."})
            resp.headers["Cache-Control"] = "no-store"
            return resp, 404
        resp = jsonify(
            {
                "ok": True,
                "student_id": int(match["student_id"]),
                "name": str(match["name"]),
                "slip": str(match["slip"]),
            }
        )
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @bp.route(
        "/api/classes/<int:class_id>/students/<int:student_id>/catchup-pin-unlock",
        methods=["POST"],
    )
    @login_required
    def api_catchup_pin_unlock(class_id: int, student_id: int):
        """Clear per-student catch-up PIN try limits after teacher review."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        user = current_user()
        if user is None or not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden."}), 403
        owned = school.game.conn.execute(
            "SELECT 1 FROM students WHERE id = ? AND class_id = ?", (student_id, class_id)
        ).fetchone()
        if owned is None:
            return jsonify({"ok": False, "error": "Not found."}), 404
        school.teacher_unlock_catchup_pin(class_id, student_id)
        return jsonify({"ok": True})

    @bp.route("/api/classes/<int:class_id>/catchup-pin-policy", methods=["POST"])
    @login_required
    def api_catchup_pin_policy(class_id: int):
        """Toggle per-class 'require PIN change' for catch-up (MCK-207)."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        required = bool(body.get("require_pin_change"))
        if not school.set_require_catchup_pin_change(class_id, required):
            return jsonify({"ok": False, "error": "Not found."}), 404
        return jsonify({"ok": True, "require_pin_change": required})

    @bp.route("/api/student/catchup-pin", methods=["POST"])
    @student_required
    def api_student_catchup_pin():
        """Set catch-up PIN from portal session or live class with verified current PIN."""
        from participation import (
            CATCHUP_PIN_IP_LIMIT,
            CATCHUP_PIN_STUDENT_SOFT_CAP,
            KEY_PIN_CURRENT_REQUIRED,
            KEY_PIN_IP_LOCKED,
            KEY_PIN_STUDENT_SOFT,
            KEY_PIN_WRONG,
            PIN_WINDOW_S,
            catchup_ip_limit_key,
            catchup_pin_student_limit_key,
            copy,
        )

        ctx = resolve_student_live_context(school, session)
        if ctx is None or ctx.get("student_id") in (None, ""):
            return jsonify({"ok": False, "error": "Not signed in."}), 403
        class_id = int(ctx["class_id"])
        student_id = int(ctx["student_id"])
        body = request.get_json(silent=True) or {}
        pin = str(body.get("pin") or "")
        confirm = str(body.get("confirm") or "")
        current_pin = str(body.get("current_pin") or "")
        if pin != confirm:
            return jsonify({"ok": False, "error": "PINs must match."}), 400
        reserved: list[int] | None = None
        if school.catchup_portal_session_valid(session, class_id, student_id):
            pass
        elif ctx.get("unmatched"):
            return jsonify(
                {
                    "ok": False,
                    "error": "Open your catch-up link and sign in to set your PIN.",
                    "needs_catchup_portal": True,
                }
            ), 403
        else:
            gate = school.catchup_pin_student_gate(class_id, student_id)
            if gate:
                return jsonify({"ok": False, "error": copy(gate)["text"]}), 429
            client_ip_key = join_limit_ip_key(request_client_ip())
            global_ip_key = catchup_ip_limit_key(client_ip_key)
            student_key = catchup_pin_student_limit_key(class_id, student_id)
            limits = {
                global_ip_key: CATCHUP_PIN_IP_LIMIT,
                student_key: CATCHUP_PIN_STUDENT_SOFT_CAP,
            }
            reserved = school.reserve_catchup_pin_attempts(
                [global_ip_key, student_key], limits, window_s=PIN_WINDOW_S
            )
            if reserved is None:
                return jsonify({"ok": False, "error": copy(KEY_PIN_IP_LOCKED)["text"]}), 429
            if school.student_catchup_pin_hash_set(class_id, student_id) and not current_pin:
                school.release_catchup_pin_attempts(reserved)
                return jsonify(
                    {"ok": False, "error": copy(KEY_PIN_CURRENT_REQUIRED)["text"]}
                ), 400
            if not school.verify_student_current_pin(class_id, student_id, current_pin):
                school.record_catchup_pin_failure(class_id, student_id)
                return jsonify({"ok": False, "error": copy(KEY_PIN_WRONG)["text"]}), 403
            school.release_catchup_pin_attempts(reserved)
            reserved = None
        try:
            school.set_student_pin(int(class_id), int(student_id), pin, pin_set_by="student")
        except ValueError as exc:
            if reserved:
                school.release_catchup_pin_attempts(reserved)
            return jsonify({"ok": False, "error": str(exc)}), 400
        school.refresh_catchup_portal_pin_stamp(session, class_id, student_id)
        return jsonify({"ok": True, "toast": "PIN saved. You're set for catch-up."})

    @bp.route("/api/student/catchup-pin/later", methods=["POST"])
    @student_required
    def api_student_catchup_pin_later():
        """Dismiss the optional catch-up PIN pop-up until the next visit (MCK-207)."""
        ctx = resolve_student_live_context(school, session)
        if ctx is None or ctx.get("student_id") in (None, ""):
            return jsonify({"ok": False, "error": "Not signed in."}), 403
        class_id = int(ctx["class_id"])
        student_id = int(ctx["student_id"])
        if school.require_catchup_pin_change(int(class_id)):
            return jsonify({"ok": False, "error": "PIN change required."}), 403
        session[f"catchup_pin_later:{class_id}:{student_id}"] = True
        return jsonify({"ok": True})
    app.register_blueprint(bp)

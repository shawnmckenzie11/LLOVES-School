"""Staff, IT, roster, bank and module import, portfolio, and the public front door.

The front door includes the home page, request-access, and the privacy,
terms, and support pages.

Split from ``app.py`` for MCK-364. URLs, methods, decorators, and
endpoint names match the routes that were registered on the app.
"""

from __future__ import annotations

from routes_common import LaneBlueprint
from app import (
    AwardTallyBusy,
    AwardTallyShared,
    CELEBRATION_STAFF_COPY,
    DEV_SECRET_KEY,
    EMPTY_BANKS_MESSAGE,
    MGS_TEMPLATES,
    Path,
    Response,
    SCHOOL_NAME,
    STAFF_2FA_MODE_LABELS,
    START_FRESH_FAILED,
    TIME_OPTIONS,
    WONDER_COPY,
    _catchup_student_for_class,
    _format_last_taken,
    _include_test_students,
    _json_error,
    _library_source,
    _optional_date,
    _pack_dest_root,
    _pin_portal_identity,
    _ready_library,
    _render_component,
    _same_origin_post,
    _send_module_file,
    _serve_component_item,
    _student_dashboard_live_handover,
    _student_dashboard_redirect_if_live,
    _wants_json,
    abort,
    annotate_offering_pack,
    apply_question_bank_bulk,
    apply_staff_question_patch,
    apply_visible_bank_question_counts,
    backfill_module_pack,
    bind_pin_portal_session,
    bind_student_session,
    build_celebration_board,
    celebration_candidates,
    class_is_simple,
    clear_rejoin_cookie,
    create_live_bank_question,
    create_staff_bank_question,
    current_user,
    delete_staff_bank_question,
    email_service,
    end_time_options,
    forget_featured_students,
    get_question_bank,
    instances_payload,
    it_emails,
    it_required,
    join_limit_ip_key,
    json,
    jsonify,
    landing_kwargs,
    library_counts,
    library_file_path,
    list_assignments,
    list_bots,
    list_pages,
    list_question_banks,
    list_questions,
    list_quizzes,
    list_staff_bank_questions,
    live_class_registry,
    live_media_url_swap_allowed,
    live_results_csv,
    logger,
    login_required,
    make_response,
    next_student_endpoint,
    offering_uses_shell_tabs,
    onboarding_clean_preset,
    onboarding_preset_lists,
    os,
    outline_nav,
    outline_raw_modules,
    pack_needs_resume,
    pack_ui_state,
    plain_name_wording,
    pop_dashboard_focus_catchup,
    re,
    read_pack_status,
    redirect,
    rejoin_token_from_cookie,
    render_template,
    request,
    request_client_ip,
    resolve_module_pack,
    send_from_directory,
    session,
    set_dashboard_focus_catchup,
    set_featured_award,
    set_rejoin_cookie,
    simple_course,
    sqlite3,
    staff_award_periods,
    staff_invites,
    staff_or_student_scoreboard,
    staff_required,
    start_fresh_award_tally,
    status_payload,
    student_required,
    student_url_with_token,
    syllabus_mod,
    threading,
    tour_run_type,
    url_for,
    wizard_defaults,
    wrap_page,
)

def register_teacher(app, school, shared):
    """Register teacher, IT, and front-door routes on the teacher blueprint.

    The blueprint keeps each view's original endpoint name so ``url_for``
    and the URL-map snapshot stay on the bare function name.

    Args:
        app: Flask application.
        school: Open school database for this process.
        shared: Helpers used by more than one lane.
    """
    bp = LaneBlueprint('teacher', __name__)
    try:
        from features import deny_unless, has_feature, limits_apply
    except ImportError:
        from lms.features import deny_unless, has_feature, limits_apply
    try:
        from feature_gates import (
            BASIC_CLASS_CAP,
            BASIC_SEAT_CAP,
            catchup_row_allowed,
            class_owner,
            scope_catchup_links,
        )
    except ImportError:
        from lms.feature_gates import (
            BASIC_CLASS_CAP,
            BASIC_SEAT_CAP,
            catchup_row_allowed,
            class_owner,
            scope_catchup_links,
        )
    _audit = shared._audit
    _can_access_class_live_asset = shared._can_access_class_live_asset
    _content_questions_scope = shared._content_questions_scope
    _reject_basic_roster = shared._reject_basic_roster
    _require_class_staff = shared._require_class_staff
    _staff_post = shared._staff_post
    _student_identity = shared._student_identity
    _viewer_is_stmtg_tester = shared._viewer_is_stmtg_tester


    def _staff_nav_courses(teacher_user_id: int) -> list[dict[str, Any]]:
        """Build top-menu quicklinks for a teacher's active-semester courses.

        Links into each offering's staff course page when a class exists;
        otherwise points at the teacher dashboard so they can Populate Class.

        Args:
            teacher_user_id: Staff or IT user id.

        Returns:
            List of ``{label, href, class_id, offering_id}`` dicts.
        """
        active = school.get_active_semester()
        if not active:
            return []
        offerings = school.list_offerings(
            teacher_user_id=int(teacher_user_id),
            semester_id=int(active["id"]),
            include_archived=False,
        )
        items: list[dict[str, Any]] = []
        for offering in offerings:
            label = str(
                offering.get("section_code")
                or offering.get("ontario_code")
                or ""
            )
            classes = offering.get("classes") or []
            class_id = int(classes[0]["id"]) if classes else None
            href = (
                url_for("staff_course", class_id=class_id)
                if class_id
                else url_for("staff_home")
            )
            items.append(
                {
                    "label": label,
                    "href": href,
                    "class_id": class_id,
                    "offering_id": int(offering["id"]),
                }
            )
        return items

    def _assign_schedule_from_form() -> tuple[str, str, str]:
        """Read and validate live days/time from an Admin assign form.

        Returns:
            ``(live_days, live_time, live_end_time)`` wizard presets.

        Raises:
            ValueError: Missing or invalid schedule fields.
        """
        live_days = (request.form.get("live_days") or "").strip()
        live_time = (request.form.get("live_time") or "").strip()
        live_end_time = (request.form.get("live_end_time") or "").strip()
        if not live_days or not live_time:
            raise ValueError("Choose live-class days and start time.")
        # Empty end time is filled from the Ontario course in set_offering_schedule.
        return live_days, live_time, live_end_time

    def _assign_slides_theme_from_form() -> tuple[str | None, str | None]:
        """Parse optional template / style-guide URL or file id from Assign Course.

        Empty fields mean school defaults (resolved in ``assign_course``).

        Returns:
            ``(slides_template_id, style_guide_file_id)`` or Nones when blank.

        Raises:
            ValueError: Non-empty value that is not a Google URL or file id.
        """
        from slides_template import parse_google_file_id

        raw_template = (
            request.form.get("slides_template")
            or request.form.get("slides_template_id")
            or ""
        ).strip()
        raw_style = (
            request.form.get("style_guide")
            or request.form.get("style_guide_file_id")
            or ""
        ).strip()
        template = parse_google_file_id(raw_template) if raw_template else None
        style = parse_google_file_id(raw_style) if raw_style else None
        return template or None, style or None

    def _roster_codenames_from_assign_form() -> list[str] | None:
        """Parse an optional Admin assign-form roster CSV (first column only).

        Returns:
            ``None`` when no file was uploaded; otherwise a validated Codename list.

        Raises:
            ValueError: Empty file, no names, invalid/duplicate Codenames, or commas.
        """
        uploaded = request.files.get("roster_csv")
        if uploaded is None or not getattr(uploaded, "filename", None):
            return None
        if not str(uploaded.filename).strip():
            return None
        raw = uploaded.read()
        if not raw:
            raise ValueError("Roster CSV is empty.")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        from db import parse_codename_column_csv, roster_from_codenames

        names = parse_codename_column_csv(text)
        roster_from_codenames(names)  # normalize + reject commas/duplicates early
        return names

    def _populate_offering_class(
        offering: dict[str, Any],
        *,
        teacher_user_id: int,
        codenames: list[str],
    ) -> dict[str, Any]:
        """Create the Attendance & Participation class for a newly assigned offering.

        Args:
            offering: ``course_offerings`` row (must already have live schedule).
            teacher_user_id: Staff ``users.id`` who owns the class.
            codenames: Validated Codename roster.

        Returns:
            Enriched class payload from ``create_class``.

        Raises:
            ValueError: Missing semester/schedule or invalid roster.
        """
        semester = school.get_semester(int(offering["semester_id"]))
        if not semester:
            raise ValueError("Semester is missing.")
        days = str(offering.get("live_days") or "").strip()
        time_label = str(offering.get("live_time") or "").strip()
        if not days or not time_label:
            raise ValueError(
                "Set live-class days and start time before populating the class."
            )
        created = school.game.create_class(
            year=str(semester["year_display"]),
            semester=str(semester["term"]),
            course_code=str(offering["ontario_code"]),
            days_preset=days,
            time_label=time_label,
            codenames=codenames,
            offering_id=int(offering["id"]),
            teacher_user_id=int(teacher_user_id),
        )
        class_id = int(created["id"])
        school.ensure_class_test_student(class_id)
        school.ensure_default_pins_for_class(class_id, str(app.secret_key or DEV_SECRET_KEY))
        return school.enrich_class(created)

    def _owned_offering_for_pack(class_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return the class and offering for a staff module-pack route, or abort.

        Args:
            class_id: Staff class id.
        """
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            abort(403)
        cls = school.enrich_class(school.game.get_class(class_id))
        offering_id = cls.get("offering_id")
        if not offering_id:
            abort(403)
        offering = school.get_offering(int(offering_id))
        if int(offering["teacher_user_id"]) != int(user["id"]) and user["role"] != "it":
            abort(403)
        return cls, offering

    def _pack_for_staff_code(code: str):
        """Resolve the current teacher's instance pack for a legacy course-code URL.

        Args:
            code: Ontario course code from the URL.
        """
        user = current_user()
        if not user:
            return None
        active = school.get_active_semester()
        if not active:
            return None
        key = (code or "").strip().upper()
        offering = school.get_offering_for(int(active["id"]), key, int(user["id"]))
        if offering is None and user.get("role") == "it":
            matches = [
                row
                for row in school.list_offerings(semester_id=int(active["id"]))
                if str(row["ontario_code"]).upper() == key
            ]
            offering = matches[0] if matches else None
        if offering is None:
            return None
        offering = school.ensure_offering_instance(offering)
        return resolve_module_pack(
            key,
            offering.get("imscc_path"),
            data_dir=school.data_dir,
            offering_id=int(offering["id"]),
            instance_relpath=offering.get("instance_relpath"),
            library_id=offering.get("library_id"),
            library_source=_library_source(school, offering),
        )

    def _install_module_pack_job(
        offering_id: int, stored: Path, dest_root: Path
    ) -> None:
        """Stream, ingest, and seed syllabus for a stored cartridge.

        Args:
            offering_id: ``course_offerings.id``.
            stored: Path to ``course.imscc`` already on disk.
            dest_root: Shared ``libraries/<id>/`` folder (not an instance pack).
        """
        try:
            from pack_install import install_saved_module_pack
        except ImportError:
            from lms.pack_install import install_saved_module_pack
        install_saved_module_pack(
            school, int(offering_id), stored, dest_root
        )

    def _pack_status_response(offering: dict[str, Any]):
        """Poll install status, resuming a saved pack whose worker died.

        Args:
            offering: Course offering row.
        """
        dest = _library_dest(offering)
        if pack_needs_resume(dest):
            _start_pack_install(
                int(offering["id"]), dest / "course.imscc", dest
            )
        return jsonify(status_payload(offering, dest))

    def _annotate_pack_status(offering: dict[str, Any]) -> dict[str, Any]:
        """Attach badge/line/busy fields from ``install_status.json``.

        Args:
            offering: Course offering dict.
        """
        item = dict(offering)
        status = read_pack_status(_library_dest(item))
        ui = pack_ui_state(status, has_library=bool(item.get("library_id")))
        item["pack_busy"] = ui["busy"]
        item["pack_badge"] = ui["badge"]
        item["pack_badge_class"] = ui["badge_class"]
        item["pack_line"] = ui["line"]
        item["pack_stage"] = ui["stage"]
        item["pack_detail"] = ui["detail"]
        item["pack_error"] = ui["error"]
        return item

    def _library_dest(offering: dict[str, Any]) -> Path:
        """Shared library folder used for IT upload progress, with leftover fallback.

        Args:
            offering: Offering row (``library_id`` preferred).
        """
        try:
            from instances import library_root
        except ImportError:
            from lms.instances import library_root

        lib_id = offering.get("library_id")
        if lib_id:
            return library_root(school.data_dir, int(lib_id))
        return _pack_dest_root(school, offering)

    def _landing_resume_from_token(token: str):
        """Resume a student from the rejoin cookie, or render landing.

        Args:
            token: httpOnly rejoin visit token.

        Returns:
            A redirect into the live class, or a landing response when the
            token is no longer an active attendee.

        Raises:
            LivePresenceUnavailable: Presence could not confirm the token.
            The caller keeps the cookie and shows landing.
        """
        resolved = school.resolve_student_visit_token(
            token, allow_left=True
        )
        if resolved is None:
            resp = make_response(
                render_template(
                    "landing.html",
                    **landing_kwargs(one_tap_auto=False),
                )
            )
            clear_rejoin_cookie(resp)
            return resp
        attendee = school.touch_live_session_heartbeat(token)
        if attendee is None:
            resp = make_response(
                render_template(
                    "landing.html",
                    **landing_kwargs(one_tap_auto=False),
                )
            )
            clear_rejoin_cookie(resp)
            return resp
        session_row = resolved["session"]
        class_id = int(resolved["class_id"])
        try:
            cls = school.game.get_class(class_id)
            offering = school.get_offering(int(session_row["offering_id"]))
        except (KeyError, TypeError):
            resp = make_response(
                render_template(
                    "landing.html",
                    **landing_kwargs(one_tap_auto=False),
                )
            )
            clear_rejoin_cookie(resp)
            return resp
        sid = resolved.get("student_id")
        unmatched = bool(resolved.get("unmatched")) or sid in (None, "")
        student = None
        if not unmatched and sid not in (None, ""):
            try:
                student = school.game.get_student(class_id, int(sid))
            except (KeyError, TypeError):
                student = None
                unmatched = True
        display = str(
            attendee.get("codename")
            or (student or {}).get("codename")
            or ""
        )
        bind_student_session(
            session,
            offering,
            cls,
            student
            or {"id": None, "codename": display, "first_name": display},
            live_session_id=int(resolved["live_session_id"]),
            session_code=str(session_row.get("session_code") or ""),
            visit_token=token,
            participant_uuid=str(
                resolved.get("participant_uuid")
                or attendee.get("participant_uuid")
                or ""
            ),
            unmatched=unmatched,
        )
        endpoint = next_student_endpoint(
            school,
            class_id,
            int(sid) if sid not in (None, "") else None,
            visit_token=token,
            unmatched=unmatched,
        )
        resp = redirect(student_url_with_token(endpoint, token))
        set_rejoin_cookie(resp, token)
        return resp

    @bp.route("/")
    def landing():
        """Public ALC logo, then Teacher / Student / Admin entry.

        Celebrations live on the same page at ``/#celebrations``. The board
        itself loads from ``GET /api/celebrations`` the first time that hash
        opens, so a plain ``/`` hit does not scan class stats.
        While a live session is active, the httpOnly rejoin cookie skips
        code+name and resumes the unfinished join step (mood → character →
        home). It must not skip the required avatar pick.
        """
        token = rejoin_token_from_cookie()
        if token:
            try:
                return _landing_resume_from_token(token)
            except Exception as exc:
                from live_presence import LivePresenceUnavailable, note_presence_blip

                if not isinstance(exc, LivePresenceUnavailable):
                    raise
                # Keep the rejoin cookie. A presence blip is not Leave.
                note_presence_blip("landing auto-resume skipped")
        return render_template(
            "landing.html",
            **landing_kwargs(
                one_tap_auto=False,
                student_code=request.args.get("student_code") or request.args.get("code") or "",
            ),
        )

    @bp.route("/request-access", methods=["GET", "POST"])
    def request_access():
        """Capture a signup request. Never collects payment or auto-allowlists.

        New answers and the five campaign tags are length-capped and stored
        on the request row. Field values are not logged.
        """
        from school_db import access_campaign_tags, limit_access_text

        error = None
        submitted = False
        form = {
            "name": "",
            "email": "",
            "role": "parent",
            "organization": "",
            "context": "",
            "grade_band": "",
            "subject": "",
            "live_classes_per_week": "",
            "heard_about": "",
        }
        incoming = request.form if request.method == "POST" else request.args
        if request.method == "POST":
            honeypot = (request.form.get("website") or "").strip()
            form["name"] = (request.form.get("name") or "").strip()
            form["email"] = (request.form.get("email") or "").strip()
            form["role"] = (request.form.get("role") or "parent").strip()
            form["organization"] = (request.form.get("organization") or "").strip()
            form["context"] = (request.form.get("context") or "").strip()
            form["grade_band"] = limit_access_text(request.form.get("grade_band"), 16)
            form["subject"] = limit_access_text(request.form.get("subject"))
            form["live_classes_per_week"] = limit_access_text(
                request.form.get("live_classes_per_week"), 16
            )
            form["heard_about"] = limit_access_text(request.form.get("heard_about"))
            if honeypot:
                submitted = True
            else:
                try:
                    tags = access_campaign_tags(request.form)
                    row = school.create_access_request(
                        name=form["name"],
                        email=form["email"],
                        role=form["role"],
                        organization=form["organization"],
                        context=form["context"],
                        grade_band=form["grade_band"],
                        subject=form["subject"],
                        live_classes_per_week=form["live_classes_per_week"],
                        heard_about=form["heard_about"],
                        utm_source=tags["utm_source"],
                        utm_medium=tags["utm_medium"],
                        utm_campaign=tags["utm_campaign"],
                        utm_term=tags["utm_term"],
                        utm_content=tags["utm_content"],
                    )
                    try:
                        email_service.send_access_request_notice(row)
                    except Exception:  # noqa: BLE001 - form still succeeds
                        logger.exception("Access-request notice email failed")
                    submitted = True
                except ValueError as exc:
                    error = str(exc)
        return render_template(
            "request_access.html",
            error=error,
            submitted=submitted,
            form=form,
            access_utm=access_campaign_tags(incoming),
        )

    def _public_legal_page(kind: str):
        """Render the public privacy, terms, or support page.

        Args:
            kind: ``privacy``, ``terms``, or ``support``.

        Returns:
            HTML response with ``X-Robots-Tag: noindex``.
        """
        titles = {
            "privacy": "Privacy Notice",
            "terms": "Terms of Use",
            "support": "Support",
        }
        if kind not in titles:
            abort(404)
        resp = make_response(
            render_template(
                "legal_page.html",
                page_kind=kind,
                page_title=titles[kind],
            )
        )
        resp.headers["X-Robots-Tag"] = "noindex"
        return resp

    @bp.route("/privacy")
    def privacy():
        """Public privacy notice."""
        return _public_legal_page("privacy")

    @bp.route("/terms")
    def terms():
        """Public terms of use."""
        return _public_legal_page("terms")

    @bp.route("/support")
    def support():
        """Public support page: how to get help and request access."""
        return _public_legal_page("support")

    @bp.route("/it")
    @it_required
    def it_dashboard():
        """IT: activate semester, register staff, assign Ontario courses."""
        user = current_user()
        semesters = school.list_semesters()
        active = school.get_active_semester()
        tenant_id = school.tenant_id_of(user)
        offerings = school.list_offerings(
            semester_id=active["id"] if active else None,
            tenant_id=tenant_id,
        )
        all_offerings = school.list_offerings(tenant_id=tenant_id)
        offerings = [
            annotate_offering_pack(row, _library_dest(row)) for row in offerings
        ]
        all_offerings = [
            annotate_offering_pack(row, _library_dest(row)) for row in all_offerings
        ]
        from flask import make_response

        html = render_template(
            "it/dashboard.html",
            user=user,
            semesters=semesters,
            semesters_all=semesters,
            active=active,
            staff=school.list_staff(include_archived=True, tenant_id=tenant_id),
            offerings=offerings,
            all_offerings=all_offerings,
            audit_events=school.list_access_events(tenant_id=tenant_id, limit=100),
            access_requests=school.list_access_requests(status="pending"),
            courses=school.search_ontario_courses("", limit=300),
            school_name=SCHOOL_NAME,
            only_live_class_days=school.only_live_class_days(),
            staff_2fa_mode=school.staff_2fa_mode(),
            staff_2fa_modes=STAFF_2FA_MODE_LABELS,
            invite_presets=onboarding_preset_lists(school),
            invite_flash=session.pop("invite_flash", None),
            invite_tabs=staff_invites.invite_tabs(school, tenant_id),
        )
        resp = make_response(html)
        resp.set_cookie("lloves_seen", "1", max_age=86400 * 400, samesite="Lax")
        return resp

    def _invite_link(token: str) -> str:
        """Absolute ``/invite/<token>`` URL (``PUBLIC_APP_URL`` on Fly)."""
        base = (os.getenv("PUBLIC_APP_URL") or "").strip().rstrip("/")
        if not base:
            base = request.host_url.rstrip("/")
        return f"{base}/invite/{token}"

    def _invite_reply_to(actor: dict[str, Any] | None) -> str | None:
        """Reply-To for invites: ``INVITE_REPLY_TO``, else the inviting Admin."""
        configured = (os.getenv("INVITE_REPLY_TO") or "").strip()
        if configured:
            return configured
        return str((actor or {}).get("email") or "").strip() or None

    def _send_invite_email(
        invite: dict[str, Any], token: str, actor: dict[str, Any] | None
    ) -> bool:
        """Send one invite email and log it for the rate limit."""
        link = _invite_link(token)
        try:
            delivered = email_service.send_staff_invite(
                str(invite["email"]),
                str(invite.get("first_name") or ""),
                link,
                reply_to=_invite_reply_to(actor),
            )
        except Exception:  # noqa: BLE001 - a failed send still keeps the invite
            app.logger.exception("invite email failed")
            delivered = False
        staff_invites.record_send(
            school, int(invite["id"]), int(actor["id"]) if actor else None, delivered
        )
        return delivered

    @bp.route("/it/invites", methods=["POST"])
    @it_required
    def it_send_invite():
        """MCK-183 I2: allowlist a teacher, create her invite, email the link.

        Re-inviting the same email refreshes the open invite (new link, new
        7-day expiry) and sends it again. A failed email still saves the
        invite; the Admin page then offers Copy link.
        """
        actor = current_user()
        email = (request.form.get("email") or "").strip().lower()
        try:
            preset = onboarding_clean_preset(
                school,
                request.form.get("preset_code"),
                request.form.get("preset_days"),
                request.form.get("preset_time"),
                required=False,
            )
            existing = school.get_user_by_email(email) if "@" in email else None
            open_invite = None
            if existing:
                open_invite = staff_invites.open_invite_for(
                    school, int(existing.get("tenant_id") or 1), email
                )
                # #269 gate: never invite over a teacher who already has an
                # active account (only an invite's own pending account resends).
                if (
                    str(existing.get("role") or "") == "staff"
                    and not existing.get("archived_at")
                    and not (open_invite and int(open_invite.get("owns_account") or 0))
                ):
                    raise ValueError("That teacher already has an account and can sign in.")
            staff_invites.check_send_rate(
                school, int(actor["id"]) if actor else None, open_invite
            )
            invite, token = staff_invites.create_or_refresh_invite(
                school,
                email=email,
                first_name=request.form.get("first_name") or "",
                kind=request.form.get("kind") or "teacher",
                preset=preset,
                created_by_user_id=int(actor["id"]) if actor else None,
                tenant_id=school.tenant_id_of(actor),
            )
        except ValueError as exc:
            session["invite_flash"] = {"kind": "error", "message": str(exc)}
            return redirect(url_for("it_dashboard"))
        delivered = _send_invite_email(invite, token, actor)
        _audit(
            "invite.send",
            "staff",
            resource_id=invite.get("user_id"),
            detail={"invite_id": invite.get("id"), "delivered": delivered},
        )
        if delivered:
            session["invite_flash"] = {"kind": "sent", "email": invite["email"]}
        else:
            session["invite_flash"] = {
                "kind": "not_sent",
                "email": invite["email"],
                "link": _invite_link(token),
            }
        return redirect(url_for("it_dashboard"))

    def _admin_invite(invite_id: int) -> dict[str, Any]:
        """The invite, only if it is in the signed-in Admin's school."""
        row = staff_invites.get_invite(school, invite_id)
        if row is None or int(row.get("tenant_id") or 1) != school.tenant_id_of(current_user()):
            abort(404)
        return row

    @bp.route("/it/invites/<int:invite_id>/resend", methods=["POST"])
    @it_required
    def it_resend_invite(invite_id: int):
        """MCK-183 I4: new link + new 7-day expiry, emailed again (rate-limited)."""
        actor = current_user()
        row = _admin_invite(invite_id)
        if staff_invites.invite_status(row) not in {"pending", "expired"}:
            session["invite_flash"] = {"kind": "error", "message": "This invite can't be resent."}
            return redirect(url_for("it_dashboard", _anchor="invites"))
        try:
            staff_invites.check_send_rate(school, int(actor["id"]) if actor else None, row)
        except ValueError as exc:
            session["invite_flash"] = {"kind": "error", "message": str(exc)}
            return redirect(url_for("it_dashboard", _anchor="invites"))
        invite, token = staff_invites.rotate_token(school, invite_id)
        delivered = _send_invite_email(invite, token, actor)
        _audit(
            "invite.resend",
            "staff",
            resource_id=invite.get("user_id"),
            detail={"invite_id": invite_id, "delivered": delivered},
        )
        if delivered:
            session["invite_flash"] = {"kind": "sent", "email": invite["email"]}
        else:
            session["invite_flash"] = {
                "kind": "not_sent",
                "email": invite["email"],
                "link": _invite_link(token),
            }
        return redirect(url_for("it_dashboard", _anchor="invites"))

    @bp.route("/it/invites/<int:invite_id>/link", methods=["POST"])
    @it_required
    def it_invite_link(invite_id: int):
        """MCK-183 I4: Copy link. New token and expiry, no email.

        Only a hash is stored, so an old link can't be shown again; older
        links for this invite stop working.
        """
        row = _admin_invite(invite_id)
        if staff_invites.invite_status(row) not in {"pending", "expired"}:
            return jsonify({"error": "This invite has no link to copy."}), 409
        invite, token = staff_invites.rotate_token(school, invite_id)
        _audit("invite.link", "staff", resource_id=invite.get("user_id"),
               detail={"invite_id": invite_id})
        return jsonify({"link": _invite_link(token)})

    @bp.route("/it/invites/<int:invite_id>/revoke", methods=["POST"])
    @it_required
    def it_revoke_invite(invite_id: int):
        """MCK-183 I4: the link stops working and the allowlist row is archived."""
        actor = current_user()
        row = _admin_invite(invite_id)
        if staff_invites.invite_status(row) not in {"pending", "expired"}:
            session["invite_flash"] = {"kind": "error", "message": "This invite can't be revoked."}
            return redirect(url_for("it_dashboard", _anchor="invites"))
        staff_invites.revoke_invite(school, invite_id, int(actor["id"]))
        _audit("invite.revoke", "staff", resource_id=row.get("user_id"),
               detail={"invite_id": invite_id})
        session["invite_flash"] = {"kind": "revoked", "first": row.get("first_name") or row.get("email")}
        return redirect(url_for("it_dashboard", _anchor="invites"))

    @bp.route("/it/audit.csv")
    @it_required
    def it_audit_csv():
        """Download recent access/admin events for the signed-in IT tenant."""
        import csv
        from io import StringIO

        user = current_user()
        rows = school.list_access_events(
            tenant_id=school.tenant_id_of(user), limit=2000
        )
        buf = StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "created_at",
                "actor_email",
                "actor_role",
                "action",
                "resource_type",
                "resource_id",
                "student_id",
                "ip",
                "detail_json",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.get("created_at") or "",
                    row.get("actor_email") or "",
                    row.get("actor_role") or "",
                    row.get("action") or "",
                    row.get("resource_type") or "",
                    row.get("resource_id") or "",
                    row.get("student_id") or "",
                    row.get("ip") or "",
                    row.get("detail_json") or "",
                ]
            )
        return Response(
            buf.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=lloves-access-audit.csv"
            },
        )

    @bp.route("/it/semesters/activate", methods=["POST"])
    @it_required
    def it_activate_semester():
        """Clone ``frameworks/semester.json`` (or switch an existing row)."""
        existing_id = request.form.get("semester_id")
        if existing_id:
            school.set_active_semester(int(existing_id))
        else:
            school.activate_from_semester_json()
        return redirect(url_for("it_dashboard"))

    @bp.route("/api/it/settings", methods=["GET", "POST"])
    @it_required
    def it_settings_api():
        """Read or update school-wide Admin settings."""
        if request.method == "GET":
            return jsonify(
                {
                    "ok": True,
                    "only_live_class_days": school.only_live_class_days(),
                    "staff_2fa_mode": school.staff_2fa_mode(),
                }
            )
        body = request.get_json(silent=True) or {}
        enabled = body.get("only_live_class_days")
        mode_raw = body.get("staff_2fa_mode")
        if enabled is None and mode_raw is None:
            return jsonify(
                {
                    "ok": False,
                    "error": "only_live_class_days or staff_2fa_mode is required",
                }
            ), 400
        if enabled is not None:
            flag = (
                bool(enabled)
                if not isinstance(enabled, str)
                else enabled.strip().lower() in {
                    "1",
                    "true",
                    "yes",
                    "on",
                }
            )
            school.set_only_live_class_days(flag)
        if mode_raw is not None:
            try:
                school.set_staff_2fa_mode(str(mode_raw))
            except ValueError as exc:
                return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify(
            {
                "ok": True,
                "only_live_class_days": school.only_live_class_days(),
                "staff_2fa_mode": school.staff_2fa_mode(),
            }
        )

    @bp.route("/api/it/live-problems", methods=["GET", "POST"])
    @it_required
    def api_it_live_problems():
        """List or create curated live-class problems."""
        if request.method == "GET":
            code = (request.args.get("ontario_code") or "").strip() or None
            return jsonify(
                {
                    "ok": True,
                    "problems": school.list_live_problems(
                        ontario_code=code, active_only=False
                    ),
                    "processes": school.list_math_processes(),
                }
            )
        body = request.get_json(silent=True) or {}
        row = school.upsert_live_problem(body)
        return jsonify({"ok": True, "problem": row})

    @bp.route("/api/it/live-problems/<int:problem_id>", methods=["PATCH", "DELETE"])
    @it_required
    def api_it_live_problem_one(problem_id: int):
        """Update, deactivate, or replace one live problem."""
        existing = school.get_live_problem(problem_id)
        if existing is None:
            return jsonify({"ok": False, "error": "Not found"}), 404
        if request.method == "DELETE":
            school.set_live_problem_active(problem_id, False)
            return jsonify({"ok": True, "problem": school.get_live_problem(problem_id)})
        body = request.get_json(silent=True) or {}
        body["id"] = problem_id
        if "active" in body and len(body) == 2:
            row = school.set_live_problem_active(problem_id, bool(body["active"]))
            return jsonify({"ok": True, "problem": row})
        merged = {**existing, **body}
        return jsonify({"ok": True, "problem": school.upsert_live_problem(merged)})

    @bp.route("/api/it/quick-phrases", methods=["GET", "POST"])
    @it_required
    def api_it_quick_phrases():
        """List or create process-evidence phrases."""
        if request.method == "GET":
            return jsonify(
                {
                    "ok": True,
                    "phrases": school.list_quick_phrases(active_only=False),
                    "processes": school.list_math_processes(),
                }
            )
        body = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "phrase": school.upsert_quick_phrase(body)})

    @bp.route("/api/it/quick-phrases/<int:phrase_id>", methods=["PATCH"])
    @it_required
    def api_it_quick_phrase_one(phrase_id: int):
        """Update or deactivate one quick-evidence phrase."""
        body = request.get_json(silent=True) or {}
        body["id"] = phrase_id
        existing = next(
            (p for p in school.list_quick_phrases(active_only=False) if int(p["id"]) == phrase_id),
            None,
        )
        if existing is None:
            return jsonify({"ok": False, "error": "Not found"}), 404
        merged = {**existing, **body}
        return jsonify({"ok": True, "phrase": school.upsert_quick_phrase(merged)})

    @bp.route("/api/it/live-sessions/<int:session_id>/end", methods=["POST"])
    @it_required
    def api_it_end_live_session(session_id: int):
        """Force-end one stuck live class session from the IT dashboard.

        Args:
            session_id: ``live_class_sessions.id`` to terminate.
        """
        ended = school.end_live_class_session(int(session_id))
        if ended is None:
            return jsonify(
                {"ok": False, "error": "Session not found or already ended"}
            ), 404
        return jsonify({"ok": True, "session": ended})

    @bp.route("/api/it/live-sessions/end-all", methods=["POST"])
    @it_required
    def api_it_end_all_live_sessions():
        """Force-end every active live class session (stuck cleanup)."""
        ended = school.end_all_active_live_sessions()
        return jsonify(
            {
                "ok": True,
                "ended_count": len(ended),
                "sessions": ended,
            }
        )

    @bp.route("/it/staff", methods=["POST"])
    @it_required
    def it_register_staff():
        """Allowlist a personal Google email as staff."""
        try:
            actor = current_user()
            created = school.register_staff(
                request.form.get("email") or "",
                request.form.get("display_name") or None,
                tenant_id=school.tenant_id_of(actor),
            )
            _audit(
                "staff.register",
                "staff",
                resource_id=created.get("id"),
                detail={"email": created.get("email")},
            )
        except ValueError as exc:
            return render_template(
                "forbidden.html", message=str(exc)
            ), 400
        return redirect(url_for("it_dashboard"))

    @bp.route("/it/access-requests/<int:request_id>/review", methods=["POST"])
    @it_required
    def it_review_access_request(request_id: int):
        """Mark a signup request reviewed without creating a user."""
        actor = current_user()
        school.mark_access_request_reviewed(request_id, int(actor["id"]))
        return redirect(url_for("it_dashboard"))

    @bp.route("/it/courses")
    @it_required
    def it_search_courses():
        """JSON autocomplete for Ontario course codes."""
        q = request.args.get("q") or ""
        return jsonify({"ok": True, "courses": school.search_ontario_courses(q, limit=80)})

    @bp.route("/it/curriculum-expectations")
    @it_required
    def it_curriculum_expectations():
        """Local index of extracted Gr 11–12 math specific expectations."""
        from live_class_constants import (
            CURRICULUM_COURSE_DRIVE_FOLDERS,
            CURRICULUM_DRIVE_FOLDER_ID,
        )
        from math_expectations_pdf import default_local_dest

        dest = default_local_dest()
        index_path = dest / "index.json"
        courses = []
        if index_path.is_file():
            courses = json.loads(index_path.read_text(encoding="utf-8")).get(
                "courses"
            ) or []
        for row in courses:
            code = str(row.get("code") or "")
            folder_id = CURRICULUM_COURSE_DRIVE_FOLDERS.get(code)
            row["drive_folder_url"] = (
                "https://drive.google.com/drive/folders/" + folder_id
                if folder_id
                else ""
            )
        return render_template(
            "it/curriculum_expectations.html",
            courses=courses,
            drive_folder_url=(
                "https://drive.google.com/drive/folders/"
                + CURRICULUM_DRIVE_FOLDER_ID
            ),
            school_name=SCHOOL_NAME,
        )

    @bp.route("/it/curriculum-expectations/<code>")
    @it_required
    def it_curriculum_expectation_course(code: str):
        """Local list of specific statements for one Ontario math code."""
        from live_class_constants import CURRICULUM_COURSE_DRIVE_FOLDERS
        from math_expectations_pdf import default_local_dest

        safe = (code or "").strip().upper()
        path = default_local_dest() / safe / f"{safe}-specific-expectations.json"
        if not path.is_file():
            abort(404)
        payload = json.loads(path.read_text(encoding="utf-8"))
        folder_id = CURRICULUM_COURSE_DRIVE_FOLDERS.get(safe)
        return render_template(
            "it/curriculum_expectation_course.html",
            payload=payload,
            drive_folder_url=(
                "https://drive.google.com/drive/folders/" + folder_id
                if folder_id
                else ""
            ),
            school_name=SCHOOL_NAME,
        )

    @bp.route("/it/instances")
    @it_required
    def it_list_instances():
        """JSON: prior offerings of a course code (any semester, any teacher)."""
        code = (request.args.get("code") or "").strip().upper()
        return jsonify(instances_payload(school, code))

    def _upload_file() -> Any:
        """Return the IMSCC ``FileStorage`` when the IT form sent a real file."""
        uploaded = request.files.get("module_pack")
        if uploaded is None or not getattr(uploaded, "filename", None):
            return None
        if not str(uploaded.filename).strip():
            return None
        return uploaded

    def _assign_error(message: str, status: int = 400):
        """Return a JSON or HTML error for IT assign / pack upload forms."""
        if _wants_json():
            return jsonify({"ok": False, "error": message}), status
        return render_template("forbidden.html", message=message), status

    def _start_pack_install(
        offering_id: int, stored: Path, dest_root: Path
    ) -> bool:
        """Run pack install sync under TESTING, otherwise in a daemon thread.

        Args:
            offering_id: ``course_offerings.id``.
            stored: Path to ``course.imscc`` already on disk.
            dest_root: Shared library folder for unpack/inventory.

        Returns:
            True when install continues in the background (UI should poll).
        """
        if app.config.get("TESTING"):
            _install_module_pack_job(int(offering_id), stored, dest_root)
            return False
        worker = threading.Thread(
            target=_install_module_pack_job,
            args=(int(offering_id), stored, dest_root),
            daemon=True,
        )
        worker.start()
        return True

    def _pack_upload_success(
        *,
        offering_id: int,
        redirect_url: str,
        stored: Path | None = None,
        dest_root: Path | None = None,
    ):
        """Finish an IT assign/replace after optional pack store.

        Outside tests, unpack/inventory always runs in the background so the
        HTTP request returns as soon as the file is on disk.
        """
        installing = False
        status_url = None
        if stored is not None and dest_root is not None:
            installing = _start_pack_install(int(offering_id), stored, dest_root)
            status_url = url_for(
                "it_module_pack_status", offering_id=int(offering_id)
            )
        if _wants_json():
            payload: dict[str, Any] = {
                "ok": True,
                "installing": installing,
                "redirect": redirect_url,
            }
            if status_url:
                payload["status_url"] = status_url
            return jsonify(payload)
        return redirect(redirect_url)

    @bp.route("/it/offerings", methods=["POST"])
    @it_required
    def it_assign_course():
        """Assign a catalog course; optional IMSCC becomes a new shared library.

        Assigning a code a teacher already holds adds another section
        (``MCF3M-2``) rather than silently reusing the first one.
        """
        teacher_id = int(request.form.get("teacher_user_id") or 0)
        actor = current_user()
        teacher = school.get_user(teacher_id)
        if not actor or not teacher or not school.same_tenant(actor, teacher):
            return _assign_error("Ask Admin to grant access.", 403)
        code = (request.form.get("ontario_code") or "").strip().upper()
        raw_base = (request.form.get("copied_from_offering_id") or "").strip()
        copied_from = int(raw_base) if raw_base else None
        uploaded = _upload_file()
        library_id = None
        dest_root = None
        stored = None
        try:
            roster_names = _roster_codenames_from_assign_form()
            theme_template, theme_style = _assign_slides_theme_from_form()
            if uploaded is not None:
                created = school.store_upload_library(code, uploaded)
                library_id = int(created["library"]["id"])
                dest_root = created["dest_root"]
                stored = created["stored"]
            offering = school.assign_course(
                teacher_user_id=teacher_id,
                ontario_code=code,
                copied_from_offering_id=copied_from,
                library_id=library_id,
                new_section=True,
                slides_template_id=theme_template,
                style_guide_file_id=theme_style,
            )
            _audit(
                "offering.assign",
                "offering",
                resource_id=int(offering["id"]),
                detail={"ontario_code": code, "teacher_user_id": teacher_id},
            )
            live_days, live_time, live_end_time = _assign_schedule_from_form()
            offering = school.set_offering_schedule(
                int(offering["id"]),
                live_days=live_days,
                live_time=live_time,
                live_end_time=live_end_time,
            )
            if roster_names:
                _populate_offering_class(
                    offering,
                    teacher_user_id=teacher_id,
                    codenames=roster_names,
                )
        except (ValueError, KeyError) as exc:
            return _assign_error(str(exc))
        return _pack_upload_success(
            offering_id=int(offering["id"]),
            redirect_url=url_for("it_dashboard", tab="offerings"),
            stored=stored,
            dest_root=dest_root,
        )

    @bp.route("/it/offerings/<int:offering_id>/module-pack", methods=["POST"])
    @it_required
    def it_upload_module_pack(offering_id: int):
        """Attach a new shared library to an existing offering (IT only)."""
        offering = school.get_offering(int(offering_id))
        uploaded = _upload_file()
        if uploaded is None:
            return _assign_error("Choose a .imscc module pack to upload.")
        try:
            created = school.store_upload_library(
                str(offering["ontario_code"]),
                uploaded,
                offering_id=int(offering["id"]),
            )
        except ValueError as exc:
            return _assign_error(str(exc))
        return _pack_upload_success(
            offering_id=int(offering["id"]),
            redirect_url=url_for("it_dashboard", tab="offerings", pack="ok"),
            stored=created["stored"],
            dest_root=created["dest_root"],
        )

    @bp.route(
        "/it/offerings/<int:offering_id>/module-pack/backfill",
        methods=["POST"],
    )
    @it_required
    def it_backfill_module_pack(offering_id: int):
        """Merge a module pack into the library this course already uses.

        Missing rows from the cartridge are added to the pack library when
        the course has one, otherwise to the course shell. Decks, slides,
        and questions already stored are left as they are. Roster, PIN, and
        attendance tables are not written.

        Args:
            offering_id: ``course_offerings.id``.
        """
        if not _same_origin_post():
            return "Forbidden", 403
        try:
            offering = school.get_offering(int(offering_id))
        except KeyError:
            abort(404)
        uploaded = _upload_file()
        if uploaded is None:
            return _assign_error("Choose a .imscc module pack to upload.")
        try:
            counts = backfill_module_pack(school, int(offering["id"]), uploaded)
        except ValueError as exc:
            return _assign_error(str(exc))
        _audit(
            "offering.pack_backfill",
            "offering",
            resource_id=int(offering["id"]),
            detail={
                "pages": int(counts.get("pages") or 0),
                "questions": int(counts.get("questions") or 0),
            },
        )
        return redirect(url_for("it_dashboard", tab="offerings"))

    @bp.route("/it/offerings/<int:offering_id>/module-pack/status")
    @it_required
    def it_module_pack_status(offering_id: int):
        """JSON progress for an IT library upload/unpack."""
        offering = school.get_offering(int(offering_id))
        return _pack_status_response(offering)

    @bp.route("/it/offerings/<int:offering_id>/archive", methods=["POST"])
    @it_required
    def it_archive_offering(offering_id: int):
        """Soft-archive an offering so it no longer appears on the teacher's dashboard."""
        try:
            school.archive_offering(offering_id)
        except KeyError:
            abort(404)
        return redirect(url_for("it_dashboard", tab="offerings"))

    @bp.route("/it/offerings/<int:offering_id>/unarchive", methods=["POST"])
    @it_required
    def it_unarchive_offering(offering_id: int):
        """Restore an archived offering so it reappears on the teacher's dashboard."""
        try:
            school.unarchive_offering(offering_id)
        except KeyError:
            abort(404)
        return redirect(url_for("it_dashboard", tab="offerings"))

    @bp.route("/it/staff/<int:staff_id>/history")
    @it_required
    def it_staff_history(staff_id: int):
        """Return a JSON history of all offerings grouped by semester for one staff member.

        Returns:
            JSON: ``{"ok": true, "history": [...]}`` where each entry has
            ``semester_label``, ``ontario_code``, ``section_code``,
            ``course_title``, ``roster_size``, ``library_id``, ``has_pack``.
        """
        from collections import defaultdict

        try:
            from lms.instances import offering_has_pack
        except ImportError:
            try:
                from instances import offering_has_pack
            except ImportError:
                def offering_has_pack(_data_dir, _offering):
                    return False

        offerings = school.list_offerings(teacher_user_id=staff_id)
        grouped: dict[str, list[dict]] = defaultdict(list)
        for o in offerings:
            sem = str(o.get("semester_label") or "")
            grouped[sem].append(
                {
                    "semester_label": sem,
                    "ontario_code": o.get("ontario_code"),
                    "section_code": o.get("section_code"),
                    "course_title": o.get("course_title"),
                    "roster_size": int(o.get("roster_size") or 0),
                    "library_id": o.get("library_id"),
                    "has_pack": bool(o.get("library_id"))
                    or offering_has_pack(school.data_dir, o),
                }
            )
        history = [item for items in grouped.values() for item in items]
        return jsonify({"ok": True, "history": history})

    @bp.route("/it/staff/<int:staff_id>/rename", methods=["POST"])
    @it_required
    def it_staff_rename(staff_id: int):
        """Rename a staff member's display name.

        Form field:
            display_name: New display name (must be non-blank).

        Returns:
            Redirect to ``it_dashboard?tab=staff`` on success, or 400 on error.
        """
        display_name = (request.form.get("display_name") or "").strip()
        try:
            school.rename_staff(staff_id, display_name)
        except ValueError as exc:
            return render_template("forbidden.html", message=str(exc)), 400
        return redirect(url_for("it_dashboard", tab="staff"))

    @bp.route("/it/staff/<int:staff_id>/deactivate", methods=["POST"])
    @it_required
    def it_staff_deactivate(staff_id: int):
        """Soft-deactivate a staff member (set archived_at).

        Guards against self-deactivation and IT-role accounts. On success
        redirects to the staff tab of the IT dashboard.

        Returns:
            Redirect to ``it_dashboard?tab=staff`` on success, or 400 on error.
        """
        actor = current_user()
        target = school.get_user(staff_id)
        if not actor or not target or not school.same_tenant(actor, target):
            abort(403)
        actor_id = int(actor["id"])
        try:
            school.deactivate_staff(staff_id, actor_id)
            _audit("staff.deactivate", "staff", resource_id=staff_id)
        except ValueError as exc:
            return render_template("forbidden.html", message=str(exc)), 400
        return redirect(url_for("it_dashboard", tab="staff"))

    @bp.route("/it/staff/<int:staff_id>/reactivate", methods=["POST"])
    @it_required
    def it_staff_reactivate(staff_id: int):
        """Clear archived_at, restoring login access for a staff member.

        Returns:
            Redirect to ``it_dashboard?tab=staff`` on success, or 400 on error.
        """
        actor = current_user()
        target = school.get_user(staff_id)
        if not actor or not target or not school.same_tenant(actor, target):
            abort(403)
        try:
            school.reactivate_staff(staff_id)
            _audit("staff.reactivate", "staff", resource_id=staff_id)
        except ValueError as exc:
            return render_template("forbidden.html", message=str(exc)), 400
        return redirect(url_for("it_dashboard", tab="staff"))

    @bp.route("/it/staff/<int:staff_id>/delete", methods=["POST"])
    @it_required
    def it_staff_delete(staff_id: int):
        """Permanently delete a staff member and their offerings.

        Frees the email for re-registration. Shared module packs stay. Cannot
        delete IT accounts or the signed-in actor. Requires ``confirm_email``
        in the form body to match the staff member's email.

        Returns:
            Redirect to ``it_dashboard?tab=staff`` on success, or 400 on error.
        """
        actor = current_user()
        actor_id = int(actor["id"]) if actor else 0
        target = school.get_user(staff_id)
        if not actor or not target or not school.same_tenant(actor, target):
            abort(403)
        confirm = (request.form.get("confirm_email") or "").strip().lower()
        expected = str((target or {}).get("email") or "").strip().lower()
        if not target or not expected or confirm != expected:
            return (
                render_template(
                    "forbidden.html",
                    message="Type the staff email exactly to confirm permanent delete.",
                ),
                400,
            )
        try:
            school.delete_staff_permanently(staff_id, actor_id)
            _audit("staff.delete", "staff", resource_id=staff_id)
        except ValueError as exc:
            return render_template("forbidden.html", message=str(exc)), 400
        except sqlite3.Error as exc:
            # The delete rolls back. The page stays a normal error, and the
            # log is the error class only so an address cannot land in it.
            app.logger.warning(
                "staff permanent delete failed (%s)",
                type(exc).__name__,
            )
            return (
                render_template(
                    "forbidden.html",
                    message="The account could not be deleted. Nothing was changed.",
                ),
                400,
            )
        return redirect(url_for("it_dashboard", tab="staff"))

    @bp.route("/it/staff/<int:staff_id>/assign", methods=["GET", "POST"])
    @it_required
    def it_staff_assign(staff_id: int):
        """GET: render the assign-course page for one staff member.
        POST: assign the selected Ontario course to that staff member.

        On GET the template receives:
            staff_member, active semester, courses list, and an empty instances list.
        On POST behaves like ``it_assign_course`` but targets this staff member.

        Returns:
            Rendered ``it/assign.html`` on GET or error; redirect on POST success.
        """
        staff_member = school.get_user(staff_id)
        actor = current_user()
        if (
            staff_member is None
            or staff_member.get("role") != "staff"
            or not actor
            or not school.same_tenant(actor, staff_member)
        ):
            from flask import abort
            abort(404)

        if request.method == "GET":
            active = school.get_active_semester()
            courses = school.search_ontario_courses("", limit=300)
            return render_template(
                "it/assign.html",
                staff_member=staff_member,
                active=active,
                courses=courses,
                instances=[],
                day_options=wizard_defaults()["day_options"],
                time_options=list(TIME_OPTIONS),
                end_time_options=end_time_options(),
                school_name=SCHOOL_NAME,
            )

        code = (request.form.get("ontario_code") or "").strip().upper()
        raw_base = (request.form.get("copied_from_offering_id") or "").strip()
        copied_from = int(raw_base) if raw_base else None
        uploaded = _upload_file()
        library_id = None
        dest_root = None
        stored = None
        try:
            roster_names = _roster_codenames_from_assign_form()
            theme_template, theme_style = _assign_slides_theme_from_form()
            if uploaded is not None:
                created = school.store_upload_library(code, uploaded)
                library_id = int(created["library"]["id"])
                dest_root = created["dest_root"]
                stored = created["stored"]
            offering = school.assign_course(
                teacher_user_id=staff_id,
                ontario_code=code,
                copied_from_offering_id=copied_from,
                library_id=library_id,
                new_section=True,
                slides_template_id=theme_template,
                style_guide_file_id=theme_style,
            )
            live_days, live_time, live_end_time = _assign_schedule_from_form()
            offering = school.set_offering_schedule(
                int(offering["id"]),
                live_days=live_days,
                live_time=live_time,
                live_end_time=live_end_time,
            )
            if roster_names:
                _populate_offering_class(
                    offering,
                    teacher_user_id=staff_id,
                    codenames=roster_names,
                )
        except (ValueError, KeyError) as exc:
            return _assign_error(str(exc))
        return _pack_upload_success(
            offering_id=int(offering["id"]),
            redirect_url=url_for("it_dashboard", tab="offerings"),
            stored=stored,
            dest_root=dest_root,
        )

    @bp.route("/it/offerings/<int:offering_id>/pack")
    @it_required
    def it_offering_pack(offering_id: int):
        """Render the assign page in replace-pack mode for an existing offering.

        The template receives ``replace_offering`` so it can show only the file
        upload field under a "Replace pack" heading with all assign fields hidden.

        Returns:
            Rendered ``it/assign.html`` with ``replace_offering`` set.
        """
        try:
            replace_offering = school.get_offering(offering_id)
        except KeyError:
            from flask import abort
            abort(404)
        return render_template(
            "it/assign.html",
            replace_offering=replace_offering,
            school_name=SCHOOL_NAME,
        )

    #: Shawn's accounts when ``SENTRY_OWNER_EMAILS`` is unset (fly.toml sets it).
    OWNER_EMAILS_DEFAULT = ("shawnmckenzie11.sm@gmail.com", "solutions@mckenzian.com")

    def _owner_emails() -> set[str]:
        """Shawn's own sign-in emails (the onboarding tour never auto-opens for him)."""
        raw = (os.getenv("SENTRY_OWNER_EMAILS") or "").strip()
        values = raw.split(",") if raw else list(OWNER_EMAILS_DEFAULT)
        return {v.strip().lower() for v in values if v.strip()}

    _simple_course = simple_course

    app.jinja_env.globals["simple_course"] = _simple_course

    def _class_is_simple(class_id: int) -> bool:
        """Is this class's course a plain course (non-math, no pack)?"""
        return class_is_simple(school, class_id)

    def _roster_message(exc: BaseException, simple: bool) -> str:
        """Roster validation text, in first-names wording on plain courses."""
        return plain_name_wording(str(exc)) if simple else str(exc)

    def _tour_offer(user: dict[str, Any], classes: list[dict[str, Any]]) -> bool:
        """May the Dashboard tour open by itself for this teacher?

        Staff teachers with a class, never Admin accounts or Shawn. New
        teachers arrive from the names step; existing teachers get it once
        (the browser remembers Skip or Done per teacher).
        """
        if str(user.get("role") or "") != "staff" or not classes:
            return False
        return str(user.get("email") or "").strip().lower() not in _owner_emails()

    def _tour_template(
        *,
        page: str,
        roster: int,
        live_session: dict[str, Any] | None,
        class_id: int | None,
        view: str = "",
        block: bool = False,
    ) -> dict[str, Any]:
        """Template values for the spotlight tour and Getting started checklist.

        ``tour_run_type`` stays empty until MCK-269 stores Test or Live on
        the session. Show-me links are blank when the teacher has no class.
        Who is offered the tour stays in ``_tour_offer``.

        Args:
            page: ``dashboard``, ``live``, ``ap``, ``question_bank``, or ``course``.
            roster: Students already on a roster. The checklist ticks at 1+.
            live_session: Active live session, if one is running.
            class_id: Class the checklist links open.
            view: Attendance & Participation subview when ``page`` is ``ap``.
            block: True when a live class is running and this page must not dim.

        Returns:
            Keys the staff shell reads as ``data-tour-*``.
        """
        first = int(class_id) if class_id else None

        def href(tab: str, **extra: str) -> str:
            if not first:
                return ""
            return url_for("staff_course", class_id=first, tab=tab, **extra)

        return {
            "tour_page": page,
            "tour_view": view,
            "tour_roster": int(roster),
            "tour_live": live_session is not None,
            "tour_block": bool(block),
            "tour_run_type": tour_run_type(live_session),
            "tour_session": str((live_session or {}).get("id") or ""),
            "tour_dashboard": url_for("staff_home"),
            "tour_nav": url_for("staff_course", class_id=first) if first else "",
            "tour_qb": href("question-banks"),
            "tour_ap": href("ap", view="attendance"),
            "tour_part": href("ap", view="participation"),
            "tour_live_href": href("live"),
        }

    def _needs_first_run(
        user: dict[str, Any],
        offerings: list[dict[str, Any]],
        classes: list[dict[str, Any]],
    ) -> bool:
        """True for a staff teacher with nothing set up anywhere.

        No course and no class this semester, and none of these: no active
        semester, an active live session, or any non-archived class or
        course in any semester (#250 gate MED: never trap a set-up teacher,
        e.g. right after a semester rollover, on Welcome). Admin (IT)
        accounts never get it.

        Args:
            user: Signed-in user row.
            offerings: Her active-semester offerings.
            classes: Her active-semester classes.

        Returns:
            Whether ``/staff`` should send her to ``/staff/welcome``.
        """
        if str(user.get("role") or "") != "staff" or offerings or classes:
            return False
        active = school.get_active_semester()
        if not active:
            return False
        if school.get_active_live_session_for_teacher(int(user["id"])) is not None:
            return False
        elsewhere = school.teacher_setup_elsewhere(int(user["id"]), int(active["id"]))
        return not (elsewhere["any_class"] or elsewhere["other_offering"])

    def _needs_names_step(
        user: dict[str, Any],
        offerings: list[dict[str, Any]],
        classes: list[dict[str, Any]],
    ) -> bool:
        """True for a staff teacher with a course this semester and no class anywhere.

        Same guards as ``_needs_first_run`` (#250 gate MED): never with no
        active semester, during a live session, or once she has any
        non-archived class in any semester (she then uses Populate Class).

        Args:
            user: Signed-in user row.
            offerings: Her active-semester offerings.
            classes: Her active-semester classes.

        Returns:
            Whether ``/staff`` should send her to the names step.
        """
        if str(user.get("role") or "") != "staff" or not offerings or classes:
            return False
        active = school.get_active_semester()
        if not active:
            return False
        if school.get_active_live_session_for_teacher(int(user["id"])) is not None:
            return False
        return not school.teacher_setup_elsewhere(int(user["id"]), int(active["id"]))["any_class"]

    def _teacher_setup(user: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Her active-semester offerings and classes (empty with no semester)."""
        active = school.get_active_semester()
        if not active:
            return [], []
        offerings = school.list_offerings(
            teacher_user_id=int(user["id"]),
            semester_id=int(active["id"]),
            include_archived=False,
        )
        classes = school.list_staff_classes(int(user["id"]), int(active["id"]))
        return offerings, classes

    @bp.route("/staff/welcome")
    @staff_required
    def staff_welcome():
        """MCK-183 screen 1 (Welcome). Later steps arrive in their own slices.

        A teacher who already has a class goes to the Dashboard.
        """
        user = current_user()
        assert user is not None
        offerings, classes = _teacher_setup(user)
        step = (request.args.get("step") or "").strip().lower()
        if step not in {"class", "names"}:
            step = "welcome"
        names_offering = None
        names_next = url_for("staff_home", tour=1)
        if step == "names":
            pending = [o for o in offerings if not o.get("classes")]
            wanted = request.args.get("offering", type=int)
            names_offering = next(
                (o for o in pending if int(o["id"]) == wanted), pending[0] if pending else None
            )
            if names_offering is None:
                return redirect(url_for("staff_home") if classes else url_for("staff_welcome", step="class"))
            more = [o for o in pending if int(o["id"]) != int(names_offering["id"])]
            # Last course named: on to the Dashboard tour (slice D).
            names_next = (
                url_for("staff_welcome", step="names") if more else url_for("staff_home", tour=1)
            )
        elif classes:
            return redirect(url_for("staff_home"))
        return render_template(
            "staff/welcome.html",
            user=user,
            step=step,
            has_course=bool(offerings),
            presets=onboarding_preset_lists(school),
            class_rows=_onboarding_class_rows(user, offerings),
            names_offering=names_offering,
            names_next=names_next,
            school_name=SCHOOL_NAME,
        )

    def _onboarding_class_rows(
        user: dict[str, Any], offerings: list[dict[str, Any]]
    ) -> list[dict[str, str]]:
        """Screen 2 rows: her courses so far, else the invite preset, else one empty row."""
        rows = [
            {
                "code": str(o.get("ontario_code") or "").upper(),
                "days": str(o.get("live_days") or ""),
                "time": str(o.get("live_time") or ""),
            }
            for o in offerings
        ]
        if rows:
            return rows
        preset = staff_invites.accepted_preset_for(school, int(user["id"]))
        if preset:
            code, days, time_label = preset
            return [{"code": code, "days": days, "time": time_label}]
        return [{"code": "", "days": "", "time": ""}]

    #: Most classes one Next press may create (one per row).
    ONBOARDING_MAX_CLASSES = 6

    @bp.route("/api/staff/onboarding/course", methods=["POST"])
    @staff_required
    def api_staff_onboarding_course():
        """MCK-183 slice B: the teacher's own course(s) from screen 2.

        Body ``{"classes": [{"ontario_code", "live_days", "live_time"}, ...]}``.
        Every row must be a listed course, days preset and start time (Wonder's
        single error otherwise). Only during setup (no class yet). Each row
        becomes an offering for the active semester, created by the teacher
        with no module pack upload; a repeated code becomes a new section.
        Rows matching a course she already set up here (Back, then Next
        again) update its days and time instead of adding a section.
        """
        user = current_user()
        assert user is not None
        error = "Pick a course, days and a start time to continue."
        active = school.get_active_semester()
        if not active:
            return jsonify({"ok": False, "error": "No semester is active yet. Ask your ALC admin."}), 409
        offerings, classes = _teacher_setup(user)
        if classes:
            return jsonify({"ok": False, "error": "Your classes are already set up."}), 409
        payload = request.get_json(silent=True) or {}
        rows = payload.get("classes")
        if not isinstance(rows, list) or not rows or len(rows) > ONBOARDING_MAX_CLASSES:
            return jsonify({"ok": False, "error": error}), 400
        cleaned: list[tuple[str, str, str]] = []
        try:
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError(error)
                choice = onboarding_clean_preset(
                    school,
                    row.get("ontario_code"),
                    row.get("live_days"),
                    row.get("live_time"),
                    required=True,
                )
                assert choice is not None
                cleaned.append(choice)
        except ValueError:
            return jsonify({"ok": False, "error": error}), 400
        unclaimed = {}
        for offering in sorted(offerings, key=lambda o: int(o["id"])):
            unclaimed.setdefault(str(offering["ontario_code"]).upper(), []).append(offering)
        saved: list[dict[str, Any]] = []
        try:
            for code, days, time_label in cleaned:
                reuse = unclaimed.get(code) or []
                if reuse:
                    offering = reuse.pop(0)
                else:
                    held = school.get_offering_for(int(active["id"]), code, int(user["id"]))
                    offering = school.assign_course(
                        teacher_user_id=int(user["id"]),
                        ontario_code=code,
                        new_section=held is not None,
                    )
                    _audit(
                        "offering.self_assign",
                        "offering",
                        resource_id=int(offering["id"]),
                        detail={"ontario_code": code},
                    )
                offering = school.set_offering_schedule(
                    int(offering["id"]), live_days=days, live_time=time_label
                )
                saved.append(
                    {
                        "id": int(offering["id"]),
                        "ontario_code": code,
                        "live_days": days,
                        "live_time": time_label,
                    }
                )
        except (KeyError, ValueError) as exc:
            app.logger.warning("onboarding course failed: %s", exc)
            return jsonify({"ok": False, "error": error}), 400
        return jsonify(
            {"ok": True, "offerings": saved, "next": url_for("staff_welcome", step="names")}
        )

    @bp.route("/staff")
    @staff_required
    def staff_home():
        """Teacher course cards: populate, repopulate, or open a class."""
        user = current_user()
        assert user is not None
        active = school.get_active_semester()
        offerings = []
        classes = []
        if active:
            offerings = school.list_offerings(
                teacher_user_id=int(user["id"]),
                semester_id=int(active["id"]),
                include_archived=False,
            )
            offerings = [
                annotate_offering_pack(row, _library_dest(row)) for row in offerings
            ]
            for offering in offerings:
                for section in offering.get("classes") or []:
                    section["roster"] = school.roster_name_entries(
                        int(section["id"]),
                        include_test_students=_include_test_students(),
                    )
                    section["last_attendance"] = _format_last_taken(
                        school.latest_logged_attendance_date(int(section["id"]))
                    )
            classes = school.list_staff_classes(int(user["id"]), int(active["id"]))
        # MCK-183 first-run gate: a teacher with no course and no class gets
        # the Welcome setup instead of the "Ask Admin" dead end. Admin (IT)
        # accounts keep the Dashboard.
        if _needs_first_run(user, offerings, classes):
            return redirect(url_for("staff_welcome"))
        # Slice C: course chosen but no class list yet → the names step.
        if _needs_names_step(user, offerings, classes):
            return redirect(url_for("staff_welcome", step="names"))
        from flask import make_response

        active_live_session = school.get_active_live_session_for_teacher(
            int(user["id"])
        )
        # MCK-124: hold the What's new panel while any class this teacher
        # teaches is live (they may be projecting the Dashboard). Decided
        # here, so the page never carries the panel or its script.
        whats_new_hold = active_live_session is not None or any(
            school.get_active_live_session_for_class(int(row["id"])) is not None
            for row in classes
        )
        catchup_pin_alerts = school.catchup_pin_alerts_for_teacher(
            int(user["id"]), include_test_students=_include_test_students()
        )
        roster_n = 0
        for offering in offerings:
            for section in offering.get("classes") or []:
                roster_n += len(section.get("roster") or [])
        first_class = int(classes[0]["id"]) if classes else None
        tour = _tour_template(
            page="dashboard",
            roster=roster_n,
            live_session=active_live_session,
            class_id=first_class,
            block=whats_new_hold,
        )
        # While a class is live the Dashboard must not link the archived
        # class's pages (the End form is the only /staff/class/… URL).
        if whats_new_hold:
            for key in ("tour_nav", "tour_qb", "tour_ap", "tour_part", "tour_live_href"):
                tour[key] = ""
        html = render_template(
            "staff/home.html",
            user=user,
            active=active,
            offerings=offerings,
            classes=classes,
            active_live_session=active_live_session,
            catchup_pin_alerts=catchup_pin_alerts,
            whats_new_hold=whats_new_hold,
            show_test_students=_include_test_students(),
            tour_offer=_tour_offer(user, classes),
            **tour,
            plain_teacher=bool(offerings)
            and all(_simple_course(o.get("ontario_code"), o.get("library_id")) for o in offerings),
            tour_class_href=(
                url_for("staff_course", class_id=int(classes[0]["id"]), tab="ap", view="attendance", tour="done")
                if classes
                else ""
            ),
            nav_courses=_staff_nav_courses(int(user["id"])),
            time_options=list(TIME_OPTIONS),
            school_name=SCHOOL_NAME,
            celebration_candidates=celebration_candidates(
                school, int(user["id"])
            ),
            celebration_award=build_celebration_board(school)["cards"][0],
            celebration_periods=staff_award_periods(school, int(user["id"])),
            celebration_staff_copy=CELEBRATION_STAFF_COPY,
        )
        resp = make_response(html)
        resp.set_cookie("lloves_seen", "1", max_age=86400 * 400, samesite="Lax")
        return resp

    @bp.route("/api/staff/whats-new")
    @staff_required
    def api_staff_whats_new():
        """What's new history for the Dashboard panel (MCK-182 slice 2).

        Read from the ``whats_new_releases`` table, which keeps every entry
        that has shipped. Refs, SHAs and ticket ids never leave the server.
        """
        legacy = (request.args.get("legacy_seen") or "").strip()[:40] or None
        resp = jsonify(school.whats_new_teacher_payload(legacy_seen=legacy))
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @bp.route("/staff/bots")
    @staff_required
    def staff_bots():
        """IT/Admin-only Grok bots showcase (Module Engineer and later cards)."""
        user = current_user()
        assert user is not None
        email = str(user.get("email") or "").lower()
        if user.get("role") != "it" and email not in it_emails():
            return redirect(url_for("staff_home"))
        return render_template(
            "staff/bots.html",
            user=user,
            bots=list_bots(),
            nav_courses=_staff_nav_courses(int(user["id"])),
            school_name=SCHOOL_NAME,
        )

    @bp.route("/api/staff/celebration-award", methods=["GET", "POST"])
    @staff_required
    def staff_celebration_award():
        """Read or set the public Awards featured Codename."""
        user = current_user()
        assert user is not None
        if request.method == "GET":
            board = build_celebration_board(school)
            return jsonify(
                {
                    "ok": True,
                    "award": board["cards"][0],
                    "candidates": celebration_candidates(school, int(user["id"])),
                }
            )
        payload = request.get_json(silent=True) or {}
        clear = bool(payload.get("clear"))
        try:
            class_id = None if clear else int(payload.get("class_id"))
            student_id = None if clear else int(payload.get("student_id"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Pick a name."}), 400
        try:
            award = set_featured_award(
                school,
                teacher_user_id=int(user["id"]),
                class_id=class_id,
                student_id=student_id,
                blurb=str(payload.get("blurb") or ""),
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "award": award})

    @bp.route("/api/staff/celebrations/periods")
    @staff_required
    def staff_celebration_periods():
        """MCK-133: this teacher's classes and their current award period."""
        user = current_user()
        assert user is not None
        try:
            classes = staff_award_periods(school, int(user["id"]))
        except Exception:  # noqa: BLE001 - MCK-160 LOW-11: always JSON
            logger.exception("MCK-160 award periods failed (user %s)", user.get("id"))
            return jsonify({"ok": False, "error": START_FRESH_FAILED}), 500
        return jsonify({"ok": True, "classes": classes})

    @bp.route("/api/staff/celebrations/start-fresh", methods=["POST"])
    @staff_required
    def staff_celebration_start_fresh():
        """MCK-133: restart the Celebrations award tally from now.

        Body ``{"scope": "all"}`` (every class this teacher has this
        semester) or ``{"scope": "class", "class_id": N}``. Saved
        attendance and points are kept; only the award tally restarts.
        """
        user = current_user()
        assert user is not None
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            payload = {}
        scope = str(payload.get("scope") or "").strip().lower()
        class_id = None
        if scope == "class":
            try:
                class_id = int(payload.get("class_id"))
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "Pick a class."}), 400
        elif scope != "all":
            return jsonify({"ok": False, "error": "Pick all classes or one class."}), 400
        try:
            result = start_fresh_award_tally(
                school, teacher_user_id=int(user["id"]), class_id=class_id
            )
        except PermissionError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 403
        except (AwardTallyBusy, AwardTallyShared) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception:  # noqa: BLE001 - MCK-160 LOW-11: always JSON
            logger.exception("MCK-160 start fresh failed (user %s)", user.get("id"))
            return jsonify({"ok": False, "error": START_FRESH_FAILED}), 500
        return jsonify({"ok": True, "scope": scope, **result})

    @bp.route("/api/staff/defaults")
    @staff_required
    def staff_defaults():
        """Wizard defaults: inherited semester, assigned offerings, times."""
        user = current_user()
        assert user is not None
        active = school.get_active_semester()
        offerings = []
        if active:
            offerings = school.list_offerings(
                teacher_user_id=int(user["id"]),
                semester_id=int(active["id"]),
                include_archived=False,
            )
        return jsonify(
            {
                "ok": True,
                "semester": active,
                "offerings": offerings,
                "time_options": list(TIME_OPTIONS),
                "day_options": wizard_defaults()["day_options"],
            }
        )

    @bp.route("/api/staff/classes", methods=["GET", "POST"])
    @staff_required
    def staff_classes():
        """List or populate a class (Codenames only — no Canvas CSV)."""
        user = current_user()
        assert user is not None
        if request.method == "GET":
            active = school.get_active_semester()
            classes = (
                school.list_staff_classes(int(user["id"]), int(active["id"]))
                if active
                else []
            )
            return jsonify({"ok": True, "classes": classes, "semester": active})
        body = request.get_json(silent=True) or {}
        if body.get("csv_text"):
            return jsonify(
                {"ok": False, "error": "Canvas CSV import is not available in LLOVES."}
            ), 400
        try:
            offering_id = int(body.get("offering_id") or 0)
            offering = school.get_offering(offering_id)
        except (TypeError, ValueError, KeyError):
            return jsonify(
                {"ok": False, "error": "No course assignment. Ask Admin to assign a course."}
            ), 403
        if int(offering["teacher_user_id"]) != int(user["id"]):
            if user["role"] != "it":
                return jsonify(
                    {"ok": False, "error": "No course assignment. Ask Admin to assign a course."}
                ), 403
        semester = school.get_semester(int(offering["semester_id"]))
        if not semester:
            return jsonify({"ok": False, "error": "Semester is missing."}), 400
        names = body.get("codenames") or []
        if not isinstance(names, list):
            return jsonify({"ok": False, "error": "codenames must be a list"}), 400
        if limits_apply(user) and len(names) > BASIC_SEAT_CAP:
            return jsonify(
                {"ok": False, "error": "The basic plan allows 35 students in a class."}
            ), 400
        if limits_apply(user):
            active = school.get_active_semester()
            existing = (
                school.list_staff_classes(int(user["id"]), int(active["id"]))
                if active
                else []
            )
            if len(existing) >= BASIC_CLASS_CAP:
                return jsonify(
                    {"ok": False, "error": "The basic plan allows 3 classes."}
                ), 400
        days = str(body.get("days") or offering.get("live_days") or "")
        time_label = str(body.get("time") or offering.get("live_time") or "")
        if offering.get("live_days") and offering.get("live_time"):
            # Admin-locked schedule wins over any client override.
            days = str(offering["live_days"])
            time_label = str(offering["live_time"])
        try:
            created = school.game.create_class(
                year=str(semester["year_display"]),
                semester=str(semester["term"]),
                course_code=str(offering["ontario_code"]),
                days_preset=days,
                time_label=time_label,
                codenames=[str(n) for n in names],
                offering_id=int(offering["id"]),
                teacher_user_id=int(user["id"]),
            )
        except ValueError as exc:
            simple = _simple_course(offering.get("ontario_code"), offering.get("library_id"))
            return jsonify({"ok": False, "error": _roster_message(exc, simple)}), 400
        class_id = int(created["id"])
        school.ensure_class_test_student(class_id)
        school.ensure_default_pins_for_class(class_id, str(app.secret_key or DEV_SECRET_KEY))
        created = school.enrich_class(created)
        return jsonify({"ok": True, "class": created})

    @bp.route("/api/staff/classes/<int:class_id>/roster", methods=["PUT"])
    @staff_required
    def staff_replace_roster(class_id: int):
        """Replace a class Codename roster without creating a new section."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            abort(403)
        body = request.get_json(silent=True) or {}
        names = body.get("codenames") or []
        if not isinstance(names, list):
            return jsonify({"ok": False, "error": "codenames must be a list"}), 400
        if limits_apply(user) and len(names) > BASIC_SEAT_CAP:
            return jsonify(
                {"ok": False, "error": "The basic plan allows 35 students in a class."}
            ), 400
        active_live = school.get_active_live_session_for_class(int(class_id))
        if active_live is not None:
            return jsonify(
                {
                    "ok": False,
                    "error": "End the live class before editing the roster.",
                }
            ), 409
        before = school.class_roster_pairs(class_id)
        try:
            dash = school.game.replace_codename_roster(
                class_id,
                [str(n) for n in names],
                sort="az",
            )
        except ValueError as exc:
            return jsonify(
                {"ok": False, "error": _roster_message(exc, _class_is_simple(class_id))}
            ), 400
        except KeyError:
            abort(404)
        # Match id and name: a new student can reuse a removed id.
        removed = before - school.class_roster_pairs(class_id)
        school.delete_student_live_result_snapshots(
            class_id, [sid for sid, _ in removed]
        )
        # Reward rows follow the roster row; SQLite can reuse its id (MCK-116).
        school.delete_avatar_reward_grants(class_id, [sid for sid, _ in removed])
        forget_featured_students(school, class_id, [sid for sid, _ in removed])
        dash["class"] = school.enrich_class(dash["class"])
        return jsonify({"ok": True, "class": dash["class"], "students": dash.get("students")})

    @bp.route(
        "/api/classes/<int:class_id>/students/<int:student_id>/name",
        methods=["POST"],
    )
    @staff_required
    def api_rename_roster_student(class_id: int, student_id: int):
        """Rename one student on a class roster the staff member can open.

        Uses the same staff session cookie as other JSON writes
        (``SameSite=Lax``, HttpOnly). ``teacher_owns_class`` is the course
        access check used for staff roster views: the assigned teacher, or
        IT in the same tenant. The body may include ``first_name``,
        ``last_name``, and ``display_name``. Blank names and names longer
        than 80 characters are rejected. The response is the updated name.
        """
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify({"ok": False, "error": "Enter a name."}), 400
        try:
            updated = school.rename_roster_student(
                class_id,
                student_id,
                first_name=body["first_name"] if "first_name" in body else None,
                last_name=body["last_name"] if "last_name" in body else None,
                display_name=(
                    body["display_name"] if "display_name" in body else None
                ),
            )
        except KeyError:
            return jsonify({"ok": False, "error": "Student not found."}), 404
        except ValueError as exc:
            return jsonify(
                {"ok": False, "error": _roster_message(exc, _class_is_simple(class_id))}
            ), 400
        return jsonify({"ok": True, **updated})

    @bp.route("/staff/class/<int:class_id>/results.csv")
    @staff_required
    def staff_class_results_csv(class_id: int):
        """Download saved End-of-class answers for one class (MCK-46).

        One row per saved answer, read per run (``run_key``) from the End
        results snapshot. ``?run_key=`` limits the file to one run;
        otherwise every kept run is included, newest run first.
        """
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            abort(403)
        cls = school.game.get_class(class_id)
        wanted = str(request.args.get("run_key") or "").strip()
        runs = school.live_result_snapshot_runs(class_id)
        if wanted:
            runs = [run for run in runs if run["run_key"] == wanted]
            if not runs:
                abort(404)
        show_tests = _include_test_students()
        hidden_ids = set() if show_tests else set(school.test_student_ids(class_id))

        def _export_rows(run: dict[str, Any]) -> list[dict[str, Any]]:
            """Snapshot rows for one run, hiding test students unless asked."""
            rows = school.live_result_snapshot_rows(class_id, run_key=run["run_key"])
            if not hidden_ids:
                return rows
            return [
                row
                for row in rows
                if int(row.get("student_id") or 0) not in hidden_ids
            ]

        body = live_results_csv([(run, _export_rows(run)) for run in runs])
        label = re.sub(
            r"[^A-Za-z0-9_-]+",
            "-",
            str(cls.get("section_code") or cls.get("ontario_code") or f"class-{class_id}"),
        ).strip("-") or f"class-{class_id}"
        suffix = f"-{runs[0]['snapshot_at'][:10]}" if wanted and runs else ""
        resp = Response(
            body,
            mimetype="text/csv",
            headers={
                "Content-Disposition": (
                    f"attachment; filename=lloves-results-{label}{suffix}.csv"
                ),
                "Cache-Control": "no-store",
            },
        )
        return resp

    @bp.route("/staff/class/<int:class_id>")
    @staff_required
    def staff_course(class_id: int):
        """Course page: primary tabs, More menu, and the open section."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id) and user.get("role") != "it":
            abort(403)
        cls = school.enrich_class(school.game.get_class(class_id))
        offering = school.get_offering(int(cls["offering_id"])) if cls.get("offering_id") else None
        if offering:
            offering = school.ensure_offering_instance(offering)
            cls["imscc_path"] = offering.get("imscc_path")
            cls["instance_relpath"] = offering.get("instance_relpath")
        expectations = []
        if offering:
            expectations = school.list_expectations(str(offering["ontario_code"]))
        course_simple = _simple_course(
            (offering or {}).get("ontario_code") or cls.get("ontario_code"),
            cls.get("library_id") or (offering or {}).get("library_id"),
        )
        course_shell_tabs = offering_uses_shell_tabs(school, offering)
        # A default shell opens on Live Class. Pack-less non-math courses
        # that are not on that shell still open on Attendance & Participation.
        # Every other course opens on Live Class (MCK-249).
        default_tab = "live" if course_shell_tabs or not course_simple else "ap"
        tab = (request.args.get("tab") or default_tab).strip().lower()
        if tab in {"track-live", "track_live"}:
            tab = "live"
        from portfolio.flags import portfolio_tab_enabled

        show_portfolio_tab = portfolio_tab_enabled()
        # Old tracker lived at ?tab=grades; send those bookmarks to A&P.
        if tab == "grades":
            return redirect(
                url_for(
                    "staff_course",
                    class_id=class_id,
                    tab="ap",
                    view=request.args.get("view") or "attendance",
                )
            )
        # Historical aliases for the tracker / A&P surface.
        if tab in {"track", "attendance-participation", "attendance", "participation"}:
            view = "participation" if tab == "participation" else (
                "attendance" if tab == "attendance" else (request.args.get("view") or "attendance")
            )
            return redirect(
                url_for("staff_course", class_id=class_id, tab="ap", view=view)
            )
        ap_view = (request.args.get("view") or "attendance").strip().lower()
        if ap_view == "mood":
            return redirect(
                url_for(
                    "staff_course",
                    class_id=class_id,
                    tab="ap",
                    view="attendance",
                )
            )
        if ap_view not in {"attendance", "participation", "feedback", "engagement", "roster"}:
            ap_view = "attendance"
        portfolio_view = (request.args.get("view") or "build").strip().lower()
        if portfolio_view not in {"build", "marking"}:
            portfolio_view = "build"
        if tab not in {
            "modules",
            "pages",
            "assignments",
            "quizzes",
            "library",
            "question-banks",
            "syllabus",
            "ap",
            "live",
            "lesson-slides",
            "gradebook",
            "expectations",
            "profiles",
            "portfolio",
        }:
            tab = default_tab
        library_tabs = {"pages", "assignments", "quizzes"}
        library_kind = "all"
        if tab == "library":
            requested = (request.args.get("kind") or "all").strip().lower()
            library_kind = requested if requested in library_tabs else "all"
        elif tab in library_tabs:
            library_kind = tab
        if tab == "portfolio" and not show_portfolio_tab:
            tab = default_tab
        pack_error = session.pop("pack_error", None)
        pack_ok = request.args.get("pack") == "ok"
        live_step = (request.args.get("step") or "").strip().lower()
        if live_step not in {
            "",
            "validate",
            "att",
            "gamify",
            "teams",
            "names",
            "rounds",
            "score",
            "live",
        }:
            live_step = ""
        active_live = school.get_active_live_session_for_class(class_id)
        live_session_id = request.args.get("live_session_id") or ""
        if not live_session_id and active_live is not None:
            live_session_id = str(active_live["id"])
        elif not live_session_id and tab == "live":
            latest = school.get_latest_live_session_for_class(class_id)
            if latest is not None:
                live_session_id = str(latest["id"])
        live_session_code = (
            str(active_live.get("session_code") or "") if active_live is not None else ""
        )
        live_run_key = (
            str(active_live.get("run_key") or "") if active_live is not None else ""
        )
        # Idle Set Class keeps the course chrome. A confirmed class hides it.
        live_on_set_class = True
        if active_live is not None:
            teacher_state = school.live_session_teacher_state_payload(int(active_live["id"])) or {}
            live_on_set_class = not bool(teacher_state.get("class_set"))
        _audit(
            "student.record.view",
            "class",
            resource_id=class_id,
            detail={"tab": tab},
        )
        show_tests = _include_test_students()
        exit_feedback = (
            school.list_exit_feedback_for_class(class_id) if tab == "ap" else []
        )
        feedback_grid = (
            school.feedback_grid_for_class(class_id, include_test_students=show_tests)
            if tab == "ap" and ap_view == "feedback"
            else {"columns": [], "students": [], "totals": {}, "grand_total": 0}
        )
        roster_pin_rows = (
            school.roster_pin_rows(class_id, include_test_students=show_tests)
            if tab == "ap"
            and ap_view == "roster"
            and school.teacher_owns_class(int(user["id"]), class_id)
            else []
        )
        require_catchup_pin_change = (
            school.require_catchup_pin_change(class_id) if tab == "ap" and ap_view == "roster" else False
        )
        live_packs = live_class_registry(
            (offering or {}).get("ontario_code")
            or cls.get("ontario_code")
            or "MCF3M"
        )
        set_class = school.set_class_picker(class_id)
        try:
            from set_class import registry_with_setup_module
        except ImportError:
            from lms.set_class import registry_with_setup_module
        live_packs = registry_with_setup_module(
            live_packs,
            set_class.get("module"),
            set_class.get("chapters") or [],
        )
        from engagement_summary import KEY_ENGAGEMENT_SUMMARY_EMPTY, engagement_copy
        from flask import make_response

        teacher_live = school.get_active_live_session_for_teacher(int(user["id"]))
        session_for_type = active_live if active_live is not None else teacher_live
        if tab == "live":
            tour_page = "live"
        elif tab == "question-banks":
            tour_page = "question_bank"
        elif tab == "ap":
            tour_page = "ap"
        else:
            tour_page = "course"
        on_running_live = tab == "live" and active_live is not None
        roster_rows = school.roster_name_entries(class_id)
        tour = _tour_template(
            page=tour_page,
            roster=len(roster_rows),
            live_session=session_for_type,
            class_id=class_id,
            view=ap_view if tab == "ap" else "",
            block=session_for_type is not None and not on_running_live,
        )
        module_wrap = None
        module_toast = ""
        if tab == "ap":
            module_wrap = school.begin_module_board(
                class_id, include_test_students=show_tests
            )
            try:
                from begin_module import toast_for_reload
            except ImportError:
                from lms.begin_module import toast_for_reload
            module_toast = toast_for_reload(
                int(module_wrap["current_number"]),
                request.args.get("wrapped"),
                request.args.get("started"),
            )
        html = render_template(
            "staff/course.html",
            user=user,
            cls=cls,
            offering=offering,
            expectations=expectations,
            nav_courses=_staff_nav_courses(int(user["id"])),
            tab=tab,
            library_kind=library_kind,
            ap_view=ap_view,
            exit_feedback=exit_feedback,
            feedback_grid=feedback_grid,
            live_packs=live_packs,
            set_class=set_class,
            portfolio_view=portfolio_view,
            take_attendance=request.args.get("take") == "1",
            log_participation=request.args.get("participate") == "1",
            run_live=request.args.get("run") == "1",
            tour_done=request.args.get("tour") == "done" and tab == "ap",
            tour_offer=_tour_offer(user, [cls]),
            **tour,
            course_simple=course_simple,
            course_shell_tabs=course_shell_tabs,
            live_session_id=live_session_id,
            live_run_key=live_run_key,
            live_session_code=live_session_code,
            live_step=live_step,
            live_on_set_class=live_on_set_class,
            school_name=SCHOOL_NAME,
            roster_pin_rows=roster_pin_rows,
            require_catchup_pin_change=require_catchup_pin_change,
            show_module_pack_upload=False,
            pack_error=pack_error,
            pack_ok=pack_ok,
            show_portfolio_tab=show_portfolio_tab,
            bank_copy=WONDER_COPY,
            allow_media_url_swap=live_media_url_swap_allowed(
                testing=bool(app.config.get("TESTING"))
            ),
            roster_students=school.roster_name_entries(
                class_id, include_test_students=show_tests
            ) if show_tests else roster_rows,
            show_test_students=show_tests,
            engagement_summary_empty_copy=engagement_copy(KEY_ENGAGEMENT_SUMMARY_EMPTY),
            module_wrap=module_wrap,
            module_toast=module_toast,
        )
        resp = make_response(html)
        if (
            tab == "ap"
            and ap_view == "roster"
            and school.teacher_owns_class(int(user["id"]), class_id)
        ):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @bp.route("/staff/class/<int:class_id>/module-pack", methods=["POST"])
    @staff_required
    def staff_upload_module_pack(class_id: int):
        """Staff cannot upload packs; IT attaches a shared library."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            abort(403)
        message = "Only Admin can attach a module pack."
        if _wants_json():
            return jsonify({"ok": False, "error": message}), 403
        return render_template("forbidden.html", message=message), 403

    @bp.route("/staff/offerings/<int:offering_id>/module-pack/status")
    @staff_required
    def staff_offering_pack_status(offering_id: int):
        """JSON pack progress for the teacher who owns this offering."""
        user = current_user()
        assert user is not None
        offering = school.get_offering(int(offering_id))
        if int(offering["teacher_user_id"]) != int(user["id"]) and user["role"] != "it":
            abort(403)
        return _pack_status_response(offering)

    @bp.route("/staff/class/<int:class_id>/module-pack/status")
    @staff_required
    def staff_module_pack_status(class_id: int):
        """Staff pack-status for a class the teacher owns."""
        _, offering = _owned_offering_for_pack(class_id)
        return _pack_status_response(offering)

    @bp.route("/api/staff/class/<int:class_id>/modules")
    @staff_required
    def staff_modules_nav(class_id: int):
        """JSON module tree for the Modules tab."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        cls = school.enrich_class(school.game.get_class(class_id))
        code = str(cls.get("ontario_code") or cls.get("course_code") or "")
        library_id, error = _ready_library(school, cls)
        if not library_id:
            return jsonify(
                {
                    "ok": True,
                    "empty": True,
                    "message": error or "No modules for this course yet.",
                    "modules": [],
                }
            )
        nav = outline_nav(school, int(library_id))
        if not nav:
            return jsonify(
                {
                    "ok": True,
                    "empty": True,
                    "message": error
                    or "This module pack has no modules to show yet.",
                    "modules": [],
                }
            )
        return jsonify({"ok": True, "empty": False, "modules": nav, "code": code})

    @bp.route("/api/staff/class/<int:class_id>/components/<kind>")
    @staff_required
    def staff_components(class_id: int, kind: str):
        """List catalog components for a staff tab.

        Args:
            class_id: Class primary key.
            kind: ``pages``, ``assignments``, ``quizzes``, or ``question-banks``.
        """
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        listers = {
            "pages": list_pages,
            "assignments": list_assignments,
            "quizzes": list_quizzes,
            "question-banks": list_question_banks,
        }
        lister = listers.get(kind)
        if lister is None:
            return jsonify({"ok": False, "error": "Unknown component"}), 404
        cls = school.enrich_class(school.game.get_class(class_id))
        library_id, error = _ready_library(school, cls)
        if not library_id:
            empty_message = (
                EMPTY_BANKS_MESSAGE
                if kind == "question-banks"
                else (error or "No content in this course yet.")
            )
            return jsonify(
                {
                    "ok": True,
                    "empty": True,
                    "message": empty_message,
                    "items": [],
                }
            )
        items = lister(school, int(library_id))
        if kind == "question-banks":
            items = apply_visible_bank_question_counts(
                school, int(library_id), items
            )
        empty_message = EMPTY_BANKS_MESSAGE if kind == "question-banks" else None
        return jsonify(
            {
                "ok": True,
                "empty": not items,
                "kind": kind,
                "items": items,
                "counts": library_counts(school, int(library_id)),
                **({"message": empty_message} if not items and empty_message else {}),
            }
        )

    @bp.route("/api/staff/class/<int:class_id>/question-bank/<int:bank_id>")
    @staff_required
    def staff_question_bank(class_id: int, bank_id: int):
        """List the questions stored in one imported bank."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        cls = school.enrich_class(school.game.get_class(class_id))
        library_id, _error = _ready_library(school, cls)
        if not library_id:
            return jsonify({"ok": False, "error": "No module pack"}), 404
        bank = get_question_bank(school, int(library_id), int(bank_id))
        if bank is None:
            return jsonify({"ok": False, "error": "Bank not found"}), 404
        stored = list_questions(school, int(library_id), int(bank_id))
        questions = list_staff_bank_questions(
            school,
            int(library_id),
            int(bank_id),
            class_id=int(class_id),
        )
        # Bank linter (report-only): per-row fix codes + per-code counts.
        try:
            from bank_lint import fix_codes, lint_counts
        except ImportError:
            from lms.bank_lint import fix_codes, lint_counts
        try:
            codes = fix_codes(
                school,
                [int(q.get("source_question_id") or q.get("id") or 0) for q in questions
                 if str(q.get("source_question_id") or q.get("id") or "").isdigit()],
            )
            for q in questions:
                key = str(q.get("source_question_id") or q.get("id") or "")
                q["fix_codes"] = codes.get(int(key), []) if key.isdigit() else []
            fix_counts = lint_counts(school, int(library_id))
        except Exception:  # noqa: BLE001 - lint data is optional
            report_swallowed("question bank lint")
            fix_counts = {}
        return jsonify(
            {
                "ok": True,
                "bank": {
                    "id": int(bank["id"]),
                    "title": str(bank.get("title") or ""),
                    "import_key": str(bank.get("import_key") or ""),
                },
                "questions": questions,
                "hidden_duplicates": max(0, len(stored) - len(questions)),
                "fix_counts": fix_counts,
            }
        )

    @bp.route(
        "/api/staff/class/<int:class_id>/question-bank/<int:bank_id>/questions",
        methods=["POST"],
    )
    @staff_required
    def staff_question_bank_add_question(class_id: int, bank_id: int):
        """Add one teacher-authored question to an imported bank."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        cls = school.enrich_class(school.game.get_class(class_id))
        library_id, error = _ready_library(school, cls)
        if not library_id:
            return jsonify({"ok": False, "error": error or "No module pack"}), 404
        if get_question_bank(school, int(library_id), int(bank_id)) is None:
            return jsonify({"ok": False, "error": "Bank not found"}), 404
        body = request.get_json(silent=True) or {}
        try:
            question = create_staff_bank_question(
                school,
                library_id=int(library_id),
                bank_id=int(bank_id),
                body=body if isinstance(body, dict) else {},
                class_id=int(class_id),
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except KeyError:
            return jsonify({"ok": False, "error": "Bank not found"}), 404
        return jsonify({"ok": True, "question": question})

    @bp.route(
        "/api/staff/class/<int:class_id>/question-bank/<int:bank_id>/questions/<int:question_id>",
        methods=["PATCH"],
    )
    @staff_required
    def staff_question_bank_patch_question(
        class_id: int, bank_id: int, question_id: int
    ):
        """Save one bank question. Imported ``item_type`` is preserved."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        cls = school.enrich_class(school.game.get_class(class_id))
        library_id, error = _ready_library(school, cls)
        if not library_id:
            return jsonify({"ok": False, "error": error or "No module pack"}), 404
        body = request.get_json(silent=True) or {}
        try:
            question = apply_staff_question_patch(
                school,
                library_id=int(library_id),
                bank_id=int(bank_id),
                question_id=int(question_id),
                body=body if isinstance(body, dict) else {},
                class_id=int(class_id),
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except KeyError:
            return jsonify({"ok": False, "error": "Question not found"}), 404
        return jsonify({"ok": True, "question": question})

    @bp.route(
        "/api/staff/class/<int:class_id>/question-bank/<int:bank_id>/questions/<int:question_id>",
        methods=["DELETE"],
    )
    @staff_required
    def staff_question_bank_delete_question(
        class_id: int, bank_id: int, question_id: int
    ):
        """Remove one question from a bank after the staff confirm modal."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        cls = school.enrich_class(school.game.get_class(class_id))
        library_id, error = _ready_library(school, cls)
        if not library_id:
            return jsonify({"ok": False, "error": error or "No module pack"}), 404
        try:
            delete_staff_bank_question(
                school,
                library_id=int(library_id),
                bank_id=int(bank_id),
                question_id=int(question_id),
            )
        except KeyError:
            return jsonify({"ok": False, "error": "Question not found"}), 404
        return jsonify({"ok": True})

    @bp.route(
        "/api/staff/class/<int:class_id>/question-bank/bulk",
        methods=["POST"],
    )
    @staff_required
    def staff_question_bank_bulk(class_id: int):
        """Move selected bank questions, set their Kind, or remove them."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        cls = school.enrich_class(school.game.get_class(class_id))
        library_id, error = _ready_library(school, cls)
        if not library_id:
            return jsonify({"ok": False, "error": error or "No module pack"}), 404
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            body = {}
        try:
            result = apply_question_bank_bulk(
                school, library_id=int(library_id), body=body
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except KeyError:
            return jsonify({"ok": False, "error": "Question not found"}), 404
        return jsonify({"ok": True, **result})

    @bp.route(
        "/api/staff/class/<int:class_id>/live-bank/questions",
        methods=["POST"],
    )
    @staff_required
    def staff_live_bank_add_question(class_id: int):
        """Add one untagged question to the live bank for a scope.

        Writes the staff-authored bank only. Does not import onto a live
        page and does not rewrite Course Wide warmup rows.
        """
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        cls = school.enrich_class(school.game.get_class(class_id))
        library_id, error = _ready_library(school, cls)
        if not library_id:
            return jsonify({"ok": False, "error": error or "No module pack"}), 404
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            body = {}
        scope = str(body.get("bank_scope") or body.get("bankScope") or "M1")
        try:
            question = create_live_bank_question(
                school,
                library_id=int(library_id),
                bank_scope=scope,
                body=body,
                class_id=int(class_id),
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except KeyError:
            return jsonify({"ok": False, "error": "Bank not found"}), 404
        return jsonify({"ok": True, "question": question})

    @bp.route("/api/staff/class/<int:class_id>/module-banks")
    @staff_required
    def staff_module_banks(class_id: int):
        """List heuristic and confirmed module bank links for one live module."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        module = str(request.args.get("module") or "").strip().upper()
        try:
            from bank_mc_normalize import parse_module_token
        except ImportError:
            from lms.bank_mc_normalize import parse_module_token
        module_number = parse_module_token(module)
        if module_number is None:
            return jsonify({"ok": False, "error": "module required (e.g. M1)"}), 400
        cls = school.enrich_class(school.game.get_class(class_id))
        library_id, error = _ready_library(school, cls)
        if not library_id:
            return jsonify(
                {
                    "ok": True,
                    "empty": True,
                    "message": error or "No question banks for this module yet.",
                    "module": module,
                    "needs_confirmation": True,
                    "suggested": [],
                    "confirmed": [],
                }
            )
        payload = school.suggest_module_banks(int(library_id), int(module_number))
        return jsonify(
            {
                "ok": True,
                "empty": False,
                "module": module,
                "module_number": module_number,
                "library_id": int(library_id),
                **payload,
            }
        )

    @bp.route("/api/staff/class/<int:class_id>/module-banks/confirm", methods=["POST"])
    @staff_required
    def staff_module_banks_confirm(class_id: int):
        """Persist teacher-confirmed question banks for one module."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        body = request.get_json(silent=True) or {}
        module = str(body.get("module") or "").strip().upper()
        try:
            from bank_mc_normalize import parse_module_token
        except ImportError:
            from lms.bank_mc_normalize import parse_module_token
        module_number = parse_module_token(module)
        if module_number is None:
            return jsonify({"ok": False, "error": "module required (e.g. M1)"}), 400
        bank_ids = body.get("bank_ids") or body.get("bankIds") or []
        if not isinstance(bank_ids, list):
            return jsonify({"ok": False, "error": "bank_ids must be a list"}), 400
        cls = school.enrich_class(school.game.get_class(class_id))
        library_id, error = _ready_library(school, cls)
        if not library_id:
            return jsonify({"ok": False, "error": error or "No module pack"}), 404
        confirmed = school.confirm_module_bank_links(
            int(library_id), int(module_number), bank_ids
        )
        payload = school.suggest_module_banks(int(library_id), int(module_number))
        return jsonify(
            {
                "ok": True,
                "module": module,
                "module_number": module_number,
                "library_id": int(library_id),
                "confirmed": confirmed,
                "recommended": payload.get("recommended") or [],
                "needs_confirmation": bool(payload.get("needs_confirmation")),
            }
        )

    @bp.route(
        "/api/staff/class/<int:class_id>/module-banks/<module>/mc-search"
    )
    @staff_required
    def staff_module_bank_mc_search(class_id: int, module: str):
        """Search normalized MCs for one module or Course Wide bank scope."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        try:
            from bank_mc_normalize import parse_module_token
        except ImportError:
            from lms.bank_mc_normalize import parse_module_token
        query = str(request.args.get("q") or "").strip()
        kind = str(request.args.get("kind") or "").strip().lower()
        try:
            from bank_question_types import parse_type_filter
        except ImportError:
            from lms.bank_question_types import parse_type_filter
        question_type = parse_type_filter(request.args.get("type"))
        if question_type is None:
            return jsonify({"ok": False, "error": "unknown question type"}), 400
        sort = str(request.args.get("sort") or "").strip().lower()
        if sort not in {"", "bank", "edited", "type"}:
            return jsonify({"ok": False, "error": "unknown sort"}), 400
        sort_arg = sort or None
        staff_types = str(request.args.get("staff_types") or "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        placement = str(request.args.get("placement") or "").strip().lower()
        scope_arg = str(
            request.args.get("bank_scope") or request.args.get("bankScope") or ""
        ).strip().lower()
        module_token = str(module or "").strip()
        course_tokens = {"course", "course-wide", "coursewide"}
        course_scope = (
            module_token.lower() in course_tokens or scope_arg in course_tokens
        )
        all_scope = module_token.lower() == "all" or scope_arg == "all"
        module_number = None
        if not course_scope and not all_scope:
            module_number = parse_module_token(module_token.upper())
            if module_number is None:
                return jsonify({"ok": False, "error": "module required (e.g. M1)"}), 400
        cls = school.enrich_class(school.game.get_class(class_id))
        library_id, error = _ready_library(school, cls)
        if not library_id:
            return jsonify({"ok": False, "error": error or "No module pack"}), 404
        if all_scope:
            result = school.search_bank_scope_mcs(
                int(library_id),
                "all",
                1,
                query,
                class_id=int(class_id),
                kind=kind,
                question_type=question_type,
                staff_types=staff_types,
                sort=sort_arg,
            )
            items = result.get("items") or []
            return jsonify(
                {
                    "ok": True,
                    "module": "ALL",
                    "bank_scope": "all",
                    "module_number": None,
                    "library_id": int(library_id),
                    "query": query,
                    "kind": kind,
                    "type": question_type,
                    "count": len(items),
                    "total": int(result.get("total") or 0),
                    "filtered": int(result.get("filtered") or 0),
                    "type_counts": result.get("type_counts") or [],
                    "items": items,
                }
            )
        if course_scope and placement == "course":
            result = school.search_module_bank_mcs(
                int(library_id),
                1,
                query,
                class_id=int(class_id),
                kind=kind,
                question_type=question_type,
                scope_policy="course_only",
                staff_types=staff_types,
                sort=sort_arg,
            )
            items = result.get("items") or []
            return jsonify(
                {
                    "ok": True,
                    "module": "COURSE",
                    "bank_scope": "course",
                    "module_number": None,
                    "library_id": int(library_id),
                    "query": query,
                    "kind": kind,
                    "type": question_type,
                    "count": len(items),
                    "total": int(result.get("total") or 0),
                    "filtered": int(result.get("filtered") or 0),
                    "type_counts": result.get("type_counts") or [],
                    "items": items,
                }
            )
        if course_scope:
            result = school.search_bank_scope_mcs(
                int(library_id),
                "course",
                1,
                query,
                class_id=int(class_id),
                kind=kind,
                question_type=question_type,
                staff_types=staff_types,
                sort=sort_arg,
            )
            items = result.get("items") or []
            return jsonify(
                {
                    "ok": True,
                    "module": "COURSE",
                    "bank_scope": "course",
                    "module_number": None,
                    "library_id": int(library_id),
                    "query": query,
                    "kind": kind,
                    "type": question_type,
                    "count": len(items),
                    "total": int(result.get("total") or 0),
                    "filtered": int(result.get("filtered") or 0),
                    "type_counts": result.get("type_counts") or [],
                    "items": items,
                }
            )
        result = school.search_module_bank_mcs(
            int(library_id),
            int(module_number),
            query,
            class_id=int(class_id),
            kind=kind,
            question_type=question_type,
            staff_types=staff_types,
            include_course_placed=placement != "module",
            sort=sort_arg,
        )
        items = result.get("items") or []
        return jsonify(
            {
                "ok": True,
                "module": module_token.upper(),
                "bank_scope": module_token.upper(),
                "module_number": module_number,
                "library_id": int(library_id),
                "query": query,
                "kind": kind,
                "type": question_type,
                "count": len(items),
                "total": int(result.get("total") or 0),
                "filtered": int(result.get("filtered") or 0),
                "type_counts": result.get("type_counts") or [],
                "items": items,
            }
        )

    @bp.route(
        "/api/staff/class/<int:class_id>/live-lessons/<module>/<slot>/import-mc",
        methods=["POST"],
    )
    @staff_required
    def staff_import_live_mc(class_id: int, module: str, slot: str):
        """Import one module-bank MC onto a class live-lesson overlay."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        body = request.get_json(silent=True) or {}
        try:
            question_id = int(body.get("question_id") or body.get("questionId"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "question_id required"}), 400
        try:
            page_number = int(body.get("page_number") or body.get("pageNumber") or 1)
        except (TypeError, ValueError):
            page_number = 1
        stage = str(body.get("stage") or "round").strip().lower()
        order_raw = body.get("order")
        order = int(order_raw) if order_raw not in (None, "") else None
        cls = school.enrich_class(school.game.get_class(class_id))
        library_id, error = _ready_library(school, cls)
        if not library_id:
            return jsonify({"ok": False, "error": error or "No module pack"}), 404
        try:
            placement = school.import_mc_to_class_playlist(
                int(class_id),
                str(module or "").strip().upper(),
                str(slot or "").strip().upper(),
                int(question_id),
                library_id=int(library_id),
                page_number=page_number,
                stage=stage,
                order=order,
            )
        except KeyError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        module_key = str(module or "").strip().upper()
        slot_key = str(slot or "").strip().upper()
        response: dict[str, Any] = {
            "ok": True,
            "placement": placement,
            **school.playlist_staff_snapshot(int(class_id), module_key, slot_key),
        }
        return jsonify(response)

    @bp.route(
        "/api/staff/class/<int:class_id>/live-lessons/<module>/<slot>/import-contest-questions",
        methods=["POST"],
    )
    @staff_required
    def staff_import_live_content_questions(class_id: int, module: str, slot: str):
        """MCK-79: import picked Contest Questions onto the current page.

        Each pick names its group and must be in that group's current top 6.
        Cards go through the deck's Add New path (open prompt, Kind Contest).
        All-or-nothing: 409 when undone, 500 when the undo failed.
        """
        ontario_code, modules, failed = _content_questions_scope(class_id)
        if failed is not None:
            return failed
        try:
            from live_content_questions import (
                ContentImportFailed,
                StalePick,
                clean_content_picks,
                deck_target,
                import_content_questions,
            )
        except ImportError:
            from lms.live_content_questions import (
                ContentImportFailed,
                StalePick,
                clean_content_picks,
                deck_target,
                import_content_questions,
            )
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            body = {}
        try:
            # MCK-161 LOW-7: the keys Add New stores (``/M01/c4/`` -> M1, C4),
            # so a failed batch's rollback finds every row it placed.
            module_key, slot_key = deck_target(module, slot)
            picks = clean_content_picks(body.get("picks"), modules)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        try:
            page_number = int(body.get("page_number") or body.get("pageNumber") or 1)
        except (TypeError, ValueError):
            page_number = 1
        stage = str(body.get("stage") or "round").strip().lower()
        try:
            placements = import_content_questions(
                school,
                int(class_id),
                module_key,
                slot_key,
                picks,
                ontario_code=ontario_code,
                modules=modules,
                page_number=page_number,
                stage=stage,
            )
        except StalePick as exc:
            # MCK-161 LOW-8: teacher copy plus the stale picks to untick.
            return (
                jsonify({"ok": False, "error": str(exc), "imported": 0, "stale": exc.stale}),
                404,
            )
        except ContentImportFailed as exc:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": str(exc),
                        "imported": 0,
                        "landed": exc.landed,
                        "rolled_back": exc.rolled_back,
                    }
                ),
                exc.status,
            )
        return jsonify(
            {
                "ok": True,
                "imported": len(placements),
                "placements": placements,
                **school.playlist_staff_snapshot(int(class_id), module_key, slot_key),
            }
        )

    @bp.route(
        "/api/staff/class/<int:class_id>/question-overlays/<int:question_id>",
        methods=["PATCH"],
    )
    @staff_required
    def staff_question_overlay_patch(class_id: int, question_id: int):
        """Persist staff edits for one ingest MC via ``library_question_overlays``."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        body = request.get_json(silent=True) or {}
        cls = school.enrich_class(school.game.get_class(class_id))
        library_id, error = _ready_library(school, cls)
        if not library_id:
            return jsonify({"ok": False, "error": error or "No module pack"}), 404
        stem = str(body.get("stem_text") or body.get("text") or "").strip()
        options = body.get("options") or body.get("choices") or []
        if not isinstance(options, list):
            return jsonify({"ok": False, "error": "options must be a list"}), 400
        correct = str(
            body.get("correct_answer") or body.get("correctAnswer") or ""
        ).strip()
        points_raw = body.get("points")
        points = None
        if points_raw not in (None, ""):
            try:
                points = float(points_raw)
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "invalid points"}), 400
        if not stem:
            return jsonify({"ok": False, "error": "stem_text required"}), 400
        if len([opt for opt in options if str(opt).strip()]) < 2:
            return jsonify({"ok": False, "error": "at least two options required"}), 400
        if not correct:
            return jsonify({"ok": False, "error": "correct_answer required"}), 400
        try:
            overlay = school.upsert_library_question_overlay(
                int(library_id),
                int(question_id),
                stem_text=stem,
                options=[str(opt) for opt in options],
                correct_answer=correct,
                points=points,
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except KeyError:
            return jsonify({"ok": False, "error": "Question not found"}), 404
        return jsonify({"ok": True, "overlay": overlay})

    @bp.route("/staff/class/<int:class_id>/component/<kind>/<int:component_id>")
    @staff_required
    def staff_component_preview(class_id: int, kind: str, component_id: int):
        """Preview one catalog component in the tab's viewer pane."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            abort(403)
        if kind not in {"page", "assignment", "quiz", "bank"}:
            abort(404)
        cls = school.enrich_class(school.game.get_class(class_id))
        library_id, _error = _ready_library(school, cls)
        if not library_id:
            abort(404)
        return _render_component(
            school,
            cls,
            int(library_id),
            kind,
            int(component_id),
            title=kind.title(),
            files_root=f"/staff/class/{class_id}/module-files/web_resources",
        )

    @bp.route("/staff/class/<int:class_id>/module-item")
    @staff_required
    def staff_module_item(class_id: int):
        """Serve a wiki page from this class's module pack."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            abort(403)
        cls = school.enrich_class(school.game.get_class(class_id))
        library_id, _error = _ready_library(school, cls)
        if not library_id:
            abort(404)
        try:
            item_id = int(request.args.get("item") or 0)
        except ValueError:
            abort(400)
        if not item_id:
            abort(400)
        return _serve_component_item(
            school,
            cls,
            int(library_id),
            item_id,
            files_root=f"/staff/class/{class_id}/module-files/web_resources",
        )

    @bp.route("/staff/class/<int:class_id>/module-files/<path:rel>")
    @staff_required
    def staff_module_file(class_id: int, rel: str):
        """Serve a page asset from the shared blob store."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            abort(403)
        cls = school.enrich_class(school.game.get_class(class_id))
        library_id, _error = _ready_library(school, cls)
        if not library_id:
            abort(404)
        target = library_file_path(school, school.data_dir, int(library_id), rel)
        if target is None:
            abort(404)
        return _send_module_file(target, rel)

    @bp.route("/staff/class/<int:class_id>/syllabus")
    @staff_required
    def staff_syllabus(class_id: int):
        """Saved month-grid or a prompt to open the click-to-place editor."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            abort(403)
        cls = school.enrich_class(school.game.get_class(class_id))
        if cls.get("offering_id"):
            offering = school.ensure_offering_instance(
                school.get_offering(int(cls["offering_id"]))
            )
            cls["instance_relpath"] = offering.get("instance_relpath")
            cls["imscc_path"] = offering.get("imscc_path")
        label = str(cls.get("semester_label") or "")
        code = str(cls.get("ontario_code") or cls.get("course_code") or "")
        saved = (
            syllabus_mod.saved_html_path(
                label,
                code,
                data_dir=school.data_dir,
                instance_relpath=cls.get("instance_relpath"),
            )
            if label
            else None
        )
        if saved and request.args.get("view") != "edit":
            return saved.read_text(encoding="utf-8")
        return redirect(url_for("staff_syllabus_editor", class_id=class_id))

    @bp.route("/staff/class/<int:class_id>/syllabus/editor")
    @staff_required
    def staff_syllabus_editor(class_id: int):
        """Embedded click-to-place editor; calendar locked from IT semester."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            abort(403)
        cls = school.enrich_class(school.game.get_class(class_id))
        semester = school.get_active_semester()
        offering = None
        if cls.get("offering_id"):
            offering = school.ensure_offering_instance(
                school.get_offering(int(cls["offering_id"]))
            )
            cls["instance_relpath"] = offering.get("instance_relpath")
            cls["imscc_path"] = offering.get("imscc_path")
            semester = school.get_semester(int(offering["semester_id"]))
        if not semester:
            return wrap_page(
                "Syllabus",
                "<h1>Syllabus</h1><p>Ask Admin to activate a semester first.</p>",
            )
        calendar = syllabus_mod.calendar_from_semester_row(
            semester,
            data_dir=school.data_dir,
            instance_relpath=cls.get("instance_relpath"),
        )
        slots = syllabus_mod.slots_from_class(str(cls["days"]), str(cls["time"]))
        library_id, _pack_error = _ready_library(school, cls)
        try:
            modules = syllabus_mod.editor_modules_from_outline(
                outline_raw_modules(school, int(library_id)) if library_id else []
            )
        except ValueError as exc:
            return wrap_page("Syllabus", f"<h1>Syllabus</h1><p>{exc}</p>")
        if not modules:
            return wrap_page(
                "Syllabus",
                "<h1>Syllabus</h1><p>Ask Admin to attach a module pack. "
                "The click-to-place editor needs an IMSCC cartridge.</p>",
            )
        save_url = url_for("staff_syllabus_save", class_id=class_id)
        html_out = syllabus_mod.build_editor_page(
            course=str(cls.get("ontario_code") or cls["course_code"]),
            calendar=calendar,
            slots=slots,
            modules=modules,
            save_url=save_url,
        )
        return html_out

    @bp.route("/staff/class/<int:class_id>/syllabus/save", methods=["POST"])
    @staff_required
    def staff_syllabus_save(class_id: int):
        """Write CSV + HTML + answers JSON per offering. No sequential packer."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        cls = school.enrich_class(school.game.get_class(class_id))
        semester = school.get_active_semester()
        if cls.get("offering_id"):
            offering = school.ensure_offering_instance(
                school.get_offering(int(cls["offering_id"]))
            )
            cls["instance_relpath"] = offering.get("instance_relpath")
            cls["imscc_path"] = offering.get("imscc_path")
            semester = school.get_semester(int(offering["semester_id"]))
        if not semester:
            return jsonify({"ok": False, "error": "No active semester"}), 400
        calendar = syllabus_mod.calendar_from_semester_row(
            semester,
            data_dir=school.data_dir,
            instance_relpath=cls.get("instance_relpath"),
        )
        slots = syllabus_mod.slots_from_class(str(cls["days"]), str(cls["time"]))
        library_id, _pack_error = _ready_library(school, cls)
        try:
            modules = syllabus_mod.editor_modules_from_outline(
                outline_raw_modules(school, int(library_id)) if library_id else []
            )
            payload = request.get_json(force=True, silent=False) or {}
            paths = syllabus_mod.save_placements(
                payload=payload,
                course=str(cls.get("ontario_code") or cls["course_code"]),
                semester_label=str(semester["label"]),
                calendar=calendar,
                slots=slots,
                modules=modules,
                data_dir=school.data_dir,
                instance_relpath=cls.get("instance_relpath"),
            )
        except (ValueError, json.JSONDecodeError, OSError, KeyError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, **paths})

    @bp.route("/class/<int:class_id>")
    @staff_required
    def class_redirect(class_id: int):
        """Send the old game-show class URL to Attendance & Participation."""
        return redirect(
            url_for("staff_course", class_id=class_id, tab="ap", view="participation")
        )

    @bp.route("/class/<int:class_id>/setup")
    @staff_required
    def class_setup(class_id: int):
        """Teacher game setup (attendance / teams)."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            abort(403)
        return send_from_directory(MGS_TEMPLATES, "setup.html")

    @bp.route("/class/<int:class_id>/game")
    @staff_required
    def class_game(class_id: int):
        """Teacher scoring dashboard — students never get this route."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            abort(403)
        return send_from_directory(MGS_TEMPLATES, "game.html")

    @bp.route("/scoreboard")
    @bp.route("/scoreboard/<int:class_id>")
    @staff_or_student_scoreboard
    def scoreboard_page(class_id: int | None = None):
        """ESPN board: staff overlay or student join."""
        return send_from_directory(MGS_TEMPLATES, "scoreboard.html")

    @bp.route("/api/classes/<int:class_id>/module-files/<path:rel>")
    def serve_class_module_file(class_id: int, rel: str):
        """Serve one pack or mirrored bank image to class participants.

        Twin of ``/staff/class/<id>/module-files/<rel>``. Bank imports store
        staff URLs; student payloads rewrite them here so the same library
        blob is visible on the student card.

        Args:
            class_id: Game-show ``classes.id``.
            rel: Cartridge-relative path such as ``web_resources/graph.png``.
        """
        if not _can_access_class_live_asset(class_id):
            abort(403)
        try:
            cls = school.enrich_class(school.game.get_class(class_id))
        except KeyError:
            abort(404)
        library_id, _error = _ready_library(school, cls)
        if not library_id:
            abort(404)
        target = library_file_path(school, school.data_dir, int(library_id), rel)
        if target is None:
            abort(404)
        return _send_module_file(target, rel)

    @bp.route("/student/dashboard")
    @student_required
    def student_dashboard():
        """PIN-signed student home: dashboard, or live class when in session (MCK-208)."""
        ids = _pin_portal_identity(school)
        if ids is None:
            pin_sid = _catchup_student_for_class(school, int(session.get("student_class_id") or 0))
            if pin_sid is not None:
                try:
                    bind_pin_portal_session(
                        session, school, int(session.get("student_class_id")), int(pin_sid)
                    )
                except PermissionError:
                    return redirect(url_for("landing"))
                ids = (int(session.get("student_class_id")), int(pin_sid))
        if ids is None:
            return redirect(url_for("landing"))
        class_id, student_id = ids
        live_redirect = _student_dashboard_redirect_if_live(school, class_id, student_id)
        if live_redirect is not None:
            return live_redirect
        offering = school.get_offering(
            int(school.game.get_class(class_id)["offering_id"])
        )
        dash = school.student_dashboard_payload(class_id, student_id)
        handover = _student_dashboard_live_handover(school, class_id, student_id)
        focus = pop_dashboard_focus_catchup(session)
        from student_dashboard import (
            dashboard_copy,
            dashboard_header_line,
            finish_these_row_text,
            slot_class_label,
        )

        course_code = school._course_code_for_class(class_id)
        dashboard_subtitle = dash.get("header_line") or dashboard_header_line(
            course_code, str(dash.get("current_module") or "M1")
        )
        for row in dash.get("finish_these") or []:
            if not str(row.get("finish_row_label") or "").strip():
                slot_key = str(row.get("live_slot") or row.get("label") or "")
                row["class_label"] = slot_class_label(slot_key)
                row["finish_row_label"] = finish_these_row_text(
                    slot_key, int(row.get("open_count") or 0)
                )

        return render_template(
            "student/dashboard.html",
            offering=offering,
            dashboard=dash,
            dashboard_subtitle=dashboard_subtitle,
            school_name=SCHOOL_NAME,
            focus_catchup=focus,
            copy=dashboard_copy(),
            live_handover=handover,
        )

    @bp.route("/student/catchup", methods=["GET", "POST"])
    @bp.route("/student/catchup/landing", methods=["GET", "POST"])
    def student_catchup_landing():
        """MCK-222: pick a class to catch up on after portal code sign-in."""
        from catchup_landing import (
            PAGE_SIZE,
            build_matched_card,
            landing_copy,
            list_open_catchup_rows,
            peek_landing_session,
            signed_in_line,
            deadline_label,
        )
        from catchup_session import (
            codename_for_student,
            establish_catchup_identity,
            identity_for_class,
            peek_catchup_identity,
        )
        from participation import (
            CATCHUP_PIN_IP_LIMIT,
            KEY_PIN_HELP,
            KEY_PIN_IP_LOCKED,
            KEY_PIN_LABEL,
            KEY_PIN_LOOKS_LIKE_JOIN_CODE,
            KEY_PIN_NOT_SET,
            KEY_PIN_WRONG,
            PIN_WINDOW_S,
            catchup_ip_limit_key,
            copy,
        )

        held = peek_landing_session(session)
        if held is None:
            ident = peek_catchup_identity(session, school)
            if ident is None:
                return redirect(url_for("landing"))
            class_id = int(ident["class_id"])
            student_id = int(ident["student_id"])
            matched_token = ""
            display_name = codename_for_student(school, class_id, student_id)
        else:
            class_id = int(held["class_id"])
            student_id = int(held["student_id"])
            matched_token = str(held.get("matched_token") or "")
            display_name = codename_for_student(school, class_id, student_id)
        pin_sid = identity_for_class(session, school, class_id)
        if request.method == "POST" and request.form.get("action") == "pin" and pin_sid is None:
            client_ip_key = join_limit_ip_key(request_client_ip())
            global_ip_key = catchup_ip_limit_key(client_ip_key)
            limits = {global_ip_key: CATCHUP_PIN_IP_LIMIT}
            reserved = school.reserve_catchup_pin_attempts(
                [global_ip_key], limits, window_s=PIN_WINDOW_S
            )
            pin_error = None
            if reserved is None:
                pin_error = copy(KEY_PIN_IP_LOCKED)
            else:
                gate = school.catchup_pin_student_gate(class_id, int(student_id))
                if gate:
                    school.release_catchup_pin_attempts(reserved)
                    pin_error = copy(gate)
                else:
                    result = school.catchup_verify_student_pin(
                        class_id, int(student_id), request.form.get("pin", "")
                    )
                    if result["ok"]:
                        school.release_catchup_pin_attempts(reserved)
                        establish_catchup_identity(
                            session,
                            school,
                            class_id,
                            int(result["student_id"]),
                            str(result.get("pin_stamp") or ""),
                        )
                        return redirect(url_for("student_catchup_landing"))
                    if result["reason"] == "wrong":
                        school.record_catchup_pin_failure(class_id, int(student_id))
                    else:
                        school.release_catchup_pin_attempts(reserved)
                    pin_error = copy(
                        {
                            "looks_like_join_code": KEY_PIN_LOOKS_LIKE_JOIN_CODE,
                            "not_set": KEY_PIN_NOT_SET,
                        }.get(result["reason"], KEY_PIN_WRONG)
                    )
            return render_template(
                "student/catchup_landing.html",
                course_code=school._course_code_for_class(class_id),
                signed_in_line=signed_in_line(display_name),
                pin_required=True,
                pin_error=pin_error,
                pin_label=copy(KEY_PIN_LABEL),
                pin_help=copy(KEY_PIN_HELP),
                copy=landing_copy(),
                school_name=SCHOOL_NAME,
            )
        if pin_sid is None:
            return render_template(
                "student/catchup_landing.html",
                course_code=school._course_code_for_class(class_id),
                signed_in_line=signed_in_line(display_name),
                pin_required=True,
                pin_error=None,
                pin_label=copy(KEY_PIN_LABEL),
                pin_help=copy(KEY_PIN_HELP),
                copy=landing_copy(),
                school_name=SCHOOL_NAME,
            )
        student_id = int(pin_sid)
        display_name = codename_for_student(school, class_id, student_id)
        course_code = school._course_code_for_class(class_id)
        state = str(request.args.get("state") or "").strip().lower()
        link = school.get_catchup_link(matched_token) or {}
        from catchup_landing import link_still_open

        closed = state == "closed" or (
            link and not link_still_open(school, {**link, "class_id": class_id})
        )
        matched = build_matched_card(school, {**link, "class_id": class_id}, student_id) if link else None
        others, total = list_open_catchup_rows(
            school,
            class_id,
            student_id,
            exclude_token=matched_token if not closed else matched_token,
            offset=0,
            limit=PAGE_SIZE,
        )
        copy = landing_copy()
        return render_template(
            "student/catchup_landing.html",
            course_code=course_code,
            signed_in_line=signed_in_line(display_name),
            matched=matched,
            others=others,
            others_total=total,
            offset=0,
            page_size=PAGE_SIZE,
            closed=closed,
            closed_detail=copy["closed_detail"].format(
                date=deadline_label(
                    link,
                    school.ended_at_for_participation_run(
                        class_id, str(link.get("run_key") or "")
                    )
                    if link
                    else "",
                )
                if link
                else ""
            ),
            copy=copy,
            school_name=SCHOOL_NAME,
            loading=False,
        )

    @bp.route("/student/catchup/sign-out")
    def student_catchup_landing_sign_out():
        """Clear the catch-up session and return to sign-in.

        The join code is not placed on the URL.
        """
        from catchup_session import clear_catchup_browser_session

        clear_catchup_browser_session(session)
        # MCK-42: signed_out=1 tells the landing page to clear the device's
        # unsent-answer outbox (localStorage) for the next student.
        return redirect(url_for("landing", signed_out=1))

    @bp.route("/api/student/catchup-landing")
    def api_student_catchup_landing():
        """JSON rows for catch-up landing pagination."""
        from catchup_landing import PAGE_SIZE, list_open_catchup_rows, peek_landing_session

        held = peek_landing_session(session)
        if held is None:
            return jsonify({"ok": False, "error": "Sign in again."}), 403
        try:
            offset = max(0, int(request.args.get("offset") or 0))
        except (TypeError, ValueError):
            offset = 0
        rows, total = list_open_catchup_rows(
            school,
            int(held["class_id"]),
            int(held["student_id"]),
            exclude_token=str(held.get("matched_token") or ""),
            offset=offset,
            limit=PAGE_SIZE,
        )
        try:
            from feature_gates import class_owner as _class_owner
            from feature_gates import scope_catchup_links as _scope_catchup
        except ImportError:
            from lms.feature_gates import class_owner as _class_owner
            from lms.feature_gates import scope_catchup_links as _scope_catchup
        listed = school.list_catchup_links(int(held["class_id"]))
        visible = _scope_catchup(_class_owner(school, int(held["class_id"])), listed)
        if len(visible) != len(listed):
            allowed = {
                (
                    str(row.get("live_module") or ""),
                    str(row.get("live_slot") or ""),
                    str(row.get("run_key") or ""),
                )
                for row in visible
            }
            rows = [
                row
                for row in rows
                if (
                    str(row.get("live_module") or ""),
                    str(row.get("live_slot") or ""),
                    str(row.get("run_key") or ""),
                )
                in allowed
            ]
            total = len(rows)
        return jsonify(
            {
                "ok": True,
                "rows": rows,
                "offset": offset,
                "total": total,
                "has_more": offset + len(rows) < total,
            }
        )

    @bp.route("/api/student/dashboard")
    @student_required
    def api_student_dashboard():
        """JSON dashboard for the signed-in student only (MCK-208)."""
        ids = _pin_portal_identity(school)
        if ids is None:
            return jsonify({"ok": False, "error": "Sign in with your PIN."}), 403
        class_id, student_id = ids
        handover = _student_dashboard_live_handover(school, class_id, student_id)
        return jsonify(
            {
                "ok": True,
                "dashboard": school.student_dashboard_payload(class_id, student_id),
                "live": handover,
            }
        )

    def _stmtg_tester_identity() -> tuple[dict[str, Any], int, int] | None:
        """Offering, class, and student for a student session, live or not.

        Live join fills ``_student_identity``. A student-code session that
        is not in a live class still has offering and class ids on the
        session, which is enough to accept tester feedback.
        """
        ident = _student_identity()
        if ident is not None and ident[2] not in (None, ""):
            return ident[0], int(ident[1]), int(ident[2])
        class_id = session.get("student_class_id")
        student_id = session.get("student_id")
        offering_id = session.get("student_offering_id")
        if class_id in (None, "") or student_id in (None, ""):
            return None
        offering = None
        if offering_id not in (None, ""):
            try:
                offering = school.get_offering(int(offering_id))
            except KeyError:
                offering = None
        if offering is None:
            cls = school.game.get_class(int(class_id))
            if cls.get("offering_id"):
                offering = school.get_offering(int(cls["offering_id"]))
        if offering is None:
            return None
        return offering, int(class_id), int(student_id)

    @bp.route("/student/tester-feedback", methods=["POST"])
    @student_required
    def student_tester_feedback():
        """Send STMTG tester feedback to Linear as text.

        The response is ``{"ok": true}`` or a generic error. The submitted
        text is not logged and is not returned.
        """
        import tester_feedback as tester_feedback_mod

        ident = _stmtg_tester_identity()
        if ident is None:
            return jsonify({"ok": False, "error": "Not joined."}), 403
        offering, class_id, student_id = ident
        if not _viewer_is_stmtg_tester(offering, student_id):
            return jsonify({"ok": False, "error": "Feedback is not available right now."}), 403
        body = request.get_json(silent=True) or {}
        text = body.get("text") if isinstance(body, dict) else ""
        if text in (None, "") and request.form:
            text = request.form.get("text") or ""
        names = set(school.class_codenames(int(class_id)).values())
        try:
            tester_feedback_mod.post_tester_feedback(
                str(text or ""), forbidden_names=names
            )
        except ValueError as exc:
            message = str(exc)
            status = 503 if "not available" in message else 400
            return jsonify({"ok": False, "error": message}), status
        return jsonify({"ok": True})

    def _dashboard_payload(class_id: int, sort: str) -> dict[str, Any]:
        """Spreadsheet JSON with offering metadata attached."""
        show_tests = _include_test_students()
        payload = school.game.dashboard(
            class_id, sort, include_test_students=show_tests
        )
        payload["class"] = school.enrich_class(payload["class"])
        # MCK-198: participation column and catch-up links (staff-only route).
        try:
            payload["participation"] = school.class_participation(
                class_id, include_test_students=show_tests
            )
            payload["catchup_links"] = _catchup_links_payload(class_id)
        except Exception:  # noqa: BLE001 - never break the gradebook
            logger.exception("participation payload failed class=%s", class_id)
        return payload

    # --- MCK-198: individual participation + catch-up link ---------------

    def _catchup_url(link: dict[str, Any]) -> str:
        """Canonical shared catch-up URL for one class lesson (MCK-207)."""
        from participation import catchup_lesson_slug

        course = school._course_code_for_class(int(link["class_id"])).upper()
        slug = catchup_lesson_slug(str(link.get("live_module") or ""), str(link.get("live_slot") or ""))
        return url_for(
            "catchup_page_canonical",
            course=course,
            slug=slug,
            token=str(link["token"]),
            _external=True,
        )

    def _catchup_links_payload(class_id: int) -> list[dict[str, Any]]:
        """Catch-up rows the class owner may show.

        Full catch-up keeps every row. The free plan keeps the newest
        class inside the history window. The URL is built here and is
        not logged.

        Args:
            class_id: Game-show ``classes.id``.
        """
        visible = scope_catchup_links(
            class_owner(school, int(class_id)),
            school.list_catchup_links(class_id),
        )
        return [
            {
                **link,
                "class_id": int(class_id),
                "is_open": bool(link["is_open"]),
                "discarded": bool(link.get("discarded")),
                "url": _catchup_url({**link, "class_id": int(class_id)}),
            }
            for link in visible
        ]

    @bp.route("/api/classes/<int:class_id>/participation")
    @login_required
    def api_class_participation(class_id: int):
        """Teacher-only per-module participation column (separate from team points)."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        return jsonify({"ok": True, **school.class_participation(class_id),
                        "catchup_links": _catchup_links_payload(class_id)})

    @bp.route("/api/classes/<int:class_id>/participation/start-check")
    @login_required
    def api_participation_start_check(class_id: int):
        """Whether Run Live Class should ask to finish the open module."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        selected = str(request.args.get("module") or "").strip()
        prompt = school.participation_start_prompt(class_id, selected)
        return jsonify({"ok": True, "prompt": prompt})

    @bp.route("/api/classes/<int:class_id>/participation/modules/<module>/complete", methods=["POST"])
    @login_required
    def api_mark_module_complete(class_id: int, module: str):
        """'Mark Module N Complete' (reversible with ``{"complete": false}``)."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        user = current_user()
        state = school.set_module_complete(
            class_id, str(module)[:20], bool(body.get("complete", True)),
            user_id=int(user["id"]) if user else None,
        )
        return jsonify({"ok": True, "module": module, "complete": state})

    @bp.route("/api/classes/<int:class_id>/participation/modules/<module>/finish", methods=["POST"])
    @login_required
    def api_finish_participation_module(class_id: int, module: str):
        """Log module total column and mark the module complete (MCK-230)."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        sort = str(body.get("sort") or "az")
        user = current_user()
        try:
            dash = school.finish_participation_module(
                class_id,
                str(module)[:20],
                user_id=int(user["id"]) if user else None,
                sort=sort,
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "module": module, "dashboard": dash})

    @bp.route("/api/classes/<int:class_id>/participation/modules/<module>/settings", methods=["POST"])
    @login_required
    def api_module_settings(class_id: int, module: str):
        """Save 'Level 4 needs group work' (Phase 1 stores only; 409 once complete)."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        user = current_user()
        try:
            value = school.set_module_l4_needs_group(
                class_id, str(module)[:20], bool(body.get("l4_needs_group")),
                user_id=int(user["id"]) if user else None,
            )
        except PermissionError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        return jsonify({"ok": True, "module": module, "l4_needs_group": value})

    @bp.route("/api/classes/<int:class_id>/catchup-links")
    @login_required
    def api_catchup_links(class_id: int):
        """Catch-up links per live class, for the Lesson Slides page."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        return jsonify({"ok": True, "catchup_links": _catchup_links_payload(class_id)})

    @bp.route("/api/classes/<int:class_id>/catchup-links/<token>", methods=["POST"])
    @login_required
    def api_catchup_link_toggle(class_id: int, token: str):
        """Open or close one catch-up link: ``{"open": true|false}``."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        if not school.set_catchup_link_open(class_id, token, bool(body.get("open"))):
            return jsonify({"ok": False, "error": "Not found."}), 404
        return jsonify({"ok": True, "open": bool(body.get("open"))})

    @bp.route(
        "/api/classes/<int:class_id>/participation/runs/<path:run_key>/discard",
        methods=["POST"],
    )
    @login_required
    def api_discard_participation_run(class_id: int, run_key: str):
        """Soft-discard a live run so its answers do not count toward participation."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        if not body.get("confirm"):
            return jsonify({"ok": False, "error": "Confirmation required."}), 400
        user = current_user()
        if not school.set_participation_run_discarded(
            class_id,
            run_key,
            True,
            user_id=int(user["id"]) if user else None,
        ):
            return jsonify({"ok": False, "error": "Not found."}), 404
        return jsonify({"ok": True, "discarded": True})

    @bp.route(
        "/api/classes/<int:class_id>/participation/runs/<path:run_key>/class-kind",
        methods=["POST"],
    )
    @login_required
    def api_set_participation_run_class_kind(class_id: int, run_key: str):
        """Teacher toggle: mark the ended run real class or test (MCK-207)."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        if "real" not in body:
            return jsonify({"ok": False, "error": "Missing real."}), 400
        user = current_user()
        if not school.set_participation_run_real(
            class_id,
            run_key,
            bool(body.get("real")),
            user_id=int(user["id"]) if user else None,
        ):
            return jsonify({"ok": False, "error": "Not found."}), 404
        kind = school.participation_run_kind(class_id, run_key) or {}
        return jsonify({"ok": True, "real": bool(body.get("real")), **kind})

    @bp.route("/staff/classes/<int:class_id>/participation")
    @login_required
    def staff_participation_page(class_id: int):
        """MCK-220: legacy URL → Participation tab (module table merged into main grid)."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        return redirect(
            url_for("staff_course", class_id=class_id, tab="ap", view="participation")
        )

    def _serve_catchup_page(token: str, link: dict[str, Any]):
        """Shared catch-up page: pick a codename, then PIN (MCK-207)."""
        import time as _time

        from participation import (
            CATCHUP_PIN_IP_LIMIT,
            catchup_header_kicker,
            CATCHUP_SESSION_S,
            COPY_CATCHUP_TAG,
            COPY_CLOSED,
            KEY_NAME_UNKNOWN,
            KEY_PIN_HELP,
            KEY_PIN_IP_LOCKED,
            KEY_PIN_LABEL,
            KEY_PIN_LOOKS_LIKE_JOIN_CODE,
            KEY_PIN_NOT_SET,
            KEY_PIN_WRONG,
            PIN_MAX_ATTEMPTS,
            PIN_WINDOW_S,
            catchup_ip_limit_key,
            catchup_link_ip_limit_key,
            copy,
        )

        from catchup_session import (
            clear_global_student_keys,
            codename_for_student,
            establish_catchup_identity,
            identity_for_class,
            landing_pending_student,
            CATCHUP_IDENTITY_KEY,
        )

        class_id = int(link["class_id"])
        if not catchup_row_allowed(school, class_id, link):
            abort(404)
        course_code = school._course_code_for_class(class_id)
        header_kicker = catchup_header_kicker(
            course_code, str(link.get("live_module") or ""), str(link.get("live_slot") or "")
        )
        page_ctx = {"token": token, "header_kicker": header_kicker}

        def catchup_play_mode() -> bool:
            """True when this request should show catch-up questions (not the dashboard)."""
            return str(request.args.get("play") or "").strip().lower() in {
                "1",
                "true",
                "yes",
            }

        def render_catchup_questions(sid: int, **extra: Any) -> Any:
            """Signed-in catch-up question page (MCK-198 renderer)."""
            progress = school.student_module_participation(
                class_id, int(sid), str(link["live_module"])
            )
            return render_template(
                "student/catchup.html",
                line=progress.get("line"),
                questions=school.catchup_questions(token, int(sid)),
                tag=COPY_CATCHUP_TAG,
                codename=codename_for_student(school, class_id, int(sid)),
                **extra,
                **page_ctx,
            )

        def go_student_dashboard(sid: int) -> Any:
            """Bind PIN portal session and open the class dashboard (MCK-208)."""
            try:
                bind_pin_portal_session(session, school, class_id, int(sid))
            except PermissionError:
                from participation import KEY_PIN_SIGN_IN_AGAIN

                session.pop(CATCHUP_IDENTITY_KEY, None)
                clear_global_student_keys(session)
                return pin_screen(KEY_PIN_SIGN_IN_AGAIN)
            live_resp = _student_dashboard_redirect_if_live(school, class_id, int(sid))
            if live_resp is not None:
                return live_resp
            if catchup_play_mode():
                return render_catchup_questions(int(sid))
            set_dashboard_focus_catchup(session, token)
            return redirect(url_for("student_dashboard"))

        def closed() -> Any:
            return render_template(
                "student/catchup.html", closed=COPY_CLOSED, **page_ctx
            ), 403

        from catchup_landing import link_still_open

        # is_open alone stays 1 after the deadline. The stored open_until
        # is what closes the direct URL and every POST on this page,
        # including answer submit.
        if not link_still_open(school, {**link, "class_id": class_id}):
            return closed()
        student_id = identity_for_class(session, school, class_id)
        action = request.form.get("action") if request.method == "POST" else None

        def pin_screen(error_key: str | None = None) -> Any:
            return render_template(
                "student/catchup.html",
                pin_step=True,
                pin_label=copy(KEY_PIN_LABEL),
                pin_help=copy(KEY_PIN_HELP),
                error=copy(error_key) if error_key else None,
                **page_ctx,
            )

        def verify_pin_for_pending() -> Any:
            """PIN for the landing-matched student (no name picker)."""
            target_sid = landing_pending_student(session, class_id)
            if target_sid is None:
                return redirect(url_for("landing"))
            client_ip_key = join_limit_ip_key(request_client_ip())
            global_ip_key = catchup_ip_limit_key(client_ip_key)
            link_ip_key = catchup_link_ip_limit_key(token, client_ip_key)
            limits = {
                global_ip_key: CATCHUP_PIN_IP_LIMIT,
                link_ip_key: PIN_MAX_ATTEMPTS,
            }
            reserved = school.reserve_catchup_pin_attempts(
                [global_ip_key, link_ip_key], limits, window_s=PIN_WINDOW_S
            )
            if reserved is None:
                return pin_screen(KEY_PIN_IP_LOCKED)
            gate = school.catchup_pin_student_gate(class_id, int(target_sid))
            if gate:
                school.release_catchup_pin_attempts(reserved)
                return pin_screen(gate)
            result = school.catchup_verify_student_pin(
                class_id, int(target_sid), request.form.get("pin", "")
            )
            if result["ok"]:
                school.release_catchup_pin_attempts(reserved)
                establish_catchup_identity(
                    session,
                    school,
                    class_id,
                    int(result["student_id"]),
                    str(result.get("pin_stamp") or ""),
                )
                return go_student_dashboard(int(result["student_id"]))
            if result["reason"] == "wrong":
                school.record_catchup_pin_failure(class_id, int(target_sid))
            else:
                school.release_catchup_pin_attempts(reserved)
            return pin_screen(
                {
                    "looks_like_join_code": KEY_PIN_LOOKS_LIKE_JOIN_CODE,
                    "not_set": KEY_PIN_NOT_SET,
                }.get(result["reason"], KEY_PIN_WRONG)
            )

        if action == "pin":
            pending_sid = landing_pending_student(session, class_id)
            if (
                student_id is not None
                and pending_sid is not None
                and int(pending_sid) != int(student_id)
            ):
                session.pop(CATCHUP_IDENTITY_KEY, None)
                clear_global_student_keys(session)
                student_id = None
            if student_id is not None and pending_sid is None:
                return go_student_dashboard(int(student_id))
            return verify_pin_for_pending()
        elif action == "pin_later" and student_id is not None:
            if not school.require_catchup_pin_change(class_id):
                session[f"catchup_pin_later:{class_id}:{student_id}"] = True
        elif action == "set_catchup_pin" and student_id is not None:
            pin = str(request.form.get("pin") or "")
            confirm = str(request.form.get("confirm") or "")
            pin_error = None
            pin_saved_toast = None
            if pin != confirm:
                pin_error = "PINs must match."
            else:
                try:
                    school.authorize_student_pin_change(
                        class_id, int(student_id), session
                    )
                    school.set_student_pin(
                        class_id, int(student_id), pin, pin_set_by="student"
                    )
                    school.refresh_catchup_portal_pin_stamp(
                        session, class_id, int(student_id)
                    )
                    session.pop(f"catchup_pin_later:{class_id}:{student_id}", None)
                    pin_saved_toast = "PIN saved. You're set for catch-up."
                except PermissionError:
                    from participation import KEY_PIN_SIGN_IN_AGAIN

                    pin_error = copy(KEY_PIN_SIGN_IN_AGAIN)["text"]
                except ValueError as exc:
                    pin_error = str(exc)
            pin_prompt = school.catchup_pin_prompt_for_student(class_id, int(student_id))
            if pin_error or (pin_prompt and pin_prompt.get("forced")):
                return render_template(
                    "student/catchup.html",
                    pin_modal=True,
                    pin_prompt=pin_prompt or {"forced": False, "show": True},
                    pin_error=pin_error,
                    codename=codename_for_student(school, class_id, int(student_id)),
                    **page_ctx,
                )
            return go_student_dashboard(int(student_id))
        elif request.method == "POST" and student_id is not None and action not in {
            "pin", "set_catchup_pin", "pin_later"
        }:
            if school.catchup_pin_change_blocks_student(class_id, int(student_id), session):
                pin_prompt = school.catchup_pin_prompt_for_student(class_id, int(student_id))
                return render_template(
                    "student/catchup.html",
                    pin_modal=True,
                    pin_prompt=pin_prompt or {"forced": True, "show": True},
                    pin_error="Set a PIN before answering catch-up questions.",
                    codename=codename_for_student(school, class_id, int(student_id)),
                    **page_ctx,
                )
            try:
                school.submit_catchup_answer(
                    token, int(student_id), request.form.get("question_key", ""),
                    request.form.get("answer", ""),
                )
            except PermissionError:
                return closed()
            except ValueError as exc:
                from team_draw import BLANK_DRAWING_ERROR, DRAWING_TOO_LARGE_ERROR

                message = str(exc)
                if message not in (BLANK_DRAWING_ERROR, DRAWING_TOO_LARGE_ERROR):
                    raise
                return jsonify({"ok": False, "error": message}), 400
        if student_id is None:
            if landing_pending_student(session, class_id) is not None:
                return pin_screen()
            return redirect(url_for("student_catchup_landing"))
        dismiss_key = f"catchup_pin_later:{class_id}:{student_id}"
        pin_prompt = school.catchup_pin_prompt_for_student(class_id, int(student_id))
        if pin_prompt and pin_prompt.get("forced"):
            session.pop(dismiss_key, None)
        elif pin_prompt and session.get(dismiss_key):
            pin_prompt = None
        if pin_prompt and pin_prompt.get("forced"):
            return render_template(
                "student/catchup.html",
                pin_modal=True,
                pin_prompt=pin_prompt,
                codename=codename_for_student(school, class_id, int(student_id)),
                **page_ctx,
            )
        if pin_prompt and not catchup_play_mode():
            return render_template(
                "student/catchup.html",
                pin_modal=True,
                pin_prompt=pin_prompt,
                codename=codename_for_student(school, class_id, int(student_id)),
                **page_ctx,
            )
        return go_student_dashboard(int(student_id))

    @bp.route("/catchup/<course>/<slug>/<token>", methods=["GET", "POST"])
    def catchup_page_canonical(course: str, slug: str, token: str):
        """Canonical shared catch-up URL (MCK-207)."""
        from participation import catchup_lesson_slug

        link = school.get_catchup_link(token)
        if link is None:
            abort(404)
        resolved = school.resolve_catchup_link(token)
        if resolved is None:
            abort(404)
        want_course = school._course_code_for_class(int(resolved["class_id"])).upper()
        want_slug = catchup_lesson_slug(
            str(resolved.get("live_module") or ""), str(resolved.get("live_slot") or "")
        )
        want_token = str(resolved["token"])
        if request.method == "GET" and (
            course.upper() != want_course
            or slug.lower() != want_slug
            or want_token != token
        ):
            return redirect(
                url_for(
                    "catchup_page_canonical",
                    course=want_course,
                    slug=want_slug,
                    token=want_token,
                )
            )
        return _serve_catchup_page(want_token, resolved)

    @bp.route("/catchup/<token>", methods=["GET", "POST"])
    def catchup_page_legacy(token: str):
        """Legacy token-only URL; GET redirects to the canonical path."""
        from participation import catchup_lesson_slug

        link = school.get_catchup_link(token)
        if link is None:
            abort(404)
        resolved = school.resolve_catchup_link(token)
        if resolved is None:
            abort(404)
        if request.method == "GET":
            dest = url_for(
                "catchup_page_canonical",
                course=school._course_code_for_class(int(resolved["class_id"])).upper(),
                slug=catchup_lesson_slug(
                    str(resolved.get("live_module") or ""),
                    str(resolved.get("live_slot") or ""),
                ),
                token=str(resolved["token"]),
            )
            if request.query_string:
                dest = f"{dest}?{request.query_string.decode()}"
            return redirect(dest)
        return _serve_catchup_page(str(resolved["token"]), resolved)

    @bp.route("/api/classes/<int:class_id>/engagement-events")
    @login_required
    def api_class_engagement_events(class_id: int):
        """MCK-52 P0: teacher-only engagement log (first names, counts only)."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        raw_session = request.args.get("live_session")
        try:
            live_session = int(raw_session) if raw_session else None
            limit = int(request.args.get("limit") or 2000)
        except ValueError:
            return jsonify({"ok": False, "error": "Bad query."}), 400
        run_key = (request.args.get("run_key") or "").strip()[:80] or None
        events = school.list_engagement_events(
            class_id, live_session=live_session, run_key=run_key, limit=limit
        )
        return jsonify({"ok": True, "events": events})

    @bp.route("/api/classes/<int:class_id>/engagement-summary/runs")
    @login_required
    def api_engagement_summary_runs(class_id: int):
        """MCK-59: ended runs with rollup rows (teacher-only)."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        return jsonify({"ok": True, "runs": school.list_engagement_summary_runs(class_id)})

    @bp.route("/api/classes/<int:class_id>/engagement-summary")
    @login_required
    def api_engagement_summary(class_id: int):
        """MCK-59: end-of-class engagement summary for one run."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        run_key = (request.args.get("run_key") or "").strip()
        if not run_key:
            runs = school.list_engagement_summary_runs(class_id)
            if not runs:
                return jsonify({"ok": True, "summary": None, "runs": []})
            run_key = str(runs[0]["run_key"])
        summary = school.engagement_summary_for_run(
            class_id, run_key, include_test_students=_include_test_students()
        )
        if summary is None:
            return jsonify({"ok": False, "error": "No rollup for this run."}), 404
        return jsonify(
            {
                "ok": True,
                "summary": summary,
                "runs": school.list_engagement_summary_runs(class_id),
            }
        )

    @bp.route(
        "/api/classes/<int:class_id>/engagement-summary/<run_key>/students/<int:student_id>",
        methods=["PUT"],
    )
    @login_required
    def api_engagement_level_override(class_id: int, run_key: str, student_id: int):
        """MCK-61: teacher override + note (never gradebook)."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        user = current_user()
        try:
            level = int(body.get("level"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "level required (1–4)."}), 400
        note = str(body.get("note") or "")
        try:
            row = school.set_engagement_level_override(
                class_id,
                str(run_key),
                int(student_id),
                level=level,
                note=note,
                teacher_user_id=int(user["id"]) if user else 0,
            )
        except KeyError:
            return jsonify({"ok": False, "error": "Student not found."}), 404
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        summary = school.engagement_summary_for_run(
            class_id,
            str(run_key),
            include_test_students=_include_test_students(),
        )
        return jsonify({"ok": True, "override": row, "summary": summary})

    @bp.route("/api/classes/<int:class_id>/dashboard")
    @login_required
    def api_dashboard(class_id: int):
        """Grades spreadsheet payload."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        sort = request.args.get("sort") or "az"
        try:
            return jsonify({"ok": True, **_dashboard_payload(class_id, sort)})
        except Exception as exc:  # noqa: BLE001
            return _json_error(exc)

    @bp.route("/api/classes/<int:class_id>")
    @login_required
    def api_class(class_id: int):
        """One class row."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        try:
            cls = school.enrich_class(school.game.get_class(class_id))
            return jsonify({"ok": True, "class": cls})
        except Exception as exc:  # noqa: BLE001
            return _json_error(exc)

    @bp.route("/api/classes/<int:class_id>/game")
    @login_required
    def api_game_state(class_id: int):
        """Teacher game state."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        try:
            return jsonify({"ok": True, **school.game.game_state(class_id)})
        except Exception as exc:  # noqa: BLE001
            return _json_error(exc)

    @bp.route("/api/scoreboard")
    @bp.route("/api/classes/<int:class_id>/scoreboard")
    @staff_or_student_scoreboard
    def api_scoreboard(class_id: int | None = None):
        """Public board: staff uses current class; students are scoped."""
        scoped = class_id
        if session.get("student_class_id") and current_user() is None:
            scoped = int(session["student_class_id"])
            live_session_id = session.get("student_live_session_id")
            if live_session_id not in (None, ""):
                try:
                    projected = school.live_scoreboard_projection(
                        int(live_session_id)
                    )
                except (KeyError, ValueError):
                    projected = None
                if projected is None:
                    return jsonify(
                        {
                            "ok": True,
                            "teams": [],
                            "live": False,
                            "hidden": True,
                        }
                    )
                return jsonify(projected)
        try:
            return jsonify(school.game.scoreboard(scoped))
        except Exception as exc:  # noqa: BLE001
            return _json_error(exc)

    @bp.route("/api/sessions/<int:session_id>/log")
    @login_required
    def api_session_log(session_id: int):
        """Plain-text game log for a class this staff member owns.

        A missing session and a session the caller does not own return the
        same 404, so ids cannot be probed. IT is limited to its own school
        via ``teacher_owns_class``.
        """
        hidden = jsonify({"ok": False, "error": "Not found"}), 404
        user = current_user()
        if user is None:
            return jsonify({"ok": False, "error": "Authentication required."}), 401
        try:
            with school.game._lock:
                row = school.game.conn.execute(
                    "SELECT class_id FROM sessions WHERE id = ?",
                    (int(session_id),),
                ).fetchone()
        except Exception as exc:  # noqa: BLE001
            return _json_error(exc)
        if row is None or not school.teacher_owns_class(
            int(user["id"]), int(row["class_id"])
        ):
            return hidden
        try:
            path = school.game.session_log_path(session_id)
            return (
                path.read_text(encoding="utf-8"),
                200,
                {"Content-Type": "text/plain; charset=utf-8"},
            )
        except (KeyError, FileNotFoundError):
            return hidden
        except Exception as exc:  # noqa: BLE001
            return _json_error(exc)

    @bp.route("/api/classes/<int:class_id>/log-context")
    @login_required
    def api_log_context(class_id: int):
        """Live-class calendar context for overlays and date pickers."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        try:
            return jsonify(school.log_context_for_class(class_id))
        except Exception as exc:  # noqa: BLE001
            return _json_error(exc)

    @bp.route("/api/quick-phrases")
    @staff_required
    def api_quick_phrases():
        """Staff list of active evidence phrases (optional category filter)."""
        category = (request.args.get("category") or "").strip() or None
        return jsonify(
            {
                "ok": True,
                "phrases": school.list_quick_phrases(category=category, active_only=True),
                "processes": school.list_math_processes(),
            }
        )

    @bp.route("/api/classes/<int:class_id>/attendance-grid")
    @login_required
    def api_attendance_grid(class_id: int):
        """Weekday attendance grid for the Attendance sub-tab."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        sort = request.args.get("sort") or "az"
        try:
            return jsonify(
                school.attendance_week_grid(
                    class_id,
                    sort=sort,
                    include_test_students=_include_test_students(),
                )
            )
        except Exception as exc:  # noqa: BLE001
            return _json_error(exc)

    @bp.route("/api/classes/<int:class_id>/mood-grid")
    @login_required
    def api_mood_grid(class_id: int):
        """Weekday mood grid for the Mood sub-tab."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        sort = request.args.get("sort") or "az"
        try:
            return jsonify(
                school.mood_week_grid(
                    class_id,
                    sort=sort,
                    include_test_students=_include_test_students(),
                )
            )
        except Exception as exc:  # noqa: BLE001
            return _json_error(exc)

    @bp.route("/api/classes/<int:class_id>/show-rank", methods=["POST"])
    @login_required
    def api_show_rank(class_id: int):
        """Toggle whether students see class rank on their board."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            raw = body.get("enabled")
            if isinstance(raw, str):
                enabled = raw.strip().lower() in {"1", "true", "yes", "on"}
            else:
                enabled = bool(raw)
            return school.game.set_show_rank(class_id, enabled)

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/attendance-day")
    @login_required
    def api_attendance_day(class_id: int):
        """Present roster for one logged school day."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        chosen = _optional_date(request.args.get("date"))
        if chosen is None:
            return _json_error(ValueError("date is required (YYYY-MM-DD)"))
        try:
            return jsonify(school.attendance_for_date(class_id, chosen))
        except Exception as exc:  # noqa: BLE001
            return _json_error(exc)

    @bp.route("/api/classes/<int:class_id>/attendance-day/clear", methods=["POST"])
    @login_required
    def api_clear_attendance_day(class_id: int):
        """Delete sessions (attendance + participation) for one school day."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            chosen = _optional_date(body.get("date"))
            if chosen is None:
                raise ValueError("date is required (YYYY-MM-DD)")
            sort = str(body.get("sort") or "az")
            return school.clear_attendance_day(
                class_id,
                chosen,
                sort=sort,
                include_test_students=_include_test_students(),
            )

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/feedback-live/clear", methods=["POST"])
    @login_required
    def api_clear_live_class_feedback(class_id: int):
        """Delete How-was-class rows for one live class column (e.g. M1C1)."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            key = str(body.get("key") or body.get("live_key") or "").strip()
            return school.clear_live_class_feedback(class_id, key)

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/participation-grid")
    @login_required
    def api_participation_grid(class_id: int):
        """Semester calendar participation grid for the Participation sub-tab."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        sort = request.args.get("sort") or "az"
        try:
            return jsonify(
                school.participation_week_grid(
                    class_id,
                    sort=sort,
                    include_test_students=_include_test_students(),
                )
            )
        except Exception as exc:  # noqa: BLE001
            return _json_error(exc)

    @bp.route("/api/classes/<int:class_id>/ap-round-profiles", methods=["GET", "PUT"])
    @login_required
    def api_ap_round_profiles(class_id: int):
        """Read or replace Open Question action profiles for this course section."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        cls = school.game.get_class(int(class_id))
        offering_id = cls.get("offering_id")
        if not offering_id:
            return _json_error(ValueError("This class has no course offering"))
        if request.method == "GET":
            try:
                doc = school.get_offering_ap_round_profiles(int(offering_id))
                return jsonify({"ok": True, "document": doc})
            except Exception as exc:  # noqa: BLE001
                return _json_error(exc)
        payload = request.get_json(silent=True) or {}
        document = payload.get("document", payload)
        try:
            saved = school.set_offering_ap_round_profiles(int(offering_id), document)
            return jsonify({"ok": True, "document": saved})
        except Exception as exc:  # noqa: BLE001
            return _json_error(exc)

    def _portfolio_guard(class_id: int):
        """Local-only Portfolio APIs; 404 in production."""
        from portfolio.flags import portfolio_tab_enabled

        if not portfolio_tab_enabled():
            return jsonify({"ok": False, "error": "Not found"}), 404
        return _require_class_staff(class_id)

    def _portfolio_course_code(class_id: int) -> str:
        """Ontario code for the open class."""
        cls = school.enrich_class(school.game.get_class(class_id))
        offering = (
            school.get_offering(int(cls["offering_id"])) if cls.get("offering_id") else None
        )
        return str((offering or {}).get("ontario_code") or cls.get("ontario_code") or "").upper()

    @bp.route("/api/classes/<int:class_id>/portfolio/context")
    @login_required
    def api_portfolio_context(class_id: int):
        """Build-card context for one module (local Portfolio tab)."""
        denied = _portfolio_guard(class_id)
        if denied:
            return denied
        from portfolio.assemble import assemble_context

        code = _portfolio_course_code(class_id)
        module_n = int(request.args.get("module") or 1)
        try:
            payload = assemble_context(
                course_code=code,
                module_number=module_n,
                data_dir=Path(app.config["DATA_DIR"]),
            )
            payload["ok"] = True
            return jsonify(payload)
        except Exception as exc:  # noqa: BLE001
            return _json_error(exc)

    @bp.route("/api/classes/<int:class_id>/portfolio/generate", methods=["POST"])
    @login_required
    def api_portfolio_generate(class_id: int):
        """Write M{n}.json, return HTML, optionally convert a Drive Doc."""
        denied = _portfolio_guard(class_id)
        if denied:
            return denied
        from live_class_slides import GoogleSlidesClient, SlidesConnectRequired
        from portfolio.assemble import generate_portfolio
        from portfolio.drive_docs import create_or_update_assignment_doc

        user = current_user()
        assert user is not None
        body = request.get_json(silent=True) or {}
        if body.get("drive"):
            deny_unless("portfolio_gdoc")
        code = _portfolio_course_code(class_id)
        module_n = int(body.get("module") or 1)
        try:
            result = generate_portfolio(
                course_code=code,
                module_number=module_n,
                edits=body.get("edits") if isinstance(body.get("edits"), dict) else None,
                data_dir=Path(app.config["DATA_DIR"]),
            )
        except Exception as exc:  # noqa: BLE001
            return _json_error(exc)
        if body.get("drive"):
            semester = school.get_active_semester() or {}
            try:
                client = GoogleSlidesClient(school, user=user)
                drive = create_or_update_assignment_doc(
                    client,
                    ontario_code=code,
                    module_number=module_n,
                    title=str(result.get("doc_title") or "Portfolio Assignment"),
                    doc_html=str(result.get("doc_html") or ""),
                    semester_label=str(semester.get("label") or ""),
                )
                result["drive_id"] = drive.get("id")
                result["drive_url"] = drive.get("url")
                result["needs_drive_connect"] = False
            except SlidesConnectRequired:
                result["needs_drive_connect"] = True
            except Exception as exc:  # noqa: BLE001
                report_swallowed("portfolio drive doc")
                result["drive_error"] = str(exc)
                result["needs_drive_connect"] = False
        return jsonify(result)

    @bp.route("/api/classes/<int:class_id>/portfolio/marking", methods=["GET", "POST"])
    @login_required
    def api_portfolio_marking(class_id: int):
        """Local marking sheet: suggestions, overrides, look-for tallies."""
        denied = _portfolio_guard(class_id)
        if denied:
            return denied
        from live_class_slides import GoogleSlidesClient, SlidesConnectRequired
        from portfolio.lookfors import LOOKFOR_IDS, LOOKFOR_LABELS, empty_tally
        from portfolio.marking import (
            drive_path_label,
            list_local_submissions,
            match_submission_for_student,
            read_local_text,
        )
        from portfolio.drive_docs import (
            ensure_portfolio_folder,
            export_google_doc_text,
            list_folder_files,
        )
        from portfolio.suggest import submission_text_from_html, suggest_levels
        from portfolio.assemble import assemble_context, list_modules

        user = current_user()
        assert user is not None
        code = _portfolio_course_code(class_id)

        def marking_sheet(*, module_n: int, score: bool) -> dict[str, Any]:
            """Assemble file list, optional suggestions, and look-for tallies."""
            n_modules = max(len(list_modules(code)), 1)
            students = [
                dict(row)
                for row in school.game.conn.execute(
                    """
                    SELECT * FROM students
                    WHERE class_id = ?
                    ORDER BY last_display, first_name
                    """,
                    (class_id,),
                )
            ]
            useful: list[str] = []
            try:
                ctx = assemble_context(
                    course_code=code,
                    module_number=module_n,
                    data_dir=Path(app.config["DATA_DIR"]),
                )
                useful = list(ctx.get("useful_words") or [])
            except Exception:  # noqa: BLE001
                report_swallowed("portfolio useful words")
            semester = school.get_active_semester() or {}
            label = str(semester.get("label") or "")
            submissions: list[dict[str, str]] = []
            drive_error = ""
            client = None
            try:
                if not has_feature(user, "portfolio_gdoc"):
                    raise SlidesConnectRequired()
                client = GoogleSlidesClient(school, user=user)
                folder_id = ensure_portfolio_folder(
                    client,
                    ontario_code=code,
                    module_number=module_n,
                    semester_label=label,
                )
                submissions.extend(list_folder_files(client, folder_id))
            except SlidesConnectRequired:
                drive_error = "Drive not connected"
            except Exception as exc:  # noqa: BLE001
                report_swallowed("portfolio drive folder")
                drive_error = str(exc)
            submissions.extend(list_local_submissions(code, module_n))
            saved = {
                int(r["student_id"]): r
                for r in school.list_portfolio_mark_rows(class_id, module_n)
            }
            tallies = school.lookfor_tallies_for_module(
                class_id, module_n, n_modules=n_modules
            )
            rows = []
            for student in students:
                sid = int(student["id"])
                match = match_submission_for_student(student, submissions)
                stored = saved.get(sid) or {}
                suggested = stored.get("suggested") or {}
                if score:
                    text = ""
                    if match and match.get("source") == "local":
                        text = read_local_text(Path(match["id"]))
                    elif match and match.get("source") == "drive" and client is not None:
                        try:
                            raw = export_google_doc_text(client, match["id"])
                            text = (
                                submission_text_from_html(raw)
                                if "<" in raw[:200].lower()
                                else raw
                            )
                        except Exception:  # noqa: BLE001
                            report_swallowed("portfolio doc text")
                            text = ""
                    if text:
                        suggested = suggest_levels(text, useful)
                        school.upsert_portfolio_mark(
                            class_id=class_id,
                            ontario_code=code,
                            module_number=module_n,
                            student_id=sid,
                            suggested=suggested,
                        )
                look = tallies.get(str(sid)) or empty_tally()
                rows.append(
                    {
                        "id": sid,
                        "codename": student.get("last_display")
                        or student.get("first_name")
                        or "",
                        "name": student.get("last_display")
                        or student.get("first_name")
                        or "",
                        "suggested": suggested,
                        "override": stored.get("override") or {},
                        "lookfors": look,
                        "submission": match,
                    }
                )
            return {
                "ok": True,
                "students": rows,
                "submissions": submissions,
                "files": [
                    {
                        "name": row.get("name") or "",
                        "source": row.get("source") or "",
                    }
                    for row in submissions
                ],
                "drive_error": drive_error,
                "drive_path": drive_path_label(code, module_n, label),
                "lookfor_ids": list(LOOKFOR_IDS),
                "lookfor_labels": LOOKFOR_LABELS,
            }

        if request.method == "POST":
            body = request.get_json(silent=True) or {}
            module_n = int(body.get("module") or 1)
            action = str(body.get("action") or "").strip().lower()
            if action == "run":
                return jsonify(marking_sheet(module_n=module_n, score=True))
            student_id = int(body.get("student_id") or 0)
            override = body.get("override") if isinstance(body.get("override"), dict) else {}
            existing_rows = {
                int(r["student_id"]): r
                for r in school.list_portfolio_mark_rows(class_id, module_n)
            }
            prev = (existing_rows.get(student_id) or {}).get("override") or {}
            merged = dict(prev)
            merged.update({k: v for k, v in override.items() if v})
            for key, val in list(merged.items()):
                if val in ("", None):
                    merged.pop(key, None)
            school.upsert_portfolio_mark(
                class_id=class_id,
                ontario_code=code,
                module_number=module_n,
                student_id=student_id,
                override=merged or None,
            )
            return jsonify({"ok": True})

        module_n = int(request.args.get("module") or 1)
        return jsonify(marking_sheet(module_n=module_n, score=False))

    @bp.route("/api/classes/<int:class_id>/gradebook")
    @login_required
    def api_gradebook(class_id: int):
        """Weighted gradebook (Ontario 10/65/25) with Module 1 portfolio auto-score."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        sort = request.args.get("sort") or "az"
        try:
            return jsonify(
                school.gradebook_for_class(
                    class_id,
                    sort=sort,
                    include_test_students=_include_test_students(),
                )
            )
        except Exception as exc:  # noqa: BLE001
            return _json_error(exc)

    @bp.route("/api/classes/<int:class_id>/grade-weights", methods=["GET", "POST"])
    @login_required
    def api_grade_weights(class_id: int):
        """Read or update category weights (extension point for edit UI)."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        if request.method == "GET":
            try:
                return jsonify(
                    {"ok": True, "weights": school.grade_weights_for_class(class_id)}
                )
            except Exception as exc:  # noqa: BLE001
                return _json_error(exc)

        def run(body):
            """Apply one staff JSON mutation for this class."""
            raw = body.get("weights") if isinstance(body.get("weights"), dict) else body
            weights = school.set_grade_weights(class_id, raw or {})
            return {"weights": weights}

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/grade-scheme", methods=["GET", "POST"])
    @login_required
    def api_grade_scheme(class_id: int):
        """Read or save course weights plus Term Mark portfolio rules."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        if request.method == "GET":
            try:
                book = school.gradebook_for_class(
                    class_id,
                    sort="az",
                    include_test_students=_include_test_students(),
                )
                return jsonify({"ok": True, "scheme": book.get("scheme")})
            except Exception as exc:  # noqa: BLE001
                return _json_error(exc)

        def run(body):
            """Persist the staff grading scheme and return the full book."""
            saved = school.set_grade_scheme(class_id, body or {})
            book = school.gradebook_for_class(
                class_id,
                sort="az",
                include_test_students=_include_test_students(),
            )
            return {"scheme": saved, "gradebook": book}

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/module-reflections", methods=["POST"])
    @login_required
    def api_module_reflections(class_id: int):
        """Staff-log whether a student answered all module reflection questions."""

        def run(body):
            """Persist one student/module reflections-complete flag."""
            try:
                student_id = int(body.get("student_id"))
            except (TypeError, ValueError) as exc:
                raise ValueError("student_id is required") from exc
            try:
                module_number = int(body.get("module_number") or body.get("module") or 1)
            except (TypeError, ValueError) as exc:
                raise ValueError("module_number must be an integer") from exc
            if module_number < 1:
                raise ValueError("module_number must be 1 or greater")
            show_tests = _include_test_students()
            roster = {
                int(row["id"])
                for row in school.game.dashboard(
                    class_id, sort="az", include_test_students=show_tests
                ).get("students")
                or []
            }
            if student_id not in roster:
                raise ValueError("Student is not on this class roster")
            complete = body.get("complete")
            if isinstance(complete, str):
                complete = complete.strip().lower() in {"1", "true", "yes", "on"}
            saved = school.set_module_reflection(
                class_id, student_id, module_number, bool(complete)
            )
            book = school.gradebook_for_class(
                class_id, sort="az", include_test_students=show_tests
            )
            return {"reflection": saved, "gradebook": book}

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/students", methods=["POST"])
    @login_required
    def api_add_student(class_id: int):
        """Add a Codename (LLOVES) or last/first (legacy)."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            _reject_basic_roster(class_id)
            try:
                return _dashboard_payload_from_add(class_id, body)
            except ValueError as exc:
                if class_is_simple(school, class_id):
                    raise ValueError(plain_name_wording(str(exc))) from exc
                raise

        return _staff_post(class_id, run)

    def _dashboard_payload_from_add(class_id: int, body: dict[str, Any]) -> dict[str, Any]:
        """Insert a roster row and return an enriched dashboard."""
        dash = school.game.add_student(
            class_id,
            first_name=str(body.get("first_name") or ""),
            last_display=str(body.get("last_display") or ""),
            sort=str(body.get("sort") or "az"),
            codename=body.get("codename"),
        )
        dash["class"] = school.enrich_class(dash["class"])
        return dash

    @bp.route("/api/classes/<int:class_id>/students/delete", methods=["POST"])
    @login_required
    def api_del_student(class_id: int):
        """Remove a roster row."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            student_id = int(body.get("student_id") or 0)
            dash = school.game.delete_student(
                class_id,
                student_id,
                sort=str(body.get("sort") or "az"),
            )
            # Saved End-of-class answers carry the name; drop them too.
            school.delete_student_live_result_snapshots(class_id, [student_id])
            school.delete_avatar_reward_grants(class_id, [student_id])
            # MCK-116 MED-2: a reused id must not inherit the Shoutout.
            forget_featured_students(school, class_id, [student_id])
            dash["class"] = school.enrich_class(dash["class"])
            return dash

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/sessions", methods=["POST"])
    @login_required
    def api_add_session(class_id: int):
        """Append a session column."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            chosen = _optional_date(body.get("meeting_date"))
            if chosen is None:
                raise ValueError("meeting_date is required (YYYY-MM-DD)")
            dash = school.game.add_session_column(
                class_id,
                chosen,
                time_label=str(body["time"]) if body.get("time") else None,
                sort=str(body.get("sort") or "az"),
            )
            dash["class"] = school.enrich_class(dash["class"])
            return dash

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/sessions/delete", methods=["POST"])
    @login_required
    def api_del_session(class_id: int):
        """Delete a session column."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            dash = school.game.delete_session_column(
                class_id,
                int(body.get("session_id") or 0),
                sort=str(body.get("sort") or "az"),
            )
            dash["class"] = school.enrich_class(dash["class"])
            return dash

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/subtotals", methods=["POST"])
    @login_required
    def api_freeze(class_id: int):
        """Freeze a SUBTOTAL column."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            dash = school.game.freeze_subtotal(
                class_id,
                name=str(body["name"]) if body.get("name") else None,
                sort=str(body.get("sort") or "az"),
            )
            dash["class"] = school.enrich_class(dash["class"])
            return dash

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/subtotals/rename", methods=["POST"])
    @login_required
    def api_rename_sub(class_id: int):
        """Rename a frozen subtotal."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            dash = school.game.rename_subtotal(
                class_id,
                int(body.get("id") or 0),
                str(body.get("name") or ""),
                sort=str(body.get("sort") or "az"),
            )
            dash["class"] = school.enrich_class(dash["class"])
            return dash

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/subtotals/delete", methods=["POST"])
    @login_required
    def api_del_sub(class_id: int):
        """Delete a frozen subtotal."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            dash = school.game.delete_subtotal(
                class_id,
                int(body.get("id") or 0),
                sort=str(body.get("sort") or "az"),
            )
            dash["class"] = school.enrich_class(dash["class"])
            return dash

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/stat-window", methods=["POST"])
    @login_required
    def api_stat_window(class_id: int):
        """Set scoreboard stats period."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            dash = school.game.set_stat_window(
                class_id,
                str(body.get("window") or ""),
                sort=str(body.get("sort") or "az"),
            )
            dash["class"] = school.enrich_class(dash["class"])
            return dash

        return _staff_post(class_id, run)
    app.register_blueprint(bp)

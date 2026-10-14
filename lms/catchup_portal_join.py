"""MCK-221: route landing join codes to live class or catch-up landing."""

from __future__ import annotations

from typing import Any

from catchup_landing import (
    COPY_LIVE_JOINING,
    COPY_WRONG_CODE,
    build_matched_card,
    clear_landing_session,
    link_still_open,
    list_open_catchup_rows,
    set_landing_session,
    landing_display_name,
    signed_in_line,
    landing_copy,
)
from catchup_portal_code import (
    decode_portal_join_code,
    normalize_portal_code,
    structured_codes_enabled,
)


def try_portal_code_join(
    db: Any,
    session: Any,
    *,
    code: str,
    name: str,
    chosen_id_raw: Any,
    request_is_json: bool,
    landing_kwargs: Any,
    render_template: Any,
    url_for: Any,
    redirect: Any,
    jsonify: Any,
    join_limit_ip_key: Any,
    record_join_failures: Any,
    bind_student_session: Any,
    next_student_endpoint: Any,
    student_url_with_token: Any,
    set_rejoin_cookie: Any,
    first_name_only: Any,
) -> Any | None:
    """Handle a structured catch-up portal code on ``/auth/student-code``.

    Returns a Flask response when this path owns the request, else ``None``.
    """
    if not structured_codes_enabled():
        return None
    normalized = normalize_portal_code(code)
    if not normalized or len(normalized) != 5:
        return None
    if decode_portal_join_code(normalized) is None:
        # Five- and six-character live session codes share this shape; only
        # checksum-valid portal codes are handled here.
        return None

    link = db.resolve_catchup_link_by_portal_code(normalized)
    if link is None:
        return _portal_fail(
            COPY_WRONG_CODE,
            code=code,
            name=name,
            request_is_json=request_is_json,
            landing_kwargs=landing_kwargs,
            render_template=render_template,
            jsonify=jsonify,
            record_join_failures=record_join_failures,
            join_limit_ip_key=join_limit_ip_key,
        )

    class_id = int(link["class_id"])
    if not name:
        return _portal_fail(
            "Enter the first name or Codename on your class roster.",
            code=code,
            name=name,
            request_is_json=request_is_json,
            landing_kwargs=landing_kwargs,
            render_template=render_template,
            jsonify=jsonify,
            record_join_failures=record_join_failures,
            join_limit_ip_key=join_limit_ip_key,
        )

    matches = db.list_roster_matches_for_class(class_id, name)
    chosen_id = None
    try:
        if str(chosen_id_raw).strip():
            chosen_id = int(chosen_id_raw)
    except (TypeError, ValueError):
        chosen_id = None
    student = None
    if chosen_id is not None:
        student = next((row for row in matches if int(row["id"]) == chosen_id), None)
        if student is None:
            return _portal_fail(
                "More than one student matches that name. Pick yours.",
                code=code,
                name=name,
                request_is_json=request_is_json,
                landing_kwargs=landing_kwargs,
                render_template=render_template,
                jsonify=jsonify,
                record_join_failures=record_join_failures,
                join_limit_ip_key=join_limit_ip_key,
            )
    elif len(matches) > 1:
        labels = db.disambiguated_roster_labels(matches)
        return _portal_fail(
            "More than one student matches that name. Pick yours.",
            code=code,
            name=name,
            request_is_json=request_is_json,
            landing_kwargs=landing_kwargs,
            render_template=render_template,
            jsonify=jsonify,
            record_join_failures=record_join_failures,
            join_limit_ip_key=join_limit_ip_key,
            candidates=labels,
            status=409,
        )
    elif len(matches) == 1:
        student = matches[0]
    else:
        return _portal_fail(
            "Double-check the code with your teacher, or make sure your "
            "username matches the roster for this course.",
            code=code,
            name=name,
            request_is_json=request_is_json,
            landing_kwargs=landing_kwargs,
            render_template=render_template,
            jsonify=jsonify,
            record_join_failures=record_join_failures,
            join_limit_ip_key=join_limit_ip_key,
        )

    student_id = int(student["id"])
    display = landing_display_name(db, class_id, student_id)

    from catchup_session import peek_catchup_identity, clear_catchup_browser_session

    ident = peek_catchup_identity(session, db)
    if ident is not None and (
        int(ident["student_id"]) != student_id or int(ident["class_id"]) != class_id
    ):
        clear_catchup_browser_session(session)

    live = db.get_active_live_session_for_class(class_id)
    if live is not None:
        return _join_live_from_portal(
            db,
            session,
            live,
            class_id,
            student,
            display,
            code=normalized,
            request_is_json=request_is_json,
            landing_kwargs=landing_kwargs,
            render_template=render_template,
            url_for=url_for,
            redirect=redirect,
            jsonify=jsonify,
            bind_student_session=bind_student_session,
            next_student_endpoint=next_student_endpoint,
            student_url_with_token=student_url_with_token,
            set_rejoin_cookie=set_rejoin_cookie,
        )

    open_now = link_still_open(db, link)
    if not open_now:
        set_landing_session(
            session,
            class_id=class_id,
            student_id=student_id,
            matched_token=str(link["token"]),
            portal_code=normalized,
            display_name=display,
        )
        return redirect(url_for("student_catchup_landing", state="closed"))

    set_landing_session(
        session,
        class_id=class_id,
        student_id=student_id,
        matched_token=str(link["token"]),
        portal_code=normalized,
        display_name=display,
    )
    if request_is_json:
        return jsonify({"ok": True, "redirect": url_for("student_catchup_landing")})
    return redirect(url_for("student_catchup_landing"))


def _portal_fail(
    msg: str,
    *,
    code: str,
    name: str,
    request_is_json: bool,
    landing_kwargs: Any,
    render_template: Any,
    jsonify: Any,
    record_join_failures: Any,
    join_limit_ip_key: Any,
    candidates: list[dict[str, Any]] | None = None,
    status: int = 401,
) -> Any:
    """Record a portal join failure and return the landing error."""
    record_join_failures([join_limit_ip_key])
    extra: dict[str, Any] = {}
    if candidates:
        extra["student_candidates"] = candidates
    if request_is_json:
        body = {"ok": False, "error": msg}
        if candidates:
            body["candidates"] = candidates
        return jsonify(body), status
    return render_template(
        "landing.html",
        **landing_kwargs(
            student_error=msg,
            student_candidates=candidates or [],
            student_code=code,
            student_name=name,
            **extra,
        ),
    ), status


def _join_live_from_portal(
    db: Any,
    session: Any,
    live: dict[str, Any],
    class_id: int,
    student: dict[str, Any],
    display: str,
    *,
    code: str,
    request_is_json: bool,
    landing_kwargs: Any,
    render_template: Any,
    url_for: Any,
    redirect: Any,
    jsonify: Any,
    bind_student_session: Any,
    next_student_endpoint: Any,
    student_url_with_token: Any,
    set_rejoin_cookie: Any,
) -> Any:
    """Join the active live session after a portal code match."""
    try:
        cls = db.game.get_class(class_id)
        offering = db.get_offering(int(cls["offering_id"]))
    except (KeyError, TypeError):
        return _portal_fail(
            COPY_WRONG_CODE,
            code=code,
            name=display,
            request_is_json=request_is_json,
            landing_kwargs=landing_kwargs,
            render_template=render_template,
            jsonify=jsonify,
            record_join_failures=lambda _: None,
            join_limit_ip_key="",
        )
    join_name = display
    try:
        join_result = db.join_live_class_session(
            int(live["id"]),
            int(student["id"]),
            codename=join_name,
        )
    except ValueError as exc:
        return _portal_fail(
            str(exc) or COPY_WRONG_CODE,
            code=code,
            name=display,
            request_is_json=request_is_json,
            landing_kwargs=landing_kwargs,
            render_template=render_template,
            jsonify=jsonify,
            record_join_failures=lambda _: None,
            join_limit_ip_key="",
            status=409,
        )
    attendee = join_result.get("attendee") or {}
    visit_token = str(attendee.get("visit_token") or "")
    participant_uuid = str(attendee.get("participant_uuid") or "")
    sid = attendee.get("student_id")
    from catchup_session import clear_catchup_browser_session

    clear_catchup_browser_session(session)
    bind_student_session(
        session,
        offering,
        cls,
        student,
        live_session_id=int(live["id"]),
        session_code=str(live.get("session_code") or code),
        visit_token=visit_token,
        participant_uuid=participant_uuid,
        unmatched=False,
    )
    endpoint = next_student_endpoint(
        db,
        class_id,
        int(sid) if sid not in (None, "") else int(student["id"]),
        visit_token=visit_token,
        unmatched=False,
    )
    dest = student_url_with_token(endpoint, visit_token)
    if request_is_json:
        return jsonify({"ok": True, "redirect": dest, "message": COPY_LIVE_JOINING})
    return render_template(
        "student/catchup_live_joining.html",
        message=COPY_LIVE_JOINING,
        redirect_url=dest,
        copy=landing_copy(),
    )

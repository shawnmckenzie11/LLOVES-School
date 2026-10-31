"""Live class, board, celebration, slides, live media, and student live pages.

Split from ``app.py`` for MCK-364. URLs, methods, decorators, and
endpoint names match the routes that were registered on the app.
"""

from __future__ import annotations

from routes_common import LaneBlueprint
from app import (
    BoardOpRejected,
    BoardSessionClosed,
    C2_TRANSFORM_MEDIA_URL,
    CELEBRATION_COPY,
    DEFAULT_LIVE_MEDIA_STEM,
    DEFAULT_LIVE_MEDIA_TITLE,
    DEFAULT_LIVE_MEDIA_URL,
    DeckCopyLiveConflict,
    DeckReplaceNotConfirmed,
    DeleteTestLiveClassConflict,
    EARNED_AVATARS,
    EARNED_AVATAR_SRC,
    EARNED_CHARACTERS,
    EXIT_FEEDBACK_SESSION_KEY,
    GroupAnswerLocked,
    HOW_WAS_CLASS,
    LAYOUT_PRESETS,
    LiveRunKindConflict,
    MGS_TEMPLATES,
    Path,
    PollBudgetExceeded,
    RankAgreeConflict,
    RankRaceStepConflict,
    RankTurnConflict,
    SCHOOL_NAME,
    TRANSFORMATIONS_ARTIFACT_ID,
    WHITEBOARD_REOPEN_CLASS_ENDED,
    WHITEBOARD_REOPEN_ENDED_REASON,
    WhiteboardReopenConflict,
    _artifact_busy,
    _board_read_pool_busy,
    _guard_live_poll,
    _json_error,
    _live_defaults_from_body,
    _note_live_session_gone,
    _optional_board_seq,
    _optional_date,
    _pin_portal_identity,
    _ready_library,
    _same_origin_post,
    _start_live_and_wake,
    _student_post_live_end_destination,
    abort,
    active_media_event,
    bind_student_session,
    board_write_closed,
    character_choices,
    clear_live_student_session_keys,
    clear_rejoin_cookie,
    clear_student_session_keys,
    cons_catalog,
    current_user,
    date,
    default_teacher_state,
    earned_avatar_keys,
    emit_answer_landed,
    emit_session_news,
    events_for_teacher_patch,
    explicit_visit_token_from_request,
    flag_work_event,
    html_escape,
    is_artifact_payload,
    is_meet_team_payload,
    is_minds_on_payload,
    json,
    json_safe,
    jsonify,
    live_media_catalog,
    live_media_url_swap_allowed,
    live_news_response,
    logger,
    login_required,
    missing_live_session,
    mood_choices,
    next_student_endpoint,
    normalize_board_key,
    prompt_event,
    prompt_response_count,
    public_celebration_board,
    public_feedback_fragment,
    record_push_stages,
    redirect,
    rejoin_token_from_cookie,
    render_template,
    request,
    resolve_pin_portal_context,
    resolve_student_live_context,
    reward_appearances,
    reward_title,
    send_from_directory,
    session,
    set_rejoin_cookie,
    shutil,
    sqlite3,
    staff_required,
    student_required,
    student_url_with_token,
    teacher_state_seq,
    time,
    url_for,
    visit_token_from_request,
)

def register_live(app, school, shared):
    """Register live-class routes on the live blueprint.

    The blueprint keeps each view's original endpoint name so ``url_for``
    and the URL-map snapshot stay on the bare function name.

    Args:
        app: Flask application.
        school: Open school database for this process.
        shared: Helpers used by more than one lane.
    """
    bp = LaneBlueprint('live', __name__)
    try:
        from features import (
            deny_unless,
            feature_required,
            feature_required_any,
            has_feature,
        )
    except ImportError:
        from lms.features import (
            deny_unless,
            feature_required,
            feature_required_any,
            has_feature,
        )
    try:
        from feature_gates import (
            WHITEBOARD_SUBMIT_REFRESH_S,
            overview_refresh_allowed,
        )
    except ImportError:
        from lms.feature_gates import (
            WHITEBOARD_SUBMIT_REFRESH_S,
            overview_refresh_allowed,
        )
    _bound_student_class_id = shared._bound_student_class_id
    _can_access_class_live_asset = shared._can_access_class_live_asset
    _content_questions_scope = shared._content_questions_scope
    _reject_basic_roster = shared._reject_basic_roster
    _require_class_staff = shared._require_class_staff
    _staff_post = shared._staff_post
    _student_identity = shared._student_identity
    _student_live_context = shared._student_live_context
    _viewer_is_stmtg_tester = shared._viewer_is_stmtg_tester

    def _whiteboard_flags(session_id: int) -> tuple[bool, bool]:
        """Return ``(realtime, submit)`` for the caller of this live session.

        A signed-in teacher uses their own flags. A student uses the
        session owner. ``session_id`` is ignored for a signed-in teacher.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        user = current_user()
        if user is not None:
            return has_feature(user, "whiteboard"), has_feature(user, "whiteboard_submit")
        try:
            from features import feature_for_session
        except ImportError:
            from lms.features import feature_for_session
        return (
            feature_for_session(int(session_id), "whiteboard"),
            feature_for_session(int(session_id), "whiteboard_submit"),
        )

    def _team_board_closed(session_id: int) -> bool:
        """True when a team board is not on this plan.

        Real-time whiteboards and group draw may use a ``team:`` board.
        Draw-and-submit stays on the student's own board.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        realtime, _submit = _whiteboard_flags(int(session_id))
        if realtime:
            return False
        try:
            from features import feature_for_session
        except ImportError:
            from lms.features import feature_for_session
        return not feature_for_session(int(session_id), "group_draw")

    def _whiteboard_overview_hold(session_id: int):
        """404 when whiteboards are off. 429 when a submit-only overview is early.

        Real-time whiteboards are not held. The hold is per reader id.

        Args:
            session_id: ``live_class_sessions.id``.

        Returns:
            A response tuple, or None when the read may continue.
        """
        realtime, submit = _whiteboard_flags(int(session_id))
        if not realtime and not submit:
            abort(404)
        if realtime:
            return None
        user = current_user()
        if user is not None:
            actor = f"u:{int(user['id'])}"
        else:
            actor = f"s:{int(session.get('student_id') or 0)}"
        tap = str(request.args.get("tap") or "").strip().lower() in {"1", "true", "yes"}
        if overview_refresh_allowed(int(session_id), actor, tap=tap):
            return None
        response = jsonify(
            {
                "ok": False,
                "error": "Refresh in a moment.",
                "refresh_ms": WHITEBOARD_SUBMIT_REFRESH_S * 1000,
            }
        )
        response.status_code = 429
        response.headers["Retry-After"] = str(WHITEBOARD_SUBMIT_REFRESH_S)
        return response


    @bp.route("/calc")
    def calc_awards_redirect():
        """Old /calc bookmark — send people to the ALC hash route."""
        return redirect("/#celebrations")

    @bp.route("/dev/whiteboard-formative")
    @bp.route("/dev/whiteboard-formative/<role>")
    def dev_whiteboard_formative(role: str = "student"):
        """Headless + shoot.py target for MCK-218 formative whiteboard mocks."""
        from local_dev_seed import local_dev_login_enabled

        if not local_dev_login_enabled():
            return jsonify({"ok": False, "error": "Not found"}), 404
        token = str(role or "student").strip().lower()
        if token not in {"student", "teacher"}:
            token = "student"
        return render_template("dev/whiteboard_formative.html", role=token)

    @bp.route("/api/celebrations", methods=["GET"])
    def public_celebrations():
        """Public read-only celebration board for ``/#celebrations``.

        Returns filled cards and the Wonder copy slots. Any failure becomes
        an empty card list so the landing page can keep Coming soon.

        Returns:
            JSON ``{cards, copy}`` with ``Cache-Control: max-age=60``.
        """
        try:
            cards = public_celebration_board(school).get("cards") or []
            if not isinstance(cards, list):
                cards = []
        except Exception:
            logger.exception("celebration board unavailable")
            cards = []
        resp = jsonify({"cards": cards, "copy": dict(CELEBRATION_COPY)})
        resp.headers["Cache-Control"] = "max-age=60"
        return resp

    @bp.route("/staff/class/<int:class_id>/run-live", methods=["POST"])
    @staff_required
    def staff_run_live_class(class_id: int):
        """Mint a live session (one per teacher) and open Set Class."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            abort(403)
        try:
            live_session = _start_live_and_wake(
                school, class_id, int(user["id"])
            )
        except ValueError as exc:
            return render_template("forbidden.html", message=str(exc)), 400
        return redirect(
            url_for(
                "staff_course",
                class_id=class_id,
                tab="live",
                run=1,
                live_session_id=live_session["id"],
            )
        )

    def _already_ended_page():
        """Say this class already ended and this request changed nothing.

        Returns:
            A short HTML page and status 409.
        """
        back = url_for("staff_home")
        body = (
            "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>Already ended</title>"
            "<link rel=\"stylesheet\" href=\"/static/lloves.css\"></head>"
            "<body><div class=\"wrap\"><h1>Already ended</h1>"
            "<p class=\"sub\">This class already ended. "
            "Nothing else was changed.</p>"
            f"<p><a class=\"btn\" href=\"{html_escape(back)}\">Back to classes</a></p>"
            "</div></body></html>"
        )
        return body, 409, {"Content-Type": "text/html; charset=utf-8"}

    def _finish_busy_page():
        """Say a finish is still open and the teacher should try again.

        A crashed worker can hold the finish claim for up to five minutes.
        This request did not End or Quit.

        Returns:
            A short HTML page and status 409.
        """
        back = url_for("staff_home")
        body = (
            "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>Try again in a moment</title>"
            "<link rel=\"stylesheet\" href=\"/static/lloves.css\"></head>"
            "<body><div class=\"wrap\"><h1>Try again in a moment</h1>"
            "<p class=\"sub\">Nothing was changed. Try again in a moment.</p>"
            f"<p><a class=\"btn\" href=\"{html_escape(back)}\">Back to classes</a></p>"
            "</div></body></html>"
        )
        return body, 409, {"Content-Type": "text/html; charset=utf-8"}

    @bp.route("/staff/class/<int:class_id>/end-live", methods=["POST"])
    @staff_required
    def staff_end_live_class(class_id: int):
        """End Class: always save attendance and participation.

        Staff may only terminate a class they own (IT in-tenant included via
        ``teacher_owns_class``), and only their one active session
        (``class_id`` must match ``get_active_live_session_for_teacher``).
        Dashboard cards always post this route with the active session's
        ``class_id``, even when that class is not the card being rendered.

        Posted ``save_attendance`` / ``save_participation`` fields are
        ignored. An old tab cannot skip either write. Live, test, and
        open office classes all save both.

        After a save, staff land on Attendance & Participation so the
        new class-day column is visible.
        """
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            abort(403)
        active = school.get_active_live_session_for_teacher(int(user["id"]))
        if active is None or int(active["class_id"]) != int(class_id):
            return _already_ended_page()
        # Test or Live was chosen at Start. A posted end-time run_mode is ignored.
        live_run = True
        # The run this request saw as active. If another End closes it
        # first, this one is a no-op instead of a new column (MCK-72).
        ended = school.finish_live_class(
            int(class_id),
            celebrate=True,
            run_key=str(active.get("run_key") or "") or None,
            live_run=live_run,
        )
        if isinstance(ended, dict) and ended.get("already_ended"):
            return _already_ended_page()
        if isinstance(ended, dict) and ended.get("finish_busy"):
            return _finish_busy_page()
        if isinstance(ended, dict) and ended.get("in_progress"):
            # Another End holds this run and the game is still open (it is
            # stuck, or its worker died mid-write). The session stays open
            # so nothing is lost; the teacher presses End again.
            back = url_for("staff_course", class_id=int(class_id), tab="live")
            body = (
                "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
                "<title>End in progress</title>"
                "<link rel=\"stylesheet\" href=\"/static/lloves.css\"></head>"
                "<body><div class=\"wrap\"><h1>End in progress, try again</h1>"
                "<p class=\"sub\">The class is still open and nothing was lost. "
                "Wait a moment, then press End Class again.</p>"
                f"<p><a class=\"btn\" href=\"{html_escape(back)}\">Back to the class</a></p>"
                "</div></body></html>"
            )
            return body, 409, {"Content-Type": "text/html; charset=utf-8"}
        # Streaming tabs fetch the celebration now, not at the safety poll.
        ended_id = int(active["id"])
        emit_session_news(
            school,
            ended_id,
            [
                {
                    "type": "state_seq",
                    "state_seq": teacher_state_seq(school, ended_id),
                }
            ],
        )
        return redirect(
            url_for(
                "staff_course",
                class_id=int(class_id),
                tab="ap",
                view="attendance",
            )
        )

    def _wants_quit_json() -> bool:
        """True when Quit was submitted by fetch, not a plain form POST.

        The browser form sends ``*/*``. The dialog sends
        ``application/json`` so a 409 can stay inside the dialog.
        """
        accept = request.headers.get("Accept") or ""
        return "application/json" in accept.lower()

    @bp.route("/staff/class/<int:class_id>/quit-live", methods=["POST"])
    @staff_required
    def staff_quit_live_class(class_id: int):
        """Quit: save nothing, then clear this session's live rows now.

        Same ownership rules as End Class. Attendance, participation,
        and a catch-up link are not written. Responses, attendees, board
        assignments, engagement events, and teacher notes for this
        active session are deleted here, not on the next Start. Slides
        attached during the live class stay on the course. Quit after
        the wipe is a no-op. A fetch retry after a busy 409 calls finish
        again and completes the wipe. A plain form POST still gets HTML.
        """
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            abort(403)
        home = url_for("staff_home")
        wants_json = _wants_quit_json()

        def _ended():
            """Report a wipe that already finished.

            JSON navigates home. HTML stays a 409 for a normal form POST.
            """
            if wants_json:
                return jsonify(
                    {"ok": True, "already_ended": True, "redirect": home}
                )
            return _already_ended_page()

        def _busy():
            """Ask the dialog to retry. The session is still open."""
            if wants_json:
                return jsonify(
                    {
                        "ok": False,
                        "finish_busy": True,
                        "error": "Still finishing — Retry",
                    }
                ), 409
            return _finish_busy_page()

        active = school.get_active_live_session_for_teacher(int(user["id"]))
        # Read seqs before the wipe deletes the rows.
        quit_seqs = {
            int(row["id"]): teacher_state_seq(school, int(row["id"]))
            for row in school.list_live_sessions_for_class(int(class_id))
        }
        if active is None or int(active["class_id"]) != int(class_id):
            return _ended()
        ended = school.finish_live_class(
            int(class_id), persist=False, celebrate=False
        )
        if isinstance(ended, dict) and ended.get("already_ended"):
            return _ended()
        if isinstance(ended, dict) and ended.get("finish_busy"):
            return _busy()
        # Open tabs (often on the End celebration) fetch now and leave,
        # instead of waiting for the safety poll.
        for quit_sid, quit_seq in quit_seqs.items():
            emit_session_news(
                school,
                quit_sid,
                [{"type": "state_seq", "state_seq": int(quit_seq) + 1}],
            )
        if wants_json:
            return jsonify({"ok": True, "redirect": home})
        return redirect(home)

    @bp.route(
        "/api/staff/class/<int:class_id>/live-lessons/<module>/<slot>/deck",
        methods=["GET"],
    )
    @staff_required
    def staff_live_lesson_deck(class_id: int, module: str, slot: str):
        """Return merged seed + bank-import metadata for one class lesson deck."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        module_key = str(module or "").strip().upper()
        slot_key = str(slot or "").strip().upper()
        # MCK-178: a catalogue fix reaches the bank rows and unedited class
        # imports before the deck is built, even if nobody searched the bank
        # since the deploy. Cheap when current (per-process memo).
        library_id = school._class_library_id(int(class_id))
        if library_id is not None:
            school.ensure_rank_bank(int(library_id))
        try:
            live_metadata = school.live_class_metadata_for_class_lesson(
                int(class_id), module_key, slot_key, fresh=True
            )
        except KeyError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404
        response = jsonify(
            {
                "ok": True,
                "module": module_key,
                "slot": slot_key,
                "deck_revision": school.class_deck_revision(
                    int(class_id), module_key, slot_key
                ),
                "live_metadata": live_metadata,
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @bp.route(
        "/api/staff/class/<int:class_id>/live-lessons/<module>/<slot>/deck-seed-options",
        methods=["GET"],
    )
    @staff_required
    def staff_live_deck_seed_options(class_id: int, module: str, slot: str):
        """Return Set Class choices for seeding this challenge's live deck."""

        user = current_user()
        assert user is not None
        if _positive_sqlite_id(class_id) is None:
            return jsonify({"ok": False, "error": "Choose a deck from this course."}), 400
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        try:
            options = school.deck_seed_options(int(class_id), module, slot, teacher_user_id=int(user["id"]))
        except KeyError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        response = jsonify({"ok": True, **options})
        response.headers["Cache-Control"] = "no-store"
        return response

    def _positive_sqlite_id(value: Any) -> int | None:
        """Return ``value`` as a SQLite row id, or None when it will not fit.

        SQLite integers are signed 64-bit. A larger Python int reaches the
        driver as OverflowError, and the deck-seed routes would answer 500.
        A float such as ``1.5`` is rejected whole; ``int(1.5)`` would
        truncate it to ``1`` and copy the wrong class.

        Args:
            value: Path or JSON id.

        Returns:
            The id, or None when it is missing, not a whole integer, or
            out of range.
        """

        if isinstance(value, bool) or isinstance(value, float) or value in (None, ""):
            return None
        if isinstance(value, str):
            text = value.strip()
            if not text.isascii() or not text.isdigit():
                return None
            number = int(text)
        elif isinstance(value, int):
            number = value
        else:
            return None
        if number < 1 or number > 2**63 - 1:
            return None
        return number

    @bp.route(
        "/api/staff/class/<int:class_id>/live-lessons/<module>/<slot>/deck-seed",
        methods=["POST"],
    )
    @staff_required
    def staff_live_deck_seed(class_id: int, module: str, slot: str):
        """Copy a course deck, keep the current deck, or start blank.

        The destination gets its own working copy. Use current writes
        nothing. ``keep_existing: true`` (sent for an auto-picked mode)
        also writes nothing when the slot already has a deck. A cross-section source is refused unless this staff
        member can manage that class. The source challenge and the course
        seed JSON are not rewritten.

        MCK-132: a cross-section source must be one of this teacher's own
        current (non-archived) sections of the same course, admins
        included. A ``course`` copy onto a slot that already has a deck
        needs ``replace: true``; otherwise 409 with ``needs_confirm``.
        MCK-151: copying into the slot a live class is teaching is 409.
        """

        user = current_user()
        assert user is not None
        if _positive_sqlite_id(class_id) is None:
            return jsonify({"ok": False, "error": "Choose a deck from this course."}), 400
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        body = request.get_json(silent=True) or {}
        raw_source_class = body.get("source_class_id")
        if raw_source_class in (None, ""):
            raw_source_class = body.get("sourceClassId")
        source_class_id = None
        if raw_source_class not in (None, ""):
            source_class_id = _positive_sqlite_id(raw_source_class)
            if source_class_id is None:
                return jsonify({"ok": False, "error": "Choose a deck from this course."}), 400
        try:
            seeded = school.apply_class_deck_seed(
                int(class_id),
                module,
                slot,
                mode=str(body.get("mode") or ""),
                source_module=body.get("source_module") or body.get("sourceModule"),
                source_slot=body.get("source_slot") or body.get("sourceSlot"),
                source_class_id=source_class_id,
                teacher_user_id=int(user["id"]),
                keep_existing=body.get("keep_existing") is True or body.get("keepExisting") is True,
                # MCK-132: a clicked Course deck never silently replaces a
                # deck. The client sends replace: true only from Replace deck.
                require_replace_confirm=True,
                replace=body.get("replace") is True,
            )
        except KeyError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404
        except DeckCopyLiveConflict as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        except DeckReplaceNotConfirmed as exc:
            return jsonify({"ok": False, "error": str(exc), "needs_confirm": True}), 409
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except PermissionError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 403
        response = jsonify({"ok": True, "module": str(module or "").upper(), "slot": str(slot or "").upper(), **seeded})
        response.headers["Cache-Control"] = "no-store"
        return response

    @bp.route("/api/staff/class/<int:class_id>/live-lessons/contest-questions")
    @staff_required
    def staff_live_content_questions(class_id: int):
        """MCK-79: Contest Questions groups plus one group's top 6.

        Read-only. ``?module=M2`` (or ``COURSE``) returns the group list
        (modules with contest ``live_problems``, the requested module, and
        the course-wide Contest group) and that group's rows.
        """
        ontario_code, modules, failed = _content_questions_scope(class_id)
        if failed is not None:
            return failed
        try:
            from live_content_questions import (
                CONTENT_QUESTIONS_PER_MODULE,
                group_key,
                module_content_questions,
                module_summaries,
            )
        except ImportError:
            from lms.live_content_questions import (
                CONTENT_QUESTIONS_PER_MODULE,
                group_key,
                module_content_questions,
                module_summaries,
            )
        wanted = str(request.args.get("module") or "").strip()
        key = group_key(wanted, modules) if wanted else ""
        if wanted and not key:
            return jsonify({"ok": False, "error": "module must be a Run Live Class module"}), 400
        response = jsonify(
            {
                "ok": True,
                "source": "live_problems",
                "per_module": CONTENT_QUESTIONS_PER_MODULE,
                "modules": module_summaries(school, ontario_code, modules, key),
                "group": (
                    module_content_questions(school, ontario_code, key, modules)
                    if key
                    else None
                ),
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @bp.route(
        "/api/staff/class/<int:class_id>/live-lessons/<module>/<slot>/add-question",
        methods=["POST"],
    )
    @staff_required
    def staff_add_live_question(class_id: int, module: str, slot: str):
        """Add a staff-authored MC, numeric, poll, or rank onto the current page."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        body = request.get_json(silent=True) or {}
        try:
            page_number = int(body.get("page_number") or body.get("pageNumber") or 1)
        except (TypeError, ValueError):
            page_number = 1
        cls = school.enrich_class(school.game.get_class(class_id))
        library_id, _error = _ready_library(school, cls)
        save_raw = body.get("save_to_bank")
        if save_raw is None:
            save_raw = body.get("saveToBank")
        save_to_bank = bool(save_raw)
        if isinstance(save_raw, str):
            save_to_bank = save_raw.strip().lower() in {"1", "true", "yes"}
        question_type = str(body.get("type") or body.get("question_type") or "").strip().lower()
        response_mode = str(body.get("response_mode") or "").strip().lower()
        if question_type == "team_draw" or response_mode in {"team_draw", "group_draw"}:
            if response_mode in {"group", "group_submit", "group_draw"}:
                deny_unless("group_draw")
            else:
                deny_unless("team_draw")
        try:
            placement = school.add_staff_question_to_class_playlist(
                int(class_id),
                str(module or "").strip().upper(),
                str(slot or "").strip().upper(),
                question_type=str(body.get("type") or body.get("question_type") or ""),
                text=str(body.get("text") or body.get("stem") or ""),
                page_number=page_number,
                stage=str(body.get("stage") or "round"),
                options=list(body.get("options") or []),
                correct_index=body.get("correct_index", body.get("correctIndex")),
                correct_answer=body.get("correct_answer", body.get("correctAnswer")),
                tolerance=body.get("tolerance"),
                tolerance_kind=body.get("tolerance_kind", body.get("toleranceKind")),
                equation=body.get("equation") or body.get("equation_latex"),
                image_url=body.get("image_url") or body.get("imageUrl"),
                save_to_bank=save_to_bank,
                bank_scope=str(body.get("bank_scope") or body.get("bankScope") or "module"),
                bank_kind=str(body.get("bank_kind") or body.get("bankKind") or ""),
                library_id=int(library_id) if library_id else None,
                rank_key=body.get("rank_key", body.get("rankKey")),
                live_defaults=_live_defaults_from_body(body),
                extra_item={
                    key: body[key]
                    for key in (
                        "team_draw_prompt_id",
                        "team_draw_custom_text",
                        "team_draw_mystery_steps",
                    )
                    if key in body
                },
            )
        except KeyError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        module_key = str(module or "").strip().upper()
        slot_key = str(slot or "").strip().upper()
        return jsonify(
            {
                "ok": True,
                "placement": placement,
                **school.playlist_staff_snapshot(int(class_id), module_key, slot_key),
            }
        )

    @bp.route(
        "/api/staff/class/<int:class_id>/live-question-image",
        methods=["POST"],
    )
    @staff_required
    def staff_upload_live_question_image(class_id: int):
        """Store one staff-authored question image on the data volume."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        uploaded = request.files.get("image") or request.files.get("file")
        try:
            stored = school.store_live_question_image(int(class_id), uploaded)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, **stored})

    def _staff_relocate_playlist_item_response(
        class_id: int,
        module: str,
        slot: str,
        item_id: str,
        body: dict[str, Any],
    ):
        """Shared remove/move handler for playlist item staff APIs."""
        action = str(body.get("action") or "").strip().lower()
        module_key = str(module or "").strip().upper()
        slot_key = str(slot or "").strip().upper()
        token = str(item_id or "").strip()
        if not token:
            return jsonify({"ok": False, "error": "item_id required"}), 400
        try:
            if action == "remove":
                result = school.remove_class_playlist_item(
                    int(class_id), module_key, slot_key, token
                )
            else:
                target_raw = body.get("target_page_index")
                if target_raw in (None, ""):
                    target_raw = body.get("targetPageIndex")
                result = school.move_class_playlist_item(
                    int(class_id),
                    module_key,
                    slot_key,
                    token,
                    target_page_index=int(target_raw),
                )
        except KeyError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        snapshot = school.playlist_staff_snapshot(
            int(class_id), module_key, slot_key
        )
        return jsonify({"ok": True, **result, **snapshot})

    @bp.route(
        "/api/staff/class/<int:class_id>/live-lessons/<module>/<slot>/playlist-item",
        methods=["POST"],
    )
    @staff_required
    def staff_relocate_playlist_item_post(
        class_id: int, module: str, slot: str
    ):
        """Remove or move one question within a class live-lesson overlay."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        body = request.get_json(silent=True) or {}
        item_id = str(body.get("item_id") or body.get("itemId") or "").strip()
        return _staff_relocate_playlist_item_response(
            class_id, module, slot, item_id, body
        )

    @bp.route(
        "/api/staff/class/<int:class_id>/live-lessons/<module>/<slot>/playlist-items/<path:item_id>",
        methods=["PATCH"],
    )
    @staff_required
    def staff_relocate_playlist_item(
        class_id: int, module: str, slot: str, item_id: str
    ):
        """Remove or move one question within a class live-lesson overlay."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        body = request.get_json(silent=True) or {}
        return _staff_relocate_playlist_item_response(
            class_id, module, slot, item_id, body
        )

    @bp.route(
        "/api/staff/class/<int:class_id>/live-lessons/<module>/<slot>/pages",
        methods=["POST"],
    )
    @staff_required
    def staff_add_live_lesson_page(class_id: int, module: str, slot: str):
        """Insert an overlay page after the named current page.

        Last-page delete is a sibling DELETE route. Adding never writes
        seed JSON; the page is a ``class_live_playlist_pages`` row.
        Body ``kind`` / ``page_kind`` selects blank, welcome, or winner.
        """
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        body = request.get_json(silent=True) or {}
        module_key = str(module or "").strip().upper()
        slot_key = str(slot or "").strip().upper()
        try:
            result = school.add_class_playlist_page(
                int(class_id),
                module_key,
                slot_key,
                name=str(body.get("name") or "").strip(),
                after_page_id=str(
                    body.get("after_page_id") or body.get("afterPageId") or ""
                ).strip(),
                kind=str(body.get("kind") or body.get("page_kind") or "blank"),
            )
        except KeyError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, **result})

    @bp.route(
        "/api/staff/class/<int:class_id>/live-lessons/<module>/<slot>/pages/<path:page_id>",
        methods=["DELETE"],
    )
    @staff_required
    def staff_delete_live_lesson_page(
        class_id: int, module: str, slot: str, page_id: str
    ):
        """Remove one overlay or authored page from this class deck.

        Last-page rule: HTTP 400 ``cannot delete the last remaining page``.
        The deck always keeps at least one page. Confirmed deletes land on
        the next page, or the previous page when the deleted page was last.
        """
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        module_key = str(module or "").strip().upper()
        slot_key = str(slot or "").strip().upper()
        try:
            result = school.delete_class_playlist_page(
                int(class_id), module_key, slot_key, str(page_id or "")
            )
        except KeyError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, **result})

    @bp.route("/live-overlay/<int:session_id>")
    @staff_required
    def live_session_overlay_page(session_id: int):
        """Narrow Zoom-share overlay for an active live class session."""
        user = current_user()
        assert user is not None
        session_row = school.get_live_session(session_id)
        if session_row is None:
            abort(404)
        if user.get("role") != "it" and not school.teacher_owns_class(
            int(user["id"]), int(session_row["class_id"])
        ):
            abort(403)
        return send_from_directory(MGS_TEMPLATES, "live_session_overlay.html")

    @bp.route(
        "/api/classes/<int:class_id>/live-question-images/<path:filename>"
    )
    def serve_live_question_image(class_id: int, filename: str):
        """Serve one staff-authored live question image to class participants.

        Google Slides page files use this store too. Those files are
        returned only while an active session is sharing that page, and
        only to a student whose join is that active session. The same
        browser may also hold catch-up. Catch-up alone, a join from an
        ended session of this class, and unpublished or closed pages
        get 404. The class owner can still preview an unpublished page.
        Other question images keep the existing class-access rule.
        """
        if not _can_access_class_live_asset(class_id):
            abort(403)
        visibility = school.google_slide_image_visibility(int(class_id), filename)
        if visibility != "absent":
            user = current_user()
            owner = user is not None and school.teacher_owns_class(
                int(user["id"]), int(class_id)
            )
            if not owner:
                ctx = _student_live_context()
                active = school.get_active_live_session_for_class(int(class_id))
                try:
                    active_id = int(active["id"]) if active else 0
                except (TypeError, ValueError, KeyError):
                    active_id = 0
                joined_id = 0
                if (
                    isinstance(ctx, dict)
                    and int(ctx.get("class_id") or 0) == int(class_id)
                ):
                    try:
                        joined_id = int(ctx.get("live_session_id") or 0)
                    except (TypeError, ValueError):
                        joined_id = 0
                if (
                    active_id <= 0
                    or joined_id != active_id
                    or visibility != "published"
                ):
                    abort(404)
        path = school.live_question_image_path(int(class_id), filename)
        if path is None:
            abort(404)
        response = send_from_directory(path.parent, path.name)
        if visibility != "absent":
            response.headers["Cache-Control"] = "private, max-age=300"
            etag, _weak = response.get_etag()
            if not etag:
                stat = path.stat()
                response.set_etag(f"{stat.st_mtime_ns:x}-{stat.st_size:x}")
        return response

    def _ended_student_response(*, as_json: bool = False):
        """Clear live keys; PIN portal students return to the dashboard."""
        dest = _student_post_live_end_destination(school)
        session.pop(EXIT_FEEDBACK_SESSION_KEY, None)
        if resolve_pin_portal_context(school, session) is not None:
            clear_live_student_session_keys(session)
        else:
            clear_student_session_keys(session)
        if as_json:
            resp = jsonify(
                {
                    "ok": True,
                    "status": "ended",
                    "celebrate": False,
                    "feedback": False,
                    "redirect": dest,
                }
            )
        else:
            resp = redirect(dest)
        clear_rejoin_cookie(resp)
        return resp

    def _student_poll_ended_response(live_session_id: int):
        """JSON 200 ended body for a missing or closed Meet.

        Non-200 responses make the student page show Reconnecting… and
        poll again. This stays HTTP 200 with ``redirect`` so the browser
        leaves. It does not log a traceback.

        Args:
            live_session_id: Session the client was polling.
        """
        _note_live_session_gone(live_session_id)
        return jsonify(
            {
                "ok": True,
                "status": "ended",
                "phase": "ended",
                "celebrate": False,
                "feedback": False,
                "redirect": _student_post_live_end_destination(school),
                "fault": (
                    "Live class state is gone. This Meet is no longer running."
                ),
            }
        )

    def _require_active_live_attendee(
        *,
        as_json: bool = False,
    ):
        """Gate student home/mood/state on a still-active live session.

        Identity comes from visit token / rejoin cookie (preferred) or Flask
        session. A valid token on an active session re-admits after missed
        heartbeats. Ended sessions clear the auto-resume cookie.

        Args:
            as_json: When True, return a JSON landing redirect instead of HTML.

        Returns:
            ``None`` when access is allowed; otherwise a Flask response.
        """
        token = visit_token_from_request()
        if token:
            try:
                attendee = school.touch_live_session_heartbeat(token)
            except sqlite3.OperationalError as exc:
                # Same lock the heartbeat route can see. JSON keeps the last
                # painted Artifact frame; the portal shows Reconnecting… / Retry.
                if "locked" not in str(exc).lower() or not as_json:
                    raise
                logger.warning("student /state sqlite lock")
                return jsonify(
                    {
                        "ok": True,
                        "error": "state unavailable",
                        "status": "waiting",
                        "retry": True,
                    }
                )
            except Exception as exc:
                from live_presence import LivePresenceUnavailable, note_presence_blip

                if not isinstance(exc, LivePresenceUnavailable):
                    raise
                note_presence_blip("student live heartbeat skipped")
                if as_json:
                    return jsonify(
                        {
                            "ok": True,
                            "error": "state unavailable",
                            "status": "waiting",
                            "retry": True,
                        }
                    )
                # HTML reload keeps the session page. Ending the class here
                # would log the student out because Postgres blinked.
                return None
            if attendee is not None:
                return None
            return _ended_student_response(as_json=as_json)
        ctx = _student_live_context()
        if (
            ctx is not None
            and ctx["live_session_id"]
            and school.student_is_active_live_attendee(
                int(ctx["live_session_id"]),
                ctx.get("student_id"),
                visit_token=str(ctx.get("visit_token") or ""),
            )
        ):
            return None
        return _ended_student_response(as_json=as_json)

    AVATAR_REWARD_SYNCED_KEY = "avatar_reward_synced"
    AVATAR_REWARD_PROMPTED_KEY = "avatar_reward_prompted"

    def _avatar_reward_view(ctx: dict[str, Any], class_id: int, student_id: int):
        """MCK-116 reward pop-up state for one roster student, or None.

        Once per join (visit token) it records a pending grant for each
        appearance on the Celebrations board (``reward_appearances``; the
        frozen board while frozen). Grants are idempotent per appearance, so
        another login never re-grants. The pop-up itself only shows while the
        live session is in JOIN with no live question (Minds-On idle;
        ``live_session_reward_window_open``), never mid-class or in TEAMS, and
        opens on its own once per join; the slot or chip reopens it.

        Args:
            ctx: ``_student_live_context()`` for the request.
            class_id: Roster class.
            student_id: Roster student.

        Returns:
            ``{grants, choices, auto_open}`` for ``_avatar_reward.html``.
        """
        mark = f"{int(class_id)}:{int(student_id)}:{ctx.get('visit_token') or ''}"
        try:
            if session.get(AVATAR_REWARD_SYNCED_KEY) != mark:
                mine = [
                    row
                    for row in reward_appearances(school)
                    if row["class_id"] == int(class_id) and row["student_id"] == int(student_id)
                ]
                if mine:
                    school.sync_avatar_reward_grants(
                        int(class_id),
                        int(student_id),
                        mine,
                        catalogue_size=len(EARNED_CHARACTERS),
                    )
                session[AVATAR_REWARD_SYNCED_KEY] = mark
            pending = school.pending_avatar_reward_grants(int(class_id), int(student_id))
        except Exception:  # noqa: BLE001 - a reward must never block joining
            logger.exception(
                "avatar reward lookup failed class=%s student=%s", class_id, student_id
            )
            return None
        if not pending:
            return None
        try:
            live_id = int(ctx.get("live_session_id") or 0)
            open_now = bool(live_id) and school.live_session_reward_window_open(live_id)
        except Exception:  # noqa: BLE001 - unknown state counts as mid-class
            report_swallowed("avatar reward window")
            open_now = False
        if not open_now:
            return None
        owned = set(earned_avatar_keys(school, int(class_id), int(student_id)))
        auto_open = session.get(AVATAR_REWARD_PROMPTED_KEY) != mark
        session[AVATAR_REWARD_PROMPTED_KEY] = mark
        return {
            "grants": [
                {"id": int(row["id"]), "award_title": reward_title(str(row["source"]))}
                for row in pending
            ],
            "choices": [
                {
                    "key": key,
                    "label": EARNED_AVATARS[key],
                    "img": EARNED_AVATAR_SRC.format(key=key),
                    "owned": key in owned,
                }
                for key in EARNED_CHARACTERS
            ],
            "auto_open": auto_open,
        }

    def _student_advance():
        """Redirect to pick, landing, or the next unfinished student step."""
        ctx = _student_live_context()
        if ctx is None:
            if session.get("student_offering_id") and session.get("student_codename"):
                token = visit_token_from_request()
                return redirect(
                    student_url_with_token("student_pick", token)
                )
            return _ended_student_response()
        denied = _require_active_live_attendee()
        if denied is not None:
            return denied
        token = ctx.get("visit_token") or ""
        endpoint = next_student_endpoint(
            school,
            int(ctx["class_id"]),
            ctx.get("student_id"),
            visit_token=token,
            unmatched=bool(ctx.get("unmatched")),
        )
        return redirect(student_url_with_token(endpoint, token))

    @bp.route("/student/s/<token>")
    def student_visit_token(token: str):
        """Bookmarkable join resume: resolve opaque token, then cookie home.

        Does not put ``student_id`` in the URL; the cookie remains the source
        of truth after this redirect.
        """
        try:
            resolved = school.resolve_student_visit_token(token, allow_left=True)
            attendee = (
                school.touch_live_session_heartbeat(token)
                if resolved is not None
                else None
            )
        except Exception as exc:
            from live_presence import LivePresenceUnavailable, note_presence_blip

            if not isinstance(exc, LivePresenceUnavailable):
                raise
            note_presence_blip("visit-token resume skipped")
            return redirect(url_for("landing"))
        if resolved is None or attendee is None:
            resp = redirect(url_for("landing"))
            clear_student_session_keys(session)
            clear_rejoin_cookie(resp)
            return resp
        session_row = resolved["session"]
        class_id = int(resolved["class_id"])
        student_id = resolved.get("student_id")
        unmatched = bool(resolved.get("unmatched")) or student_id in (None, "")
        try:
            cls = school.game.get_class(class_id)
            offering = school.get_offering(int(session_row["offering_id"]))
        except (KeyError, TypeError):
            resp = redirect(url_for("landing"))
            clear_student_session_keys(session)
            clear_rejoin_cookie(resp)
            return resp
        student = None
        if not unmatched and student_id not in (None, ""):
            try:
                student = school.game.get_student(class_id, int(student_id))
            except (KeyError, TypeError):
                unmatched = True
        display = str(attendee.get("codename") or (student or {}).get("codename") or "")
        visit_token = str(attendee.get("visit_token") or token)
        bind_student_session(
            session,
            offering,
            cls,
            student or {"id": None, "codename": display, "first_name": display},
            live_session_id=int(resolved["live_session_id"]),
            session_code=str(session_row.get("session_code") or ""),
            visit_token=visit_token,
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
            int(student_id) if student_id not in (None, "") else None,
            visit_token=visit_token,
            unmatched=unmatched,
        )
        resp = redirect(student_url_with_token(endpoint, visit_token))
        set_rejoin_cookie(resp, visit_token)
        return resp

    @bp.route("/student/waiting")
    @student_required
    def student_waiting():
        """Legacy waiting URL; send the student to mood / character / home."""
        return _student_advance()

    @bp.route("/student/game")
    @student_required
    def student_game():
        """Legacy game URL; send the student to mood / character / home."""
        return _student_advance()

    @bp.route("/student/mood", methods=["GET", "POST"])
    @student_required
    def student_mood():
        """Optional mood check-in; Join Class / Skip continue to character pick."""
        denied = _require_active_live_attendee()
        if denied is not None:
            return denied
        ctx = _student_live_context()
        if ctx is None:
            return _student_advance()
        offering = ctx["offering"]
        class_id = int(ctx["class_id"])
        student_id = ctx.get("student_id")
        visit_token = str(ctx.get("visit_token") or "")
        unmatched = bool(ctx.get("unmatched")) or student_id in (None, "")
        if unmatched:
            return redirect(student_url_with_token("student_home", visit_token))
        student_id = int(student_id)
        if request.method == "POST":
            mood = (request.form.get("mood") or "").strip()
            skip = (request.form.get("skip") or "").strip() in {"1", "true", "yes"}
            if mood and not skip:
                try:
                    school.game.set_mood(class_id, student_id, mood)
                    school.record_engagement_event("checkin.mood", class_id=class_id, student_id=student_id)
                except ValueError as exc:
                    return render_template(
                        "student/mood.html",
                        offering=offering,
                        moods=mood_choices(),
                        error=str(exc),
                        school_name=SCHOOL_NAME,
                        visit_token=visit_token,
                        codename=str(ctx.get("codename") or ""),
                    )
            if not visit_token or visit_token == session.get("student_visit_token"):
                session["student_mood_done"] = True
            return _student_advance()
        nxt = next_student_endpoint(
            school,
            class_id,
            student_id,
            visit_token=visit_token,
            unmatched=False,
        )
        if nxt != "student_mood":
            return _student_advance()
        return render_template(
            "student/mood.html",
            offering=offering,
            moods=mood_choices(),
            error=None,
            school_name=SCHOOL_NAME,
            visit_token=visit_token,
            codename=str(ctx.get("codename") or ""),
        )


    def _code_box_refusal():
        """Send this browser to the home code box without saving.

        A stale visit token must not 500 and must not submit How-was-class.
        API callers get a JSON redirect; a form post navigates.

        Returns:
            Flask response that lands on the code box.
        """
        session.pop(EXIT_FEEDBACK_SESSION_KEY, None)
        clear_student_session_keys(session)
        if request.path.startswith("/api/"):
            resp = jsonify({"ok": False, "redirect": url_for("landing")})
            resp.status_code = 401
        else:
            resp = redirect(url_for("landing"))
        resp.headers["Cache-Control"] = "no-store"
        clear_rejoin_cookie(resp)
        return resp

    def _exit_feedback_visit_matches(row: dict[str, Any], resolved: dict[str, Any]) -> bool:
        """True when this feedback row belongs to the resolved visit.

        Args:
            row: ``live_class_feedback`` row.
            resolved: ``resolve_student_visit_token`` payload.
        """
        if int(row.get("class_id") or 0) != int(resolved.get("class_id") or 0):
            return False
        row_sid = row.get("student_id")
        visit_sid = resolved.get("student_id")
        if row_sid not in (None, "") and visit_sid not in (None, ""):
            return int(row_sid) == int(visit_sid)
        row_pid = str(row.get("participant_uuid") or "")
        visit_pid = str(resolved.get("participant_uuid") or "")
        if row_pid and visit_pid:
            return row_pid == visit_pid
        return True

    def _exit_feedback_refusal(row: dict[str, Any] | None):
        """Refuse How-was-class when the visit token is stale or unmatched.

        A request that presents a visit token must still resolve to this
        feedback row. After Done, the old token does not resolve, so the
        answer is not saved. A session that is still signed in, with no
        visit token on the request, keeps the existing submit path.

        Args:
            row: Pending feedback row, when one was found.

        Returns:
            A code-box response, or ``None`` when the submit may continue.
        """
        presented = explicit_visit_token_from_request() or rejoin_token_from_cookie()
        if presented:
            resolved = school.resolve_student_visit_token(presented, allow_left=True)
            if resolved is None:
                return _code_box_refusal()
            if row is not None and not _exit_feedback_visit_matches(row, resolved):
                return _code_box_refusal()
            return None
        if (
            session.get("student_id")
            or session.get("student_live_session_id")
            or session.get(EXIT_FEEDBACK_SESSION_KEY)
        ):
            return None
        return _code_box_refusal()

    @bp.route("/api/student/exit-feedback", methods=["POST"])
    def api_student_exit_feedback():
        """Submit How-was-class from the celebrating student home overlay."""
        data = request.get_json(silent=True) or {}
        token = str(
            data.get("token") or session.get(EXIT_FEEDBACK_SESSION_KEY) or ""
        ).strip()
        row = school.get_exit_feedback_by_token(token) if token else None
        refused = _exit_feedback_refusal(row)
        if refused is not None:
            return refused
        if row is None or row.get("submitted_at"):
            return jsonify({"ok": False, "error": "No pending feedback."}), 404
        skip = str(data.get("skip") or "").strip() in {"1", "true", "yes"}
        try:
            school.submit_exit_feedback(
                token,
                mood=str(data.get("mood") or "") or None,
                comment=str(data.get("comment") or ""),
                skip=skip,
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True})

    @bp.route("/student/exit", methods=["GET", "POST"])
    def student_exit_feedback():
        """How-was-class faces + optional comment after Quit or Save and End."""
        token = str(session.get(EXIT_FEEDBACK_SESSION_KEY) or "").strip()
        row = school.get_exit_feedback_by_token(token) if token else None
        if row is None or row.get("submitted_at"):
            session.pop(EXIT_FEEDBACK_SESSION_KEY, None)
            return redirect(url_for("landing"))
        error = None
        if request.method == "POST":
            refused = _exit_feedback_refusal(row)
            if refused is not None:
                return refused
            skip = (request.form.get("skip") or "").strip() in {"1", "true", "yes"}
            mood = (request.form.get("mood") or "").strip()
            comment = request.form.get("comment") or ""
            try:
                school.submit_exit_feedback(
                    token,
                    mood=mood or None,
                    comment=comment,
                    skip=skip,
                )
            except ValueError as exc:
                error = str(exc)
            else:
                session.pop(EXIT_FEEDBACK_SESSION_KEY, None)
                return redirect(url_for("landing"))
        return render_template(
            "student/exit.html",
            moods=mood_choices(),
            error=error,
            school_name=SCHOOL_NAME,
            heading=HOW_WAS_CLASS,
            comment=request.form.get("comment") or "",
        )

    @bp.route("/student/character", methods=["GET", "POST"])
    @student_required
    def student_character():
        """Required avatar pick after mood (or mood skip)."""
        denied = _require_active_live_attendee()
        if denied is not None:
            return denied
        ctx = _student_live_context()
        if ctx is None:
            return _student_advance()
        offering = ctx["offering"]
        class_id = int(ctx["class_id"])
        student_id = ctx.get("student_id")
        visit_token = str(ctx.get("visit_token") or "")
        unmatched = bool(ctx.get("unmatched")) or student_id in (None, "")
        nxt = next_student_endpoint(
            school,
            class_id,
            student_id,
            visit_token=visit_token,
            unmatched=unmatched,
        )
        if nxt != "student_character":
            return _student_advance()
        student_id = int(student_id)
        # MCK-116: reward avatars this student owns (claimed rewards, S1).
        earned = earned_avatar_keys(school, class_id, student_id)
        if request.method == "POST":
            character = (request.form.get("character") or "").strip()
            try:
                if character in EARNED_CHARACTERS and character not in earned:
                    raise ValueError("Choose an avatar.")
                school.game.set_character(class_id, student_id, character)
                school.record_engagement_event("checkin.avatar", class_id=class_id, student_id=student_id)
            except ValueError as exc:
                return render_template(
                    "student/character.html",
                    offering=offering,
                    characters=character_choices(earned),
                    error=str(exc),
                    school_name=SCHOOL_NAME,
                    visit_token=visit_token,
                    codename=str(ctx.get("codename") or ""),
                    reward=_avatar_reward_view(ctx, class_id, student_id),
                    reward_next_url=student_url_with_token("student_home", visit_token),
                )
            return _student_advance()
        return render_template(
            "student/character.html",
            offering=offering,
            characters=character_choices(earned),
            error=None,
            school_name=SCHOOL_NAME,
            visit_token=visit_token,
            codename=str(ctx.get("codename") or ""),
            reward=_avatar_reward_view(ctx, class_id, student_id),
            reward_next_url=student_url_with_token("student_home", visit_token),
        )


    @bp.route("/student/rejoin-live")
    @student_required
    def student_rejoin_live():
        """Resume or join live class from the PIN dashboard (MCK-208)."""
        ids = _pin_portal_identity(school)
        if ids is None:
            return redirect(url_for("landing"))
        class_id, student_id = ids
        live = school.get_active_live_session_for_class(class_id)
        if live is None:
            return redirect(url_for("student_dashboard"))
        live_id = int(live["id"])
        try:
            cls = school.game.get_class(class_id)
            offering = school.get_offering(int(cls["offering_id"]))
            student = school.game.get_student(class_id, int(student_id))
        except KeyError:
            return redirect(url_for("student_dashboard"))
        codename = str(student.get("codename") or student.get("first_name") or "")
        prior_token = str(session.get("student_visit_token") or "")
        attendee = school.get_live_session_attendee(live_id, int(student_id))
        if attendee is not None:
            prior_token = str(attendee.get("visit_token") or prior_token)
        try:
            joined = school.join_live_class_session(
                live_id,
                int(student_id),
                codename=codename,
                visit_token=prior_token,
            )
        except (KeyError, ValueError):
            return redirect(url_for("student_dashboard"))
        attendee = joined["attendee"]
        visit_token = str(attendee.get("visit_token") or "")
        bind_student_session(
            session,
            offering,
            cls,
            student,
            live_session_id=live_id,
            session_code=str(joined["session"].get("session_code") or ""),
            visit_token=visit_token,
            participant_uuid=str(attendee.get("participant_uuid") or ""),
            unmatched=False,
        )
        endpoint = next_student_endpoint(
            school,
            class_id,
            int(student_id),
            visit_token=visit_token,
            unmatched=False,
        )
        resp = redirect(student_url_with_token(endpoint, visit_token))
        set_rejoin_cookie(resp, visit_token)
        return resp

    def _redact_tester_payload(
        payload: Any, offering: Any, class_id: int, student_id: Any
    ) -> Any:
        """Blank exact real roster names when the viewer is the STMTG tester."""
        from tester_feedback import redact_real_roster

        if not _viewer_is_stmtg_tester(offering, student_id):
            return payload
        names = set(school.class_codenames(int(class_id)).values())
        return redact_real_roster(payload, names)

    def _done_visit_tokens() -> list[str]:
        """Visit tokens this Done POST can revoke.

        The page may be signed in by cookie, by the Flask session, or by
        the visit token alone. Empty values are dropped. Tokens are not
        logged.

        Returns:
            Unique tokens in the order they were found.
        """
        found: list[str] = []
        for candidate in (
            explicit_visit_token_from_request(),
            rejoin_token_from_cookie(),
            str(session.get("student_visit_token") or ""),
        ):
            cleaned = str(candidate or "").strip()
            if cleaned and cleaned not in found:
                found.append(cleaned)
        ctx = _student_live_context()
        cleaned = str((ctx or {}).get("visit_token") or "").strip()
        if cleaned and cleaned not in found:
            found.append(cleaned)
        return found

    @bp.route("/student/celebration-done", methods=["POST"])
    def student_celebration_done():
        """Sign this browser out of a finished class celebration.

        Done rotates this student's visit token, clears this browser's
        student session and rejoin cookie, and returns to the home page
        code box. A class that is still live is refused. When the class
        has already ended or the session is gone, the same sign-out runs
        without claiming the class is still running.

        The rotation touches only this attendee's visit token. It does
        not change the class, the live session, other students,
        attendance, participation, the gradebook, or catch-up rows.
        """
        if not _same_origin_post():
            return "Forbidden", 403
        tokens = _done_visit_tokens()
        ctx = _student_live_context()
        live_id = int((ctx or {}).get("live_session_id") or 0)
        if not live_id:
            live_id = int(session.get("student_live_session_id") or 0)
        session_row = school.get_live_session(live_id) if live_id else None
        status = str((session_row or {}).get("status") or "")
        if status == "active" or any(
            school.session_status_for_visit_token(token) == "active" for token in tokens
        ):
            return "Class is still running.", 403
        for token in tokens:
            school.revoke_student_visit_token(token)
        session.pop(EXIT_FEEDBACK_SESSION_KEY, None)
        clear_student_session_keys(session)
        resp = redirect(url_for("landing"))
        resp.headers["Cache-Control"] = "no-store"
        clear_rejoin_cookie(resp)
        return resp

    @bp.route("/student/home")
    @student_required
    def student_home():
        """Student live-class boards after mood and character."""
        denied = _require_active_live_attendee()
        if denied is not None:
            return denied
        ctx = _student_live_context()
        if ctx is None:
            return _student_advance()
        offering = ctx["offering"]
        class_id = int(ctx["class_id"])
        student_id = ctx.get("student_id")
        visit_token = str(ctx.get("visit_token") or "")
        unmatched = bool(ctx.get("unmatched")) or student_id in (None, "")
        if (
            next_student_endpoint(
                school,
                class_id,
                student_id,
                visit_token=visit_token,
                unmatched=unmatched,
            )
            != "student_home"
        ):
            return _student_advance()
        live_session_id = int(ctx["live_session_id"])
        pid = str(ctx.get("participant_uuid") or "")
        # MCK-191 (LLOVES-LMS-4): the page only needs the shell; its first
        # /api/student/state poll (slot-capped, backed off) paints the rest.
        # The full build here doubled every page load's heavy work.
        fallback = False
        try:
            payload = json_safe(
                school.assemble_student_home_shell(
                    live_session_id,
                    class_id,
                    int(student_id) if student_id not in (None, "") else None,
                    participant_uuid=pid,
                    codename=str(ctx.get("codename") or ""),
                    unmatched=unmatched,
                )
            )
        except PollBudgetExceeded:
            # Same degrade as /state: the waiting shell renders and the
            # browser's first poll retries with backoff. Not a crash.
            logger.warning(
                "student home shell over poll budget session=%s", live_session_id
            )
            fallback = True
        except Exception:
            logger.exception(
                "student home payload failed session=%s", live_session_id
            )
            fallback = True
        if fallback:
            payload = {
                "ok": True,
                "status": "waiting",
                "scoring": False,
                "waiting_room": True,
                "error": "state unavailable",
                "me": {
                    "codename": str(ctx.get("codename") or ""),
                    "points": 0,
                },
            }
        if payload.get("celebrate") and payload.get("exit_feedback", {}).get("token"):
            session[EXIT_FEEDBACK_SESSION_KEY] = payload["exit_feedback"]["token"]
        reward = None
        if not unmatched and not payload.get("scoring") and not payload.get("celebrate"):
            reward = _avatar_reward_view(ctx, class_id, int(student_id))
        tester_feedback = _viewer_is_stmtg_tester(offering, student_id)
        if tester_feedback:
            payload = _redact_tester_payload(payload, offering, class_id, student_id)
        resp = app.make_response(
            render_template(
                "student/home.html",
                offering=offering,
                payload=payload,
                school_name=SCHOOL_NAME,
                visit_token=visit_token,
                codename=str(ctx.get("codename") or payload.get("me", {}).get("codename") or ""),
                reward=reward,
                reward_next_url="",
                tester_feedback=tester_feedback,
            )
        )
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @bp.route("/api/student/avatar-reward/claim", methods=["POST"])
    @student_required
    def api_student_avatar_reward_claim():
        """Pick one reward avatar for a pending Celebrations grant (MCK-116).

        Body ``{grant_id, avatar_key, visit_token}``. Only the caller's own
        pending grant can be claimed, once; a repeat returns the stored pick
        (``already``). The pick is a permanent unlock and also becomes the
        current avatar (best effort). 409 once the class is under way (the
        pop-up's MED-1 rule): no claim and no avatar change mid-class.
        """
        denied = _require_active_live_attendee(as_json=True)
        if denied is not None:
            return denied
        ident = _student_identity()
        if ident is None:
            return jsonify(
                {"ok": False, "error": "Not joined.", "redirect": url_for("landing")}
            ), 401
        _offering, class_id, student_id = ident
        if student_id in (None, ""):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        class_id = int(class_id)
        student_id = int(student_id)
        body = request.get_json(silent=True) or {}
        key = str(body.get("avatar_key") or "").strip()
        try:
            grant_id = int(body.get("grant_id"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Choose an avatar."}), 400
        if key not in EARNED_CHARACTERS:
            return jsonify({"ok": False, "error": "Choose an avatar."}), 400
        try:
            live_id = int((_student_live_context() or {}).get("live_session_id") or 0)
            open_now = bool(live_id) and school.live_session_reward_window_open(live_id)
        except Exception:  # noqa: BLE001 - unknown state counts as mid-class
            logger.exception("avatar reward window check failed class=%s", class_id)
            open_now = False
        if not open_now:
            return jsonify(
                {"ok": False, "error": "Class is under way.", "closed": True}
            ), 409
        result = school.claim_avatar_reward(grant_id, class_id, student_id, key)
        status = result["status"]
        if status == "missing":
            return jsonify({"ok": False, "error": "Not found."}), 404
        if status == "owned":
            return jsonify({"ok": False, "error": "Choose an avatar."}), 400
        stored = str(result.get("avatar_key") or key)
        if status == "claimed":
            try:
                school.game.set_character(class_id, student_id, stored)
            except Exception:  # noqa: BLE001 - the unlock stands either way
                logger.exception(
                    "avatar reward set_character failed class=%s student=%s",
                    class_id,
                    student_id,
                )
        pending = school.pending_avatar_reward_grants(class_id, student_id)
        nxt = (
            {"grant_id": int(pending[0]["id"]), "award_title": reward_title(str(pending[0]["source"]))}
            if pending
            else None
        )
        return jsonify(
            {
                "ok": True,
                "already": status == "already",
                "avatar_key": stored,
                "name": EARNED_AVATARS.get(stored, ""),
                "src": EARNED_AVATAR_SRC.format(key=stored) if stored in EARNED_AVATARS else "",
                "next": nxt,
                "remaining": len(pending),
            }
        )

    @bp.route("/student/pick", methods=["GET", "POST"])
    @student_required
    def student_pick():
        """Disambiguate overlapping sections that share the same Codename."""
        code = session.get("student_live_code") or ""
        name = session.get("student_codename") or ""
        preserved_live_id = session.get("student_live_session_id")
        preserved_token = visit_token_from_request() or session.get("student_visit_token")
        matches = school.find_roster_matches(code, name)
        error = None
        if request.method == "POST":
            class_id = request.form.get("class_id")
            chosen = next(
                (row for row in matches if str(row["class"]["id"]) == str(class_id)),
                None,
            )
            if chosen:
                offering = school.get_offering(int(session["student_offering_id"]))
                bind_student_session(
                    session,
                    offering,
                    chosen["class"],
                    chosen["student"],
                    live_session_id=(
                        int(preserved_live_id) if preserved_live_id else None
                    ),
                    session_code=code,
                    visit_token=str(preserved_token or ""),
                )
                return _student_advance()
            error = "That section is not available."
        return render_template(
            "student/pick.html",
            classes=[row["class"] for row in matches],
            error=error,
            school_name=SCHOOL_NAME,
        )

    @bp.route("/api/student/mvp-vote", methods=["POST"])
    @student_required
    def api_student_mvp_vote():
        """Winning-team MVP ballot. The response does not echo the choice."""
        ident = _student_identity()
        ctx = _student_live_context()
        if ident is None or ctx is None:
            return jsonify({"ok": False, "error": "Join class first"}), 403
        _offering, _class_id, student_id = ident
        if student_id in (None, ""):
            return jsonify({"ok": False, "error": "Only the winning team can vote"}), 403
        body = request.get_json(silent=True) or {}
        try:
            school.cast_mvp_vote(
                int(ctx["live_session_id"]),
                int(student_id),
                str(body.get("nominee_codename") or ""),
            )
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        return jsonify({"ok": True, "voted": True})

    @bp.route("/api/student/state")
    @student_required
    @_guard_live_poll
    def student_state():
        """Live-class payload for the bound student (or a pick redirect)."""
        denied = _require_active_live_attendee(as_json=True)
        if denied is not None:
            return denied
        ctx = _student_live_context()
        if ctx is not None and _bound_student_class_id(ctx) is None:
            # Phone poll fallback: stay on JSON so the tab keeps polling.
            return jsonify({"ok": True, "status": "waiting", "retry": True})
        ident = _student_identity()
        if ident is None:
            if session.get("student_codename"):
                token = visit_token_from_request()
                return jsonify(
                    {
                        "ok": True,
                        "status": "pick",
                        "redirect": student_url_with_token("student_pick", token),
                    }
                )
            return jsonify(
                {"ok": True, "status": "waiting", "redirect": url_for("landing")}
            )
        _offering, class_id, student_id = ident
        ctx = _student_live_context()
        live_raw = (ctx or {}).get("live_session_id") or session.get(
            "student_live_session_id"
        )
        try:
            live_session_id = int(live_raw)
        except (TypeError, ValueError):
            return jsonify({"ok": True, "status": "waiting", "retry": True})
        pid = str((ctx or {}).get("participant_uuid") or "")
        unmatched = bool((ctx or {}).get("unmatched")) or student_id in (None, "")
        # Opt-in 0:00 close on the server clock, before the unchanged
        # short-circuit so the new stamp reaches this poll (MCK-27 M1).
        school.close_answers_if_timer_due(live_session_id)
        try:
            poll_student = (
                int(student_id) if student_id not in (None, "") else None
            )
            unchanged = school.student_live_poll_unchanged(
                live_session_id,
                int(class_id),
                request.args.get("seq"),
                request.args.get("stamp"),
                poll_student,
            )
            if unchanged is not None:
                if unchanged.get("status") == "ended" and not unchanged.get(
                    "celebrate"
                ):
                    return _student_poll_ended_response(live_session_id)
                unchanged = dict(unchanged)
                unchanged["live_session_id"] = live_session_id
                unchanged = _redact_tester_payload(
                    unchanged, _offering, int(class_id), student_id
                )
                # A phone that has not seen a slide yet still needs the
                # card on this unchanged poll. A matching slide_rev does
                # not attach it again. The poll stamp stays put (MCK-283).
                if unchanged.get("unchanged") is True:
                    card = school.student_google_slide_for_poll(live_session_id)
                    if isinstance(card, dict):
                        slide_rev = request.args.get("slide_rev")
                        current = str(
                            card.get("rev") if card.get("rev") is not None else 0
                        )
                        if slide_rev in (None, "") or current != str(slide_rev).strip():
                            unchanged["google_slide"] = card
                return jsonify(unchanged)
            # MCK-155 gate MED-4: take the stamp BEFORE the build. A write
            # that lands while the payload is being built then leaves the
            # client holding an older stamp, so its next poll rebuilds
            # (one extra build at worst) instead of matching a newer stamp
            # on a stale payload and answering ``unchanged`` for ~20 s.
            stamp = school.live_student_poll_stamp(
                live_session_id, int(class_id), poll_student
            )
            payload = school.assemble_student_live_payload(
                live_session_id,
                int(class_id),
                int(student_id) if student_id not in (None, "") else None,
                participant_uuid=pid,
                codename=str(
                    (ctx or {}).get("codename")
                    or session.get("student_codename")
                    or ""
                ),
                unmatched=unmatched,
            )
            payload["display_time"] = school.live_session_display_time(int(class_id))
            payload["stamp"] = stamp
            payload["state_seq"] = int(
                (payload.get("teacher_state") or {}).get("state_seq") or 0
            )
            payload["live_session_id"] = live_session_id
            payload = json_safe(payload)
        except PollBudgetExceeded:
            return jsonify(
                {
                    "ok": True,
                    "error": "state unavailable",
                    "status": "waiting",
                    "retry": True,
                }
            )
        except KeyError as exc:
            # Missing Meet. Do not logger.exception — that traceback on
            # every poll is what hung the shared-cpu machine.
            if missing_live_session(exc) or "live session" in str(exc):
                return _student_poll_ended_response(live_session_id)
            logger.exception(
                "student /state failed session=%s", live_session_id
            )
            return jsonify(
                {
                    "ok": True,
                    "error": "state unavailable",
                    "status": "waiting",
                }
            )
        except Exception:
            logger.exception(
                "student /state failed session=%s", live_session_id
            )
            return jsonify(
                {
                    "ok": True,
                    "error": "state unavailable",
                    "status": "waiting",
                }
            )
        if payload.get("celebrate") and payload.get("exit_feedback", {}).get("token"):
            session[EXIT_FEEDBACK_SESSION_KEY] = payload["exit_feedback"]["token"]
        active_q = payload.get("active_questions") or []
        if not active_q:
            dismiss_key = f"catchup_pin_later:{class_id}:{student_id}"
            prompt = school.catchup_pin_prompt_for_student(int(class_id), int(student_id))
            if prompt and prompt.get("forced"):
                session.pop(dismiss_key, None)
            elif prompt and session.get(dismiss_key):
                prompt = None
            if prompt:
                try:
                    portal_url = school.open_catchup_portal_url_for_class(int(class_id))
                except Exception:
                    logger.exception(
                        "catch-up portal URL failed class=%s student=%s",
                        class_id,
                        student_id,
                    )
                    portal_url = None
                payload["catchup_pin_prompt"] = {
                    **prompt,
                    "catchup_url": portal_url,
                }
        payload = _redact_tester_payload(payload, _offering, int(class_id), student_id)
        return jsonify(payload)

    @bp.route("/api/student/live-prompt")
    @student_required
    def api_student_live_prompt():
        """Active slide prompt for the bound student (poll companion)."""
        denied = _require_active_live_attendee(as_json=True)
        if denied is not None:
            return denied
        ident = _student_identity()
        if ident is None:
            return jsonify(
                {"ok": False, "error": "Not joined.", "redirect": url_for("landing")}
            ), 401
        _offering, _class_id, student_id = ident
        ctx = _student_live_context()
        live_session_id = int(
            (ctx or {}).get("live_session_id") or session["student_live_session_id"]
        )
        pid = str((ctx or {}).get("participant_uuid") or "")
        frag = school.student_live_prompt_payload(
            live_session_id,
            int(student_id) if student_id not in (None, "") else None,
            participant_uuid=pid,
        )
        return jsonify({"ok": True, **frag})

    @bp.route("/api/student/live-prompt/artifact-preview", methods=["POST"])
    @student_required
    def api_student_live_prompt_artifact_preview():
        """Record Artifact slider preview for Group Q matching."""
        denied = _require_active_live_attendee(as_json=True)
        if denied is not None:
            return denied
        ident = _student_identity()
        if ident is None:
            return jsonify(
                {"ok": False, "error": "Not joined.", "redirect": url_for("landing")}
            ), 401
        _offering, _class_id, student_id = ident
        ctx = _student_live_context()
        live_session_id = int(
            (ctx or {}).get("live_session_id") or session["student_live_session_id"]
        )
        body = request.get_json(silent=True) or {}
        try:
            prompt_id = int(body.get("prompt_id"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "No prompt."}), 409
        params = body.get("params")
        prompt = next(
            (
                row
                for row in school._list_live_session_prompts(live_session_id)
                if int(row["id"]) == prompt_id
            ),
            None,
        )
        payload = (prompt or {}).get("payload") if isinstance(prompt, dict) else {}
        if prompt is None or not is_artifact_payload(payload):
            return jsonify({"ok": False, "error": "No artifact prompt."}), 409
        sid = int(student_id) if student_id not in (None, "") else None
        try:
            school.record_artifact_slider_preview(
                live_session_id, sid, prompt_id, params
            )
            status = school.artifact_group_q_status(
                live_session_id, sid, prompt, params=params
            )
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        except Exception:
            return _artifact_busy("preview")
        return jsonify(
            {
                "ok": True,
                "group_q_ready": status["ready"],
                "needed": status["needed"],
                "matched": status["matched"],
                "members": status["members"],
            }
        )

    @bp.route("/api/student/canvas-presence", methods=["POST"])
    @student_required
    def api_student_canvas_presence():
        """Student cursor / team-stroke tick on the bound live session."""
        denied = _require_active_live_attendee(as_json=True)
        if denied is not None:
            return denied
        ident = _student_identity()
        ctx = _student_live_context()
        if ident is None or ctx is None:
            return jsonify(
                {"ok": False, "error": "Not joined.", "redirect": url_for("landing")}
            ), 401
        _offering, class_id, student_id = ident
        if student_id in (None, ""):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        live_session_id = int(
            ctx.get("live_session_id") or session["student_live_session_id"]
        )
        from feature_gates import session_whiteboard_closed

        if session_whiteboard_closed(live_session_id):
            abort(404)
        body = request.get_json(silent=True) or {}
        team_id = school.student_team_id_for_class(int(class_id), int(student_id))
        name = str(ctx.get("codename") or body.get("name") or student_id).strip()
        text_id = str(body.get("text_id") or "").strip()
        batch_id = str(body.get("client_batch_id") or "").strip() or None
        try:
            if text_id:
                school.apply_live_canvas_text(
                    live_session_id,
                    owner=str(int(student_id)),
                    name=name or str(student_id),
                    text_id=text_id,
                    text=str(body.get("text") if body.get("text") is not None else ""),
                    team_id=team_id,
                    x=body.get("x"),
                    y=body.get("y"),
                    as_teacher=False,
                    claimed_run_key=str(body.get("run_key") or "").strip(),
                )
            elif str(body.get("op") or body.get("type") or "") == "stroke_remove":
                school.remove_live_board_stroke(
                    live_session_id,
                    owner=str(int(student_id)),
                    stroke_id=str(body.get("stroke_id") or body.get("id") or ""),
                    team_id=team_id,
                    as_teacher=False,
                    client_batch_id=batch_id,
                    claimed_run_key=str(body.get("run_key") or "").strip(),
                )
            else:
                raw_points = body.get("points")
                school.apply_live_canvas_presence(
                    live_session_id,
                    owner=str(int(student_id)),
                    name=name or str(student_id),
                    team_id=team_id,
                    x=body.get("x"),
                    y=body.get("y"),
                    stroke_id=str(body.get("stroke_id") or "") or None,
                    point=body.get("point"),
                    points=raw_points if isinstance(raw_points, list) else None,
                    ended=bool(body.get("ended")),
                    as_teacher=False,
                    client_batch_id=batch_id,
                    claimed_run_key=str(body.get("run_key") or "").strip(),
                )
            # The reply reads the session again. Start can delete the row
            # in the few milliseconds after the write has already landed.
            reply = school.canvas_presence_reply(
                live_session_id,
                student_id=int(student_id),
                since=_optional_board_seq(body.get("since")),
                teacher_since=_optional_board_seq(body.get("teacher_since")),
            )
        except BoardSessionClosed as exc:
            return board_write_closed(exc)
        except BoardOpRejected as exc:
            return jsonify({"ok": False, "error": str(exc)}), 403
        except KeyError as exc:
            # Start deletes the row a few milliseconds after End. The
            # student write that lands in that gap is a closed class.
            if not missing_live_session(exc):
                raise
            return board_write_closed(BoardSessionClosed("session has ended"))
        # Own team plus the teacher. Other groups are not in this reply.
        return jsonify(reply)

    @bp.route("/api/student/live-prompt/response", methods=["POST"])
    @student_required
    def api_student_live_prompt_response():
        """Submit a live-prompt response; ack only, never keyed student copy."""
        denied = _require_active_live_attendee(as_json=True)
        if denied is not None:
            return denied
        ident = _student_identity()
        if ident is None:
            return jsonify(
                {"ok": False, "error": "Not joined.", "redirect": url_for("landing")}
            ), 401
        _offering, class_id, student_id = ident
        ctx = _student_live_context()
        live_session_id = int(
            (ctx or {}).get("live_session_id") or session["student_live_session_id"]
        )
        facing_payload = school.student_live_prompt_payload(
            live_session_id,
            int(student_id) if student_id not in (None, "") else None,
            participant_uuid=str((ctx or {}).get("participant_uuid") or ""),
        )
        facing = facing_payload.get("prompt") if isinstance(facing_payload, dict) else None
        active = school.get_active_live_prompt(live_session_id)
        allowed_ids: set[int] = set()
        if isinstance(facing, dict) and facing.get("id") not in (None, ""):
            allowed_ids.add(int(facing["id"]))
        if active is not None and active.get("id") not in (None, ""):
            allowed_ids.add(int(active["id"]))
        for item in facing_payload.get("active_questions") or []:
            item_prompt = item.get("prompt") if isinstance(item, dict) else None
            if isinstance(item_prompt, dict) and item_prompt.get("id") not in (
                None,
                "",
            ):
                allowed_ids.add(int(item_prompt["id"]))
        if not allowed_ids:
            return jsonify({"ok": False, "error": "No active prompt."}), 409
        body = request.get_json(silent=True) or {}
        response = body.get("response")
        if not isinstance(response, dict):
            response = {k: body[k] for k in body if k != "prompt_id"}
        prompt_id = int(body.get("prompt_id") or next(iter(allowed_ids)))
        if prompt_id not in allowed_ids:
            return jsonify({"ok": False, "error": "Prompt is no longer active."}), 409
        target = (
            facing
            if isinstance(facing, dict) and int(facing.get("id") or 0) == prompt_id
            else active
        )
        if target is None or int(target.get("id") or 0) != prompt_id:
            target = next(
                (
                    item.get("prompt")
                    for item in facing_payload.get("active_questions") or []
                    if isinstance(item, dict)
                    and isinstance(item.get("prompt"), dict)
                    and int(item["prompt"].get("id") or 0) == prompt_id
                ),
                None,
            )
        target_payload = (target or {}).get("payload") or {}
        if school.live_session_mc_poll_closed(live_session_id) and is_minds_on_payload(
            target_payload
        ):
            return jsonify(
                {"ok": False, "error": "Poll is closed.", "poll_closed": True}
            ), 409
        if bool(body.get("draft")):
            choice = ""
            if isinstance(response, dict):
                choice = str(
                    response.get("choice") or response.get("text") or ""
                ).strip()
            draft = school.set_group_question_draft(
                live_session_id,
                prompt_id=prompt_id,
                student_id=int(student_id) if student_id not in (None, "") else None,
                choice=choice,
            )
            return jsonify({"ok": True, "draft": True, "group_draft": draft})
        meet_payload = (target or {}).get("payload") or {}
        lifecycle_owns_prompt = any(
            isinstance(item, dict)
            and isinstance(item.get("prompt"), dict)
            and int(item["prompt"].get("id") or 0) == prompt_id
            for item in facing_payload.get("active_questions") or []
        )
        if is_meet_team_payload(meet_payload):
            choice = ""
            if isinstance(response, dict):
                choice = str(
                    response.get("choice") or response.get("text") or ""
                ).strip()
            stored = dict(response) if isinstance(response, dict) else {}
            if choice:
                stored["choice"] = choice
            try:
                stored_row = school.submit_live_prompt_response(
                    prompt_id,
                    int(student_id) if student_id not in (None, "") else None,
                    stored,
                    participant_uuid=str((ctx or {}).get("participant_uuid") or ""),
                )
            except (KeyError, ValueError) as exc:
                return _json_error(exc)
            replay = bool(
                isinstance(stored_row, dict) and stored_row.pop("idempotent_replay", False)
            )
            recorded = None if replay else school.record_meet_chain_pick(
                live_session_id,
                participant_uuid=str((ctx or {}).get("participant_uuid") or ""),
                student_id=int(student_id) if student_id not in (None, "") else None,
                choice=choice,
            )
            my_response = {
                "response": {"choice": choice},
                "awarded_points": None,
                "updated_at": None,
                "ephemeral": True,
            }
            body: dict[str, Any] = {
                "ok": True,
                "ack": True,
                "my_response": my_response,
                "meet_chip": school.student_meet_chip(
                    live_session_id,
                    participant_uuid=str((ctx or {}).get("participant_uuid") or ""),
                    student_id=int(student_id)
                    if student_id not in (None, "")
                    else None,
                ),
            }
            if recorded is None and not choice and not replay:
                return jsonify({"ok": False, "error": "Meet is not live."}), 409
            try:
                teacher = school.live_session_teacher_state_payload(live_session_id)
            except KeyError:
                teacher = None
            tally = school.live_session_mc_tally(live_session_id)
            if tally is not None and school._student_may_see_tally(
                live_session_id,
                target,
                my_response=my_response,
                teacher=teacher,
            ):
                # MCK-155 gate HIGH-1: no answer key while the question is open.
                body["mc_tally"] = school.student_safe_tally(
                    live_session_id, target, tally, teacher
                )
            if not replay:
                emit_answer_landed(
                    school,
                    live_session_id,
                    student_id=int(student_id) if student_id not in (None, "") else None,
                    count=prompt_response_count(school, prompt_id),
                )
            return jsonify(body)
        try:
            saved = school.submit_live_prompt_response(
                prompt_id,
                int(student_id) if student_id not in (None, "") else None,
                response,
                participant_uuid=str((ctx or {}).get("participant_uuid") or ""),
            )
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        replay = bool(isinstance(saved, dict) and saved.pop("idempotent_replay", False))
        my_response = {
            "response": saved.get("response") or {},
            "awarded_points": saved.get("awarded_points"),
            "updated_at": saved.get("updated_at"),
        }
        fragment = public_feedback_fragment(
            target_payload, saved.get("response") or response
        )
        if fragment:
            my_response["feedback"] = fragment
        body: dict[str, Any] = {
            "ok": True,
            "ack": True,
            "my_response": my_response,
        }
        if fragment:
            body["feedback"] = fragment
        try:
            teacher = school.live_session_teacher_state_payload(live_session_id)
        except KeyError:
            teacher = None
        tally = school._tally_for_prompt(live_session_id, target, teacher, for_student=True)
        if tally is None:
            tally = school.live_session_mc_tally(live_session_id)
        if tally is not None and school._student_may_see_tally(
            live_session_id,
            target,
            my_response=my_response,
            teacher=teacher,
        ):
            # MCK-155 gate HIGH-1: no answer key while the question is open.
            body["mc_tally"] = school.student_safe_tally(
                live_session_id, target, tally, teacher
            )
        if not replay:
            emit_answer_landed(
                school,
                live_session_id,
                student_id=int(student_id) if student_id not in (None, "") else None,
                count=prompt_response_count(school, prompt_id),
            )
        # MCK-176: students only ever see rank option aliases.
        body = school.alias_student_prompt_reply(prompt_id, body)
        return jsonify(body)

    @bp.route(
        "/api/student/live-items/<int:live_item_id>/vote",
        methods=["POST"],
    )
    @student_required
    def api_student_group_vote(live_item_id: int):
        """Submit a private vote for the student's active consensus item."""

        denied = _require_active_live_attendee(as_json=True)
        if denied is not None:
            return denied
        ident = _student_identity()
        ctx = _student_live_context()
        if ident is None or ctx is None or ident[2] in (None, ""):
            return jsonify({"ok": False, "error": "Roster student required"}), 403
        body = request.get_json(silent=True) or {}
        response = body.get("response")
        if not isinstance(response, dict):
            response = {
                key: body[key]
                for key in ("choice", "value", "text", "share")
                if key in body
            }
        try:
            result = school.submit_group_consensus_vote(
                int(ctx["live_session_id"]),
                live_item_id,
                int(ident[2]),
                response,
            )
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        emit_answer_landed(
            school,
            int(ctx["live_session_id"]),
            student_id=int(ident[2]),
            team_id=result.get("team_id") if isinstance(result, dict) else None,
            count=result.get("vote_count") if isinstance(result, dict) else None,
            scope="group",
        )
        return jsonify({"ok": True, **result})

    @bp.route(
        "/api/student/live-items/<int:live_item_id>/team-answer",
        methods=["POST"],
    )
    @student_required
    def api_student_group_team_answer(live_item_id: int):
        """Atomically finalize the student's one canonical team answer."""

        denied = _require_active_live_attendee(as_json=True)
        if denied is not None:
            return denied
        ident = _student_identity()
        ctx = _student_live_context()
        if ident is None or ctx is None or ident[2] in (None, ""):
            return jsonify({"ok": False, "error": "Roster student required"}), 403
        body = request.get_json(silent=True) or {}
        response = body.get("response")
        if not isinstance(response, dict):
            response = {
                key: body[key]
                for key in ("choice", "value", "text", "share")
                if key in body
            }
        try:
            team = school.finalize_group_consensus_answer(
                int(ctx["live_session_id"]),
                live_item_id,
                int(ident[2]),
                response,
            )
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        emit_answer_landed(
            school,
            int(ctx["live_session_id"]),
            student_id=int(ident[2]),
            team_id=team.get("team_id") if isinstance(team, dict) else None,
            count=team.get("vote_count") if isinstance(team, dict) else None,
            scope="group",
        )
        return jsonify({"ok": True, "group_consensus": team})

    @bp.route(
        "/api/student/live-items/<int:live_item_id>/group-draft",
        methods=["POST"],
    )
    @student_required
    def api_student_group_draft(live_item_id: int):
        """Save the shared multiple-choice draft. Does not submit or celebrate."""

        denied = _require_active_live_attendee(as_json=True)
        if denied is not None:
            return denied
        ident = _student_identity()
        ctx = _student_live_context()
        if ident is None or ctx is None or ident[2] in (None, ""):
            return jsonify({"ok": False, "error": "Roster student required"}), 403
        body = request.get_json(silent=True) or {}
        try:
            card = school.save_group_mc_draft(
                int(ctx["live_session_id"]),
                live_item_id,
                int(ident[2]),
                choice=body.get("choice"),
                why=body.get("why"),
                order=body.get("order"),
                tap=body.get("tap"),
                clear=bool(body.get("clear")),
                undo_last=bool(body.get("undo_last")),
            )
        except GroupAnswerLocked as exc:
            return jsonify({"ok": False, "error": str(exc), "locked": True, "group_submit": exc.card}), 409
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        race = card.get("race") if isinstance(card, dict) else None
        if isinstance(race, dict) and race.pop("agrees_cleared", False):
            # MCK-171: drafts stay silent, except a move that cleared
            # teammates' "I agree" (their phones and the lane must reset).
            emit_answer_landed(
                school,
                int(ctx["live_session_id"]),
                student_id=int(ident[2]),
                team_id=card.get("team_id"),
                scope="group",
            )
        return jsonify({"ok": True, "ack": True, "group_submit": card})

    @bp.route(
        "/api/student/live-items/<int:live_item_id>/group-submit",
        methods=["POST"],
    )
    @student_required
    def api_student_group_submit(live_item_id: int):
        """Submit the shared MC answer once choice and why are both present."""

        denied = _require_active_live_attendee(as_json=True)
        if denied is not None:
            return denied
        ident = _student_identity()
        ctx = _student_live_context()
        if ident is None or ctx is None or ident[2] in (None, ""):
            return jsonify({"ok": False, "error": "Roster student required"}), 403
        body = request.get_json(silent=True) or {}
        try:
            card = school.submit_group_mc_answer(
                int(ctx["live_session_id"]),
                live_item_id,
                int(ident[2]),
                choice=body.get("choice"),
                why=body.get("why"),
                order=body.get("order"),
            )
        except GroupAnswerLocked as exc:
            return jsonify({"ok": False, "error": str(exc), "locked": True, "group_submit": exc.card}), 409
        except RankAgreeConflict as exc:
            return jsonify(
                {"ok": False, "error": str(exc), "reason": exc.reason, "group_submit": exc.card}
            ), 409
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        emit_answer_landed(
            school,
            int(ctx["live_session_id"]),
            student_id=int(ident[2]),
            team_id=card.get("team_id") if isinstance(card, dict) else None,
            scope="group",
        )
        return jsonify({"ok": True, "ack": True, "group_submit": card})

    @bp.route(
        "/api/student/live-items/<int:live_item_id>/group-pick",
        methods=["POST"],
    )
    @student_required
    def api_student_group_pick(live_item_id: int):
        """MCK-155 option B step 1: store my own MC pick (no why)."""

        denied = _require_active_live_attendee(as_json=True)
        if denied is not None:
            return denied
        ident = _student_identity()
        ctx = _student_live_context()
        if ident is None or ctx is None or ident[2] in (None, ""):
            return jsonify({"ok": False, "error": "Roster student required"}), 403
        body = request.get_json(silent=True) or {}
        try:
            card = school.submit_group_mc_pick(
                int(ctx["live_session_id"]),
                live_item_id,
                int(ident[2]),
                choice=body.get("choice"),
            )
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        emit_answer_landed(
            school,
            int(ctx["live_session_id"]),
            student_id=int(ident[2]),
            team_id=card.get("team_id") if isinstance(card, dict) else None,
            scope="group",
        )
        return jsonify({"ok": True, "ack": True, "group_submit": card})

    @bp.route(
        "/api/student/live-items/<int:live_item_id>/rank-turn",
        methods=["POST"],
    )
    @student_required
    def api_student_rank_turn(live_item_id: int):
        """MCK-155 take turns: place one item, or undo my last placement."""

        denied = _require_active_live_attendee(as_json=True)
        if denied is not None:
            return denied
        ident = _student_identity()
        ctx = _student_live_context()
        if ident is None or ctx is None or ident[2] in (None, ""):
            return jsonify({"ok": False, "error": "Roster student required"}), 403
        body = request.get_json(silent=True) or {}
        try:
            card = school.rank_turn_place(
                int(ctx["live_session_id"]),
                live_item_id,
                int(ident[2]),
                option_id=body.get("option_id"),
                undo=bool(body.get("undo")),
            )
        except RankTurnConflict as exc:
            return jsonify(
                {"ok": False, "error": str(exc), "reason": exc.reason, "group_submit": exc.card}
            ), 409
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        emit_answer_landed(
            school,
            int(ctx["live_session_id"]),
            student_id=int(ident[2]),
            team_id=card.get("team_id") if isinstance(card, dict) else None,
            scope="group",
        )
        return jsonify({"ok": True, "ack": True, "group_submit": card})

    @bp.route(
        "/api/student/live-items/<int:live_item_id>/rank-check",
        methods=["POST"],
    )
    @student_required
    def api_student_rank_check(live_item_id: int):
        """MCK-293: one count of how many spots match. No answer order."""

        denied = _require_active_live_attendee(as_json=True)
        if denied is not None:
            return denied
        ident = _student_identity()
        ctx = _student_live_context()
        if ident is None or ctx is None or ident[2] in (None, ""):
            return jsonify({"ok": False, "error": "Roster student required"}), 403
        try:
            card = school.check_group_rank(
                int(ctx["live_session_id"]),
                live_item_id,
                int(ident[2]),
            )
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        return jsonify({"ok": True, "ack": True, "group_submit": card})

    @bp.route(
        "/api/student/live-items/<int:live_item_id>/rank-agree",
        methods=["POST"],
    )
    @student_required
    def api_student_rank_agree(live_item_id: int):
        """MCK-216 Team challenge: explicit "Lock in our order" on the team draft."""

        denied = _require_active_live_attendee(as_json=True)
        if denied is not None:
            return denied
        ident = _student_identity()
        ctx = _student_live_context()
        if ident is None or ctx is None or ident[2] in (None, ""):
            return jsonify({"ok": False, "error": "Roster student required"}), 403
        body = request.get_json(silent=True) or {}
        try:
            card = school.rank_race_agree(
                int(ctx["live_session_id"]),
                live_item_id,
                int(ident[2]),
                agree=body.get("agree", True),
                order=body.get("order"),
            )
        except GroupAnswerLocked as exc:
            return jsonify({"ok": False, "error": str(exc), "locked": True, "group_submit": exc.card}), 409
        except RankAgreeConflict as exc:
            return jsonify(
                {"ok": False, "error": str(exc), "reason": exc.reason, "group_submit": exc.card}
            ), 409
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        emit_answer_landed(
            school,
            int(ctx["live_session_id"]),
            student_id=int(ident[2]),
            team_id=card.get("team_id") if isinstance(card, dict) else None,
            scope="group",
        )
        return jsonify({"ok": True, "ack": True, "group_submit": card})

    @bp.route(
        "/api/student/live-items/<int:live_item_id>/team-draw",
        methods=["POST"],
    )
    @student_required
    def api_student_team_draw(live_item_id: int):
        """Submit a team or individual drawing. The body is the PNG only."""

        denied = _require_active_live_attendee(as_json=True)
        if denied is not None:
            return denied
        ident = _student_identity()
        ctx = _student_live_context()
        if ident is None or ctx is None or ident[2] in (None, ""):
            return jsonify({"ok": False, "error": "Roster student required"}), 403
        body = request.get_json(silent=True) or {}
        try:
            from school_db import GroupAnswerLocked

            session_id = int(ctx["live_session_id"])
            item = school.get_live_session_item(
                session_id, live_item_id, sync="if_stale"
            )
            image_b64 = str(body.get("image") or body.get("image_png") or "")
            mode = str(item.get("response_mode") or "")
            if mode == "group_draw":
                deny_unless("group_draw", session_id=session_id)
            else:
                deny_unless("team_draw", session_id=session_id)
            individual = (
                school._question_answer_kind(item) == "team_draw"
                and mode == "individual"
            )
            if individual:
                card = school.submit_individual_draw(
                    session_id,
                    live_item_id,
                    int(ident[2]),
                    image_b64=image_b64,
                )
            else:
                card = school.submit_team_draw(
                    session_id,
                    live_item_id,
                    int(ident[2]),
                    image_b64=image_b64,
                )
        except GroupAnswerLocked as exc:
            return jsonify(
                {"ok": False, "error": str(exc), "locked": True, "team_draw": exc.card}
            ), 409
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        emit_answer_landed(
            school,
            int(ctx["live_session_id"]),
            student_id=int(ident[2]),
            team_id=card.get("team_id") if isinstance(card, dict) else None,
            scope="group",
        )
        return jsonify({"ok": True, "ack": True, "team_draw": card})

    def _can_view_live_session(session_row: dict[str, Any]) -> bool:
        """True when the current user may poll this live session.

        Args:
            session_row: ``live_class_sessions`` dict.
        """
        user = current_user()
        if user is None:
            return False
        return school.teacher_owns_class(
            int(user["id"]), int(session_row["class_id"])
        )

    def _active_owned_live_session(
        session_id: int,
    ) -> tuple[dict[str, Any] | None, Any | None]:
        """Resolve an active teacher-owned session for mutation APIs."""

        row = school.get_live_session(session_id)
        if row is None:
            return None, (
                jsonify({"ok": False, "error": "Session not found"}),
                404,
            )
        if not _can_view_live_session(row):
            return None, (
                jsonify({"ok": False, "error": "Forbidden"}),
                403,
            )
        if row.get("status") != "active":
            return None, (
                jsonify({"ok": False, "error": "Session is not active"}),
                409,
            )
        return row, None

    @bp.route("/api/live-sessions/active")
    @login_required
    def api_live_sessions_active():
        """List active live-class sessions (IT dashboard + overlay poll)."""
        user = current_user()
        assert user is not None
        sessions = school.list_active_live_sessions()
        sessions = [
            row
            for row in sessions
            if school.teacher_owns_class(int(user["id"]), int(row["class_id"]))
        ]
        return jsonify({"ok": True, "sessions": sessions})

    def _degraded_live_session_state(session_row: dict[str, Any]) -> dict[str, Any]:
        """Return a minimal light snapshot when /state builders fail.

        Args:
            session_row: ``live_class_sessions`` row already authorized.

        Returns:
            JSON-safe light payload so staff/overlay polls stay 200.
        """
        phase = "ended" if session_row.get("status") == "ended" else "live"
        shell_session = school.staff_session_for_shell(session_row)
        return json_safe(
            {
                "session": shell_session,
                "code": session_row.get("session_code"),
                "count": 0,
                "attendees": [],
                "phase": phase,
                "teacher_state": shell_session.get("teacher_state") or {},
                "allow_unmatched_guests": bool(
                    int(shell_session.get("allow_unmatched_guests") or 0)
                ),
                "mc_tally": None,
                "lifecycle_response_counts": {},
                "state_seq": int(
                    (shell_session.get("teacher_state") or {}).get("state_seq") or 0
                ),
                "light": True,
                "groups": [],
                "error": "state unavailable",
                "fault": (
                    "Live class state failed. The Meet is still open — use Retry."
                ),
            }
        )

    def _missing_live_session_state(session_id: int) -> dict[str, Any]:
        """Return a clean ended payload when the session row is gone.

        Pollers must not 404/500-loop after a crash deletes the row.
        The teacher shell stops polling and shows ``fault``.

        Args:
            session_id: Requested ``live_class_sessions.id``.
        """
        return {
            "ok": True,
            "phase": "ended",
            "fault": (
                "Live class state is gone. This Meet is no longer running."
            ),
            "session": {"id": int(session_id), "status": "ended"},
            "code": None,
            "count": 0,
            "attendees": [],
            "teacher_state": {},
            "allow_unmatched_guests": False,
            "mc_tally": None,
            "lifecycle_response_counts": {},
            "state_seq": 0,
            "light": True,
            "groups": [],
        }

    @bp.route("/api/live-sessions/<int:session_id>/state")
    @login_required
    @_guard_live_poll
    def api_live_session_state(session_id: int):
        """Return code, attendee count, roster, and phase for one live session.

        ``?light=1`` omits cards, metadata, and scoreboard for interval polls.
        Heavy builder failures degrade to light (or a stub) instead of 500.
        """
        session_row = school.get_live_session(session_id)
        if session_row is None:
            _note_live_session_gone(session_id)
            return jsonify(_missing_live_session_state(session_id))
        if not _can_view_live_session(session_row):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        light = str(request.args.get("light") or "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        # Opt-in 0:00 close on the server clock (MCK-27), throttled.
        school.close_answers_if_timer_due(session_id)
        try:
            state = school.get_live_session_state(session_id, light=light)
        except PollBudgetExceeded:
            state = _degraded_live_session_state(session_row)
            state["fault"] = "Live class state is slow. Retry."
            state["error"] = "state unavailable"
        except KeyError as exc:
            fresh = school.get_live_session(session_id)
            if fresh is None:
                _note_live_session_gone(session_id)
                return jsonify(_missing_live_session_state(session_id))
            if missing_live_session(exc):
                # Row came back, or a field raced a delete. Degrade without
                # a traceback. Do not call the gone-Meet log: the row exists.
                state = _degraded_live_session_state(fresh)
            else:
                logger.exception(
                    "live session %s /state KeyError light=%s", session_id, light
                )
                state = _degraded_live_session_state(fresh)
        except Exception as exc:
            from live_presence import LivePresenceUnavailable, note_presence_blip

            if isinstance(exc, LivePresenceUnavailable):
                note_presence_blip(f"staff /state session={session_id}")
                state = _degraded_live_session_state(session_row)
                return jsonify({"ok": True, **state})
            logger.exception(
                "live session %s /state failed light=%s", session_id, light
            )
            if not light:
                try:
                    state = school.get_live_session_state(session_id, light=True)
                except Exception as light_exc:
                    from live_presence import (
                        LivePresenceUnavailable,
                        note_presence_blip,
                    )

                    if isinstance(light_exc, LivePresenceUnavailable):
                        note_presence_blip(
                            f"staff light /state session={session_id}"
                        )
                    else:
                        logger.exception(
                            "live session %s light /state fallback failed",
                            session_id,
                        )
                    state = _degraded_live_session_state(session_row)
            else:
                state = _degraded_live_session_state(session_row)
        return jsonify({"ok": True, **state})

    @bp.route("/api/live-sessions/<int:session_id>/guests", methods=["POST"])
    @login_required
    def api_live_session_guests(session_id: int):
        """Mid-session toggle: allow names that are not on the roster.

        Body JSON: ``allow_unmatched_guests`` (bool). Default for new sessions
        is false — unmatched names are rejected until staff checks the box.
        """
        session_row = school.get_live_session(session_id)
        if session_row is None:
            return jsonify({"ok": False, "error": "Session not found"}), 404
        if not _can_view_live_session(session_row):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        body = request.get_json(silent=True) or {}
        raw = body.get("allow_unmatched_guests")
        if isinstance(raw, str):
            allowed = raw.strip().lower() in {"1", "true", "yes", "on"}
        else:
            allowed = bool(raw)
        try:
            updated = school.set_live_session_allow_unmatched_guests(
                session_id, allowed
            )
        except KeyError:
            return jsonify({"ok": False, "error": "Session not found"}), 404
        flag = bool(int(updated.get("allow_unmatched_guests") or 0))
        return jsonify(
            {
                "ok": True,
                "allow_unmatched_guests": flag,
                "session": {
                    "id": int(updated["id"]),
                    "allow_unmatched_guests": flag,
                },
            }
        )

    @bp.route("/api/live-sessions/<int:session_id>/prompts/active")
    @login_required
    def api_live_session_prompt_active(session_id: int):
        """Staff: read the active slide-index prompt for a live session."""
        session_row = school.get_live_session(session_id)
        if session_row is None:
            return jsonify({"ok": False, "error": "Session not found"}), 404
        if not _can_view_live_session(session_row):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        prompt = school.get_active_live_prompt(session_id)
        return jsonify({"ok": True, "prompt": prompt})

    @bp.route("/api/live-sessions/<int:session_id>/prompts", methods=["POST"])
    @login_required
    def api_live_session_prompt_set(session_id: int):
        """Staff driver stub: set/activate a slide-index prompt (mc/numeric/share/draw).

        Body JSON: ``slide_index``, ``kind``, optional ``payload``, optional
        ``active`` (default true). ``kind=idle`` or ``active=false`` clears the
        student Live response shell.
        """
        session_row = school.get_live_session(session_id)
        if session_row is None:
            return jsonify({"ok": False, "error": "Session not found"}), 404
        if not _can_view_live_session(session_row):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        body = request.get_json(silent=True) or {}
        kind = str(body.get("kind") or "idle").strip().lower()
        activate = body.get("active", True)
        if isinstance(activate, str):
            activate = activate.strip().lower() in {"1", "true", "yes"}
        try:
            slide_index = int(body.get("slide_index", 0))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "slide_index must be an int"}), 400
        payload = body.get("payload")
        if payload is not None and not isinstance(payload, dict):
            return jsonify({"ok": False, "error": "payload must be an object"}), 400
        if kind == "idle" or activate is False:
            school.clear_active_live_prompt(session_id)
            if kind == "idle":
                try:
                    prompt = school.set_live_session_prompt(
                        session_id,
                        slide_index=slide_index,
                        kind="idle",
                        payload=payload or {},
                        activate=False,
                    )
                except (KeyError, ValueError) as exc:
                    return _json_error(exc)
                return jsonify({"ok": True, "prompt": prompt, "active": None})
            return jsonify(
                {"ok": True, "prompt": None, "active": school.get_active_live_prompt(session_id)}
            )
        try:
            prompt = school.set_live_session_prompt(
                session_id,
                slide_index=slide_index,
                kind=kind,
                payload=payload,
                activate=True,
            )
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        seq = teacher_state_seq(school, session_id)
        news = [
            {"type": "state_seq", "state_seq": seq},
            prompt_event(prompt.get("id") if isinstance(prompt, dict) else None, seq),
        ]
        if isinstance(prompt, dict) and prompt.get("slide_index") not in (None, ""):
            news.append(
                {
                    "type": "slide",
                    "slide_index": int(prompt.get("slide_index") or 0),
                    "deck_version": seq,
                    "state_seq": seq,
                }
            )
        emit_session_news(school, session_id, news)
        return jsonify({"ok": True, "prompt": prompt})

    @bp.route("/api/live-sessions/<int:session_id>/artifacts", methods=["POST"])
    @login_required
    def api_live_session_artifact_mint(session_id: int):
        """Staff: mint an Artifact question on the current live-class page.

        Body JSON: ``artifact_id``, ``snapshot`` (slider map), optional
        ``target_mode`` (``graph`` / ``equation``), optional ``parent``,
        optional ``slide_index``.
        """
        session_row = school.get_live_session(session_id)
        if session_row is None:
            return jsonify({"ok": False, "error": "Session not found"}), 404
        if not _can_view_live_session(session_row):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        body = request.get_json(silent=True) or {}
        slide_raw = body.get("slide_index")
        slide_index = None
        if slide_raw not in (None, ""):
            try:
                slide_index = int(slide_raw)
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "slide_index must be an int"}), 400
        try:
            minted = school.mint_live_artifact(
                session_id,
                artifact_id=str(
                    body.get("artifact_id") or TRANSFORMATIONS_ARTIFACT_ID
                ),
                snapshot=body.get("snapshot") or body.get("params"),
                target_mode=body.get("target_mode") or "graph",
                parent=body.get("parent") if isinstance(body.get("parent"), dict) else None,
                slide_index=slide_index,
                hot_cold_visible=body.get("hot_cold_visible"),
                group_q=body.get("group_q"),
                accuracy_margin=body.get("accuracy_margin"),
            )
            seq = teacher_state_seq(school, session_id)
            minted_prompt = minted.get("prompt") if isinstance(minted, dict) else None
            emit_session_news(
                school,
                session_id,
                [
                    {"type": "state_seq", "state_seq": seq},
                    prompt_event(
                        minted_prompt.get("id")
                        if isinstance(minted_prompt, dict)
                        else None,
                        seq,
                    ),
                    active_media_event(
                        minted.get("active_media") if isinstance(minted, dict) else None,
                        seq,
                    ),
                    flag_work_event("mint", seq),
                ],
            )
            return jsonify(
                {
                    "ok": True,
                    "prompt": minted.get("prompt"),
                    "live_item": minted.get("live_item"),
                    "live_items": minted.get("live_items") or [],
                    "question_cards": minted.get("question_cards") or [],
                    "active_media": minted.get("active_media"),
                    "first_mint": bool(minted.get("first_mint")),
                    "toast": str(minted.get("toast") or ""),
                    "match_index": int(minted.get("match_index") or 0),
                    "teacher_state": school.live_session_teacher_state_payload(
                        session_id
                    ),
                }
            )
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        except Exception:
            return _artifact_busy("mint")

    def _google_slides_response(session_id: int, deck: dict[str, Any]):
        """Staff JSON for one deck plus the student card it currently pushes."""
        _emit_google_slide_news(int(session_id))
        return jsonify(
            {
                "ok": True,
                "google_slides": deck,
                "google_slide": school.student_google_slide(int(session_id)),
            }
        )

    def _emit_google_slide_news(session_id: int) -> None:
        """Tell joined phones the current slide without a ``/state`` rebuild.

        The postcard carries the student card only: an unpublished page
        has an empty image URL. Wire faults are swallowed by the emitter.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        card = school.student_google_slide(int(session_id))
        if not isinstance(card, dict):
            return
        emit_session_news(
            school,
            int(session_id),
            [
                {
                    "type": "google_slide",
                    "index": int(card.get("index") or 0),
                    "shared": 1 if card.get("shared") else 0,
                    "page_count": int(card.get("page_count") or 0),
                    "image_url": str(card.get("image_url") or ""),
                    "rev": int(card.get("rev") or 0),
                }
            ],
        )


    def _require_slide_placement(module: Any, slot: Any) -> tuple[str, str]:
        """Return a module and slot, or refuse one that would fall back.

        A missing module or slot stays M1 / C1. A posted token that
        ``normalize_live_module`` or ``normalize_live_slot`` would rewrite
        is rejected so a bad save cannot overwrite that default deck.

        Args:
            module: Posted module, or empty.
            slot: Posted slot, or empty.

        Returns:
            Normalized ``(module, slot)``.

        Raises:
            ValueError: The posted module or slot is not a known placement.
        """
        from live_teacher_state import normalize_live_module, normalize_live_slot

        raw_module = str(module or "").strip()
        raw_slot = str(slot or "").strip()
        live_module = normalize_live_module(raw_module)
        live_slot = normalize_live_slot(raw_slot)
        if raw_module and live_module != raw_module.upper():
            raise ValueError("Unknown module.")
        if raw_slot and live_slot != raw_slot.upper():
            raise ValueError("Unknown slot.")
        return live_module, live_slot

    def _slide_default_published(value: Any) -> bool:
        """True when a form or JSON flag asks for pages to start shared.

        Args:
            value: Checkbox, boolean, or string.
        """
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    def _slide_body_too_large():
        """400 when Content-Length is past the slide cap, before the body is read.

        A missing length is capped on the WSGI stream so a chunked body
        cannot grow to the IMSCC limit before this check runs.
        """
        import course_slide_files as slide_files

        if not slide_files.upload_content_length_allowed(request.content_length):
            return (
                jsonify({"ok": False, "error": "That file is too large to attach."}),
                400,
            )
        if request.content_length is None:
            slide_files.cap_wsgi_input(request.environ)
        return None

    def _slide_form_file():
        """Return the uploaded deck, or a 400 when the capped stream overflows.

        Returns:
            ``(upload, error)``. ``error`` is a Flask response tuple when
            the body passed the slide cap. ``upload`` may be None.
        """
        from werkzeug.exceptions import RequestEntityTooLarge

        try:
            files = request.files
        except RequestEntityTooLarge:
            return None, (
                jsonify({"ok": False, "error": "That file is too large to attach."}),
                400,
            )
        upload = files.get("file") if files else None
        return upload, None

    def _slide_json_body():
        """Return JSON for a slide link, or a 400 when the body is too large.

        Returns:
            ``(body, error)``. ``error`` is a Flask response tuple when
            the capped stream overflows.
        """
        from werkzeug.exceptions import RequestEntityTooLarge

        try:
            return request.get_json(silent=True) or {}, None
        except RequestEntityTooLarge:
            return {}, (
                jsonify({"ok": False, "error": "That file is too large to attach."}),
                400,
            )

    def _queue_slide_import(owner_id: int, class_id: int | None, session_id: int | None, work):
        """Insert a running job and return 202.

        ``work`` runs off this request. It must not touch ``request``.

        Args:
            owner_id: Signed-in staff user.
            class_id: Course, when the deck is course content.
            session_id: Live run, when the attach is for one session.
            work: Called with the job id. It must not touch ``request``.
        """
        import uuid

        from slide_import_jobs import start_slide_import

        job_id = uuid.uuid4().hex
        school.insert_slide_import_job(job_id, int(owner_id), class_id, session_id)

        def run() -> dict:
            """Run the import with the id of the row just inserted."""
            return work(job_id)

        start_slide_import(school, job_id, run)
        return jsonify({"ok": True, "status": "running", "job_id": job_id}), 202

    def _spill_slide_upload(upload, suffix: str):
        """Stream a PowerPoint to a temp file, or return an error response.

        Args:
            upload: Werkzeug file storage.
            suffix: ``.ppt`` or ``.pptx``.

        Returns:
            Temp path, or a Flask ``(response, status)`` tuple.
        """
        import tempfile

        import course_slide_files as slide_files

        handle = tempfile.NamedTemporaryFile(
            prefix="lloves-slide-upload-",
            suffix=suffix,
            delete=False,
        )
        try:
            slide_files.write_capped_upload(
                upload, slide_files.COURSE_SLIDE_UPLOAD_MAX_BYTES, handle
            )
            handle.close()
        except ValueError as exc:
            handle.close()
            Path(handle.name).unlink(missing_ok=True)
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception:
            handle.close()
            Path(handle.name).unlink(missing_ok=True)
            raise
        return handle.name

    def _emit_slide_news_for_class(class_id: int) -> None:
        """Emit a slide postcard when this class has an active run.

        Safe to call from the import thread. It does not read ``request``.

        Args:
            class_id: Game-show ``classes.id``.
        """
        active = school.get_active_live_session_for_class(int(class_id))
        if active is None:
            return
        _emit_google_slide_news(int(active["id"]))

    @bp.route("/api/slide-imports/<job_id>", methods=["GET"])
    @login_required
    @feature_required("slides_import")
    def api_slide_import_status(job_id: str):
        """Poll one slide import started by this teacher.

        ``running`` stays HTTP 200 so the client can keep asking. A
        finished deck uses the same JSON the attach route used to
        return on 200. The PowerPoint itself is not in the payload.
        """
        import re
        from datetime import datetime, timezone

        from slide_import_jobs import JOB_BUDGET_SECONDS

        if not re.fullmatch(r"[0-9a-f]{32}", str(job_id or "")):
            return jsonify({"ok": False, "error": "That import was not found."}), 404
        user = current_user()
        if user is None:
            return jsonify({"ok": False, "error": "Authentication required."}), 401
        school.fail_expired_slide_import_jobs(JOB_BUDGET_SECONDS)
        job = school.get_slide_import_job(str(job_id))
        if job is None or int(job.get("owner_user_id") or 0) != int(user["id"]):
            return jsonify({"ok": False, "error": "That import was not found."}), 404
        status = str(job.get("status") or "")
        if status == "running":
            try:
                started_raw = str(job.get("started_at") or "")
                if not started_raw:
                    age = 0
                else:
                    started = datetime.fromisoformat(started_raw)
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=timezone.utc)
                    age = (datetime.now(timezone.utc) - started).total_seconds()
            except ValueError:
                age = 0
            if age > JOB_BUDGET_SECONDS:
                return (
                    jsonify(
                        {
                            "ok": False,
                            "status": "error",
                            "error": "That deck took too long to attach.",
                        }
                    ),
                    400,
                )
            return jsonify({"ok": True, "status": "running", "job_id": job_id})
        if status == "ready":
            try:
                result = json.loads(str(job.get("result_json") or "{}"))
            except json.JSONDecodeError:
                result = {}
            if not isinstance(result, dict):
                result = {}
            return jsonify({"ok": True, "status": "ready", **result})
        body: dict[str, Any] = {
            "ok": False,
            "status": "error",
            "error": str(job.get("error") or "Could not attach that deck."),
        }
        code = int(job.get("http_status") or 400)
        if job.get("needs_slides_read"):
            body["needs_slides_read"] = True
            class_id = int(job.get("class_id") or 0)
            body["connect_url"] = url_for(
                "auth_google_slides_read",
                next=url_for("staff_course", class_id=class_id, tab="live"),
            )
            code = 403
        return jsonify(body), code

    @bp.route(
        "/api/live-sessions/<int:session_id>/google-slides/current",
        methods=["POST"],
    )
    @login_required
    @feature_required("slides_import")
    def api_live_session_google_slides_current(session_id: int):
        """Move the slide students see when that page is already published.

        Body JSON: ``{"index": <zero-based page>}``.
        """
        _row, err = _active_owned_live_session(session_id)
        if err is not None:
            return err
        body = request.get_json(silent=True) or {}
        try:
            index = int(body.get("index"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "slide page is out of range"}), 400
        try:
            deck = school.set_google_slides_current(
                int(session_id), index, follow=True
            )
        except KeyError:
            return jsonify({"ok": False, "error": "Session not found"}), 404
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return _google_slides_response(session_id, deck)

    @bp.route(
        "/api/live-sessions/<int:session_id>/google-slides/pages/<int:index>/publish",
        methods=["POST"],
    )
    @login_required
    @feature_required("slides_import")
    def api_live_session_google_slides_publish(session_id: int, index: int):
        """Publish one slide page and push it to students."""
        _row, err = _active_owned_live_session(session_id)
        if err is not None:
            return err
        try:
            deck = school.publish_google_slide_page(int(session_id), int(index))
        except KeyError:
            return jsonify({"ok": False, "error": "Session not found"}), 404
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return _google_slides_response(session_id, deck)

    @bp.route(
        "/api/live-sessions/<int:session_id>/google-slides/pages/<int:index>/unpublish",
        methods=["POST"],
    )
    @login_required
    @feature_required("slides_import")
    def api_live_session_google_slides_unpublish(session_id: int, index: int):
        """Unpublish one slide page. Other pages stay as they were."""
        _row, err = _active_owned_live_session(session_id)
        if err is not None:
            return err
        try:
            deck = school.unpublish_google_slide_page(int(session_id), int(index))
        except KeyError:
            return jsonify({"ok": False, "error": "Session not found"}), 404
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return _google_slides_response(session_id, deck)


    def _course_deck_job_body(class_id: int, saved: dict[str, Any]) -> dict[str, Any]:
        """Finished course-deck import, plus the student card when a run is bound.

        Args:
            class_id: Game-show class id.
            saved: Return value of ``save_course_slide_deck``.

        Returns:
            ``deck`` and ``google_slides``. ``google_slide`` is included
            when the active run was pointed at this deck.
        """
        deck = saved.get("google_slides") if isinstance(saved, dict) else None
        body: dict[str, Any] = {
            "deck": saved.get("deck") if isinstance(saved, dict) else None,
            "google_slides": deck,
        }
        if not isinstance(deck, dict):
            return body
        active = school.get_active_live_session_for_class(int(class_id))
        if active is None:
            return body
        body["google_slide"] = school.student_google_slide(int(active["id"]))
        return body

    @bp.route(
        "/api/classes/<int:class_id>/slide-deck",
        methods=["GET", "POST", "DELETE"],
    )
    @login_required
    def api_class_slide_deck(class_id: int):
        """Read, attach, or remove the course slide deck for one placement.

        GET returns the deck name for the Set Class summary. POST accepts
        a Google Slides share link as JSON, or a ``.pptx`` file as
        multipart. A legacy ``.ppt`` is refused. The file is stored on
        the media volume. The row
        stores the path. Conversion runs on a background job. A matching
        live run is pointed at that same copy when the job finishes.
        DELETE removes that placement's deck. Page files another deck or
        session still names stay on disk.
        """
        denied = _require_class_staff(int(class_id))
        if denied is not None:
            return denied
        if request.method == "DELETE":
            if not _same_origin_post():
                return jsonify({"ok": False, "error": "Forbidden."}), 403
            payload = request.get_json(silent=True) or {}
            if not isinstance(payload, dict):
                payload = {}
            try:
                module, slot = _require_slide_placement(
                    request.args.get("module") or payload.get("module"),
                    request.args.get("slot") or payload.get("slot"),
                )
            except ValueError as exc:
                return jsonify({"ok": False, "error": str(exc)}), 400
            removed = school.delete_course_slide_deck(int(class_id), module, slot)
            if removed.get("deleted"):
                _emit_slide_news_for_class(int(class_id))
            return jsonify({"ok": True, "deck": None, **removed})
        if request.method == "GET":
            try:
                module, slot = _require_slide_placement(
                    request.args.get("module"),
                    request.args.get("slot"),
                )
            except ValueError as exc:
                return jsonify({"ok": False, "error": str(exc)}), 400
            summary = school.course_slide_deck_summary(
                int(class_id),
                module,
                slot,
            )
            return jsonify({"ok": True, "deck": summary})
        too_big = _slide_body_too_large()
        if too_big is not None:
            return too_big
        deny_unless("slides_import")
        user = current_user()
        if user is None:
            return jsonify({"ok": False, "error": "Authentication required."}), 401
        owner_id = int(user["id"])
        upload, upload_err = _slide_form_file()
        if upload_err is not None:
            return upload_err
        module = request.form.get("module") or request.args.get("module")
        slot = request.form.get("slot") or request.args.get("slot")
        published_raw = request.form.get("default_published")
        if upload is not None and str(upload.filename or "").strip():
            import course_slide_files as slide_files

            filename = str(upload.filename or "")
            published = _slide_default_published(published_raw)
            try:
                suffix = slide_files.office_suffix(filename)
            except ValueError as exc:
                return jsonify({"ok": False, "error": str(exc)}), 400
            try:
                module, slot = _require_slide_placement(module, slot)
            except ValueError as exc:
                return jsonify({"ok": False, "error": str(exc)}), 400
            spilled = _spill_slide_upload(upload, suffix)
            if isinstance(spilled, tuple):
                return spilled
            title = slide_files.slide_deck_title(
                Path(filename).stem, fallback="PowerPoint"
            )

            def work(job_id: str) -> dict[str, Any]:
                """Convert the spilled upload into the course deck.

                Conversion and rendering run in capped subprocesses. Pages
                are files. The upload is read again only when it is stored.
                """
                import course_slide_files as files

                path = Path(str(spilled))
                page_dir = path.parent / f"pages-{job_id}"
                try:
                    page_paths, truncated = files.pages_from_office_to_dir(
                        path, suffix, page_dir, timeout=80
                    )
                    saved = school.save_course_slide_deck(
                        int(class_id),
                        module,
                        slot,
                        title=title,
                        source="pptx",
                        page_paths=page_paths,
                        default_published=published,
                        truncated=truncated,
                        source_path=path,
                        source_suffix=suffix,
                        job_id=job_id,
                    )
                finally:
                    path.unlink(missing_ok=True)
                    shutil.rmtree(page_dir, ignore_errors=True)
                _emit_slide_news_for_class(int(class_id))
                return _course_deck_job_body(int(class_id), saved)

            try:
                return _queue_slide_import(owner_id, int(class_id), None, work)
            except Exception:
                Path(str(spilled)).unlink(missing_ok=True)
                raise
        body, body_err = _slide_json_body()
        if body_err is not None:
            return body_err
        if body.get("module") not in (None, ""):
            module = body.get("module")
        if body.get("slot") not in (None, ""):
            slot = body.get("slot")
        try:
            module, slot = _require_slide_placement(module, slot)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        url = str(body.get("url") or request.form.get("url") or "").strip()
        published = body.get("default_published")
        if published is None:
            published = published_raw
        published_flag = _slide_default_published(published)
        try:
            from google_slides_import import parse_presentation_id

            presentation_id = parse_presentation_id(url)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        def work(job_id: str) -> dict[str, Any]:
            """Import a share link with the same 45 second deadline as a live run.

            Pages are written one at a time in a capped subprocess. The
            worker does not keep the PNG list.
            """
            import tempfile
            import time as time_mod

            from google_slides_import import (
                IMPORT_TIME_LIMIT_SECONDS,
                SlidesShareNotPublic,
                write_shared_deck_pages,
            )
            from slide_import_jobs import SlideImportFailed

            deadline = time_mod.monotonic() + IMPORT_TIME_LIMIT_SECONDS
            work_dir = Path(tempfile.mkdtemp(prefix="lloves-gslides-"))
            try:
                try:
                    page_paths, truncated, source = write_shared_deck_pages(
                        school,
                        presentation_id,
                        user=user,
                        http=None,
                        deadline=deadline,
                        work_dir=work_dir,
                    )
                except SlidesShareNotPublic as exc:
                    raise SlideImportFailed(str(exc), needs_slides_read=True) from exc
                saved = school.save_course_slide_deck(
                    int(class_id),
                    module,
                    slot,
                    title="Google Slides",
                    source=source,
                    presentation_id=presentation_id,
                    page_paths=page_paths,
                    default_published=published_flag,
                    truncated=truncated,
                    job_id=job_id,
                )
            finally:
                shutil.rmtree(work_dir, ignore_errors=True)
            _emit_slide_news_for_class(int(class_id))
            return _course_deck_job_body(int(class_id), saved)

        return _queue_slide_import(owner_id, int(class_id), None, work)


    def _emit_bound_slide_news(saved: dict[str, Any]) -> None:
        """Emit a slide postcard when this save bound a live run.

        Args:
            saved: Return value of ``save_course_slide_deck``.
        """
        deck = saved.get("google_slides") if isinstance(saved, dict) else None
        if not isinstance(deck, dict):
            return
        active = school.get_active_live_session_for_class(
            int(request.view_args.get("class_id") or 0)
        ) if request.view_args else None
        if active is None:
            return
        _emit_google_slide_news(int(active["id"]))

    @bp.route(
        "/api/live-sessions/<int:session_id>/active-media",
        methods=["GET", "POST"],
    )
    @login_required
    @feature_required("media")
    def api_live_session_active_media(session_id: int):
        """Staff: read or set/swap/clear/patch live-session active media.

        POST JSON: ``url`` (same-origin ``/static/...``) to set or swap;
        ``clear: true`` or empty ``url`` to hide the student iframe;
        omit ``url`` to patch control-state (``reveal_axes``,
        ``reveal_lateral``, ``allow_3d_limited``, ``frozen``,
        ``student_controls_unlocked``, ``unlock_flags``, ``params``,
        view tools, stem/caption/answers, ``cons_item``, ``challenge``,
        toast) on the current page. ``cons_item`` unlocks only after
        ``frozen: true`` (C1 on this blob; C2/C3 on teacher ``text_ride``).
        C3 clears media and does not seed ``active_media_json``. C2 seeds
        the Transformations iframe. GET still seeds authored playlist media
        for the current course/slot when present. CONS table checkboxes in
        the Real-slice iframe are disabled until the next step.
        """
        session_row = school.get_live_session(session_id)
        if session_row is None:
            return jsonify({"ok": False, "error": "Session not found"}), 404
        if not _can_view_live_session(session_row):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        if request.method == "GET":
            try:
                live_slot = school.session_live_slot(session_id)
                school.ensure_live_class_media(session_id)
                pack = cons_catalog(live_slot)
                return jsonify(
                {
                    "ok": True,
                    "active_media": school.live_session_active_media_payload(
                        session_id
                    ),
                    "live_slot": live_slot,
                    "text_ride": school.session_text_ride(session_id),
                    "defaults": {
                        "url": DEFAULT_LIVE_MEDIA_URL,
                        "title": DEFAULT_LIVE_MEDIA_TITLE,
                        "stem": DEFAULT_LIVE_MEDIA_STEM,
                        "challenge": "C1",
                        "reveal_axes": False,
                        "reveal_lateral": False,
                        "allow_3d_limited": False,
                        "frozen": False,
                        "c2": {
                            "url": C2_TRANSFORM_MEDIA_URL,
                            "challenge": "C2",
                            "seed": True,
                            "note": "C2 seeds Transformations media; teacher sliders stay hidden until Artifact mint.",
                        },
                        "live_media_catalog": live_media_catalog(),
                        "c3": {
                            "url": None,
                            "challenge": "C3",
                            "seed": False,
                            "note": "C3 does not seed active_media_json.",
                        },
                        "allow_url_swap": live_media_url_swap_allowed(
                            testing=bool(app.config.get("TESTING"))
                        ),
                    },
                    "cons_pack": [
                        {
                            "id": item["id"],
                            "index": item["index"],
                            "kind": item["kind"],
                            "prompt": item["prompt"],
                            **(
                                {
                                    "chips": [
                                        str(code).strip()
                                        for code in (item.get("chips") or [])
                                        if str(code).strip()
                                    ]
                                }
                                if item.get("chips")
                                else {}
                            ),
                        }
                        for item in pack
                    ],
                }
                )
            except Exception:
                return _artifact_busy("media-read")
        body = request.get_json(silent=True) or {}
        clear = body.get("clear", False)
        if isinstance(clear, str):
            clear = clear.strip().lower() in {"1", "true", "yes"}
        url_posted = "url" in body
        merge = not clear and not url_posted
        kwargs: dict[str, Any] = {
            "clear": bool(clear),
            "merge": merge,
            "allow_url_swap": live_media_url_swap_allowed(
                testing=bool(app.config.get("TESTING"))
            ),
        }
        if url_posted:
            kwargs["url"] = body.get("url")
        if "title" in body:
            kwargs["title"] = body.get("title")
        if "caption" in body:
            kwargs["caption"] = body.get("caption")
        if "stem" in body:
            kwargs["stem"] = body.get("stem")
        if "entry_chip" in body:
            kwargs["entry_chip"] = body.get("entry_chip")
        if "student_controls_unlocked" in body:
            kwargs["student_controls_unlocked"] = body.get(
                "student_controls_unlocked"
            )
        if "param_push" in body:
            kwargs["param_push"] = body.get("param_push")
        if "param_frozen" in body:
            kwargs["param_frozen"] = body.get("param_frozen")
        if "reveal_axes" in body:
            kwargs["reveal_axes"] = body.get("reveal_axes")
        if "reveal_lateral" in body:
            kwargs["reveal_lateral"] = body.get("reveal_lateral")
        if "allow_3d_limited" in body:
            kwargs["allow_3d_limited"] = body.get("allow_3d_limited")
        if "show_z_axis" in body:
            kwargs["show_z_axis"] = body.get("show_z_axis")
        if "student_zoom" in body:
            kwargs["student_zoom"] = body.get("student_zoom")
        if "freeze_zoom" in body:
            kwargs["freeze_zoom"] = body.get("freeze_zoom")
        if "surface_transparency" in body:
            kwargs["surface_transparency"] = body.get("surface_transparency")
        if "freeze_surface" in body:
            kwargs["freeze_surface"] = body.get("freeze_surface")
        if "student_yaw_range" in body:
            kwargs["student_yaw_range"] = body.get("student_yaw_range")
        if "freeze_yaw" in body:
            kwargs["freeze_yaw"] = body.get("freeze_yaw")
        if "frozen" in body:
            kwargs["frozen"] = body.get("frozen")
        if "unlock_flags" in body:
            kwargs["unlock_flags"] = body.get("unlock_flags")
        if "answers" in body:
            kwargs["answers"] = body.get("answers")
        if "params" in body:
            kwargs["params"] = body.get("params")
        if "challenge" in body:
            kwargs["challenge"] = body.get("challenge")
        if "cons_item" in body:
            kwargs["cons_item"] = body.get("cons_item")
        if "toast" in body:
            kwargs["toast"] = body.get("toast")
        if "toast_key" in body:
            kwargs["toast_key"] = body.get("toast_key")
        if "artifact" in body:
            kwargs["artifact"] = body.get("artifact")
        if "state_event" in body:
            kwargs["state_event"] = body.get("state_event")
        if "arm_reveal" in body:
            kwargs["arm_reveal"] = body.get("arm_reveal")
        if "class_size" in body:
            kwargs["class_size"] = body.get("class_size")
        try:
            media = school.set_live_session_active_media(session_id, **kwargs)
            if "artifact" in body:
                school.sync_artifact_teacher_flags(session_id, body.get("artifact"))
            seq = teacher_state_seq(school, session_id)
            emit_session_news(
                school,
                session_id,
                [
                    {"type": "state_seq", "state_seq": seq},
                    active_media_event(media if isinstance(media, dict) else None, seq),
                ],
            )
            return jsonify(
                {
                    "ok": True,
                    "active_media": media,
                    "live_slot": school.session_live_slot(session_id),
                    "text_ride": school.session_text_ride(session_id),
                    "teacher_state": school.live_session_teacher_state_payload(
                        session_id
                    ),
                }
            )
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        except Exception:
            return _artifact_busy("media-write")

    @bp.route("/api/live/session/<int:session_id>/events")
    def api_live_session_events(session_id: int):
        """SSE LiveNewsWire for one live session.

        Staff cookie or the student rejoin cookie. ``Last-Event-ID`` resumes
        the postcard tape. A full worker sheds one ``busy`` event and does
        not reload the tab.
        """
        return live_news_response(
            school,
            session_id,
            testing=bool(app.config.get("TESTING")),
            last_event_header=request.headers.get("Last-Event-ID"),
            last_event_arg=request.args.get("last_event_id"),
        )

    @bp.route(
        "/api/live-sessions/<int:session_id>/teacher-state",
        methods=["GET", "POST"],
    )
    @login_required
    def api_live_session_teacher_state(session_id: int):
        """Staff: read or patch the thin LiveTeacherState channel.

        POST JSON may include ``advance`` (``next`` / ``prev``) to move
        ``stage`` only, plus any subset of stage / round / round_flags /
        teams_mode / layout_preset / frames / active_tab / refs / cue_id /
        meet_chain / student_frames / unlocks / ``mc_ui``, or ``meet_action``
        (``next`` / ``skip_c`` / ``clear``). TEAMS→MEET may include
        ``assign`` (``n_teams``, ``mode``, ``present_ids``, optional
        ``assignments``) so Generate and ``stage=meet`` share one
        ``state_seq``. Every write increments ``state_seq``. Reveal
        toggles are ``mc_ui`` only — no Wonder cue. Does not duplicate
        ``active_media`` or prompt payloads. ``canvas_ephemeral`` is
        always true.
        """
        session_row = school.get_live_session(session_id)
        posted = (
            request.get_json(silent=True) if request.method == "POST" else None
        )
        release_only = (
            isinstance(posted, dict)
            and set(posted) == {"eyes_up"}
            and posted.get("eyes_up") in (False, 0, "false", "0", "off")
        )
        if session_row is None:
            # MCK-159: a missing id is a 404 for every body, Release
            # included (Quit wipes the row; the staff toggle treats this
            # 404 on a Release as "already released").
            return jsonify({"ok": False, "error": "Session not found"}), 404
        if not _can_view_live_session(session_row):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        if request.method == "GET":
            return jsonify(
                {
                    "ok": True,
                    "teacher_state": school.live_session_teacher_state_payload(
                        session_id
                    ),
                    "defaults": default_teacher_state(),
                    "layout_presets": LAYOUT_PRESETS,
                }
            )
        body = posted or {}
        if release_only and session_row.get("status") != "active":
            # MCK-26: Release after End is a harmless no-op, never a 400.
            released = school.release_eyes_up(session_id)
            return jsonify({"ok": True, "teacher_state": released, "ended": True})
        kwargs: dict[str, Any] = {}
        for key in (
            "advance",
            "stage",
            "round",
            "round_flags",
            "teams_mode",
            "groups_configured",
            "run_as_group",
            "scoreboard_visible",
            "hide_absent",
            "timer_closes_answers",
            "class_set",
            "eyes_up",
            "layout_preset",
            "frames",
            "active_tab",
            "active_media_ref",
            "prompt_ref",
            "cue_id",
            "meet_chain",
            "meet_action",
            "assign",
            "student_frames",
            "student_view",
            "unlocks",
            "canvas_align",
            "mc_ui",
            "live_slot",
            "live_module",
            "text_ride",
            "question_views",
            "page_id",
            "gamification_on",
        ):
            if key in body:
                kwargs[key] = body.get(key)
        try:
            before_state = school.live_session_teacher_state_payload(session_id)
        except KeyError:
            before_state = None
        try:
            state = school.set_live_session_teacher_state(session_id, **kwargs)
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        emit_session_news(
            school,
            session_id,
            events_for_teacher_patch(before_state, state),
        )
        payload = {"ok": True, "teacher_state": state}
        if "assign" in body or "advance" in body:
            try:
                payload["game"] = school.game.game_state(int(session_row["class_id"]))
            except Exception:
                report_swallowed("teacher patch game state")
        return jsonify(payload)

    @bp.route(
        "/api/live-sessions/<int:session_id>/questions/<question_id>/visibility",
        methods=["POST"],
    )
    @login_required
    def api_live_question_visibility(session_id: int, question_id: str):
        """Set one question teacher-only or activate it for individual students."""

        session_row = school.get_live_session(session_id)
        if session_row is None:
            return jsonify({"ok": False, "error": "Session not found"}), 404
        if not _can_view_live_session(session_row):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        if session_row.get("status") != "active":
            return jsonify({"ok": False, "error": "Session is not active"}), 409
        body = request.get_json(silent=True) or {}
        try:
            result = school.set_live_question_visibility(
                session_id,
                question_id,
                str(body.get("mode") or "none"),
            )
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        seq = teacher_state_seq(school, session_id)
        emit_session_news(
            school,
            session_id,
            [
                {"type": "state_seq", "state_seq": seq},
                flag_work_event("visibility", seq),
            ],
        )
        return jsonify({"ok": True, **result})

    @bp.route(
        "/api/live-sessions/<int:session_id>/items/<int:live_item_id>/publish",
        methods=["POST"],
    )
    @login_required
    def api_publish_live_item(session_id: int, live_item_id: int):
        """Publish one lifecycle item without deactivating other items."""

        _row, error = _active_owned_live_session(session_id)
        if error is not None:
            return error
        body = request.get_json(silent=True) or {}
        write_started = time.perf_counter()
        try:
            item = school.publish_live_session_item(
                session_id,
                live_item_id,
                publish_mode=str(body.get("publish_mode") or "individual"),
            )
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        write_ms = (time.perf_counter() - write_started) * 1000
        card_started = time.perf_counter()
        seq = teacher_state_seq(school, session_id)
        card = None
        if isinstance(item, dict):
            try:
                card = school.public_push_card(item)
            except Exception:
                logger.warning(
                    "push card skipped session=%s item=%s",
                    session_id,
                    live_item_id,
                )
                card = None
        card_ms = (time.perf_counter() - card_started) * 1000
        prompt_id = item.get("prompt_id") if isinstance(item, dict) else None
        emit_started = time.perf_counter()
        emit_session_news(
            school,
            session_id,
            [
                {"type": "state_seq", "state_seq": seq},
                prompt_event(prompt_id, seq, card),
                flag_work_event("publish", seq),
            ],
        )
        record_push_stages(
            state_write_ms=write_ms,
            seq_and_card_ms=card_ms,
            emit_ms=(time.perf_counter() - emit_started) * 1000,
        )
        return jsonify(
            {
                "ok": True,
                "item": item,
                "active_questions": school.list_active_live_questions(
                    session_id
                ),
            }
        )

    @bp.route(
        "/api/live-sessions/<int:session_id>/items/<int:live_item_id>/close",
        methods=["POST"],
    )
    @login_required
    def api_close_live_item(session_id: int, live_item_id: int):
        """Close one published item and lock its result set."""

        _row, error = _active_owned_live_session(session_id)
        if error is not None:
            return error
        try:
            item = school.close_live_session_item(session_id, live_item_id)
            results = school.live_session_item_results(
                session_id, live_item_id
            )
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        seq = teacher_state_seq(school, session_id)
        emit_session_news(
            school,
            session_id,
            [
                {"type": "state_seq", "state_seq": seq},
                flag_work_event("close", seq),
            ],
        )
        return jsonify({"ok": True, "item": item, "results": results})

    @bp.route(
        "/api/live-sessions/<int:session_id>/items/<int:live_item_id>/reopen",
        methods=["POST"],
    )
    @login_required
    def api_reopen_live_whiteboard(session_id: int, live_item_id: int):
        """Reopen a closed whiteboard with the last board or a fresh one.

        Body ``{"start": "last" | "fresh"}`` (default ``last``). One pick
        covers the teacher board and every group or individual board
        (MCK-174). Students hear it on the same ``state_seq`` postcard a
        publish sends.
        """

        def reopen_conflict(exc: WhiteboardReopenConflict):
            # 409 with a machine-readable reason (MCK-174): "already_open"
            # (not closed, or a second tab reopened it first) repaints
            # quietly; "class_ended" shows wb.reopen.error.ended.
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": str(exc),
                        "conflict": True,
                        "reason": exc.reason,
                    }
                ),
                409,
            )

        _row, error = _active_owned_live_session(session_id)
        if error is not None:
            session_row = school.get_live_session(session_id)
            if (
                session_row is not None
                and _can_view_live_session(session_row)
                and str(session_row.get("status") or "") != "active"
            ):
                # Ended class the teacher owns. The store picks the answer
                # (MCK-181 LOW-3): missing item 404, not a whiteboard 400,
                # else 409 class_ended.
                try:
                    school.reopen_live_whiteboard(session_id, live_item_id)
                except WhiteboardReopenConflict as exc:
                    return reopen_conflict(exc)
                except (KeyError, ValueError) as exc:
                    return _json_error(exc)
                return reopen_conflict(
                    WhiteboardReopenConflict(
                        WHITEBOARD_REOPEN_CLASS_ENDED,
                        reason=WHITEBOARD_REOPEN_ENDED_REASON,
                    )
                )
            return error
        body = request.get_json(silent=True) or {}
        try:
            item = school.reopen_live_whiteboard(
                session_id,
                live_item_id,
                start=str(body.get("start") or "last"),
            )
        except WhiteboardReopenConflict as exc:
            return reopen_conflict(exc)
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        seq = teacher_state_seq(school, session_id)
        emit_session_news(
            school,
            session_id,
            [
                {"type": "state_seq", "state_seq": seq},
                flag_work_event("reopen", seq),
            ],
        )
        return jsonify(
            {
                "ok": True,
                "item": item,
                "run_key": school.live_board_run_key(session_id),
            }
        )

    @bp.route(
        "/api/live-sessions/<int:session_id>/timer-expired",
        methods=["POST"],
    )
    @login_required
    def api_live_timer_expired(session_id: int):
        """Teacher clock reached 0:00: close answers if the opt-in is on.

        The server re-checks the flag and its own clock, so an early or
        repeated call closes nothing (MCK-27).
        """

        _row, error = _active_owned_live_session(session_id)
        if error is not None:
            return error
        try:
            # The close publishes its own news (same postcards as Close).
            closed = school.close_answers_if_timer_expired(session_id)
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        flag, class_id = school._timer_closes_answers_on(session_id)
        due_in_ms = (
            school.session_timer_due_in_ms(class_id)
            if flag and class_id is not None
            else None
        )
        return jsonify(
            {
                "ok": True,
                "closed": closed,
                # Server clock: ms until 0:00, 0 once passed, null when the
                # switch is off or the clock is paused/idle. The teacher tab
                # retries on this, not on its own clock (MCK-27 M2).
                "due_in_ms": due_in_ms,
                "active_questions": school.list_active_live_questions(session_id),
            }
        )

    @bp.route(
        "/api/live-sessions/<int:session_id>/items/<int:live_item_id>/settings",
        methods=["PATCH", "POST"],
    )
    @login_required
    def api_live_item_settings(session_id: int, live_item_id: int):
        """Update one item's teacher settings without publishing it."""

        _row, error = _active_owned_live_session(session_id)
        if error is not None:
            return error
        body = request.get_json(silent=True) or {}
        has_results = "show_live_results" in body
        has_save = "save_to_card" in body
        has_publish = "publish_mode" in body
        has_response = "response_mode" in body
        has_rank_mode = "group_rank_mode" in body
        # MCK-171: Team challenge (Group line).
        has_race = "group_rank_race" in body
        if not any(
            (has_results, has_save, has_publish, has_response, has_rank_mode, has_race)
        ):
            return jsonify(
                {
                    "ok": False,
                    "error": (
                        "show_live_results, save_to_card, publish_mode, "
                        "or response_mode is required"
                    ),
                }
            ), 400
        try:
            item = school.update_live_session_item_settings(
                session_id,
                live_item_id,
                show_live_results=body.get("show_live_results") if has_results else None,
                save_to_card=body.get("save_to_card") if has_save else None,
                publish_mode=body.get("publish_mode") if has_publish else None,
                response_mode=body.get("response_mode") if has_response else None,
                group_rank_mode=body.get("group_rank_mode") if has_rank_mode else None,
                group_rank_race=body.get("group_rank_race") if has_race else None,
            )
            teacher_state = school.live_session_teacher_state_payload(session_id)
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        return jsonify(
            {
                "ok": True,
                "item": item,
                "teacher_state": teacher_state,
            }
        )

    @bp.route(
        "/api/live-sessions/<int:session_id>/items/<int:live_item_id>/results"
    )
    @login_required
    def api_live_item_results(session_id: int, live_item_id: int):
        """Return teacher-only live or final results for one item."""

        _row, error = _active_owned_live_session(session_id)
        if error is not None:
            return error
        try:
            results = school.live_session_item_results(
                session_id, live_item_id
            )
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        return jsonify({"ok": True, "results": results})

    @bp.route(
        "/api/live-sessions/<int:session_id>/items/<int:live_item_id>/team-draw/gallery",
        methods=["GET"],
    )
    @login_required
    @feature_required_any("team_draw", "group_draw")
    def api_team_draw_gallery(session_id: int, live_item_id: int):
        """Teacher gallery for one Team Draw item (session-only images)."""

        _row, error = _active_owned_live_session(session_id)
        if error is not None:
            return error
        try:
            gallery = school.team_draw_gallery(session_id, live_item_id)
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        return jsonify({"ok": True, "gallery": gallery})

    @bp.route(
        "/api/live-sessions/<int:session_id>/items/<int:live_item_id>/team-draw/clear",
        methods=["POST"],
    )
    @login_required
    @feature_required_any("team_draw", "group_draw")
    def api_team_draw_clear(session_id: int, live_item_id: int):
        """End Team Draw boards: purge team ink and gallery blobs."""

        _row, error = _active_owned_live_session(session_id)
        if error is not None:
            return error
        try:
            view = school.team_draw_end_and_clear(session_id, live_item_id)
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        # Class-wide on purpose: every team's boards were cleared.
        emit_answer_landed(
            school,
            session_id,
            student_id=None,
            scope="group",
        )
        return jsonify({"ok": True, "team_draw": view})

    @bp.route(
        "/api/live-sessions/<int:session_id>/items/<int:live_item_id>/end-voting",
        methods=["POST"],
    )
    @login_required
    def api_end_group_voting(session_id: int, live_item_id: int):
        """Advance every unfinished group from voting to discussion."""

        _row, error = _active_owned_live_session(session_id)
        if error is not None:
            return error
        try:
            item = school.get_live_session_item(session_id, live_item_id)
            if str(item.get("response_mode") or "") == "group_submit":
                # MCK-155 option B: Start group step sends every group still
                # picking to the agree step.
                view = school.end_group_mc_pick_step(session_id, live_item_id)
                # Gate MED-3: same postcard as Skip, so live-channel tabs
                # move to the agree step now, not on the safety poll.
                # Class-wide on purpose: every group leaves the pick step.
                emit_answer_landed(
                    school,
                    session_id,
                    student_id=None,
                    scope="group",
                )
                return jsonify({"ok": True, "item": view.get("item"), "group_submit": view})
            summary = school.end_group_consensus_voting(
                session_id, live_item_id
            )
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        return jsonify({"ok": True, **summary})

    @bp.route(
        "/api/live-sessions/<int:session_id>/items/<int:live_item_id>/rank-turn/skip",
        methods=["POST"],
    )
    @login_required
    def api_rank_turn_skip(session_id: int, live_item_id: int):
        """MCK-155 take turns: teacher Skip for one group."""

        _row, error = _active_owned_live_session(session_id)
        if error is not None:
            return error
        body = request.get_json(silent=True) or {}
        # Gate LOW-3: a missing or non-numeric team is a 400, not a 500.
        try:
            team_id = int(body.get("team_id"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Choose a group to skip."}), 400
        try:
            result = school.rank_turn_skip(
                session_id, live_item_id, team_id=team_id
            )
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        emit_answer_landed(
            school,
            session_id,
            student_id=None,
            team_id=team_id,
            scope="group",
        )
        return jsonify({"ok": True, **result})

    @bp.route(
        "/api/live-sessions/<int:session_id>/items/<int:live_item_id>/rank-lock",
        methods=["POST"],
    )
    @login_required
    def api_rank_race_lock_for(session_id: int, live_item_id: int):
        """MCK-171 Team challenge: teacher "Lock in for {team}"."""

        _row, error = _active_owned_live_session(session_id)
        if error is not None:
            return error
        body = request.get_json(silent=True) or {}
        try:
            team_id = int(body.get("team_id"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Choose a group to lock in."}), 400
        try:
            result = school.rank_race_lock_for(
                session_id, live_item_id, team_id=team_id
            )
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        if result.get("locked"):
            emit_answer_landed(
                school,
                session_id,
                student_id=None,
                team_id=team_id,
                scope="group",
            )
        return jsonify({"ok": True, **result})

    @bp.route(
        "/api/live-sessions/<int:session_id>/items/<int:live_item_id>/race-step",
        methods=["POST"],
    )
    @login_required
    def api_rank_race_step(session_id: int, live_item_id: int):
        """MCK-171 Team challenge: move the projector reveal to ``step``.

        Only the exact next step is accepted (compare-and-set on the stored
        step). Anything else (double-click, a second tab, End Game racing
        Next) is a 409 carrying the real ``step`` and ``results`` so the
        client resyncs quietly. Reaching the points step pays the team
        points once through the award ledger. Phones hear about it on the
        same news wire as Close and Reopen.
        """

        _row, error = _active_owned_live_session(session_id)
        if error is not None:
            return error
        body = request.get_json(silent=True) or {}
        try:
            step = int(body.get("step"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Missing reveal step."}), 400
        try:
            result = school.set_rank_race_step(session_id, live_item_id, step=step)
        except RankRaceStepConflict as exc:
            try:
                view = school.rank_race_step_view(session_id, live_item_id)
            except (KeyError, ValueError):
                view = {"step": exc.step, "results": None}
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": str(exc),
                        "conflict": True,
                        **view,
                    }
                ),
                409,
            )
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        seq = teacher_state_seq(school, session_id)
        emit_session_news(
            school,
            session_id,
            [
                {"type": "state_seq", "state_seq": seq},
                flag_work_event("race_step", seq),
            ],
        )
        return jsonify({"ok": True, **result})

    @bp.route(
        "/api/live-sessions/<int:session_id>/items/<int:live_item_id>/points",
        methods=["POST"],
    )
    @login_required
    def api_award_group_item_points(session_id: int, live_item_id: int):
        """Award a finalized group response to every current team member."""

        _row, error = _active_owned_live_session(session_id)
        if error is not None:
            return error
        body = request.get_json(silent=True) or {}
        try:
            result = school.award_group_consensus_points(
                session_id,
                live_item_id,
                team_id=int(body.get("team_id")),
                amount=int(body.get("amount") or 1),
            )
        except (KeyError, TypeError, ValueError) as exc:
            return _json_error(exc)
        return jsonify({"ok": True, **result})

    @bp.route(
        "/api/live-sessions/<int:session_id>/items/<int:live_item_id>/rank-points",
        methods=["POST"],
    )
    @login_required
    def api_award_group_rank_points(session_id: int, live_item_id: int):
        """MCK-185 "Score teams": set teams' points on a group answer-order rank.

        Body ``{awards: [{team_id, points}], team_rule}`` (one row's Assign,
        or Assign all). Re-assigning replaces that team's earlier award for
        this question; the same points and rule again change nothing.
        Manual only; nothing is awarded unless the teacher presses Assign.
        """

        _row, error = _active_owned_live_session(session_id)
        if error is not None:
            return error
        body = request.get_json(silent=True)
        # Gate LOW on f0a15be: a malformed body (not an object, awards not
        # a list of objects) is a 400, never a 500.
        if not isinstance(body, dict):
            return jsonify({"ok": False, "error": "Send {awards: [{team_id, points}], team_rule}"}), 400
        awards = body.get("awards")
        if not isinstance(awards, list) or not all(isinstance(row, dict) for row in awards):
            return jsonify({"ok": False, "error": "awards must be a list of {team_id, points}"}), 400
        rule = body.get("team_rule")
        if rule is not None and not isinstance(rule, str):
            return jsonify({"ok": False, "error": "team_rule must be text"}), 400
        try:
            result = school.assign_rank_team_points(
                session_id,
                live_item_id,
                awards=awards,
                team_rule=(rule or None),
            )
        except (KeyError, TypeError, ValueError) as exc:
            return _json_error(exc)
        return jsonify({"ok": True, **result})

    def _without_response_names(rows: list[Any]) -> list[Any]:
        """Response rows with names and characters blanked (Hide names).

        Args:
            rows: ``live_prompt_response_roster`` rows.
        """
        out = []
        for row in rows:
            if isinstance(row, dict):
                row = {**row, "name": "", "character": None}
            out.append(row)
        return out

    @bp.route(
        "/api/live-sessions/<int:session_id>/questions/<int:prompt_id>/responses",
        methods=["GET", "POST"],
    )
    @login_required
    def api_live_question_responses(session_id: int, prompt_id: int):
        """Read ephemeral response rows or award their live-game points."""

        session_row = school.get_live_session(session_id)
        if session_row is None:
            return jsonify({"ok": False, "error": "Session not found"}), 404
        if not _can_view_live_session(session_row):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        if session_row.get("status") != "active":
            return jsonify({"ok": False, "error": "Responses are active-session only"}), 409
        try:
            if request.method == "GET":
                responses = school.live_prompt_response_roster(
                    session_id, prompt_id
                )
                if str(request.args.get("hide_names") or "") == "1":
                    responses = _without_response_names(responses)
                # Hide-names labels stay fixed per run (MCK-111).
                return jsonify(
                    {
                        "ok": True,
                        "responses": responses,
                        "run_key": session_row.get("run_key"),
                    }
                )
            body = request.get_json(silent=True) or {}
            result = school.award_live_prompt_points(
                session_id,
                prompt_id,
                mode=str(body.get("mode") or ""),
                student_ids=body.get("student_ids")
                if isinstance(body.get("student_ids"), list)
                else [],
                amount=int(body.get("amount") or 1),
                replace=bool(body.get("replace", True)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            return _json_error(exc)
        if body.get("hide_names") is True and isinstance(
            result.get("responses"), list
        ):
            result = {
                **result,
                "responses": _without_response_names(result["responses"]),
            }
        return jsonify({"ok": True, **result})

    @bp.route(
        "/api/live-sessions/<int:session_id>/canvas-presence",
        methods=["POST"],
    )
    def api_live_session_canvas_presence(session_id: int):
        """Thin cursor / stroke tick for Frozen or team-shared canvas.

        Staff or a joined student may post. Unique-per-student alignment
        stores a cursor only. Wonder is not involved.
        """
        session_row = school.get_live_session(session_id)
        if session_row is None:
            return jsonify({"ok": False, "error": "Session not found"}), 404
        from feature_gates import session_whiteboard_closed

        if session_whiteboard_closed(session_id):
            abort(404)
        body = request.get_json(silent=True) or {}
        as_teacher = False
        owner = ""
        name = ""
        team_id = None
        if _can_view_live_session(session_row):
            as_teacher = True
            owner = "teacher"
            name = "Teacher"
        else:
            # This route lives outside create_app, so student identity is
            # resolved here. A joined student may post; anyone else may not.
            ctx = resolve_student_live_context(school, session)
            if (
                ctx is None
                or int(ctx.get("live_session_id") or 0) != int(session_id)
            ):
                return jsonify({"ok": False, "error": "Forbidden"}), 403
            student_id = ctx.get("student_id")
            if student_id in (None, ""):
                return jsonify({"ok": False, "error": "Forbidden"}), 403
            token = str(ctx.get("visit_token") or visit_token_from_request() or "")
            if not school.student_is_active_live_attendee(
                int(session_id),
                int(student_id),
                visit_token=token,
            ):
                return jsonify({"ok": False, "error": "Forbidden"}), 403
            if token:
                try:
                    school.touch_live_session_heartbeat(token)
                except Exception as exc:
                    from live_presence import LivePresenceUnavailable

                    locked = isinstance(exc, sqlite3.OperationalError) and (
                        "locked" in str(exc).lower()
                    )
                    if not isinstance(exc, LivePresenceUnavailable) and not locked:
                        raise
            owner = str(int(student_id))
            name = str(ctx.get("codename") or body.get("name") or owner).strip() or owner
            team_id = school.student_team_id_for_class(
                int(session_row["class_id"]), int(student_id)
            )
        text_id = str(body.get("text_id") or "").strip()
        batch_id = str(body.get("client_batch_id") or "").strip() or None
        try:
            if text_id:
                school.apply_live_canvas_text(
                    session_id,
                    owner=owner,
                    name=name,
                    text_id=text_id,
                    text=str(body.get("text") if body.get("text") is not None else ""),
                    team_id=team_id,
                    x=body.get("x"),
                    y=body.get("y"),
                    as_teacher=as_teacher,
                    claimed_run_key=str(body.get("run_key") or "").strip(),
                )
            elif str(body.get("op") or body.get("type") or "") == "stroke_remove":
                school.remove_live_board_stroke(
                    session_id,
                    owner=owner,
                    stroke_id=str(body.get("stroke_id") or body.get("id") or ""),
                    team_id=team_id,
                    as_teacher=as_teacher,
                    client_batch_id=batch_id,
                    claimed_run_key=str(body.get("run_key") or "").strip(),
                )
            else:
                raw_points = body.get("points")
                school.apply_live_canvas_presence(
                    session_id,
                    owner=owner,
                    name=name,
                    team_id=team_id,
                    x=body.get("x"),
                    y=body.get("y"),
                    stroke_id=str(body.get("stroke_id") or "") or None,
                    point=body.get("point"),
                    points=raw_points if isinstance(raw_points, list) else None,
                    ended=bool(body.get("ended")),
                    as_teacher=as_teacher,
                    client_batch_id=batch_id,
                    claimed_run_key=str(body.get("run_key") or "").strip(),
                )
            reply = school.canvas_presence_reply(
                session_id,
                student_id=int(owner) if owner.isdigit() else None,
                as_teacher=as_teacher,
                since=_optional_board_seq(body.get("since")),
                teacher_since=_optional_board_seq(body.get("teacher_since")),
            )
        except BoardSessionClosed as exc:
            return board_write_closed(exc)
        except BoardOpRejected as exc:
            return jsonify({"ok": False, "error": str(exc)}), 403
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        # Staff stays on the teacher board (#165). A student receives only
        # their own team plus the teacher — never another group's ops.
        return jsonify(reply)

    def _student_for_board(session_id: int) -> tuple[int | None, Any]:
        """Return the joined student id, or an error response.

        Args:
            session_id: Live session the board belongs to.

        Returns:
            ``(student_id, None)`` or ``(None, response)``.
        """
        ctx = resolve_student_live_context(school, session)
        if ctx is None or int(ctx.get("live_session_id") or 0) != int(session_id):
            return None, (jsonify({"ok": False, "error": "Forbidden"}), 403)
        student_id = ctx.get("student_id")
        if student_id in (None, ""):
            return None, (jsonify({"ok": False, "error": "Forbidden"}), 403)
        token = str(ctx.get("visit_token") or visit_token_from_request() or "")
        if not school.student_is_active_live_attendee(
            int(session_id), int(student_id), visit_token=token
        ):
            return None, (jsonify({"ok": False, "error": "Forbidden"}), 403)
        return int(student_id), None

    def _resolve_student_board_key(session_id: int, student_id: int, raw_key: str) -> tuple[str | None, Any]:
        """Map ``mine`` and reject a board this student cannot read.

        Args:
            session_id: Live session id.
            student_id: Roster id.
            raw_key: Path key, including the ``mine`` alias.

        Returns:
            ``(key, None)`` or ``(None, 403 response)``.
        """
        access = school.student_board_access(int(session_id), int(student_id))
        token = "mine" if str(raw_key or "").strip() == "mine" else str(raw_key or "").strip()
        if token == "mine":
            return str(access["collab_key"]), None
        try:
            key = normalize_board_key(token)
        except ValueError:
            return None, (jsonify({"ok": False, "error": "Forbidden"}), 403)
        if key not in access["read"]:
            return None, (jsonify({"ok": False, "error": "Forbidden"}), 403)
        return key, None

    def _joined_student_board() -> tuple[dict[str, Any] | None, Any]:
        """Return the visit-token student, or an error response.

        These routes sit outside ``create_app``, so they cannot call the
        nested student helpers defined there.

        Returns:
            ``(context, None)`` or ``(None, response)``.
        """
        ctx = resolve_student_live_context(school, session)
        if ctx is None:
            return None, (jsonify({"ok": False, "error": "Not joined."}), 401)
        student_id = ctx.get("student_id")
        live_session_id = int(ctx.get("live_session_id") or 0)
        if student_id in (None, "") or not live_session_id:
            return None, (jsonify({"ok": False, "error": "Forbidden"}), 403)
        token = str(ctx.get("visit_token") or visit_token_from_request() or "")
        if not school.student_is_active_live_attendee(
            live_session_id, int(student_id), visit_token=token
        ):
            return None, (jsonify({"ok": False, "error": "Forbidden"}), 403)
        return ctx, None

    @bp.route("/api/student/board/<board_key>", methods=["GET"])
    @student_required
    def api_student_board(board_key: str):
        """Ink since seq for this student's team board, plus teacher ink.

        ``mine`` is the student's own team (or ``shared``). Another team's
        key is 403. A wide gap returns ``snapshot`` instead of the op list.
        """
        ctx, denied = _joined_student_board()
        if denied is not None:
            return denied
        student_id = int(ctx["student_id"])
        live_session_id = int(ctx["live_session_id"])
        from feature_gates import session_whiteboard_closed

        if session_whiteboard_closed(live_session_id):
            abort(404)
        key, rejected = _resolve_student_board_key(
            live_session_id, student_id, board_key
        )
        if rejected is not None:
            return rejected
        if str(key).startswith("team:") and _team_board_closed(live_session_id):
            abort(404)
        try:
            reply = school.read_board(
                live_session_id,
                str(key),
                _optional_board_seq(request.args.get("since")) or 0,
                student_id=student_id,
                teacher_since=_optional_board_seq(request.args.get("teacher_since")),
            )
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        except Exception as exc:
            busy = _board_read_pool_busy(exc)
            if busy is not None:
                return busy
            raise
        return jsonify(reply)

    @bp.route("/api/student/board/<board_key>/ops", methods=["POST"])
    @student_required
    def api_student_board_ops(board_key: str):
        """Append a batch of ops to a board this student may write.

        ``stroke_remove`` is rejected when the stroke is not theirs. The
        reply piggybacks ops since the caller's cursors.
        """
        ctx, denied = _joined_student_board()
        if denied is not None:
            return denied
        student_id = int(ctx["student_id"])
        class_id = int(ctx["class_id"])
        live_session_id = int(ctx["live_session_id"])
        from feature_gates import session_whiteboard_closed

        if session_whiteboard_closed(live_session_id):
            abort(404)
        access = school.student_board_access(live_session_id, int(student_id))
        token = str(board_key or "").strip()
        if token == "mine":
            key = str(access["collab_key"])
        else:
            try:
                key = normalize_board_key(token)
            except ValueError:
                return jsonify({"ok": False, "error": "Forbidden"}), 403
        if key not in access["write"]:
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        if str(key).startswith("team:") and _team_board_closed(live_session_id):
            abort(404)
        body = request.get_json(silent=True) or {}
        ops = body.get("ops") if isinstance(body.get("ops"), list) else []
        team_id = school.student_team_id_for_class(int(class_id), int(student_id))
        try:
            school.append_live_board_ops(
                live_session_id,
                key,
                owner=str(int(student_id)),
                ops=[
                    {**op, "team_id": team_id, "name": str(ctx.get("codename") or student_id)}
                    if isinstance(op, dict)
                    else op
                    for op in ops
                ],
                client_batch_id=str(body.get("client_batch_id") or "").strip() or None,
                claimed_run_key=str(body.get("run_key") or "").strip(),
            )
            reply = school.canvas_presence_reply(
                live_session_id,
                student_id=int(student_id),
                since=_optional_board_seq(body.get("since")),
                teacher_since=_optional_board_seq(body.get("teacher_since")),
            )
        except BoardSessionClosed as exc:
            return board_write_closed(exc)
        except BoardOpRejected as exc:
            return jsonify({"ok": False, "error": str(exc)}), 403
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        return jsonify(reply)

    def _teacher_formative_session(session_id: int):
        """Return a teacher-owned session, or an error response.

        Tile polls and Show on board are staff routes. A joined student
        is not a teacher for this session.

        Args:
            session_id: ``live_class_sessions.id``.

        Returns:
            ``(row, None)`` or ``(None, response)``.
        """
        row = school.get_live_session(session_id)
        if row is None:
            return None, (jsonify({"ok": False, "error": "Session not found"}), 404)
        if not _can_view_live_session(row):
            return None, (jsonify({"ok": False, "error": "Forbidden"}), 403)
        return row, None

    @bp.route(
        "/api/live-sessions/<int:session_id>/whiteboard-gallery",
        methods=["GET"],
    )
    @login_required
    @feature_required_any("whiteboard", "whiteboard_submit")
    def api_whiteboard_gallery(session_id: int):
        """Teacher gallery. Hidden until a whiteboard question is published."""
        from formative_live_board import parse_cursors

        held = _whiteboard_overview_hold(session_id)
        if held is not None:
            return held
        _row, denied = _teacher_formative_session(session_id)
        if denied is not None:
            return denied
        try:
            reply = school.whiteboard_gallery(
                session_id, parse_cursors(request.args.get("cursors"))
            )
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        return jsonify(reply)

    @bp.route(
        "/api/live-sessions/<int:session_id>/whiteboard-gallery/<path:board_key>",
        methods=["GET"],
    )
    @login_required
    @feature_required_any("whiteboard", "whiteboard_submit")
    def api_whiteboard_gallery_one(session_id: int, board_key: str):
        """Teacher side panel for one team or solo board."""
        held = _whiteboard_overview_hold(session_id)
        if held is not None:
            return held
        _row, denied = _teacher_formative_session(session_id)
        if denied is not None:
            return denied
        try:
            reply = school.whiteboard_open_board(
                session_id,
                board_key,
                since=_optional_board_seq(request.args.get("since")) or 0,
            )
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        return jsonify(reply)

    @bp.route(
        "/api/live-sessions/<int:session_id>/whiteboard-gallery/<path:board_key>/feedback",
        methods=["POST"],
    )
    @login_required
    @feature_required_any("whiteboard", "whiteboard_submit")
    def api_whiteboard_gallery_feedback(session_id: int, board_key: str):
        """Append live teacher ink. Does not create a feedback layer."""
        _row, denied = _teacher_formative_session(session_id)
        if denied is not None:
            return denied
        body = request.get_json(silent=True) or {}
        ops = body.get("ops") if isinstance(body.get("ops"), list) else []
        try:
            school.append_whiteboard_feedback(
                session_id,
                board_key,
                ops,
                client_batch_id=str(body.get("client_batch_id") or "").strip() or None,
                claimed_run_key=str(body.get("run_key") or "").strip() or None,
            )
            reply = school.whiteboard_open_board(session_id, board_key)
        except BoardSessionClosed as exc:
            return board_write_closed(exc)
        except BoardOpRejected as exc:
            return jsonify({"ok": False, "error": str(exc)}), 403
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        return jsonify(reply)

    @bp.route(
        "/api/live-sessions/<int:session_id>/whiteboard-gallery/<path:board_key>/release",
        methods=["POST"],
    )
    @login_required
    @feature_required_any("whiteboard", "whiteboard_submit")
    def api_whiteboard_gallery_release(session_id: int, board_key: str):
        """Release feedback rows already stored. New ink does not use this."""
        _row, denied = _teacher_formative_session(session_id)
        if denied is not None:
            return denied
        try:
            reply = school.release_whiteboard_feedback(session_id, board_key)
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        return jsonify(reply)

    @bp.route(
        "/api/live-sessions/<int:session_id>/whiteboard-gallery/<path:board_key>/clear",
        methods=["POST"],
    )
    @login_required
    @feature_required_any("whiteboard", "whiteboard_submit")
    def api_whiteboard_gallery_clear(session_id: int, board_key: str):
        """Clear student ink on one board. Teacher ink stays."""
        _row, denied = _teacher_formative_session(session_id)
        if denied is not None:
            return denied
        try:
            reply = school.clear_whiteboard_board(session_id, board_key)
        except BoardSessionClosed as exc:
            return board_write_closed(exc)
        except BoardOpRejected as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        return jsonify(reply)

    @bp.route(
        "/api/live-sessions/<int:session_id>/whiteboard-gallery/<path:board_key>/show",
        methods=["POST"],
    )
    @login_required
    @feature_required_any("whiteboard", "whiteboard_submit")
    def api_whiteboard_gallery_show(session_id: int, board_key: str):
        """Show one board to the class, including its colour legend."""
        _row, denied = _teacher_formative_session(session_id)
        if denied is not None:
            return denied
        try:
            reply = school.show_whiteboard_board(session_id, board_key)
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        return jsonify(reply)

    @bp.route(
        "/api/live-sessions/<int:session_id>/board/<board_key>",
        methods=["GET"],
    )
    def api_live_session_board(session_id: int, board_key: str):
        """Since-seq read. Staff may read only the teacher board.

        A joined student may read their own team and the teacher board.
        Cross-team keys are 403. Group boards are not returned to staff.
        """
        session_row = school.get_live_session(session_id)
        if session_row is None:
            return jsonify({"ok": False, "error": "Session not found"}), 404
        from feature_gates import session_whiteboard_closed

        if session_whiteboard_closed(session_id):
            abort(404)
        if _can_view_live_session(session_row):
            if str(board_key or "").strip() != "teacher":
                return jsonify({"ok": False, "error": "Forbidden"}), 403
            held = _whiteboard_overview_hold(session_id)
            if held is not None:
                return held
            try:
                reply = school.read_board(
                    session_id,
                    "teacher",
                    _optional_board_seq(request.args.get("since")) or 0,
                    as_teacher=True,
                )
            except (KeyError, ValueError) as exc:
                return _json_error(exc)
            except Exception as exc:
                busy = _board_read_pool_busy(exc)
                if busy is not None:
                    return busy
                raise
            return jsonify(reply)
        student_id, rejected = _student_for_board(session_id)
        if rejected is not None:
            return rejected
        key, key_rejected = _resolve_student_board_key(
            session_id, int(student_id or 0), board_key
        )
        if key_rejected is not None:
            return key_rejected
        if str(key).startswith("team:") and _team_board_closed(session_id):
            abort(404)
        try:
            reply = school.read_board(
                session_id,
                str(key),
                _optional_board_seq(request.args.get("since")) or 0,
                student_id=student_id,
                teacher_since=_optional_board_seq(request.args.get("teacher_since")),
            )
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        except Exception as exc:
            busy = _board_read_pool_busy(exc)
            if busy is not None:
                return busy
            raise
        return jsonify(reply)

    @bp.route(
        "/api/live-sessions/<int:session_id>/board/<board_key>/ops",
        methods=["POST"],
    )
    def api_live_session_board_ops(session_id: int, board_key: str):
        """Append ops. Staff write the teacher board only.

        A student write to another team's board is 403. Removing someone
        else's stroke is 403.
        """
        session_row = school.get_live_session(session_id)
        if session_row is None:
            return jsonify({"ok": False, "error": "Session not found"}), 404
        from feature_gates import session_whiteboard_closed

        if session_whiteboard_closed(session_id):
            abort(404)
        body = request.get_json(silent=True) or {}
        ops = body.get("ops") if isinstance(body.get("ops"), list) else []
        batch_id = str(body.get("client_batch_id") or "").strip() or None
        if _can_view_live_session(session_row):
            if str(board_key or "").strip() != "teacher":
                return jsonify({"ok": False, "error": "Forbidden"}), 403
            try:
                school.append_live_board_ops(
                    session_id,
                    "teacher",
                    owner="teacher",
                    ops=ops,
                    client_batch_id=batch_id,
                    claimed_run_key=str(body.get("run_key") or "").strip(),
                )
                reply = school.canvas_presence_reply(
                    session_id,
                    as_teacher=True,
                    since=_optional_board_seq(body.get("since")),
                )
            except BoardSessionClosed as exc:
                return board_write_closed(exc)
            except BoardOpRejected as exc:
                return jsonify({"ok": False, "error": str(exc)}), 403
            except (KeyError, ValueError) as exc:
                return _json_error(exc)
            return jsonify(reply)
        student_id, rejected = _student_for_board(session_id)
        if rejected is not None:
            return rejected
        access = school.student_board_access(session_id, int(student_id or 0))
        token = str(board_key or "").strip()
        if token == "mine":
            key = str(access["collab_key"])
        else:
            try:
                key = normalize_board_key(token)
            except ValueError:
                return jsonify({"ok": False, "error": "Forbidden"}), 403
        if key not in access["write"]:
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        if str(key).startswith("team:") and _team_board_closed(session_id):
            abort(404)
        try:
            school.append_live_board_ops(
                session_id,
                key,
                owner=str(int(student_id or 0)),
                ops=ops,
                client_batch_id=batch_id,
                    claimed_run_key=str(body.get("run_key") or "").strip(),
            )
            reply = school.canvas_presence_reply(
                session_id,
                student_id=student_id,
                since=_optional_board_seq(body.get("since")),
                teacher_since=_optional_board_seq(body.get("teacher_since")),
            )
        except BoardSessionClosed as exc:
            return board_write_closed(exc)
        except BoardOpRejected as exc:
            return jsonify({"ok": False, "error": str(exc)}), 403
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        return jsonify(reply)

    @bp.route("/api/classes/<int:class_id>/participation/modules/begin", methods=["POST"])
    @login_required
    def api_begin_new_module(class_id: int):
        """Wrap this section's open module. The wrap cannot be undone.

        The body is ``{"from_module": "M3"}``. One section only. The
        response has no roster fields.
        """
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        if body.get("undo"):
            return jsonify({"ok": False, "error": "This can't be undone."}), 400
        user = current_user()
        user_id = int(user["id"]) if user else None
        try:
            from begin_module import BeginModuleRejected
        except ImportError:
            from lms.begin_module import BeginModuleRejected
        try:
            result = school.begin_new_module(
                class_id,
                str(body.get("from_module") or "")[:20],
                user_id=user_id,
            )
        except BeginModuleRejected as exc:
            return jsonify({"ok": False, "error": str(exc)}), int(exc.status)
        return jsonify(result)

    @bp.route("/api/classes/<int:class_id>/lesson-slides/table")
    @login_required
    def api_lesson_slides_table(class_id: int):
        """MCK-229: one row per live class with latest-run deck and questions."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        from lesson_slides_table import build_lesson_slides_table

        payload = build_lesson_slides_table(school, class_id)
        response = jsonify(payload)
        response.headers["Cache-Control"] = "no-store"
        return response

    @bp.route(
        "/api/classes/<int:class_id>/lesson-slides/catchup-access",
        methods=["POST"],
    )
    @login_required
    def api_lesson_slides_catchup_access(class_id: int):
        """Close or reopen catch-up for one Lesson Slides row.

        Body is ``{module, slot, open, is_test?}``. The catch-up token is
        resolved on the server and passed to ``set_catchup_link_open``
        (MCK-198). The response has no token, join code, or PIN. Only the
        teacher who owns the class, or school IT in the same tenant, may
        call it. Closing does not delete student answers.
        """
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        user = current_user()
        if user is None or not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden."}), 403
        body = request.get_json(silent=True) or {}
        is_open = body.get("open")
        is_test = body.get("is_test", False)
        if not isinstance(is_open, bool) or not isinstance(is_test, bool):
            return jsonify({"ok": False, "error": "Missing open."}), 400
        try:
            result = school.set_lesson_slides_catchup_open(
                class_id,
                module=str(body.get("module") or ""),
                slot=str(body.get("slot") or ""),
                is_open=is_open,
                is_test=is_test,
            )
        except ValueError:
            return jsonify({"ok": False, "error": "Missing class."}), 400
        if result is None:
            return jsonify({"ok": False, "error": "Not found."}), 404
        response = jsonify({"ok": True, **result})
        response.headers["Cache-Control"] = "no-store"
        return response

    @bp.route(
        "/api/classes/<int:class_id>/lesson-slides/catchup-auto-close",
        methods=["POST"],
    )
    @login_required
    def api_lesson_slides_catchup_auto_close(class_id: int):
        """Save how many days after class end catch-up stays open.

        Applies to classes that end later. Existing rows keep their
        deadline. Only the owning teacher, or school IT in the same
        tenant, may change it.
        """
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        user = current_user()
        if user is None or not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden."}), 403
        body = request.get_json(silent=True) or {}
        if "days" not in body:
            return jsonify({"ok": False, "error": "Enter a whole number of days."}), 400
        try:
            days = school.set_catchup_auto_close_days(class_id, body.get("days"))
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        response = jsonify({"ok": True, "days": days})
        response.headers["Cache-Control"] = "no-store"
        return response

    def _staff_owns_lesson_slides(class_id: int):
        """Owning staff teacher, or a 401/403 response.

        School IT and any other teacher are refused. Lesson Slides
        delete stays on the section the teacher owns.

        Args:
            class_id: Game-show ``classes.id``.

        Returns:
            The signed-in user, or a Flask response to return.
        """
        user = current_user()
        if user is None:
            return jsonify({"ok": False, "error": "Authentication required."}), 401
        if str(user.get("role") or "") != "staff" or not school.teacher_owns_class(
            int(user["id"]), class_id
        ):
            return jsonify({"ok": False, "error": "Forbidden."}), 403
        return user

    @bp.route(
        "/api/classes/<int:class_id>/lesson-slides/entries/delete",
        methods=["POST"],
    )
    @login_required
    def api_delete_lesson_slides_entry(class_id: int):
        """Remove one Lesson Slides entry for the owning teacher.

        Live, Open Office, and Test entries are accepted. Attendance,
        participation, the gradebook, and catch-up stay.
        """
        owned = _staff_owns_lesson_slides(class_id)
        if isinstance(owned, tuple):
            return owned
        body = request.get_json(silent=True) or {}
        try:
            result = school.delete_lesson_slides_entry(
                int(class_id),
                body.get("module"),
                body.get("slot"),
                body.get("is_test"),
                body.get("run_key"),
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        response = jsonify(result)
        response.headers["Cache-Control"] = "no-store"
        return response

    @bp.route(
        "/api/classes/<int:class_id>/lesson-slides/test-entries/<int:session_id>/delete",
        methods=["POST"],
    )
    @login_required
    def api_delete_lesson_slides_test_entry(class_id: int, session_id: int):
        """Remove the Lesson Slides entry for one ended session.

        Live, Open Office, and Test sessions are accepted. School IT
        and any other teacher are refused. Attendance, participation,
        the gradebook, and catch-up stay.
        """
        owned = _staff_owns_lesson_slides(class_id)
        if isinstance(owned, tuple):
            return owned
        try:
            result = school.delete_lesson_slides_test_entry(int(class_id), int(session_id))
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except KeyError:
            return jsonify({"ok": False, "error": "That class was not found."}), 404
        response = jsonify(result)
        response.headers["Cache-Control"] = "no-store"
        return response

    def _meeting_from_state(class_id: int) -> date | None:
        """Read the open session's calendar date, if any.

        Args:
            class_id: Class primary key.

        Returns:
            Meeting date or None.
        """
        try:
            from gradebook import session_meeting_date
        except ImportError:
            from lms.gradebook import session_meeting_date
        try:
            state = school.game.game_state(class_id)
        except Exception:  # noqa: BLE001
            report_swallowed("session meeting date")
            return None
        sess = state.get("session") or {}
        return session_meeting_date(sess.get("starts_at") or sess.get("meeting_date"))

    def _validate_log_date(class_id: int, meeting: date | None) -> None:
        """Enforce school-day and optional live-class-day rules.

        Args:
            class_id: Class primary key.
            meeting: Chosen date.

        Raises:
            ValueError: When the date is not allowed.
        """
        if meeting is None:
            raise ValueError("Choose a date to log.")
        school.assert_log_date_allowed(class_id, meeting)

    @bp.route("/api/classes/<int:class_id>/begin", methods=["POST"])
    @login_required
    def api_begin(class_id: int):
        """Start a new game (staff only)."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        try:
            meeting = _optional_date(body.get("meeting_date"))
            if meeting is not None and body.get("validate"):
                _validate_log_date(class_id, meeting)
            state = school.begin_or_resume_live_game(
                class_id, meeting_date=meeting
            )
            return jsonify({"ok": True, **state})
        except Exception as exc:  # noqa: BLE001
            return _json_error(exc)

    @bp.route("/api/classes/<int:class_id>/live-session/start", methods=["POST"])
    @login_required
    def api_start_live_session(class_id: int):
        """Mint (or reuse) an active live join session without wiping setup game."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        user = current_user()
        assert user is not None
        try:
            body = request.get_json(silent=True) or {}
            session_row = _start_live_and_wake(
                school,
                class_id,
                int(user["id"]),
                live_module=body.get("live_module"),
                live_slot=body.get("live_slot"),
            )
            return jsonify(
                {
                    "ok": True,
                    "live_session": session_row,
                    "live_session_id": int(session_row["id"]),
                }
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            return _json_error(exc)

    @bp.route("/api/classes/<int:class_id>/live-session/kind", methods=["POST"])
    @login_required
    def api_commit_live_run_kind(class_id: int):
        """Record Test or Live on the active session before the class begins."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        user = current_user()
        assert user is not None
        from live_class_flags import parse_is_test_form, parse_is_test_json

        if request.is_json:
            body = request.get_json(silent=True) or {}
            is_test = parse_is_test_json(body.get("is_test"))
        else:
            is_test = parse_is_test_form(request.form.get("is_test"))
        try:
            session_row = school.commit_live_run_kind(
                int(class_id),
                int(user["id"]),
                is_test=is_test,
            )
            return jsonify(
                {
                    "ok": True,
                    "is_test": bool(int(session_row.get("is_test") or 0)),
                    "live_session_id": int(session_row["id"]),
                }
            )
        except LiveRunKindConflict as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            return _json_error(exc)

    @bp.route(
        "/api/classes/<int:class_id>/live-sessions/<int:session_id>/delete-test",
        methods=["POST"],
    )
    @login_required
    def api_delete_test_live_class(class_id: int, session_id: int):
        """Delete one test live class. Real runs are refused."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        try:
            result = school.delete_test_live_class(int(class_id), int(session_id))
            return jsonify(result)
        except DeleteTestLiveClassConflict as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        except PermissionError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 403
        except KeyError:
            return jsonify({"ok": False, "error": "That class was not found."}), 404
        except Exception as exc:  # noqa: BLE001
            return _json_error(exc)

    @bp.route("/api/classes/<int:class_id>/live-slides", methods=["GET", "POST"])
    @staff_required
    def api_class_live_slides(class_id: int):
        """Generate or fetch the live-class deck for a Class Date."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id) and user["role"] != "it":
            abort(403)
        from live_class_constants import is_slides_operator_email
        from live_class_slides import (
            SlidesConnectRequired,
            SlidesOperatorDenied,
            generate_live_class_slides,
        )

        if request.method == "GET":
            iso = (request.args.get("date") or "")[:10]
            row = (
                school.get_live_session_for_class_date(class_id, iso)
                if iso
                else school.get_active_live_session_for_class(class_id)
            )
            if row is None:
                return jsonify(
                    {
                        "ok": True,
                        "presentation_id": None,
                        "presentation_url": None,
                        "has_presentation": False,
                    }
                )
            pres_id = row.get("presentation_id")
            return jsonify(
                {
                    "ok": True,
                    "presentation_id": pres_id,
                    "presentation_url": row.get("presentation_url"),
                    "has_presentation": bool(pres_id),
                    "meeting_date": row.get("meeting_date"),
                    "slides_json": row.get("slides_json"),
                    "live_session_id": row.get("id"),
                }
            )
        body = request.get_json(silent=True) or {}
        iso = str(body.get("meeting_date") or "")[:10]
        if not iso:
            return jsonify({"ok": False, "error": "meeting_date is required"}), 400
        if not is_slides_operator_email(user.get("email")):
            return jsonify(
                {
                    "ok": False,
                    "skipped": True,
                    "error": "Slides connect is limited to ALC admin accounts.",
                }
            ), 403
        try:
            result = generate_live_class_slides(
                school,
                class_id=class_id,
                meeting_date=date.fromisoformat(iso),
                user=user,
                force_regenerate=bool(body.get("force_regenerate")),
            )
            return jsonify(result)
        except SlidesConnectRequired:
            return jsonify(
                {
                    "ok": False,
                    "needs_slides_connect": True,
                    "connect_url": url_for(
                        "auth_google_slides",
                        next=url_for("staff_course", class_id=class_id, tab="lesson-slides"),
                    ),
                }
            ), 409
        except SlidesOperatorDenied as exc:
            return jsonify({"ok": False, "error": str(exc)}), 403
        except Exception as exc:  # noqa: BLE001
            return _json_error(exc)

    @bp.route("/api/classes/<int:class_id>/lesson-slides", methods=["GET", "POST"])
    @staff_required
    def api_class_lesson_slides(class_id: int):
        """Preview connected async Lessons, or copy/fill the theme template."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id) and user["role"] != "it":
            abort(403)
        from live_class_constants import is_slides_operator_email
        from live_class_slides import SlidesConnectRequired, SlidesOperatorDenied
        from slide_builder import generate_lesson_slides, preview_live_class

        cls = school.enrich_class(school.game.get_class(class_id))
        offering_id = cls.get("offering_id")
        if not offering_id:
            return jsonify({"ok": False, "error": "Class has no offering"}), 400
        offering = school.ensure_offering_instance(school.get_offering(int(offering_id)))
        if request.method == "GET":
            module_n = int(request.args.get("module") or 1)
            live_i = int(request.args.get("live_index") or 1)
            preview = preview_live_class(
                school,
                offering=offering,
                module_number=module_n,
                live_index=live_i,
                lives_per_module=int(request.args.get("lives_per_module") or 4),
                weeks_per_module=int(request.args.get("weeks_per_module") or 2),
                keyword=str(request.args.get("keyword") or "Lesson"),
            )
            deck = school.get_lesson_slide_deck(class_id, module_n, live_i)
            saved_decks = [
                {
                    "module_number": row.get("module_number"),
                    "live_index": row.get("live_index"),
                    "presentation_id": row.get("presentation_id"),
                    "presentation_url": row.get("presentation_url"),
                    "updated_at": row.get("updated_at"),
                }
                for row in school.list_lesson_slide_decks(class_id)
                if row.get("presentation_url")
            ]
            return jsonify(
                {
                    "ok": True,
                    "preview": preview,
                    "presentation_id": (deck or {}).get("presentation_id"),
                    "presentation_url": (deck or {}).get("presentation_url"),
                    "has_presentation": bool((deck or {}).get("presentation_id")),
                    "saved_decks": saved_decks,
                }
            )
        if not is_slides_operator_email(user.get("email")):
            return jsonify(
                {
                    "ok": False,
                    "skipped": True,
                    "error": "Slides connect is limited to ALC admin accounts.",
                }
            ), 403
        body = request.get_json(silent=True) or {}
        try:
            result = generate_lesson_slides(
                school,
                class_id=class_id,
                user=user,
                module_number=int(body.get("module") or 1),
                live_index=int(body.get("live_index") or 1),
                lives_per_module=int(body.get("lives_per_module") or 4),
                weeks_per_module=int(body.get("weeks_per_module") or 2),
                keyword=str(body.get("keyword") or "Lesson"),
                context=str(body.get("context") or ""),
                question=str(body.get("question") or ""),
                speaker_notes=str(body.get("speaker_notes") or ""),
                force_regenerate=bool(body.get("force_regenerate")),
            )
            return jsonify(result)
        except SlidesConnectRequired:
            return jsonify(
                {
                    "ok": False,
                    "needs_slides_connect": True,
                    "connect_url": url_for(
                        "auth_google_slides",
                        next=url_for("staff_course", class_id=class_id, tab="lesson-slides"),
                    ),
                }
            ), 409
        except SlidesOperatorDenied as exc:
            return jsonify({"ok": False, "error": str(exc)}), 403
        except Exception as exc:  # noqa: BLE001
            return _json_error(exc)

    @bp.route("/api/classes/<int:class_id>/live-lessons", methods=["GET", "POST"])
    @staff_required
    def api_class_live_lessons(class_id: int):
        """List saved live-lesson files, or Save As a new module/class template."""

        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id) and user["role"] != "it":
            abort(403)
        try:
            from live_class_metadata import (
                list_live_lesson_summaries,
                load_live_class_metadata,
                parse_live_lesson_code,
                write_live_lesson_playlist,
            )
        except ImportError:
            from lms.live_class_metadata import (
                list_live_lesson_summaries,
                load_live_class_metadata,
                parse_live_lesson_code,
                write_live_lesson_playlist,
            )
        cls = school.enrich_class(school.game.get_class(class_id))
        offering = (
            school.get_offering(int(cls["offering_id"])) if cls.get("offering_id") else None
        )
        course = str(
            (offering or {}).get("ontario_code") or cls.get("ontario_code") or "MCF3M"
        ).upper()

        def merged_lesson(module: str, slot: str) -> dict | None:
            """Return class-merged metadata so inventory counts follow edits.

            Args:
                module: Module token such as ``M1``.
                slot: Live slot such as ``C2``.
            """
            try:
                return school.live_class_metadata_for_class_lesson(
                    class_id, module, slot, fresh=True
                )
            except KeyError:
                return None

        if request.method == "GET":
            response = jsonify(
                {
                    "ok": True,
                    "course": course,
                    "lessons": list_live_lesson_summaries(
                        course, metadata_for=merged_lesson
                    ),
                }
            )
            response.headers["Cache-Control"] = "no-store"
            return response
        body = request.get_json(silent=True) or {}
        parsed = parse_live_lesson_code(body.get("code") or body.get("as") or "")
        if parsed:
            module, slot = parsed
        else:
            module = str(body.get("module") or "").strip().upper()
            slot = str(body.get("live_class") or body.get("slot") or "").strip().upper()
        if not parsed:
            parsed = parse_live_lesson_code(f"{module}{slot}")
        if not parsed:
            return jsonify({"ok": False, "error": "Use a code like M2C3."}), 400
        module, slot = parsed
        source_module = str(body.get("source_module") or module).strip().upper()
        source_slot = str(body.get("source_slot") or slot).strip().upper()
        meta = load_live_class_metadata(course, source_module, source_slot)
        path = write_live_lesson_playlist(course, module, slot, meta)
        return jsonify(
            {
                "ok": True,
                "course": course,
                "module": module,
                "live_class": slot,
                "path": str(path),
                "lessons": list_live_lesson_summaries(
                    course, metadata_for=merged_lesson
                ),
            }
        )

    @bp.route("/api/classes/<int:class_id>/live-lessons/<module>/<slot>", methods=["GET"])
    @staff_required
    def api_class_live_lesson_detail(class_id: int, module: str, slot: str):
        """Return one saved live-lesson playlist for Set Class load."""

        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id) and user["role"] != "it":
            abort(403)
        try:
            from live_class_metadata import load_live_class_metadata, parse_live_lesson_code
        except ImportError:
            from lms.live_class_metadata import (
                load_live_class_metadata,
                parse_live_lesson_code,
            )
        parsed = parse_live_lesson_code(f"{module}{slot}")
        if not parsed:
            return jsonify({"ok": False, "error": "Unknown live lesson."}), 404
        module_code, slot_code = parsed
        cls = school.enrich_class(school.game.get_class(class_id))
        offering = (
            school.get_offering(int(cls["offering_id"])) if cls.get("offering_id") else None
        )
        course = str(
            (offering or {}).get("ontario_code") or cls.get("ontario_code") or "MCF3M"
        ).upper()
        response = jsonify(
            {
                "ok": True,
                "course": course,
                "module": module_code,
                "live_class": slot_code,
                "deck_revision": school.class_deck_revision(
                    int(class_id), module_code, slot_code
                ),
                "live_metadata": school.live_class_metadata_for_class_lesson(
                    int(class_id), module_code, slot_code, fresh=True
                ),
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @bp.route("/staff/offerings/<int:offering_id>/slides/<date_iso>.html")
    @staff_required
    def staff_mock_slides(offering_id: int, date_iso: str):
        """Serve a localhost HTML deck preview written under the offering instance."""
        user = current_user()
        assert user is not None
        offering = school.get_offering(offering_id)
        if int(offering["teacher_user_id"]) != int(user["id"]) and user["role"] != "it":
            abort(403)
        offering = school.ensure_offering_instance(offering)
        rel = offering.get("instance_relpath") or f"instances/{offering_id}"
        dest = Path(school.data_dir) / str(rel) / "slides"
        filename = f"{date_iso}.html"
        if not (dest / filename).is_file():
            abort(404)
        return send_from_directory(dest, filename)

    @bp.route("/staff/offerings/<int:offering_id>/lesson-slides/<lesson_key>.html")
    @staff_required
    def staff_mock_lesson_slides(offering_id: int, lesson_key: str):
        """Serve the Lesson Slides HTML mock written under the offering instance."""
        user = current_user()
        assert user is not None
        offering = school.get_offering(offering_id)
        if int(offering["teacher_user_id"]) != int(user["id"]) and user["role"] != "it":
            abort(403)
        offering = school.ensure_offering_instance(offering)
        rel = offering.get("instance_relpath") or f"instances/{offering_id}"
        dest = Path(school.data_dir) / str(rel) / "slides"
        filename = f"{lesson_key}.html"
        if not (dest / filename).is_file():
            abort(404)
        return send_from_directory(dest, filename)

    def _owned_live_session(session_id: int):
        """Return a live session the current teacher owns, or an error response.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        user = current_user()
        assert user is not None
        row = school.get_live_session(session_id)
        if row is None:
            return None, (jsonify({"ok": False, "error": "Session not found"}), 404)
        owns = school.teacher_owns_class(int(user["id"]), int(row["class_id"]))
        if not owns and user["role"] != "it":
            return None, (jsonify({"ok": False, "error": "Ask Admin to grant access."}), 403)
        return row, None

    @bp.route("/api/live-sessions/<int:session_id>/observations", methods=["GET", "POST"])
    @staff_required
    def api_live_session_observations(session_id: int):
        """List or create qualitative process-evidence observations."""
        row, err = _owned_live_session(session_id)
        if err:
            return err
        user = current_user()
        assert user is not None
        if request.method == "GET":
            return jsonify(
                {"ok": True, "observations": school.list_observations(session_id)}
            )
        body = request.get_json(silent=True) or {}
        try:
            obs = school.create_observation(
                live_session_id=session_id,
                class_id=int(row["class_id"]),
                observer_user_id=int(user["id"]),
                scope=str(body.get("scope") or "student"),
                note=str(body.get("note") or ""),
                student_ids=body.get("student_ids") or [],
                team_id=body.get("team_id"),
                process_keys=body.get("process_keys") or [],
                evidence_strength=body.get("evidence_strength"),
                follow_up_required=bool(body.get("follow_up_required")),
                source=str(body.get("source") or "manual"),
                quick_phrase_id=body.get("quick_phrase_id"),
            )
            return jsonify({"ok": True, "observation": obs})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @bp.route(
        "/api/live-sessions/<int:session_id>/observations/<int:observation_id>",
        methods=["PATCH", "DELETE"],
    )
    @staff_required
    def api_live_session_observation_one(session_id: int, observation_id: int):
        """Edit or delete one observation on a session the teacher owns."""
        _row, err = _owned_live_session(session_id)
        if err:
            return err
        obs = school.get_observation(observation_id)
        if obs is None or int(obs["live_session_id"]) != int(session_id):
            return jsonify({"ok": False, "error": "Not found"}), 404
        if request.method == "DELETE":
            school.delete_observation(observation_id)
            return jsonify({"ok": True})
        try:
            updated = school.update_observation(
                observation_id, request.get_json(silent=True) or {}
            )
            return jsonify({"ok": True, "observation": updated})
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @bp.route("/api/live-sessions/<int:session_id>/observations/coverage")
    @staff_required
    def api_live_session_observation_coverage(session_id: int):
        """Student × process coverage counts for after-class review."""
        _row, err = _owned_live_session(session_id)
        if err:
            return err
        return jsonify({"ok": True, **school.observation_coverage(session_id)})

    @bp.route("/api/classes/<int:class_id>/game/meeting", methods=["POST"])
    @login_required
    def api_meeting(class_id: int):
        """Set the live-game meeting date."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            chosen = _optional_date(body.get("meeting_date"))
            if chosen is None:
                raise ValueError("meeting_date is required (YYYY-MM-DD)")
            time_label = body.get("time")
            return school.game.set_meeting_date(
                class_id,
                chosen,
                time_label=str(time_label) if time_label else None,
            )

        return _staff_post(class_id, run)

    def _apply_validated_meeting(class_id: int, body: dict[str, Any]) -> None:
        """Optionally set meeting date, then enforce log-day rules.

        Args:
            class_id: Class primary key.
            body: JSON with optional ``meeting_date`` / ``time``.
        """
        chosen = _optional_date(body.get("meeting_date"))
        if chosen is not None:
            time_label = body.get("time")
            school.game.set_meeting_date(
                class_id,
                chosen,
                time_label=str(time_label) if time_label else None,
            )
        _validate_log_date(class_id, _meeting_from_state(class_id) or chosen)

    @bp.route("/api/classes/<int:class_id>/game/cancel", methods=["POST"])
    @login_required
    def api_cancel(class_id: int):
        """Quit setup / live session without completing scoring."""

        def run(body):
            """Cancel MGS setup; end live-class session unless preserved."""
            result = school.game.cancel_setup(class_id)
            preserve = bool((body or {}).get("preserve_live_session"))
            if preserve:
                ended: list[dict[str, Any]] = []
            else:
                ended = school.end_active_live_sessions_for_class(class_id)
            if isinstance(result, dict):
                result = {**result, "live_sessions_ended": [row["id"] for row in ended]}
            return result

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/game/attendance", methods=["POST"])
    @login_required
    def api_attendance(class_id: int):
        """Save who is present and advance to teams (Begin Class Tracking)."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            _apply_validated_meeting(class_id, body)
            ids = [int(x) for x in (body.get("present_ids") or [])]
            return school.game.save_attendance(class_id, ids)

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/game/finalize-attendance", methods=["POST"])
    @login_required
    def api_finalize_attendance(class_id: int):
        """Save attendance into the week grid and close without teams/live."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            _apply_validated_meeting(class_id, body)
            ids = [int(x) for x in (body.get("present_ids") or [])]
            return school.game.finalize_attendance_only(class_id, ids)

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/game/ungamified", methods=["POST"])
    @login_required
    def api_ungamified(class_id: int):
        """Prepare or start individual Class-team tracking (no scoreboard).

        Body may include ``go_live`` (default True). When False, parks on
        rounds setup so Start Round can begin an Open Question.
        """

        def run(body):
            """Apply one staff JSON mutation for this class."""
            _apply_validated_meeting(class_id, body)
            ids = body.get("present_ids")
            state = school.game.game_state(class_id)
            status = str((state.get("game") or {}).get("status") or "")
            if ids is not None and status == "attendance":
                school.game.save_attendance(class_id, [int(x) for x in ids])
            go_live_raw = body.get("go_live")
            if go_live_raw is None:
                go_live = True
            elif isinstance(go_live_raw, str):
                go_live = go_live_raw.strip().lower() in {"1", "true", "yes", "on"}
            else:
                go_live = bool(go_live_raw)
            return school.game.start_ungamified_live(class_id, go_live=go_live)

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/game/step", methods=["POST"])
    @login_required
    def api_step(class_id: int):
        """Advance setup status."""
        return _staff_post(
            class_id,
            lambda body: school.game.set_setup_step(class_id, str(body.get("status") or "")),
        )

    @bp.route("/api/classes/<int:class_id>/game/assign", methods=["POST"])
    @login_required
    def api_assign(class_id: int):
        """Assign teams. JOIN keeps waiting-room Minds-On; TEAMS stage commit clears it."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            raw_assignments = body.get("assignments")
            if raw_assignments is not None and not isinstance(raw_assignments, list):
                raise ValueError("assignments must be a list")
            live = school.get_active_live_session_for_class(class_id)
            if live is not None:
                raw_present = body.get("present_ids")
                if raw_present is None:
                    raw_present = school.game.game_state(class_id).get(
                        "present_ids"
                    ) or []
                setup = school.setup_live_session_groups(
                    int(live["id"]),
                    n_teams=int(body.get("n_teams") or 0),
                    mode=str(body.get("mode") or ""),
                    present_ids=[int(value) for value in raw_present],
                    assignments=raw_assignments,
                    scoreboard_visible=body.get("scoreboard_visible"),
                )
                return setup
            return school.game.assign_teams(
                class_id,
                int(body.get("n_teams") or 0),
                str(body.get("mode") or ""),
                assignments=raw_assignments,
            )

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/game/rename", methods=["POST"])
    @login_required
    def api_rename(class_id: int):
        """Rename teams (optionally stay on rounds setup)."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            names = body.get("teams") or []
            if not isinstance(names, list):
                raise ValueError("teams must be a list")
            go_live = body.get("go_live")
            if go_live is None:
                go_live = True
            rounds = body.get("rounds")
            return school.game.rename_teams(
                class_id,
                names,
                go_live=bool(go_live),
                rounds=rounds if isinstance(rounds, list) else None,
            )

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/game/start-rounds", methods=["POST"])
    @login_required
    def api_start_rounds(class_id: int):
        """Save the first round(s) and open live team scoring."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            rounds = body.get("rounds")
            if rounds is not None and not isinstance(rounds, list):
                raise ValueError("rounds must be a list")
            state = school.game.start_live_with_rounds(class_id, rounds)
            school.clear_session_warmups_for_class(class_id)
            return state

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/game/append-round", methods=["POST"])
    @login_required
    def api_append_round(class_id: int):
        """Append one sequential round to the live plan and start it."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            if not isinstance(body, dict):
                raise ValueError("round body must be an object")
            spec = body.get("round") if isinstance(body.get("round"), dict) else body
            if not isinstance(spec, dict):
                raise ValueError("round must be an object")
            state = school.game.append_and_start_round(class_id, spec)
            school.clear_session_warmups_for_class(class_id)
            return state

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/game/meet-teams", methods=["POST"])
    @login_required
    def api_meet_teams(class_id: int):
        """Start Meet overlay timer and mount the QH chain at A."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            minutes = body.get("minutes", 3)
            try:
                minutes_i = int(minutes)
            except (TypeError, ValueError) as exc:
                raise ValueError("minutes must be an integer") from exc
            state = school.game.start_meet_teams(class_id, minutes_i)
            school.activate_meet_team_warmup_for_class(class_id)
            return state

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/game/timer/start", methods=["POST"])
    @login_required
    def api_timer_start(class_id: int):
        """Start the stage-independent session countdown."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            minutes = body.get("minutes", 3)
            try:
                minutes_i = int(minutes)
            except (TypeError, ValueError) as exc:
                raise ValueError("minutes must be an integer") from exc
            return school.game.start_session_timer(class_id, minutes_i)

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/game/timer/pause", methods=["POST"])
    @login_required
    def api_timer_pause(class_id: int):
        """Pause the active Meet / round countdown."""

        def run(_body):
            """Apply one staff JSON mutation for this class."""
            return school.game.pause_round_timer(class_id)

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/game/timer/resume", methods=["POST"])
    @login_required
    def api_timer_resume(class_id: int):
        """Resume a paused Meet / round countdown."""

        def run(_body):
            """Apply one staff JSON mutation for this class."""
            return school.game.resume_round_timer(class_id)

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/game/timer/remaining", methods=["POST"])
    @login_required
    def api_timer_remaining(class_id: int):
        """Set remaining session/Meet countdown to N integer minutes while paused."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            minutes = body.get("minutes")
            try:
                minutes_i = int(minutes)
            except (TypeError, ValueError) as exc:
                raise ValueError("minutes must be an integer") from exc
            return school.game.set_paused_timer_minutes(class_id, minutes_i)

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/game/anticipation", methods=["POST"])
    @login_required
    def api_anticipation(class_id: int):
        """Signal the live overlay to run the pre-rounds anticipation sequence."""

        def run(_body):
            """Apply one staff JSON mutation for this class."""
            return school.game.start_anticipation(class_id)

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/game/round", methods=["POST"])
    @login_required
    def api_round(class_id: int):
        """Start a scoring round."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            state = school.game.start_round(class_id, int(body.get("round") or 0))
            school.clear_session_warmups_for_class(class_id)
            return state

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/game/score", methods=["POST"])
    @login_required
    def api_score(class_id: int):
        """Award points — staff only."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            result = school.game.award_points(
                class_id,
                kind=str(body.get("kind") or ""),
                target_id=int(body.get("id") or 0),
                amount=int(body.get("amount") or 0),
                team_rule=(str(body["team_rule"]) if body.get("team_rule") else None),
                label=(str(body["label"]) if body.get("label") else None),
            )
            lookfor_id = str(body.get("lookfor_id") or "").strip()
            if lookfor_id:
                try:
                    from portfolio.lookfors import (
                        LOOKFOR_IDS,
                        observation_note_for_lookfor,
                        process_keys_for_lookfor,
                    )

                    if lookfor_id in LOOKFOR_IDS:
                        live = school.get_active_live_session_for_class(class_id)
                        if live is None:
                            live = school.get_latest_live_session_for_class(class_id)
                        observer = current_user()
                        if live is not None and observer is not None:
                            kind = str(body.get("kind") or "")
                            target_id = int(body.get("id") or 0)
                            student_ids: list[int] = []
                            team_id = None
                            scope = "student"
                            if kind == "student":
                                student_ids = [target_id]
                            elif kind == "team":
                                team_id = target_id
                                student_ids = school.team_student_ids(team_id)
                                if len(student_ids) >= 2:
                                    scope = "students"
                                elif len(student_ids) == 1:
                                    scope = "student"
                                else:
                                    scope = "team"
                            school.create_observation(
                                live_session_id=int(live["id"]),
                                class_id=class_id,
                                observer_user_id=int(observer["id"]),
                                scope=scope,
                                note=observation_note_for_lookfor(
                                    lookfor_id, str(body.get("label") or "")
                                ),
                                student_ids=student_ids or None,
                                team_id=team_id,
                                process_keys=process_keys_for_lookfor(lookfor_id),
                                source="lookfor",
                                lookfor_key=lookfor_id,
                            )
                except Exception:  # noqa: BLE001 — points still stand
                    logger.exception(
                        "look-for observation failed class_id=%s lookfor_id=%s",
                        class_id,
                        lookfor_id,
                    )
            return result

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/game/add-student", methods=["POST"])
    @login_required
    def api_late(class_id: int):
        """Add a late student to a live game."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            _reject_basic_roster(class_id)
            return school.game.add_late_student(
                class_id,
                int(body.get("student_id") or 0),
                int(body.get("team_id") or 0),
            )

        return _staff_post(class_id, run)

    @bp.route("/api/classes/<int:class_id>/game/end", methods=["POST"])
    @login_required
    def api_end(class_id: int):
        """End the live game and save the live class unless it is preserved.

        Scoring-panel End Class is a real End: participation, catch-up,
        and the Lesson Slides row. IT force-end and ``/game/cancel`` do
        not use this route.
        """

        def run(body):
            """Apply End Game, then save the live class unless preserved."""
            chosen = _optional_date(body.get("meeting_date"))
            # MCK-171: a Team challenge never stepped to its points pays
            # before the game day closes.
            school.award_pending_rank_races_for_class(class_id)
            result = school.game.end_game(class_id, meeting_date=chosen)
            preserve = bool((body or {}).get("preserve_live_session"))
            if not preserve:
                ended = school.end_active_live_sessions_for_class(
                    class_id, record_participation=True
                )
                result["live_sessions_ended"] = [row["id"] for row in ended]
            else:
                result["live_sessions_ended"] = []
            return result

        return _staff_post(class_id, run)
    app.register_blueprint(bp)

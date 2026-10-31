#!/usr/bin/env python3
"""LLOVES LMS — Flask entry point.

Usage:
    python3 lms/app.py
    # http://127.0.0.1:8787
"""

from __future__ import annotations

import json
import logging
import os
import posixpath
import re
import shutil
import sqlite3
import sys
import threading
import time
from datetime import date, timedelta


def _format_last_taken(iso_day: str) -> str:
    """Format a logged attendance day for the teacher course card.

    Args:
        iso_day: ``YYYY-MM-DD`` school day.

    Returns:
        A short date such as ``Oct 19, 2026``, or ``""`` when blank.
    """
    raw = (iso_day or "").strip()
    if not raw:
        return ""
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        return ""
    return f"{day.strftime('%b')} {day.day}, {day.year}"
from html import escape as html_escape
from pathlib import Path
from typing import Any
from urllib.parse import unquote

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
MGS_DIR = REPO_ROOT / "tools" / "math-game-show"
if str(LMS_DIR) not in sys.path:
    sys.path.insert(0, str(LMS_DIR))
for path in (str(REPO_ROOT), str(MGS_DIR)):
    if path not in sys.path:
        sys.path.append(path)

from dotenv import load_dotenv

load_dotenv(LMS_DIR / ".env")
load_dotenv(REPO_ROOT / ".env")

import email_service  # noqa: E402

from flask import (  # noqa: E402
    Flask,
    Response,
    abort,
    current_app,
    g,
    has_request_context,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402

from auth import (  # noqa: E402
    _safe_next_url,
    current_user,
    google_oauth_ready,
    it_emails,
    it_required,
    join_limit_ip_key,
    landing_kwargs,
    login_required,
    register_auth_routes,
    request_client_ip,
    staff_or_student_scoreboard,
    staff_required,
    student_required,
)
from bots import list_bots  # noqa: E402
from celebration import (  # noqa: E402
    SECRET_KEY_DEV_DEFAULT as DEV_SECRET_KEY,
    WONDER_COPY as CELEBRATION_COPY,
    bind_celebration_secret,
    build_celebration_board,
    celebration_candidates,
    forget_featured_students,
    note_celebrations_unfrozen,
    public_celebration_board,
    reward_appearances,
    reward_title,
    set_featured_award,
)
from curriculum import seed_curriculum  # noqa: E402
from local_dev_seed import (  # noqa: E402
    local_dev_bind_host,
    local_dev_login_enabled,
    seed_local_dev_school,
)
from local_test_login import (  # noqa: E402
    cli_bind_host,
    cli_bind_port,
    enforce_local_test_login,
    register_local_test_login,
)
from onboarding_tour import tour_run_type  # noqa: E402
from school_db import (  # noqa: E402
    STAFF_2FA_MODE_LABELS,
    DeckCopyLiveConflict,
    DeleteTestLiveClassConflict,
    LiveRunKindConflict,
    DeckReplaceNotConfirmed,
    GroupAnswerLocked,
    RankAgreeConflict,
    RankRaceStepConflict,
    RankTurnConflict,
    SchoolDB,
    WHITEBOARD_REOPEN_CLASS_ENDED,
    WHITEBOARD_REOPEN_ENDED_REASON,
    WhiteboardReopenConflict,
    json_safe,
)
from celebration import (  # noqa: E402
    START_FRESH_FAILED,
    STAFF_COPY as CELEBRATION_STAFF_COPY,
    AwardTallyBusy,
    AwardTallyShared,
    staff_award_periods,
    start_fresh_award_tally,
)
from serve_capacity import (  # noqa: E402
    PollBudgetExceeded,
    is_live_state_poll,
    maybe_arm_worker_warmup,
    run_once_per_boot,
    missing_live_session,
    poll_slot,
    worker_warmup_active,
)
from live_news_wire import (  # noqa: E402
    active_media_event,
    emit_answer_landed,
    emit_session_news,
    events_for_teacher_patch,
    flag_work_event,
    install_stream_shutdown_hook,
    live_news_response,
    prompt_event,
    prompt_response_count,
    record_push_stages,
    response_landed_events,
    teacher_state_seq,
)
from sentry_wire import (  # noqa: E402
    SENTRY_CONNECT_SRC,
    browser_context as sentry_browser_tags,
    init_flask_sentry,
    install_request_scope as install_sentry_request_scope,
    note_busy_shed,
    report_swallowed,
    sentry_browser_context,
)
from artifact import (  # noqa: E402
    C2_TRANSFORM_MEDIA_URL,
    TRANSFORMATIONS_ARTIFACT_ID,
    is_artifact_payload,
    live_media_catalog,
)
from live_media import (  # noqa: E402
    DEFAULT_LIVE_MEDIA_STEM,
    DEFAULT_LIVE_MEDIA_TITLE,
    DEFAULT_LIVE_MEDIA_URL,
    cons_catalog,
    live_media_url_swap_allowed,
)
from live_class_packs import live_class_registry  # noqa: E402
from live_results_csv import live_results_csv  # noqa: E402
from live_teacher_state import LAYOUT_PRESETS, default_teacher_state  # noqa: E402
from meet_team import is_meet_team_payload  # noqa: E402
from live_prompt_feedback import public_feedback_fragment  # noqa: E402
from minds_on import is_minds_on_payload  # noqa: E402
from bank_dedupe import apply_visible_bank_question_counts  # noqa: E402
from bank_edit import (  # noqa: E402
    EMPTY_BANKS_MESSAGE,
    WONDER_COPY,
    apply_question_bank_bulk,
    apply_staff_question_patch,
    create_staff_bank_question,
    delete_staff_bank_question,
    list_staff_bank_questions,
)
from live_bank import create_live_bank_question  # noqa: E402
from question_math import format_mc_html_fragment  # noqa: E402
from components import (  # noqa: E402
    blob_file_path,
    ensure_ingested,
    get_assignment,
    get_module_item,
    get_page,
    get_question_bank,
    get_quiz,
    library_counts,
    library_file_path,
    library_is_ingested,
    library_pack_summary,
    list_assignments,
    list_pages,
    list_question_banks,
    list_questions,
    list_quizzes,
    outline_nav,
    outline_raw_modules,
    quiz_questions,
)
try:
    from pack_progress import (
        annotate_offering_pack,
        instances_payload,
        status_payload,
    )
except ImportError:
    from lms.pack_progress import (
        annotate_offering_pack,
        instances_payload,
        status_payload,
    )
from course_shell import (  # noqa: E402
    backfill_module_pack,
    offering_uses_shell_tabs,
)
from modules import (  # noqa: E402
    IMSCC_MAX_BYTES,
    ensure_unpacked,
    module_pack_root,
    pack_needs_resume,
    pack_ui_state,
    placeholder_html,
    read_pack_status,
    resolve_module_pack,
    rewrite_wiki_html,
    wrap_page,
)
from paths import (  # noqa: E402
    DATA_DIR,
    DEFAULT_DB_PATH,
    DEFAULT_IT_EMAIL,
    MGS_DIR as MGS_PATH,
    SCHOOL_NAME,
    SCHOOL_SHORT,
    public_brand,
)
from student_portal import (  # noqa: E402
    EARNED_AVATAR_SRC,
    EARNED_AVATARS,
    EARNED_CHARACTERS,
    EXIT_FEEDBACK_SESSION_KEY,
    HOW_WAS_CLASS,
    bind_pin_portal_session,
    bind_student_session,
    character_choices,
    clear_live_student_session_keys,
    clear_rejoin_cookie,
    clear_student_session_keys,
    earned_avatar_keys,
    mood_choices,
    next_student_endpoint,
    rejoin_token_from_cookie,
    pop_dashboard_focus_catchup,
    resolve_pin_portal_context,
    set_dashboard_focus_catchup,
    resolve_student_live_context,
    set_rejoin_cookie,
    student_url_with_token,
    explicit_visit_token_from_request,
    visit_token_from_request,
)
import syllabus as syllabus_mod  # noqa: E402
from live_class_run import end_time_options  # noqa: E402
from schedule import TIME_OPTIONS, format_live_schedule_line, wizard_defaults  # noqa: E402
import staff_invites  # noqa: E402
from onboarding_presets import clean_preset as onboarding_clean_preset  # noqa: E402
from onboarding_presets import preset_lists as onboarding_preset_lists  # noqa: E402
from show_test_students import show_test_students_requested  # noqa: E402

MGS_TEMPLATES = MGS_PATH / "templates"
MGS_STATIC = MGS_PATH / "static"


def _optional_date(value: Any) -> date | None:
    """Parse YYYY-MM-DD from a JSON field, or None if blank."""
    if value is None or value == "":
        return None
    return date.fromisoformat(str(value)[:10])


def _include_test_students() -> bool:
    """True when this staff request asked to show test-student rows.

    The query value wins, including an explicit off, so an export link
    matches the page. Otherwise the staff cookie is used. Missing both
    means hidden, which keeps attendance and participation totals on
    real students.
    """
    return show_test_students_requested(request.args, request.cookies)


logger = logging.getLogger(__name__)

# One warning per missing session. A full traceback on every student poll
# starved the shared Fly CPU after a Meet row disappeared.
_gone_live_sessions_logged: set[int] = set()


def _poll_shed_response():
    """JSON retry when every heavy ``/state`` slot is already taken.

    Does not touch sqlite or presence. The thread returns immediately
    so ``/health`` can still be served.

    Returns:
        HTTP 200 ``{retry: true}``.
    """
    return jsonify(
        {
            "ok": True,
            "error": "state unavailable",
            "status": "waiting",
            "retry": True,
            "fault": "Live class is busy. Retry.",
        }
    )


def _guard_live_poll(view):
    """Shed a live poll when the in-flight cap is full.

    The cap is off under ``TESTING`` so unit fan-out still runs the
    builders. Production keeps threads free for ``/health``. Staff
    ``/state`` is registered outside ``create_app``, so this wrapper
    reads ``current_app`` at request time.

    Args:
        view: The Flask view to wrap.

    Returns:
        A view that admits or sheds before the original body.
    """

    def wrapped(*args, **kwargs):
        """Admit one poll or return the shed body."""
        testing = bool(current_app.config.get("TESTING"))
        with poll_slot(enabled=not testing) as admitted:
            if not admitted:
                return _poll_shed_response()
            return view(*args, **kwargs)

    wrapped.__name__ = view.__name__
    wrapped.__doc__ = view.__doc__
    return wrapped


def _note_live_session_gone(session_id: int) -> None:
    """Log once per process when polls hit a missing or closed Meet.

    Student browsers poll about once a second. Formatting a traceback on
    each of those requests pegged the machine. One warning is enough to
    see which session id went away.

    Args:
        session_id: ``live_class_sessions.id`` the client asked for.
    """
    sid = int(session_id)
    if sid in _gone_live_sessions_logged:
        return
    if len(_gone_live_sessions_logged) > 1024:
        # MCK-231: bounded; a repeat log line beats unbounded growth.
        _gone_live_sessions_logged.clear()
    _gone_live_sessions_logged.add(sid)
    logger.warning("live session %s is gone; polls return ended", sid)


from board_ops import BoardOpRejected, BoardSessionClosed, normalize_board_key  # noqa: E402


def _start_live_and_wake(
    school: SchoolDB, class_id: int, teacher_user_id: int, **kwargs: Any
) -> dict[str, Any]:
    """Start a live run, then wake tabs still on the last run's stream.

    Tabs left on End's celebration keep their stream to that session id
    and poll at the slow ended pace. A ``state_seq`` postcard on the old
    ids and the new one makes those tabs fetch the new class now.
    Resuming the teacher's open session sends nothing.

    Args:
        school: App SchoolDB.
        class_id: Game-show ``classes.id``.
        teacher_user_id: Staff user starting the run.
        **kwargs: Passed to ``start_live_class_session``.
    """
    open_row = school.get_active_live_session_for_teacher(int(teacher_user_id))
    celebrating = [
        int(row["id"])
        for row in school.list_live_sessions_for_class(int(class_id))
        if school.session_is_celebrating(int(row["id"]))
    ]
    session_row = school.start_live_class_session(
        class_id, teacher_user_id, **kwargs
    )
    new_id = int(session_row["id"])
    if open_row is not None and int(open_row["id"]) == new_id:
        return session_row
    seq = teacher_state_seq(school, new_id)
    for sid in sorted(set(celebrating) | {new_id}):
        emit_session_news(
            school, sid, [{"type": "state_seq", "state_seq": seq}]
        )
    return session_row


def _optional_board_seq(raw: Any) -> int | None:
    """Parse a since-seq cursor. Missing means the caller wants a full view.

    Args:
        raw: JSON or query value. ``0`` is a real cursor.

    Returns:
        Non-negative sequence, or ``None`` when the field was omitted.
    """
    if raw is None or raw == "":
        return None
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return None


_CODENAME_WORD_RE = re.compile(r"\b(Codenames?|codenames?|Names?|names?)\b")


def _rewrite_plain_name_words(text: str) -> str:
    """Replace Name/Codename words in one roster sentence.

    A word already sitting after "first" or "last" is left as written, so
    "First name and last name" does not grow another "first".

    Args:
        text: Message text that may still say Name or Codename.
    """

    def swap(match: re.Match[str]) -> str:
        """Replace one Name/Codename, keeping sentence-start capitals."""
        before = text[: match.start()]
        if re.search(r"(?i)\b(?:first|last)\s+$", before):
            return match.group(0)
        plural = match.group(1).lower().endswith("s")
        word = "first names" if plural else "first name"
        lead = before.rstrip()
        if not lead or lead[-1] in ".!?":
            return word[0].upper() + word[1:]
        return word

    return _CODENAME_WORD_RE.sub(swap, text)


def plain_name_wording(message: Any) -> str:
    """Say "first name(s)" instead of "Name(s)" in a roster message.

    Shawn's decision for plain courses (non-math, no pack): every roster
    validation message uses first-names wording, e.g. "Names cannot
    contain commas" becomes "First names cannot contain commas". The word
    is capitalised at the start of the message or a sentence, lower case
    elsewhere ("Duplicate first name: Sam"). Math courses say Name.
    A leftover Codename message is rewritten the same way.

    Words that already follow "first" or "last" stay put. The student's
    own name, the text after the colon in a Duplicate message, is never
    rewritten.

    Args:
        message: Validation message from the roster code.
    """
    text = str(message or "")
    duplicate = re.match(r"^(Duplicate\b[^:]*:\s*)(.*)$", text, re.DOTALL)
    if duplicate:
        return _rewrite_plain_name_words(duplicate.group(1)) + duplicate.group(2)
    return _rewrite_plain_name_words(text)


def simple_course(ontario_code: Any, library_id: Any) -> bool:
    """MCK-183 slice E: a course with no module pack that is not math.

    Ontario math codes start with "M" (MCF3M, MHF4U, MPM2D...), so Shawn's
    courses, with or without a pack, keep every tab and label. A
    pack-less non-math course (SBI4U, ENG2D...) shows the plain teacher
    view: Run Live Class and Attendance & Participation.
    """
    code = str(ontario_code or "").strip().upper()
    return bool(code) and not library_id and not code.startswith("M")


def class_is_simple(school: Any, class_id: int) -> bool:
    """Is this class's course a plain course (non-math, no pack)?

    Module level (#272 gate LOW-1) so every route group can use it.

    Args:
        school: ``SchoolDB``.
        class_id: Game-show ``classes.id``.
    """
    try:
        cls = school.game.get_class(int(class_id))
        offering = (
            school.get_offering(int(cls["offering_id"]))
            if cls and cls.get("offering_id")
            else None
        )
    except Exception:  # noqa: BLE001
        report_swallowed("class is simple")
        return False
    if not offering:
        return False
    return simple_course(offering.get("ontario_code"), offering.get("library_id"))


def _is_whats_new_source_path(path: str) -> bool:
    """True for any spelling of a path under ``/static/whats-new/``.

    MCK-182 LOW: ``/static/./whats-new/releases.json``, doubled slashes,
    ``..`` hops, backslashes and percent-encoding all resolve to the same
    file, so the path is decoded and normalised before the check.

    Args:
        path: Request path (already URL-decoded once by Werkzeug).
    """
    text = str(path or "")
    for _ in range(3):
        decoded = unquote(text)
        if decoded == text:
            break
        text = decoded
    text = text.replace("\\", "/")
    norm = posixpath.normpath("/" + text.lstrip("/")).lower()
    return norm == "/static/whats-new" or norm.startswith("/static/whats-new/")


def _json_error(exc: BaseException):
    """Map domain exceptions to JSON API errors."""
    if isinstance(exc, KeyError):
        return jsonify({"ok": False, "error": f"Not found: {exc.args[0]}"}), 404
    if isinstance(exc, FileNotFoundError):
        return jsonify({"ok": False, "error": str(exc)}), 404
    if isinstance(exc, ValueError):
        return jsonify({"ok": False, "error": str(exc)}), 400
    # KeyError, FileNotFoundError, and ValueError are the API contract.
    # Anything else is a fault the handler was hiding behind a JSON 500.
    report_swallowed("json api")
    return jsonify({"ok": False, "error": str(exc)}), 500


_BOARD_POOL_RETRY_AFTER = "2"


def _board_read_pool_busy(exc: BaseException):
    """Map a saturated presence pool to HTTP 503 for board delta reads.

    Args:
        exc: Error from ``read_board`` / ``board_delta``.

    Returns:
        ``(response, status)`` when ``exc`` is pool busy, else ``None``.
    """
    from live_presence import LivePresenceUnavailable, note_presence_blip

    if not isinstance(exc, LivePresenceUnavailable):
        return None
    if "pool busy" not in str(exc):
        return None
    note_presence_blip("board read pool busy")
    response = jsonify(
        {
            "ok": False,
            "error": "Board is busy — try again.",
            "retry": True,
        }
    )
    response.headers["Retry-After"] = _BOARD_POOL_RETRY_AFTER
    return response, 503


def _artifact_busy(action: str):
    """JSON retry when an Artifact open path fails under class load.

    Call from an ``except`` block so the traceback is logged. The body is
    JSON. Flask's default HTML 500 says the server is overloaded, which is
    what teacher and student browsers showed when Artifact open raised.

    Args:
        action: Short label such as ``mint`` or ``preview``.

    Returns:
        HTTP 503 ``{ok: false, retry: true}``.
    """
    # Full traceback stays in the process log. Sentry gets the sampled
    # counter below, not one issue per student during a busy open.
    logger.exception(
        "live artifact %s failed",
        action,
        extra={"lloves_sentry_skip": True},
    )
    note_busy_shed(action)
    return jsonify(
        {
            "ok": False,
            "error": "Artifact is busy — try again.",
            "retry": True,
        }
    ), 503


def _wants_json() -> bool:
    """True when the client asked for a JSON module-pack response."""
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    best = request.accept_mimetypes.best_match(("application/json", "text/html"))
    return best == "application/json"


def _library_source(school: SchoolDB, offering: dict[str, Any] | None) -> str | None:
    """Return the shared library IMSCC path for an offering, if any.

    Args:
        school: Open school database.
        offering: Offering row (may include ``library_id``).
    """
    if not offering:
        return None
    lib_id = offering.get("library_id")
    if lib_id:
        lib = school.get_library(int(lib_id))
        if lib and lib.get("source_path"):
            return str(lib["source_path"])
    stored = offering.get("imscc_path")
    return str(stored) if stored else None


def _class_pack(school: SchoolDB, cls: dict[str, Any]):
    """Resolve the IMSCC pack for a staff class (shared library, leftover fallback).

    Args:
        school: Open school database (provides ``data_dir``).
        cls: Enriched class dict.

    Returns:
        ``ModulePackPaths`` for this offering.
    """
    code = str(cls.get("ontario_code") or cls.get("course_code") or "")
    offering_id = cls.get("offering_id")
    offering = None
    if offering_id:
        offering = school.ensure_offering_instance(school.get_offering(int(offering_id)))
        cls["imscc_path"] = offering.get("imscc_path")
        cls["instance_relpath"] = offering.get("instance_relpath")
        cls["library_id"] = offering.get("library_id")
        cls["shell_library_id"] = offering.get("shell_library_id")
    return resolve_module_pack(
        code,
        cls.get("imscc_path"),
        data_dir=getattr(school, "data_dir", None),
        offering_id=int(offering_id) if offering_id else None,
        instance_relpath=cls.get("instance_relpath"),
        library_id=cls.get("library_id") or (offering.get("library_id") if offering else None),
        library_source=_library_source(school, offering),
    )


def _pack_dest_root(school: SchoolDB, offering: dict[str, Any]) -> Path:
    """Instance ``pack/`` folder used for staff IMSCC upload and status.

    Args:
        school: Open school database.
        offering: Offering row (migrated if needed).
    """
    offering = school.ensure_offering_instance(offering)
    return module_pack_root(
        school.data_dir,
        int(offering["id"]),
        instance_relpath=offering.get("instance_relpath"),
    )


def _discard_unpacked(unpacked: Path) -> None:
    """Delete an unpacked cartridge tree after its components are stored.

    Pages, assets, and outlines all live in the database and blob store once
    ingest succeeds, so the expanded tree is pure duplicate bytes on the Fly
    volume. The ``.imscc`` itself is kept as the archive of record.

    Args:
        unpacked: Unpacked cartridge directory to remove.
    """
    try:
        if Path(unpacked).is_dir():
            shutil.rmtree(unpacked, ignore_errors=True)
    except OSError:
        logger.warning("Could not remove unpacked tree %s", unpacked)


def _live_defaults_from_body(body: dict) -> dict | None:
    """Return live-default keys present on an Add New body, or None.

    Omitted keys stay out so a question added without touching Live defaults
    keeps today's launch behaviour.

    Args:
        body: JSON body from the add-question route.
    """
    try:
        from live_defaults import parse_live_defaults
    except ImportError:
        from lms.live_defaults import parse_live_defaults

    if not isinstance(body, dict):
        return None
    parsed = parse_live_defaults(body)
    return parsed or None


def _ready_library(school, cls: dict) -> tuple[int | None, str | None]:
    """Resolve the pack library, or the course shell when no pack is attached.

    A shared pack is unpacked and ingested once. A course with no pack
    uses ``shell_library_id``, which already stores the default banks.
    The shell is not a cartridge, so this does not ingest it and does not
    create a library when that pointer is empty.

    Args:
        school: Open school database.
        cls: Enriched class row.

    Returns:
        ``(library_id, error_message)``. ``library_id`` is None when the
        course has neither a shared pack nor a shell.
    """
    pack = _class_pack(school, cls)
    library_id = cls.get("library_id") or cls.get("shell_library_id")
    if not library_id:
        return None, "No course library yet."
    # A shell is already stored. Do not ingest a missing cartridge over it.
    if not cls.get("library_id"):
        return int(library_id), None
    try:
        if not library_is_ingested(school, int(library_id)):
            if pack.imscc:
                status = ensure_unpacked(pack.imscc, pack.unpacked)
                if not status.get("ok"):
                    return int(library_id), str(status.get("error") or "")
            summary = ensure_ingested(
                school, school.data_dir, int(library_id), pack.unpacked
            )
            if summary and library_is_ingested(school, int(library_id)):
                _discard_unpacked(pack.unpacked)
    except Exception as exc:  # noqa: BLE001 - surface import problems in the UI
        logger.exception("Component backfill failed for library %s", library_id)
        return int(library_id), f"Could not import module content: {exc}"
    return int(library_id), None


def _escape(value: object) -> str:
    """Escape a value for safe interpolation into component chrome."""
    return html_escape(str(value or ""))


def _render_page_component(
    page: dict, *, ontario_code: str, files_root: str, data_dir: Path
):
    """Render one stored page by kind (HTML, PDF, or Google share URL).

    Args:
        page: ``pages`` row.
        ontario_code: Course code used by the wiki-token rewriter.
        files_root: Prefix for ``web_resources`` asset URLs.
        data_dir: LMS data volume holding blobs.
    """
    title = page.get("title") or "Page"
    kind = page.get("kind")
    if kind == "html":
        raw = page.get("html_text") or ""
        return wrap_page(
            title, rewrite_wiki_html(raw, ontario_code, files_root=files_root)
        )
    if kind == "pdf":
        target = blob_file_path(data_dir, page.get("blob_sha") or "")
        if target is None:
            abort(404)
        return send_from_directory(
            target.parent, target.name, mimetype="application/pdf"
        )
    if kind in {"gdoc", "gslides"}:
        url = _escape(page.get("url"))
        label = "Google Slides" if kind == "gslides" else "Google Doc"
        return wrap_page(
            title,
            f'<h1>{_escape(title)}</h1>'
            f'<p><a href="{url}" target="_blank" rel="noopener">'
            f"Open in {label}</a></p>"
            f'<iframe src="{url}" title="{_escape(title)}" '
            'style="width:100%;height:70vh;border:1px solid #d0d7de;'
            'border-radius:8px"></iframe>',
        )
    return placeholder_html(title, str(kind or "page"))


QUESTION_TYPE_LABELS = {
    "multiple_choice_question": "Multiple choice",
    "multiple_answers_question": "Multiple answers",
    "true_false_question": "True / false",
    "essay_question": "Essay",
    "short_answer_question": "Short answer",
    "numerical_question": "Numerical",
    "matching_question": "Matching",
    "fill_in_multiple_blanks_question": "Fill in the blanks",
    "file_upload_question": "File upload",
    "text_only_question": "Text (no answer)",
}

QUESTION_STYLE = """
<style>
 .qlist { list-style: none; margin: 0; padding: 0; }
 .qcard { border: 1px solid #d0d7de; border-radius: 10px; padding: .85rem 1rem;
          margin: 0 0 1rem; background: #fff; }
 .qhead { display: flex; gap: .6rem; flex-wrap: wrap; align-items: baseline;
          font-size: .82rem; color: #57606a; margin-bottom: .5rem; }
 .qhead .qnum { font-weight: 700; color: #0f172a; font-size: .95rem; }
 .qtag { background: #eef2f6; border-radius: 999px; padding: .1rem .55rem; }
 .qstem { margin: .25rem 0 .6rem; }
 .qchoices { list-style: none; margin: 0; padding: 0; }
 .qchoices li { border: 1px solid #e4e8ee; border-radius: 8px;
                padding: .35rem .6rem; margin-bottom: .35rem; }
 .qchoices li.correct { border-color: #0f766e; background: #effaf7; }
 .qmark { color: #0f766e; font-weight: 700; margin-right: .4rem; }
 .qmark.blank { color: #b0b8c1; }
 .qblank { margin: .5rem 0; padding-left: .75rem;
           border-left: 3px solid #e4e8ee; }
 .qanswer { color: #0f766e; }
 .qnote { color: #57606a; font-size: .85rem; }
 .qdesc { border: 1px solid #e4e8ee; border-radius: 10px; padding: .5rem .9rem;
          background: #fafbfc; }
</style>
"""


def _question_type_label(item_type: str) -> str:
    """Human label for a Canvas question type.

    Args:
        item_type: Raw ``questions.item_type`` value.
    """
    key = str(item_type or "")
    return QUESTION_TYPE_LABELS.get(key, key.replace("_", " ").strip() or "Question")


def _render_choices(choices: list, *, code: str, files_root: str) -> str:
    """Render an answer-choice list, marking the correct option(s).

    Args:
        choices: Payload choices (``id``, ``html``, ``correct``).
        code: Ontario course code for the wiki-token rewriter.
        files_root: Prefix for ``web_resources`` asset URLs.
    """
    rows = []
    for choice in choices:
        correct = bool(choice.get("correct"))
        body = format_mc_html_fragment(
            rewrite_wiki_html(
                str(choice.get("html") or ""), code, files_root=files_root
            )
        )
        mark = (
            '<span class="qmark">&#10003;</span>'
            if correct
            else '<span class="qmark blank">&#9675;</span>'
        )
        klass = ' class="correct"' if correct else ""
        rows.append(f"<li{klass}>{mark}{body or '<em>(blank choice)</em>'}</li>")
    return f'<ul class="qchoices">{"".join(rows)}</ul>' if rows else ""


def _render_question(
    index: int, question: dict, *, code: str, files_root: str
) -> str:
    """Render one imported question as read-only HTML.

    Shows the number, type, points, stem, and whatever answer detail the
    cartridge provided. Nothing here is interactive: LLOVES does not yet let
    students answer or grade quizzes.

    Args:
        index: 1-based display position.
        question: Row from :func:`components.list_questions`.
        code: Ontario course code for the wiki-token rewriter.
        files_root: Prefix for ``web_resources`` asset URLs.
    """
    payload = question.get("payload") or {}
    tags = [f'<span class="qtag">{_escape(_question_type_label(question.get("item_type")))}</span>']
    points = payload.get("points_possible")
    if isinstance(points, (int, float)):
        tags.append(f'<span class="qtag">{points:g} pt</span>')
    bank_title = payload.get("bank_title")
    if bank_title:
        tags.append(f'<span class="qtag">from {_escape(bank_title)}</span>')

    stem = format_mc_html_fragment(
        rewrite_wiki_html(
            str(payload.get("stem_html") or ""), code, files_root=files_root
        )
    )
    body = [f'<div class="qstem">{stem or "<em>No stem in the import.</em>"}</div>']

    choices = payload.get("choices") or []
    if choices:
        body.append(_render_choices(choices, code=code, files_root=files_root))
    answers = [str(a) for a in (payload.get("correct_answers") or []) if str(a)]
    if answers:
        joined = ", ".join(_escape(a) for a in answers)
        body.append(f'<p class="qanswer"><strong>Accepted:</strong> {joined}</p>')
    for blank in payload.get("blanks") or []:
        label = rewrite_wiki_html(
            str(blank.get("label_html") or ""), code, files_root=files_root
        )
        part = [f'<div class="qblank"><p><strong>{label or "Blank"}</strong></p>']
        if blank.get("choices"):
            part.append(
                _render_choices(blank["choices"], code=code, files_root=files_root)
            )
        blank_answers = [str(a) for a in (blank.get("correct_answers") or []) if str(a)]
        if blank_answers:
            joined = ", ".join(_escape(a) for a in blank_answers)
            part.append(f'<p class="qanswer"><strong>Accepted:</strong> {joined}</p>')
        part.append("</div>")
        body.append("".join(part))
    if not choices and not answers and not payload.get("blanks"):
        body.append(
            '<p class="qnote">No answer key in the cartridge for this type.</p>'
        )

    return (
        '<li class="qcard">'
        f'<div class="qhead"><span class="qnum">Question {index}</span>'
        f'{"".join(tags)}</div>'
        f'{"".join(body)}</li>'
    )


def _render_questions(questions: list, *, code: str, files_root: str) -> str:
    """Render a numbered list of imported questions.

    Args:
        questions: Rows from :func:`components.list_questions`.
        code: Ontario course code for the wiki-token rewriter.
        files_root: Prefix for ``web_resources`` asset URLs.
    """
    if not questions:
        return (
            '<p class="qnote">No questions came through in this import. The '
            "cartridge may hold the quiz shell only.</p>"
        )
    rows = "".join(
        _render_question(index, question, code=code, files_root=files_root)
        for index, question in enumerate(questions, start=1)
    )
    return f'<ol class="qlist">{rows}</ol>'


def _render_quiz(
    school,
    cls: dict,
    library_id: int,
    quiz_id: int,
    *,
    title: str,
    files_root: str,
):
    """Render a quiz as its settings summary plus its imported questions.

    Args:
        school: Open school database.
        cls: Enriched class row.
        library_id: Shared ``content_libraries.id``.
        quiz_id: ``quizzes.id``.
        title: Display title fallback.
        files_root: Prefix for ``web_resources`` asset URLs.
    """
    row = get_quiz(school, int(library_id), int(quiz_id))
    if row is None:
        abort(404)
    code = str(cls.get("ontario_code") or cls.get("course_code") or "")
    try:
        settings = json.loads(row.get("settings_json") or "{}")
    except json.JSONDecodeError:
        settings = {}
    description = settings.pop("description_html", "")
    groups = settings.pop("question_groups", [])

    bits = "".join(
        f"<li><strong>{_escape(key.replace('_', ' '))}:</strong> "
        f"{_escape(value)}</li>"
        for key, value in settings.items()
    )
    notes = "".join(
        "<li>Canvas draws "
        f"{_escape(group.get('pick') or 'some')} question(s) at random from "
        f"<em>{_escape(group.get('title') or 'a bank')}</em> "
        f"({int(group.get('available') or 0)} in the pool)</li>"
        for group in groups
    )
    if notes:
        notes = f"<p><strong>Randomized groups</strong></p><ul>{notes}</ul>"
    questions = quiz_questions(school, int(library_id), str(row.get("import_key") or ""))
    described = (
        f'<div class="qdesc">'
        f"{rewrite_wiki_html(description, code, files_root=files_root)}</div>"
        if description
        else ""
    )
    return wrap_page(
        title,
        QUESTION_STYLE
        + f"<h1>{_escape(row.get('title') or title)}</h1>"
        f"<p>{len(questions)} imported question(s).</p>"
        f"<ul>{bits}</ul>{notes}{described}"
        '<p class="qnote">Read-only preview: students do not answer quizzes in '
        "LLOVES yet, and correct answers are marked for staff.</p>"
        "<h2>Questions</h2>"
        + _render_questions(questions, code=code, files_root=files_root),
    )


def _render_bank(
    school,
    cls: dict,
    library_id: int,
    bank_id: int,
    *,
    files_root: str,
):
    """Render one question bank's imported questions.

    Args:
        school: Open school database.
        cls: Enriched class row.
        library_id: Shared ``content_libraries.id``.
        bank_id: ``question_banks.id``.
        files_root: Prefix for ``web_resources`` asset URLs.
    """
    bank = get_question_bank(school, int(library_id), int(bank_id))
    if bank is None:
        abort(404)
    code = str(cls.get("ontario_code") or cls.get("course_code") or "")
    views = list_staff_bank_questions(
        school,
        int(library_id),
        int(bank_id),
        class_id=int(cls.get("id") or 0) or None,
    )
    questions = [
        {
            "id": view["id"],
            "item_type": view.get("item_type"),
            "payload": {
                "stem_html": view.get("stem_html") or "",
                "points_possible": view.get("points"),
                "choices": view.get("choices") or [],
                "correct_answers": view.get("correct_answers") or [],
            },
        }
        for view in views
    ]
    title = str(bank.get("title") or "Question bank")
    return wrap_page(
        title,
        QUESTION_STYLE
        + f"<h1>{_escape(title)}</h1>"
        f"<p>{len(questions)} imported question(s).</p>"
        + _render_questions(questions, code=code, files_root=files_root),
    )


def _render_component(
    school,
    cls: dict,
    library_id: int,
    component_type: str,
    component_id: int | None,
    *,
    title: str,
    files_root: str,
    source_type: str = "",
):
    """Render one stored component for the Modules pane or a catalog tab.

    Args:
        school: Open school database.
        cls: Enriched class row.
        library_id: Shared ``content_libraries.id``.
        component_type: ``page``, ``assignment``, ``quiz``, or ``header``.
        component_id: Primary key in the component table.
        title: Display title fallback.
        files_root: Prefix for ``web_resources`` asset URLs.
        source_type: Original cartridge content type, for placeholders.
    """
    code = str(cls.get("ontario_code") or cls.get("course_code") or "")

    if component_type == "header":
        return wrap_page(title, f"<h1>{_escape(title)}</h1>")
    if component_type == "page" and component_id:
        page = get_page(school, int(library_id), int(component_id))
        if page is None:
            abort(404)
        return _render_page_component(
            page,
            ontario_code=code,
            files_root=files_root,
            data_dir=school.data_dir,
        )
    if component_type == "assignment" and component_id:
        row = get_assignment(school, int(library_id), int(component_id))
        if row is None:
            abort(404)
        points = row.get("points")
        meta = f"<p><strong>Out of:</strong> {points:g}</p>" if points else ""
        body = row.get("body_html") or "<p>No description in the import.</p>"
        return wrap_page(
            title,
            f"<h1>{_escape(row.get('title') or title)}</h1>{meta}"
            + rewrite_wiki_html(body, code, files_root=files_root),
        )
    if component_type == "quiz" and component_id:
        return _render_quiz(
            school,
            cls,
            int(library_id),
            int(component_id),
            title=title,
            files_root=files_root,
        )
    if component_type == "bank" and component_id:
        return _render_bank(
            school, cls, int(library_id), int(component_id), files_root=files_root
        )
    return placeholder_html(title, str(source_type or component_type or ""))


def _serve_component_item(
    school, cls: dict, library_id: int, item_id: int, *, files_root: str
):
    """Render one module item by resolving its linked component.

    Args:
        school: Open school database.
        cls: Enriched class row.
        library_id: Shared ``content_libraries.id``.
        item_id: ``module_items.id``.
        files_root: Prefix for ``web_resources`` asset URLs.
    """
    item = get_module_item(school, int(library_id), int(item_id))
    if item is None:
        abort(404)
    return _render_component(
        school,
        cls,
        int(library_id),
        str(item.get("component_type") or ""),
        item.get("component_id"),
        title=item.get("title") or "Item",
        files_root=files_root,
        source_type=str(item.get("source_type") or ""),
    )



def board_write_closed(exc: BoardSessionClosed):
    """JSON for a board write the server did not store.

    A missing or mismatched run key on a live session is ``stale_run``.
    An ended session, including a run closed while End is still in
    progress, is ``ended``. The two flags are not set together.

    Args:
        exc: Close raised by the session check or the board store.

    Returns:
        A 409 response.
    """
    if getattr(exc, "stale_run", False):
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "This board is from an earlier class.",
                    "stale_run": True,
                }
            ),
            409,
        )
    return jsonify({"ok": False, "error": "Session has ended.", "ended": True}), 409


def _catchup_student_for_class(school: SchoolDB, class_id: int) -> int | None:
    """A student signed in for catch-up in this class, else None (MCK-234)."""
    from catchup_session import identity_for_class

    return identity_for_class(session, school, int(class_id))


def _pin_portal_identity(school: SchoolDB) -> tuple[int, int] | None:
    """Roster ids for a valid PIN portal session (MCK-208)."""
    ctx = resolve_pin_portal_context(school, session)
    if ctx is None:
        return None
    return int(ctx["class_id"]), int(ctx["student_id"])


def _student_post_live_end_destination(school: SchoolDB) -> str:
    """Where students land when live ends; PIN portal returns to the dashboard.

    The join page gets ``ended=1`` from this same redirect. That flag is
    the quit/end signal the student poll already follows. It does not
    start another poll.
    """
    if resolve_pin_portal_context(school, session) is not None:
        return url_for("student_dashboard")
    return url_for("landing", ended=1)


def _student_dashboard_live_handover(
    school: SchoolDB, class_id: int, student_id: int
) -> dict[str, Any]:
    """Live handover for the PIN dashboard (poll + first paint)."""
    live = school.get_active_live_session_for_class(int(class_id))
    if live is None:
        return {
            "live_active": False,
            "in_live": False,
            "redirect_url": None,
            "show_rejoin_strip": False,
            "rejoin_url": None,
        }
    live_id = int(live["id"])
    token = visit_token_from_request() or str(session.get("student_visit_token") or "")
    if not token:
        attendee = school.get_live_session_attendee(live_id, int(student_id))
        if attendee is not None:
            token = str(attendee.get("visit_token") or "")
    if token and school.student_is_active_live_attendee(
        live_id,
        int(student_id),
        visit_token=token,
    ):
        return {
            "live_active": True,
            "in_live": True,
            "redirect_url": url_for("student_rejoin_live"),
            "show_rejoin_strip": False,
            "rejoin_url": None,
        }
    return {
        "live_active": True,
        "in_live": False,
        "redirect_url": None,
        "show_rejoin_strip": True,
        "rejoin_url": url_for("student_rejoin_live"),
    }


def _student_dashboard_redirect_if_live(school: SchoolDB, class_id: int, student_id: int) -> Any | None:
    """Send live attendees to Run Live Class home with the visit token."""
    live = school.get_active_live_session_for_class(int(class_id))
    if live is None:
        return None
    live_id = int(live["id"])
    token = visit_token_from_request() or str(session.get("student_visit_token") or "")
    if not token:
        attendee = school.get_live_session_attendee(live_id, int(student_id))
        if attendee is not None:
            token = str(attendee.get("visit_token") or "")
    if token:
        try:
            resolved = school.resolve_student_visit_token(token, allow_left=True)
        except Exception:
            report_swallowed("student visit token")
            resolved = None
        if (
            resolved is not None
            and int(resolved.get("class_id") or 0) == int(class_id)
            and int(resolved.get("student_id") or 0) == int(student_id)
            and school.student_is_active_live_attendee(
                live_id,
                int(student_id),
                visit_token=token,
            )
        ):
            endpoint = next_student_endpoint(
                school,
                int(class_id),
                int(student_id),
                visit_token=token,
                unmatched=False,
            )
            return redirect(student_url_with_token(endpoint, token))
    ctx = resolve_student_live_context(school, session)
    if (
        ctx is not None
        and int(ctx.get("class_id") or 0) == int(class_id)
        and int(ctx.get("student_id") or 0) == int(student_id)
        and ctx.get("live_session_id")
        and school.student_is_active_live_attendee(
            int(ctx["live_session_id"]),
            int(student_id),
            visit_token=str(ctx.get("visit_token") or ""),
        )
    ):
        return redirect(
            student_url_with_token(
                "student_home", str(ctx.get("visit_token") or "")
            )
        )
    return None


def _svgz_is_gzip(target: Path) -> bool:
    """Return True when a blob starts with gzip magic.

    Werkzeug labels a ``.gz`` download ``Content-Encoding: gzip`` from
    the suffix alone, and the same guess applies to ``.svgz``. A raw
    SVG stored under either name must not get that label.

    Args:
        target: Blob path on disk.
    """
    try:
        with target.open("rb") as handle:
            return handle.read(2) == b"\x1f\x8b"
    except OSError:
        return False


def _send_module_file(target: Path, rel: str) -> Response:
    """Serve one library blob under the module-file allowlist.

    Staff and student module-file routes both use this. Raster images
    stay inline. SVG names stay inline as ``image/svg+xml`` with the
    sandbox policy. Every other type is an attachment with that policy.
    The stored bytes are not rewritten. A ``.svgz`` or ``.svg.gz`` name
    is labelled gzip only when the blob itself is gzip.

    Args:
        target: Blob path on disk.
        rel: Cartridge-relative path used as the download name.

    Returns:
        The file response.
    """
    from bank_image_mirror import apply_module_file_headers

    name = Path(rel).name.lower()
    svg_name = name.endswith(".svg") or name.endswith(".svgz") or name.endswith(".svg.gz")
    gzip_name = name.endswith(".svgz") or name.endswith(".svg.gz")
    kwargs: dict[str, str] = {}
    if svg_name:
        kwargs["mimetype"] = "image/svg+xml"
    response = send_from_directory(
        target.parent, target.name, download_name=Path(rel).name, **kwargs
    )
    if gzip_name:
        if _svgz_is_gzip(target):
            response.headers["Content-Encoding"] = "gzip"
        else:
            response.headers.pop("Content-Encoding", None)
    return apply_module_file_headers(response, rel)


def create_app(
    *,
    db_path: Path | None = None,
    data_dir: Path | None = None,
    testing: bool = False,
    live_database_url: str | None = None,
    live_presence_schema: str | None = None,
) -> Flask:
    """Build the LLOVES Flask application.

    Args:
        db_path: Override sqlite path (tests).
        data_dir: Override game-show uploads/logs directory.
        testing: Disable CSRF-adjacent secure cookies; used by tests.
        live_database_url: Postgres URL for live heartbeat / presence.
            ``None`` reads the environment. ``""`` forces sqlite.
        live_presence_schema: Optional Postgres schema for tests.
    """
    if not testing:
        init_flask_sentry()
    app = Flask(
        __name__,
        template_folder=str(LMS_DIR / "templates"),
        static_folder=None,
    )
    secret = (os.getenv("FLASK_SECRET_KEY") or "").strip()
    app.secret_key = secret or DEV_SECRET_KEY
    app.config["TESTING"] = testing
    # Refuse before opening sqlite when the test-login flag is on in production,
    # on Fly, or without HOST set explicitly to localhost / 127.0.0.1.
    enforce_local_test_login(app)
    app.config["MAX_CONTENT_LENGTH"] = IMSCC_MAX_BYTES
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=31)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    secure = (os.getenv("FLASK_ENV") or "").lower() == "production"
    app.config["SESSION_COOKIE_SECURE"] = secure and not testing
    if not secure:
        # Local runs keep debug off; still reload staff HTML after template edits.
        app.config["TEMPLATES_AUTO_RELOAD"] = True
        app.jinja_env.auto_reload = True
    if secure:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
        app.config["PREFERRED_URL_SCHEME"] = "https"
        if local_dev_login_enabled():
            raise RuntimeError(
                "LOCAL_DEV_LOGIN is set with FLASK_ENV=production; refusing to "
                "start with the offline login picker exposed publicly."
            )
        if not secret or secret == DEV_SECRET_KEY:
            raise RuntimeError(
                "FLASK_SECRET_KEY is not set with FLASK_ENV=production; refusing "
                "to start with the public dev key signing sessions."
            )

    db_file = Path(db_path or os.getenv("LLOVES_DB") or DEFAULT_DB_PATH)
    store = Path(data_dir or os.getenv("LLOVES_DATA_DIR") or db_file.parent)
    it_email = (os.getenv("IT_EMAILS") or DEFAULT_IT_EMAIL).split(",")[0].strip()
    school = SchoolDB(
        db_file,
        store,
        it_email=it_email or DEFAULT_IT_EMAIL,
        live_database_url=live_database_url,
        live_presence_schema=live_presence_schema,
    )
    app.config["SCHOOL_DB"] = school
    app.config["DATA_DIR"] = store
    try:
        from course_slide_files import sweep_stale_slide_dirs

        def _close_slide_imports() -> None:
            """Drop stale conversion dirs and close jobs a dead worker left running."""
            sweep_stale_slide_dirs()
            closed = school.fail_orphaned_slide_import_jobs()
            if closed:
                logger.info("closed %s slide import left running", int(closed))

        if testing:
            _close_slide_imports()
        else:
            run_once_per_boot("slide-import-sweep", _close_slide_imports)
    except Exception:
        logger.exception("slide import sweep failed")
    # MCK-118 LOW-A: Most Engaged fingerprints are keyed to the app secret.
    bind_celebration_secret(school, app.secret_key)
    # MCK-176: rank option aliases for students are keyed to the app secret.
    school.rank_alias_secret = str(app.secret_key or "")
    try:
        # MCK-118: CELEBRATIONS_FROZEN=0 at boot retires the old snapshot.
        note_celebrations_unfrozen(school)
    except Exception:
        logger.exception("celebration freeze flag check failed")
    seed_curriculum(school)
    if not testing:
        try:
            from mck210_catchup_migration import run_mck210_catchup_migration_if_needed

            # MCK-231: once per machine boot, not once per gunicorn worker.
            run_once_per_boot(
                "mck210", lambda: run_mck210_catchup_migration_if_needed(school)
            )
        except Exception:  # noqa: BLE001 - migration must not block boot
            app.logger.exception("MCK-210 catch-up migration failed")
        try:
            from pack_install import resume_interrupted_packs

            # A dead installer leaves course.imscc on disk. Retry once per boot.
            run_once_per_boot(
                "pack-resume", lambda: resume_interrupted_packs(school)
            )
        except Exception:  # noqa: BLE001 - resume must not block boot
            app.logger.exception("module pack resume failed")
    if local_dev_login_enabled() and not secure:
        try:
            seed_local_dev_school(school)
        except Exception:  # noqa: BLE001 - empty school is worse than a log
            app.logger.exception("LOCAL_DEV_LOGIN seed failed")
    # MCK-345: one grant for whoever already has an account. Later boots
    # leave the table alone, so a teacher who joins after this stays basic.
    try:
        from features import grandfather_features_once
    except ImportError:
        from lms.features import grandfather_features_once
    grandfather_features_once(school)

    register_auth_routes(app)
    register_local_test_login(app)
    from types import SimpleNamespace

    from routes_common import LaneBlueprint, bind_shared
    from routes_live import register_live
    from routes_platform import register_platform
    from routes_teacher import register_teacher

    shared = _call_with_app_globals(
        bind_shared,
        (app, school),
        {"SimpleNamespace": SimpleNamespace},
    )
    lane_extra = {"LaneBlueprint": LaneBlueprint}
    _call_with_app_globals(register_teacher, (app, school, shared), lane_extra)
    _call_with_app_globals(register_platform, (app, school, shared), lane_extra)
    _call_with_app_globals(register_live, (app, school, shared), lane_extra)
    _register_request_hooks(app)
    app.jinja_env.globals["live_schedule"] = format_live_schedule_line
    # MCK-195: landing Teacher login reuses sign-in's next= check.
    app.jinja_env.globals["safe_next_url"] = _safe_next_url

    @app.context_processor
    def inject_teacher_features() -> dict[str, Any]:
        """Expose registry flags for the signed-in teacher or the session owner.

        Students follow the live session's teacher. The payload is booleans
        only. A lookup failure hides pro-only controls.
        """
        import json

        try:
            from features import FEATURE_KEYS, feature_for_session, feature_map
        except ImportError:
            from lms.features import FEATURE_KEYS, feature_for_session, feature_map
        flags = {key: False for key in FEATURE_KEYS}
        try:
            if has_request_context():
                user = current_user()
                if user is not None:
                    flags = feature_map(user)
                else:
                    sid = session.get("student_live_session_id")
                    if sid:
                        flags = {
                            key: feature_for_session(int(sid), key) for key in FEATURE_KEYS
                        }
        except Exception:
            logger.exception("feature flags for this page failed")
        return {
            "teacher_features": flags,
            "teacher_features_json": json.dumps(flags),
        }

    @app.context_processor
    def inject_public_brand() -> dict[str, str]:
        """Expose ALC display names and McKenzian credit on every template."""
        return public_brand()

    @app.context_processor
    def inject_sentry_browser() -> dict[str, str]:
        """Expose the browser Sentry meta values. Empty DSN skips the SDK.

        MCK-183: with a DSN, the page also carries the teacher id (staff
        only) and the same tags the server sets. No student data.
        """
        values = sentry_browser_context()
        if values["sentry_live_dsn"] and has_request_context():
            try:
                values.update(
                    sentry_browser_tags(
                        request.path,
                        request.args,
                        session,
                        request.view_args,
                        school.telemetry_class_course,
                    )
                )
            except Exception:  # noqa: BLE001 - telemetry never breaks a page
                app.logger.debug("Sentry browser tags failed", exc_info=True)
        return values

    # MCK-183: Sentry user (staff only), tags, and key-action log lines.
    install_sentry_request_scope(app, school.telemetry_class_course)

    @app.after_request
    def add_security_headers(response: Response) -> Response:
        """Browser isolation headers. HSTS only behind production TLS."""
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://accounts.google.com; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self' https://accounts.google.com "
            f"{SENTRY_CONNECT_SRC}; "
            "frame-src 'self' https://accounts.google.com; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self' https://accounts.google.com",
        )
        if secure:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response

    @app.before_request
    def _hide_whats_new_source_file():
        """MCK-182 gate LOW: the deploy-written history file is not public.

        It carries SHAs, PR and ticket refs; teachers read the scrubbed
        ``/api/staff/whats-new`` instead. The app still reads it from disk.
        """
        if _is_whats_new_source_path(request.path) or (
            request.endpoint == "static"
            and _is_whats_new_source_path(
                "/static/" + str((request.view_args or {}).get("filename") or "")
            )
        ):
            abort(404)
        return None

    @app.before_request
    def _shed_live_polls_during_worker_warmup():
        """Shed boot-queue ``/state`` polls so ``/health`` can be served.

        gunicorn binds before this worker accepts. A soft restart under
        live polls fills that queue. Retry JSON skips auth, sqlite, and
        presence. ``/health`` and every other path stay on the normal view.

        Returns:
            The shed body during warmup, otherwise ``None`` so the view runs.
        """
        if not is_live_state_poll(request.path):
            return None
        if not worker_warmup_active():
            return None
        return _poll_shed_response()

    maybe_arm_worker_warmup(testing=testing)
    # After gunicorn init_signals. Ends 25 s LiveNewsWire holds on SIGTERM
    # so the deploy drain can release the volume. No-op outside gunicorn.
    install_stream_shutdown_hook()
    from cli import register_cli

    register_cli(app)
    return app


def _same_origin_post() -> bool:
    """True when this request came from one of our own pages.

    Used for state-changing POSTs and for DELETE of a course slide deck.
    When the browser sends ``Sec-Fetch-Site`` it decides: only
    ``same-origin`` passes (``cross-site``, ``same-site`` and ``none``
    are refused), whatever ``Origin`` says. Without it, ``Origin`` must
    match this host when sent; with no ``Origin``, a ``Referer`` from
    another host is refused too. Old browsers that send neither pass.
    """
    site = str(request.headers.get("Sec-Fetch-Site") or "").strip().lower()
    if site:
        # #272 gate MED-1: invite.html sends no Referer, so Chrome posts
        # ``Origin: null`` with ``Sec-Fetch-Site: same-origin``. Fetch
        # metadata is the browser's own verdict: trust it when present.
        return site == "same-origin"
    own = request.host_url.rstrip("/").lower()
    origin = str(request.headers.get("Origin") or "").strip().lower()
    if origin:
        return origin == own
    referer = str(request.headers.get("Referer") or "").strip().lower()
    if referer:
        return referer == own or referer.startswith(own + "/")
    return True

def _call_with_app_globals(func, args: tuple, extra: dict) -> object:
    """Call ``func`` with this module's globals plus ``extra``.

    Route bodies and shared helpers live in other modules. Running them
    against this module means a view resolves ``jsonify`` and
    ``resolve_student_live_context`` here, so a test patch on ``app``
    still reaches the request.

    Args:
        func: Registrar or ``bind_shared``.
        args: Positional arguments for ``func``.
        extra: Names the function loads that this module does not define,
            such as ``LaneBlueprint`` or ``SimpleNamespace``.

    Returns:
        Whatever ``func`` returns.
    """
    import types

    had = {key: key in globals() for key in extra}
    previous = {key: globals().get(key) for key in extra}
    globals().update(extra)
    try:
        bound = types.FunctionType(func.__code__, globals(), func.__name__)
        return bound(*args)
    finally:
        for key, was_present in had.items():
            if was_present:
                globals()[key] = previous[key]
            else:
                globals().pop(key, None)


def _register_request_hooks(app: Flask) -> None:
    """Register app-wide hooks that used to sit among the page routes.

    These stay on the application, not on a blueprint, so they run for
    every request and every template. Registration order is the upload
    error handler, the live-poll clock, the live-poll connection closer,
    then the Help menu.

    Args:
        app: Flask application.
    """
    app.errorhandler(413)(upload_too_large)
    app.before_request(_mark_live_state_started)
    app.after_request(_close_live_poll_connection)
    app.context_processor(_help_menu_context)



def upload_too_large(_err):
    """Explain HTTP 413 when a request body was rejected upstream or locally."""
    if IMSCC_MAX_BYTES is None:
        message = (
            "Upload rejected (HTTP 413). LLOVES does not cap module-pack size "
            "in the app — check Fly idle timeout, Cloudflare (must be DNS-only), "
            "or /data volume free space. See lms/DEPLOY.md."
        )
    else:
        max_mb = IMSCC_MAX_BYTES // (1024 * 1024)
        message = (
            f"Module pack is too large (max {max_mb} MB). "
            "If the file is under that limit, the edge proxy or volume may still "
            "be capping uploads — see lms/DEPLOY.md."
        )
    if _wants_json():
        return jsonify({"ok": False, "error": message}), 413
    if "/module-pack" in (request.path or ""):
        session["pack_error"] = message
        parts = request.path.strip("/").split("/")
        try:
            class_id = int(parts[parts.index("class") + 1])
            return redirect(
                url_for("staff_course", class_id=class_id, tab="modules")
            )
        except (ValueError, IndexError):
            pass
        return redirect(url_for("it_dashboard", tab="offerings"))
    return (message, 413)

def _mark_live_state_started():
    """Start the clock for a live ``/state`` poll.

    The duration is logged after the response. No session id, query,
    or body is stored.
    """
    if is_live_state_poll(request.path):
        g.live_state_started = time.perf_counter()

def _close_live_poll_connection(response):
    """Drop keepalive on live ``/state`` so idle tabs cannot pile up sockets.

    gunicorn's default ``worker_connections`` is 1000. Poll responses
    that stay open become ESTABLISHED fds. ``Connection: close`` keeps
    those polls out of the keepalive set. ``/health`` is not a poll.
    A live poll also logs its duration and payload size, with no
    session id, code, name, or address.

    Args:
        response: The view's response.

    Returns:
        The same response, with ``Connection: close`` on live polls.
    """
    if is_live_state_poll(request.path):
        response.headers["Connection"] = "close"
        started = getattr(g, "live_state_started", None)
        if started is not None:
            duration_ms = int((time.perf_counter() - float(started)) * 1000)
            try:
                payload_bytes = response.calculate_content_length()
            except Exception:  # noqa: BLE001 - the poll must still be returned
                payload_bytes = 0
            logger.info(
                "live /state duration_ms=%d payload_bytes=%d",
                duration_ms,
                int(payload_bytes or 0),
            )
    return response

def _help_contact_email() -> str:
    """Help menu "Get help" mailto target."""
    for key in ("HELP_CONTACT_EMAIL", "INVITE_REPLY_TO"):
        value = (os.getenv(key) or "").strip()
        if value:
            return value
    return "solutions@mckenzian.com"

def _help_menu_context() -> dict[str, Any]:
    """MCK-183 slice D: the Help menu's contact address on staff pages."""
    return {"help_contact_email": _help_contact_email()}


if __name__ == "__main__":
    # The test script passes --host "$HOST". Other launches keep the dev default.
    host = cli_bind_host(sys.argv)
    port = cli_bind_port(sys.argv)
    application = create_app()
    catalog_n = len(application.config["SCHOOL_DB"].list_ontario_courses())
    print(f"LLOVES LMS: http://{host}:{port}/")
    print(f"Database: {application.config['SCHOOL_DB'].db_path}")
    print(f"Ontario catalog: {catalog_n} courses")
    application.run(host=host, port=port, debug=os.getenv("FLASK_DEBUG") == "1")

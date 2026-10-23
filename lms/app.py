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
    response_landed_events,
    teacher_state_seq,
)
from sentry_wire import (  # noqa: E402
    SENTRY_CONNECT_SRC,
    browser_context as sentry_browser_tags,
    init_flask_sentry,
    install_request_scope as install_sentry_request_scope,
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
    logger.exception("live artifact %s failed", action)
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
    """Where students land when live ends; PIN portal returns to the dashboard."""
    if resolve_pin_portal_context(school, session) is not None:
        return url_for("student_dashboard")
    return url_for("landing")


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
    """Return True when a ``.svgz`` blob actually starts with gzip magic.

    Werkzeug labels a download ``Content-Encoding: gzip`` from the
    ``.svgz`` suffix alone. A raw SVG stored under that name must not
    get that label.

    Args:
        target: Blob path on disk.
    """
    try:
        with target.open("rb") as handle:
            return handle.read(2) == b"\x1f\x8b"
    except OSError:
        return False


def _send_module_file(target: Path, rel: str) -> Response:
    """Serve one library blob and sandbox active markup on this route.

    Staff and student module-file routes both serve mirrored bank images.
    The sandbox policy is applied when the response is built, so an SVG
    already stored on the volume is covered without rewriting it. A
    ``.svgz`` name is labelled gzip only when the blob itself is gzip.

    Args:
        target: Blob path on disk.
        rel: Cartridge-relative path used as the download name.

    Returns:
        The file response.
    """
    from bank_image_mirror import apply_mirrored_svg_headers

    kwargs: dict[str, str] = {}
    gzip_svgz = False
    if Path(rel).name.lower().endswith(".svgz"):
        gzip_svgz = _svgz_is_gzip(target)
        kwargs["mimetype"] = "image/svg+xml"
    response = send_from_directory(
        target.parent, target.name, download_name=Path(rel).name, **kwargs
    )
    if gzip_svgz:
        response.headers["Content-Encoding"] = "gzip"
    return apply_mirrored_svg_headers(response, rel)


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

    register_auth_routes(app)
    register_local_test_login(app)
    _register_pages(app, school)
    _register_game_api(app, school)
    app.jinja_env.globals["live_schedule"] = format_live_schedule_line
    # MCK-195: landing Teacher login reuses sign-in's next= check.
    app.jinja_env.globals["safe_next_url"] = _safe_next_url

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


def _register_pages(app: Flask, school: SchoolDB) -> None:
    """Landing, IT, staff, student, static, and module/syllabus routes."""

    def _audit(
        action: str,
        resource_type: str,
        *,
        resource_id: str | int | None = None,
        student_id: int | None = None,
        detail: dict[str, Any] | None = None,
        actor: dict[str, Any] | None = None,
    ) -> None:
        """Write an access/admin audit row; never raise to the request."""
        user = actor if actor is not None else current_user()
        try:
            school.record_access_event(
                action=action,
                resource_type=resource_type,
                actor_user_id=int(user["id"]) if user else None,
                actor_role=str((user or {}).get("role") or "unknown"),
                tenant_id=school.tenant_id_of(user) if user else None,
                resource_id=resource_id,
                student_id=student_id,
                ip=request_client_ip(),
                detail=detail,
            )
        except Exception:  # noqa: BLE001 - pages must still render
            app.logger.exception("access audit write failed")

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

    @app.errorhandler(413)
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

    @app.route("/static/<path:filename>")
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

    @app.route("/")
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

    @app.route("/calc")
    def calc_awards_redirect():
        """Old /calc bookmark — send people to the ALC hash route."""
        return redirect("/#celebrations")

    @app.route("/dev/whiteboard-formative")
    @app.route("/dev/whiteboard-formative/<role>")
    def dev_whiteboard_formative(role: str = "student"):
        """Headless + shoot.py target for MCK-218 formative whiteboard mocks."""
        from local_dev_seed import local_dev_login_enabled

        if not local_dev_login_enabled():
            return jsonify({"ok": False, "error": "Not found"}), 404
        token = str(role or "student").strip().lower()
        if token not in {"student", "teacher"}:
            token = "student"
        return render_template("dev/whiteboard_formative.html", role=token)

    @app.route("/api/celebrations", methods=["GET"])
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

    @app.route("/request-access", methods=["GET", "POST"])
    def request_access():
        """Capture a signup request. Never collects payment or auto-allowlists."""
        error = None
        submitted = False
        form = {
            "name": "",
            "email": "",
            "role": "parent",
            "organization": "",
            "context": "",
        }
        if request.method == "POST":
            honeypot = (request.form.get("website") or "").strip()
            form["name"] = (request.form.get("name") or "").strip()
            form["email"] = (request.form.get("email") or "").strip()
            form["role"] = (request.form.get("role") or "parent").strip()
            form["organization"] = (request.form.get("organization") or "").strip()
            form["context"] = (request.form.get("context") or "").strip()
            if honeypot:
                submitted = True
            else:
                try:
                    row = school.create_access_request(
                        name=form["name"],
                        email=form["email"],
                        role=form["role"],
                        organization=form["organization"],
                        context=form["context"],
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
        )

    @app.route("/health")
    def health():
        """Fly / DNS liveness — no auth, no presence ping, no live session."""
        return jsonify(
            {
                "ok": True,
                "school": SCHOOL_SHORT,
                "live_presence": school.live_presence_label(),
            }
        )

    @app.after_request
    def _close_live_poll_connection(response):
        """Drop keepalive on live ``/state`` so idle tabs cannot pile up sockets.

        gunicorn's default ``worker_connections`` is 1000. Poll responses
        that stay open become ESTABLISHED fds. ``Connection: close`` keeps
        those polls out of the keepalive set. ``/health`` is not a poll.

        Args:
            response: The view's response.

        Returns:
            The same response, with ``Connection: close`` on live polls.
        """
        if is_live_state_poll(request.path):
            response.headers["Connection"] = "close"
        return response

    @app.route("/it")
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

    @app.route("/it/invites", methods=["POST"])
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

    @app.route("/it/invites/<int:invite_id>/resend", methods=["POST"])
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

    @app.route("/it/invites/<int:invite_id>/link", methods=["POST"])
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

    @app.route("/it/invites/<int:invite_id>/revoke", methods=["POST"])
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

    @app.route("/it/audit.csv")
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

    @app.route("/it/semesters/activate", methods=["POST"])
    @it_required
    def it_activate_semester():
        """Clone ``frameworks/semester.json`` (or switch an existing row)."""
        existing_id = request.form.get("semester_id")
        if existing_id:
            school.set_active_semester(int(existing_id))
        else:
            school.activate_from_semester_json()
        return redirect(url_for("it_dashboard"))

    @app.route("/api/it/settings", methods=["GET", "POST"])
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

    @app.route("/api/it/live-problems", methods=["GET", "POST"])
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

    @app.route("/api/it/live-problems/<int:problem_id>", methods=["PATCH", "DELETE"])
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

    @app.route("/api/it/quick-phrases", methods=["GET", "POST"])
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

    @app.route("/api/it/quick-phrases/<int:phrase_id>", methods=["PATCH"])
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

    @app.route("/api/it/live-sessions/<int:session_id>/end", methods=["POST"])
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

    @app.route("/api/it/live-sessions/end-all", methods=["POST"])
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

    @app.route("/it/staff", methods=["POST"])
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

    @app.route("/it/access-requests/<int:request_id>/review", methods=["POST"])
    @it_required
    def it_review_access_request(request_id: int):
        """Mark a signup request reviewed without creating a user."""
        actor = current_user()
        school.mark_access_request_reviewed(request_id, int(actor["id"]))
        return redirect(url_for("it_dashboard"))

    @app.route("/it/courses")
    @it_required
    def it_search_courses():
        """JSON autocomplete for Ontario course codes."""
        q = request.args.get("q") or ""
        return jsonify({"ok": True, "courses": school.search_ontario_courses(q, limit=80)})

    @app.route("/it/curriculum-expectations")
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

    @app.route("/it/curriculum-expectations/<code>")
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

    @app.route("/it/instances")
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

    @app.route("/it/offerings", methods=["POST"])
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

    @app.route("/it/offerings/<int:offering_id>/module-pack", methods=["POST"])
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

    @app.route(
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

    @app.route("/it/offerings/<int:offering_id>/module-pack/status")
    @it_required
    def it_module_pack_status(offering_id: int):
        """JSON progress for an IT library upload/unpack."""
        offering = school.get_offering(int(offering_id))
        return _pack_status_response(offering)

    @app.route("/it/offerings/<int:offering_id>/rotate", methods=["POST"])
    @it_required
    def it_rotate_code(offering_id: int):
        """Rotate the shared student key for a (semester, course) pair."""
        school.rotate_live_access_code(offering_id)
        return redirect(url_for("it_dashboard"))

    @app.route("/it/offerings/<int:offering_id>/archive", methods=["POST"])
    @it_required
    def it_archive_offering(offering_id: int):
        """Soft-archive an offering so it no longer appears on the teacher's dashboard."""
        try:
            school.archive_offering(offering_id)
        except KeyError:
            abort(404)
        return redirect(url_for("it_dashboard", tab="offerings"))

    @app.route("/it/offerings/<int:offering_id>/unarchive", methods=["POST"])
    @it_required
    def it_unarchive_offering(offering_id: int):
        """Restore an archived offering so it reappears on the teacher's dashboard."""
        try:
            school.unarchive_offering(offering_id)
        except KeyError:
            abort(404)
        return redirect(url_for("it_dashboard", tab="offerings"))

    @app.route("/it/staff/<int:staff_id>/history")
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

    @app.route("/it/staff/<int:staff_id>/rename", methods=["POST"])
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

    @app.route("/it/staff/<int:staff_id>/deactivate", methods=["POST"])
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

    @app.route("/it/staff/<int:staff_id>/reactivate", methods=["POST"])
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

    @app.route("/it/staff/<int:staff_id>/delete", methods=["POST"])
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

    @app.route("/it/staff/<int:staff_id>/assign", methods=["GET", "POST"])
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

    @app.route("/it/offerings/<int:offering_id>/pack")
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

    def _help_contact_email() -> str:
        """Help → "Ask Shawn for help" mailto target."""
        for key in ("HELP_CONTACT_EMAIL", "INVITE_REPLY_TO"):
            value = (os.getenv(key) or "").strip()
            if value:
                return value
        return "solutions@mckenzian.com"

    @app.context_processor
    def _help_menu_context() -> dict[str, Any]:
        """MCK-183 slice D: the Help menu's contact address on staff pages."""
        return {"help_contact_email": _help_contact_email()}

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

    def _same_origin_post() -> bool:
        """True when this POST came from one of our own pages.

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

    @app.route("/invite/<token>")
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

    @app.route("/invite/<token>/switch", methods=["POST"])
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

    @app.route("/welcome")
    def welcome_link():
        """MCK-183: the link Shawn sends a new teacher.

        Signed in already: straight to the Welcome screen. Otherwise Google
        sign-in (and the first-login email code) with ``next`` kept, so she
        lands on Welcome. An unlisted Google account still gets the usual 403.
        """
        if current_user() is not None:
            return redirect(url_for("staff_welcome"))
        return redirect(url_for("auth_google", portal="staff", next=url_for("staff_welcome")))

    @app.route("/staff/welcome")
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

    @app.route("/api/staff/onboarding/course", methods=["POST"])
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
            return jsonify({"ok": False, "error": "No semester is active yet. Ask Shawn."}), 409
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

    @app.route("/staff")
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

    @app.route("/api/staff/whats-new")
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

    @app.route("/staff/bots")
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

    @app.route("/api/staff/celebration-award", methods=["GET", "POST"])
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

    @app.route("/api/staff/celebrations/periods")
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

    @app.route("/api/staff/celebrations/start-fresh", methods=["POST"])
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

    @app.route("/api/staff/defaults")
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

    @app.route("/api/staff/classes", methods=["GET", "POST"])
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

    @app.route("/api/staff/classes/<int:class_id>/roster", methods=["PUT"])
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

    @app.route(
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

    @app.route("/staff/class/<int:class_id>/run-live", methods=["POST"])
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

    @app.route("/staff/class/<int:class_id>/results.csv")
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

    @app.route("/staff/class/<int:class_id>/end-live", methods=["POST"])
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

    @app.route("/staff/class/<int:class_id>/quit-live", methods=["POST"])
    @staff_required
    def staff_quit_live_class(class_id: int):
        """Quit: save nothing, then clear this session's live rows now.

        Same ownership rules as End Class. Attendance, participation,
        and a catch-up link are not written. Responses, attendees, board
        assignments, engagement events, and teacher notes for this
        active session are deleted here, not on the next Start. Quit
        after End changes nothing and says the class already ended.
        """
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            abort(403)
        active = school.get_active_live_session_for_teacher(int(user["id"]))
        # Read seqs before the wipe deletes the rows.
        quit_seqs = {
            int(row["id"]): teacher_state_seq(school, int(row["id"]))
            for row in school.list_live_sessions_for_class(int(class_id))
        }
        if active is None or int(active["class_id"]) != int(class_id):
            return _already_ended_page()
        ended = school.finish_live_class(
            int(class_id), persist=False, celebrate=False
        )
        if isinstance(ended, dict) and ended.get("already_ended"):
            return _already_ended_page()
        if isinstance(ended, dict) and ended.get("finish_busy"):
            return _finish_busy_page()
        # Open tabs (often on the End celebration) fetch now and leave,
        # instead of waiting for the safety poll.
        for quit_sid, quit_seq in quit_seqs.items():
            emit_session_news(
                school,
                quit_sid,
                [{"type": "state_seq", "state_seq": int(quit_seq) + 1}],
            )
        return redirect(url_for("staff_home"))

    @app.route("/staff/class/<int:class_id>")
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
        )
        resp = make_response(html)
        if (
            tab == "ap"
            and ap_view == "roster"
            and school.teacher_owns_class(int(user["id"]), class_id)
        ):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.route("/staff/class/<int:class_id>/module-pack", methods=["POST"])
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

    @app.route("/staff/offerings/<int:offering_id>/module-pack/status")
    @staff_required
    def staff_offering_pack_status(offering_id: int):
        """JSON pack progress for the teacher who owns this offering."""
        user = current_user()
        assert user is not None
        offering = school.get_offering(int(offering_id))
        if int(offering["teacher_user_id"]) != int(user["id"]) and user["role"] != "it":
            abort(403)
        return _pack_status_response(offering)

    @app.route("/staff/class/<int:class_id>/module-pack/status")
    @staff_required
    def staff_module_pack_status(class_id: int):
        """Staff pack-status for a class the teacher owns."""
        _, offering = _owned_offering_for_pack(class_id)
        return _pack_status_response(offering)

    @app.route("/api/staff/class/<int:class_id>/modules")
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

    @app.route("/api/staff/class/<int:class_id>/components/<kind>")
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

    @app.route("/api/staff/class/<int:class_id>/question-bank/<int:bank_id>")
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route("/api/staff/class/<int:class_id>/module-banks")
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

    @app.route("/api/staff/class/<int:class_id>/module-banks/confirm", methods=["POST"])
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    def _content_questions_scope(class_id: int):
        """MCK-79: course code and selectable modules for Contest Questions.

        Contest Questions come from ``live_problems`` (by course), but the
        section lives in Import from bank, so a ready module pack is still
        required, as for the rest of the picker.

        Returns:
            ``(ontario_code, modules, None)`` or ``(None, [], error_response)``.
        """
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return None, [], (jsonify({"ok": False, "error": "Forbidden"}), 403)
        cls = school.enrich_class(school.game.get_class(class_id))
        library_id, error = _ready_library(school, cls)
        if not library_id:
            return None, [], (
                jsonify({"ok": False, "error": error or "No module pack"}),
                404,
            )
        try:
            from live_content_questions import selectable_live_modules
        except ImportError:
            from lms.live_content_questions import selectable_live_modules
        code = str(cls.get("ontario_code") or "MCF3M").strip().upper()
        return code, selectable_live_modules(code), None

    @app.route("/api/staff/class/<int:class_id>/live-lessons/contest-questions")
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route("/staff/class/<int:class_id>/component/<kind>/<int:component_id>")
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

    @app.route("/staff/class/<int:class_id>/module-item")
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

    @app.route("/staff/class/<int:class_id>/module-files/<path:rel>")
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

    @app.route("/staff/class/<int:class_id>/syllabus")
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

    @app.route("/staff/class/<int:class_id>/syllabus/editor")
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

    @app.route("/staff/class/<int:class_id>/syllabus/save", methods=["POST"])
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

    @app.route("/class/<int:class_id>")
    @staff_required
    def class_redirect(class_id: int):
        """Send the old game-show class URL to Attendance & Participation."""
        return redirect(
            url_for("staff_course", class_id=class_id, tab="ap", view="participation")
        )

    @app.route("/class/<int:class_id>/setup")
    @staff_required
    def class_setup(class_id: int):
        """Teacher game setup (attendance / teams)."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            abort(403)
        return send_from_directory(MGS_TEMPLATES, "setup.html")

    @app.route("/class/<int:class_id>/game")
    @staff_required
    def class_game(class_id: int):
        """Teacher scoring dashboard — students never get this route."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            abort(403)
        return send_from_directory(MGS_TEMPLATES, "game.html")

    @app.route("/scoreboard")
    @app.route("/scoreboard/<int:class_id>")
    @staff_or_student_scoreboard
    def scoreboard_page(class_id: int | None = None):
        """ESPN board: staff overlay or student join."""
        return send_from_directory(MGS_TEMPLATES, "scoreboard.html")

    @app.route("/live-overlay/<int:session_id>")
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

    def _student_live_context() -> dict[str, Any] | None:
        """Return token- or cookie-scoped student live context."""
        return resolve_student_live_context(school, session)

    def _positive_class_id(value: Any) -> int | None:
        """Return a positive class id, or None when the value is unusable.

        Args:
            value: Raw class id from a dict, session, or database row.
        """
        if value in (None, ""):
            return None
        try:
            cid = int(value)
        except (TypeError, ValueError):
            return None
        return cid if cid > 0 else None

    def _bound_student_class_id(ctx: dict[str, Any] | None) -> int | None:
        """Resolve the signed-in student's class without a KeyError.

        Order matches other student endpoints: the live context, then the
        Flask session, then the live-session row.

        Args:
            ctx: ``resolve_student_live_context`` result, or None.

        Returns:
            Classes primary key, or None when every fallback is empty.
        """
        sources: list[Any] = []
        if isinstance(ctx, dict):
            sources.append(ctx.get("class_id"))
        sources.append(session.get("student_class_id"))
        for source in sources:
            cid = _positive_class_id(source)
            if cid is not None:
                return cid
        live_id = ctx.get("live_session_id") if isinstance(ctx, dict) else None
        if not live_id:
            live_id = session.get("student_live_session_id")
        if not live_id:
            return None
        try:
            row = school.get_live_session(int(live_id))
        except (TypeError, ValueError):
            return None
        if not isinstance(row, dict):
            return None
        return _positive_class_id(row.get("class_id"))

    def _student_identity() -> tuple[dict[str, Any], int, int] | None:
        """Return offering and roster ids when the student session is bound.

        ``class_id`` is read with ``.get``. A missing key falls back to the
        student session and then the live-session row.

        Returns:
            ``(offering, class_id, student_id)`` or ``None`` if incomplete.
        """
        ctx = _student_live_context()
        if ctx is None:
            return None
        offering = ctx.get("offering")
        class_id = _bound_student_class_id(ctx)
        if not offering or class_id is None:
            return None
        return offering, class_id, ctx.get("student_id")

    def _can_access_class_live_asset(class_id: int) -> bool:
        """Return True when class staff, a bound live student, or a student
        signed in on one of the class's catch-up links may fetch assets.

        ``@login_required`` is staff-only, so image ``<img>`` tags from the
        student portal must use this check instead. Visit-token cookies and
        Flask student session both resolve through ``_student_live_context``.

        Args:
            class_id: Game-show ``classes.id``.
        """
        user = current_user()
        if user is not None and school.teacher_owns_class(
            int(user["id"]), int(class_id)
        ):
            return True
        ctx = _student_live_context()
        if ctx and int(ctx.get("class_id") or 0) == int(class_id):
            return True
        return _catchup_student_for_class(school, int(class_id)) is not None

    @app.route(
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
        return send_from_directory(path.parent, path.name)

    @app.route("/api/classes/<int:class_id>/module-files/<path:rel>")
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

    @app.route("/student/s/<token>")
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

    @app.route("/student/waiting")
    @student_required
    def student_waiting():
        """Legacy waiting URL; send the student to mood / character / home."""
        return _student_advance()

    @app.route("/student/game")
    @student_required
    def student_game():
        """Legacy game URL; send the student to mood / character / home."""
        return _student_advance()

    @app.route("/student/mood", methods=["GET", "POST"])
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

    @app.route("/api/student/exit-feedback", methods=["POST"])
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

    @app.route("/student/exit", methods=["GET", "POST"])
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

    @app.route("/student/character", methods=["GET", "POST"])
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

    @app.route("/student/dashboard")
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

    @app.route("/student/catchup", methods=["GET", "POST"])
    @app.route("/student/catchup/landing", methods=["GET", "POST"])
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

    @app.route("/student/catchup/sign-out")
    def student_catchup_landing_sign_out():
        """Clear the catch-up session and return to sign-in.

        The join code is not placed on the URL.
        """
        from catchup_session import clear_catchup_browser_session

        clear_catchup_browser_session(session)
        # MCK-42: signed_out=1 tells the landing page to clear the device's
        # unsent-answer outbox (localStorage) for the next student.
        return redirect(url_for("landing", signed_out=1))

    @app.route("/api/student/catchup-landing")
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
        return jsonify(
            {
                "ok": True,
                "rows": rows,
                "offset": offset,
                "total": total,
                "has_more": offset + len(rows) < total,
            }
        )

    @app.route("/api/student/dashboard")
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


    @app.route("/student/rejoin-live")
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

    def _viewer_is_stmtg_tester(offering: Any, student_id: Any) -> bool:
        """True when the bound student is the STMTG test seat."""
        from tester_feedback import viewer_is_stmtg_tester

        return viewer_is_stmtg_tester(school, offering, student_id)

    def _redact_tester_payload(
        payload: Any, offering: Any, class_id: int, student_id: Any
    ) -> Any:
        """Blank exact real roster names when the viewer is the STMTG tester."""
        from tester_feedback import redact_real_roster

        if not _viewer_is_stmtg_tester(offering, student_id):
            return payload
        names = set(school.class_codenames(int(class_id)).values())
        return redact_real_roster(payload, names)

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

    @app.route("/student/celebration-done", methods=["POST"])
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

    @app.route("/student/home")
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

    @app.route("/student/tester-feedback", methods=["POST"])
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

    @app.route("/api/student/avatar-reward/claim", methods=["POST"])
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

    @app.route("/student/pick", methods=["GET", "POST"])
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

    @app.route("/api/student/mvp-vote", methods=["POST"])
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

    @app.route("/api/student/state")
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

    @app.route("/api/student/live-prompt")
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

    @app.route("/api/student/live-prompt/artifact-preview", methods=["POST"])
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

    @app.route("/api/student/canvas-presence", methods=["POST"])
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

    @app.route("/api/student/live-prompt/response", methods=["POST"])
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route(
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
            if (
                school._question_answer_kind(item) == "team_draw"
                and str(item.get("response_mode") or "") == "individual"
            ):
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

def _register_game_api(app: Flask, school: SchoolDB) -> None:
    """Mount Math Game Show JSON APIs with staff (or student scoreboard) auth."""

    def _require_class_staff(class_id: int) -> tuple[Any, int] | None:
        """Return a JSON 403/401 tuple if the current user cannot score this class."""
        user = current_user()
        if user is None:
            return jsonify({"ok": False, "error": "Authentication required."}), 401
        if not school.teacher_owns_class(int(user["id"]), class_id):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        return None

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

    @app.route("/api/live-sessions/active")
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

    @app.route("/api/live-sessions/<int:session_id>/state")
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

    @app.route("/api/live-sessions/<int:session_id>/guests", methods=["POST"])
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

    @app.route("/api/live-sessions/<int:session_id>/prompts/active")
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

    @app.route("/api/live-sessions/<int:session_id>/prompts", methods=["POST"])
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

    @app.route("/api/live-sessions/<int:session_id>/artifacts", methods=["POST"])
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

    @app.route("/api/slide-imports/<job_id>", methods=["GET"])
    @login_required
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

    @app.route(
        "/api/live-sessions/<int:session_id>/google-slides",
        methods=["POST"],
    )
    @login_required
    def api_live_session_google_slides(session_id: int):
        """Attach a Google Slides share link or a PowerPoint.

        Public PDF export is tried first (no teacher OAuth). A private deck
        falls back to Connect Google Slides. Conversion runs on a background
        job so it stays inside the worker timeout. This response is 202
        with ``job_id``. A link that is not a Google Slides URL is still
        rejected here, before a job starts.
        """
        _row, err = _active_owned_live_session(session_id)
        if err is not None:
            return err
        too_big = _slide_body_too_large()
        if too_big is not None:
            return too_big
        user = current_user()
        if user is None:
            return jsonify({"ok": False, "error": "Authentication required."}), 401
        owner_id = int(user["id"])
        class_id = int(_row["class_id"])
        upload, upload_err = _slide_form_file()
        if upload_err is not None:
            return upload_err
        if upload is not None and str(upload.filename or "").strip():
            import course_slide_files as slide_files

            published = _slide_default_published(request.form.get("default_published"))
            filename = str(upload.filename or "")
            try:
                suffix = slide_files.office_suffix(filename)
            except ValueError as exc:
                return jsonify({"ok": False, "error": str(exc)}), 400
            spilled = _spill_slide_upload(upload, suffix)
            if isinstance(spilled, tuple):
                return spilled
            title = slide_files.slide_deck_title(
                Path(filename).stem, fallback="PowerPoint"
            )

            def work(job_id: str) -> dict[str, Any]:
                """Convert the spilled upload and point this run at it."""
                import course_slide_files as files

                path = Path(str(spilled))
                try:
                    raw = path.read_bytes()
                    pngs, truncated = files.pages_from_office(raw, suffix, timeout=80)
                    deck = school.import_google_slides_pages(
                        int(session_id),
                        presentation_id="",
                        page_pngs=pngs,
                        source="pptx",
                        truncated=truncated,
                        default_published=published,
                        title=title,
                        source_bytes=raw,
                        source_suffix=suffix,
                        job_id=job_id,
                    )
                finally:
                    path.unlink(missing_ok=True)
                _emit_google_slide_news(int(session_id))
                return {
                    "google_slides": deck,
                    "google_slide": school.student_google_slide(int(session_id)),
                }

            try:
                return _queue_slide_import(owner_id, class_id, int(session_id), work)
            except Exception:
                Path(str(spilled)).unlink(missing_ok=True)
                raise
        body, body_err = _slide_json_body()
        if body_err is not None:
            return body_err
        if request.form:
            body = {
                "url": request.form.get("url") or "",
                "default_published": request.form.get("default_published"),
            }
        url = str(body.get("url") or "").strip()
        published = _slide_default_published(body.get("default_published"))
        try:
            from google_slides_import import parse_presentation_id

            parse_presentation_id(url)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        def work(job_id: str) -> dict[str, Any]:
            """Download the share link under the existing 45 second cap."""
            from google_slides_import import (
                IMPORT_TIME_LIMIT_SECONDS,
                SlidesShareNotPublic,
                import_shared_deck,
            )
            from slide_import_jobs import SlideImportFailed

            try:
                deck = import_shared_deck(
                    school,
                    int(session_id),
                    url,
                    user=user,
                    default_published=published,
                    time_limit=IMPORT_TIME_LIMIT_SECONDS,
                    job_id=job_id,
                )
            except SlidesShareNotPublic as exc:
                raise SlideImportFailed(str(exc), needs_slides_read=True) from exc
            _emit_google_slide_news(int(session_id))
            return {
                "google_slides": deck,
                "google_slide": school.student_google_slide(int(session_id)),
            }

        return _queue_slide_import(owner_id, class_id, int(session_id), work)


    @app.route(
        "/api/live-sessions/<int:session_id>/google-slides/current",
        methods=["POST"],
    )
    @login_required
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
            deck = school.set_google_slides_current(int(session_id), index)
        except KeyError:
            return jsonify({"ok": False, "error": "Session not found"}), 404
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return _google_slides_response(session_id, deck)

    @app.route(
        "/api/live-sessions/<int:session_id>/google-slides/pages/<int:index>/publish",
        methods=["POST"],
    )
    @login_required
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

    @app.route(
        "/api/live-sessions/<int:session_id>/google-slides/pages/<int:index>/unpublish",
        methods=["POST"],
    )
    @login_required
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

    @app.route("/api/classes/<int:class_id>/slide-deck", methods=["GET", "POST"])
    @login_required
    def api_class_slide_deck(class_id: int):
        """Read or attach the course slide deck for one module and slot.

        GET returns the deck name for the Set Class summary. POST accepts
        a Google Slides share link as JSON, or a ``.pptx`` file as
        multipart. A legacy ``.ppt`` is refused. The file is stored on
        the media volume. The row
        stores the path. Conversion runs on a background job. A matching
        live run is pointed at that same copy when the job finishes.
        """
        denied = _require_class_staff(int(class_id))
        if denied is not None:
            return denied
        if request.method == "GET":
            summary = school.course_slide_deck_summary(
                int(class_id),
                request.args.get("module"),
                request.args.get("slot"),
            )
            return jsonify({"ok": True, "deck": summary})
        too_big = _slide_body_too_large()
        if too_big is not None:
            return too_big
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
            spilled = _spill_slide_upload(upload, suffix)
            if isinstance(spilled, tuple):
                return spilled
            title = slide_files.slide_deck_title(
                Path(filename).stem, fallback="PowerPoint"
            )

            def work(job_id: str) -> dict[str, Any]:
                """Convert the spilled upload into the course deck."""
                import course_slide_files as files

                path = Path(str(spilled))
                try:
                    raw = path.read_bytes()
                    pngs, truncated = files.pages_from_office(raw, suffix, timeout=80)
                    saved = school.save_course_slide_deck(
                        int(class_id),
                        module,
                        slot,
                        title=title,
                        source="pptx",
                        page_pngs=pngs,
                        default_published=published,
                        truncated=truncated,
                        source_bytes=raw,
                        source_suffix=suffix,
                        job_id=job_id,
                    )
                finally:
                    path.unlink(missing_ok=True)
                _emit_slide_news_for_class(int(class_id))
                return {
                    "deck": saved.get("deck"),
                    "google_slides": saved.get("google_slides"),
                }

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
            """Import a share link with the same 45 second deadline as a live run."""
            import time as time_mod

            from google_slides_import import (
                IMPORT_TIME_LIMIT_SECONDS,
                SlidesShareNotPublic,
                _download_with_oauth,
                fetch_public_pdf,
                render_pdf_pages,
            )
            from slide_import_jobs import SlideImportFailed

            deadline = time_mod.monotonic() + IMPORT_TIME_LIMIT_SECONDS
            source = "public_export"
            try:
                try:
                    pdf = fetch_public_pdf(presentation_id, deadline=deadline)
                    pngs, truncated = render_pdf_pages(pdf, deadline=deadline)
                except SlidesShareNotPublic:
                    pngs, truncated = _download_with_oauth(
                        school,
                        presentation_id,
                        user=user,
                        http=None,
                        deadline=deadline,
                    )
                    source = "oauth_thumbnail"
            except SlidesShareNotPublic as exc:
                raise SlideImportFailed(str(exc), needs_slides_read=True) from exc
            saved = school.save_course_slide_deck(
                int(class_id),
                module,
                slot,
                title="Google Slides",
                source=source,
                presentation_id=presentation_id,
                page_pngs=pngs,
                default_published=published_flag,
                truncated=truncated,
                job_id=job_id,
            )
            _emit_slide_news_for_class(int(class_id))
            return {
                "deck": saved.get("deck"),
                "google_slides": saved.get("google_slides"),
            }

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

    @app.route(
        "/api/live-sessions/<int:session_id>/active-media",
        methods=["GET", "POST"],
    )
    @login_required
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

    @app.route("/api/live/session/<int:session_id>/events")
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

    @app.route(
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
                pass
        return jsonify(payload)

    @app.route(
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

    @app.route(
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
        try:
            item = school.publish_live_session_item(
                session_id,
                live_item_id,
                publish_mode=str(body.get("publish_mode") or "individual"),
            )
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        seq = teacher_state_seq(school, session_id)
        prompt_id = item.get("prompt_id") if isinstance(item, dict) else None
        emit_session_news(
            school,
            session_id,
            [
                {"type": "state_seq", "state_seq": seq},
                prompt_event(prompt_id, seq),
                flag_work_event("publish", seq),
            ],
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route(
        "/api/live-sessions/<int:session_id>/items/<int:live_item_id>/team-draw/gallery",
        methods=["GET"],
    )
    @login_required
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

    @app.route(
        "/api/live-sessions/<int:session_id>/items/<int:live_item_id>/team-draw/clear",
        methods=["POST"],
    )
    @login_required
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route("/api/student/board/<board_key>", methods=["GET"])
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
        key, rejected = _resolve_student_board_key(
            live_session_id, student_id, board_key
        )
        if rejected is not None:
            return rejected
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

    @app.route("/api/student/board/<board_key>/ops", methods=["POST"])
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

    @app.route(
        "/api/live-sessions/<int:session_id>/whiteboard-gallery",
        methods=["GET"],
    )
    @login_required
    def api_whiteboard_gallery(session_id: int):
        """Teacher gallery. Hidden until a whiteboard question is published."""
        from formative_live_board import parse_cursors

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

    @app.route(
        "/api/live-sessions/<int:session_id>/whiteboard-gallery/<path:board_key>",
        methods=["GET"],
    )
    @login_required
    def api_whiteboard_gallery_one(session_id: int, board_key: str):
        """Teacher side panel for one team or solo board."""
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

    @app.route(
        "/api/live-sessions/<int:session_id>/whiteboard-gallery/<path:board_key>/feedback",
        methods=["POST"],
    )
    @login_required
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

    @app.route(
        "/api/live-sessions/<int:session_id>/whiteboard-gallery/<path:board_key>/release",
        methods=["POST"],
    )
    @login_required
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

    @app.route(
        "/api/live-sessions/<int:session_id>/whiteboard-gallery/<path:board_key>/clear",
        methods=["POST"],
    )
    @login_required
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

    @app.route(
        "/api/live-sessions/<int:session_id>/whiteboard-gallery/<path:board_key>/show",
        methods=["POST"],
    )
    @login_required
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

    @app.route(
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
        if _can_view_live_session(session_row):
            if str(board_key or "").strip() != "teacher":
                return jsonify({"ok": False, "error": "Forbidden"}), 403
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

    @app.route(
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
        return [
            {
                **link,
                "class_id": int(class_id),
                "is_open": bool(link["is_open"]),
                "discarded": bool(link.get("discarded")),
                "url": _catchup_url({**link, "class_id": int(class_id)}),
            }
            for link in school.list_catchup_links(class_id)
        ]

    @app.route("/api/classes/<int:class_id>/participation")
    @login_required
    def api_class_participation(class_id: int):
        """Teacher-only per-module participation column (separate from team points)."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        return jsonify({"ok": True, **school.class_participation(class_id),
                        "catchup_links": _catchup_links_payload(class_id)})

    @app.route("/api/classes/<int:class_id>/participation/start-check")
    @login_required
    def api_participation_start_check(class_id: int):
        """Whether Run Live Class should ask to finish the open module."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        selected = str(request.args.get("module") or "").strip()
        prompt = school.participation_start_prompt(class_id, selected)
        return jsonify({"ok": True, "prompt": prompt})

    @app.route("/api/classes/<int:class_id>/participation/modules/<module>/complete", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/participation/modules/<module>/finish", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/participation/modules/<module>/settings", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/catchup-links")
    @login_required
    def api_catchup_links(class_id: int):
        """Catch-up links per live class, for the Lesson Slides page."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        return jsonify({"ok": True, "catchup_links": _catchup_links_payload(class_id)})

    @app.route("/api/classes/<int:class_id>/lesson-slides/table")
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route(
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

    @app.route("/api/classes/<int:class_id>/catchup-links/<token>", methods=["POST"])
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

    @app.route(
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

    @app.route(
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

    @app.route("/api/classes/<int:class_id>/students/<int:student_id>/pin", methods=["PUT"])
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

    @app.route("/api/classes/<int:class_id>/default-pin-slips", methods=["GET"])
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

    @app.route(
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

    @app.route(
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

    @app.route("/api/classes/<int:class_id>/catchup-pin-policy", methods=["POST"])
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

    @app.route("/api/student/catchup-pin", methods=["POST"])
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

    @app.route("/api/student/catchup-pin/later", methods=["POST"])
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

    @app.route("/staff/classes/<int:class_id>/participation")
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

    @app.route("/catchup/<course>/<slug>/<token>", methods=["GET", "POST"])
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

    @app.route("/catchup/<token>", methods=["GET", "POST"])
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

    @app.route("/api/classes/<int:class_id>/engagement-events")
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

    @app.route("/api/classes/<int:class_id>/engagement-summary/runs")
    @login_required
    def api_engagement_summary_runs(class_id: int):
        """MCK-59: ended runs with rollup rows (teacher-only)."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        return jsonify({"ok": True, "runs": school.list_engagement_summary_runs(class_id)})

    @app.route("/api/classes/<int:class_id>/engagement-summary")
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

    @app.route(
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

    @app.route("/api/classes/<int:class_id>/dashboard")
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

    @app.route("/api/classes/<int:class_id>")
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

    @app.route("/api/classes/<int:class_id>/game")
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

    @app.route("/api/scoreboard")
    @app.route("/api/classes/<int:class_id>/scoreboard")
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

    @app.route("/api/sessions/<int:session_id>/log")
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

    @app.route("/api/classes/<int:class_id>/begin", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/log-context")
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

    @app.route("/api/classes/<int:class_id>/live-session/start", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/live-session/kind", methods=["POST"])
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

    @app.route(
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

    @app.route("/api/classes/<int:class_id>/live-slides", methods=["GET", "POST"])
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
                    "error": "Slides connect is limited to solutions@ and Shawn's Gmail.",
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

    @app.route("/api/classes/<int:class_id>/lesson-slides", methods=["GET", "POST"])
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
                    "error": "Slides connect is limited to solutions@ and Shawn's Gmail.",
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

    @app.route("/api/classes/<int:class_id>/live-lessons", methods=["GET", "POST"])
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

    @app.route("/api/classes/<int:class_id>/live-lessons/<module>/<slot>", methods=["GET"])
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

    @app.route("/staff/offerings/<int:offering_id>/slides/<date_iso>.html")
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

    @app.route("/staff/offerings/<int:offering_id>/lesson-slides/<lesson_key>.html")
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

    @app.route("/api/quick-phrases")
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

    @app.route("/api/live-sessions/<int:session_id>/observations", methods=["GET", "POST"])
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

    @app.route(
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

    @app.route("/api/live-sessions/<int:session_id>/observations/coverage")
    @staff_required
    def api_live_session_observation_coverage(session_id: int):
        """Student × process coverage counts for after-class review."""
        _row, err = _owned_live_session(session_id)
        if err:
            return err
        return jsonify({"ok": True, **school.observation_coverage(session_id)})

    def _staff_post(class_id: int, handler):
        """Run a GameShowDB mutation for a staff-owned class."""
        denied = _require_class_staff(class_id)
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        try:
            return jsonify({"ok": True, **handler(body)})
        except Exception as exc:  # noqa: BLE001
            return _json_error(exc)

    @app.route("/api/classes/<int:class_id>/game/meeting", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/game/cancel", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/game/attendance", methods=["POST"])
    @login_required
    def api_attendance(class_id: int):
        """Save who is present and advance to teams (Begin Class Tracking)."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            _apply_validated_meeting(class_id, body)
            ids = [int(x) for x in (body.get("present_ids") or [])]
            return school.game.save_attendance(class_id, ids)

        return _staff_post(class_id, run)

    @app.route("/api/classes/<int:class_id>/game/finalize-attendance", methods=["POST"])
    @login_required
    def api_finalize_attendance(class_id: int):
        """Save attendance into the week grid and close without teams/live."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            _apply_validated_meeting(class_id, body)
            ids = [int(x) for x in (body.get("present_ids") or [])]
            return school.game.finalize_attendance_only(class_id, ids)

        return _staff_post(class_id, run)

    @app.route("/api/classes/<int:class_id>/game/ungamified", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/attendance-grid")
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

    @app.route("/api/classes/<int:class_id>/mood-grid")
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

    @app.route("/api/classes/<int:class_id>/show-rank", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/attendance-day")
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

    @app.route("/api/classes/<int:class_id>/attendance-day/clear", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/feedback-live/clear", methods=["POST"])
    @login_required
    def api_clear_live_class_feedback(class_id: int):
        """Delete How-was-class rows for one live class column (e.g. M1C1)."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            key = str(body.get("key") or body.get("live_key") or "").strip()
            return school.clear_live_class_feedback(class_id, key)

        return _staff_post(class_id, run)

    @app.route("/api/classes/<int:class_id>/participation-grid")
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

    @app.route("/api/classes/<int:class_id>/ap-round-profiles", methods=["GET", "PUT"])
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

    @app.route("/api/classes/<int:class_id>/portfolio/context")
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

    @app.route("/api/classes/<int:class_id>/portfolio/generate", methods=["POST"])
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
                result["drive_error"] = str(exc)
                result["needs_drive_connect"] = False
        return jsonify(result)

    @app.route("/api/classes/<int:class_id>/portfolio/marking", methods=["GET", "POST"])
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
                pass
            semester = school.get_active_semester() or {}
            label = str(semester.get("label") or "")
            submissions: list[dict[str, str]] = []
            drive_error = ""
            client = None
            try:
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

    @app.route("/api/classes/<int:class_id>/gradebook")
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

    @app.route("/api/classes/<int:class_id>/grade-weights", methods=["GET", "POST"])
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

    @app.route("/api/classes/<int:class_id>/grade-scheme", methods=["GET", "POST"])
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

    @app.route("/api/classes/<int:class_id>/module-reflections", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/game/step", methods=["POST"])
    @login_required
    def api_step(class_id: int):
        """Advance setup status."""
        return _staff_post(
            class_id,
            lambda body: school.game.set_setup_step(class_id, str(body.get("status") or "")),
        )

    @app.route("/api/classes/<int:class_id>/game/assign", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/game/rename", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/game/start-rounds", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/game/append-round", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/game/meet-teams", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/game/timer/start", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/game/timer/pause", methods=["POST"])
    @login_required
    def api_timer_pause(class_id: int):
        """Pause the active Meet / round countdown."""

        def run(_body):
            """Apply one staff JSON mutation for this class."""
            return school.game.pause_round_timer(class_id)

        return _staff_post(class_id, run)

    @app.route("/api/classes/<int:class_id>/game/timer/resume", methods=["POST"])
    @login_required
    def api_timer_resume(class_id: int):
        """Resume a paused Meet / round countdown."""

        def run(_body):
            """Apply one staff JSON mutation for this class."""
            return school.game.resume_round_timer(class_id)

        return _staff_post(class_id, run)

    @app.route("/api/classes/<int:class_id>/game/timer/remaining", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/game/anticipation", methods=["POST"])
    @login_required
    def api_anticipation(class_id: int):
        """Signal the live overlay to run the pre-rounds anticipation sequence."""

        def run(_body):
            """Apply one staff JSON mutation for this class."""
            return school.game.start_anticipation(class_id)

        return _staff_post(class_id, run)

    @app.route("/api/classes/<int:class_id>/game/round", methods=["POST"])
    @login_required
    def api_round(class_id: int):
        """Start a scoring round."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            state = school.game.start_round(class_id, int(body.get("round") or 0))
            school.clear_session_warmups_for_class(class_id)
            return state

        return _staff_post(class_id, run)

    @app.route("/api/classes/<int:class_id>/game/score", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/game/add-student", methods=["POST"])
    @login_required
    def api_late(class_id: int):
        """Add a late student to a live game."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            return school.game.add_late_student(
                class_id,
                int(body.get("student_id") or 0),
                int(body.get("team_id") or 0),
            )

        return _staff_post(class_id, run)

    @app.route("/api/classes/<int:class_id>/game/end", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/students", methods=["POST"])
    @login_required
    def api_add_student(class_id: int):
        """Add a Codename (LLOVES) or last/first (legacy)."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
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

    @app.route("/api/classes/<int:class_id>/students/delete", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/sessions", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/sessions/delete", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/subtotals", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/subtotals/rename", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/subtotals/delete", methods=["POST"])
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

    @app.route("/api/classes/<int:class_id>/stat-window", methods=["POST"])
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

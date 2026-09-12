#!/usr/bin/env python3
"""LLOVES LMS — Flask entry point.

Usage:
    python3 lms/app.py
    # http://127.0.0.1:8787
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import sys
import threading
from datetime import date, timedelta
from html import escape as html_escape
from pathlib import Path
from typing import Any

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
    current_user,
    google_oauth_ready,
    it_required,
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
    build_celebration_board,
    celebration_candidates,
    set_featured_award,
)
from curriculum import seed_curriculum  # noqa: E402
from local_dev_seed import (  # noqa: E402
    local_dev_bind_host,
    local_dev_login_enabled,
    seed_local_dev_school,
)
from school_db import STAFF_2FA_MODE_LABELS, SchoolDB  # noqa: E402
from live_media import (  # noqa: E402
    DEFAULT_LIVE_MEDIA_STEM,
    DEFAULT_LIVE_MEDIA_TITLE,
    DEFAULT_LIVE_MEDIA_URL,
    cons_catalog,
    live_media_url_swap_allowed,
)
from live_teacher_state import LAYOUT_PRESETS, default_teacher_state  # noqa: E402
from live_prompt_feedback import public_feedback_fragment  # noqa: E402
from meet_team import is_meet_team_payload  # noqa: E402
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
        finish_install_detail,
        instances_payload,
        status_payload,
    )
    from syllabus_seed import seed_syllabus_from_due_dates
except ImportError:
    from lms.pack_progress import (
        annotate_offering_pack,
        finish_install_detail,
        instances_payload,
        status_payload,
    )
    from lms.syllabus_seed import seed_syllabus_from_due_dates
from modules import (  # noqa: E402
    IMSCC_MAX_BYTES,
    ensure_unpacked,
    explain_pack_exception,
    install_uploaded_module_pack,
    module_pack_root,
    pack_ui_state,
    placeholder_html,
    read_pack_status,
    resolve_module_pack,
    rewrite_wiki_html,
    wrap_page,
    write_pack_status,
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
    bind_student_session,
    character_choices,
    clear_rejoin_cookie,
    clear_student_session_keys,
    mood_choices,
    next_student_endpoint,
    rejoin_token_from_cookie,
    resolve_student_live_context,
    set_rejoin_cookie,
    student_url_with_token,
    visit_token_from_request,
)
import syllabus as syllabus_mod  # noqa: E402
from schedule import TIME_OPTIONS, format_live_schedule_line, wizard_defaults  # noqa: E402

MGS_TEMPLATES = MGS_PATH / "templates"
MGS_STATIC = MGS_PATH / "static"


def _optional_date(value: Any) -> date | None:
    """Parse YYYY-MM-DD from a JSON field, or None if blank."""
    if value is None or value == "":
        return None
    return date.fromisoformat(str(value)[:10])


logger = logging.getLogger(__name__)


def _json_error(exc: BaseException):
    """Map domain exceptions to JSON API errors."""
    if isinstance(exc, KeyError):
        return jsonify({"ok": False, "error": f"Not found: {exc.args[0]}"}), 404
    if isinstance(exc, FileNotFoundError):
        return jsonify({"ok": False, "error": str(exc)}), 404
    if isinstance(exc, ValueError):
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": False, "error": str(exc)}), 500


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


def _ready_library(school, cls: dict) -> tuple[int | None, str | None]:
    """Resolve a class's shared library id, backfilling components once.

    Modules and the component tabs read only from the database. Offerings
    created before the component store have no rows yet, so the first request
    unpacks the shared cartridge and ingests it; later requests skip both.

    Args:
        school: Open school database.
        cls: Enriched class row.

    Returns:
        ``(library_id, error_message)``. ``library_id`` is None when the
        course has no attached pack.
    """
    pack = _class_pack(school, cls)
    library_id = cls.get("library_id")
    if not library_id:
        return None, "Ask Admin to attach a module pack."
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
        body = rewrite_wiki_html(
            str(choice.get("html") or ""), code, files_root=files_root
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

    stem = rewrite_wiki_html(
        str(payload.get("stem_html") or ""), code, files_root=files_root
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
    questions = list_questions(school, int(library_id), int(bank_id))
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


def create_app(
    *,
    db_path: Path | None = None,
    data_dir: Path | None = None,
    testing: bool = False,
) -> Flask:
    """Build the LLOVES Flask application.

    Args:
        db_path: Override sqlite path (tests).
        data_dir: Override game-show uploads/logs directory.
        testing: Disable CSRF-adjacent secure cookies; used by tests.
    """
    app = Flask(
        __name__,
        template_folder=str(LMS_DIR / "templates"),
        static_folder=None,
    )
    secret = os.getenv("FLASK_SECRET_KEY", "lloves-dev-secret-change-me")
    app.secret_key = secret
    app.config["TESTING"] = testing
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

    db_file = Path(db_path or os.getenv("LLOVES_DB") or DEFAULT_DB_PATH)
    store = Path(data_dir or os.getenv("LLOVES_DATA_DIR") or db_file.parent)
    it_email = (os.getenv("IT_EMAILS") or DEFAULT_IT_EMAIL).split(",")[0].strip()
    school = SchoolDB(
        db_file,
        store,
        it_email=it_email or DEFAULT_IT_EMAIL,
    )
    app.config["SCHOOL_DB"] = school
    app.config["DATA_DIR"] = store
    seed_curriculum(school)
    if local_dev_login_enabled() and not secure:
        try:
            seed_local_dev_school(school)
        except Exception:  # noqa: BLE001 - empty school is worse than a log
            app.logger.exception("LOCAL_DEV_LOGIN seed failed")

    register_auth_routes(app)
    _register_pages(app, school)
    _register_game_api(app, school)
    app.jinja_env.globals["live_schedule"] = format_live_schedule_line

    @app.context_processor
    def inject_public_brand() -> dict[str, str]:
        """Expose ALC display names and McKenzian credit on every template."""
        return public_brand()

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
            "connect-src 'self' https://accounts.google.com; "
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

    def _assign_schedule_from_form() -> tuple[str, str]:
        """Read and validate live days/time from an Admin assign form.

        Returns:
            ``(live_days, live_time)`` wizard presets.

        Raises:
            ValueError: Missing or invalid schedule fields.
        """
        live_days = (request.form.get("live_days") or "").strip()
        live_time = (request.form.get("live_time") or "").strip()
        if not live_days or not live_time:
            raise ValueError("Choose live-class days and start time.")
        # Validation happens in set_offering_schedule.
        return live_days, live_time

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
        """Unpack, ingest, and seed syllabus for a stored cartridge.

        Args:
            offering_id: ``course_offerings.id``.
            stored: Path to ``course.imscc`` already on disk.
            dest_root: Shared ``libraries/<id>/`` folder (not an instance pack).
        """
        try:
            status = install_uploaded_module_pack(stored, dest_root)
            if not status.get("ok"):
                return
            school.set_offering_imscc(int(offering_id), str(stored))
            offering = school.get_offering(int(offering_id))
            library_id = offering.get("library_id")
            unpacked = Path(dest_root) / "unpacked"
            if library_id and unpacked.is_dir():
                ingest_detail = "Loading modules, pages, and assessments…"
                write_pack_status(
                    dest_root,
                    stage="ingest",
                    detail=ingest_detail,
                )
                with _pack_heartbeat(dest_root, "ingest", ingest_detail):
                    ensure_ingested(
                        school,
                        school.data_dir,
                        int(library_id),
                        unpacked,
                        force=True,
                    )
                _discard_unpacked(unpacked)
                write_pack_status(
                    dest_root,
                    stage="syllabus",
                    detail="Placing tests, quizzes, and assignments on the syllabus…",
                )
                try:
                    seed_syllabus_from_due_dates(
                        school, offering, data_dir=school.data_dir
                    )
                except Exception:  # noqa: BLE001 — pack still usable without seed
                    logger.exception("Syllabus seed from due dates failed")
            write_pack_status(
                dest_root,
                stage="done",
                detail=finish_install_detail(school, library_id),
            )
        except Exception as exc:  # noqa: BLE001 — surface on the status poll
            logger.exception("Background module-pack install failed")
            message = explain_pack_exception(exc)
            write_pack_status(
                dest_root, stage="error", detail=message, error=message
            )

    @contextlib.contextmanager
    def _pack_heartbeat(dest_root: Path, stage: str, detail: str):
        """Keep ``install_status.json`` fresh while a long stage runs.

        Args:
            dest_root: Shared library folder.
            stage: Busy stage name (``ingest``, …).
            detail: Teacher-facing line to leave unchanged.
        """
        stop = threading.Event()

        def beat() -> None:
            """Rewrite pack status until the surrounding stage finishes."""
            while not stop.wait(8.0):
                write_pack_status(dest_root, stage=stage, detail=detail)

        worker = threading.Thread(
            target=beat, daemon=True, name="pack-heartbeat"
        )
        worker.start()
        try:
            yield
        finally:
            stop.set()

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

    @app.route("/")
    def landing():
        """Public ALC logo, then Teacher / Student / Admin entry.

        Celebrations live on the same page at ``/#celebrations`` (coming soon).
        While a live session is active, the httpOnly rejoin cookie skips
        code+name and resumes the unfinished join step (mood → character →
        home). It must not skip the required avatar pick.
        """
        token = rejoin_token_from_cookie()
        if token:
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
        return render_template(
            "landing.html",
            **landing_kwargs(one_tap_auto=False),
        )

    @app.route("/calc")
    def calc_awards_redirect():
        """Old /calc bookmark — send people to the ALC hash route."""
        return redirect("/#celebrations")

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
        """Fly / DNS liveness — no auth."""
        return jsonify({"ok": True, "school": SCHOOL_SHORT})

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
        )
        resp = make_response(html)
        resp.set_cookie("lloves_seen", "1", max_age=86400 * 400, samesite="Lax")
        return resp

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
            if uploaded is None and not school.base_layer_available(code):
                return _assign_error(
                    f"A module pack (.imscc) is required for {code} — no template exists yet."
                )
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
            live_days, live_time = _assign_schedule_from_form()
            offering = school.set_offering_schedule(
                int(offering["id"]),
                live_days=live_days,
                live_time=live_time,
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

    @app.route("/it/offerings/<int:offering_id>/module-pack/status")
    @it_required
    def it_module_pack_status(offering_id: int):
        """JSON progress for an IT library upload/unpack."""
        offering = school.get_offering(int(offering_id))
        return jsonify(status_payload(offering, _library_dest(offering)))

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
            if uploaded is None and not school.base_layer_available(code):
                return _assign_error(
                    f"A module pack (.imscc) is required for {code} — no template exists yet."
                )
            offering = school.assign_course(
                teacher_user_id=staff_id,
                ontario_code=code,
                copied_from_offering_id=copied_from,
                library_id=library_id,
                new_section=True,
                slides_template_id=theme_template,
                style_guide_file_id=theme_style,
            )
            live_days, live_time = _assign_schedule_from_form()
            offering = school.set_offering_schedule(
                int(offering["id"]),
                live_days=live_days,
                live_time=live_time,
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
            classes = school.list_staff_classes(int(user["id"]), int(active["id"]))
        from flask import make_response

        active_live_session = school.get_active_live_session_for_teacher(
            int(user["id"])
        )
        html = render_template(
            "staff/home.html",
            user=user,
            active=active,
            offerings=offerings,
            classes=classes,
            active_live_session=active_live_session,
            nav_courses=_staff_nav_courses(int(user["id"])),
            time_options=list(TIME_OPTIONS),
            school_name=SCHOOL_NAME,
            celebration_candidates=celebration_candidates(
                school, int(user["id"])
            ),
            celebration_award=build_celebration_board(school)["cards"][0],
        )
        resp = make_response(html)
        resp.set_cookie("lloves_seen", "1", max_age=86400 * 400, samesite="Lax")
        return resp

    @app.route("/staff/bots")
    @staff_required
    def staff_bots():
        """Staff-only Grok bots showcase (Module Engineer and later cards)."""
        user = current_user()
        assert user is not None
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
            return jsonify({"ok": False, "error": "Pick a Codename."}), 400
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
            return jsonify({"ok": False, "error": str(exc)}), 400
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
        try:
            dash = school.game.replace_codename_roster(
                class_id,
                [str(n) for n in names],
                sort="az",
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except KeyError:
            abort(404)
        dash["class"] = school.enrich_class(dash["class"])
        return jsonify({"ok": True, "class": dash["class"], "students": dash.get("students")})

    @app.route("/staff/class/<int:class_id>/run-live", methods=["POST"])
    @staff_required
    def staff_run_live_class(class_id: int):
        """Mint a live session (one per teacher) and open Mark Attendance."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            abort(403)
        try:
            live_session = school.start_live_class_session(class_id, int(user["id"]))
        except ValueError as exc:
            return render_template("forbidden.html", message=str(exc)), 400
        return redirect(
            url_for(
                "staff_course",
                class_id=class_id,
                tab="live",
                live_session_id=live_session["id"],
            )
        )

    @app.route("/staff/class/<int:class_id>/end-live", methods=["POST"])
    @staff_required
    def staff_end_live_class(class_id: int):
        """Save and End Class: persist A&P, then wipe the SID.

        Staff may only terminate a class they own (IT in-tenant included via
        ``teacher_owns_class``), and only their one active session
        (``class_id`` must match ``get_active_live_session_for_teacher``).
        Dashboard cards always post this route with the active session's
        ``class_id``, even when that class is not the card being rendered.

        After a save, staff land on Attendance & Participation so the
        new class-day column is visible.
        """
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            abort(403)
        active = school.get_active_live_session_for_teacher(int(user["id"]))
        if active is None or int(active["class_id"]) != int(class_id):
            return redirect(url_for("staff_home"))
        school.finish_live_class(int(class_id), persist=True)
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
        """Quit: keep attendance, discard participation, wipe the SID.

        Same ownership rules as Save and End Class. Attendance always
        persists. Participation, meet taps, and live QH are discarded.
        """
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            abort(403)
        active = school.get_active_live_session_for_teacher(int(user["id"]))
        if active is None or int(active["class_id"]) != int(class_id):
            return redirect(url_for("staff_home"))
        school.finish_live_class(int(class_id), persist=False)
        return redirect(url_for("staff_home"))

    @app.route("/staff/class/<int:class_id>")
    @staff_required
    def staff_course(class_id: int):
        """Course dashboard: Modules, Attendance & Participation, Grades."""
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
        tab = (request.args.get("tab") or "modules").strip().lower()
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
        if ap_view not in {"attendance", "participation"}:
            ap_view = "attendance"
        portfolio_view = (request.args.get("view") or "build").strip().lower()
        if portfolio_view not in {"build", "marking"}:
            portfolio_view = "build"
        if tab not in {
            "modules",
            "pages",
            "assignments",
            "quizzes",
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
            tab = "modules"
        if tab == "portfolio" and not show_portfolio_tab:
            tab = "modules"
        pack_error = session.pop("pack_error", None)
        pack_ok = request.args.get("pack") == "ok"
        live_step = (request.args.get("step") or "").strip().lower()
        if live_step not in {"", "att", "gamify", "teams", "names", "rounds", "score", "live"}:
            live_step = ""
        active_live = school.get_active_live_session_for_class(class_id)
        live_session_id = request.args.get("live_session_id") or ""
        if not live_session_id and active_live is not None:
            live_session_id = str(active_live["id"])
        live_session_code = (
            str(active_live.get("session_code") or "") if active_live is not None else ""
        )
        _audit(
            "student.record.view",
            "class",
            resource_id=class_id,
            detail={"tab": tab},
        )
        return render_template(
            "staff/course.html",
            user=user,
            cls=cls,
            offering=offering,
            expectations=expectations,
            nav_courses=_staff_nav_courses(int(user["id"])),
            tab=tab,
            ap_view=ap_view,
            portfolio_view=portfolio_view,
            take_attendance=request.args.get("take") == "1",
            log_participation=request.args.get("participate") == "1",
            run_live=request.args.get("run") == "1",
            live_session_id=live_session_id,
            live_session_code=live_session_code,
            live_step=live_step,
            school_name=SCHOOL_NAME,
            show_module_pack_upload=False,
            pack_error=pack_error,
            pack_ok=pack_ok,
            show_portfolio_tab=show_portfolio_tab,
            allow_media_url_swap=live_media_url_swap_allowed(
                testing=bool(app.config.get("TESTING"))
            ),
        )

    @app.route("/staff/class/<int:class_id>/module-pack", methods=["POST"])
    @staff_required
    def staff_upload_module_pack(class_id: int):
        """Staff cannot upload packs; IT attaches a shared library."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
            abort(403)
        message = "Ask Admin to attach a module pack."
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
        return jsonify(status_payload(offering, _library_dest(offering)))

    @app.route("/staff/class/<int:class_id>/module-pack/status")
    @staff_required
    def staff_module_pack_status(class_id: int):
        """Staff pack-status for a class the teacher owns."""
        _, offering = _owned_offering_for_pack(class_id)
        return jsonify(status_payload(offering, _library_dest(offering)))

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
                    "message": error or "Ask Admin to attach a module pack.",
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
            return jsonify(
                {
                    "ok": True,
                    "empty": True,
                    "message": error or "Ask Admin to attach a module pack.",
                    "items": [],
                }
            )
        items = lister(school, int(library_id))
        return jsonify(
            {
                "ok": True,
                "empty": not items,
                "kind": kind,
                "items": items,
                "counts": library_counts(school, int(library_id)),
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
        return jsonify(
            {
                "ok": True,
                "questions": list_questions(school, int(library_id), int(bank_id)),
            }
        )

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
        return send_from_directory(
            target.parent, target.name, download_name=Path(rel).name
        )

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

    def _student_identity() -> tuple[dict[str, Any], int, int] | None:
        """Return offering and roster ids when the student session is bound.

        Returns:
            ``(offering, class_id, student_id)`` or ``None`` if incomplete.
        """
        ctx = _student_live_context()
        if ctx is None:
            return None
        return ctx["offering"], ctx["class_id"], ctx.get("student_id")

    def _ended_student_response(*, as_json: bool = False):
        """Clear student keys + rejoin cookie and send the student to landing."""
        clear_student_session_keys(session)
        if as_json:
            resp = jsonify(
                {
                    "ok": True,
                    "status": "ended",
                    "redirect": url_for("landing"),
                }
            )
        else:
            resp = redirect(url_for("landing"))
        clear_rejoin_cookie(resp)
        return resp

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
            attendee = school.touch_live_session_heartbeat(token)
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
        resolved = school.resolve_student_visit_token(token, allow_left=True)
        if resolved is None:
            resp = redirect(url_for("landing"))
            clear_student_session_keys(session)
            clear_rejoin_cookie(resp)
            return resp
        attendee = school.touch_live_session_heartbeat(token)
        if attendee is None:
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
        if request.method == "POST":
            character = (request.form.get("character") or "").strip()
            try:
                school.game.set_character(class_id, student_id, character)
            except ValueError as exc:
                return render_template(
                    "student/character.html",
                    offering=offering,
                    characters=character_choices(),
                    error=str(exc),
                    school_name=SCHOOL_NAME,
                    visit_token=visit_token,
                    codename=str(ctx.get("codename") or ""),
                )
            return _student_advance()
        return render_template(
            "student/character.html",
            offering=offering,
            characters=character_choices(),
            error=None,
            school_name=SCHOOL_NAME,
            visit_token=visit_token,
            codename=str(ctx.get("codename") or ""),
        )

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
        if unmatched:
            payload = school.guest_student_live_payload(
                codename=str(ctx.get("codename") or ""),
                class_id=class_id,
            )
        else:
            payload = school.game.student_live_payload(class_id, int(student_id))
        prompt_frag = school.student_live_prompt_payload(
            live_session_id,
            int(student_id) if student_id not in (None, "") else None,
            participant_uuid=pid,
        )
        payload.update(prompt_frag)
        payload["active_media"] = school.live_session_active_media_payload(
            live_session_id
        )
        payload["teacher_state"] = school.live_session_teacher_state_payload(
            live_session_id
        )
        return render_template(
            "student/home.html",
            offering=offering,
            payload=payload,
            school_name=SCHOOL_NAME,
            visit_token=visit_token,
            codename=str(ctx.get("codename") or payload.get("me", {}).get("codename") or ""),
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

    @app.route("/api/student/state")
    @student_required
    def student_state():
        """Live-class payload for the bound student (or a pick redirect)."""
        denied = _require_active_live_attendee(as_json=True)
        if denied is not None:
            return denied
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
        live_session_id = int(
            (ctx or {}).get("live_session_id") or session["student_live_session_id"]
        )
        pid = str((ctx or {}).get("participant_uuid") or "")
        unmatched = bool((ctx or {}).get("unmatched")) or student_id in (None, "")
        if unmatched:
            payload = school.guest_student_live_payload(
                codename=str((ctx or {}).get("codename") or session.get("student_codename") or ""),
                class_id=int(class_id),
            )
        else:
            payload = school.game.student_live_payload(class_id, int(student_id))
        payload.update(
            school.student_live_prompt_payload(
                live_session_id,
                int(student_id) if student_id not in (None, "") else None,
                participant_uuid=pid,
            )
        )
        payload["active_media"] = school.live_session_active_media_payload(
            live_session_id
        )
        payload["teacher_state"] = school.live_session_teacher_state_payload(
            live_session_id
        )
        payload["display_time"] = school.live_session_display_time(int(class_id))
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

    @app.route("/api/student/live-prompt/response", methods=["POST"])
    @student_required
    def api_student_live_prompt_response():
        """Submit a live-prompt response; return instant text feedback when keyed."""
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
        active = school.get_active_live_prompt(live_session_id)
        if active is None or active.get("kind") == "idle":
            return jsonify({"ok": False, "error": "No active prompt."}), 409
        if school.live_session_mc_poll_closed(live_session_id):
            return jsonify(
                {"ok": False, "error": "Poll is closed.", "poll_closed": True}
            ), 409
        body = request.get_json(silent=True) or {}
        response = body.get("response")
        if not isinstance(response, dict):
            response = {k: body[k] for k in body if k != "prompt_id"}
        prompt_id = int(body.get("prompt_id") or active["id"])
        if prompt_id != int(active["id"]):
            return jsonify({"ok": False, "error": "Prompt is no longer active."}), 409
        meet_payload = active.get("payload") or {}
        if is_meet_team_payload(meet_payload):
            choice = ""
            if isinstance(response, dict):
                choice = str(
                    response.get("choice") or response.get("text") or ""
                ).strip()
            recorded = school.record_meet_chain_pick(
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
            if recorded is None and not choice:
                return jsonify({"ok": False, "error": "Meet is not live."}), 409
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
        # Gradebook auto-insert stays stubbed for the slides-plugin branch.
        if student_id not in (None, ""):
            school.apply_prompt_score_to_participation(
                class_id,
                int(student_id),
                0.0,
                prompt_id=prompt_id,
                label=str(active.get("kind") or "prompt"),
            )
        my_response = {
            "response": saved.get("response") or {},
            "awarded_points": saved.get("awarded_points"),
            "updated_at": saved.get("updated_at"),
        }
        fragment = public_feedback_fragment(
            active.get("payload") or {}, my_response["response"]
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
        return jsonify(body)

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

    @app.route("/api/live-sessions/<int:session_id>/state")
    @login_required
    def api_live_session_state(session_id: int):
        """Return code, attendee count, roster, and phase for one live session."""
        session_row = school.get_live_session(session_id)
        if session_row is None:
            return jsonify({"ok": False, "error": "Session not found"}), 404
        if not _can_view_live_session(session_row):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        try:
            state = school.get_live_session_state(session_id)
        except KeyError:
            return jsonify({"ok": False, "error": "Session not found"}), 404
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
        return jsonify({"ok": True, "prompt": prompt})

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
        C2/C3 clear media and do not seed ``active_media_json``. CONS table
        checkboxes in the Real-slice iframe are disabled until the next step.
        """
        session_row = school.get_live_session(session_id)
        if session_row is None:
            return jsonify({"ok": False, "error": "Session not found"}), 404
        if not _can_view_live_session(session_row):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        if request.method == "GET":
            live_slot = school.session_live_slot(session_id)
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
                            "url": None,
                            "challenge": "C2",
                            "seed": False,
                            "note": "C2 does not seed active_media_json.",
                        },
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
        try:
            media = school.set_live_session_active_media(session_id, **kwargs)
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
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
        if session_row is None:
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
        body = request.get_json(silent=True) or {}
        kwargs: dict[str, Any] = {}
        for key in (
            "advance",
            "stage",
            "round",
            "round_flags",
            "teams_mode",
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
            "unlocks",
            "mc_ui",
            "live_slot",
            "text_ride",
        ):
            if key in body:
                kwargs[key] = body.get(key)
        try:
            state = school.set_live_session_teacher_state(session_id, **kwargs)
        except (KeyError, ValueError) as exc:
            return _json_error(exc)
        payload = {"ok": True, "teacher_state": state}
        if "assign" in body or "advance" in body:
            try:
                payload["game"] = school.game.game_state(int(session_row["class_id"]))
            except Exception:
                pass
        return jsonify(payload)

    def _dashboard_payload(class_id: int, sort: str) -> dict[str, Any]:
        """Spreadsheet JSON with offering metadata attached."""
        payload = school.game.dashboard(class_id, sort)
        payload["class"] = school.enrich_class(payload["class"])
        return payload

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
        try:
            return jsonify(school.game.scoreboard(scoped))
        except Exception as exc:  # noqa: BLE001
            return _json_error(exc)

    @app.route("/api/sessions/<int:session_id>/log")
    @login_required
    def api_session_log(session_id: int):
        """Plain-text game log."""
        try:
            path = school.game.session_log_path(session_id)
            return path.read_text(encoding="utf-8"), 200, {"Content-Type": "text/plain; charset=utf-8"}
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
            state = school.game.begin_game(class_id, meeting_date=meeting)
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
            session_row = school.start_live_class_session(
                class_id, int(user["id"])
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
            return jsonify(
                {
                    "ok": True,
                    "preview": preview,
                    "presentation_id": (deck or {}).get("presentation_id"),
                    "presentation_url": (deck or {}).get("presentation_url"),
                    "has_presentation": bool((deck or {}).get("presentation_id")),
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
            return jsonify(school.attendance_week_grid(class_id, sort=sort))
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
            return jsonify(school.mood_week_grid(class_id, sort=sort))
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
            return school.clear_attendance_day(class_id, chosen, sort=sort)

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
            return jsonify(school.participation_week_grid(class_id, sort=sort))
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
            return jsonify(school.gradebook_for_class(class_id, sort=sort))
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
                book = school.gradebook_for_class(class_id, sort="az")
                return jsonify({"ok": True, "scheme": book.get("scheme")})
            except Exception as exc:  # noqa: BLE001
                return _json_error(exc)

        def run(body):
            """Persist the staff grading scheme and return the full book."""
            saved = school.set_grade_scheme(class_id, body or {})
            book = school.gradebook_for_class(class_id, sort="az")
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
            roster = {
                int(row["id"])
                for row in school.game.dashboard(class_id, sort="az").get("students") or []
            }
            if student_id not in roster:
                raise ValueError("Student is not on this class roster")
            complete = body.get("complete")
            if isinstance(complete, str):
                complete = complete.strip().lower() in {"1", "true", "yes", "on"}
            saved = school.set_module_reflection(
                class_id, student_id, module_number, bool(complete)
            )
            book = school.gradebook_for_class(class_id, sort="az")
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
        """End the live game; tear down live-class session unless preserved."""

        def run(body):
            """Apply End Game then optionally invalidate the ephemeral join code."""
            chosen = _optional_date(body.get("meeting_date"))
            result = school.game.end_game(class_id, meeting_date=chosen)
            preserve = bool((body or {}).get("preserve_live_session"))
            if not preserve:
                ended = school.end_active_live_sessions_for_class(class_id)
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
            return _dashboard_payload_from_add(class_id, body)

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
            dash = school.game.delete_student(
                class_id,
                int(body.get("student_id") or 0),
                sort=str(body.get("sort") or "az"),
            )
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
    port = int(os.getenv("PORT") or "8787")
    host = local_dev_bind_host()
    application = create_app()
    catalog_n = len(application.config["SCHOOL_DB"].list_ontario_courses())
    print(f"LLOVES LMS: http://{host}:{port}/")
    print(f"Database: {application.config['SCHOOL_DB'].db_path}")
    print(f"Ontario catalog: {catalog_n} courses")
    application.run(host=host, port=port, debug=os.getenv("FLASK_DEBUG") == "1")

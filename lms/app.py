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

from flask import (  # noqa: E402
    Flask,
    abort,
    jsonify,
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
    staff_or_student_scoreboard,
    staff_required,
    student_required,
)
from curriculum import seed_curriculum  # noqa: E402
from school_db import SchoolDB  # noqa: E402
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
    list_assignments,
    list_pages,
    list_question_banks,
    list_questions,
    list_quizzes,
    outline_nav,
    outline_raw_modules,
    quiz_questions,
)
from modules import (  # noqa: E402
    IMSCC_MAX_BYTES,
    ensure_unpacked,
    install_uploaded_module_pack,
    module_pack_root,
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
)
import syllabus as syllabus_mod  # noqa: E402
from schedule import TIME_OPTIONS, wizard_defaults  # noqa: E402

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
    if secure:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
        app.config["PREFERRED_URL_SCHEME"] = "https"
        if (os.getenv("LOCAL_DEV_LOGIN") or "").strip().lower() in {
            "1",
            "true",
            "yes",
        }:
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

    register_auth_routes(app)
    _register_pages(app, school)
    _register_game_api(app, school)
    return app


def _register_pages(app: Flask, school: SchoolDB) -> None:
    """Landing, IT, staff, student, static, and module/syllabus routes."""

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
        """Unpack and inventory a stored cartridge into a shared library folder.

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
            write_pack_status(
                dest_root, stage="done", detail="Module pack installed."
            )
        except Exception as exc:  # noqa: BLE001 — surface on the status poll
            logger.exception("Background module-pack install failed")
            write_pack_status(
                dest_root, stage="error", detail=str(exc), error=str(exc)
            )

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
        """Reject oversized IMSCC uploads with a clear size message."""
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
            return send_from_directory(LMS_DIR / "static", filename)
        return send_from_directory(MGS_STATIC, filename)

    @app.route("/")
    def landing():
        """Public landing: Staff Login, IT Login, Student Code."""
        returning = request.cookies.get("lloves_seen") == "1"
        return render_template(
            "landing.html",
            **landing_kwargs(one_tap_auto=returning),
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
        offerings = school.list_offerings(semester_id=active["id"] if active else None)
        all_offerings = school.list_offerings()
        from flask import make_response

        html = render_template(
            "it/dashboard.html",
            user=user,
            semesters=semesters,
            semesters_all=semesters,
            active=active,
            staff=school.list_staff(include_archived=False),
            offerings=offerings,
            all_offerings=all_offerings,
            courses=school.search_ontario_courses("", limit=300),
            school_name=SCHOOL_NAME,
            only_live_class_days=school.only_live_class_days(),
        )
        resp = make_response(html)
        resp.set_cookie("lloves_seen", "1", max_age=86400 * 400, samesite="Lax")
        return resp

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
                }
            )
        body = request.get_json(silent=True) or {}
        enabled = body.get("only_live_class_days")
        if enabled is None:
            return jsonify({"ok": False, "error": "only_live_class_days is required"}), 400
        flag = bool(enabled) if not isinstance(enabled, str) else enabled.strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        school.set_only_live_class_days(flag)
        return jsonify({"ok": True, "only_live_class_days": school.only_live_class_days()})

    @app.route("/it/staff", methods=["POST"])
    @it_required
    def it_register_staff():
        """Allowlist a personal Google email as staff."""
        try:
            school.register_staff(
                request.form.get("email") or "",
                request.form.get("display_name") or None,
            )
        except ValueError as exc:
            return render_template(
                "forbidden.html", message=str(exc)
            ), 400
        return redirect(url_for("it_dashboard"))

    @app.route("/it/courses")
    @it_required
    def it_search_courses():
        """JSON autocomplete for Ontario course codes."""
        q = request.args.get("q") or ""
        return jsonify({"ok": True, "courses": school.search_ontario_courses(q, limit=80)})

    @app.route("/it/instances")
    @it_required
    def it_list_instances():
        """JSON: prior offerings of a course code (any semester, any teacher)."""
        code = (request.args.get("code") or "").strip().upper()
        if not code:
            return jsonify({"ok": True, "instances": []})
        return jsonify({"ok": True, "instances": school.list_prior_instances(code)})

    def _upload_file() -> Any:
        """Return the IMSCC ``FileStorage`` when the IT form sent a real file."""
        uploaded = request.files.get("module_pack")
        if uploaded is None or not getattr(uploaded, "filename", None):
            return None
        if not str(uploaded.filename).strip():
            return None
        return uploaded

    @app.route("/it/offerings", methods=["POST"])
    @it_required
    def it_assign_course():
        """Assign a catalog course; optional IMSCC becomes a new shared library.

        Assigning a code a teacher already holds adds another section
        (``MCF3M-2``) rather than silently reusing the first one.
        """
        teacher_id = int(request.form.get("teacher_user_id") or 0)
        code = (request.form.get("ontario_code") or "").strip().upper()
        raw_base = (request.form.get("copied_from_offering_id") or "").strip()
        copied_from = int(raw_base) if raw_base else None
        uploaded = _upload_file()
        library_id = None
        dest_root = None
        stored = None
        try:
            if uploaded is not None:
                created = school.store_upload_library(code, uploaded)
                library_id = int(created["library"]["id"])
                dest_root = created["dest_root"]
                stored = created["stored"]
            if uploaded is None and not school.base_layer_available(code):
                return render_template(
                    "forbidden.html",
                    message=f"A module pack (.imscc) is required for {code} — no template exists yet.",
                ), 400
            offering = school.assign_course(
                teacher_user_id=teacher_id,
                ontario_code=code,
                copied_from_offering_id=copied_from,
                library_id=library_id,
                new_section=True,
            )
            live_days, live_time = _assign_schedule_from_form()
            offering = school.set_offering_schedule(
                int(offering["id"]),
                live_days=live_days,
                live_time=live_time,
            )
        except (ValueError, KeyError) as exc:
            return render_template("forbidden.html", message=str(exc)), 400
        if stored is not None and dest_root is not None:
            if app.config.get("TESTING"):
                _install_module_pack_job(int(offering["id"]), stored, dest_root)
            else:
                worker = threading.Thread(
                    target=_install_module_pack_job,
                    args=(int(offering["id"]), stored, dest_root),
                    daemon=True,
                )
                worker.start()
        return redirect(url_for("it_dashboard"))

    @app.route("/it/offerings/<int:offering_id>/module-pack", methods=["POST"])
    @it_required
    def it_upload_module_pack(offering_id: int):
        """Attach a new shared library to an existing offering (IT only)."""
        offering = school.get_offering(int(offering_id))
        uploaded = _upload_file()
        json_client = _wants_json()
        if uploaded is None:
            message = "Choose a .imscc module pack to upload."
            if json_client:
                return jsonify({"ok": False, "error": message}), 400
            return render_template("forbidden.html", message=message), 400
        try:
            created = school.store_upload_library(
                str(offering["ontario_code"]),
                uploaded,
                offering_id=int(offering["id"]),
            )
        except ValueError as exc:
            if json_client:
                return jsonify({"ok": False, "error": str(exc)}), 400
            return render_template("forbidden.html", message=str(exc)), 400
        dest_root = created["dest_root"]
        stored = created["stored"]
        done_url = url_for("it_dashboard", pack="ok")
        background = json_client and not app.config.get("TESTING")
        if background:
            worker = threading.Thread(
                target=_install_module_pack_job,
                args=(int(offering["id"]), stored, dest_root),
                daemon=True,
            )
            worker.start()
            return jsonify({"ok": True, "installing": True, "redirect": done_url})
        _install_module_pack_job(int(offering["id"]), stored, dest_root)
        if json_client:
            return jsonify({"ok": True, "installing": False, "redirect": done_url})
        return redirect(done_url)

    @app.route("/it/offerings/<int:offering_id>/module-pack/status")
    @it_required
    def it_module_pack_status(offering_id: int):
        """JSON progress for an IT library upload/unpack."""
        offering = school.get_offering(int(offering_id))
        return jsonify(read_pack_status(_library_dest(offering)))

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
        actor_id = int(actor["id"]) if actor else 0
        try:
            school.deactivate_staff(staff_id, actor_id)
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
        try:
            school.reactivate_staff(staff_id)
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
        if staff_member is None or staff_member.get("role") != "staff":
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
            if uploaded is not None:
                created = school.store_upload_library(code, uploaded)
                library_id = int(created["library"]["id"])
                dest_root = created["dest_root"]
                stored = created["stored"]
            if uploaded is None and not school.base_layer_available(code):
                return render_template(
                    "forbidden.html",
                    message=f"A module pack (.imscc) is required for {code} — no template exists yet.",
                ), 400
            offering = school.assign_course(
                teacher_user_id=staff_id,
                ontario_code=code,
                copied_from_offering_id=copied_from,
                library_id=library_id,
                new_section=True,
            )
            live_days, live_time = _assign_schedule_from_form()
            offering = school.set_offering_schedule(
                int(offering["id"]),
                live_days=live_days,
                live_time=live_time,
            )
        except (ValueError, KeyError) as exc:
            return render_template("forbidden.html", message=str(exc)), 400
        if stored is not None and dest_root is not None:
            if app.config.get("TESTING"):
                _install_module_pack_job(int(offering["id"]), stored, dest_root)
            else:
                worker = threading.Thread(
                    target=_install_module_pack_job,
                    args=(int(offering["id"]), stored, dest_root),
                    daemon=True,
                )
                worker.start()
        return redirect(url_for("it_dashboard", tab="staff"))

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
            classes = school.list_staff_classes(int(user["id"]), int(active["id"]))
        from flask import make_response

        html = render_template(
            "staff/home.html",
            user=user,
            active=active,
            offerings=offerings,
            classes=classes,
            nav_courses=_staff_nav_courses(int(user["id"])),
            time_options=list(TIME_OPTIONS),
            school_name=SCHOOL_NAME,
        )
        resp = make_response(html)
        resp.set_cookie("lloves_seen", "1", max_age=86400 * 400, samesite="Lax")
        return resp

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

    @app.route("/staff/class/<int:class_id>")
    @staff_required
    def staff_course(class_id: int):
        """Course dashboard: Modules, Attendance & Participation, Grades."""
        user = current_user()
        assert user is not None
        if not school.teacher_owns_class(int(user["id"]), class_id):
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
        if ap_view not in {"attendance", "participation"}:
            ap_view = "attendance"
        if tab not in {
            "modules",
            "pages",
            "assignments",
            "quizzes",
            "question-banks",
            "syllabus",
            "ap",
            "gradebook",
        }:
            tab = "modules"
        pack_error = session.pop("pack_error", None)
        pack_ok = request.args.get("pack") == "ok"
        return render_template(
            "staff/course.html",
            user=user,
            cls=cls,
            offering=offering,
            expectations=expectations,
            nav_courses=_staff_nav_courses(int(user["id"])),
            tab=tab,
            ap_view=ap_view,
            take_attendance=request.args.get("take") == "1",
            school_name=SCHOOL_NAME,
            show_module_pack_upload=False,
            pack_error=pack_error,
            pack_ok=pack_ok,
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

    @app.route("/staff/class/<int:class_id>/module-pack/status")
    @staff_required
    def staff_module_pack_status(class_id: int):
        """Staff pack-status endpoint kept as a no-op idle payload."""
        _, offering = _owned_offering_for_pack(class_id)
        return jsonify(read_pack_status(_library_dest(offering)))

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

    @app.route("/student/waiting")
    @student_required
    def student_waiting():
        """Waiting room when the teacher has not started a live game."""
        offering = school.get_offering(int(session["student_offering_id"]))
        return render_template(
            "student/waiting.html",
            offering=offering,
            school_name=SCHOOL_NAME,
        )

    @app.route("/student/game")
    @student_required
    def student_game():
        """Student scoreboard-first live game (no scoring controls)."""
        if not session.get("student_class_id"):
            return redirect(url_for("student_waiting"))
        return send_from_directory(MGS_TEMPLATES, "scoreboard.html")

    @app.route("/student/pick", methods=["GET", "POST"])
    @student_required
    def student_pick():
        """Disambiguate overlapping live sections by Codename or days/time."""
        code = session.get("student_live_code") or ""
        live = school.live_games_for_access_code(code)
        error = None
        if request.method == "POST":
            class_id = request.form.get("class_id")
            codename = request.form.get("codename") or ""
            if class_id:
                chosen = next((g for g in live if str(g["class_id"]) == str(class_id)), None)
                if chosen:
                    session["student_class_id"] = int(chosen["class_id"])
                    return redirect(url_for("student_game"))
                error = "That section is not live."
            elif codename.strip():
                try:
                    match = school.find_class_by_codename(code, codename)
                except ValueError as exc:
                    match = None
                    error = str(exc)
                if match:
                    session["student_class_id"] = int(match["id"])
                    return redirect(url_for("student_game"))
                error = error or "No live section matched that Codename."
        return render_template(
            "student/pick.html",
            live=live,
            classes=school.classes_for_access_code(code),
            error=error,
            school_name=SCHOOL_NAME,
        )

    @app.route("/api/student/state")
    @student_required
    def student_state():
        """Waiting-room poll: join when a live game appears."""
        code = session.get("student_live_code") or ""
        live = school.live_games_for_access_code(code)
        if session.get("student_class_id"):
            still = [g for g in live if int(g["class_id"]) == int(session["student_class_id"])]
            if still:
                return jsonify({"ok": True, "status": "live", "redirect": url_for("student_game")})
        if len(live) == 1:
            session["student_class_id"] = int(live[0]["class_id"])
            return jsonify({"ok": True, "status": "live", "redirect": url_for("student_game")})
        if len(live) > 1:
            return jsonify({"ok": True, "status": "pick", "redirect": url_for("student_pick")})
        return jsonify({"ok": True, "status": "waiting"})


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
        """Quit setup without starting."""
        return _staff_post(class_id, lambda _b: school.game.cancel_setup(class_id))

    @app.route("/api/classes/<int:class_id>/game/attendance", methods=["POST"])
    @login_required
    def api_attendance(class_id: int):
        """Save who is present and advance to teams (Log Participation)."""

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
        """Start individual participation scoring without teams/scoreboard."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            _apply_validated_meeting(class_id, body)
            ids = body.get("present_ids")
            state = school.game.game_state(class_id)
            status = str((state.get("game") or {}).get("status") or "")
            if ids is not None and status == "attendance":
                school.game.save_attendance(class_id, [int(x) for x in ids])
            return school.game.start_ungamified_live(class_id)

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

    @app.route("/api/classes/<int:class_id>/gradebook")
    @login_required
    def api_gradebook(class_id: int):
        """Weighted gradebook scaffold (Participation / Term / Exam)."""
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
        """Assign teams."""

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
        """Rename teams."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            names = body.get("teams") or []
            if not isinstance(names, list):
                raise ValueError("teams must be a list")
            return school.game.rename_teams(class_id, names)

        return _staff_post(class_id, run)

    @app.route("/api/classes/<int:class_id>/game/round", methods=["POST"])
    @login_required
    def api_round(class_id: int):
        """Start a scoring round."""
        return _staff_post(
            class_id,
            lambda body: school.game.start_round(class_id, int(body.get("round") or 0)),
        )

    @app.route("/api/classes/<int:class_id>/game/score", methods=["POST"])
    @login_required
    def api_score(class_id: int):
        """Award points — staff only."""

        def run(body):
            """Apply one staff JSON mutation for this class."""
            return school.game.award_points(
                class_id,
                kind=str(body.get("kind") or ""),
                target_id=int(body.get("id") or 0),
                amount=int(body.get("amount") or 0),
                team_rule=(str(body["team_rule"]) if body.get("team_rule") else None),
            )

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
        """End the live game."""
        return _staff_post(class_id, lambda _b: school.game.end_game(class_id))

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
    host = os.getenv("HOST") or "127.0.0.1"
    application = create_app()
    catalog_n = len(application.config["SCHOOL_DB"].list_ontario_courses())
    print(f"LLOVES LMS: http://{host}:{port}/")
    print(f"Database: {application.config['SCHOOL_DB'].db_path}")
    print(f"Ontario catalog: {catalog_n} courses")
    application.run(host=host, port=port, debug=os.getenv("FLASK_DEBUG") == "1")

"""Seed a sqlite content library from local ``module-lessons.json``.

Admin can assign a math course without an IMSCC when this outline exists.
Page bodies are empty AU-spine stubs, not legacy pack HTML.
"""

from __future__ import annotations

import json
import re
from html import escape as html_escape
from pathlib import Path
from typing import Any

try:
    from components import library_is_ingested
    from slide_builder import local_curriculum_course_dir
except ImportError:  # package-style import
    from lms.components import library_is_ingested
    from lms.slide_builder import local_curriculum_course_dir

AU_SPINE_HEADINGS = (
    "Minds-On",
    "Explore",
    "Examples",
    "Formative",
    "Practice",
    "Summary: Need to Know",
)

_GLUED_LESSON = re.compile(r"(?i)^Lesson(\d+):\s*")
_HAS_LESSON_WORD = re.compile(r"(?i)\bLesson\b")
_OUTLINE_PREFIX = re.compile(r"(?i)^Module\s+(\d+)\b")


def module_lessons_path(
    course_code: str,
    cache_root: Path | None = None,
) -> Path:
    """Return ``{cache}/{CODE}/module-lessons.json``.

    Args:
        course_code: Ontario course code.
        cache_root: Override of ``.local-data/curriculum`` (tests).
    """
    return local_curriculum_course_dir(course_code, cache_root) / "module-lessons.json"


def json_outline_available(
    course_code: str,
    cache_root: Path | None = None,
) -> bool:
    """Return True when a ``module-lessons.json`` file exists for the code.

    Args:
        course_code: Ontario course code.
        cache_root: Override of ``.local-data/curriculum`` (tests).
    """
    return module_lessons_path(course_code, cache_root).is_file()


def try_load_module_lessons(
    course_code: str,
    cache_root: Path | None = None,
) -> dict[str, Any] | None:
    """Parsed ``module-lessons.json`` when present and well-formed, else None.

    Does not raise on a missing file. Invalid JSON or a non-object payload
    returns None so callers (portfolio dropdown) can fall back to strand-map.

    Args:
        course_code: Ontario course code.
        cache_root: Override of ``.local-data/curriculum`` (tests).
    """
    path = module_lessons_path(course_code, cache_root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def normalize_outline_title(module_number: int, title: str) -> str:
    """Return ``Module {n}: {title}`` so syllabus/slides can match the module.

    Leaves an existing ``Module {n}`` prefix in place.

    Args:
        module_number: 1-based module index.
        title: Title from JSON (usually without the Module prefix).
    """
    text = str(title or "").strip() or f"Module {module_number}"
    matched = _OUTLINE_PREFIX.match(text)
    if matched and int(matched.group(1)) == int(module_number):
        return text
    return f"Module {module_number}: {text}"


def normalize_lesson_title(title: str, lesson_index: int) -> str:
    """Fix ``Lesson2:`` spacing and prefix titles that lack the Lesson keyword.

    Lesson Slides numbers pages whose titles contain ``Lesson``. MCF3M Module 7
    pages have no such word until prefixed ``Lesson 1:`` / ``2:`` / ``3:``.

    Args:
        title: Raw lesson string from JSON.
        lesson_index: 1-based index within the module.
    """
    text = str(title or "").strip()
    glued = _GLUED_LESSON.match(text)
    if glued:
        rest = text[glued.end() :].strip()
        text = f"Lesson {int(glued.group(1))}: {rest}".strip()
    if not _HAS_LESSON_WORD.search(text):
        text = f"Lesson {int(lesson_index)}: {text}".strip()
    return text


def au_spine_stub_html(title: str) -> str:
    """Return empty AU-spine page HTML for one lesson.

    Args:
        title: Normalized lesson title shown as the page heading.
    """
    heading = html_escape(title)
    sections = "\n".join(
        f"<h2>{html_escape(name)}</h2>\n<p></p>" for name in AU_SPINE_HEADINGS
    )
    return f"<h1>{heading}</h1>\n{sections}\n"


def load_module_lessons(
    course_code: str,
    cache_root: Path | None = None,
) -> dict[str, Any]:
    """Load and validate ``module-lessons.json`` for seeding.

    Args:
        course_code: Ontario course code the JSON must match.
        cache_root: Override of ``.local-data/curriculum`` (tests).

    Returns:
        Payload with ``modules`` sorted 1…N.

    Raises:
        ValueError: Missing file, course-code mismatch, or non-consecutive
            module numbers.
    """
    code = re.sub(r"[^A-Z0-9]", "", (course_code or "").upper())
    payload = try_load_module_lessons(code, cache_root=cache_root)
    if payload is None:
        raise ValueError(f"No module-lessons.json outline for {code or course_code}")
    listed = str(payload.get("course_code") or "").strip().upper()
    if listed != code:
        raise ValueError(
            f"module-lessons.json course_code {listed or '(missing)'} "
            f"does not match {code}"
        )
    raw_modules = payload.get("modules") or []
    if not isinstance(raw_modules, list) or not raw_modules:
        raise ValueError(f"{code} module-lessons.json has no modules")
    numbered: list[dict[str, Any]] = []
    for row in raw_modules:
        if not isinstance(row, dict):
            raise ValueError(f"{code} module-lessons.json has a non-object module")
        try:
            num = int(row.get("module") or row.get("module_number") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{code} module-lessons.json has a bad module number") from exc
        if num < 1:
            raise ValueError(f"{code} module-lessons.json modules must start at 1")
        numbered.append(row)
    nums = [int(row.get("module") or row.get("module_number") or 0) for row in numbered]
    expected = list(range(1, len(nums) + 1))
    if sorted(nums) != expected:
        raise ValueError(
            f"{code} modules must be consecutive 1…{len(nums)}, got {sorted(nums)}"
        )
    numbered.sort(key=lambda row: int(row.get("module") or row.get("module_number") or 0))
    out = dict(payload)
    out["modules"] = numbered
    return out


def seed_json_library(
    db: Any,
    course_code: str,
    *,
    cache_root: Path | None = None,
) -> dict[str, Any]:
    """Create an ``origin=json`` library with outlines, pages, and items.

    Idempotent: if this code already has an ingested library, return
    ``latest_library_for_code`` instead of inserting a second copy.

    Args:
        db: School database with ``create_library`` and component tables.
        course_code: Ontario course code.
        cache_root: Override of ``.local-data/curriculum`` (tests).

    Returns:
        The ``content_libraries`` row (existing ingested, or newly seeded).
    """
    code = re.sub(r"[^A-Z0-9]", "", (course_code or "").upper())
    existing = db.latest_library_for_code(code)
    if existing and library_is_ingested(db, int(existing["id"])):
        return existing
    payload = load_module_lessons(code, cache_root=cache_root)
    library = db.create_library(code, origin="json", source_path=None)
    library_id = int(library["id"])
    _insert_outline_components(db, library_id, payload["modules"])
    row = db.get_library(library_id)
    assert row is not None
    return row


def _insert_outline_components(
    db: Any,
    library_id: int,
    modules: list[dict[str, Any]],
) -> None:
    """Write module_outlines, html pages, and module_items for a JSON library.

    Args:
        db: School database.
        library_id: ``content_libraries.id``.
        modules: Validated ``modules[]`` rows in order 1…N.
    """
    lock = getattr(db, "_lock", None)
    ctx = lock if lock is not None else _nullcontext()
    with ctx:
        for row in modules:
            module_n = int(row.get("module") or row.get("module_number") or 0)
            outline_title = normalize_outline_title(
                module_n, str(row.get("title") or "")
            )
            cur = db.conn.execute(
                """
                INSERT INTO module_outlines (
                    library_id, import_key, title, position, created_at
                ) VALUES (?, ?, ?, ?, datetime('now'))
                """,
                (int(library_id), f"m{module_n}", outline_title, module_n),
            )
            outline_id = int(cur.lastrowid)
            lessons = row.get("lessons") or []
            if not isinstance(lessons, list):
                lessons = []
            for index, raw_title in enumerate(lessons, start=1):
                title = normalize_lesson_title(str(raw_title or ""), index)
                html = au_spine_stub_html(title)
                page_cur = db.conn.execute(
                    """
                    INSERT INTO pages (
                        library_id, import_key, kind, title, html_text, created_at
                    ) VALUES (?, ?, 'html', ?, ?, datetime('now'))
                    """,
                    (int(library_id), f"m{module_n}-p{index}", title, html),
                )
                page_id = int(page_cur.lastrowid)
                db.conn.execute(
                    """
                    INSERT INTO module_items (
                        outline_id, import_key, title, position, component_type,
                        component_id, source_type, created_at
                    ) VALUES (?, ?, ?, ?, 'page', ?, 'html', datetime('now'))
                    """,
                    (
                        outline_id,
                        f"m{module_n}-i{index}",
                        title,
                        index,
                        page_id,
                    ),
                )
        db.conn.commit()


def _nullcontext() -> Any:
    """Return a no-op context manager when the database has no lock."""
    from contextlib import nullcontext

    return nullcontext()

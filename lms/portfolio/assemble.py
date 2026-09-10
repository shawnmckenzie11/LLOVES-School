"""Assemble Build-card context and generate module portfolio JSON/HTML/Docs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from live_class_slides import _load_module_strand_map
from portfolio.html_pages import render_doc_html, render_toggle_html
from portfolio.lenses import draft_lenses, representations_for_module
from portfolio.store import (
    load_core_questions,
    load_exemplar,
    load_generated,
    save_generated,
)
from portfolio.useful_words import useful_word_union
from slide_builder import local_curriculum_course_dir


def _module_row(payload: dict[str, Any] | None, module_number: int) -> dict[str, Any] | None:
    """One modules[] entry for ``module_number``."""
    if not isinstance(payload, dict):
        return None
    for row in payload.get("modules") or []:
        if not isinstance(row, dict):
            continue
        if int(row.get("module_number") or 0) == int(module_number):
            return row
    return None


def _overall_codes(payload: dict[str, Any], module_number: int, limit: int = 3) -> list[dict[str, str]]:
    """Up to ``limit`` overalls whose module_numbers include this module."""
    out: list[dict[str, str]] = []
    for row in payload.get("specifics") or []:
        if not isinstance(row, dict) or not row.get("overall"):
            continue
        nums = row.get("module_numbers") or []
        if int(module_number) not in [int(n) for n in nums if str(n).isdigit() or isinstance(n, int)]:
            continue
        out.append(
            {
                "code": str(row.get("code") or ""),
                "statement": str(row.get("statement") or ""),
            }
        )
        if len(out) >= limit:
            break
    return out


def _specifics_for_module(payload: dict[str, Any], module_number: int) -> list[dict[str, str]]:
    """Specific statements for this module (not overalls)."""
    out: list[dict[str, str]] = []
    for row in payload.get("specifics") or []:
        if not isinstance(row, dict) or row.get("overall"):
            continue
        nums = row.get("module_numbers") or []
        if int(module_number) not in [int(n) for n in nums if str(n).isdigit() or isinstance(n, int)]:
            continue
        out.append(
            {
                "code": str(row.get("code") or ""),
                "statement": str(row.get("statement") or ""),
            }
        )
    return out


def list_modules(course_code: str, cache_root: Path | None = None) -> list[dict[str, Any]]:
    """Module dropdown rows from ``module-lessons.json``, else strand-map.

    Pack-free names stay in lockstep with the seeded syllabus outline.
    Strand letters still come from the strand map when that file exists.
    ``assemble_context`` keeps using the strand map for overalls/specifics.

    Args:
        course_code: Ontario code.
        cache_root: Curriculum cache override.
    """
    code = str(course_code or "").upper()
    map_payload = _load_module_strand_map(code, cache_root=cache_root) or {}
    map_by_num: dict[int, dict[str, Any]] = {}
    for item in map_payload.get("modules") or []:
        if not isinstance(item, dict):
            continue
        num = int(item.get("module_number") or 0)
        if num < 1:
            continue
        map_by_num[num] = item

    try:
        from outline_from_json import try_load_module_lessons
    except ImportError:
        from lms.outline_from_json import try_load_module_lessons

    lessons = try_load_module_lessons(code, cache_root=cache_root)
    if lessons:
        rows: list[dict[str, Any]] = []
        for item in lessons.get("modules") or []:
            if not isinstance(item, dict):
                continue
            try:
                num = int(item.get("module") or item.get("module_number") or 0)
            except (TypeError, ValueError):
                continue
            if num < 1:
                continue
            mapped = map_by_num.get(num) or {}
            rows.append(
                {
                    "module_number": num,
                    "title": str(item.get("title") or mapped.get("title") or f"Module {num}"),
                    "strand": str(mapped.get("strand") or ""),
                    "strand_name": str(mapped.get("strand_name") or ""),
                }
            )
        if rows:
            return rows

    rows = []
    for item in map_payload.get("modules") or []:
        if not isinstance(item, dict):
            continue
        num = int(item.get("module_number") or 0)
        if num < 1:
            continue
        rows.append(
            {
                "module_number": num,
                "title": str(item.get("title") or f"Module {num}"),
                "strand": str(item.get("strand") or ""),
                "strand_name": str(item.get("strand_name") or ""),
            }
        )
    return rows


def assemble_context(
    *,
    course_code: str,
    module_number: int,
    data_dir: Path | None = None,
    cache_root: Path | None = None,
) -> dict[str, Any]:
    """Build card payload: cores, map context, useful words, lens draft.

    Args:
        course_code: Ontario code for the open class.
        module_number: Selected module.
        data_dir: LMS data root (glossary files).
        cache_root: ``.local-data/curriculum`` override.
    """
    code = str(course_code or "").upper()
    payload = _load_module_strand_map(code, cache_root=cache_root) or {}
    module_row = _module_row(payload, int(module_number))
    if module_row is None:
        raise ValueError(f"No module {module_number} in {code} strand map")
    overalls = _overall_codes(payload, int(module_number))
    specifics = _specifics_for_module(payload, int(module_number))
    exp_codes = [str(c) for c in (module_row.get("expectation_codes") or [])]
    statements = [row["statement"] for row in specifics]
    words = useful_word_union(
        course_code=code,
        expectation_codes=exp_codes + [row["code"] for row in overalls],
        statements=statements,
        data_dir=data_dir,
    )
    locked = load_exemplar(code, int(module_number))
    generated = load_generated(code, int(module_number), cache_root=cache_root)
    if locked is not None:
        portfolio = dict(locked)
        lens_source = "exemplar"
    elif generated is not None:
        portfolio = dict(generated)
        lens_source = "generated"
    else:
        lenses = draft_lenses(module_number=int(module_number), module_row=module_row)
        reps = representations_for_module(module_row)
        questions = {}
        for key, lens in lenses.items():
            questions[key] = {
                "lens": lens,
                "useful_words": words,
                "evidence": "Use your screenshot + one detail from your quick reflection.",
            }
        portfolio = {
            "course_code": code,
            "module_number": int(module_number),
            "locked": False,
            "title": f"Module {int(module_number)} Portfolio",
            "hero_subtitle": f"Module {int(module_number)} · {module_row.get('title') or ''}".strip(" ·"),
            "hero_note": "3 questions.\nUse your group work.\nShow your own thinking.",
            "evidence_placeholders": [
                "Lesson 1 screenshot",
                "Lesson 2 screenshot",
                "Lesson 3 screenshot",
            ],
            "intro": "Start with the course question. Tap Module lens only when you want a more specific version.",
            "reasoning_focus": [
                "Connect representations",
                "Justify relationships",
                "Reflect and transfer",
            ],
            "strand": module_row.get("strand") or "",
            "strand_name": module_row.get("strand_name") or "",
            "overalls": [row["code"] for row in overalls],
            "representations": reps,
            "questions": questions,
        }
        lens_source = "draft"
    # Keep teacher-edited useful words on saved JSON; otherwise show union.
    for key, q in (portfolio.get("questions") or {}).items():
        if isinstance(q, dict) and not q.get("useful_words"):
            q["useful_words"] = words
    return {
        "course_code": code,
        "module_number": int(module_number),
        "modules": list_modules(code, cache_root=cache_root),
        "strand": str(module_row.get("strand") or ""),
        "strand_name": str(module_row.get("strand_name") or ""),
        "module_title": str(module_row.get("title") or ""),
        "overalls": overalls,
        "specifics": specifics,
        "useful_words": words,
        "cores": load_core_questions(),
        "portfolio": portfolio,
        "lens_source": lens_source,
        "locked_exemplar": bool(locked and locked.get("locked")),
        "curriculum_dir": str(local_curriculum_course_dir(code, cache_root=cache_root)),
    }


def apply_teacher_edits(base: dict[str, Any], edits: dict[str, Any] | None) -> dict[str, Any]:
    """Merge teacher lens / useful-word edits into a portfolio document.

    Args:
        base: Assembled portfolio JSON.
        edits: Optional ``questions`` map with lens and useful_words.
    """
    out = dict(base)
    questions = dict(out.get("questions") or {})
    incoming = (edits or {}).get("questions") if isinstance(edits, dict) else None
    if isinstance(incoming, dict):
        for key, row in incoming.items():
            if not isinstance(row, dict):
                continue
            current = dict(questions.get(key) or {})
            if "lens" in row:
                current["lens"] = str(row.get("lens") or "")
            if "useful_words" in row:
                words = row.get("useful_words")
                if isinstance(words, str):
                    current["useful_words"] = [w.strip() for w in words.split("·") if w.strip()]
                elif isinstance(words, list):
                    current["useful_words"] = [str(w).strip() for w in words if str(w).strip()]
            questions[key] = current
    out["questions"] = questions
    if isinstance(edits, dict) and edits.get("hero_subtitle"):
        out["hero_subtitle"] = str(edits["hero_subtitle"])
    return out


def generate_portfolio(
    *,
    course_code: str,
    module_number: int,
    edits: dict[str, Any] | None = None,
    data_dir: Path | None = None,
    cache_root: Path | None = None,
) -> dict[str, Any]:
    """Write ``M{n}.json`` and return HTML strings.

    Locked M1 exemplars are not overwritten in the repo; a working copy is
    still written under ``.local-data/curriculum/{CODE}/portfolios/``.

    Args:
        course_code: Ontario code.
        module_number: Module index.
        edits: Teacher lens edits.
        data_dir: LMS data root.
        cache_root: Curriculum cache override.
    """
    context = assemble_context(
        course_code=course_code,
        module_number=module_number,
        data_dir=data_dir,
        cache_root=cache_root,
    )
    portfolio = apply_teacher_edits(context["portfolio"], edits)
    if int(module_number) == 1 and context.get("locked_exemplar"):
        # Keep locked wording unless the teacher explicitly edited lenses.
        if edits:
            portfolio["locked"] = True
    path = save_generated(
        portfolio,
        course_code=course_code,
        module_number=module_number,
        cache_root=cache_root,
    )
    return {
        "ok": True,
        "path": str(path),
        "portfolio": portfolio,
        "html": render_toggle_html(portfolio),
        "doc_html": render_doc_html(portfolio),
        "filename": f"{str(course_code).upper()}-M{int(module_number)}-portfolio.html",
        "doc_filename": f"{str(course_code).upper()}-M{int(module_number)}-portfolio.doc",
        "doc_title": "Module 1 Portfolio Assignment"
        if int(module_number) == 1
        else f"Module {int(module_number)} Portfolio Assignment",
    }

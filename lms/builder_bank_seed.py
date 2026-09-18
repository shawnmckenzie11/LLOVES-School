"""Seed LMS module banks from content-builder catalogue MC items."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from question_math import graph_image_for_builder_item

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BUILDER_BANKS = _REPO_ROOT / "content-builder" / "catalogue" / "banks"
_STATIC_GRAPHS = Path(__file__).resolve().parent / "static" / "bank-graphs"
_MC_SPLIT_RE = re.compile(r"\s+([A-D])\)\s+")
_MC_ANSWER_RE = re.compile(r"^[A-D]$", re.IGNORECASE)


def _now() -> str:
    """Return an ISO timestamp for sqlite inserts."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_builder_mc_stem(stem: str) -> tuple[str, list[str]] | None:
    """Split a builder ``stem_student`` into question stem and A–D options.

    Args:
        stem: Student-facing stem that may embed ``A) … B) …`` options.

    Returns:
        ``(question_stem, options)`` or ``None`` when parsing fails.
    """
    text = str(stem or "").replace("\n", " ").strip()
    if not text:
        return None
    parts = _MC_SPLIT_RE.split(text)
    if len(parts) < 3:
        return None
    question = parts[0].strip()
    options: list[str] = []
    index = 1
    while index + 1 < len(parts):
        letter = parts[index].strip().upper()
        body = parts[index + 1].strip()
        if letter not in {"A", "B", "C", "D"} or not body:
            return None
        options.append(body)
        index += 2
    if len(options) < 2:
        return None
    return question, options


def builder_item_to_ingest_payload(item: dict[str, Any]) -> dict[str, Any] | None:
    """Convert one content-builder bank item into an ingest MC payload.

    Args:
        item: Parsed ``bank-item.v1`` JSON document.

    Returns:
        Ingest ``payload_json`` shape, or ``None`` when the item is not MC.
    """
    answer = str(item.get("answer") or "").strip().upper()
    if not _MC_ANSWER_RE.match(answer):
        return None
    parsed = parse_builder_mc_stem(str(item.get("stem_student") or ""))
    if parsed is None:
        return None
    stem, options = parsed
    correct_index = ord(answer) - ord("A")
    if correct_index < 0 or correct_index >= len(options):
        return None
    choices = []
    correct_ids: list[str] = []
    for index, option in enumerate(options):
        choice_id = chr(ord("a") + index)
        is_correct = index == correct_index
        choices.append(
            {
                "id": choice_id,
                "html": option,
                "correct": is_correct,
            }
        )
        if is_correct:
            correct_ids.append(choice_id)
    image_url = graph_image_for_builder_item(
        str(item.get("id") or ""),
        stem,
        process_tags=list(item.get("process_tags") or []),
    )
    payload: dict[str, Any] = {
        "stem_html": stem,
        "points_possible": 1.0,
        "choices": choices,
        "correct_ids": correct_ids,
        "builder_item_id": str(item.get("id") or ""),
    }
    if image_url:
        payload["image_url"] = image_url
    return payload


def _ensure_graph_assets() -> None:
    """Copy known graph SVG assets into ``lms/static/bank-graphs/``."""
    _STATIC_GRAPHS.mkdir(parents=True, exist_ok=True)
    source = (
        _REPO_ROOT
        / "content-builder"
        / "lessons"
        / "MCF3M"
        / "M4-L2-completing-the-square"
        / "media"
        / "cts-area-diagram-ex960.svg"
    )
    if source.is_file():
        shutil.copy2(source, _STATIC_GRAPHS / "mcf3m-m4-cts-area-diagram.svg")


def _write_default_graph_svgs() -> None:
    """Create lightweight parabola SVG placeholders when missing."""
    _STATIC_GRAPHS.mkdir(parents=True, exist_ok=True)
    grid = _STATIC_GRAPHS / "parabola-grid.svg"
    if not grid.is_file():
        grid.write_text(
            """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 220" role="img" aria-label="Coordinate grid with parabola">
  <rect width="320" height="220" fill="#fff"/>
  <line x1="40" y1="180" x2="300" y2="180" stroke="#334155" stroke-width="2"/>
  <line x1="160" y1="20" x2="160" y2="200" stroke="#334155" stroke-width="2"/>
  <path d="M 60 170 Q 160 20 260 170" fill="none" stroke="#0f766e" stroke-width="3"/>
</svg>
""",
            encoding="utf-8",
        )
    transformed = _STATIC_GRAPHS / "parabola-transformed.svg"
    if not transformed.is_file():
        transformed.write_text(
            """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 220" role="img" aria-label="Transformed parabola">
  <rect width="320" height="220" fill="#fff"/>
  <line x1="40" y1="180" x2="300" y2="180" stroke="#334155" stroke-width="2"/>
  <line x1="120" y1="20" x2="120" y2="200" stroke="#334155" stroke-width="2"/>
  <path d="M 70 170 Q 120 40 170 170" fill="none" stroke="#94a3b8" stroke-width="2" stroke-dasharray="6 4"/>
  <path d="M 90 150 Q 120 60 150 150" fill="none" stroke="#0f766e" stroke-width="3"/>
</svg>
""",
            encoding="utf-8",
        )


def iter_builder_mc_items(course_code: str, module_number: int) -> list[dict[str, Any]]:
    """Load importable MC builder items for one module.

    Args:
        course_code: Course code such as ``MCF3M``.
        module_number: One-based module index.

    Returns:
        List of ingest-ready payloads with ``title`` and ``import_key`` metadata.
    """
    module_dir = (
        _BUILDER_BANKS / str(course_code).upper() / f"M{int(module_number)}"
    )
    items_dir = module_dir / "items"
    if not items_dir.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(items_dir.glob("*.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        payload = builder_item_to_ingest_payload(item)
        if payload is None:
            continue
        rows.append(
            {
                "title": str(item.get("title") or item.get("id") or path.stem),
                "import_key": f"builder:{item.get('id') or path.stem}",
                "payload": payload,
            }
        )
    return rows


def seed_builder_module_bank(
    school: Any,
    *,
    library_id: int,
    course_code: str,
    module_number: int,
) -> dict[str, Any]:
    """Insert or refresh one module bank from content-builder catalogue MCs.

    Args:
        school: ``SchoolDB`` instance.
        library_id: Target ``content_libraries.id``.
        course_code: Course code such as ``MCF3M``.
        module_number: One-based module index.

    Returns:
        Summary with ``bank_id``, ``question_ids``, and ``count``.
    """
    _ensure_graph_assets()
    _write_default_graph_svgs()
    items = iter_builder_mc_items(course_code, module_number)
    bank_title = f"{course_code.upper()} Module {module_number} Builder Bank"
    bank_import_key = f"builder-bank:{course_code.upper()}:M{module_number}"
    with school._lock:
        existing = school.conn.execute(
            """
            SELECT id FROM question_banks
            WHERE library_id = ? AND import_key = ?
            """,
            (int(library_id), bank_import_key),
        ).fetchone()
        if existing is None:
            cur = school.conn.execute(
                """
                INSERT INTO question_banks (library_id, import_key, title, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (int(library_id), bank_import_key, bank_title, _now()),
            )
            bank_id = int(cur.lastrowid)
        else:
            bank_id = int(existing["id"])
        question_ids: list[int] = []
        for row in items:
            import_key = str(row["import_key"])
            payload = row["payload"]
            existing_q = school.conn.execute(
                """
                SELECT id FROM questions
                WHERE bank_id = ? AND import_key = ?
                """,
                (bank_id, import_key),
            ).fetchone()
            encoded = json.dumps(payload)
            if existing_q is None:
                cur = school.conn.execute(
                    """
                    INSERT INTO questions (
                        bank_id, import_key, item_type, title, payload_json, created_at
                    ) VALUES (?, ?, 'multiple_choice_question', ?, ?, ?)
                    """,
                    (
                        bank_id,
                        import_key,
                        str(row["title"]),
                        encoded,
                        _now(),
                    ),
                )
                question_ids.append(int(cur.lastrowid))
            else:
                school.conn.execute(
                    """
                    UPDATE questions
                    SET title = ?, payload_json = ?
                    WHERE id = ?
                    """,
                    (str(row["title"]), encoded, int(existing_q["id"])),
                )
                question_ids.append(int(existing_q["id"]))
        school.conn.commit()
    return {
        "bank_id": bank_id,
        "question_ids": question_ids,
        "count": len(question_ids),
        "module_number": int(module_number),
    }



def confirm_recommended_module_test_banks(
    school: Any,
    library_id: int,
    *,
    module_numbers: list[int] | None = None,
    extra_bank_ids_by_module: dict[int, list[int]] | None = None,
) -> list[dict[str, Any]]:
    """Link IMSCC module test MC banks (and optional extras) for live import.

    Args:
        school: ``SchoolDB`` instance.
        library_id: Target ``content_libraries.id``.
        module_numbers: One-based module indices; defaults to modules 1–8.
        extra_bank_ids_by_module: Additional bank ids to keep linked per module.

    Returns:
        Per-module summary rows with linked bank ids and importable MC totals.
    """
    modules = module_numbers or list(range(1, 9))
    extras = extra_bank_ids_by_module or {}
    linked: list[dict[str, Any]] = []
    for module_number in modules:
        recommended = school.recommended_module_test_banks(
            int(library_id), int(module_number)
        )
        recommended_ids = [int(row["bank_id"]) for row in recommended]
        confirmed = school.list_module_bank_links(int(library_id), int(module_number))
        confirmed_ids = [int(row["bank_id"]) for row in confirmed]
        merged_ids = list(
            dict.fromkeys(
                [
                    *confirmed_ids,
                    *recommended_ids,
                    *(int(bid) for bid in extras.get(int(module_number), [])),
                ]
            )
        )
        if not merged_ids:
            continue
        school.confirm_module_bank_links(
            int(library_id), int(module_number), merged_ids
        )
        search = school.search_module_bank_mcs(
            int(library_id), int(module_number), limit=500
        )
        linked.append(
            {
                "module_number": int(module_number),
                "bank_ids": merged_ids,
                "recommended_count": len(recommended_ids),
                "importable_mcs": int(search.get("total") or 0),
            }
        )
    return linked


def seed_mcf3m_builder_banks(school: Any, library_id: int) -> dict[str, Any]:
    """Seed MCF3M builder MC banks and link IMSCC module test pools for import.

    Args:
        school: ``SchoolDB`` instance.
        library_id: Attached ``content_libraries.id``.

    Returns:
        Summary dict with builder banks and per-module importable MC counts.
    """
    builder_modules = [1, 4]
    banks: list[dict[str, Any]] = []
    bank_ids: list[int] = []
    extra_by_module: dict[int, list[int]] = {}
    for module_number in builder_modules:
        summary = seed_builder_module_bank(
            school,
            library_id=int(library_id),
            course_code="MCF3M",
            module_number=module_number,
        )
        if summary["count"]:
            banks.append(summary)
            bank_id = int(summary["bank_id"])
            bank_ids.append(bank_id)
            extra_by_module.setdefault(int(module_number), []).append(bank_id)
    linked = confirm_recommended_module_test_banks(
        school,
        int(library_id),
        module_numbers=list(range(1, 9)),
        extra_bank_ids_by_module=extra_by_module,
    )
    return {
        "banks": banks,
        "bank_ids": bank_ids,
        "builder_modules": builder_modules,
        "linked_modules": linked,
    }

"""Local Import banks when the tip sqlite has no IMSCC cartridge.

Alc Import lists question banks ingested from the Admin IMSCC on Fly
``/data``. That cartridge is not in git. A fresh local library is an upload
with no module-test banks, so confirmed links are only Course Wide warmups.
Core Math hides those warmups, and Import looks empty (MCF3M Module 2
included).

This module fills that gap when ``LOCAL_DEV_LOGIN`` is on:

- content-builder catalogue MC items for every course/module directory
- constructed items in ``.local-data/curriculum/{CODE}/banks`` as open prompts

It does not copy Fly data, does not invent answer keys, and does not run in
production. Alc's extra IMSCC rows stay on alc until a cartridge is uploaded
locally. Courses whose curriculum cache files are empty stay empty here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

try:
    from bank_kinds import retag_module2_teaching_today
    from builder_bank_seed import seed_builder_module_bank
    from slide_builder import local_curriculum_course_dir
except ImportError:
    from lms.bank_kinds import retag_module2_teaching_today
    from lms.builder_bank_seed import seed_builder_module_bank
    from lms.slide_builder import local_curriculum_course_dir


def is_curriculum_open(payload: Any) -> bool:
    """True when a stored row is a constructed curriculum prompt.

    Args:
        payload: Parsed ``questions.payload_json``.
    """
    return isinstance(payload, dict) and bool(payload.get("curriculum_open"))


def normalize_curriculum_open(
    *,
    question_id: int,
    bank_id: int,
    title: str,
    payload: Any,
    bank_title: str = "",
) -> dict[str, Any] | None:
    """Turn one curriculum cache row into an importable open prompt.

    No answer key is invented. The live item is a poll so publish does not
    score it as multiple choice.

    Args:
        question_id: ``questions.id``.
        bank_id: ``question_banks.id``.
        title: Question title.
        payload: Parsed payload. Must have ``curriculum_open``.
        bank_title: Bank label copied onto the normalized row.

    Returns:
        Normalized item, or ``None`` when the stem is empty.
    """
    blob = payload if isinstance(payload, dict) else {}
    if not is_curriculum_open(blob):
        return None
    stem = str(blob.get("stem_html") or blob.get("text") or title or "").strip()
    if not stem:
        return None
    return {
        "type": "poll",
        "text": stem,
        "prompt": stem,
        "options": [],
        "choices": [],
        "points": 1,
        "curriculum_open": True,
        "response_mode": "individual",
        "publish_modes": ["individual"],
        "question_id": int(question_id),
        "bank_id": int(bank_id),
        "bank_title": str(bank_title or ""),
        "question_title": str(title or stem)[:80],
        "source_question_id": int(question_id),
        "source_bank_id": int(bank_id),
    }


def _catalogue_modules(course_code: str) -> list[int]:
    """Return module numbers that have a content-builder bank directory.

    Args:
        course_code: Ontario course code.
    """
    try:
        from builder_bank_seed import _BUILDER_BANKS
    except ImportError:
        from lms.builder_bank_seed import _BUILDER_BANKS

    root = _BUILDER_BANKS / str(course_code or "").strip().upper()
    if not root.is_dir():
        return []
    numbers: list[int] = []
    for path in sorted(root.glob("M*")):
        if not path.is_dir():
            continue
        suffix = path.name[1:]
        if suffix.isdigit() and 1 <= int(suffix) <= 8:
            numbers.append(int(suffix))
    return numbers


def _iter_curriculum_items(
    course_code: str, cache_root: Path | None
) -> list[tuple[int, dict[str, Any]]]:
    """Read constructed items from the local curriculum cache.

    Args:
        course_code: Ontario course code.
        cache_root: Override of ``.local-data/curriculum`` (tests).

    Returns:
        ``(module_number, item)`` pairs with a non-empty stem.
    """
    banks = local_curriculum_course_dir(course_code, cache_root) / "banks"
    if not banks.is_dir():
        return []
    found: list[tuple[int, dict[str, Any]]] = []
    for module_dir in sorted(banks.glob("M*")):
        if not module_dir.is_dir():
            continue
        suffix = module_dir.name[1:]
        if not suffix.isdigit():
            continue
        module_number = int(suffix)
        if not 1 <= module_number <= 8:
            continue
        for path in sorted(module_dir.glob("*/items.json")):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            raw_items = document.get("items") if isinstance(document, dict) else document
            if not isinstance(raw_items, list):
                continue
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                stem = str(item.get("stem") or "").strip()
                if stem:
                    found.append((module_number, item))
    return found


def _upsert_curriculum_bank(
    school: Any,
    *,
    library_id: int,
    course_code: str,
    module_number: int,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Insert or refresh one module's curriculum open prompts.

    Rows are stored as ``multiple_choice_question`` so the Import SQL
    filter can see them. ``curriculum_open`` tells search to skip the MC
    key requirement.

    Args:
        school: ``SchoolDB`` instance.
        library_id: ``content_libraries.id``.
        course_code: Ontario course code.
        module_number: One-based module index.
        items: Cache items for this module.

    Returns:
        Summary with ``bank_id`` and ``count``.
    """
    from datetime import datetime, timezone

    code = str(course_code or "").strip().upper()
    import_key = f"curriculum-bank:{code}:M{int(module_number)}"
    title = f"{code} Module {int(module_number)} curriculum bank"
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    question_ids: list[int] = []
    with school._lock:
        existing = school.conn.execute(
            """
            SELECT id FROM question_banks
            WHERE library_id = ? AND import_key = ?
            """,
            (int(library_id), import_key),
        ).fetchone()
        if existing is None:
            cur = school.conn.execute(
                """
                INSERT INTO question_banks (
                    library_id, import_key, title, settings_json, created_at
                ) VALUES (?, ?, ?, '{}', ?)
                """,
                (int(library_id), import_key, title, now),
            )
            bank_id = int(cur.lastrowid)
        else:
            bank_id = int(existing["id"])
            school.conn.execute(
                "UPDATE question_banks SET title = ? WHERE id = ?",
                (title, bank_id),
            )
        for item in items:
            smart_id = str(item.get("smart_id") or item.get("id") or "").strip()
            stem = str(item.get("stem") or "").strip()
            if not smart_id:
                smart_id = hashlib.sha256(stem.encode("utf-8")).hexdigest()[:16]
            q_key = f"curriculum:{code}:M{int(module_number)}:{smart_id}"
            payload = {
                "stem_html": stem,
                "text": stem,
                "curriculum_open": True,
                "points_possible": 1.0,
                "choices": [],
                "smart_id": smart_id,
            }
            encoded = json.dumps(payload)
            q_title = stem.replace("\n", " ")[:80] or smart_id
            found = school.conn.execute(
                """
                SELECT id FROM questions
                WHERE bank_id = ? AND import_key = ?
                """,
                (bank_id, q_key),
            ).fetchone()
            if found is None:
                cur = school.conn.execute(
                    """
                    INSERT INTO questions (
                        bank_id, import_key, item_type, title, payload_json, created_at
                    ) VALUES (?, ?, 'multiple_choice_question', ?, ?, ?)
                    """,
                    (bank_id, q_key, q_title, encoded, now),
                )
                question_ids.append(int(cur.lastrowid))
            else:
                school.conn.execute(
                    """
                    UPDATE questions
                    SET title = ?, payload_json = ?
                    WHERE id = ?
                    """,
                    (q_title, encoded, int(found["id"])),
                )
                question_ids.append(int(found["id"]))
        school.conn.commit()
    school._ensure_module_bank_link(int(library_id), int(module_number), bank_id)
    return {
        "bank_id": bank_id,
        "module_number": int(module_number),
        "count": len(question_ids),
    }


def seed_library_import_banks(
    school: Any,
    library_id: int,
    course_code: str,
    *,
    cache_root: Path | None = None,
) -> dict[str, Any]:
    """Link catalogue MCs and curriculum open prompts for one library.

    Safe to call from tests with ``cache_root``. Does not check
    ``LOCAL_DEV_LOGIN``; the local-dev wrapper does.

    Args:
        school: ``SchoolDB`` instance.
        library_id: ``content_libraries.id``.
        course_code: Ontario course code on the library.
        cache_root: Optional curriculum cache root.

    Returns:
        Counts for catalogue modules, curriculum questions, and retags.
    """
    code = str(course_code or "").strip().upper()
    catalogue: list[dict[str, Any]] = []
    for module_number in _catalogue_modules(code):
        summary = seed_builder_module_bank(
            school,
            library_id=int(library_id),
            course_code=code,
            module_number=int(module_number),
        )
        if int(summary.get("count") or 0):
            school._ensure_module_bank_link(
                int(library_id), int(module_number), int(summary["bank_id"])
            )
            catalogue.append(summary)
    grouped: dict[int, list[dict[str, Any]]] = {}
    for module_number, item in _iter_curriculum_items(code, cache_root):
        grouped.setdefault(int(module_number), []).append(item)
    curriculum: list[dict[str, Any]] = []
    for module_number, items in sorted(grouped.items()):
        curriculum.append(
            _upsert_curriculum_bank(
                school,
                library_id=int(library_id),
                course_code=code,
                module_number=int(module_number),
                items=items,
            )
        )
    retagged = retag_module2_teaching_today(school, int(library_id))
    return {
        "course_code": code,
        "library_id": int(library_id),
        "catalogue": catalogue,
        "curriculum": curriculum,
        "curriculum_questions": sum(int(row["count"]) for row in curriculum),
        "teaching_today_retagged": int(retagged),
    }


def seed_local_question_banks(school: Any, *, cache_root: Path | None = None) -> dict[str, Any]:
    """Populate Import banks for every local library when local-dev is on.

    Args:
        school: ``SchoolDB`` instance.
        cache_root: Optional curriculum cache root.

    Returns:
        Per-library summaries, or a skipped marker when local-dev is off.
    """
    try:
        from local_dev_seed import local_dev_login_enabled
    except ImportError:
        from lms.local_dev_seed import local_dev_login_enabled

    if not local_dev_login_enabled():
        return {"skipped": True, "reason": "LOCAL_DEV_LOGIN is off"}
    rows = school.conn.execute(
        "SELECT id, ontario_code FROM content_libraries"
    ).fetchall()
    libraries = [
        seed_library_import_banks(
            school,
            int(row["id"]),
            str(row["ontario_code"] or ""),
            cache_root=cache_root,
        )
        for row in rows
    ]
    return {"skipped": False, "libraries": libraries}

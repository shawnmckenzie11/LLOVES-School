"""MCK-79: each module's top Content Questions for Run Live Class Import.

A module's **Content Questions** are the content pack's own questions in
that module's linked banks: the default Core Math mix the Import picker
already lists (untagged and contest rows). Warmups, Custom (stored
``standard``) and the staff-authored bank are left out, because they are
not pack content.

**Top 6** is the existing Import order (linked banks by title, then pack
ingest order), after the same duplicate filter. The pack carries no rank
field, so nothing is re-ranked here. A module with fewer than six shows
what it has.

Reads go through ``search_module_bank_mcs`` so warmup, overlay and
duplicate rules are not forked. Imports go through
``import_mc_to_class_playlist``, the path Import already uses.
"""

from __future__ import annotations

from typing import Any

try:
    from bank_mc_normalize import parse_module_token
    from course_warmup_seed import COURSE_WIDE_WARMUP_BANK_KEY
    from live_class_packs import live_class_registry
except ImportError:  # ``python3 lms/app.py`` package import
    from lms.bank_mc_normalize import parse_module_token
    from lms.course_warmup_seed import COURSE_WIDE_WARMUP_BANK_KEY
    from lms.live_class_packs import live_class_registry

CONTENT_QUESTIONS_PER_MODULE = 6
# Banks that are not content-pack questions.
NON_PACK_BANK_KEYS = ("staff-authored", COURSE_WIDE_WARMUP_BANK_KEY)
# Eight modules × six questions.
MAX_CONTENT_PICKS = 48


def selectable_live_modules(course: Any) -> list[str]:
    """Return the Run Live Class module tokens that have a bank scope.

    Args:
        course: Ontario code from the class offering.

    Returns:
        ``M1``–``M8`` tokens in the order the Module select lists them.
    """
    modules = live_class_registry(course).get("modules") or []
    out: list[str] = []
    for token in modules:
        number = parse_module_token(token)
        if number is None:
            continue
        key = f"M{number}"
        if key not in out:
            out.append(key)
    return out


def _non_pack_bank_ids(school: Any, library_id: int) -> set[int]:
    """Ids of the staff-authored and Course Wide warmup banks."""
    placeholders = ",".join("?" for _ in NON_PACK_BANK_KEYS)
    with school._lock:
        rows = school.conn.execute(
            f"""
            SELECT id FROM question_banks
            WHERE library_id = ? AND import_key IN ({placeholders})
            """,
            (int(library_id), *NON_PACK_BANK_KEYS),
        ).fetchall()
    return {int(row["id"]) for row in rows}


def link_recommended_banks_if_unlinked(
    school: Any,
    library_id: int,
    module_number: int,
    *,
    skip_bank_ids: set[int] | None = None,
) -> bool:
    """Link a module's recommended ``Module N Test`` banks when it has no pack bank.

    Import from bank already links these for the class's own module when
    the picker opens. Here it only runs when no content-pack bank is linked
    (a staff-authored or Course Wide warmup link does not count). Existing
    links are kept, so a teacher's own choices never change.

    Args:
        school: ``SchoolDB``.
        library_id: ``content_libraries.id``.
        module_number: One-based module index.
        skip_bank_ids: Non-pack bank ids, when the caller already has them.

    Returns:
        True when links were written.
    """
    skip = (
        skip_bank_ids
        if skip_bank_ids is not None
        else _non_pack_bank_ids(school, int(library_id))
    )
    linked = [
        int(row["bank_id"])
        for row in school.list_module_bank_links(int(library_id), int(module_number))
    ]
    if any(bank_id not in skip for bank_id in linked):
        return False
    recommended = school.recommended_module_test_banks(
        int(library_id), int(module_number)
    )
    ids = [int(row["bank_id"]) for row in recommended]
    if not ids:
        return False
    school.confirm_module_bank_links(
        int(library_id), int(module_number), [*linked, *ids]
    )
    return True


def module_content_questions(
    school: Any,
    library_id: int,
    module_number: int,
    *,
    class_id: int | None = None,
    limit: int = CONTENT_QUESTIONS_PER_MODULE,
    skip_bank_ids: set[int] | None = None,
) -> dict[str, Any]:
    """Return one module's top Content Questions in Import order.

    Args:
        school: ``SchoolDB``.
        library_id: ``content_libraries.id``.
        module_number: One-based module index.
        class_id: Class id for image URL resolution.
        limit: How many to keep (6).
        skip_bank_ids: Non-pack bank ids, when the caller already has them.

    Returns:
        ``module``, ``module_number``, ``label``, ``items`` (each tagged with
        ``module`` and a 1-based ``content_rank``), ``available`` (pack
        content count before the cap) and ``linked`` (a pack bank is linked).
    """
    number = int(module_number)
    key = f"M{number}"
    skip = (
        skip_bank_ids
        if skip_bank_ids is not None
        else _non_pack_bank_ids(school, int(library_id))
    )
    linked = any(
        int(row["bank_id"]) not in skip
        for row in school.list_module_bank_links(int(library_id), number)
    )
    result = school.search_module_bank_mcs(
        int(library_id), number, "", limit=500, class_id=class_id, kind=""
    )
    content = [
        row
        for row in result.get("items") or []
        if int(row.get("bank_id") or 0) not in skip
        and int(row.get("question_id") or 0) > 0
    ]
    items: list[dict[str, Any]] = []
    for rank, row in enumerate(content[: max(0, int(limit))], start=1):
        item = dict(row)
        item["module"] = key
        item["content_rank"] = rank
        items.append(item)
    return {
        "module": key,
        "module_number": number,
        "label": f"Module {number}",
        "items": items,
        "available": len(content),
        "linked": linked,
    }


def content_questions_by_module(
    school: Any,
    library_id: int,
    modules: list[str],
    *,
    class_id: int | None = None,
    link_unlinked: bool = True,
) -> list[dict[str, Any]]:
    """Top Content Questions for every selectable module, grouped by module.

    Args:
        school: ``SchoolDB``.
        library_id: ``content_libraries.id``.
        modules: ``M1``–``M8`` tokens from ``selectable_live_modules``.
        class_id: Class id for image URL resolution.
        link_unlinked: Link recommended test banks for a module with none.

    Returns:
        One group per module, in Module-select order.
    """
    skip = _non_pack_bank_ids(school, int(library_id))
    groups: list[dict[str, Any]] = []
    for token in modules:
        number = parse_module_token(token)
        if number is None:
            continue
        if link_unlinked:
            link_recommended_banks_if_unlinked(
                school, int(library_id), number, skip_bank_ids=skip
            )
        groups.append(
            module_content_questions(
                school,
                int(library_id),
                number,
                class_id=class_id,
                skip_bank_ids=skip,
            )
        )
    return groups


def clean_content_picks(raw: Any, modules: list[str]) -> list[tuple[int, str]]:
    """Validate posted picks into ``(question_id, module)`` pairs.

    Args:
        raw: ``picks`` list of ``{question_id, module}``.
        modules: Selectable module tokens.

    Returns:
        De-duplicated picks in posted order.

    Raises:
        ValueError: Empty, too many, or a malformed pick.
    """
    if not isinstance(raw, list) or not raw:
        raise ValueError("Pick at least one Content Question.")
    if len(raw) > MAX_CONTENT_PICKS:
        raise ValueError(f"Pick at most {MAX_CONTENT_PICKS} Content Questions.")
    allowed = set(modules)
    picks: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for row in raw:
        if not isinstance(row, dict):
            raise ValueError("Each pick needs question_id and module.")
        try:
            question_id = int(row.get("question_id") or row.get("questionId") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("Each pick needs question_id and module.") from exc
        number = parse_module_token(row.get("module"))
        key = f"M{number}" if number is not None else ""
        if question_id <= 0 or key not in allowed:
            raise ValueError("Each pick needs question_id and module.")
        pair = (question_id, key)
        if pair in seen:
            continue
        seen.add(pair)
        picks.append(pair)
    return picks


def import_content_questions(
    school: Any,
    class_id: int,
    module: str,
    slot: str,
    picks: list[tuple[int, str]],
    *,
    library_id: int,
    page_number: int,
    stage: str,
) -> list[dict[str, Any]]:
    """Import picked Content Questions onto one class live-lesson page.

    Every pick is checked against its module's current top six before
    anything is written, so a stale or hand-made id cannot import an
    arbitrary bank row. A pick from another module is allowed because that
    module's banks are named as the source.

    Args:
        school: ``SchoolDB``.
        class_id: Game-show ``classes.id``.
        module: Target live module (``M1``).
        slot: Target live slot (``C2``).
        picks: Output of ``clean_content_picks``.
        library_id: Attached pack library id.
        page_number: Teacher page for the placement.
        stage: Lifecycle stage for the placement.

    Returns:
        Placement rows in pick order.

    Raises:
        KeyError: A pick is not in its module's top Content Questions.
    """
    skip = _non_pack_bank_ids(school, int(library_id))
    top_by_module: dict[str, set[int]] = {}
    for question_id, key in picks:
        if key not in top_by_module:
            number = parse_module_token(key)
            group = module_content_questions(
                school,
                int(library_id),
                int(number or 0),
                class_id=int(class_id),
                skip_bank_ids=skip,
            )
            top_by_module[key] = {
                int(item["question_id"]) for item in group["items"]
            }
        if question_id not in top_by_module[key]:
            raise KeyError(
                f"question {question_id} is not a {key} Content Question"
            )
    placements: list[dict[str, Any]] = []
    for question_id, key in picks:
        placements.append(
            school.import_mc_to_class_playlist(
                int(class_id),
                module,
                slot,
                int(question_id),
                library_id=int(library_id),
                page_number=int(page_number),
                stage=stage,
                source_module=key,
            )
        )
    return placements

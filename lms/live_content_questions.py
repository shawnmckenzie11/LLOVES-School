"""MCK-79: each module's top Content Questions for Run Live Class Import.

A module's **Content Questions** are the content pack's own questions in
that module's linked banks: the default Core Math mix the Import picker
already lists (untagged and contest rows). Warmups, Custom (stored
``standard``) and the staff-authored bank are left out, because they are
not pack content.

**Top 6** is the existing Import order: linked banks by bank title (byte
order), then insertion id, after the same duplicate filter. It is not a
quality or difficulty rank; the pack carries no rank field, so nothing is
re-ranked here. A module with fewer than six shows what it has.

Loading is lazy and read-only. The list call returns every selectable
module's linked flag (cheap SQL) plus one module's top 6. Other modules
load when the teacher expands them. Nothing here links banks: the picker
still links the class's current module the way it did before MCK-79.

Reads go through ``search_module_bank_mcs`` so warmup, overlay and
duplicate rules are not forked. Imports go through
``import_mc_to_class_playlist``, the path Import already uses, and a batch
is all-or-nothing (see ``import_content_questions``).
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


class ContentImportFailed(RuntimeError):
    """A batch placement failed; the batch was rolled back.

    Attributes:
        landed: Question ids that could not be rolled back (normally empty).
        rolled_back: How many placements were removed again.
    """

    def __init__(self, message: str, *, landed: list[int], rolled_back: int) -> None:
        """Keep the failure text plus what is still on the deck."""
        super().__init__(message)
        self.landed = landed
        self.rolled_back = rolled_back


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


def _pack_bank_linked(
    school: Any, library_id: int, module_number: int, skip: set[int]
) -> bool:
    """True when any content-pack bank is linked to the module."""
    return any(
        int(row["bank_id"]) not in skip
        for row in school.list_module_bank_links(int(library_id), int(module_number))
    )


def module_summaries(
    school: Any, library_id: int, modules: list[str]
) -> list[dict[str, Any]]:
    """Cheap per-module rows for the collapsed groups (no question scan).

    Args:
        school: ``SchoolDB``.
        library_id: ``content_libraries.id``.
        modules: Selectable module tokens.

    Returns:
        ``{module, module_number, label, linked}`` per module, in order.
    """
    skip = _non_pack_bank_ids(school, int(library_id))
    out: list[dict[str, Any]] = []
    for token in modules:
        number = parse_module_token(token)
        if number is None:
            continue
        out.append(
            {
                "module": f"M{number}",
                "module_number": number,
                "label": f"Module {number}",
                "linked": _pack_bank_linked(school, int(library_id), number, skip),
            }
        )
    return out


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

    Read-only apart from the existing Module 2 retag inside
    ``search_module_bank_mcs``. A module with no linked pack bank is not
    searched and never linked here.

    Args:
        school: ``SchoolDB``.
        library_id: ``content_libraries.id``.
        module_number: One-based module index.
        class_id: Class id for image URL resolution.
        limit: How many to keep (6).
        skip_bank_ids: Non-pack bank ids, when the caller already has them.

    Returns:
        ``module``, ``module_number``, ``label``, ``items`` (each tagged with
        ``module`` and a 1-based ``content_rank``) and ``linked``.
    """
    number = int(module_number)
    key = f"M{number}"
    skip = (
        skip_bank_ids
        if skip_bank_ids is not None
        else _non_pack_bank_ids(school, int(library_id))
    )
    group: dict[str, Any] = {
        "module": key,
        "module_number": number,
        "label": f"Module {number}",
        "items": [],
        "linked": _pack_bank_linked(school, int(library_id), number, skip),
    }
    if not group["linked"]:
        return group
    result = school.search_module_bank_mcs(
        int(library_id), number, "", limit=500, class_id=class_id, kind=""
    )
    content = [
        row
        for row in result.get("items") or []
        if int(row.get("bank_id") or 0) not in skip
        and int(row.get("question_id") or 0) > 0
    ]
    for rank, row in enumerate(content[: max(0, int(limit))], start=1):
        item = dict(row)
        item["module"] = key
        item["content_rank"] = rank
        group["items"].append(item)
    return group


def clean_content_picks(raw: Any, modules: list[str]) -> list[tuple[int, str]]:
    """Validate posted picks into ``(question_id, module)`` pairs.

    The same question id twice in one batch (even under two modules whose
    banks overlap) keeps only the first pick.

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
    seen: set[int] = set()
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
        if question_id in seen:
            continue
        seen.add(question_id)
        picks.append((question_id, key))
    return picks


def _roll_back_placements(
    school: Any, class_id: int, module: str, slot: str, placement_ids: list[int]
) -> int:
    """Delete this batch's placement rows by id and resync the live deck.

    Deletes by row id, not item id, so an earlier copy of the same
    question on the deck is left alone.

    Returns:
        Rows deleted.
    """
    if not placement_ids:
        return 0
    placeholders = ",".join("?" for _ in placement_ids)
    with school._lock:
        cur = school.conn.execute(
            f"""
            DELETE FROM class_live_playlist_placements
            WHERE class_id = ? AND id IN ({placeholders})
            """,
            (int(class_id), *[int(pid) for pid in placement_ids]),
        )
        school.conn.commit()
        deleted = int(cur.rowcount or 0)
    school._sync_playlist_change(int(class_id), module, slot)
    return deleted


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

    All-or-nothing. Every pick is checked against its module's current top
    six before anything is written, so a stale or hand-made id cannot
    import an arbitrary bank row. Then each pick goes through the shared
    ``import_mc_to_class_playlist``. That function commits per call and
    resyncs the live session, so one SQL transaction is not possible
    without forking it; instead, if any placement fails, this batch's rows
    are deleted again by id and the deck is resynced before the error is
    raised.

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
        KeyError: A pick is not in its module's top Content Questions
            (nothing written).
        ContentImportFailed: A placement failed and the batch was rolled back.
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
    try:
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
    except Exception as exc:  # noqa: BLE001 - any failure rolls the batch back
        ids = [int(row["id"]) for row in placements if row.get("id")]
        deleted = _roll_back_placements(school, int(class_id), module, slot, ids)
        landed: list[int] = []
        if ids and deleted < len(ids):
            placeholders = ",".join("?" for _ in ids)
            with school._lock:
                rows = school.conn.execute(
                    f"""
                    SELECT source_question_id FROM class_live_playlist_placements
                    WHERE class_id = ? AND id IN ({placeholders})
                    """,
                    (int(class_id), *ids),
                ).fetchall()
            landed = [int(row["source_question_id"] or 0) for row in rows]
        reason = str(exc).strip("'\"")
        message = f"Import stopped and was undone; nothing was imported. {reason}"
        if landed:
            message = (
                f"Import stopped: {reason}. Could not undo questions "
                f"{', '.join(map(str, landed))}; they are on the deck."
            )
        raise ContentImportFailed(
            message, landed=landed, rolled_back=deleted
        ) from exc
    return placements

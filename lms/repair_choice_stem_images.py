"""Repair MC rows whose stem shows a choice image.

Canvas import stores the stem and the choices separately. A later display
pass used to copy the first choice image onto the question hero, and some
rows also have that image glued into ``stem_html``. This module finds those
rows and removes the choice image from the stem fields only.

The scan is idempotent: a second run finds nothing. Choice HTML is left
alone, and a graph that is not a choice is left on the stem.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

try:
    from question_math import bank_image_identity, scrub_choice_images_from_stem
except ImportError:
    from lms.question_math import bank_image_identity, scrub_choice_images_from_stem


# (table, json column, sql column that already is the question id)
_TARGETS: tuple[tuple[str, str, str | None], ...] = (
    ("questions", "payload_json", "id"),
    ("class_live_playlist_placements", "item_json", "source_question_id"),
    ("live_session_items", "item_json", None),
    ("live_session_prompts", "payload", None),
    ("participation_items", "item_json", None),
)
_NESTED_KEYS = ("payload", "item", "content")
_QUESTION_ID_KEYS = ("source_question_id", "question_id")


@dataclass(frozen=True)
class StemImageRepair:
    """One stored JSON document that would lose a choice image from its stem.

    Args:
        table: Sqlite table name from :data:`_TARGETS`.
        column: JSON column to rewrite.
        row_id: Primary key.
        question_id: Bank ``questions.id`` when it can be read without guessing.
        fields: Stem fields that change (``stem_html``, ``image_url``, …).
        old_json: Exact stored text, used so apply does not clobber a newer write.
        new_json: Replacement document.
    """

    table: str
    column: str
    row_id: int
    question_id: int | None
    fields: tuple[str, ...]
    old_json: str
    new_json: str


def _table_exists(school: Any, name: str) -> bool:
    """True when ``name`` is a table in this database."""
    row = school.conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def mirror_identity_aliases(school: Any) -> dict[str, str]:
    """Map mirror paths and their source URLs onto one identity.

    A playlist card may store the mirrored file on ``image_url`` while the
    choice HTML still has the remote URL. The cache row joins them.

    Args:
        school: Open school database.

    Returns:
        Identity → canonical identity. Empty when the cache table is absent.
    """
    if not _table_exists(school, "bank_image_cache"):
        return {}
    parent: dict[str, str] = {}

    def find(ident: str) -> str:
        """Disjoint-set root for one image identity."""
        while parent.get(ident, ident) != ident:
            parent[ident] = parent.get(parent[ident], parent[ident])
            ident = parent[ident]
        return ident

    def union(left: str, right: str) -> None:
        """Join two identities that are the same mirrored image."""
        if not left or not right:
            return
        root_left = find(left)
        root_right = find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    rows = school.conn.execute(
        "SELECT source_url, relpath FROM bank_image_cache"
    ).fetchall()
    for row in rows:
        union(bank_image_identity(row[0]), bank_image_identity(row[1]))
    return {ident: find(ident) for ident in list(parent)}


def _positive_int(value: Any) -> int | None:
    """Return a positive int, or ``None`` for anything else."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = str(value).strip()
    if text.isdigit():
        number = int(text)
        return number if number > 0 else None
    return None


def _question_id(blob: dict[str, Any], column_value: Any) -> int | None:
    """Best question id for a stored card, without inventing one.

    Args:
        blob: Parsed JSON object.
        column_value: SQL column that is already the question id, if any.
    """
    found = _positive_int(column_value)
    if found:
        return found
    for key in _QUESTION_ID_KEYS:
        found = _positive_int(blob.get(key))
        if found:
            return found
    for key in _NESTED_KEYS:
        nested = blob.get(key)
        if not isinstance(nested, dict):
            continue
        for inner in _QUESTION_ID_KEYS:
            found = _positive_int(nested.get(inner))
            if found:
                return found
    item_id = str(blob.get("id") or "")
    if item_id.startswith("bank-import-"):
        return _positive_int(item_id.rsplit("-", 1)[-1])
    return None


def scrub_stored_document(
    blob: dict[str, Any],
    *,
    aliases: dict[str, str] | None = None,
) -> list[str]:
    """Scrub a stored question and one nested card, if present.

    Args:
        blob: Parsed JSON object. Mutated in place.
        aliases: Mirror-cache identity map.

    Returns:
        Field names that changed. Nested fields are prefixed with their key.
    """
    changed = list(scrub_choice_images_from_stem(blob, aliases=aliases))
    for key in _NESTED_KEYS:
        nested = blob.get(key)
        if not isinstance(nested, dict):
            continue
        for field in scrub_choice_images_from_stem(nested, aliases=aliases):
            changed.append(f"{key}.{field}")
    return changed


def plan_choice_stem_image_repairs(school: Any) -> list[StemImageRepair]:
    """List stored documents that still show a choice image on the stem.

    Args:
        school: Open school database. Nothing is written.

    Returns:
        Repairs in table order, then primary key.
    """
    aliases = mirror_identity_aliases(school)
    planned: list[StemImageRepair] = []
    for table, column, question_column in _TARGETS:
        if not _table_exists(school, table):
            continue
        question_sql = question_column or "NULL"
        rows = school.conn.execute(
            f"SELECT id, {column}, {question_sql} FROM {table}"
        ).fetchall()
        for row in rows:
            raw = row[1]
            if not raw or not isinstance(raw, str):
                continue
            try:
                blob = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(blob, dict):
                continue
            fields = scrub_stored_document(blob, aliases=aliases)
            if not fields:
                continue
            planned.append(
                StemImageRepair(
                    table=table,
                    column=column,
                    row_id=int(row[0]),
                    question_id=_question_id(blob, row[2]),
                    fields=tuple(fields),
                    old_json=raw,
                    new_json=json.dumps(blob, ensure_ascii=False),
                )
            )
    return planned


def apply_choice_stem_image_repairs(
    school: Any,
    repairs: list[StemImageRepair],
) -> int:
    """Write planned repairs. A row that changed since the plan is skipped.

    Args:
        school: Open school database.
        repairs: Result of :func:`plan_choice_stem_image_repairs`.

    Returns:
        Number of rows updated.
    """
    written = 0
    lock = getattr(school, "_lock", None)
    if lock is not None:
        lock.acquire()
    try:
        for repair in repairs:
            if repair.table not in {item[0] for item in _TARGETS}:
                continue
            if repair.column not in {item[1] for item in _TARGETS}:
                continue
            cursor = school.conn.execute(
                f"UPDATE {repair.table} SET {repair.column} = ? "
                f"WHERE id = ? AND {repair.column} = ?",
                (repair.new_json, int(repair.row_id), repair.old_json),
            )
            written += int(cursor.rowcount or 0)
        school.conn.commit()
    finally:
        if lock is not None:
            lock.release()
    return written


def format_choice_stem_image_report(
    repairs: list[StemImageRepair],
    *,
    dry_run: bool,
) -> list[str]:
    """Lines for stdout. No stem text, names, or secrets.

    Args:
        repairs: Planned or applied rows.
        dry_run: When true, the heading says nothing was written.
    """
    lines = [
        "DRY RUN (no writes; pass --apply to write)" if dry_run else "APPLIED"
    ]
    counts = {table: 0 for table, _column, _qid in _TARGETS}
    question_ids: list[int] = []
    for repair in repairs:
        counts[repair.table] = counts.get(repair.table, 0) + 1
        if repair.question_id:
            question_ids.append(int(repair.question_id))
        qid = repair.question_id if repair.question_id is not None else "-"
        fields = ",".join(repair.fields)
        lines.append(
            f"{repair.table} row={repair.row_id} question_id={qid} fields={fields}"
        )
    for table, _column, _qid in _TARGETS:
        lines.append(f"count {table}={counts.get(table, 0)}")
    unique = sorted(set(question_ids))
    shown = ",".join(str(item) for item in unique) if unique else "none"
    lines.append(f"affected_question_ids: {shown}")
    lines.append(f"affected_question_id_count: {len(unique)}")
    lines.append(f"affected_row_count: {len(repairs)}")
    return lines

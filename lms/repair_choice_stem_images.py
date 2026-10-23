"""Repair MC question rows whose stem shows a choice image.

Canvas import stores the stem and the choices separately. A later display
pass used to copy the first choice image onto the question hero, and some
rows also have that image glued into ``stem_html``. This module finds those
``questions`` rows and removes the choice image from the stem fields only.

Pack libraries are shared across teachers, so rewriting one question row
changes that question for every teacher using the pack. The repair updates
``questions.payload_json`` on affected rows and no other table or column.
A second run finds nothing and writes zero rows. Choice HTML is left alone,
and a graph that is not a choice is left on the stem.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

try:
    from question_math import bank_image_identity, scrub_choice_images_from_stem
except ImportError:
    from lms.question_math import bank_image_identity, scrub_choice_images_from_stem


# Course-owned shells are not the shared pack. Any other library origin is
# the pack every teacher of that course uses.
_PRIVATE_LIBRARY_ORIGINS = frozenset({"shell", "backfill-staging"})
_SCHEMA_TABLES = frozenset(
    {"questions", "question_banks", "content_libraries", "course_offerings", "bank_image_cache"}
)
_NESTED_KEYS = ("payload", "item", "content")


@dataclass(frozen=True)
class StemImageRepair:
    """One question row that would lose a choice image from its stem.

    Args:
        table: Always ``questions``. Kept so apply can refuse any other table.
        column: Always ``payload_json``.
        row_id: ``questions.id``.
        question_id: Same as ``row_id`` for a question row.
        library_id: ``content_libraries.id`` for the question's bank.
        shared: True when that library is a pack, or when more than one
            teacher already points an offering at it.
        fields: Stem fields that change (``stem_html``, ``image_url``, …).
        old_json: Exact stored text, used so apply does not clobber a newer write.
        new_json: Replacement document.
    """

    table: str
    column: str
    row_id: int
    question_id: int | None
    library_id: int | None
    shared: bool
    fields: tuple[str, ...]
    old_json: str
    new_json: str


def _table_exists(school: Any, name: str) -> bool:
    """True when ``name`` is a table in this database."""
    if name not in _SCHEMA_TABLES:
        return False
    row = school.conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _columns(school: Any, table: str) -> set[str]:
    """Column names for one whitelisted table, or an empty set when it is absent.

    Args:
        school: Open school database.
        table: Table name from :data:`_SCHEMA_TABLES`.
    """
    if not _table_exists(school, table):
        return set()
    return {
        str(row[1])
        for row in school.conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def library_share_flags(school: Any) -> dict[int, bool]:
    """Return each library id and whether repairing it reaches every teacher.

    A pack (any origin other than ``shell`` or ``backfill-staging``) is the
    library later teachers of that course receive, so it is shared even when
    only one offering points at it today. A shell is shared when two or more
    teachers already point an offering at it.

    Args:
        school: Open school database. Nothing is written.
    """
    flags: dict[int, bool] = {}
    if "origin" in _columns(school, "content_libraries"):
        rows = school.conn.execute("SELECT id, origin FROM content_libraries").fetchall()
        for row in rows:
            origin = str(row[1] or "")
            flags[int(row[0])] = bool(origin) and origin not in _PRIVATE_LIBRARY_ORIGINS
    offering_columns = _columns(school, "course_offerings")
    if "teacher_user_id" not in offering_columns:
        return flags
    teachers: dict[int, set[int]] = {}
    for column in ("library_id", "shell_library_id"):
        if column not in offering_columns:
            continue
        rows = school.conn.execute(
            f"SELECT {column}, teacher_user_id FROM course_offerings "
            f"WHERE {column} IS NOT NULL AND teacher_user_id IS NOT NULL"
        ).fetchall()
        for library_id, teacher_id in rows:
            teachers.setdefault(int(library_id), set()).add(int(teacher_id))
    for library_id, teacher_ids in teachers.items():
        if len(teacher_ids) > 1:
            flags[library_id] = True
        else:
            flags.setdefault(library_id, False)
    return flags


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


def _question_rows(school: Any) -> list[tuple[Any, ...]]:
    """Return ``(id, payload_json, library_id, origin)`` for every question.

    Args:
        school: Open school database.
    """
    if "payload_json" not in _columns(school, "questions"):
        return []
    bank_columns = _columns(school, "question_banks")
    library_columns = _columns(school, "content_libraries")
    if (
        "bank_id" in _columns(school, "questions")
        and "library_id" in bank_columns
        and "origin" in library_columns
    ):
        return school.conn.execute(
            """
            SELECT q.id, q.payload_json, b.library_id, l.origin
            FROM questions q
            LEFT JOIN question_banks b ON b.id = q.bank_id
            LEFT JOIN content_libraries l ON l.id = b.library_id
            ORDER BY q.id
            """
        ).fetchall()
    return school.conn.execute(
        "SELECT id, payload_json, NULL, NULL FROM questions ORDER BY id"
    ).fetchall()


def plan_choice_stem_image_repairs(school: Any) -> list[StemImageRepair]:
    """List question rows that still show a choice image on the stem.

    Playlist, prompt, and participation copies are not included. Those cards
    are class-local, and the display path already drops a choice image before
    it is shown. This plan is the shared question row only.

    Args:
        school: Open school database. Nothing is written.

    Returns:
        Repairs in question id order.
    """
    aliases = mirror_identity_aliases(school)
    shared = library_share_flags(school)
    planned: list[StemImageRepair] = []
    for row in _question_rows(school):
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
        library_id = _positive_int(row[2])
        planned.append(
            StemImageRepair(
                table="questions",
                column="payload_json",
                row_id=int(row[0]),
                question_id=int(row[0]),
                library_id=library_id,
                shared=bool(library_id and shared.get(library_id, False)),
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
    """Write planned question repairs. A row that changed since the plan is skipped.

    Only ``questions.payload_json`` is updated, and only when that stored text
    still matches the plan. Banks, libraries, offerings, and every other
    question row are left as they are. An empty plan writes nothing.

    Args:
        school: Open school database.
        repairs: Result of :func:`plan_choice_stem_image_repairs`.

    Returns:
        Number of question rows updated. Zero on a second run.
    """
    written = 0
    lock = getattr(school, "_lock", None)
    if lock is not None:
        lock.acquire()
    try:
        for repair in repairs:
            if repair.table != "questions" or repair.column != "payload_json":
                continue
            cursor = school.conn.execute(
                "UPDATE questions SET payload_json = ? "
                "WHERE id = ? AND payload_json = ?",
                (repair.new_json, int(repair.row_id), repair.old_json),
            )
            written += int(cursor.rowcount or 0)
        if written:
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

    Dry-run lists each affected question id with its library id and whether
    that library is shared. A shared pack change is visible to every teacher
    using the pack.

    Args:
        repairs: Planned or applied question rows.
        dry_run: When true, the heading says nothing was written.
    """
    lines = [
        "DRY RUN (no writes; pass --apply to write)" if dry_run else "APPLIED",
        "writes: questions.payload_json only",
    ]
    seen: dict[int, tuple[int | None, bool]] = {}
    for repair in repairs:
        qid = int(repair.question_id if repair.question_id is not None else repair.row_id)
        seen[qid] = (repair.library_id, repair.shared)
        library = repair.library_id if repair.library_id is not None else "-"
        shared = "yes" if repair.shared else "no"
        fields = ",".join(repair.fields)
        lines.append(
            f"questions row={repair.row_id} question_id={qid} "
            f"library_id={library} shared={shared} fields={fields}"
        )
    lines.append("affected_questions:")
    if not seen:
        lines.append("none")
    for question_id in sorted(seen):
        library_id, shared_flag = seen[question_id]
        library = library_id if library_id is not None else "-"
        shared = "yes" if shared_flag else "no"
        lines.append(
            f"question_id={question_id} library_id={library} shared={shared}"
        )
    lines.append(f"affected_question_id_count: {len(seen)}")
    lines.append(f"affected_row_count: {len(repairs)}")
    return lines

"""Rebase module-file URLs a deck copy left pointing at the source class.

Dry-run planning and the library-pointer copy live here so the deck-copy
write and ``scripts/repair_deck_copy_images.py`` share one rule. The
library module is not named like the script, so a ``scripts/`` entry on
``sys.path`` cannot shadow it. The
script defaults to a dry run. Nothing in this module opens a database.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

try:
    from question_math import (
        module_file_lookup_relpaths,
        module_file_refs_in_text,
        rebase_stored_module_file_text,
    )
except ImportError:
    from lms.question_math import (
        module_file_lookup_relpaths,
        module_file_refs_in_text,
        rebase_stored_module_file_text,
    )


# Columns that store a deck snapshot next to a class id. Live items are
# scanned separately because their class id lives on the session row.
_TEXT_SCANS: tuple[tuple[str, str], ...] = (
    ("class_live_playlist_placements", "item_json"),
    ("class_live_deck_seeds", "media_json"),
    ("class_live_deck_seeds", "team_challenge_json"),
    ("class_live_media_overlays", "stem"),
    ("class_live_media_overlays", "caption"),
)


@dataclass
class CrossCourseImageRef:
    """One stored module-file URL that names a different course.

    ``--apply`` does not rewrite these and does not copy a library pointer.

    Attributes:
        table: Sqlite table.
        row_id: Primary key.
        class_id: Class that owns the row.
        column: Text column that holds the URL.
        url_class_id: Class id inside the URL.
        relpath: Path after ``module-files/``.
        owner_course: Course code of the row's class.
        url_course: Course code of ``url_class_id``.
    """

    table: str
    row_id: int
    class_id: int
    column: str
    url_class_id: int
    relpath: str
    owner_course: str
    url_course: str


@dataclass
class DeckImageRepairPlan:
    """Repairs that may be written, plus cross-course URLs that may not.

    Attributes:
        repairs: Rows whose same-course source URLs can be rebased.
        cross_course: URLs that name another course. Never applied.
    """

    repairs: list[DeckImageRepair] = field(default_factory=list)
    cross_course: list[CrossCourseImageRef] = field(default_factory=list)


@dataclass
class DeckImageRepair:
    """One stored text whose module-file URLs name another class.

    Attributes:
        table: Sqlite table.
        row_id: Primary key.
        class_id: Class that owns the row.
        column: Text column to replace.
        old_text: Text read from the column.
        new_text: Text with module-file URLs rebased onto ``class_id``.
        copies: ``(source_library_id, dest_library_id, relpath)`` pointers
            to add when the destination library does not already have them.
    """

    table: str
    row_id: int
    class_id: int
    column: str
    old_text: str
    new_text: str
    copies: list[tuple[int, int, str]] = field(default_factory=list)


def find_library_blob(
    conn: sqlite3.Connection, library_id: int, relpath: str
) -> tuple[str, str] | None:
    """Return the stored relpath and blob sha for one module-file path.

    Args:
        conn: Open sqlite connection.
        library_id: ``content_libraries.id``.
        relpath: Path from a module-file URL.

    Returns:
        ``(stored_relpath, blob_sha)``, or None when this library has
        none of the lookup keys.
    """
    if not library_id:
        return None
    for candidate in module_file_lookup_relpaths(relpath):
        row = conn.execute(
            """
            SELECT relpath, blob_sha FROM library_files
            WHERE library_id = ? AND relpath = ?
            """,
            (int(library_id), candidate),
        ).fetchone()
        if row is not None and row[1]:
            return str(row[0]), str(row[1])
    return None


def copy_library_file_pointer(
    conn: sqlite3.Connection,
    source_library_id: int,
    dest_library_id: int,
    relpath: str,
    *,
    stamp: str,
) -> bool:
    """Point the destination library at a blob the source library already has.

    Does not commit and does not copy bytes. The same library, a missing
    source row, and a destination row that already exists are no-ops.

    Args:
        conn: Open sqlite connection inside the caller's transaction.
        source_library_id: Library that can already serve ``relpath``.
        dest_library_id: Library of the class that received the deck.
        relpath: Cartridge-relative path from the module-file URL.
        stamp: ``created_at`` value for a new row.

    Returns:
        True when a row was inserted.
    """
    source = int(source_library_id)
    dest = int(dest_library_id)
    if not source or not dest or source == dest:
        return False
    found = find_library_blob(conn, source, relpath)
    if found is None:
        return False
    stored_rel, blob_sha = found
    existing = conn.execute(
        """
        SELECT 1 FROM library_files
        WHERE library_id = ? AND relpath = ?
        """,
        (dest, stored_rel),
    ).fetchone()
    if existing is not None:
        return False
    conn.execute(
        """
        INSERT INTO library_files (library_id, relpath, blob_sha, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (dest, stored_rel, blob_sha, stamp),
    )
    return True


def _class_library_id(conn: sqlite3.Connection, class_id: int) -> int | None:
    """Return the pack or shell library for one class.

    Args:
        conn: Open sqlite connection.
        class_id: ``classes.id``.
    """
    try:
        row = conn.execute(
            """
            SELECT COALESCE(o.library_id, o.shell_library_id) AS library_id
            FROM classes c
            JOIN course_offerings o ON o.id = c.offering_id
            WHERE c.id = ?
            """,
            (int(class_id),),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None or row[0] is None:
        return None
    return int(row[0])


def _class_scope(
    conn: sqlite3.Connection, class_id: int
) -> tuple[str, int | None] | None:
    """Return ``(course_code, teacher_user_id)`` for one class.

    The offering's course and teacher win when the class is attached to
    one. A missing class returns None. No names or emails are read.

    Args:
        conn: Open sqlite connection.
        class_id: ``classes.id``.
    """
    try:
        row = conn.execute(
            """
            SELECT c.course_code,
                   c.teacher_user_id,
                   o.ontario_code,
                   o.teacher_user_id
            FROM classes c
            LEFT JOIN course_offerings o ON o.id = c.offering_id
            WHERE c.id = ?
            """,
            (int(class_id),),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    course = str(row[2] or row[0] or "").strip().upper()
    teacher_raw = row[3] if row[3] is not None else row[1]
    teacher = int(teacher_raw) if teacher_raw is not None else None
    return course, teacher


def module_file_pointer_scope(
    conn: sqlite3.Connection, dest_class_id: int, source_class_id: int
) -> str:
    """Classify whether a repair may copy one class's library file.

    ``same`` is the widest grant: a different class, the same course code,
    and the same teacher. ``cross_course`` is never copied. ``other_teacher``
    and ``unknown`` are never copied either.

    Args:
        conn: Open sqlite connection.
        dest_class_id: Class that owns the stored row.
        source_class_id: Class id named by the module-file URL.

    Returns:
        ``same_class``, ``same``, ``cross_course``, ``other_teacher``,
        or ``unknown``.
    """
    dest_id = int(dest_class_id)
    source_id = int(source_class_id)
    if dest_id == source_id:
        return "same_class"
    dest = _class_scope(conn, dest_id)
    source = _class_scope(conn, source_id)
    if dest is None or source is None:
        return "unknown"
    dest_course, dest_teacher = dest
    source_course, source_teacher = source
    if not dest_course or not source_course or dest_course != source_course:
        return "cross_course"
    if (
        dest_teacher is None
        or source_teacher is None
        or dest_teacher != source_teacher
    ):
        return "other_teacher"
    return "same"


def _copies_for_refs(
    conn: sqlite3.Connection,
    dest_class_id: int,
    refs: list[tuple[int, str]],
) -> list[tuple[int, int, str]]:
    """Plan library pointers for same-course, same-teacher refs.

    A ref whose class is another course, another teacher, or missing is
    skipped. The destination library is not given that file.

    Args:
        conn: Open sqlite connection.
        dest_class_id: Class that owns the row being repaired.
        refs: ``(source_class_id, relpath)`` pairs from the text.
    """
    dest_library = _class_library_id(conn, int(dest_class_id))
    if dest_library is None:
        return []
    planned: list[tuple[int, int, str]] = []
    seen: set[tuple[int, str]] = set()
    for source_class_id, relpath in refs:
        key = (int(source_class_id), str(relpath))
        if key in seen:
            continue
        seen.add(key)
        if module_file_pointer_scope(conn, int(dest_class_id), key[0]) != "same":
            continue
        source_library = _class_library_id(conn, key[0])
        if source_library is None or source_library == dest_library:
            continue
        if find_library_blob(conn, source_library, key[1]) is None:
            continue
        if find_library_blob(conn, dest_library, key[1]) is not None:
            continue
        planned.append((source_library, dest_library, key[1]))
    return planned


def _repair_from_text(
    conn: sqlite3.Connection,
    *,
    table: str,
    row_id: int,
    class_id: int,
    column: str,
    text: str,
) -> tuple[DeckImageRepair | None, list[CrossCourseImageRef]]:
    """Build one repair when ``text`` links a same-course source class.

    A URL for another course is listed and left unchanged. No library
    pointer is planned for it.

    Args:
        conn: Open sqlite connection.
        table: Sqlite table.
        row_id: Primary key.
        class_id: Class that owns the row.
        column: Text column.
        text: Current column value.
    """
    raw = str(text or "")
    if "module-files/" not in raw:
        return None, []
    allowed: set[int] = set()
    cross: list[CrossCourseImageRef] = []
    seen_cross: set[tuple[int, str]] = set()
    owner = _class_scope(conn, int(class_id))
    owner_course = owner[0] if owner is not None else ""
    for source_id, relpath in module_file_refs_in_text(raw):
        if int(source_id) == int(class_id):
            continue
        scope = module_file_pointer_scope(conn, int(class_id), int(source_id))
        if scope == "same":
            allowed.add(int(source_id))
            continue
        if scope != "cross_course":
            continue
        key = (int(source_id), str(relpath))
        if key in seen_cross:
            continue
        seen_cross.add(key)
        foreign = _class_scope(conn, int(source_id))
        cross.append(
            CrossCourseImageRef(
                table=table,
                row_id=int(row_id),
                class_id=int(class_id),
                column=column,
                url_class_id=int(source_id),
                relpath=str(relpath),
                owner_course=owner_course,
                url_course=foreign[0] if foreign is not None else "",
            )
        )
    if not allowed:
        return None, cross
    new_text, refs = rebase_stored_module_file_text(
        raw, int(class_id), allowed_source_ids=allowed
    )
    if not refs or new_text == raw:
        return None, cross
    return (
        DeckImageRepair(
            table=table,
            row_id=int(row_id),
            class_id=int(class_id),
            column=column,
            old_text=raw,
            new_text=new_text,
            copies=_copies_for_refs(conn, int(class_id), refs),
        ),
        cross,
    )


def _scan_table(
    conn: sqlite3.Connection, table: str, column: str
) -> tuple[list[DeckImageRepair], list[CrossCourseImageRef]]:
    """Scan one class-owned text column for foreign module-file URLs.

    Args:
        conn: Open sqlite connection.
        table: Table with ``id``, ``class_id``, and ``column``.
        column: Text column to read.
    """
    found: list[DeckImageRepair] = []
    cross: list[CrossCourseImageRef] = []
    try:
        rows = conn.execute(
            f"""
            SELECT id, class_id, {column}
            FROM {table}
            WHERE {column} LIKE '%module-files/%'
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return [], []
    for row in rows:
        repair, refs = _repair_from_text(
            conn,
            table=table,
            row_id=int(row[0]),
            class_id=int(row[1]),
            column=column,
            text=str(row[2] or ""),
        )
        if repair is not None:
            found.append(repair)
        cross.extend(refs)
    return found, cross


def _scan_live_items(
    conn: sqlite3.Connection,
) -> tuple[list[DeckImageRepair], list[CrossCourseImageRef]]:
    """Scan live-session item JSON via the session's class id.

    The session code and attendee rows are not read.

    Args:
        conn: Open sqlite connection.
    """
    found: list[DeckImageRepair] = []
    cross: list[CrossCourseImageRef] = []
    try:
        rows = conn.execute(
            """
            SELECT items.id, sessions.class_id, items.item_json
            FROM live_session_items AS items
            JOIN live_class_sessions AS sessions
              ON sessions.id = items.live_session_id
            WHERE items.item_json LIKE '%module-files/%'
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return [], []
    for row in rows:
        repair, refs = _repair_from_text(
            conn,
            table="live_session_items",
            row_id=int(row[0]),
            class_id=int(row[1]),
            column="item_json",
            text=str(row[2] or ""),
        )
        if repair is not None:
            found.append(repair)
        cross.extend(refs)
    return found, cross


def plan_deck_copy_image_repairs(
    conn: sqlite3.Connection,
) -> DeckImageRepairPlan:
    """List stored rows whose module-file URLs name a different class.

    Same-teacher, same-course URLs are repairs. Another course is listed
    on ``cross_course`` and is not a repair.

    Args:
        conn: Open sqlite connection. Not written.
    """
    planned = DeckImageRepairPlan()
    for table, column in _TEXT_SCANS:
        repairs, cross = _scan_table(conn, table, column)
        planned.repairs.extend(repairs)
        planned.cross_course.extend(cross)
    repairs, cross = _scan_live_items(conn)
    planned.repairs.extend(repairs)
    planned.cross_course.extend(cross)
    return planned


def apply_deck_copy_image_repairs(
    conn: sqlite3.Connection,
    planned: list[DeckImageRepair],
    *,
    stamp: str,
) -> dict[str, int]:
    """Write a plan. A row is updated only when its text is unchanged.

    Library pointers are inserted only after that row's text update
    matches. The caller commits.

    Args:
        conn: Open read-write sqlite connection.
        planned: Result of :func:`plan_deck_copy_image_repairs`.
        stamp: ``created_at`` for new library rows.

    Returns:
        Counts of text rows updated and library pointers inserted.
    """
    updated = 0
    pointers = 0
    for item in planned:
        if item.table not in {name for name, _column in _TEXT_SCANS} and (
            item.table != "live_session_items"
        ):
            continue
        if item.column not in {column for _table, column in _TEXT_SCANS} and (
            item.column != "item_json"
        ):
            continue
        cur = conn.execute(
            f"""
            UPDATE {item.table}
            SET {item.column} = ?
            WHERE id = ? AND {item.column} = ?
            """,
            (item.new_text, int(item.row_id), item.old_text),
        )
        if int(cur.rowcount or 0) != 1:
            continue
        updated += 1
        for source_library, dest_library, relpath in item.copies:
            if copy_library_file_pointer(
                conn,
                source_library,
                dest_library,
                relpath,
                stamp=stamp,
            ):
                pointers += 1
    return {"rows": updated, "file_pointers": pointers}


def format_deck_copy_image_report(
    planned: DeckImageRepairPlan, *, mode: str
) -> str:
    """Return a dry-run report with class ids and paths, not people.

    Cross-course URLs are a separate section. They are not counted as
    rows that would be written.

    Args:
        planned: Repairs and cross-course refs from
            :func:`plan_deck_copy_image_repairs`.
        mode: ``dry-run`` or ``apply``.
    """
    lines = [
        f"mode={mode}",
        f"rows={len(planned.repairs)}",
        f"file_pointers={sum(len(item.copies) for item in planned.repairs)}",
        f"cross_course={len(planned.cross_course)}",
    ]
    for item in planned.repairs:
        lines.append(
            f"{item.table} id={item.row_id} class={item.class_id} "
            f"column={item.column} file_pointers={len(item.copies)}"
        )
    lines.append("cross-course")
    for ref in planned.cross_course:
        lines.append(
            f"{ref.table} id={ref.row_id} class={ref.class_id} "
            f"url_class={ref.url_class_id} column={ref.column} "
            f"relpath={ref.relpath} owner_course={ref.owner_course} "
            f"url_course={ref.url_course}"
        )
    return "\n".join(lines)


def repair_summary(planned: DeckImageRepairPlan) -> dict[str, Any]:
    """Return counts a test can assert without reading the report text.

    Args:
        planned: Plan from :func:`plan_deck_copy_image_repairs`.
    """
    return {
        "rows": len(planned.repairs),
        "file_pointers": sum(len(item.copies) for item in planned.repairs),
        "tables": sorted({item.table for item in planned.repairs}),
        "cross_course": len(planned.cross_course),
    }

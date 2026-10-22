"""Default course shell and admin module-pack backfill (MCK-312).

A new teacher course gets one course-owned content library, twenty modules
with four class slots each, and empty default question banks. Admin can
later merge a module pack into that library. The merge inserts missing
pack rows and leaves anything already stored — decks, slides, and bank
questions included — as it was. Roster, PIN, and attendance tables are
not written.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SHELL_MODULE_COUNT = 20
SHELL_SLOT_COUNT = 4
SHELL_ORIGIN = "shell"
STAGING_ORIGIN = "backfill-staging"
SHELL_MODE = "default"
_PACK_ORIGINS = frozenset({"upload", "template", "legacy"})

# Tables the backfill must not change. Compared around the merge and never
# logged. Roster and PIN material lives on ``students``. Attendance marks
# live on ``session_scores``.
PROTECTED_TABLES = (
    "students",
    "sessions",
    "session_scores",
    "catchup_links",
    "catchup_pin_failures",
    "catchup_pin_unlocks",
    "class_catchup_settings",
    "lesson_slide_decks",
    "lesson_slides_latest",
    "class_live_deck_seeds",
    "class_live_playlist_placements",
    "class_live_playlist_item_overrides",
    "class_live_playlist_pages",
    "class_live_media_overlays",
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the course-owned class-slot table when it is missing.

    Args:
        conn: Open school sqlite connection.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS course_class_slots (
            id INTEGER PRIMARY KEY,
            offering_id INTEGER NOT NULL
                REFERENCES course_offerings(id) ON DELETE CASCADE,
            module_number INTEGER NOT NULL,
            slot_number INTEGER NOT NULL,
            module_key TEXT NOT NULL,
            slot_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(offering_id, module_number, slot_number)
        );
        CREATE INDEX IF NOT EXISTS idx_course_class_slots_offering
            ON course_class_slots(offering_id, module_number, slot_number);
        """
    )


def _now() -> str:
    """Return a UTC timestamp for shell rows."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _import_course_shell():
    """Import helpers that live next to this module or under ``lms``."""
    try:
        from instances import library_root
        from ingest import ingest_library
        from modules import install_uploaded_module_pack, store_uploaded_module_pack
    except ImportError:
        from lms.instances import library_root
        from lms.ingest import ingest_library
        from lms.modules import install_uploaded_module_pack, store_uploaded_module_pack
    return library_root, ingest_library, install_uploaded_module_pack, store_uploaded_module_pack


def offering_uses_shell_tabs(school: Any, offering: dict[str, Any] | None) -> bool:
    """True when this course should show the four-tab default shell.

    A course marked as the default shell keeps those four tabs until a
    classic module pack (upload, template, or legacy) is attached. Courses
    that already point at one of those packs keep the tab bar they have.

    Args:
        school: ``SchoolDB``.
        offering: Offering row, or None.
    """
    if not offering:
        return False
    if str(offering.get("course_shell") or "") != SHELL_MODE:
        return False
    library_id = offering.get("library_id")
    if not library_id:
        return True
    library = school.get_library(int(library_id))
    origin = str((library or {}).get("origin") or "")
    return origin not in _PACK_ORIGINS


def ensure_default_course_shell(
    school: Any,
    offering_id: int,
    *,
    mark: bool = True,
) -> dict[str, Any]:
    """Create the default shell for one offering, without duplicating rows.

    Safe to call again. A second call leaves slot, bank, and library counts
    unchanged. ``mark`` records the offering as a default shell so new
    courses use the four-tab bar. Backfill passes ``mark=False`` so a course
    that already has a classic pack keeps its tabs.

    Args:
        school: ``SchoolDB``.
        offering_id: ``course_offerings.id``.
        mark: When True, set ``course_shell`` when it is still blank.

    Returns:
        The offering row after the shell is in place.
    """
    ensure_schema(school.conn)
    offering = school.get_offering(int(offering_id))
    library_id = offering.get("shell_library_id")
    library = school.get_library(int(library_id)) if library_id else None
    if library is None or str(library.get("origin") or "") != SHELL_ORIGIN:
        library = school.create_library(
            str(offering["ontario_code"]), origin=SHELL_ORIGIN
        )
        library_id = int(library["id"])
    else:
        library_id = int(library["id"])
    _store_shell_pointer(school, int(offering_id), library_id, mark=mark)
    _ensure_slots(school, int(offering_id))
    _ensure_default_banks(school, library_id)
    return school.get_offering(int(offering_id))


def _store_shell_pointer(
    school: Any, offering_id: int, library_id: int, *, mark: bool
) -> None:
    """Point the offering at its course-owned library.

    Args:
        school: ``SchoolDB``.
        offering_id: ``course_offerings.id``.
        library_id: Shell ``content_libraries.id``.
        mark: Set ``course_shell`` when it is still blank.
    """
    with school._lock:  # noqa: SLF001 - shared connection lock
        if mark:
            school.conn.execute(
                """
                UPDATE course_offerings
                SET shell_library_id = ?,
                    course_shell = CASE
                        WHEN course_shell IS NULL OR TRIM(course_shell) = ''
                        THEN ? ELSE course_shell END
                WHERE id = ?
                """,
                (int(library_id), SHELL_MODE, int(offering_id)),
            )
        else:
            school.conn.execute(
                """
                UPDATE course_offerings
                SET shell_library_id = ?
                WHERE id = ?
                """,
                (int(library_id), int(offering_id)),
            )
        school.conn.commit()


def _ensure_slots(school: Any, offering_id: int) -> None:
    """Insert M1–M20 × C1–C4 when those slots are not already stored.

    Args:
        school: ``SchoolDB``.
        offering_id: ``course_offerings.id``.
    """
    stamp = _now()
    rows = [
        (
            int(offering_id),
            module_number,
            slot_number,
            f"M{module_number}",
            f"C{slot_number}",
            stamp,
        )
        for module_number in range(1, SHELL_MODULE_COUNT + 1)
        for slot_number in range(1, SHELL_SLOT_COUNT + 1)
    ]
    with school._lock:  # noqa: SLF001 - shared connection lock
        school.conn.executemany(
            """
            INSERT INTO course_class_slots (
                offering_id, module_number, slot_number,
                module_key, slot_key, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(offering_id, module_number, slot_number) DO NOTHING
            """,
            rows,
        )
        school.conn.commit()


def _ensure_default_banks(school: Any, library_id: int) -> None:
    """Insert one empty bank per module plus a course bank.

    Existing banks and their questions are left as they are.

    Args:
        school: ``SchoolDB``.
        library_id: Shell ``content_libraries.id``.
    """
    stamp = _now()
    specs = [
        (f"shell-module-{number:02d}", f"Module {number}", number)
        for number in range(1, SHELL_MODULE_COUNT + 1)
    ]
    specs.append(("shell-course", "Course", 0))
    with school._lock:  # noqa: SLF001 - shared connection lock
        for import_key, title, module_number in specs:
            school.conn.execute(
                """
                INSERT INTO question_banks (
                    library_id, import_key, title, settings_json, created_at
                ) VALUES (?, ?, ?, '{}', ?)
                ON CONFLICT(library_id, import_key) DO NOTHING
                """,
                (int(library_id), import_key, title, stamp),
            )
            row = school.conn.execute(
                """
                SELECT id FROM question_banks
                WHERE library_id = ? AND import_key = ?
                """,
                (int(library_id), import_key),
            ).fetchone()
            if row is None:
                continue
            school.conn.execute(
                """
                INSERT INTO course_module_bank_links (
                    library_id, module_number, bank_id, confirmed_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(library_id, module_number, bank_id) DO NOTHING
                """,
                (int(library_id), int(module_number), int(row["id"]), stamp),
            )
        school.conn.commit()


def backfill_module_pack(
    school: Any,
    offering_id: int,
    file_storage: Any,
) -> dict[str, int]:
    """Merge an uploaded module pack into the course-owned library.

    Pack rows are added when their import key is new. Rows already in the
    library — including questions, pages, and decks added before the pack —
    are not updated. A second call with the same pack adds nothing. Roster,
    PIN, and attendance tables are not written.

    Args:
        school: ``SchoolDB``.
        offering_id: ``course_offerings.id``.
        file_storage: Werkzeug ``FileStorage`` for the ``.imscc``.

    Returns:
        Counts of pages and questions in the course library after the merge.

    Raises:
        ValueError: The upload is missing or not a cartridge.
        RuntimeError: The merge would have changed a protected table.
    """
    offering = ensure_default_course_shell(school, int(offering_id), mark=False)
    target_id = int(offering["shell_library_id"])
    before = _protected_snapshot(school)
    library_root, ingest_library, install_pack, store_pack = _import_course_shell()
    staging = school.create_library(
        str(offering["ontario_code"]), origin=STAGING_ORIGIN
    )
    staging_id = int(staging["id"])
    dest = library_root(school.data_dir, staging_id)
    try:
        stored = store_pack(file_storage, dest, full_validate=True)
        status = install_pack(stored, dest)
        if not status.get("ok"):
            raise ValueError(str(status.get("error") or "Could not read that module pack."))
        unpacked = dest / "unpacked"
        if unpacked.is_dir():
            ingest_library(school, school.data_dir, staging_id, unpacked)
        _assert_protected(school, before)
        _merge_library(school, staging_id, target_id, before)
        _assert_protected(school, before)
    finally:
        _drop_staging(school, staging_id, dest)
    _assert_protected(school, before)
    return _library_counts(school, target_id)


def _protected_snapshot(school: Any) -> dict[str, list[tuple]]:
    """Copy protected tables so a merge can prove it did not write them.

    Args:
        school: ``SchoolDB``.

    Returns:
        Table name to ordered row tuples. Missing tables are omitted.
    """
    with school._lock:  # noqa: SLF001 - shared connection lock
        present = {
            str(row[0])
            for row in school.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        out: dict[str, list[tuple]] = {}
        for table in PROTECTED_TABLES:
            if table not in present:
                continue
            rows = school.conn.execute(
                f'SELECT * FROM "{table}" ORDER BY rowid'
            ).fetchall()
            out[table] = [tuple(row) for row in rows]
    return out


def _assert_protected(school: Any, before: dict[str, list[tuple]]) -> None:
    """Raise when a protected table differs from ``before``.

    The message names the table only.

    Args:
        school: ``SchoolDB``.
        before: Snapshot from :func:`_protected_snapshot`.

    Raises:
        RuntimeError: A protected table changed.
    """
    after = _protected_snapshot(school)
    for table, rows in before.items():
        if after.get(table) != rows:
            raise RuntimeError(f"Backfill changed a protected table: {table}")
    for table in after:
        if table not in before:
            raise RuntimeError(f"Backfill changed a protected table: {table}")


def _drop_staging(school: Any, staging_id: int, dest: Path) -> None:
    """Remove a temporary ingest library and its folder.

    Args:
        school: ``SchoolDB``.
        staging_id: Staging ``content_libraries.id``.
        dest: Library folder on disk.
    """
    with school._lock:  # noqa: SLF001 - shared connection lock
        school.conn.execute(
            "DELETE FROM content_libraries WHERE id = ? AND origin = ?",
            (int(staging_id), STAGING_ORIGIN),
        )
        school.conn.commit()
    shutil.rmtree(dest, ignore_errors=True)


def _library_counts(school: Any, library_id: int) -> dict[str, int]:
    """Return page and question totals for one library.

    Args:
        school: ``SchoolDB``.
        library_id: ``content_libraries.id``.
    """
    with school._lock:  # noqa: SLF001 - shared connection lock
        pages = school.conn.execute(
            "SELECT COUNT(*) AS n FROM pages WHERE library_id = ?",
            (int(library_id),),
        ).fetchone()
        questions = school.conn.execute(
            """
            SELECT COUNT(*) AS n FROM questions q
            JOIN question_banks b ON b.id = q.bank_id
            WHERE b.library_id = ?
            """,
            (int(library_id),),
        ).fetchone()
    return {"pages": int(pages["n"]), "questions": int(questions["n"])}


def _merge_library(
    school: Any,
    staging_id: int,
    target_id: int,
    before: dict[str, list[tuple]],
) -> None:
    """Copy new staging rows into the course library.

    Existing import keys are kept, including their titles and payloads.

    Args:
        school: ``SchoolDB``.
        staging_id: Temporary library that holds the pack ingest.
        target_id: Course-owned shell library.
        before: Protected-table snapshot taken before the ingest.
    """
    conn = school.conn
    with school._lock:  # noqa: SLF001 - shared connection lock
        conn.execute("BEGIN")
        try:
            page_map = _copy_keyed(conn, "pages", staging_id, target_id, _PAGE_COLUMNS)
            assignment_map = _copy_keyed(
                conn, "assignments", staging_id, target_id, _ASSIGNMENT_COLUMNS
            )
            quiz_map = _copy_keyed(conn, "quizzes", staging_id, target_id, _QUIZ_COLUMNS)
            bank_map = _copy_keyed(
                conn, "question_banks", staging_id, target_id, _BANK_COLUMNS
            )
            question_map = _copy_questions(conn, bank_map)
            _copy_overlays(conn, staging_id, target_id, question_map)
            outline_map = _copy_keyed(
                conn, "module_outlines", staging_id, target_id, _OUTLINE_COLUMNS
            )
            _copy_items(
                conn,
                outline_map,
                {
                    "page": page_map,
                    "assignment": assignment_map,
                    "quiz": quiz_map,
                },
            )
            _copy_files(conn, staging_id, target_id)
            if _protected_snapshot_unlocked(conn) != before:
                raise RuntimeError("Backfill changed a protected table")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def _protected_snapshot_unlocked(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    """Protected snapshot while the caller already holds the school lock.

    Args:
        conn: School sqlite connection.
    """
    present = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    out: dict[str, list[tuple]] = {}
    for table in PROTECTED_TABLES:
        if table not in present:
            continue
        rows = conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
        out[table] = [tuple(row) for row in rows]
    return out


_PAGE_COLUMNS = (
    "library_id",
    "import_key",
    "kind",
    "title",
    "html_text",
    "blob_sha",
    "url",
    "created_at",
)
_ASSIGNMENT_COLUMNS = (
    "library_id",
    "import_key",
    "title",
    "body_html",
    "blob_sha",
    "points",
    "settings_json",
    "created_at",
)
_QUIZ_COLUMNS = (
    "library_id",
    "import_key",
    "title",
    "settings_json",
    "qti_blob_sha",
    "created_at",
)
_BANK_COLUMNS = (
    "library_id",
    "import_key",
    "title",
    "settings_json",
    "created_at",
)
_OUTLINE_COLUMNS = (
    "library_id",
    "import_key",
    "title",
    "position",
    "created_at",
)


def _copy_keyed(
    conn: sqlite3.Connection,
    table: str,
    staging_id: int,
    target_id: int,
    columns: tuple[str, ...],
) -> dict[int, int]:
    """Insert staging rows whose import key is not already in the target.

    Args:
        conn: School sqlite connection inside a transaction.
        table: ``pages``, ``assignments``, ``quizzes``, ``question_banks``,
            or ``module_outlines``.
        staging_id: Source library id.
        target_id: Destination library id.
        columns: Insert column list. ``library_id`` is rewritten.

    Returns:
        Staging row id to the target row id (existing or newly inserted).
    """
    mapping: dict[int, int] = {}
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE library_id = ?",
        (int(staging_id),),
    ).fetchall()
    for row in rows:
        data = dict(row)
        found = conn.execute(
            f"SELECT id FROM {table} WHERE library_id = ? AND import_key = ?",
            (int(target_id), data["import_key"]),
        ).fetchone()
        if found is not None:
            mapping[int(data["id"])] = int(found["id"])
            continue
        values = [
            int(target_id) if column == "library_id" else data.get(column)
            for column in columns
        ]
        placeholders = ", ".join("?" for _ in columns)
        names = ", ".join(columns)
        cur = conn.execute(
            f"INSERT INTO {table} ({names}) VALUES ({placeholders})",
            values,
        )
        mapping[int(data["id"])] = int(cur.lastrowid)
    return mapping


def _copy_questions(
    conn: sqlite3.Connection, bank_map: dict[int, int]
) -> dict[int, int]:
    """Insert pack questions that are not already in the target bank.

    A question with the same import key is left untouched.

    Args:
        conn: School sqlite connection inside a transaction.
        bank_map: Staging bank id to target bank id.

    Returns:
        Staging question id to target question id.
    """
    mapping: dict[int, int] = {}
    for staging_bank, target_bank in bank_map.items():
        rows = conn.execute(
            "SELECT * FROM questions WHERE bank_id = ?",
            (int(staging_bank),),
        ).fetchall()
        for row in rows:
            data = dict(row)
            found = conn.execute(
                """
                SELECT id FROM questions
                WHERE bank_id = ? AND import_key = ?
                """,
                (int(target_bank), data["import_key"]),
            ).fetchone()
            if found is not None:
                mapping[int(data["id"])] = int(found["id"])
                continue
            cur = conn.execute(
                """
                INSERT INTO questions (
                    bank_id, import_key, item_type, title, payload_json,
                    created_at, live_response_mode, live_show_results,
                    live_points, bank_scope
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(target_bank),
                    data.get("import_key"),
                    data.get("item_type") or "",
                    data.get("title") or "",
                    data.get("payload_json") or "{}",
                    data.get("created_at"),
                    data.get("live_response_mode"),
                    data.get("live_show_results"),
                    data.get("live_points"),
                    data.get("bank_scope"),
                ),
            )
            mapping[int(data["id"])] = int(cur.lastrowid)
    return mapping


def _copy_overlays(
    conn: sqlite3.Connection,
    staging_id: int,
    target_id: int,
    question_map: dict[int, int],
) -> None:
    """Copy question overlays that the course library does not already have.

    An overlay already stored for that question is left as the teacher saved it.

    Args:
        conn: School sqlite connection inside a transaction.
        staging_id: Source library id.
        target_id: Destination library id.
        question_map: Staging question id to target question id.
    """
    rows = conn.execute(
        "SELECT * FROM library_question_overlays WHERE library_id = ?",
        (int(staging_id),),
    ).fetchall()
    for row in rows:
        data = dict(row)
        target_question = question_map.get(int(data["question_id"]))
        if not target_question:
            continue
        conn.execute(
            """
            INSERT INTO library_question_overlays (
                library_id, question_id, stem_text, options_json,
                correct_answer, points
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(library_id, question_id) DO NOTHING
            """,
            (
                int(target_id),
                int(target_question),
                data.get("stem_text") or "",
                data.get("options_json") or "[]",
                data.get("correct_answer") or "",
                data.get("points"),
            ),
        )


def _copy_items(
    conn: sqlite3.Connection,
    outline_map: dict[int, int],
    component_maps: dict[str, dict[int, int]],
) -> None:
    """Insert module items whose import key is new on the target outline.

    Args:
        conn: School sqlite connection inside a transaction.
        outline_map: Staging outline id to target outline id.
        component_maps: Component type to staging-id → target-id.
    """
    for staging_outline, target_outline in outline_map.items():
        rows = conn.execute(
            "SELECT * FROM module_items WHERE outline_id = ?",
            (int(staging_outline),),
        ).fetchall()
        for row in rows:
            data = dict(row)
            found = conn.execute(
                """
                SELECT id FROM module_items
                WHERE outline_id = ? AND import_key = ?
                """,
                (int(target_outline), data["import_key"]),
            ).fetchone()
            if found is not None:
                continue
            component_id = data.get("component_id")
            component_type = str(data.get("component_type") or "")
            mapped = component_maps.get(component_type) or {}
            if component_id is not None:
                component_id = mapped.get(int(component_id))
            conn.execute(
                """
                INSERT INTO module_items (
                    outline_id, import_key, title, position, component_type,
                    component_id, source_type, source_href, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(target_outline),
                    data.get("import_key"),
                    data.get("title") or "",
                    int(data.get("position") or 0),
                    component_type,
                    component_id,
                    data.get("source_type") or "",
                    data.get("source_href"),
                    data.get("created_at"),
                ),
            )


def _copy_files(conn: sqlite3.Connection, staging_id: int, target_id: int) -> None:
    """Insert library file rows whose path is not already stored.

    Args:
        conn: School sqlite connection inside a transaction.
        staging_id: Source library id.
        target_id: Destination library id.
    """
    rows = conn.execute(
        "SELECT * FROM library_files WHERE library_id = ?",
        (int(staging_id),),
    ).fetchall()
    for row in rows:
        data = dict(row)
        conn.execute(
            """
            INSERT INTO library_files (library_id, relpath, blob_sha, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(library_id, relpath) DO NOTHING
            """,
            (
                int(target_id),
                data.get("relpath"),
                data.get("blob_sha"),
                data.get("created_at"),
            ),
        )

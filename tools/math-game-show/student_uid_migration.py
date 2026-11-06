#!/usr/bin/env python3
"""Replace the legacy external student column with a random row uid.

The legacy column was an LMS/SIS identifier (or a synthetic stand-in for
one). This migration copies every ``students`` row onto a rebuilt table
that has no such column, assigns an immutable random ``row_uid``, and
rewrites celebration fingerprints that still match the row being copied.

Fingerprints that do not match that row are left unchanged. They are not
attached to whichever row now holds the numeric id. An unresolvable
reference blocks the drop and leaves the database as it was.

The ledger row is inserted in the same transaction as the rebuild. A
failure rolls that transaction back. Restoring production is a database
snapshot restore, not a reverse of this DDL: the legacy values are not
kept as a rollback column.

Repeat imports are not matched here. See ``csv_import`` for how a gradebook
file becomes new rows.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MIGRATION_KEY = "mck365_drop_canvas_id_v1"
LEGACY_COLUMN = "canvas_id"
UID_COLUMN = "row_uid"
_NEW_TABLE = "students_mck365"
_UID_MAP = "mck365_uid_map"

# Same derivation as ``lms/celebration.py``. A drift test locks these.
_FP_KEY_CONTEXT = b"lloves:celebration-fp:v1"
_KID_MESSAGE = b"lloves:celebration-fp:key-id"
_SECRET_ENV = "FLASK_SECRET_KEY"
_SECRET_DEFAULT = "lloves-dev-secret-change-me"

# Settings whose JSON holds celebration fingerprints. Any other setting
# that also carries an ``fp`` key blocks the drop.
CELEBRATION_SETTING_KEYS = (
    "celebration_featured_award",
    "celebration_featured_award_id",
    "celebration_public_snapshot",
    "celebration_held_cards",
)

_CLASS_CSV = re.compile(r"^class-\d+\.csv$")
_GRANT_SOURCES = ("shoutout", "engaged", "mvp")


class StudentUidMigrationInterrupted(RuntimeError):
    """A test failpoint rolled the rebuild back. The old table is intact."""

    def __init__(self, failpoint: str) -> None:
        """Record which failpoint fired.

        Args:
            failpoint: The injected stop, such as ``after_drop``.
        """
        super().__init__(failpoint)
        self.failpoint = failpoint


class _Blocked(Exception):
    """In-transaction stop. The caller rolls back and reports ``reason``."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Interrupt(Exception):
    """In-transaction test stop. The caller rolls back and re-raises."""

    def __init__(self, failpoint: str) -> None:
        super().__init__(failpoint)
        self.failpoint = failpoint


def celebration_fp_key(secret: bytes | str | None = None) -> bytes:
    """HMAC key for celebration fingerprints, matching ``celebration._fp_key``.

    Args:
        secret: App secret. Empty uses ``FLASK_SECRET_KEY`` or the dev default,
            stripped the same way ``create_app`` strips it.

    Returns:
        32-byte key. Not the raw secret.
    """
    if secret in (None, "", b""):
        secret = (os.environ.get(_SECRET_ENV) or "").strip() or _SECRET_DEFAULT
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    return hmac.new(bytes(secret), _FP_KEY_CONTEXT, hashlib.sha256).digest()


def celebration_key_id(fp_key: bytes) -> str:
    """Short id stored as ``kid`` on a celebration snapshot.

    Args:
        fp_key: Output of :func:`celebration_fp_key`.
    """
    return hmac.new(fp_key, _KID_MESSAGE, hashlib.sha256).hexdigest()[:16]


def student_fp(fp_key: bytes, class_id: int, student_id: int, token: str) -> str:
    """Truncated HMAC of class, numeric id, and the row's stable token.

    Args:
        fp_key: Celebration fingerprint key.
        class_id: ``classes.id``.
        student_id: ``students.id``.
        token: ``row_uid``, or the legacy column while that column still exists.

    Returns:
        20 hex characters. The same length ``celebration._student_fp`` stores.
    """
    raw = f"{int(class_id)}:{int(student_id)}:{token or ''}"
    return hmac.new(fp_key, raw.encode("utf-8"), hashlib.sha256).hexdigest()[:20]


def new_row_uid() -> str:
    """Return a new random per-row uid.

    The value is not derived from a name, a clock, a CSV position, a numeric
    id, or an external identifier.
    """
    return uuid.uuid4().hex


def sweep_legacy_roster_csv(uploads_dir: Path | None) -> int:
    """Delete leftover gradebook copies written as ``uploads/class-<id>.csv``.

    Those files are the raw import, not a database backup. Snapshots, WAL
    files, and anything outside this exact name are left alone. File contents
    are not read or logged.

    Args:
        uploads_dir: Game Show uploads directory, or None.

    Returns:
        How many files were removed.
    """
    if uploads_dir is None or not uploads_dir.is_dir():
        return 0
    removed = 0
    for path in uploads_dir.iterdir():
        if path.is_file() and _CLASS_CSV.fullmatch(path.name):
            path.unlink()
            removed += 1
    return removed


def count_legacy_roster_csv(uploads_dir: Path | None) -> int:
    """Count leftover ``class-<id>.csv`` imports without opening them.

    Args:
        uploads_dir: Game Show uploads directory, or None.
    """
    if uploads_dir is None or not uploads_dir.is_dir():
        return 0
    return sum(
        1
        for path in uploads_dir.iterdir()
        if path.is_file() and _CLASS_CSV.fullmatch(path.name)
    )


def migrate_students_off_canvas_id(
    conn: sqlite3.Connection,
    *,
    uploads_dir: Path | None = None,
    secret: bytes | str | None = None,
    failpoint: str | None = None,
) -> dict[str, Any]:
    """Drop the legacy external column once fingerprints are mapped.

    Already-migrated databases keep their ``row_uid`` values, stored
    fingerprints, and row counts. A blocked or interrupted run rolls back
    and leaves the previous table usable.

    Args:
        conn: SQLite connection on the school file. Must not already be
            inside a transaction. Foreign keys are turned off only for the
            rebuild, then restored.
        uploads_dir: When the drop commits, or the column is already gone,
            matching gradebook copies here are deleted.
        secret: Celebration secret. Empty uses the environment, then the
            dev default.
        failpoint: Test-only stop (``after_copy`` or ``after_drop``). The
            transaction is rolled back and
            :class:`StudentUidMigrationInterrupted` is raised.

    Returns:
        ``status`` of ``applied``, ``noop``, or ``blocked``, plus counts.
        Counts do not include names, uids, fingerprints, or legacy values.
    """
    if conn.in_transaction:
        logger.warning("student uid migration blocked: connection_in_transaction")
        return {"status": "blocked", "reason": "connection_in_transaction"}

    fp_key = celebration_fp_key(secret)
    fk_was_on = int(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    if LEGACY_COLUMN not in _column_names(conn, "students"):
        _ensure_ledger(conn)
        _insert_marker_ignore(
            conn,
            {"schema": "already_without_legacy_column"},
        )
        _commit_if_needed(conn)
        removed = sweep_legacy_roster_csv(uploads_dir)
        return {"status": "noop", "legacy_roster_csv_removed": removed}

    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            result = _rebuild(conn, fp_key, failpoint)
            conn.execute("COMMIT")
        except _Blocked as blocked:
            _rollback(conn)
            logger.warning("student uid migration blocked: %s", blocked.reason)
            return {
                "status": "blocked",
                "reason": blocked.reason,
                "legacy_roster_csv_files": count_legacy_roster_csv(uploads_dir),
            }
        except _Interrupt as interrupted:
            _rollback(conn)
            raise StudentUidMigrationInterrupted(interrupted.failpoint) from None
        except Exception:
            _rollback(conn)
            logger.exception("student uid migration rolled back")
            raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON" if fk_was_on else "PRAGMA foreign_keys = OFF")

    removed = sweep_legacy_roster_csv(uploads_dir)
    result["legacy_roster_csv_removed"] = removed
    return result


def _rebuild(conn: sqlite3.Connection, fp_key: bytes, failpoint: str | None) -> dict[str, Any]:
    """Rebuild ``students`` inside the open transaction.

    Args:
        conn: Connection with a write transaction and foreign keys off.
        fp_key: Celebration HMAC key.
        failpoint: Optional test stop.

    Returns:
        Applied-status counts for the ledger and the caller.

    Raises:
        _Blocked: The drop is not safe. Caller rolls back.
        _Interrupt: A failpoint fired. Caller rolls back.
    """
    columns = _table_info(conn, "students")
    names = [col[1] for col in columns]
    if LEGACY_COLUMN not in names:
        _insert_marker_ignore(conn, {"schema": "already_without_legacy_column"})
        return {"status": "noop"}

    if sum(1 for col in columns if col[5]) > 1:
        raise _Blocked("composite_primary_key")
    own_fks = _own_foreign_keys(conn)
    child_fks = _child_foreign_keys(conn)
    index_sql = _saved_index_sql(conn)
    trigger_sql = _saved_trigger_sql(conn)
    before_ids = _id_list(conn)
    before_children = _child_row_counts(conn, child_fks)
    before_grants = _grant_count(conn)

    uid_by_id, legacy_by_id, class_by_id = _load_identity(conn, names)
    settings, mutated = _plan_fingerprints(conn, fp_key, class_by_id, legacy_by_id, uid_by_id)
    _check_failpoint(failpoint, "before_write")

    _create_replacement(conn, columns, own_fks)
    conn.execute(f"CREATE TEMP TABLE {_UID_MAP} (student_id INTEGER PRIMARY KEY, row_uid TEXT NOT NULL)")
    conn.executemany(
        f"INSERT INTO {_UID_MAP} (student_id, row_uid) VALUES (?, ?)",
        list(uid_by_id.items()),
    )
    kept = [name for name in names if name != LEGACY_COLUMN]
    insert_cols = kept + ([] if UID_COLUMN in kept else [UID_COLUMN])
    select_cols = []
    for name in kept:
        if name == UID_COLUMN:
            select_cols.append(f"m.{_q(UID_COLUMN)}")
        else:
            select_cols.append(f"o.{_q(name)}")
    if UID_COLUMN not in kept:
        select_cols.append(f"m.{_q(UID_COLUMN)}")
    conn.execute(
        f"INSERT INTO {_q(_NEW_TABLE)} ({', '.join(_q(name) for name in insert_cols)}) "
        f"SELECT {', '.join(select_cols)} FROM {_q('students')} AS o "
        f"JOIN {_UID_MAP} AS m ON m.student_id = o.id"
    )
    copied = conn.execute(f"SELECT COUNT(*) FROM {_q(_NEW_TABLE)}").fetchone()[0]
    if int(copied) != len(before_ids):
        raise _Blocked("row_count_mismatch")
    _check_failpoint(failpoint, "after_copy")

    for key, raw in mutated:
        conn.execute(
            "UPDATE school_settings SET value = ? WHERE key = ?",
            (raw, key),
        )
    grants_removed = _detach_reused_grants(conn, settings["detached_ids"])
    _check_failpoint(failpoint, "before_drop")

    conn.execute(f"DROP TABLE {_q('students')}")
    _check_failpoint(failpoint, "after_drop")
    conn.execute(f"ALTER TABLE {_q(_NEW_TABLE)} RENAME TO {_q('students')}")
    for sql in index_sql:
        conn.execute(sql)
    for sql in trigger_sql:
        conn.execute(sql)
    conn.execute(f"DROP TABLE {_UID_MAP}")

    after_ids = _id_list(conn)
    if after_ids != before_ids:
        raise _Blocked("student_id_set_changed")
    after_children = _child_row_counts(conn, child_fks)
    expected_children = dict(before_children)
    if "avatar_reward_grants" in expected_children:
        expected_children["avatar_reward_grants"] -= grants_removed
    if after_children != expected_children:
        raise _Blocked("child_row_count_changed")
    if _child_foreign_keys(conn) != child_fks or _own_foreign_keys(conn) != own_fks:
        raise _Blocked("foreign_keys_changed")
    if not _row_uid_unique(conn):
        raise _Blocked("row_uid_not_unique")
    if conn.execute(
        f"SELECT COUNT(*) FROM students WHERE {UID_COLUMN} IS NULL OR TRIM({UID_COLUMN}) = ''"
    ).fetchone()[0]:
        raise _Blocked("row_uid_missing")
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise _Blocked("foreign_key_check_failed")
    integrity = conn.execute("PRAGMA integrity_check").fetchall()
    if [row[0] for row in integrity] != ["ok"]:
        raise _Blocked("integrity_check_failed")
    if _grant_count(conn) + grants_removed != before_grants:
        raise _Blocked("grant_count_mismatch")

    detail = {
        "students": len(before_ids),
        "fingerprints_rewritten": settings["rewritten"],
        "fingerprints_detached": settings["detached"],
        "fingerprints_orphaned": settings["orphaned"],
        "grants_removed": grants_removed,
        "child_rows": before_children,
    }
    _insert_marker_ignore(conn, detail)
    return {"status": "applied", **detail}


def _plan_fingerprints(
    conn: sqlite3.Connection,
    fp_key: bytes,
    class_by_id: dict[int, int],
    legacy_by_id: dict[int, str],
    uid_by_id: dict[int, str],
) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    """Decide fingerprint rewrites. Writes nothing.

    Args:
        conn: Open transaction.
        fp_key: Celebration HMAC key.
        class_by_id: ``students.id`` to ``class_id`` for live rows.
        legacy_by_id: ``students.id`` to the legacy column value.
        uid_by_id: ``students.id`` to the uid that will be stored.

    Returns:
        Counts plus detached numeric ids, and the settings to write.

    Raises:
        _Blocked: A reference cannot be mapped without guessing.
    """
    kid = celebration_key_id(fp_key)
    loaded = _load_settings(conn)
    _reject_unknown_fp_stores(loaded)
    _reject_kid_mismatch(loaded, kid)
    stats: dict[str, Any] = {
        "rewritten": 0,
        "detached": 0,
        "orphaned": 0,
        "detached_ids": set(),
        "matched_ids": set(),
    }
    new_fps: set[str] = set()
    leftover_fps: set[str] = set()
    mutated: list[tuple[str, str]] = []
    for key, raw, payload in loaded:
        if key not in CELEBRATION_SETTING_KEYS or payload is None:
            continue
        _visit_refs(
            payload,
            fp_key,
            class_by_id,
            legacy_by_id,
            uid_by_id,
            stats,
            new_fps,
            leftover_fps,
        )
        encoded = json.dumps(payload)
        if encoded != raw:
            mutated.append((key, encoded))
    if stats["matched_ids"] & stats["detached_ids"]:
        raise _Blocked("inconsistent_fingerprint")
    if new_fps & leftover_fps:
        raise _Blocked("fingerprint_collision")
    return stats, mutated


def _visit_refs(
    node: Any,
    fp_key: bytes,
    class_by_id: dict[int, int],
    legacy_by_id: dict[int, str],
    uid_by_id: dict[int, str],
    stats: dict[str, Any],
    new_fps: set[str],
    leftover_fps: set[str],
) -> None:
    """Rewrite one JSON tree's celebration refs in memory.

    A ref matches only when its stored fingerprint equals the HMAC of the
    named class, the named numeric id, and that row's legacy value. Any
    other case is left unchanged.

    Args:
        node: Parsed JSON.
        fp_key: Celebration HMAC key.
        class_by_id: Live class ids by student id.
        legacy_by_id: Legacy column values by student id.
        uid_by_id: Replacement uids by student id.
        stats: Running counts. Mutated.
        new_fps: Rewritten fingerprints. Mutated.
        leftover_fps: Fingerprints left as stored. Mutated.

    Raises:
        _Blocked: The ref has ids but no fingerprint, or a fingerprint
            without ids.
    """
    if isinstance(node, list):
        for item in node:
            _visit_refs(
                item, fp_key, class_by_id, legacy_by_id, uid_by_id, stats, new_fps, leftover_fps
            )
        return
    if not isinstance(node, dict):
        return
    if "class_id" in node and "student_id" in node:
        fp = str(node.get("fp") or "")
        if not fp:
            raise _Blocked("unbound_reference")
        try:
            class_id = int(node["class_id"])
            student_id = int(node["student_id"])
        except (TypeError, ValueError) as exc:
            raise _Blocked("unbound_reference") from exc
        if student_id not in legacy_by_id or class_by_id.get(student_id) != class_id:
            stats["orphaned"] += 1
            leftover_fps.add(fp)
        else:
            previous = student_fp(fp_key, class_id, student_id, legacy_by_id[student_id])
            if _same_fp(fp, previous):
                updated = student_fp(fp_key, class_id, student_id, uid_by_id[student_id])
                node["fp"] = updated
                stats["rewritten"] += 1
                stats["matched_ids"].add((class_id, student_id))
                new_fps.add(updated)
            else:
                stats["detached"] += 1
                stats["detached_ids"].add((class_id, student_id))
                leftover_fps.add(fp)
    elif "fp" in node:
        raise _Blocked("unbound_reference")
    for value in node.values():
        if isinstance(value, (dict, list)):
            _visit_refs(
                value, fp_key, class_by_id, legacy_by_id, uid_by_id, stats, new_fps, leftover_fps
            )


def _load_identity(
    conn: sqlite3.Connection, names: list[str]
) -> tuple[dict[int, str], dict[int, str], dict[int, int]]:
    """Read ids and legacy values, and assign uids without logging them.

    Existing non-empty ``row_uid`` values are kept. Missing ones are random.

    Args:
        conn: Open transaction.
        names: Current ``students`` column names.

    Returns:
        ``(uid_by_id, legacy_by_id, class_by_id)``.
    """
    select_uid = f", {UID_COLUMN}" if UID_COLUMN in names else ""
    rows = conn.execute(
        f"SELECT id, class_id, {LEGACY_COLUMN}{select_uid} FROM students ORDER BY id"
    ).fetchall()
    uid_by_id: dict[int, str] = {}
    legacy_by_id: dict[int, str] = {}
    class_by_id: dict[int, int] = {}
    seen: set[str] = set()
    for row in rows:
        student_id = int(row[0])
        class_by_id[student_id] = int(row[1])
        legacy_by_id[student_id] = "" if row[2] is None else str(row[2])
        current = ""
        if UID_COLUMN in names and row[3] is not None:
            current = str(row[3]).strip()
        if not current:
            current = new_row_uid()
        if current in seen:
            raise _Blocked("row_uid_not_unique")
        seen.add(current)
        uid_by_id[student_id] = current
    return uid_by_id, legacy_by_id, class_by_id


def _load_settings(
    conn: sqlite3.Connection,
) -> list[tuple[str, str, Any]]:
    """Return school settings as ``(key, raw, parsed)``.

    Parsed is None when the value is empty. Invalid JSON on a celebration
    key blocks the drop.

    Args:
        conn: Open transaction.
    """
    if not _table_exists(conn, "school_settings"):
        return []
    loaded: list[tuple[str, str, Any]] = []
    for key, raw in conn.execute("SELECT key, value FROM school_settings"):
        text = "" if raw is None else str(raw)
        if not text.strip():
            loaded.append((str(key), text, None))
            continue
        try:
            loaded.append((str(key), text, json.loads(text)))
        except json.JSONDecodeError as exc:
            if str(key) in CELEBRATION_SETTING_KEYS:
                raise _Blocked("unparsed_celebration_json") from exc
            loaded.append((str(key), text, None))
    return loaded


def _reject_unknown_fp_stores(loaded: list[tuple[str, str, Any]]) -> None:
    """Block when a non-celebration setting contains an ``fp`` key.

    Args:
        loaded: Output of :func:`_load_settings`.
    """
    for key, _raw, payload in loaded:
        if key in CELEBRATION_SETTING_KEYS or payload is None:
            continue
        if _contains_key(payload, "fp"):
            raise _Blocked("unknown_fingerprint_store")


def _reject_kid_mismatch(loaded: list[tuple[str, str, Any]], kid: str) -> None:
    """Block when a celebration payload was signed with another key.

    Args:
        loaded: Output of :func:`_load_settings`.
        kid: Key id for the secret this process will verify with.
    """
    for key, _raw, payload in loaded:
        if key not in CELEBRATION_SETTING_KEYS or not isinstance(payload, dict):
            continue
        stored = payload.get("kid")
        if stored in (None, ""):
            continue
        if str(stored) != kid:
            raise _Blocked("kid_mismatch")


def _contains_key(node: Any, wanted: str) -> bool:
    """Return True when any dict in ``node`` has ``wanted`` as a key."""
    if isinstance(node, dict):
        if wanted in node:
            return True
        return any(_contains_key(value, wanted) for value in node.values())
    if isinstance(node, list):
        return any(_contains_key(value, wanted) for value in node)
    return False


def _detach_reused_grants(conn: sqlite3.Connection, detached_ids: set[tuple[int, int]]) -> int:
    """Remove celebration grants whose numeric id now belongs to someone else.

    A grant is removed only when a stored fingerprint names that class and
    id and does not match the row's legacy value. Matching winners keep
    their grants, including claimed avatars and source refs.

    Args:
        conn: Open transaction.
        detached_ids: ``(class_id, student_id)`` pairs proven not to be the
            original fingerprint owner.

    Returns:
        Rows deleted.
    """
    if not detached_ids or not _table_exists(conn, "avatar_reward_grants"):
        return 0
    removed = 0
    marks = ",".join("?" for _ in _GRANT_SOURCES)
    for class_id, student_id in detached_ids:
        cur = conn.execute(
            f"DELETE FROM avatar_reward_grants "
            f"WHERE class_id = ? AND student_id = ? AND source IN ({marks})",
            (int(class_id), int(student_id), *_GRANT_SOURCES),
        )
        removed += int(cur.rowcount or 0)
    return removed


def _create_replacement(
    conn: sqlite3.Connection,
    columns: list[tuple[Any, ...]],
    own_fks: tuple[tuple[str, str, str, str, str], ...],
) -> None:
    """Create the replacement ``students`` table without the legacy column.

    Args:
        conn: Open transaction.
        columns: ``PRAGMA table_info`` rows.
        own_fks: Foreign keys declared on ``students``.
    """
    fk_by_col = {fk[0]: fk for fk in own_fks}
    parts: list[str] = []
    for col in columns:
        if col[1] == LEGACY_COLUMN:
            continue
        parts.append(_column_sql(col, fk_by_col.get(col[1])))
    if not any(col[1] == UID_COLUMN for col in columns):
        parts.append(f"{_q(UID_COLUMN)} TEXT NOT NULL UNIQUE")
    conn.execute(f"CREATE TABLE {_q(_NEW_TABLE)} ({', '.join(parts)})")


def _column_sql(col: tuple[Any, ...], fk: tuple[str, str, str, str, str] | None) -> str:
    """One column definition, including its foreign-key action when it has one.

    Args:
        col: ``PRAGMA table_info`` row.
        fk: ``(from_col, table, to_col, on_update, on_delete)`` or None.
    """
    name, col_type, notnull, default, pk = col[1], col[2], col[3], col[4], col[5]
    pieces = [_q(name)]
    if col_type:
        pieces.append(str(col_type))
    if pk:
        pieces.append("PRIMARY KEY")
    elif notnull:
        pieces.append("NOT NULL")
    if default is not None:
        pieces.append(f"DEFAULT {default}")
    if fk is not None:
        _from, table, to_col, on_update, on_delete = fk
        clause = f"REFERENCES {_q(table)}({_q(to_col)})"
        if on_update and on_update.upper() != "NO ACTION":
            clause += f" ON UPDATE {on_update}"
        if on_delete and on_delete.upper() != "NO ACTION":
            clause += f" ON DELETE {on_delete}"
        pieces.append(clause)
    return " ".join(pieces)


def _own_foreign_keys(conn: sqlite3.Connection) -> tuple[tuple[str, str, str, str, str], ...]:
    """Foreign keys declared on ``students``, or on the replacement after rename.

    Args:
        conn: Open connection.

    Returns:
        ``(from_col, dest_table, dest_col, on_update, on_delete)`` rows.

    Raises:
        _Blocked: A composite foreign key cannot be copied inline.
    """
    found = []
    for row in conn.execute("PRAGMA foreign_key_list(students)").fetchall():
        if int(row[1]) != 0:
            raise _Blocked("composite_foreign_key")
        found.append((str(row[3]), str(row[2]), str(row[4]), str(row[5]), str(row[6])))
    return tuple(sorted(found))


def _child_foreign_keys(
    conn: sqlite3.Connection,
) -> tuple[tuple[str, str, str, str, str], ...]:
    """Foreign keys in other tables that point at ``students``.

    Args:
        conn: Open connection.

    Returns:
        ``(child_table, from_col, to_col, on_update, on_delete)`` rows.
    """
    found = []
    for (table,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ):
        if table in {_NEW_TABLE, "students"}:
            continue
        for row in conn.execute(f"PRAGMA foreign_key_list({_q(table)})"):
            if str(row[2]) != "students":
                continue
            found.append((str(table), str(row[3]), str(row[4]), str(row[5]), str(row[6])))
    return tuple(sorted(found))


def _child_row_counts(
    conn: sqlite3.Connection,
    child_fks: tuple[tuple[str, str, str, str, str], ...],
) -> dict[str, int]:
    """Row counts for tables that reference ``students``, plus grant and answer tables.

    Args:
        conn: Open connection.
        child_fks: Output of :func:`_child_foreign_keys`.
    """
    names = {fk[0] for fk in child_fks}
    for extra in (
        "avatar_reward_grants",
        "live_session_attendees",
        "live_result_snapshots",
        "module_reflection_flags",
    ):
        if _table_exists(conn, extra):
            names.add(extra)
    return {name: _count(conn, name) for name in sorted(names)}


def _saved_index_sql(conn: sqlite3.Connection) -> list[str]:
    """CREATE INDEX statements on ``students`` that do not mention the legacy column.

    Args:
        conn: Open connection. Read before the table is dropped.
    """
    saved = []
    for sql, in conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'students' AND sql IS NOT NULL"
    ):
        if LEGACY_COLUMN in str(sql):
            continue
        saved.append(str(sql))
    return saved


def _saved_trigger_sql(conn: sqlite3.Connection) -> list[str]:
    """CREATE TRIGGER statements on ``students``.

    Args:
        conn: Open connection. Read before the table is dropped.
    """
    return [
        str(sql)
        for (sql,) in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'students' AND sql IS NOT NULL"
        )
    ]


def _row_uid_unique(conn: sqlite3.Connection) -> bool:
    """True when some unique index covers ``row_uid`` alone.

    Args:
        conn: Open connection after the rename.
    """
    for row in conn.execute("PRAGMA index_list(students)"):
        if int(row[2]) != 1:
            continue
        info = list(conn.execute(f"PRAGMA index_info({_q(row[1])})"))
        if len(info) == 1 and str(info[0][2]) == UID_COLUMN:
            return True
    return False


def _id_list(conn: sqlite3.Connection) -> list[int]:
    """Every ``students.id`` in order. Compared, not logged."""
    return [int(row[0]) for row in conn.execute("SELECT id FROM students ORDER BY id")]


def _grant_count(conn: sqlite3.Connection) -> int:
    """How many avatar reward grants exist, or 0 when the table is absent."""
    if not _table_exists(conn, "avatar_reward_grants"):
        return 0
    return _count(conn, "avatar_reward_grants")


def _count(conn: sqlite3.Connection, table: str) -> int:
    """Return the row count of one table.

    Args:
        conn: Open connection.
        table: Table name from sqlite_master, not user input.
    """
    return int(conn.execute(f"SELECT COUNT(*) FROM {_q(table)}").fetchone()[0])


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    """Return True when ``name`` is a table in this file."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    """Column names for ``table``, or an empty set when it is missing."""
    if not _table_exists(conn, table):
        return set()
    return {str(col[1]) for col in _table_info(conn, table)}


def _table_info(conn: sqlite3.Connection, table: str) -> list[tuple[Any, ...]]:
    """``PRAGMA table_info`` rows for ``table``."""
    return list(conn.execute(f"PRAGMA table_info({_q(table)})"))


def _ensure_ledger(conn: sqlite3.Connection) -> None:
    """Create the data-migration marker table when this file does not have it."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lms_data_migrations (
            migration_key TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            detail_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )


def _insert_marker_ignore(conn: sqlite3.Connection, detail: dict[str, Any]) -> None:
    """Insert the success marker once. A second run does not refresh it.

    Args:
        conn: Open connection. Caller commits.
        detail: Counts only. No names, uids, or legacy values.
    """
    _ensure_ledger(conn)
    conn.execute(
        """
        INSERT OR IGNORE INTO lms_data_migrations
            (migration_key, status, applied_at, detail_json)
        VALUES (?, 'applied', ?, ?)
        """,
        (
            MIGRATION_KEY,
            datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            json.dumps(detail, sort_keys=True),
        ),
    )


def _check_failpoint(failpoint: str | None, name: str) -> None:
    """Raise when a test asked to stop at ``name``.

    Args:
        failpoint: Requested stop, or None in production.
        name: The stop this call represents.
    """
    if failpoint == name:
        raise _Interrupt(name)


def _rollback(conn: sqlite3.Connection) -> None:
    """Roll back the open write transaction, if one is open."""
    if conn.in_transaction:
        conn.execute("ROLLBACK")


def _commit_if_needed(conn: sqlite3.Connection) -> None:
    """Commit when this connection is holding a transaction open.

    Autocommit connections persist each statement on their own. A connection
    with an isolation level still needs an explicit commit for the marker.
    """
    if conn.in_transaction:
        conn.commit()


def _same_fp(stored: str, expected: str) -> bool:
    """Constant-time fingerprint compare. Different lengths do not match."""
    if len(stored) != len(expected):
        return False
    return hmac.compare_digest(stored, expected)


def _q(name: str) -> str:
    """Quote one SQL identifier.

    Args:
        name: Table or column name taken from sqlite_master or this module.
    """
    return '"' + str(name).replace('"', '""') + '"'

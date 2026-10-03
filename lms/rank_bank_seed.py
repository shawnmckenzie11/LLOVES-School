"""Store the reviewed rank items in per-module course banks (MCK-169 part B).

``seeds/rank_items/{CODE}.json`` (git-tracked, built by
``scripts/build_rank_items.py``) holds the Module Director-reviewed rank
items. This module upserts them into ``question_banks`` / ``questions``:

- one bank per course module, ``import_key = rank-bank:<CODE>:M<n>``;
- one row per item, ``import_key = rank:<candidate id>``, stored as
  ``essay_question`` with payload ``type: rank`` (what bank search reads).

Re-running is safe:

- each row records ``rank_catalogue_hash``, a hash of its stored content.
  A row whose content no longer matches its hash was edited by a teacher
  and is never overwritten;
- a refreshed row's text reaches class imports (``class_live_playlist_placements``
  with that ``source_question_id``) only when the import still shows exactly
  what the old row produced; a teacher-edited import is never touched (MCK-178);
- rows that leave the catalogue are flagged ``retired`` (never deleted,
  because class imports point at them) and search skips them;
- banks and rows insert with ``ON CONFLICT DO NOTHING`` on the existing
  ``UNIQUE(library_id, import_key)`` / ``UNIQUE(bank_id, import_key)``, inside
  one ``BEGIN IMMEDIATE`` transaction, so concurrent workers never duplicate.

A bank is linked to its module only when this module creates it. A teacher
who later unconfirms the bank is not overridden.

``ensure_rank_bank`` is the cheap per-search check: a per-process memo, then
one small query of the bank version markers, and a seed only when stale.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

RANK_ITEMS_DIR = Path(__file__).resolve().parent / "seeds" / "rank_items"
RANK_BANK_KEY_PREFIX = "rank-bank"
RANK_ITEM_KEY_PREFIX = "rank"
#: Payload keys that describe the row rather than its content. They are
#: left out of the content hash, so flagging or re-versioning a row does
#: not make it look teacher-edited.
META_FIELDS = frozenset({"rank_catalogue_hash", "retired", "retired_at"})
_ENSURE_LOCK = threading.Lock()


def rank_bank_key(course_code: str, module_number: int) -> str:
    """Return ``rank-bank:<CODE>:M<n>``."""
    return f"{RANK_BANK_KEY_PREFIX}:{str(course_code).strip().upper()}:M{int(module_number)}"


def is_rank_bank_key(import_key: Any) -> bool:
    """True for a seeded rank bank (``rank-bank:<CODE>:M<n>``)."""
    return str(import_key or "").startswith(f"{RANK_BANK_KEY_PREFIX}:")


def rank_item_key(candidate_id: str) -> str:
    """Return ``rank:<candidate id>``."""
    return f"{RANK_ITEM_KEY_PREFIX}:{str(candidate_id).strip()}"


def rank_bank_title(course_code: str, module_number: int) -> str:
    """Bank label shown in Import and the Question banks tab."""
    return f"{str(course_code).strip().upper()} Module {int(module_number)} rank items"


@lru_cache(maxsize=16)
def _catalogue(course_code: str, items_dir: str) -> tuple[str, tuple[dict[str, Any], ...]]:
    path = Path(items_dir) / f"{course_code}.json"
    if not path.is_file():
        return "", ()
    raw = path.read_bytes()
    doc = json.loads(raw.decode("utf-8"))
    items = doc.get("items") if isinstance(doc, dict) else None
    version = hashlib.sha256(raw).hexdigest()[:16]
    return version, tuple(item for item in items or [] if isinstance(item, dict))


def load_rank_catalogue(
    course_code: str, items_dir: Path | None = None
) -> tuple[str, list[dict[str, Any]]]:
    """Return ``(version, items)`` for one course; ``("", [])`` when none.

    Args:
        course_code: Ontario course code.
        items_dir: Override of ``seeds/rank_items`` (tests).
    """
    code = str(course_code or "").strip().upper()
    version, items = _catalogue(code, str(items_dir or RANK_ITEMS_DIR))
    return version, [json.loads(json.dumps(item)) for item in items]


def catalogue_version(course_code: str, items_dir: Path | None = None) -> str:
    """Return the catalogue version for one course without copying items."""
    code = str(course_code or "").strip().upper()
    return _catalogue(code, str(items_dir or RANK_ITEMS_DIR))[0]


def content_hash(title: str, payload: dict[str, Any]) -> str:
    """Hash a row's title and payload, ignoring :data:`META_FIELDS`."""
    body = {k: v for k, v in payload.items() if k not in META_FIELDS}
    blob = json.dumps({"title": title, "payload": body}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def row_is_pristine(title: str, payload: Any) -> bool:
    """True when a stored row still matches the content this seeder wrote."""
    if not isinstance(payload, dict):
        return False
    stored = str(payload.get("rank_catalogue_hash") or "")
    return bool(stored) and stored == content_hash(title, payload)


def stored_row(item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return ``(title, payload)`` exactly as the seeder stores one item.

    Args:
        item: One catalogue item from ``seeds/rank_items``.
    """
    payload = dict(item.get("payload") or {})
    payload["type"] = "rank"
    payload["bank_kind"] = ""
    payload["catalogue_id"] = str(item["id"])
    payload["live_class_slot"] = str(item.get("live_class_slot") or "")
    payload["md_status"] = str(item.get("md_status") or "")
    payload["expectation_codes"] = list(item.get("expectations") or [])
    if item.get("source_ids"):
        payload["source_ids"] = list(item["source_ids"])
    title = str(payload.get("text") or item["id"]).replace("\n", " ").strip()[:80]
    payload["rank_catalogue_hash"] = content_hash(title, payload)
    return title, payload


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def seed_rank_bank(
    school: Any,
    library_id: int,
    course_code: str,
    *,
    items_dir: Path | None = None,
) -> dict[str, Any]:
    """Insert or refresh one library's rank banks from the catalogue.

    Args:
        school: ``SchoolDB`` instance.
        library_id: ``content_libraries.id``.
        course_code: Ontario course code of the library.
        items_dir: Override of ``seeds/rank_items`` (tests).

    Returns:
        Summary with ``version``, per-module ``banks`` and counts of rows
        ``inserted``, ``updated``, ``unchanged``, ``kept_edited``,
        ``retired`` and ``restored``.
    """
    code = str(course_code or "").strip().upper()
    version, items = load_rank_catalogue(code, items_dir)
    summary: dict[str, Any] = {
        "course": code,
        "version": version,
        "banks": {},
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "kept_edited": 0,
        "retired": 0,
        "restored": 0,
        "imports_updated": 0,
        "imports_kept_edited": 0,
    }
    if not version:
        return summary
    by_module: dict[int, list[dict[str, Any]]] = {}
    for item in items:
        by_module.setdefault(int(item["module"]), []).append(item)
    settings = json.dumps({"rank_catalogue_version": version, "seeded_by": "MCK-169"})
    conn = school.conn
    stamp = _now()
    with school._lock:
        # The school connection runs in autocommit mode, so the transaction
        # is explicit: BEGIN IMMEDIATE takes the write lock up front, which
        # serializes concurrent workers seeding the same file.
        own = not conn.in_transaction
        if own:
            conn.execute("BEGIN IMMEDIATE")
        try:
            bank_ids: dict[int, int] = {}
            for number in sorted(by_module):
                key = rank_bank_key(code, number)
                cur = conn.execute(
                    """
                    INSERT INTO question_banks (
                        library_id, import_key, title, settings_json, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(library_id, import_key) DO NOTHING
                    """,
                    (int(library_id), key, rank_bank_title(code, number), settings, stamp),
                )
                created = cur.rowcount == 1
                bank_id = int(
                    conn.execute(
                        "SELECT id FROM question_banks WHERE library_id = ? AND import_key = ?",
                        (int(library_id), key),
                    ).fetchone()["id"]
                )
                bank_ids[number] = bank_id
                if created:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO course_module_bank_links (
                            library_id, module_number, bank_id, confirmed_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (int(library_id), int(number), bank_id, stamp),
                    )
                summary["banks"][f"M{number}"] = {"bank_id": bank_id, "count": len(by_module[number])}
                for item in by_module[number]:
                    title, payload = stored_row(item)
                    q_key = rank_item_key(item["id"])
                    encoded = json.dumps(payload, ensure_ascii=False)
                    cur = conn.execute(
                        """
                        INSERT INTO questions (
                            bank_id, import_key, item_type, title, payload_json, created_at
                        ) VALUES (?, ?, 'essay_question', ?, ?, ?)
                        ON CONFLICT(bank_id, import_key) DO NOTHING
                        """,
                        (bank_id, q_key, title, encoded, stamp),
                    )
                    if cur.rowcount == 1:
                        summary["inserted"] += 1
                        continue
                    row = conn.execute(
                        "SELECT id, title, payload_json FROM questions WHERE bank_id = ? AND import_key = ?",
                        (bank_id, q_key),
                    ).fetchone()
                    old = _refresh_row(conn, row, title, payload, summary)
                    if old is not None:
                        _refresh_imports(conn, int(row["id"]), old, (title, payload), summary)
            # Retire rows that left the catalogue, in any of this course's
            # rank banks (including a module the catalogue no longer has).
            wanted = {
                (rank_bank_key(code, int(item["module"])), rank_item_key(item["id"]))
                for item in items
            }
            rows = conn.execute(
                """
                SELECT q.id, q.import_key, q.payload_json, b.import_key AS bank_key
                FROM questions q
                JOIN question_banks b ON b.id = q.bank_id
                WHERE b.library_id = ? AND b.import_key LIKE ?
                """,
                (int(library_id), f"{RANK_BANK_KEY_PREFIX}:{code}:M%"),
            ).fetchall()
            for row in rows:
                if (str(row["bank_key"]), str(row["import_key"])) in wanted:
                    continue
                payload = _loads(row["payload_json"])
                if payload.get("retired"):
                    continue
                payload["retired"] = True
                payload["retired_at"] = stamp
                conn.execute(
                    "UPDATE questions SET payload_json = ? WHERE id = ?",
                    (json.dumps(payload, ensure_ascii=False), int(row["id"])),
                )
                summary["retired"] += 1
            conn.execute(
                """
                UPDATE question_banks SET settings_json = ?
                WHERE library_id = ? AND import_key LIKE ?
                """,
                (settings, int(library_id), f"{RANK_BANK_KEY_PREFIX}:{code}:M%"),
            )
            if own:
                conn.execute("COMMIT")
        except Exception:
            if own and conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    return summary


def _loads(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _refresh_row(
    conn: Any,
    row: Any,
    title: str,
    payload: dict[str, Any],
    summary: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    """Update one existing row unless a teacher edited it.

    Returns:
        ``(old_title, old_payload)`` when the row's content was replaced,
        else ``None``.
    """
    current_title = str(row["title"] or "")
    current = _loads(row["payload_json"])
    restore = bool(current.get("retired"))
    if not row_is_pristine(current_title, current):
        summary["kept_edited"] += 1
        if restore:
            # Back in the catalogue: clear the flag, keep the teacher's text.
            current.pop("retired", None)
            current.pop("retired_at", None)
            _swap(conn, row, current_title, current)
            summary["restored"] += 1
        return None
    if current_title == title and current.get("rank_catalogue_hash") == payload["rank_catalogue_hash"] and not restore:
        summary["unchanged"] += 1
        return None
    # Compare-and-swap on the old JSON, so a concurrent teacher save wins.
    swapped = _swap(conn, row, title, payload)
    summary["restored" if restore else "updated"] += 1
    if not swapped or restore:
        return None
    return current_title, current


#: Fields of a class import (``normalize_bank_rank`` output) that come from
#: the bank row's content. Everything else (group mode, preset, publish
#: settings, placement ids) belongs to the class and is kept.
IMPORT_CONTENT_FIELDS = (
    "text",
    "prompt",
    "rank_options",
    "options",
    "choices",
    "rank_key",
    "question_title",
    "teacher_key",
    "teacher_note",
)


def _import_content(question_id: int, bank_id: int, title: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    try:
        from bank_mc_normalize import normalize_bank_rank
    except ImportError:
        from lms.bank_mc_normalize import normalize_bank_rank
    live, _reason = normalize_bank_rank(
        question_id=question_id, bank_id=bank_id, title=title, payload=payload, bank_title=""
    )
    if live is None:
        return None
    return {field: live.get(field) for field in IMPORT_CONTENT_FIELDS}


def _refresh_imports(
    conn: Any,
    question_id: int,
    old: tuple[str, dict[str, Any]],
    new: tuple[str, dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    """Carry a refreshed bank row into class imports that were not edited.

    An import is rewritten only when every content field still equals what
    the old row imported as, so a teacher's edit is never overwritten. The
    lifecycle copies pick the new text up on the next deck refresh.

    Args:
        conn: School connection (inside the seeder's transaction).
        question_id: ``questions.id`` that was refreshed.
        old: ``(title, payload)`` before the refresh.
        new: ``(title, payload)`` after the refresh.
        summary: Seed summary (``imports_updated`` / ``imports_kept_edited``).
    """
    rows = conn.execute(
        "SELECT id, item_json FROM class_live_playlist_placements WHERE source_question_id = ?",
        (int(question_id),),
    ).fetchall()
    if not rows:
        return
    for row in rows:
        item = _loads(row["item_json"])
        if str(item.get("type") or "").strip().lower() != "rank":
            continue
        bank_id = int(item.get("source_bank_id") or item.get("bank_id") or 0)
        before = _import_content(int(question_id), bank_id, old[0], old[1])
        after = _import_content(int(question_id), bank_id, new[0], new[1])
        if before is None or after is None or before == after:
            continue
        current = {field: item.get(field) for field in IMPORT_CONTENT_FIELDS}
        if current != before:
            summary["imports_kept_edited"] += 1
            continue
        updated = dict(item)
        for field, value in after.items():
            if value is None:
                updated.pop(field, None)
            else:
                updated[field] = value
        cur = conn.execute(
            "UPDATE class_live_playlist_placements SET item_json = ? WHERE id = ? AND item_json = ?",
            (json.dumps(updated, ensure_ascii=False), int(row["id"]), row["item_json"]),
        )
        if cur.rowcount == 1:
            summary["imports_updated"] += 1


def _swap(conn: Any, row: Any, title: str, payload: dict[str, Any]) -> bool:
    cur = conn.execute(
        "UPDATE questions SET title = ?, payload_json = ? WHERE id = ? AND payload_json = ?",
        (title, json.dumps(payload, ensure_ascii=False), int(row["id"]), row["payload_json"]),
    )
    return cur.rowcount == 1


def rank_bank_is_current(
    school: Any, library_id: int, course_code: str, *, items_dir: Path | None = None
) -> bool:
    """One small query: every catalogue module bank carries this version.

    Args:
        school: ``SchoolDB`` instance.
        library_id: ``content_libraries.id``.
        course_code: Ontario course code.
        items_dir: Override of ``seeds/rank_items`` (tests).
    """
    code = str(course_code or "").strip().upper()
    version, items = load_rank_catalogue(code, items_dir)
    if not version:
        return True
    wanted = {rank_bank_key(code, int(item["module"])) for item in items}
    with school._lock:
        rows = school.conn.execute(
            """
            SELECT import_key, settings_json FROM question_banks
            WHERE library_id = ? AND import_key LIKE ?
            """,
            (int(library_id), f"{RANK_BANK_KEY_PREFIX}:{code}:M%"),
        ).fetchall()
    have = {str(row["import_key"]): _loads(row["settings_json"]) for row in rows}
    if not wanted.issubset(have):
        return False
    return all(str(s.get("rank_catalogue_version") or "") == version for s in have.values())


def ensure_rank_bank(
    school: Any, library_id: int, *, items_dir: Path | None = None
) -> dict[str, Any] | None:
    """Seed one library's rank banks when they are missing or stale.

    Cheap when current: a per-``SchoolDB`` memo answers without touching
    the database; otherwise one small query, and a seed only when stale.

    Args:
        school: ``SchoolDB`` instance.
        library_id: ``content_libraries.id``.
        items_dir: Override of ``seeds/rank_items`` (tests).

    Returns:
        The seed summary when it ran, else ``None``.
    """
    memo = school.__dict__.setdefault("_rank_bank_memo", {})
    lib = int(library_id)
    marker = memo.get(lib)
    if marker is not None:
        code, version = marker
        if not code or catalogue_version(code, items_dir) == version:
            return None
    library = school.get_library(lib)
    code = str((library or {}).get("ontario_code") or "").strip().upper()
    version = catalogue_version(code, items_dir) if code else ""
    if not version:
        memo[lib] = ("", "")
        return None
    summary = None
    with _ENSURE_LOCK:
        if not rank_bank_is_current(school, lib, code, items_dir=items_dir):
            summary = seed_rank_bank(school, lib, code, items_dir=items_dir)
    memo[lib] = (code, version)
    return summary

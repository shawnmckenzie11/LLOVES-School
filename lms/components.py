"""Read layer for normalized course components.

Modules, Pages, Assignments, Quizzes, and Question banks all render from the
database and the shared blob store. Nothing here reads an unpacked cartridge
tree, so a teacher instance stays thin regardless of course code.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

try:
    from content_store import blob_path
    from ingest import ingest_library
except ImportError:  # package-style import
    from lms.content_store import blob_path
    from lms.ingest import ingest_library

logger = logging.getLogger(__name__)

ITEM_KIND_BY_COMPONENT = {
    "page": "page",
    "assignment": "assignment",
    "quiz": "quiz",
    "header": "header",
    "unsupported": "placeholder",
}


def library_is_ingested(db: Any, library_id: int) -> bool:
    """Return True when a library already has normalized components.

    Args:
        db: School database.
        library_id: ``content_libraries.id``.
    """
    if not library_id:
        return False
    row = db.conn.execute(
        "SELECT COUNT(*) AS n FROM module_outlines WHERE library_id = ?",
        (int(library_id),),
    ).fetchone()
    return int(row["n"]) > 0


def ensure_ingested(
    db: Any,
    data_dir: Path,
    library_id: int,
    unpacked: Path,
    *,
    force: bool = False,
) -> dict[str, Any] | None:
    """Ingest a library's cartridge once, on first use.

    Existing offerings created before the component store have no rows yet.
    This backfills them the first time Modules is opened, then never again.

    Args:
        db: School database.
        data_dir: LMS data volume.
        library_id: ``content_libraries.id``.
        unpacked: Unpacked cartridge root to ingest from.
        force: Re-ingest even when components already exist, refreshing rows
            in place (used by the re-import path after a parser fix).

    Returns:
        Ingest summary when work was done, else None.
    """
    if not library_id:
        return None
    if not force and library_is_ingested(db, int(library_id)):
        return None
    if not Path(unpacked).is_dir():
        return None
    logger.info(
        "%s components for library %s",
        "Re-ingesting" if force else "Backfilling",
        library_id,
    )
    return ingest_library(db, Path(data_dir), int(library_id), Path(unpacked))


def outline_nav(db: Any, library_id: int) -> list[dict[str, Any]]:
    """Build the Modules left-nav from stored outlines and items.

    Args:
        db: School database.
        library_id: ``content_libraries.id``.

    Returns:
        Modules in position order, each with its items.
    """
    if not library_id:
        return []
    outlines = db.conn.execute(
        """
        SELECT id, title, import_key
        FROM module_outlines
        WHERE library_id = ?
        ORDER BY position, id
        """,
        (int(library_id),),
    ).fetchall()
    nav: list[dict[str, Any]] = []
    for outline in outlines:
        items = db.conn.execute(
            """
            SELECT id, title, component_type, component_id, source_type
            FROM module_items
            WHERE outline_id = ?
            ORDER BY position, id
            """,
            (int(outline["id"]),),
        ).fetchall()
        nav.append(
            {
                "title": outline["title"],
                "identifier": outline["import_key"],
                "items": [
                    {
                        "id": int(item["id"]),
                        "title": item["title"] or "Untitled",
                        "component_type": item["component_type"],
                        "component_id": item["component_id"],
                        "content_type": item["source_type"],
                        "kind": ITEM_KIND_BY_COMPONENT.get(
                            item["component_type"], "placeholder"
                        ),
                    }
                    for item in items
                ],
            }
        )
    return nav


def outline_raw_modules(db: Any, library_id: int) -> list[dict[str, Any]]:
    """Return the outline in the shape the syllabus editor expects.

    Mirrors what the cartridge parser produces, so the syllabus editor can be
    driven from stored components rather than the ``.imscc`` file.

    Args:
        db: School database.
        library_id: ``content_libraries.id``.
    """
    return [
        {
            "title": module["title"],
            "identifier": module["identifier"],
            "items": [
                {
                    "title": item["title"],
                    "identifier": str(item["id"]),
                    "content_type": item["content_type"],
                }
                for item in module["items"]
            ],
        }
        for module in outline_nav(db, library_id)
    ]


def get_module_item(db: Any, library_id: int, item_id: int) -> dict[str, Any] | None:
    """Fetch one module item, scoped to a library to prevent cross-course reads.

    Args:
        db: School database.
        library_id: ``content_libraries.id`` that must own the item.
        item_id: ``module_items.id``.
    """
    if not library_id:
        return None
    row = db.conn.execute(
        """
        SELECT mi.*
        FROM module_items mi
        JOIN module_outlines mo ON mo.id = mi.outline_id
        WHERE mi.id = ? AND mo.library_id = ?
        """,
        (int(item_id), int(library_id)),
    ).fetchone()
    return dict(row) if row else None


def get_page(db: Any, library_id: int, page_id: int) -> dict[str, Any] | None:
    """Fetch one page component within a library.

    Args:
        db: School database.
        library_id: Owning library.
        page_id: ``pages.id``.
    """
    row = db.conn.execute(
        "SELECT * FROM pages WHERE id = ? AND library_id = ?",
        (int(page_id), int(library_id)),
    ).fetchone()
    return dict(row) if row else None


def get_assignment(db: Any, library_id: int, assignment_id: int) -> dict[str, Any] | None:
    """Fetch one assignment component within a library.

    Args:
        db: School database.
        library_id: Owning library.
        assignment_id: ``assignments.id``.
    """
    row = db.conn.execute(
        "SELECT * FROM assignments WHERE id = ? AND library_id = ?",
        (int(assignment_id), int(library_id)),
    ).fetchone()
    return dict(row) if row else None


def get_quiz(db: Any, library_id: int, quiz_id: int) -> dict[str, Any] | None:
    """Fetch one quiz component, with its question count.

    Args:
        db: School database.
        library_id: Owning library.
        quiz_id: ``quizzes.id``.
    """
    row = db.conn.execute(
        "SELECT * FROM quizzes WHERE id = ? AND library_id = ?",
        (int(quiz_id), int(library_id)),
    ).fetchone()
    if row is None:
        return None
    data = dict(row)
    counts = db.conn.execute(
        """
        SELECT COUNT(q.id) AS n
        FROM question_banks b
        LEFT JOIN questions q ON q.bank_id = b.id
        WHERE b.library_id = ? AND b.import_key = ?
        """,
        (int(library_id), f"bank:{row['import_key']}"),
    ).fetchone()
    data["question_count"] = int(counts["n"]) if counts else 0
    return data


def get_question_bank(
    db: Any, library_id: int, bank_id: int
) -> dict[str, Any] | None:
    """Fetch one question bank within a library.

    Args:
        db: School database.
        library_id: Owning library.
        bank_id: ``question_banks.id``.
    """
    row = db.conn.execute(
        "SELECT * FROM question_banks WHERE id = ? AND library_id = ?",
        (int(bank_id), int(library_id)),
    ).fetchone()
    return _with_settings(row) if row else None


def quiz_questions(
    db: Any, library_id: int, quiz_import_key: str
) -> list[dict[str, Any]]:
    """Return the questions linked to one quiz, in cartridge order.

    A quiz's questions live in the bank keyed ``bank:<quiz import key>``, which
    ingest fills with the quiz's own items plus every item of the banks it
    draws from.

    Args:
        db: School database.
        library_id: Owning library.
        quiz_import_key: ``quizzes.import_key``.
    """
    row = db.conn.execute(
        "SELECT id FROM question_banks WHERE library_id = ? AND import_key = ?",
        (int(library_id), f"bank:{quiz_import_key}"),
    ).fetchone()
    if row is None:
        return []
    return list_questions(db, int(library_id), int(row["id"]))


def library_file_path(
    db: Any, data_dir: Path, library_id: int, relpath: str
) -> Path | None:
    """Resolve a cartridge-relative asset path to a blob file on disk.

    Args:
        db: School database.
        data_dir: LMS data volume.
        library_id: Owning library.
        relpath: Cartridge-relative path such as ``web_resources/x.png``.

    Returns:
        Existing blob path, or None when unknown.
    """
    if not library_id or not relpath:
        return None
    key = str(relpath).lstrip("/")
    row = db.conn.execute(
        "SELECT blob_sha FROM library_files WHERE library_id = ? AND relpath = ?",
        (int(library_id), key),
    ).fetchone()
    if row is None and not key.startswith("web_resources/"):
        row = db.conn.execute(
            "SELECT blob_sha FROM library_files WHERE library_id = ? AND relpath = ?",
            (int(library_id), f"web_resources/{key}"),
        ).fetchone()
    if row is None:
        return None
    path = blob_path(Path(data_dir), str(row["blob_sha"]))
    return path if path.is_file() else None


def blob_file_path(data_dir: Path, sha256: str) -> Path | None:
    """Return an existing blob path for a digest, or None.

    Args:
        data_dir: LMS data volume.
        sha256: Blob digest.
    """
    if not sha256:
        return None
    path = blob_path(Path(data_dir), str(sha256))
    return path if path.is_file() else None


def list_pages(db: Any, library_id: int) -> list[dict[str, Any]]:
    """List page components for the staff Pages tab.

    Args:
        db: School database.
        library_id: Owning library.
    """
    if not library_id:
        return []
    rows = db.conn.execute(
        """
        SELECT id, import_key, kind, title, url, blob_sha,
               length(html_text) AS html_bytes
        FROM pages WHERE library_id = ?
        ORDER BY kind, title
        """,
        (int(library_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def list_assignments(db: Any, library_id: int) -> list[dict[str, Any]]:
    """List assignment components for the staff Assignments tab.

    Args:
        db: School database.
        library_id: Owning library.
    """
    if not library_id:
        return []
    rows = db.conn.execute(
        """
        SELECT id, import_key, title, points, settings_json
        FROM assignments WHERE library_id = ?
        ORDER BY title
        """,
        (int(library_id),),
    ).fetchall()
    return [_with_settings(row) for row in rows]


def list_quizzes(db: Any, library_id: int) -> list[dict[str, Any]]:
    """List quiz components for the staff Quizzes tab.

    Args:
        db: School database.
        library_id: Owning library.
    """
    if not library_id:
        return []
    rows = db.conn.execute(
        """
        SELECT q.id, q.import_key, q.title, q.settings_json,
               (SELECT COUNT(*) FROM questions qq
                JOIN question_banks b ON b.id = qq.bank_id
                WHERE b.library_id = q.library_id
                  AND b.import_key = 'bank:' || q.import_key) AS question_count
        FROM quizzes q WHERE q.library_id = ?
        ORDER BY q.title
        """,
        (int(library_id),),
    ).fetchall()
    return [_with_settings(row) for row in rows]


def list_question_banks(db: Any, library_id: int) -> list[dict[str, Any]]:
    """List question banks with item counts for the staff tab.

    Args:
        db: School database.
        library_id: Owning library.
    """
    if not library_id:
        return []
    rows = db.conn.execute(
        """
        SELECT b.id, b.import_key, b.title,
               COUNT(q.id) AS question_count
        FROM question_banks b
        LEFT JOIN questions q ON q.bank_id = b.id
        WHERE b.library_id = ?
        GROUP BY b.id
        ORDER BY b.title
        """,
        (int(library_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def list_questions(db: Any, library_id: int, bank_id: int) -> list[dict[str, Any]]:
    """List questions in one bank, scoped to a library.

    Args:
        db: School database.
        library_id: Owning library.
        bank_id: ``question_banks.id``.
    """
    rows = db.conn.execute(
        """
        SELECT q.id, q.import_key, q.item_type, q.title, q.payload_json
        FROM questions q
        JOIN question_banks b ON b.id = q.bank_id
        WHERE b.library_id = ? AND b.id = ?
        ORDER BY q.id
        """,
        (int(library_id), int(bank_id)),
    ).fetchall()
    out = []
    for row in rows:
        data = dict(row)
        try:
            data["payload"] = json.loads(data.pop("payload_json") or "{}")
        except json.JSONDecodeError:
            data["payload"] = {}
        out.append(data)
    # Cartridge order lives in the payload so re-ingest cannot be reordered by
    # sqlite rowids; rows written before positions existed sort last by id.
    out.sort(key=lambda item: (int(item["payload"].get("position") or 10**6), item["id"]))
    return out


def library_counts(db: Any, library_id: int) -> dict[str, int]:
    """Return component counts used for staff tab badges.

    Args:
        db: School database.
        library_id: Owning library.
    """
    if not library_id:
        return {"pages": 0, "assignments": 0, "quizzes": 0, "question_banks": 0}
    out: dict[str, int] = {}
    for table in ("pages", "assignments", "quizzes", "question_banks"):
        row = db.conn.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE library_id = ?",
            (int(library_id),),
        ).fetchone()
        out[table] = int(row["n"])
    return out


def library_pack_summary(db: Any, library_id: int | None) -> dict[str, Any]:
    """Counts for the Admin base-layer picker (modules present and loaded).

    Assignments and tests are items **in modules**, not orphan catalog rows.
    Banks are ok when at least one question bank has a non-zero question total.

    Args:
        db: School database.
        library_id: ``content_libraries.id``, or None/0 for no pack.
    """
    empty = {
        "ingested": False,
        "modules": 0,
        "pages": 0,
        "assignments": 0,
        "tests": 0,
        "banks_ok": False,
        "note": "no pack",
    }
    if not library_id:
        return empty
    if not library_is_ingested(db, int(library_id)):
        return {**empty, "note": "loading…"}
    lib = int(library_id)
    modules = int(
        db.conn.execute(
            "SELECT COUNT(*) AS n FROM module_outlines WHERE library_id = ?",
            (lib,),
        ).fetchone()["n"]
    )
    pages = int(
        db.conn.execute(
            "SELECT COUNT(*) AS n FROM pages WHERE library_id = ?", (lib,)
        ).fetchone()["n"]
    )
    assignments = int(
        db.conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM module_items mi
            JOIN module_outlines mo ON mo.id = mi.outline_id
            WHERE mo.library_id = ? AND mi.component_type = 'assignment'
            """,
            (lib,),
        ).fetchone()["n"]
    )
    tests = int(
        db.conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM module_items mi
            JOIN module_outlines mo ON mo.id = mi.outline_id
            WHERE mo.library_id = ? AND mi.component_type = 'quiz'
            """,
            (lib,),
        ).fetchone()["n"]
    )
    banks_ok = (
        int(
            db.conn.execute(
                """
                SELECT COUNT(*) AS n FROM (
                    SELECT b.id
                    FROM question_banks b
                    JOIN questions q ON q.bank_id = b.id
                    WHERE b.library_id = ?
                    GROUP BY b.id
                    HAVING COUNT(q.id) > 0
                )
                """,
                (lib,),
            ).fetchone()["n"]
        )
        > 0
    )
    note = (
        f"{modules} modules · {pages} pages · {assignments} assignments · "
        f"{tests} tests · banks {'ok' if banks_ok else 'none'}"
    )
    return {
        "ingested": True,
        "modules": modules,
        "pages": pages,
        "assignments": assignments,
        "tests": tests,
        "banks_ok": banks_ok,
        "note": note,
    }


def _with_settings(row: Any) -> dict[str, Any]:
    """Decode a ``settings_json`` column into a nested dict."""
    data = dict(row)
    raw = data.pop("settings_json", None)
    try:
        data["settings"] = json.loads(raw or "{}")
    except json.JSONDecodeError:
        data["settings"] = {}
    return data

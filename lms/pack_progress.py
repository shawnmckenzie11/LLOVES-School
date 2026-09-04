"""Admin/staff pack-install badge truth and base-layer count notes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from components import library_is_ingested
    from modules import read_pack_status
except ImportError:
    from lms.components import library_is_ingested
    from lms.modules import read_pack_status


def pack_ui_state(
    status: dict[str, Any], *, has_library: bool
) -> dict[str, Any]:
    """Badge and one-line copy for Admin Offerings and staff course cards.

    ``library_id`` is set as soon as the file lands, so Installed is reserved
    for a finished unpack (``stage=done``) or an already-usable attached
    library that never wrote a status file.

    Args:
        status: Payload from ``read_pack_status``.
        has_library: True when the offering has a ``library_id``.
    """
    stage = str(status.get("stage") or "idle")
    detail = str(status.get("detail") or "").strip()
    error = status.get("error")
    busy = bool(status.get("busy"))
    if stage == "error":
        line = str(error or detail or "Pack install failed.")
        return {
            "badge": "Failed",
            "badge_class": "badge-warn",
            "line": line,
            "busy": False,
            "stage": stage,
            "detail": detail,
            "error": line,
        }
    if busy:
        line = detail or "Loading module pack…"
        return {
            "badge": "Loading",
            "badge_class": "badge-warn",
            "line": line,
            "busy": True,
            "stage": stage,
            "detail": detail,
            "error": None,
        }
    if has_library:
        line = detail if stage == "done" and detail else "Installed"
        return {
            "badge": "Installed",
            "badge_class": "badge-ok",
            "line": line,
            "busy": False,
            "stage": stage or "done",
            "detail": detail,
            "error": None,
        }
    return {
        "badge": "No pack",
        "badge_class": "badge-warn",
        "line": "No pack",
        "busy": False,
        "stage": stage,
        "detail": detail,
        "error": None,
    }


def annotate_offering_pack(
    offering: dict[str, Any], dest_root: Path
) -> dict[str, Any]:
    """Attach badge/line/busy fields from ``install_status.json``.

    Args:
        offering: Course offering dict.
        dest_root: Shared library (or leftover pack) folder for this offering.
    """
    item = dict(offering)
    status = read_pack_status(dest_root)
    ui = pack_ui_state(status, has_library=bool(item.get("library_id")))
    item["pack_busy"] = ui["busy"]
    item["pack_badge"] = ui["badge"]
    item["pack_badge_class"] = ui["badge_class"]
    item["pack_line"] = ui["line"]
    item["pack_stage"] = ui["stage"]
    item["pack_detail"] = ui["detail"]
    item["pack_error"] = ui["error"]
    return item


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


def instances_payload(school: Any, code: str) -> dict[str, Any]:
    """JSON body for ``GET /it/instances`` including pack notes.

    Args:
        school: School database.
        code: Ontario course code.
    """
    if not code:
        return {"ok": True, "instances": [], "template_note": "no pack"}
    instances = school.list_prior_instances(code)
    for inst in instances:
        lib_id = inst.get("library_id")
        summary = library_pack_summary(school, int(lib_id) if lib_id else 0)
        inst["pack_note"] = summary["note"]
        inst["pack_summary"] = summary
    latest = school.latest_library_for_code(code)
    template = library_pack_summary(school, int(latest["id"]) if latest else 0)
    return {
        "ok": True,
        "instances": instances,
        "template_note": template["note"],
        "template_summary": template,
    }


def status_payload(offering: dict[str, Any], dest_root: Path) -> dict[str, Any]:
    """Merge disk status with badge fields for a JSON poll.

    Args:
        offering: Course offering dict.
        dest_root: Library/pack folder.
    """
    status = read_pack_status(dest_root)
    ui = pack_ui_state(status, has_library=bool(offering.get("library_id")))
    return {**status, **ui}


def finish_install_detail(school: Any, library_id: int | None) -> str:
    """Done-stage sentence including loaded component counts.

    Args:
        school: School database.
        library_id: Attached library, if any.
    """
    if not library_id:
        return "Module pack installed."
    note = library_pack_summary(school, int(library_id)).get("note") or "installed"
    return f"Module pack installed — {note}"

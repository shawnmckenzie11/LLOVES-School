"""Group past live classes into finished modules (MCK-251).

For every class in the active semester, each earlier module is written
once: one ``Finish Module N`` subtotal (via ``freeze_subtotal``) placed
after that module's last session, plus one ``participation_module_marks``
upsert. A module is skipped when either of those already exists, so a
second run cannot mint ``Finish Module 1 2`` through
``_unique_subtotal_name``. The current (latest unfinished) module stays
open. This module never decides that production is safe to write; the
script refuses production before it opens a database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from participation_bands import build_participation_bands
from participation_weight import module_number

_FINISH_SUBTOTAL = re.compile(r"^Finish Module \d+$")


@dataclass
class ModuleAction:
    """One planned or applied step for a class module.

    Attributes:
        class_id: Game-show class id.
        course: Ontario code, or the class course code.
        section: Offering section index.
        module: Module token such as ``M1``.
        action: ``finish``, ``skip-current``, ``skip-finished``, or
            ``skip-no-column``.
        classes: How many live-class columns sit in the module.
        detail: Short reason safe to print.
        through_session_id: Last session of this module. Set on a
            ``finish`` so the subtotal is not pinned to a later module.
    """

    class_id: int
    course: str
    section: int
    module: str
    action: str
    classes: int
    detail: str
    through_session_id: int | None = None


def finish_module_subtotal_name(module: str) -> str:
    """Exact subtotal header written once per module.

    Args:
        module: Module token such as ``M1``.

    Returns:
        ``Finish Module N`` for that module's number.
    """
    return f"Finish Module {module_number(module)}"


def list_current_live_classes(school: Any) -> list[dict[str, Any]]:
    """Non-archived classes in the active semester that have participation.

    Args:
        school: Open ``SchoolDB``.

    Returns:
        Rows with ``id``, ``course``, and ``section``, ordered by course.
    """
    semester = school.get_active_semester()
    if not semester:
        return []
    rows = school.conn.execute(
        """
        SELECT cl.id AS id,
               COALESCE(NULLIF(o.ontario_code, ''), cl.course_code, '') AS course,
               COALESCE(o.section_index, 1) AS section
        FROM classes cl
        JOIN course_offerings o ON o.id = cl.offering_id
        WHERE o.semester_id = ?
          AND o.archived_at IS NULL
        ORDER BY course, section, cl.id
        """,
        (int(semester["id"]),),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        class_id = int(row["id"])
        if not _class_has_participation(school, class_id):
            continue
        out.append(
            {
                "id": class_id,
                "course": str(row["course"] or "").strip() or f"class {class_id}",
                "section": int(row["section"] or 1),
            }
        )
    return out


def plan_participation_module_backfill(school: Any) -> list[ModuleAction]:
    """Describe module finishes without writing.

    Args:
        school: Open ``SchoolDB``.

    Returns:
        One action per numbered module that has live-class columns.
        The current module is ``skip-current``. A module that already
        has a mark row or a ``Finish Module N`` subtotal is
        ``skip-finished``.
    """
    actions: list[ModuleAction] = []
    for course in list_current_live_classes(school):
        actions.extend(_plan_class(school, course))
    return actions


def apply_participation_module_backfill(
    school: Any,
    plan: list[ModuleAction] | None = None,
) -> list[ModuleAction]:
    """Write one subtotal and one mark for each ``finish`` action.

    Re-checks the skip rule immediately before writing, so applying the
    same plan twice does not call ``freeze_subtotal`` again.

    Args:
        school: Open ``SchoolDB``.
        plan: Actions from :func:`plan_participation_module_backfill`.
            When omitted, a new plan is built first.

    Returns:
        The actions that were considered. Skipped steps are unchanged.
    """
    steps = list(plan) if plan is not None else plan_participation_module_backfill(school)
    for step in steps:
        if step.action != "finish":
            continue
        if _should_skip(school, step.class_id, step.module):
            continue
        if step.through_session_id is None:
            raise ValueError(
                f"{step.module} in class {step.class_id} has no last session"
            )
        school.game.freeze_subtotal(
            int(step.class_id),
            name=finish_module_subtotal_name(step.module),
            sort="az",
            through_session_id=int(step.through_session_id),
            return_dashboard=False,
        )
        if _marks_row_exists(school, step.class_id, step.module):
            continue
        school.set_module_complete(step.class_id, step.module, True)
    return steps


def backfill_counts(school: Any) -> dict[str, int]:
    """Row counts printed before and after a dry-run or apply.

    Args:
        school: Open ``SchoolDB``.

    Returns:
        ``marks`` (every ``participation_module_marks`` row) and
        ``finish_subtotals`` (subtotals named exactly ``Finish Module N``).
    """
    marks = int(
        school.conn.execute(
            "SELECT COUNT(*) AS n FROM participation_module_marks"
        ).fetchone()["n"]
    )
    names = [
        str(row["name"] or "")
        for row in school.game.conn.execute("SELECT name FROM subtotals")
    ]
    finish = sum(1 for name in names if _FINISH_SUBTOTAL.fullmatch(name))
    return {"marks": marks, "finish_subtotals": finish}


def format_backfill_report(
    actions: list[ModuleAction],
    *,
    dry_run: bool,
    database: str,
    before: dict[str, int],
    after: dict[str, int],
) -> list[str]:
    """Plain-text report for the script stdout.

    Args:
        actions: Planned or applied actions.
        dry_run: When true, the heading says DRY RUN.
        database: Sqlite path, printed so the operator sees which file.
        before: Counts from :func:`backfill_counts` before any write.
        after: The same counts after apply, or a fresh read on a dry-run.

    Returns:
        Lines with a trailing newline omitted (the caller prints them).
    """
    mode = "DRY RUN" if dry_run else "APPLIED"
    finishes = sum(1 for step in actions if step.action == "finish")
    lines = [
        f"participation module backfill ({mode})",
        f"database: {database}",
        (
            "before: "
            f"participation_module_marks={int(before.get('marks', 0))} "
            f"finish_module_subtotals={int(before.get('finish_subtotals', 0))}"
        ),
        (
            "after: "
            f"participation_module_marks={int(after.get('marks', 0))} "
            f"finish_module_subtotals={int(after.get('finish_subtotals', 0))}"
        ),
        f"planned finishes: {finishes}",
    ]
    if not actions:
        lines.append("(no current live classes with participation)")
        return lines
    grouped: dict[tuple[int, str, int], list[ModuleAction]] = {}
    order: list[tuple[int, str, int]] = []
    for step in actions:
        key = (step.class_id, step.course, step.section)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(step)
    lines.append(f"classes: {len(order)}")
    for key in order:
        class_id, course, section = key
        lines.append(f"class {class_id} {course} section {section}")
        for step in grouped[key]:
            lines.append(
                f"  {step.action} {step.module} — {step.classes} classes — {step.detail}"
            )
    return lines


def _class_has_participation(school: Any, class_id: int) -> bool:
    """True when the class has captured participation items."""
    row = school.conn.execute(
        "SELECT 1 FROM participation_items WHERE class_id = ? LIMIT 1",
        (int(class_id),),
    ).fetchone()
    return row is not None


def _marks_row_exists(school: Any, class_id: int, module: str) -> bool:
    """True when this class and module already have a marks row.

    A C4 run can make ``module_complete`` true without inserting this row.
    Only the row itself is a skip.
    """
    row = school.conn.execute(
        """
        SELECT 1 FROM participation_module_marks
        WHERE class_id = ? AND live_module = ?
        LIMIT 1
        """,
        (int(class_id), str(module)),
    ).fetchone()
    return row is not None


def _finish_subtotal_exists(school: Any, class_id: int, module: str) -> bool:
    """True when a subtotal is already named exactly ``Finish Module N``."""
    label = finish_module_subtotal_name(module)
    row = school.game.conn.execute(
        "SELECT 1 FROM subtotals WHERE class_id = ? AND name = ? LIMIT 1",
        (int(class_id), label),
    ).fetchone()
    return row is not None


def _should_skip(school: Any, class_id: int, module: str) -> bool:
    """Skip when a marks row or a ``Finish Module N`` subtotal already exists."""
    return _marks_row_exists(school, class_id, module) or _finish_subtotal_exists(
        school, class_id, module
    )


def _plan_class(school: Any, course: dict[str, Any]) -> list[ModuleAction]:
    """Plan one class. The current module is never finished."""
    class_id = int(course["id"])
    meta = school.participation_session_meta(class_id)
    columns = [
        {"id": int(session_id), "kind": "session"}
        for session_id in meta
    ]
    modules = {
        str(info.get("live_module") or "").strip().upper()
        for info in meta.values()
        if str(info.get("live_module") or "").strip()
    }
    complete = {
        module: bool(school.module_complete(class_id, module))
        for module in modules
        if module_number(module) > 0
    }
    built = build_participation_bands(columns, meta, complete)
    actions: list[ModuleAction] = []
    for band in built["bands"]:
        module = str(band["module"] or "")
        number = int(band["number"] or 0)
        band_columns = list(band["columns"] or [])
        count = len(band_columns)
        if number <= 0:
            continue
        last_id = int(band_columns[-1]["id"]) if band_columns else None
        base = {
            "class_id": class_id,
            "course": str(course["course"]),
            "section": int(course["section"]),
            "module": module,
            "classes": count,
            "through_session_id": last_id,
        }
        if band["role"] == "current":
            actions.append(
                ModuleAction(
                    **base,
                    action="skip-current",
                    detail="current module stays open",
                )
            )
            continue
        if count <= 0:
            actions.append(
                ModuleAction(
                    **base,
                    action="skip-no-column",
                    detail="no live-class column",
                )
            )
            continue
        has_mark = _marks_row_exists(school, class_id, module)
        has_total = _finish_subtotal_exists(school, class_id, module)
        label = finish_module_subtotal_name(module)
        if has_mark or has_total:
            if has_mark and has_total:
                detail = f"participation mark and {label} subtotal already exist"
            elif has_mark:
                detail = "participation_module_marks row already exists"
            else:
                detail = f"{label} subtotal already exists"
            actions.append(
                ModuleAction(**base, action="skip-finished", detail=detail)
            )
            continue
        actions.append(
            ModuleAction(
                **base,
                action="finish",
                detail=f"one {label} subtotal through session {last_id}, plus one mark",
            )
        )
    return actions

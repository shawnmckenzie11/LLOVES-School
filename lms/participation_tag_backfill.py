"""Propose and write module tags for untagged participation sessions (MCK-265).

A session is untagged when its counted participation run has no
``live_module``. The proposed tag comes from that class's own meeting
calendar. A date the calendar does not cover stays untagged and is
reported as ``UNMAPPED``.

``--apply`` updates blank ``participation_items.live_module`` and
``live_slot`` rows for that run. It does not insert items, marks,
observations, or finish subtotals. A second apply finds the module
filled, so it writes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from participation_schedule import (
    calendar_date_from_stored,
    course_module_slots,
    live_weekdays,
    participation_date_slots,
)

_OFFICE_WEEKDAY = 4


@dataclass(frozen=True)
class TagAction:
    """One untagged participation session and the tag the calendar proposes.

    Attributes:
        class_id: Class primary key.
        course: Ontario course code.
        section: Offering section index.
        session_id: Game-show session id.
        run_key: Counted participation run linked to the session.
        starts_at: Stored session start, used only to order same-day rows.
        session_date: Toronto calendar date, or ``None`` when unreadable.
        module: Proposed module, or ``""`` when unmapped.
        slot: Proposed slot, or ``""`` when unmapped.
        item_rows: Blank ``participation_items`` rows this tag would fill.
    """

    class_id: int
    course: str
    section: int
    session_id: int
    run_key: str
    starts_at: str
    session_date: date | None
    module: str
    slot: str
    item_rows: int

    @property
    def unmapped(self) -> bool:
        """True when the calendar has no slot for this session."""
        return not self.module or not self.slot


def plan_participation_tag_backfill(school: Any) -> list[TagAction]:
    """List untagged participation sessions and a proposed tag for each.

    Args:
        school: Open ``SchoolDB``.

    Returns:
        One action per untagged session, ordered by class, date, and start.
    """
    actions: list[TagAction] = []
    for course in _live_classes(school):
        actions.extend(_plan_class(school, course))
    actions.sort(
        key=lambda row: (
            row.course,
            row.section,
            row.class_id,
            row.session_date or date.max,
            row.starts_at,
            row.session_id,
        )
    )
    return actions


def apply_participation_tag_backfill(
    school: Any, plan: list[TagAction] | None = None
) -> int:
    """Write proposed tags onto blank participation item rows.

    Mapped sessions with no item row are left untagged. Existing module
    text is not overwritten. Marks, observations, and runs are not written.

    Args:
        school: Open ``SchoolDB``.
        plan: Actions from :func:`plan_participation_tag_backfill`.
            Defaults to a fresh plan.

    Returns:
        Number of ``participation_items`` rows updated.
    """
    steps = plan if plan is not None else plan_participation_tag_backfill(school)
    written = 0
    with school._lock:
        for step in steps:
            if step.unmapped or step.item_rows <= 0:
                continue
            cursor = school.conn.execute(
                """
                UPDATE participation_items
                   SET live_module = ?, live_slot = ?
                 WHERE class_id = ?
                   AND run_key = ?
                   AND TRIM(COALESCE(live_module, '')) = ''
                """,
                (step.module, step.slot, int(step.class_id), step.run_key),
            )
            written += int(cursor.rowcount or 0)
        school.conn.commit()
    return written


def format_tag_plan(plan: list[TagAction]) -> list[str]:
    """Lines for the dry-run listing.

    Args:
        plan: Actions from :func:`plan_participation_tag_backfill`.

    Returns:
        A heading plus one line per untagged session.
    """
    lines = [f"untagged: {len(plan)}"]
    for step in plan:
        when = step.session_date.isoformat() if step.session_date else "—"
        tag = "UNMAPPED" if step.unmapped else f"{step.module} {step.slot}"
        lines.append(
            f"  class {step.class_id} {step.course} s{step.section} "
            f"session {step.session_id} {when} {tag} rows={step.item_rows}"
        )
    return lines


def _live_classes(school: Any) -> list[dict[str, Any]]:
    """Current-semester classes that already have counted participation."""
    from participation_module_backfill import list_current_live_classes

    return list_current_live_classes(school)


def _plan_class(school: Any, course: dict[str, Any]) -> list[TagAction]:
    """Untagged sessions for one class, zipped onto that class's calendar."""
    class_id = int(course["id"])
    live_days = _live_days(school, class_id)
    weekdays = live_weekdays(live_days)
    office = _OFFICE_WEEKDAY in weekdays
    date_slots = (
        participation_date_slots(
            live_days,
            module_slots=course_module_slots(str(course.get("course") or "")),
            include_office=office,
        )
        if weekdays
        else {}
    )
    sessions = _untagged_sessions(school, class_id)
    by_day: dict[date | None, list[dict[str, Any]]] = {}
    for row in sessions:
        by_day.setdefault(row["day"], []).append(row)
    actions: list[TagAction] = []
    for day, rows in by_day.items():
        rows.sort(key=lambda item: (str(item["starts_at"]), int(item["session_id"])))
        slots = list(date_slots.get(day) or []) if day is not None else []
        for index, row in enumerate(rows):
            module, slot = ("", "")
            if index < len(slots):
                module, slot = slots[index]
            actions.append(
                TagAction(
                    class_id=class_id,
                    course=str(course.get("course") or f"class {class_id}"),
                    section=int(course.get("section") or 1),
                    session_id=int(row["session_id"]),
                    run_key=str(row["run_key"]),
                    starts_at=str(row["starts_at"] or ""),
                    session_date=day,
                    module=module,
                    slot=slot,
                    item_rows=int(row["item_rows"]),
                )
            )
    return actions


def _live_days(school: Any, class_id: int) -> str:
    """Offering live days, otherwise the class day label."""
    row = school.conn.execute(
        """
        SELECT COALESCE(NULLIF(TRIM(o.live_days), ''), NULLIF(TRIM(cl.days), ''), '') AS live_days
          FROM classes cl
          LEFT JOIN course_offerings o ON o.id = cl.offering_id
         WHERE cl.id = ?
        """,
        (int(class_id),),
    ).fetchone()
    if row is None:
        return ""
    return str(row["live_days"] or "").strip()


def _untagged_sessions(school: Any, class_id: int) -> list[dict[str, Any]]:
    """Counted sessions whose run has no stored module."""
    rows = school.conn.execute(
        """
        SELECT s.id AS session_id,
               s.starts_at AS starts_at,
               MIN(lpc.run_key) AS run_key
          FROM sessions s
          JOIN live_participation_credits lpc ON lpc.session_id = s.id
         WHERE s.class_id = ?
           AND EXISTS (
                SELECT 1 FROM participation_runs pr
                 WHERE pr.class_id = s.class_id
                   AND pr.run_key = lpc.run_key
                   AND pr.discarded = 0
                   AND pr.is_real = 1
           )
         GROUP BY s.id
        """,
        (int(class_id),),
    ).fetchall()
    untagged: list[dict[str, Any]] = []
    for row in rows:
        run_key = str(row["run_key"] or "")
        if not run_key or _run_has_module(school, class_id, run_key):
            continue
        untagged.append(
            {
                "session_id": int(row["session_id"]),
                "starts_at": str(row["starts_at"] or ""),
                "run_key": run_key,
                "day": calendar_date_from_stored(str(row["starts_at"] or "")),
                "item_rows": _blank_item_rows(school, class_id, run_key),
            }
        )
    return untagged


def _run_has_module(school: Any, class_id: int, run_key: str) -> bool:
    """True when any item on the run already stores a module."""
    row = school.conn.execute(
        """
        SELECT 1
          FROM participation_items
         WHERE class_id = ?
           AND run_key = ?
           AND TRIM(COALESCE(live_module, '')) != ''
         LIMIT 1
        """,
        (int(class_id), run_key),
    ).fetchone()
    return row is not None


def _blank_item_rows(school: Any, class_id: int, run_key: str) -> int:
    """How many item rows on the run still have a blank module."""
    row = school.conn.execute(
        """
        SELECT COUNT(*) AS n
          FROM participation_items
         WHERE class_id = ?
           AND run_key = ?
           AND TRIM(COALESCE(live_module, '')) = ''
        """,
        (int(class_id), run_key),
    ).fetchone()
    return int(row["n"] if row is not None else 0)

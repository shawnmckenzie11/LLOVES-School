"""Begin a section's next module from Attendance & Participation (MCK-339).

The action marks the open module complete and stores that section's next
module. Wrap-up is final. It does not delete or re-key answers, items,
runs, or result snapshots. Late catch-up stays on the run's own module.

Attendance has no totals table. A class counts as held when a real,
non-discarded run saved that module and class slot. A student counts as
attended when ``session_scores.present`` is 1 on the tracker session
linked to that run (``live_class_sessions.mgs_session_id`` or
``live_participation_credits.session_id``).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

try:
    from participation_weight import module_number
except ImportError:
    from lms.participation_weight import module_number

_MONTH = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

REASON_LIVE = "End the live class first."
REASON_EMPTY = "No classes held in this module yet."
REASON_LAST = "This is the last module."
BUTTON_LABEL = "Begin new module"


class BeginModuleRejected(ValueError):
    """The begin or un-mark request does not apply to this section."""

    def __init__(self, message: str, status: int = 409) -> None:
        """Store the teacher-facing reason and HTTP status.

        Args:
            message: Short reason. No roster data.
            status: HTTP status for the API response.
        """
        super().__init__(message)
        self.status = int(status)


def normalize_module(value: Any) -> str:
    """Return ``M3`` for ``m3``, ``M3``, or ``3``.

    Args:
        value: Module token from the button or a mark row.

    Returns:
        Canonical token, or ``""`` when it is not a numbered module.
    """
    text = str(value or "").strip().upper()
    if text.startswith("MODULE"):
        text = text.replace("MODULE", "", 1).strip()
    if text.isdigit():
        text = f"M{text}"
    number = module_number(text)
    if number <= 0:
        return ""
    return f"M{number}"


def is_class_slot(live_slot: Any) -> bool:
    """True for class slots such as ``C1``. Office slots are not classes.

    Args:
        live_slot: Slot token stored on a run.
    """
    return re.fullmatch(r"C\d+", str(live_slot or "").strip().upper()) is not None


def plan_from_slot_rows(rows: list[tuple[str, str]]) -> list[tuple[str, list[str]]]:
    """Group ``(module, slot)`` rows into teaching order.

    Args:
        rows: Module and slot tokens, already in module then slot order.

    Returns:
        ``(module, [slots])`` pairs. Blank modules are dropped.
    """
    order: list[str] = []
    slots: dict[str, list[str]] = {}
    for raw_module, raw_slot in rows:
        module = normalize_module(raw_module)
        slot = str(raw_slot or "").strip().upper()
        if not module or not is_class_slot(slot):
            continue
        if module not in slots:
            order.append(module)
            slots[module] = []
        if slot not in slots[module]:
            slots[module].append(slot)
    return [(module, slots[module]) for module in order]


def next_in_plan(modules: list[str], current: str) -> str | None:
    """The module after ``current``, or None on the last module.

    Args:
        modules: Plan tokens in order, such as ``M1`` … ``M20``.
        current: Open module token.
    """
    token = normalize_module(current)
    ordered = [normalize_module(module) for module in modules if normalize_module(module)]
    if not token or not ordered:
        return None
    if token in ordered:
        index = ordered.index(token)
        if index + 1 >= len(ordered):
            return None
        return ordered[index + 1]
    number = module_number(token)
    for module in ordered:
        if module_number(module) > number:
            return module
    return None


def derive_current_module(
    modules: list[str],
    held_counts: dict[str, int],
    complete: dict[str, bool],
) -> str:
    """Open module before the teacher has stored one.

    The highest numbered module that still has classes and is not
    complete stays open. When every module with classes is complete,
    that highest module stays on screen until Begin new module runs.
    A section with no classes yet starts at the first planned module.

    Args:
        modules: Plan tokens in order.
        held_counts: Module token → classes held.
        complete: Module token → whether a teacher mark says complete.

    Returns:
        Module token. ``M1`` when the plan is empty.
    """
    ordered = [normalize_module(module) for module in modules if normalize_module(module)]
    if not ordered:
        return "M1"
    with_classes = [module for module in ordered if int(held_counts.get(module) or 0) > 0]
    incomplete = [module for module in with_classes if not complete.get(module)]
    if incomplete:
        return incomplete[-1]
    if with_classes:
        return with_classes[-1]
    return ordered[0]


def disabled_reason(*, live: bool, held: int, is_last: bool) -> str:
    """Why Begin new module cannot run, or ``""`` when it can.

    A last module stays disabled even during a live class: there is no
    next module to start. A live class is next, then a module with no
    saved classes.

    Args:
        live: This section has a live class with status active.
        held: Saved class slots in the open module.
        is_last: The open module is the last module in the section plan.

    Returns:
        The disabled sentence, or ``""``.
    """
    if is_last:
        return REASON_LAST
    if live:
        return REASON_LIVE
    if int(held) <= 0:
        return REASON_EMPTY
    return ""


def dialog_copy(current_number: int, next_number: int, held: int, planned: int) -> dict[str, Any]:
    """Confirm-dialog sentences for wrapping one section's module.

    Args:
        current_number: Open module number.
        next_number: The module that would start.
        held: Class slots already saved in the open module.
        planned: Class slots the section plan gives that module.

    Returns:
        Title, body lines, button labels, and the success toast.
    """
    current = int(current_number)
    nxt = int(next_number)
    return {
        "title": f"Start Module {nxt}?",
        "lines": [
            (
                f"This wraps up Module {current} for this section "
                f"({int(held)} of {int(planned)} classes held)."
            ),
            (
                f"Your Module {current} totals move to Past modules, "
                "and nothing is deleted."
            ),
            f"Module {nxt} totals start at zero.",
            f"Catch-up answers that come in late still count toward Module {current}.",
            "This can't be undone.",
        ],
        "cancel": "Cancel",
        "confirm": f"Start Module {nxt}",
        "toast": f"Module {current} wrapped up. Module {nxt} has started.",
        "button": BUTTON_LABEL,
    }


def wrapped_up_label(updated_at: str | None) -> str:
    """Past-module note ``Wrapped up Oct 14``.

    The calendar day is the date stored on the complete mark. A time
    suffix does not move that day.

    Args:
        updated_at: ``participation_module_marks.updated_at``.

    Returns:
        The note, or ``""`` when the stamp has no date.
    """
    text = str(updated_at or "").strip()
    if len(text) < 10:
        return ""
    try:
        day = date.fromisoformat(text[:10])
    except ValueError:
        return ""
    return f"Wrapped up {_MONTH[day.month - 1]} {day.day}"


def toast_for_reload(current_number: int, wrapped: Any, started: Any) -> str:
    """Toast after a reload, only when the query matches the open module.

    Args:
        current_number: Module now open for this section.
        wrapped: Query value for the module that was wrapped.
        started: Query value for the module that started.

    Returns:
        The toast sentence, or ``""``.
    """
    try:
        wrapped_n = int(wrapped)
        started_n = int(started)
    except (TypeError, ValueError):
        return ""
    if started_n == int(current_number) and wrapped_n == started_n - 1 and wrapped_n > 0:
        return f"Module {wrapped_n} wrapped up. Module {started_n} has started."
    return ""


def tally_attendance(
    meetings: list[dict[str, Any]],
    present: dict[int, set[int]],
    student_ids: list[int],
) -> tuple[int, dict[int, int]]:
    """Count held class slots and how many each student attended.

    One slot counts once even when the section ran it twice. A student
    attended that slot when any linked tracker session has them present.

    Args:
        meetings: ``slot`` plus ``session_ids`` for one module.
        present: Tracker session id → student ids with present = 1.
        student_ids: Roster ids to include.

    Returns:
        ``(held slots, student id → attended slots)``.
    """
    by_slot: dict[str, set[int]] = {}
    for meeting in meetings:
        slot = str(meeting.get("slot") or "").strip().upper()
        if not is_class_slot(slot):
            continue
        bucket = by_slot.setdefault(slot, set())
        for raw in meeting.get("session_ids") or []:
            try:
                bucket.add(int(raw))
            except (TypeError, ValueError):
                continue
    attended = {int(student_id): 0 for student_id in student_ids}
    for session_ids in by_slot.values():
        for student_id in attended:
            if any(student_id in present.get(session_id, set()) for session_id in session_ids):
                attended[student_id] += 1
    return len(by_slot), attended


def live_place(teacher_state_json: Any, item_place: tuple[str, str] | None) -> tuple[str, str]:
    """Module and slot for an ended live class.

    The teacher state wins. A saved participation item fills a blank
    module or a non-class slot.

    Args:
        teacher_state_json: ``live_class_sessions.teacher_state_json``.
        item_place: ``(module, slot)`` from a participation item, if any.

    Returns:
        ``(module, slot)``. Either side may be blank.
    """
    import json

    state: dict[str, Any] = {}
    try:
        parsed = json.loads(teacher_state_json or "{}")
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        state = parsed
    module = normalize_module(state.get("live_module"))
    slot = str(state.get("live_slot") or "").strip().upper()
    if item_place:
        if not module:
            module = normalize_module(item_place[0])
        if not is_class_slot(slot):
            slot = str(item_place[1] or "").strip().upper()
    return module, slot


def percent_text(percent: Any) -> str:
    """Cell text for a participation percent. Zero offered is an em dash.

    Args:
        percent: Rounded percent, or None when nothing was offered.
    """
    if percent is None:
        return "—"
    return f"{int(percent)}%"

"""Participation table bands and the Run Live Class finish prompt (MCK-251).

Finished modules collapse to one summary column. The latest unfinished
module stays expanded as the current module. Starting a later module
while that current module is open asks the teacher to finish it first.

Column labels use the stored module token. A blank module is not shown
as ``M1``, and the number is not shifted down by one.
"""

from __future__ import annotations

import re
from typing import Any

from participation_exclusion import toronto_datetime_from_ended_at
from participation_weight import module_number

_WEEKDAY = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
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
_OFFICE_SLOT = re.compile(r"^O\d+$")


def is_office_slot(live_slot: str | None) -> bool:
    """True for open-office slots such as ``O1``.

    Args:
        live_slot: Lesson slot token from the live class.

    Returns:
        Whether the column should render muted as open office.
    """
    return _OFFICE_SLOT.fullmatch(str(live_slot or "").strip().upper()) is not None


def format_participation_grouped_header(
    live_module: str | None,
    live_slot: str | None,
    ended_at: str | None,
) -> str:
    """Column title used inside a module group.

    Class columns read ``M3 · C1 · Mon Oct 12``. Office columns omit the
    date (``M3 · O1``); the table adds an Open office pill beside that.
    The stored module is used as-is: a blank module is not labeled ``M1``,
    a blank slot is not labeled ``C1``, and ``M2`` is not shown as ``M1``.

    Args:
        live_module: Module token such as ``M3``.
        live_slot: Slot token such as ``C1`` or ``O1``.
        ended_at: Stored session end (naive UTC).

    Returns:
        Header text for one class column, or ``""`` when the module is blank.
    """
    mod = str(live_module or "").strip().upper()
    slot = str(live_slot or "").strip().upper()
    if module_number(mod) <= 0:
        return ""
    if is_office_slot(slot):
        return f"{mod} · {slot}"
    when = toronto_datetime_from_ended_at(ended_at)
    parts = [mod]
    if slot:
        parts.append(slot)
    if when is not None:
        weekday = _WEEKDAY[when.weekday()]
        month = _MONTH[when.month - 1]
        parts.append(f"{weekday} {month} {when.day}")
    return " · ".join(parts)


def module_classes_are_done(columns: list[dict[str, Any]]) -> bool:
    """True when every class column in the module has ended.

    ``class_ended`` is the live-class end flag. When a caller only stored
    ``ended_at`` (tests), a non-empty end time counts as done. This reads
    the columns already on the table and does not write a module mark.

    Args:
        columns: Band columns for one module.

    Returns:
        Whether the view should treat those classes as finished.
    """
    if not columns:
        return False
    for column in columns:
        if "class_ended" in column:
            if not column["class_ended"]:
                return False
            continue
        if not str(column.get("ended_at") or "").strip():
            return False
    return True


def _module_sort_key(module: str) -> tuple[int, int, str]:
    """Numbered modules first, in order; blank modules last."""
    number = module_number(module)
    if not module or number <= 0:
        return (1, 9999, module)
    return (0, number, module)


def build_participation_bands(
    columns: list[dict[str, Any]],
    session_meta: dict[Any, dict[str, Any]],
    complete_by_module: dict[str, bool],
) -> dict[str, Any]:
    """Group live-class columns into finished, open, and current bands.

    Subtotal snapshots are omitted. A column with no stored module is
    omitted, so it cannot appear as an ``M1 C1`` placeholder after the
    current module. The current module is the highest numbered module
    that still has classes and is not complete. A module is ``finished``
    when it is marked complete, or when every class in it has ended and
    a later module is the current one. That second case is view-only:
    it does not write the module mark. An earlier module that still has
    a class in progress stays ``open``.

    Args:
        columns: Dashboard columns (``kind`` ``session`` or ``subtotal``).
        session_meta: Session id → deck metadata from the participation grid.
        complete_by_module: Module token → ``module_complete``.

    Returns:
        ``{"current_module": str | None, "bands": [...]}``. Each band has
        ``module``, ``number``, ``role``, and ``columns``.
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    for column in columns or []:
        if str(column.get("kind") or "") == "subtotal":
            continue
        if str(column.get("kind") or "session") not in {"session", ""}:
            continue
        try:
            session_id = int(column["id"])
        except (KeyError, TypeError, ValueError):
            continue
        meta = session_meta.get(str(session_id)) or session_meta.get(session_id) or {}
        module = str(meta.get("live_module") or "").strip().upper()
        if module_number(module) <= 0:
            continue
        slot = str(meta.get("live_slot") or "").strip().upper()
        ended = str(meta.get("ended_at") or "")
        if "class_ended" in meta:
            class_ended = bool(meta.get("class_ended"))
        else:
            class_ended = bool(ended)
        buckets.setdefault(module, []).append(
            {
                "id": session_id,
                "module": module,
                "slot": slot,
                "office": is_office_slot(slot),
                "label": format_participation_grouped_header(module, slot, ended),
                "ended_at": ended,
                "class_ended": class_ended,
            }
        )
    for rows in buckets.values():
        rows.sort(key=lambda row: (row["ended_at"], row["id"]))
    order = sorted(buckets, key=_module_sort_key)
    numbered = [key for key in order if module_number(key) > 0]
    incomplete = [
        key for key in numbered if not bool(complete_by_module.get(key))
    ]
    current = incomplete[-1] if incomplete else None
    bands: list[dict[str, Any]] = []
    for key in order:
        number = module_number(key)
        if number <= 0:
            continue
        if bool(complete_by_module.get(key)):
            role = "finished"
        elif key == current:
            role = "current"
        elif module_classes_are_done(buckets[key]):
            role = "finished"
        else:
            role = "open"
        bands.append(
            {
                "module": key,
                "number": number,
                "role": role,
                "columns": buckets[key],
            }
        )
    return {"current_module": current, "bands": bands}


def start_prompt_for(
    bands: dict[str, Any],
    selected_module: str | None,
) -> dict[str, str] | None:
    """Dialog copy when Set Class starts a module after the open one.

    Args:
        bands: Payload from :func:`build_participation_bands`.
        selected_module: Module the teacher is about to start (``M4``).

    Returns:
        Title, body, and button labels, or ``None`` when no prompt is due.
    """
    current = str(bands.get("current_module") or "").strip().upper()
    current_n = module_number(current)
    selected_n = module_number(str(selected_module or ""))
    if current_n <= 0 or selected_n <= current_n:
        return None
    return {
        "module": current,
        "number": str(current_n),
        "start_module": f"M{selected_n}",
        "start_number": str(selected_n),
        "title": f"Module {current_n} isn't finished yet",
        "body": (
            f"Finish it in the gradebook first? This locks the Module "
            f"{current_n} total and collapses the module."
        ),
        "finish_label": f"Finish M{current_n} and start M{selected_n}",
        "cancel_label": "Cancel",
        "skip_label": "Start without finishing",
    }

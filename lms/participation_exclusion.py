"""MCK-230: shared Participation tab exclusions (test runs, Test Student).

Test runs (`participation_runs.is_real = 0` or `discarded = 1`) are omitted from
the Participation tab grid, SUBTOTAL, and TOTAL. Test Student roster rows are
excluded when deciding which live answers count toward denominators.
"""

from __future__ import annotations

from typing import Any

from datetime import datetime
from zoneinfo import ZoneInfo

_WEEKDAY_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_UTC = ZoneInfo("UTC")
_TORONTO = ZoneInfo("America/Toronto")


def toronto_datetime_from_ended_at(value: str | None) -> datetime | None:
    """Parse stored ``ended_at`` as UTC-naive and convert to Toronto, or None."""
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_UTC).astimezone(_TORONTO)
    return dt.astimezone(_TORONTO)


def sql_exclude_non_counted_participation_run(
    class_id_expr: str,
    run_key_expr: str,
    *,
    alias: str = "dr",
) -> str:
    """SQL fragment: skip test/discarded ``participation_runs`` rows.

    Args:
        class_id_expr: SQL expression for ``participation_runs.class_id``.
        run_key_expr: SQL expression for ``participation_runs.run_key``.
        alias: Subquery table alias.

    Returns:
        ``AND NOT EXISTS (...)`` suitable to append to a ``WHERE`` clause.
    """
    return (
        f" AND EXISTS (SELECT 1 FROM participation_runs {alias} "
        f"WHERE {alias}.class_id = {class_id_expr} AND {alias}.run_key = {run_key_expr} "
        f"AND {alias}.discarded = 0 AND {alias}.is_real = 1)"
    )


def participation_run_counts_in_tab(kind: dict[str, Any] | None) -> bool:
    """True when a run should appear in Participation tab columns and rollups."""
    if kind is None:
        return False
    return bool(kind.get("is_real")) and not bool(kind.get("discarded"))


def _stored_module_token(raw: str | None) -> str:
    """Return ``M2``-style module text, or blank when none is stored.

    Does not substitute ``M1`` and does not change the module number.

    Args:
        raw: Module token from a participation item or session.

    Returns:
        Uppercase module token, or ``""``.
    """
    text = str(raw or "").strip().upper()
    digits = text[1:] if text.startswith("M") else ""
    if digits.isdigit() and int(digits) > 0:
        return text
    return ""


def format_participation_session_header(
    live_module: str,
    live_slot: str,
    ended_at: str | None,
) -> str:
    """Column label: stored lesson slot plus Toronto calendar date.

    Open office keeps the stored label (``M3 O1``). A live class stays
    compact (``M2C1``). A blank module is left blank instead of ``M1``,
    and a blank slot is not filled in as ``C1``.

    Args:
        live_module: Stored module token such as ``M2``.
        live_slot: Stored slot token such as ``C1`` or ``O1``.
        ended_at: Session end, naive UTC.

    Returns:
        Header text, or ``""`` when the module was not stored.
    """
    mod = _stored_module_token(live_module)
    if not mod:
        return ""
    slot = str(live_slot or "").strip().upper()
    try:
        from set_class import is_open_office_slot, open_office_label
    except ImportError:
        from lms.set_class import is_open_office_slot, open_office_label
    if is_open_office_slot(slot):
        slot_label = open_office_label(mod, slot)
    elif slot:
        slot_label = f"{mod}{slot}"
    else:
        slot_label = mod
    when = toronto_datetime_from_ended_at(ended_at)
    if when is None:
        return f"{slot_label} · —"
    abbr = _WEEKDAY_ABBR[when.weekday()]
    return f"{slot_label} · {abbr} {when.month}/{when.day}/{when.year % 100:02d}"


def exclude_participation_answer_student_ids(class_id: int, school: Any) -> set[int]:
    """Test Student ids excluded from participation answer denominators (MCK-242)."""
    from participation_real_students import exclude_test_student_ids

    return exclude_test_student_ids(int(class_id), school)


def toronto_date_label(ended_at: str | None) -> str:
    """Short Toronto date for tests and display helpers."""
    when = toronto_datetime_from_ended_at(ended_at)
    if when is None:
        return ""
    return f"{when.month}/{when.day}/{when.year % 100:02d}"

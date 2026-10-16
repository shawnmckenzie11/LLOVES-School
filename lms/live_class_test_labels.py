"""Display labels for test live classes (MCK-269 Mobbin UI)."""

from __future__ import annotations

from datetime import date
from typing import Any

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


def _calendar_day(ended_at: str | None) -> date | None:
    """School-calendar day for one stored timestamp."""
    try:
        from participation_schedule import calendar_date_from_stored
    except ImportError:
        from lms.participation_schedule import calendar_date_from_stored

    return calendar_date_from_stored(str(ended_at or ""))


def test_same_day_indices(rows: list[dict[str, Any]]) -> dict[int, int]:
    """Map each test session id to its 1-based index on that school day.

    Args:
        rows: Dicts with ``live_session_id`` and ``ended_at``, oldest first.

    Returns:
        ``live_class_sessions.id`` → same-day index (1, 2, …).
    """
    ordered = sorted(
        rows,
        key=lambda row: (
            str(row.get("ended_at") or ""),
            int(row.get("live_session_id") or 0),
        ),
    )
    by_day: dict[date, list[int]] = {}
    for row in ordered:
        sid = int(row.get("live_session_id") or 0)
        if sid <= 0:
            continue
        day = _calendar_day(str(row.get("ended_at") or ""))
        if day is None:
            by_day.setdefault(date.min, []).append(sid)
        else:
            by_day.setdefault(day, []).append(sid)
    out: dict[int, int] = {}
    for ids in by_day.values():
        for index, sid in enumerate(ids, start=1):
            out[sid] = index
    return out


def format_test_participation_label(ended_at: str | None, *, same_day_index: int = 1) -> str:
    """Column title for a test class on the Participation tab.

    Args:
        ended_at: Stored end or start time.
        same_day_index: Second test on the same day becomes ``(2)``.

    Returns:
        Text such as ``TEST · Oct 16`` or ``TEST · Oct 16 (2)``.
    """
    day = _calendar_day(ended_at)
    if day is None:
        return "TEST"
    month = _MONTH[day.month - 1]
    base = f"TEST · {month} {day.day}"
    if int(same_day_index or 1) > 1:
        return f"{base} ({int(same_day_index)})"
    return base


def format_test_lesson_slides_label(ended_at: str | None, *, same_day_index: int = 1) -> str:
    """Row title for a test class on the Lesson Slides tab.

    Args:
        ended_at: Stored end time for the run.
        same_day_index: Second test on the same day becomes ``(2)``.

    Returns:
        Text such as ``TEST Fri Oct 16`` or ``TEST Fri Oct 16 (2)``.
    """
    day = _calendar_day(ended_at)
    if day is None:
        return "TEST"
    weekday = _WEEKDAY[day.weekday()]
    month = _MONTH[day.month - 1]
    base = f"TEST {weekday} {month} {day.day}"
    if int(same_day_index or 1) > 1:
        return f"{base} ({int(same_day_index)})"
    return base

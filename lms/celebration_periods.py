"""MCK-133: Celebrations award periods ("Start fresh") and timeframe labels.

The award tally behind Celebrations (Most Engaged) is counted per class.
A class's *award period* starts at its latest Start fresh, or at semester
day 1 when it never had one. Only game sessions scheduled at or after the
period start (``sessions.starts_at``, school wall time, the same clock the
class schedule uses) count toward the board. Nothing is deleted: saved
attendance and points stay in the course database and its views.

Storage is append-only. ``celebration_award_periods`` gets one row per
class per Start fresh; the newest row for a class in the active semester is
its current period. A class with no row is in its first period. Row ids are
``AUTOINCREMENT`` so a period id is never reused (MCK-116 reward grants can
key on it).

This module has no Flask or ``celebration`` import, so both can use it.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

SCHOOL_TZ = ZoneInfo("America/Toronto")

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def school_now() -> datetime:
    """Now as naive school wall time (America/Toronto), whole seconds.

    Same form as ``sessions.starts_at`` (``2026-10-02T14:00:00``), so the
    two compare directly whatever the server's own time zone is.
    """
    return datetime.now(SCHOOL_TZ).replace(microsecond=0, tzinfo=None)


def school_date(text: Any) -> date | None:
    """Calendar date in school time for an ISO date or timestamp.

    An aware timestamp (``…+00:00``) is converted to America/Toronto; a
    naive one is already school wall time.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        if "T" not in raw and len(raw) <= 10:
            return date.fromisoformat(raw)
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(SCHOOL_TZ)
    return stamp.date()


def _wall(text: Any) -> datetime | None:
    """Naive wall-clock datetime for a session or period start, or None."""
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        if "T" not in raw and len(raw) <= 10:
            return datetime.combine(date.fromisoformat(raw), datetime.min.time())
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(SCHOOL_TZ).replace(tzinfo=None)
    return stamp


def session_in_period(session_starts_at: Any, period_starts_at: Any) -> bool:
    """True when a session counts toward the award period.

    No period start (first period) counts everything. A session whose start
    cannot be read is left out once a period has started.
    """
    start = _wall(period_starts_at)
    if start is None:
        return True
    when = _wall(session_starts_at)
    return when is not None and when >= start


def current_periods(school: Any, semester_id: int | None) -> dict[int, dict[str, Any]]:
    """Current period per class in one semester: ``{class_id: row}``.

    Classes with no row are in their first period and are left out.
    A missing table (an old file before boot ran SCHEMA) reads as none.
    """
    if semester_id is None:
        return {}
    try:
        with school._lock:
            rows = school.conn.execute(
                """
                SELECT p.id, p.class_id, p.starts_at, p.created_at, p.created_by_user_id
                FROM celebration_award_periods p
                JOIN (
                    SELECT class_id, MAX(id) AS id
                    FROM celebration_award_periods
                    WHERE semester_id = ?
                    GROUP BY class_id
                ) cur ON cur.id = p.id
                """,
                (int(semester_id),),
            ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {int(row["class_id"]): dict(row) for row in rows}


def open_periods(
    school: Any,
    *,
    semester_id: int,
    class_ids: list[int],
    user_id: int | None,
    starts_at: datetime,
) -> list[dict[str, Any]]:
    """Start a new award period for each class (one transaction).

    Returns:
        The inserted rows.
    """
    stamp = starts_at.replace(microsecond=0).isoformat()
    created = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    out: list[dict[str, Any]] = []
    with school._lock:
        for class_id in sorted({int(c) for c in class_ids}):
            cur = school.conn.execute(
                """
                INSERT INTO celebration_award_periods
                    (semester_id, class_id, starts_at, created_by_user_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (int(semester_id), class_id, stamp, user_id, created),
            )
            out.append(
                {
                    "id": int(cur.lastrowid),
                    "class_id": class_id,
                    "starts_at": stamp,
                    "created_at": created,
                    "created_by_user_id": user_id,
                }
            )
        school.conn.commit()
    return out


def semester_first_day(semester: dict[str, Any] | None) -> date | None:
    """Semester day 1 (``first_day_of_school``), the first period's start."""
    if not semester:
        return None
    return school_date(semester.get("instructional_first"))


def _day(value: date) -> str:
    return f"{_MONTHS[value.month - 1]} {value.day}"


def period_label(start: date | None, end: date | None, copy: dict[str, str]) -> str:
    """Placeholder timeframe text for one award card (Wonder replaces copy).

    ``Sep 8 – Oct 1, 2026``, ``Dec 1, 2026 – Jan 15, 2027``, or, with no
    end (live board), ``Since Oct 2, 2026``. Empty when the start is unknown.
    """
    if start is None:
        return ""
    if end is None:
        return copy["period_since"].format(start=f"{_day(start)}, {start.year}")
    if end < start:
        end = start
    first = _day(start) if start.year == end.year else f"{_day(start)}, {start.year}"
    return copy["period_range"].format(start=first, end=f"{_day(end)}, {end.year}")

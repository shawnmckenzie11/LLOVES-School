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

# The first award period (no Start fresh yet) runs from semester day 1 to
# this school date, as Shawn defined it (MCK-133, confirmed Oct 2 15:07).
# The first-period ranking only counts saved classes that met on or before
# it; classes after it count toward no award until a Start fresh opens a
# new period. It is also the label's end whenever no stored snapshot gives
# an earlier one. School setting
# ``SETTING_FIRST_AWARD_PERIOD_END`` (ISO date) overrides it; a blank or
# unreadable setting falls back to this constant. A date before the active
# semester's day 1 is ignored (a later semester's first period is open).
FIRST_AWARD_PERIOD_END = date(2026, 10, 1)
SETTING_FIRST_AWARD_PERIOD_END = "celebration_first_award_period_end"

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


def server_local_to_school(text: Any) -> datetime | None:
    """Naive server-local timestamp (game ``games.created_at``) as school wall time.

    The game DB stamps ``datetime.now()`` in the server's own zone (UTC on
    Fly, Toronto on a dev box); this converts it to naive America/Toronto
    wall time so it compares with ``starts_at`` and period starts. Aware
    values are converted directly. Unreadable values give None.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return stamp.astimezone(SCHOOL_TZ).replace(tzinfo=None, microsecond=0)
    except (ValueError, OverflowError, OSError):
        return None


def session_in_period(
    session_starts_at: Any, period_starts_at: Any, begun_at: datetime | None = None
) -> bool:
    """True when a session counts toward the award period.

    No period start (first period) counts everything. Otherwise a session
    counts when its scheduled slot (``starts_at``) is at or after the
    period start, or when it was actually begun (``begun_at``, school wall
    time) at or after the period start on a meeting day no earlier than
    the period's first day (MED-2): a 2pm class begun at 14:12 after a
    14:10 Start fresh counts, but attendance back-filled after the click
    for a day before it does not. A session with no readable slot is left
    out.
    """
    start = _wall(period_starts_at)
    if start is None:
        return True
    when = _wall(session_starts_at)
    if when is None:
        return False
    if when >= start:
        return True
    return begun_at is not None and begun_at >= start and when.date() >= start.date()


def met_on_or_before(session_starts_at: Any, last_day: date) -> bool:
    """True when a session's meeting day (school date) is on or before ``last_day``.

    An unreadable start is left out.
    """
    when = _wall(session_starts_at)
    return when is not None and when.date() <= last_day


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


# LOW-5: a Start fresh within this many seconds of the class's current
# period reuses that period instead of opening another (double click,
# retries, concurrent workers).
REPEAT_WINDOW_SECONDS = 60


def _recent(row: dict[str, Any] | None, now: datetime) -> bool:
    """True when a period row was created within ``REPEAT_WINDOW_SECONDS``."""
    if not row:
        return False
    try:
        created = datetime.fromisoformat(str(row.get("created_at") or ""))
    except ValueError:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return abs((now - created).total_seconds()) < REPEAT_WINDOW_SECONDS


def recent_periods(school: Any, semester_id: int, class_ids: list[int]) -> set[int]:
    """Class ids whose current period was opened within the repeat window."""
    now = datetime.now(timezone.utc)
    current = current_periods(school, semester_id)
    return {int(c) for c in class_ids if _recent(current.get(int(c)), now)}


def open_periods(
    school: Any,
    *,
    semester_id: int,
    class_ids: list[int],
    user_id: int | None,
    starts_at: datetime,
) -> list[dict[str, Any]]:
    """Start a new award period for each class, idempotently (one transaction).

    A class whose current period was opened within ``REPEAT_WINDOW_SECONDS``
    keeps it (row returned with ``reused: True``). The check and the insert
    run under ``BEGIN IMMEDIATE``, so concurrent calls from several workers
    still leave a single new row per class.

    Returns:
        One row per class (``reused`` False for a newly opened period).
    """
    stamp = starts_at.replace(microsecond=0).isoformat()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    created = now.isoformat()
    out: list[dict[str, Any]] = []
    with school._lock:
        conn = school.conn
        began = not conn.in_transaction
        if began:
            conn.execute("BEGIN IMMEDIATE")
        try:
            for class_id in sorted({int(c) for c in class_ids}):
                row = conn.execute(
                    """
                    SELECT id, class_id, starts_at, created_at, created_by_user_id
                    FROM celebration_award_periods
                    WHERE semester_id = ? AND class_id = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (int(semester_id), class_id),
                ).fetchone()
                current = dict(row) if row else None
                if _recent(current, now):
                    out.append({**current, "reused": True})
                    continue
                cur = conn.execute(
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
                        "reused": False,
                    }
                )
            if began:
                conn.execute("COMMIT")
            else:
                conn.commit()
        except BaseException:
            if began and conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    return out


def semester_first_day(semester: dict[str, Any] | None) -> date | None:
    """Semester day 1 (``first_day_of_school``), the first period's start."""
    if not semester:
        return None
    return school_date(semester.get("instructional_first"))


def first_period_end(school: Any, start: date | None) -> date | None:
    """Defined end of the first award period, or None when it does not apply.

    The setting wins when it is a readable date on or after ``start``;
    otherwise the constant (LOW-6: a setting before day 1 falls back to
    Oct 1, 2026, not to an open period). None only when the constant is
    before ``start`` too (a later semester). Never raises.
    """
    try:
        raw = school.get_school_setting(SETTING_FIRST_AWARD_PERIOD_END, "")
    except Exception:  # noqa: BLE001 - the label must not fail
        raw = ""
    for end in (school_date(raw), FIRST_AWARD_PERIOD_END):
        if end is not None and (start is None or end >= start):
            return end
    return None


def _day(value: date) -> str:
    return f"{_MONTHS[value.month - 1]} {value.day}"


def period_label(start: date | None, end: date | None, copy: dict[str, str]) -> str:
    """Placeholder timeframe text for one award card (Wonder replaces copy).

    ``Sep 8 – Oct 1, 2026``, ``Dec 1, 2026 – Jan 15, 2027``, one day as
    ``Oct 2, 2026`` (LOW-6), or, with no end (an open period on the live
    board), ``Since Oct 2, 2026``. With no known start (no semester day 1):
    ``Through Oct 1, 2026``, else ``This semester``. Never empty.
    """
    if start is None:
        if end is not None:
            return copy["period_through"].format(end=f"{_day(end)}, {end.year}")
        return copy["period_unknown"]
    if end is None:
        return copy["period_since"].format(start=f"{_day(start)}, {start.year}")
    if end <= start:
        return copy["period_day"].format(day=f"{_day(start)}, {start.year}")
    first = _day(start) if start.year == end.year else f"{_day(start)}, {start.year}"
    return copy["period_range"].format(start=first, end=f"{_day(end)}, {end.year}")

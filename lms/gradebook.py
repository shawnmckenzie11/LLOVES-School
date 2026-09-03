"""Weighted gradebook scaffold and attendance week-grid helpers for staff APG."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from paths import SEMESTER_JSON
except ImportError:  # ``python3 lms/app.py`` package import
    from lms.paths import SEMESTER_JSON

# Default category weights (percent). Extension point: persist + later edit UI.
DEFAULT_GRADE_WEIGHTS: dict[str, float] = {
    "participation": 15.0,
    "term": 60.0,
    "exam": 25.0,
}

GRADE_CATEGORIES = ("participation", "term", "exam")

WEEKDAY_HEADERS = ("M", "T", "W", "T", "F")

# First letter of the English month, matching “S8” for September 8.
MONTH_LETTER = {
    1: "J",
    2: "F",
    3: "M",
    4: "A",
    5: "M",
    6: "J",
    7: "J",
    8: "A",
    9: "S",
    10: "O",
    11: "N",
    12: "D",
}

SETTING_ONLY_LIVE_CLASS_DAYS = "only_live_class_days"


def default_grade_weights() -> dict[str, float]:
    """Return a copy of the v1 category weight defaults."""
    return dict(DEFAULT_GRADE_WEIGHTS)


def normalize_grade_weights(raw: dict[str, Any] | None) -> dict[str, float]:
    """Coerce a weight map to the three categories; fill missing from defaults.

    Args:
        raw: Partial or full ``{category: percent}`` map.

    Returns:
        Normalized weights for participation / term / exam.
    """
    out = default_grade_weights()
    if not raw:
        return out
    for key in GRADE_CATEGORIES:
        if key not in raw:
            continue
        try:
            val = float(raw[key])
        except (TypeError, ValueError):
            continue
        if val < 0:
            continue
        out[key] = val
    return out


def short_day_label(day: date) -> str:
    """Compact month+day label, e.g. ``S8`` for September 8.

    Args:
        day: Calendar date.

    Returns:
        One-letter month plus day-of-month with no leading zero.
    """
    return f"{MONTH_LETTER[day.month]}{day.day}"


def load_semester_calendar(semester_json: Path | None = None) -> Any:
    """Load the board calendar helper from ``scripts/syllabus_calendar.py``.

    Args:
        semester_json: Optional override path.

    Returns:
        ``SemesterCalendar`` named tuple from the syllabus packer.
    """
    try:
        from paths import SCRIPTS_DIR
    except ImportError:
        from lms.paths import SCRIPTS_DIR

    path = Path(semester_json or SEMESTER_JSON)
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location(
        "syllabus_calendar", SCRIPTS_DIR / "syllabus_calendar.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError("scripts/syllabus_calendar.py is missing")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("syllabus_calendar", mod)
    spec.loader.exec_module(mod)
    return mod.load_semester_calendar(path)


def teacher_weekday_span(semester_json: Path | None = None) -> list[date]:
    """Mon–Fri from first instructional day through last day before exams.

    Includes holidays and PD so the grid can grey them out rather than omit them.

    Args:
        semester_json: Optional override path.

    Returns:
        Sorted weekdays in the teacher attendance window.
    """
    cal = load_semester_calendar(semester_json)
    days: list[date] = []
    cursor = cal.first_day
    while cursor <= cal.last_instructional:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def live_weekday_set(days_label: str | None) -> set[int]:
    """Map a class schedule string to Python weekday ints (Mon=0).

    Args:
        days_label: ``M/W/F``, ``T/Th/F``, or stored ``Mon/Wed/Fri`` form.

    Returns:
        Weekday ints for live class meetings.
    """
    try:
        from schedule import DAY_PRESETS, STORED_DAYS_TO_WEEKDAYS
    except ImportError:
        try:
            from paths import GAME_SHOW
        except ImportError:
            from lms.paths import GAME_SHOW
        if str(GAME_SHOW) not in sys.path:
            sys.path.insert(0, str(GAME_SHOW))
        from schedule import DAY_PRESETS, STORED_DAYS_TO_WEEKDAYS
    raw = (days_label or "").strip()
    stored = DAY_PRESETS.get(raw, raw)
    return set(STORED_DAYS_TO_WEEKDAYS.get(stored) or ())


def is_live_class_date(
    day: date,
    *,
    days_label: str | None,
    instructional: set[date],
) -> bool:
    """True when ``day`` is an instructional live-class weekday for the course.

    Args:
        day: Candidate meeting date.
        days_label: Course live-day preset.
        instructional: School days (not PD/holiday).

    Returns:
        Whether attendance/participation may log on this date under the live-day rule.
    """
    if day not in instructional:
        return False
    allowed = live_weekday_set(days_label)
    if not allowed:
        return False
    return day.weekday() in allowed


def load_instructional_weekdays(semester_json: Path | None = None) -> list[date]:
    """Mon–Fri instructional days from ``frameworks/semester.json``.

    Args:
        semester_json: Optional override path.

    Returns:
        Sorted school days (weekdays minus holidays/PD from the calendar).
    """
    cal = load_semester_calendar(semester_json)
    return list(cal.instructional_days)


def group_weekdays_into_weeks(days: list[date]) -> list[list[date | None]]:
    """Bucket school days into Mon–Fri week slots (missing days as None).

    Args:
        days: Instructional weekdays sorted ascending.

    Returns:
        Each inner list has length 5: Mon…Fri (None when not a school day).
    """
    if not days:
        return []
    weeks: list[list[date | None]] = []
    by_iso: dict[tuple[int, int], list[date | None]] = {}
    order: list[tuple[int, int]] = []
    for d in days:
        key = d.isocalendar()[:2]  # year, week
        if key not in by_iso:
            by_iso[key] = [None, None, None, None, None]
            order.append(key)
        wd = d.weekday()  # Mon=0 … Fri=4
        if 0 <= wd <= 4:
            by_iso[key][wd] = d
    for key in order:
        weeks.append(by_iso[key])
    return weeks


def session_meeting_date(starts_at: str | None) -> date | None:
    """Parse a session ``starts_at`` ISO string into a calendar date.

    Args:
        starts_at: Session start timestamp.

    Returns:
        Meeting date, or None when unparseable.
    """
    if not starts_at:
        return None
    text = str(starts_at).strip()
    try:
        if "T" in text:
            return datetime.fromisoformat(text).date()
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def build_attendance_week_grid(
    *,
    students: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    score_rows: list[dict[str, Any]],
    instructional_days: list[date] | None = None,
    span_days: list[date] | None = None,
    closed: dict[date, str] | None = None,
) -> dict[str, Any]:
    """Build the slim M–T–W–T–F attendance grid payload.

    Present mark for a day is true when any non-template session on that date
    has ``present=1`` for the student. Total = count of present school days.
    The grid spans semester start through last instructional day and greys
    holidays / PD.

    Args:
        students: Roster rows with ``id`` and display fields.
        sessions: Class sessions (``id``, ``starts_at``, ``status``).
        score_rows: ``session_scores`` dicts with session_id, student_id, present.
        instructional_days: Optional school-day list; loads semester.json if omitted.
        span_days: Optional Mon–Fri span including closed days.
        closed: Map of non-school dates to a reason label.

    Returns:
        Grid payload including ``date_labels`` and ``day_meta``.
    """
    school_days = (
        instructional_days
        if instructional_days is not None
        else load_instructional_weekdays()
    )
    school_set = set(school_days)
    closed_map = dict(closed or {})
    if closed is None:
        cal = load_semester_calendar()
        closed_map = dict(cal.closed)
    days = span_days if span_days is not None else teacher_weekday_span()
    weeks = group_weekdays_into_weeks(days)
    session_dates: dict[int, date] = {}
    for sess in sessions:
        if str(sess.get("status") or "") == "template":
            continue
        meeting = session_meeting_date(sess.get("starts_at"))
        if meeting is None:
            continue
        session_dates[int(sess["id"])] = meeting

    present_by_student_day: dict[tuple[int, str], bool] = {}
    for row in score_rows:
        sid = int(row["student_id"])
        sess_id = int(row["session_id"])
        meeting = session_dates.get(sess_id)
        if meeting is None:
            continue
        key = (sid, meeting.isoformat())
        if int(row.get("present") or 0) == 1:
            present_by_student_day[key] = True
        else:
            present_by_student_day.setdefault(key, False)

    cells: dict[str, bool | None] = {}
    totals: dict[str, int] = {}
    day_meta: list[list[dict[str, Any] | None]] = []
    for week in weeks:
        meta_week: list[dict[str, Any] | None] = []
        for day in week:
            if day is None:
                meta_week.append(None)
                continue
            school = day in school_set
            reason = "" if school else str(closed_map.get(day) or "No school")
            meta_week.append(
                {
                    "iso": day.isoformat(),
                    "label": short_day_label(day),
                    "school_day": school,
                    "reason": reason,
                }
            )
        day_meta.append(meta_week)

    day_totals: dict[str, int] = {}
    for student in students:
        sid = int(student["id"])
        present_count = 0
        for week in weeks:
            for day in week:
                if day is None:
                    continue
                iso = day.isoformat()
                key = f"{sid}:{iso}"
                if day not in school_set:
                    cells[key] = None
                    continue
                marked = present_by_student_day.get((sid, iso))
                cells[key] = marked
                if marked is True:
                    present_count += 1
                    day_totals[iso] = day_totals.get(iso, 0) + 1
        totals[str(sid)] = present_count

    return {
        "weekday_headers": list(WEEKDAY_HEADERS),
        "weeks": [
            [d.isoformat() if d else None for d in week] for week in weeks
        ],
        "date_labels": [
            [(m["label"] if m else "") for m in week] for week in day_meta
        ],
        "day_meta": day_meta,
        "students": students,
        "cells": cells,
        "totals": totals,
        "day_totals": day_totals,
        "school_day_count": len(school_days),
        "first_day": days[0].isoformat() if days else None,
        "last_day": days[-1].isoformat() if days else None,
    }


def build_participation_week_grid(
    *,
    students: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    score_rows: list[dict[str, Any]],
    instructional_days: list[date] | None = None,
    span_days: list[date] | None = None,
    closed: dict[date, str] | None = None,
) -> dict[str, Any]:
    """Semester calendar grid for participation points (same columns as attendance).

    Sums credited points from non-template sessions onto each school-day cell.
    Multiple sessions on one day are aggregated.

    Args:
        students: Roster rows with ``id`` and display fields.
        sessions: Class sessions (``id``, ``starts_at``, ``status``).
        score_rows: ``session_scores`` with points and round slices.
        instructional_days: Optional school-day list.
        span_days: Optional Mon–Fri span including closed days.
        closed: Map of non-school dates to a reason label.

    Returns:
        Grid payload aligned with ``build_attendance_week_grid`` plus point cells.
    """
    school_days = (
        instructional_days
        if instructional_days is not None
        else load_instructional_weekdays()
    )
    school_set = set(school_days)
    closed_map = dict(closed or {})
    if closed is None:
        cal = load_semester_calendar()
        closed_map = dict(cal.closed)
    days = span_days if span_days is not None else teacher_weekday_span()
    weeks = group_weekdays_into_weeks(days)
    session_dates: dict[int, date] = {}
    for sess in sessions:
        if str(sess.get("status") or "") == "template":
            continue
        meeting = session_meeting_date(sess.get("starts_at"))
        if meeting is None:
            continue
        session_dates[int(sess["id"])] = meeting

    points_by_student_day: dict[tuple[int, str], dict[str, float]] = {}
    for row in score_rows:
        sid = int(row["student_id"])
        sess_id = int(row["session_id"])
        meeting = session_dates.get(sess_id)
        if meeting is None or meeting not in school_set:
            continue
        iso = meeting.isoformat()
        bucket = points_by_student_day.setdefault(
            (sid, iso),
            {"points": 0.0, "points_r1": 0.0, "points_r2": 0.0, "points_r3": 0.0},
        )
        for key in ("points", "points_r1", "points_r2", "points_r3"):
            bucket[key] = float(bucket.get(key, 0)) + float(row.get(key) or 0)

    cells: dict[str, dict[str, float] | None] = {}
    totals: dict[str, float] = {}
    day_totals: dict[str, float] = {}
    day_meta: list[list[dict[str, Any] | None]] = []
    for week in weeks:
        meta_week: list[dict[str, Any] | None] = []
        for day in week:
            if day is None:
                meta_week.append(None)
                continue
            school = day in school_set
            reason = "" if school else str(closed_map.get(day) or "No school")
            meta_week.append(
                {
                    "iso": day.isoformat(),
                    "label": short_day_label(day),
                    "school_day": school,
                    "reason": reason,
                }
            )
        day_meta.append(meta_week)

    for student in students:
        sid = int(student["id"])
        running = 0.0
        for week in weeks:
            for day in week:
                if day is None:
                    continue
                iso = day.isoformat()
                key = f"{sid}:{iso}"
                if day not in school_set:
                    cells[key] = None
                    continue
                cell = points_by_student_day.get((sid, iso))
                if cell is None:
                    cells[key] = {
                        "points": 0.0,
                        "points_r1": 0.0,
                        "points_r2": 0.0,
                        "points_r3": 0.0,
                    }
                    continue
                cells[key] = cell
                pts = float(cell.get("points") or 0)
                running += pts
                if pts:
                    day_totals[iso] = float(day_totals.get(iso, 0)) + pts
        totals[str(sid)] = running

    return {
        "weekday_headers": list(WEEKDAY_HEADERS),
        "weeks": [
            [d.isoformat() if d else None for d in week] for week in weeks
        ],
        "date_labels": [
            [(m["label"] if m else "") for m in week] for week in day_meta
        ],
        "day_meta": day_meta,
        "students": students,
        "cells": cells,
        "totals": totals,
        "day_totals": day_totals,
        "school_day_count": len(school_days),
        "first_day": days[0].isoformat() if days else None,
        "last_day": days[-1].isoformat() if days else None,
    }

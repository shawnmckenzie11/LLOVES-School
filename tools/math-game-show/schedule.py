#!/usr/bin/env python3
"""Semester defaults and next-meeting labels for Math Game Show classes."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parents[1]
SEMESTER_JSON = REPO_ROOT / "frameworks" / "semester.json"

DAY_PRESETS: dict[str, str] = {
    "M/W/F": "Mon/Wed/Fri",
    "T/Th/F": "Tue/Thu/Fri",
}

STORED_DAYS_TO_WEEKDAYS: dict[str, set[int]] = {
    "Mon/Wed/Fri": {0, 2, 4},
    "Tue/Thu/Fri": {1, 3, 4},
}

TIME_OPTIONS: tuple[str, ...] = (
    "8:00am",
    "9:15am",
    "10:40am",
    "12:35pm",
    "2:00pm",
    "3:15pm",
)

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*(am|pm)$", re.IGNORECASE)

WEEKDAY_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def load_semester_json(path: Path | None = None) -> dict[str, Any] | None:
    """Load ``frameworks/semester.json`` if it exists.

    Args:
        path: Override path (tests).

    Returns:
        Parsed JSON object, or None if missing/invalid.
    """
    target = path or SEMESTER_JSON
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def parse_semester_field(semester: str) -> tuple[str, str]:
    """Turn ``2026-2027 S1`` into ``(2026/27, Semester 1)``.

    Args:
        semester: Value of ``semester.json``'s ``semester`` field.

    Returns:
        ``(year_display, semester_name)``.
    """
    text = (semester or "").strip()
    parts = text.split()
    year_raw = parts[0] if parts else ""
    term = parts[1] if len(parts) > 1 else ""
    years = year_raw.split("-")
    if len(years) == 2 and years[0].isdigit() and years[1].isdigit():
        year_display = f"{years[0]}/{years[1][-2:]}"
    else:
        year_display = year_raw or "2026/27"
    if "S2" in term.upper():
        sem_name = "Semester 2"
    else:
        sem_name = "Semester 1"
    return year_display, sem_name


def heuristic_year_semester(today: date | None = None) -> tuple[str, str]:
    """Infer school year and semester from a calendar date.

    Sept–Jan → Semester 1; Feb–June → Semester 2; Jul–Aug → upcoming S1.

    Args:
        today: Date to classify (defaults to local today).

    Returns:
        ``(year_display, semester_name)`` e.g. ``("2026/27", "Semester 1")``.
    """
    day = today or date.today()
    y, m = day.year, day.month
    if m >= 9:
        return f"{y}/{(y + 1) % 100:02d}", "Semester 1"
    if m == 1:
        return f"{y - 1}/{y % 100:02d}", "Semester 1"
    if 2 <= m <= 6:
        return f"{y - 1}/{y % 100:02d}", "Semester 2"
    return f"{y}/{(y + 1) % 100:02d}", "Semester 1"


def skip_dates_from_semester(data: dict[str, Any] | None) -> set[date]:
    """Collect PD, holiday, and secondary exam dates from semester JSON.

    Args:
        data: Parsed ``semester.json`` object.

    Returns:
        Set of dates that are not live-class meeting days.
    """
    skipped: set[date] = set()
    if not data:
        return skipped

    def _add(value: str) -> None:
        """Parse a YYYY-MM-DD string into ``skipped``, ignoring bad values.

        Args:
            value: ISO date from semester JSON.
        """
        try:
            skipped.add(date.fromisoformat(value[:10]))
        except ValueError:
            return

    for row in data.get("pd_days") or []:
        if isinstance(row, dict) and row.get("date"):
            _add(str(row["date"]))
    for row in data.get("holidays") or []:
        if isinstance(row, dict) and row.get("date"):
            _add(str(row["date"]))
    exam = data.get("exam_window") or {}
    for value in exam.get("secondary_exam_days") or []:
        _add(str(value))
    return skipped


def first_instructional_day(data: dict[str, Any] | None) -> date | None:
    """Return the board first day of school from semester JSON.

    Args:
        data: Parsed ``semester.json`` object.
    """
    if not data:
        return None
    instructional = data.get("instructional") or {}
    raw = instructional.get("first_day_of_school")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def wizard_defaults(today: date | None = None) -> dict[str, Any]:
    """Defaults for the Create New Class wizard.

    Year/semester come from ``semester.json`` when present; course is MCF3M.

    Args:
        today: Optional date for the existing-class picker heuristic.

    Returns:
        Wizard field defaults plus picker year/semester and day/time options.
    """
    data = load_semester_json()
    if data and data.get("semester"):
        year, semester = parse_semester_field(str(data["semester"]))
    else:
        year, semester = heuristic_year_semester(today)
    picker_year, picker_semester = picker_year_semester(today)
    return {
        "year": year,
        "semester": semester,
        "course_code": "MCF3M",
        "picker_year": picker_year,
        "picker_semester": picker_semester,
        "day_options": [
            {"value": "M/W/F", "label": "M/W/F", "stored": "Mon/Wed/Fri"},
            {"value": "T/Th/F", "label": "T/Th/F", "stored": "Tue/Thu/Fri"},
        ],
        "time_options": list(TIME_OPTIONS),
    }


def picker_year_semester(today: date | None = None) -> tuple[str, str]:
    """Year/semester used to list existing classes.

    Uses ``semester.json`` when it agrees with the Sept–Jan / Feb–June
    heuristic (Jul–Aug counts as upcoming S1, so summer prep matches S1).
    Otherwise the heuristic wins so a stale JSON file does not hide S2.

    Args:
        today: Date to classify.

    Returns:
        ``(year_display, semester_name)``.
    """
    day = today or date.today()
    h_year, h_sem = heuristic_year_semester(day)
    data = load_semester_json()
    if data and data.get("semester"):
        j_year, j_sem = parse_semester_field(str(data["semester"]))
        if (j_year, j_sem) == (h_year, h_sem):
            return j_year, j_sem
    return h_year, h_sem


def store_days(preset: str) -> str:
    """Map wizard ``M/W/F`` / ``T/Th/F`` to stored weekday labels.

    Args:
        preset: Wizard days value or already-stored ``Mon/Wed/Fri`` form.

    Returns:
        Stored days string.

    Raises:
        ValueError: If the preset is unknown.
    """
    key = (preset or "").strip()
    if key in DAY_PRESETS:
        return DAY_PRESETS[key]
    if key in STORED_DAYS_TO_WEEKDAYS:
        return key
    raise ValueError("Days must be M/W/F or T/Th/F")


def parse_time_label(label: str) -> time:
    """Parse a wizard time like ``2:00pm`` into a ``datetime.time``.

    Args:
        label: One of :data:`TIME_OPTIONS`.

    Returns:
        Naive local time.

    Raises:
        ValueError: If the label is not a supported time.
    """
    text = (label or "").strip().lower().replace(" ", "")
    match = _TIME_RE.match(text)
    if not match:
        raise ValueError(f"Unsupported class time: {label}")
    hour = int(match.group(1))
    minute = int(match.group(2))
    ampm = match.group(3).lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    return time(hour=hour, minute=minute)


def format_header_label(when: datetime, time_label: str) -> str:
    """Format a session header like ``Tue 9/8 2:00pm``.

    Args:
        when: Session start datetime.
        time_label: Original wizard time string (keeps am/pm style).
    """
    abbr = WEEKDAY_ABBR[when.weekday()]
    return f"{abbr} {when.month}/{when.day} {time_label}"


def format_time_label(clock: time) -> str:
    """Turn a ``datetime.time`` into a wizard label like ``2:00pm``.

    Args:
        clock: Naive local time.
    """
    hour = clock.hour
    suffix = "am" if hour < 12 else "pm"
    display = hour % 12 or 12
    return f"{display}:{clock.minute:02d}{suffix}"


def unique_header_label(base: str, existing: set[str]) -> str:
    """Return ``base``, or ``base_2`` / ``base_3`` / … if that label is taken.

    Same calendar slot can be played more than once; suffixes keep SQLite
    headers distinct without bumping to the next class day.

    Args:
        base: Header from :func:`format_header_label`.
        existing: Headers already used by this class (other sessions).
    """
    if base not in existing:
        return base
    n = 2
    while f"{base}_{n}" in existing:
        n += 1
    return f"{base}_{n}"


def next_meeting_datetime(
    days_stored: str,
    time_label: str,
    *,
    today: date | None = None,
    after_date: date | None = None,
    semester_data: dict[str, Any] | None = None,
) -> datetime:
    """First instructional meeting on this class's weekdays at ``time_label``.

    Walks forward from ``max(today, first_day_of_school)``, or from the day
    after ``after_date`` when appending a column. Skips PD/holidays/exams.

    Args:
        days_stored: ``Mon/Wed/Fri`` or ``Tue/Thu/Fri``.
        time_label: Wizard time such as ``2:00pm``.
        today: Reference date (defaults to local today).
        after_date: If set, start looking the day after this date.
        semester_data: Optional parsed semester JSON (tests).

    Returns:
        Naive datetime for the next meeting.

    Raises:
        ValueError: If days/time are invalid or no date is found in a year.
    """
    weekdays = STORED_DAYS_TO_WEEKDAYS.get(days_stored)
    if not weekdays:
        raise ValueError(f"Unknown class days: {days_stored}")
    clock = parse_time_label(time_label)
    data = semester_data if semester_data is not None else load_semester_json()
    skipped = skip_dates_from_semester(data)
    first_day = first_instructional_day(data)
    start = today or date.today()
    if first_day:
        start = max(start, first_day)
    if after_date is not None:
        start = max(start, after_date + timedelta(days=1))
    cursor = start
    for _ in range(400):
        if cursor.weekday() in weekdays and cursor not in skipped:
            return datetime.combine(cursor, clock)
        cursor += timedelta(days=1)
    raise ValueError("Could not find a next class meeting in the next year")

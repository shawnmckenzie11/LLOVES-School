#!/usr/bin/env python3
"""MCK-231: refuse a production deploy while a live class is running.

Windows come from schedule data, not this file:

* ``lms/seeds/live_class_windows.json``: per course code ``live_days``
  (e.g. ``M/W/F``), ``live_time`` (start) and ``live_end_time``.
* ``frameworks/semester.json``: instructional days, holidays, PD and exam
  days. No class on those, so no block.

A deploy replaces the one Fly machine (about 25 s of refused connections
plus a cold boot under poll load), so it is blocked from ``PRE_MINUTES``
before each start until ``POST_MINUTES`` after each end, America/Toronto.

Exit 0 = clear to deploy, 1 = inside a class window (or bad data).
``--override`` always exits 0 and prints what it overrode.
``--at 2026-10-14T14:40`` checks a given local time (tests / dry runs).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
SCHEDULE = REPO / "lms" / "seeds" / "live_class_windows.json"
SEMESTER = REPO / "frameworks" / "semester.json"
DEFAULT_TZ = "America/Toronto"
TZ = ZoneInfo(DEFAULT_TZ)


def schedule_tz(schedule: dict) -> ZoneInfo:
    """The schedule's ``tz`` (IANA name), else America/Toronto.

    Raises:
        ValueError: ``tz`` is set but is not a known zone (fail closed).
    """
    name = schedule.get("tz") if isinstance(schedule, dict) else None
    if name in (None, ""):
        return ZoneInfo(DEFAULT_TZ)
    try:
        return ZoneInfo(str(name))
    except Exception as exc:  # ZoneInfoNotFoundError, ValueError
        raise ValueError(f"unknown tz {name!r}") from exc
PRE_MINUTES = 15
POST_MINUTES = 10

_DAY = {"M": 0, "T": 1, "W": 2, "R": 3, "TH": 3, "F": 4}


def parse_days(text: str) -> set[int]:
    """``M/W/F`` -> {0, 2, 4}. Accepts ``Th``/``R`` for Thursday."""
    out: set[int] = set()
    for part in re.split(r"[/,\s]+", str(text or "").strip().upper()):
        if part in _DAY:
            out.add(_DAY[part])
        elif part:
            raise ValueError(f"unknown day {part!r} in {text!r}")
    return out


def parse_clock(text: str) -> time:
    """``2:00pm`` / ``14:00`` -> time."""
    raw = str(text or "").strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)?", raw)
    if not m:
        raise ValueError(f"bad time {text!r}")
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ampm == "pm" and hour != 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    return time(hour, minute)


def class_windows(schedule: dict) -> list[dict]:
    """Every (label, days, start, end) the schedule data defines.

    Raises:
        ValueError: No courses, so the guard would never block (fail closed).
    """
    if not isinstance(schedule, dict) or not isinstance(schedule.get("courses"), dict) or not schedule["courses"]:
        raise ValueError("'courses' is missing or empty")
    rows: list[dict] = []
    for code, row in (schedule.get("courses") or {}).items():
        rows.append({"label": code, **row})
    out = []
    for row in rows:
        if not row.get("live_time") or not row.get("live_end_time") or not row.get("live_days"):
            raise ValueError(f"{row['label']}: live_days, live_time and live_end_time are required")
        out.append(
            {
                "label": row["label"],
                "days": parse_days(row["live_days"]),
                "start": parse_clock(row["live_time"]),
                "end": parse_clock(row["live_end_time"]),
            }
        )
    return out


def no_class_day(day: date, semester: dict) -> str | None:
    """Reason there is no class on ``day``, or None for a school day."""
    inst = semester.get("instructional") or {}
    first = inst.get("first_day_of_school")
    last = inst.get("last_instructional_day_before_exams")
    iso = day.isoformat()
    if first and iso < first:
        return "before the first day of school"
    if last and iso > last:
        return "after the last instructional day"
    for key in ("holidays", "pd_days"):
        for row in semester.get(key) or []:
            if row.get("date") == iso:
                return f"{key[:-1]} {row.get('name') or row.get('type') or ''}".strip()
    if iso in ((semester.get("exam_window") or {}).get("secondary_exam_days") or []):
        return "exam day"
    return None


def active_window(now: datetime, schedule: dict, semester: dict) -> dict | None:
    """The class window ``now`` falls in, padded by PRE/POST minutes."""
    tz = schedule_tz(schedule)
    windows = class_windows(schedule)
    local = now.astimezone(tz)
    if no_class_day(local.date(), semester):
        return None
    for win in windows:
        if local.weekday() not in win["days"]:
            continue
        start = datetime.combine(local.date(), win["start"], tz) - timedelta(minutes=PRE_MINUTES)
        end = datetime.combine(local.date(), win["end"], tz) + timedelta(minutes=POST_MINUTES)
        if start <= local < end:
            return {**win, "blocked_from": start, "blocked_until": end}
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--override", action="store_true")
    ap.add_argument("--at", help="Local time in the schedule's tz, ISO (default: now)")
    ap.add_argument("--schedule", type=Path, default=SCHEDULE)
    ap.add_argument("--semester", type=Path, default=SEMESTER)
    args = ap.parse_args(argv)
    try:
        schedule = json.loads(args.schedule.read_text(encoding="utf-8"))
        semester = json.loads(args.semester.read_text(encoding="utf-8"))
        tz = schedule_tz(schedule)
        now = datetime.fromisoformat(args.at).replace(tzinfo=tz) if args.at else datetime.now(tz)
        win = active_window(now, schedule, semester)
    except (OSError, ValueError) as exc:
        if args.override:
            print(f"Schedule data unreadable ({exc}); override set, deploying.")
            return 0
        print(f"::error title=Class-hours guard::Cannot read the class schedule {args.schedule}: {exc}. Deploy refused (fail closed); fix the file or use override_class_hours=true.")
        return 1
    stamp = now.strftime("%a %Y-%m-%d %H:%M ET")
    if win is None:
        print(f"Clear to deploy at {stamp}: no live class window.")
        return 0
    span = f"{win['blocked_from']:%H:%M}-{win['blocked_until']:%H:%M} ET"
    if args.override:
        print(f"::warning title=Class-hours override::Deploying during {win['label']} ({span}) at {stamp}.")
        return 0
    print(
        f"::error title=Deploy blocked during class::{win['label']} is live ({span}); it is {stamp}. "
        "A deploy replaces the only Fly machine. Run Deploy LLOVES LMS again after "
        f"{win['blocked_until']:%H:%M} ET, or use Run workflow with override_class_hours=true."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())

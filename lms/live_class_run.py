"""Classify ended live runs as real class vs test (MCK-207).

Real runs end on a scheduled live-class day (from the course/offering
schedule) within a window around the scheduled *end* time in
``America/Toronto``. Only the slack around that end time lives in
``config/live_class_run.json``; days and end times come from the database.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TORONTO = ZoneInfo("America/Toronto")
CONFIG_PATH = Path(__file__).resolve().parent / "config" / "live_class_run.json"


@dataclass(frozen=True)
class RealClassWindow:
    """Minutes before/after scheduled end that still count as a real class."""

    before_min: int
    after_min: int


@dataclass(frozen=True)
class LiveClassSchedule:
    """Days preset (``M/W/F``) and scheduled end time label (``10:30am``)."""

    days_key: str
    end_time_label: str


def load_real_class_window(path: Path | None = None) -> RealClassWindow:
    """Read slack defaults from ``live_class_run.json``.

    Args:
        path: Override config path (tests).

    Returns:
        Window with positive minute counts.
    """
    target = path or CONFIG_PATH
    data: dict[str, Any] = {}
    if target.is_file():
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw.get("real_class_window") or raw
        except (OSError, json.JSONDecodeError, TypeError):
            data = {}
    before = int(data.get("minutes_before_scheduled_end") or 20)
    after = int(data.get("minutes_after_scheduled_end") or 45)
    return RealClassWindow(before_min=max(0, before), after_min=max(0, after))


def _weekdays_for_days_key(days_key: str) -> set[int]:
    import sys

    try:
        from paths import MGS_DIR
    except ImportError:
        from lms.paths import MGS_DIR

    if str(MGS_DIR) not in sys.path:
        sys.path.insert(0, str(MGS_DIR))
    from schedule import DAY_PRESETS, STORED_DAYS_TO_WEEKDAYS, store_days

    key = (days_key or "").strip()
    if key in DAY_PRESETS:
        stored = store_days(key)
    elif key in STORED_DAYS_TO_WEEKDAYS:
        stored = key
    else:
        reverse = {v: k for k, v in DAY_PRESETS.items()}
        key = reverse.get(key, key)
        stored = store_days(key) if key in DAY_PRESETS else key
    return set(STORED_DAYS_TO_WEEKDAYS.get(stored, set()))


def _parse_time_label(label: str) -> time:
    import sys

    try:
        from paths import MGS_DIR
    except ImportError:
        from lms.paths import MGS_DIR

    if str(MGS_DIR) not in sys.path:
        sys.path.insert(0, str(MGS_DIR))
    from schedule import parse_time_label

    return parse_time_label(str(label or "").strip())


def classify_run_end(
    ended_at: datetime,
    schedule: LiveClassSchedule,
    *,
    window: RealClassWindow | None = None,
) -> bool:
    """Return True when ``ended_at`` looks like a real scheduled class end.

    Args:
        ended_at: When the run ended (aware or naive local).
        schedule: Course live days and scheduled end time.
        window: Slack around end time; defaults from config file.

    Returns:
        True for a real class run, False for a test run.
    """
    slack = window or load_real_class_window()
    if not str(schedule.days_key or "").strip() or not str(schedule.end_time_label or "").strip():
        return False
    weekdays = _weekdays_for_days_key(schedule.days_key)
    if not weekdays:
        return False
    if ended_at.tzinfo is None:
        local = ended_at.replace(tzinfo=TORONTO)
    else:
        local = ended_at.astimezone(TORONTO)
    if local.weekday() not in weekdays:
        return False
    end_clock = _parse_time_label(schedule.end_time_label)
    class_end = local.replace(
        hour=end_clock.hour, minute=end_clock.minute, second=0, microsecond=0
    )
    start = class_end - timedelta(minutes=slack.before_min)
    finish = class_end + timedelta(minutes=slack.after_min)
    return start <= local <= finish


def schedule_from_row(
    *,
    live_days: str | None,
    live_end_time: str | None,
    fallback_days: str | None = None,
    fallback_end: str | None = None,
) -> LiveClassSchedule | None:
    """Build a schedule from offering/course columns.

    Returns:
        ``LiveClassSchedule`` when days and end are known, else ``None``.
    """
    days = (live_days or "").strip() or (fallback_days or "").strip()
    end = (live_end_time or "").strip() or (fallback_end or "").strip()
    if not days or not end:
        return None
    return LiveClassSchedule(days_key=days, end_time_label=end)


def end_time_options() -> tuple[str, ...]:
    """Selectable scheduled end times (wizard starts plus common bell times)."""
    import sys

    try:
        from paths import MGS_DIR
    except ImportError:
        from lms.paths import MGS_DIR

    if str(MGS_DIR) not in sys.path:
        sys.path.insert(0, str(MGS_DIR))
    from schedule import TIME_OPTIONS

    extra = ("10:30am", "11:55am", "3:15pm")
    seen: set[str] = set()
    out: list[str] = []
    for label in (*TIME_OPTIONS, *extra):
        key = str(label or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(str(label))
    return tuple(out)


def parse_ended_at(value: str | None, *, fallback: datetime | None = None) -> datetime:
    """Parse session ``ended_at`` ISO into Toronto-aware datetime."""
    text = str(value or "").strip()
    if text:
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            dt = None
        if dt is not None:
            if dt.tzinfo is None:
                return dt.replace(tzinfo=TORONTO)
            return dt.astimezone(TORONTO)
    ref = fallback or datetime.now(TORONTO)
    if ref.tzinfo is None:
        return ref.replace(tzinfo=TORONTO)
    return ref.astimezone(TORONTO)

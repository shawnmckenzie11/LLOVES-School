"""Scheduled class window helpers (legacy MCK-207).

MCK-233: Test vs Live is chosen on the End Live Class dialog (schedule
only suggests the default). These helpers remain for tests and legacy
end-window utilities; production no longer auto-discards runs by time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TORONTO = ZoneInfo("America/Toronto")
UTC = ZoneInfo("UTC")
CONFIG_PATH = Path(__file__).resolve().parent / "config" / "live_class_run.json"


@dataclass(frozen=True)
class RealClassWindow:
    """Minutes before/after scheduled end that still count as a real class."""

    before_min: int
    after_min: int


@dataclass(frozen=True)
class LiveClassSchedule:
    """Days preset (``M/W/F``) and scheduled start/end time labels."""

    days_key: str
    end_time_label: str
    start_time_label: str = ""


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
        local = ended_at.replace(tzinfo=UTC).astimezone(TORONTO)
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




def load_real_class_start_window(path: Path | None = None) -> RealClassWindow:
    """Read start-time slack from ``live_class_run.json`` (MCK-229 backfill)."""
    target = path or CONFIG_PATH
    data: dict[str, Any] = {}
    if target.is_file():
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw.get("real_class_start_window") or raw
        except (OSError, json.JSONDecodeError, TypeError):
            data = {}
    before = int(data.get("minutes_before_scheduled_start") or 10)
    after = int(data.get("minutes_after_scheduled_start") or 90)
    return RealClassWindow(before_min=max(0, before), after_min=max(0, after))


def classify_run_start(
    ended_at: datetime,
    schedule: LiveClassSchedule,
    *,
    window: RealClassWindow | None = None,
) -> bool:
    """Return True when ``ended_at`` falls on a scheduled day within the start window."""
    slack = window or load_real_class_start_window()
    if not str(schedule.days_key or "").strip() or not str(schedule.start_time_label or "").strip():
        return False
    weekdays = _weekdays_for_days_key(schedule.days_key)
    local = ended_at.astimezone(TORONTO) if ended_at.tzinfo else ended_at.replace(tzinfo=TORONTO)
    if local.weekday() not in weekdays:
        return False
    start_clock = _parse_time_label(schedule.start_time_label)
    class_start = local.replace(
        hour=start_clock.hour, minute=start_clock.minute, second=0, microsecond=0
    )
    start = class_start - timedelta(minutes=slack.before_min)
    finish = class_start + timedelta(minutes=slack.after_min)
    return start <= local <= finish

def schedule_from_row(
    *,
    live_days: str | None,
    live_end_time: str | None,
    live_time: str | None = None,
    fallback_days: str | None = None,
    fallback_end: str | None = None,
    fallback_start: str | None = None,
) -> LiveClassSchedule | None:
    """Build a schedule from offering/course columns.

    Returns:
        ``LiveClassSchedule`` when days and end are known, else ``None``.
    """
    days = (live_days or "").strip() or (fallback_days or "").strip()
    end = (live_end_time or "").strip() or (fallback_end or "").strip()
    start = (live_time or "").strip() or (fallback_start or "").strip()
    if not days or not end:
        return None
    return LiveClassSchedule(days_key=days, end_time_label=end, start_time_label=start)


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
    """Parse session ``ended_at`` ISO into Toronto-aware datetime.

    Production stores UTC timestamps without a ``Z`` suffix; treat zone-less
    values as UTC before converting to ``America/Toronto``.
    """
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if text:
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            dt = None
        if dt is not None:
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC).astimezone(TORONTO)
            return dt.astimezone(TORONTO)
    ref = fallback or datetime.now(TORONTO)
    if ref.tzinfo is None:
        return ref.replace(tzinfo=UTC).astimezone(TORONTO)
    return ref.astimezone(TORONTO)


def scheduled_class_end_datetime(
    schedule: LiveClassSchedule,
    *,
    which: int = 0,
    reference: date | None = None,
    window: RealClassWindow | None = None,
) -> datetime:
    """Return a Toronto ``ended_at`` on a real scheduled class day at bell end.

    Walks backward from ``reference`` (default today in Toronto) through
    weekdays that match ``schedule.days_key``. ``which=0`` is the most recent
    matching day; ``which=1`` is the one before that, and so on.

    Args:
        schedule: Live days and scheduled end time from the course/offering.
        which: How many matching days to step back (0 = latest).
        reference: Calendar day to start from (Toronto).
        window: Slack around end; defaults from config.

    Returns:
        Aware datetime at scheduled end on the chosen day.

    Raises:
        ValueError: No matching day within a year of ``reference``.
    """
    slack = window or load_real_class_window()
    if not str(schedule.days_key or "").strip() or not str(
        schedule.end_time_label or ""
    ).strip():
        raise ValueError("schedule days and end time are required")
    weekdays = _weekdays_for_days_key(schedule.days_key)
    if not weekdays:
        raise ValueError(f"unknown live days preset: {schedule.days_key!r}")
    end_clock = _parse_time_label(schedule.end_time_label)
    ref_day = reference or datetime.now(TORONTO).date()
    hits = 0
    day = ref_day
    for _ in range(370):
        if day.weekday() in weekdays:
            candidate = datetime(
                day.year,
                day.month,
                day.day,
                end_clock.hour,
                end_clock.minute,
                tzinfo=TORONTO,
            )
            if classify_run_end(candidate, schedule, window=slack):
                if hits == int(which):
                    return candidate
                hits += 1
        day -= timedelta(days=1)
    raise ValueError(
        f"no scheduled class end within 370 days before {ref_day} for {schedule!r}"
    )

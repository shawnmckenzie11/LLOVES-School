"""End Live Class Test vs Live default from scheduled start window (MCK-233)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from live_class_run import TORONTO, UTC, _parse_time_label, _weekdays_for_days_key, parse_ended_at

LIVE_WINDOW_BEFORE_START_MIN = 10
LIVE_WINDOW_AFTER_START_MIN = 90


@dataclass(frozen=True)
class LiveClassStartSchedule:
    """Scheduled live-class weekdays and bell start label (``9:15am``)."""

    days_key: str
    start_time_label: str


def default_end_live_run_mode(
    started_at: datetime,
    schedule: LiveClassStartSchedule,
    *,
    before_min: int = LIVE_WINDOW_BEFORE_START_MIN,
    after_min: int = LIVE_WINDOW_AFTER_START_MIN,
) -> str:
    """Return ``live`` or ``test`` for the End Live Class dialog default.

    Default is ``test`` unless ``started_at`` falls on a scheduled class day
    within ``before_min`` minutes before through ``after_min`` minutes after
    the scheduled start time (``America/Toronto``).
    """
    if not str(schedule.days_key or "").strip() or not str(
        schedule.start_time_label or ""
    ).strip():
        return "test"
    weekdays = _weekdays_for_days_key(schedule.days_key)
    if not weekdays:
        return "test"
    if started_at.tzinfo is None:
        # Production stores naive timestamps as UTC (see parse_ended_at).
        local = started_at.replace(tzinfo=UTC).astimezone(TORONTO)
    else:
        local = started_at.astimezone(TORONTO)
    if local.weekday() not in weekdays:
        return "test"
    start_clock = _parse_time_label(schedule.start_time_label)
    class_start = local.replace(
        hour=start_clock.hour, minute=start_clock.minute, second=0, microsecond=0
    )
    window_start = class_start - timedelta(minutes=max(0, int(before_min)))
    window_end = class_start + timedelta(minutes=max(0, int(after_min)))
    if window_start <= local <= window_end:
        return "live"
    return "test"


def parse_session_started_at(value: str | None) -> datetime:
    """Parse ``live_class_sessions.started_at`` like ``parse_ended_at``."""
    return parse_ended_at(value)


def resolve_live_run_from_posted_run_mode(
    run_mode: str | None,
    *,
    default_mode: str,
) -> bool:
    """Return True when the End Live run should persist as Live (MCK-233).

    An explicit ``live`` or ``test`` in the form wins. When ``run_mode`` is
    omitted, use ``default_mode`` (same schedule window as the staff UI).
    """
    token = str(run_mode or "").strip().lower()
    if token == "live":
        return True
    if token == "test":
        return False
    return str(default_mode or "test").strip().lower() == "live"

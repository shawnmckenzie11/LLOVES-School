"""Load live-class schedule defaults from ``seeds/live_class_schedules.json`` (MCK-210)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class CatchupScheduleError(RuntimeError):
    """Raised when a class cannot be classified for catch-up repair."""


def seed_path() -> Path:
    """Path to the schedule seed JSON shipped with the LMS."""
    return Path(__file__).resolve().parent / "seeds" / "live_class_schedules.json"


def load_live_class_schedule_seed(path: Path | None = None) -> dict[str, Any]:
    """Parse schedule seed data for offering sync (not used at classify time)."""
    target = path or seed_path()
    if not target.is_file():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def schedule_defaults_for_offering(
    ontario_code: str,
    section_index: int,
    *,
    seed: dict[str, Any] | None = None,
) -> dict[str, str] | None:
    """Return ``live_days`` / ``live_end_time`` from seed for one offering section.

    Section-specific rows in ``offering_sections`` override course defaults.
    """
    data = seed if seed is not None else load_live_class_schedule_seed()
    code = str(ontario_code or "").strip().upper()
    index = int(section_index or 1)
    sections = data.get("offering_sections") if isinstance(data, dict) else None
    if isinstance(sections, list):
        for row in sections:
            if not isinstance(row, dict):
                continue
            if str(row.get("ontario_code") or "").strip().upper() != code:
                continue
            try:
                row_index = int(row.get("section_index") or 1)
            except (TypeError, ValueError):
                row_index = 1
            if row_index != index:
                continue
            days = str(row.get("live_days") or "").strip()
            end = str(row.get("live_end_time") or "").strip()
            if days or end:
                return {"live_days": days, "live_end_time": end}
    courses = data.get("courses") if isinstance(data, dict) else None
    if isinstance(courses, dict):
        row = courses.get(code)
        if isinstance(row, dict):
            days = str(row.get("live_days") or "").strip()
            end = str(row.get("live_end_time") or "").strip()
            if days or end:
                return {"live_days": days, "live_end_time": end}
    return None

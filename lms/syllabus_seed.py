"""Place module-present assessments on the syllabus calendar by due date."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    import syllabus as syllabus_mod
except ImportError:
    from lms import syllabus as syllabus_mod


def parse_due_date(raw: str | None) -> date | None:
    """Parse a Canvas ``due_at`` stamp into an America/Toronto calendar date.

    Args:
        raw: ISO-8601 timestamp from assignment/quiz settings.
    """
    text = (raw or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.date()
    try:
        from zoneinfo import ZoneInfo

        return dt.astimezone(ZoneInfo("America/Toronto")).date()
    except Exception:  # noqa: BLE001 — fall back to the stated calendar date
        return dt.date()


def snap_to_content_day(day: date, content: list[date]) -> date | None:
    """Move a due date onto a module-work school day.

    Args:
        day: Parsed due date.
        content: ``content_days`` from the semester calendar (no intro/review).
    """
    pool = sorted(content)
    if not pool:
        return None
    allowed = set(pool)
    if day in allowed:
        return day
    earlier = [item for item in pool if item <= day]
    if earlier:
        return earlier[-1]
    later = [item for item in pool if item >= day]
    return later[0] if later else None


def slots_from_offering(live_days: str | None, live_time: str | None) -> list[Any]:
    """Live-class slots from Admin wizard days/time on the offering.

    Args:
        live_days: Wizard ``M/W/F`` / ``T/Th/F`` or stored weekday labels.
        live_time: Wizard start time such as ``2:00pm``.
    """
    from schedule import DAY_PRESETS, store_days

    days_key = (live_days or "").strip()
    time_key = (live_time or "").strip()
    if not days_key or not time_key:
        return []
    if days_key in DAY_PRESETS:
        stored = store_days(days_key)
    elif days_key in {"Mon/Wed/Fri", "Tue/Thu/Fri"}:
        stored = days_key
    else:
        return []
    try:
        return syllabus_mod.slots_from_class(stored, time_key)
    except ValueError:
        return []


def _assessment_kind(component_type: str, title: str) -> str:
    """Map a module item to a syllabus assessment kind.

    Args:
        component_type: Stored ``assignment`` or ``quiz``.
        title: Module item title.
    """
    from syllabus_calendar import is_end_of_module_test, is_skipped_assessment

    if is_skipped_assessment(title):
        return ""
    if component_type == "assignment":
        return "assignment"
    if component_type == "quiz":
        if is_end_of_module_test(title, "Quizzes::Quiz"):
            return "test"
        return "quiz"
    return ""


def _component_due_at(
    db: Any, library_id: int, component_type: str, component_id: int
) -> str | None:
    """Return the stored Canvas ``due_at`` for one assignment or quiz.

    Args:
        db: School database.
        library_id: Owning library.
        component_type: ``assignment`` or ``quiz``.
        component_id: Row id in that table.
    """
    table = "assignments" if component_type == "assignment" else "quizzes"
    if table not in {"assignments", "quizzes"}:
        return None
    row = db.conn.execute(
        f"SELECT settings_json FROM {table} WHERE id = ? AND library_id = ?",
        (int(component_id), int(library_id)),
    ).fetchone()
    if row is None:
        return None
    try:
        settings = json.loads(row["settings_json"] or "{}")
    except json.JSONDecodeError:
        return None
    raw = settings.get("due_at")
    return str(raw) if raw else None


def seed_syllabus_from_due_dates(
    db: Any,
    offering: dict[str, Any],
    *,
    data_dir: Path,
) -> dict[str, Any] | None:
    """Place module-present tests, quizzes, and assignments by Canvas due date.

    Skips when a syllabus calendar already exists for this offering.
    Dates snap to content-pool school days.

    Args:
        db: School database.
        offering: Course offering dict.
        data_dir: LMS data volume.
    """
    try:
        from components import outline_nav, outline_raw_modules
        from syllabus_calendar import content_days, is_skipped_assessment
    except ImportError:
        from lms.components import outline_nav, outline_raw_modules
        from syllabus_calendar import content_days, is_skipped_assessment

    library_id = offering.get("library_id")
    if not library_id:
        return None
    semester = db.get_semester(int(offering["semester_id"]))
    label = str(semester.get("label") or "")
    code = str(offering.get("ontario_code") or "")
    instance_relpath = offering.get("instance_relpath")
    if syllabus_mod.saved_html_path(
        label, code, data_dir=data_dir, instance_relpath=instance_relpath
    ):
        return None
    out_dir = syllabus_mod.offering_output_dir(
        label, code, data_dir=data_dir, instance_relpath=instance_relpath
    )
    slug = label.replace(" ", "-")
    if (out_dir / f"{slug}.answers.json").is_file():
        return None
    calendar = syllabus_mod.calendar_from_semester_row(
        semester, data_dir=data_dir, instance_relpath=instance_relpath
    )
    pool = content_days(calendar)
    nav = outline_nav(db, int(library_id))
    if not nav:
        return None
    placements: dict[str, dict[str, Any]] = {}
    for index, module in enumerate(nav, start=1):
        for item in module.get("items") or []:
            component_type = str(item.get("component_type") or "")
            title = str(item.get("title") or "").strip()
            if not title or is_skipped_assessment(title):
                continue
            kind = _assessment_kind(component_type, title)
            if not kind:
                continue
            component_id = item.get("component_id")
            if not component_id:
                continue
            parsed = parse_due_date(
                _component_due_at(db, int(library_id), component_type, int(component_id))
            )
            if parsed is None:
                continue
            day = snap_to_content_day(parsed, pool)
            if day is None:
                continue
            iso = day.isoformat()
            existing = placements.get(iso)
            if existing:
                prior = str(existing.get("assessment_title") or "").strip()
                existing["assessment_title"] = (
                    f"{prior} · {title}" if prior else title
                )
                rank = {"test": 3, "quiz": 2, "assignment": 1}
                if rank.get(kind, 0) > rank.get(
                    str(existing.get("assessment_kind") or ""), 0
                ):
                    existing["assessment_kind"] = kind
                continue
            placements[iso] = {
                "module": index,
                "assessment_kind": kind,
                "assessment_title": title,
            }
    if not placements:
        return None
    modules = syllabus_mod.editor_modules_from_outline(
        outline_raw_modules(db, int(library_id))
    )
    if not modules:
        return None
    return syllabus_mod.save_placements(
        payload={"placements": placements},
        course=code,
        semester_label=label,
        calendar=calendar,
        slots=slots_from_offering(offering.get("live_days"), offering.get("live_time")),
        modules=modules,
        data_dir=data_dir,
        instance_relpath=instance_relpath,
    )

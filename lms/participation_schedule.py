"""Display-only module and slot labels for untagged participation columns.

Older game sessions have points and no stored ``live_module``. The
Participation tab labels them from the class meeting calendar: two class
days and, when Friday is on the schedule, an office day. Nothing here is
written to participation runs, marks, or observations.

For a Monday/Wednesday/Friday course the first module week is::

    Mon Sep 14 M1 C1
    Wed Sep 16 M1 C2
    Fri Sep 18 M1 O1
    Mon Sep 21 M1 C3
    Wed Sep 23 M1 C4 and M1 O2

``O1`` and ``O2`` are omitted when the course does not meet on Friday.
A session whose date is not on that calendar stays visible as ``Untagged``.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from participation_bands import (
    UNTAGGED_MODULE,
    format_participation_grouped_header,
    is_office_slot,
    untagged_column_label,
)
from participation_exclusion import calendar_date_from_stored
from participation_weight import module_number

try:
    from paths import SEMESTER_JSON
except ImportError:
    from lms.paths import SEMESTER_JSON

_OFFICE_WEEKDAY = 4


def load_semester_payload(path: Any | None = None) -> dict[str, Any]:
    """Read ``frameworks/semester.json`` for the meeting calendar.

    Args:
        path: Override file. Defaults to the repo semester file.

    Returns:
        Parsed object, or ``{}`` when the file is missing or invalid.
    """
    target = path or SEMESTER_JSON
    try:
        raw = target.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _iso_dates(rows: Any) -> set[date]:
    """Collect ``YYYY-MM-DD`` values from semester date rows."""
    found: set[date] = set()
    if isinstance(rows, dict):
        rows = [rows]
    for row in rows or []:
        raw = row.get("date") if isinstance(row, dict) else row
        try:
            found.add(date.fromisoformat(str(raw)[:10]))
        except (TypeError, ValueError):
            continue
    return found


def closed_school_dates(semester: dict[str, Any] | None) -> set[date]:
    """Holidays, PD days, and exam days, which are not module meetings.

    Args:
        semester: Parsed ``semester.json`` object.
    """
    data = semester or {}
    closed = _iso_dates(data.get("holidays"))
    closed |= _iso_dates(data.get("pd_days"))
    exam = data.get("exam_window") or {}
    closed |= _iso_dates(exam.get("secondary_exam_days") if isinstance(exam, dict) else None)
    return closed


def _bound(semester: dict[str, Any], key: str) -> date | None:
    """One instructional bound from the semester payload."""
    instructional = semester.get("instructional") or {}
    if not isinstance(instructional, dict):
        return None
    raw = instructional.get(key)
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def instructional_days(semester: dict[str, Any] | None) -> list[date]:
    """Weekdays from the first school day through the last day before exams.

    Holidays and PD days are left out. Weekends are left out.

    Args:
        semester: Parsed ``semester.json`` object.
    """
    data = semester or {}
    first = _bound(data, "first_day_of_school")
    last = _bound(data, "last_instructional_day_before_exams")
    if first is None or last is None or last < first:
        return []
    closed = closed_school_dates(data)
    days: list[date] = []
    cursor = first
    while cursor <= last:
        if cursor.weekday() < 5 and cursor not in closed:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def review_starts_on(semester: dict[str, Any] | None) -> date | None:
    """Monday of the last instructional week before the exam window.

    The leftover instructional day after that week (the Monday of exam
    week, when there is one) is review as well, so mapping stops at this
    Monday.

    Args:
        semester: Parsed ``semester.json`` object.
    """
    data = semester or {}
    exam = data.get("exam_window") or {}
    exam_days = sorted(
        _iso_dates(exam.get("secondary_exam_days") if isinstance(exam, dict) else None)
    )
    if not exam_days:
        return None
    exam_monday = exam_days[0] - timedelta(days=exam_days[0].weekday())
    return exam_monday - timedelta(days=7)


def live_weekdays(days_key: str) -> set[int]:
    """Weekday numbers for ``M/W/F`` or ``Mon/Wed/Fri``.

    Args:
        days_key: Class schedule preset or stored label.

    Returns:
        ``datetime.weekday`` values, or an empty set when unknown.
    """
    try:
        from schedule import DAY_PRESETS, STORED_DAYS_TO_WEEKDAYS, store_days
    except ImportError:
        from lms.paths import MGS_DIR
        import sys

        if str(MGS_DIR) not in sys.path:
            sys.path.insert(0, str(MGS_DIR))
        from schedule import DAY_PRESETS, STORED_DAYS_TO_WEEKDAYS, store_days

    key = str(days_key or "").strip()
    if key in DAY_PRESETS:
        stored = store_days(key)
    elif key in STORED_DAYS_TO_WEEKDAYS:
        stored = key
    else:
        reverse = {value: name for name, value in DAY_PRESETS.items()}
        stored = key if key in reverse else ""
    return set(STORED_DAYS_TO_WEEKDAYS.get(stored) or ())


def course_module_slots(course_code: str) -> list[tuple[str, list[str]]]:
    """Class slots in teaching order for one Ontario course.

    Office files are not part of the live-class template. ``O1`` and
    ``O2`` are added by :func:`participation_date_slots` when Friday is a
    meeting day.

    Args:
        course_code: Ontario code such as ``MCF3M``.

    Returns:
        ``(module, [C1, C2, ...])`` pairs. Falls back to eight modules of
        ``C1``–``C4`` when the course has no template files.
    """
    try:
        from live_class_packs import live_class_registry
    except ImportError:
        from lms.live_class_packs import live_class_registry

    registry = live_class_registry(course_code)
    slots_by = registry.get("slots_by_module") or {}
    rows: list[tuple[str, list[str]]] = []
    for module in registry.get("modules") or []:
        token = str(module or "").strip().upper()
        slots = [
            str(slot).strip().upper()
            for slot in (slots_by.get(module) or slots_by.get(token) or [])
            if str(slot).strip().upper().startswith("C")
        ]
        if token and slots:
            rows.append((token, slots))
    if rows:
        return rows
    return [(f"M{number}", ["C1", "C2", "C3", "C4"]) for number in range(1, 9)]


def _add_slot(
    found: dict[date, list[tuple[str, str]]],
    day: date,
    module: str,
    slot: str,
) -> None:
    """Append one display slot on ``day`` without duplicating it."""
    pair = (module, slot)
    bucket = found.setdefault(day, [])
    if pair not in bucket:
        bucket.append(pair)


def participation_date_slots(
    live_days: str,
    *,
    semester: dict[str, Any] | None = None,
    module_slots: list[tuple[str, list[str]]] | None = None,
    include_office: bool | None = None,
) -> dict[date, list[tuple[str, str]]]:
    """Map each meeting date to the module slots taught that day.

    Each module occupies the next two-week block that contains a school
    day, starting the first class weekday on or after the third
    instructional day. Class slots alternate across the class weekdays
    (Monday then Wednesday for ``M/W/F``): the first of each is ``C1`` and
    ``C2``, the second is ``C3`` and ``C4``. A holiday does not move a
    later slot onto an earlier weekday. When Friday is a meeting day,
    the first Friday of the block is ``O1`` and ``O2`` shares the ``C4``
    date. A two-week block with no school days, such as winter break, is
    skipped and does not consume a module.

    Args:
        live_days: ``M/W/F``, ``Mon/Wed/Fri``, or the Tuesday/Thursday form.
        semester: Parsed semester object. Defaults to ``semester.json``.
        module_slots: Course slot order. Defaults to eight ``C1``–``C4`` modules.
        include_office: Add ``O1`` and ``O2``. Defaults to whether Friday
            is one of ``live_days``.

    Returns:
        Date → ``[(module, slot), ...]`` in class-then-office order.
        Two slots on one date (``C4`` and ``O2``) stay in that order.
    """
    data = semester if semester is not None else load_semester_payload()
    weekdays = live_weekdays(live_days)
    office = include_office if include_office is not None else (_OFFICE_WEEKDAY in weekdays)
    class_days = sorted(day for day in weekdays if day != _OFFICE_WEEKDAY)
    if not class_days:
        class_days = sorted(weekdays)
    if not class_days:
        return {}
    slots = list(module_slots or [(f"M{number}", ["C1", "C2", "C3", "C4"]) for number in range(1, 9)])
    school = instructional_days(data)
    if len(school) <= 2:
        return {}
    content_start = school[2]
    review = review_starts_on(data)
    last_content = school[-1]
    if review is not None:
        last_content = min(last_content, review - timedelta(days=1))
    closed = closed_school_dates(data)
    week_start = class_days[0]
    delta = (week_start - content_start.weekday()) % 7
    block_start = content_start + timedelta(days=delta)
    found: dict[date, list[tuple[str, str]]] = {}
    module_index = 0
    while module_index < len(slots) and block_start <= last_content:
        window = [block_start + timedelta(days=offset) for offset in range(14)]
        live = [
            day
            for day in window
            if day.weekday() in weekdays and day not in closed and content_start <= day <= last_content
        ]
        block_start = block_start + timedelta(days=14)
        if not live:
            continue
        module, class_slots = slots[module_index]
        module_index += 1
        # Count weekdays inside the block, including holidays, so a closed
        # Monday drops C1 instead of moving it onto the next Monday.
        by_weekday = {
            weekday: [
                day
                for day in window
                if day.weekday() == weekday and content_start <= day <= last_content
            ]
            for weekday in class_days
        }
        last_slot_date: date | None = None
        for index, slot in enumerate(class_slots):
            weekday = class_days[index % len(class_days)]
            occurrence = index // len(class_days)
            days = by_weekday[weekday]
            if occurrence >= len(days):
                continue
            meeting = days[occurrence]
            if meeting in closed:
                continue
            _add_slot(found, meeting, module, slot)
            if index == len(class_slots) - 1:
                last_slot_date = meeting
        if office:
            fridays = [
                day
                for day in window
                if day.weekday() == _OFFICE_WEEKDAY and content_start <= day <= last_content
            ]
            if fridays and fridays[0] not in closed:
                _add_slot(found, fridays[0], module, "O1")
        if office and last_slot_date is not None:
            _add_slot(found, last_slot_date, module, "O2")
    return found


def _session_day(session: dict[str, Any], meta: dict[str, Any]) -> date | None:
    """Calendar date for one column, preferring the game session start."""
    for raw in (session.get("starts_at"), meta.get("ended_at"), meta.get("starts_at")):
        day = calendar_date_from_stored(str(raw or ""))
        if day is not None:
            return day
    return None


def label_sessions_from_schedule(
    sessions: list[dict[str, Any]],
    stored_meta: dict[Any, dict[str, Any]],
    date_slots: dict[date, list[tuple[str, str]]],
    *,
    today: date | None = None,
) -> dict[str, dict[str, Any]]:
    """Copy session metadata and fill blank modules from ``date_slots``.

    A stored module is left as stored. Blank sessions on one date take the
    remaining slots in time order, so two sessions on the ``C4`` / ``O2``
    day become those two slots. A session with no slot left is ``Untagged``.
    The returned dict is for the Participation table only.

    Args:
        sessions: Dashboard session rows (``id``, ``starts_at``, ``status``).
        stored_meta: ``participation_session_meta`` result. Not modified.
        date_slots: Output of :func:`participation_date_slots`.
        today: Date used to treat a past meeting as ended. Defaults to
            the local today.

    Returns:
        Session id → display metadata, including columns that had none.
    """
    clock = today or date.today()
    by_id: dict[str, dict[str, Any]] = {}
    for key, info in (stored_meta or {}).items():
        by_id[str(key)] = dict(info)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for session in sessions or []:
        try:
            sid = str(int(session["id"]))
        except (KeyError, TypeError, ValueError):
            continue
        seen.add(sid)
        rows.append(session)
        by_id.setdefault(sid, {})
    for sid, info in list(by_id.items()):
        if sid in seen:
            continue
        rows.append({"id": int(sid), "starts_at": info.get("ended_at") or ""})
    grouped: dict[date, list[tuple[str, dict[str, Any]]]] = {}
    used_on: dict[date, list[tuple[str, str]]] = {}
    for session in rows:
        sid = str(int(session["id"]))
        info = by_id[sid]
        module = str(info.get("live_module") or "").strip().upper()
        day = _session_day(session, info)
        slot = str(info.get("live_slot") or "").strip().upper()
        if module_number(module) > 0:
            if day is not None and slot:
                used_on.setdefault(day, []).append((module, slot))
            continue
        if day is None:
            _mark_untagged(info, "")
            continue
        grouped.setdefault(day, []).append((sid, session))
    for day, members in grouped.items():
        members.sort(key=lambda item: (str(item[1].get("starts_at") or ""), int(item[0])))
        available = list(date_slots.get(day) or [])
        for pair in used_on.get(day) or []:
            if pair in available:
                available.remove(pair)
        for sid, session in members:
            info = by_id[sid]
            if module_number(str(info.get("live_module") or "")) > 0:
                continue
            if not available:
                _mark_untagged(info, str(session.get("starts_at") or info.get("ended_at") or ""))
                continue
            module, slot = available.pop(0)
            _mark_mapped(info, session, module, slot, clock)
    return by_id


def _mark_mapped(
    info: dict[str, Any],
    session: dict[str, Any],
    module: str,
    slot: str,
    today: date,
) -> None:
    """Set display-only module fields on one metadata copy."""
    started = str(session.get("starts_at") or info.get("ended_at") or "")
    day = calendar_date_from_stored(started)
    info["live_module"] = module
    info["live_slot"] = slot
    info["open_office"] = is_office_slot(slot)
    if not str(info.get("ended_at") or "").strip():
        info["ended_at"] = started
    if "class_ended" not in info:
        status = str(session.get("status") or "").strip().lower()
        info["class_ended"] = status == "ended" or (day is not None and day < today)
    info["header_label"] = format_participation_grouped_header(
        module, slot, str(info.get("ended_at") or started)
    )
    info["schedule_label"] = True


def _mark_untagged(info: dict[str, Any], started: str) -> None:
    """Label one column Untagged. Never substitutes ``M1 C1``."""
    info["live_module"] = UNTAGGED_MODULE
    info["live_slot"] = ""
    info["open_office"] = False
    if started and not str(info.get("ended_at") or "").strip():
        info["ended_at"] = started
    info["header_label"] = untagged_column_label(str(info.get("ended_at") or started))
    info["schedule_label"] = True

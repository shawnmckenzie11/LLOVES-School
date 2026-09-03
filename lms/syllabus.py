"""Flask helpers for the click-to-place syllabus editor (no sequential packer)."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, datetime, timedelta, time
from pathlib import Path
from typing import Any

from paths import REPO_ROOT, SCRIPTS_DIR, SEMESTER_JSON, SYLLABUS_DATA_DIR

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

_spec = importlib.util.spec_from_file_location(
    "syllabus_calendar", SCRIPTS_DIR / "syllabus_calendar.py"
)
if _spec is None or _spec.loader is None:
    raise ImportError("scripts/syllabus_calendar.py is missing")
_cal = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("syllabus_calendar", _cal)
_spec.loader.exec_module(_cal)

LiveSlot = _cal.LiveSlot
SemesterCalendar = _cal.SemesterCalendar
load_semester_calendar = _cal.load_semester_calendar
load_modules_from_imscc_file = _cal.load_modules_from_imscc_file
modules_from_uploaded_imscc = _cal.modules_from_uploaded_imscc
render_editor_html = _cal.render_editor_html
parse_editor_placements = _cal.parse_editor_placements
build_table_rows_from_placements = _cal.build_table_rows_from_placements
write_calendar_outputs = _cal.write_calendar_outputs
render_html = _cal.render_html


def calendar_from_semester_row(
    row: dict[str, Any],
    *,
    data_dir: Path | None = None,
    instance_relpath: str | None = None,
) -> Any:
    """Build a ``SemesterCalendar`` from an IT-activated semester row.

    Prefers the cloned ``raw_json`` (same fields as ``semester.json``). Falls
    back to reconstructing a payload from the row columns. Writes the temp
    JSON onto the data volume (instance ``syllabus/`` when known).

    Args:
        row: ``semesters`` dict.
        data_dir: LMS data volume.
        instance_relpath: Offering instance folder, if any.
    """
    if data_dir is not None and instance_relpath:
        tmp_parent = Path(data_dir) / str(instance_relpath) / "syllabus"
    elif data_dir is not None:
        tmp_parent = Path(data_dir)
    else:
        tmp_parent = SYLLABUS_DATA_DIR
    raw = row.get("raw_json")
    if raw:
        payload = json.loads(raw) if isinstance(raw, str) else raw
        tmp = tmp_parent / "_active_semester.json"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        return load_semester_calendar(tmp)
    payload = {
        "semester": row.get("label"),
        "instructional": {
            "first_day_of_school": row.get("instructional_first"),
            "last_instructional_day_before_exams": row.get("instructional_last"),
        },
        "pd_days": json.loads(row.get("pd_days_json") or "[]"),
        "holidays": json.loads(row.get("holidays_json") or "[]"),
        "exam_window": {
            "secondary_exam_days": json.loads(row.get("exam_window_json") or "[]")
        },
    }
    tmp = tmp_parent / "_active_semester.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    return load_semester_calendar(tmp)


def slots_from_class(days_stored: str, time_label: str) -> list[Any]:
    """Live-class slots from Populate Class days/time (75-minute meetings).

    Args:
        days_stored: ``Mon/Wed/Fri`` or ``Tue/Thu/Fri``.
        time_label: Wizard time such as ``2:00pm``.
    """
    from schedule import STORED_DAYS_TO_WEEKDAYS, parse_time_label

    weekdays = STORED_DAYS_TO_WEEKDAYS.get(days_stored)
    if not weekdays:
        raise ValueError(f"Unknown class days: {days_stored}")
    clock = parse_time_label(time_label)
    end_dt = datetime.combine(date.today(), clock) + timedelta(minutes=75)
    end: time = end_dt.time()
    start_s = f"{clock.hour:02d}:{clock.minute:02d}"
    end_s = f"{end.hour:02d}:{end.minute:02d}"
    return [
        LiveSlot(weekday=w, start=start_s, end=end_s) for w in sorted(weekdays)
    ]


def offering_output_dir(
    semester_label: str,
    ontario_code: str,
    *,
    data_dir: Path | None = None,
    instance_relpath: str | None = None,
) -> Path:
    """Per-teacher syllabus folder on the data volume.

    Args:
        semester_label: e.g. ``2026-2027 S1``.
        ontario_code: Course code.
        data_dir: LMS data volume (``school.data_dir``).
        instance_relpath: ``course_offerings.instance_relpath``.
    """
    if data_dir is not None and instance_relpath:
        path = Path(data_dir) / str(instance_relpath) / "syllabus"
        path.mkdir(parents=True, exist_ok=True)
        return path
    slug = semester_label.replace(" ", "-")
    if data_dir is not None:
        path = Path(data_dir) / "syllabus" / slug / ontario_code.upper()
        path.mkdir(parents=True, exist_ok=True)
        return path
    path = SYLLABUS_DATA_DIR / slug / ontario_code.upper()
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_editor_modules(imscc_path: Path | None) -> list[Any]:
    """Parse IMSCC modules for the editor, or return an empty list.

    Args:
        imscc_path: Cartridge file, or None.
    """
    if imscc_path is None or not imscc_path.is_file():
        return []
    return load_modules_from_imscc_file(imscc_path)


def editor_modules_from_outline(raw_modules: list[dict[str, Any]]) -> list[Any]:
    """Build editor modules from stored component outlines.

    Lets the syllabus editor work off the database instead of re-reading the
    cartridge, so a course needs no ``.imscc`` on disk after ingest.

    Args:
        raw_modules: Modules in ``{"title", "identifier", "items"}`` shape.
    """
    if not raw_modules:
        return []
    return modules_from_uploaded_imscc(raw_modules)


def build_editor_page(
    *,
    course: str,
    calendar: Any,
    slots: list[Any],
    modules: list[Any],
    save_url: str,
) -> str:
    """Render click-to-place HTML and point Save at the Flask route.

    Args:
        course: Ontario course code.
        calendar: Locked IT semester calendar.
        slots: Live meetings from the class.
        modules: IMSCC modules (may be empty).
        save_url: POST target for placements JSON.
    """
    included = {module.number: list(module.lessons) for module in modules}
    empty_rows = build_table_rows_from_placements(
        calendar, slots, {}, blank_calendar=True
    )
    page = render_editor_html(
        empty_rows,
        course=course,
        calendar=calendar,
        live_slots=slots,
        modules=modules,
        included=included,
    )
    return page.replace('fetch("/save"', f'fetch("{save_url}"')


def save_placements(
    *,
    payload: dict[str, Any],
    course: str,
    semester_label: str,
    calendar: Any,
    slots: list[Any],
    modules: list[Any],
    data_dir: Path | None = None,
    instance_relpath: str | None = None,
) -> dict[str, str]:
    """Write CSV, Canvas-RCE-safe HTML, and answers JSON under the instance.

    Does **not** call ``pack_modules()``.

    Args:
        payload: Editor POST JSON.
        course: Course code.
        semester_label: Semester id used as the filename slug.
        calendar: Locked calendar.
        slots: Live-class slots.
        modules: IMSCC modules.
        data_dir: LMS data volume.
        instance_relpath: Offering instance folder.

    Returns:
        Relative paths for csv/html/answers.
    """
    if not modules:
        raise ValueError("No module pack for this course yet")
    pool = set(calendar.instructional_days)
    placements = parse_editor_placements(payload, pool, modules)
    rows = build_table_rows_from_placements(
        calendar, slots, placements, blank_calendar=True
    )
    included = {module.number: list(module.lessons) for module in modules}
    out_dir = offering_output_dir(
        semester_label,
        course,
        data_dir=data_dir,
        instance_relpath=instance_relpath,
    )
    slug = semester_label.replace(" ", "-")
    csv_path, html_path, answers_path = write_calendar_outputs(
        out_dir,
        slug,
        rows,
        course=course,
        calendar=calendar,
        slots=slots,
        warnings=[],
        content=modules,
        included=included,
    )

    def _rel(path: Path) -> str:
        """Repo-relative POSIX path when possible."""
        try:
            return path.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            return str(path)

    return {
        "csv": _rel(csv_path),
        "html": _rel(html_path),
        "answers": _rel(answers_path),
        "html_abs": str(html_path),
    }


def saved_html_path(
    semester_label: str,
    ontario_code: str,
    *,
    data_dir: Path | None = None,
    instance_relpath: str | None = None,
) -> Path | None:
    """Return the saved month-grid HTML if it exists.

    Looks in the instance ``syllabus/`` folder first, then leftover
    ``<data_dir>/syllabus/<slug>/<CODE>/`` and ``lms/data/syllabus/...``.
    Missing HTML is treated as “open editor”, not a migrate failure.
    """
    slug = semester_label.replace(" ", "-")
    name = f"{slug}.html"
    candidates: list[Path] = []
    if data_dir is not None and instance_relpath:
        candidates.append(Path(data_dir) / str(instance_relpath) / "syllabus" / name)
    try:
        from instances import legacy_syllabus_dirs
    except ImportError:
        from lms.instances import legacy_syllabus_dirs
    if data_dir is not None:
        for folder in legacy_syllabus_dirs(semester_label, ontario_code, data_dir):
            candidates.append(folder / name)
    else:
        candidates.append(SYLLABUS_DATA_DIR / slug / ontario_code.upper() / name)
    seen: set[Path] = set()
    for path in candidates:
        try:
            key = path.resolve()
        except OSError:
            key = path
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            return path
    return None

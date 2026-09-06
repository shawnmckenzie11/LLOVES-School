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

# Ontario Ministry split: 15% Att & Participation, 65% Term, 20% Exam.
DEFAULT_GRADE_WEIGHTS: dict[str, float] = {
    "participation": 15.0,
    "term": 65.0,
    "exam": 20.0,
}

# Pre-Ministry scaffold; rewrite stored rows that still match this exactly.
LEGACY_DEFAULT_GRADE_WEIGHTS: dict[str, float] = {
    "participation": 15.0,
    "term": 60.0,
    "exam": 25.0,
}

GRADE_CATEGORIES = ("participation", "term", "exam")
GRADE_CATEGORY_LABELS: dict[str, str] = {
    "participation": "Att & Participation",
    "term": "Term",
    "exam": "Exam",
}

# Math courses: 8 content modules, even split of school days after intro
# and before the last instructional week (review).
MATH_MODULE_COUNT = 8
PORTFOLIO_MIN_SESSIONS = 3
PORTFOLIO_MIN_R1 = 10.0
PORTFOLIO_MIN_R3 = 10.0

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


def assert_weights_sum_100(weights: dict[str, float]) -> None:
    """Raise when category percents do not add to 100.

    Args:
        weights: Normalized participation / term / exam map.

    Raises:
        ValueError: When the three percents are not 100±0.05.
    """
    total = sum(float(weights[key]) for key in GRADE_CATEGORIES)
    if abs(total - 100.0) > 0.05:
        raise ValueError(
            f"Term, Exam, and Att & Participation must add to 100% (now {total:g}%)."
        )


def default_module_portfolio_rules() -> dict[str, Any]:
    """Return Module 1 welcome-page portfolio thresholds."""
    return {
        "min_sessions": int(PORTFOLIO_MIN_SESSIONS),
        "min_r1": float(PORTFOLIO_MIN_R1),
        "min_r3": float(PORTFOLIO_MIN_R3),
        "require_reflections": True,
    }


def normalize_module_portfolio_rules(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Coerce one module's portfolio thresholds; fill missing from defaults.

    Args:
        raw: Partial rule map.

    Returns:
        ``min_sessions``, ``min_r1``, ``min_r3``, ``require_reflections``.
    """
    out = default_module_portfolio_rules()
    if not raw:
        return out
    if "min_sessions" in raw:
        try:
            sessions = int(raw["min_sessions"])
        except (TypeError, ValueError):
            sessions = out["min_sessions"]
        if sessions >= 0:
            out["min_sessions"] = sessions
    for key in ("min_r1", "min_r3"):
        if key not in raw:
            continue
        try:
            val = float(raw[key])
        except (TypeError, ValueError):
            continue
        if val >= 0:
            out[key] = val
    if "require_reflections" in raw:
        flag = raw["require_reflections"]
        if isinstance(flag, str):
            out["require_reflections"] = flag.strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
        else:
            out["require_reflections"] = bool(flag)
    return out


def gradebook_overview_text(
    *,
    weights: dict[str, float],
    window: dict[str, Any] | None,
    rules: dict[str, Any],
) -> str:
    """Staff-facing paragraph of the current course grading scheme.

    Args:
        weights: Category percents.
        window: Module 1 even-split window, if known.
        rules: Module 1 portfolio thresholds.

    Returns:
        Plain-language overview for the Grades tab.
    """
    term = weights.get("term", 65)
    exam = weights.get("exam", 20)
    ap = weights.get("participation", 15)
    start = (window or {}).get("start") or ""
    end = (window or {}).get("end") or ""
    if start and end:
        span = f"Module 1 runs {start} through {end}"
    else:
        span = "Module 1 uses the even two-week content window"
    reflections = (
        "answer every reflection after group activities"
        if rules.get("require_reflections", True)
        else "reflections are not required"
    )
    sessions = int(rules.get("min_sessions") or PORTFOLIO_MIN_SESSIONS)
    r1 = rules.get("min_r1", PORTFOLIO_MIN_R1)
    r3 = rules.get("min_r3", PORTFOLIO_MIN_R3)
    r1_s = str(int(r1)) if float(r1) == int(r1) else str(r1)
    r3_s = str(int(r3)) if float(r3) == int(r3) else str(r3)
    return (
        f"The course mark is {term:g}% Term (tests and module portfolios), "
        f"{exam:g}% Exam, and {ap:g}% Attendance & Participation. "
        f"{span}. A student earns 100% on the Module 1 portfolio — and skips "
        f"the conference — when they attend {sessions}+ live classes or Friday "
        f"open offices, {reflections}, and accumulate {r1_s}+ Open Question "
        f"(Round 1) points and {r3_s}+ Formative (Round 3) points. The Module 1 "
        f"test is a placeholder until you enter it. Missing the portfolio rules "
        f"does not write a zero; the conference remains required."
    )


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


def weights_match(stored: dict[str, float], expected: dict[str, float]) -> bool:
    """True when every category percent matches ``expected``.

    Args:
        stored: Persisted category → percent map.
        expected: Comparison map (defaults or legacy defaults).

    Returns:
        Whether all three category values agree.
    """
    return all(
        abs(float(stored.get(key, -1)) - float(expected[key])) < 0.001
        for key in GRADE_CATEGORIES
    )


def review_flex_start(calendar: Any) -> date | None:
    """Monday of the last instructional week before the exam window.

    Exam week leftover instructional days (e.g. Mon Jan 25) stay review/flex
    with that week. Used so content modules stop before review.

    Args:
        calendar: ``SemesterCalendar`` from the syllabus packer.

    Returns:
        Review-week Monday, or None when exam days are missing.
    """
    exam_days = list(getattr(calendar, "exam_days", None) or [])
    if not exam_days:
        return None
    exam_start = min(exam_days)
    exam_week_monday = exam_start - timedelta(days=exam_start.weekday())
    return exam_week_monday - timedelta(days=7)


def math_content_days(semester_json: Path | None = None) -> list[date]:
    """Instructional days after the 2 intro days and before review week.

    Args:
        semester_json: Optional override path.

    Returns:
        Sorted school days that even-split across math modules.
    """
    cal = load_semester_calendar(semester_json)
    instructional = list(cal.instructional_days)
    intro = set(instructional[:2])
    review_start = review_flex_start(cal)
    out: list[date] = []
    for day in instructional:
        if day in intro:
            continue
        if review_start is not None and day >= review_start:
            continue
        out.append(day)
    return out


def even_module_windows(
    days: list[date] | None = None,
    *,
    n_modules: int = MATH_MODULE_COUNT,
    semester_json: Path | None = None,
) -> list[dict[str, Any]]:
    """Split content days evenly across math modules (~2 weeks each).

    Extra leftover days go to the first modules, matching
    ``leftover_day_shares`` in the syllabus packer.

    Args:
        days: Optional precomputed content-span days.
        n_modules: Module count (8 for MCF3M and other math courses).
        semester_json: Optional calendar override when ``days`` is omitted.

    Returns:
        One dict per module: number, start, end, days (ISO strings + dates).
    """
    span = list(days) if days is not None else math_content_days(semester_json)
    if n_modules <= 0:
        return []
    leftover = max(0, len(span))
    base, extra = divmod(leftover, n_modules)
    shares = [base + (1 if i < extra else 0) for i in range(n_modules)]
    windows: list[dict[str, Any]] = []
    cursor = 0
    for index, share in enumerate(shares):
        chunk = span[cursor : cursor + share]
        cursor += share
        start = chunk[0] if chunk else None
        end = chunk[-1] if chunk else None
        windows.append(
            {
                "number": index + 1,
                "start": start.isoformat() if start else None,
                "end": end.isoformat() if end else None,
                "days": [d.isoformat() for d in chunk],
                "day_count": len(chunk),
            }
        )
    return windows


def module_window_for(
    module_number: int,
    *,
    n_modules: int = MATH_MODULE_COUNT,
    semester_json: Path | None = None,
) -> dict[str, Any] | None:
    """Return one even-split module window.

    Args:
        module_number: 1-based module index.
        n_modules: Course module count.
        semester_json: Optional calendar override.

    Returns:
        Window dict, or None when the number is out of range.
    """
    if module_number < 1 or module_number > n_modules:
        return None
    windows = even_module_windows(n_modules=n_modules, semester_json=semester_json)
    if module_number > len(windows):
        return None
    return windows[module_number - 1]


def _session_date_map(sessions: list[dict[str, Any]]) -> dict[int, date]:
    """Map non-template session ids to meeting dates.

    Args:
        sessions: Class session rows.

    Returns:
        ``session_id`` → calendar date.
    """
    out: dict[int, date] = {}
    for sess in sessions:
        if str(sess.get("status") or "") == "template":
            continue
        meeting = session_meeting_date(sess.get("starts_at"))
        if meeting is None:
            continue
        out[int(sess["id"])] = meeting
    return out


def evaluate_module_portfolio(
    *,
    student_id: int,
    window: dict[str, Any],
    days_label: str | None,
    instructional: set[date],
    sessions: list[dict[str, Any]],
    score_rows: list[dict[str, Any]],
    reflections_complete: bool,
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score one student's module portfolio from A&P tracker evidence.

    100% uses the class grading scheme (defaults: 3+ live or Friday office
    sessions, all reflections, 10+ Round 1 Open Question points, and 10+
    Round 3 Formative points).

    Args:
        student_id: Game-show student primary key.
        window: Even-split module window (``days`` as ISO strings).
        days_label: Course live-day preset (``M/W/F`` or ``T/Th/F``).
        instructional: School-day set.
        sessions: Class sessions.
        score_rows: ``session_scores`` with present/late and round points.
        reflections_complete: Staff-logged flag for post-activity reflections.
        rules: Optional stored thresholds; defaults to welcome-page values.

    Returns:
        Criteria breakdown and ``score`` (100 or None).
    """
    scheme = normalize_module_portfolio_rules(rules)
    min_sessions = int(scheme["min_sessions"])
    min_r1 = float(scheme["min_r1"])
    min_r3 = float(scheme["min_r3"])
    require_reflections = bool(scheme["require_reflections"])
    window_days = {date.fromisoformat(iso) for iso in (window.get("days") or [])}
    session_dates = _session_date_map(sessions)
    present_days: set[date] = set()
    points_r1 = 0.0
    points_r3 = 0.0
    sid = int(student_id)
    for row in score_rows:
        if int(row.get("student_id") or 0) != sid:
            continue
        meeting = session_dates.get(int(row["session_id"]))
        if meeting is None or meeting not in window_days:
            continue
        if not is_live_class_date(
            meeting, days_label=days_label, instructional=instructional
        ):
            continue
        present = int(row.get("present") or 0) == 1
        late = int(row.get("late") or 0) == 1
        if present or late:
            present_days.add(meeting)
        points_r1 += float(row.get("points_r1") or 0)
        points_r3 += float(row.get("points_r3") or 0)
    sessions_n = len(present_days)
    sessions_met = sessions_n >= min_sessions
    r1_met = points_r1 + 1e-9 >= min_r1
    r3_met = points_r3 + 1e-9 >= min_r3
    reflections_met = True if not require_reflections else bool(reflections_complete)
    earned = sessions_met and r1_met and r3_met and reflections_met
    return {
        "module": int(window.get("number") or 0),
        "sessions": sessions_n,
        "sessions_needed": min_sessions,
        "sessions_met": sessions_met,
        "points_r1": round(points_r1, 1),
        "r1_needed": min_r1,
        "r1_met": r1_met,
        "points_r3": round(points_r3, 1),
        "r3_needed": min_r3,
        "r3_met": r3_met,
        "reflections_complete": bool(reflections_complete),
        "reflections_met": reflections_met,
        "require_reflections": require_reflections,
        "earned_100": earned,
        "score": 100.0 if earned else None,
        "pending_reason": None
        if earned
        else "Conference required until all four criteria are met",
    }


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

    present mark for a day is true when any non-template session on that date
    has ``present=1`` for the student. Late joiners (``late=1``) surface as the
    string ``"L"`` instead of ``True``. Total = count of present school days
    (including late). The grid spans semester start through last instructional
    day and greys holidays / PD.

    Args:
        students: Roster rows with ``id`` and display fields.
        sessions: Class sessions (``id``, ``starts_at``, ``status``).
        score_rows: ``session_scores`` dicts with session_id, student_id, present,
            and optional ``late``.
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

    present_by_student_day: dict[tuple[int, str], bool | str] = {}
    for row in score_rows:
        sid = int(row["student_id"])
        sess_id = int(row["session_id"])
        meeting = session_dates.get(sess_id)
        if meeting is None:
            continue
        key = (sid, meeting.isoformat())
        if int(row.get("present") or 0) == 1:
            if int(row.get("late") or 0) == 1:
                present_by_student_day[key] = "L"
            elif present_by_student_day.get(key) != "L":
                present_by_student_day[key] = True
        else:
            present_by_student_day.setdefault(key, False)

    cells: dict[str, bool | str | None] = {}
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
                if marked is True or marked == "L":
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

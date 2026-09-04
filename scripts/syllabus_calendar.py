#!/usr/bin/env python3
"""Build a day-by-day MCF3M syllabus calendar (CSV + highlighted HTML).

Derives school days from ``frameworks/semester.json``, live-class times from
``courses/<CODE>/schedule.json``, and the legacy IMSCC lesson/assessment list
from ``courses/<CODE>/canvas/inventory.json`` (falls back to reading the
``.imscc`` ZIP via ``CourseSource`` — does not unpack).

This semester’s lesson list is the **legacy 8 content modules**, not the
rebuild 5-theme course plan.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import socket
import sys
import tempfile
import threading
import webbrowser
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import ParseError

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from canvas_inventory import CourseSource, build_resource_index, parse_modules

DEFAULT_SEMESTER_JSON = ROOT / "frameworks/semester.json"
INTRO_DAY_COUNT = 2
EXAM_PREP_DAY_COUNT = 3
CATCHUP_TITLE = "Review"
LIVE_CLASS_ID = "__live_class__"
LIVE_CLASS_TITLE = "Live class"
FRIDAY_WEEKDAY = 4

KIND_ORDER = (
    "exam",
    "test",
    "conference",
    "assignment",
    "intro",
    "review",
    "live",
    "async",
    "nonschool",
)

WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

WEEKDAY_INDEX = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

CONTENT_MODULE_RE = re.compile(r"^Module\s+([1-8])\s*:", re.I)
LESSON_TITLE_RE = re.compile(r"^Lesson\s*\d*", re.I)
SKIP_ASSESSMENT_RE = re.compile(
    r"warm[\s-]?up|honorlock|checkpoint|minds[\s-]?on",
    re.I,
)
SKIP_LESSON_RE = re.compile(
    r"live class notes|learning goals|honorlock",
    re.I,
)
EXTRA_LESSON_RE = re.compile(
    r"putting it all together|mid[\s-]?chapter|hmwk|homework|recording|\breview\b",
    re.I,
)

MODULE_COLORS = {
    "intro": "#e8eef5",
    "m1": "#dbeafe",
    "m2": "#d1fae5",
    "m3": "#fef3c7",
    "m4": "#fce7f3",
    "m5": "#ede9fe",
    "m6": "#ffedd5",
    "m7": "#e0f2fe",
    "m8": "#f3e8ff",
    "review": "#f1f5f9",
    "exam": "#fecaca",
    "nonschool": "#f8fafc",
}


@dataclass
class LiveSlot:
    """One weekly live-class meeting time."""

    weekday: int
    start: str
    end: str

    def label(self) -> str:
        """Return a compact start–end time string."""
        return f"{self.start}–{self.end}"


@dataclass
class NonschoolMark:
    """A weekday that is not instructional (holiday or PD)."""

    date: date
    reason: str


@dataclass
class SemesterCalendar:
    """Derived school-day lists for one semester."""

    semester_id: str
    first_day: date
    last_instructional: date
    instructional_days: list[date]
    exam_days: list[date]
    nonschool: list[NonschoolMark]
    table_weekdays: list[date]
    closed: dict[date, str]


@dataclass
class LessonCandidate:
    """A wiki page that may be scheduled as an async lesson."""

    module_number: int
    identifier: str
    title: str
    extra: bool
    default_include: bool


@dataclass
class AssessmentCandidate:
    """A test, assignment, conference, extra quiz, or course exam."""

    module_number: int | None
    kind: str
    identifier: str | None
    title: str
    default_include: bool
    is_extra: bool = False
    role: str = ""


@dataclass
class ContentModule:
    """One legacy IMSCC content module (M1–M8)."""

    number: int
    title: str
    identifier: str
    lessons: list[LessonCandidate] = field(default_factory=list)
    test: AssessmentCandidate | None = None
    assignment: AssessmentCandidate | None = None
    extras: list[AssessmentCandidate] = field(default_factory=list)


@dataclass
class PlacedAssessment:
    """An assessment the wizard kept, with a due date."""

    module_number: int | None
    kind: str
    identifier: str | None
    title: str
    date: date
    is_extra: bool = False


@dataclass
class DayPlacement:
    """User or packer content for one content-pool day.

    Slack / empty tagged days may have only ``module_number``. Test days
    should not carry a lesson; Review occupies the lesson column.
    """

    module_number: int
    lesson: str = ""
    lesson_id: str | None = None
    review: bool = False
    live: bool = False
    assessment_kind: str | None = None
    assessment_title: str = ""


@dataclass
class TableRow:
    """One syllabus-calendar table row.

    ``Lesson`` and ``Assessment`` are always separate columns. ``date`` stays
    a full calendar date internally; CSV/HTML Date cells show only the day of
    month (e.g. ``2`` for 2 September).
    """

    week: int
    month: str
    date: date
    weekday: str
    module: str
    kind: str
    lesson: str
    assessment: str
    time: str
    color_key: str
    week_changed: bool
    month_changed: bool
    emphasized: bool
    dimmed: bool

    def date_number(self) -> str:
        """Return the day-of-month as a bare number (e.g. ``2``)."""
        return str(self.date.day)


def parse_iso_date(value: str) -> date:
    """Parse a ``YYYY-MM-DD`` string into a date.

    Args:
        value: ISO date string.

    Returns:
        Parsed calendar date.

    Raises:
        ValueError: If ``value`` is not a valid ISO date.
    """
    return date.fromisoformat(value.strip())


def load_json(path: Path) -> dict[str, Any]:
    """Load a UTF-8 JSON object from disk.

    Args:
        path: File to read.

    Returns:
        Parsed JSON object.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    return json.loads(path.read_text(encoding="utf-8"))


def semester_slug(semester_id: str) -> str:
    """Turn ``2026-2027 S1`` into ``2026-2027-S1``.

    Args:
        semester_id: Semester label from ``semester.json``.

    Returns:
        Filesystem-safe slug.
    """
    return re.sub(r"\s+", "-", semester_id.strip())


def weekday_name(day: date) -> str:
    """Return the English weekday name for ``day``.

    Args:
        day: Calendar date.

    Returns:
        Weekday name (Monday–Sunday).
    """
    return WEEKDAY_NAMES[day.weekday()]


def month_name(day: date) -> str:
    """Return the English month name for ``day``.

    Args:
        day: Calendar date.

    Returns:
        Full month name.
    """
    return day.strftime("%B")


def week_index(day: date, first_day: date) -> int:
    """1-based instructional week number (Monday-aligned from first school day).

    Args:
        day: Date to number.
        first_day: First instructional day of the semester.

    Returns:
        Week number starting at 1 for the week containing ``first_day``.
    """
    first_monday = first_day - timedelta(days=first_day.weekday())
    this_monday = day - timedelta(days=day.weekday())
    return 1 + (this_monday - first_monday).days // 7


def html_unescape_title(title: str | None) -> str:
    """Normalize an IMSCC/inventory title for display.

    Args:
        title: Raw title, possibly HTML-escaped.

    Returns:
        Unescaped stripped title, or empty string.
    """
    if not title:
        return ""
    return html.unescape(title).strip()


def parse_live_slots(schedule: dict[str, Any]) -> list[LiveSlot]:
    """Read live-class weekdays and times from ``schedule.json``.

    Args:
        schedule: Parsed course schedule object.

    Returns:
        Live slots (typically Monday and Wednesday).

    Raises:
        ValueError: If no recognizable live-class entries exist.
    """
    slots: list[LiveSlot] = []
    for raw in schedule.get("live_classes") or []:
        name = str(raw.get("weekday") or "").strip().lower()
        if name not in WEEKDAY_INDEX:
            continue
        start = str(raw.get("start") or "14:00").strip()
        end = str(raw.get("end") or "15:15").strip()
        slots.append(LiveSlot(weekday=WEEKDAY_INDEX[name], start=start, end=end))
    if not slots:
        raise ValueError("schedule.json has no live_classes entries")
    return slots


def live_time_for(day: date, slots: list[LiveSlot]) -> str:
    """Return the live meeting time label for ``day``, or empty.

    Args:
        day: Calendar date.
        slots: Weekly live-class slots.

    Returns:
        ``HH:MM–HH:MM`` when ``day`` is a live weekday, else ``""``.
    """
    for slot in slots:
        if day.weekday() == slot.weekday:
            return slot.label()
    return ""


def is_live_weekday(day: date, slots: list[LiveSlot]) -> bool:
    """Return True if ``day`` falls on a scheduled live-class weekday.

    Args:
        day: Calendar date.
        slots: Weekly live-class slots.

    Returns:
        Whether the weekday matches a live slot (ignores holidays).
    """
    return any(day.weekday() == slot.weekday for slot in slots)


def load_semester_calendar(path: Path) -> SemesterCalendar:
    """Build instructional, exam, and table day lists from ``semester.json``.

    School days are weekdays from the first instructional day through the last
    instructional day, minus holidays and PD days. Exam-window days (including
    a secondary PD that is also marked E) stay exam days, not ``nonschool``.

    Args:
        path: Path to ``frameworks/semester.json``.

    Returns:
        Derived calendar lists.

    Raises:
        FileNotFoundError: If ``path`` is missing.
        KeyError: If required semester fields are absent.
    """
    payload = load_json(path)
    instructional = payload["instructional"]
    first_day = parse_iso_date(instructional["first_day_of_school"])
    last_instructional = parse_iso_date(
        instructional["last_instructional_day_before_exams"]
    )
    holidays = {
        parse_iso_date(item["date"]): str(item.get("name") or "Holiday")
        for item in payload.get("holidays") or []
    }
    pd_days = {
        parse_iso_date(item["date"]): "PD day"
        for item in payload.get("pd_days") or []
    }
    exam_days = [
        parse_iso_date(value)
        for value in (payload.get("exam_window") or {}).get("secondary_exam_days")
        or []
    ]
    exam_set = set(exam_days)
    blocked = set(holidays) | set(pd_days)

    instructional_days: list[date] = []
    cursor = first_day
    while cursor <= last_instructional:
        if cursor.weekday() < 5 and cursor not in blocked:
            instructional_days.append(cursor)
        cursor += timedelta(days=1)

    last_table = max([last_instructional, *exam_days])
    table_weekdays: list[date] = []
    nonschool: list[NonschoolMark] = []
    cursor = first_day
    while cursor <= last_table:
        if cursor.weekday() < 5:
            table_weekdays.append(cursor)
            if cursor not in exam_set and cursor in blocked:
                reason = holidays.get(cursor) or pd_days.get(cursor) or "No school"
                nonschool.append(NonschoolMark(date=cursor, reason=reason))
        cursor += timedelta(days=1)

    return SemesterCalendar(
        semester_id=str(payload.get("semester") or path.stem),
        first_day=first_day,
        last_instructional=last_instructional,
        instructional_days=instructional_days,
        exam_days=exam_days,
        nonschool=nonschool,
        table_weekdays=table_weekdays,
        closed={**holidays, **pd_days},
    )


def intro_days(calendar: SemesterCalendar) -> list[date]:
    """Return the first two instructional days (intro / course overview).

    Args:
        calendar: Derived semester calendar.

    Returns:
        Intro dates (may be shorter than two if the calendar is tiny).
    """
    return calendar.instructional_days[:INTRO_DAY_COUNT]


def review_days(calendar: SemesterCalendar) -> list[date]:
    """Return the last three instructional days before the first exam-window day.

    These are course-level exam-prep days, labeled Review. They are not a
    content module and do not carry a final-exam assessment.

    Args:
        calendar: Derived semester calendar.

    Returns:
        Exam-prep instructional dates, chronological (up to three).
    """
    if calendar.exam_days:
        exam_start = min(calendar.exam_days)
        before_exam = [d for d in calendar.instructional_days if d < exam_start]
    else:
        before_exam = list(calendar.instructional_days)
    if not before_exam:
        return []
    return before_exam[-EXAM_PREP_DAY_COUNT:]


def content_days(calendar: SemesterCalendar) -> list[date]:
    """Instructional days after intro and before the three exam-prep Review days.

    Args:
        calendar: Derived semester calendar.

    Returns:
        Content-span dates, chronological.
    """
    intro = set(intro_days(calendar))
    review = set(review_days(calendar))
    return [d for d in calendar.instructional_days if d not in intro and d not in review]


def course_dir_for(course: str) -> Path:
    """Return ``courses/<CODE>`` under the repo root.

    Args:
        course: Course code (e.g. ``MCF3M``).

    Returns:
        Course directory path.
    """
    return ROOT / "courses" / course


def find_imscc(course_dir: Path) -> Path | None:
    """Locate the course ``.imscc`` archive without unpacking it.

    Args:
        course_dir: ``courses/<CODE>`` directory.

    Returns:
        Archive path, or None if none exists.
    """
    sources = course_dir / "sources"
    if not sources.is_dir():
        return None
    matches = sorted(sources.glob("*.imscc"))
    return matches[0] if matches else None


def load_canvas_modules(course_dir: Path) -> list[dict[str, Any]]:
    """Load IMSCC module trees from inventory.json, or the ZIP if inventory is missing.

    Does not unpack the cartridge. Inventory is preferred so agents can run
    without the large archive open.

    Args:
        course_dir: ``courses/<CODE>`` directory.

    Returns:
        Module dicts as in ``inventory.json``.

    Raises:
        FileNotFoundError: If neither inventory nor IMSCC exists.
    """
    inv_path = course_dir / "canvas" / "inventory.json"
    if inv_path.is_file():
        payload = load_json(inv_path)
        return list(payload.get("modules") or [])
    imscc = find_imscc(course_dir)
    if imscc is None:
        raise FileNotFoundError(
            f"No canvas/inventory.json or sources/*.imscc under {course_dir}"
        )
    print(
        f"inventory.json missing; reading {imscc} directly (not unpacking).",
        file=sys.stderr,
    )
    with CourseSource(root=None, archive=imscc) as source:
        resources = build_resource_index(source)
        return parse_modules(source, resources)


def classify_wiki_lesson(title: str, module_number: int) -> str | None:
    """Classify a wiki page as ``core``, ``extra``, or skip (None).

    Core pages are Lesson-titled items, plus Module 7’s untitled instructional
    pages. Extras (mid-chapter reviews, “Putting it all Together”, homework
    recordings, end-of-module review pages) default to excluded.

    Args:
        title: Item title.
        module_number: Legacy module number 1–8.

    Returns:
        ``"core"``, ``"extra"``, or None.
    """
    text = html_unescape_title(title)
    if not text or SKIP_LESSON_RE.search(text):
        return None
    if LESSON_TITLE_RE.match(text):
        return "core"
    if module_number == 7 and not EXTRA_LESSON_RE.search(text):
        return "core"
    if EXTRA_LESSON_RE.search(text):
        return "extra"
    return None


def is_skipped_assessment(title: str) -> bool:
    """Return True for warm-ups, Honorlock setup, Checkpoint, and minds-on quizzes.

    Args:
        title: Assessment title.

    Returns:
        Whether the item should be ignored as a syllabus assessment.
    """
    return bool(SKIP_ASSESSMENT_RE.search(title or ""))


def is_end_of_module_test(title: str, content_type: str) -> bool:
    """Return True if this module item is the end-of-module test.

    Args:
        title: Item title.
        content_type: Canvas ``content_type``.

    Returns:
        Whether this is a kept-by-default module test.
    """
    if content_type != "Quizzes::Quiz" or is_skipped_assessment(title):
        return False
    lower = title.lower()
    if "assignment" in lower:
        return False
    return "test" in lower


def is_end_of_module_assignment(title: str, content_type: str) -> bool:
    """Return True if this item is the module portfolio / assignment.

    Module 8 stores its assignment as a quiz titled “Assignment”.

    Args:
        title: Item title.
        content_type: Canvas ``content_type``.

    Returns:
        Whether this is a kept-by-default module assignment.
    """
    if is_skipped_assessment(title):
        return False
    if content_type == "Assignment":
        return True
    if content_type == "Quizzes::Quiz" and "assignment" in title.lower():
        return True
    return False


def is_extra_quiz(title: str, content_type: str) -> bool:
    """Return True for mid-module quizzes (off by default).

    Args:
        title: Item title.
        content_type: Canvas ``content_type``.

    Returns:
        Whether this is an optional extra quiz.
    """
    if content_type != "Quizzes::Quiz" or is_skipped_assessment(title):
        return False
    lower = title.lower()
    if "test" in lower or "assignment" in lower or "final exam" in lower:
        return False
    return True


def is_course_exam_item(title: str, module_title: str, content_type: str) -> bool:
    """Return True if this is the course final exam quiz.

    Args:
        title: Item title.
        module_title: Parent Canvas module title.
        content_type: Canvas ``content_type``.

    Returns:
        Whether this is the course exam candidate.
    """
    if content_type != "Quizzes::Quiz" or is_skipped_assessment(title):
        return False
    if re.search(r"final\s+exam", title, re.I):
        return True
    return module_title.strip().upper() == "FINAL EXAM"


def detect_content_modules(modules: list[dict[str, Any]]) -> list[ContentModule]:
    """Extract M1–M8 lessons and assessments from IMSCC module items.

    Args:
        modules: Inventory module list.

    Returns:
        Content modules in numeric order.
    """
    found: dict[int, ContentModule] = {}
    for raw in modules:
        title = html_unescape_title(raw.get("title"))
        match = CONTENT_MODULE_RE.match(title)
        if not match:
            continue
        number = int(match.group(1))
        module = ContentModule(
            number=number,
            title=title,
            identifier=str(raw.get("identifier") or f"m{number}"),
        )
        for item in raw.get("items") or []:
            item_title = html_unescape_title(item.get("title"))
            ctype = str(item.get("content_type") or "")
            ident = str(item.get("identifier") or item_title)
            if ctype == "WikiPage":
                kind = classify_wiki_lesson(item_title, number)
                if kind == "core":
                    module.lessons.append(
                        LessonCandidate(
                            module_number=number,
                            identifier=ident,
                            title=item_title,
                            extra=False,
                            default_include=True,
                        )
                    )
                elif kind == "extra":
                    module.lessons.append(
                        LessonCandidate(
                            module_number=number,
                            identifier=ident,
                            title=item_title,
                            extra=True,
                            default_include=False,
                        )
                    )
                continue
            if is_end_of_module_test(item_title, ctype) and module.test is None:
                module.test = AssessmentCandidate(
                    module_number=number,
                    kind="test",
                    identifier=ident,
                    title=item_title,
                    default_include=True,
                    role="test",
                )
                continue
            if (
                is_end_of_module_assignment(item_title, ctype)
                and module.assignment is None
            ):
                module.assignment = AssessmentCandidate(
                    module_number=number,
                    kind="assignment",
                    identifier=ident,
                    title=item_title,
                    default_include=True,
                    role="assignment",
                )
                continue
            if is_extra_quiz(item_title, ctype):
                module.extras.append(
                    AssessmentCandidate(
                        module_number=number,
                        kind="test",
                        identifier=ident,
                        title=item_title,
                        default_include=False,
                        is_extra=True,
                        role="extra",
                    )
                )
        found[number] = module
    return [found[n] for n in sorted(found)]


def modules_from_uploaded_imscc(raw_modules: list[dict[str, Any]]) -> list[ContentModule]:
    """Turn every IMSCC module into an editor queue (all titled items).

    Unlike ``detect_content_modules``, this does not filter to M1–M8 lesson
    pages. Order follows the cartridge. Each module is numbered 1…n.

    Args:
        raw_modules: ``parse_modules`` output.

    Returns:
        Content modules for the editor dropdown and remaining-items list.
    """
    result: list[ContentModule] = []
    for index, raw in enumerate(raw_modules, start=1):
        title = html_unescape_title(raw.get("title")) or f"Module {index}"
        module = ContentModule(
            number=index,
            title=title,
            identifier=str(raw.get("identifier") or f"m{index}"),
        )
        for item in raw.get("items") or []:
            item_title = html_unescape_title(item.get("title"))
            if not item_title:
                continue
            ident = str(item.get("identifier") or item_title)
            ctype = str(item.get("content_type") or "")
            module.lessons.append(
                LessonCandidate(
                    module_number=index,
                    identifier=ident,
                    title=item_title,
                    extra=False,
                    default_include=True,
                )
            )
            if is_end_of_module_test(item_title, ctype) and module.test is None:
                module.test = AssessmentCandidate(
                    module_number=index,
                    kind="test",
                    identifier=ident,
                    title=item_title,
                    default_include=True,
                    role="test",
                )
        result.append(module)
    return result


def load_modules_from_imscc_file(path: Path) -> list[ContentModule]:
    """Parse a Canvas ``.imscc`` ZIP into editor modules without unpacking.

    Args:
        path: Path to the uploaded cartridge.

    Returns:
        All titled module items, numbered in cartridge order.

    Raises:
        FileNotFoundError: If ``path`` is missing.
        ValueError: If the file is not a readable IMSCC.
    """
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        with CourseSource(root=None, archive=path) as source:
            resources = build_resource_index(source)
            raw = parse_modules(source, resources)
    except (OSError, zipfile.BadZipFile, KeyError, ParseError) as exc:
        raise ValueError(f"Could not read IMSCC: {exc}") from exc
    if not raw:
        raise ValueError("No Canvas modules found in that .imscc file")
    return modules_from_uploaded_imscc(raw)


def detect_course_exam(modules: list[dict[str, Any]]) -> AssessmentCandidate | None:
    """Find the course final exam quiz in non-content modules.

    Args:
        modules: Full inventory module list.

    Returns:
        Exam candidate, or None if not present.
    """
    for raw in modules:
        module_title = html_unescape_title(raw.get("title"))
        for item in raw.get("items") or []:
            title = html_unescape_title(item.get("title"))
            ctype = str(item.get("content_type") or "")
            if is_course_exam_item(title, module_title, ctype):
                return AssessmentCandidate(
                    module_number=None,
                    kind="exam",
                    identifier=str(item.get("identifier") or title),
                    title="MCF3M Final Exam",
                    default_include=True,
                    role="exam",
                )
    return None


@dataclass
class ModuleSequence:
    """Consecutive lessons, Review, conference and test, then leftover empty days.

    Slack sits *after* conference and test and stays tagged as this module.
    Live-class times may still show on slack days; there is no lesson.
    """

    lesson_days: list[date]
    review_day: date | None
    conference_day: date | None
    test_day: date | None
    slack_days: list[date] = field(default_factory=list)


def leftover_day_shares(n_modules: int, leftover: int) -> list[int]:
    """Split leftover empty days across modules.

    Each module gets ``leftover // n_modules`` trailing empty days, plus one
    extra to the first ``leftover % n_modules`` modules.

    Args:
        n_modules: Number of content modules.
        leftover: Content-pool days remaining after tight packing. Negative
            values are treated as zero.

    Returns:
        Per-module slack counts summing to ``max(leftover, 0)``.
    """
    if n_modules <= 0:
        return []
    leftover = max(0, leftover)
    base, extra = divmod(leftover, n_modules)
    return [base + (1 if i < extra else 0) for i in range(n_modules)]


def take_pool_days(
    pool: list[date],
    start: int,
    count: int,
) -> tuple[list[date], int]:
    """Take up to ``count`` days from ``pool`` beginning at ``start``.

    Args:
        pool: Ordered content-span dates.
        start: Index to begin (inclusive).
        count: Requested length.

    Returns:
        ``(days, next_index)`` where ``next_index`` is ``start + len(days)``.
    """
    if count <= 0 or start >= len(pool):
        return [], start
    end = min(len(pool), start + count)
    return pool[start:end], end


def conference_first_on(day: date, slots: list[LiveSlot]) -> bool:
    """Return True if conference lands on ``day`` (test the next school day).

    Live-class weekdays (Mon/Wed for MCF3M) and Friday take conference first.
    Async weekdays (Tue/Thu) take the test first.

    Args:
        day: First school day after the module Review.
        slots: Weekly live-class slots.

    Returns:
        True when conference is that day and the test is the next school day.
    """
    if is_live_weekday(day, slots):
        return True
    return day.weekday() == FRIDAY_WEEKDAY


def assign_conference_and_test(
    close_days: list[date],
    slots: list[LiveSlot],
) -> tuple[date | None, date | None]:
    """Order conference and test on the school days immediately after Review.

    Args:
        close_days: Up to two school days after Review.
        slots: Live-class slots (weekday rule).

    Returns:
        ``(conference_day, test_day)``. Either may be None if days are missing.
    """
    if not close_days:
        return None, None
    first = close_days[0]
    second = close_days[1] if len(close_days) > 1 else None
    if conference_first_on(first, slots):
        return first, second
    return second, first


def closeout_assessments(
    module: ContentModule,
    conference_day: date | None,
    test_day: date | None,
) -> list[PlacedAssessment]:
    """Build the auto Test and Conference for one module.

    Test titles come from the IMSCC when present, else ``Module N Test``.
    Conferences are always ``Module N Conference``. No portfolios, extra
    quizzes, or course exam.

    Args:
        module: Content module (IMSCC test title when present).
        conference_day: Conference date, or None if the pool ran out.
        test_day: Test date, or None if the pool ran out.

    Returns:
        Zero to two placed assessments.
    """
    placed: list[PlacedAssessment] = []
    if test_day is not None:
        title = module_test_title(module)
        ident = module.test.identifier if module.test else None
        placed.append(
            PlacedAssessment(
                module_number=module.number,
                kind="test",
                identifier=ident,
                title=title,
                date=test_day,
            )
        )
    if conference_day is not None:
        placed.append(
            PlacedAssessment(
                module_number=module.number,
                kind="conference",
                identifier=None,
                title=f"Module {module.number} Conference",
                date=conference_day,
            )
        )
    return placed


def pack_modules(
    modules: list[ContentModule],
    included_lessons: dict[int, list[LessonCandidate]],
    pool: list[date],
    slots: list[LiveSlot],
    warnings: list[str],
) -> tuple[dict[int, list[date]], dict[int, ModuleSequence], list[PlacedAssessment]]:
    """Walk the content pool: consecutive lessons, Review, conference/test, slack.

    Tight length of module *i* is ``n_lessons + 1`` (Review) ``+ 2``
    (conference and test). Leftover pool days are split as trailing empty
    days after each module's conference and test: each module gets
    ``leftover // n_modules``, plus one extra to the first
    ``leftover % n_modules`` modules.

    Module 1 lesson 1 is the first content day. Lessons occupy consecutive
    school days, including live-class days. The next school day is Review.
    The following school day is conference (if live or Friday) or test (if
    async Tue/Thu), with the other closeout on the next school day. Then
    that module's leftover empty days. The next module starts the next
    school day.

    Args:
        modules: Legacy content modules in order.
        included_lessons: Wizard-kept lessons per module number.
        pool: Instructional days after intro and before exam-prep Review.
        slots: Live-class slots (weekday rule).
        warnings: Collect pool-shortage notes.

    Returns:
        Module windows, sequences, and auto-placed tests/conferences.
    """
    n_modules = len(modules)
    tight = [
        len(included_lessons.get(module.number) or []) + 3 for module in modules
    ]
    leftover = len(pool) - sum(tight)
    if leftover < 0:
        warnings.append(
            f"Content pool has {len(pool)} days but tight packing needs "
            f"{sum(tight)}; later modules may be truncated"
        )
    shares = leftover_day_shares(n_modules, leftover)
    windows: dict[int, list[date]] = {}
    sequences: dict[int, ModuleSequence] = {}
    placed: list[PlacedAssessment] = []
    cursor = 0
    for index, module in enumerate(modules):
        lessons = included_lessons.get(module.number) or []
        n_lessons = len(lessons)
        lesson_days, cursor = take_pool_days(pool, cursor, n_lessons)
        if len(lesson_days) < n_lessons:
            warnings.append(
                f"M{module.number}: only {len(lesson_days)} day(s) for "
                f"{n_lessons} lesson(s)"
            )
        review_taken, cursor = take_pool_days(pool, cursor, 1)
        review_day = review_taken[0] if review_taken else None
        if review_day is None:
            warnings.append(f"M{module.number}: no day left for Review")
        close_days, cursor = take_pool_days(pool, cursor, 2)
        if len(close_days) < 2:
            warnings.append(
                f"M{module.number}: only {len(close_days)} day(s) for "
                "conference and test"
            )
        conference_day, test_day = assign_conference_and_test(close_days, slots)
        slack_days, cursor = take_pool_days(pool, cursor, shares[index])
        window = (
            list(lesson_days)
            + ([review_day] if review_day else [])
            + list(close_days)
            + list(slack_days)
        )
        windows[module.number] = window
        sequences[module.number] = ModuleSequence(
            lesson_days=list(lesson_days),
            review_day=review_day,
            conference_day=conference_day,
            test_day=test_day,
            slack_days=list(slack_days),
        )
        placed.extend(closeout_assessments(module, conference_day, test_day))
    if cursor < len(pool) and modules:
        extra = pool[cursor:]
        warnings.append(
            f"{len(extra)} content-pool day(s) unused after packing; "
            "appended to the last module as slack"
        )
        last = modules[-1].number
        sequences[last].slack_days.extend(extra)
        windows[last].extend(extra)
    return windows, sequences, placed


def test_dates(placed: list[PlacedAssessment]) -> set[date]:
    """Return dates that carry a module or extra **test** (not assignments).

    Args:
        placed: Assessments the wizard kept.

    Returns:
        Test dates. Async lessons must never land on these days.
    """
    return {item.date for item in placed if item.kind == "test"}


def distribute_lessons(
    lessons: list[LessonCandidate],
    async_days: list[date],
) -> tuple[dict[date, list[LessonCandidate]], list[date]]:
    """Place IMSCC-order lessons onto async days.

    One lesson per day from the start. Extra lessons stack on later async days.
    Unused later days are catch-up (returned separately).

    Args:
        lessons: Included lessons in IMSCC order.
        async_days: Async school days in the module window.

    Returns:
        Mapping of date → lessons, and leftover catch-up dates.
    """
    placed: dict[date, list[LessonCandidate]] = {day: [] for day in async_days}
    if not async_days:
        return placed, []
    if not lessons:
        return placed, list(async_days)
    if len(lessons) <= len(async_days):
        for day, lesson in zip(async_days, lessons):
            placed[day].append(lesson)
        return placed, async_days[len(lessons) :]
    base, remainder = divmod(len(lessons), len(async_days))
    counts = [base] * len(async_days)
    for offset in range(remainder):
        counts[-(offset + 1)] += 1
    index = 0
    for day, count in zip(async_days, counts):
        placed[day] = lessons[index : index + count]
        index += count
    return placed, []


def prompt(text: str, default: str, accept_defaults: bool) -> str:
    """Read a line from stdin, or return ``default`` when ``accept_defaults``.

    Args:
        text: Prompt shown to the user.
        default: Default value (shown in brackets).
        accept_defaults: If True, skip the prompt.

    Returns:
        User string, or ``default`` when empty / non-interactive.
    """
    if accept_defaults:
        return default
    suffix = f" [{default}] " if default else " "
    try:
        reply = input(f"{text}{suffix}").strip()
    except EOFError:
        return default
    return reply if reply else default


def answers_lesson_flags(
    answers: dict[str, Any],
    module: ContentModule,
) -> dict[str, bool]:
    """Resolve include flags for a module’s lessons from answers or defaults.

    Args:
        answers: Loaded answers JSON (possibly empty).
        module: Content module with detected lessons.

    Returns:
        Map of lesson identifier → include.
    """
    flags = {lesson.identifier: lesson.default_include for lesson in module.lessons}
    blob = (answers.get("lessons") or {}).get(str(module.number)) or {}
    included = set(blob.get("included") or [])
    excluded = set(blob.get("excluded") or [])
    if not included and not excluded:
        return flags
    for lesson in module.lessons:
        if lesson.identifier in included:
            flags[lesson.identifier] = True
        elif lesson.identifier in excluded:
            flags[lesson.identifier] = False
        elif lesson.title in included:
            flags[lesson.identifier] = True
        elif lesson.title in excluded:
            flags[lesson.identifier] = False
    return flags


def wizard_lessons(
    modules: list[ContentModule],
    answers: dict[str, Any],
    accept_defaults: bool,
) -> dict[int, list[LessonCandidate]]:
    """List detected lessons and let the user include/exclude extras.

    This is the only wizard step. Tests and conferences are packed
    automatically; there are no assessment-date prompts.

    Args:
        modules: Detected content modules.
        answers: Previous answers, if any (lesson flags only).
        accept_defaults: Skip prompts.

    Returns:
        Module number → included lessons in IMSCC order.
    """
    chosen: dict[int, list[LessonCandidate]] = {}
    for module in modules:
        flags = answers_lesson_flags(answers, module)
        print(f"\n=== {module.title} ===")
        print("Detected lessons:")
        for index, lesson in enumerate(module.lessons, start=1):
            included = flags.get(lesson.identifier, lesson.default_include)
            mark = "x" if included else " "
            extra = " (extra)" if lesson.extra else ""
            print(f"  {index}. [{mark}] {lesson.title}{extra}")
        if not accept_defaults and module.lessons:
            reply = prompt(
                "Toggle numbers to include/exclude (comma-separated), or Enter to accept",
                "",
                accept_defaults,
            )
            if reply:
                for token in re.split(r"[,\s]+", reply):
                    if not token:
                        continue
                    try:
                        index = int(token)
                    except ValueError:
                        continue
                    if 1 <= index <= len(module.lessons):
                        ident = module.lessons[index - 1].identifier
                        flags[ident] = not flags.get(
                            ident, module.lessons[index - 1].default_include
                        )
        chosen[module.number] = [
            lesson
            for lesson in module.lessons
            if flags.get(lesson.identifier, lesson.default_include)
        ]
        print(
            f"  using {len(chosen[module.number])} lesson(s) "
            f"(of {len(module.lessons)} detected)"
        )
    return chosen


def build_answers_payload(
    course: str,
    semester_id: str,
    modules: list[ContentModule],
    included_lessons: dict[int, list[LessonCandidate]],
) -> dict[str, Any]:
    """Serialize lesson include/exclude flags so a later run can skip prompts.

    Tests, conferences, quizzes, assignments, and the exam are packed
    automatically and are not stored.

    Args:
        course: Course code.
        semester_id: Semester label.
        modules: Detected modules.
        included_lessons: Lessons kept per module.

    Returns:
        JSON-serializable answers object (lesson flags only).
    """
    lessons_out: dict[str, Any] = {}
    for module in modules:
        kept_ids = {item.identifier for item in included_lessons.get(module.number, [])}
        lessons_out[str(module.number)] = {
            "included": [
                lesson.identifier for lesson in module.lessons if lesson.identifier in kept_ids
            ],
            "excluded": [
                lesson.identifier
                for lesson in module.lessons
                if lesson.identifier not in kept_ids
            ],
        }
    return {
        "course": course,
        "semester": semester_id,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lessons": lessons_out,
    }


def kind_priority(kinds: list[str]) -> str:
    """Pick the strongest Kind value for a combined row.

    Args:
        kinds: Kinds present on the day.

    Returns:
        A Kind from the syllabus table vocabulary.
    """
    present = set(kinds)
    for kind in KIND_ORDER:
        if kind in present:
            return kind
    return kinds[0] if kinds else "async"


def module_label(number: int | None, phase: str) -> str:
    """Short Module-column text.

    Args:
        number: Content module number, or None.
        phase: ``intro``, ``review``, ``exam``, ``nonschool``, or ``content``.

    Returns:
        Cell text.
    """
    if phase == "intro":
        return "Intro"
    if phase == "review":
        return "Review"
    if phase == "exam":
        return "Exam"
    if phase == "nonschool":
        return ""
    if number is None:
        return ""
    return f"M{number}"


def color_key_for(phase: str, number: int | None) -> str:
    """CSS color key for a row.

    Args:
        phase: Calendar phase.
        number: Module number when in content.

    Returns:
        Key into ``MODULE_COLORS``.
    """
    if phase == "content" and number is not None:
        return f"m{(number - 1) % 8 + 1}"
    return phase


def module_test_title(module: ContentModule) -> str:
    """Return the IMSCC test title for ``module``, or ``Module N Test``.

    Args:
        module: Content module.

    Returns:
        Display title for that module's test.
    """
    if module.test and module.test.title:
        return module.test.title
    return f"Module {module.number} Test"


def placements_from_pack(
    modules: list[ContentModule],
    windows: dict[int, list[date]],
    sequences: dict[int, ModuleSequence],
    included_lessons: dict[int, list[LessonCandidate]],
    placed: list[PlacedAssessment],
    warnings: list[str],
) -> dict[date, DayPlacement]:
    """Turn packer sequences into per-day placements.

    Slack days stay tagged as their module with no lesson. Test dates never
    receive a lesson.

    Args:
        modules: Content modules.
        windows: Per-module date spans (including slack).
        sequences: Lesson / Review / conference / test / slack dates.
        included_lessons: Lessons to place.
        placed: Auto tests and conferences.
        warnings: Collect packing notes.

    Returns:
        Content-pool dates mapped to placements.
    """
    blocked_tests = test_dates(placed)
    out: dict[date, DayPlacement] = {}
    for module in modules:
        number = module.number
        for day in windows.get(number) or []:
            out[day] = DayPlacement(module_number=number)
        seq = sequences.get(number) or ModuleSequence([], None, None, None, [])
        lessons = included_lessons.get(number) or []
        lesson_days = list(seq.lesson_days)
        blocked = {
            d
            for d in (seq.review_day, seq.conference_day, seq.test_day)
            if d is not None
        }
        blocked |= set(seq.slack_days)
        blocked |= blocked_tests
        lesson_days = [day for day in lesson_days if day not in blocked]
        if lessons and not lesson_days:
            fallback = [
                day for day in (windows.get(number) or []) if day not in blocked
            ]
            warnings.append(
                f"M{number} has no lesson days; stacking on "
                f"{fallback[-1].isoformat() if fallback else 'n/a'}"
            )
            lesson_days = [fallback[-1]] if fallback else []
        placed_lessons, leftover = distribute_lessons(lessons, lesson_days)
        if leftover:
            warnings.append(
                f"M{number}: {len(leftover)} unused lesson slot(s) "
                "before Review/conference/test"
            )
        for day, items in placed_lessons.items():
            if not items or day in blocked_tests:
                continue
            slot = out.setdefault(day, DayPlacement(module_number=number))
            slot.lesson = items[0].title
            slot.lesson_id = items[0].identifier
        if seq.review_day and seq.review_day not in blocked_tests:
            slot = out.setdefault(
                seq.review_day, DayPlacement(module_number=number)
            )
            slot.review = True
            slot.lesson = ""
            slot.lesson_id = None
    for item in placed:
        if item.kind == "exam" or item.module_number is None:
            continue
        slot = out.setdefault(
            item.date, DayPlacement(module_number=item.module_number)
        )
        slot.module_number = item.module_number
        slot.assessment_kind = item.kind
        slot.assessment_title = item.title
        if item.kind == "test":
            slot.lesson = ""
            slot.lesson_id = None
            slot.review = False
    return out


def parse_editor_placements(
    raw: dict[str, Any],
    content_pool: set[date],
    modules: list[ContentModule],
) -> dict[date, DayPlacement]:
    """Validate editor POST JSON into content-pool placements.

    Args:
        raw: ``{"placements": {iso: {...}}}`` from the editor.
        content_pool: Dates that may receive user placements.
        modules: Known content modules (for number checks).

    Returns:
        Parsed placements.

    Raises:
        ValueError: If a date, module, or assessment kind is invalid.
    """
    known = {module.number for module in modules}
    blob = raw.get("placements")
    if blob is None:
        blob = raw
    if not isinstance(blob, dict):
        raise ValueError("placements must be an object keyed by date")
    out: dict[date, DayPlacement] = {}
    for key, value in blob.items():
        day = parse_iso_date(str(key))
        if day not in content_pool:
            raise ValueError(f"{day.isoformat()} is not a content-pool day")
        if not isinstance(value, dict):
            raise ValueError(f"placement for {day.isoformat()} must be an object")
        number = int(value.get("module") or value.get("module_number") or 0)
        if number not in known:
            raise ValueError(f"unknown module {number} on {day.isoformat()}")
        kind = value.get("assessment_kind") or value.get("assessmentKind")
        if kind in ("", None):
            kind = None
        if kind not in {None, "test", "conference", "assignment", "quiz"}:
            raise ValueError(f"invalid assessment kind on {day.isoformat()}")
        review = bool(value.get("review"))
        live = bool(value.get("live"))
        lesson = str(value.get("lesson") or value.get("lessonTitle") or "")
        lesson_id = value.get("lesson_id") or value.get("lessonId")
        if str(lesson_id) == LIVE_CLASS_ID:
            live = True
            lesson_id = None
            if lesson == LIVE_CLASS_TITLE:
                lesson = ""
        title = str(
            value.get("assessment_title") or value.get("assessmentTitle") or ""
        )
        if kind == "test":
            lesson = ""
            lesson_id = None
            review = False
        if review:
            lesson = ""
            lesson_id = None
        out[day] = DayPlacement(
            module_number=number,
            lesson=lesson,
            lesson_id=str(lesson_id) if lesson_id else None,
            review=review,
            live=live,
            assessment_kind=kind,
            assessment_title=title if kind else "",
        )
    return out


def build_table_rows_from_placements(
    calendar: SemesterCalendar,
    slots: list[LiveSlot],
    placements: dict[date, DayPlacement],
    *,
    blank_calendar: bool = False,
) -> list[TableRow]:
    """Assemble syllabus rows from locked bands plus content-pool placements.

    Intro, exam-prep Review, exam-window, and PD/holiday days ignore
    placements unless ``blank_calendar`` is True (editor): then only PD,
    holidays, and exam-window cells stay locked; intro, exam-prep, and
    live times are left empty until the user places them.

    Args:
        calendar: Derived school days.
        slots: Live-class times (used when a day is marked live).
        placements: Optional content on editable dates.
        blank_calendar: If True, do not auto-fill intro, exam-prep, or
            schedule live classes.

    Returns:
        Ordered table rows.
    """
    intro = set(intro_days(calendar))
    exam_prep = set(review_days(calendar))
    exam_set = set(calendar.exam_days)
    nonschool_by_date = {mark.date: mark.reason for mark in calendar.nonschool}
    assessments_on: dict[date, list[PlacedAssessment]] = {}
    for day, slot in placements.items():
        if not slot.assessment_kind:
            continue
        assessments_on.setdefault(day, []).append(
            PlacedAssessment(
                module_number=slot.module_number,
                kind=slot.assessment_kind,
                identifier=slot.lesson_id if slot.assessment_kind == "test" else None,
                title=slot.assessment_title,
                date=day,
            )
        )
    blocked_tests = {day for day, items in assessments_on.items()
                     if any(item.kind == "test" for item in items)}

    rows: list[TableRow] = []
    previous_week: int | None = None
    previous_month: str | None = None
    for day in calendar.table_weekdays:
        week = week_index(day, calendar.first_day)
        month = month_name(day)
        week_changed = previous_week is not None and week != previous_week
        month_changed = previous_month != month
        previous_week = week
        previous_month = month
        weekday = weekday_name(day)

        if day in nonschool_by_date:
            rows.append(
                TableRow(
                    week=week,
                    month=month,
                    date=day,
                    weekday=weekday,
                    module="",
                    kind="nonschool",
                    lesson=f"No school — {nonschool_by_date[day]}",
                    assessment="",
                    time="",
                    color_key="nonschool",
                    week_changed=week_changed,
                    month_changed=month_changed,
                    emphasized=False,
                    dimmed=True,
                )
            )
            continue

        lesson_parts: list[str] = []
        assessment_parts: list[str] = []
        kinds: list[str] = []
        time = ""
        slot = placements.get(day)
        number = slot.module_number if slot else None
        phase = "content"
        is_test_day = day in blocked_tests

        if not blank_calendar and day in intro:
            phase = "intro"
            number = None
            lesson_parts.append("Course overview")
            kinds.append("intro")
            if is_live_weekday(day, slots):
                time = live_time_for(day, slots)
        elif not blank_calendar and day in exam_prep:
            phase = "review"
            number = None
            lesson_parts.append(CATCHUP_TITLE)
            kinds.append("review")
            if is_live_weekday(day, slots):
                time = live_time_for(day, slots)
        elif day in exam_set:
            phase = "exam"
            number = None
            kinds.append("exam")
        else:
            phase = "content"
            scheduled_live = (not blank_calendar) and is_live_weekday(day, slots)
            placed_live = bool(slot and slot.live)
            if scheduled_live or placed_live:
                kinds.append("live")
                time = live_time_for(day, slots) or (" " if placed_live else "")
            if slot and not is_test_day:
                if slot.review:
                    lesson_parts.append(CATCHUP_TITLE)
                    kinds.append("review")
                elif slot.lesson:
                    lesson_parts.append(slot.lesson)
                    if "live" not in kinds:
                        kinds.append("async")
            for item in assessments_on.get(day, []):
                if item.kind == "exam":
                    continue
                assessment_parts.append(item.title)
                kinds.append(item.kind)
            if not kinds:
                kinds.append("async")

        kind = kind_priority(kinds)
        rows.append(
            TableRow(
                week=week,
                month=month,
                date=day,
                weekday=weekday,
                module=module_label(number, phase),
                kind=kind,
                lesson=" · ".join(lesson_parts),
                assessment=" · ".join(assessment_parts),
                time=time,
                color_key=color_key_for(phase, number),
                week_changed=week_changed,
                month_changed=month_changed,
                emphasized=kind in {"test", "exam", "conference"},
                dimmed=False,
            )
        )
    return rows


def build_table_rows(
    calendar: SemesterCalendar,
    slots: list[LiveSlot],
    modules: list[ContentModule],
    windows: dict[int, list[date]],
    sequences: dict[int, ModuleSequence],
    included_lessons: dict[int, list[LessonCandidate]],
    placed: list[PlacedAssessment],
    warnings: list[str],
) -> list[TableRow]:
    """Assemble one table row per weekday in the semester span.

    Args:
        calendar: Derived school days.
        slots: Live-class times.
        modules: Content modules (for titles).
        windows: Module windows.
        sequences: Per-module lesson / Review / conference / test / slack dates.
        included_lessons: Lessons to place (including on live-class days).
        placed: Assessments with dates.
        warnings: Collect packing warnings.

    Returns:
        Ordered table rows.
    """
    placements = placements_from_pack(
        modules, windows, sequences, included_lessons, placed, warnings
    )
    return build_table_rows_from_placements(calendar, slots, placements)


def write_csv(path: Path, rows: list[TableRow]) -> None:
    """Write the syllabus table as CSV.

    Args:
        path: Destination ``.csv``.
        rows: Table rows.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "Week",
                "Month",
                "Date",
                "Weekday",
                "Module",
                "Kind",
                "Lesson",
                "Assessment",
                "Time",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.week,
                    row.month,
                    row.date_number(),
                    row.weekday,
                    row.module,
                    row.kind,
                    row.lesson,
                    row.assessment,
                    row.time,
                ]
            )


def months_covering(start: date, end: date) -> list[tuple[int, int]]:
    """Return ``(year, month)`` pairs from ``start``'s month through ``end``'s.

    Args:
        start: First date in the span (typically the 1st of the first month).
        end: Last date in the span.

    Returns:
        Inclusive year-month pairs in order.
    """
    pairs: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        pairs.append((year, month))
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return pairs


def month_weekday_weeks(year: int, month: int) -> list[list[date | None]]:
    """Build Mon–Fri week rows for one month, board-calendar style.

    Cells outside the month are ``None`` (leading/trailing blanks). Weeks that
    contain no days of this month are omitted (month starts on a weekend).

    Args:
        year: Calendar year.
        month: Month number 1–12.

    Returns:
        Rows of five cells (Monday through Friday).
    """
    first = date(year, month, 1)
    if month == 12:
        stop = date(year + 1, 1, 1)
    else:
        stop = date(year, month + 1, 1)
    last = stop - timedelta(days=1)
    start_monday = first - timedelta(days=first.weekday())
    weeks: list[list[date | None]] = []
    cursor = start_monday
    while cursor <= last:
        week: list[date | None] = []
        for offset in range(5):
            day = cursor + timedelta(days=offset)
            week.append(day if day.month == month else None)
        if any(cell is not None for cell in week):
            weeks.append(week)
        cursor += timedelta(days=7)
    return weeks


GRID_INK = "#1e293b"
GRID_LINE = "#475569"
GRID_PAD_LINE = "#cbd5e1"
GRID_EMPHASIS = "#b91c1c"
GRID_LIVE = "#1d4ed8"
GRID_ASSESS = "#991b1b"
GRID_MUTED = "#475569"
GRID_DIM = "#94a3b8"
GRID_HEAD_BG = "#334155"
GRID_OUT_BG = "#ffffff"

_OTHER_LESSON_LABELS = frozenset({"Course overview", "Exam window", CATCHUP_TITLE})


def _grid_lesson_span(text: str, *, dimmed: bool) -> str:
    """Return an inline-styled span for a lesson, Review, or closed line.

    Args:
        text: Lesson-column text for the day.
        dimmed: Closed/PD days use muted italic type.

    Returns:
        An HTML ``<span>`` with styles inlined for Canvas RCE paste.
    """
    escaped = html.escape(text)
    if dimmed or text.startswith("No school"):
        return (
            f'<span style="color:{GRID_DIM};font-style:italic;font-weight:400;">'
            f"{escaped}</span>"
        )
    if text in _OTHER_LESSON_LABELS:
        return (
            f'<span style="color:{GRID_MUTED};font-style:italic;font-weight:400;">'
            f"{escaped}</span>"
        )
    return (
        f'<span style="color:{GRID_INK};font-weight:500;">{escaped}</span>'
    )


def grid_cell_parts(
    day: date | None,
    by_date: dict[date, TableRow],
    calendar: SemesterCalendar,
) -> tuple[str, str, str, str]:
    """Split one calendar day into background plus three band fragments.

    Lesson vs Review vs closed/PD comes from the existing ``TableRow`` (or
    ``calendar.closed`` when the day is before the table span). Live class is
    a second line in the lesson fragment (``Live class<br>Lesson …``).

    Args:
        day: Date in this month, or ``None`` for a leading/trailing blank.
        by_date: Syllabus rows keyed by date.
        calendar: Closed/PD lookup when a day has no table row.

    Returns:
        ``(bg, day_html, lesson_html, assessment_html)``. Blank days use the
        pad background and empty strings.
    """
    pad_bg = MODULE_COLORS["nonschool"]
    if day is None:
        return (pad_bg, "", "", "")

    day_html = (
        f'<div style="font-size:0.85em;font-weight:700;text-align:left;'
        f'line-height:1.15;">{day.day}</div>'
    )
    row = by_date.get(day)
    if row is None:
        reason = calendar.closed.get(day)
        if reason:
            lesson_html = (
                f'<span style="color:{GRID_DIM};font-style:italic;font-weight:400;">'
                f"{html.escape(reason)}</span>"
            )
            return (pad_bg, day_html, lesson_html, "")
        return (GRID_OUT_BG, day_html, "", "")

    bg = MODULE_COLORS.get(row.color_key, GRID_OUT_BG)
    lesson_bits: list[str] = []
    if row.time:
        lesson_bits.append(
            f'<span style="color:{GRID_LIVE};font-weight:700;">Live class</span>'
        )
    if row.lesson:
        lesson_bits.append(_grid_lesson_span(row.lesson, dimmed=row.dimmed))
    lesson_html = "<br>".join(lesson_bits)
    assessment_html = ""
    if row.assessment:
        assessment_html = (
            f'<span style="color:{GRID_ASSESS};font-weight:700;">'
            f"{html.escape(row.assessment)}</span>"
        )
    return (bg, day_html, lesson_html, assessment_html)


def _grid_td(
    *,
    band: str,
    bg: str,
    inner: str,
    pad: bool,
    emphasized: bool,
    dimmed: bool,
    tooltip: str,
    extra_attrs: str = "",
) -> str:
    """Build one Canvas-safe ``<td>`` for a day / lesson / assessment sub-row.

    Outer box per day: top border on the day row, left/right on all three,
    bottom on the assessment row. No inner line between the three sub-rows.

    Args:
        band: ``day``, ``lesson``, or ``assessment``.
        bg: Cell background colour (same for all three bands of a day).
        inner: Already-built inner HTML (may be empty).
        pad: Leading/trailing blank outside the month.
        emphasized: Test/exam outer box uses a red border.
        dimmed: Muted text colour for closed days.
        tooltip: ``title`` attribute text.
        extra_attrs: Optional attributes (editor ``data-date`` / ``class``).

    Returns:
        An HTML ``<td>`` with all layout CSS inlined.

    Raises:
        ValueError: If ``band`` is not one of the three sub-row names.
    """
    if band not in {"day", "lesson", "assessment"}:
        raise ValueError(f"unknown grid band: {band}")
    line = GRID_EMPHASIS if emphasized else (GRID_PAD_LINE if pad else GRID_LINE)
    width = "2px" if emphasized else "1px"
    sides = (
        f"border-left:{width} solid {line};border-right:{width} solid {line};"
    )
    if band == "day":
        borders = (
            f"border-top:{width} solid {line};border-bottom:0;{sides}"
        )
        valign = "top"
        padding = "0.22rem 0.28rem 0.04rem"
        content = inner
    elif band == "lesson":
        borders = f"border-top:0;border-bottom:0;{sides}"
        valign = "top"
        padding = "0.08rem 0.28rem"
        content = (
            f'<div style="min-height:2.6em;line-height:1.25;">{inner}</div>'
        )
    else:
        borders = (
            f"border-top:0;border-bottom:{width} solid {line};{sides}"
        )
        valign = "bottom"
        padding = "0.08rem 0.28rem 0.22rem"
        content = (
            f'<div style="min-height:1.35em;line-height:1.25;">{inner}</div>'
        )
    color = GRID_DIM if dimmed else GRID_INK
    style = (
        f"{borders}background:{bg};width:20%;vertical-align:{valign};"
        f"padding:{padding};color:{color};"
    )
    title = f' title="{html.escape(tooltip)}"' if tooltip else ""
    extra = extra_attrs if extra_attrs.startswith(" ") or not extra_attrs else f" {extra_attrs}"
    return f'<td style="{style}"{title}{extra}>{content}</td>'


def _grid_cell_flags(
    day: date | None,
    by_date: dict[date, TableRow],
    calendar: SemesterCalendar,
) -> tuple[bool, bool, bool, str]:
    """Return pad / emphasized / dimmed flags and a tooltip for one day.

    Args:
        day: Date in this month, or ``None`` for a blank.
        by_date: Syllabus rows keyed by date.
        calendar: Closed/PD lookup.

    Returns:
        ``(pad, emphasized, dimmed, tooltip)``.
    """
    if day is None:
        return (True, False, False, "")
    row = by_date.get(day)
    if row is not None:
        bits = [
            bit
            for bit in (row.module, row.lesson, row.assessment, row.time)
            if bit
        ]
        tooltip = (
            f"{day.isoformat()} — " + " · ".join(bits) if bits else day.isoformat()
        )
        return (False, row.emphasized, row.dimmed, tooltip)
    reason = calendar.closed.get(day)
    if reason:
        return (False, False, True, f"{day.isoformat()} — {reason}")
    return (False, False, False, day.isoformat())


def _clickable_cell_attrs(day: date | None, clickable: set[date] | None, band: str) -> str:
    """Return editor attributes for a clickable content-pool cell.

    Args:
        day: Calendar date, or None for a pad cell.
        clickable: Dates the editor may assign; None disables attributes.
        band: ``day``, ``lesson``, or ``assessment``.

    Returns:
        Attribute string beginning with a space, or empty.
    """
    if day is None or not clickable or day not in clickable:
        return ""
    return (
        f' data-date="{day.isoformat()}" data-band="{html.escape(band)}" '
        f'class="js-cal-day"'
    )


def render_week_band(
    days: list[date | None],
    by_date: dict[date, TableRow],
    calendar: SemesterCalendar,
    clickable: set[date] | None = None,
) -> str:
    """Emit three table rows (day number, lesson/Review, assessments) for one week.

    All cell styles are inline so the band survives Canvas RCE paste, which
    strips ``<style>`` blocks. Live class shares the middle cell with the
    lesson or Review line.

    Args:
        days: Five Monday–Friday cells; ``None`` for days outside the month.
        by_date: Syllabus rows keyed by date.
        calendar: Closed/PD days and the instructional span.
        clickable: Optional content-pool dates that get ``data-date`` (editor).

    Returns:
        HTML for three ``<tr>`` elements, each with five ``<td>``s.
    """
    day_cells: list[str] = []
    lesson_cells: list[str] = []
    assess_cells: list[str] = []
    for day in days:
        bg, day_html, lesson_html, assessment_html = grid_cell_parts(
            day, by_date, calendar
        )
        pad, emphasized, dimmed, tooltip = _grid_cell_flags(
            day, by_date, calendar
        )
        day_cells.append(
            _grid_td(
                band="day",
                bg=bg,
                inner=day_html,
                pad=pad,
                emphasized=emphasized,
                dimmed=dimmed,
                tooltip=tooltip,
                extra_attrs=_clickable_cell_attrs(day, clickable, "day"),
            )
        )
        lesson_cells.append(
            _grid_td(
                band="lesson",
                bg=bg,
                inner=lesson_html,
                pad=pad,
                emphasized=emphasized,
                dimmed=dimmed,
                tooltip=tooltip,
                extra_attrs=_clickable_cell_attrs(day, clickable, "lesson"),
            )
        )
        assess_cells.append(
            _grid_td(
                band="assessment",
                bg=bg,
                inner=assessment_html,
                pad=pad,
                emphasized=emphasized,
                dimmed=dimmed,
                tooltip=tooltip,
                extra_attrs=_clickable_cell_attrs(day, clickable, "assessment"),
            )
        )
    return (
        f"<tr>{''.join(day_cells)}</tr>"
        f"<tr>{''.join(lesson_cells)}</tr>"
        f"<tr>{''.join(assess_cells)}</tr>"
    )


def render_month_grids(
    rows: list[TableRow],
    calendar: SemesterCalendar,
    clickable: set[date] | None = None,
) -> str:
    """Render stacked Mon–Fri month tables covering the semester span.

    Each month is one five-column ``table-layout:fixed`` table. Weekday
    headers plus ``render_week_band`` rows live in that table. Grid layout
    CSS is inlined on tags so Canvas RCE paste (which drops ``<style>``)
    keeps the alignment.

    Args:
        rows: Day-list rows (lookup by date).
        calendar: Semester dates and closed days.
        clickable: Optional content-pool dates that get ``data-date`` (editor).

    Returns:
        HTML for the grid section.
    """
    if not calendar.table_weekdays:
        return ""
    by_date = {row.date: row for row in rows}
    span_start = calendar.first_day.replace(day=1)
    span_end = max(calendar.table_weekdays)
    table_style = (
        "border-collapse:collapse;table-layout:fixed;width:100%;"
        "font-size:75%;margin:0;"
    )
    th_style = (
        f"background:{GRID_HEAD_BG};color:#fff;font-size:0.75rem;"
        "font-weight:600;padding:0.25rem;text-align:center;"
        f"border:1px solid {GRID_HEAD_BG};width:20%;"
    )
    h2_style = (
        "margin:0 0 0.35rem;background:#0f172a;color:#fff;"
        "padding:0.35rem 0.6rem;font-size:0.95rem;"
    )
    headers = "".join(
        f'<th style="{th_style}">{name}</th>'
        for name in ("Mon", "Tue", "Wed", "Thu", "Fri")
    )
    blocks: list[str] = []
    for year, month in months_covering(span_start, span_end):
        label = date(year, month, 1).strftime("%B %Y")
        week_rows = "".join(
            render_week_band(week, by_date, calendar, clickable)
            for week in month_weekday_weeks(year, month)
        )
        blocks.append(
            f'<div class="month-block" style="margin:0 0 1.25rem;">'
            f'<h2 style="{h2_style}">{html.escape(label)}</h2>'
            f'<table style="{table_style}">'
            f"<thead><tr>{headers}</tr></thead>"
            f"<tbody>{week_rows}</tbody>"
            f"</table></div>"
        )
    return (
        f'<div class="month-stack" style="margin-bottom:2rem;">'
        f"{''.join(blocks)}</div>"
    )


def render_html(
    rows: list[TableRow],
    *,
    course: str,
    semester_id: str,
    live_slots: list[LiveSlot],
    warnings: list[str],
    calendar: SemesterCalendar,
) -> str:
    """Render a print-friendly HTML calendar: Canvas-safe month grid, then the day list.

    Args:
        rows: Table rows.
        course: Course code.
        semester_id: Semester label.
        live_slots: For the legend.
        warnings: Optional snap notes shown under the table.
        calendar: Needed for stacked month grids (holidays before day 1).

    Returns:
        Full HTML document.
    """
    slot_bits = ", ".join(
        f"{WEEKDAY_NAMES[slot.weekday]} {slot.label()}" for slot in live_slots
    )
    legend_items = [
        ("Intro", "intro"),
        ("M1", "m1"),
        ("M2", "m2"),
        ("M3", "m3"),
        ("M4", "m4"),
        ("M5", "m5"),
        ("M6", "m6"),
        ("M7", "m7"),
        ("M8", "m8"),
        ("Review", "review"),
        ("Exam", "exam"),
        ("No school", "nonschool"),
    ]
    legend_html = "".join(
        f'<span class="legend-item">'
        f'<span class="swatch" style="background:{MODULE_COLORS[key]}"></span>'
        f"{html.escape(label)}</span>"
        for label, key in legend_items
    )
    body_rows: list[str] = []
    for row in rows:
        if row.month_changed:
            body_rows.append(
                "<tr class=\"month-banner\">"
                f"<td colspan=\"9\">{html.escape(row.month)} {row.date.year}</td>"
                "</tr>"
            )
        classes = [f"mod-{row.color_key}", f"kind-{row.kind}"]
        if row.week_changed:
            classes.append("week-start")
        if row.month_changed:
            classes.append("month-start")
        if row.emphasized:
            classes.append("emphasized")
        if row.dimmed:
            classes.append("dimmed")
        bg = MODULE_COLORS.get(row.color_key, "#ffffff")
        body_rows.append(
            f'<tr class="{" ".join(classes)}" style="background:{bg}">'
            f"<td>{row.week}</td>"
            f"<td>{html.escape(row.month)}</td>"
            f"<td>{html.escape(row.date_number())}</td>"
            f"<td>{html.escape(row.weekday)}</td>"
            f"<td>{html.escape(row.module)}</td>"
            f"<td>{html.escape(row.kind)}</td>"
            f"<td>{html.escape(row.lesson)}</td>"
            f"<td>{html.escape(row.assessment)}</td>"
            f"<td>{html.escape(row.time)}</td>"
            "</tr>"
        )
    warning_block = ""
    if warnings:
        items = "".join(f"<li>{html.escape(item)}</li>" for item in warnings)
        warning_block = f"<h2>Notes</h2><ul>{items}</ul>"
    month_grids = render_month_grids(rows, calendar)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>{html.escape(course)} syllabus calendar · {html.escape(semester_id)}</title>
<style>
  :root {{
    --ink: #1e293b;
    --muted: #64748b;
    --line: #cbd5e1;
    --week: #334155;
  }}
  body {{
    font-family: "Segoe UI", system-ui, sans-serif;
    color: var(--ink);
    margin: 1.5rem;
    background: #fff;
  }}
  h1 {{ font-size: 1.4rem; margin: 0 0 0.25rem; }}
  h2 {{ font-size: 1.05rem; margin: 1.25rem 0 0.45rem; }}
  .meta {{ color: var(--muted); margin-bottom: 1rem; }}
  .legend {{
    display: flex; flex-wrap: wrap; gap: 0.6rem 1rem;
    font-size: 0.85rem; margin: 0 0 1rem;
  }}
  .legend-item {{ display: inline-flex; align-items: center; gap: 0.3rem; }}
  .swatch {{
    display: inline-block; width: 0.9rem; height: 0.9rem;
    border: 1px solid var(--line);
  }}
  /* Month grids: all layout CSS is inlined on table/th/td so Canvas RCE
     paste (which strips this <style> block) keeps the 3-row week bands.
     Local rules below are for the day-list table only. */
  table.day-list {{
    border-collapse: collapse;
    width: 100%;
    font-size: 0.86rem;
  }}
  table.day-list th, table.day-list td {{
    border: 1px solid var(--line);
    padding: 0.28rem 0.45rem;
    text-align: left;
    vertical-align: top;
  }}
  table.day-list th {{
    background: #0f172a;
    color: #fff;
    position: sticky;
    top: 0;
  }}
  table.day-list tr.week-start td:first-child {{
    box-shadow: inset 4px 0 0 var(--week);
  }}
  table.day-list tr.month-start td {{
    border-top: 2px solid var(--week);
  }}
  table.day-list tr.month-banner td {{
    background: #0f172a;
    color: #fff;
    font-weight: 600;
    letter-spacing: 0.04em;
  }}
  table.day-list tr.emphasized td {{
    font-weight: 700;
    border-top: 2px solid #b91c1c;
    border-bottom: 2px solid #b91c1c;
  }}
  table.day-list td:nth-child(8) {{
    font-weight: 600;
  }}
  table.day-list tr.kind-exam td {{
    box-shadow: inset 4px 0 0 #b91c1c;
  }}
  table.day-list tr.dimmed td {{
    color: #94a3b8;
    font-style: italic;
  }}
  @media print {{
    body {{ margin: 0.4in; font-size: 9pt; }}
    table.day-list th {{ position: static; }}
    .month-block {{ page-break-inside: avoid; }}
    tr {{ page-break-inside: avoid; }}
    .meta, .legend {{ color: #333; }}
    a {{ color: inherit; text-decoration: none; }}
  }}
</style>
</head>
<body>
<h1>{html.escape(course)} syllabus calendar</h1>
<p class="meta">{html.escape(semester_id)} · live classes {html.escape(slot_bits)} ·
legacy 8-module IMSCC lesson list (not the 5-theme rebuild)</p>
<div class="legend">{legend_html}</div>
{month_grids}
<h2>Day list</h2>
<table class="day-list">
<thead>
<tr>
  <th>Week</th><th>Month</th><th>Date</th><th>Weekday</th>
  <th>Module</th><th>Kind</th><th>Lesson</th><th>Assessment</th><th>Time</th>
</tr>
</thead>
<tbody>
{"".join(body_rows)}
</tbody>
</table>
{warning_block}
</body>
</html>
"""


def write_html(path: Path, document: str) -> None:
    """Write an HTML document to ``path``.

    Args:
        path: Destination ``.html``.
        document: Full HTML text.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")


def write_answers(path: Path, payload: dict[str, Any]) -> None:
    """Write wizard answers JSON.

    Args:
        path: Destination ``.answers.json``.
        payload: Answers object.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_calendar_outputs(
    out_dir: Path,
    slug: str,
    rows: list[TableRow],
    *,
    course: str,
    calendar: SemesterCalendar,
    slots: list[LiveSlot],
    warnings: list[str],
    content: list[ContentModule],
    included: dict[int, list[LessonCandidate]],
) -> tuple[Path, Path, Path]:
    """Write the static CSV, Canvas-safe HTML, and lesson-flag answers.

    Args:
        out_dir: ``courses/<CODE>/syllabus-calendar``.
        slug: Semester slug for filenames.
        rows: Built table rows.
        course: Course code.
        calendar: Semester calendar.
        slots: Live-class times.
        warnings: Notes to show on the static HTML.
        content: Content modules.
        included: Included lessons per module.

    Returns:
        ``(csv_path, html_path, answers_path)``.
    """
    csv_path = out_dir / f"{slug}.csv"
    html_path = out_dir / f"{slug}.html"
    answers_path = out_dir / f"{slug}.answers.json"
    write_csv(csv_path, rows)
    write_html(
        html_path,
        render_html(
            rows,
            course=course,
            semester_id=calendar.semester_id,
            live_slots=slots,
            warnings=warnings,
            calendar=calendar,
        ),
    )
    write_answers(
        answers_path,
        build_answers_payload(course, calendar.semester_id, content, included),
    )
    return csv_path, html_path, answers_path


def build_editor_payload(
    *,
    course: str,
    calendar: SemesterCalendar,
    slots: list[LiveSlot],
    modules: list[ContentModule],
    included: dict[int, list[LessonCandidate]],
    rows: list[TableRow],
) -> dict[str, Any]:
    """JSON for the click-to-place editor (lesson queues and locked days).

    Args:
        course: Course code.
        calendar: Semester calendar.
        slots: Live-class times.
        modules: Content modules.
        included: Included lessons (the editor queue).
        rows: Empty-start table rows (locked bands + live times).

    Returns:
        JSON-serializable editor state.
    """
    clickable = set(calendar.instructional_days)
    by_date = {row.date: row for row in rows}
    days: dict[str, Any] = {}
    for row in rows:
        iso = row.date.isoformat()
        days[iso] = {
            "iso": iso,
            "day": row.date.day,
            "weekday": row.weekday,
            "week": row.week,
            "month": row.month,
            "clickable": row.date in clickable,
            "live": False,
            "time": "",
            "slotTime": live_time_for(row.date, slots),
            "kind": row.kind,
            "module": row.module,
            "lesson": row.lesson,
            "assessment": row.assessment,
            "colorKey": row.color_key,
            "emphasized": row.emphasized,
            "dimmed": row.dimmed,
        }
    module_list = []
    for module in modules:
        lessons = included.get(module.number) or list(module.lessons)
        module_list.append(
            {
                "number": module.number,
                "title": module.title,
                "testTitle": module_test_title(module),
                "lessons": [
                    {"id": lesson.identifier, "title": lesson.title, "kind": "item"}
                    for lesson in lessons
                    if lesson.identifier != LIVE_CLASS_ID
                ],
            }
        )
    return {
        "course": course,
        "semester": calendar.semester_id,
        "colors": dict(MODULE_COLORS),
        "catchup": CATCHUP_TITLE,
        "liveId": LIVE_CLASS_ID,
        "modules": module_list,
        "days": days,
        "clickable": sorted(d.isoformat() for d in clickable),
        "byDateCount": len(by_date),
    }


def render_editor_day_list(
    rows: list[TableRow],
    clickable: set[date],
) -> str:
    """Render the editor day-list table body with clickable content-pool rows.

    Args:
        rows: Empty-start rows.
        clickable: Content-pool dates.

    Returns:
        HTML ``<tr>`` elements.
    """
    body_rows: list[str] = []
    for row in rows:
        if row.month_changed:
            body_rows.append(
                "<tr class=\"month-banner\">"
                f"<td colspan=\"9\">{html.escape(row.month)} {row.date.year}</td>"
                "</tr>"
            )
        classes = [f"mod-{row.color_key}", f"kind-{row.kind}"]
        if row.week_changed:
            classes.append("week-start")
        if row.month_changed:
            classes.append("month-start")
        if row.emphasized:
            classes.append("emphasized")
        if row.dimmed:
            classes.append("dimmed")
        extra = ""
        if row.date in clickable:
            classes.append("js-cal-day")
            extra = f' data-date="{row.date.isoformat()}"'
        bg = MODULE_COLORS.get(row.color_key, "#ffffff")
        body_rows.append(
            f'<tr class="{" ".join(classes)}" style="background:{bg}"{extra}>'
            f"<td>{row.week}</td>"
            f"<td>{html.escape(row.month)}</td>"
            f"<td>{html.escape(row.date_number())}</td>"
            f"<td>{html.escape(row.weekday)}</td>"
            f"<td class=\"js-mod\">{html.escape(row.module)}</td>"
            f"<td class=\"js-kind\">{html.escape(row.kind)}</td>"
            f"<td class=\"js-lesson\">{html.escape(row.lesson)}</td>"
            f"<td class=\"js-assess\">{html.escape(row.assessment)}</td>"
            f"<td class=\"js-time\">{html.escape(row.time)}</td>"
            "</tr>"
        )
    return "".join(body_rows)


EDITOR_JS = r"""
(function () {
  const DATA = JSON.parse(document.getElementById("editor-data").textContent);
  const remaining = {};
  DATA.modules.forEach(function (mod) {
    remaining[mod.number] = mod.lessons.map(function (item) {
      return { id: item.id, title: item.title, kind: item.kind || "item" };
    });
  });
  const placements = {};
  let tool = "lesson";
  let moduleNum = DATA.modules.length ? DATA.modules[0].number : 1;
  let dirty = false;

  const statusEl = document.getElementById("editor-status");
  const queueEl = document.getElementById("lesson-queue");
  const moduleEl = document.getElementById("module-select");

  function moduleMeta(number) {
    return DATA.modules.find(function (mod) { return mod.number === number; });
  }
  function testTitle(number) {
    const mod = moduleMeta(number);
    return mod ? mod.testTitle : ("Module " + number + " Test");
  }
  function confTitle(number) {
    return "Module " + number + " Conference";
  }
  function setStatus(text, ok) {
    statusEl.textContent = text;
    statusEl.className = ok ? "ok" : "err";
  }
  function colorFor(iso) {
    const p = placements[iso];
    if (p) {
      const key = "m" + (((p.module - 1) % 8) + 1);
      return DATA.colors[key] || "#ffffff";
    }
    const meta = DATA.days[iso];
    if (!meta) return "#ffffff";
    return DATA.colors[meta.colorKey] || "#ffffff";
  }
  function kindPriority(kinds) {
    const order = ["exam","test","conference","assignment","intro","review","live","async","nonschool"];
    for (let i = 0; i < order.length; i++) {
      if (kinds.indexOf(order[i]) !== -1) return order[i];
    }
    return kinds[0] || "async";
  }
  function displayFor(iso) {
    const meta = DATA.days[iso];
    const p = placements[iso];
    const isLive = !!(p && p.live);
    const liveLine = isLive
      ? '<span style="color:#1d4ed8;font-weight:700;">Live class</span>'
      : "";
    let lessonText = "";
    let assessText = "";
    const kinds = [];
    if (isLive) kinds.push("live");
    if (p) {
      if (p.assessmentKind === "test") {
        assessText = p.assessmentTitle;
        kinds.push("test");
      } else {
        if (p.review) {
          lessonText = DATA.catchup;
          kinds.push("review");
        } else if (p.lessonTitle) {
          lessonText = p.lessonTitle;
          if (kinds.indexOf("live") === -1) kinds.push("async");
        }
        if (p.assessmentKind === "conference") {
          assessText = p.assessmentTitle;
          kinds.push("conference");
        }
      }
    }
    if (!kinds.length) kinds.push("async");
    const lessonHtml = [liveLine, lessonText ? escapeHtml(lessonText) : ""]
      .filter(Boolean)
      .join("<br>");
    const assessHtml = assessText
      ? '<span style="color:#991b1b;font-weight:700;">' + escapeHtml(assessText) + "</span>"
      : "";
    const kind = kindPriority(kinds);
    const lessonList = [isLive ? "Live class" : "", lessonText]
      .filter(Boolean)
      .join(" · ");
    return {
      bg: colorFor(iso),
      kind: kind,
      module: p ? ("M" + p.module) : "",
      lessonList: lessonList,
      assessment: assessText,
      time: isLive ? ((meta && meta.slotTime) || "") : "",
      lessonHtml: lessonHtml,
      assessHtml: assessHtml,
      emphasized: kind === "test" || kind === "conference",
    };
  }
  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function paintDay(iso) {
    const view = displayFor(iso);
    document.querySelectorAll('[data-date="' + iso + '"]').forEach(function (el) {
      if (el.tagName === "TR") {
        el.style.background = view.bg;
        el.classList.toggle("emphasized", view.emphasized);
        const mod = el.querySelector(".js-mod");
        const kind = el.querySelector(".js-kind");
        const lesson = el.querySelector(".js-lesson");
        const assess = el.querySelector(".js-assess");
        if (mod) mod.textContent = view.module;
        if (kind) kind.textContent = view.kind;
        if (lesson) lesson.textContent = view.lessonList;
        if (assess) assess.textContent = view.assessment;
        const timeEl = el.querySelector(".js-time");
        if (timeEl) timeEl.textContent = view.time;
        return;
      }
      el.style.background = view.bg;
      const band = el.getAttribute("data-band");
      if (band === "lesson") {
        const wrap = el.querySelector("div") || el;
        wrap.innerHTML = view.lessonHtml;
      } else if (band === "assessment") {
        const wrap = el.querySelector("div") || el;
        wrap.innerHTML = view.assessHtml;
      }
    });
  }
  function returnLesson(p) {
    if (!p || !p.lessonId) return;
    const queue = remaining[p.module];
    if (!queue) return;
    if (queue.some(function (item) { return item.id === p.lessonId; })) return;
    queue.unshift({ id: p.lessonId, title: p.lessonTitle, kind: "item" });
  }
  function findKindDay(number, kind) {
    return Object.keys(placements).find(function (iso) {
      const p = placements[iso];
      return p && p.module === number && p.assessmentKind === kind;
    });
  }
  function emptyPlacement(number) {
    return {
      module: number,
      lessonId: null,
      lessonTitle: "",
      review: false,
      live: false,
      assessmentKind: null,
      assessmentTitle: "",
    };
  }
  function clearPlacement(iso, keepAssess) {
    const p = placements[iso];
    if (!p) return;
    returnLesson(p);
    if (keepAssess && p.assessmentKind) {
      placements[iso] = emptyPlacement(p.module);
      placements[iso].assessmentKind = p.assessmentKind;
      placements[iso].assessmentTitle = p.assessmentTitle;
    } else {
      delete placements[iso];
    }
    paintDay(iso);
  }
  function applyTool(iso) {
    const meta = DATA.days[iso];
    if (!meta || !meta.clickable) {
      setStatus("That day is locked (PD, holiday, or exam window).", false);
      return;
    }
    dirty = true;
    if (tool === "clear") {
      clearPlacement(iso, false);
      setStatus("Cleared " + iso, true);
      renderQueue();
      return;
    }
    if (tool === "live") {
      const prev = placements[iso] || emptyPlacement(moduleNum);
      const keepModule = (prev.lessonId || prev.review || prev.assessmentKind)
        ? prev.module
        : moduleNum;
      placements[iso] = {
        module: keepModule,
        lessonId: prev.lessonId,
        lessonTitle: prev.lessonTitle,
        review: prev.review,
        live: true,
        assessmentKind: prev.assessmentKind,
        assessmentTitle: prev.assessmentTitle,
      };
      if (!prev.lessonId && !prev.review && !prev.assessmentKind) {
        placements[iso].module = moduleNum;
      }
      paintDay(iso);
      setStatus("Live class on " + iso, true);
      return;
    }
    if (tool === "lesson") {
      const queue = remaining[moduleNum] || [];
      if (!queue.length) {
        setStatus("No remaining items in M" + moduleNum + ".", false);
        return;
      }
      const next = queue.shift();
      const prev = placements[iso] || emptyPlacement(moduleNum);
      if (prev.lessonId) {
        returnLesson(prev);
      }
      placements[iso] = {
        module: moduleNum,
        lessonId: next.id,
        lessonTitle: next.title,
        review: false,
        live: !!prev.live,
        assessmentKind: null,
        assessmentTitle: "",
      };
      paintDay(iso);
      setStatus("Placed next item on " + iso, true);
      renderQueue();
      return;
    }
    if (tool === "review") {
      const prev = placements[iso] || emptyPlacement(moduleNum);
      if (prev.lessonId) returnLesson(prev);
      placements[iso] = {
        module: moduleNum,
        lessonId: null,
        lessonTitle: "",
        review: true,
        live: prev.module === moduleNum ? !!prev.live : false,
        assessmentKind: prev && prev.assessmentKind !== "test" ? prev.assessmentKind : null,
        assessmentTitle: prev && prev.assessmentKind !== "test" ? prev.assessmentTitle : "",
      };
      paintDay(iso);
      setStatus("Review on " + iso, true);
      renderQueue();
      return;
    }
    if (tool === "test" || tool === "conference") {
      const moved = findKindDay(moduleNum, tool);
      if (moved && moved !== iso) {
        const old = placements[moved];
        if (old && !old.lessonId && !old.review && !old.live) delete placements[moved];
        else if (old) {
          old.assessmentKind = null;
          old.assessmentTitle = "";
        }
        paintDay(moved);
      }
      const prev = placements[iso] || emptyPlacement(moduleNum);
      if (tool === "test" && prev.lessonId) returnLesson(prev);
      const title = tool === "test" ? testTitle(moduleNum) : confTitle(moduleNum);
      placements[iso] = {
        module: moduleNum,
        lessonId: tool === "test" ? null : prev.lessonId,
        lessonTitle: tool === "test" ? "" : prev.lessonTitle,
        review: tool === "test" ? false : !!prev.review,
        live: prev.module === moduleNum ? !!prev.live : false,
        assessmentKind: tool,
        assessmentTitle: title,
      };
      paintDay(iso);
      setStatus((tool === "test" ? "Test" : "Conference") + " on " + iso, true);
      renderQueue();
    }
  }
  function renderQueue() {
    const queue = remaining[moduleNum] || [];
    if (!queue.length) {
      queueEl.innerHTML = "<p class=\"queue-empty\">No remaining items in M" +
        moduleNum + ".</p>";
      return;
    }
    queueEl.innerHTML = "<ol>" + queue.map(function (item, index) {
      const mark = index === 0 ? " <em>(next)</em>" : "";
      return "<li>" +
        "<span class=\"item-title\">" + escapeHtml(item.title) + mark + "</span>" +
        "<span class=\"item-btns\">" +
        "<button type=\"button\" data-act=\"up\" data-index=\"" + index + "\" title=\"Move up\">↑</button>" +
        "<button type=\"button\" data-act=\"down\" data-index=\"" + index + "\" title=\"Move down\">↓</button>" +
        "<button type=\"button\" data-act=\"del\" data-index=\"" + index + "\" title=\"Remove\">×</button>" +
        "</span></li>";
    }).join("") + "</ol>";
  }
  function payload() {
    const out = {};
    Object.keys(placements).forEach(function (iso) {
      const p = placements[iso];
      out[iso] = {
        module: p.module,
        lesson_id: p.lessonId,
        lesson: p.lessonTitle,
        review: p.review,
        live: !!p.live,
        assessment_kind: p.assessmentKind,
        assessment_title: p.assessmentTitle,
      };
    });
    return { placements: out };
  }
  document.querySelectorAll("[data-tool]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      tool = btn.getAttribute("data-tool");
      document.querySelectorAll("[data-tool]").forEach(function (other) {
        other.classList.toggle("active", other === btn);
      });
    });
  });
  moduleEl.addEventListener("change", function () {
    moduleNum = parseInt(moduleEl.value, 10);
    renderQueue();
  });
  document.body.addEventListener("click", function (event) {
    const actBtn = event.target.closest("#lesson-queue [data-act]");
    if (actBtn) {
      event.preventDefault();
      event.stopPropagation();
      const queue = remaining[moduleNum] || [];
      const index = parseInt(actBtn.getAttribute("data-index"), 10);
      const act = actBtn.getAttribute("data-act");
      if (isNaN(index) || index < 0 || index >= queue.length) return;
      if (act === "del") {
        queue.splice(index, 1);
        dirty = true;
        setStatus("Removed item from M" + moduleNum, true);
      } else if (act === "up" && index > 0) {
        const item = queue.splice(index, 1)[0];
        queue.splice(index - 1, 0, item);
        dirty = true;
      } else if (act === "down" && index < queue.length - 1) {
        const item = queue.splice(index, 1)[0];
        queue.splice(index + 1, 0, item);
        dirty = true;
      }
      renderQueue();
      return;
    }
    const hit = event.target.closest("[data-date]");
    if (!hit) return;
    applyTool(hit.getAttribute("data-date"));
  });
  document.getElementById("save-btn").addEventListener("click", function () {
    setStatus("Saving…", true);
    fetch("/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload()),
    }).then(function (res) { return res.json().then(function (body) {
      return { ok: res.ok, body: body };
    }); }).then(function (result) {
      if (!result.ok) {
        setStatus(result.body.error || "Save failed", false);
        return;
      }
      dirty = false;
      setStatus("Saved " + (result.body.html || "calendar files") +
        ". You can close this tab.", true);
    }).catch(function (err) {
      setStatus("Save failed: " + err, false);
    });
  });
  window.addEventListener("beforeunload", function (event) {
    if (!dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });
  renderQueue();
  setStatus("Click a school day to place the current tool. Change module from the dropdown. Save writes CSV + HTML.", true);
})();
"""


def render_editor_html(
    rows: list[TableRow],
    *,
    course: str,
    calendar: SemesterCalendar,
    live_slots: list[LiveSlot],
    modules: list[ContentModule],
    included: dict[int, list[LessonCandidate]],
) -> str:
    """Render the local click-to-place editor (not for Canvas paste).

    Args:
        rows: Empty-start table rows.
        course: Course code.
        calendar: Semester calendar.
        live_slots: Live-class times (legend).
        modules: Content modules (dropdown + queues).
        included: Included lessons per module.

    Returns:
        Full HTML document with toolbar and editor script.
    """
    clickable = set(calendar.instructional_days)
    payload = build_editor_payload(
        course=course,
        calendar=calendar,
        slots=live_slots,
        modules=modules,
        included=included,
        rows=rows,
    )
    payload_json = json.dumps(payload).replace("<", "\\u003c")
    options = "".join(
        f'<option value="{module.number}">M{module.number}</option>'
        for module in modules
    )
    month_grids = render_month_grids(rows, calendar, clickable)
    day_list = render_editor_day_list(rows, clickable)
    legend_items = [
        ("Intro", "intro"),
        ("M1", "m1"),
        ("M2", "m2"),
        ("M3", "m3"),
        ("M4", "m4"),
        ("M5", "m5"),
        ("M6", "m6"),
        ("M7", "m7"),
        ("M8", "m8"),
        ("Review", "review"),
        ("Exam", "exam"),
        ("No school", "nonschool"),
    ]
    legend_html = "".join(
        f'<span class="legend-item">'
        f'<span class="swatch" style="background:{MODULE_COLORS[key]}"></span>'
        f"{html.escape(label)}</span>"
        for label, key in legend_items
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>{html.escape(course)} syllabus editor · {html.escape(calendar.semester_id)}</title>
<style>
  :root {{
    --ink: #1e293b;
    --muted: #64748b;
    --line: #cbd5e1;
    --week: #334155;
  }}
  body {{
    font-family: "Segoe UI", system-ui, sans-serif;
    color: var(--ink);
    margin: 0;
    background: #fff;
  }}
  .toolbar {{
    position: sticky; top: 0; z-index: 30;
    background: #0f172a; color: #fff;
    display: flex; flex-wrap: wrap; gap: 0.6rem 1rem;
    align-items: center; padding: 0.7rem 1.1rem;
  }}
  .toolbar label {{ font-size: 0.85rem; }}
  .toolbar select, .toolbar button {{
    font: inherit; padding: 0.25rem 0.55rem; border-radius: 4px; border: 0;
  }}
  .toolbar button {{ background: #334155; color: #fff; cursor: pointer; }}
  .toolbar button.active {{ background: #2563eb; }}
  .toolbar #save-btn {{ background: #15803d; font-weight: 600; }}
  #editor-status {{ font-size: 0.85rem; color: #cbd5e1; }}
  #editor-status.err {{ color: #fecaca; }}
  #editor-status.ok {{ color: #bbf7d0; }}
  .wrap {{ display: grid; grid-template-columns: 16rem 1fr; gap: 1rem;
    padding: 1rem 1.25rem 2rem; align-items: start; }}
  .sidebar {{
    position: sticky; top: 4.2rem;
    border: 1px solid var(--line); border-radius: 6px; padding: 0.75rem;
    background: #f8fafc; max-height: calc(100vh - 5.5rem); overflow: auto;
  }}
  .sidebar h2 {{ font-size: 0.95rem; margin: 0 0 0.4rem; }}
  .sidebar ol {{ margin: 0; padding-left: 1.2rem; font-size: 0.82rem; }}
  .sidebar li {{
    margin: 0.25rem 0; display: flex; align-items: flex-start; gap: 0.25rem;
  }}
  .item-title {{ flex: 1; min-width: 0; }}
  .item-btns {{ display: inline-flex; gap: 0.1rem; flex-shrink: 0; }}
  .item-btns button {{
    font: inherit; font-size: 0.72rem; line-height: 1.2;
    padding: 0 0.28rem; border: 1px solid var(--line); background: #fff;
    color: var(--ink); cursor: pointer; border-radius: 3px;
  }}
  .queue-empty {{ color: var(--muted); font-size: 0.85rem; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 0.25rem; }}
  h2 {{ font-size: 1.05rem; margin: 1.25rem 0 0.45rem; }}
  .meta {{ color: var(--muted); margin-bottom: 1rem; }}
  .legend {{
    display: flex; flex-wrap: wrap; gap: 0.6rem 1rem;
    font-size: 0.85rem; margin: 0 0 1rem;
  }}
  .legend-item {{ display: inline-flex; align-items: center; gap: 0.3rem; }}
  .swatch {{
    display: inline-block; width: 0.9rem; height: 0.9rem;
    border: 1px solid var(--line);
  }}
  .js-cal-day {{ cursor: pointer; }}
  td.js-cal-day:hover, tr.js-cal-day:hover td {{
    outline: 2px solid #2563eb; outline-offset: -2px;
  }}
  table.day-list {{
    border-collapse: collapse; width: 100%; font-size: 0.86rem;
  }}
  table.day-list th, table.day-list td {{
    border: 1px solid var(--line); padding: 0.28rem 0.45rem;
    text-align: left; vertical-align: top;
  }}
  table.day-list th {{ background: #0f172a; color: #fff; position: sticky; top: 3.6rem; }}
  table.day-list tr.week-start td:first-child {{
    box-shadow: inset 4px 0 0 var(--week);
  }}
  table.day-list tr.month-start td {{ border-top: 2px solid var(--week); }}
  table.day-list tr.month-banner td {{
    background: #0f172a; color: #fff; font-weight: 600;
  }}
  table.day-list tr.emphasized td {{
    font-weight: 700; border-top: 2px solid #b91c1c; border-bottom: 2px solid #b91c1c;
  }}
  table.day-list tr.dimmed td {{ color: #94a3b8; font-style: italic; }}
</style>
</head>
<body>
<div class="toolbar">
  <strong>{html.escape(course)} editor</strong>
  <label>Module
    <select id="module-select">{options}</select>
  </label>
  <span>
    <button type="button" data-tool="live">Live class</button>
    <button type="button" data-tool="lesson" class="active">Lesson</button>
    <button type="button" data-tool="review">Review</button>
    <button type="button" data-tool="test">Test</button>
    <button type="button" data-tool="conference">Conference</button>
    <button type="button" data-tool="clear">Clear</button>
  </span>
  <button type="button" id="save-btn">Save</button>
  <span id="editor-status"></span>
</div>
<div class="wrap">
<aside class="sidebar">
  <h2>Remaining items</h2>
  <div id="lesson-queue"></div>
</aside>
<main>
<h1>{html.escape(course)} syllabus calendar</h1>
<p class="meta">{html.escape(calendar.semester_id)} · board calendar (PD / holidays / exam window locked) ·
click a school day · Save writes CSV + HTML (this page is not for Canvas)</p>
<div class="legend">{legend_html}</div>
{month_grids}
<h2>Day list</h2>
<table class="day-list">
<thead>
<tr>
  <th>Week</th><th>Month</th><th>Date</th><th>Weekday</th>
  <th>Module</th><th>Kind</th><th>Lesson</th><th>Assessment</th><th>Time</th>
</tr>
</thead>
<tbody>
{day_list}
</tbody>
</table>
</main>
</div>
<script type="application/json" id="editor-data">{payload_json}</script>
<script>{EDITOR_JS}</script>
</body>
</html>
"""


def read_multipart_named_file(
    header_items: list[tuple[str, str]],
    body: bytes,
    field: str,
) -> tuple[str, bytes]:
    """Read one named file field from a multipart POST body.

    Args:
        header_items: Request headers as ``(name, value)`` pairs.
        body: Raw request body.
        field: Form field name (``imscc``).

    Returns:
        ``(filename, file_bytes)``.

    Raises:
        ValueError: If the part is missing or empty.
    """
    content_type = ""
    for name, value in header_items:
        if name.lower() == "content-type":
            content_type = value
            break
    if "boundary=" not in content_type.lower():
        raise ValueError("expected a multipart file upload")
    boundary = content_type.split("boundary=", 1)[1].strip().strip('"')
    delim = b"--" + boundary.encode("ascii", errors="replace")
    for raw_part in body.split(delim):
        part = raw_part
        if part.startswith(b"--"):
            continue
        if part.startswith(b"\r\n"):
            part = part[2:]
        elif part.startswith(b"\n"):
            part = part[1:]
        if not part:
            continue
        if b"\r\n\r\n" in part:
            head, payload = part.split(b"\r\n\r\n", 1)
        elif b"\n\n" in part:
            head, payload = part.split(b"\n\n", 1)
        else:
            continue
        header_text = head.decode("utf-8", errors="replace")
        if f'name="{field}"' not in header_text and f"name={field}" not in header_text:
            continue
        filename = "course.imscc"
        match = re.search(r'filename="([^"]+)"', header_text, re.I)
        if match:
            filename = match.group(1)
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        elif payload.endswith(b"\n"):
            payload = payload[:-1]
        if not payload:
            raise ValueError("uploaded file was empty")
        return filename, payload
    raise ValueError(f"missing form field {field}")


def render_upload_html(*, course: str, semester_id: str) -> str:
    """Render the IMSCC upload screen (same toolbar look as the editor).

    Args:
        course: Course code for the heading.
        semester_id: Semester label.

    Returns:
        Full HTML document.
    """
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>{html.escape(course)} syllabus editor · {html.escape(semester_id)}</title>
<style>
  :root {{ --ink: #1e293b; --muted: #64748b; --line: #cbd5e1; }}
  body {{
    font-family: "Segoe UI", system-ui, sans-serif;
    color: var(--ink); margin: 0; background: #fff;
  }}
  .toolbar {{
    position: sticky; top: 0; z-index: 30;
    background: #0f172a; color: #fff;
    display: flex; flex-wrap: wrap; gap: 0.6rem 1rem;
    align-items: center; padding: 0.7rem 1.1rem;
  }}
  main {{ padding: 1.25rem; max-width: 40rem; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 0.25rem; }}
  .meta {{ color: var(--muted); margin-bottom: 1rem; }}
  label {{ display: block; margin: 0.75rem 0 0.35rem; font-size: 0.9rem; }}
  input[type="file"] {{ font: inherit; }}
  button {{
    font: inherit; padding: 0.35rem 0.7rem; border: 0; border-radius: 4px;
    background: #15803d; color: #fff; font-weight: 600; cursor: pointer;
    margin-top: 0.75rem;
  }}
  #upload-status {{ margin-left: 0.75rem; font-size: 0.85rem; color: var(--muted); }}
  #upload-status.err {{ color: #b91c1c; }}
</style>
</head>
<body>
<div class="toolbar">
  <strong>{html.escape(course)} editor</strong>
</div>
<main>
<h1>Upload course cartridge</h1>
<p class="meta">{html.escape(semester_id)} · blank board calendar (PD / holidays / exam window from
the school-year calendar). Choose a Canvas <code>.imscc</code> export to load module items.</p>
<form id="upload-form">
  <label for="imscc">Canvas .imscc file</label>
  <input id="imscc" name="imscc" type="file" accept=".imscc,.zip" required/>
  <div>
    <button type="submit">Open calendar</button>
    <span id="upload-status"></span>
  </div>
</form>
</main>
<script>
(function () {{
  const form = document.getElementById("upload-form");
  const status = document.getElementById("upload-status");
  form.addEventListener("submit", function (event) {{
    event.preventDefault();
    const file = document.getElementById("imscc").files[0];
    if (!file) {{ status.textContent = "Choose a .imscc file."; status.className = "err"; return; }}
    status.textContent = "Reading…";
    status.className = "";
    const data = new FormData();
    data.append("imscc", file, file.name);
    fetch("/upload", {{ method: "POST", body: data }})
      .then(function (res) {{ return res.json().then(function (body) {{
        return {{ ok: res.ok, body: body }};
      }}); }})
      .then(function (result) {{
        if (!result.ok) {{
          status.textContent = result.body.error || "Upload failed";
          status.className = "err";
          return;
        }}
        window.location.reload();
      }})
      .catch(function (err) {{
        status.textContent = String(err);
        status.className = "err";
      }});
  }});
}})();
</script>
</body>
</html>
"""


def pick_editor_port(preferred: int = 8765) -> int:
    """Bind an available localhost port, preferring ``preferred``.

    Args:
        preferred: First port to try.

    Returns:
        A free TCP port on 127.0.0.1.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])


def run_editor_server(
    *,
    course: str,
    calendar: SemesterCalendar,
    slots: list[LiveSlot],
    out_dir: Path,
    slug: str,
) -> int:
    """Serve upload, then the click-to-place editor, until Ctrl-C.

    Starts on a blank board calendar (PD / holidays / exam window locked).
    POST ``/upload`` loads an ``.imscc``; POST ``/save`` writes static files.

    Args:
        course: Course code (output folder).
        calendar: Semester calendar from ``semester.json``.
        slots: Optional live-class times (used only when Live class is placed).
        out_dir: Output folder for Save.
        slug: Semester slug.

    Returns:
        Process exit code.
    """
    empty_rows = build_table_rows_from_placements(
        calendar, slots, {}, blank_calendar=True
    )
    upload_html = render_upload_html(
        course=course, semester_id=calendar.semester_id
    )
    state: dict[str, Any] = {
        "content": [],
        "included": {},
        "editor_html": None,
        "temp_imscc": None,
    }
    pool = set(calendar.instructional_days)

    def json_error(handler: BaseHTTPRequestHandler, message: str, status: int = 400) -> None:
        """Send a JSON error body."""
        body = json.dumps({"ok": False, "error": message}).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def send_bytes(
        handler: BaseHTTPRequestHandler,
        payload: bytes,
        content_type: str,
        status: int = 200,
    ) -> None:
        """Send a complete HTTP response body."""
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(payload)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(payload)

    class EditorHandler(BaseHTTPRequestHandler):
        """Local editor: upload IMSCC, click days, save CSV/HTML."""

        def log_message(self, format: str, *args: Any) -> None:
            """Print a short access line to stdout."""
            sys.stdout.write(
                "%s - %s\n" % (self.address_string(), format % args)
            )

        def do_GET(self) -> None:
            """Serve the upload form or the editor once a cartridge is loaded."""
            if self.path.split("?", 1)[0] not in {"/", "/index.html"}:
                self.send_error(404)
                return
            page = state["editor_html"] or upload_html
            send_bytes(self, page.encode("utf-8"), "text/html; charset=utf-8")

        def do_POST(self) -> None:
            """Handle IMSCC upload or placement save."""
            path = self.path.split("?", 1)[0]
            if path == "/upload":
                self._handle_upload()
                return
            if path == "/save":
                self._handle_save()
                return
            self.send_error(404)

        def _handle_upload(self) -> None:
            """Parse the uploaded ``.imscc`` and build the editor page."""
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                json_error(self, "empty upload")
                return
            raw = self.rfile.read(length)
            try:
                _name, data = read_multipart_named_file(
                    list(self.headers.items()), raw, "imscc"
                )
                handle = tempfile.NamedTemporaryFile(
                    suffix=".imscc", delete=False
                )
                path = Path(handle.name)
                try:
                    handle.write(data)
                    handle.close()
                    modules = load_modules_from_imscc_file(path)
                except (ValueError, OSError, KeyError):
                    try:
                        handle.close()
                    except OSError:
                        pass
                    path.unlink(missing_ok=True)
                    raise
                previous = state.get("temp_imscc")
                state["temp_imscc"] = str(path)
                if previous and previous != str(path):
                    Path(previous).unlink(missing_ok=True)
                included = {
                    module.number: list(module.lessons) for module in modules
                }
                state["content"] = modules
                state["included"] = included
                state["editor_html"] = render_editor_html(
                    empty_rows,
                    course=course,
                    calendar=calendar,
                    live_slots=slots,
                    modules=modules,
                    included=included,
                )
            except (ValueError, OSError, KeyError) as exc:
                json_error(self, str(exc))
                return
            body = json.dumps({"ok": True}).encode("utf-8")
            send_bytes(self, body, "application/json")
            print(f"Loaded IMSCC with {len(state['content'])} module(s)")

        def _handle_save(self) -> None:
            """Accept placements JSON and write CSV/HTML/answers."""
            content: list[ContentModule] = state["content"]
            included: dict[int, list[LessonCandidate]] = state["included"]
            if not content:
                json_error(self, "Upload a .imscc file first")
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
                placements = parse_editor_placements(payload, pool, content)
                rows = build_table_rows_from_placements(
                    calendar, slots, placements, blank_calendar=True
                )
                csv_path, html_path, answers_path = write_calendar_outputs(
                    out_dir,
                    slug,
                    rows,
                    course=course,
                    calendar=calendar,
                    slots=slots,
                    warnings=[],
                    content=content,
                    included=included,
                )
            except (ValueError, json.JSONDecodeError, OSError, KeyError) as exc:
                json_error(self, str(exc))
                return
            rel_html = html_path.relative_to(ROOT).as_posix()
            result = json.dumps(
                {
                    "ok": True,
                    "csv": csv_path.relative_to(ROOT).as_posix(),
                    "html": rel_html,
                    "answers": answers_path.relative_to(ROOT).as_posix(),
                }
            ).encode("utf-8")
            send_bytes(self, result, "application/json")
            print(f"Saved {rel_html}")

    port = pick_editor_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), EditorHandler)
    url = f"http://127.0.0.1:{port}/"
    print(f"{course} · {calendar.semester_id} · click-to-place editor", flush=True)
    print(f"Open {url} and upload a .imscc file", flush=True)
    print("Save writes CSV + HTML. Ctrl-C stops the server.", flush=True)
    threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEditor stopped.")
    finally:
        server.server_close()
        temp = state.get("temp_imscc")
        if temp:
            Path(temp).unlink(missing_ok=True)
    return 0


def format_day(day: date | None) -> str:
    """Format a date as ISO, or an em dash when missing.

    Args:
        day: Calendar date, or None.

    Returns:
        ``YYYY-MM-DD`` or ``—``.
    """
    return day.isoformat() if day is not None else "—"


def print_pack_lines(
    modules: list[ContentModule],
    sequences: dict[int, ModuleSequence],
    included_lessons: dict[int, list[LessonCandidate]],
) -> None:
    """Print one packing line per module.

    Args:
        modules: Content modules in order.
        sequences: Packed sequences.
        included_lessons: Included lessons per module.
    """
    print("\nModule packing (sequential walk):")
    for module in modules:
        seq = sequences.get(module.number)
        n_lessons = len(included_lessons.get(module.number) or [])
        if seq is None:
            print(f"  M{module.number}: (unpacked)")
            continue
        lessons = seq.lesson_days
        lesson_span = (
            f"{format_day(lessons[0])} → {format_day(lessons[-1])}"
            if lessons
            else "—"
        )
        slack_span = ""
        if seq.slack_days:
            slack_span = (
                f" ({seq.slack_days[0].isoformat()}→"
                f"{seq.slack_days[-1].isoformat()})"
            )
        print(
            f"  M{module.number}: lessons {lesson_span} ({n_lessons}) · "
            f"Review {format_day(seq.review_day)} · "
            f"Conf {format_day(seq.conference_day)} · "
            f"Test {format_day(seq.test_day)} · "
            f"slack {len(seq.slack_days)}{slack_span}"
        )


def print_sanity_summary(
    intro: list[date],
    modules: list[ContentModule],
    sequences: dict[int, ModuleSequence],
    included_lessons: dict[int, list[LessonCandidate]],
    exam_prep: list[date],
    exam_days: list[date],
    placed: list[PlacedAssessment],
    slots: list[LiveSlot],
    content_start: date | None,
) -> None:
    """Print a compact M1 / last-module / exam-prep check.

    Args:
        intro: First two instructional days.
        modules: Content modules in order.
        sequences: Packed sequences.
        included_lessons: Included lessons per module.
        exam_prep: Three Review days before the exam window.
        exam_days: Exam-window dates (empty cells).
        placed: Auto-placed tests and conferences.
        slots: Live-class slots (weekday rule check).
        content_start: First day of the content pool (M1 lesson 1).
    """
    kinds = {item.kind for item in placed}
    print("\nSanity:")
    print(f"  Intro: {', '.join(d.isoformat() for d in intro) or '—'}")
    spotlight = []
    if modules:
        spotlight.append(modules[0])
        if modules[-1] is not modules[0]:
            spotlight.append(modules[-1])
    for module in spotlight:
        seq = sequences.get(module.number)
        n_lessons = len(included_lessons.get(module.number) or [])
        if seq is None:
            continue
        start_note = ""
        if (
            content_start is not None
            and seq.lesson_days
            and module is modules[0]
        ):
            start_note = (
                " · M1 starts first content day"
                if seq.lesson_days[0] == content_start
                else " · M1 does not start on first content day"
            )
        close_rule = "close —"
        if seq.conference_day and seq.test_day:
            first_close = min(seq.conference_day, seq.test_day)
            conf_first = conference_first_on(first_close, slots)
            actual_conf_first = seq.conference_day < seq.test_day
            close_rule = (
                f"close {first_close.strftime('%a')} → "
                + ("conf then test" if actual_conf_first else "test then conf")
                + (" ✓" if actual_conf_first == conf_first else " ✗")
            )
        print(
            f"  M{module.number}: {n_lessons} consecutive lessons "
            f"{format_day(seq.lesson_days[0] if seq.lesson_days else None)}"
            f"→{format_day(seq.lesson_days[-1] if seq.lesson_days else None)}"
            f"{start_note}; Review {format_day(seq.review_day)}; "
            f"{close_rule}; slack {len(seq.slack_days)}"
        )
    prep = ", ".join(d.isoformat() for d in exam_prep) or "—"
    exam = ", ".join(d.isoformat() for d in exam_days) or "—"
    print(f"  Exam-prep Review: {prep}")
    print(f"  Exam-window (empty cells): {exam}")
    print(
        f"  Placed kinds: {', '.join(sorted(kinds)) or 'none'} "
        f"(no exam, no portfolios)"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for the syllabus calendar wizard.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Parsed namespace.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Build a highlighted CSV/HTML syllabus calendar from the board "
            "calendar, live-class times, and legacy IMSCC lessons."
        )
    )
    parser.add_argument(
        "--course",
        default="MCF3M",
        help="Course code under courses/ (default: MCF3M)",
    )
    parser.add_argument(
        "--answers",
        type=Path,
        default=None,
        help="Previous wizard answers JSON to load as defaults",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Accept defaults after loading answers (or built-in defaults)",
    )
    parser.add_argument(
        "--edit",
        action="store_true",
        help=(
            "Open a local click-to-place calendar (content pool starts empty). "
            "Save writes CSV + HTML. Takes precedence over --yes."
        ),
    )
    parser.add_argument(
        "--semester-json",
        type=Path,
        default=DEFAULT_SEMESTER_JSON,
        help=f"Semester calendar JSON (default: {DEFAULT_SEMESTER_JSON})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry: run the wizard and write CSV, HTML, and answers JSON.

    Args:
        argv: Optional argument list.

    Returns:
        Process exit code (0 on success).
    """
    args = parse_args(argv)
    course_dir = course_dir_for(args.course)
    schedule_path = course_dir / "schedule.json"
    if not args.semester_json.is_file():
        print(f"error: missing {args.semester_json}", file=sys.stderr)
        return 1

    if args.edit:
        try:
            calendar = load_semester_calendar(args.semester_json)
            slots: list[LiveSlot] = []
            if schedule_path.is_file():
                slots = parse_live_slots(load_json(schedule_path))
        except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        slug = semester_slug(calendar.semester_id)
        out_dir = course_dir / "syllabus-calendar"
        print(f"{args.course} · {calendar.semester_id} · blank board calendar")
        return run_editor_server(
            course=args.course,
            calendar=calendar,
            slots=slots,
            out_dir=out_dir,
            slug=slug,
        )

    if not schedule_path.is_file():
        print(f"error: missing {schedule_path}", file=sys.stderr)
        return 1

    try:
        calendar = load_semester_calendar(args.semester_json)
        slots = parse_live_slots(load_json(schedule_path))
        raw_modules = load_canvas_modules(course_dir)
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    content = detect_content_modules(raw_modules)
    if not content:
        print("error: no IMSCC content modules M1–M8 found", file=sys.stderr)
        return 1

    slug = semester_slug(calendar.semester_id)
    out_dir = course_dir / "syllabus-calendar"
    answers: dict[str, Any] = {}
    if args.answers is not None:
        if not args.answers.is_file():
            print(f"error: answers file not found: {args.answers}", file=sys.stderr)
            return 1
        answers = load_json(args.answers)
    else:
        default_answers = out_dir / f"{slug}.answers.json"
        if default_answers.is_file():
            answers = load_json(default_answers)
            print(f"Loaded lesson flags from {default_answers.relative_to(ROOT)}")

    intro = intro_days(calendar)
    review = review_days(calendar)
    span = content_days(calendar)
    print(f"{args.course} · {calendar.semester_id}")
    print(
        f"Instructional days: {len(calendar.instructional_days)} "
        f"({calendar.first_day.isoformat()} → {calendar.last_instructional.isoformat()})"
    )
    print(
        "Intro: "
        + ", ".join(d.isoformat() for d in intro)
        + (
            f" · Exam-prep Review: {review[0].isoformat()} → {review[-1].isoformat()}"
            if review
            else ""
        )
    )
    print(
        f"Content span: {len(span)} days · Exam window: "
        f"{len(calendar.exam_days)} days (empty cells)"
    )

    included = wizard_lessons(content, answers, args.yes)

    warnings: list[str] = []
    windows, sequences, placed = pack_modules(
        content, included, span, slots, warnings
    )
    print_pack_lines(content, sequences, included)
    print_sanity_summary(
        intro,
        content,
        sequences,
        included,
        review,
        calendar.exam_days,
        placed,
        slots,
        span[0] if span else None,
    )
    rows = build_table_rows(
        calendar,
        slots,
        content,
        windows,
        sequences,
        included,
        placed,
        warnings,
    )
    csv_path, html_path, answers_path = write_calendar_outputs(
        out_dir,
        slug,
        rows,
        course=args.course,
        calendar=calendar,
        slots=slots,
        warnings=warnings,
        content=content,
        included=included,
    )
    print(f"\nWrote {csv_path.relative_to(ROOT)}")
    print(f"Wrote {html_path.relative_to(ROOT)}")
    print(f"Wrote {answers_path.relative_to(ROOT)}")
    if warnings:
        print("Warnings:", file=sys.stderr)
        for item in warnings:
            print(f"  - {item}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

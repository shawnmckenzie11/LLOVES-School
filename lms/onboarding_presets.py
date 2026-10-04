"""MCK-183: the only course / days / start-time choices a teacher can pick.

The Your class screen and the Admin invite form both use these lists. They
are the same lists the Admin class assignment uses: catalog courses
(``search_ontario_courses``), the ``DAY_PRESETS`` keys and ``TIME_OPTIONS``
from ``tools/math-game-show/schedule.py``. There is no free text.
"""

from __future__ import annotations

from typing import Any

from schedule import DAY_PRESETS, TIME_OPTIONS

#: Days dropdown values, in Admin order.
DAY_OPTIONS: tuple[str, ...] = tuple(DAY_PRESETS.keys())

#: Labels shown in the dropdown. Wonder v1.3: "MWF", "TThF".
DAY_LABELS: dict[str, str] = {"M/W/F": "MWF", "T/Th/F": "TThF"}


def course_options(school: Any) -> list[dict[str, str]]:
    """Catalog courses as ``{"code", "title"}`` for a dropdown.

    Args:
        school: ``SchoolDB``.

    Returns:
        Every catalog course, code order.
    """
    rows = school.search_ontario_courses("", limit=1000)
    out = [
        {"code": str(row.get("code") or "").upper(), "title": str(row.get("title") or "")}
        for row in rows
        if row.get("code")
    ]
    return sorted(out, key=lambda row: row["code"])


def preset_lists(school: Any) -> dict[str, Any]:
    """All three dropdown lists for a template.

    Args:
        school: ``SchoolDB``.

    Returns:
        ``courses``, ``day_options`` (value/label), ``time_options``.
    """
    return {
        "courses": course_options(school),
        "day_options": [{"value": d, "label": DAY_LABELS.get(d, d)} for d in DAY_OPTIONS],
        "time_options": list(TIME_OPTIONS),
    }


def clean_preset(
    school: Any,
    code: str | None,
    days: str | None,
    time: str | None,
    *,
    required: bool,
) -> tuple[str, str, str] | None:
    """Validate a course / days / time choice against the lists.

    Args:
        school: ``SchoolDB``.
        code: Course code from the dropdown.
        days: ``M/W/F`` or ``T/Th/F``.
        time: One of ``TIME_OPTIONS``.
        required: When False, all three empty means "no preset" (``None``).

    Returns:
        ``(code, days, time)``, or ``None`` when optional and left empty.

    Raises:
        ValueError: A value is missing or not one of the listed options.
    """
    code_s = str(code or "").strip().upper()
    days_s = str(days or "").strip()
    time_s = str(time or "").strip()
    if not required and not (code_s or days_s or time_s):
        return None
    if not (code_s and days_s and time_s):
        raise ValueError("Pick a course, days and a start time to continue.")
    if school.get_course(code_s) is None:
        raise ValueError("Pick a course, days and a start time to continue.")
    if days_s not in DAY_OPTIONS or time_s not in TIME_OPTIONS:
        raise ValueError("Pick a course, days and a start time to continue.")
    return code_s, days_s, time_s

"""Teacher switch for showing roster rows flagged ``is_test_student``.

The switch defaults to off. Attendance, participation, gradebook, engagement,
feedback, the staff roster, and result exports keep today's real-student
numbers until a teacher turns it on. Celebrations and student class lists
never use this switch.
"""

from __future__ import annotations

from typing import Any

QUERY_KEY = "show_test_students"
COOKIE_NAME = "lloves_show_test_students"

_ON = {"1", "true", "yes", "on"}


def _is_on(value: Any) -> bool:
    """True for the usual truthy form values."""
    return str(value or "").strip().lower() in _ON


def show_test_students_requested(args: Any, cookies: Any = None) -> bool:
    """Whether this request should include test-student rows.

    A query value wins, including an explicit off, so an export link can
    match the page the teacher is looking at. With no query value, the
    staff cookie is used. Missing both means hidden.

    Args:
        args: Request args mapping. Must support ``in`` and ``.get``.
        cookies: Request cookies mapping, or None.

    Returns:
        True only when the teacher asked to show test students.
    """
    if args is not None and QUERY_KEY in args:
        return _is_on(args.get(QUERY_KEY))
    if cookies is None:
        return False
    return _is_on(cookies.get(COOKIE_NAME))

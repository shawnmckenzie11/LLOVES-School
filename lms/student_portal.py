"""Student live-class portal: mood, character, and home boards."""

from __future__ import annotations

from typing import Any

from db import STUDENT_CHARACTERS, STUDENT_MOODS

MOOD_LABELS = {
    "good": "Good",
    "ok": "Okay",
    "low": "Not great",
}

CHARACTER_LABELS = {
    "char_a": "Avery",
    "char_b": "Jordan",
    "char_c": "Samira",
    "char_d": "Kenji",
}


def bind_student_session(
    session: Any,
    offering: dict[str, Any],
    cls: dict[str, Any],
    student: dict[str, Any],
    *,
    live_session_id: int | None = None,
    session_code: str | None = None,
) -> None:
    """Store a roster-bound student-code session.

    Preserves an existing staff/IT Google login in the same browser cookie so
    a teacher testing student join in another tab does not lose Mark Attendance
    API auth (Begin Class Tracking).

    Args:
        session: Flask session mapping.
        offering: Course offering row.
        cls: Class section row.
        student: Roster row.
        live_session_id: Active ``live_class_sessions.id`` when joining live.
        session_code: Ephemeral join code for this meeting (preferred over
            the durable offering code when provided).
    """
    preserved: dict[str, Any] = {}
    if session.get("logged_in") and session.get("user_id"):
        for key in (
            "logged_in",
            "user_id",
            "portal",
            "email",
            "display_name",
            "picture",
            "role",
        ):
            if key in session:
                preserved[key] = session[key]
    session.clear()
    session.update(preserved)
    session["student_offering_id"] = int(offering["id"])
    session["student_live_code"] = (
        (session_code or "").strip().upper()
        or offering["live_access_code"]
    )
    session["student_course"] = offering["ontario_code"]
    session["student_class_id"] = int(cls["id"])
    session["student_id"] = int(student["id"])
    session["student_codename"] = str(
        student.get("codename") or student.get("first_name") or ""
    )
    if live_session_id is not None:
        session["student_live_session_id"] = int(live_session_id)
    if "role" not in preserved:
        session["role"] = "student"
    session.permanent = True


def next_student_endpoint(school: Any, class_id: int, student_id: int) -> str:
    """Return the Flask endpoint for the next unfinished student step.

    Args:
        school: SchoolDB.
        class_id: Class primary key.
        student_id: Students primary key.

    Returns:
        ``student_mood`` or ``student_home`` (character pick retired).
    """
    student = school.game.get_student(class_id, student_id)
    if not student.get("mood"):
        return "student_mood"
    return "student_home"


def character_choices() -> list[dict[str, str]]:
    """Four placeholder characters for the join screen."""
    return [{"key": key, "label": CHARACTER_LABELS[key]} for key in STUDENT_CHARACTERS]


def mood_choices() -> list[dict[str, str]]:
    """Three mood faces for the check-in screen."""
    return [{"key": key, "label": MOOD_LABELS[key]} for key in STUDENT_MOODS]

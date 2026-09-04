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
) -> None:
    """Store a roster-bound student-code session.

    Args:
        session: Flask session mapping.
        offering: Course offering row.
        cls: Class section row.
        student: Roster row.
    """
    session.clear()
    session["student_offering_id"] = int(offering["id"])
    session["student_live_code"] = offering["live_access_code"]
    session["student_course"] = offering["ontario_code"]
    session["student_class_id"] = int(cls["id"])
    session["student_id"] = int(student["id"])
    session["student_codename"] = str(
        student.get("codename") or student.get("first_name") or ""
    )
    session["role"] = "student"
    session.permanent = True


def next_student_endpoint(school: Any, class_id: int, student_id: int) -> str:
    """Return the Flask endpoint for the next unfinished student step.

    Args:
        school: SchoolDB.
        class_id: Class primary key.
        student_id: Students primary key.

    Returns:
        ``student_mood``, ``student_character``, or ``student_home``.
    """
    student = school.game.get_student(class_id, student_id)
    if not student.get("mood"):
        return "student_mood"
    if not (student.get("character_key") or "").strip():
        return "student_character"
    return "student_home"


def character_choices() -> list[dict[str, str]]:
    """Four placeholder characters for the join screen."""
    return [{"key": key, "label": CHARACTER_LABELS[key]} for key in STUDENT_CHARACTERS]


def mood_choices() -> list[dict[str, str]]:
    """Three mood faces for the check-in screen."""
    return [{"key": key, "label": MOOD_LABELS[key]} for key in STUDENT_MOODS]

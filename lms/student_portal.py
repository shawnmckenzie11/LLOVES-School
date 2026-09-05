"""Student live-class portal: mood, character, and home boards."""

from __future__ import annotations

from typing import Any

from db import STUDENT_CHARACTERS, STUDENT_MOODS

MOOD_LABELS = {
    "good": "Good",
    "ok": "Okay",
    "low": "Not great",
    "tired": "Tired",
    "energetic": "Energetic",
    "focused": "Focused",
    "anxious": "Anxious",
    "confused": "Confused",
    "excited": "Excited",
}

CHARACTER_LABELS = {
    "char_a": "Avery",
    "char_b": "Jordan",
    "char_c": "Samira",
    "char_d": "Kenji",
}

# Flask session keys owned by the student-code join path.
STUDENT_SESSION_KEYS = (
    "student_offering_id",
    "student_live_code",
    "student_course",
    "student_class_id",
    "student_id",
    "student_codename",
    "student_live_session_id",
    "student_visit_token",
    "student_mood_done",
)


def clear_student_session_keys(session: Any) -> None:
    """Remove student-code keys without wiping a same-browser staff login.

    Args:
        session: Flask session mapping.
    """
    for key in STUDENT_SESSION_KEYS:
        session.pop(key, None)
    if session.get("role") == "student":
        session.pop("role", None)


def bind_student_session(
    session: Any,
    offering: dict[str, Any],
    cls: dict[str, Any],
    student: dict[str, Any],
    *,
    live_session_id: int | None = None,
    session_code: str | None = None,
    visit_token: str | None = None,
) -> None:
    """Store a roster-bound student-code session.

    Preserves an existing staff/IT Google login in the same browser cookie so
    a teacher testing student join in another tab does not lose Mark Attendance
    API auth (Begin Class Tracking).

    Student keys are non-permanent: access is gated on an active live
    attendee row, and ending the live session clears them on the next request.

    Args:
        session: Flask session mapping.
        offering: Course offering row.
        cls: Class section row.
        student: Roster row.
        live_session_id: Active ``live_class_sessions.id`` when joining live.
        session_code: Ephemeral join code for this meeting (preferred over
            the durable offering code when provided).
        visit_token: Opaque attendee token for ``/student/s/<token>``.
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
    token = (visit_token or "").strip()
    if token:
        session["student_visit_token"] = token
    if "role" not in preserved:
        session["role"] = "student"
    # Live student access must not outlive the browser session as a permanent
    # cookie; the active-attendee gate clears keys when class ends.
    session.permanent = False


def next_student_endpoint(school: Any, class_id: int, student_id: int) -> str:
    """Return the Flask endpoint after join / mood / legacy redirects.

    Mood is optional. After pick/skip (``student_mood_done``) or when a mood
    is already stored, continue to home.

    Args:
        school: SchoolDB.
        class_id: Class primary key.
        student_id: Students primary key.

    Returns:
        ``student_mood`` or ``student_home``.
    """
    from flask import session

    if session.get("student_mood_done"):
        return "student_home"
    student = school.game.get_student(class_id, student_id)
    if student.get("mood"):
        session["student_mood_done"] = True
        return "student_home"
    return "student_mood"


def character_choices() -> list[dict[str, str]]:
    """Four placeholder characters for the join screen."""
    return [{"key": key, "label": CHARACTER_LABELS[key]} for key in STUDENT_CHARACTERS]


def mood_choices() -> list[dict[str, str]]:
    """Mood faces for the optional check-in screen."""
    return [{"key": key, "label": MOOD_LABELS[key]} for key in STUDENT_MOODS]

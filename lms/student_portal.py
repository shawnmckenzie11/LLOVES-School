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


def visit_token_from_request(req: Any | None = None) -> str:
    """Read opaque visit token from query, form, header, or JSON body.

    Args:
        req: Flask ``request``; defaults to the active request when omitted.

    Returns:
        Trimmed token string, or ``""`` when absent.
    """
    from flask import request as flask_request

    req = req or flask_request
    token = (
        (req.args.get("v") or req.headers.get("X-Student-Visit-Token") or "")
        .strip()
    )
    if token:
        return token
    if req.form:
        token = (req.form.get("visit_token") or "").strip()
        if token:
            return token
    payload = req.get_json(silent=True) or {}
    if isinstance(payload, dict):
        token = (payload.get("visit_token") or "").strip()
        if token:
            return token
    return ""


def resolve_student_live_context(
    school: Any,
    session: Any,
    req: Any | None = None,
) -> dict[str, Any] | None:
    """Resolve student live context from visit token (preferred) or Flask session.

    Visit tokens let multiple student tabs share one browser cookie without
    cross-talk: each tab sends its opaque ``v`` token on requests.

    Args:
        school: SchoolDB instance.
        session: Flask session mapping.
        req: Flask ``request``; defaults to the active request when omitted.

    Returns:
        Dict with ``offering``, ``class_id``, ``student_id``, ``live_session_id``,
        ``visit_token``, and ``codename``; or ``None`` when unresolved.
    """
    token = visit_token_from_request(req)
    if token:
        resolved = school.resolve_student_visit_token(token)
        if resolved is not None:
            attendee = resolved["attendee"]
            session_row = resolved["session"]
            class_id = int(resolved["class_id"])
            student_id = int(resolved["student_id"])
            live_session_id = int(resolved["live_session_id"])
            try:
                cls = school.game.get_class(class_id)
                student = school.game.get_student(class_id, student_id)
                offering = school.get_offering(int(session_row["offering_id"]))
            except (KeyError, TypeError):
                return None
            codename = str(
                attendee.get("codename")
                or student.get("codename")
                or student.get("first_name")
                or ""
            )
            return {
                "offering": offering,
                "class_id": class_id,
                "student_id": student_id,
                "live_session_id": live_session_id,
                "visit_token": token,
                "codename": codename,
            }
    offering_id = session.get("student_offering_id")
    class_id = session.get("student_class_id")
    student_id = session.get("student_id")
    if not offering_id or not class_id or not student_id:
        return None
    try:
        offering = school.get_offering(int(offering_id))
    except KeyError:
        return None
    live_session_id = session.get("student_live_session_id")
    return {
        "offering": offering,
        "class_id": int(class_id),
        "student_id": int(student_id),
        "live_session_id": int(live_session_id) if live_session_id else 0,
        "visit_token": str(session.get("student_visit_token") or ""),
        "codename": str(session.get("student_codename") or ""),
    }


def student_url_with_token(endpoint: str, token: str, **kwargs: Any) -> str:
    """Build a student route URL scoped to a visit token.

    Args:
        endpoint: Flask endpoint name (for example ``student_home``).
        token: Opaque ``live_session_attendees.visit_token``.
        **kwargs: Extra ``url_for`` keyword arguments.

    Returns:
        Relative URL, with ``?v=`` appended when ``token`` is non-empty.
    """
    from flask import url_for

    cleaned = (token or "").strip()
    if cleaned:
        kwargs["v"] = cleaned
    return url_for(endpoint, **kwargs)


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
    clear_student_session_keys(session)
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
    if not session.get("logged_in"):
        session["role"] = "student"
    # Live student access must not outlive the browser session as a permanent
    # cookie; the active-attendee gate clears keys when class ends.
    session.permanent = False


def next_student_endpoint(
    school: Any,
    class_id: int,
    student_id: int,
    *,
    visit_token: str = "",
) -> str:
    """Return the Flask endpoint after join / mood / legacy redirects.

    Mood is optional. After pick/skip (``student_mood_done``) or when a mood
    is already stored, continue to home.

    When ``visit_token`` is set (multi-tab testing), cookie ``student_mood_done``
    from another tab is ignored so each visit token keeps its own mood step.

    Args:
        school: SchoolDB.
        class_id: Class primary key.
        student_id: Students primary key.
        visit_token: Opaque attendee token when routing a token-scoped visit.

    Returns:
        ``student_mood`` or ``student_home``.
    """
    from flask import session

    student = school.game.get_student(class_id, student_id)
    if student.get("mood"):
        if not visit_token:
            session["student_mood_done"] = True
        return "student_home"
    if not visit_token and session.get("student_mood_done"):
        return "student_home"
    return "student_mood"


def character_choices() -> list[dict[str, str]]:
    """Four placeholder characters for the join screen."""
    return [{"key": key, "label": CHARACTER_LABELS[key]} for key in STUDENT_CHARACTERS]


def mood_choices() -> list[dict[str, str]]:
    """Mood faces for the optional check-in screen."""
    return [{"key": key, "label": MOOD_LABELS[key]} for key in STUDENT_MOODS]

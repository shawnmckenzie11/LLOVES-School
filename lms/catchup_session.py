"""MCK-234: one browser catch-up identity shared across landing and class links."""

from __future__ import annotations

import time
from typing import Any

CATCHUP_IDENTITY_KEY = "catchup_identity"
CATCHUP_LANDING_SESSION_KEY = "catchup_landing"
CATCHUP_LANDING_SESSION_S = 30 * 60


def catchup_url_with_play(path: str) -> str:
    """Append ``play=1`` so catch-up opens questions instead of the dashboard."""
    text = str(path or "").strip()
    if not text:
        return "?play=1"
    if "play=" in text.lower():
        return text
    sep = "&" if "?" in text else "?"
    return f"{text}{sep}play=1"


def _legacy_catchup_keys(session: Any) -> list[str]:
    """Per-link catch-up session keys (legacy MCK-207)."""
    return [
        str(key)
        for key in list(session.keys())
        if str(key).startswith("catchup:") and not str(key).endswith(":who")
    ]


def _pin_later_keys(session: Any) -> list[str]:
    return [str(key) for key in list(session.keys()) if str(key).startswith("catchup_pin_later:")]


def clear_global_student_keys(session: Any) -> None:
    """Drop Flask keys ``student_required`` reads for PIN portal students."""
    keys = (
        "student_offering_id",
        "student_class_id",
        "student_id",
        "student_codename",
    )
    for key in keys:
        session.pop(key, None)
    if not session.get("logged_in"):
        session.pop("role", None)


def clear_catchup_browser_session(session: Any) -> str:
    """Clear landing, identity, legacy link keys, PIN-later flags, and student globals.

    Returns:
        Portal join code from the landing session when present (for sign-out redirect).
    """
    held = session.pop(CATCHUP_LANDING_SESSION_KEY, None) or {}
    portal_code = str(held.get("portal_code") or "").strip()
    session.pop(CATCHUP_IDENTITY_KEY, None)
    for key in _legacy_catchup_keys(session):
        session.pop(key, None)
    for key in list(session.keys()):
        if str(key).startswith("catchup:") and str(key).endswith(":who"):
            session.pop(key, None)
    for key in _pin_later_keys(session):
        session.pop(key, None)
    clear_global_student_keys(session)
    return portal_code


def peek_catchup_identity(session: Any, school: Any) -> dict[str, Any] | None:
    """Return a valid ``catchup_identity`` payload or clear stale session data."""
    held = session.get(CATCHUP_IDENTITY_KEY)
    if not isinstance(held, dict):
        _drop_stale_legacy_sessions(session, school)
        return None
    now = time.time()
    cid = int(held.get("cid") or 0)
    sid = int(held.get("sid") or 0)
    exp = float(held.get("exp") or 0)
    stamp = str(held.get("pin") or "")
    want = school.catchup_pin_stamp(cid, sid) if cid and sid else ""
    valid = (
        cid > 0
        and sid > 0
        and exp > now
        and stamp
        and want
        and stamp == want
    )
    if not valid:
        clear_catchup_browser_session(session)
        return None
    return {"class_id": cid, "student_id": sid, "pin_stamp": stamp, "exp": exp}


def establish_catchup_identity(
    session: Any,
    school: Any,
    class_id: int,
    student_id: int,
    pin_stamp: str,
) -> None:
    """Record PIN-verified catch-up identity and bind global student session keys."""
    from participation import CATCHUP_SESSION_S

    cid = int(class_id)
    sid = int(student_id)
    stamp = str(pin_stamp or "").strip()
    if not stamp:
        raise ValueError("pin_stamp required")
    landing_hold = session.get(CATCHUP_LANDING_SESSION_KEY)
    for key in _legacy_catchup_keys(session):
        session.pop(key, None)
    for key in list(session.keys()):
        if str(key).startswith("catchup:") and str(key).endswith(":who"):
            session.pop(key, None)
    session.pop(CATCHUP_IDENTITY_KEY, None)
    for key in _pin_later_keys(session):
        session.pop(key, None)
    clear_global_student_keys(session)
    session[CATCHUP_IDENTITY_KEY] = {
        "cid": cid,
        "sid": sid,
        "pin": stamp,
        "exp": time.time() + float(CATCHUP_SESSION_S),
    }
    bind_global_student_session(session, school, cid, sid)
    if isinstance(landing_hold, dict) and int(landing_hold.get("class_id") or 0) == cid:
        session[CATCHUP_LANDING_SESSION_KEY] = landing_hold


def bind_global_student_session(session: Any, school: Any, class_id: int, student_id: int) -> None:
    """Mirror roster ids into student portal keys (names from roster map only)."""
    cid = int(class_id)
    sid = int(student_id)
    cls = school.game.get_class(cid)
    offering = school.get_offering(int(cls["offering_id"]))
    session["student_offering_id"] = int(offering["id"])
    session["student_class_id"] = cid
    session["student_id"] = sid
    session["student_codename"] = codename_for_student(school, cid, sid)
    if not session.get("logged_in"):
        session["role"] = "student"
    session.permanent = False


def codename_for_student(school: Any, class_id: int, student_id: int) -> str:
    """Display name for one roster student (never a free-typed landing string)."""
    names = school.class_codenames(int(class_id), include_test_students=True)
    return str(names.get(int(student_id)) or "")


def refresh_identity_pin_stamp(session: Any, school: Any, class_id: int, student_id: int) -> None:
    """Update the identity stamp after the student sets a new PIN."""
    from participation import CATCHUP_SESSION_S

    held = session.get(CATCHUP_IDENTITY_KEY)
    if not isinstance(held, dict):
        return
    cid = int(class_id)
    sid = int(student_id)
    if int(held.get("cid") or 0) != cid or int(held.get("sid") or 0) != sid:
        return
    stamp = school.catchup_pin_stamp(cid, sid)
    if not stamp:
        return
    held = dict(held)
    held["pin"] = stamp
    held["exp"] = max(float(held.get("exp") or 0), time.time() + float(CATCHUP_SESSION_S))
    session[CATCHUP_IDENTITY_KEY] = held


def identity_for_class(session: Any, school: Any, class_id: int) -> int | None:
    """Student id when the browser holds a valid identity for this class."""
    ident = peek_catchup_identity(session, school)
    if ident is None:
        return None
    if int(ident["class_id"]) != int(class_id):
        return None
    return int(ident["student_id"])


def set_landing_session(
    session: Any,
    *,
    class_id: int,
    student_id: int,
    matched_token: str,
    portal_code: str,
    display_name: str,
) -> None:
    """Store a short-lived catch-up landing session (name matched, PIN pending)."""
    session[CATCHUP_LANDING_SESSION_KEY] = {
        "class_id": int(class_id),
        "student_id": int(student_id),
        "matched_token": str(matched_token or "")[:80],
        "portal_code": str(portal_code or "").strip().upper()[:8],
        "display_name": str(display_name or "").strip()[:80],
        "exp": time.time() + CATCHUP_LANDING_SESSION_S,
    }


def peek_landing_session(session: Any) -> dict[str, Any] | None:
    """Read landing session without clearing it."""
    held = session.get(CATCHUP_LANDING_SESSION_KEY)
    if not isinstance(held, dict):
        return None
    if float(held.get("exp") or 0) <= time.time():
        session.pop(CATCHUP_LANDING_SESSION_KEY, None)
        return None
    return held


def pop_landing_session(session: Any) -> dict[str, Any] | None:
    """Read and remove landing session (one-shot flows only)."""
    held = peek_landing_session(session)
    if held is not None:
        session.pop(CATCHUP_LANDING_SESSION_KEY, None)
    return held


def clear_landing_session(session: Any) -> str:
    """Clear landing session; return prefilled portal code if any."""
    held = session.pop(CATCHUP_LANDING_SESSION_KEY, None) or {}
    return str(held.get("portal_code") or "").strip()


def landing_pending_student(session: Any, class_id: int) -> int | None:
    """Roster id from landing name match before PIN (same class only)."""
    held = peek_landing_session(session)
    if held is None:
        return None
    if int(held.get("class_id") or 0) != int(class_id):
        return None
    sid = held.get("student_id")
    return int(sid) if sid is not None else None


def replace_identity_for_live_join(
    session: Any,
    school: Any,
    class_id: int,
    student_id: int,
) -> None:
    """Clear catch-up state when a different student joins live from the portal."""
    ident = peek_catchup_identity(session, school)
    if ident is not None and int(ident["student_id"]) == int(student_id):
        return
    clear_catchup_browser_session(session)


def _drop_stale_legacy_sessions(session: Any, school: Any) -> None:
    """Remove expired per-link sessions so they cannot win dashboard resolution."""
    now = time.time()
    for key in _legacy_catchup_keys(session):
        held = session.get(key) or {}
        if not isinstance(held, dict):
            session.pop(key, None)
            continue
        cid = int(held.get("cid") or 0)
        sid = int(held.get("sid") or 0)
        exp = float(held.get("exp") or 0)
        stamp = str(held.get("pin") or "")
        want = school.catchup_pin_stamp(cid, sid) if cid and sid else ""
        if exp <= now or not stamp or stamp != want:
            session.pop(key, None)


def catchup_portal_session_valid(
    session: Any, school: Any, class_id: int, student_id: int
) -> bool:
    """True when ``catchup_identity`` matches this class and student."""
    ident = peek_catchup_identity(session, school)
    if ident is None:
        return False
    return (
        int(ident["class_id"]) == int(class_id)
        and int(ident["student_id"]) == int(student_id)
    )

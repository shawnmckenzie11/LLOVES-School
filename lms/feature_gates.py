"""Plan caps and live-class gates that sit on top of the feature registry.

Basic teachers keep Run Live Class, questions, attendance, the most
recent catch-up class, and draw-and-submit whiteboards. The teacher
overview for that whiteboard refreshes on a tap or on a 30 second
timer, and the ink is not pushed on the news wire. Pro, and the
launch grant, keep the rest.

Nothing here logs join codes, catch-up tokens, names, or emails.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

# Free-plan caps. The test seat the app adds for a class is not one of
# the 35 named students.
BASIC_SEAT_CAP = 35
BASIC_CLASS_CAP = 3
BASIC_HISTORY_DAYS = 30
WHITEBOARD_SUBMIT_REFRESH_S = 30

# Postcards that are not part of questions, slides, or the scoreboard.
# ``flag_work`` stays: publishing a question is on the basic list.
_NEWS_FEATURE = {
    "board_ink": "whiteboard",
    "board_show": "whiteboard",
    "active_media": "media",
    "google_slide": "slides_import",
}

_overview_at: dict[tuple[str, int], float] = {}


def session_whiteboard_closed(session_id: int) -> bool:
    """True when this live session exists and its teacher has no whiteboard.

    A missing session is not closed here. The route keeps its own
    ended-session answer instead of a feature 404.

    Args:
        session_id: ``live_class_sessions.id``.
    """
    try:
        sid = int(session_id)
    except (TypeError, ValueError):
        return False
    try:
        from features import _school, feature_for_session
    except ImportError:
        from lms.features import _school, feature_for_session
    school = _school()
    with school._lock:
        row = school.conn.execute(
            "SELECT id FROM live_class_sessions WHERE id = ?",
            (sid,),
        ).fetchone()
    if row is None:
        return False
    return not (
        feature_for_session(sid, "whiteboard")
        or feature_for_session(sid, "whiteboard_submit")
    )


def reset_whiteboard_refresh() -> None:
    """Forget overview timers. Tests call this between cases."""
    _overview_at.clear()


def news_events_for_session(
    session_id: int, events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Drop pro-only postcards when this session's teacher does not have them.

    Question, stage, and slide postcards always pass. A lookup failure
    keeps the original list so a live class is not silenced by a
    missing app context. Callers that already hold no extra types skip
    the lookup.

    Args:
        session_id: ``live_class_sessions.id``.
        events: Postcards about to be stored.

    Returns:
        The events this session may broadcast.
    """
    if not events:
        return events
    if not any(str(event.get("type") or "") in _NEWS_FEATURE for event in events):
        return events
    try:
        from features import feature_for_session
    except ImportError:
        from lms.features import feature_for_session
    try:
        flags = {
            key: feature_for_session(int(session_id), key)
            for key in set(_NEWS_FEATURE.values()) | {"news_wire_extras"}
        }
    except Exception:
        return events
    kept: list[dict[str, Any]] = []
    for event in events:
        needed = _NEWS_FEATURE.get(str(event.get("type") or ""))
        if needed is None:
            kept.append(event)
            continue
        if flags.get(needed) or flags.get("news_wire_extras"):
            kept.append(event)
    return kept


def overview_refresh_allowed(session_id: int, actor: str, *, tap: bool) -> bool:
    """True when this teacher overview read may hit the board store.

    Real-time whiteboards are not limited. Submit-only teachers may
    refresh on ``tap`` or at most once every 30 seconds. The actor token
    is an id, never a name.

    Args:
        session_id: ``live_class_sessions.id``.
        actor: Stable id for the reader, such as ``u:4``.
        tap: The reader asked to refresh now.

    Returns:
        Whether the read may proceed.
    """
    if tap:
        _overview_at[(str(actor), int(session_id))] = time.monotonic()
        return True
    key = (str(actor), int(session_id))
    now = time.monotonic()
    previous = _overview_at.get(key)
    if previous is not None and (now - previous) < WHITEBOARD_SUBMIT_REFRESH_S:
        return False
    _overview_at[key] = now
    return True


def scope_catchup_links(user: Any, links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Catch-up rows this teacher may hand out.

    Full catch-up returns every row. Otherwise only the newest row, and
    only when it was created inside the free history window. Rows are
    not logged.

    Args:
        user: Owning teacher, or None when the class has no teacher.
        links: Rows from ``list_catchup_links``, oldest first.

    Returns:
        The rows the plan allows, in the same order.
    """
    try:
        from features import has_feature, limits_apply
    except ImportError:
        from lms.features import has_feature, limits_apply
    if user is not None and has_feature(user, "catchup_full"):
        return list(links)
    if not links:
        return []
    # ``list_catchup_links`` orders by id, oldest first.
    newest = links[-1]
    if limits_apply(user) and not _within_history(newest.get("created_at")):
        return []
    return [newest]


def _within_history(raw: Any) -> bool:
    """True when ``raw`` is missing or inside the free history window."""
    text = str(raw or "").strip()
    if not text:
        return True
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return True
    if stamp.tzinfo is not None:
        stamp = stamp.replace(tzinfo=None)
    return datetime.now() - stamp <= timedelta(days=BASIC_HISTORY_DAYS)


def class_owner(school: Any, class_id: int) -> dict[str, Any] | None:
    """The teacher who owns this class, or None.

    Args:
        school: Open school database.
        class_id: Game-show ``classes.id``.
    """
    try:
        cls = school.game.get_class(int(class_id)) or {}
    except Exception:
        return None
    owner_id = cls.get("teacher_user_id")
    if owner_id in (None, "") and cls.get("offering_id"):
        offering = school.get_offering(int(cls["offering_id"])) or {}
        owner_id = offering.get("teacher_user_id")
    if owner_id in (None, ""):
        return None
    return school.get_user(int(owner_id))


def catchup_row_allowed(school: Any, class_id: int, link: dict[str, Any]) -> bool:
    """True when this catch-up row is inside the owner's plan.

    Compares module, slot, and run key. The token is not read into a log.

    Args:
        school: Open school database.
        class_id: Game-show ``classes.id``.
        link: One catch-up row, including ``run_key``.
    """
    owner = class_owner(school, int(class_id))
    visible = scope_catchup_links(owner, school.list_catchup_links(int(class_id)))
    module = str(link.get("live_module") or "")
    slot = str(link.get("live_slot") or "")
    run_key = str(link.get("run_key") or "")
    for row in visible:
        if (
            str(row.get("live_module") or "") == module
            and str(row.get("live_slot") or "") == slot
            and str(row.get("run_key") or "") == run_key
        ):
            return True
    return False

"""MCK-222 catch-up landing page: copy, tier cells, and list rows."""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from student_dashboard import module_number, slot_class_label

TORONTO = ZoneInfo("America/Toronto")

# Wonder copy, verbatim.
COPY_PICK_TITLE = "Pick a class to catch up on"
COPY_SIGNED_IN_AS = "Signed in as {name}"
COPY_NOT_YOU = "Not you?"
COPY_YOUR_CODE_MATCHED = "YOUR CODE MATCHED"
COPY_OPEN_CATCHUP = "Open catch-up"
COPY_OPEN_UNTIL = "open until {date}"
COPY_OTHER_HEADER = "OTHER OPEN CLASSES IN {course} · newest first"
COPY_SHOW_OLDER = "Show older"
COPY_TIER_INDIVIDUAL = "T1 Individual"
COPY_TIER_GROUP = "T2 Group"
COPY_TIER_UNDERSTANDING = "T3 Understanding"
COPY_TIER_DONE = "Done"
COPY_TIER_NA = "NA"
COPY_TIER_COMPLETE = "Complete"
COPY_TIER_INCOMPLETE = "Incomplete"
COPY_CLOSED_HEADING = "This class has ended and catch-up is closed."
COPY_CLOSED_DETAIL = "Catch-up closed on {date}. Ask your teacher if you need access."
COPY_ENTER_DIFFERENT = "Enter a different code"
COPY_WRONG_CODE = "That code didn't work. Check it and try again."
COPY_LIVE_JOINING = "Class is live — joining…"
COPY_ALL_CAUGHT_UP = "All caught up."
COPY_OFFLINE = "No connection. Retry."

CATCHUP_LANDING_SESSION_KEY = "catchup_landing"
CATCHUP_LANDING_SESSION_S = 30 * 60
PAGE_SIZE = 6


def catchup_open_days() -> int:
    """Days after class end that catch-up stays open (configurable)."""
    try:
        return max(1, int(os.getenv("CATCHUP_OPEN_DAYS") or "7"))
    except ValueError:
        return 7


def enforce_open_until() -> bool:
    """When true, links past their open-until date behave as closed."""
    raw = (os.getenv("CATCHUP_ENFORCE_OPEN_UNTIL") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def landing_copy() -> dict[str, str]:
    """Template-facing Wonder strings."""
    return {
        "pick_title": COPY_PICK_TITLE,
        "not_you": COPY_NOT_YOU,
        "your_code_matched": COPY_YOUR_CODE_MATCHED,
        "open_catchup": COPY_OPEN_CATCHUP,
        "other_header": COPY_OTHER_HEADER,
        "show_older": COPY_SHOW_OLDER,
        "tier_individual": COPY_TIER_INDIVIDUAL,
        "tier_group": COPY_TIER_GROUP,
        "tier_understanding": COPY_TIER_UNDERSTANDING,
        "closed_heading": COPY_CLOSED_HEADING,
        "closed_detail": COPY_CLOSED_DETAIL,
        "enter_different": COPY_ENTER_DIFFERENT,
        "wrong_code": COPY_WRONG_CODE,
        "live_joining": COPY_LIVE_JOINING,
        "all_caught_up": COPY_ALL_CAUGHT_UP,
        "offline": COPY_OFFLINE,
    }


def format_class_date(iso: str) -> str:
    """Format an ISO timestamp as ``Wed Oct 14`` in Toronto."""
    text = str(iso or "").strip()
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TORONTO)
    else:
        dt = dt.astimezone(TORONTO)
    return dt.strftime("%a %b %-d")


def class_row_label(
    live_module: str, live_slot: str, ended_at: str
) -> str:
    """Row label ``M3 · Class 1 · Wed Oct 14``."""
    mod = str(live_module or "").strip().upper()
    date = format_class_date(ended_at)
    slot_label = slot_class_label(live_slot)
    parts = [mod, slot_label]
    if date:
        parts.append(date)
    return " · ".join(parts)


def open_until_date(ended_at: str) -> str:
    """Last open day formatted for display."""
    text = str(ended_at or "").strip()
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TORONTO)
    else:
        dt = dt.astimezone(TORONTO)
    close = dt + timedelta(days=catchup_open_days())
    return close.strftime("%a %b %-d")


def link_still_open(school: Any, link: dict[str, Any]) -> bool:
    """True when the link is open and still inside the open-until window."""
    if not int(link.get("is_open") or 0):
        return False
    if not enforce_open_until():
        return True
    ended = school.ended_at_for_participation_run(
        int(link["class_id"]), str(link.get("run_key") or "")
    )
    if not ended:
        return True
    try:
        dt = datetime.fromisoformat(ended.replace("Z", "+00:00"))
    except ValueError:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TORONTO)
    else:
        dt = dt.astimezone(TORONTO)
    close = dt + timedelta(days=catchup_open_days())
    return datetime.now(tz=TORONTO) <= close


def landing_display_name(school: Any, class_id: int, student_id: int) -> str:
    """Signed-in label; adds last initial when first names collide in the class."""
    from school_db import first_name_only

    student = school.game.get_student(int(class_id), int(student_id))
    first = first_name_only(
        str(student.get("first_name") or student.get("codename") or "")
    )
    with school.game._lock:
        peers = school.game.conn.execute(
            "SELECT id, first_name, codename FROM students WHERE class_id = ?",
            (int(class_id),),
        ).fetchall()
    same = 0
    for row in peers:
        peer_first = first_name_only(
            str(row["first_name"] or row["codename"] or "")
        )
        if peer_first.casefold() == first.casefold():
            same += 1
    if same <= 1:
        return first
    last = str(student.get("last_display") or "").strip()
    if last:
        return f"{first} {last[0].upper()}."
    return first


def tier_cells_for_link(school: Any, link: dict[str, Any], student_id: int) -> dict[str, str]:
    """Three tier cells for one picker row (delegates to ``SchoolDB``)."""
    return school.catchup_landing_tier_cells(
        int(link["class_id"]), int(student_id), link
    )


def _row_sort_key(school: Any, link: dict[str, Any]) -> tuple[str, str]:
    ended = school.ended_at_for_participation_run(
        int(link["class_id"]), str(link.get("run_key") or "")
    )
    return (ended, str(link.get("live_module") or ""), str(link.get("live_slot") or ""))


def list_open_catchup_rows(
    school: Any,
    class_id: int,
    student_id: int,
    *,
    exclude_token: str | None = None,
    offset: int = 0,
    limit: int = PAGE_SIZE,
) -> tuple[list[dict[str, Any]], int]:
    """Open catch-up rows for one student, newest first.

    Returns:
        ``(rows, total_open)`` where rows are dicts for the template/API.
    """
    cid = int(class_id)
    sid = int(student_id)
    course = school._course_code_for_class(cid)
    open_links: list[dict[str, Any]] = []
    for link in school.list_catchup_links(cid):
        if exclude_token and str(link.get("token")) == str(exclude_token):
            continue
        if not link_still_open(school, link):
            continue
        open_links.append(link)
    open_links.sort(key=lambda row: _row_sort_key(school, row), reverse=True)
    total = len(open_links)
    slice_links = open_links[int(offset) : int(offset) + int(limit)]
    rows: list[dict[str, Any]] = []
    for link in slice_links:
        token = str(link["token"])
        ended = school.ended_at_for_participation_run(cid, str(link.get("run_key") or ""))
        tiers = tier_cells_for_link(school, link, sid)
        rows.append(
            {
                "token": token,
                "label": class_row_label(
                    str(link.get("live_module") or ""),
                    str(link.get("live_slot") or ""),
                    ended,
                ),
                "title": "",
                "tiers": tiers,
                "url": school.catchup_canonical_path_for_link({**link, "class_id": cid}),
                "open_until": open_until_date(ended),
            }
        )
    return rows, total


def build_matched_card(
    school: Any,
    link: dict[str, Any],
    student_id: int,
) -> dict[str, Any]:
    """Featured matched-class card for the landing picker."""
    cid = int(link["class_id"])
    ended = school.ended_at_for_participation_run(cid, str(link.get("run_key") or ""))
    return {
        "token": str(link["token"]),
        "label": class_row_label(
            str(link.get("live_module") or ""),
            str(link.get("live_slot") or ""),
            ended,
        ),
        "title": "",
        "tiers": tier_cells_for_link(school, link, int(student_id)),
        "open_until_line": COPY_OPEN_UNTIL.format(date=open_until_date(ended)),
        "url": school.catchup_canonical_path_for_link({**link, "class_id": cid}),
    }


def set_landing_session(
    session: Any,
    *,
    class_id: int,
    student_id: int,
    matched_token: str,
    portal_code: str,
    display_name: str,
) -> None:
    """Store a short-lived catch-up landing session."""
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


def signed_in_line(display_name: str) -> str:
    """``Signed in as Maya`` line."""
    return COPY_SIGNED_IN_AS.format(name=str(display_name or "").strip())

"""MCK-222 catch-up landing page: copy, tier cells, and list rows."""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from catchup_session import (
    CATCHUP_LANDING_SESSION_KEY,
    CATCHUP_LANDING_SESSION_S,
    catchup_url_with_play,
    clear_landing_session,
    peek_landing_session,
    pop_landing_session,
    set_landing_session,
)
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

PAGE_SIZE = 6


CATCHUP_AUTO_CLOSE_DAYS_DEFAULT = 7
CATCHUP_AUTO_CLOSE_DAYS_MIN = 1
CATCHUP_AUTO_CLOSE_DAYS_MAX = 90


def catchup_open_days() -> int:
    """Days after class end that catch-up stays open (configurable).

    This is the legacy window for rows that have no stored deadline.
    The Lesson Slides setting (MCK-289) snapshots a deadline onto each
    new class and does not read this value.
    """
    try:
        return max(1, int(os.getenv("CATCHUP_OPEN_DAYS") or str(CATCHUP_AUTO_CLOSE_DAYS_DEFAULT)))
    except ValueError:
        return CATCHUP_AUTO_CLOSE_DAYS_DEFAULT


def parse_auto_close_days(value: Any) -> int:
    """Return a whole-day auto-close window, or raise ``ValueError``.

    Args:
        value: Teacher input. Booleans are rejected. Numeric strings are
            accepted so a form field can post ``\"7\"``.

    Returns:
        An integer from 1 through 90.
    """
    if isinstance(value, bool):
        raise ValueError("Enter a whole number of days.")
    if isinstance(value, str):
        text = value.strip()
        if not text.isdigit():
            raise ValueError("Enter a whole number of days.")
        value = int(text)
    if isinstance(value, float) and not value.is_integer():
        raise ValueError("Enter a whole number of days.")
    try:
        days = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Enter a whole number of days.") from exc
    if days < CATCHUP_AUTO_CLOSE_DAYS_MIN or days > CATCHUP_AUTO_CLOSE_DAYS_MAX:
        raise ValueError("Enter a number from 1 to 90.")
    return days


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


def parse_class_instant(iso: str) -> datetime | None:
    """Parse a class timestamp as an aware Toronto instant.

    Naive values are Toronto wall time, matching the existing catch-up
    window. Offsets and ``Z`` are converted into Toronto.

    Args:
        iso: Stored timestamp.

    Returns:
        An aware datetime, or ``None`` when the text is empty or invalid.
    """
    text = str(iso or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=TORONTO)
    return dt.astimezone(TORONTO)


def format_class_date(iso: str) -> str:
    """Format an ISO timestamp as ``Wed Oct 14`` in Toronto."""
    dt = parse_class_instant(iso)
    if dt is None:
        return ""
    return dt.strftime("%a %b %-d")


def format_month_day(iso: str) -> str:
    """Format an ISO timestamp as ``Oct 26`` in Toronto.

    Args:
        iso: Stored timestamp.
    """
    dt = parse_class_instant(iso)
    if dt is None:
        return ""
    return dt.strftime("%b %-d")


def close_instant_iso(anchor_iso: str, days: int) -> str:
    """Deadline counted from a class end, as an aware Toronto ISO string.

    Args:
        anchor_iso: When the live class ended.
        days: Whole days the window stays open. Values below 1 become 1.
    """
    dt = parse_class_instant(anchor_iso)
    if dt is None:
        return ""
    close = (dt + timedelta(days=max(1, int(days)))).replace(microsecond=0)
    return close.isoformat(timespec="seconds")


def close_from_now(days: int, *, now: datetime | None = None) -> str:
    """Deadline counted from this moment, for a manual reopen.

    Args:
        days: Whole days the new window stays open.
        now: Clock override for tests. Naive values are Toronto.
    """
    current = now or datetime.now(tz=TORONTO)
    if current.tzinfo is None:
        current = current.replace(tzinfo=TORONTO)
    else:
        current = current.astimezone(TORONTO)
    close = (current.replace(microsecond=0) + timedelta(days=max(1, int(days))))
    return close.isoformat(timespec="seconds")


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
    """Last open day formatted for display from a class end plus the legacy window."""
    close = close_instant_iso(ended_at, catchup_open_days())
    return format_class_date(close)


def deadline_label(link: dict[str, Any], ended_at: str) -> str:
    """Weekday date students see for one catch-up deadline.

    A stored ``open_until`` wins. Rows from before MCK-289 fall back to
    the class end plus the legacy day count.

    Args:
        link: Catch-up row. May omit ``open_until``.
        ended_at: Class end timestamp used only for the legacy fallback.
    """
    stored = str(link.get("open_until") or "").strip()
    if stored:
        labeled = format_class_date(stored)
        if labeled:
            return labeled
    return open_until_date(ended_at)


def link_still_open(
    school: Any, link: dict[str, Any], *, now: datetime | None = None
) -> bool:
    """True when the link is open and still inside the open-until window.

    A stored ``open_until`` is the window. Reopening writes a new one, so
    moving the class end later does not stretch a closed window, and a
    manual reopen is not cut short by the original end. Rows with no
    stored deadline still use the class end plus ``catchup_open_days``.

    Args:
        school: ``SchoolDB`` instance. Used only for the legacy fallback.
        link: Catch-up row. ``is_open`` of 0 is always closed.
        now: Clock override for tests. Naive values are Toronto.
    """
    if not int(link.get("is_open") or 0):
        return False
    if not enforce_open_until():
        return True
    current = now or datetime.now(tz=TORONTO)
    if current.tzinfo is None:
        current = current.replace(tzinfo=TORONTO)
    else:
        current = current.astimezone(TORONTO)
    stored = str(link.get("open_until") or "").strip()
    if stored:
        close = parse_class_instant(stored)
        if close is None:
            return True
        return current <= close
    ended = school.ended_at_for_participation_run(
        int(link["class_id"]), str(link.get("run_key") or "")
    )
    if not ended:
        return True
    close = parse_class_instant(close_instant_iso(ended, catchup_open_days()))
    if close is None:
        return True
    return current <= close


def catchup_access_view(
    school: Any, link: dict[str, Any] | None, *, now: datetime | None = None
) -> dict[str, Any]:
    """Teacher column for one catch-up row. No codes, tokens, or URLs.

    Args:
        school: ``SchoolDB`` instance.
        link: Catch-up row, or ``None`` when this class has no catch-up
            (never ended, or Quit).
        now: Clock override for tests.

    Returns:
        ``catchup_access`` of ``open``, ``closed``, or ``none``, plus a
        display label and whether a Close or Reopen button is allowed.
    """
    if not link:
        return {
            "catchup_access": "none",
            "catchup_label": "none",
            "catchup_can_toggle": False,
        }
    if link_still_open(school, link, now=now):
        until = str(link.get("open_until") or "").strip()
        day = format_month_day(until)
        if not day:
            ended = school.ended_at_for_participation_run(
                int(link["class_id"]), str(link.get("run_key") or "")
            )
            long = open_until_date(ended)
            parts = long.split()
            day = " ".join(parts[1:]) if len(parts) >= 2 else ""
        label = f"Open until {day}" if day else "Open"
        return {
            "catchup_access": "open",
            "catchup_label": label,
            "catchup_can_toggle": True,
        }
    return {
        "catchup_access": "closed",
        "catchup_label": "Closed",
        "catchup_can_toggle": True,
    }


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
                "url": catchup_url_with_play(
                    school.catchup_canonical_path_for_link({**link, "class_id": cid})
                ),
                "open_until": deadline_label(link, ended),
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
        "open_until_line": COPY_OPEN_UNTIL.format(date=deadline_label(link, ended)),
        "url": catchup_url_with_play(
            school.catchup_canonical_path_for_link({**link, "class_id": cid})
        ),
    }


def signed_in_line(display_name: str) -> str:
    """``Signed in as Maya`` line."""
    return COPY_SIGNED_IN_AS.format(name=str(display_name or "").strip())

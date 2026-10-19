"""MCK-218 Formative-style group whiteboard helpers.

Member slot colours, roster order, and feature gating. Ink still lives in
board ops; this module only shapes metadata and colours for collab boards.
"""

from __future__ import annotations

import os
from typing import Any

# Contrast ≥3:1 on board fill #0f172a (spec).
FORMATIVE_MEMBER_PALETTE: tuple[str, ...] = (
    "#2dd4bf",
    "#f59e0b",
    "#a78bfa",
    "#f472b6",
    "#60a5fa",
    "#a3e635",
)


def formative_whiteboard_enabled() -> bool:
    """True when the formative whiteboard UI should load.

    Set ``LLOVES_FORMATIVE_WHITEBOARD=0`` to disable without a deploy.
    """
    return os.environ.get("LLOVES_FORMATIVE_WHITEBOARD", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def member_color(slot: int) -> str:
    """Colour for a member's join-order slot (0-based).

    A 7th+ member reuses the palette; clients draw dashed strokes.
    Ink on the board does not use this. ``student_ink_color`` does,
    so a late joiner cannot shift anyone else's colour.

    Args:
        slot: Join order index inside one board (``team:<sort_order>``).
    """
    if slot < 0:
        slot = 0
    return FORMATIVE_MEMBER_PALETTE[slot % len(FORMATIVE_MEMBER_PALETTE)]


def student_ink_color(student_id: Any) -> str:
    """Stable ink colour for one student, hashed from their id.

    The colour does not depend on join order or list position. A late
    joiner cannot shift anyone else. The palette is finite, so two
    students can share a colour. Stored strokes already use this same
    hash (``cursor_color_for``).

    Args:
        student_id: Roster id, or any other stable token.
    """
    from live_canvas import cursor_color_for

    return cursor_color_for(student_id)


def first_name_only(raw: str) -> str:
    """First token of a display name for chips and legend.

    Args:
        raw: Codename or full name from roster.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    return text.split()[0]


def formative_board_bucket(board_key: str) -> int | None:
    """Integer suffix of a ``team:<sort_order>`` board key, else None.

    ``0`` is the first named team (``team:0``), not a missing key.

    Args:
        board_key: Canonical board key (``team:0``, ``teacher``, ``shared``).
    """
    key = str(board_key or "")
    if not key.startswith("team:"):
        return None
    try:
        return int(key.split(":", 1)[1])
    except (TypeError, ValueError):
        return None


def roster_board_bucket(row: dict[str, Any]) -> int | None:
    """Colour-sequence key for one attendee row.

    ``team_sort_order`` wins when the row carries it, including ``0``
    (the first named team). ``team_id`` is only the fallback when the
    row has no sort order. Callers must not substitute with ``or``:
    ``0`` is a real board.

    Args:
        row: Attendee row from ``list_live_session_team_members``.
    """
    if "team_sort_order" in row:
        raw_order = row.get("team_sort_order")
        if raw_order is not None and raw_order != "":
            try:
                return int(raw_order)
            except (TypeError, ValueError):
                return None
    raw_team = row.get("team_id")
    if raw_team is None or raw_team == "":
        return None
    try:
        return int(raw_team)
    except (TypeError, ValueError):
        return None


def formative_membership_token(row: dict[str, Any]) -> str:
    """Cache token for one roster row: id, leave time, and colour bucket.

    The bucket is ``team_sort_order`` when present, including ``0``.
    Leave fingerprints built from ``live_board_assignments`` use this
    same token so a ``left_at`` change drops the student from the legend.

    Args:
        row: Attendee or assignment row with ``student_id`` and ``left_at``.
    """
    bucket = roster_board_bucket(row)
    mark = "" if bucket is None else str(bucket)
    left = row.get("left_at")
    left_token = "" if left is None else str(left)
    return f"{row.get('student_id')}:{left_token}:{mark}"


def resolve_formative_bucket(
    school: Any,
    session_id: int,
    *,
    board_key: str,
    student_id: int | None,
) -> int | None:
    """Sort-order suffix for this read, or None for every team.

    A ``team:<sort_order>`` key scopes to that board, and ``0`` is the
    first named team. The teacher board stays session-wide so its legend
    matches each team view. A student read without a team suffix uses
    ``student_live_board_key`` (the assignment pin, then the game sort
    order).

    Args:
        school: Open ``SchoolDB``.
        session_id: ``live_class_sessions.id``.
        board_key: Canonical board key being read.
        student_id: Viewer roster id when a student.
    """
    bucket = formative_board_bucket(board_key)
    if bucket is not None:
        return bucket
    if str(board_key or "") == "teacher":
        return None
    if student_id in (None, ""):
        return None
    try:
        pinned = school.student_live_board_key(int(session_id), int(student_id))
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    return formative_board_bucket(str(pinned or ""))


def formative_slot_map_from_rows(
    rows: list[dict[str, Any]],
) -> dict[int, int]:
    """Join-order colour slots inside each board bucket.

    Leavers keep a slot. The next joiner gets the lowest index that no
    current or departed teammate in that bucket already holds. The
    bucket is ``roster_board_bucket`` (``team_sort_order``, including
    ``0``).

    Args:
        rows: Attendee rows already in join order (``joined_at``, then id).

    Returns:
        ``student_id`` → 0-based slot within the student's board.
    """
    used: dict[Any, set[int]] = {}
    out: dict[int, int] = {}
    for row in rows:
        sid = row.get("student_id")
        if sid in (None, ""):
            continue
        try:
            sid_n = int(sid)
        except (TypeError, ValueError):
            continue
        if sid_n in out:
            continue
        key = roster_board_bucket(row)
        taken = used.setdefault(key, set())
        slot = 0
        while slot in taken:
            slot += 1
        taken.add(slot)
        out[sid_n] = slot
    return out


def formative_session_slot_map(
    school: Any,
    session_id: int,
) -> dict[int, int]:
    """Map roster id to a colour slot inside that student's board.

    Slots include students who have left so remaining chip colours stay
    stable. Boards are ``team:<sort_order>``. Two boards can reuse the
    same slot; teammates cannot until the palette and dash pattern both
    wrap (12 people).

    Args:
        school: Open ``SchoolDB``.
        session_id: ``live_class_sessions.id``.

    Returns:
        ``student_id`` → 0-based slot within the student's board.
    """
    rows = school.list_live_session_team_members(int(session_id), None)
    return formative_slot_map_from_rows(rows)


def formative_board_members(
    school: Any,
    session_id: int,
    team_sort_order: int | None,
    *,
    rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Members on one ``team:<sort_order>`` board in stable join order.

    ``team_sort_order`` selects which roster to load. ``0`` is the first
    named team and must be passed through; only None loads every team.
    Colour slots come from ``roster_board_bucket`` and still decide the
    dash pattern. The ink colour is ``student_ink_color`` (the student
    id), not the slot, so a late joiner cannot shift anyone else's
    colour. Pass ``rows`` when the caller already loaded that scope,
    including leavers.

    Args:
        school: Open ``SchoolDB``.
        session_id: ``live_class_sessions.id``.
        team_sort_order: Board suffix, or None for every team.
        rows: Join-ordered attendee rows for this scope, including leavers.

    Returns:
        List of ``{id, name, color, slot, dashed}`` dicts.
    """
    if rows is None:
        load_order = None if team_sort_order is None else int(team_sort_order)
        rows = school.list_live_session_team_members(int(session_id), load_order)
    slot_map = formative_slot_map_from_rows(rows)
    out: list[dict[str, Any]] = []
    for row in rows:
        sid = row.get("student_id")
        if sid in (None, ""):
            continue
        if row.get("left_at"):
            continue
        slot = int(slot_map.get(int(sid), 0))
        name = first_name_only(str(row.get("codename") or row.get("name") or sid))
        out.append(
            {
                "id": str(int(sid)),
                "name": name,
                "color": student_ink_color(sid),
                "slot": slot,
                "dashed": slot >= len(FORMATIVE_MEMBER_PALETTE),
            }
        )
    return out


def formative_meta_for_board(
    school: Any,
    session_id: int,
    *,
    student_id: int | None = None,
    as_teacher: bool = False,
    board_key: str = "",
    member_rows: list[dict[str, Any]] | None = None,
    bucket: int | None = None,
) -> dict[str, Any] | None:
    """Optional formative payload for a board read.

    Args:
        school: Open ``SchoolDB``.
        session_id: Live session id.
        student_id: Viewer roster id when a student.
        as_teacher: Staff viewer.
        board_key: Canonical board key being read.
        member_rows: Join-ordered rows already loaded for this board.
            When set, ``bucket`` is that ``team:<sort_order>`` scope and
            the roster is not listed again. None lets this function load
            the bucket. ``0`` is a real board.
        bucket: Sort-order suffix matching ``member_rows``, or None for
            the session legend. Ignored when ``member_rows`` is None.

    Returns:
        Dict with ``members`` and teacher toggles, or None when disabled.
    """
    if not formative_whiteboard_enabled():
        return None
    if not school._whiteboard_collab_active(int(session_id)):
        return None
    teacher = school.live_session_teacher_state_payload(int(session_id))
    if member_rows is None:
        team_sort_order = resolve_formative_bucket(
            school,
            int(session_id),
            board_key=str(board_key or ""),
            student_id=student_id,
        )
        members = formative_board_members(school, int(session_id), team_sort_order)
    else:
        members = formative_board_members(
            school,
            int(session_id),
            bucket,
            rows=member_rows,
        )
    return {
        "members": members,
        "always_show_names": True,
        "projector_show_names": False,
        "viewer_is_teacher": bool(as_teacher),
    }

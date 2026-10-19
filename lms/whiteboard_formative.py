"""MCK-218 Formative-style group whiteboard helpers.

Member slot colours, roster order, and feature gating. Ink still lives in
board ops; this module only shapes metadata and colours for collab boards.
"""

from __future__ import annotations

import json
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
    ids can hash to the same colour. A board legend uses
    ``assign_board_colors`` to bump that clash. Stored strokes are
    not rewritten.

    Args:
        student_id: Roster id, or any other stable token.
    """
    from live_canvas import cursor_color_for

    return cursor_color_for(student_id)


def assign_board_colors(student_ids: list[Any]) -> dict[str, str]:
    """Stable unique ink colours for one board, in the caller's order.

    Each id starts at ``cursor_color_for``. When that colour is already
    claimed by an earlier id, the next free palette entry is used,
    walking forward from the hash. A later id bumps. Earlier ids stay
    put, including leavers who are only here so a departure does not
    free their colour. More people than palette entries must reuse the
    hash. Nothing is written to stored strokes.

    Args:
        student_ids: Join-ordered ids for this board, including leavers.

    Returns:
        Map of id string to a hex colour.
    """
    from live_canvas import CURSOR_COLORS, cursor_color_for

    palette = tuple(CURSOR_COLORS)
    size = len(palette)
    if size == 0:
        return {}
    taken: set[str] = set()
    out: dict[str, str] = {}
    for raw in student_ids:
        if raw in (None, ""):
            continue
        try:
            sid = str(int(raw))
        except (TypeError, ValueError):
            sid = str(raw).strip()
        if not sid or sid in out:
            continue
        preferred = cursor_color_for(sid)
        try:
            start = palette.index(preferred)
        except ValueError:
            start = 0
            preferred = palette[0]
        colour = preferred
        if colour in taken:
            for step in range(1, size):
                candidate = palette[(start + step) % size]
                if candidate not in taken:
                    colour = candidate
                    break
        out[sid] = colour
        if len(taken) < size:
            taken.add(colour)
    return out


def drawing_owner_ids(rows: list[dict[str, Any]] | None) -> set[str]:
    """Student ids who added a stroke or non-empty text on these ops.

    Teacher rows are ignored. An empty text label does not count.
    The op may be a dict or a JSON string. Rows are not rewritten.

    Args:
        rows: ``board_ops`` rows for one board.

    Returns:
        Student id strings.
    """
    owners: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        op = row.get("op")
        if isinstance(op, str):
            try:
                op = json.loads(op)
            except (TypeError, ValueError):
                op = None
        if not isinstance(op, dict):
            op = row if row.get("type") else None
        if not isinstance(op, dict):
            continue
        kind = str(op.get("type") or "")
        who = str(row.get("owner") or op.get("owner") or "").strip()
        if not who or who == "teacher" or not who.isdigit():
            continue
        drew = kind in {"stroke_add", "pts_append"} or (
            kind == "text_upsert" and bool(str(op.get("text") or "").strip())
        )
        if drew:
            owners.add(str(int(who)))
    return owners


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
    keep_left: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Members on one ``team:<sort_order>`` board in stable join order.

    ``team_sort_order`` selects which roster to load. ``0`` is the first
    named team and must be passed through; only None loads every team.
    Colour slots come from ``roster_board_bucket`` and still decide the
    dash pattern. Ink colour is ``assign_board_colors`` over the full
    join order, including leavers, so a late joiner or a departure
    cannot shift anyone else's colour. A leaver is listed only when
    their id is in ``keep_left`` (they drew on this board). Pass
    ``rows`` when the caller already loaded that scope, including leavers.

    Args:
        school: Open ``SchoolDB``.
        session_id: ``live_class_sessions.id``.
        team_sort_order: Board suffix, or None for every team.
        rows: Join-ordered attendee rows for this scope, including leavers.
        keep_left: Student ids who drew and should stay after leaving.

    Returns:
        List of ``{id, name, color, slot, dashed}`` dicts.
    """
    if rows is None:
        load_order = None if team_sort_order is None else int(team_sort_order)
        rows = school.list_live_session_team_members(int(session_id), load_order)
    slot_map = formative_slot_map_from_rows(rows)
    ordered: list[int] = []
    for row in rows:
        sid = row.get("student_id")
        if sid in (None, ""):
            continue
        try:
            ordered.append(int(sid))
        except (TypeError, ValueError):
            continue
    colors = assign_board_colors(ordered)
    staying = {str(item) for item in (keep_left or set())}
    out: list[dict[str, Any]] = []
    for row in rows:
        sid = row.get("student_id")
        if sid in (None, ""):
            continue
        sid_s = str(int(sid))
        if row.get("left_at") and sid_s not in staying:
            continue
        slot = int(slot_map.get(int(sid), 0))
        name = first_name_only(str(row.get("codename") or row.get("name") or sid))
        out.append(
            {
                "id": sid_s,
                "name": name,
                "color": colors.get(sid_s) or student_ink_color(sid),
                "slot": slot,
                "dashed": slot >= len(FORMATIVE_MEMBER_PALETTE),
            }
        )
    return out


def legend_member_rows(
    school: Any,
    session_id: int,
    team_sort_order: int | None,
    *,
    board_key: str = "",
    rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Legend members for one board, including leavers who drew on it.

    Colour claims use the full join order, including leavers who are
    not shown, so a departure does not move a teammate's colour. The
    teacher board does not scan ink. A leaver with no stroke or
    non-empty text on this board stays off the legend. Board ops are
    read only when some row has already left.

    Args:
        school: Open ``SchoolDB``.
        session_id: ``live_class_sessions.id``.
        team_sort_order: Board suffix, or None to load every team.
        board_key: ``team:<n>``, ``solo:<id>``, or ``teacher``.
        rows: Join-ordered rows already loaded for this scope.

    Returns:
        ``formative_board_members`` rows for the legend.
    """
    key = str(board_key or "")
    if rows is None:
        load_order = None if team_sort_order is None else int(team_sort_order)
        loaded = school.list_live_session_team_members(int(session_id), load_order)
        rows = list(loaded or [])
    else:
        rows = list(rows)
    if key.startswith("solo:"):
        suffix = key.split(":", 1)[1]
        rows = [row for row in rows if str(row.get("student_id") or "") == str(suffix)]
    keep: set[str] = set()
    if key.startswith(("team:", "solo:")) and any(row.get("left_at") for row in rows):
        try:
            stored = school._board_rows(int(session_id), key)
        except (AttributeError, KeyError, TypeError, ValueError):
            stored = []
        keep = drawing_owner_ids(stored)
    scope = None if team_sort_order is None else int(team_sort_order)
    return formative_board_members(
        school,
        int(session_id),
        scope,
        rows=rows,
        keep_left=keep,
    )


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
    key = str(board_key or "")
    if member_rows is None:
        team_sort_order = resolve_formative_bucket(
            school,
            int(session_id),
            board_key=key,
            student_id=student_id,
        )
        members = legend_member_rows(
            school, int(session_id), team_sort_order, board_key=key
        )
    else:
        members = legend_member_rows(
            school,
            int(session_id),
            bucket,
            board_key=key,
            rows=member_rows,
        )
    return {
        "members": members,
        "always_show_names": True,
        "projector_show_names": False,
        "viewer_is_teacher": bool(as_teacher),
    }

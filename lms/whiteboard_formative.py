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

    Args:
        slot: Join order index for the session team roster.
    """
    if slot < 0:
        slot = 0
    return FORMATIVE_MEMBER_PALETTE[slot % len(FORMATIVE_MEMBER_PALETTE)]


def first_name_only(raw: str) -> str:
    """First token of a display name for chips and legend.

    Args:
        raw: Codename or full name from roster.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    return text.split()[0]


def _members_for_team_sort_order(
    school: Any,
    session_id: int,
    team_sort_order: int,
) -> list[dict[str, Any]]:
    """Roster rows on one named team bucket (``team:<sort_order>``).

    Args:
        school: Open ``SchoolDB``.
        session_id: ``live_class_sessions.id``.
        team_sort_order: ``game_teams.sort_order`` for the board key.
    """
    session_row = school.get_live_session(int(session_id))
    if session_row is None:
        return []
    class_id = int(session_row["class_id"])
    try:
        index = school.game.team_membership_index(class_id)
    except (TypeError, ValueError, AttributeError):
        index = {}
    by_student = (index.get("by_student") if isinstance(index, dict) else None) or {}
    rows = school.list_live_session_team_members(int(session_id), None)
    matched: list[dict[str, Any]] = []
    for row in rows:
        sid = row.get("student_id")
        if sid in (None, ""):
            continue
        tid = by_student.get(int(sid))
        if tid in (None, ""):
            continue
        sort_order = school._team_sort_order_for_team_id(class_id, int(tid))
        if sort_order != int(team_sort_order):
            continue
        matched.append(row)
    return matched


def formative_board_members(
    school: Any,
    session_id: int,
    team_sort_order: int | None,
) -> list[dict[str, Any]]:
    """Members on one team board in stable join order.

    Args:
        school: Open ``SchoolDB``.
        session_id: ``live_class_sessions.id``.
        team_sort_order: Named team sort order for the board key, or None
            for the all-teams legend.

    Returns:
        List of ``{id, name, color, slot, dashed}`` dicts.
    """
    if team_sort_order is None:
        rows = school.list_live_session_team_members(int(session_id), None)
    else:
        rows = _members_for_team_sort_order(school, int(session_id), int(team_sort_order))
    out: list[dict[str, Any]] = []
    for slot, row in enumerate(rows):
        sid = row.get("student_id")
        if sid in (None, ""):
            continue
        if row.get("left_at"):
            continue
        name = first_name_only(str(row.get("codename") or row.get("name") or sid))
        out.append(
            {
                "id": str(int(sid)),
                "name": name,
                "color": member_color(slot),
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
) -> dict[str, Any] | None:
    """Optional formative payload for a board read.

    Args:
        school: Open ``SchoolDB``.
        session_id: Live session id.
        student_id: Viewer roster id when a student.
        as_teacher: Staff viewer.
        board_key: Canonical board key being read.

    Returns:
        Dict with ``members`` and teacher toggles, or None when disabled.
    """
    if not formative_whiteboard_enabled():
        return None
    if not school._whiteboard_collab_active(int(session_id)):
        return None
    teacher = school.live_session_teacher_state_payload(int(session_id))
    team_sort_order: int | None = None
    key = str(board_key or "")
    if key.startswith("team:"):
        try:
            team_sort_order = int(key.split(":", 1)[1])
        except (TypeError, ValueError):
            team_sort_order = None
    elif student_id not in (None, ""):
        session_row = school.get_live_session(int(session_id))
        if session_row:
            collab = school.student_live_board_key(int(session_id), int(student_id))
            if collab.startswith("team:"):
                try:
                    team_sort_order = int(collab.split(":", 1)[1])
                except (TypeError, ValueError):
                    team_sort_order = None
    members = formative_board_members(school, int(session_id), team_sort_order)
    return {
        "members": members,
        "always_show_names": bool(teacher.get("whiteboard_always_show_names")),
        "projector_show_names": bool(teacher.get("whiteboard_projector_names")),
        "viewer_is_teacher": bool(as_teacher),
    }

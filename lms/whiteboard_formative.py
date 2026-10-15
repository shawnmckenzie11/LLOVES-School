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


def formative_board_members(
    school: Any,
    session_id: int,
    team_id: int | None,
) -> list[dict[str, Any]]:
    """Members on one team board in stable join order.

    Args:
        school: Open ``SchoolDB``.
        session_id: ``live_class_sessions.id``.
        team_id: Game-show team id, or None for shared bucket.

    Returns:
        List of ``{id, name, color, slot, dashed}`` dicts.
    """
    rows = school.list_live_session_team_members(int(session_id), team_id)
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
    team_id: int | None = None
    key = str(board_key or "")
    if key.startswith("team:"):
        try:
            team_id = int(key.split(":", 1)[1])
        except (TypeError, ValueError):
            team_id = None
    elif student_id not in (None, ""):
        class_row = school.get_live_session(int(session_id))
        if class_row:
            team_id = school.student_team_id_for_class(
                int(class_row["class_id"]), int(student_id)
            )
    members = formative_board_members(school, int(session_id), team_id)
    return {
        "members": members,
        "always_show_names": bool(teacher.get("whiteboard_always_show_names")),
        "projector_show_names": bool(teacher.get("whiteboard_projector_names")),
        "viewer_is_teacher": bool(as_teacher),
    }

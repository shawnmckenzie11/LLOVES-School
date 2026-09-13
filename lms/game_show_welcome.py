"""Student TEAMS welcome card: VLC Math Game Show intro copy."""

from __future__ import annotations

from typing import Any

GAME_SHOW_TITLE = "VLC Math Game Show"

GAME_SHOW_ROUNDS: tuple[dict[str, str], ...] = (
    {
        "kind": "open",
        "title": "Open Question Round",
        "blurb": "The class works one question together. Ideas score as they land.",
    },
    {
        "kind": "challenge",
        "title": "Team Challenge Round",
        "blurb": "Your team takes on a richer problem. Teamwork and look-fors count.",
    },
    {
        "kind": "consolidation",
        "title": "Consolidation Round",
        "blurb": "Lock in today’s big idea and check that it stuck.",
    },
)


def game_show_welcome_payload(
    *,
    class_code: str,
    lesson_code: str,
    participants: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return the student TEAMS welcome payload.

    Args:
        class_code: Course section code (for example ``MCF3M``).
        lesson_code: Teacher-selected module and live slot (``M1-C1``).
        participants: Present attendees with ``codename`` and ``character``.
    """
    return {
        "title": GAME_SHOW_TITLE,
        "class_code": str(class_code or "").strip(),
        "lesson_code": str(lesson_code or "").strip(),
        "rounds": [dict(row) for row in GAME_SHOW_ROUNDS],
        "participants": list(participants),
    }

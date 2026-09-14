"""Student TEAMS welcome card: VLC Math Game Show intro copy."""

from __future__ import annotations

from typing import Any

GAME_SHOW_TITLE = "VLC Math Game Show"

GAME_SHOW_ROUNDS: tuple[dict[str, str], ...] = (
    {
        "kind": "open",
        "title": "You Lead the Way",
        "blurb": (
            "Ask any questions you have about the module lesson work you've "
            "completed: unclear topics, processes, or homework problems."
        ),
    },
    {
        "kind": "challenge",
        "title": "Team Challenge",
        "blurb": (
            "Work with your team to share ideas, try things out, problem "
            "solve, and collaborate"
        ),
    },
    {
        "kind": "consolidation",
        "title": "Test-style practice + feedback",
        "blurb": (
            "Check in to see if you've picked anything up from today's lesson "
            "related to group work or specific topics discussed"
        ),
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

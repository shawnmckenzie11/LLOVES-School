"""Student TEAMS welcome card: VLC Math Game Show intro copy."""

from __future__ import annotations

from typing import Any

GAME_SHOW_TITLE = "VLC Math Game Show"

GAME_SHOW_ROUNDS: tuple[dict[str, str], ...] = (
    {
        "kind": "open",
        "title": "You Lead the Way",
        "blurb": (
            "Every class begins with 10–30 minutes of open questions, like a "
            "traditional open office. This is the time to ask about the "
            "content, have module ideas explained, or get help with homework."
        ),
    },
    {
        "kind": "challenge",
        "title": "Team Challenge",
        "blurb": (
            "You will work with your group on one challenge that uses this "
            "module's math and your ability to collaborate, model, and "
            "problem-solve — not just to get the right answer, but to spot "
            "connections that are hard to see alone. You finish by documenting "
            "and sharing what you took from the group and how your thinking "
            "sharpened; that write-up is the foundation of the portfolio."
        ),
    },
    {
        "kind": "consolidation",
        "title": "Test-style practice + feedback",
        "blurb": (
            "We finish every class with a typical test-style question so you "
            "get specific, timely feedback. That consolidates the processes "
            "from the async lessons and supports solving the team challenge."
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

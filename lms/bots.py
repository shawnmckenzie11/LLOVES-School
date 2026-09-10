"""Staff-facing Grok bot showcase cards.

This directory describes bots Shawn uses to engineer ALC challenge-led
modules. It is not a student surface and must not contain Ministry
expectation wording or contest stems — add new cards here, not in
templates.
"""

from __future__ import annotations

from typing import Any

# Featured cards sort first. Append a real bot or another placeholder slot.
BOTS: list[dict[str, Any]] = [
    {
        "slug": "module-engineer",
        "name": "Module Engineer",
        "aka": "dr eggbot",
        "role": "Lead / Module Engineer for ALC Math challenge-led modules",
        "focus": [
            "Live-class team challenges (contest and team questions)",
            "Teacher-facing live lesson notes and lesson-plan check-ins",
            "Tying module lesson ideas to C1→C3 storylines",
        ],
        "perspectives": ["Student storyline", "Teacher storyline"],
        "status": "active",
        "featured": True,
        "note": "Staff pick this round — quietly keeping the module shop running.",
        "placeholder": False,
    },
    {
        "slug": None,
        "name": "Next bot",
        "hint": "Open slot. Add a card in lms/bots.py.",
        "placeholder": True,
        "featured": False,
    },
    {
        "slug": None,
        "name": "Next bot",
        "hint": "Open slot. Add a card in lms/bots.py.",
        "placeholder": True,
        "featured": False,
    },
]


def list_bots() -> list[dict[str, Any]]:
    """Return showcase cards with featured entries first.

    Returns:
        A new list of bot dicts in display order.
    """
    return sorted(
        BOTS,
        key=lambda bot: 0 if bot.get("featured") and not bot.get("placeholder") else 1,
    )

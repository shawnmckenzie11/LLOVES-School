"""Waiting-room meet-math: ephemeral Minds On check-in.

Shown after students join a live session, before Generate teams / Team
Challenge. Never the Team Challenge stem. Cleared (or suppressed) when
scoring starts or challenge media mounts. Not a gradebook writeback.
"""

from __future__ import annotations

from typing import Any

MEET_MATH_ITEM_ID = "meet-math"
MEET_MATH_PACK_ID = "waiting-room"
MEET_MATH_SLIDE_INDEX = 800
MEET_MATH_KIND = "mc"
MEET_MATH_LABEL = "Minds On"

WAITING_ROOM_WAIT_LINE = "Waiting room — class is about to begin."

# M1C1 (first live of the module) uses a prior-module linear-rate item.
MEET_MATH_PROMPT = (
    "A line has constant rate of change. Which best matches that?"
)
MEET_MATH_CHOICES: tuple[str, ...] = (
    "Every step up adds the same amount",
    "The graph curves",
    "Second differences are constant",
    "Not sure",
)


def meet_math_prompt_payload() -> dict[str, Any]:
    """Student-facing MC payload for the waiting-room skills check-in.

    Returns:
        Live-prompt payload with ``item_id`` ``meet-math``.
    """
    return {
        "pack": MEET_MATH_PACK_ID,
        "item_id": MEET_MATH_ITEM_ID,
        "label": MEET_MATH_LABEL,
        "prompt": MEET_MATH_PROMPT,
        "choices": list(MEET_MATH_CHOICES),
    }


def is_meet_math_payload(payload: Any) -> bool:
    """True when a live-prompt payload is the waiting-room meet-math MC.

    Args:
        payload: Prompt JSON object.
    """
    if not isinstance(payload, dict):
        return False
    item_id = str(payload.get("item_id") or "").strip().lower()
    pack = str(payload.get("pack") or "").strip().lower()
    return item_id == MEET_MATH_ITEM_ID or pack == MEET_MATH_PACK_ID

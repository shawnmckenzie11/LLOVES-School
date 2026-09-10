"""Waiting-room Minds-On question: ephemeral skills check-in.

Shown after students join a live session, before Generate teams / Team
Challenge. Course · module · lesson # item (prior 1–2 classes, or the
prior-module main idea when this is the first live of a module). Never
the Team Challenge stem. Cleared (or suppressed) when scoring starts or
challenge media mounts. Not a gradebook writeback. No curriculum chips.
"""

from __future__ import annotations

from typing import Any

MINDS_ON_ITEM_ID = "minds_on"
MINDS_ON_PACK_ID = "minds_on"
MINDS_ON_SLIDE_INDEX = 800
MINDS_ON_KIND = "mc"
MINDS_ON_LABEL = "Minds-On"

# Prior seed ids so an already-open live session still clears on Team Challenge.
_LEGACY_ITEM_IDS = frozenset({MINDS_ON_ITEM_ID, "meet-math", "minds-on"})
_LEGACY_PACK_IDS = frozenset({MINDS_ON_PACK_ID, "waiting-room"})

WAITING_ROOM_WAIT_LINE = "Waiting room — class is about to begin."

# M1C1 (first live of the module) uses a prior-module linear-rate item.
MINDS_ON_PROMPT = (
    "A line has constant rate of change. Which best matches that?"
)
MINDS_ON_CHOICES: tuple[str, ...] = (
    "Every step up adds the same amount",
    "The graph curves",
    "Second differences are constant",
    "Not sure",
)


def minds_on_prompt_payload() -> dict[str, Any]:
    """Student-facing MC payload for the waiting-room Minds-On question.

    Returns:
        Live-prompt payload with ``item_id`` ``minds_on``.
    """
    return {
        "pack": MINDS_ON_PACK_ID,
        "item_id": MINDS_ON_ITEM_ID,
        "label": MINDS_ON_LABEL,
        "prompt": MINDS_ON_PROMPT,
        "choices": list(MINDS_ON_CHOICES),
    }


def is_minds_on_payload(payload: Any) -> bool:
    """True when a live-prompt payload is the waiting-room Minds-On question.

    Args:
        payload: Prompt JSON object.
    """
    if not isinstance(payload, dict):
        return False
    item_id = str(payload.get("item_id") or "").strip().lower()
    pack = str(payload.get("pack") or "").strip().lower()
    return item_id in _LEGACY_ITEM_IDS or pack in _LEGACY_PACK_IDS

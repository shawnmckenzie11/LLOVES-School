"""Waiting-room Minds-On question: ephemeral skills check-in.

Shown after students join a live session, before Generate teams / Team
Challenge. Course · module · lesson # item (prior 1–2 classes, or the
prior-module main idea when this is the first live of a module). Never
the Team Challenge stem. Cleared (or suppressed) when scoring starts or
challenge media mounts. Not a gradebook writeback. No curriculum chips.
"""

from __future__ import annotations

from typing import Any

try:
    from quick_hitter import (
        CLEAR_ON_TEAM_CHALLENGE,
        QUICK_HITTER_ARTIFACT_ID,
        RIDE_CONS,
        RIDE_MINDS_ON,
        quick_hitter_packaging,
    )
except ImportError:  # ``python3 lms/app.py`` package import
    from lms.quick_hitter import (
        CLEAR_ON_TEAM_CHALLENGE,
        QUICK_HITTER_ARTIFACT_ID,
        RIDE_CONS,
        RIDE_MINDS_ON,
        quick_hitter_packaging,
    )

MINDS_ON_ITEM_ID = "minds_on"
MINDS_ON_PACK_ID = "minds_on"
MINDS_ON_SLIDE_INDEX = 800
MINDS_ON_KIND = "mc"
MINDS_ON_LABEL = "Minds-On"

# Prior seed ids so an already-open live session still clears on Team Challenge.
_LEGACY_ITEM_IDS = frozenset({MINDS_ON_ITEM_ID, "meet-math", "minds-on"})
_LEGACY_PACK_IDS = frozenset({MINDS_ON_PACK_ID, "waiting-room"})

WAITING_ROOM_WAIT_LINE = "Waiting room — class is about to begin."

# Authoritative student-copywriter final (MCF3M M1C1 waiting-room).
MINDS_ON_BRIEF_PATH = (
    "catalogue/challenges/module-briefs/minds-on/MCF3M-M1-C1-minds-on-student.md"
)
MINDS_ON_PROMPT = (
    "A straight-line graph has a **constant rate of change**. "
    "Which statement best matches that?"
)
MINDS_ON_CHOICES: tuple[str, ...] = (
    "Every equal step across adds the same amount up (or down)",
    "The graph curves",
    "Second differences in a table are constant",
    "Not sure",
)
# Soft key is teacher-only; student_live_prompt_payload must strip it.
MINDS_ON_KEY = "A"


def minds_on_prompt_payload() -> dict[str, Any]:
    """MC payload for the waiting-room Minds-On question.

    Includes teacher-only ``key``. Student APIs must strip it before send.

    Returns:
        Live-prompt payload with ``item_id`` ``minds_on`` on the
        ``quick-hitter-question-chain`` artifact (usually one item).
    """
    payload = quick_hitter_packaging(
        ride=RIDE_MINDS_ON,
        chain_index=1,
        chain_length=1,
        ephemeral=True,
        durable_store=False,
        clear_on=CLEAR_ON_TEAM_CHALLENGE,
    )
    payload.update(
        {
            "pack": MINDS_ON_PACK_ID,
            "item_id": MINDS_ON_ITEM_ID,
            "label": MINDS_ON_LABEL,
            "prompt": MINDS_ON_PROMPT,
            "choices": list(MINDS_ON_CHOICES),
            "key": MINDS_ON_KEY,
        }
    )
    return payload


def is_minds_on_payload(payload: Any) -> bool:
    """True when a live-prompt payload is the waiting-room Minds-On question.

    Args:
        payload: Prompt JSON object.
    """
    if not isinstance(payload, dict):
        return False
    artifact = str(payload.get("artifact_id") or "").strip()
    ride = str(payload.get("ride") or "").strip().lower()
    if artifact == QUICK_HITTER_ARTIFACT_ID:
        if ride == RIDE_CONS:
            return False
        if ride == RIDE_MINDS_ON:
            return True
    item_id = str(payload.get("item_id") or "").strip().lower()
    pack = str(payload.get("pack") or "").strip().lower()
    return item_id in _LEGACY_ITEM_IDS or pack in _LEGACY_PACK_IDS

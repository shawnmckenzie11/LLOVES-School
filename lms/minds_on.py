"""Waiting-room Minds-On question: one ephemeral MC, not a chain.

Shown after students join a live session, before Generate teams / Team
Challenge. Course · module · lesson # item (prior 1–2 classes, or the
prior-module main idea when this is the first live of a module). Never
the Team Challenge stem. Cleared (or suppressed) when scoring starts or
challenge media mounts. Not a gradebook writeback. No curriculum chips.
``items`` length is 1 — do not build a Minds-On carousel. CONS is the
multi-item ``quick-hitter-question-chain`` ride.

Packs resolve by live slot ``C1`` / ``C2`` / ``C3``. Unknown slots fall
back to C1 so existing smoke sessions keep the linear-rate check-in.
"""

from __future__ import annotations

import re
from typing import Any

try:
    from quick_hitter import (
        CLEAR_ON_TEAM_CHALLENGE,
        QUICK_HITTER_ARTIFACT_ID,
        RIDE_CONS,
        RIDE_MEET_TEAM,
        RIDE_MINDS_ON,
        quick_hitter_packaging,
    )
except ImportError:  # ``python3 lms/app.py`` package import
    from lms.quick_hitter import (
        CLEAR_ON_TEAM_CHALLENGE,
        QUICK_HITTER_ARTIFACT_ID,
        RIDE_CONS,
        RIDE_MEET_TEAM,
        RIDE_MINDS_ON,
        quick_hitter_packaging,
    )

MINDS_ON_ITEM_ID = "minds_on"
MINDS_ON_PACK_ID = "minds_on"
MINDS_ON_SLIDE_INDEX = 800
MINDS_ON_KIND = "mc"
MINDS_ON_LABEL = "Minds-On"
DEFAULT_LIVE_SLOT = "C1"
LIVE_SLOTS: tuple[str, ...] = ("C1", "C2", "C3")

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

MINDS_ON_C2_BRIEF_PATH = (
    "catalogue/challenges/module-briefs/minds-on/MCF3M-M1-C2-minds-on-student.md"
)
MINDS_ON_C2_PROMPT = (
    "Looking at y = x^2, which claim is **forced** by the picture?"
)
MINDS_ON_C2_CHOICES: tuple[str, ...] = (
    "a > 0 (it opens upward)",
    "a < 0 (it opens downward)",
    "The graph is a straight line",
    "Not sure",
)
MINDS_ON_C2_KEY = "A"

MINDS_ON_C3_BRIEF_PATH = (
    "catalogue/challenges/module-briefs/minds-on/MCF3M-M1-C3-minds-on-student.md"
)
MINDS_ON_C3_PROMPT = (
    "A graph of y = x^2 has been moved so it still passes through a marked "
    "point. Which claim is safest?"
)
MINDS_ON_C3_CHOICES: tuple[str, ...] = (
    "Every parameter a, h, and k is frozen by the point alone",
    "The point links the parameters — some stay free",
    "Domain and range are always all real numbers",
    "Not sure",
)
MINDS_ON_C3_KEY = "B"

_MINDS_ON_PACKS: dict[str, dict[str, Any]] = {
    "C1": {
        "brief_path": MINDS_ON_BRIEF_PATH,
        "prompt": MINDS_ON_PROMPT,
        "choices": MINDS_ON_CHOICES,
        "key": MINDS_ON_KEY,
        "feedback_id": "minds_on",
    },
    "C2": {
        "brief_path": MINDS_ON_C2_BRIEF_PATH,
        "prompt": MINDS_ON_C2_PROMPT,
        "choices": MINDS_ON_C2_CHOICES,
        "key": MINDS_ON_C2_KEY,
        "feedback_id": "C2-minds_on",
    },
    "C3": {
        "brief_path": MINDS_ON_C3_BRIEF_PATH,
        "prompt": MINDS_ON_C3_PROMPT,
        "choices": MINDS_ON_C3_CHOICES,
        "key": MINDS_ON_C3_KEY,
        "feedback_id": "C3-minds_on",
    },
}


def normalize_live_slot(raw: Any) -> str:
    """Return ``C1``, ``C2``, or ``C3``. Unknown values fall back to C1.

    Args:
        raw: Posted or stored live-slot / challenge id.
    """
    text = str(raw or "").strip().upper()
    if text in LIVE_SLOTS:
        return text
    return DEFAULT_LIVE_SLOT


def minds_on_pack(live_slot: Any = None) -> dict[str, Any]:
    """Return the waiting-room Minds-On pack for one live slot.

    Args:
        live_slot: ``C1`` / ``C2`` / ``C3``. Defaults to C1.
    """
    slot = normalize_live_slot(live_slot)
    return dict(_MINDS_ON_PACKS[slot])


def minds_on_items(live_slot: Any = None) -> list[dict[str, Any]]:
    """Return the waiting-room Minds-On catalog: exactly one MC.

    Teacher-only ``key`` lives on the item. Student APIs must strip it.

    Args:
        live_slot: ``C1`` / ``C2`` / ``C3``. Defaults to C1.

    Returns:
        A one-element list. Callers must not paginate or carousel it.
    """
    pack = minds_on_pack(live_slot)
    return [
        {
            "item_id": MINDS_ON_ITEM_ID,
            "kind": MINDS_ON_KIND,
            "prompt": pack["prompt"],
            "choices": list(pack["choices"]),
            "key": pack["key"],
        }
    ]


def minds_on_prompt_payload(live_slot: Any = None) -> dict[str, Any]:
    """MC payload for the waiting-room Minds-On question.

    Includes teacher-only ``key``. Student APIs must strip it before send.
    ``items`` length is 1. The Live response shell shows that one MC.

    Args:
        live_slot: ``C1`` / ``C2`` / ``C3``. Defaults to C1.

    Returns:
        Live-prompt payload with ``item_id`` ``minds_on`` on the
        ``quick-hitter-question-chain`` artifact.
    """
    slot = normalize_live_slot(live_slot)
    pack = minds_on_pack(slot)
    items = minds_on_items(slot)
    item = items[0]
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
            "item_id": item["item_id"],
            "label": MINDS_ON_LABEL,
            "prompt": item["prompt"],
            "choices": list(item["choices"]),
            "key": item["key"],
            "items": items,
            "live_slot": slot,
            "feedback_id": pack["feedback_id"],
        }
    )
    return payload


def parse_minds_on_student_md(text: str) -> dict[str, Any]:
    """Parse stem, choices, and teacher soft key from a Minds-On student brief.

    Keeps LMS seeds locked to the catalogue copywriter file. Soft keys stay
    teacher-only.

    Args:
        text: Markdown from ``minds-on/MCF3M-M1-C*-minds-on-student.md``.

    Returns:
        ``prompt``, ``choices`` (A–D texts without the letter prefix), and
        ``key``.
    """
    stem_match = re.search(
        r"## Stem \(student-facing\)\s+(.+?)\n\s*A\)",
        text,
        flags=re.DOTALL,
    )
    if not stem_match:
        raise ValueError("Minds-On student brief is missing a Stem section.")
    prompt = " ".join(stem_match.group(1).split())
    choices: list[str] = []
    for letter in "ABCD":
        row = re.search(
            rf"^{letter}\) (.+)$",
            text,
            flags=re.MULTILINE,
        )
        if row is None:
            raise ValueError(f"Minds-On student brief is missing choice {letter}.")
        choices.append(row.group(1).strip())
    key_match = re.search(r"Soft key:\s+\*\*([A-D])\*\*", text)
    if not key_match:
        raise ValueError("Minds-On student brief is missing a teacher soft key.")
    return {"prompt": prompt, "choices": choices, "key": key_match.group(1)}


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
        if ride == RIDE_CONS or ride == RIDE_MEET_TEAM:
            return False
        if ride == RIDE_MINDS_ON:
            return True
    item_id = str(payload.get("item_id") or "").strip().lower()
    pack = str(payload.get("pack") or "").strip().lower()
    return item_id in _LEGACY_ITEM_IDS or pack in _LEGACY_PACK_IDS

"""Meet Your Team warm-up: one ephemeral teammate MC.

Shown after Generate teams / Meet Teams, before Team Challenge. Universal
across courses, modules, and lessons. Session-ephemeral only — cleared
when Team Challenge starts. Not a gradebook writeback. No curriculum chips.
"""

from __future__ import annotations

import random
from typing import Any

try:
    from quick_hitter import (
        CLEAR_ON_TEAM_CHALLENGE,
        QUICK_HITTER_ARTIFACT_ID,
        RIDE_MEET_TEAM,
        quick_hitter_packaging,
    )
except ImportError:  # ``python3 lms/app.py`` package import
    from lms.quick_hitter import (
        CLEAR_ON_TEAM_CHALLENGE,
        QUICK_HITTER_ARTIFACT_ID,
        RIDE_MEET_TEAM,
        quick_hitter_packaging,
    )

MEET_TEAM_ITEM_ID = "meet-team"
MEET_TEAM_PACK_ID = "meet-team"
MEET_TEAM_SLIDE_INDEX = 801
MEET_TEAM_KIND = "mc"
MEET_TEAM_LABEL = "Meet Your Team"

MEET_TEAM_PROMPT = "Today I’m the teammate who…"

MEET_TEAM_FIXED_CHOICES: tuple[str, ...] = (
    "keeps us kind",
    "wants to try being team leader",
    "Not sure",
)

# Documented as meet-team-warmup-pool — rotate two per session.
MEET_TEAM_WARMUP_POOL: tuple[str, ...] = (
    "notices details",
    "asks the good question",
    "tries the weird idea",
    "checks our work",
    "explains so it clicks",
    "brings the calm",
    "connects ideas",
)


def pick_rotated_choices(rng: random.Random | None = None) -> list[str]:
    """Return two distinct pool lines for one live session.

    Args:
        rng: Optional ``random.Random`` so tests can pin the draw.
    """
    picker = rng if rng is not None else random.Random()
    return picker.sample(list(MEET_TEAM_WARMUP_POOL), 2)


def meet_team_choices(
    rotated: list[str] | tuple[str, ...] | None = None,
    *,
    rng: random.Random | None = None,
) -> list[str]:
    """Build the five-choice list: two rotated, then the three fixed lines.

    ``Not sure`` stays last. The two rotated lines come from
    ``MEET_TEAM_WARMUP_POOL``.

    Args:
        rotated: Optional pre-picked pair. When omitted, two pool lines
            are drawn with ``rng``.
        rng: Optional ``random.Random`` used when ``rotated`` is omitted.

    Returns:
        Five student-facing choice strings.

    Raises:
        ValueError: If ``rotated`` is provided and is not length 2.
    """
    extra = list(rotated) if rotated is not None else pick_rotated_choices(rng)
    if len(extra) != 2:
        raise ValueError("meet-team warm-up rotates exactly two pool choices")
    return [*extra, *MEET_TEAM_FIXED_CHOICES]


def meet_team_items(
    rotated: list[str] | tuple[str, ...] | None = None,
    *,
    rng: random.Random | None = None,
) -> list[dict[str, Any]]:
    """Return the Meet Your Team catalog: exactly one MC.

    Args:
        rotated: Optional pre-picked pair from the warmup pool.
        rng: Optional ``random.Random`` used when ``rotated`` is omitted.

    Returns:
        A one-element list. Callers must not paginate or carousel it.
    """
    choices = meet_team_choices(rotated, rng=rng)
    return [
        {
            "item_id": MEET_TEAM_ITEM_ID,
            "kind": MEET_TEAM_KIND,
            "prompt": MEET_TEAM_PROMPT,
            "choices": choices,
        }
    ]


def meet_team_prompt_payload(
    rotated: list[str] | tuple[str, ...] | None = None,
    *,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Student-facing MC payload for the Meet Your Team warm-up.

    Args:
        rotated: Optional pre-picked pair from the warmup pool.
        rng: Optional ``random.Random`` used when ``rotated`` is omitted.

    Returns:
        Live-prompt payload with ``item_id`` ``meet-team`` on the
        ``quick-hitter-question-chain`` artifact.
    """
    items = meet_team_items(rotated, rng=rng)
    item = items[0]
    payload = quick_hitter_packaging(
        ride=RIDE_MEET_TEAM,
        chain_index=1,
        chain_length=1,
        ephemeral=True,
        durable_store=False,
        clear_on=CLEAR_ON_TEAM_CHALLENGE,
    )
    payload.update(
        {
            "pack": MEET_TEAM_PACK_ID,
            "item_id": item["item_id"],
            "label": MEET_TEAM_LABEL,
            "prompt": item["prompt"],
            "choices": list(item["choices"]),
            "items": items,
        }
    )
    return payload


def is_meet_team_payload(payload: Any) -> bool:
    """True when a live-prompt payload is the Meet Your Team warm-up.

    Args:
        payload: Prompt JSON object.
    """
    if not isinstance(payload, dict):
        return False
    artifact = str(payload.get("artifact_id") or "").strip()
    ride = str(payload.get("ride") or "").strip().lower()
    if artifact == QUICK_HITTER_ARTIFACT_ID and ride == RIDE_MEET_TEAM:
        return True
    item_id = str(payload.get("item_id") or "").strip().lower()
    pack = str(payload.get("pack") or "").strip().lower()
    return item_id == MEET_TEAM_ITEM_ID or pack == MEET_TEAM_PACK_ID

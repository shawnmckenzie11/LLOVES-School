"""Team Challenge look-for ids → rubric keys and Ontario process keys."""

from __future__ import annotations

from typing import Any

LOOKFOR_RUBRIC_KEYS: dict[str, list[str]] = {
    "represented": ["connect"],
    "connected": ["connect"],
    "noticed_generalized": ["justify"],
    "justified": ["justify"],
    "checked_revised": ["transfer", "justify"],
    "transferred_extended": ["transfer"],
    "mathematical_language": ["communicate"],
}

LOOKFOR_PROCESS_KEYS: dict[str, list[str]] = {
    "represented": ["representing", "connecting"],
    "connected": ["connecting", "representing"],
    "noticed_generalized": ["reasoning_proving"],
    "justified": ["reasoning_proving"],
    "checked_revised": ["reflecting", "reasoning_proving"],
    "transferred_extended": ["reflecting"],
    "mathematical_language": ["communicating"],
}

LOOKFOR_LABELS: dict[str, str] = {
    "represented": "represented",
    "connected": "connected",
    "noticed_generalized": "noticed/generalized",
    "justified": "justified",
    "checked_revised": "checked/revised",
    "transferred_extended": "transferred/extended",
    "mathematical_language": "mathematical language",
}

LOOKFOR_IDS: tuple[str, ...] = tuple(LOOKFOR_LABELS.keys())


def process_keys_for_lookfor(lookfor_id: str) -> list[str]:
    """Ontario process keys stored on ``observation_processes``.

    Args:
        lookfor_id: Challenge action id.
    """
    return list(LOOKFOR_PROCESS_KEYS.get(str(lookfor_id or "").strip(), []))


def rubric_keys_for_lookfor(lookfor_id: str) -> list[str]:
    """Written-rubric keys for later merge (not stored on point_events).

    Args:
        lookfor_id: Challenge action id.
    """
    return list(LOOKFOR_RUBRIC_KEYS.get(str(lookfor_id or "").strip(), []))


def observation_note_for_lookfor(lookfor_id: str, label: str | None = None) -> str:
    """Staff-visible observation note for a look-for tap.

    Args:
        lookfor_id: Challenge action id.
        label: Overlay label (optional).
    """
    pretty = (label or LOOKFOR_LABELS.get(lookfor_id) or lookfor_id or "").strip()
    return f"Look-for: {pretty}"


def empty_tally() -> dict[str, int]:
    """Zero counts for the seven look-fors."""
    return {key: 0 for key in LOOKFOR_IDS}


def increment_tally(counts: dict[str, int], lookfor_id: str) -> None:
    """Add one to a look-for bucket when the id is known.

    Args:
        counts: Mutable tally dict.
        lookfor_id: Challenge action id.
    """
    key = str(lookfor_id or "").strip()
    if key in counts:
        counts[key] = int(counts[key]) + 1

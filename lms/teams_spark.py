"""Welcome-stage C2: one ephemeral integer poll, not a chain.

Shown after students submit Join C1, and on Welcome (TEAMS). Same stem
for MCF3M and MCR3U M1C1. Class-wide integer histogram after submit.
No gradebook. No curriculum chips.

Clears on TEAMS→MEET. Wonder fires ``cue.teams_spark`` on enter only;
mid-TEAMS patches stay silent.
"""

from __future__ import annotations

from typing import Any

TEAMS_SPARK_ITEM_ID = "teams-spark"
TEAMS_SPARK_PACK_ID = "teams-spark"
TEAMS_SPARK_PROMPT_REF = TEAMS_SPARK_ITEM_ID
TEAMS_SPARK_SLIDE_INDEX = 810
TEAMS_SPARK_KIND = "numeric"
TEAMS_SPARK_LABEL = "C2"
CUE_TEAMS_SPARK = "cue.teams_spark"

TEAMS_SPARK_PROMPT = "What integer will most students enter into this box?"
TEAMS_SPARK_INTEGER_ONLY = True

_LEGACY_ITEM_IDS = frozenset({TEAMS_SPARK_ITEM_ID, "teams_spark"})
_LEGACY_PACK_IDS = frozenset({TEAMS_SPARK_PACK_ID, "teams_spark"})


def teams_spark_prompt_payload() -> dict[str, Any]:
    """Return the Welcome C2 integer-poll payload.

    ``gradebook`` is false. Student APIs must not invent a key. Integer
    answers only.

    Returns:
        Live-prompt payload with ``item_id`` ``teams-spark``.
    """
    return {
        "pack": TEAMS_SPARK_PACK_ID,
        "item_id": TEAMS_SPARK_ITEM_ID,
        "label": TEAMS_SPARK_LABEL,
        "kind": TEAMS_SPARK_KIND,
        "prompt": TEAMS_SPARK_PROMPT,
        "integer_only": TEAMS_SPARK_INTEGER_ONLY,
        "placeholder": "Enter an integer",
        "source": "teams_spark",
        "gradebook": False,
        "meet_chip": False,
        "ephemeral": True,
        "durable_store": False,
        "chips": False,
    }


def is_teams_spark_payload(payload: Any) -> bool:
    """True when a live-prompt payload is the Welcome C2 integer poll.

    Args:
        payload: Prompt JSON object.
    """
    if not isinstance(payload, dict):
        return False
    if str(payload.get("source") or "").strip().lower() == "teams_spark":
        return True
    item_id = str(payload.get("item_id") or "").strip().lower()
    pack = str(payload.get("pack") or "").strip().lower()
    return item_id in _LEGACY_ITEM_IDS or pack in _LEGACY_PACK_IDS


def is_teams_spark_ref(ref: Any) -> bool:
    """True when a teacher ``prompt_ref`` points at Welcome C2.

    Args:
        ref: Stored or public prompt_ref value.
    """
    return str(ref or "").strip().lower() in _LEGACY_ITEM_IDS


def staff_teams_spark_card(
    payload: Any,
    *,
    reveal: bool = False,
) -> dict[str, Any]:
    """Teacher Question-frame card for Welcome C2.

    Args:
        payload: Stored spark payload.
        reveal: True when the class histogram has been shared.
    """
    body = payload if isinstance(payload, dict) else teams_spark_prompt_payload()
    return {
        "prompt": str(body.get("prompt") or TEAMS_SPARK_PROMPT),
        "kind": TEAMS_SPARK_KIND,
        "integer_only": True,
        "choices": [],
        "teacher_key": "",
        "student_feedback_after_reveal": "",
        "reveal": bool(reveal),
    }

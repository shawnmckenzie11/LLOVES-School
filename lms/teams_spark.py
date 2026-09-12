"""TEAMS shared spark: one ephemeral class-wide riddle, not a chain.

Shown while the teacher is on TEAMS (selection / assignment). One prompt
on the Question frame for teacher and students. Not Minds-On, not the
Meet A→C→B ride, not Team Challenge. No gradebook. No curriculum chips.

Clears on TEAMS→MEET. Wonder fires ``cue.teams_spark`` on enter only;
mid-TEAMS patches stay silent.
"""

from __future__ import annotations

from typing import Any

TEAMS_SPARK_ITEM_ID = "teams-spark"
TEAMS_SPARK_PACK_ID = "teams-spark"
TEAMS_SPARK_PROMPT_REF = TEAMS_SPARK_ITEM_ID
TEAMS_SPARK_SLIDE_INDEX = 810
TEAMS_SPARK_KIND = "mc"
TEAMS_SPARK_LABEL = "Shared spark"
CUE_TEAMS_SPARK = "cue.teams_spark"

TEAMS_SPARK_PROMPT = (
    "A farmer has 17 sheep. All but 9 run away. How many are left?"
)
TEAMS_SPARK_CHOICES: tuple[str, ...] = ("8", "9", "17", "0")
# Soft key is teacher-only; student_live_prompt_payload must strip it.
TEAMS_SPARK_KEY = "9"
TEAMS_SPARK_TEACHER_KEY = "9 — “all but 9” means 9 remain."
TEAMS_SPARK_STUDENT_FEEDBACK = "All but nine means nine stay."

_LEGACY_ITEM_IDS = frozenset({TEAMS_SPARK_ITEM_ID, "teams_spark"})
_LEGACY_PACK_IDS = frozenset({TEAMS_SPARK_PACK_ID, "teams_spark"})


def teams_spark_prompt_payload() -> dict[str, Any]:
    """Return the locked TEAMS shared-spark MC payload.

    Includes teacher-only ``key`` / ``teacher_key``. Student APIs must
    strip those before send. ``gradebook`` is false. No chips.

    Returns:
        Live-prompt payload with ``item_id`` ``teams-spark``.
    """
    return {
        "pack": TEAMS_SPARK_PACK_ID,
        "item_id": TEAMS_SPARK_ITEM_ID,
        "label": TEAMS_SPARK_LABEL,
        "kind": TEAMS_SPARK_KIND,
        "prompt": TEAMS_SPARK_PROMPT,
        "choices": list(TEAMS_SPARK_CHOICES),
        "key": TEAMS_SPARK_KEY,
        "teacher_key": TEAMS_SPARK_TEACHER_KEY,
        "student_feedback_after_reveal": TEAMS_SPARK_STUDENT_FEEDBACK,
        "source": "teams_spark",
        "gradebook": False,
        "meet_chip": False,
        "ephemeral": True,
        "durable_store": False,
        "chips": False,
    }


def is_teams_spark_payload(payload: Any) -> bool:
    """True when a live-prompt payload is the TEAMS shared spark.

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
    """True when a teacher ``prompt_ref`` points at the TEAMS spark.

    Args:
        ref: Stored or public prompt_ref value.
    """
    return str(ref or "").strip().lower() in _LEGACY_ITEM_IDS


def staff_teams_spark_card(
    payload: Any,
    *,
    reveal: bool = False,
) -> dict[str, Any]:
    """Teacher Question-frame card for the locked spark.

    Args:
        payload: Stored spark payload (may include teacher-only fields).
        reveal: True when the stay-line has been shared with students.
    """
    body = payload if isinstance(payload, dict) else teams_spark_prompt_payload()
    return {
        "prompt": str(body.get("prompt") or TEAMS_SPARK_PROMPT),
        "choices": list(body.get("choices") or TEAMS_SPARK_CHOICES),
        "teacher_key": str(
            body.get("teacher_key") or TEAMS_SPARK_TEACHER_KEY
        ),
        "student_feedback_after_reveal": str(
            body.get("student_feedback_after_reveal")
            or TEAMS_SPARK_STUDENT_FEEDBACK
        ),
        "reveal": bool(reveal),
    }

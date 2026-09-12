"""Instant text feedback for Minds-On and CONS quick-hitters.

Teacher-authored soft keys from
``catalogue/challenges/module-briefs/quick-hitters/MCF3M-M1-C{1,2,3}-feedback-keys.md``.
Resolved server-side on submit. Student GET never includes the map, keys, or
cement. Team Challenge stem has no keys. Local teacher DB only.
"""

from __future__ import annotations

from typing import Any

CHOICE_LETTERS = "ABCDEFGH"
TEACHER_ONLY_FIELDS = (
    "key",
    "cement",
    "soft_key",
    "by_choice",
    "on_submit",
    "feedback",
    "feedback_id",
    "chips",
    "curriculum_chips",
    "expectation_codes",
)

# Waiting-room item ids (current main + PR #42 rename).
_MINDS_ON_ITEM_IDS = frozenset({"minds_on", "minds-on", "meet-math"})
_MINDS_ON_PACK_IDS = frozenset({"minds_on", "waiting-room"})

# Soft-key juice. One short line per choice / share submit.
M1C1_FEEDBACK: dict[str, dict[str, Any]] = {
    "minds_on": {
        "soft_key": "A",
        "by_choice": {
            "A": "Same step, same change — that’s a constant rate.",
            "B": "A curve changes steepness as you go. Constant rate stays even.",
            "C": (
                "Second differences are a quadratic tell. "
                "Linear rate is first differences."
            ),
            "D": (
                "Constant rate means every equal step changes y "
                "by the same amount."
            ),
        },
    },
    "C1-CONS-1": {
        "soft_key": "B",
        "by_choice": {
            "A": "This picture opens upward, so a is positive.",
            "B": "Opens upward → a > 0.",
            "C": "If a were 0, this wouldn’t be a parabola.",
            "D": "The opening is on this picture — a must be positive.",
        },
    },
    "C1-CONS-2": {
        "soft_key": "C",
        "by_choice": {
            "A": "The intercept is the origin, so c is 0 — not positive.",
            "B": "The intercept is the origin, so c is 0 — not negative.",
            "C": "Y-intercept at the origin → c = 0.",
            "D": "This picture pins the intercept at the origin, so c is 0.",
        },
    },
    "C1-CONS-3": {
        "soft_key": "C",
        "by_choice": {
            "A": "No sideways lean on this picture — b is 0, not positive.",
            "B": "No sideways lean on this picture — b is 0, not negative.",
            "C": "Vertex at the origin and y-axis symmetry → b = 0.",
            "D": "For this picture, y-axis symmetry forces b = 0.",
        },
    },
    "C1-CONS-4": {
        "on_submit": "Feature → claim. That’s the whole move.",
    },
    "C1-CONS-5": {
        "on_submit": "Leave the blank honest — what’s still free here?",
    },
}

M1C2_FEEDBACK: dict[str, dict[str, Any]] = {
    "C2-minds_on": {
        "soft_key": "A",
        "by_choice": {
            "A": "Good work. The U opens up — so a > 0.",
            "B": "Not that one — look which way the arms open.",
            "C": "Curves wait. This picture is still a parabola.",
            "D": "Hint: which way do the arms open?",
        },
    },
    "C2-CONS-1": {
        "soft_key": "B",
        "by_choice": {
            "A": "One special case isn’t a law. Must the vertex sit on (2,5)?",
            "B": (
                "Good work. The point ties parameters — "
                "it doesn’t freeze h alone."
            ),
            "C": "Hint: can you hit (2,5) with a vertex somewhere else?",
        },
    },
    "C2-CONS-2": {
        "on_submit": (
            "The point ties a, h, and k — it doesn’t freeze one parameter."
        ),
    },
    "C2-CONS-3": {
        "on_submit": (
            "Another writing through (2,5) shows the family — "
            "not a single graph."
        ),
    },
}

M1C3_FEEDBACK: dict[str, dict[str, Any]] = {
    "C3-minds_on": {
        "soft_key": "B",
        "by_choice": {
            "A": (
                "Not that one — one point doesn’t freeze a, h, and k "
                "all at once."
            ),
            "B": "Good work. The point links the parameters — some stay free.",
            "C": (
                "Domain and range aren’t always all real numbers. "
                "Context can cut them."
            ),
            "D": "Hint: does one marked point lock every parameter?",
        },
    },
    "C3-CONS-1": {
        "soft_key": "B",
        "by_choice": {
            "A": (
                "Not above ground there — the path is already underground "
                "at x = 8."
            ),
            "B": "Good work. At the wall the model is not above ground.",
            "C": "Hint: check a table at x = 8. Is y still at least 0?",
        },
    },
    "C3-CONS-2": {
        "on_submit": "Heights on this path run from the ground up to the peak.",
    },
    "C3-CONS-3": {
        "on_submit": (
            "Shade the x-values that stay above ground — "
            "then defend from the picture."
        ),
    },
}

FEEDBACK_TABLE: dict[str, dict[str, Any]] = {
    **M1C1_FEEDBACK,
    **M1C2_FEEDBACK,
    **M1C3_FEEDBACK,
}

_MINDS_ON_FEEDBACK_BY_SLOT = {
    "C1": "minds_on",
    "C2": "C2-minds_on",
    "C3": "C3-minds_on",
}


def canonical_feedback_item_id(payload: Any) -> str:
    """Return the feedback table key, or empty when this prompt has none.

    Args:
        payload: Live-prompt JSON (item_id / pack / ride / live_slot).
    """
    if not isinstance(payload, dict):
        return ""
    explicit = str(payload.get("feedback_id") or "").strip()
    if explicit in FEEDBACK_TABLE:
        return explicit
    item_id = str(payload.get("item_id") or "").strip()
    lowered = item_id.lower()
    ride = str(payload.get("ride") or "").strip().lower()
    pack = str(payload.get("pack") or "").strip().lower()
    slot = str(payload.get("live_slot") or "").strip().upper()
    if lowered in _MINDS_ON_ITEM_IDS or ride == "minds_on" or pack in _MINDS_ON_PACK_IDS:
        return _MINDS_ON_FEEDBACK_BY_SLOT.get(slot, "minds_on")
    if item_id in FEEDBACK_TABLE:
        return item_id
    return ""


def choice_letter(response: Any, choices: Any) -> str | None:
    """Map a student MC response to A–H.

    Accepts a letter, ``B)`` prefix, or the choice text.

    Args:
        response: Student answer object (``choice`` or ``value``).
        choices: Prompt choice list, in A–H order.
    """
    raw: Any = None
    if isinstance(response, dict):
        raw = response.get("choice")
        if raw is None:
            raw = response.get("value")
    text = str(raw if raw is not None else "").strip()
    if not text:
        return None
    upper = text.upper()
    if len(upper) == 1 and upper in CHOICE_LETTERS:
        index = CHOICE_LETTERS.index(upper)
        if not choices or index < len(list(choices)):
            return upper
        return None
    if len(upper) >= 2 and upper[0] in CHOICE_LETTERS and upper[1] in ".)":
        return upper[0]
    rows = [str(choice).strip() for choice in (choices or [])]
    for index, choice in enumerate(rows):
        if choice == text or choice.casefold() == text.casefold():
            return CHOICE_LETTERS[index]
    return None


def resolve_live_prompt_feedback(
    payload: Any, response: Any
) -> dict[str, str] | None:
    """Return ``{text, source}`` for a Minds-On / CONS submit, or None.

    ``source`` is ``by_choice`` or ``on_submit``. Team Challenge and unknown
    prompts return None.

    Args:
        payload: Live-prompt payload (may include teacher-only fields).
        response: Student answer JSON.
    """
    item_id = canonical_feedback_item_id(payload)
    if not item_id:
        return None
    entry = FEEDBACK_TABLE.get(item_id) or {}
    by_choice = entry.get("by_choice") or {}
    on_submit = str(entry.get("on_submit") or "").strip()
    letter = choice_letter(
        response, (payload or {}).get("choices") if isinstance(payload, dict) else []
    )
    if by_choice and letter:
        text = str(by_choice.get(letter) or "").strip()
        if text:
            return {"text": text, "source": "by_choice"}
    if on_submit:
        return {"text": on_submit, "source": "on_submit"}
    return None


def public_feedback_fragment(
    payload: Any, response: Any
) -> dict[str, str] | None:
    """Student-safe feedback object for submit / my_response JSON.

    Args:
        payload: Live-prompt payload.
        response: Student answer JSON.
    """
    resolved = resolve_live_prompt_feedback(payload, response)
    if not resolved:
        return None
    return {"text": resolved["text"], "source": resolved["source"]}


def strip_teacher_prompt_fields(payload: Any) -> dict[str, Any]:
    """Copy a prompt payload without keys, cement, or feedback maps.

    Strips the same teacher-only fields from nested ``items`` rows.

    Args:
        payload: Prompt JSON object.
    """
    if not isinstance(payload, dict):
        return {}
    out = dict(payload)
    for key in TEACHER_ONLY_FIELDS:
        out.pop(key, None)
    items = out.get("items")
    if isinstance(items, list):
        stripped: list[Any] = []
        for item in items:
            if isinstance(item, dict):
                row = dict(item)
                for key in TEACHER_ONLY_FIELDS:
                    row.pop(key, None)
                stripped.append(row)
            else:
                stripped.append(item)
        out["items"] = stripped
    return out

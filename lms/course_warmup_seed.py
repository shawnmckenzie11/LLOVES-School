"""Course Wide live-class warmups (icebreakers), separate from math unit banks.

These stems are preference and opinion prompts. They belong on the Run Live
Class Import picker when Bank scope is Course Wide. They are not A2/A3 (or
any module) math items, so module Import must not list them.
"""

from __future__ import annotations

import json
from typing import Any

COURSE_WIDE_WARMUP_BANK_KEY = "course-wide-warmups"
COURSE_WIDE_WARMUP_BANK_TITLE = "Course Wide warmups"
COURSE_SCOPE_TOKENS = frozenset({"course", "course-wide", "coursewide", "all"})
# Course Wide search unions confirmed banks on M1–M8. Confirming every
# module lets that union see these rows. Process Kind still hides them.
COURSE_WIDE_WARMUP_CONFIRM_MODULES = (1, 2, 3, 4, 5, 6, 7, 8)

# Bank-curator locked pack. Pick-a-side rows are unkeyed choices.
# Opinion and trivia are open polls. Prediction-ranking is group submit.
_COURSE_WIDE_WARMUP_SPECS: tuple[dict[str, Any], ...] = (
    {
        "import_key": "warmup-aisle-window",
        "title": "Aisle or window",
        "category": "pick-a-side",
        "stem": "Aisle seat or window seat — pick one and defend it in one sentence.",
        "options": ["Aisle seat", "Window seat"],
        "response_mode": "individual",
    },
    {
        "import_key": "warmup-text-call",
        "title": "Text or call",
        "category": "pick-a-side",
        "stem": "Texting or calling forever — pick one, no going back.",
        "options": ["Texting", "Calling"],
        "response_mode": "individual",
    },
    {
        "import_key": "warmup-beach-cabin",
        "title": "Beach or cabin",
        "category": "pick-a-side",
        "stem": "Beach vacation or mountain cabin?",
        "options": ["Beach vacation", "Mountain cabin"],
        "response_mode": "individual",
    },
    {
        "import_key": "warmup-overrated-food",
        "title": "Most overrated food",
        "category": "opinion",
        "stem": "What's the most overrated food that everyone else seems to love?",
        "options": [],
        "response_mode": "individual",
    },
    {
        "import_key": "warmup-rule-differs",
        "title": "A rule that should differ",
        "category": "opinion",
        "stem": "Name a rule (school, home, or the world) that should honestly just be different.",
        "options": [],
        "response_mode": "individual",
    },
    {
        "import_key": "warmup-confident-opinion",
        "title": "Unimportant confident opinion",
        "category": "opinion",
        "stem": "What's your most confident opinion about something completely unimportant?",
        "options": [],
        "response_mode": "individual",
    },
    {
        "import_key": "warmup-useless-skill",
        "title": "Weirdly useless skill",
        "category": "trivia-about-you",
        "stem": "What's something you're weirdly good at that has almost no real-world use?",
        "options": [],
        "response_mode": "individual",
    },
    {
        "import_key": "warmup-group-mascot",
        "title": "Group mascot",
        "category": "trivia-about-you",
        "stem": "If your group had a mascot right now, what would it be?",
        "options": [],
        "response_mode": "individual",
    },
    {
        "import_key": "warmup-laughed-too-hard",
        "title": "Laughed too hard",
        "category": "trivia-about-you",
        "stem": "What's the last thing that made you laugh way harder than it should have?",
        "options": [],
        "response_mode": "individual",
    },
    {
        "import_key": "warmup-fraction-never",
        "title": "Fraction who never did X",
        "category": "prediction-ranking",
        "stem": "As a group, guess: what fraction of the class has never ridden a roller coaster? (Teacher swaps X.)",
        "options": [],
        "response_mode": "group_consensus",
        "answer_shape": "one fraction + one-line why",
    },
    {
        "import_key": "warmup-rank-annoyances",
        "title": "Rank three annoyances",
        "category": "prediction-ranking",
        "stem": "Rank most→least annoying: slow wifi · wet socks · someone chewing loudly. Group must agree on one order.",
        "options": [],
        "response_mode": "group_consensus",
        "answer_shape": "one ranking + one-line why",
    },
)


def locked_course_warmup_titles() -> tuple[str, ...]:
    """Return the eleven Course Wide warmup titles, in catalogue order.

    These match the ``live_problems`` icebreaker titles seeded for MCF3M and
    MCR3U. Import lists this tuple when Bank scope is Course Wide and Kind
    is Warmup.

    Returns:
        Locked title strings.
    """
    return tuple(str(row["title"]) for row in _COURSE_WIDE_WARMUP_SPECS)


def course_wide_warmup_catalogue() -> list[dict[str, Any]]:
    """Return a copy of the Course Wide warmup catalogue.

    Returns:
        Specs with ``import_key``, ``title``, ``stem``, and ``options``.
    """
    return [dict(row) for row in _COURSE_WIDE_WARMUP_SPECS]


def course_wide_warmup_payload(spec: dict[str, Any]) -> dict[str, Any]:
    """Build the stored question payload for one warmup spec.

    Args:
        spec: One catalogue row from ``course_wide_warmup_catalogue``.

    Returns:
        ``payload_json`` tagged ``kind=warmup``, ``tags=["warmup"]``, and
        ``bank_scope=course``.
    """
    options = [str(opt).strip() for opt in (spec.get("options") or []) if str(opt).strip()]
    choices = [
        {"id": chr(ord("a") + index), "html": option, "text": option}
        for index, option in enumerate(options)
    ]
    category = str(spec.get("category") or "").strip()
    response_mode = str(spec.get("response_mode") or "individual").strip().lower()
    if response_mode != "group_consensus":
        response_mode = "individual"
    payload: dict[str, Any] = {
        "kind": "warmup",
        "tags": ["warmup"],
        "bank_scope": "course",
        "category": category,
        "module_hint": f"COURSE/{category}" if category else "COURSE",
        "expectation_codes": [],
        "stem_html": str(spec.get("stem") or "").strip(),
        "points_possible": 0,
        "choices": choices,
        "type": "mc" if choices else "poll",
        "response_mode": response_mode,
    }
    shape = str(spec.get("answer_shape") or "").strip()
    if shape:
        payload["answer_shape"] = shape
    if response_mode == "group_consensus":
        payload["publish_modes"] = ["individual", "group_consensus"]
    return payload


def is_course_scoped_warmup(payload: Any) -> bool:
    """True when a question payload is a Course Wide warmup.

    Args:
        payload: Parsed ``questions.payload_json``.
    """
    if not isinstance(payload, dict):
        return False
    kind = str(payload.get("kind") or payload.get("bank_kind") or "").strip().lower()
    scope = str(payload.get("bank_scope") or payload.get("bankScope") or "").strip().lower()
    return kind == "warmup" and scope in COURSE_SCOPE_TOKENS


def normalize_course_warmup(
    *,
    question_id: int,
    bank_id: int,
    title: str,
    payload: Any,
    bank_title: str = COURSE_WIDE_WARMUP_BANK_TITLE,
) -> dict[str, Any] | None:
    """Turn one stored warmup into an importable live-class item.

    Choice prompts stay multiple choice with no answer key, so publishing
    keeps the options and scoring does not mark a preference wrong. Open
    prompts stay polls.

    Args:
        question_id: ``questions.id``.
        bank_id: ``question_banks.id``.
        title: Question title.
        payload: Parsed payload. Must be a course-scoped warmup.
        bank_title: Bank label shown on the imported card.

    Returns:
        Normalized item, or ``None`` when the payload is not a warmup.
    """
    blob = payload if isinstance(payload, dict) else {}
    if not is_course_scoped_warmup(blob):
        return None
    stem = str(blob.get("stem_html") or blob.get("text") or title or "").strip()
    if not stem:
        return None
    raw_choices = blob.get("choices") if isinstance(blob.get("choices"), list) else []
    options: list[str] = []
    for choice in raw_choices:
        if isinstance(choice, dict):
            text = str(choice.get("text") or choice.get("html") or "").strip()
        else:
            text = str(choice or "").strip()
        if text:
            options.append(text)
    kind = "mc" if options else "poll"
    response_mode = str(blob.get("response_mode") or "individual").strip().lower()
    if response_mode != "group_consensus":
        response_mode = "individual"
    publish_modes = blob.get("publish_modes")
    if not isinstance(publish_modes, list) or not publish_modes:
        publish_modes = (
            ["individual", "group_consensus"]
            if response_mode == "group_consensus"
            else ["individual"]
        )
    category = str(blob.get("category") or "").strip()
    return {
        "type": kind,
        "text": stem,
        "prompt": stem,
        "options": options,
        "choices": list(options),
        "points": 0,
        "kind": "warmup",
        "bank_scope": "course",
        "category": category,
        "module_hint": str(blob.get("module_hint") or (f"COURSE/{category}" if category else "")),
        "expectation_codes": [],
        "answer_shape": str(blob.get("answer_shape") or "").strip(),
        "response_mode": response_mode,
        "publish_modes": [str(mode) for mode in publish_modes],
        "question_id": int(question_id),
        "bank_id": int(bank_id),
        "bank_title": str(bank_title or COURSE_WIDE_WARMUP_BANK_TITLE),
        "question_title": str(title or stem),
        "source_question_id": int(question_id),
        "source_bank_id": int(bank_id),
    }


def warmup_settings_json() -> str:
    """Return bank ``settings_json`` marking Course Wide warmup scope."""
    return json.dumps({"bank_scope": "course", "kind": "warmup"})

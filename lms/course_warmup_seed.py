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

# Shawn's icebreaker list. Choice rows import as unkeyed multiple choice.
# Open rows import as polls (students type).
_COURSE_WIDE_WARMUP_SPECS: tuple[dict[str, Any], ...] = (
    {
        "import_key": "warmup-aisle-window",
        "title": "Aisle or window",
        "stem": "Aisle or window: which seat do you pick?",
        "options": ["Aisle", "Window"],
    },
    {
        "import_key": "warmup-text-call",
        "title": "Text or call",
        "stem": "Text or call: how do you reach a friend?",
        "options": ["Text", "Call"],
    },
    {
        "import_key": "warmup-beach-cabin",
        "title": "Beach or cabin",
        "stem": "Beach or cabin: where is the better long weekend?",
        "options": ["Beach", "Cabin"],
    },
    {
        "import_key": "warmup-overrated-food",
        "title": "Overrated food",
        "stem": "Which food is overrated?",
        "options": ["Pineapple on pizza", "Raisins", "Decaf coffee", "Kale"],
    },
    {
        "import_key": "warmup-rule-differs",
        "title": "A rule that should differ",
        "stem": "What rule should work differently than it does?",
        "options": [],
    },
    {
        "import_key": "warmup-confident-opinion",
        "title": "Unimportant confident opinion",
        "stem": "What unimportant opinion do you hold with complete confidence?",
        "options": [],
    },
    {
        "import_key": "warmup-useless-skill",
        "title": "Useless skill",
        "stem": "What useless skill are you weirdly proud of?",
        "options": [],
    },
    {
        "import_key": "warmup-group-mascot",
        "title": "Group mascot",
        "stem": "If this group had a mascot, what would it be?",
        "options": [],
    },
    {
        "import_key": "warmup-laughed-too-hard",
        "title": "Laughed too hard",
        "stem": "When did you last laugh too hard?",
        "options": [],
    },
    {
        "import_key": "warmup-fraction-never",
        "title": "Fraction who never did it",
        "stem": "What fraction of this class has never done something the rest of us have?",
        "options": ["Almost none", "About a quarter", "About half", "Most of us"],
    },
    {
        "import_key": "warmup-rank-annoyances",
        "title": "Rank three annoyances",
        "stem": "Rank three small annoyances from most annoying to least.",
        "options": [],
    },
)


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
        ``payload_json`` object tagged ``kind=warmup`` and ``bank_scope=course``.
    """
    options = [str(opt).strip() for opt in (spec.get("options") or []) if str(opt).strip()]
    choices = [
        {"id": chr(ord("a") + index), "html": option, "text": option}
        for index, option in enumerate(options)
    ]
    return {
        "kind": "warmup",
        "bank_scope": "course",
        "stem_html": str(spec.get("stem") or "").strip(),
        "points_possible": 0,
        "choices": choices,
        "type": "mc" if choices else "poll",
    }


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
    return {
        "type": kind,
        "text": stem,
        "prompt": stem,
        "options": options,
        "choices": list(options),
        "points": 0,
        "kind": "warmup",
        "bank_scope": "course",
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

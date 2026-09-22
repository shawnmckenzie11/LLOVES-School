"""Course Wide live-class warmups locked by the bank curator.

These icebreakers belong on Run Live Class Import when Bank scope is Course
Wide. They are not A2/A3 math items. ``live_problems`` has no ``bank_scope``
column yet, so each row carries ``module_hint`` ``COURSE/<category>``.
"""

from __future__ import annotations

import json
from typing import Any

COURSE_WIDE_WARMUP_BANK_KEY = "course-wide-warmups"
COURSE_WIDE_WARMUP_BANK_TITLE = "Course Wide warmups"
COURSE_WIDE_WARMUP_COURSES = ("MCF3M", "MCR3U")
COURSE_SCOPE_TOKENS = frozenset({"course", "course-wide", "coursewide", "all"})
# Course Wide search unions confirmed banks on M1–M8. One link is enough
# for that union. Default Kind still hides these rows.
COURSE_WIDE_WARMUP_CONFIRM_MODULE = 1

# Locked pack. Choice rows are pick-a-side. Open rows are polls.
# prediction-ranking is group-submit practice.
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
        "stem": (
            "As a group, guess: what fraction of the class has never ridden "
            "a roller coaster? (Teacher swaps X.)"
        ),
        "answer_shape": "one fraction + one-line why",
        "options": [],
        "response_mode": "group_consensus",
    },
    {
        "import_key": "warmup-rank-annoyances",
        "title": "Rank three annoyances",
        "category": "prediction-ranking",
        "stem": (
            "Rank most→least annoying: slow wifi · wet socks · someone chewing loudly. "
            "Group must agree on one order."
        ),
        "answer_shape": "one ranking + one-line why",
        "options": [],
        "response_mode": "group_consensus",
    },
)


def course_wide_warmup_catalogue() -> list[dict[str, Any]]:
    """Return a copy of the locked Course Wide warmup catalogue.

    Returns:
        Specs with title, exact stem, category, ``COURSE/<category>`` hint,
        and empty expectation codes.
    """
    rows: list[dict[str, Any]] = []
    for spec in _COURSE_WIDE_WARMUP_SPECS:
        row = dict(spec)
        category = str(row.get("category") or "").strip()
        row["module_hint"] = f"COURSE/{category}"
        row["expectation_codes"] = []
        row["options"] = list(row.get("options") or [])
        rows.append(row)
    return rows


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
        "module_hint": str(spec.get("module_hint") or f"COURSE/{category}"),
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


def course_wide_warmup_live_problem(
    spec: dict[str, Any], ontario_code: str
) -> dict[str, Any]:
    """Build one ``live_problems`` row for a course copy of a warmup.

    ``bank_scope`` is not a column on that table yet. Course scope is the
    ``module_hint`` prefix ``COURSE/``.

    Args:
        spec: One catalogue row.
        ontario_code: ``MCF3M`` or ``MCR3U``.

    Returns:
        Payload for ``upsert_live_problem``.
    """
    category = str(spec.get("category") or "").strip()
    return {
        "ontario_code": str(ontario_code or "").strip().upper(),
        "module_hint": str(spec.get("module_hint") or f"COURSE/{category}"),
        "kind": "warmup",
        "title": str(spec.get("title") or "").strip(),
        "stem_html": str(spec.get("stem") or "").strip(),
        "task_html": str(spec.get("answer_shape") or "").strip(),
        "diagram_note": "",
        "source": "course_wide_warmup",
        "license": "teacher_original",
        "expectation_codes": [],
        "processes": [],
        "sort_order": 800,
        "active": True,
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
    hint = str(payload.get("module_hint") or "").strip().upper()
    if kind == "warmup" and scope in COURSE_SCOPE_TOKENS:
        return True
    return kind == "warmup" and hint.startswith("COURSE/")


def normalize_course_warmup(
    *,
    question_id: int,
    bank_id: int,
    title: str,
    payload: Any,
    bank_title: str = COURSE_WIDE_WARMUP_BANK_TITLE,
) -> dict[str, Any] | None:
    """Turn one stored warmup into an importable live-class item.

    Pick-a-side prompts stay unkeyed multiple choice. Opinion, trivia, and
    prediction prompts stay polls. Prediction-ranking defaults to a group
    submit.

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
        publish_modes = ["individual"]
        if response_mode == "group_consensus":
            publish_modes = ["individual", "group_consensus"]
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
        "module_hint": str(blob.get("module_hint") or f"COURSE/{category}"),
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

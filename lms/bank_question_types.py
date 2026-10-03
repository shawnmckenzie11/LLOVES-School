"""MCK-170: question type of a bank search row, its label, and per-type counts.

Bank search rows are normalized first (overlay, warmup, curriculum open,
dedupe), and Kind and text search already run on those rows. The type is
read from the same normalized row plus the stored ``item_type`` and
payload, so it filters after Kind and search, and the per-type counts
always match the list a teacher sees.
"""

from __future__ import annotations

from typing import Any

# Display order of the Type chips. Only types present in results are shown.
TYPE_ORDER: tuple[str, ...] = (
    "mc",
    "true_false",
    "numeric",
    "short_answer",
    "rank",
    "open",
)
TYPE_LABELS: dict[str, str] = {
    "mc": "Multiple choice",
    "true_false": "True/false",
    "numeric": "Numeric",
    "short_answer": "Short answer",
    "rank": "Rank",
    "open": "Open-ended",
}
_ALIASES: dict[str, str] = {
    "multiple_choice": "mc",
    "multiple_choice_question": "mc",
    "tf": "true_false",
    "true-false": "true_false",
    "truefalse": "true_false",
    "true_false_question": "true_false",
    "number": "numeric",
    "numerical": "numeric",
    "numerical_question": "numeric",
    "short": "short_answer",
    "short-answer": "short_answer",
    "short_answer_question": "short_answer",
    "order": "rank",
    "ordering": "rank",
    "poll": "open",
    "open_ended": "open",
    "open-ended": "open",
    "essay": "open",
}
_ITEM_TYPE_MAP: dict[str, str] = {
    "true_false_question": "true_false",
    "numerical_question": "numeric",
    "short_answer_question": "short_answer",
    "fill_in_multiple_blanks_question": "short_answer",
}


def parse_type_filter(raw: Any) -> str | None:
    """Return a canonical type token, ``""`` for All, or ``None`` if unknown.

    Args:
        raw: ``?type=`` value (``rank``, ``order``, ``mc``, ``all``, ...).
    """
    token = str(raw or "").strip().lower()
    if token in {"", "all", "any"}:
        return ""
    token = _ALIASES.get(token, token)
    return token if token in TYPE_LABELS else None


def _is_true_false(options: Any) -> bool:
    """True when the options are exactly True and False (any order or case)."""
    if not isinstance(options, list) or len(options) != 2:
        return False
    texts = set()
    for opt in options:
        text = opt.get("text") or opt.get("html") if isinstance(opt, dict) else opt
        texts.add(str(text or "").strip().lower().rstrip("."))
    return texts == {"true", "false"}


def bank_question_type(
    item: dict[str, Any], *, item_type: str = "", payload: Any = None
) -> str:
    """Classify one normalized bank search row.

    Args:
        item: Normalized row (``type`` is ``mc`` or ``poll`` today).
        item_type: Stored ``questions.item_type``.
        payload: Parsed ``questions.payload_json``.

    Returns:
        A key of :data:`TYPE_LABELS`.
    """
    blob = payload if isinstance(payload, dict) else {}
    explicit = parse_type_filter(item.get("question_type"))
    if explicit:
        return explicit
    tokens = {
        str(item.get("type") or "").strip().lower(),
        str(blob.get("type") or "").strip().lower(),
        str(blob.get("kind") or "").strip().lower(),
    }
    if tokens & {"rank", "order"} or item.get("rank_options") or blob.get("rank_options"):
        return "rank"
    mapped = _ITEM_TYPE_MAP.get(str(item_type or "").strip().lower())
    if mapped:
        return mapped
    if "numeric" in tokens:
        return "numeric"
    if "short_answer" in tokens:
        return "short_answer"
    if str(item.get("type") or "").strip().lower() == "poll":
        return "open"
    if _is_true_false(item.get("options")):
        return "true_false"
    return "mc"


def type_counts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-type counts in chip order, types with no rows left out.

    Args:
        items: Rows that already carry ``question_type``.
    """
    counts: dict[str, int] = {}
    for item in items:
        token = str(item.get("question_type") or "mc")
        counts[token] = counts.get(token, 0) + 1
    return [
        {"type": token, "label": TYPE_LABELS[token], "count": counts[token]}
        for token in TYPE_ORDER
        if counts.get(token)
    ]


def filter_by_type(
    items: list[dict[str, Any]], qtype: str | None
) -> list[dict[str, Any]]:
    """Keep rows of one type; ``""``/``None`` keeps all.

    Args:
        items: Rows that already carry ``question_type``.
        qtype: Canonical token from :func:`parse_type_filter`.
    """
    if not qtype:
        return items
    return [item for item in items if str(item.get("question_type") or "mc") == qtype]

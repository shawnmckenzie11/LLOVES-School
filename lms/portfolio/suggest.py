"""Deterministic first-pass L1–L4 suggestions from the four classifier bullets."""

from __future__ import annotations

import re
from typing import Any

from portfolio.store import load_rubric

CRITERIA = ("connect", "justify", "transfer", "communicate")

_REPR_WORDS = (
    "graph",
    "table",
    "equation",
    "diagram",
    "representation",
    "model",
    "number line",
    "factor",
    "grid",
)


def _words(text: str) -> list[str]:
    """Lowercase tokens."""
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _count_repr(text: str) -> int:
    """How many distinct representation words appear."""
    blob = (text or "").lower()
    return sum(1 for word in _REPR_WORDS if word in blob)


def classify_connect(text: str) -> str:
    """Apply the Connect Representations classifier bullets."""
    blob = (text or "").lower()
    n = _count_repr(text)
    if n == 0:
        return "L1"
    insight = any(k in blob for k in ("insight", "compare", "select", "better", "strateg"))
    connected = any(
        k in blob
        for k in ("connect", "corresponding", "same idea", "both show", "maps to")
    )
    if insight and connected and n >= 2:
        return "L4"
    if connected and n >= 2:
        return "L3"
    if n >= 2:
        return "L2"
    return "L1"


def classify_justify(text: str) -> str:
    """Apply the Reason & Justify classifier bullets."""
    blob = (text or "").lower()
    if not blob.strip():
        return "L1"
    general = any(
        k in blob for k in ("always", "every", "in general", "verify", "case", "extend")
    )
    why = any(k in blob for k in ("because", "therefore", "so that", "relationship"))
    example = any(k in blob for k in ("example", "for instance", "such as", "e.g"))
    if general and why:
        return "L4"
    if why:
        return "L3"
    if example:
        return "L2"
    return "L1"


def classify_transfer(text: str) -> str:
    """Apply the Reflect & Transfer classifier bullets."""
    blob = (text or "").lower()
    if not blob.strip():
        return "L1"
    evaluate = any(
        k in blob for k in ("when to", "alternative", "compared", "flexib", "unfamiliar")
    )
    apply = any(k in blob for k in ("another", "new problem", "then i", "applied", "elsewhere"))
    change = any(k in blob for k in ("changed", "strategy", "used to", "now i"))
    if evaluate and apply:
        return "L4"
    if change and apply:
        return "L3"
    if change:
        return "L2"
    return "L1"


def classify_communicate(text: str, useful_words: list[str] | None = None) -> str:
    """Apply the Communication classifier bullets."""
    blob = (text or "").lower()
    tokens = _words(text)
    if len(tokens) < 8:
        return "L1"
    vocab = useful_words or []
    hits = sum(1 for w in vocab if w.lower() in blob)
    forms = _count_repr(text)
    if hits >= 3 and forms >= 2 and len(tokens) >= 40:
        return "L4"
    if hits >= 2 or (hits >= 1 and forms >= 1):
        return "L3"
    if hits >= 1 or forms >= 1:
        return "L2"
    return "L1"


def slice_questions(text: str) -> dict[str, str]:
    """Split HTML/Doc text into Q1/Q2/Q3 when headings are present.

    Args:
        text: Full submission text.

    Returns:
        Keys q1/q2/q3 plus ``full``.
    """
    raw = text or ""
    parts = {"q1": "", "q2": "", "q3": "", "full": raw}
    matches = list(re.finditer(r"(?:question\s*([123])|q\s*([123])|connect|justify|transfer)", raw, re.I))
    if len(matches) < 2:
        return parts
    spans: list[tuple[str, int]] = []
    for match in matches:
        num = match.group(1) or match.group(2)
        if num:
            key = f"q{num}"
        else:
            word = match.group(0).lower()
            key = {"connect": "q1", "justify": "q2", "transfer": "q3"}.get(word, "")
        if key:
            spans.append((key, match.start()))
    spans.sort(key=lambda item: item[1])
    for index, (key, start) in enumerate(spans):
        end = spans[index + 1][1] if index + 1 < len(spans) else len(raw)
        chunk = raw[start:end].strip()
        if chunk and not parts[key]:
            parts[key] = chunk
    return parts


def suggest_levels(text: str, useful_words: list[str] | None = None) -> dict[str, Any]:
    """Return suggested L1–L4 per criterion (labeled as suggestions).

    Args:
        text: Submission text.
        useful_words: Module useful-word list for communication hits.

    Returns:
        Dict with ``suggestion`` True and levels per criterion.
    """
    slices = slice_questions(text)
    connect_text = slices["q1"] or slices["full"]
    justify_text = slices["q2"] or slices["full"]
    transfer_text = slices["q3"] or slices["full"]
    rubric = load_rubric()
    levels = {
        "connect": classify_connect(connect_text),
        "justify": classify_justify(justify_text),
        "transfer": classify_transfer(transfer_text),
        "communicate": classify_communicate(slices["full"], useful_words),
    }
    return {
        "suggestion": True,
        "levels": levels,
        "rubric": rubric,
        "slices_used": {k: bool(slices[k]) for k in ("q1", "q2", "q3")},
    }


def submission_text_from_html(html: str) -> str:
    """Strip tags for the classifier.

    Args:
        html: Raw HTML or Doc-exported HTML.
    """
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", html or "")
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

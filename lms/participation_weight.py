"""MCK-230: shared participation opportunity weights for Lesson Slides decks.

Each multiple-choice question (individual or group) counts as one opportunity;
other question types count as zero. Lesson Slides and Attendance & Participation
both import this module so denominators stay aligned.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

# Live points logged before this date may exceed deck opportunities (legacy).
LEGACY_PERCENT_CAP_BEFORE = date(2026, 10, 14)

_MC_KINDS = frozenset({"mc", "multiple_choice", "multiple_choice_question"})
_GROUP_PUBLISH = frozenset({"group_consensus", "group_shared"})


def _parse_item_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def item_is_multiple_choice(prompt_kind: str, item_json: Any = None) -> bool:
    """True when the live item should count as one MC participation opportunity."""
    kind = str(prompt_kind or "").strip().lower()
    if kind in _MC_KINDS:
        return True
    payload = _parse_item_json(item_json)
    qtype = str(payload.get("type") or payload.get("question_type") or "").strip().lower()
    if qtype in _MC_KINDS or qtype == "multiple_choice":
        return True
    return False


def item_participation_weight(
    prompt_kind: str,
    *,
    publish_mode: str = "individual",
    item_json: Any = None,
) -> int:
    """Return 1 for MC questions, else 0 (individual or group publish modes)."""
    mode = str(publish_mode or "individual").strip().lower()
    if mode not in {"individual", *_GROUP_PUBLISH}:
        return 0
    return 1 if item_is_multiple_choice(prompt_kind, item_json) else 0


def item_matches_scope(publish_mode: str, *, include_individual: bool, include_group: bool) -> bool:
    """Whether a deck item counts toward the active Individual/Group filters."""
    mode = str(publish_mode or "individual").strip().lower()
    if mode == "individual":
        return include_individual
    if mode in _GROUP_PUBLISH:
        return include_group
    return False


def count_participation_opportunities(
    items: list[dict[str, Any]],
    *,
    include_individual: bool,
    include_group: bool,
) -> int:
    """Sum deck weights for items that match the scope checkboxes."""
    total = 0
    for row in items:
        if not item_matches_scope(
            str(row.get("publish_mode") or "individual"),
            include_individual=include_individual,
            include_group=include_group,
        ):
            continue
        total += item_participation_weight(
            str(row.get("prompt_kind") or ""),
            publish_mode=str(row.get("publish_mode") or "individual"),
            item_json=row.get("item_json"),
        )
    return total


def participation_fraction(score: float, opportunities: int) -> str:
    """Format ``score/opportunities`` or em dash when no opportunities."""
    if opportunities <= 0:
        return "—"
    return f"{_format_score(score)}/{opportunities}"


def participation_percent(
    score: float,
    opportunities: int,
    *,
    session_ended_at: str | None = None,
) -> int | None:
    """Percent for display; legacy sessions cap at 100% when score exceeds opportunities."""
    if opportunities <= 0:
        return None
    raw = 100.0 * float(score) / float(opportunities)
    if _legacy_cap_applies(session_ended_at) and raw > 100.0:
        raw = 100.0
    return int(round(raw))


def format_participation_cell(
    score: float,
    opportunities: int,
    mode: str,
    *,
    session_ended_at: str | None = None,
) -> str:
    """Render one cell as points, fraction, or percent."""
    token = str(mode or "points").strip().lower()
    if token == "fraction":
        return participation_fraction(score, opportunities)
    if token in {"percent", "pct", "%"}:
        pct = participation_percent(score, opportunities, session_ended_at=session_ended_at)
        if pct is None:
            return participation_fraction(score, opportunities)
        return f"{pct}%"
    return _format_score(score)


def module_number(live_module: str) -> int:
    """Parse ``M3`` → 3 for labels."""
    text = str(live_module or "").strip().upper()
    digits = re.sub(r"^M", "", text)
    return int(digits) if digits.isdigit() else 0


def module_finish_label(live_module: str) -> str:
    """Frozen column / button label for a completed module."""
    return f"Module {module_number(live_module)} total"


def _format_score(value: float) -> str:
    n = float(value)
    if abs(n - round(n)) < 1e-9:
        return str(int(round(n)))
    return str(round(n * 10) / 10)


def _legacy_cap_applies(ended_at: str | None) -> bool:
    if not ended_at:
        return False
    text = str(ended_at).strip()
    if not text:
        return False
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return when.date() < LEGACY_PERCENT_CAP_BEFORE

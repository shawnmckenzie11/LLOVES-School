"""MCK-60: engagement level rubric (L1–L4) for end-of-class summary.

Placeholder thresholds and weights until Live-Class Designer finalizes copy.
Separate from MCK-198 participation percent bands in ``participation.py``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from engagement_rollup import GROUP_SHARE_SIGNALS, ITEM_SIGNALS

_CONFIG_PATH = Path(__file__).with_name("engagement_rubric.json")


@lru_cache(maxsize=1)
def load_rubric() -> dict[str, Any]:
    """Return the engagement rubric config (cached)."""
    raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    levels = sorted(
        (
            (int(row["level"]), float(row["min_score"]))
            for row in raw.get("levels") or []
        ),
        reverse=True,
    )
    raw["_levels_sorted"] = levels
    return raw


def level_for_score(score: float | None, rubric: dict[str, Any] | None = None) -> int | None:
    """Map a weighted 0–1 score to engagement L1–L4."""
    if score is None:
        return None
    cfg = rubric or load_rubric()
    for level, floor in cfg.get("_levels_sorted") or []:
        if score >= floor:
            return int(level)
    return 1


def format_engagement_level(
    level: int | None,
    rubric: dict[str, Any] | None = None,
    *,
    set_by_teacher: bool = False,
) -> str:
    """Teacher-facing label: engagement level + draft or teacher-set marker."""
    if level is None:
        return "Engagement —"
    cfg = rubric or load_rubric()
    if set_by_teacher:
        suffix = "Set by teacher"
    else:
        suffix = str(cfg.get("level_label") or "draft level").strip()
    return f"Engagement L{int(level)} ({suffix})"


def component_score(signal: str, row: dict[str, Any], rubric: dict[str, Any]) -> float | None:
    """Normalize one rollup cell to 0–1 for weighting."""
    count = int(row.get("count") or 0)
    if signal in ITEM_SIGNALS:
        offered = row.get("offered")
        if offered is None:
            return None
        offered_n = int(offered)
        if offered_n <= 0:
            return 0.0
        return min(1.0, count / offered_n)
    if signal in GROUP_SHARE_SIGNALS:
        share = row.get("share_of_group")
        if share is None:
            return 0.0 if count <= 0 else None
        return min(1.0, max(0.0, float(share)))
    caps = rubric.get("signal_caps") or {}
    cap = int(caps.get(signal) or 1)
    if cap <= 0:
        return 0.0
    return min(1.0, count / cap)


def weighted_score(
    cells: dict[str, dict[str, Any]], rubric: dict[str, Any] | None = None
) -> tuple[float | None, int]:
    """Return ``(score, evidence_count)`` from student-scope rollup cells."""
    cfg = rubric or load_rubric()
    weights = cfg.get("signal_weights") or {}
    evidence = 0
    weighted = 0.0
    total_weight = 0.0
    for signal, weight in weights.items():
        w = float(weight)
        if w <= 0:
            continue
        row = cells.get(signal)
        if not row:
            continue
        evidence += int(row.get("count") or 0)
        part = component_score(str(signal), row, cfg)
        if part is None:
            continue
        weighted += w * part
        total_weight += w
    if total_weight <= 0:
        return None, evidence
    return weighted / total_weight, evidence


def apply_evidence_floor(level: int | None, evidence_count: int, rubric: dict[str, Any] | None = None) -> int | None:
    """Cap suggested level when total evidence is below the rubric floor."""
    if level is None:
        return None
    cfg = rubric or load_rubric()
    floor = int(cfg.get("evidence_floor") or 0)
    if floor > 0 and evidence_count < floor:
        return min(int(level), 1)
    return int(level)

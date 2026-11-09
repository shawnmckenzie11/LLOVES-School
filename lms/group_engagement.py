"""Participation points for a completed group question (MCK-442).

A real live class records one row per student when the class ends.
A keyed rank sent all correct, without Check our ranking, stores 2
on a question whose maximum is 1 (shown 2/1). Every other rank
completion stores 1 (shown 1/1). An unsent rank is 0 and is not a
completion. Catch-up stores at most 1. Rows already stored on the
doubled scale (possible 2, earned 0, 2, or 4) stay as written.
Teacher totals show those rows in the same 1-point terms: 4/2 as
2/1, 2/2 as 1/1, and 0/2 as 0/1. Nothing in this module rewrites a
row. A group multiple-choice send is 1 on a question whose maximum
is 1.

This module is pure. It does not read the database or the answer order.
"""

from __future__ import annotations

import json
from typing import Any

# Ranking questions are worth 1. A no-check all-correct send earns 2.
RANK_POSSIBLE = 1
RANK_EARNED_BASE = 1
RANK_EARNED_BONUS = 2
# MCK-301 stored the same ratios with both numbers doubled. Those rows
# stay in the database. Display halves them; writers do not.
RANK_DOUBLED_POSSIBLE = 2
# A matching group multiple-choice send is one engagement point.
MC_POSSIBLE = 1
MC_EARNED = 1


def format_fraction(earned: int, possible: int) -> str:
    """Return ``earned/possible``, including ``0/0``, ``1/1``, and ``2/1``.

    Args:
        earned: Points recorded for the student.
        possible: Points offered. A ranking question offers 1. A group
            multiple-choice question offers 1. A no-check rank can earn 2.
    """
    return f"{int(earned)}/{int(possible)}"


def member_ids_from_json(raw: Any) -> list[int]:
    """Parse a send-time roster snapshot. Bad JSON is an empty list.

    Args:
        raw: ``engagement_member_ids_json`` text or an already-parsed list.
    """
    if isinstance(raw, list):
        parsed = raw
    else:
        try:
            parsed = json.loads(raw or "[]")
        except (TypeError, ValueError):
            return []
    if not isinstance(parsed, list):
        return []
    out: list[int] = []
    for item in parsed:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return sorted(set(out))


def check_message(right: int, total: int) -> str:
    """Count-only check chip. Names no item and no spot.

    Args:
        right: Spots that match the answer order.
        total: Spots in the answer order.
    """
    return f"Checked: {int(right)} of {int(total)} in the right spot"


def send_earned(*, keyed: bool, all_correct: bool, check_used: bool) -> int:
    """Points for one completed group rank. The question maximum is 1.

    A keyed rank sent all correct, without a check, is 2. Every other
    completion is 1. An unsent question is not passed here.

    Args:
        keyed: The question has an answer order.
        all_correct: The sent order matches that answer order in every spot.
        check_used: The team already used Check our ranking.
    """
    if keyed and all_correct and not check_used:
        return RANK_EARNED_BONUS
    return RANK_EARNED_BASE


def catchup_earned(current: int | None) -> int:
    """Catch-up completion for a group rank. The saved cap is 1.

    A student who already earned 1 or more keeps that value, including a
    no-check bonus of 2 or a doubled-scale 4. Anyone else earns 1.
    Catch-up never writes the no-check bonus. This does not see
    ``possible``. A doubled-scale completion is left stored by the caller.

    Args:
        current: Points already stored for this question, if any.
    """
    if current is not None and int(current) >= RANK_EARNED_BASE:
        return int(current)
    return RANK_EARNED_BASE


def legacy_doubled_rank(possible: int | None, kind: str) -> bool:
    """True when a rank row is stored on the doubled MCK-301 scale.

    Those rows use possible 2. Earned 4, 2, and 0 are the doubled forms
    of 2/1, 1/1, and 0/1. They are not rewritten. The forward scale is
    possible 1, so a new rank is not this row. Multiple choice is also
    possible 1 and is not this scale.

    Args:
        possible: Stored ``group_engagement.possible``, if a row exists.
        kind: ``group_engagement_items.prompt_kind``.
    """
    if str(kind or "") != "rank" or possible is None:
        return False
    return int(possible) == RANK_DOUBLED_POSSIBLE


def display_points(earned: int, possible: int, kind: str) -> tuple[int, int]:
    """Normalize one stored row for teacher totals. Does not write.

    A doubled rank (possible 2) is shown in 1-point terms. Earned 4
    displays as 2, earned 2 as 1, and earned 0 as 0, each with 1
    possible, so 4/2 and 2/1 stay the same ratio. The forward scale
    (possible 1) and multiple choice pass through. An unrecognized
    earned value on the doubled scale is shown unchanged so a bad row
    is not given a new meaning.

    Args:
        earned: Stored points for this question.
        possible: Stored maximum for this question.
        kind: ``group_engagement_items.prompt_kind``.

    Returns:
        ``(earned, possible)`` to add into a displayed total.
    """
    if legacy_doubled_rank(possible, kind):
        stored = int(earned)
        shown = {0: 0, 2: RANK_EARNED_BASE, 4: RANK_EARNED_BONUS}.get(stored)
        if shown is None:
            return stored, int(possible)
        return shown, RANK_POSSIBLE
    return int(earned), int(possible)


def match_tally(picks: list[str], total: int) -> dict[str, Any]:
    """How many present members currently share one multiple-choice pick.

    ``matched`` is the size of the largest identical pick. ``ready`` is
    true only when every present member has that same pick. ``choice``
    is that pick when ready, otherwise empty.

    Args:
        picks: Pick text for present members, empty when someone has
            not picked yet.
        total: Present members. Absent members are not included.
    """
    filled = [str(pick).strip() for pick in picks if str(pick).strip()]
    best = 0
    if filled:
        counts: dict[str, int] = {}
        for pick in filled:
            counts[pick] = counts.get(pick, 0) + 1
        best = max(counts.values())
    size = max(int(total), 0)
    ready = bool(size) and best == size and len(set(filled)) == 1 and len(filled) == size
    shared = filled[0] if ready else ""
    return {"matched": best, "total": size, "ready": ready, "choice": shared}


def match_reason(matched: int, total: int) -> str:
    """Disabled-send line until every present member has the same pick.

    Args:
        matched: Members who currently share the largest pick.
        total: Present members.
    """
    return f"Everyone needs the same pick ({int(matched)} of {int(total)} match)"

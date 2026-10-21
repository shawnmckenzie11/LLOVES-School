"""Participation points for a completed group question (MCK-301).

A real live class records one row per student when the class ends.
A keyed rank sent all correct, without Check our ranking, is 4 on a
question whose maximum is 2. Every other rank completion is 2.
Catch-up never writes 4. A rank row still stored as 1 possible is
the older scale and is already finished: catch-up does not offer it
and nothing rewrites it. Teacher totals show that row on the 2-point
scale: earned 2 displays as 4, earned 1 as 2, and earned 0 as 0. A group multiple-choice send is 1 on a question whose maximum
is 1.

This module is pure. It does not read the database or the answer order.
"""

from __future__ import annotations

import json
from typing import Any

# Ranking questions are worth 2. The no-check bonus doubles that to 4.
RANK_POSSIBLE = 2
RANK_EARNED_BASE = 2
RANK_EARNED_BONUS = 4
# A matching group multiple-choice send is one engagement point.
MC_POSSIBLE = 1
MC_EARNED = 1


def format_fraction(earned: int, possible: int) -> str:
    """Return ``earned/possible``, including ``0/0`` and ``4/2``.

    Args:
        earned: Points recorded for the student.
        possible: Points offered. A ranking question offers 2. A group
            multiple-choice question offers 1.
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
    """Points for one completed group rank. The question maximum is 2.

    A keyed rank sent all correct, without a check, is 4. Every other
    completion is 2. An unsent question is not passed here.

    Args:
        keyed: The question has an answer order.
        all_correct: The sent order matches that answer order in every spot.
        check_used: The team already used Check our ranking.
    """
    if keyed and all_correct and not check_used:
        return RANK_EARNED_BONUS
    return RANK_EARNED_BASE


def catchup_earned(current: int | None) -> int:
    """Catch-up completion for a group rank. Never raises a score to 4.

    A student who already earned 2 or 4 keeps that value. Anyone else
    earns 2. This does not see ``possible``. A stored rank of 1 possible
    is the older scale and is handled by ``old_rank_scale``, not here.

    Args:
        current: Points already stored for this question, if any.
    """
    if current is not None and int(current) >= RANK_EARNED_BASE:
        return int(current)
    return RANK_EARNED_BASE


def old_rank_scale(possible: int | None, kind: str) -> bool:
    """True when a rank row is still stored on the one-point scale.

    Those rows (1 possible, from the scale in #388) are already
    finished. Catch-up must not offer them, and no writer may change
    their earned or possible values. Multiple choice is also 1 possible
    and is not this scale.

    Args:
        possible: Stored ``group_engagement.possible``, if a row exists.
        kind: ``group_engagement_items.prompt_kind``.
    """
    if str(kind or "") != "rank" or possible is None:
        return False
    return int(possible) == 1


def display_points(earned: int, possible: int, kind: str) -> tuple[int, int]:
    """Normalize one stored row for teacher totals. Does not write.

    A rank row stored with 1 possible stays in the database and is shown
    on the 2-point scale. Earned 2 displays as 4, earned 1 as 2, and
    earned 0 as 0, each with 2 possible. An old 2/1 plus a new 4/2
    therefore totals 8/4. Multiple choice and the current rank scale
    pass through unchanged.

    Args:
        earned: Stored points for this question.
        possible: Stored maximum for this question.
        kind: ``group_engagement_items.prompt_kind``.

    Returns:
        ``(earned, possible)`` to add into a displayed total.
    """
    if old_rank_scale(possible, kind):
        stored = int(earned)
        shown = {0: 0, 1: RANK_EARNED_BASE, 2: RANK_EARNED_BONUS}.get(stored, stored * 2)
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

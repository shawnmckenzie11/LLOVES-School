"""Participation points for a completed group rank (MCK-293).

A real live class records one row per student when the class ends.
Sending a keyed rank that is entirely right, without using Check our
ranking, is 2 points on a question whose maximum is 1. Every other
completion is 1. Catch-up never writes the extra point.

This module is pure. It does not read the database or the answer order.
"""

from __future__ import annotations

import json
from typing import Any


def format_fraction(earned: int, possible: int) -> str:
    """Return ``earned/possible``, including ``0/0`` and ``2/1``.

    Args:
        earned: Points recorded for the student.
        possible: Questions offered. Stays 1 per question even when
            ``earned`` is 2.
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
    """Count-only check feedback. Names no item and no spot.

    Args:
        right: Spots that match the answer order.
        total: Spots in the answer order.
    """
    return f"{int(right)} of {int(total)} in the right spot."


def send_earned(*, keyed: bool, all_correct: bool, check_used: bool) -> int:
    """Points for one completed group rank. The question maximum stays 1.

    A keyed rank sent all correct, without a check, is 2. Every other
    completion is 1. An unsent question is not passed here.

    Args:
        keyed: The question has an answer order.
        all_correct: The sent order matches that answer order in every spot.
        check_used: The team already used Check our ranking.
    """
    if keyed and all_correct and not check_used:
        return 2
    return 1


def catchup_earned(current: int | None) -> int:
    """Catch-up completion. Never raises a 0 or a miss to 2.

    A student who already earned 1 or 2 keeps that value. Anyone else
    earns 1.

    Args:
        current: Points already stored for this question, if any.
    """
    if current is not None and int(current) >= 1:
        return int(current)
    return 1

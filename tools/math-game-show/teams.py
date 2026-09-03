#!/usr/bin/env python3
"""Random and balanced team assignment for Math Game Show."""

from __future__ import annotations

import random
from typing import Any, Sequence, TypeVar

T = TypeVar("T")


def assign_random(items: Sequence[T], n_teams: int, rng: random.Random | None = None) -> list[list[T]]:
    """Deal shuffled present students round-robin into ``n_teams`` teams.

    Args:
        items: Present students (or any hashable/objects).
        n_teams: Number of teams (>= 1).
        rng: Optional random source (tests inject a seeded Random).

    Returns:
        List of team member lists (may include empty teams if n > len(items)).

    Raises:
        ValueError: If ``n_teams`` is less than 1.
    """
    if n_teams < 1:
        raise ValueError("Need at least 1 team")
    pool = list(items)
    (rng or random).shuffle(pool)
    teams: list[list[T]] = [[] for _ in range(n_teams)]
    for index, item in enumerate(pool):
        teams[index % n_teams].append(item)
    return teams


def _target_sizes(n: int, k: int) -> tuple[int, int]:
    """Return ``(low, high)`` legal team sizes for ``n`` students on ``k`` teams.

    Sizes are always ``floor(n/k)`` or ``ceil(n/k)`` (off by one at most).

    Args:
        n: Number of present students.
        k: Number of teams.

    Returns:
        Inclusive legal size range.

    Raises:
        ValueError: If ``k`` is less than 1.
    """
    if k < 1:
        raise ValueError("Need at least 1 team")
    low = n // k
    high = low + 1 if n % k else low
    return low, high


def assign_balanced(
    items: Sequence[T],
    n_teams: int,
    totals: Sequence[float],
) -> list[list[T]]:
    """Deal even rosters, then swap to tighten career-score strength.

    Team sizes stay ``floor(n/k)`` or ``ceil(n/k)``. High TOTALs fill the
    weakest team that still has an open seat. Pairwise swaps and size-legal
    one-player moves then shrink the gap between strongest and weakest.

    Args:
        items: Present students.
        n_teams: Number of teams (>= 1).
        totals: Career individual TOTAL aligned with ``items``.

    Returns:
        List of team member lists.

    Raises:
        ValueError: If lengths disagree or ``n_teams`` is less than 1.
    """
    if n_teams < 1:
        raise ValueError("Need at least 1 team")
    if len(items) != len(totals):
        raise ValueError("items and totals must be the same length")
    values = [float(total) for total in totals]
    n = len(items)
    extra = n % n_teams
    base = n // n_teams
    caps = [base + (1 if team < extra else 0) for team in range(n_teams)]
    ranked = sorted(range(n), key=lambda i: (-values[i], i))
    buckets: list[list[int]] = [[] for _ in range(n_teams)]
    scores = [0.0] * n_teams
    for index in ranked:
        open_seats = [team for team in range(n_teams) if len(buckets[team]) < caps[team]]
        slot = min(open_seats, key=lambda team: (scores[team], len(buckets[team]), team))
        buckets[slot].append(index)
        scores[slot] = round(scores[slot] + values[index], 1)
    low, high = _target_sizes(n, n_teams)
    _tighten_team_totals(buckets, scores, values, low, high)
    return [[items[index] for index in bucket] for bucket in buckets]


def _team_spread(scores: Sequence[float]) -> float:
    """Return the gap between the strongest and weakest team.

    Args:
        scores: Current team strength totals.
    """
    return round(max(scores) - min(scores), 1)


def _tighten_team_totals(
    buckets: list[list[int]],
    scores: list[float],
    values: Sequence[float],
    low: int,
    high: int,
) -> None:
    """Swap or move students while it reduces max-minus-min team strength.

    A one-player move is allowed only when the donor stays at least ``low``
    and the receiver stays at most ``high``, so roster sizes remain even.

    Args:
        buckets: Team slots holding indexes into ``values``.
        scores: Running strength per team (mutated in place).
        values: Career totals aligned with student indexes.
        low: Smallest legal team size.
        high: Largest legal team size.
    """
    improved = True
    while improved:
        improved = False
        current = _team_spread(scores)
        best: tuple[float, str, int, int, int, int, float, float] | None = None
        n_teams = len(buckets)
        for t1 in range(n_teams):
            for t2 in range(n_teams):
                if t1 == t2:
                    continue
                for i1, idx1 in enumerate(buckets[t1]):
                    val1 = values[idx1]
                    if len(buckets[t1]) > low and len(buckets[t2]) < high:
                        s1 = round(scores[t1] - val1, 1)
                        s2 = round(scores[t2] + val1, 1)
                        trial = list(scores)
                        trial[t1] = s1
                        trial[t2] = s2
                        spread = _team_spread(trial)
                        if spread < current - 1e-9:
                            gain = current - spread
                            if best is None or gain > best[0]:
                                best = (gain, "move", t1, i1, t2, 0, s1, s2)
                    if t2 <= t1:
                        continue
                    for i2, idx2 in enumerate(buckets[t2]):
                        val2 = values[idx2]
                        s1 = round(scores[t1] - val1 + val2, 1)
                        s2 = round(scores[t2] - val2 + val1, 1)
                        trial = list(scores)
                        trial[t1] = s1
                        trial[t2] = s2
                        spread = _team_spread(trial)
                        if spread < current - 1e-9:
                            gain = current - spread
                            if best is None or gain > best[0]:
                                best = (gain, "swap", t1, i1, t2, i2, s1, s2)
        if best is None:
            return
        _apply_tighten(buckets, scores, best)
        improved = True


def _apply_tighten(
    buckets: list[list[int]],
    scores: list[float],
    move: tuple[float, str, int, int, int, int, float, float],
) -> None:
    """Apply one swap or transfer chosen by :func:`_tighten_team_totals`.

    Args:
        buckets: Team slots holding student indexes.
        scores: Running strength per team.
        move: Packed ``(gain, kind, t1, i1, t2, i2, s1, s2)``.
    """
    _gain, kind, t1, i1, t2, i2, s1, s2 = move
    if kind == "swap":
        buckets[t1][i1], buckets[t2][i2] = buckets[t2][i2], buckets[t1][i1]
    else:
        idx = buckets[t1].pop(i1)
        buckets[t2].append(idx)
    scores[t1] = s1
    scores[t2] = s2


def validate_team_count(n_teams: int, present_count: int) -> None:
    """Reject team counts that cannot run a game.

    Args:
        n_teams: Requested team count.
        present_count: Number of present students.

    Raises:
        ValueError: If the count is out of range.
    """
    if present_count < 1:
        raise ValueError("Mark at least one student present")
    if n_teams < 2:
        raise ValueError("Need at least 2 teams")
    if n_teams > present_count:
        raise ValueError("Cannot have more teams than present students")


def assign_manual(
    present_ids: Sequence[int],
    n_teams: int,
    assignments: Sequence[Any],
) -> list[list[int]]:
    """Place each present student on a teacher-chosen team.

    Args:
        present_ids: Students marked present.
        n_teams: Number of teams.
        assignments: ``{student_id, team_index}`` rows with 0-based team slots.

    Returns:
        List of team member id lists in team-index order.

    Raises:
        ValueError: Missing/extra students, a bad team index, or an empty team.
    """
    validate_team_count(n_teams, len(present_ids))
    present = {int(sid) for sid in present_ids}
    buckets: list[list[int]] = [[] for _ in range(int(n_teams))]
    seen: set[int] = set()
    for item in assignments:
        if not isinstance(item, dict):
            raise ValueError("Each assignment needs student_id and team_index")
        sid = int(item.get("student_id") or 0)
        if "team_index" not in item:
            raise ValueError("Each assignment needs a team_index")
        try:
            team_index = int(item["team_index"])
        except (TypeError, ValueError) as exc:
            raise ValueError("team_index must be a number") from exc
        if sid not in present:
            raise ValueError("Can only assign present students")
        if sid in seen:
            raise ValueError("Each student can only be on one team")
        if team_index < 0 or team_index >= int(n_teams):
            raise ValueError("team_index is out of range")
        seen.add(sid)
        buckets[team_index].append(sid)
    if present - seen:
        raise ValueError("Assign every present student to a team")
    empty = [index + 1 for index, members in enumerate(buckets) if not members]
    if empty:
        names = ", ".join(f"Team {n}" for n in empty)
        raise ValueError(f"Every team needs at least one student (empty: {names})")
    return buckets


TEAM_COLORS: tuple[str, ...] = (
    "#c8102e",
    "#0b3d91",
    "#ffb81c",
    "#00843d",
    "#7b2d8e",
    "#e87722",
    "#00a3e0",
    "#5c3317",
)


def color_for_team(sort_order: int) -> str:
    """Return an ESPN-bar color for a 0-based team index.

    Args:
        sort_order: Team slot (0 = Team 1).
    """
    return TEAM_COLORS[sort_order % len(TEAM_COLORS)]


def default_team_name(sort_order: int) -> str:
    """Default display name ``Team N`` (1-based).

    Args:
        sort_order: Team slot (0 = Team 1).
    """
    return f"Team {sort_order + 1}"


def membership_payload(teams: Sequence[Sequence[Any]]) -> list[dict[str, Any]]:
    """Describe an assignment as JSON-friendly team buckets.

    Args:
        teams: Output of :func:`assign_random` or :func:`assign_balanced`
            where members are student id ints (or objects with ``id``).

    Returns:
        List of ``{sort_order, name, color, student_ids}``.
    """
    payload: list[dict[str, Any]] = []
    for order, members in enumerate(teams):
        student_ids: list[int] = []
        for member in members:
            if isinstance(member, int):
                student_ids.append(member)
            else:
                student_ids.append(int(member["id"] if isinstance(member, dict) else member.id))
        payload.append(
            {
                "sort_order": order,
                "name": default_team_name(order),
                "color": color_for_team(order),
                "student_ids": student_ids,
            }
        )
    return payload

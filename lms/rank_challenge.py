"""MCK-171 Team challenge on a group rank (shape B "Lock-in").

Students never see the word "race"; internally the item flag is still
``group_rank_race`` (MCK-166 naming). Values and copy: Wonder v3
(``rank-race-points-copy-wonder-v3.md``). UX: Mobbin Sites
``rank-race-mck171-ia-v0.md`` slices R2-R6.

Pure helpers only (no database, no Flask), so the rules can be unit tested:

* the per-member "I agree" state for Rank together, stored on the team
  row's draft (``proposed_answer_json``) as ``agree`` (roster ids) and
  ``agree_reset`` (an order change cleared someone's agree);
* when the agrees lock the order in (every *present* member agreed).

Speed counts nowhere: no lock-in time is ever compared between teams.
"""

from __future__ import annotations

from typing import Any, Iterable

#: ``item_json`` flag for the teacher's "Team challenge" toggle.
RACE_FLAG = "group_rank_race"
#: ``item_json`` stamp written at Close: ``"timer"`` when the SessionTimer's
#: "Close answers at 0:00" closed it, else ``"teacher"``. Picks the phone's
#: unlocked-at-Close line (race.timesup vs race.closed).
RACE_CLOSED_BY = "group_rank_race_closed_by"
#: Reveal steps (``live_rank_race_steps.step``): 0 = nothing, 1..n = spot
#: rows, n+1 = points, n+2 = podium. Phones and game points wait for them so
#: the room never sees a total early. The step lives in its own table, not
#: ``item_json``, so a deck refresh can never rewrite it from a stale copy.


#: MCK-185: the coloured results run on every group answer-order rank (Team
#: challenge, Rank together, Take turns). Rows, then a podium by right spots;
#: no Points step and no automatic points. The teacher scores teams in the
#: "Score teams" pop-up (default ``BASE_PER_SPOT`` per right spot).
#: An answer order needs this many spots (same rule as the Team challenge
#: toggle in ``group_setup.js`` ``rankRaceSettings``).
MIN_KEY_SPOTS = 3
#: MCK-185 (Shawn, option B): nothing is awarded automatically on any group
#: answer-order rank. ``SchoolDB._rank_race_pays`` reads this and every
#: MCK-171 payout path (points step, End Game, End Live Class) checks that
#: one method, so turning the automatic payout back on is this one switch.
CHALLENGE_AUTO_PAYS = False
#: MCK-185 full-order notice timing: ``"reveal"`` shows "{team} put every item
#: in the right order." after Close & reveal; ``"lock"`` shows it as soon as
#: a team's final order (locked, sent, or last Take turns spot) is all right.
#: Drafts never count. Shawn chose ``"lock"`` (Oct 4); the flag stays so
#: ``"reveal"`` is one change away.
FULL_ORDER_WHEN = "lock"
FULL_ORDER_TIMINGS = ("reveal", "lock")


def full_order_visible(status: str, when: str | None = None) -> bool:
    """True when the full-order notice may show for an item in ``status``.

    Args:
        status: Lifecycle status (``active``, ``closed``, ...).
        when: ``"reveal"`` or ``"lock"`` (default: ``FULL_ORDER_WHEN``).
    """
    timing = when or FULL_ORDER_WHEN
    if timing == "lock":
        return status in {"active", "closed"}
    return status == "closed"


def full_order_timing(
    *, challenge: bool, rank_mode: str, results_on: bool, when: str | None = None
) -> str:
    """The timing that applies to one item (gate MED-1 on f0a15be).

    ``"lock"`` only holds where a final order really is final: a Team
    challenge lock-in (unlock is refused) or the last Take turns spot (Undo
    is refused). A plain Rank together send can be changed and resent while
    the question is open, so under ``"lock"`` it would let a team resend
    until the line appears (a right/wrong check before Close). That mode
    shows the line at Close instead. With live results off the line also
    waits for Close.

    Args:
        challenge: The item is a Team challenge.
        rank_mode: ``"together"`` or ``"turns"``.
        results_on: The item's Show Live Results setting.
        when: Override (default: ``FULL_ORDER_WHEN``).
    """
    timing = when or FULL_ORDER_WHEN
    if timing != "lock":
        return timing
    if not results_on:
        return "reveal"
    if challenge or str(rank_mode) == "turns":
        return "lock"
    return "reveal"


#: Highest "Score teams" award per team on one question.
MAX_AWARD_POINTS = 999


def _award_number(value: Any) -> float | None:
    """The raw "Score teams" points as a finite float, or ``None``."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def clamp_award_points(value: Any) -> int | None:
    """A "Score teams" points value as a whole number in 0..999.

    Numbers (and numeric text, as a form would send) are rounded and
    capped at 999. ``True``/``False``, other text, NaN, infinity and any
    negative amount give ``None`` (the route answers 400). Gate LOW-2 on
    2cc6a9e: a negative used to clamp to 0, which wiped an earlier award.

    Args:
        value: Raw JSON value.
    """
    number = _award_number(value)
    if number is None or number < 0:
        return None
    return min(MAX_AWARD_POINTS, int(round(number)))


def award_points_problem(value: Any) -> str | None:
    """Why ``value`` is refused as "Score teams" points, or ``None`` if fine."""
    number = _award_number(value)
    if number is None:
        return "Points must be a number"
    if number < 0:
        return "Points can't be negative"
    return None


def score_default(right: int) -> int:
    """The "Score teams" default: ``BASE_PER_SPOT`` per right spot."""
    return team_points(right)


def points_step(spots: int, *, with_points: bool = True) -> int | None:
    """Reveal step that shows points (after every spot row).

    Args:
        spots: Spots in the answer order.
        with_points: False for the spots results, which have no Points step.

    Returns:
        The step, or ``None`` when there is no Points step.
    """
    return int(spots) + 1 if with_points else None


def podium_step(spots: int, *, with_points: bool = True) -> int:
    """Reveal step that shows the podium (last step).

    Args:
        spots: Spots in the answer order.
        with_points: False for the spots results (rows, then the podium).
    """
    return int(spots) + (2 if with_points else 1)


def flag_on(question: Any, key: str, *, default: bool = False) -> bool:
    """Read one boolean ``item_json`` flag.

    Args:
        question: The item's ``item_json`` dict.
        key: Flag name.
        default: Value when the flag is missing.
    """
    if not isinstance(question, dict) or key not in question:
        return default
    raw = question.get(key)
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return bool(raw)


def agree_ids(proposed: Any) -> list[int]:
    """Roster ids that tapped "I agree" on the current draft.

    Args:
        proposed: The team row's parsed ``proposed_answer`` (or ``None``).
    """
    if not isinstance(proposed, dict):
        return []
    out: list[int] = []
    for raw in proposed.get("agree") or []:
        try:
            sid = int(raw)
        except (TypeError, ValueError):
            continue
        if sid not in out:
            out.append(sid)
    return out


def with_agree(proposed: dict[str, Any], student_id: int, agree: bool) -> dict[str, Any]:
    """Return a copy of the draft with one member's agree set or cleared.

    Any agree tap ends the "order changed" notice.

    Args:
        proposed: The team's rank draft (``kind: rank``).
        student_id: Roster id tapping I agree / Not yet.
        agree: True for I agree, False for Not yet.
    """
    body = dict(proposed)
    ids = [sid for sid in agree_ids(proposed) if sid != int(student_id)]
    if agree:
        ids.append(int(student_id))
    body["agree"] = ids
    body.pop("agree_reset", None)
    return body


def clear_agrees(proposed: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Drop every agree after an order change.

    Args:
        proposed: The new draft body.

    Returns:
        ``(body, cleared)``. ``cleared`` is True when at least one member
        had agreed; the body then carries ``agree_reset: True`` so every
        teammate reads "The order changed. Agree again when you're ready."
    """
    body = dict(proposed)
    had = bool(agree_ids(body))
    body.pop("agree", None)
    if had:
        body["agree_reset"] = True
    return body, had


def present_with(present_ids: Iterable[int], student_id: int | None = None) -> list[int]:
    """Present teammates, plus the tapping student (presence can lag a tap).

    Args:
        present_ids: Currently joined team members.
        student_id: The member writing now, if any.
    """
    out = sorted({int(sid) for sid in present_ids})
    if student_id is not None and int(student_id) not in out:
        out.append(int(student_id))
        out.sort()
    return out


def all_agreed(agreed: Iterable[int], present_ids: Iterable[int]) -> bool:
    """True when every present member agreed. Absent members never block.

    Args:
        agreed: Roster ids that tapped I agree.
        present_ids: Present team members (see ``present_with``).
    """
    present = {int(sid) for sid in present_ids}
    return bool(present) and present.issubset({int(sid) for sid in agreed})


def agree_counts(agreed: Iterable[int], present_ids: Iterable[int]) -> tuple[int, int]:
    """``(k, m)`` for "{k} of {m} agree": agreed present members of present.

    Args:
        agreed: Roster ids that tapped I agree.
        present_ids: Present team members.
    """
    present = {int(sid) for sid in present_ids}
    agreed_set = {int(sid) for sid in agreed}
    return len(present & agreed_set), len(present)


# ---------------------------------------------------------------------------
# Results (R6): 2 points per right spot, nothing else. Ties share a step.
# ---------------------------------------------------------------------------

#: Wonder v3 ``BASE_PER_SPOT``: points per spot in the right place.
BASE_PER_SPOT = 2
#: Podium steps shown (top 3 distinct totals).
PODIUM_STEPS = 3


def team_points(right: int) -> int:
    """Team total: ``BASE_PER_SPOT`` per right spot. No bonus, no speed.

    Every member of the team gets this same total.

    Args:
        right: Spots in the right place.
    """
    return BASE_PER_SPOT * max(0, int(right))


def podium(
    teams: Iterable[dict[str, Any]], *, by: str = "points", drop_unscored: bool = False
) -> dict[str, Any]:
    """Top-3 podium by dense ranking; tied teams share a step.

    A team is on the podium when it was scored (it placed something) and
    has points (or, ``by="right"``, at least one right spot). Everyone else
    goes to "Also on the board", alphabetical, with no place number. Speed
    and lock-in time play no part.

    Args:
        teams: Dicts with ``team_id``, ``team_name``, ``points``, ``right``,
            ``scored``.
        by: ``points`` (Team challenge) or ``right`` (MCK-185 spots results).
        drop_unscored: Leave teams that sent nothing (absent teams too) off
            the board entirely instead of listing them under "Also on the
            board" (MCK-185 spots results).

    Returns:
        ``{"steps": [{"step", "points", "team_ids"}], "others": [team_id]}``.
        ``steps`` is ordered 1, 2, 3 (fewer when there are fewer totals).
        With ``by="right"`` each step also carries ``right``; its
        ``points`` is the same count (no points are paid).
    """
    field = "right" if by == "right" else "points"
    rows = list(teams)
    totals = sorted(
        {int(row.get(field) or 0) for row in rows if row.get("scored") and int(row.get(field) or 0) > 0},
        reverse=True,
    )[:PODIUM_STEPS]
    steps = []
    on: set[int] = set()
    for index, total in enumerate(totals):
        ids = sorted(
            (
                row
                for row in rows
                if row.get("scored") and int(row.get(field) or 0) == total
            ),
            key=lambda row: str(row.get("team_name") or "").casefold(),
        )
        step: dict[str, Any] = {
            "step": index + 1,
            "points": total,
            "team_ids": [int(row["team_id"]) for row in ids],
        }
        if field == "right":
            step["right"] = total
        steps.append(step)
        on.update(int(row["team_id"]) for row in ids)
    others = [
        int(row["team_id"])
        for row in sorted(rows, key=lambda row: str(row.get("team_name") or "").casefold())
        if int(row["team_id"]) not in on and (row.get("scored") or not drop_unscored)
    ]
    return {"steps": steps, "others": others}

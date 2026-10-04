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


#: MCK-185: the coloured results also run on a group answer-order rank that
#: is not a Team challenge ("spots" results). Same rows and podium, but no
#: Points step and no automatic points: the podium ranks right spots and the
#: teacher awards by hand through the usual team scoring chips.
#: An answer order needs this many spots (same rule as the Team challenge
#: toggle in ``group_setup.js`` ``rankRaceSettings``).
MIN_KEY_SPOTS = 3
#: Team challenge pays ``BASE_PER_SPOT`` per right spot by itself (MCK-171).
#: Set False to make a challenge behave like the spots results (points become
#: a hint and the teacher awards by hand); ``SchoolDB._rank_race_pays`` reads
#: it, and every payout path checks that one method.
CHALLENGE_AUTO_PAYS = True


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

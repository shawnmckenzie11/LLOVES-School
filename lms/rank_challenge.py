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

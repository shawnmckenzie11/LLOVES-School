"""RANK live questions: option ids, tap/undo, display-only Borda order, and
the optional answer order (``rank_key``) with its spot-by-spot score.

Borda points order the class list. They never award participation or game
points. Callers that paint ``/state`` must use the safe builders here so a
bad order cannot raise.
"""

from __future__ import annotations

from typing import Any

MIN_RANK_OPTIONS = 3
MAX_RANK_OPTIONS = 6
MAX_RANK_VOTES = 64
MAX_OPTION_ID_LEN = 40
MAX_OPTION_LABEL_LEN = 240


def is_rank_prompt(prompt: Any) -> bool:
    """True when a live prompt is a rank question.

    Args:
        prompt: Prompt row with ``kind`` and ``payload``, or a payload dict.
    """
    if not isinstance(prompt, dict):
        return False
    kind = str(prompt.get("kind") or "").strip().lower()
    payload = prompt.get("payload") if isinstance(prompt.get("payload"), dict) else prompt
    if not isinstance(payload, dict):
        payload = {}
    token = str(payload.get("type") or payload.get("kind") or "").strip().lower()
    return kind == "rank" or token == "rank"


def build_rank_options(raw: Any) -> list[dict[str, str]]:
    """Validate authored rank options into stable id/label rows.

    Args:
        raw: A list of labels or ``{id, label}`` objects.

    Returns:
        Three to six options. Ids are ``o1``… when the caller does not
        supply a unique id.

    Raises:
        ValueError: When the list is short, long, blank, or duplicated.
    """
    if not isinstance(raw, list):
        raise ValueError("rank needs 3 to 6 options")
    cleaned: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        opt_id = ""
        if isinstance(item, dict):
            text = str(item.get("label") or item.get("text") or "").strip()
            opt_id = str(item.get("id") or "").strip()[:MAX_OPTION_ID_LEN]
        else:
            text = str(item or "").strip()
        if not text:
            continue
        text = text[:MAX_OPTION_LABEL_LEN]
        key = text.casefold()
        if key in seen:
            raise ValueError("options must be unique")
        seen.add(key)
        cleaned.append((opt_id, text))
    if len(cleaned) < MIN_RANK_OPTIONS or len(cleaned) > MAX_RANK_OPTIONS:
        raise ValueError("rank needs 3 to 6 options")
    rows: list[dict[str, str]] = []
    used: set[str] = set()
    for index, (opt_id, text) in enumerate(cleaned, start=1):
        token = opt_id if opt_id and opt_id not in used else f"o{index}"
        if token in used:
            token = f"o{index}"
        used.add(token)
        rows.append({"id": token, "label": text})
    return rows


def safe_rank_options(raw: Any) -> list[dict[str, str]]:
    """Return authored rank options, or an empty list when the payload is bad.

    Args:
        raw: ``rank_options``, or a label/object list.
    """
    try:
        return build_rank_options(raw)
    except (TypeError, ValueError):
        if not isinstance(raw, list):
            return []
        rows: list[dict[str, str]] = []
        seen: set[str] = set()
        for index, item in enumerate(raw[:MAX_RANK_OPTIONS], start=1):
            if isinstance(item, dict):
                text = str(item.get("label") or item.get("text") or "").strip()
                opt_id = str(item.get("id") or "").strip()[:MAX_OPTION_ID_LEN]
            else:
                text = str(item or "").strip()
                opt_id = ""
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            token = opt_id or f"o{index}"
            if any(row["id"] == token for row in rows):
                token = f"o{index}"
            rows.append({"id": token, "label": text[:MAX_OPTION_LABEL_LEN]})
        return rows


def option_ids(options: list[dict[str, str]]) -> list[str]:
    """Return option ids in authored order, capped at six.

    Args:
        options: Rows from ``build_rank_options`` or ``safe_rank_options``.
    """
    ids: list[str] = []
    for row in options[:MAX_RANK_OPTIONS]:
        token = str(row.get("id") or "").strip()
        if token and token not in ids:
            ids.append(token)
    return ids


def parse_rank_order(
    raw: Any,
    allowed: list[str],
    *,
    complete: bool = False,
) -> list[str]:
    """Return a rank order of known option ids.

    A partial order is a unique subset. A complete order is a permutation.
    Removal never leaves gaps: position in the list is the rank.

    Args:
        raw: Submitted ``order`` list.
        allowed: Authored option ids.
        complete: When True, every option must appear once.

    Raises:
        ValueError: When the order is the wrong shape or uses a bad id.
    """
    allowed_ids = []
    for item in allowed[:MAX_RANK_OPTIONS]:
        token = str(item or "").strip()
        if token and token not in allowed_ids:
            allowed_ids.append(token)
    known = set(allowed_ids)
    if raw is None:
        parsed: list[Any] = []
    elif isinstance(raw, list):
        parsed = raw
    else:
        raise ValueError("order must be a list of option ids")
    if len(parsed) > MAX_RANK_OPTIONS:
        raise ValueError("order is too long")
    order: list[str] = []
    for item in parsed:
        token = str(item or "").strip()
        if not token or token not in known or token in order:
            raise ValueError("order must use each option once")
        order.append(token)
    if complete and (len(order) != len(allowed_ids) or set(order) != known):
        raise ValueError("rank every option before submitting")
    return order


def toggle_rank_order(
    order: list[str] | None,
    option_id: str,
    allowed: list[str],
) -> list[str]:
    """Apply one tap to a shared order and keep ranks contiguous.

    An unnumbered option gets the next number. A numbered option is removed
    and every later number shifts down by one.

    Args:
        order: Current option-id order. Position is the rank.
        option_id: Option the student tapped.
        allowed: Authored option ids.

    Raises:
        ValueError: When ``option_id`` is not one of the authored options.
    """
    allowed_ids: list[str] = []
    for item in allowed[:MAX_RANK_OPTIONS]:
        text = str(item or "").strip()
        if text and text not in allowed_ids:
            allowed_ids.append(text)
    current = parse_rank_order(order or [], allowed_ids, complete=False)
    token = str(option_id or "").strip()
    if token not in set(allowed_ids):
        raise ValueError("Choose one of the options.")
    if token in current:
        return [item for item in current if item != token]
    return [*current, token]


def format_rank_order(order: list[str], options: list[dict[str, str]]) -> str:
    """Return a one-line ``1 wifi · 2 socks`` label for an order.

    Args:
        order: Option ids, first place first.
        options: Authored rows with ``id`` and ``label``.
    """
    labels = {
        str(row.get("id") or ""): str(row.get("label") or "")
        for row in options
        if isinstance(row, dict)
    }
    parts: list[str] = []
    for index, opt_id in enumerate(order[:MAX_RANK_OPTIONS], start=1):
        label = labels.get(str(opt_id), str(opt_id))
        parts.append(f"{index} {label}")
    return " · ".join(parts)


def borda_class_order(
    votes: list[Any],
    options: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Order options by Borda count. Display only — not a student score.

    For ``n`` options, first place scores ``n - 1`` and last place scores 0.
    Ties share a rank (1, 2, 2, 4). Display order breaks ties by more
    first-place picks, then authored order. Votes that are not a full
    permutation are ignored. At most 64 votes are read.

    Args:
        votes: Submitted orders (option ids). One vote per team or student.
        options: Authored option rows.

    Returns:
        Display rows with points, first picks, shared rank, and bar percent.
        Zero votes stay in authored order with empty bars.
    """
    opts = [row for row in options[:MAX_RANK_OPTIONS] if isinstance(row, dict)]
    ids = option_ids(opts)
    n = len(ids)
    points = {opt_id: 0 for opt_id in ids}
    firsts = {opt_id: 0 for opt_id in ids}
    counted = 0
    for vote in list(votes or [])[:MAX_RANK_VOTES]:
        try:
            order = parse_rank_order(vote, ids, complete=True)
        except (TypeError, ValueError):
            continue
        counted += 1
        for place, opt_id in enumerate(order):
            points[opt_id] += n - 1 - place
            if place == 0:
                firsts[opt_id] += 1
    authored = {opt_id: index for index, opt_id in enumerate(ids)}
    display = sorted(ids, key=lambda opt_id: (-points[opt_id], -firsts[opt_id], authored[opt_id]))
    max_possible = counted * max(n - 1, 0)
    rows: list[dict[str, Any]] = []
    labels = {str(row.get("id") or ""): str(row.get("label") or "") for row in opts}
    for opt_id in display:
        better = sum(1 for other in ids if points[other] > points[opt_id])
        score = int(points[opt_id])
        pct = int(round(100.0 * score / max_possible)) if max_possible else 0
        rows.append(
            {
                "option_id": opt_id,
                "label": labels.get(opt_id, opt_id),
                "points": score,
                "first_picks": int(firsts[opt_id]),
                "rank": better + 1,
                "bar_pct": max(0, min(100, pct)),
            }
        )
    return rows


def rank_fingerprint(rows: list[dict[str, Any]], responded: int) -> int:
    """Stable bind key for a collate strip. Changes when rank or points change.

    Args:
        rows: Rows from ``borda_class_order``.
        responded: Count of orders included in the Borda sum.
    """
    sig = int(responded) & 0x7FFFFFFF
    for row in rows[:MAX_RANK_OPTIONS]:
        sig = (
            sig * 33
            + int(row.get("points") or 0)
            + int(row.get("rank") or 0) * 17
            + int(row.get("first_picks") or 0)
        ) & 0x7FFFFFFF
    return sig


def build_rank_tally(
    prompt: Any,
    *,
    responses: list[dict[str, Any]] | None = None,
    present: int = 0,
) -> dict[str, Any] | None:
    """Build an individual class-order tally. Never raises.

    Args:
        prompt: Live prompt row.
        responses: Parsed response rows. Only ``response.order`` is read.
        present: Students currently in the session.

    Returns:
        A rank tally, or ``None`` when the prompt is not rank.
    """
    if not is_rank_prompt(prompt):
        return None
    payload = prompt.get("payload") if isinstance(prompt, dict) and isinstance(prompt.get("payload"), dict) else {}
    if not isinstance(prompt, dict):
        return None
    options = safe_rank_options(
        payload.get("rank_options") or payload.get("options") or payload.get("choices")
    )
    if len(options) < MIN_RANK_OPTIONS:
        options = safe_rank_options(payload.get("options") or payload.get("choices"))
    ids = option_ids(options)
    votes: list[list[str]] = []
    for row in list(responses or [])[:MAX_RANK_VOTES]:
        if not isinstance(row, dict):
            continue
        answer = row.get("response") if isinstance(row.get("response"), dict) else {}
        if not isinstance(answer, dict):
            continue
        try:
            votes.append(parse_rank_order(answer.get("order"), ids, complete=True))
        except (TypeError, ValueError):
            continue
    class_order = borda_class_order(votes, options)
    responded = len(votes)
    try:
        present_n = max(0, int(present), responded)
    except (TypeError, ValueError):
        present_n = responded
    prompt_id = prompt.get("id")
    ref = str(payload.get("item_id") or "").strip()
    return {
        "kind": "rank",
        "prompt_ref": ref or (f"prompt-{int(prompt_id)}" if prompt_id not in (None, "") else ""),
        "prompt_id": int(prompt_id) if prompt_id not in (None, "") else None,
        "item_id": ref,
        "unit": "student",
        "responded": responded,
        "present": present_n,
        "response_count": responded,
        "rows": class_order,
        "class_order": class_order,
        "response_seq": rank_fingerprint(class_order, responded),
    }


# ---------------------------------------------------------------------------
# MCK-172 (race R1): optional answer order on a rank item.
#
# ``item_json.rank_key`` is the option ids in the right order. It is a
# teacher-only field (``TEACHER_ONLY_FIELDS``), so no student payload ever
# carries it. Borda display ignores it. Race scoring (later slices) reads it
# through ``rank_race_score``.
# ---------------------------------------------------------------------------


def parse_rank_key(raw: Any, options: list[dict[str, str]]) -> list[str] | None:
    """Validate an authored answer order against the item's options.

    Args:
        raw: ``None`` or ``[]`` for no answer order, else a list of option
            ids (``o2``) or zero-based option indices (``1`` or ``"1"``).
        options: Rows from ``build_rank_options`` / ``safe_rank_options``.

    Returns:
        Option ids in the right order, or ``None`` when no order was given.

    Raises:
        ValueError: When the order is not a permutation of the options.
    """
    if raw in (None, "", []):
        return None
    if not isinstance(raw, list):
        raise ValueError("answer order must list every option once")
    ids = option_ids(options)
    key: list[str] = []
    for item in raw:
        token = str(item if item is not None else "").strip()
        if token not in ids and token.isdigit() and int(token) < len(ids):
            token = ids[int(token)]
        if token not in ids or token in key:
            raise ValueError("answer order must list every option once")
        key.append(token)
    if len(key) != len(ids):
        raise ValueError("answer order must list every option once")
    return key


def safe_rank_key(raw: Any, options: list[dict[str, str]]) -> list[str] | None:
    """Read a stored answer order. A stale or bad key reads as no key.

    Args:
        raw: Stored ``rank_key``.
        options: The item's current rank option rows.
    """
    try:
        return parse_rank_key(raw, options)
    except (TypeError, ValueError):
        return None


def rank_race_score(order: Any, key: Any) -> dict[str, Any]:
    """Score one order against the answer order, spot by spot. Never raises.

    A spot is right only when the option at that position matches the key
    exactly. A partial order (an unsent draft) scores its filled spots;
    empty or missing spots are not right.

    Args:
        order: Option ids by position. May be short, or hold ``None``/``""``
            for empty spots.
        key: Answer order (option ids), e.g. from ``safe_rank_key``.

    Returns:
        ``{"right": int, "total": int, "spots": [bool]}`` with one spot per
        key position.
    """
    answer = [str(item or "").strip() for item in key] if isinstance(key, list) else []
    placed = list(order) if isinstance(order, list) else []
    spots: list[bool] = []
    for index, want in enumerate(answer):
        got = str(placed[index] or "").strip() if index < len(placed) else ""
        spots.append(bool(want) and got == want)
    return {"right": sum(spots), "total": len(answer), "spots": spots}


# ---------------------------------------------------------------------------
# MCK-155: "Take turns" group rank (pure helpers; the server owns the state).
#
# Rule (Shawn, approved): first come, no repeats. Anyone who has not placed
# yet takes the next spot. After that, nobody places again until every other
# present teammate has placed (or been skipped) since their own last turn.
# Absent members are not in ``active_ids``, so they never hold the group up.
# ---------------------------------------------------------------------------

RANK_MODE_TOGETHER = "together"
RANK_MODE_TURNS = "turns"
RANK_MODES = (RANK_MODE_TOGETHER, RANK_MODE_TURNS)


class TurnConflict(ValueError):
    """A take-turns write lost a race or is out of turn.

    Attributes:
        reason: ``turn_taken``, ``not_your_turn``, ``done``, or ``no_undo``.
        by: Roster id that placed the option, for ``turn_taken``.
    """

    def __init__(self, reason: str, message: str, by: int | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.by = by


def normalize_rank_mode(raw: Any) -> str:
    """Return ``together`` or ``turns`` (anything else is ``together``).

    Args:
        raw: Stored or posted mode token.
    """
    token = str(raw or "").strip().lower()
    return RANK_MODE_TURNS if token == RANK_MODE_TURNS else RANK_MODE_TOGETHER


def empty_turns_state() -> dict[str, Any]:
    """A fresh take-turns draft."""
    return {"kind": "rank", "mode": RANK_MODE_TURNS, "order": [], "events": [], "rev": 0, "complete": False}


def turns_state(raw: Any, allowed: list[str]) -> dict[str, Any]:
    """Normalize a stored take-turns draft. Bad rows become a fresh draft.

    Args:
        raw: ``proposed_answer`` dict (or anything).
        allowed: Option ids for the item.
    """
    if not isinstance(raw, dict) or raw.get("mode") != RANK_MODE_TURNS:
        return empty_turns_state()
    events: list[list[Any]] = []
    order: list[str] = []
    for event in raw.get("events") or []:
        if not isinstance(event, (list, tuple)) or len(event) < 2:
            continue
        try:
            sid = int(event[0])
        except (TypeError, ValueError):
            continue
        kind = str(event[1])
        if kind == "place":
            opt = str(event[2] if len(event) > 2 else "")
            if opt not in allowed or opt in order:
                continue
            order.append(opt)
            events.append([sid, "place", opt])
        elif kind == "skip":
            events.append([sid, "skip", None])
    try:
        rev = int(raw.get("rev") or 0)
    except (TypeError, ValueError):
        rev = 0
    return {
        "kind": "rank",
        "mode": RANK_MODE_TURNS,
        "order": order,
        "events": events,
        "rev": rev,
        "complete": bool(allowed) and len(order) == len(allowed),
    }


def _last_event_index(events: list[list[Any]], student_id: int) -> int:
    for index in range(len(events) - 1, -1, -1):
        if int(events[index][0]) == int(student_id):
            return index
    return -1


def turn_can_place(state: dict[str, Any], student_id: int, active_ids: list[int]) -> bool:
    """True when this student may place the next option now.

    Args:
        state: Normalized take-turns draft.
        student_id: Roster id asking.
        active_ids: Present teammates (absent members are auto-skipped).
    """
    if state.get("complete"):
        return False
    events = state.get("events") or []
    last = _last_event_index(events, student_id)
    if last < 0:
        return True
    for other in active_ids:
        if int(other) == int(student_id):
            continue
        if _last_event_index(events, other) <= last:
            return False
    return True


def turn_next_ids(state: dict[str, Any], active_ids: list[int]) -> list[int]:
    """Present teammates who may place right now, in ``active_ids`` order.

    Args:
        state: Normalized take-turns draft.
        active_ids: Present teammates.
    """
    if state.get("complete"):
        return []
    return [int(sid) for sid in active_ids if turn_can_place(state, int(sid), active_ids)]


def apply_turn_place(
    state: dict[str, Any],
    student_id: int,
    option_id: str,
    allowed: list[str],
    active_ids: list[int],
) -> tuple[dict[str, Any], bool]:
    """Place one option into the next spot.

    Args:
        state: Normalized take-turns draft.
        student_id: Roster id placing.
        option_id: Option to place.
        allowed: Item option ids.
        active_ids: Present teammates.

    Returns:
        ``(new_state, changed)``. ``changed`` is False for a safe retry
        (this student's own last placement of the same option).

    Raises:
        TurnConflict: Done, unknown option, already placed, or out of turn.
    """
    opt = str(option_id or "")
    if opt not in allowed:
        raise TurnConflict("bad_option", "Choose one of the items.")
    events = list(state.get("events") or [])
    order = list(state.get("order") or [])
    if opt in order:
        placer = next((int(e[0]) for e in events if e[1] == "place" and e[2] == opt), None)
        if placer == int(student_id) and events and events[-1][1] == "place" and events[-1][2] == opt:
            return state, False
    # MCK-155 gate LOW-2: once the order is complete every other place is
    # "already sent", never "{name} just placed that one".
    if state.get("complete"):
        raise TurnConflict("done", "Your group's order is already in.")
    if opt in order:
        raise TurnConflict("turn_taken", "That one was just placed. Pick another.", by=placer)
    if not turn_can_place(state, student_id, active_ids):
        raise TurnConflict("not_your_turn", "Not your turn yet.")
    order.append(opt)
    events.append([int(student_id), "place", opt])
    new_state = {
        **state,
        "order": order,
        "events": events,
        "rev": int(state.get("rev") or 0) + 1,
        "complete": len(order) == len(allowed),
    }
    return new_state, True


def apply_turn_undo(state: dict[str, Any], student_id: int) -> dict[str, Any]:
    """Take back this student's placement while it is still the last move.

    Raises:
        TurnConflict: Nothing to undo, someone moved since, or the order is in.
    """
    events = list(state.get("events") or [])
    if state.get("complete") or not events:
        raise TurnConflict("no_undo", "That spot is locked now.")
    last = events[-1]
    if last[1] != "place" or int(last[0]) != int(student_id):
        raise TurnConflict("no_undo", "That spot is locked now.")
    events.pop()
    order = [str(e[2]) for e in events if e[1] == "place"]
    return {**state, "order": order, "events": events, "rev": int(state.get("rev") or 0) + 1, "complete": False}


def apply_turn_skip(state: dict[str, Any], active_ids: list[int]) -> tuple[dict[str, Any], list[int]]:
    """Teacher Skip: pass the turn of everyone the group is waiting on.

    The members who could place now are skipped, but only when someone
    else is blocked on them. At the very start everyone may place, so Skip
    does nothing.

    Returns:
        ``(new_state, skipped_ids)``. ``skipped_ids`` is empty when nothing
        was waiting.
    """
    if state.get("complete"):
        return state, []
    events = list(state.get("events") or [])
    waiting = turn_next_ids(state, active_ids)
    targets = waiting if len(waiting) < len(active_ids) else []
    if not targets:
        return state, []
    for sid in targets:
        events.append([int(sid), "skip", None])
    return {**state, "events": events, "rev": int(state.get("rev") or 0) + 1}, targets


def turn_recent_skips(state: dict[str, Any]) -> list[int]:
    """Skips since the latest placement (for "{name}'s turn was skipped.")."""
    out: list[int] = []
    for event in reversed(state.get("events") or []):
        if event[1] == "place":
            break
        out.append(int(event[0]))
    return list(reversed(out))

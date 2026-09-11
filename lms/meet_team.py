"""Meet engagement: ephemeral QH-style chain A → C → B.

Shown after TEAMS, on the MEET beat, before ROUND / Team Challenge.
Universal across courses. Session-ephemeral only — wiped on MEET clear.
Not a gradebook writeback. Waiting-room Minds-On stays on JOIN.

Chain order is locked: A → C → B. Density escape drops C first (A → B).
One visible item at a time; teacher / timer advances. Ops owns spark +
need pools — C/B copy here is stub IDs only.
"""

from __future__ import annotations

import random
from typing import Any

try:
    from quick_hitter import (
        CLEAR_ON_TEAM_CHALLENGE,
        QUICK_HITTER_ARTIFACT_ID,
        RIDE_MEET_TEAM,
        quick_hitter_packaging,
    )
except ImportError:  # ``python3 lms/app.py`` package import
    from lms.quick_hitter import (
        CLEAR_ON_TEAM_CHALLENGE,
        QUICK_HITTER_ARTIFACT_ID,
        RIDE_MEET_TEAM,
        quick_hitter_packaging,
    )

MEET_TEAM_ITEM_ID = "meet-team"
MEET_STEP_C_ITEM_ID = "meet-c"
MEET_STEP_B_ITEM_ID = "meet-b"
MEET_TEAM_PACK_ID = "meet-team"
MEET_TEAM_SLIDE_INDEX = 801
MEET_STEP_SLIDE: dict[str, int] = {"A": 801, "C": 802, "B": 803}
MEET_TEAM_KIND = "mc"
MEET_TEAM_LABEL = "Meet Your Team"
MEET_STEP_LABELS: dict[str, str] = {
    "A": MEET_TEAM_LABEL,
    "C": "Meet · Spark",
    "B": "Meet · Need",
}

MEET_TEAM_PROMPT = "Today I’m the teammate who…"

MEET_TEAM_FIXED_CHOICES: tuple[str, ...] = (
    "keeps us kind",
    "wants to try being team leader",
    "Not sure",
)

# Documented as meet-team-warmup-pool — rotate two per session.
MEET_TEAM_WARMUP_POOL: tuple[str, ...] = (
    "notices details",
    "asks the good question",
    "tries the weird idea",
    "checks our work",
    "explains so it clicks",
    "brings the calm",
    "connects ideas",
)

# Ops spark pool — stubs only; do not invent course-specific copy.
MEET_SPARK_POOL: tuple[dict[str, str], ...] = (
    {
        "id": "ops-spark-stub-1",
        "prompt": "Shared spark (Ops stub 1) — replace with the course-agnostic pool.",
    },
    {
        "id": "ops-spark-stub-2",
        "prompt": "Shared spark (Ops stub 2) — replace with the course-agnostic pool.",
    },
)
MEET_SPARK_REACTS: tuple[str, ...] = ("This sparks something", "Not sure")

# Ops need stems — stubs only; rotate one prompt per Meet window.
MEET_NEED_POOL: tuple[dict[str, Any], ...] = (
    {
        "id": "ops-need-stub-1",
        "prompt": "One thing our team might need…",
        "stem": "a slower start",
    },
    {
        "id": "ops-need-stub-2",
        "prompt": "One thing our team might need…",
        "stem": "someone to talk it out",
    },
    {
        "id": "ops-need-stub-3",
        "prompt": "One thing our team might need…",
        "stem": "a reminder of the goal",
    },
)
MEET_NEED_CHOICES: tuple[str, ...] = (
    "Yes, that",
    "Something else",
    "Not sure",
)

MEET_CHAIN_DEFAULT: tuple[str, ...] = ("A", "C", "B")
MEET_CHAIN_SKIP_C: tuple[str, ...] = ("A", "B")
MEET_STEPS: frozenset[str] = frozenset({"A", "C", "B"})
MEET_ITEM_IDS: frozenset[str] = frozenset(
    {MEET_TEAM_ITEM_ID, MEET_STEP_C_ITEM_ID, MEET_STEP_B_ITEM_ID, "meet-a"}
)
MEET_NO_CHIP: frozenset[str] = frozenset({"Not sure", "Pass", "Something else"})

CUE_MEET_OPEN = "cue.meet_open"
CUE_MEET_CLEAR = "cue.meet_clear"
MEET_CUES: frozenset[str] = frozenset({CUE_MEET_OPEN, CUE_MEET_CLEAR})


def pick_rotated_choices(rng: random.Random | None = None) -> list[str]:
    """Return two distinct pool lines for one live session.

    Args:
        rng: Optional ``random.Random`` so tests can pin the draw.
    """
    picker = rng if rng is not None else random.Random()
    return picker.sample(list(MEET_TEAM_WARMUP_POOL), 2)


def meet_team_choices(
    rotated: list[str] | tuple[str, ...] | None = None,
    *,
    rng: random.Random | None = None,
) -> list[str]:
    """Build the five-choice list: two rotated, then the three fixed lines.

    ``Not sure`` stays last. The two rotated lines come from
    ``MEET_TEAM_WARMUP_POOL``.

    Args:
        rotated: Optional pre-picked pair. When omitted, two pool lines
            are drawn with ``rng``.
        rng: Optional ``random.Random`` used when ``rotated`` is omitted.

    Returns:
        Five student-facing choice strings.

    Raises:
        ValueError: If ``rotated`` is provided and is not length 2.
    """
    extra = list(rotated) if rotated is not None else pick_rotated_choices(rng)
    if len(extra) != 2:
        raise ValueError("meet-team warm-up rotates exactly two pool choices")
    return [*extra, *MEET_TEAM_FIXED_CHOICES]


def meet_chain_order(*, include_c: bool = True) -> list[str]:
    """Return the locked Meet chain, optionally dropping C.

    Args:
        include_c: False for the density escape (A → B).
    """
    return list(MEET_CHAIN_DEFAULT if include_c else MEET_CHAIN_SKIP_C)


def pick_spark(
    rng: random.Random | None = None,
    spark_id: str | None = None,
) -> dict[str, str]:
    """Return one Ops spark stub.

    Args:
        rng: Optional ``random.Random`` used when ``spark_id`` is omitted.
        spark_id: Optional pool id to pin.
    """
    if spark_id:
        for row in MEET_SPARK_POOL:
            if row["id"] == spark_id:
                return dict(row)
    picker = rng if rng is not None else random.Random()
    return dict(picker.choice(MEET_SPARK_POOL))


def pick_need(
    rng: random.Random | None = None,
    need_prompt_id: str | None = None,
) -> dict[str, Any]:
    """Return one Ops need-stem stub.

    Args:
        rng: Optional ``random.Random`` used when ``need_prompt_id`` is omitted.
        need_prompt_id: Optional pool id to pin.
    """
    if need_prompt_id:
        for row in MEET_NEED_POOL:
            if row["id"] == need_prompt_id:
                return dict(row)
    picker = rng if rng is not None else random.Random()
    return dict(picker.choice(MEET_NEED_POOL))


def new_meet_chain_state(
    *,
    include_c: bool = True,
    rng: random.Random | None = None,
    timer_ends_at: str | None = None,
    rotated: list[str] | tuple[str, ...] | None = None,
    spark_id: str | None = None,
    need_prompt_id: str | None = None,
) -> dict[str, Any]:
    """Build a fresh ephemeral MeetChainState (session RAM / teacher_state).

    Args:
        include_c: False drops C (density escape A → B).
        rng: Optional ``random.Random`` for pool draws.
        timer_ends_at: Optional ISO / overlay timer stamp.
        rotated: Optional pinned A warmup pair.
        spark_id: Optional pinned Ops spark id.
        need_prompt_id: Optional pinned Ops need id.

    Returns:
        Thin chain dict: ``chain``, ``index``, empty pick maps, pool ids.
    """
    picker = rng if rng is not None else random.Random()
    extra = list(rotated) if rotated is not None else pick_rotated_choices(picker)
    spark = pick_spark(picker, spark_id)
    need = pick_need(picker, need_prompt_id)
    return {
        "stage": "meet",
        "chain": meet_chain_order(include_c=include_c),
        "index": 0,
        "a_picks": {},
        "c_reacts": {},
        "b_picks": {},
        "spark_id": spark["id"],
        "need_prompt_id": need["id"],
        "rotated": extra,
        "timer_ends_at": timer_ends_at,
    }


def public_meet_chain(stored: Any) -> dict[str, Any] | None:
    """Normalize a stored MeetChainState, or None when absent / invalid.

    Args:
        stored: Dict from ``teacher_state.meet_chain``.
    """
    if not isinstance(stored, dict):
        return None
    raw_chain = stored.get("chain")
    if not isinstance(raw_chain, list):
        return None
    chain = [str(step) for step in raw_chain if str(step) in MEET_STEPS]
    if not chain or chain[0] != "A" or "A" not in chain or "B" not in chain:
        return None
    if "C" in chain and chain != ["A", "C", "B"]:
        return None
    if "C" not in chain and chain != ["A", "B"]:
        return None
    try:
        index = int(stored.get("index") or 0)
    except (TypeError, ValueError):
        index = 0
    index = max(0, min(len(chain) - 1, index))
    rotated = stored.get("rotated")
    extra = (
        [str(item) for item in rotated]
        if isinstance(rotated, list) and len(rotated) == 2
        else []
    )

    def _picks(raw: Any) -> dict[str, str]:
        if not isinstance(raw, dict):
            return {}
        out: dict[str, str] = {}
        for key, value in raw.items():
            token = str(key or "").strip()
            choice = str(value or "").strip()
            if token and choice:
                out[token] = choice
        return out

    timer = stored.get("timer_ends_at")
    return {
        "stage": "meet",
        "chain": chain,
        "index": index,
        "a_picks": _picks(stored.get("a_picks")),
        "c_reacts": _picks(stored.get("c_reacts")),
        "b_picks": _picks(stored.get("b_picks")),
        "spark_id": str(stored.get("spark_id") or "").strip() or None,
        "need_prompt_id": str(stored.get("need_prompt_id") or "").strip() or None,
        "rotated": extra,
        "timer_ends_at": str(timer).strip() if timer not in (None, "") else None,
    }


def current_meet_step(state: dict[str, Any] | None) -> str | None:
    """Return the visible chain letter, or None.

    Args:
        state: Public MeetChainState.
    """
    cleaned = public_meet_chain(state)
    if cleaned is None:
        return None
    return cleaned["chain"][cleaned["index"]]


def advance_meet_chain(state: dict[str, Any] | None) -> dict[str, Any] | None:
    """Advance one step. No-op on the last item.

    Args:
        state: Current MeetChainState.
    """
    cleaned = public_meet_chain(state)
    if cleaned is None:
        return None
    if cleaned["index"] < len(cleaned["chain"]) - 1:
        cleaned["index"] += 1
    return cleaned


def skip_meet_c(state: dict[str, Any] | None) -> dict[str, Any] | None:
    """Drop C from the remaining chain (density escape).

    On A, the chain becomes A → B and the visible item stays A.
    On C, the chain becomes A → B and the visible item becomes B.
    On B (or a chain that already omitted C), the state is unchanged.

    Args:
        state: Current MeetChainState.
    """
    cleaned = public_meet_chain(state)
    if cleaned is None:
        return None
    step = cleaned["chain"][cleaned["index"]]
    cleaned["chain"] = meet_chain_order(include_c=False)
    if step == "C":
        cleaned["index"] = cleaned["chain"].index("B")
    else:
        cleaned["index"] = cleaned["chain"].index(step if step in cleaned["chain"] else "A")
    return cleaned


def meet_soft_counts(state: dict[str, Any] | None, step: str) -> dict[str, int]:
    """Optional A/B distribution counts. C has no leaderboard.

    Args:
        state: Current MeetChainState.
        step: ``A`` or ``B``.
    """
    cleaned = public_meet_chain(state)
    if cleaned is None or step not in {"A", "B"}:
        return {}
    bag = cleaned["a_picks"] if step == "A" else cleaned["b_picks"]
    counts: dict[str, int] = {}
    for choice in bag.values():
        counts[choice] = counts.get(choice, 0) + 1
    return counts


def meet_chip_for(
    state: dict[str, Any] | None,
    participant_key: str,
) -> str | None:
    """Own-avatar A chip for this Meet window, or None.

    Pass / Not sure does not land a chip.

    Args:
        state: Current MeetChainState.
        participant_key: ``participant_uuid`` or ``student:<id>``.
    """
    cleaned = public_meet_chain(state)
    token = str(participant_key or "").strip()
    if cleaned is None or not token:
        return None
    choice = str(cleaned["a_picks"].get(token) or "").strip()
    if not choice or choice in MEET_NO_CHIP:
        return None
    return choice


def record_meet_pick(
    state: dict[str, Any] | None,
    *,
    participant_key: str,
    choice: str,
    step: str | None = None,
) -> dict[str, Any] | None:
    """Store one ephemeral pick on the visible (or named) step.

    Args:
        state: Current MeetChainState.
        participant_key: ``participant_uuid`` or ``student:<id>``.
        choice: Student-facing option text.
        step: Optional letter; defaults to the visible step.
    """
    cleaned = public_meet_chain(state)
    token = str(participant_key or "").strip()
    text = str(choice or "").strip()
    if cleaned is None or not token or not text:
        return cleaned
    letter = step if step in MEET_STEPS else cleaned["chain"][cleaned["index"]]
    key = {"A": "a_picks", "C": "c_reacts", "B": "b_picks"}[letter]
    cleaned[key][token] = text
    return cleaned


def _step_item(
    step: str,
    *,
    rotated: list[str] | tuple[str, ...] | None = None,
    spark_id: str | None = None,
    need_prompt_id: str | None = None,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Build one catalog item for A, C, or B.

    Args:
        step: Chain letter.
        rotated: Optional A warmup pair.
        spark_id: Optional Ops spark id.
        need_prompt_id: Optional Ops need id.
        rng: Optional ``random.Random``.
    """
    if step == "C":
        spark = pick_spark(rng, spark_id)
        return {
            "item_id": MEET_STEP_C_ITEM_ID,
            "kind": MEET_TEAM_KIND,
            "prompt": spark["prompt"],
            "choices": list(MEET_SPARK_REACTS),
            "spark_id": spark["id"],
        }
    if step == "B":
        need = pick_need(rng, need_prompt_id)
        stem = str(need.get("stem") or "").strip()
        prompt = str(need.get("prompt") or "One thing our team might need…")
        if stem:
            prompt = f"{prompt.rstrip('…').rstrip()}… {stem}"
        return {
            "item_id": MEET_STEP_B_ITEM_ID,
            "kind": MEET_TEAM_KIND,
            "prompt": prompt,
            "choices": list(MEET_NEED_CHOICES),
            "need_prompt_id": need["id"],
        }
    return {
        "item_id": MEET_TEAM_ITEM_ID,
        "kind": MEET_TEAM_KIND,
        "prompt": MEET_TEAM_PROMPT,
        "choices": meet_team_choices(rotated, rng=rng),
    }


def meet_team_items(
    rotated: list[str] | tuple[str, ...] | None = None,
    *,
    rng: random.Random | None = None,
) -> list[dict[str, Any]]:
    """Return the A-step catalog row (compat helper).

    Args:
        rotated: Optional pre-picked pair from the warmup pool.
        rng: Optional ``random.Random`` used when ``rotated`` is omitted.

    Returns:
        A one-element list for the teammate MC.
    """
    return [_step_item("A", rotated=rotated, rng=rng)]


def meet_step_payload(
    step: str,
    *,
    chain: list[str] | tuple[str, ...] | None = None,
    rng: random.Random | None = None,
    rotated: list[str] | tuple[str, ...] | None = None,
    spark_id: str | None = None,
    need_prompt_id: str | None = None,
) -> dict[str, Any]:
    """Student-facing live-prompt payload for one visible Meet step.

    Args:
        step: ``A``, ``C``, or ``B``.
        chain: Visible chain letters (default A → C → B).
        rng: Optional ``random.Random``.
        rotated: Optional pinned A warmup pair.
        spark_id: Optional pinned Ops spark id.
        need_prompt_id: Optional pinned Ops need id.

    Returns:
        Live-prompt payload on ``quick-hitter-question-chain`` / ``meet_team``.

    Raises:
        ValueError: Unknown step or empty chain.
    """
    letter = str(step or "").strip().upper()
    order = [str(item) for item in (chain or MEET_CHAIN_DEFAULT) if str(item) in MEET_STEPS]
    if letter not in MEET_STEPS:
        raise ValueError(f"unknown meet step: {step}")
    if letter not in order:
        raise ValueError(f"step {letter} is not on this meet chain")
    item = _step_item(
        letter,
        rotated=rotated,
        spark_id=spark_id,
        need_prompt_id=need_prompt_id,
        rng=rng,
    )
    index = order.index(letter) + 1
    payload = quick_hitter_packaging(
        ride=RIDE_MEET_TEAM,
        chain_index=index,
        chain_length=len(order),
        ephemeral=True,
        durable_store=False,
        clear_on=CLEAR_ON_TEAM_CHALLENGE,
    )
    payload.update(
        {
            "pack": MEET_TEAM_PACK_ID,
            "item_id": item["item_id"],
            "label": MEET_STEP_LABELS.get(letter, MEET_TEAM_LABEL),
            "prompt": item["prompt"],
            "choices": list(item["choices"]),
            "items": [item],
            "step": letter,
            "chain": list(order),
        }
    )
    if item.get("spark_id"):
        payload["spark_id"] = item["spark_id"]
    if item.get("need_prompt_id"):
        payload["need_prompt_id"] = item["need_prompt_id"]
    return payload


def meet_payload_for_state(
    state: dict[str, Any] | None,
    *,
    rng: random.Random | None = None,
) -> dict[str, Any] | None:
    """Build the visible-step payload from MeetChainState.

    Args:
        state: Public MeetChainState.
        rng: Optional ``random.Random`` (unused when ids are pinned).
    """
    cleaned = public_meet_chain(state)
    if cleaned is None:
        return None
    step = cleaned["chain"][cleaned["index"]]
    return meet_step_payload(
        step,
        chain=cleaned["chain"],
        rng=rng,
        rotated=cleaned.get("rotated") or None,
        spark_id=cleaned.get("spark_id"),
        need_prompt_id=cleaned.get("need_prompt_id"),
    )


def meet_team_prompt_payload(
    rotated: list[str] | tuple[str, ...] | None = None,
    *,
    rng: random.Random | None = None,
    include_c: bool = True,
) -> dict[str, Any]:
    """Student-facing payload for Meet step A (chain mount).

    Args:
        rotated: Optional pre-picked pair from the warmup pool.
        rng: Optional ``random.Random`` used when ``rotated`` is omitted.
        include_c: False for the density-escape chain A → B.

    Returns:
        Live-prompt payload with ``item_id`` ``meet-team`` on the
        ``quick-hitter-question-chain`` artifact.
    """
    return meet_step_payload(
        "A",
        chain=meet_chain_order(include_c=include_c),
        rng=rng,
        rotated=rotated,
    )


def is_meet_team_payload(payload: Any) -> bool:
    """True when a live-prompt payload is any Meet chain step.

    Args:
        payload: Prompt JSON object.
    """
    if not isinstance(payload, dict):
        return False
    artifact = str(payload.get("artifact_id") or "").strip()
    ride = str(payload.get("ride") or "").strip().lower()
    if artifact == QUICK_HITTER_ARTIFACT_ID and ride == RIDE_MEET_TEAM:
        return True
    item_id = str(payload.get("item_id") or "").strip().lower()
    pack = str(payload.get("pack") or "").strip().lower()
    return item_id in MEET_ITEM_IDS or pack == MEET_TEAM_PACK_ID


def meet_participant_key(
    *,
    participant_uuid: str = "",
    student_id: int | None = None,
) -> str:
    """Stable ephemeral pick key for one live attendee.

    Args:
        participant_uuid: Preferred live-session person key.
        student_id: Fallback roster id.
    """
    token = str(participant_uuid or "").strip()
    if token:
        return token
    if student_id not in (None, ""):
        return f"student:{int(student_id)}"
    return ""

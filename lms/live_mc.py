"""Source-agnostic live MC tallies for the teacher Questions card.

Any active ``kind=mc`` prompt (JOIN Minds-On, MEET soft MC, CONS, ROUND/PLAY)
shares one tally shape. Meet picks stay ephemeral (meet_chain bags); other
rides read ``live_session_responses``. Not a gradebook writeback.
"""

from __future__ import annotations

from typing import Any

try:
    from live_prompt_feedback import CHOICE_LETTERS, choice_letter
    from meet_team import current_meet_step, is_meet_team_payload, public_meet_chain
except ImportError:  # ``python3 lms/app.py`` package import
    from lms.live_prompt_feedback import CHOICE_LETTERS, choice_letter
    from lms.meet_team import current_meet_step, is_meet_team_payload, public_meet_chain


def is_mc_prompt(prompt: Any) -> bool:
    """True when a live-prompt row is multiple-choice.

    Args:
        prompt: Active prompt dict (``kind`` + ``payload``), or None.
    """
    if not isinstance(prompt, dict):
        return False
    kind = str(prompt.get("kind") or "").strip().lower()
    payload = prompt.get("payload")
    body = payload if isinstance(payload, dict) else {}
    item_kind = str(body.get("kind") or "").strip().lower()
    if kind == "mc" or item_kind == "mc":
        return True
    choices = body.get("choices")
    return isinstance(choices, list) and len(choices) > 0


def prompt_ref_for(prompt: Any, teacher_state: Any = None) -> str:
    """Stable id for the current MC (item / teacher prompt_ref / slide).

    Args:
        prompt: Active prompt dict.
        teacher_state: Optional public LiveTeacherState.
    """
    payload = (prompt or {}).get("payload") if isinstance(prompt, dict) else {}
    if isinstance(payload, dict):
        item = str(payload.get("item_id") or "").strip()
        if item:
            return item
        pack = str(payload.get("pack") or "").strip()
        if pack:
            return pack
    if isinstance(teacher_state, dict):
        ref = str(teacher_state.get("prompt_ref") or "").strip()
        if ref:
            return ref
    if isinstance(prompt, dict) and prompt.get("id") not in (None, ""):
        return f"prompt-{int(prompt['id'])}"
    return ""


def extract_choice_labels(payload: Any) -> list[str]:
    """Return student-facing choice labels in A–H order.

    Args:
        payload: Live-prompt payload with a ``choices`` list.
    """
    if not isinstance(payload, dict):
        return []
    raw = payload.get("choices") or []
    if not isinstance(raw, list):
        return []
    labels: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            text = str(item.get("label") or item.get("text") or item.get("choice") or "").strip()
        else:
            text = str(item or "").strip()
        if text:
            labels.append(text)
    return labels


def _meet_pick_bag(meet_chain: Any) -> dict[str, str]:
    """Return the visible Meet step's ephemeral pick map."""
    cleaned = public_meet_chain(meet_chain)
    step = current_meet_step(cleaned)
    if cleaned is None or not step:
        return {}
    key = {"A": "a_picks", "C": "c_reacts", "B": "b_picks"}.get(step, "a_picks")
    bag = cleaned.get(key) or {}
    return bag if isinstance(bag, dict) else {}


def _choice_rows_from_values(
    values: list[Any],
    *,
    payload: Any,
) -> list[str]:
    """Map raw student answers onto choice letters."""
    letters: list[str] = []
    choices = extract_choice_labels(payload)
    for raw in values:
        response = raw if isinstance(raw, dict) else {"choice": raw}
        letter = choice_letter(response, choices)
        if letter:
            letters.append(letter)
    return letters


def _fingerprint(counts: list[int], responded: int) -> int:
    """Integer bind key that changes when any bar or N changes."""
    sig = int(responded)
    for count in counts:
        sig = (sig * 33 + int(count)) & 0x7FFFFFFF
    return sig


def build_mc_tally(
    prompt: Any,
    *,
    responses: list[dict[str, Any]] | None = None,
    meet_chain: Any = None,
    present: int = 0,
    teacher_state: Any = None,
) -> dict[str, Any] | None:
    """Build a staff-only MC distribution for the Questions primary slot.

    Meet rides prefer ephemeral ``meet_chain`` picks (no gradebook). Every
    other MC ride tallies ``live_session_responses``.

    Args:
        prompt: Active live-prompt row.
        responses: Parsed response rows with a ``response`` object.
        meet_chain: Optional MeetChainState for MEET soft MC.
        present: Students currently in the session (N of N/N).
        teacher_state: Optional public LiveTeacherState (prompt_ref).

    Returns:
        Tally dict, or ``None`` when the active prompt is not MC.
    """
    if not is_mc_prompt(prompt):
        return None
    payload = prompt.get("payload") if isinstance(prompt.get("payload"), dict) else {}
    labels = extract_choice_labels(payload)
    if not labels:
        return None
    letters = [CHOICE_LETTERS[i] for i in range(len(labels))]
    counts = {letter: 0 for letter in letters}
    source = "live_prompt"
    raw_values: list[Any] = []
    if is_meet_team_payload(payload) and public_meet_chain(meet_chain) is not None:
        source = "meet_chain"
        raw_values = list(_meet_pick_bag(meet_chain).values())
    else:
        for row in responses or []:
            if not isinstance(row, dict):
                continue
            answer = row.get("response")
            raw_values.append(answer if isinstance(answer, dict) else {"choice": answer})
    for letter in _choice_rows_from_values(raw_values, payload=payload):
        if letter in counts:
            counts[letter] += 1
    responded = len(raw_values)
    present_n = max(0, int(present))
    count_list = [counts[letter] for letter in letters]
    denom = responded if responded > 0 else 0
    choices_out: list[dict[str, Any]] = []
    for letter, label, count in zip(letters, labels, count_list):
        pct = int(round(100.0 * count / denom)) if denom else 0
        choices_out.append(
            {
                "id": letter,
                "label": label,
                "count": count,
                "pct": pct,
            }
        )
    ref = prompt_ref_for(prompt, teacher_state)
    prompt_id = prompt.get("id")
    return {
        "prompt_ref": ref,
        "prompt_id": int(prompt_id) if prompt_id not in (None, "") else None,
        "kind": "mc",
        "item_id": str(payload.get("item_id") or ref),
        "prompt": str(payload.get("prompt") or "").strip(),
        "source": source,
        "choices": choices_out,
        "responded": responded,
        "present": present_n,
        "response_count": responded,
        "response_seq": _fingerprint(count_list, responded),
    }

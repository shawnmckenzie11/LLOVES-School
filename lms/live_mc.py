"""Source-agnostic live MC tallies for the teacher Questions card.

Any active ``kind=mc`` prompt (JOIN Minds-On, MEET soft MC, CONS, ROUND/PLAY)
shares one tally shape. Meet picks stay ephemeral (meet_chain bags); other
rides read ``live_session_responses``. Not a gradebook writeback.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

try:
    from live_prompt_feedback import CHOICE_LETTERS, choice_letter
    from live_rank import build_rank_tally, is_rank_prompt
    from meet_team import current_meet_step, is_meet_team_payload, public_meet_chain
except ImportError:  # ``python3 lms/app.py`` package import
    from lms.live_prompt_feedback import CHOICE_LETTERS, choice_letter
    from lms.live_rank import build_rank_tally, is_rank_prompt
    from lms.meet_team import current_meet_step, is_meet_team_payload, public_meet_chain


def is_mc_prompt(prompt: Any) -> bool:
    """True when a live-prompt row is multiple-choice.

    Args:
        prompt: Active prompt dict (``kind`` + ``payload``), or None.
    """
    if not isinstance(prompt, dict):
        return False
    if is_rank_prompt(prompt):
        return False
    kind = str(prompt.get("kind") or "").strip().lower()
    payload = prompt.get("payload")
    body = payload if isinstance(payload, dict) else {}
    item_kind = str(body.get("kind") or body.get("type") or "").strip().lower()
    if item_kind == "rank":
        return False
    if kind == "mc" or item_kind in {"mc", "poll"}:
        return True
    choices = body.get("choices") or body.get("options")
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
    raw = payload.get("choices") or payload.get("options") or []
    if not isinstance(raw, list) or not raw:
        items = payload.get("items")
        if isinstance(items, list) and items and isinstance(items[0], dict):
            nested = items[0].get("choices") or items[0].get("options") or []
            raw = nested if isinstance(nested, list) else []
    if not isinstance(raw, list):
        return []
    labels: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            text = str(
                item.get("label") or item.get("text") or item.get("choice") or ""
            ).strip()
        else:
            text = str(item or "").strip()
        if text:
            labels.append(text)
    return labels


def _response_choice_text(raw: Any) -> str:
    """Return the student-facing token from one stored answer.

    Args:
        raw: Response object or a bare choice string.
    """
    response = raw if isinstance(raw, dict) else {"choice": raw}
    for key in ("choice", "value", "text"):
        value = response.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


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

    Individual prompt responses are the source of truth. Ephemeral
    ``meet_chain`` picks are only a fallback when no response rows exist.

    Args:
        prompt: Active live-prompt row.
        responses: Parsed response rows with a ``response`` object.
        meet_chain: Optional MeetChainState for MEET soft MC.
        present: Students currently in the session (N of N/N).
        teacher_state: Optional public LiveTeacherState (prompt_ref).

    Returns:
        Tally dict, or ``None`` when the active prompt is not MC.

    Labels past A–H are dropped. A ninth option or a pile of distinct
    free-text answers must not raise while ``/state`` is building bars.
    """
    if not is_mc_prompt(prompt):
        return None
    payload = prompt.get("payload") if isinstance(prompt.get("payload"), dict) else {}
    labels = extract_choice_labels(payload)
    key = str(payload.get("key") or "").strip().upper()
    if not key:
        correct_ids = payload.get("correct_ids") or []
        if isinstance(correct_ids, list) and correct_ids:
            key = str(correct_ids[0] or "").strip().upper()
    if not key:
        key = str(payload.get("correct_answer") or "").strip().upper()
    source = "live_prompt"
    raw_values: list[Any] = []
    for row in responses or []:
        if not isinstance(row, dict):
            continue
        answer = row.get("response")
        raw_values.append(answer if isinstance(answer, dict) else {"choice": answer})
    if (
        not raw_values
        and is_meet_team_payload(payload)
        and public_meet_chain(meet_chain) is not None
    ):
        source = "meet_chain"
        raw_values = [
            value if isinstance(value, dict) else {"choice": value}
            for value in _meet_pick_bag(meet_chain).values()
        ]
    if not labels:
        recovered: list[str] = []
        for raw in raw_values:
            text = _response_choice_text(raw)
            if text and text not in recovered:
                recovered.append(text)
            if len(recovered) >= len(CHOICE_LETTERS):
                break
        labels = recovered
    if len(labels) > len(CHOICE_LETTERS):
        labels = labels[: len(CHOICE_LETTERS)]
    for raw in raw_values:
        text = _response_choice_text(raw)
        if not text or choice_letter({"choice": text}, labels):
            continue
        if text not in labels and len(labels) < len(CHOICE_LETTERS):
            labels.append(text)
    if not labels:
        return None
    span = min(len(labels), len(CHOICE_LETTERS))
    labels = labels[:span]
    letters = [CHOICE_LETTERS[i] for i in range(span)]
    counts = {letter: 0 for letter in letters}
    for letter in _choice_rows_from_values(
        raw_values, payload={**payload, "choices": labels}
    ):
        if letter in counts:
            counts[letter] += 1
    responded = len(raw_values)
    present_n = max(0, int(present), responded)
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
                "correct": bool(key) and letter == key,
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


def _prompt_requires_integer(payload: dict[str, Any]) -> bool:
    """Return True when the prompt rejects fractional answers.

    An absent ``integer_only`` flag allows decimals. Only an explicit
    true value keeps the whole-number chart.

    Args:
        payload: Live-prompt payload.
    """
    flag = payload.get("integer_only")
    if isinstance(flag, str):
        return flag.strip().lower() in {"1", "true", "yes", "on"}
    return bool(flag)


def _numeric_bucket_label(number: float, *, integer_only: bool) -> str | None:
    """Return the class-chart label for one numeric answer.

    Integer-only prompts keep whole numbers and drop fractions.
    Decimal prompts collapse equivalent values (``2.50`` and ``2.5``)
    and show at most two decimal places.

    Args:
        number: Parsed student value.
        integer_only: When True, non-whole answers are omitted.

    Returns:
        Bucket label, or ``None`` when the value is dropped.
    """
    if not math.isfinite(number):
        return None
    if integer_only:
        if not number.is_integer():
            return None
        return str(int(number))
    try:
        quantized = Decimal(str(number)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except InvalidOperation:
        return None
    if not quantized.is_finite():
        return None
    if quantized == 0:
        return "0"
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"", "-", "-0"}:
        return "0"
    return text


def _numeric_exact_label(number: float) -> str:
    """Label one answer at up to six decimal places (no bar rounding).

    Args:
        number: Finite parsed student value.
    """
    try:
        quantized = Decimal(str(number)).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )
    except InvalidOperation:
        return str(number)
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-", "-0"} else text


def _numeric_full_label(number: float) -> str:
    """Label one answer at full float precision (shortest round-trip).

    Used when even six places print the same as the correct bar, e.g.
    2.5000001 against a key of 2.5 at zero tolerance.

    Args:
        number: Finite parsed student value.
    """
    try:
        text = format(Decimal(repr(float(number))), "f")
    except (InvalidOperation, ValueError, OverflowError):
        return str(number)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-", "-0"} else text


def _numeric_expected(payload: dict[str, Any]) -> float | None:
    """Parse the authored numeric key, if the prompt has one.

    Args:
        payload: Live-prompt payload (``key`` or ``correct_answer``).
    """
    raw = payload.get("key")
    if raw in (None, ""):
        raw = payload.get("correct_answer")
    if raw in (None, ""):
        correct_ids = payload.get("correct_ids") or []
        if isinstance(correct_ids, list) and correct_ids:
            raw = correct_ids[0]
    if raw in (None, ""):
        return None
    try:
        number = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _numeric_within_tolerance(
    actual: float,
    expected: float,
    tolerance: Any,
    tolerance_kind: Any,
) -> bool:
    """Return True when a numeric answer sits inside the authored window.

    Matches ``SchoolDb._numeric_within_tolerance`` so a class-chart bar
    is marked correct for the same values the roster scores as correct.

    Args:
        actual: Student value before display rounding.
        expected: Authored correct value.
        tolerance: Absolute delta or percent, depending on ``tolerance_kind``.
        tolerance_kind: ``absolute`` or ``percent``.
    """
    try:
        window = float(tolerance)
    except (TypeError, ValueError):
        window = 0.0
    if not math.isfinite(window) or window < 0:
        window = 0.0
    kind = str(tolerance_kind or "absolute").strip().lower()
    if kind in {"percent", "percentage", "pct"}:
        if expected == 0:
            return abs(actual) <= window
        return abs(actual - expected) <= abs(expected) * (window / 100.0)
    return abs(actual - expected) <= window


NUMERIC_KEY_FIELDS = ("key", "correct_answer", "correct_ids", "answer_key", "correct_index")


def is_numeric_prompt(prompt: Any) -> bool:
    """True for a numeric live prompt (row ``kind`` or payload ``kind``).

    Args:
        prompt: Live-prompt row.
    """
    if not isinstance(prompt, dict):
        return False
    payload = prompt.get("payload") if isinstance(prompt.get("payload"), dict) else {}
    kinds = {str(prompt.get("kind") or "").strip().lower(), str(payload.get("kind") or "").strip().lower()}
    return "numeric" in kinds


def numeric_prompt_without_key(prompt: Any) -> Any:
    """Copy of a numeric prompt row with the answer key removed.

    MCK-155 gate (NEW-HIGH-A): ``build_numeric_tally`` labels the key's bar
    with the full-precision key (MCK-83) and splits a bucket that mixes
    right and wrong answers. Both name the key, so a student tally built
    while the question is open must start from this key-free copy. The
    tolerance stays: it is not the key.

    Args:
        prompt: Live-prompt row.
    """
    if not is_numeric_prompt(prompt):
        return prompt
    out = dict(prompt)
    payload = dict(prompt.get("payload") or {}) if isinstance(prompt.get("payload"), dict) else {}
    for field in NUMERIC_KEY_FIELDS:
        payload.pop(field, None)
    out["payload"] = payload
    return out


def build_numeric_tally(
    prompt: Any,
    *,
    responses: list[dict[str, Any]] | None = None,
    present: int = 0,
    teacher_state: Any = None,
) -> dict[str, Any] | None:
    """Build a class-wide histogram of numeric answers.

    Whole-number prompts still drop fractions. Decimal prompts bucket
    equivalent values together, including negatives, and sort the bars
    by number. A bar is correct when its responses match the authored
    key, including any tolerance on the prompt. A wrong answer that rounds
    into a correct bar gets its own exact bar instead (MCK-83).

    Args:
        prompt: Active live-prompt row (``kind=numeric``).
        responses: Parsed response rows with a ``response`` object.
        present: Students currently in the session.
        teacher_state: Optional public LiveTeacherState.

    Returns:
        Tally dict, or ``None`` when the prompt is not numeric.
    """
    if not isinstance(prompt, dict):
        return None
    kind = str(prompt.get("kind") or "").strip().lower()
    payload = prompt.get("payload") if isinstance(prompt.get("payload"), dict) else {}
    item_kind = str(payload.get("kind") or "").strip().lower()
    if kind != "numeric" and item_kind != "numeric":
        return None
    integer_only = _prompt_requires_integer(payload)
    expected = _numeric_expected(payload)
    entries: list[tuple[str, float, bool]] = []
    for row in responses or []:
        if not isinstance(row, dict):
            continue
        answer = row.get("response") if isinstance(row.get("response"), dict) else {}
        raw = answer.get("value")
        if raw is None:
            raw = answer.get("choice")
        try:
            number = float(raw)
        except (TypeError, ValueError):
            continue
        label = _numeric_bucket_label(number, integer_only=integer_only)
        if label is None:
            continue
        ok = expected is not None and _numeric_within_tolerance(
            number,
            expected,
            payload.get("tolerance"),
            payload.get("tolerance_kind"),
        )
        entries.append((label, number, bool(ok)))
    responded = len(entries)
    # A rounded bar can hold right and wrong answers (3.14 and 3.141 with a
    # 0 tolerance). Wrong ones move to their own exact bar so a ✓ bar only
    # counts correct answers (MCK-83).
    right_labels = {label for label, _n, ok in entries if ok}
    wrong_labels = {label for label, _n, ok in entries if not ok}
    mixed = right_labels & wrong_labels
    # A key with more places than its bar (3.14159, 0.125, 1e-7) buckets
    # into a bar that prints as something else ("3.14", "0"). Label its ✓
    # bar with the key at full precision, and wrong answers in that bar at
    # full precision too, so the bars don't read "3.14 ✓" and "≈3.14 ✗"
    # (MCK-83). Only correct answers in the key's own bar move to it; other
    # correct answers (inside a tolerance) keep their bars.
    key_label: str | None = None
    key_bucket: str | None = None
    if expected is not None and not integer_only:
        full_key = _numeric_full_label(float(expected))
        key_bucket = _numeric_bucket_label(float(expected), integer_only=False)
        if key_bucket != full_key:
            key_label = full_key
    bars: dict[str, dict[str, Any]] = {}
    for label, number, ok in entries:
        bar_label = label
        value = float(label)
        if ok and key_label is not None and label == key_bucket:
            bar_label = key_label
            value = float(expected)
        elif label in mixed and not ok and key_label is not None and label == key_bucket:
            # The key's right answers moved to the key's bar, so the plain
            # bucket label is free ("3.14" next to "3.14159").
            bar_label = _numeric_full_label(number)
            if bar_label == key_label:
                bar_label = f"\u2248{key_label}"
            value = float(number)
        elif label in mixed and not ok:
            # Its own bar, labelled with enough digits to differ from the
            # correct bar. The id is the label, so the student reveal and
            # the class-results card (keyed by label) keep them apart.
            bar_label = _numeric_exact_label(number)
            if bar_label == label:
                bar_label = _numeric_full_label(number)
            if bar_label == label:
                bar_label = f"\u2248{label}"
            value = float(number)
        bar = bars.get(bar_label)
        if bar is None:
            bar = {"label": bar_label, "count": 0, "correct": ok, "value": value}
            bars[bar_label] = bar
        bar["count"] += 1
    ordered = sorted(
        bars.items(),
        key=lambda item: (item[1]["value"], not item[1]["correct"]),
    )
    present_n = max(0, int(present), responded)
    denom = responded if responded > 0 else 0
    choices_out: list[dict[str, Any]] = []
    count_list: list[int] = []
    for bar_id, bar in ordered:
        count = int(bar["count"])
        count_list.append(count)
        pct = int(round(100.0 * count / denom)) if denom else 0
        choices_out.append(
            {
                "id": bar_id,
                "label": bar["label"],
                "count": count,
                "pct": pct,
                "correct": bool(bar["correct"]),
            }
        )
    ref = prompt_ref_for(prompt, teacher_state)
    prompt_id = prompt.get("id")
    return {
        "prompt_ref": ref,
        "prompt_id": int(prompt_id) if prompt_id not in (None, "") else None,
        "kind": "numeric",
        "item_id": str(payload.get("item_id") or ref),
        "prompt": str(payload.get("prompt") or "").strip(),
        "source": "live_prompt",
        "choices": choices_out,
        "responded": responded,
        "present": present_n,
        "response_count": responded,
        "response_seq": _fingerprint(count_list, responded),
    }


def build_live_tally(
    prompt: Any,
    *,
    responses: list[dict[str, Any]] | None = None,
    meet_chain: Any = None,
    present: int = 0,
    teacher_state: Any = None,
) -> dict[str, Any] | None:
    """Build an MC, numeric, or rank tally for the staff/student results graph.

    Args:
        prompt: Active live-prompt row.
        responses: Parsed response rows.
        meet_chain: Optional MeetChainState.
        present: Students currently in the session.
        teacher_state: Optional public LiveTeacherState.
    """
    if is_rank_prompt(prompt):
        return build_rank_tally(prompt, responses=responses, present=present)
    numeric = build_numeric_tally(
        prompt,
        responses=responses,
        present=present,
        teacher_state=teacher_state,
    )
    if numeric is not None:
        return numeric
    return build_mc_tally(
        prompt,
        responses=responses,
        meet_chain=meet_chain,
        present=present,
        teacher_state=teacher_state,
    )

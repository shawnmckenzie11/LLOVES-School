"""Normalize IMSCC / overlay multiple-choice rows for live-class import."""

from __future__ import annotations

import html
import json
import re
from typing import Any

from question_math import (
    enrich_live_mc_display,
    extract_image_src,
    normalize_house_tex,
)

CHOICE_LETTERS = "ABCDEFGH"


def parse_module_token(raw: Any) -> int | None:
    """Return a 1–8 module number from ``M1`` / ``1`` / ``module 2``.

    Args:
        raw: Staff module token.

    Returns:
        Module number, or ``None`` when the token is empty or out of range.
    """
    token = str(raw or "").strip().lower()
    if not token:
        return None
    match = re.search(r"(?:module\s*)?m?(\d+)", token)
    if not match:
        return None
    number = int(match.group(1))
    if 1 <= number <= 8:
        return number
    return None


def _normalize_bank_title(title: str) -> str:
    """Collapse bank titles for recommended-bank de-duplication.

    Args:
        title: Question-bank display title.
    """
    return re.sub(r"\s+", " ", str(title or "").strip().lower())


def _module_needles(module_number: int) -> tuple[str, ...]:
    """Return title/import_key fragments that mean this module."""
    n = int(module_number)
    return (
        f"chapter {n}",
        f"ch {n}",
        f"ch{n}",
        f"module {n}",
        f"m{n}",
    )


def bank_matches_module(
    *,
    title: str,
    import_key: str,
    module_number: int,
) -> bool:
    """True when a bank title or import key names this module.

    Args:
        title: Question-bank title.
        import_key: Ingest import key.
        module_number: One-based module index.
    """
    haystack = f"{title} {import_key}".lower()
    haystack = re.sub(r"[_\-:]+", " ", haystack)
    for needle in _module_needles(int(module_number)):
        if re.search(rf"(?<![0-9a-z]){re.escape(needle)}(?![0-9])", haystack):
            return True
    return False


def is_primary_module_test_bank(*, title: str, module_number: int) -> bool:
    """True when the title looks like the primary ``Module N Test`` bank.

    Args:
        title: Question-bank title.
        module_number: One-based module index.
    """
    normalized = _normalize_bank_title(title)
    n = int(module_number)
    return bool(
        re.search(rf"\b(?:module\s*{n}|m{n})\b.*\btest\b", normalized)
        or re.search(rf"\btest\b.*\b(?:module\s*{n}|m{n})\b", normalized)
    )


def _plain_from_html(raw: Any) -> str:
    """Strip tags and unescape one HTML fragment."""
    text = html.unescape(str(raw or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def simplify_html(raw: Any) -> str:
    """Documented alias of ``_plain_from_html`` for restored bank tests."""
    return _plain_from_html(raw)


def _plain_stem_from_html(raw_stem_html: str) -> str:
    """Return searchable stem text, with fallbacks for image/table-only stems.

    Args:
        raw_stem_html: Ingest stem HTML.
    """
    plain = simplify_html(raw_stem_html)
    if plain:
        return plain
    lowered = str(raw_stem_html or "").lower()
    if extract_image_src(raw_stem_html):
        return "Refer to the graph."
    if "<table" in lowered:
        return "Refer to the table."
    return ""


def _plain_option_from_html(raw_choice_html: str, option_index: int) -> str:
    """Return searchable option text, using letter labels for image-only choices.

    Args:
        raw_choice_html: Ingest choice HTML.
        option_index: Zero-based option index.
    """
    plain = simplify_html(raw_choice_html)
    if plain:
        return plain
    if extract_image_src(raw_choice_html) and 0 <= option_index < len(CHOICE_LETTERS):
        return CHOICE_LETTERS[option_index]
    return ""


def _choice_letter(index: int) -> str:
    """Return A–H for a zero-based choice index."""
    if 0 <= index < len(CHOICE_LETTERS):
        return CHOICE_LETTERS[index]
    return ""


def _parse_options(raw: Any) -> list[str]:
    """Return overlay or payload option strings."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return [raw] if raw.strip() else []
    if not isinstance(raw, list):
        return []
    options: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            options.append(
                _plain_from_html(item.get("text") or item.get("html") or "")
            )
        else:
            options.append(_plain_from_html(item))
    return [opt for opt in options if opt]


def _letter_from_correct(
    *,
    overlay_answer: Any,
    choices: list[dict[str, Any]],
    options: list[str],
    payload: dict[str, Any],
) -> str:
    """Resolve one A–H key from overlay, correct ids, or choice flags."""
    letter = str(overlay_answer or "").strip().upper()
    if letter and letter in CHOICE_LETTERS:
        return letter
    correct_ids = payload.get("correct_ids") or []
    if isinstance(correct_ids, list) and correct_ids:
        wanted = str(correct_ids[0] or "")
        for index, choice in enumerate(choices):
            if str(choice.get("id") or "") == wanted:
                return _choice_letter(index)
    for index, choice in enumerate(choices):
        if choice.get("correct"):
            return _choice_letter(index)
    if letter.isdigit():
        return _choice_letter(int(letter))
    if letter and letter in {opt.upper() for opt in options}:
        for index, opt in enumerate(options):
            if opt.upper() == letter:
                return _choice_letter(index)
    return ""


def normalize_bank_mc(
    *,
    question_id: int,
    bank_id: int,
    item_type: str,
    payload: Any,
    overlay: Any = None,
    class_id: Any = None,
    school: Any = None,
    library_id: Any = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return one live-class MC dict, or ``(None, reason)`` when unusable.

    Overlay stem/options/key win over the ingested payload. Raw HTML is
    kept on ``_rich_stem_html`` / ``_rich_option_htmls`` so
    ``enrich_live_mc_display`` can attach formatted ``text_html`` and
    resolved graph images.

    Args:
        question_id: ``questions.id``.
        bank_id: ``question_banks.id``.
        item_type: Ingested Canvas item type.
        payload: Question JSON blob.
        overlay: Optional staff overlay row.
        class_id: Class id for bank-image URL resolution.
        school: School DB for optional image mirroring.
        library_id: Content library id for cartridge asset lookup.

    Returns:
        ``(normalized, skip_reason)``. ``skip_reason`` is set only when
        the row cannot become a single-key MC.
    """
    kind = str(item_type or "").strip().lower()
    if kind not in {"multiple_choice_question", "multiple_choice", "mc"}:
        return None, "not_multiple_choice"
    blob = payload if isinstance(payload, dict) else {}
    overlay_row = overlay if isinstance(overlay, dict) else {}
    choices = blob.get("choices") if isinstance(blob.get("choices"), list) else []
    choice_rows = [item for item in choices if isinstance(item, dict)]
    raw_stem_html = str(blob.get("stem_html") or blob.get("text_html") or "").strip()
    raw_option_htmls = [
        str(item.get("html") or item.get("text") or "") for item in choice_rows
    ]
    image_src = extract_image_src(raw_stem_html) or str(
        blob.get("image_url") or blob.get("image_src") or ""
    ).strip()
    options = _parse_options(overlay_row.get("options_json"))
    if not options:
        options = []
        for index, raw_choice in enumerate(raw_option_htmls):
            plain = _plain_option_from_html(raw_choice, index)
            if plain:
                options.append(plain)
            if not image_src:
                image_src = extract_image_src(raw_choice)
    else:
        for raw_choice in raw_option_htmls:
            if not image_src:
                image_src = extract_image_src(raw_choice)
    stem = str(overlay_row.get("stem_text") or "").strip()
    if not stem:
        stem = _plain_stem_from_html(raw_stem_html)
    if not stem:
        return None, "empty_stem"
    stem = normalize_house_tex(stem)
    options = [normalize_house_tex(opt) for opt in options]
    if len(options) < 2:
        return None, "need_two_options"
    if not overlay_row.get("correct_answer"):
        correct_ids = blob.get("correct_ids") or []
        if not isinstance(correct_ids, list):
            correct_ids = []
        correct_indexes = [
            idx
            for idx, choice in enumerate(choice_rows)
            if choice.get("correct") or str(choice.get("id") or "") in {
                str(item) for item in correct_ids
            }
        ]
        if len(correct_indexes) == 0:
            return None, "no_correct_answer"
        if len(correct_indexes) > 1:
            return None, "multiple_correct_answers"
    key = _letter_from_correct(
        overlay_answer=overlay_row.get("correct_answer"),
        choices=choice_rows,
        options=options,
        payload=blob,
    )
    if not key:
        return None, "no_correct_answer"
    points = overlay_row.get("points")
    if points in (None, ""):
        points = blob.get("points_possible")
    try:
        points_value = float(points) if points not in (None, "") else 1.0
    except (TypeError, ValueError):
        points_value = 1.0
    live = {
        "type": "mc",
        "text": stem,
        "options": options,
        "correct_answer": key,
        "key": key,
        "points": points_value,
        "source_question_id": int(question_id),
        "source_bank_id": int(bank_id),
        "_rich_stem_html": raw_stem_html,
        "_rich_option_htmls": raw_option_htmls[: len(options)],
        "builder_item_id": str(blob.get("builder_item_id") or ""),
    }
    if image_src:
        live["image_src"] = image_src
    enrich_live_mc_display(
        live, class_id=class_id, school=school, library_id=library_id
    )
    return live, None


def is_bank_rank_payload(payload: Any) -> bool:
    """True when a stored bank question is a rank (put-in-order) item.

    Rank items live as ``essay_question`` rows whose payload ``type`` is
    ``rank``. Staff polls share the item type but carry ``type: poll``.

    Args:
        payload: Parsed ``questions.payload_json``.
    """
    if not isinstance(payload, dict):
        return False
    return str(payload.get("type") or "").strip().lower() == "rank"


def normalize_bank_rank(
    *,
    question_id: int,
    bank_id: int,
    title: str,
    payload: Any,
    bank_title: str = "",
) -> tuple[dict[str, Any] | None, str | None]:
    """Return one live-class rank item, or ``(None, reason)`` when unusable.

    The result imports straight into a class playlist: ``type`` is
    ``rank``, ``rank_options`` hold stable ids, and ``rank_key`` survives
    only when it still lists every option once.

    Args:
        question_id: ``questions.id``.
        bank_id: ``question_banks.id``.
        title: Question title.
        payload: Parsed payload with ``type: rank``.
        bank_title: Bank label copied onto the normalized row.

    Returns:
        ``(normalized, skip_reason)``.
    """
    try:
        from live_rank import MIN_RANK_OPTIONS, safe_rank_key, safe_rank_options
    except ImportError:
        from lms.live_rank import MIN_RANK_OPTIONS, safe_rank_key, safe_rank_options

    blob = payload if isinstance(payload, dict) else {}
    if not is_bank_rank_payload(blob):
        return None, "not_rank"
    stem = ""
    for key in ("text", "prompt", "stem_text", "stem_plain"):
        stem = str(blob.get(key) or "").strip()
        if stem:
            break
    if not stem:
        stem = _plain_stem_from_html(
            str(blob.get("stem_html") or blob.get("text_html") or "")
        )
    if not stem:
        stem = str(title or "").strip()
    if not stem:
        return None, "empty_stem"
    raw_options = blob.get("rank_options")
    if not raw_options:
        raw_options = blob.get("options")
    if not raw_options and isinstance(blob.get("choices"), list):
        # Ingest-style choices carry ``html``; plain them into labels.
        raw_options = [
            (
                str(choice.get("label") or choice.get("text") or "")
                or _plain_option_from_html(str(choice.get("html") or ""), index)
            )
            if isinstance(choice, dict)
            else choice
            for index, choice in enumerate(blob["choices"])
        ]
    rank_options = safe_rank_options(raw_options)
    if len(rank_options) < MIN_RANK_OPTIONS:
        return None, "need_three_options"
    labels = [row["label"] for row in rank_options]
    points = blob.get("points", blob.get("points_possible"))
    try:
        points_value = float(points) if points not in (None, "") else 1.0
    except (TypeError, ValueError):
        points_value = 1.0
    live: dict[str, Any] = {
        "type": "rank",
        "text": stem,
        "prompt": stem,
        "rank_options": rank_options,
        "options": labels,
        "choices": list(labels),
        "points": points_value,
        "response_mode": "individual",
        "publish_modes": ["individual"],
        "question_id": int(question_id),
        "bank_id": int(bank_id),
        "bank_title": str(bank_title or ""),
        "question_title": str(title or stem)[:80],
        "source_question_id": int(question_id),
        "source_bank_id": int(bank_id),
    }
    key = safe_rank_key(blob.get("rank_key"), rank_options)
    if key:
        live["rank_key"] = key
    for field in ("equation", "equation_latex", "image_url", "image_alt"):
        value = str(blob.get(field) or "").strip()
        if value:
            live[field] = value
    # Both are on the teacher-only lists (TEACHER_ONLY_FIELDS), so students
    # never see them.
    teacher_key = _teacher_key_text(blob.get("teacher_key"))
    if teacher_key:
        live["teacher_key"] = teacher_key
    teacher_note = _teacher_key_text(blob.get("teacher_note"))
    if teacher_note:
        live["teacher_note"] = teacher_note
    # Deliberately not copied: ``live_class`` / ``live_class_slot`` (the
    # import URL picks the class, not the bank row).
    return live, None


def _teacher_key_text(raw: Any) -> str:
    """Flatten a stored teacher key (text or nested dict) into one line.

    Args:
        raw: ``teacher_key`` from a bank payload.

    Returns:
        Plain text such as ``"A: y/x = -3/2; C: 4/27"``, or ``""``.
    """
    if isinstance(raw, dict):
        parts: list[str] = []
        for key, value in raw.items():
            inner = _teacher_key_text(value)
            if inner:
                parts.append(f"{key}: {inner}" if isinstance(value, (str, int, float)) else inner)
        return "; ".join(parts)[:1000]
    if isinstance(raw, list):
        return "; ".join(t for t in (_teacher_key_text(v) for v in raw) if t)[:1000]
    if isinstance(raw, (str, int, float)) and not isinstance(raw, bool):
        return str(raw).strip()[:1000]
    return ""

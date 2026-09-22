"""Staff Question banks tab: serialize, sanitize, and persist bank edits.

Imported cartridge rows stay in ``questions``. Teacher edits land in
``library_question_overlays`` so live-class import still sees the LMS
override without rewriting ingest HTML. New questions are staff-authored
rows in the selected bank.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

try:
    from question_math import (
        format_math_html,
        format_mc_html_fragment,
        html_to_plain,
        normalize_house_tex,
    )
except ImportError:
    from lms.question_math import (
        format_math_html,
        format_mc_html_fragment,
        html_to_plain,
        normalize_house_tex,
    )

# Wonder IA §9 locked microcopy — do not paraphrase.
EMPTY_BANKS_MESSAGE = "No banks imported yet."
EDIT_BANK_LABEL = "Edit bank"
DONE_LABEL = "Done"
CANCEL_LABEL = "Cancel"
REMOVE_FROM_BANK_TITLE = "Remove from bank?"
EDITED_IN_LMS_CHIP = "Edited in LMS"
SAVE_TOAST = "Saved to this course’s copy."

WONDER_COPY = {
    "empty_banks": EMPTY_BANKS_MESSAGE,
    "edit_bank": EDIT_BANK_LABEL,
    "done": DONE_LABEL,
    "cancel": CANCEL_LABEL,
    "remove_from_bank": REMOVE_FROM_BANK_TITLE,
    "edited_in_lms": EDITED_IN_LMS_CHIP,
    "save_toast": SAVE_TOAST,
}

STEM_PREVIEW_LIMIT = 140
MC_ITEM_TYPES = frozenset(
    {
        "multiple_choice_question",
        "multiple_choice",
        "mc",
        "true_false_question",
    }
)
_SCRIPT_OR_STYLE_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_SCRIPT_OR_STYLE_OPEN_RE = re.compile(
    r"<(script|style)\b[^>]*/?>",
    re.IGNORECASE,
)

ALLOWED_NEW_ITEM_TYPES = frozenset(
    {
        "multiple_choice_question",
        "essay_question",
        "numerical_question",
        "short_answer_question",
        "true_false_question",
    }
)
CHOICE_LETTERS = "ABCDEFGH"


def sanitize_bank_html(
    raw: str,
    *,
    class_id: int | None = None,
    school: Any = None,
    library_id: int | None = None,
) -> str:
    """Sanitize teacher- or ingest-supplied HTML for bank display and save.

    Args:
        raw: HTML or plain text.
        class_id: Class id used to resolve cartridge image URLs.
        school: School database for optional image mirroring.
        library_id: Content library id for cartridge assets.
    """
    text = _SCRIPT_OR_STYLE_RE.sub("", str(raw or ""))
    text = _SCRIPT_OR_STYLE_OPEN_RE.sub("", text)
    return format_mc_html_fragment(
        text,
        class_id=class_id,
        school=school,
        library_id=library_id,
    )


def truncate_stem(plain: str, limit: int = STEM_PREVIEW_LIMIT) -> str:
    """Return a single-line stem preview, truncated with an ellipsis.

    Args:
        plain: Plain-text stem.
        limit: Maximum characters before truncation.
    """
    text = " ".join(str(plain or "").split())
    if len(text) <= int(limit):
        return text
    return text[: int(limit)].rstrip() + "…"


def truncate_stem_preserving_math(plain: str, limit: int = STEM_PREVIEW_LIMIT) -> str:
    """Truncate a stem without splitting a ``$...$`` pair.

    Args:
        plain: Plain-text stem that may contain house-style TeX.
        limit: Soft character budget before an ellipsis.
    """
    text = " ".join(str(plain or "").split())
    if len(text) <= int(limit):
        return text
    cut = int(limit)
    prefix = text[:cut]
    if len(re.findall(r"(?<!\\)\$", prefix)) % 2 == 1:
        nxt = text.find("$", cut)
        if 0 <= nxt <= cut + 48:
            cut = nxt + 1
        else:
            prev = prefix.rfind("$")
            if prev > 0:
                cut = prev
    clipped = text[:cut].rstrip()
    return clipped + ("…" if clipped != text else "")


def preview_stem_html(plain: str, limit: int = STEM_PREVIEW_LIMIT) -> str:
    """Return browse-line HTML so leftover ``$TeX$`` still typesets.

    Args:
        plain: Plain-text stem.
        limit: Soft character budget matching :func:`truncate_stem`.
    """
    return format_math_html(truncate_stem_preserving_math(plain, limit))


def is_mc_item_type(item_type: str) -> bool:
    """Return True when the Canvas item type is a single-key MC family.

    Args:
        item_type: ``questions.item_type``.
    """
    return str(item_type or "").strip().lower() in MC_ITEM_TYPES


def payload_is_warmup(payload: dict[str, Any] | None) -> bool:
    """Return True when a stored question payload is tagged warmup.

    Matches Run Live Class Import: ``kind`` / ``bank_kind`` / ``problem_kind``
    or a ``warmup`` tag. Course Wide icebreakers use ``kind=warmup``. This
    does not invent a new question type.

    Args:
        payload: Parsed ``questions.payload_json``, or None.
    """
    if not isinstance(payload, dict):
        return False
    for key in ("kind", "bank_kind", "problem_kind"):
        if str(payload.get(key) or "").strip().lower() == "warmup":
            return True
    raw_tags = payload.get("tags")
    if raw_tags is None:
        raw_tags = payload.get("tag")
    if isinstance(raw_tags, str):
        tags = [part.strip() for part in raw_tags.split(",")]
    elif isinstance(raw_tags, list):
        tags = [str(part or "") for part in raw_tags]
    else:
        tags = []
    return any(tag.strip().lower() == "warmup" for tag in tags)


def _overlay_dict(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalize a ``library_question_overlays`` row for serializers."""
    if not row:
        return None
    if row.get("stem_text") is None and row.get("options_json") is None:
        return None
    options: list[str] = []
    raw_options = row.get("options_json")
    if isinstance(raw_options, list):
        options = [str(opt) for opt in raw_options]
    else:
        try:
            parsed = json.loads(raw_options or "[]")
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list):
            options = [str(opt) for opt in parsed]
    return {
        "stem_text": str(row.get("stem_text") or ""),
        "options": options,
        "correct_answer": str(row.get("correct_answer") or "").strip().upper(),
        "points": row.get("points"),
    }


def _choice_letter(index: int) -> str:
    """Return the A–H letter for a 0-based option index."""
    if 0 <= int(index) < len(CHOICE_LETTERS):
        return CHOICE_LETTERS[int(index)]
    return ""


def _points_value(payload: dict[str, Any], overlay: dict[str, Any] | None) -> float | None:
    """Resolve overlay points, then ingest ``points_possible``."""
    if overlay and overlay.get("points") not in (None, ""):
        try:
            return float(overlay["points"])
        except (TypeError, ValueError):
            return None
    raw = payload.get("points_possible")
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def serialize_staff_question(
    question: dict[str, Any],
    overlay_row: dict[str, Any] | None,
    *,
    class_id: int | None = None,
    school: Any = None,
    library_id: int | None = None,
) -> dict[str, Any]:
    """Build the staff Question banks tab view of one question.

    Args:
        question: Row from :func:`components.list_questions`.
        overlay_row: Optional ``library_question_overlays`` row.
        class_id: Class id for image URL resolution.
        school: School database.
        library_id: Content library id.
    """
    payload = question.get("payload") if isinstance(question.get("payload"), dict) else {}
    overlay = _overlay_dict(overlay_row)
    import_key = str(question.get("import_key") or "")
    authored = import_key.startswith("staff-q-") or import_key.startswith("staff-")
    edited = overlay is not None and not authored
    item_type = str(question.get("item_type") or "")
    raw_stem = str(payload.get("stem_html") or payload.get("text_html") or "")
    if overlay and overlay.get("stem_text"):
        raw_stem = overlay["stem_text"]
    stem_html = sanitize_bank_html(
        raw_stem, class_id=class_id, school=school, library_id=library_id
    )
    stem_plain = html_to_plain(stem_html) or str(
        (overlay or {}).get("stem_text") or question.get("title") or ""
    ).strip()
    payload_choices = payload.get("choices") if isinstance(payload.get("choices"), list) else []
    choices: list[dict[str, Any]] = []
    if overlay and overlay.get("options"):
        correct = overlay.get("correct_answer") or ""
        for index, option in enumerate(overlay["options"]):
            letter = _choice_letter(index)
            option_html = sanitize_bank_html(
                option, class_id=class_id, school=school, library_id=library_id
            )
            choices.append(
                {
                    "id": letter,
                    "html": option_html,
                    "text": html_to_plain(option_html) or str(option),
                    "correct": letter == correct,
                }
            )
    else:
        for index, choice in enumerate(payload_choices):
            if not isinstance(choice, dict):
                continue
            choice_html = sanitize_bank_html(
                str(choice.get("html") or choice.get("text") or ""),
                class_id=class_id,
                school=school,
                library_id=library_id,
            )
            choices.append(
                {
                    "id": str(choice.get("id") or _choice_letter(index)),
                    "html": choice_html,
                    "text": html_to_plain(choice_html),
                    "correct": bool(choice.get("correct")),
                }
            )
    correct_answers = [
        str(answer) for answer in (payload.get("correct_answers") or []) if str(answer)
    ]
    if overlay and overlay.get("correct_answer"):
        correct_answers = [overlay["correct_answer"]]
    return {
        "id": int(question["id"]),
        "bank_id": int(question.get("bank_id") or 0) or None,
        "import_key": import_key,
        "item_type": item_type,
        "title": str(question.get("title") or ""),
        "payload": payload,
        "stem_plain": stem_plain,
        "stem_preview": truncate_stem(stem_plain),
        "stem_preview_html": preview_stem_html(stem_plain),
        "stem_html": stem_html,
        "choices": choices,
        "correct_answers": correct_answers,
        "points": _points_value(payload, overlay),
        "edited_in_lms": edited,
        "authored_in_lms": authored,
    }


def list_staff_bank_questions(
    school: Any,
    library_id: int,
    bank_id: int,
    *,
    class_id: int | None = None,
) -> list[dict[str, Any]]:
    """List one bank's questions with overlay merge for the staff tab.

    Args:
        school: School database.
        library_id: ``content_libraries.id``.
        bank_id: ``question_banks.id``.
        class_id: Class id for image URL resolution.
    """
    try:
        from components import list_questions
    except ImportError:
        from lms.components import list_questions

    rows = list_questions(school, int(library_id), int(bank_id))
    ids = [int(row["id"]) for row in rows]
    overlays = school.list_library_question_overlays(int(library_id), ids)
    out = []
    for row in rows:
        view = serialize_staff_question(
            row,
            overlays.get(int(row["id"])),
            class_id=class_id,
            school=school,
            library_id=int(library_id),
        )
        view["bank_id"] = int(bank_id)
        out.append(view)
    try:
        from bank_dedupe import drop_non_canonical, library_canonical_ids
    except ImportError:
        from lms.bank_dedupe import drop_non_canonical, library_canonical_ids

    canonical = library_canonical_ids(school, int(library_id))
    kept, _hidden = drop_non_canonical(out, canonical)
    return kept


def _parse_options(body: dict[str, Any]) -> list[str]:
    """Read option strings from a PATCH/POST body."""
    options = body.get("options") or body.get("choices") or []
    if not isinstance(options, list):
        return []
    out: list[str] = []
    for item in options:
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("html") or "").strip()
        else:
            text = str(item or "").strip()
        if text:
            out.append(normalize_house_tex(text))
    return out


def _parse_correct_answer(body: dict[str, Any], options: list[str]) -> str:
    """Resolve an A–H key from a PATCH/POST body."""
    raw = str(body.get("correct_answer") or body.get("correctAnswer") or "").strip()
    if raw.upper() in CHOICE_LETTERS:
        return raw.upper()
    choices = body.get("choices")
    if isinstance(choices, list):
        for index, item in enumerate(choices):
            if isinstance(item, dict) and item.get("correct"):
                return _choice_letter(index)
    if raw.isdigit():
        return _choice_letter(int(raw))
    wanted = raw.lower()
    for index, option in enumerate(options):
        if option.lower() == wanted:
            return _choice_letter(index)
    return ""


def _parse_points(body: dict[str, Any]) -> float | None:
    """Parse optional points from a write body."""
    raw = body.get("points")
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid points") from exc


def _stem_from_body(
    body: dict[str, Any],
    *,
    class_id: int | None,
    school: Any,
    library_id: int,
) -> tuple[str, str]:
    """Return ``(sanitized_html, plain_text)`` from a write body."""
    raw = normalize_house_tex(
        str(body.get("stem_html") or body.get("stem_text") or body.get("text") or "")
    )
    stem_html = sanitize_bank_html(
        raw, class_id=class_id, school=school, library_id=library_id
    )
    stem_plain = html_to_plain(stem_html).strip()
    if not stem_plain:
        raise ValueError("stem_text required")
    return stem_html, stem_plain


def apply_staff_question_patch(
    school: Any,
    *,
    library_id: int,
    bank_id: int,
    question_id: int,
    body: dict[str, Any],
    class_id: int | None = None,
) -> dict[str, Any]:
    """Save one bank question. Imported ``item_type`` is never rewritten.

    Args:
        school: School database.
        library_id: ``content_libraries.id``.
        bank_id: ``question_banks.id``.
        question_id: ``questions.id``.
        body: JSON PATCH body.
        class_id: Class id for image URL resolution.
    """
    row = school.get_library_question(int(library_id), int(question_id))
    if row is None or int(row["bank_id"]) != int(bank_id):
        raise KeyError(f"question {question_id}")
    item_type = str(row.get("item_type") or "")
    stored = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    warmup = payload_is_warmup(stored)
    stem_html, stem_plain = _stem_from_body(
        body, class_id=class_id, school=school, library_id=int(library_id)
    )
    options = _parse_options(body)
    points = _parse_points(body)
    if is_mc_item_type(item_type):
        if len(options) < 2 and not (warmup and not options):
            raise ValueError("at least two options required")
        correct = _parse_correct_answer(body, options)
        if not correct and not warmup:
            raise ValueError("correct_answer required")
        school.upsert_library_question_overlay(
            int(library_id),
            int(question_id),
            stem_text=stem_plain,
            options=options,
            correct_answer=correct,
            points=points,
        )
    else:
        school.upsert_library_question_overlay(
            int(library_id),
            int(question_id),
            stem_text=stem_plain,
            options=[],
            correct_answer="",
            points=points,
        )
    import_key = str(row.get("import_key") or "")
    if warmup or import_key.startswith("staff-q-"):
        payload = dict(stored)
        payload["stem_html"] = stem_html
        if warmup:
            payload["kind"] = "warmup"
        if options:
            correct = "" if warmup else _parse_correct_answer(body, options)
            payload["choices"] = [
                {
                    "id": _choice_letter(index),
                    "html": sanitize_bank_html(
                        option,
                        class_id=class_id,
                        school=school,
                        library_id=int(library_id),
                    ),
                    "correct": (not warmup) and _choice_letter(index) == correct,
                }
                for index, option in enumerate(options)
            ]
            if warmup:
                payload["type"] = "mc"
        if points is not None:
            payload["points_possible"] = points
        school.update_library_question_payload(
            int(library_id),
            int(question_id),
            title=stem_plain[:80] or str(row.get("title") or "Question"),
            payload=payload,
        )
    return serialize_staff_question(
        school.get_library_question(int(library_id), int(question_id)) or row,
        school.get_library_question_overlay(int(library_id), int(question_id)),
        class_id=class_id,
        school=school,
        library_id=int(library_id),
    )


def create_staff_bank_question(
    school: Any,
    *,
    library_id: int,
    bank_id: int,
    body: dict[str, Any],
    class_id: int | None = None,
) -> dict[str, Any]:
    """Insert one teacher-authored question into an existing bank.

    Args:
        school: School database.
        library_id: ``content_libraries.id``.
        bank_id: ``question_banks.id``.
        body: JSON POST body.
        class_id: Class id for image URL resolution.
    """
    requested = str(body.get("item_type") or "multiple_choice_question").strip()
    item_type = (
        requested
        if requested in ALLOWED_NEW_ITEM_TYPES
        else "multiple_choice_question"
    )
    stem_html, stem_plain = _stem_from_body(
        body, class_id=class_id, school=school, library_id=int(library_id)
    )
    options = _parse_options(body)
    points = _parse_points(body)
    if is_mc_item_type(item_type) and len(options) < 2:
        raise ValueError("at least two options required")
    correct = _parse_correct_answer(body, options) if is_mc_item_type(item_type) else ""
    if is_mc_item_type(item_type) and not correct:
        raise ValueError("correct_answer required")
    payload: dict[str, Any] = {
        "stem_html": stem_html,
        "points_possible": points if points is not None else 1.0,
        "position": 10**6,
    }
    if options:
        payload["choices"] = [
            {
                "id": _choice_letter(index),
                "html": sanitize_bank_html(
                    option,
                    class_id=class_id,
                    school=school,
                    library_id=int(library_id),
                ),
                "correct": _choice_letter(index) == correct,
            }
            for index, option in enumerate(options)
        ]
    question_id = school.insert_library_bank_question(
        int(library_id),
        int(bank_id),
        item_type=item_type,
        title=stem_plain[:80] or "Staff question",
        payload=payload,
    )
    if is_mc_item_type(item_type):
        school.upsert_library_question_overlay(
            int(library_id),
            int(question_id),
            stem_text=stem_plain,
            options=options,
            correct_answer=correct,
            points=points,
        )
    row = school.get_library_question(int(library_id), int(question_id))
    if row is None:
        raise KeyError(f"question {question_id}")
    return serialize_staff_question(
        row,
        school.get_library_question_overlay(int(library_id), int(question_id)),
        class_id=class_id,
        school=school,
        library_id=int(library_id),
    )


def delete_staff_bank_question(
    school: Any,
    *,
    library_id: int,
    bank_id: int,
    question_id: int,
) -> None:
    """Remove one question from a bank.

    Args:
        school: School database.
        library_id: ``content_libraries.id``.
        bank_id: ``question_banks.id``.
        question_id: ``questions.id``.
    """
    school.delete_library_bank_question(
        int(library_id), int(bank_id), int(question_id)
    )


def staff_authored_import_key() -> str:
    """Return a unique import key for a teacher-authored bank question."""
    return f"staff-q-{uuid.uuid4().hex}"

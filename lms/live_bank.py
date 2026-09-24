"""Course-tab writes into the same LMS bank Run Live Class Add New uses.

Reads stay on ``search_bank_scope_mcs`` / module ``mc-search`` so warmup
filtering is not forked. This module only inserts a staff-authored question
into the LMS sqlite bank. It does not write the content-builder catalogue
and does not replace module bank links.
"""

from __future__ import annotations

from typing import Any

try:
    from bank_edit import create_staff_bank_question
except ImportError:
    from lms.bank_edit import create_staff_bank_question

LIVE_BANK_TYPES = frozenset({"mc", "numeric", "poll", "rank"})


def create_live_bank_question(
    school: Any,
    *,
    library_id: int,
    bank_scope: str,
    body: dict[str, Any],
    class_id: int | None = None,
) -> dict[str, Any]:
    """Insert one staff question into the live bank for a module or Course Wide.

    Uses the staff-authored bank and ``INSERT OR IGNORE`` module links — the
    same tables Add New writes when Save to bank is checked. Course Wide links
    modules 1–8 without touching the Course Wide warmup bank. New rows are
    untagged, so Process Import can see them and Warmup stays a filter.

    Args:
        school: School database.
        library_id: ``content_libraries.id``.
        bank_scope: ``M1``–``M8`` or ``course``.
        body: Stem, options, and type (``mc``, ``numeric``, ``poll``, or ``rank``).
        class_id: Class id for image URL resolution.

    Returns:
        Serialized staff question, plus ``bank_id`` and ``bank_scope``.
    """
    kind = str(body.get("type") or body.get("question_type") or "mc").strip().lower()
    if kind not in LIVE_BANK_TYPES:
        raise ValueError("type must be mc, numeric, poll, or rank")
    rank_options: list[dict[str, str]] = []
    if kind == "rank":
        try:
            from live_rank import build_rank_options
        except ImportError:
            from lms.live_rank import build_rank_options

        rank_options = build_rank_options(
            body.get("rank_options") or body.get("options") or []
        )
    if kind == "numeric":
        key = str(body.get("correct_answer") or body.get("correctAnswer") or "").strip()
        if not key:
            raise ValueError("correct answer required")
        try:
            float(key)
        except (TypeError, ValueError) as exc:
            raise ValueError("correct answer must be a number") from exc
    scope = str(bank_scope or "M1").strip() or "M1"
    bank_id = int(school._ensure_staff_authored_bank(int(library_id)))
    for number in school._bank_scope_module_numbers(scope, 1):
        school._ensure_module_bank_link(int(library_id), int(number), bank_id)
    item_type = {
        "mc": "multiple_choice_question",
        "numeric": "numerical_question",
        "poll": "essay_question",
        "rank": "essay_question",
    }[kind]
    write = dict(body)
    write["item_type"] = item_type
    if kind == "rank":
        write["options"] = [row["label"] for row in rank_options]
    write.pop("kind", None)
    write.pop("tags", None)
    question = create_staff_bank_question(
        school,
        library_id=int(library_id),
        bank_id=bank_id,
        body=write,
        class_id=class_id,
    )
    payload = question.get("payload") if isinstance(question.get("payload"), dict) else {}
    payload = dict(payload)
    payload["type"] = kind
    payload.pop("kind", None)
    if kind == "numeric":
        key = str(body.get("correct_answer") or body.get("correctAnswer") or "").strip()
        if not key:
            raise ValueError("correct answer required")
        payload["correct_answer"] = key
        payload["key"] = key
    if kind == "poll":
        payload["options"] = []
        payload["choices"] = []
    if kind == "rank":
        labels = [row["label"] for row in rank_options]
        payload["type"] = "rank"
        payload["kind"] = "rank"
        payload["options"] = labels
        payload["choices"] = labels
        payload["rank_options"] = rank_options
        payload.pop("key", None)
        payload.pop("correct_answer", None)
    school.update_library_question_payload(
        int(library_id),
        int(question["id"]),
        title=str(question.get("title") or "Staff question"),
        payload=payload,
    )
    question["payload"] = payload
    question["bank_id"] = bank_id
    question["bank_scope"] = scope
    return question

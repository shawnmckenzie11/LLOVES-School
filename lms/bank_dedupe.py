"""Detect same-stem bank questions and keep one canonical copy.

Merge rule (library-scoped; MCF3M and MCR3U are separate libraries):

1. Fingerprint = lowercase, HTML-stripped, entity-unescaped stem plus the
   sorted option texts. ``$...$`` / ``\\(...\\)`` wrappers are stripped so
   the same equation in TeX or caret form matches. Whitespace, trailing
   punctuation, doubled TeX backslashes, and ``\\frac{a}{b}`` vs ``a/b``
   also collapse (near-duplicates). Better TeX wins when choosing the
   canonical row.
2. Empty stems never collapse (each keeps its own id).
3. Within a course library, one fingerprint maps to one canonical row:
   staff overlay (``edited_in_lms``) wins, then staff-authored, then a
   bank title that looks like a Module N Test, then the richest HTML
   (tables / images / math spans), then the lowest ``questions.id``.
4. Hidden copies stay in sqlite so IMSCC re-ingest and overlays stay
   stable. They are omitted from the staff Question banks tab and from
   live-class bank import search.
5. Cross-course copies (same stem in MCF3M and MCR3U) are not duplicates.
"""

from __future__ import annotations

import json
import re
from typing import Any

try:
    from question_math import collapse_double_tex, html_to_plain
except ImportError:
    from lms.question_math import collapse_double_tex, html_to_plain

_MATH_WRAP_RE = re.compile(
    r"\$\$|\$|\\\(|\\\)|\\\[|\\\]",
)
_DASH_RE = re.compile(r"[−–—]")
_FRAC_TEX_RE = re.compile(r"\\d?frac\{([^{}]+)\}\{([^{}]+)\}")
_SQRT_TEX_RE = re.compile(r"\\sqrt(?:\[[^\[\]]+\])?\{([^{}]+)\}")
_TRAIL_PUNCT_RE = re.compile(r"[.?!]+$")


def normalize_question_text(text: str) -> str:
    """Collapse stem or option text to a comparison key.

    Args:
        text: HTML or plain stem/option.

    Returns:
        Lowercase, entity-decoded, tag-stripped text with math wrappers gone.
        ``\\frac{1}{2}`` and ``1/2`` share a key after this pass.
    """
    raw = str(text or "")
    if "<" in raw or "&" in raw:
        raw = html_to_plain(raw)
    else:
        import html

        raw = html.unescape(raw)
    raw = collapse_double_tex(raw)
    raw = _FRAC_TEX_RE.sub(r"\1/\2", raw)
    raw = _SQRT_TEX_RE.sub(r"sqrt(\1)", raw)
    raw = _MATH_WRAP_RE.sub(" ", raw)
    raw = _DASH_RE.sub("-", raw)
    raw = _TRAIL_PUNCT_RE.sub("", raw)
    return re.sub(r"\s+", " ", raw).strip().lower()


def _question_id(item: dict[str, Any]) -> int:
    """Return a stable numeric id from a staff or live-import row."""
    for key in ("id", "question_id", "source_question_id"):
        raw = item.get(key)
        if raw in (None, ""):
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return 10**9


def _stem_text(item: dict[str, Any]) -> str:
    """Best plain stem for fingerprinting."""
    for key in ("stem_plain", "text", "stem_preview", "title"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return html_to_plain(
        str(item.get("stem_html") or item.get("text_html") or "")
    )


def _stem_html(item: dict[str, Any]) -> str:
    """HTML stem used for richness ranking."""
    return str(item.get("stem_html") or item.get("text_html") or "")


def _option_texts(item: dict[str, Any]) -> list[str]:
    """Collect option strings from staff views or live-import rows."""
    choices = item.get("choices")
    if isinstance(choices, list) and choices:
        out: list[str] = []
        for choice in choices:
            if isinstance(choice, dict):
                out.append(
                    str(choice.get("text") or html_to_plain(choice.get("html") or ""))
                )
            else:
                out.append(str(choice or ""))
        return out
    options = item.get("options")
    if not isinstance(options, list):
        return []
    out = []
    for option in options:
        if isinstance(option, dict):
            out.append(str(option.get("text") or option.get("html") or ""))
        else:
            out.append(str(option or ""))
    return out


def question_fingerprint(item: dict[str, Any]) -> str:
    """Return the merge key for one question.

    Args:
        item: Staff-serialized question or live-import MC dict.

    Returns:
        Fingerprint string. Empty stems use ``id:{n}`` so they never merge.
    """
    stem = normalize_question_text(_stem_text(item))
    qid = _question_id(item)
    if not stem:
        return f"id:{qid}"
    options = [
        key
        for key in sorted(normalize_question_text(opt) for opt in _option_texts(item))
        if key
    ]
    return stem + "||" + "|".join(options)


def _canonical_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    """Lower tuples win when choosing the row to keep."""
    html = _stem_html(item)
    plain = str(item.get("stem_plain") or "")
    tex_quality = (
        html.count("\\frac")
        + html.count("\\sqrt")
        + html.count("math-latex")
        + plain.count("$")
        + plain.count("\\frac")
    )
    richness = (
        html.count("<table"),
        html.count("<img"),
        html.count("math-latex"),
        html.count("<sup"),
        tex_quality,
        len(html),
    )
    title = str(item.get("bank_title") or "").lower()
    primary_test = 1 if re.search(r"\btest\b", title) else 0
    return (
        0 if item.get("edited_in_lms") else 1,
        0 if item.get("authored_in_lms") else 1,
        0 if primary_test else 1,
        tuple(-count for count in richness),
        _question_id(item),
    )


def select_canonical_questions(
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep one row per fingerprint; return ``(kept, dropped)``.

    Args:
        items: Staff or live-import question dicts.

    Returns:
        Kept rows in the original order, plus dropped copies annotated with
        ``duplicate_of``.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(question_fingerprint(item), []).append(item)
    keep_ids: set[int] = set()
    dropped: list[dict[str, Any]] = []
    for fingerprint, group in groups.items():
        if len(group) == 1 or fingerprint.startswith("id:"):
            keep_ids.update(_question_id(row) for row in group)
            continue
        ranked = sorted(group, key=_canonical_sort_key)
        winner = ranked[0]
        keep_ids.add(_question_id(winner))
        for extra in ranked[1:]:
            copy = dict(extra)
            copy["duplicate_of"] = _question_id(winner)
            copy["duplicate_fingerprint"] = fingerprint
            dropped.append(copy)
    kept = [item for item in items if _question_id(item) in keep_ids]
    return kept, dropped


def fingerprint_from_payload(
    *,
    question_id: int,
    payload: dict[str, Any],
    overlay: dict[str, Any] | None = None,
    bank_title: str = "",
    import_key: str = "",
) -> dict[str, Any]:
    """Build a lightweight row for library-wide canonical selection.

    Args:
        question_id: ``questions.id``.
        payload: Ingest JSON.
        overlay: Optional staff overlay.
        bank_title: Owning bank title (Module N Test ranking).
        import_key: Ingest key (staff-authored detection).

    Returns:
        Dict accepted by :func:`select_canonical_questions`.
    """
    overlay_row = overlay if isinstance(overlay, dict) else {}
    stem = str(overlay_row.get("stem_text") or "").strip()
    raw_stem = str(payload.get("stem_html") or payload.get("text_html") or "")
    if not stem:
        stem = html_to_plain(raw_stem)
    options: list[str] = []
    raw_options = overlay_row.get("options_json")
    if isinstance(raw_options, str):
        try:
            raw_options = json.loads(raw_options)
        except json.JSONDecodeError:
            raw_options = []
    if isinstance(raw_options, list) and raw_options:
        options = [str(opt) for opt in raw_options]
    else:
        for choice in payload.get("choices") or []:
            if isinstance(choice, dict):
                options.append(html_to_plain(str(choice.get("html") or choice.get("text") or "")))
    return {
        "id": int(question_id),
        "stem_plain": stem,
        "stem_html": raw_stem,
        "options": options,
        "bank_title": bank_title,
        "edited_in_lms": bool(str(overlay_row.get("stem_text") or "").strip()),
        "authored_in_lms": str(import_key or "").startswith("staff-q-")
        or str(import_key or "").startswith("staff-"),
    }


def library_canonical_ids(school: Any, library_id: int) -> dict[int, int]:
    """Map each question id in a library to the canonical id to keep.

    Args:
        school: Open ``SchoolDB``.
        library_id: ``content_libraries.id``.

    Returns:
        ``{question_id: canonical_question_id}``. Ids that map to themselves
        are the kept rows.
    """
    with school._lock:
        rows = school.conn.execute(
            """
            SELECT q.id, q.import_key, q.payload_json,
                   b.title AS bank_title,
                   o.stem_text, o.options_json
            FROM questions q
            JOIN question_banks b ON b.id = q.bank_id
            LEFT JOIN library_question_overlays o
                ON o.library_id = b.library_id AND o.question_id = q.id
            WHERE b.library_id = ?
            ORDER BY q.id
            """,
            (int(library_id),),
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        overlay = None
        if row["stem_text"] is not None:
            overlay = {
                "stem_text": row["stem_text"],
                "options_json": row["options_json"],
            }
        items.append(
            fingerprint_from_payload(
                question_id=int(row["id"]),
                payload=payload if isinstance(payload, dict) else {},
                overlay=overlay,
                bank_title=str(row["bank_title"] or ""),
                import_key=str(row["import_key"] or ""),
            )
        )
    kept, dropped = select_canonical_questions(items)
    mapping = {_question_id(item): _question_id(item) for item in kept}
    for extra in dropped:
        mapping[_question_id(extra)] = int(extra.get("duplicate_of") or _question_id(extra))
    return mapping


def drop_non_canonical(
    items: list[dict[str, Any]],
    canonical_ids: dict[int, int],
) -> tuple[list[dict[str, Any]], int]:
    """Filter a list to rows whose id is the canonical id.

    Args:
        items: Staff or live-import rows with an id field.
        canonical_ids: Map from :func:`library_canonical_ids`.

    Returns:
        ``(kept, hidden_count)``.
    """
    kept: list[dict[str, Any]] = []
    hidden = 0
    for item in items:
        qid = _question_id(item)
        winner = int(canonical_ids.get(qid, qid))
        if winner == qid:
            kept.append(item)
        else:
            hidden += 1
    return kept, hidden


def apply_visible_bank_question_counts(
    school: Any,
    library_id: int,
    banks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rewrite ``question_count`` so hidden duplicates are not listed.

    Args:
        school: Open ``SchoolDB``.
        library_id: ``content_libraries.id``.
        banks: Rows from :func:`components.list_question_banks`.

    Returns:
        The same list with visible counts. Ingest rows stay in sqlite.
    """
    if not banks:
        return banks
    canonical = library_canonical_ids(school, int(library_id))
    with school._lock:
        rows = school.conn.execute(
            """
            SELECT q.id, q.bank_id
            FROM questions q
            JOIN question_banks b ON b.id = q.bank_id
            WHERE b.library_id = ?
            """,
            (int(library_id),),
        ).fetchall()
    visible: dict[int, int] = {}
    for row in rows:
        qid = int(row["id"])
        if int(canonical.get(qid, qid)) == qid:
            bank_id = int(row["bank_id"])
            visible[bank_id] = visible.get(bank_id, 0) + 1
    for bank in banks:
        bank["question_count"] = visible.get(int(bank["id"]), 0)
    return banks

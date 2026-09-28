"""Staff-facing question-bank Kind labels and storage tokens.

Display names changed for class (2026-09-28): Process is **Core Math** and
Standard is **Custom**. Stored values stay stable so existing sqlite rows,
seeds, and Import filters do not need a migration:

| Staff label | Stored token | Import filter |
|---|---|---|
| Core Math | ``""`` (untagged). Aliases ``process``, ``core-math`` | empty query |
| Custom | ``standard``. Alias ``custom`` | ``standard`` |
| Contest | ``contest`` | ``contest`` |
| Warmup | ``warmup`` | ``warmup`` |

Core Math hides warmup and Custom. Contest stays in the Core Math mix.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Storage tokens the Import filter understands.
STORED_KINDS = frozenset({"warmup", "contest", "standard"})

_CORE_ALIASES = frozenset(
    {"", "process", "core", "core-math", "core_math", "core math"}
)
_CUSTOM_ALIASES = frozenset({"standard", "custom"})

KIND_DISPLAY = {
    "": "Core Math",
    "standard": "Custom",
    "contest": "Contest",
    "warmup": "Warmup",
}

# Module 2 stem Shawn asked to move off Core Math. Apostrophe variants included.
_TEACHING_TODAY_RE = re.compile(
    r"""^(?:you['’]re|youre|you are)\s+teaching\s+today\b""",
    re.IGNORECASE,
)


def storage_bank_kind(raw: Any) -> str:
    """Map a staff Kind choice onto the stored token.

    Args:
        raw: Select value or alias (``custom``, ``process``, ``core-math``).

    Returns:
        ``warmup``, ``contest``, ``standard``, or ``""`` for Core Math.
        Unknown tokens stay untagged so they remain visible in Core Math.
    """
    token = str(raw or "").strip().lower()
    if token in _CORE_ALIASES:
        return ""
    if token in _CUSTOM_ALIASES:
        return "standard"
    if token in {"contest", "warmup"}:
        return token
    return ""


def kind_display_label(raw: Any) -> str:
    """Return the staff label for a stored or aliased Kind token.

    Args:
        raw: Stored kind, alias, or empty.

    Returns:
        ``Core Math``, ``Custom``, ``Contest``, or ``Warmup``.
    """
    return KIND_DISPLAY[storage_bank_kind(raw)]


def bank_kind_visible(tagged: str, wanted: str | None) -> bool:
    """Return whether one row belongs in the requested Kind filter.

    Args:
        tagged: Kind already read from the payload (``""`` when untagged).
        wanted: Requested filter, including ``custom`` / ``process`` aliases.

    Returns:
        True when the row should appear. Core Math (empty) drops warmup
        and Custom (``standard``). Contest stays in that default mix.
    """
    tag = str(tagged or "").strip().lower()
    want = storage_bank_kind(wanted)
    if want in STORED_KINDS:
        return tag == want
    return tag not in {"warmup", "standard"}


def stem_is_teaching_today(text: str) -> bool:
    """True when a stem opens with the Module 2 teaching prompt.

    Args:
        text: Stem or title text, any whitespace.

    Returns:
        True for ``You're teaching today…`` and close apostrophe variants.
    """
    flat = re.sub(r"\s+", " ", str(text or "")).strip()
    flat = flat.lstrip("\"'“”‘")
    return bool(_TEACHING_TODAY_RE.match(flat))


def apply_stored_bank_kind(payload: dict[str, Any], raw: Any) -> dict[str, Any]:
    """Set or clear ``bank_kind`` without touching a rank payload's ``kind``.

    Args:
        payload: Question payload to update in place.
        raw: Staff Kind choice.

    Returns:
        The same payload.
    """
    token = storage_bank_kind(raw)
    if token:
        payload["bank_kind"] = token
    else:
        payload.pop("bank_kind", None)
    return payload


def retag_module2_teaching_today(school: Any, library_id: int) -> int:
    """Move Module 2 teaching-today stems to Custom (stored ``standard``).

    Scans questions in banks linked to module 2. Does not invent a row when
    the stem is absent. Rank rows keep ``kind=rank`` and gain ``bank_kind``.

    Args:
        school: ``SchoolDB`` instance.
        library_id: ``content_libraries.id``.

    Returns:
        Number of question rows updated.
    """
    with school._lock:
        rows = school.conn.execute(
            """
            SELECT q.id, q.title, q.payload_json
            FROM questions q
            JOIN question_banks b ON b.id = q.bank_id
            JOIN course_module_bank_links l
              ON l.bank_id = q.bank_id AND l.library_id = b.library_id
            WHERE b.library_id = ? AND l.module_number = 2
            """,
            (int(library_id),),
        ).fetchall()
        updated = 0
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            stem = " ".join(
                [
                    str(payload.get("stem_html") or ""),
                    str(payload.get("text") or ""),
                    str(row["title"] or ""),
                ]
            )
            if not stem_is_teaching_today(stem):
                continue
            kind_token = str(payload.get("kind") or "").strip().lower()
            bank_token = str(payload.get("bank_kind") or "").strip().lower()
            if kind_token == "rank":
                if bank_token == "standard":
                    continue
                payload["bank_kind"] = "standard"
            elif kind_token == "standard" and bank_token == "standard":
                continue
            else:
                payload["bank_kind"] = "standard"
                payload["kind"] = "standard"
            school.conn.execute(
                "UPDATE questions SET payload_json = ? WHERE id = ?",
                (json.dumps(payload), int(row["id"])),
            )
            updated += 1
        if updated:
            school.conn.commit()
    return updated

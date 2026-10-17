"""Structured 5-character catch-up portal join codes (MCK-222).

Example ``R3K1X``: course letter + module digit + random letter + class (1–4) +
check character. Not case-sensitive; auto-uppercase on input.
"""

from __future__ import annotations

import os
import re
import secrets
from typing import Any

from codes import SESSION_CODE_ALPHABET

# Prefix may include module/class digits 1–9 and 1–4; live session alphabet omits ``1``.
_PORTAL_PREFIX_CHARSET = "ABCDEFGHJKMNPQRSTUVWXYZ123456789"

# Course letter is the first character of the Ontario code when unambiguous.
_COURSE_LETTER: dict[str, str] = {
    "MCF3M": "F",
    "MCR3U": "R",
}
_LETTER_TO_COURSE = {v: k for k, v in _COURSE_LETTER.items()}

_CLASS_SLOT_RE = re.compile(r"^[1-4]$")
_CODE_RE = re.compile(r"^[A-Z0-9]{5}$")
_RANDOM_ALPHABET = "".join(ch for ch in SESSION_CODE_ALPHABET if ch not in "1234")


def structured_codes_enabled() -> bool:
    """Feature flag for structured portal codes (default on)."""
    raw = (os.getenv("CATCHUP_STRUCTURED_CODES_ENABLED") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def normalize_portal_code(raw: Any) -> str:
    """Uppercase, strip spaces; empty when invalid shape."""
    text = str(raw or "").strip().upper().replace(" ", "")
    if not _CODE_RE.match(text):
        return ""
    return text


# Live join codes are 4–6 characters (8 for older sessions). Structured
# catch-up codes are 5. Lookup accepts every length already stored.
_STORED_JOIN_LENGTHS = frozenset({4, 5, 6, 8})
_STORED_JOIN_RE = re.compile(r"^[A-Z0-9]{4,8}$")


def normalize_stored_join_code(raw: Any) -> str:
    """Uppercase a live join code or a stored catch-up code.

    Args:
        raw: Typed or stored code.

    Returns:
        The code when its length is 4, 5, 6, or 8. Empty otherwise.
    """
    text = str(raw or "").strip().upper().replace(" ", "")
    if len(text) not in _STORED_JOIN_LENGTHS or not _STORED_JOIN_RE.fullmatch(text):
        return ""
    return text


def _checksum_char(prefix: str) -> str:
    """One check character from the first four code characters."""
    total = 0
    for ch in prefix:
        total = (total + _PORTAL_PREFIX_CHARSET.index(ch)) % len(_PORTAL_PREFIX_CHARSET)
    return _PORTAL_PREFIX_CHARSET[total]


def mint_portal_join_code(course_code: str, live_module: str, live_slot: str) -> str:
    """Mint a new structured portal code for one lesson slot.

    Args:
        course_code: Ontario course code (e.g. MCR3U).
        live_module: Live module label (e.g. M3).
        live_slot: Class slot (e.g. C1).

    Returns:
        Five-character portal join code.

    Raises:
        ValueError: Course or slot cannot be encoded.
    """
    course = str(course_code or "").strip().upper()
    letter = _COURSE_LETTER.get(course)
    if not letter:
        raise ValueError(f"No portal course letter for {course}")
    mod = str(live_module or "").strip().upper()
    mod_n = re.sub(r"^M", "", mod)
    if not mod_n.isdigit() or int(mod_n) < 1 or int(mod_n) > 9:
        raise ValueError(f"Bad module for portal code: {live_module}")
    slot = str(live_slot or "").strip().upper()
    slot_n = re.sub(r"^C", "", slot)
    if not _CLASS_SLOT_RE.match(slot_n):
        raise ValueError(f"Bad class slot for portal code: {live_slot}")
    random_letter = secrets.choice(_RANDOM_ALPHABET)
    prefix = f"{letter}{int(mod_n)}{random_letter}{slot_n}"
    return prefix + _checksum_char(prefix)


def decode_portal_join_code(code: str) -> dict[str, str] | None:
    """Decode a structured portal code when the checksum matches.

    Returns:
        ``{course_code, live_module, live_slot}`` or ``None``.
    """
    text = normalize_portal_code(code)
    if not text or not structured_codes_enabled():
        return None
    prefix, check = text[:4], text[4]
    if _checksum_char(prefix) != check:
        return None
    course_letter, mod_ch, _random, class_ch = prefix[0], prefix[1], prefix[2], prefix[3]
    course = _LETTER_TO_COURSE.get(course_letter)
    if not course:
        return None
    if not mod_ch.isdigit() or not _CLASS_SLOT_RE.match(class_ch):
        return None
    return {
        "course_code": course,
        "live_module": f"M{int(mod_ch)}",
        "live_slot": f"C{class_ch}",
    }


def portal_code_preview_label(code: str) -> str | None:
    """Human label before join, e.g. ``MCR3U · M3 · Class 1``."""
    decoded = decode_portal_join_code(code)
    if not decoded:
        return None
    slot_n = decoded["live_slot"].lstrip("C")
    return f"{decoded['course_code']} · {decoded['live_module']} · Class {slot_n}"

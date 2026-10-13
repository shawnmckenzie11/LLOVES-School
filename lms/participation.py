"""MCK-198 (MCK-52 Phase 1): individual participation level + catch-up copy.

Pure functions only. Storage lives in ``school_db``.

Participation % for one student in one module = distinct individual
questions they answered (in class or on the catch-up link) / distinct
individual questions offered in that module's live classes. Group
questions are never counted. Correctness is Phase 3 and is not read here.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
from typing import Any

# School rubric bands, lowest percent for each level.
BANDS: tuple[tuple[int, int], ...] = ((4, 80), (3, 70), (2, 60), (1, 50))
MIN_OFFERED_FOR_LINE = 3
PIN_MAX_ATTEMPTS = 5
PIN_WINDOW_S = 15 * 60
CATCHUP_SESSION_S = 30 * 60
SOURCES = ("live", "catchup")

# Wonder copy, verbatim.
COPY_L4 = "{a}/{o} completed \u00b7 Level 4"
COPY_LEVEL = "{a}/{o} completed \u00b7 Level {n} \u00b7 {k} more"
COPY_START = "{a}/{o} completed \u00b7 {k} more"
COPY_CATCHUP_TAG = "Answered after class"
COPY_CLOSED = "This catch-up is closed. Ask your teacher if you need it reopened."
COPY_WRONG_PIN = "That PIN didn't match. Try again or ask your teacher to reset it."

_PIN = re.compile(r"^[0-9]{4}$")

# Copy keys until Wonder's final copy lands. Templates render the key itself.
KEY_PIN_LABEL = "pin.label"
KEY_PIN_LOOKS_LIKE_JOIN_CODE = "pin.looks_like_join_code"


def looks_like_join_code(raw: Any, active_codes: Any = ()) -> bool:
    """True when a PIN entry is really a join code.

    Join codes are uppercase from ``codes.SESSION_CODE_ALPHABET`` (no 0, 1,
    I, L, O), 4-6 characters for a live session or 8 for a class code. A
    typed code with a letter in it, or any exact current code, is refused
    as a PIN without counting as a failed attempt.
    """
    from codes import LIVE_ACCESS_CODE_LENGTHS, SESSION_CODE_ALPHABET

    text = str(raw or "").strip().upper()
    if not text:
        return False
    if text in {str(c or "").strip().upper() for c in active_codes if c}:
        return True
    return (
        len(text) in LIVE_ACCESS_CODE_LENGTHS
        and all(ch in SESSION_CODE_ALPHABET for ch in text)
        and any(ch.isalpha() for ch in text)
    )


def percent(answered: int, offered: int) -> float | None:
    """Participation percent, or None when nothing was offered."""
    if offered <= 0:
        return None
    return 100.0 * min(max(answered, 0), offered) / offered


def level_for(pct: float | None) -> int | None:
    """Rubric level 1-4, 0 for below Level 1, None when nothing was offered."""
    if pct is None:
        return None
    for level, floor in BANDS:
        if pct >= floor:
            return level
    return 0


def more_to_next(answered: int, offered: int) -> int | None:
    """Fewest more answers to reach the next band, from questions offered so far.

    Returns None at Level 4 or when nothing was offered.
    """
    level = level_for(percent(answered, offered))
    if level is None or level >= 4:
        return None
    floor = dict(BANDS)[level + 1]
    need = math.ceil(floor * offered / 100.0 - 1e-9) - answered
    return max(1, min(need, offered - answered))


def level_line(answered: int, offered: int) -> str | None:
    """The student's own level line, hidden until 3 questions were offered."""
    if offered < MIN_OFFERED_FOR_LINE:
        return None
    level = level_for(percent(answered, offered))
    a = min(max(answered, 0), offered)
    if level == 4:
        return COPY_L4.format(a=a, o=offered)
    k = more_to_next(answered, offered)
    if level == 0:
        return COPY_START.format(a=a, o=offered, k=k)
    return COPY_LEVEL.format(a=a, o=offered, n=level, k=k)


def summary(answered: int, offered: int) -> dict[str, Any]:
    """Teacher cell: counts, percent, level."""
    pct = percent(answered, offered)
    return {
        "answered": answered,
        "offered": offered,
        "percent": None if pct is None else round(pct),
        "level": level_for(pct),
    }


def valid_pin(pin: Any) -> bool:
    return bool(_PIN.match(str(pin or "")))


def hash_pin(pin: str, salt: str | None = None) -> str:
    """``salt$hex`` PBKDF2-SHA256 hash of a 4-digit PIN."""
    salt = salt or secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 60000).hex()
    return f"{salt}${digest}"


def check_pin(pin: str, stored: str) -> bool:
    if not stored or "$" not in stored:
        return False
    salt = stored.split("$", 1)[0]
    return hmac.compare_digest(hash_pin(str(pin), salt), stored)


def new_token() -> str:
    """Unguessable catch-up link token."""
    return secrets.token_urlsafe(24)

"""8-character live-access codes for student join (unique per class)."""

from __future__ import annotations

import secrets

# Unambiguous alphabet: no 0/O or 1/I.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_live_access_code(length: int = 8) -> str:
    """Return a random course live-access code.

    Args:
        length: Character count (default 8).

    Returns:
        Uppercase alphanumeric code from ``ALPHABET``.
    """
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def normalize_live_access_code(raw: str) -> str:
    """Normalize a student-typed course key.

    Args:
        raw: Typed code.

    Returns:
        Uppercased stripped code.

    Raises:
        ValueError: If the code is not exactly 8 alphabet characters.
    """
    code = (raw or "").strip().upper().replace(" ", "")
    if len(code) != 8 or any(ch not in ALPHABET for ch in code):
        raise ValueError("Enter the 8-character course code")
    return code

"""MCK-204: course-wide default catch-up PINs (hashed + Fernet for teacher view).

Raw PINs are never logged. Teachers see decrypted defaults only on the
Roster tab; ciphertext is cleared when the PIN hash is replaced.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Iterable

from participation import FORBIDDEN_PINS, hash_pin, valid_pin

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # pragma: no cover - installed via lms/requirements.txt
    Fernet = None  # type: ignore[misc, assignment]
    InvalidToken = Exception  # type: ignore[misc, assignment]

SLIP_TEMPLATE = "Your catch-up PIN: {pin} (not the join code)"


def fernet_from_secret(secret: str) -> Fernet:
    """Build Fernet for default-PIN ciphertext (HKDF from app secret or env key)."""
    if Fernet is None:
        raise RuntimeError("cryptography package is required for default PIN encryption")
    import os

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    raw = os.getenv("DEFAULT_PIN_KEY") or str(secret or "")
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"default-pin",
    ).derive(raw.encode())
    key = base64.urlsafe_b64encode(derived)
    return Fernet(key)


def encrypt_pin(pin: str, secret: str) -> str:
    """Encrypt a 4-digit PIN for teacher-only storage."""
    return fernet_from_secret(secret).encrypt(str(pin).encode()).decode()


def decrypt_pin(ciphertext: str, secret: str) -> str:
    """Decrypt a stored default PIN. Raises ``ValueError`` when invalid."""
    try:
        raw = fernet_from_secret(secret).decrypt(str(ciphertext or "").encode())
    except InvalidToken:
        raise ValueError("invalid ciphertext") from None
    pin = raw.decode()
    if not valid_pin(pin):
        raise ValueError("invalid pin payload")
    return pin


def pin_conflicts_class_code(pin: str, class_code: str) -> bool:
    """True when a default PIN must not be issued (equals the class join code).

    Product copy says "join code" (Wonder MCK-204).
    """
    code = str(class_code or "").strip().upper()
    if not code:
        return False
    return str(pin) == code


def generate_unique_default_pin(
    *,
    class_code: str,
    used: Iterable[str],
) -> str:
    """Return a random 4-digit PIN unique in ``used`` and not the join code."""
    taken = {str(p) for p in used if valid_pin(p)}
    for _ in range(500):
        pin = f"{secrets.randbelow(10000):04d}"
        if pin in taken:
            continue
        if pin_conflicts_class_code(pin, class_code):
            continue
        if pin in FORBIDDEN_PINS:
            continue
        return pin
    raise RuntimeError("could not allocate a unique default PIN for this class")


def assign_default_pin_fields(pin: str, secret: str) -> tuple[str, str]:
    """Return ``(pin_hash, default_pin_ciphertext)`` for one new default PIN."""
    if not valid_pin(pin):
        raise ValueError("PIN must be 4 digits.")
    return hash_pin(pin), encrypt_pin(pin, secret)

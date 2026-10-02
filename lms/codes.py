"""Live-access codes for student join and live sessions.

Two kinds of code share this module:

* **Live-session join codes** (``live_class_sessions.session_code``), the
  code students type on the landing page. MCK-117: 4 characters from
  ``SESSION_CODE_ALPHABET``, growing to 5 then 6 when many sessions are
  active or random picks keep colliding (``pick_session_code``).
* **Durable offering and class codes** (``live_access_code``). These stay 8
  characters from ``ALPHABET``.

``normalize_live_access_code`` accepts every length either kind uses, so
8-character session codes minted before MCK-117 keep working until their
sessions end.
"""

from __future__ import annotations

import secrets
from typing import Callable

# Durable 8-character codes: no 0/O or 1/I. Kept as is so stored codes stay valid.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
# Live-session join codes also drop L (reads as 1/I on a projector): 31 symbols.
SESSION_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
# Session code lengths, shortest first. 31**4 is about 923k codes.
SESSION_CODE_LENGTHS: tuple[int, ...] = (4, 5, 6)
# Start one length longer once this many sessions are active at that length.
# 300 active out of 923k 4-character codes is a 0.03% collision chance per pick.
SESSION_CODE_GROW_AT_ACTIVE: dict[int, int] = {4: 300, 5: 3000}
# Random picks tried at one length before moving to the next length.
SESSION_CODE_TRIES_PER_LENGTH = 8
# Every code length ``normalize_live_access_code`` accepts.
LIVE_ACCESS_CODE_LENGTHS: frozenset[int] = frozenset((*SESSION_CODE_LENGTHS, 8))


def generate_live_access_code(length: int = 8, alphabet: str = ALPHABET) -> str:
    """Return a random live-access code.

    Args:
        length: Character count (default 8, the durable code length).
        alphabet: Symbols to draw from (default ``ALPHABET``).

    Returns:
        Uppercase alphanumeric code.
    """
    return "".join(secrets.choice(alphabet) for _ in range(length))


def session_code_start_length(active_sessions: int) -> int:
    """Shortest session code length to try for this many active sessions.

    Args:
        active_sessions: Live class sessions currently ``active``.

    Returns:
        A length from ``SESSION_CODE_LENGTHS``.
    """
    for length in SESSION_CODE_LENGTHS[:-1]:
        if int(active_sessions) < SESSION_CODE_GROW_AT_ACTIVE[length]:
            return length
    return SESSION_CODE_LENGTHS[-1]


def pick_session_code(
    taken: Callable[[str], bool],
    active_sessions: int = 0,
    *,
    generate: Callable[[int], str] | None = None,
) -> str:
    """Pick a live-session join code that ``taken`` reports free.

    Starts at ``session_code_start_length(active_sessions)``. After
    ``SESSION_CODE_TRIES_PER_LENGTH`` collisions it moves to the next
    length. The longest length gets the same budget again.

    Args:
        taken: True when a candidate is already in use.
        active_sessions: Live class sessions currently ``active``.
        generate: Code maker for tests; defaults to a random draw from
            ``SESSION_CODE_ALPHABET``.

    Returns:
        A free code.

    Raises:
        RuntimeError: If every try at every length collided.
    """
    make = generate or (
        lambda length: generate_live_access_code(length, SESSION_CODE_ALPHABET)
    )
    start = session_code_start_length(active_sessions)
    lengths = [n for n in SESSION_CODE_LENGTHS if n >= start]
    lengths.append(SESSION_CODE_LENGTHS[-1])
    for length in lengths:
        for _ in range(SESSION_CODE_TRIES_PER_LENGTH):
            code = make(length)
            if not taken(code):
                return code
    raise RuntimeError("Could not pick a free live-session join code")


def normalize_live_access_code(raw: str) -> str:
    """Normalize a student-typed course or session code.

    Args:
        raw: Typed code.

    Returns:
        Uppercased code with spaces removed.

    Raises:
        ValueError: If the length is not in ``LIVE_ACCESS_CODE_LENGTHS`` or a
            character is outside ``ALPHABET``.
    """
    code = (raw or "").strip().upper().replace(" ", "")
    if len(code) not in LIVE_ACCESS_CODE_LENGTHS or any(
        ch not in ALPHABET for ch in code
    ):
        raise ValueError("Enter the code from your teacher's screen")
    return code

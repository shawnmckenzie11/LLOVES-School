"""Live-access codes for student join and live sessions.

Two kinds of code share this module:

* **Live-session join codes** (``live_class_sessions.session_code``), the
  code students type on the landing page. MCK-117: 4 characters from
  ``SESSION_CODE_ALPHABET``, growing to 5 then 6 when many sessions are
  active or random picks keep colliding (``pick_session_code``).
* **Durable offering and class codes** (``live_access_code``). These stay 8
  characters from ``ALPHABET``.

Session codes are re-picked when they spell a word on
``OFFENSIVE_CODE_PARTS`` (including digit look-alikes such as 5EXY).

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
# Last resort after every 4/5/6 try collided: an 8-character session code
# (join already accepts 8). Only then does Start give up with a clean error.
SESSION_CODE_FALLBACK_LENGTH = 8

# MCK-117 M1: a 4-character code is a whole word on the projector. A code is
# re-picked when it, or its letter reading (4->A, 3->E, 5->S, 7->T, 8->B,
# 6/9->G, 2->Z, V->U), contains any of these. Kept short and generic; add to
# it freely, since each entry removes only a sliver of the code space.
OFFENSIVE_CODE_PARTS: tuple[str, ...] = (
    "KKK", "NAZI", "HEIL", "SS88", "HH88", "88", "666",
    "FUCK", "FUK", "FCK", "FUQ", "PHUK", "SHIT", "SHT", "CUNT", "TWAT", "DICK",
    "COCK", "PUSSY", "PUSY", "TITS", "TIT", "BOOB", "ANUS", "ANAL", "ARSE", "ASS",
    "CUM", "JIZZ", "PORN", "SEX", "SEXY", "RAPE", "SLUT", "WHORE", "HOE", "HOR",
    "FAG", "DYKE", "TRANNY", "NIG", "NGR", "NGA", "NEGRO", "COON", "SPIC", "SPIK",
    "CHINK", "GOOK", "KIKE", "WOP", "RETARD", "TARD", "KYS", "KILL", "DIE", "DED",
    "WTF", "STFU", "GTFO", "DAMN", "PISS", "BUTT", "POOP", "PEE", "PENIS",
    "VAG", "BJ", "HJ", "69", "420", "GUN", "BOMB", "ISIS", "JEW",
    "NGGR", "KUNT", "WANK", "BTCH", "PRCK", "GAY", "G4Y", "SUCK", "CRAP",
    "TURD", "NUDE", "SEMEN", "METH", "WEED", "H8",
    "NGGA", "HUMP", "JERK", "SHAG", "DRUG",
)
_CODE_LETTER_READING = str.maketrans(
    {"4": "A", "3": "E", "5": "S", "7": "T", "8": "B", "6": "G", "9": "G", "2": "Z", "V": "U"}
)


def is_offensive_code(code: str) -> bool:
    """True when a code spells, or reads like, a word on the blocklist.

    Args:
        code: Candidate code (any case).

    Returns:
        True to re-pick.
    """
    text = str(code or "").upper()
    readings = {text, text.translate(_CODE_LETTER_READING)}
    return any(part in reading for reading in readings for part in OFFENSIVE_CODE_PARTS)


class SessionCodeUnavailable(ValueError):
    """Start could not find a free join code. A ``ValueError`` so the Start
    routes show it as a normal 400 message instead of a 500."""


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
    """Pick a live-session join code that is free and not offensive.

    Starts at ``session_code_start_length(active_sessions)``. After
    ``SESSION_CODE_TRIES_PER_LENGTH`` rejected picks it moves to the next
    length; the longest gets the budget twice, then
    ``SESSION_CODE_FALLBACK_LENGTH`` gets one more. A pick on the
    ``OFFENSIVE_CODE_PARTS`` blocklist is re-picked and uses up one try.

    Args:
        taken: True when a candidate is already in use.
        active_sessions: Live class sessions currently ``active``.
        generate: Code maker for tests; defaults to a random draw from
            ``SESSION_CODE_ALPHABET``.

    Returns:
        A free code.

    Raises:
        SessionCodeUnavailable: If every try at every length was rejected.
    """
    make = generate or (
        lambda length: generate_live_access_code(length, SESSION_CODE_ALPHABET)
    )
    start = session_code_start_length(active_sessions)
    lengths = [n for n in SESSION_CODE_LENGTHS if n >= start]
    lengths.append(SESSION_CODE_LENGTHS[-1])
    lengths.append(SESSION_CODE_FALLBACK_LENGTH)
    for length in lengths:
        for _ in range(SESSION_CODE_TRIES_PER_LENGTH):
            code = make(length)
            if is_offensive_code(code):
                continue
            if not taken(code):
                return code
    raise SessionCodeUnavailable(
        "Couldn’t make a free join code just now. Press Start again."
    )


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

"""Compare student-typed names with roster names (MCK-110).

SQLite ``lower()`` folds ASCII only, so a roster "Élodie" never matched a
typed "élodie" or "Élodie". These helpers fold in Python instead:

* Unicode NFKC, then ``str.casefold`` (handles É, Ó, ß, Turkish dotted I…).
* Apostrophe look-alikes (’ ‘ ʼ ` ´ ′) become ``'``; curly quotes become ``"``.
* Dash look-alikes (‐ ‑ ‒ – — −) and hyphens compare equal to a space, so
  "Jean-Luc", "Jean Luc" and "Jean–Luc" are the same name.
* Runs of whitespace collapse to one space.

``loose_name_key`` also drops accents and apostrophes/quotes. It is only a
fallback for a student who types "Elodie" or "OBrien" on a keyboard
without them.
"""

from __future__ import annotations

import re
import unicodedata

_APOSTROPHES = dict.fromkeys(map(ord, "\u2019\u2018\u02bc\u02bb`\u00b4\u2032\uff07"), "'")
_QUOTES = dict.fromkeys(map(ord, "\u201c\u201d\u201e\u00ab\u00bb\u2033\uff02"), '"')
_DASHES = dict.fromkeys(map(ord, "-\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe63\uff0d"), " ")
_SPACE = re.compile(r"\s+")


def name_key(raw: object) -> str:
    """Return the comparison key for a typed or roster name.

    Args:
        raw: Name text, or anything ``str()`` can render.

    Returns:
        Folded key; ``""`` for blank input.
    """
    text = unicodedata.normalize("NFKC", str(raw or ""))
    text = text.translate(_APOSTROPHES).translate(_QUOTES).translate(_DASHES)
    text = text.casefold()
    # casefold can emit decomposed forms; recompose so keys compare equal.
    text = unicodedata.normalize("NFC", text)
    return _SPACE.sub(" ", text).strip()


def loose_name_key(raw: object) -> str:
    """``name_key`` without accents, apostrophes or quotes (fallback only)."""
    text = unicodedata.normalize("NFKD", name_key(raw))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("'", "").replace('"', "")
    return _SPACE.sub(" ", text).strip()


def first_token_key(raw: object) -> str:
    """Key of the first space-separated token (hyphens count as spaces)."""
    key = name_key(raw)
    return key.split(" ", 1)[0] if key else ""


def same_name(a: object, b: object) -> bool:
    """True when two names fold to the same key (both non-blank)."""
    left = name_key(a)
    return bool(left) and left == name_key(b)

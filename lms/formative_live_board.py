"""Teacher gallery payloads for the one live whiteboard.

Ink stays on the surviving boards (``team:<sort>`` and ``solo:<id>``).
This module only shapes the JSON a teacher is allowed to see. It does
not write the gradebook.
"""

from __future__ import annotations

import re
from typing import Any

# Keys that must never leave a board payload, even if a client posted them.
_SECRET_KEYS = frozenset(
    {
        "pin",
        "pins",
        "join_code",
        "session_code",
        "visit_token",
        "live_access_code",
        "access_code",
        "course_pin",
        "live_code",
        "first_name",
        "last_name",
        "last_display",
    }
)
_QUESTION_KEYS = ("prompt", "stem", "text", "question", "title", "stem_html")
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


_GALLERY_KEY = re.compile(r"^(?:team:[0-9]+|solo:[A-Za-z0-9_-]{1,80})$")


def gallery_board_key(raw: str) -> str:
    """Return a surviving board key, or raise ``ValueError``.

    Args:
        raw: Path token such as ``team:0`` or ``solo:12``.
    """
    text = str(raw or "").strip()
    if not _GALLERY_KEY.fullmatch(text):
        raise ValueError("unknown board")
    return text


def team_fields(sort_order: int | None) -> dict[str, Any]:
    """Team chip fields for a ``team:<sort_order>`` pin.

    Sort order ``0`` is Team 1. A missing pin has an empty chip.

    Args:
        sort_order: ``live_board_assignments.team_sort_order``, or None.
    """
    if sort_order is None:
        return {"team_board": "", "team_label": "", "team_sort_order": None}
    order = int(sort_order)
    return {
        "team_board": f"team:{order}",
        "team_label": f"Team {order + 1}",
        "team_sort_order": order,
    }


def question_text(prompt: dict[str, Any] | None) -> str:
    """Student-visible question line from a live prompt.

    Answer keys and choice lists are not read.

    Args:
        prompt: ``get_active_live_prompt`` row, or None.
    """
    if not isinstance(prompt, dict):
        return ""
    payload = prompt.get("payload") if isinstance(prompt.get("payload"), dict) else {}
    for key in _QUESTION_KEYS:
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            return _plain(raw)[:240]
    return ""


def context_line(course: str, module: str, slot: str, question: str) -> str:
    """Header line such as ``MCR3U · M3 · C1 · Sketch …``.

    Args:
        course: Ontario course code.
        module: Live module label.
        slot: Live class slot.
        question: Short question text.
    """
    parts = [
        str(part).strip()
        for part in (course, module, slot, question)
        if str(part or "").strip()
    ]
    return " · ".join(parts)[:180]


def summary_line(tiles: list[dict[str, Any]]) -> str:
    """Right-hand count line for the teacher grid.

    Args:
        tiles: Public tile dicts with ``drawing``, ``submitted``, and ``away``.
    """
    drawing = sum(1 for tile in tiles if tile.get("drawing"))
    submitted = sum(1 for tile in tiles if tile.get("submitted"))
    away = sum(1 for tile in tiles if tile.get("away"))
    return f"{drawing} drawing · {submitted} submitted ✓ · {away} away"


def team_filters(tiles: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Filter chips built from board pins.

    Args:
        tiles: Public tile dicts.
    """
    orders = sorted(
        {
            int(tile["team_sort_order"])
            for tile in tiles
            if tile.get("team_sort_order") is not None
        }
    )
    chips = [{"id": "all", "label": f"All {len(tiles)}"}]
    for order in orders:
        chips.append({"id": f"team:{order}", "label": f"Team {order + 1}"})
    chips.append({"id": "teacher_wrote", "label": "Teacher wrote"})
    return chips


def legend_entries(
    school: Any, session_id: int, card: dict[str, Any]
) -> list[dict[str, str]]:
    """Colour dots and names for one gallery card.

    Same rows the teacher panel and the shown board use
    (``legend_member_rows``). Colours are unique on this board. A
    student who drew and then left stays. Blank names are left out so
    a teacher ink row does not become an empty chip.

    Args:
        school: Open ``SchoolDB``.
        session_id: ``live_class_sessions.id``.
        card: Gallery card with ``board_key``, ``team_sort_order``, and
            ``student_ids``.

    Returns:
        ``{id, name, color}`` rows.
    """
    from whiteboard_formative import legend_member_rows

    key = str(card.get("board_key") or "")
    order = card.get("team_sort_order")
    scope = None if order is None or key.startswith("solo:") else int(order)
    members = legend_member_rows(
        school, int(session_id), scope, board_key=key
    )
    if key.startswith("solo:"):
        wanted = {
            str(int(sid))
            for sid in (card.get("student_ids") or [])
            if sid not in (None, "")
        }
        if wanted:
            members = [member for member in members if str(member.get("id")) in wanted]
    out: list[dict[str, str]] = []
    for member in members:
        name = str(member.get("name") or "").strip()
        if not name:
            continue
        out.append(
            {
                "id": str(member.get("id") or ""),
                "name": name,
                "color": str(member.get("color") or ""),
            }
        )
    return out


def parse_cursors(raw: Any) -> dict[str, int]:
    """Read a since-seq map. Unknown values are dropped.

    Args:
        raw: JSON object or a JSON string. Missing means an empty map.
    """
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            import json

            raw = json.loads(text)
        except (TypeError, ValueError):
            return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in raw.items():
        try:
            out[str(key)] = max(0, int(value))
        except (TypeError, ValueError):
            continue
    return out


def scrub_op(op: dict[str, Any]) -> dict[str, Any]:
    """Drop secret and name fields from one client op before it is stored.

    Args:
        op: One whiteboard op from the browser.
    """
    cleaned: dict[str, Any] = {}
    for key, value in op.items():
        token = str(key).lower()
        if token in _SECRET_KEYS or token in {"name", "codename"}:
            continue
        if isinstance(value, dict):
            cleaned[str(key)] = scrub_op(value)
        else:
            cleaned[str(key)] = value
    return cleaned


def public_board_payload(value: Any) -> Any:
    """Copy a payload with secret keys removed at every level.

    Args:
        value: JSON-ready object.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in _SECRET_KEYS:
                continue
            out[str(key)] = public_board_payload(item)
        return out
    if isinstance(value, list):
        return [public_board_payload(item) for item in value]
    return value


def _plain(raw: str) -> str:
    """Strip tags and collapse whitespace.

    Args:
        raw: Prompt text that may contain simple HTML.
    """
    return _SPACE_RE.sub(" ", _TAG_RE.sub(" ", raw)).strip()


def _delta_view(delta: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize one since-seq result for a public payload.

    Args:
        delta: ``since_many`` entry, or None when the board has no rows.
    """
    src = delta if isinstance(delta, dict) else {}
    view = src.get("canvas_view") if isinstance(src.get("canvas_view"), dict) else None
    return {
        "board_seq": int(src.get("board_seq") or 0),
        "since": int(src.get("since") or 0),
        "snapshot": bool(src.get("snapshot")),
        "ops": [] if src.get("snapshot") else list(src.get("ops") or []),
        "canvas_view": view,
        "last_stroke_at": str(src.get("last_at") or ""),
    }


def build_gallery_tile(
    *,
    board_key: str,
    name: str,
    team_sort_order: int | None,
    away: bool,
    delta: dict[str, Any] | None,
    student_ids: list[int] | None = None,
) -> dict[str, Any]:
    """One teacher-gallery card.

    ``name`` is the roster name or the team name. A blank name stays
    blank. This card never invents a numbered stand-in.

    Args:
        board_key: ``team:<sort>`` or ``solo:<id>``.
        name: Label already read from the roster or the team row.
        team_sort_order: Team suffix, including ``0``. None on a solo card.
        away: True when every student on the card has left.
        delta: Since-seq delta for this board, feedback included.
        student_ids: Roster ids on the card. Omitted from the public copy
            when empty.
    """
    ink = _delta_view(delta)
    tile: dict[str, Any] = {
        "board_key": gallery_board_key(board_key),
        "name": str(name or "").strip(),
        "away": bool(away),
        "drawing": not away,
        "last_stroke_at": ink["last_stroke_at"],
        "board_seq": ink["board_seq"],
        "snapshot": bool(ink["snapshot"]),
        "ops": ink["ops"],
        "canvas_view": ink["canvas_view"],
    }
    if student_ids:
        tile["student_ids"] = [int(sid) for sid in student_ids]
        if len(student_ids) == 1 and str(board_key).startswith("solo:"):
            tile["student_id"] = int(student_ids[0])
    tile.update(team_fields(team_sort_order))
    if team_sort_order is None:
        tile["team_label"] = ""
        tile["team_board"] = ""
    elif str(name or "").strip():
        tile["team_label"] = str(name).strip()
    return public_board_payload(tile)

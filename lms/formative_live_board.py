"""Per-student formative whiteboard keys and public payloads.

Ink stays in ``board_ops`` (``student:<id>`` and ``fb:<id>``). This module
only shapes keys and the JSON a teacher or student is allowed to see.
It does not write the gradebook.
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


def student_ink_key(student_id: int) -> str:
    """Board key for one student's own ink.

    Args:
        student_id: Roster id from the live-session attendee row.
    """
    return f"student:{int(student_id)}"


def feedback_ink_key(student_id: int) -> str:
    """Board key for teacher pen feedback on one student's board.

    Args:
        student_id: Roster id the feedback belongs to.
    """
    return f"fb:{int(student_id)}"


def blank_codename(codename: str, *, show: bool) -> str:
    """Return the attendee codename, or an empty string when names are off.

    The caller must already have read ``live_session_attendees.codename``.
    A hidden name is blank on the server. The client may say "Student N".

    Args:
        codename: Codename stored on the attendee row.
        show: True when the relevant names toggle is on.
    """
    if not show:
        return ""
    return str(codename or "").strip()


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
    return chips


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


def build_tile(
    *,
    student_id: int,
    codename: str,
    show_names: bool,
    team_sort_order: int | None,
    submitted_at: str | None,
    left_at: str | None,
    anon_index: int,
    student_delta: dict[str, Any] | None,
    feedback_delta: dict[str, Any] | None,
) -> dict[str, Any]:
    """One teacher-grid tile. Names are blank when the toggle is off.

    Args:
        student_id: Roster id.
        codename: ``live_session_attendees.codename`` only.
        show_names: Stored ``whiteboard_always_show_names`` flag.
        team_sort_order: Pin suffix, including ``0``.
        submitted_at: Done timestamp, or None.
        left_at: Attendee leave time, or None.
        anon_index: 1-based join order for the "Student N" label.
        student_delta: Since-seq delta for ``student:<id>``.
        feedback_delta: Since-seq delta for ``fb:<id>``.
    """
    submitted = bool(str(submitted_at or "").strip())
    away = bool(str(left_at or "").strip())
    ink = _delta_view(student_delta)
    feedback = _delta_view(feedback_delta)
    tile = {
        "student_id": int(student_id),
        "codename": blank_codename(codename, show=show_names),
        "anon_index": int(anon_index),
        "board_key": student_ink_key(student_id),
        "feedback_key": feedback_ink_key(student_id),
        "submitted": submitted,
        "away": away,
        "drawing": not submitted and not away,
        "last_stroke_at": ink["last_stroke_at"] or feedback["last_stroke_at"],
        "board_seq": ink["board_seq"],
        "feedback_seq": feedback["board_seq"],
        "snapshot": bool(ink["snapshot"] or feedback["snapshot"]),
        "ops": ink["ops"],
        "feedback_ops": feedback["ops"],
        "canvas_view": ink["canvas_view"],
        "feedback_view": feedback["canvas_view"],
    }
    tile.update(team_fields(team_sort_order))
    return public_board_payload(tile)


def build_student_view(
    *,
    student_id: int,
    team_sort_order: int | None,
    submitted_at: str | None,
    question: str,
    context: str,
    student_delta: dict[str, Any] | None,
    feedback_delta: dict[str, Any] | None,
    released_at: str | None,
) -> dict[str, Any]:
    """The signed-in student's board. Feedback is omitted until release.

    Args:
        student_id: Roster id taken from the session, never from the query.
        team_sort_order: That student's pin.
        submitted_at: Done timestamp, or None.
        question: Active prompt text.
        context: Course / module / slot / question line.
        student_delta: Since-seq delta for this student's ink.
        feedback_delta: Teacher ink. Ignored until ``released_at`` is set.
        released_at: When the teacher pressed Send feedback.
    """
    ink = _delta_view(student_delta)
    body: dict[str, Any] = {
        "ok": True,
        "visible": True,
        "student_id": int(student_id),
        "board_key": student_ink_key(student_id),
        "submitted": bool(str(submitted_at or "").strip()),
        "question": question,
        "context": context,
        "board_seq": ink["board_seq"],
        "snapshot": bool(ink["snapshot"]),
        "ops": ink["ops"],
        "canvas_view": ink["canvas_view"],
        "saved": True,
    }
    body.update(team_fields(team_sort_order))
    if str(released_at or "").strip():
        feedback = _delta_view(feedback_delta)
        body["feedback"] = {
            "released_at": str(released_at),
            "board_seq": feedback["board_seq"],
            "snapshot": bool(feedback["snapshot"]),
            "ops": feedback["ops"],
            "canvas_view": feedback["canvas_view"],
        }
    return public_board_payload(body)


def build_projection(
    *,
    student_id: int,
    codename: str,
    show_names: bool,
    team_sort_order: int | None,
    question: str,
    context: str,
    student_delta: dict[str, Any] | None,
    feedback_delta: dict[str, Any] | None,
    include_feedback: bool = True,
) -> dict[str, Any]:
    """Show-on-board payload. The projector names toggle blanks the codename.

    The feedback layer is omitted until the teacher has released it.
    The side panel passes ``include_feedback`` true so unreleased marks
    stay on the teacher's own screen.

    Args:
        student_id: Board being projected.
        codename: Attendee codename.
        show_names: Stored ``whiteboard_projector_names`` flag.
        team_sort_order: Pin suffix.
        question: Active prompt text.
        context: Header line.
        student_delta: Full or since-seq student ink.
        feedback_delta: Teacher ink. Used only when ``include_feedback``.
        include_feedback: True once ``released_at`` is set for Show to class.
    """
    ink = _delta_view(student_delta)
    body: dict[str, Any] = {
        "ok": True,
        "projected": True,
        "student_id": int(student_id),
        "codename": blank_codename(codename, show=show_names),
        "board_key": student_ink_key(student_id),
        "question": question,
        "context": context,
        "board_seq": ink["board_seq"],
        "ops": ink["ops"],
        "canvas_view": ink["canvas_view"],
    }
    if include_feedback:
        feedback = _delta_view(feedback_delta)
        body["feedback_key"] = feedback_ink_key(student_id)
        body["feedback_seq"] = feedback["board_seq"]
        body["feedback_ops"] = feedback["ops"]
        body["feedback_view"] = feedback["canvas_view"]
    body.update(team_fields(team_sort_order))
    return public_board_payload(body)

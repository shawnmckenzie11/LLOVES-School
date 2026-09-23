"""Ephemeral live-class canvas sync (alignment + thin presence cursors).

Pixels are never gradebooked. The blob dies with the live SID. Team-shared
mode publishes named cursors in distinct colours; Frozen-to-teacher
replays the teacher stroke list. Unique-per-student stays local.
"""

from __future__ import annotations

from typing import Any

CANVAS_ALIGNS: tuple[str, ...] = ("teacher", "student", "team")
DEFAULT_CANVAS_ALIGN = "student"

CURSOR_COLORS: tuple[str, ...] = (
    "#c8102e",
    "#0b3d91",
    "#ffb81c",
    "#00843d",
    "#7b2d8e",
    "#e87722",
    "#00a3e0",
    "#5c3317",
    "#d5006d",
    "#006d77",
)

MAX_STROKES = 80
MAX_POINTS = 80
MAX_CURSORS = 40
MAX_TEXTS = 40
MAX_TEXT_LEN = 240


def default_canvas_sync() -> dict[str, Any]:
    """Empty ephemeral canvas blob.

    Strokes and cursors stay on the live session. ``texts`` is the
    whiteboard Text tool, also kept only for this session.
    """
    return {"strokes": {"teacher": [], "teams": {}}, "cursors": {}, "texts": []}


def normalize_canvas_align(raw: Any) -> str:
    """Return ``teacher``, ``student``, or ``team``.

    Args:
        raw: Posted or stored alignment token.
    """
    token = str(raw or "").strip().lower()
    if token in CANVAS_ALIGNS:
        return token
    return DEFAULT_CANVAS_ALIGN


def cursor_color_for(seed: Any) -> str:
    """Pick a stable distinct colour from a student/teacher id.

    Args:
        seed: Roster id, ``teacher``, or any hashable token.
    """
    text = str(seed or "").strip() or "0"
    total = 0
    for ch in text:
        total = (total * 33 + ord(ch)) % 10_000
    return CURSOR_COLORS[total % len(CURSOR_COLORS)]


def _clean_point(raw: Any) -> list[float] | None:
    """Return a normalized ``[x, y]`` pair in 0–1, or None."""
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None
    try:
        x = float(raw[0])
        y = float(raw[1])
    except (TypeError, ValueError):
        return None
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        return None
    return [round(x, 4), round(y, 4)]


def _clean_stroke(raw: Any) -> dict[str, Any] | None:
    """Keep one thin polyline stroke."""
    if not isinstance(raw, dict):
        return None
    points: list[list[float]] = []
    for item in raw.get("points") or []:
        point = _clean_point(item)
        if point is not None:
            points.append(point)
        if len(points) >= MAX_POINTS:
            break
    if not points:
        return None
    owner = str(raw.get("owner") or "").strip() or "teacher"
    color = str(raw.get("color") or "").strip() or cursor_color_for(owner)
    stroke_id = str(raw.get("id") or "").strip()
    team_id = raw.get("team_id")
    try:
        team_n = int(team_id) if team_id not in (None, "") else None
    except (TypeError, ValueError):
        team_n = None
    return {
        "id": stroke_id,
        "owner": owner,
        "team_id": team_n,
        "color": color,
        "points": points,
    }


def public_canvas_sync(raw: Any) -> dict[str, Any]:
    """Normalize a stored canvas-sync blob.

    Args:
        raw: Dict from ``canvas_sync_json``, or empty.
    """
    base = default_canvas_sync()
    if not isinstance(raw, dict):
        return base
    strokes = raw.get("strokes") if isinstance(raw.get("strokes"), dict) else {}
    teacher_strokes: list[dict[str, Any]] = []
    for item in strokes.get("teacher") or []:
        cleaned = _clean_stroke(item)
        if cleaned is not None:
            teacher_strokes.append(cleaned)
        if len(teacher_strokes) >= MAX_STROKES:
            break
    teams: dict[str, list[dict[str, Any]]] = {}
    raw_teams = strokes.get("teams") if isinstance(strokes.get("teams"), dict) else {}
    for key, rows in raw_teams.items():
        bucket: list[dict[str, Any]] = []
        if not isinstance(rows, list):
            continue
        for item in rows:
            cleaned = _clean_stroke(item)
            if cleaned is not None:
                bucket.append(cleaned)
            if len(bucket) >= MAX_STROKES:
                break
        if bucket:
            teams[str(key)] = bucket
    cursors: dict[str, dict[str, Any]] = {}
    raw_cursors = raw.get("cursors") if isinstance(raw.get("cursors"), dict) else {}
    for key, row in list(raw_cursors.items())[:MAX_CURSORS]:
        if not isinstance(row, dict):
            continue
        point = _clean_point([row.get("x"), row.get("y")])
        if point is None:
            continue
        owner = str(row.get("owner") or key).strip() or str(key)
        team_id = row.get("team_id")
        try:
            team_n = int(team_id) if team_id not in (None, "") else None
        except (TypeError, ValueError):
            team_n = None
        cursors[str(key)] = {
            "owner": owner,
            "name": str(row.get("name") or owner).strip() or owner,
            "color": str(row.get("color") or "").strip() or cursor_color_for(owner),
            "x": point[0],
            "y": point[1],
            "team_id": team_n,
        }
    texts: list[dict[str, Any]] = []
    raw_texts = raw.get("texts") if isinstance(raw.get("texts"), list) else []
    for item in raw_texts:
        cleaned_text = _clean_text(item)
        if cleaned_text is not None:
            texts.append(cleaned_text)
        if len(texts) >= MAX_TEXTS:
            break
    base["strokes"]["teacher"] = teacher_strokes
    base["strokes"]["teams"] = teams
    base["cursors"] = cursors
    base["texts"] = texts
    return base


def _clean_text(raw: Any) -> dict[str, Any] | None:
    """Keep one whiteboard text label in normalized coordinates.

    Args:
        raw: Posted or stored text object.
    """
    if not isinstance(raw, dict):
        return None
    text = str(raw.get("text") or "").replace("\r", "").strip()
    if not text:
        return None
    point = _clean_point([raw.get("x"), raw.get("y")])
    if point is None:
        return None
    text_id = str(raw.get("id") or "").strip()
    if not text_id:
        return None
    owner = str(raw.get("owner") or "").strip() or "teacher"
    team_id = raw.get("team_id")
    try:
        team_n = int(team_id) if team_id not in (None, "") else None
    except (TypeError, ValueError):
        team_n = None
    return {
        "id": text_id[:80],
        "owner": owner,
        "name": str(raw.get("name") or owner).strip() or owner,
        "color": str(raw.get("color") or "").strip() or cursor_color_for(owner),
        "x": point[0],
        "y": point[1],
        "team_id": team_n,
        "text": text[:MAX_TEXT_LEN],
    }


def apply_canvas_text(
    stored: Any,
    *,
    owner: str,
    name: str,
    text_id: str,
    text: str,
    x: Any = None,
    y: Any = None,
    color: str | None = None,
    team_id: int | None = None,
) -> dict[str, Any]:
    """Insert, replace, or delete one session text label.

    An empty ``text`` removes ``text_id``. Only the owning writer may
    replace an existing label.

    Args:
        stored: Current canvas-sync blob.
        owner: ``teacher`` or roster id string.
        name: Display name stored with the label.
        text_id: Stable id from the client.
        text: Label body. Empty deletes.
        x: Normalized x in 0–1.
        y: Normalized y in 0–1.
        color: Optional hex colour.
        team_id: Team bucket when the board is shared within a group.

    Returns:
        Updated public canvas-sync blob.
    """
    blob = public_canvas_sync(stored)
    key = str(owner or "teacher").strip() or "teacher"
    sid = str(text_id or "").strip()
    if not sid:
        return blob
    body = str(text or "").strip()
    kept: list[dict[str, Any]] = []
    replaced = False
    for row in blob["texts"]:
        if str(row.get("id") or "") != sid:
            kept.append(row)
            continue
        if str(row.get("owner") or "") != key:
            kept.append(row)
            replaced = True
            continue
        replaced = True
        if not body:
            continue
        point = _clean_point([x, y]) or [row["x"], row["y"]]
        kept.append(
            {
                "id": sid[:80],
                "owner": key,
                "name": str(name or key).strip() or key,
                "color": (color or "").strip() or str(row.get("color") or cursor_color_for(key)),
                "x": point[0],
                "y": point[1],
                "team_id": team_id if team_id is not None else row.get("team_id"),
                "text": body[:MAX_TEXT_LEN],
            }
        )
    if not replaced and body:
        point = _clean_point([x, y])
        if point is not None:
            kept.append(
                {
                    "id": sid[:80],
                    "owner": key,
                    "name": str(name or key).strip() or key,
                    "color": (color or "").strip() or cursor_color_for(key),
                    "x": point[0],
                    "y": point[1],
                    "team_id": team_id,
                    "text": body[:MAX_TEXT_LEN],
                }
            )
    blob["texts"] = kept[:MAX_TEXTS]
    return public_canvas_sync(blob)


def _mark_text_ownership(
    rows: list[dict[str, Any]], viewer: str | None
) -> list[dict[str, Any]]:
    """Copy text rows and flag the ones this viewer may edit.

    Args:
        rows: Public text labels.
        viewer: ``teacher`` or roster id string.
    """
    owner = str(viewer or "").strip()
    marked: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["mine"] = bool(owner) and str(item.get("owner") or "") == owner
        marked.append(item)
    return marked


def apply_canvas_presence(
    stored: Any,
    *,
    owner: str,
    name: str,
    color: str | None = None,
    team_id: int | None = None,
    x: Any = None,
    y: Any = None,
    stroke_id: str | None = None,
    point: Any = None,
    ended: bool = False,
    publish_stroke: bool = False,
    stroke_bucket: str = "teacher",
) -> dict[str, Any]:
    """Merge one cursor tick and optional stroke point.

    Args:
        stored: Current canvas-sync blob.
        owner: ``teacher`` or roster id string.
        name: Display name on the cursor chip.
        color: Optional hex colour.
        team_id: Team bucket for shared-group mode.
        x: Cursor x in 0–1.
        y: Cursor y in 0–1.
        stroke_id: Stable id while a pointer is down.
        point: Optional ``[x, y]`` appended to that stroke.
        ended: True to stop appending (leave the stroke in place).
        publish_stroke: When False, only the cursor is stored.
        stroke_bucket: ``teacher`` or ``team``.

    Returns:
        Updated public canvas-sync blob.
    """
    blob = public_canvas_sync(stored)
    key = str(owner or "teacher").strip() or "teacher"
    tint = (color or "").strip() or cursor_color_for(key)
    cursor_point = _clean_point([x, y])
    if cursor_point is None and point is not None:
        cursor_point = _clean_point(point)
    if cursor_point is not None:
        blob["cursors"][key] = {
            "owner": key,
            "name": str(name or key).strip() or key,
            "color": tint,
            "x": cursor_point[0],
            "y": cursor_point[1],
            "team_id": team_id,
        }
        if len(blob["cursors"]) > MAX_CURSORS:
            extras = list(blob["cursors"])[MAX_CURSORS:]
            for extra in extras:
                blob["cursors"].pop(extra, None)
    if not publish_stroke or ended:
        return blob
    add_point = _clean_point(point) or cursor_point
    if add_point is None:
        return blob
    sid = str(stroke_id or "").strip()
    if not sid:
        return blob
    if stroke_bucket == "team":
        team_key = str(int(team_id)) if team_id is not None else "shared"
        bucket = blob["strokes"]["teams"].setdefault(team_key, [])
    else:
        bucket = blob["strokes"]["teacher"]
    target = None
    for row in bucket:
        if row.get("id") == sid:
            target = row
            break
    if target is None:
        target = {
            "id": sid,
            "owner": key,
            "team_id": team_id,
            "color": tint,
            "points": [],
        }
        bucket.append(target)
        if len(bucket) > MAX_STROKES:
            del bucket[0 : len(bucket) - MAX_STROKES]
    if len(target["points"]) < MAX_POINTS:
        target["points"].append(add_point)
    return public_canvas_sync(blob)


def canvas_view_for(
    stored: Any,
    *,
    align: str,
    team_id: int | None = None,
    include_all_teams: bool = False,
    viewer: str | None = None,
) -> dict[str, Any]:
    """Filter strokes, cursors, and text for one viewer.

    Team alignment is the collaborative board: group strokes, the
    teacher's strokes, and named cursors. Individual alignment returns
    only that viewer's text labels.

    Args:
        stored: Public or stored canvas-sync blob.
        align: ``teacher`` / ``student`` / ``team``.
        team_id: Viewer's team for shared-group mode.
        include_all_teams: Staff preview of every team bucket.
        viewer: ``teacher`` or roster id. Used to flag editable text.
    """
    blob = public_canvas_sync(stored)
    mode = normalize_canvas_align(align)
    texts = list(blob.get("texts") or [])
    teacher_strokes = list(blob["strokes"].get("teacher") or [])
    if mode == "student":
        own = [
            row
            for row in texts
            if viewer and str(row.get("owner") or "") == str(viewer)
        ]
        return {
            "strokes": [],
            "cursors": [],
            "texts": _mark_text_ownership(own, viewer),
        }
    if mode == "teacher":
        teacher_cursor = blob["cursors"].get("teacher")
        own = [row for row in texts if str(row.get("owner") or "") == "teacher"]
        return {
            "strokes": teacher_strokes,
            "cursors": [teacher_cursor] if teacher_cursor else [],
            "texts": _mark_text_ownership(own, viewer or "teacher"),
        }
    strokes: list[dict[str, Any]] = []
    cursors: list[dict[str, Any]] = []
    visible_texts: list[dict[str, Any]] = []
    if include_all_teams:
        strokes.extend(teacher_strokes)
        for rows in (blob["strokes"].get("teams") or {}).values():
            strokes.extend(rows)
        cursors.extend(blob["cursors"].values())
        visible_texts = texts
    elif team_id is not None:
        strokes.extend(
            (blob["strokes"].get("teams") or {}).get(str(int(team_id))) or []
        )
        strokes.extend(teacher_strokes)
        for row in blob["cursors"].values():
            if row.get("team_id") == int(team_id) or row.get("owner") == "teacher":
                cursors.append(row)
        visible_texts = [
            row
            for row in texts
            if row.get("team_id") == int(team_id) or row.get("owner") == "teacher"
        ]
    else:
        strokes.extend((blob["strokes"].get("teams") or {}).get("shared") or [])
        strokes.extend(teacher_strokes)
        for row in blob["cursors"].values():
            if row.get("team_id") in (None, "") or row.get("owner") == "teacher":
                cursors.append(row)
        visible_texts = [
            row
            for row in texts
            if row.get("team_id") == 0 or row.get("owner") == "teacher"
        ]
    return {
        "strokes": strokes,
        "cursors": cursors,
        "texts": _mark_text_ownership(visible_texts, viewer),
    }

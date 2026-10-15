"""Per-question live defaults for the teacher question bank (MCK-254).

A bank question may store how it should start in Run Live Class:
Individual or Group, whether results are visible, and the award amount.
Null means today's behaviour: individual (a rank import preset can still
choose Group), show results on, and a +1 award. Existing rows stay null
until a teacher sets a default.
"""

from __future__ import annotations

import sqlite3
from typing import Any

LIVE_DEFAULT_COLUMNS = (
    "live_response_mode",
    "live_show_results",
    "live_points",
    "bank_scope",
)


def ensure_question_editor_columns(conn: sqlite3.Connection) -> None:
    """Add nullable question columns. Safe to run on every boot.

    ``PRAGMA table_info`` then ``ALTER TABLE`` so a second worker, or a
    second call, finds the column and skips the ``ALTER``. New installs
    that already created the columns in ``CREATE TABLE`` take the same
    skip. Existing rows are left NULL.

    Args:
        conn: Open sqlite connection for the school database.
    """
    cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(questions)")}
    if "live_response_mode" not in cols:
        conn.execute("ALTER TABLE questions ADD COLUMN live_response_mode TEXT")
    if "live_show_results" not in cols:
        conn.execute("ALTER TABLE questions ADD COLUMN live_show_results INTEGER")
    if "live_points" not in cols:
        conn.execute("ALTER TABLE questions ADD COLUMN live_points REAL")
    if "bank_scope" not in cols:
        conn.execute("ALTER TABLE questions ADD COLUMN bank_scope TEXT")


def _parse_show_results(raw: Any) -> bool | None:
    """Parse a show-results flag. ``None`` clears the stored default."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return bool(int(raw))
    token = str(raw).strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off", ""}:
        return False if token else None
    raise ValueError("live_show_results must be true or false")


def parse_live_defaults(body: dict[str, Any]) -> dict[str, Any]:
    """Read live-default keys that are actually present on a write body.

    Omitted keys are left out of the result so a stem save does not pin
    today's implied settings onto the row.

    Args:
        body: JSON object from the question editor or Add New.

    Returns:
        A dict with any of ``live_response_mode``, ``live_show_results``,
        and ``live_points``. Values may be ``None`` to clear a stored default.
    """
    out: dict[str, Any] = {}
    if "live_response_mode" in body or "liveResponseMode" in body:
        raw = body.get("live_response_mode", body.get("liveResponseMode"))
        if raw in (None, ""):
            out["live_response_mode"] = None
        else:
            token = str(raw).strip().lower()
            if token in {"group", "group_submit", "group_consensus"}:
                out["live_response_mode"] = "group"
            elif token == "individual":
                out["live_response_mode"] = "individual"
            else:
                raise ValueError("live_response_mode must be individual or group")
    if "live_show_results" in body or "liveShowResults" in body:
        raw = body.get("live_show_results", body.get("liveShowResults"))
        out["live_show_results"] = _parse_show_results(raw)
    if "live_points" in body or "livePoints" in body:
        raw = body.get("live_points", body.get("livePoints"))
        if raw in (None, ""):
            out["live_points"] = None
        else:
            try:
                points = float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError("live_points must be a number") from exc
            if points < 0:
                raise ValueError("live_points cannot be negative")
            out["live_points"] = points
    return out


def defaults_from_row(row: Any) -> dict[str, Any]:
    """Read stored defaults from a sqlite row. Missing columns count as null.

    Args:
        row: A ``sqlite3.Row`` or mapping that may include the new columns.
    """
    def _get(name: str) -> Any:
        try:
            if hasattr(row, "keys") and name not in row.keys():
                return None
            return row[name]
        except (KeyError, IndexError, TypeError):
            return None

    mode = _get("live_response_mode")
    show = _get("live_show_results")
    points = _get("live_points")
    scope = _get("bank_scope")
    parsed: dict[str, Any] = {
        "live_response_mode": str(mode).strip().lower() if mode else None,
        "live_show_results": None if show is None else bool(int(show)),
        "live_points": None if points is None else float(points),
        "bank_scope": str(scope).strip() if scope else None,
    }
    if parsed["live_response_mode"] not in {None, "individual", "group"}:
        parsed["live_response_mode"] = None
    return parsed


def stamp_launch_defaults(item: dict[str, Any], defaults: dict[str, Any] | None) -> dict[str, Any]:
    """Copy non-null defaults onto a live item as its starting values.

    Null fields are skipped. A rank import preset still applies later when
    ``live_response_mode`` was never stored. An explicit Individual clears
    that preset so launch does not flip the card back to Group.

    Args:
        item: Playlist or lifecycle question dict, updated in place.
        defaults: Parsed defaults. ``None`` or an empty dict changes nothing.

    Returns:
        The same ``item``.
    """
    if not defaults:
        return item
    mode = defaults.get("live_response_mode")
    if mode == "group":
        item["live_response_mode"] = "group"
        item["response_mode"] = "group_submit"
        item["publish_mode"] = "group_submit"
        modes = [
            str(value)
            for value in (item.get("publish_modes") or [])
            if str(value).strip()
        ]
        if "individual" not in modes:
            modes.insert(0, "individual")
        if "group_submit" not in modes:
            modes.append("group_submit")
        item["publish_modes"] = modes
    elif mode == "individual":
        item["live_response_mode"] = "individual"
        item["response_mode"] = "individual"
        item["publish_mode"] = "individual"
        item["publish_modes"] = ["individual"]
        item.pop("import_group_preset", None)
    if "live_show_results" in defaults and defaults.get("live_show_results") is not None:
        visible = bool(defaults["live_show_results"])
        item["live_show_results"] = visible
        item["show_live_results"] = visible
    if "live_points" in defaults and defaults.get("live_points") is not None:
        item["live_points"] = float(defaults["live_points"])
    return item


def normalize_scope_token(raw: Any) -> str:
    """Return ``M1``–``M8``, ``course``, or ``""`` for an empty scope.

    Args:
        raw: Staff Bank scope value.
    """
    token = str(raw or "").strip()
    if not token:
        return ""
    lowered = token.lower().replace("_", "-")
    if lowered in {"course", "course-wide", "coursewide", "all"}:
        return "course"
    upper = token.upper()
    if len(upper) == 2 and upper[0] == "M" and upper[1] in "12345678":
        return upper
    if token.isdigit() and token in {"1", "2", "3", "4", "5", "6", "7", "8"}:
        return f"M{token}"
    raise ValueError("bank_scope must be M1–M8 or course")


def scope_visible(
    stored: Any, viewing: str, *, include_course_on_module: bool = True
) -> bool:
    """Return whether a question with ``stored`` scope shows on ``viewing``.

    A blank stored scope stays visible wherever the bank is linked, which
    is today's behaviour. A module pin shows only on that module. Course
    Wide stays importable from every module picker; the bank's module tabs
    pass ``include_course_on_module=False`` so those cards stay on Course Wide.

    Args:
        stored: ``questions.bank_scope``, or blank.
        viewing: ``M1``–``M8`` or ``course``.
        include_course_on_module: When false, a ``course`` pin is hidden
            from a module view. Import leaves this on.
    """
    token = str(stored or "").strip()
    if not token:
        return True
    if token.lower() == "course":
        view = str(viewing or "").strip().lower()
        if view in {"course", "course-wide", "coursewide"}:
            return True
        return bool(include_course_on_module)
    view = str(viewing or "").strip().lower()
    if view in {"course", "course-wide", "coursewide"}:
        return False
    return token.upper() == str(viewing or "").strip().upper()

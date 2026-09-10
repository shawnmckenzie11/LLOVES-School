"""Active-media payload helpers for a live class session.

One current media object per ``live_class_sessions`` row (JSON column), not a
parallel session table and not a live-prompt kind. Teacher set/swap/clear plus
mid-session control-state (params + student unlock) share this payload.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

# Seed C1 Real-slice page (Grade-11 parabola / real slice of the 3D saddle).
DEFAULT_LIVE_MEDIA_URL = "/static/live-media/m1c1-c1-real-slice.html"
DEFAULT_LIVE_MEDIA_TITLE = "C1 Real-slice"
DEFAULT_LIVE_MEDIA_STEM = (
    "When looking at the parabola represented by y = ax^2 + bx + c, "
    "what do you know about a, b, and c?"
)
DEFAULT_LIVE_MEDIA_CAPTION = ""
DEFAULT_LIVE_MEDIA_PARAMS: dict[str, float] = {"a": 1.0, "b": 0.0, "c": 0.0}

_UNSET = object()

_PARAM_RANGES: dict[str, tuple[float, float]] = {
    "a": (-5.0, 5.0),
    "b": (-10.0, 10.0),
    "c": (-10.0, 10.0),
}

_TEXT_MAX = {
    "title": 120,
    "caption": 400,
    "stem": 400,
}


def default_seed_media(*, url: str | None = None) -> dict[str, Any]:
    """Return the C1 Real-slice payload teachers push as the session default.

    Args:
        url: Optional same-origin ``/static/...`` path. Defaults to the seed page.
    """
    path = normalize_active_media_url(url or DEFAULT_LIVE_MEDIA_URL)
    assert path is not None
    return {
        "url": path,
        "title": DEFAULT_LIVE_MEDIA_TITLE,
        "caption": DEFAULT_LIVE_MEDIA_CAPTION,
        "stem": DEFAULT_LIVE_MEDIA_STEM,
        "student_controls_unlocked": False,
        "params": dict(DEFAULT_LIVE_MEDIA_PARAMS),
    }


def normalize_active_media_url(raw: Any) -> str | None:
    """Return a same-origin ``/static/...`` path, or ``None`` when clearing.

    Absolute http(s) URLs are reduced to their path. Hostnames are discarded so
    the student iframe always loads this origin. External non-static paths are
    rejected.

    Args:
        raw: Posted URL, empty string, or ``None``.

    Returns:
        Normalized path, or ``None`` if ``raw`` is empty/None (clear).

    Raises:
        ValueError: If ``raw`` is non-empty but not an allowed static path.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    parsed = urlparse(text)
    scheme = (parsed.scheme or "").lower()
    if scheme and scheme not in {"http", "https"}:
        raise ValueError("Active media must be a same-origin /static/ URL.")
    path = parsed.path or ""
    if not path.startswith("/static/"):
        raise ValueError("Active media must be a same-origin /static/ URL.")
    parts = [part for part in path.split("/") if part]
    if ".." in parts or not parts:
        raise ValueError("Active media path is not allowed.")
    if path.endswith("/"):
        raise ValueError("Active media must point at a file under /static/.")
    return path


def normalize_active_media_params(raw: Any) -> dict[str, float]:
    """Clamp ``a``, ``b``, ``c`` for y = ax^2 + bx + c.

    Args:
        raw: Mapping of coefficient names, or ``None`` for defaults.

    Returns:
        Dict with float ``a``, ``b``, ``c``.

    Raises:
        ValueError: If a provided coefficient is not a finite number.
    """
    values = dict(DEFAULT_LIVE_MEDIA_PARAMS)
    if raw is None:
        return values
    if not isinstance(raw, dict):
        raise ValueError("params must be an object with a, b, and c.")
    for key, (low, high) in _PARAM_RANGES.items():
        if key not in raw:
            continue
        try:
            number = float(raw[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"params.{key} must be a number.") from exc
        if number != number or number in (float("inf"), float("-inf")):
            raise ValueError(f"params.{key} must be a finite number.")
        values[key] = max(low, min(high, number))
    return values


def _clip_text(raw: Any, field: str) -> str:
    """Strip and bound a title/caption/stem string.

    Args:
        raw: Posted text.
        field: ``title``, ``caption``, or ``stem``.
    """
    text = str(raw or "").strip()
    limit = _TEXT_MAX[field]
    if len(text) > limit:
        return text[:limit]
    return text


def public_active_media_payload(stored: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the student/staff JSON fragment, or ``None`` when media is cleared.

    Args:
        stored: Dict from ``active_media_json``, possibly partial.
    """
    if not stored or not isinstance(stored, dict):
        return None
    url = str(stored.get("url") or "").strip()
    if not url:
        return None
    params = stored.get("params")
    try:
        coeffs = normalize_active_media_params(params if isinstance(params, dict) else None)
    except ValueError:
        coeffs = dict(DEFAULT_LIVE_MEDIA_PARAMS)
    return {
        "url": url,
        "title": str(stored.get("title") or ""),
        "caption": str(stored.get("caption") or ""),
        "stem": str(stored.get("stem") or ""),
        "student_controls_unlocked": bool(stored.get("student_controls_unlocked")),
        "params": coeffs,
        "updated_at": stored.get("updated_at"),
    }


def apply_active_media_update(
    current: dict[str, Any] | None,
    *,
    clear: bool = False,
    url: Any = _UNSET,
    title: Any = _UNSET,
    caption: Any = _UNSET,
    stem: Any = _UNSET,
    student_controls_unlocked: Any = _UNSET,
    params: Any = _UNSET,
    updated_at: str | None = None,
) -> dict[str, Any] | None:
    """Set, swap, merge control-state, or clear the session media object.

    A missing ``url`` with an existing payload is a control-state patch
    (unlock flags / quadratic params) without swapping the page.

    Args:
        current: Existing public payload, or ``None``.
        clear: When True, drop media regardless of other fields.
        url: New ``/static/...`` path, empty/None to clear, or omitted to merge.
        title: Optional title overlay.
        caption: Optional caption slot (Wonder delight pass).
        stem: Optional student stem overlay.
        student_controls_unlocked: Optional teacher unlock flag.
        params: Optional ``{a,b,c}`` overlay (merged onto current/defaults).
        updated_at: ISO timestamp stamped onto the stored object.

    Returns:
        New payload dict, or ``None`` when cleared.

    Raises:
        ValueError: Invalid URL/params, or a merge with no current media.
    """
    if clear:
        return None
    url_given = url is not _UNSET
    if url_given:
        normalized = normalize_active_media_url(url)
        if normalized is None:
            return None
        seed = default_seed_media(url=normalized)
        if normalized != DEFAULT_LIVE_MEDIA_URL:
            seed["title"] = ""
            seed["stem"] = ""
            seed["caption"] = ""
        base = seed
        if current and str(current.get("url") or "") == normalized:
            # Same URL: treat like a merge so unlock/params persist unless posted.
            base = {**default_seed_media(url=normalized), **{
                key: current[key]
                for key in (
                    "title",
                    "caption",
                    "stem",
                    "student_controls_unlocked",
                    "params",
                )
                if key in current
            }}
            base["url"] = normalized
    elif current is None:
        raise ValueError("No active media to update. Set a /static/ URL first.")
    else:
        base = dict(current)

    if title is not _UNSET:
        base["title"] = _clip_text(title, "title")
    if caption is not _UNSET:
        base["caption"] = _clip_text(caption, "caption")
    if stem is not _UNSET:
        base["stem"] = _clip_text(stem, "stem")
    if student_controls_unlocked is not _UNSET:
        if isinstance(student_controls_unlocked, str):
            base["student_controls_unlocked"] = (
                student_controls_unlocked.strip().lower() in {"1", "true", "yes"}
            )
        else:
            base["student_controls_unlocked"] = bool(student_controls_unlocked)
    if params is not _UNSET:
        incoming = params if isinstance(params, dict) else None
        merged = dict(base.get("params") or DEFAULT_LIVE_MEDIA_PARAMS)
        if incoming:
            merged.update(incoming)
        base["params"] = normalize_active_media_params(merged)
    else:
        base["params"] = normalize_active_media_params(base.get("params"))
    if updated_at:
        base["updated_at"] = updated_at
    return public_active_media_payload(base)

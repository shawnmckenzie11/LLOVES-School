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
DEFAULT_LIVE_MEDIA_CHIP = "From this view only — what must be true?"
DEFAULT_LIVE_MEDIA_LATERAL_CHIP = "This picture was always a slice"
DEFAULT_LIVE_MEDIA_PARAMS: dict[str, float] = {"a": 1.0, "b": 0.0, "c": 0.0}
LAYER_KEYS: tuple[str, ...] = ("L0", "L1", "L2", "L3", "L4")
# Optional post-freeze encore (click-out only; never an iframe, never autoplay).
ENCORE_YOUTUBE_URL = "https://www.youtube.com/watch?v=T647CGsuOVU&t=54s"
ENCORE_LABEL = (
    "Optional encore — Welch Labs, Imaginary Numbers Are Real Part 1 (~0:54)"
)

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
    "entry_chip": 160,
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
        "entry_chip": DEFAULT_LIVE_MEDIA_CHIP,
        "student_controls_unlocked": False,
        "reveal_axes": False,
        "reveal_lateral": False,
        "allow_3d_limited": False,
        "frozen": False,
        "unlock_flags": default_unlock_flags(),
        "answers": [],
        "params": dict(DEFAULT_LIVE_MEDIA_PARAMS),
    }


def default_unlock_flags() -> dict[str, bool]:
    """L0 is entry (always on). L1–L3 are unnamed peels; L4 is in-pane lateral."""
    return {"L0": True, "L1": False, "L2": False, "L3": False, "L4": False}


def _as_bool(raw: Any) -> bool:
    """Coerce JSON/form booleans."""
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes"}
    return bool(raw)


def normalize_unlock_flags(
    raw: Any, *, base: dict[str, bool] | None = None
) -> dict[str, bool]:
    """Merge L0–L4 unlock flags. L0 (entry) cannot be turned off.

    Args:
        raw: Partial ``{L0..L4: bool}`` overlay, or ``None``.
        base: Existing flags to merge onto.
    """
    flags = dict(base) if base else default_unlock_flags()
    for key in LAYER_KEYS:
        flags.setdefault(key, key == "L0")
    if isinstance(raw, dict):
        for key in LAYER_KEYS:
            if key in raw:
                flags[key] = _as_bool(raw[key])
    flags["L0"] = True
    return {key: bool(flags[key]) for key in LAYER_KEYS}


def normalize_answers(raw: Any) -> list[str]:
    """Optional engagement-engine choices; same stem, answers change per reveal.

    Args:
        raw: List of short strings, or ``None``/empty for no choices yet.

    Raises:
        ValueError: If ``raw`` is present but not a list.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("answers must be a list of strings.")
    out: list[str] = []
    for item in raw[:8]:
        text = str(item or "").strip()
        if text:
            out.append(text[:80])
    return out


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
        field: ``title``, ``caption``, ``stem``, or ``entry_chip``.
    """
    text = str(raw or "").strip()
    limit = _TEXT_MAX[field]
    if len(text) > limit:
        return text[:limit]
    return text


def public_active_media_payload(stored: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the student/staff JSON fragment, or ``None`` when media is cleared.

    Lateral peel sets ``chip`` to ``DEFAULT_LIVE_MEDIA_LATERAL_CHIP``; the stored
    ``entry_chip`` and stem stay put. Encore URL is always present; student
    chrome must not show it until ``frozen``.

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
    try:
        answer_list = normalize_answers(stored.get("answers") or [])
    except ValueError:
        answer_list = []
    flags = normalize_unlock_flags(stored.get("unlock_flags"))
    reveal_lateral = bool(stored.get("reveal_lateral")) or bool(flags.get("L4"))
    flags["L4"] = reveal_lateral
    stored_chip = str(stored.get("entry_chip") or "")
    if stored_chip == DEFAULT_LIVE_MEDIA_LATERAL_CHIP:
        stored_chip = DEFAULT_LIVE_MEDIA_CHIP
    display_chip = (
        DEFAULT_LIVE_MEDIA_LATERAL_CHIP if reveal_lateral else stored_chip
    )
    return {
        "url": url,
        "title": str(stored.get("title") or ""),
        "caption": str(stored.get("caption") or ""),
        "stem": str(stored.get("stem") or ""),
        "entry_chip": stored_chip,
        "chip": display_chip,
        "student_controls_unlocked": bool(stored.get("student_controls_unlocked")),
        "reveal_axes": bool(stored.get("reveal_axes")),
        "reveal_lateral": reveal_lateral,
        "allow_3d_limited": bool(stored.get("allow_3d_limited")),
        "frozen": bool(stored.get("frozen")),
        "encore_url": ENCORE_YOUTUBE_URL,
        "encore_label": ENCORE_LABEL,
        "unlock_flags": flags,
        "answers": answer_list,
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
    entry_chip: Any = _UNSET,
    student_controls_unlocked: Any = _UNSET,
    reveal_axes: Any = _UNSET,
    reveal_lateral: Any = _UNSET,
    allow_3d_limited: Any = _UNSET,
    frozen: Any = _UNSET,
    unlock_flags: Any = _UNSET,
    answers: Any = _UNSET,
    params: Any = _UNSET,
    updated_at: str | None = None,
) -> dict[str, Any] | None:
    """Set, swap, merge control-state, or clear the session media object.

    A missing ``url`` with an existing payload is a control-state patch
    (unlock flags / quadratic params / axes peel) without swapping the page.

    Args:
        current: Existing public payload, or ``None``.
        clear: When True, drop media regardless of other fields.
        url: New ``/static/...`` path, empty/None to clear, or omitted to merge.
        title: Optional title overlay.
        caption: Optional caption slot (Wonder delight pass).
        stem: Optional student stem overlay (engagement stem stays stable).
        entry_chip: Optional chip overlay (entry vibe).
        student_controls_unlocked: Teacher unlock for student sliders.
        reveal_axes: Teacher peel for axes/grid on the student face.
        reveal_lateral: In-pane yaw so the gold parabola reads as a slice (L4).
        allow_3d_limited: Small student yaw around the lateral pose only.
        frozen: Argue is done; student chrome may offer the click-out encore.
        unlock_flags: Partial L0–L4 flags (delight pass); L0 stays on; L4
            locksteps with ``reveal_lateral``.
        answers: Optional choice list that may change with each reveal.
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
    persist_keys = (
        "title",
        "caption",
        "stem",
        "entry_chip",
        "student_controls_unlocked",
        "reveal_axes",
        "reveal_lateral",
        "allow_3d_limited",
        "frozen",
        "unlock_flags",
        "answers",
        "params",
    )
    if url_given:
        normalized = normalize_active_media_url(url)
        if normalized is None:
            return None
        seed = default_seed_media(url=normalized)
        if normalized != DEFAULT_LIVE_MEDIA_URL:
            seed["title"] = ""
            seed["stem"] = ""
            seed["caption"] = ""
            seed["entry_chip"] = ""
        base = seed
        if current and str(current.get("url") or "") == normalized:
            # Same URL: treat like a merge so unlock/params persist unless posted.
            base = {**default_seed_media(url=normalized), **{
                key: current[key]
                for key in persist_keys
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
    if entry_chip is not _UNSET:
        base["entry_chip"] = _clip_text(entry_chip, "entry_chip")
    if student_controls_unlocked is not _UNSET:
        base["student_controls_unlocked"] = _as_bool(student_controls_unlocked)
    if reveal_axes is not _UNSET:
        base["reveal_axes"] = _as_bool(reveal_axes)
    if unlock_flags is not _UNSET:
        base["unlock_flags"] = normalize_unlock_flags(
            unlock_flags, base=base.get("unlock_flags")
        )
    else:
        base["unlock_flags"] = normalize_unlock_flags(
            None, base=base.get("unlock_flags")
        )
    flags = normalize_unlock_flags(None, base=base.get("unlock_flags"))
    lateral = bool(base.get("reveal_lateral")) or bool(flags.get("L4"))
    if unlock_flags is not _UNSET and isinstance(unlock_flags, dict) and "L4" in unlock_flags:
        lateral = _as_bool(unlock_flags["L4"])
    if reveal_lateral is not _UNSET:
        lateral = _as_bool(reveal_lateral)
    flags["L4"] = lateral
    base["unlock_flags"] = flags
    base["reveal_lateral"] = lateral
    if allow_3d_limited is not _UNSET:
        base["allow_3d_limited"] = _as_bool(allow_3d_limited)
    else:
        base["allow_3d_limited"] = bool(base.get("allow_3d_limited"))
    if frozen is not _UNSET:
        base["frozen"] = _as_bool(frozen)
    else:
        base["frozen"] = bool(base.get("frozen"))
    if answers is not _UNSET:
        base["answers"] = normalize_answers(answers)
    else:
        try:
            base["answers"] = normalize_answers(base.get("answers") or [])
        except ValueError:
            base["answers"] = []
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

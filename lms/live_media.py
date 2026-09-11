"""Active-media payload helpers for a live class session.

One current media object per ``live_class_sessions`` row (JSON column), not a
parallel session table, not a live-prompt kind, and not a FlagStrip. Teacher
set/swap/clear plus mid-session peels (``reveal_axes`` / L0–L4 /
``reveal_lateral`` / ``allow_3d_limited`` / view tools) share this blob.
CONS-1…5 unlock on ``frozen: true`` here. C2/C3 never seed
``active_media_json``.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlparse

try:
    from quick_hitter import (
        QUICK_HITTER_ARTIFACT_ID,
        RIDE_CONS,
        quick_hitter_packaging,
    )
except ImportError:  # ``python3 lms/app.py`` package import
    from lms.quick_hitter import (
        QUICK_HITTER_ARTIFACT_ID,
        RIDE_CONS,
        quick_hitter_packaging,
    )

# Seed C1 Real-slice page (Grade-11 parabola / real slice of the 3D saddle).
DEFAULT_LIVE_MEDIA_URL = "/static/live-media/m1c1-c1-real-slice.html"
DEFAULT_LIVE_MEDIA_STEM = (
    "Consider the parabola represented by y = ax^2 + bx + c. "
    "What do you know about a, b, and c?"
)
DEFAULT_LIVE_MEDIA_TITLE = DEFAULT_LIVE_MEDIA_STEM
DEFAULT_LIVE_MEDIA_CAPTION = ""
# Entry chip is on in the iframe (stem text). Do not restore "From this view only".
DEFAULT_LIVE_MEDIA_CHIP = ""
DEFAULT_LIVE_MEDIA_LATERAL_CHIP = "This picture was always a slice"
DEFAULT_LIVE_MEDIA_PARAMS: dict[str, float] = {"a": 1.0, "b": 0.0, "c": 0.0}
PARAM_KEYS: tuple[str, ...] = ("a", "b", "c")
LAYER_KEYS: tuple[str, ...] = ("L0", "L1", "L2", "L3", "L4")
# Teacher-iframe view tools (0 = fixed/faint/locked; upper bound is "full").
STUDENT_ZOOM_MAX = 10.0
SURFACE_TRANSPARENCY_MAX = 10.0
STUDENT_YAW_RANGE_MAX = 360.0
DEFAULT_STUDENT_ZOOM = 0.0
# Half of the previous default 1.5 so the entry saddle is fainter.
DEFAULT_SURFACE_TRANSPARENCY = 0.75
DEFAULT_STUDENT_YAW_RANGE = 0.0
# Old limited-yaw span was ±0.38 rad ≈ 44° total when the staff checkbox is on.
DEFAULT_LIMITED_YAW_DEG = 44.0
# CONS-1…5 stay on the blob/API; the Real-slice table checkboxes are inert.
CONS_TABLE_TOOLS_ENABLED = False
# Optional post-freeze encore (click-out only; never an iframe, never autoplay).
ENCORE_YOUTUBE_URL = "https://www.youtube.com/watch?v=T647CGsuOVU&t=54s"
ENCORE_LABEL = (
    "Optional encore — Welch Labs · scrub to the out-of-page / lateral beat"
)
# Wonder delight toasts (ephemeral student chrome; peels stay in this blob).
TOAST_REVEAL_AXES = "Same question. New reference."
TOAST_STUDENT_UNLOCK = (
    "New control — same question. What changes? What doesn’t?"
)
TOAST_FREEZE = "Park the wonderings. Leave the blank honest."
TOAST_CONS_UNLOCK = (
    "Argue’s parked. Time to name what this picture forced."
)
TOAST_CONS_4 = "Feature → claim. That’s the whole move."
C1_CONS_PACK_ID = "C1-CONS"
C1_CONS_SLIDE_BASE = 900
C2_C3_CHALLENGES = frozenset({"C2", "C3"})

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
    "toast": 160,
    "toast_key": 40,
    "challenge": 8,
    "cons_item": 24,
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
        "param_push": default_param_push(),
        "param_frozen": default_param_frozen(),
        "reveal_axes": False,
        "reveal_lateral": False,
        "allow_3d_limited": False,
        "show_z_axis": False,
        "student_zoom": DEFAULT_STUDENT_ZOOM,
        "freeze_zoom": False,
        "surface_transparency": DEFAULT_SURFACE_TRANSPARENCY,
        "freeze_surface": False,
        "student_yaw_range": DEFAULT_STUDENT_YAW_RANGE,
        "freeze_yaw": False,
        "frozen": False,
        "unlock_flags": default_unlock_flags(),
        "answers": [],
        "params": dict(DEFAULT_LIVE_MEDIA_PARAMS),
        "challenge": "C1",
        "cons_item": "",
        "toast": "",
        "toast_key": "",
    }


def default_unlock_flags() -> dict[str, bool]:
    """L0 is entry (always on). L1–L3 are unnamed peels; L4 is in-pane lateral."""
    return {"L0": True, "L1": False, "L2": False, "L3": False, "L4": False}


def default_param_push() -> dict[str, bool]:
    """a/b/c start unpushed: students do not get those sliders yet."""
    return {key: False for key in PARAM_KEYS}


def default_param_frozen() -> dict[str, bool]:
    """Frozen is on until a slider is pushed, then the teacher may uncheck it."""
    return {key: True for key in PARAM_KEYS}


def normalize_param_bool_map(
    raw: Any,
    *,
    default: dict[str, bool],
    base: dict[str, Any] | None = None,
) -> dict[str, bool]:
    """Merge a partial ``{a,b,c: bool}`` map onto defaults.

    Args:
        raw: Posted map, or ``None``.
        default: Fallback for missing keys.
        base: Existing stored map to overlay first.

    Returns:
        Complete a/b/c bool map.
    """
    out = dict(default)
    if isinstance(base, dict):
        for key in PARAM_KEYS:
            if key in base:
                out[key] = _as_bool(base[key])
    if isinstance(raw, dict):
        for key in PARAM_KEYS:
            if key in raw:
                out[key] = _as_bool(raw[key])
    return out


def coerce_param_push(
    stored: dict[str, Any],
    *,
    posted: Any = _UNSET,
) -> dict[str, bool]:
    """Return a/b/c push flags, inferring from legacy unlock when missing.

    Args:
        stored: Current media object.
        posted: Optional posted ``param_push`` overlay.

    Returns:
        Complete push map.
    """
    base_map = stored.get("param_push")
    if not isinstance(base_map, dict) and bool(stored.get("student_controls_unlocked")):
        base_map = {key: True for key in PARAM_KEYS}
    raw = None if posted is _UNSET else posted
    return normalize_param_bool_map(
        raw, default=default_param_push(), base=base_map if isinstance(base_map, dict) else None
    )


def coerce_param_frozen(
    stored: dict[str, Any],
    *,
    posted: Any = _UNSET,
    push: dict[str, bool] | None = None,
) -> dict[str, bool]:
    """Return a/b/c freeze flags. Unpushed sliders stay frozen.

    Args:
        stored: Current media object.
        posted: Optional posted ``param_frozen`` overlay.
        push: Push map used to force-freeze unpushed keys.

    Returns:
        Complete freeze map with unpushed keys locked on.
    """
    raw = None if posted is _UNSET else posted
    frozen = normalize_param_bool_map(
        raw,
        default=default_param_frozen(),
        base=stored.get("param_frozen") if isinstance(stored.get("param_frozen"), dict) else None,
    )
    flags = push if isinstance(push, dict) else default_param_push()
    for key in PARAM_KEYS:
        if not flags.get(key):
            frozen[key] = True
    return frozen


def live_media_url_swap_allowed(*, testing: bool = False) -> bool:
    """True when teachers may swap away from the seed Real-slice URL.

    Production seed-locks the C1 page. ``LOCAL_DEV_LOGIN`` and Flask tests
    keep the swap field so localhost can still point at other ``/static/`` files.

    Args:
        testing: Flask ``TESTING`` flag from the request app.
    """
    if testing:
        return True
    return (os.getenv("LOCAL_DEV_LOGIN") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def c1_cons_catalog() -> list[dict[str, Any]]:
    """C1 post-freeze consolidation items (student wording; keys stay staff-side).

    Returns:
        CONS-1…5 dicts in order. ``kind`` is ``mc``, ``share``, or ``draw``.
    """
    return [
        {
            "id": "C1-CONS-1",
            "index": 1,
            "kind": "mc",
            "slide_index": C1_CONS_SLIDE_BASE + 1,
            "prompt": (
                "For this picture, what must be true about a in "
                "y = ax² + bx + c?"
            ),
            "choices": [
                "a < 0",
                "a > 0",
                "a = 0",
                "Can’t tell from this picture",
            ],
            "key": "B",
            "cement": "opens upward → a > 0",
        },
        {
            "id": "C1-CONS-2",
            "index": 2,
            "kind": "mc",
            "slide_index": C1_CONS_SLIDE_BASE + 2,
            "prompt": (
                "This parabola meets the y-axis at the origin. "
                "What does that force about c?"
            ),
            "choices": ["c > 0", "c < 0", "c = 0", "c could be anything"],
            "key": "C",
            "cement": "y-intercept at origin → c = 0",
        },
        {
            "id": "C1-CONS-3",
            "index": 3,
            "kind": "mc",
            "slide_index": C1_CONS_SLIDE_BASE + 3,
            "prompt": (
                "The vertex sits on the origin and the graph is symmetric "
                "about the y-axis. What must be true about b for this picture?"
            ),
            "choices": [
                "b > 0",
                "b < 0",
                "b = 0",
                "b is free — we’d need more info",
            ],
            "key": "C",
            "cement": "no linear term / b = 0 for y = x²",
        },
        {
            "id": "C1-CONS-4",
            "index": 4,
            "kind": "draw",
            "slide_index": C1_CONS_SLIDE_BASE + 4,
            "prompt": (
                "Mark one feature on the picture and name the coefficient "
                "claim it forces."
            ),
            "share_alt": (
                "From this picture I know ___ about a / b / c because ___."
            ),
            "key": "",
            "cement": "graph feature → coefficient language",
        },
        {
            "id": "C1-CONS-5",
            "index": 5,
            "kind": "share",
            "slide_index": C1_CONS_SLIDE_BASE + 5,
            "prompt": (
                "What about a, b, or c (or the quadratic family) do you "
                "still not know without changing this picture?"
            ),
            "key": "",
            "cement": "still free / unknown without opening C2",
        },
    ]


def get_c1_cons_item(raw: Any) -> dict[str, Any] | None:
    """Resolve CONS-1…5 from an index, id, or ``CONS-n`` slug.

    Args:
        raw: ``1`` / ``"C1-CONS-1"`` / ``"CONS-1"``, or empty to clear.

    Returns:
        Catalog row, or ``None`` when clearing.

    Raises:
        ValueError: If ``raw`` is present but not a C1 CONS item.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        if not raw:
            return None
        raise ValueError("cons_item must be CONS-1…5, not a boolean.")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        if int(raw) == 0:
            return None
        text = str(int(raw))
    else:
        text = str(raw).strip()
    if not text or text.lower() in {"none", "null", "clear", "0", "false"}:
        return None
    match = re.search(r"(\d+)$", text.upper().replace(" ", ""))
    if match is None:
        raise ValueError("cons_item must be C1-CONS-1 through C1-CONS-5.")
    index = int(match.group(1))
    for item in c1_cons_catalog():
        if int(item["index"]) == index:
            return item
    raise ValueError("cons_item must be C1-CONS-1 through C1-CONS-5.")


def student_cons_prompt_payload(item: dict[str, Any]) -> dict[str, Any]:
    """Student-facing live-prompt payload (no keys / cement / park notes).

    Args:
        item: Row from ``c1_cons_catalog``.
    """
    payload: dict[str, Any] = quick_hitter_packaging(
        ride=RIDE_CONS,
        chain_index=int(item.get("index") or 1),
        chain_length=len(c1_cons_catalog()),
        ephemeral=True,
        durable_store=False,
    )
    payload.update(
        {
            "pack": C1_CONS_PACK_ID,
            "item_id": item["id"],
            "prompt": item["prompt"],
        }
    )
    if item.get("kind") == "mc":
        payload["choices"] = list(item.get("choices") or [])
    if item.get("share_alt"):
        payload["placeholder"] = item["share_alt"]
    return payload


def staff_cons_prompt_payload(item: dict[str, Any]) -> dict[str, Any]:
    """Staff live-prompt payload including cement notes (not shown to students).

    Args:
        item: Row from ``c1_cons_catalog``.
    """
    payload = student_cons_prompt_payload(item)
    payload["key"] = item.get("key") or ""
    payload["cement"] = item.get("cement") or ""
    return payload


def is_c1_cons_payload(payload: Any) -> bool:
    """True when a live-prompt payload belongs to the C1 CONS pack.

    Args:
        payload: Prompt JSON object.
    """
    if not isinstance(payload, dict):
        return False
    artifact = str(payload.get("artifact_id") or "").strip()
    ride = str(payload.get("ride") or "").strip().lower()
    if artifact == QUICK_HITTER_ARTIFACT_ID and ride == RIDE_CONS:
        return True
    pack = str(payload.get("pack") or "").strip().upper()
    item_id = str(payload.get("item_id") or "").strip().upper()
    return pack == C1_CONS_PACK_ID or item_id.startswith("C1-CONS-")


def normalize_challenge(raw: Any) -> str:
    """Return ``C1``, ``C2``, ``C3``, or empty.

    Args:
        raw: Posted challenge id.
    """
    text = str(raw or "").strip().upper()
    if text in {"C1", "C2", "C3"}:
        return text
    if not text:
        return ""
    raise ValueError("challenge must be C1, C2, or C3.")


def is_c1_real_slice(media: dict[str, Any] | None) -> bool:
    """True when the session is on the C1 Real-slice page.

    Args:
        media: Public active-media payload.
    """
    if not media or not isinstance(media, dict):
        return False
    url = str(media.get("url") or "").strip()
    challenge = str(media.get("challenge") or "").strip().upper()
    return url == DEFAULT_LIVE_MEDIA_URL or challenge == "C1"


def challenge_clears_active_media(raw: Any) -> bool:
    """True when C2/C3 is posted: do **not** seed ``active_media_json``.

    Live-Class Designer: C2/C3 are text-only (empty ArtifactViewer). C1 peels
    (``reveal_axes`` / L0–L4 / ``reveal_lateral`` / ``allow_3d_limited`` /
    view tools / ``frozen``) live only on the C1 Real-slice blob.

    Args:
        raw: Posted challenge id.
    """
    try:
        return normalize_challenge(raw) in C2_C3_CHALLENGES
    except ValueError:
        return False


def _as_bool(raw: Any) -> bool:
    """Coerce JSON/form booleans."""
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes"}
    return bool(raw)


def _clip_unit(raw: Any, low: float, high: float, default: float) -> float:
    """Clamp a finite number into ``[low, high]``, else ``default``.

    Args:
        raw: Posted number.
        low: Inclusive minimum.
        high: Inclusive maximum.
        default: Value when ``raw`` is missing or not finite.
    """
    if raw is None or raw is False:
        return float(default)
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return float(default)
    if number != number or number in (float("inf"), float("-inf")):
        return float(default)
    return max(low, min(high, number))


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


def _apply_wonder_peel_delight(
    base: dict[str, Any],
    *,
    previous: dict[str, Any] | None,
    caption_given: bool,
    toast_given: bool,
) -> None:
    """Fill toast + caption on rising peel edges (axes, unlock, freeze, CONS).

    Args:
        base: In-progress payload (mutated).
        previous: Public payload before this update.
        caption_given: Teacher posted ``caption`` this request.
        toast_given: Teacher posted ``toast`` this request.
    """
    prev = previous if isinstance(previous, dict) else {}
    axes_on = bool(base.get("reveal_axes")) and not bool(prev.get("reveal_axes"))
    unlock_on = bool(base.get("student_controls_unlocked")) and not bool(
        prev.get("student_controls_unlocked")
    )
    freeze_on = bool(base.get("frozen")) and not bool(prev.get("frozen"))
    prev_cons = str(prev.get("cons_item") or "")
    new_cons = str(base.get("cons_item") or "")
    pack_unlock_on = bool(new_cons) and not prev_cons and new_cons.startswith(
        "C1-CONS-"
    )
    cons1_on = new_cons == "C1-CONS-1" and prev_cons != "C1-CONS-1"
    cons_unlock_on = cons1_on or pack_unlock_on
    cons4_on = new_cons == "C1-CONS-4" and prev_cons != "C1-CONS-4"
    line = ""
    key = str(base.get("toast_key") or "")
    if freeze_on:
        line = TOAST_FREEZE
        key = "freeze"
    elif cons4_on:
        line = TOAST_CONS_4
        key = "cons_4"
    elif cons_unlock_on:
        line = TOAST_CONS_UNLOCK
        key = "cons_unlock"
    elif unlock_on:
        line = TOAST_STUDENT_UNLOCK
        key = "unlock"
    elif axes_on:
        line = TOAST_REVEAL_AXES
        key = "reveal_axes"
    else:
        return
    if not toast_given:
        base["toast"] = line
        base["toast_key"] = key
    if not caption_given:
        base["caption"] = line


def public_active_media_payload(stored: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the student/staff JSON fragment, or ``None`` when media is cleared.

    Lateral peel sets ``chip`` to ``DEFAULT_LIVE_MEDIA_LATERAL_CHIP``; the stored
    ``entry_chip`` and stem stay put. Encore URL is always present on C1; student
    chrome must not show it until ``frozen``. C2/C3 never seed this blob
    (empty URL is treated as cleared, not a challenge stub).

    Args:
        stored: Dict from ``active_media_json``, possibly partial.
    """
    if not stored or not isinstance(stored, dict):
        return None
    try:
        challenge = normalize_challenge(stored.get("challenge"))
    except ValueError:
        challenge = ""
    url = str(stored.get("url") or "").strip()
    if url == DEFAULT_LIVE_MEDIA_URL:
        challenge = "C1"
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
    cons_item = ""
    try:
        cons = get_c1_cons_item(stored.get("cons_item"))
    except ValueError:
        cons = None
    if cons is not None:
        cons_item = str(cons["id"])
    frozen = bool(stored.get("frozen"))
    if not frozen:
        cons_item = ""
    yaw_range = _clip_unit(
        stored.get("student_yaw_range"),
        0.0,
        STUDENT_YAW_RANGE_MAX,
        DEFAULT_STUDENT_YAW_RANGE,
    )
    allow_limited = bool(stored.get("allow_3d_limited")) or yaw_range > 0
    if allow_limited and yaw_range <= 0:
        yaw_range = DEFAULT_LIMITED_YAW_DEG
    if not allow_limited:
        yaw_range = 0.0
    return {
        "url": url,
        "title": str(stored.get("title") or ""),
        "caption": str(stored.get("caption") or ""),
        "stem": str(stored.get("stem") or ""),
        "entry_chip": stored_chip,
        "chip": display_chip,
        "student_controls_unlocked": bool(stored.get("student_controls_unlocked")),
        "param_push": coerce_param_push(stored),
        "param_frozen": coerce_param_frozen(
            stored, push=coerce_param_push(stored)
        ),
        "reveal_axes": bool(stored.get("reveal_axes")),
        "reveal_lateral": reveal_lateral,
        "allow_3d_limited": allow_limited,
        "show_z_axis": bool(stored.get("show_z_axis")),
        "student_zoom": _clip_unit(
            stored.get("student_zoom"), 0.0, STUDENT_ZOOM_MAX, DEFAULT_STUDENT_ZOOM
        ),
        "freeze_zoom": bool(stored.get("freeze_zoom")),
        "surface_transparency": _clip_unit(
            stored.get("surface_transparency"),
            0.0,
            SURFACE_TRANSPARENCY_MAX,
            DEFAULT_SURFACE_TRANSPARENCY,
        ),
        "freeze_surface": bool(stored.get("freeze_surface")),
        "student_yaw_range": yaw_range,
        "freeze_yaw": bool(stored.get("freeze_yaw")),
        "frozen": frozen,
        "encore_url": ENCORE_YOUTUBE_URL,
        "encore_label": ENCORE_LABEL,
        "unlock_flags": flags,
        "answers": answer_list,
        "params": coeffs,
        "challenge": challenge or ("C1" if url == DEFAULT_LIVE_MEDIA_URL else ""),
        "cons_item": cons_item,
        "toast": str(stored.get("toast") or ""),
        "toast_key": str(stored.get("toast_key") or ""),
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
    show_z_axis: Any = _UNSET,
    student_zoom: Any = _UNSET,
    freeze_zoom: Any = _UNSET,
    surface_transparency: Any = _UNSET,
    freeze_surface: Any = _UNSET,
    student_yaw_range: Any = _UNSET,
    freeze_yaw: Any = _UNSET,
    frozen: Any = _UNSET,
    unlock_flags: Any = _UNSET,
    answers: Any = _UNSET,
    params: Any = _UNSET,
    param_push: Any = _UNSET,
    param_frozen: Any = _UNSET,
    challenge: Any = _UNSET,
    cons_item: Any = _UNSET,
    toast: Any = _UNSET,
    toast_key: Any = _UNSET,
    allow_url_swap: bool = True,
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
        show_z_axis: Teacher peel for the z-axis (x/y stay on at entry).
        student_zoom: 0 = paper-locked distance, 10 = full zoom-in.
        freeze_zoom: Lock the student zoom slider.
        surface_transparency: 0 = very faint saddle, 10 = solid.
        freeze_surface: Lock the 3D surface transparency slider.
        student_yaw_range: 0 = fixed camera, 360 = full student yaw (degrees).
        freeze_yaw: Lock the student yaw-range slider.
        frozen: Argue is done; student chrome may offer the click-out encore.
        unlock_flags: Partial L0–L4 flags (delight pass); L0 stays on; L4
            locksteps with ``reveal_lateral``.
        answers: Optional choice list that may change with each reveal.
        params: Optional ``{a,b,c}`` overlay (merged onto current/defaults).
        param_push: Optional ``{a,b,c}`` student-view push flags.
        param_frozen: Optional ``{a,b,c}`` freeze flags (locked until pushed).
        challenge: ``C1`` / ``C2`` / ``C3``. C2/C3 **clear** the blob (do not seed).
        cons_item: Post-freeze CONS-1…5 id, or empty to clear.
        toast: Optional explicit Wonder toast overlay.
        toast_key: Optional toast identity (``reveal_axes`` / ``unlock`` /
            ``freeze`` / ``cons_unlock`` / ``cons_4``).
        allow_url_swap: When False, only the seed Real-slice URL (or clear) is allowed.
        updated_at: ISO timestamp stamped onto the stored object.

    Returns:
        New payload dict, or ``None`` when cleared.

    Raises:
        ValueError: Invalid URL/params, CONS before freeze, or a merge with no
            current media.
    """
    if clear:
        return None
    if challenge is not _UNSET and challenge_clears_active_media(challenge):
        return None
    url_given = url is not _UNSET
    persist_keys = (
        "title",
        "caption",
        "stem",
        "entry_chip",
        "student_controls_unlocked",
        "param_push",
        "param_frozen",
        "reveal_axes",
        "reveal_lateral",
        "allow_3d_limited",
        "show_z_axis",
        "student_zoom",
        "freeze_zoom",
        "surface_transparency",
        "freeze_surface",
        "student_yaw_range",
        "freeze_yaw",
        "frozen",
        "unlock_flags",
        "answers",
        "params",
        "challenge",
        "cons_item",
        "toast",
        "toast_key",
    )
    if url_given:
        normalized = normalize_active_media_url(url)
        if normalized is None:
            return None
        if not allow_url_swap and normalized != DEFAULT_LIVE_MEDIA_URL:
            raise ValueError("Production seed-locks the C1 Real-slice URL.")
        seed = default_seed_media(url=normalized)
        if normalized != DEFAULT_LIVE_MEDIA_URL:
            seed["title"] = ""
            seed["stem"] = ""
            seed["caption"] = ""
            seed["entry_chip"] = ""
            seed["challenge"] = ""
            seed["cons_item"] = ""
            seed["toast"] = ""
            seed["toast_key"] = ""
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
    if student_controls_unlocked is not _UNSET and param_push is _UNSET:
        unlocked = _as_bool(student_controls_unlocked)
        base["param_push"] = {key: unlocked for key in PARAM_KEYS}
    elif param_push is not _UNSET:
        base["param_push"] = coerce_param_push(base, posted=param_push)
    else:
        base["param_push"] = coerce_param_push(base)
    if param_frozen is not _UNSET:
        base["param_frozen"] = coerce_param_frozen(
            base, posted=param_frozen, push=base["param_push"]
        )
    else:
        base["param_frozen"] = coerce_param_frozen(base, push=base["param_push"])
    base["student_controls_unlocked"] = any(base["param_push"].values())
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
    if student_yaw_range is not _UNSET:
        base["student_yaw_range"] = _clip_unit(
            student_yaw_range,
            0.0,
            STUDENT_YAW_RANGE_MAX,
            DEFAULT_STUDENT_YAW_RANGE,
        )
    else:
        base["student_yaw_range"] = _clip_unit(
            base.get("student_yaw_range"),
            0.0,
            STUDENT_YAW_RANGE_MAX,
            DEFAULT_STUDENT_YAW_RANGE,
        )
    if allow_3d_limited is not _UNSET:
        limited = _as_bool(allow_3d_limited)
        base["allow_3d_limited"] = limited
        if limited and base["student_yaw_range"] <= 0:
            base["student_yaw_range"] = DEFAULT_LIMITED_YAW_DEG
        if not limited and student_yaw_range is _UNSET:
            base["student_yaw_range"] = 0.0
    else:
        if student_yaw_range is not _UNSET:
            base["allow_3d_limited"] = base["student_yaw_range"] > 0
        else:
            base["allow_3d_limited"] = bool(base.get("allow_3d_limited"))
            if base["allow_3d_limited"] and base["student_yaw_range"] <= 0:
                base["student_yaw_range"] = DEFAULT_LIMITED_YAW_DEG
            if not base["allow_3d_limited"]:
                base["student_yaw_range"] = 0.0
    if show_z_axis is not _UNSET:
        base["show_z_axis"] = _as_bool(show_z_axis)
    else:
        base["show_z_axis"] = bool(base.get("show_z_axis"))
    if student_zoom is not _UNSET:
        base["student_zoom"] = _clip_unit(
            student_zoom, 0.0, STUDENT_ZOOM_MAX, DEFAULT_STUDENT_ZOOM
        )
    else:
        base["student_zoom"] = _clip_unit(
            base.get("student_zoom"), 0.0, STUDENT_ZOOM_MAX, DEFAULT_STUDENT_ZOOM
        )
    if freeze_zoom is not _UNSET:
        base["freeze_zoom"] = _as_bool(freeze_zoom)
    else:
        base["freeze_zoom"] = bool(base.get("freeze_zoom"))
    if surface_transparency is not _UNSET:
        base["surface_transparency"] = _clip_unit(
            surface_transparency,
            0.0,
            SURFACE_TRANSPARENCY_MAX,
            DEFAULT_SURFACE_TRANSPARENCY,
        )
    else:
        base["surface_transparency"] = _clip_unit(
            base.get("surface_transparency"),
            0.0,
            SURFACE_TRANSPARENCY_MAX,
            DEFAULT_SURFACE_TRANSPARENCY,
        )
    if freeze_surface is not _UNSET:
        base["freeze_surface"] = _as_bool(freeze_surface)
    else:
        base["freeze_surface"] = bool(base.get("freeze_surface"))
    if freeze_yaw is not _UNSET:
        base["freeze_yaw"] = _as_bool(freeze_yaw)
    else:
        base["freeze_yaw"] = bool(base.get("freeze_yaw"))
    if frozen is not _UNSET:
        base["frozen"] = _as_bool(frozen)
    else:
        base["frozen"] = bool(base.get("frozen"))
    if challenge is not _UNSET:
        base["challenge"] = normalize_challenge(challenge)
    elif str(base.get("url") or "") == DEFAULT_LIVE_MEDIA_URL:
        base["challenge"] = "C1"
    else:
        try:
            base["challenge"] = normalize_challenge(base.get("challenge"))
        except ValueError:
            base["challenge"] = ""
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
    if toast is not _UNSET:
        base["toast"] = _clip_text(toast, "toast")
    else:
        base["toast"] = _clip_text(base.get("toast"), "toast")
    if toast_key is not _UNSET:
        base["toast_key"] = _clip_text(toast_key, "toast_key")
    else:
        base["toast_key"] = _clip_text(base.get("toast_key"), "toast_key")
    cons = None
    if cons_item is not _UNSET:
        cons = get_c1_cons_item(cons_item)
        base["cons_item"] = str(cons["id"]) if cons else ""
    else:
        try:
            cons = get_c1_cons_item(base.get("cons_item"))
        except ValueError:
            cons = None
        base["cons_item"] = str(cons["id"]) if cons else ""
    if cons and not bool(base.get("frozen")):
        if cons_item is not _UNSET:
            raise ValueError("Consolidation is available only after freeze.")
        cons = None
        base["cons_item"] = ""
    if cons and not is_c1_real_slice(base):
        raise ValueError("C1 consolidation is only for the Real-slice channel.")
    if not bool(base.get("frozen")):
        base["cons_item"] = ""
    _apply_wonder_peel_delight(
        base,
        previous=current,
        caption_given=caption is not _UNSET,
        toast_given=toast is not _UNSET,
    )
    if updated_at:
        base["updated_at"] = updated_at
    return public_active_media_payload(base)

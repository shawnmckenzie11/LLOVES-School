"""Class B state-machine fills on the active-media channel.

Real-slice is the first fill (existing peel blob). ``jigsawable-class-size``
is the second fill: teacher StateEventBar peels, student ArtifactViewer
sync, thin ``artifact_state`` snapshots. Not a new channel, not a prompt
kind, and not a bank-stem rewrite.
"""

from __future__ import annotations

from typing import Any

ARTIFACT_KIND_STATE_MACHINE = "state_machine"
INTERACTION_KIND_ARTIFACT_STATE = "artifact_state"
REAL_SLICE_FILL_ID = "real-slice"
REAL_SLICE_MEDIA_URL = "/static/live-media/m1c1-c1-real-slice.html"
JIGSAWABLE_FILL_ID = "jigsawable-class-size"
JIGSAWABLE_MEDIA_URL = "/static/live-media/jigsawable-class-size.html"
JIGSAWABLE_CLASS_SIZE = 16
JIGSAWABLE_PROMPT_SLIDE = 940
# LCD owns the live 20–30s clock. This is the teacher-side placeholder.
JIGSAWABLE_PREDICTION_WINDOW_SECONDS = 25
WONDER_CUE_PRIYA = "jigsawable:priya_leaves_pause_15"

# Ordered peels. ``clear`` unmounts and is allowed from any mounted state.
JIGSAWABLE_EVENTS: tuple[str, ...] = (
    "seed_16",
    "show_4x4",
    "priya_leaves_pause_15",
    "reveal_5x3",
    "show_algebra",
    "clear",
)
_ADVANCE_ORDER: tuple[str, ...] = JIGSAWABLE_EVENTS[:-1]

# First fill stays on the Real-slice peel blob. Second fill is this module.
STATE_MACHINE_FILLS: tuple[dict[str, str], ...] = (
    {
        "fill_id": REAL_SLICE_FILL_ID,
        "artifact_kind": ARTIFACT_KIND_STATE_MACHINE,
        "url": REAL_SLICE_MEDIA_URL,
    },
    {
        "fill_id": JIGSAWABLE_FILL_ID,
        "artifact_kind": ARTIFACT_KIND_STATE_MACHINE,
        "url": JIGSAWABLE_MEDIA_URL,
    },
)

_STATE_FIELDS: tuple[str, ...] = (
    "artifact_kind",
    "fill_id",
    "state_event",
    "class_size",
    "g",
    "n",
    "centre",
    "reveal_armed",
    "prediction_hold",
    "prediction_opened_at",
    "wonder_cue",
    "layout",
)


def is_jigsawable_url(url: Any) -> bool:
    """True when ``url`` is the jigsawable class-size visualizer.

    Args:
        url: Active-media path or empty.
    """
    return str(url or "").strip() == JIGSAWABLE_MEDIA_URL


def is_jigsawable_media(media: dict[str, Any] | None) -> bool:
    """True when this active-media blob is the jigsawable fill.

    Args:
        media: Public or stored active-media object.
    """
    if not isinstance(media, dict):
        return False
    if is_jigsawable_url(media.get("url")):
        return True
    return str(media.get("fill_id") or "").strip() == JIGSAWABLE_FILL_ID and str(
        media.get("artifact_kind") or ""
    ).strip() == ARTIFACT_KIND_STATE_MACHINE


def initial_jigsawable_state() -> dict[str, Any]:
    """Return the mounted-but-unpeeled jigsawable state.

    Push mounts the fill. ``seed_16`` is the first teacher peel.
    """
    return {
        "artifact_kind": ARTIFACT_KIND_STATE_MACHINE,
        "fill_id": JIGSAWABLE_FILL_ID,
        "state_event": "",
        "class_size": JIGSAWABLE_CLASS_SIZE,
        "g": None,
        "n": None,
        "centre": None,
        "reveal_armed": False,
        "prediction_hold": False,
        "prediction_opened_at": "",
        "wonder_cue": "",
        "layout": "idle",
    }


def _optional_int(raw: Any) -> int | None:
    """Return an int, or ``None`` when the peel has not revealed that label.

    Args:
        raw: Stored ``g``, ``n``, or ``centre``.
    """
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def jigsawable_state_from(stored: dict[str, Any] | None) -> dict[str, Any]:
    """Copy jigsawable state fields off a media blob, filling defaults.

    Args:
        stored: Active-media object, or ``None`` before the first push.
    """
    base = initial_jigsawable_state()
    if not isinstance(stored, dict):
        return base
    event = str(stored.get("state_event") or "").strip()
    if event in _ADVANCE_ORDER:
        base["state_event"] = event
    base["class_size"] = JIGSAWABLE_CLASS_SIZE
    base["g"] = _optional_int(stored.get("g"))
    base["n"] = _optional_int(stored.get("n"))
    base["centre"] = _optional_int(stored.get("centre"))
    base["reveal_armed"] = bool(stored.get("reveal_armed"))
    base["prediction_hold"] = bool(stored.get("prediction_hold"))
    base["prediction_opened_at"] = str(stored.get("prediction_opened_at") or "")
    cue = str(stored.get("wonder_cue") or "").strip()
    base["wonder_cue"] = cue if cue == WONDER_CUE_PRIYA else ""
    layout = str(stored.get("layout") or "").strip()
    if layout:
        base["layout"] = layout
    return base


def _visual_for(event: str, *, opened_at: str) -> dict[str, Any]:
    """Return labels and layout for one ordered peel.

    Args:
        event: A non-clear jigsawable event id.
        opened_at: ISO timestamp captured when Priya's pause opens.
    """
    state = initial_jigsawable_state()
    state["state_event"] = event
    if event == "seed_16":
        state["layout"] = "unmarked"
        return state
    if event == "show_4x4":
        state.update(
            {
                "g": 4,
                "n": 4,
                "centre": 4,
                "layout": "grid_4x4",
            }
        )
        return state
    if event == "priya_leaves_pause_15":
        state.update(
            {
                "g": 4,
                "n": 4,
                "centre": 4,
                "reveal_armed": False,
                "prediction_hold": True,
                "prediction_opened_at": opened_at,
                "wonder_cue": WONDER_CUE_PRIYA,
                "layout": "pause_15",
            }
        )
        return state
    if event == "reveal_5x3":
        state.update({"g": 5, "n": 3, "centre": 4, "layout": "grid_5x3"})
        return state
    if event == "show_algebra":
        state.update({"g": 5, "n": 3, "centre": 4, "layout": "algebra"})
        return state
    raise ValueError(f"unsupported jigsawable event: {event}")


def _next_event(current: str) -> str | None:
    """Return the only forward peel after ``current``, or ``None`` at the end.

    Args:
        current: Stored ``state_event`` (empty before ``seed_16``).
    """
    if not current:
        return "seed_16"
    if current not in _ADVANCE_ORDER:
        return "seed_16"
    index = _ADVANCE_ORDER.index(current)
    if index + 1 >= len(_ADVANCE_ORDER):
        return None
    return _ADVANCE_ORDER[index + 1]


def coerce_jigsawable_class_size(raw: Any) -> int:
    """Accept the v0 mount size and reject the deferred 25-path.

    Args:
        raw: Posted ``class_size``.

    Returns:
        ``16``.

    Raises:
        ValueError: If the posted size is not 16.
    """
    if raw is None or raw == "":
        return JIGSAWABLE_CLASS_SIZE
    try:
        size = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("class_size must be 16 in v0.") from exc
    if size != JIGSAWABLE_CLASS_SIZE:
        raise ValueError("class_size 25 is out of v0. Use 16.")
    return size


def advance_jigsawable_state(
    stored: dict[str, Any] | None,
    *,
    state_event: str | None = None,
    arm_reveal: bool = False,
    class_size: Any = None,
    now: str = "",
) -> tuple[dict[str, Any] | None, bool]:
    """Apply one teacher peel or the prediction-window arm.

    ``reveal_5x3`` stays held until an earlier request set ``reveal_armed``.
    Arming and revealing in one request does not release the hold. ``clear``
    returns ``None`` so the caller unmounts active media.

    Args:
        stored: Current jigsawable media blob.
        state_event: Peel id, or ``None`` when this request only arms.
        arm_reveal: Teacher release for the prediction window.
        class_size: Optional mount size. v0 allows 16 only.
        now: ISO timestamp for the Priya pause and snapshots.

    Returns:
        ``(None, True)`` when the fill unmounts, otherwise
        ``(state_fields, changed)``.

    Raises:
        ValueError: Unknown event, skipped peel, early reveal, or 25-seed.
    """
    coerce_jigsawable_class_size(class_size)
    current = jigsawable_state_from(stored)
    event = str(state_event or "").strip()
    if event == "seed_25" or event.startswith("seed_25"):
        raise ValueError("seed_25 is out of v0.")
    if event and event not in JIGSAWABLE_EVENTS:
        raise ValueError(f"unsupported jigsawable event: {event}")
    if event == "clear":
        return None, True
    if event and event == current["state_event"]:
        return current, False
    if event == "reveal_5x3" and not current["reveal_armed"]:
        raise ValueError(
            "Hold reveal_5x3 until the prediction window is armed."
        )
    if event:
        expected = _next_event(str(current["state_event"] or ""))
        if event != expected:
            raise ValueError(
                f"jigsawable peels advance one at a time (next is {expected or 'clear'})."
            )
        opened = str(current.get("prediction_opened_at") or "")
        if event == "priya_leaves_pause_15":
            opened = now or opened
        return _visual_for(event, opened_at=opened), True
    if arm_reveal:
        if current["state_event"] != "priya_leaves_pause_15":
            raise ValueError(
                "Arm reveal only during priya_leaves_pause_15."
            )
        if current["reveal_armed"]:
            return current, False
        armed = dict(current)
        armed["reveal_armed"] = True
        return armed, True
    return current, False


def jigsawable_snapshot(
    media: dict[str, Any] | None,
    *,
    event: str,
    ts: str,
) -> dict[str, Any]:
    """Build the thin ``artifact_state`` payload for one teacher peel.

    Args:
        media: Media blob after the peel, or the pre-clear blob for ``clear``.
        event: Peel id, including ``clear``.
        ts: ISO timestamp.

    Returns:
        ``{event, class_size, g, n, centre, ts}``.
    """
    token = str(event or "").strip()
    if token not in JIGSAWABLE_EVENTS:
        raise ValueError(f"unsupported jigsawable event: {token}")
    if token == "clear":
        return {
            "event": "clear",
            "class_size": JIGSAWABLE_CLASS_SIZE,
            "g": None,
            "n": None,
            "centre": None,
            "ts": str(ts or ""),
        }
    state = jigsawable_state_from(media)
    return {
        "event": token,
        "class_size": JIGSAWABLE_CLASS_SIZE,
        "g": state.get("g"),
        "n": state.get("n"),
        "centre": state.get("centre"),
        "ts": str(ts or ""),
    }


def linked_prompt_for(event: Any) -> dict[str, Any] | None:
    """Return the live-packet stage ask for a peel, never a bank stem.

    The page-4 definition share stays on the playlist. These asks are
    armed later on the existing prompt channel.

    Args:
        event: Current ``state_event``.
    """
    token = str(event or "").strip()
    if token == "show_4x4":
        return {
            "kind": "share",
            "alt_kind": "numeric",
            "run_as": "individual",
            "slide_index": JIGSAWABLE_PROMPT_SLIDE,
            "prompt": "Is 16 perfectly jigsawable too? What is the centre?",
            "numeric_prompt": "What is the centre?",
        }
    if token == "priya_leaves_pause_15":
        return {
            "kind": "share",
            "run_as": "individual",
            "slide_index": JIGSAWABLE_PROMPT_SLIDE,
            "prompt": (
                "One student leaves a perfectly jigsawable class. "
                "Can 15 still form a very jigsawable arrangement? "
                "What should g and n be?"
            ),
            "window_seconds": JIGSAWABLE_PREDICTION_WINDOW_SECONDS,
        }
    if token == "reveal_5x3":
        return {
            "kind": "share",
            "run_as": "individual",
            "slide_index": JIGSAWABLE_PROMPT_SLIDE,
            "prompt": (
                "Relative to centre 4, what happened to the two dimensions?"
            ),
        }
    if token == "show_algebra":
        return {
            "kind": "share",
            "run_as": "group",
            "slide_index": JIGSAWABLE_PROMPT_SLIDE,
            "prompt": (
                "A perfectly jigsawable class has x squared students. "
                "One student leaves. Why can the remaining still form "
                "(x-1) and (x+1)? Use the animation as evidence."
            ),
        }
    return None


def public_jigsawable_fields(stored: dict[str, Any] | None) -> dict[str, Any]:
    """Return the student-safe state block for a jigsawable media blob.

    Args:
        stored: Stored active-media object.
    """
    state = jigsawable_state_from(stored)
    state["linked_prompt"] = linked_prompt_for(state.get("state_event"))
    state["state_events"] = list(JIGSAWABLE_EVENTS)
    state["prediction_window_seconds"] = JIGSAWABLE_PREDICTION_WINDOW_SECONDS
    return state


def jigsawable_state_keys() -> tuple[str, ...]:
    """Return media keys that must survive a same-URL merge."""
    return _STATE_FIELDS

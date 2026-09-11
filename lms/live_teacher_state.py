"""Thin LiveTeacherState channel for the staff Run Live Class shell.

Alongside ``active_media`` and live prompts: this object stores stage, layout,
and content-id references only. It does not duplicate media or prompt payloads.
``canvas_ephemeral`` is always true — the whiteboard stub must not persist.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

try:
    from meet_team import (
        CUE_MEET_CLEAR,
        CUE_MEET_OPEN,
        public_meet_chain,
    )
except ImportError:  # ``python3 lms/app.py`` package import
    from lms.meet_team import (
        CUE_MEET_CLEAR,
        CUE_MEET_OPEN,
        public_meet_chain,
    )

STAGES: tuple[str, ...] = ("join", "teams", "meet", "round", "play")
MEET_ACTIONS: tuple[str, ...] = ("next", "skip_c", "clear")
MEET_WONDER_CUES: tuple[str, ...] = (CUE_MEET_OPEN, CUE_MEET_CLEAR)
ROUNDS: tuple[str, ...] = ("minds_on", "action", "consolidation")
TEAMS_MODES: tuple[str, ...] = ("teams", "individual")
TABS: tuple[str, ...] = ("media", "questions", "canvas_slides")
CONTENT_IDS: tuple[str, ...] = ("media", "questions", "canvas_slides")
FRAME_KEYS: tuple[str, ...] = ("A", "B", "C")

LAYOUT_PRESETS: dict[str, dict[str, str]] = {
    "media_full": {"A": "media"},
    "questions_full": {"A": "questions"},
    "media_questions": {"A": "media", "B": "questions"},
    "three_up": {"A": "media", "B": "questions", "C": "canvas_slides"},
    "canvas_media": {"A": "canvas_slides", "B": "media"},
}

DEFAULT_LAYOUT_PRESET = "media_full"


def _now_iso() -> str:
    """UTC timestamp for ``updated_at``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_teacher_state() -> dict[str, Any]:
    """Return a fresh join-stage teacher state.

    Returns:
        Public ``LiveTeacherState`` dict with ``canvas_ephemeral: True``.
    """
    return {
        "stage": "join",
        "round": None,
        "teams_mode": "individual",
        "layout_preset": DEFAULT_LAYOUT_PRESET,
        "frames": dict(LAYOUT_PRESETS[DEFAULT_LAYOUT_PRESET]),
        "active_tab": "media",
        "active_media_ref": None,
        "prompt_ref": None,
        "canvas_ephemeral": True,
        "updated_at": _now_iso(),
        "cue_id": None,
        "meet_chain": None,
    }


def _clean_ref(raw: Any) -> str | None:
    """Return a short id string, or None when empty."""
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _clean_frames(raw: Any) -> dict[str, str]:
    """Keep A/B/C keys that point at known content ids."""
    if not isinstance(raw, dict):
        return {}
    frames: dict[str, str] = {}
    for key in FRAME_KEYS:
        value = raw.get(key)
        if value in CONTENT_IDS:
            frames[key] = str(value)
    return frames


def public_teacher_state(stored: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize a stored blob into the public thin channel.

    Args:
        stored: Dict from ``teacher_state_json``, possibly partial.

    Returns:
        Complete ``LiveTeacherState``. ``canvas_ephemeral`` is always true.
    """
    base = default_teacher_state()
    if not isinstance(stored, dict):
        return base
    stage = stored.get("stage")
    if stage in STAGES:
        base["stage"] = stage
    round_id = stored.get("round")
    if round_id in ROUNDS:
        base["round"] = round_id
    elif round_id in (None, ""):
        base["round"] = None
    mode = stored.get("teams_mode")
    if mode in TEAMS_MODES:
        base["teams_mode"] = mode
    preset = stored.get("layout_preset")
    if preset in LAYOUT_PRESETS:
        base["layout_preset"] = preset
    frames = _clean_frames(stored.get("frames"))
    if frames:
        base["frames"] = frames
    elif preset in LAYOUT_PRESETS:
        base["frames"] = dict(LAYOUT_PRESETS[preset])
    tab = stored.get("active_tab")
    if tab in TABS:
        base["active_tab"] = tab
    base["active_media_ref"] = _clean_ref(stored.get("active_media_ref"))
    base["prompt_ref"] = _clean_ref(stored.get("prompt_ref"))
    base["canvas_ephemeral"] = True
    stamp = stored.get("updated_at")
    if isinstance(stamp, str) and stamp.strip():
        base["updated_at"] = stamp.strip()
    cue = stored.get("cue_id")
    if cue in (None, ""):
        base["cue_id"] = None
    else:
        base["cue_id"] = str(cue).strip() or None
    base["meet_chain"] = public_meet_chain(stored.get("meet_chain"))
    return base


def adjacent_stage(stage: str, delta: int) -> str:
    """Step one stage forward or back without wrapping.

    Args:
        stage: Current stage id.
        delta: ``1`` for next, ``-1`` for prev.

    Returns:
        A stage in ``STAGES``.
    """
    current = stage if stage in STAGES else "join"
    index = STAGES.index(current)
    nxt = max(0, min(len(STAGES) - 1, index + int(delta)))
    return STAGES[nxt]


def apply_teacher_state_update(
    current: dict[str, Any] | None,
    *,
    advance: Any = None,
    stage: Any = None,
    round: Any = None,
    teams_mode: Any = None,
    layout_preset: Any = None,
    frames: Any = None,
    active_tab: Any = None,
    active_media_ref: Any = None,
    prompt_ref: Any = None,
    cue_id: Any = None,
    meet_chain: Any = None,
    canvas_ephemeral: Any = None,
) -> dict[str, Any]:
    """Patch the thin teacher channel. Never persists canvas pixels.

    Args:
        current: Existing public or stored state.
        advance: ``next`` / ``prev`` to move ``stage`` only.
        stage: Explicit stage id.
        round: Pedagogical round, or empty to clear.
        teams_mode: ``teams`` or ``individual``.
        layout_preset: Named preset; fills frames unless ``frames`` is set.
        frames: ``{A,B,C}`` content-id map.
        active_tab: Active Content tab.
        active_media_ref: Id pointing at existing ``active_media`` (not a copy).
        prompt_ref: Id pointing at an existing prompt (not a copy).
        cue_id: Optional one-beat Wonder cue id, or empty to clear.
        meet_chain: Optional ephemeral MeetChainState, or empty to clear.
        canvas_ephemeral: Ignored; the field stays ``True``.

    Returns:
        Updated public state.

    Raises:
        ValueError: Unknown stage, tab, preset, teams mode, or advance token.
    """
    del canvas_ephemeral  # always true; callers cannot persist the stub
    base = public_teacher_state(current)
    if advance is not None:
        token = str(advance).strip().lower()
        if token not in {"next", "prev", "previous", "back"}:
            raise ValueError("advance must be next or prev")
        delta = 1 if token == "next" else -1
        base["stage"] = adjacent_stage(str(base["stage"]), delta)
    if stage is not None:
        name = str(stage).strip()
        if name not in STAGES:
            raise ValueError(f"unknown stage: {name}")
        base["stage"] = name
    if round is not None:
        if round in (None, ""):
            base["round"] = None
        else:
            name = str(round).strip()
            if name not in ROUNDS:
                raise ValueError(f"unknown round: {name}")
            base["round"] = name
    if teams_mode is not None:
        mode = str(teams_mode).strip()
        if mode not in TEAMS_MODES:
            raise ValueError(f"unknown teams_mode: {mode}")
        base["teams_mode"] = mode
    preset_applied = False
    if layout_preset is not None:
        preset = str(layout_preset).strip()
        if preset not in LAYOUT_PRESETS:
            raise ValueError(f"unknown layout_preset: {preset}")
        base["layout_preset"] = preset
        if frames is None:
            base["frames"] = dict(LAYOUT_PRESETS[preset])
            preset_applied = True
    if frames is not None and not preset_applied:
        cleaned = _clean_frames(frames)
        if not cleaned:
            raise ValueError("frames must map A/B/C to media, questions, or canvas_slides")
        base["frames"] = cleaned
    if active_tab is not None:
        tab = str(active_tab).strip()
        if tab not in TABS:
            raise ValueError(f"unknown active_tab: {tab}")
        base["active_tab"] = tab
    if active_media_ref is not None:
        base["active_media_ref"] = _clean_ref(active_media_ref)
    if prompt_ref is not None:
        base["prompt_ref"] = _clean_ref(prompt_ref)
    if cue_id is not None:
        base["cue_id"] = _clean_ref(cue_id)
    if meet_chain is not None:
        if meet_chain in (None, "", {}, False):
            base["meet_chain"] = None
        else:
            base["meet_chain"] = public_meet_chain(meet_chain)
    base["canvas_ephemeral"] = True
    base["updated_at"] = _now_iso()
    return base

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
        MEET_TEAM_ITEM_ID,
        meet_prompt_ref_for,
        public_meet_chain,
    )
except ImportError:  # ``python3 lms/app.py`` package import
    from lms.meet_team import (
        CUE_MEET_CLEAR,
        CUE_MEET_OPEN,
        MEET_TEAM_ITEM_ID,
        meet_prompt_ref_for,
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
STUDENT_FRAME_KEYS: tuple[str, ...] = ("questions", "media", "canvas")
UNLOCK_KEYS: tuple[str, ...] = ("media", "canvas")
MINDS_ON_PROMPT_REF = "minds_on"
LIVE_SLOTS: tuple[str, ...] = ("C1", "C2", "C3")
DEFAULT_LIVE_SLOT = "C1"
CUE_FREEZE = "cue.freeze"
CUE_CONS_UNLOCK = "cue.cons_unlock"
TEXT_RIDE_CUES: tuple[str, ...] = (CUE_FREEZE, CUE_CONS_UNLOCK)

LAYOUT_PRESETS: dict[str, dict[str, str]] = {
    "media_full": {"A": "media"},
    "questions_full": {"A": "questions"},
    "media_questions": {"A": "media", "B": "questions"},
    "three_up": {"A": "media", "B": "questions", "C": "canvas_slides"},
    "canvas_media": {"A": "canvas_slides", "B": "media"},
}

DEFAULT_LAYOUT_PRESET = "questions_full"
QUESTION_ONLY_STAGES: frozenset[str] = frozenset({"join", "teams", "meet", "round"})


def _now_iso() -> str:
    """UTC timestamp for ``updated_at``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_student_frames(stage: str | None = None) -> dict[str, bool]:
    """Student frame visibility for one pedagogical stage.

    JOIN / TEAMS / MEET / ROUND project Question only. PLAY shows all
    three frames; media and canvas stay locked until ``unlocks``.

    Args:
        stage: Stage id, or None for JOIN defaults.

    Returns:
        ``{questions, media, canvas}`` booleans.
    """
    name = stage if stage in STAGES else "join"
    if name == "play":
        return {"questions": True, "media": True, "canvas": True}
    return {"questions": True, "media": False, "canvas": False}


def default_unlocks() -> dict[str, bool]:
    """Return locked media/canvas unlock flags."""
    return {"media": False, "canvas": False}


def default_text_ride() -> dict[str, Any]:
    """C2/C3 text-only freeze + CONS ride (never ``active_media_json``)."""
    return {
        "frozen": False,
        "cons_item": "",
        "toast": "",
        "toast_key": "",
    }


def default_teacher_state() -> dict[str, Any]:
    """Return a fresh join-stage teacher state.

    Returns:
        Public ``LiveTeacherState`` dict with ``canvas_ephemeral: True``.
        JOIN focuses Questions and Minds-On; students do not get media.
        ``live_slot`` defaults to C1; C2/C3 use ``text_ride`` instead of media.
    """
    return {
        "stage": "join",
        "round": None,
        "teams_mode": "individual",
        "layout_preset": DEFAULT_LAYOUT_PRESET,
        "frames": dict(LAYOUT_PRESETS[DEFAULT_LAYOUT_PRESET]),
        "active_tab": "questions",
        "active_media_ref": None,
        "prompt_ref": MINDS_ON_PROMPT_REF,
        "canvas_ephemeral": True,
        "updated_at": _now_iso(),
        "cue_id": None,
        "meet_chain": None,
        "state_seq": 0,
        "student_frames": default_student_frames("join"),
        "unlocks": default_unlocks(),
        "live_slot": DEFAULT_LIVE_SLOT,
        "text_ride": default_text_ride(),
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


def _as_bool(raw: Any) -> bool | None:
    """Parse a JSON-ish bool, or None when the value is missing."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)) and raw in (0, 1):
        return bool(raw)
    if isinstance(raw, str):
        token = raw.strip().lower()
        if token in {"1", "true", "yes", "on"}:
            return True
        if token in {"0", "false", "no", "off"}:
            return False
    return None


def _clean_student_frames(raw: Any, *, stage: str | None = None) -> dict[str, bool]:
    """Keep known student-frame keys as booleans."""
    base = default_student_frames(stage)
    if not isinstance(raw, dict):
        return base
    for key in STUDENT_FRAME_KEYS:
        parsed = _as_bool(raw.get(key))
        if parsed is not None:
            base[key] = parsed
    return base


def _clean_unlocks(raw: Any) -> dict[str, bool]:
    """Keep media/canvas unlock flags as booleans."""
    base = default_unlocks()
    if not isinstance(raw, dict):
        return base
    for key in UNLOCK_KEYS:
        parsed = _as_bool(raw.get(key))
        if parsed is not None:
            base[key] = parsed
    return base


def normalize_live_slot(raw: Any) -> str:
    """Return ``C1``, ``C2``, or ``C3``. Empty/unknown falls back to C1.

    Args:
        raw: Posted or stored slot / challenge id.
    """
    text = str(raw or "").strip().upper()
    if text in LIVE_SLOTS:
        return text
    return DEFAULT_LIVE_SLOT


def public_text_ride(raw: Any) -> dict[str, Any]:
    """Normalize the C2/C3 text-only ride blob.

    Args:
        raw: Stored ``text_ride`` object, or empty.
    """
    base = default_text_ride()
    if not isinstance(raw, dict):
        return base
    frozen = _as_bool(raw.get("frozen"))
    if frozen is not None:
        base["frozen"] = frozen
    cons = str(raw.get("cons_item") or "").strip()
    if not base["frozen"]:
        cons = ""
    base["cons_item"] = cons
    base["toast"] = str(raw.get("toast") or "").strip()
    base["toast_key"] = str(raw.get("toast_key") or "").strip()
    return base


def _clean_state_seq(raw: Any) -> int:
    """Return a non-negative integer sequence, defaulting to 0."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(0, value)


def public_mc_ui(raw: Any, *, prompt_ref: str | None = None) -> dict[str, Any] | None:
    """Normalize additive MC reveal chrome, or None when cleared / absent.

    Student mirror stays off unless ``reveal_to_students`` is explicitly
    true. Wonder cues are not part of this object.

    Args:
        raw: Stored or PATCH ``mc_ui`` object, or empty to clear.
        prompt_ref: Fallback id when the blob omits ``prompt_ref``.

    Returns:
        ``{prompt_ref, reveal, reveal_to_students}`` or ``None``.

    Raises:
        ValueError: When ``raw`` is present but not an object / has no ref.
    """
    if raw in (None, "", False, {}):
        return None
    if not isinstance(raw, dict):
        raise ValueError("mc_ui must be an object")
    ref = _clean_ref(raw.get("prompt_ref")) or _clean_ref(prompt_ref)
    if not ref:
        raise ValueError("mc_ui.prompt_ref is required")
    reveal = _as_bool(raw.get("reveal"))
    to_students = _as_bool(raw.get("reveal_to_students"))
    return {
        "prompt_ref": ref,
        "reveal": bool(reveal) if reveal is not None else False,
        "reveal_to_students": bool(to_students) if to_students is not None else False,
    }


def bind_meet_student_projection(state: dict[str, Any]) -> dict[str, Any]:
    """Bind Meet ``prompt_ref`` + ``meet_chain`` onto the student Question frame.

    MEET is Question-only. Students must see the visible chain step, not a
    leftover Minds-On / scoring wait. Teacher Active Content stays mounted.

    Args:
        state: In-progress public teacher state (mutated).

    Returns:
        The same ``state`` dict.
    """
    frames = dict(state.get("student_frames") or default_student_frames("meet"))
    frames["questions"] = True
    frames["media"] = False
    frames["canvas"] = False
    state["student_frames"] = frames
    state["active_tab"] = "questions"
    state["layout_preset"] = "questions_full"
    state["frames"] = dict(LAYOUT_PRESETS["questions_full"])
    chain = public_meet_chain(state.get("meet_chain"))
    if chain is not None:
        state["meet_chain"] = chain
        state["prompt_ref"] = meet_prompt_ref_for(chain)
    else:
        state["prompt_ref"] = MEET_TEAM_ITEM_ID
    return state


def apply_stage_projection(state: dict[str, Any], stage: str) -> dict[str, Any]:
    """Fill student frames / JOIN focus for a newly entered stage.

    Teacher Active Content stays mounted; this only swaps thin refs and
    the student projection map. PLAY reveals all three frames locked.
    MEET binds ``prompt_ref`` + Question-only frames so the chain is
    not teacher-only.

    Args:
        state: In-progress public teacher state (mutated).
        stage: Stage just entered.

    Returns:
        The same ``state`` dict.
    """
    name = stage if stage in STAGES else "join"
    state["student_frames"] = default_student_frames(name)
    if name in QUESTION_ONLY_STAGES:
        state["active_tab"] = "questions"
        if name == "join":
            state["layout_preset"] = "questions_full"
            state["frames"] = dict(LAYOUT_PRESETS["questions_full"])
            state["prompt_ref"] = MINDS_ON_PROMPT_REF
        elif name == "teams":
            state["prompt_ref"] = MINDS_ON_PROMPT_REF
        elif name == "meet":
            bind_meet_student_projection(state)
    return state


def student_should_mount_media(state: dict[str, Any] | None) -> bool:
    """True when the student Real-slice iframe may be mounted.

    JOIN / TEAMS / MEET keep media unmounted even if the teacher preview
    blob exists. PLAY mounts the frame; ``unlocks.media`` only unlocks
    controls.

    Args:
        state: Public teacher state.

    Returns:
        Whether the student media iframe should have a ``src``.
    """
    public = public_teacher_state(state if isinstance(state, dict) else None)
    frames = public.get("student_frames") or default_student_frames(public.get("stage"))
    return bool(frames.get("media"))


def student_should_mount_canvas(state: dict[str, Any] | None) -> bool:
    """True when the student canvas frame may be shown.

    Args:
        state: Public teacher state.

    Returns:
        Whether the student canvas pane is visible.
    """
    public = public_teacher_state(state if isinstance(state, dict) else None)
    frames = public.get("student_frames") or default_student_frames(public.get("stage"))
    return bool(frames.get("canvas"))


def public_teacher_state(stored: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize a stored blob into the public thin channel.

    Args:
        stored: Dict from ``teacher_state_json``, possibly partial.

    Returns:
        Complete ``LiveTeacherState``. ``canvas_ephemeral`` is always true.
        Does not increment ``state_seq``.
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
    prompt = stored.get("prompt_ref")
    if "prompt_ref" in stored:
        base["prompt_ref"] = _clean_ref(prompt)
    elif base["stage"] == "join":
        base["prompt_ref"] = MINDS_ON_PROMPT_REF
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
    if "state_seq" in stored:
        base["state_seq"] = _clean_state_seq(stored.get("state_seq"))
    if "student_frames" in stored:
        base["student_frames"] = _clean_student_frames(
            stored.get("student_frames"), stage=base["stage"]
        )
    else:
        base["student_frames"] = default_student_frames(base["stage"])
    if "unlocks" in stored:
        base["unlocks"] = _clean_unlocks(stored.get("unlocks"))
    if "mc_ui" in stored:
        try:
            cleaned = public_mc_ui(stored.get("mc_ui"), prompt_ref=base.get("prompt_ref"))
        except ValueError:
            cleaned = None
        if cleaned is None:
            base.pop("mc_ui", None)
        else:
            base["mc_ui"] = cleaned
    if "live_slot" in stored:
        base["live_slot"] = normalize_live_slot(stored.get("live_slot"))
    if "text_ride" in stored:
        base["text_ride"] = public_text_ride(stored.get("text_ride"))
    if base.get("live_slot") == "C1":
        base["text_ride"] = default_text_ride()
    if base["stage"] == "meet":
        bind_meet_student_projection(base)
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
    student_frames: Any = None,
    unlocks: Any = None,
    mc_ui: Any = None,
    live_slot: Any = None,
    text_ride: Any = None,
) -> dict[str, Any]:
    """Patch the thin teacher channel. Never persists canvas pixels.

    Every successful patch increments ``state_seq`` and stamps
    ``updated_at``. Stage pills are not a write path — only ``advance``
    or an explicit ``stage`` field moves the stage.

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
        student_frames: Optional ``{questions, media, canvas}`` projection.
        unlocks: Optional ``{media, canvas}`` PLAY unlock flags.
        mc_ui: Optional ``{prompt_ref, reveal, reveal_to_students}``.
            Reveal toggles bump ``state_seq``. ``reveal_to_students``
            defaults false. Empty clears the blob.
        live_slot: ``C1`` / ``C2`` / ``C3``. C2/C3 stay text-only.
        text_ride: Optional ``{frozen, cons_item, toast, toast_key}`` for
            C2/C3 (never written to ``active_media_json``).

    Returns:
        Updated public state.

    Raises:
        ValueError: Unknown stage, tab, preset, teams mode, or advance token.
    """
    del canvas_ephemeral  # always true; callers cannot persist the stub
    base = public_teacher_state(current)
    prev_stage = str(base.get("stage") or "join")
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
    new_stage = str(base.get("stage") or "join")
    stage_changed = new_stage != prev_stage
    if stage_changed:
        apply_stage_projection(base, new_stage)
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
    if student_frames is not None:
        if not isinstance(student_frames, dict):
            raise ValueError("student_frames must be an object")
        base["student_frames"] = _clean_student_frames(
            student_frames, stage=new_stage
        )
    if unlocks is not None:
        if not isinstance(unlocks, dict):
            raise ValueError("unlocks must be an object")
        merged = dict(base.get("unlocks") or default_unlocks())
        merged.update(unlocks)
        base["unlocks"] = _clean_unlocks(merged)
    if live_slot is not None:
        base["live_slot"] = normalize_live_slot(live_slot)
    if text_ride is not None:
        if text_ride in (None, "", {}, False):
            base["text_ride"] = default_text_ride()
        elif not isinstance(text_ride, dict):
            raise ValueError("text_ride must be an object")
        else:
            merged = dict(base.get("text_ride") or default_text_ride())
            merged.update(text_ride)
            base["text_ride"] = public_text_ride(merged)
    if base.get("live_slot") == "C1":
        base["text_ride"] = default_text_ride()
    if mc_ui is not None:
        cleaned = public_mc_ui(mc_ui, prompt_ref=base.get("prompt_ref"))
        if cleaned is None:
            base.pop("mc_ui", None)
        else:
            base["mc_ui"] = cleaned
    elif prompt_ref is not None or stage_changed:
        existing = base.get("mc_ui")
        if isinstance(existing, dict):
            current_ref = _clean_ref(base.get("prompt_ref"))
            if current_ref and existing.get("prompt_ref") != current_ref:
                base.pop("mc_ui", None)
    base["canvas_ephemeral"] = True
    base["updated_at"] = _now_iso()
    base["state_seq"] = _clean_state_seq(base.get("state_seq")) + 1
    return base

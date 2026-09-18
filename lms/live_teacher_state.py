"""Thin LiveTeacherState channel for the staff Run Live Class shell.

Alongside ``active_media`` and live prompts: this object stores stage, layout,
and content-id references only. It does not duplicate media or prompt payloads.
``canvas_ephemeral`` is always true — the whiteboard stub must not persist.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

try:
    from live_canvas import CANVAS_ALIGNS, DEFAULT_CANVAS_ALIGN
    from meet_team import (
        CUE_MEET_CLEAR,
        CUE_MEET_OPEN,
        MEET_TEAM_ITEM_ID,
        meet_prompt_ref_for,
        public_meet_chain,
    )
    from teams_spark import TEAMS_SPARK_PROMPT_REF
except ImportError:  # ``python3 lms/app.py`` package import
    from lms.live_canvas import CANVAS_ALIGNS, DEFAULT_CANVAS_ALIGN
    from lms.meet_team import (
        CUE_MEET_CLEAR,
        CUE_MEET_OPEN,
        MEET_TEAM_ITEM_ID,
        meet_prompt_ref_for,
        public_meet_chain,
    )
    from lms.teams_spark import TEAMS_SPARK_PROMPT_REF

STAGES: tuple[str, ...] = (
    "join",
    "teams",
    "meet",
    "round",
    "play",
    "round_3",
    "summary",
)
MEET_ACTIONS: tuple[str, ...] = ("next", "skip_c", "clear")
MEET_WONDER_CUES: tuple[str, ...] = (CUE_MEET_OPEN, CUE_MEET_CLEAR)
ROUNDS: tuple[str, ...] = ("minds_on", "action", "consolidation")
TEAMS_MODES: tuple[str, ...] = ("teams", "individual")
TABS: tuple[str, ...] = ("media", "questions", "canvas", "slides")
CONTENT_IDS: tuple[str, ...] = ("media", "questions", "canvas", "slides")
FRAME_KEYS: tuple[str, ...] = ("A", "B", "C")
STUDENT_FRAME_KEYS: tuple[str, ...] = ("questions", "media", "canvas", "slides")
UNLOCK_KEYS: tuple[str, ...] = ("media", "canvas", "slides")
STUDENT_VIEW_KEYS: tuple[str, ...] = ("media", "canvas", "slides", "questions")
VIEW_MODES: tuple[str, ...] = ("none", "student", "team")
QUESTION_STUDENT_STAGES: frozenset[str] = frozenset({"join", "teams", "meet"})
MINDS_ON_PROMPT_REF = "minds_on"
LIVE_SLOTS: tuple[str, ...] = ("C1", "C2", "C3", "C4")
DEFAULT_LIVE_SLOT = "C1"
DEFAULT_LIVE_MODULE = "M1"
CUE_FREEZE = "cue.freeze"
CUE_CONS_UNLOCK = "cue.cons_unlock"
TEXT_RIDE_CUES: tuple[str, ...] = (CUE_FREEZE, CUE_CONS_UNLOCK)

LAYOUT_PRESETS: dict[str, dict[str, str]] = {
    "media_full": {"A": "media"},
    "questions_full": {"A": "questions"},
    "canvas_full": {"A": "canvas"},
    "slides_full": {"A": "slides"},
    "media_questions": {"A": "media", "B": "questions"},
    "three_up": {"A": "media", "B": "questions", "C": "canvas"},
    "canvas_media": {"A": "canvas", "B": "media"},
}

DEFAULT_LAYOUT_PRESET = "questions_full"
QUESTION_ONLY_STAGES: frozenset[str] = frozenset({"join", "teams", "meet", "round"})


def _now_iso() -> str:
    """UTC timestamp for ``updated_at``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_student_view(stage: str | None = None) -> dict[str, str]:
    """Return per-surface student-view modes for one stage.

    Media and canvas start teacher-only. Questions start on the
    student Individual face for JOIN and MEET; every other stage
    keeps Questions frozen to the teacher until the dropdown changes.

    Args:
        stage: Stage id, or None for JOIN defaults.

    Returns:
        ``{media, canvas, questions}`` each ``none`` / ``student`` / ``team``.
    """
    name = stage if stage in STAGES else "join"
    questions = "student" if name in QUESTION_STUDENT_STAGES else "none"
    return {
        "media": "none",
        "canvas": "none",
        "slides": "none",
        "questions": questions,
    }


def normalize_view_mode(raw: Any, *, fallback: str = "none") -> str:
    """Return a known student-view mode.

    Args:
        raw: Posted or stored mode token.
        fallback: Value when ``raw`` is empty or unknown.
    """
    token = str(raw or "").strip().lower()
    if token in VIEW_MODES:
        return token
    if raw is True or token in {"1", "true", "yes", "on"}:
        return "student"
    if raw is False or token in {"0", "false", "no", "off"}:
        return "none"
    return fallback if fallback in VIEW_MODES else "none"


def default_student_frames(stage: str | None = None) -> dict[str, bool]:
    """Student frame visibility for one pedagogical stage.

    Derived from ``student_view``: a surface is visible when its mode
    is not ``none``. JOIN / MEET Questions start visible.

    Args:
        stage: Stage id, or None for JOIN defaults.

    Returns:
        ``{questions, media, canvas}`` booleans.
    """
    view = default_student_view(stage)
    return {
        "questions": view["questions"] != "none",
        "media": view["media"] != "none",
        "canvas": view["canvas"] != "none",
        "slides": view["slides"] != "none",
    }


def default_unlocks() -> dict[str, bool]:
    """Return locked media/canvas/slides unlock flags."""
    return {"media": False, "canvas": False, "slides": False}


def unlocks_from_student_view(view: dict[str, str] | None) -> dict[str, bool]:
    """Derive legacy unlock booleans from student-view modes.

    Args:
        view: ``{media, canvas, questions}`` modes.
    """
    body = view if isinstance(view, dict) else default_student_view()
    return {
        "media": str(body.get("media") or "none") != "none",
        "canvas": str(body.get("canvas") or "none") != "none",
        "slides": str(body.get("slides") or "none") != "none",
    }


def canvas_align_from_view(mode: str | None) -> str:
    """Map a canvas student-view mode onto the canvas-align token.

    Args:
        mode: ``none`` / ``student`` / ``team``.
    """
    token = normalize_view_mode(mode, fallback="none")
    if token == "team":
        return "team"
    if token == "student":
        return "student"
    return "teacher"


def default_round_flags() -> dict[str, bool]:
    """Return inactive Minds-On / Action / Consolidation facets."""
    return {name: False for name in ROUNDS}


def default_text_ride() -> dict[str, Any]:
    """C2/C3 text-only freeze + CONS ride (never ``active_media_json``)."""
    return {
        "frozen": False,
        "cons_item": "",
        "toast": "",
        "toast_key": "",
    }


def default_question_views() -> dict[str, str]:
    """Return an empty per-question student-visibility map."""

    return {}


def default_teacher_state() -> dict[str, Any]:
    """Return a fresh join-stage teacher state.

    Returns:
        Public ``LiveTeacherState`` dict with ``canvas_ephemeral: True``.
        JOIN focuses Questions and Minds-On; students do not get media.
        ``live_slot`` defaults to C1; C2/C3 use ``text_ride`` instead of media.
        Student-view modes default teacher-only except JOIN Questions.
    """
    view = default_student_view("join")
    return {
        "stage": "join",
        "round": None,
        "round_flags": default_round_flags(),
        "teams_mode": "individual",
        "groups_configured": False,
        "run_as_group": False,
        "scoreboard_visible": False,
        "hide_absent": False,
        "layout_preset": DEFAULT_LAYOUT_PRESET,
        "frames": dict(LAYOUT_PRESETS[DEFAULT_LAYOUT_PRESET]),
        "active_tab": "questions",
        "active_media_ref": None,
        "prompt_ref": MINDS_ON_PROMPT_REF,
        "canvas_ephemeral": True,
        "celebrate": False,
        "winner": None,
        "updated_at": _now_iso(),
        "cue_id": None,
        "meet_chain": None,
        "state_seq": 0,
        "student_frames": default_student_frames("join"),
        "student_view": view,
        "unlocks": unlocks_from_student_view(view),
        "canvas_align": canvas_align_from_view(view["canvas"]),
        "live_slot": DEFAULT_LIVE_SLOT,
        "live_module": DEFAULT_LIVE_MODULE,
        "text_ride": default_text_ride(),
        "question_views": default_question_views(),
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


def _clean_student_view(raw: Any, *, stage: str | None = None) -> dict[str, str]:
    """Keep known student-view keys as ``none`` / ``student`` / ``team``.

    Args:
        raw: Posted or stored ``student_view`` object.
        stage: Stage used for missing-key defaults.
    """
    base = default_student_view(stage)
    if not isinstance(raw, dict):
        return base
    for key in STUDENT_VIEW_KEYS:
        if key in raw:
            base[key] = normalize_view_mode(raw.get(key), fallback=base[key])
    base["questions"] = (
        "student" if base.get("questions") == "student" else "none"
    )
    return base


def _clean_question_views(raw: Any) -> dict[str, str]:
    """Keep short question ids mapped to teacher-only or individual view."""

    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, str] = {}
    for key, value in list(raw.items())[:100]:
        question_id = str(key or "").strip()[:120]
        if not question_id:
            continue
        cleaned[question_id] = "student" if value == "student" else "none"
    return cleaned


def student_view_from_legacy(
    *,
    unlocks: dict[str, bool] | None = None,
    canvas_align: str | None = None,
    student_frames: dict[str, bool] | None = None,
    stage: str | None = None,
) -> dict[str, str]:
    """Rebuild student-view modes from older unlock / align flags.

    Args:
        unlocks: Legacy ``{media, canvas}`` booleans.
        canvas_align: Legacy canvas alignment token.
        student_frames: Legacy frame visibility map.
        stage: Stage used when a surface was never stored.
    """
    view = default_student_view(stage)
    flags = unlocks if isinstance(unlocks, dict) else {}
    frames = student_frames if isinstance(student_frames, dict) else {}
    if "media" in flags:
        view["media"] = "student" if flags.get("media") else "none"
    if "canvas" in flags:
        align = str(canvas_align or "").strip().lower()
        if not flags.get("canvas"):
            view["canvas"] = "none"
        elif align == "team":
            view["canvas"] = "team"
        elif align == "teacher":
            view["canvas"] = "none"
        else:
            view["canvas"] = "student"
    if "slides" in flags:
        view["slides"] = "student" if flags.get("slides") else "none"
    if "questions" in frames:
        view["questions"] = "student" if frames.get("questions") else "none"
    return view


def normalize_canvas_align(raw: Any) -> str:
    """Return a known canvas alignment token.

    Args:
        raw: Posted or stored ``canvas_align``.
    """
    token = str(raw or "").strip().lower()
    if token in CANVAS_ALIGNS:
        return token
    return DEFAULT_CANVAS_ALIGN


def apply_unlock_frames(state: dict[str, Any]) -> dict[str, Any]:
    """Project student frames from student-view modes.

    ``none`` collapses the student frame (no empty locked pane). ROUND
    never mounts media/canvas. Mutates ``state`` and keeps legacy
    ``unlocks`` / ``canvas_align`` in sync.

    Args:
        state: In-progress public teacher state.
    """
    stage = str(state.get("stage") or "join")
    view = _clean_student_view(state.get("student_view"), stage=stage)
    if stage == "round":
        view["media"] = "none"
        view["canvas"] = "none"
        view["slides"] = "none"
    state["student_view"] = view
    state["unlocks"] = unlocks_from_student_view(view)
    state["canvas_align"] = canvas_align_from_view(view["canvas"])
    state["student_frames"] = {
        "questions": view["questions"] != "none",
        "media": view["media"] != "none",
        "canvas": view["canvas"] != "none",
        "slides": view["slides"] != "none",
    }
    return state


def _clean_round_flags(raw: Any) -> dict[str, bool]:
    """Keep known pedagogical-round facet flags as booleans.

    Args:
        raw: ``{minds_on, action, consolidation}`` object, or a list of
            active round ids.

    Returns:
        Complete flag map. Unknown keys are dropped.
    """
    base = default_round_flags()
    if isinstance(raw, dict):
        for key in ROUNDS:
            parsed = _as_bool(raw.get(key))
            if parsed is not None:
                base[key] = parsed
        return base
    if isinstance(raw, (list, tuple)):
        selected = {str(item).strip() for item in raw}
        for key in ROUNDS:
            base[key] = key in selected
        return base
    raise ValueError("round_flags must be an object")


def normalize_live_slot(raw: Any) -> str:
    """Return ``C1``, ``C2``, or ``C3``. Empty/unknown falls back to C1.

    Args:
        raw: Posted or stored slot / challenge id.
    """
    text = str(raw or "").strip().upper()
    if text in LIVE_SLOTS:
        return text
    return DEFAULT_LIVE_SLOT


def normalize_live_module(raw: Any) -> str:
    """Return ``M1``, ``M2``, … Unknown values fall back to M1.

    Args:
        raw: Posted or stored module id.
    """
    text = str(raw or "").strip().upper()
    if len(text) >= 2 and text[0] == "M" and text[1:].isdigit():
        return text
    return DEFAULT_LIVE_MODULE


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
    true (JOIN Reveal later commits share via ``bind_join_share_on_reveal``).
    Wonder cues are not part of this object.

    Args:
        raw: Stored or PATCH ``mc_ui`` object, or empty to clear.
        prompt_ref: Fallback id when the blob omits ``prompt_ref``.

    Returns:
        ``{prompt_ref, reveal, reveal_to_students, poll_closed}`` or ``None``.

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
    poll_closed = _as_bool(raw.get("poll_closed"))
    return {
        "prompt_ref": ref,
        "reveal": bool(reveal) if reveal is not None else False,
        "reveal_to_students": bool(to_students) if to_students is not None else False,
        "poll_closed": bool(poll_closed) if poll_closed is not None else False,
    }


def bind_join_share_on_reveal(
    state: dict[str, Any],
    *,
    previous_mc_ui: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """JOIN Reveal commits student share and closes the waiting-room poll.

    Hide unshares the class summary but keeps ``poll_closed`` once set so
    students cannot keep submitting. Other stages leave the posted
    ``reveal_to_students`` flag alone. Wonder is not touched.

    Args:
        state: Public teacher state (mutated).
        previous_mc_ui: Prior ``mc_ui`` so poll-closed sticks across Hide.

    Returns:
        The same ``state`` dict.
    """
    ui = state.get("mc_ui")
    if not isinstance(ui, dict):
        return state
    prior = previous_mc_ui if isinstance(previous_mc_ui, dict) else {}
    if prior.get("poll_closed"):
        ui["poll_closed"] = True
    if str(state.get("stage") or "") == "join" and ui.get("reveal"):
        ui["reveal_to_students"] = True
        ui["poll_closed"] = True
    return state


def mc_poll_closed(teacher_state: Any) -> bool:
    """True when the teacher has closed the current MC poll.

    JOIN Reveal sets ``poll_closed``. Hide keeps it closed. Other stages
    only close when the flag is stored.

    Args:
        teacher_state: Public ``LiveTeacherState`` dict, or None.
    """
    if not isinstance(teacher_state, dict):
        return False
    ui = teacher_state.get("mc_ui")
    if not isinstance(ui, dict):
        return False
    if ui.get("poll_closed"):
        return True
    return bool(
        str(teacher_state.get("stage") or "") == "join"
        and ui.get("reveal")
        and ui.get("reveal_to_students")
    )


def student_mc_summary_visible(teacher_state: Any) -> bool:
    """True when students should see the class MC distribution.

    TEAMS shared spark shares a stay-line, not tally bars / chips.

    Args:
        teacher_state: Public ``LiveTeacherState`` dict, or None.
    """
    if not isinstance(teacher_state, dict):
        return False
    if str(teacher_state.get("stage") or "") == "teams":
        return False
    ui = teacher_state.get("mc_ui")
    if not isinstance(ui, dict):
        return False
    return bool(ui.get("reveal") and ui.get("reveal_to_students"))


def clear_join_prompt_bindings(state: dict[str, Any]) -> dict[str, Any]:
    """Null JOIN Minds-On refs and reveal chrome.

    Used when leaving JOIN without binding another prompt. TEAMS now
    binds the shared spark instead of staying unbound. Does not fire a
    Wonder cue.

    Args:
        state: In-progress public teacher state (mutated).

    Returns:
        The same ``state`` dict.
    """
    state["prompt_ref"] = None
    state.pop("mc_ui", None)
    return state


def bind_teams_spark_prompt(state: dict[str, Any]) -> dict[str, Any]:
    """Bind the TEAMS shared spark onto the Question frame.

    Drops leftover JOIN Minds-On ``mc_ui`` so Reveal chrome does not
    leak. Fresh spark ``mc_ui`` starts unrevealed. Does not fire a
    Wonder cue (enter-only cue is written by the session writer).

    Args:
        state: In-progress public teacher state (mutated).

    Returns:
        The same ``state`` dict.
    """
    state["prompt_ref"] = TEAMS_SPARK_PROMPT_REF
    existing = state.get("mc_ui")
    same = (
        isinstance(existing, dict)
        and str(existing.get("prompt_ref") or "") == TEAMS_SPARK_PROMPT_REF
    )
    if not same:
        state["mc_ui"] = {
            "prompt_ref": TEAMS_SPARK_PROMPT_REF,
            "reveal": False,
            "reveal_to_students": False,
            "poll_closed": False,
        }
    return state


def bind_meet_student_projection(state: dict[str, Any]) -> dict[str, Any]:
    """Bind Meet ``prompt_ref`` + ``meet_chain`` onto the student Question frame.

    MEET is Question-only. Students must see the visible chain step, not a
    leftover Minds-On / scoring wait. Teacher Active Content stays mounted.

    Args:
        state: In-progress public teacher state (mutated).

    Returns:
        The same ``state`` dict.
    """
    view = dict(state.get("student_view") or default_student_view("meet"))
    if str(view.get("questions") or "none") == "none":
        view["questions"] = "student"
    state["student_view"] = view
    apply_unlock_frames(state)
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
    the student projection map. PLAY still needs Media/Canvas unlocks.
    TEAMS binds the shared-spark ``prompt_ref`` (Question stays
    visible; JOIN Minds-On ``mc_ui`` is dropped). MEET binds
    ``prompt_ref`` + Question-only frames so the chain is not
    teacher-only.

    Args:
        state: In-progress public teacher state (mutated).
        stage: Stage just entered.

    Returns:
        The same ``state`` dict.
    """
    name = stage if stage in STAGES else "join"
    state["student_view"] = default_student_view(name)
    state["question_views"] = default_question_views()
    apply_unlock_frames(state)
    if name in QUESTION_ONLY_STAGES:
        state["active_tab"] = "questions"
        if name == "join":
            state["layout_preset"] = "questions_full"
            state["frames"] = dict(LAYOUT_PRESETS["questions_full"])
            state["prompt_ref"] = MINDS_ON_PROMPT_REF
        elif name == "teams":
            bind_teams_spark_prompt(state)
        elif name == "meet":
            bind_meet_student_projection(state)
    return state


def student_should_mount_media(state: dict[str, Any] | None) -> bool:
    """True when the student Real-slice iframe may be mounted.

    The Media checkbox is the only mount switch. Unchecked collapses
    the student frame instead of leaving an empty locked pane.

    Args:
        state: Public teacher state.

    Returns:
        Whether the student media iframe should have a ``src``.
    """
    public = public_teacher_state(state if isinstance(state, dict) else None)
    if str(public.get("stage") or "") == "round":
        return False
    view = public.get("student_view") or default_student_view(public.get("stage"))
    return str(view.get("media") or "none") != "none"


def student_should_mount_questions(state: dict[str, Any] | None) -> bool:
    """True when the student Question frame may be shown.

    Args:
        state: Public teacher state.

    Returns:
        Whether the student question pane is visible.
    """
    public = public_teacher_state(state if isinstance(state, dict) else None)
    view = public.get("student_view") or default_student_view(public.get("stage"))
    return str(view.get("questions") or "none") != "none"


def student_view_mode(state: dict[str, Any] | None, surface: str) -> str:
    """Return ``none`` / ``student`` / ``team`` for one student surface.

    Args:
        state: Public teacher state.
        surface: ``media``, ``canvas``, or ``questions``.
    """
    public = public_teacher_state(state if isinstance(state, dict) else None)
    view = public.get("student_view") or default_student_view(public.get("stage"))
    key = str(surface or "").strip().lower()
    if key not in STUDENT_VIEW_KEYS:
        return "none"
    return normalize_view_mode(view.get(key), fallback="none")


def _clean_group_drafts(raw: Any) -> dict[str, dict[str, Any]]:
    """Keep one-response-per-group draft picks.

    Args:
        raw: Stored ``group_drafts`` map keyed ``prompt_id:team_id``.
    """
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, dict[str, Any]] = {}
    for key, row in raw.items():
        token = str(key or "").strip()
        if not token or not isinstance(row, dict):
            continue
        choice = str(row.get("choice") or "").strip()
        if not choice:
            continue
        by_raw = row.get("by")
        try:
            by = int(by_raw) if by_raw not in (None, "") else None
        except (TypeError, ValueError):
            by = None
        cleaned[token] = {"choice": choice[:240], "by": by}
    return cleaned


def student_should_mount_canvas(state: dict[str, Any] | None) -> bool:
    """True when the student canvas frame may be shown.

    Args:
        state: Public teacher state.

    Returns:
        Whether the student canvas pane is visible.
    """
    public = public_teacher_state(state if isinstance(state, dict) else None)
    if str(public.get("stage") or "") == "round":
        return False
    view = public.get("student_view") or default_student_view(public.get("stage"))
    return str(view.get("canvas") or "none") != "none"


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
    for key in (
        "groups_configured",
        "run_as_group",
        "scoreboard_visible",
        "hide_absent",
    ):
        parsed = _as_bool(stored.get(key))
        if parsed is not None:
            base[key] = parsed
    if not base["groups_configured"]:
        base["run_as_group"] = False
        base["scoreboard_visible"] = False
    base["teams_mode"] = "teams" if base["run_as_group"] else "individual"
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
    elif base["stage"] == "teams":
        base["prompt_ref"] = TEAMS_SPARK_PROMPT_REF
    base["canvas_ephemeral"] = True
    base["celebrate"] = bool(stored.get("celebrate"))
    raw_winner = stored.get("winner")
    if isinstance(raw_winner, dict):
        winner_name = str(raw_winner.get("name") or "").strip()
        base["winner"] = (
            {"name": winner_name[:80], "score": raw_winner.get("score")}
            if winner_name
            else None
        )
    else:
        base["winner"] = None
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
    if "student_view" in stored:
        base["student_view"] = _clean_student_view(
            stored.get("student_view"), stage=base["stage"]
        )
    else:
        base["student_view"] = student_view_from_legacy(
            unlocks=stored.get("unlocks") if "unlocks" in stored else None,
            canvas_align=stored.get("canvas_align")
            if "canvas_align" in stored
            else None,
            student_frames=stored.get("student_frames")
            if "student_frames" in stored
            else None,
            stage=base["stage"],
        )
    if "student_frames" in stored:
        base["student_frames"] = _clean_student_frames(
            stored.get("student_frames"), stage=base["stage"]
        )
    else:
        base["student_frames"] = default_student_frames(base["stage"])
    if "unlocks" in stored:
        base["unlocks"] = _clean_unlocks(stored.get("unlocks"))
    if "canvas_align" in stored:
        base["canvas_align"] = normalize_canvas_align(stored.get("canvas_align"))
    if "round_flags" in stored:
        try:
            base["round_flags"] = _clean_round_flags(stored.get("round_flags"))
        except ValueError:
            base["round_flags"] = default_round_flags()
    if "mc_ui" in stored:
        try:
            cleaned = public_mc_ui(stored.get("mc_ui"), prompt_ref=base.get("prompt_ref"))
        except ValueError:
            cleaned = None
        if cleaned is None:
            base.pop("mc_ui", None)
        else:
            base["mc_ui"] = cleaned
            bind_join_share_on_reveal(base)
    if "live_slot" in stored:
        base["live_slot"] = normalize_live_slot(stored.get("live_slot"))
    if "live_module" in stored:
        base["live_module"] = normalize_live_module(stored.get("live_module"))
    if "text_ride" in stored:
        base["text_ride"] = public_text_ride(stored.get("text_ride"))
    if base.get("live_slot") == "C1":
        base["text_ride"] = default_text_ride()
    if "question_views" in stored:
        base["question_views"] = _clean_question_views(stored.get("question_views"))
    if base["stage"] == "meet":
        bind_meet_student_projection(base)
    drafts = _clean_group_drafts(stored.get("group_drafts"))
    if drafts:
        base["group_drafts"] = drafts
    apply_unlock_frames(base)
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
    round_flags: Any = None,
    teams_mode: Any = None,
    groups_configured: Any = None,
    run_as_group: Any = None,
    scoreboard_visible: Any = None,
    hide_absent: Any = None,
    layout_preset: Any = None,
    frames: Any = None,
    active_tab: Any = None,
    active_media_ref: Any = None,
    prompt_ref: Any = None,
    cue_id: Any = None,
    meet_chain: Any = None,
    canvas_ephemeral: Any = None,
    student_frames: Any = None,
    student_view: Any = None,
    unlocks: Any = None,
    canvas_align: Any = None,
    mc_ui: Any = None,
    live_slot: Any = None,
    live_module: Any = None,
    text_ride: Any = None,
    question_views: Any = None,
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
        round_flags: ``{minds_on, action, consolidation}`` facet map.
        teams_mode: ``teams`` or ``individual``.
        groups_configured: Whether fixed memberships have been created.
        run_as_group: Session-global group presentation/tracking toggle.
        scoreboard_visible: Session-global student scoreboard toggle.
        hide_absent: Session-global class-list filter; defaults false.
        layout_preset: Named preset; fills frames unless ``frames`` is set.
        frames: ``{A,B,C}`` content-id map.
        active_tab: Active Content tab.
        active_media_ref: Id pointing at existing ``active_media`` (not a copy).
        prompt_ref: Id pointing at an existing prompt (not a copy).
        cue_id: Optional one-beat Wonder cue id, or empty to clear.
        meet_chain: Optional ephemeral MeetChainState, or empty to clear.
        canvas_ephemeral: Ignored; the field stays ``True``.
        student_frames: Optional ``{questions, media, canvas}`` projection.
        student_view: Optional ``{media, canvas, questions}`` modes
            (``none`` / ``student`` / ``team``).
        unlocks: Optional ``{media, canvas}`` flags. Mapped onto
            ``student_view`` when that object is omitted.
        canvas_align: ``teacher`` (frozen), ``student`` (unique), or
            ``team`` (shared within group). Mapped onto canvas mode.
        mc_ui: Optional ``{prompt_ref, reveal, reveal_to_students,
            poll_closed}``. Reveal toggles bump ``state_seq``. JOIN
            Reveal commits ``reveal_to_students`` and closes the poll.
            Empty clears the blob.
        live_slot: ``C1`` / ``C2`` / ``C3`` / ``C4``.
        live_module: ``M1`` / ``M2`` / … Catalogue module (interim).
        text_ride: Optional ``{frozen, cons_item, toast, toast_key}`` for
            C2/C3 (never written to ``active_media_json``).
        question_views: Per-question ``none`` / ``student`` visibility map.

    Returns:
        Updated public state.

    Raises:
        ValueError: Unknown stage, tab, preset, teams mode, or advance token.
    """
    del canvas_ephemeral  # always true; callers cannot persist the stub
    base = public_teacher_state(current)
    prev_mc_ui = (
        dict(base["mc_ui"]) if isinstance(base.get("mc_ui"), dict) else None
    )
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
    if round_flags is not None:
        if round_flags in (None, "", {}, False):
            base["round_flags"] = default_round_flags()
        else:
            base["round_flags"] = _clean_round_flags(round_flags)
    if teams_mode is not None:
        mode = str(teams_mode).strip()
        if mode not in TEAMS_MODES:
            raise ValueError(f"unknown teams_mode: {mode}")
        base["teams_mode"] = mode
        if base.get("groups_configured"):
            base["run_as_group"] = mode == "teams"
    configured_before = bool(base.get("groups_configured"))
    if groups_configured is not None:
        configured = _as_bool(groups_configured)
        if configured is None:
            raise ValueError("groups_configured must be a boolean")
        if configured_before and not configured:
            raise ValueError("groups cannot be unconfigured during a session")
        base["groups_configured"] = configured
        if configured and not configured_before:
            if run_as_group is None:
                base["run_as_group"] = True
            if scoreboard_visible is None:
                base["scoreboard_visible"] = True
    if run_as_group is not None:
        enabled = _as_bool(run_as_group)
        if enabled is None:
            raise ValueError("run_as_group must be a boolean")
        if enabled and not base.get("groups_configured"):
            raise ValueError("set up groups before enabling group mode")
        base["run_as_group"] = enabled
    if scoreboard_visible is not None:
        visible = _as_bool(scoreboard_visible)
        if visible is None:
            raise ValueError("scoreboard_visible must be a boolean")
        if visible and not base.get("groups_configured"):
            raise ValueError("set up groups before showing the scoreboard")
        base["scoreboard_visible"] = visible
    if hide_absent is not None:
        hidden = _as_bool(hide_absent)
        if hidden is None:
            raise ValueError("hide_absent must be a boolean")
        base["hide_absent"] = hidden
    if not base.get("groups_configured"):
        base["run_as_group"] = False
        base["scoreboard_visible"] = False
    base["teams_mode"] = "teams" if base.get("run_as_group") else "individual"
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
            raise ValueError(
                "frames must map A/B/C to media, questions, canvas, or slides"
            )
        base["frames"] = cleaned
    if active_tab is not None:
        tab = str(active_tab).strip()
        if tab not in TABS:
            raise ValueError(f"unknown active_tab: {tab}")
        base["active_tab"] = tab
    if question_views is not None:
        if not isinstance(question_views, dict):
            raise ValueError("question_views must be an object")
        base["question_views"] = _clean_question_views(question_views)
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
    view = _clean_student_view(base.get("student_view"), stage=new_stage)
    if student_view is not None:
        if not isinstance(student_view, dict):
            raise ValueError("student_view must be an object")
        view = _clean_student_view({**view, **student_view}, stage=new_stage)
    if student_frames is not None:
        if not isinstance(student_frames, dict):
            raise ValueError("student_frames must be an object")
        base["student_frames"] = _clean_student_frames(
            student_frames, stage=new_stage
        )
        if "questions" in student_frames and (
            student_view is None or "questions" not in student_view
        ):
            parsed = _as_bool(student_frames.get("questions"))
            if parsed is not None:
                view["questions"] = "student" if parsed else "none"
    if unlocks is not None:
        if not isinstance(unlocks, dict):
            raise ValueError("unlocks must be an object")
        merged = dict(base.get("unlocks") or default_unlocks())
        merged.update(unlocks)
        base["unlocks"] = _clean_unlocks(merged)
        if student_view is None:
            for key in UNLOCK_KEYS:
                if key not in unlocks:
                    continue
                parsed = _as_bool(unlocks.get(key))
                if parsed is None:
                    continue
                if parsed and view.get(key) == "none":
                    view[key] = "student"
                elif not parsed:
                    view[key] = "none"
    if canvas_align is not None:
        base["canvas_align"] = normalize_canvas_align(canvas_align)
        if student_view is None or "canvas" not in (student_view or {}):
            align = normalize_canvas_align(canvas_align)
            if align == "team":
                view["canvas"] = "team"
            elif align == "student":
                view["canvas"] = "student"
            else:
                view["canvas"] = "none"
    base["student_view"] = view
    if live_slot is not None:
        base["live_slot"] = normalize_live_slot(live_slot)
    if live_module is not None:
        base["live_module"] = normalize_live_module(live_module)
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
            bind_join_share_on_reveal(base, previous_mc_ui=prev_mc_ui)
    elif prompt_ref is not None or stage_changed:
        existing = base.get("mc_ui")
        if isinstance(existing, dict):
            current_ref = _clean_ref(base.get("prompt_ref"))
            if not current_ref or existing.get("prompt_ref") != current_ref:
                base.pop("mc_ui", None)
            else:
                bind_join_share_on_reveal(base, previous_mc_ui=prev_mc_ui)
    else:
        bind_join_share_on_reveal(base, previous_mc_ui=prev_mc_ui)
    base["canvas_ephemeral"] = True
    apply_unlock_frames(base)
    base["updated_at"] = _now_iso()
    base["state_seq"] = _clean_state_seq(base.get("state_seq")) + 1
    return base

"""Course-wide Artifact pattern: media buttons that mint connected questions.

An Artifact is a control **inside** live media. Teacher click mints a **new**
Question on the current live-class page (playlist + ``live_session_items``)
so it sits in the normal Questions section. The media snapshot is stored on
that prompt. Student submit grades against the snapshot. Teacher points stay
on existing Responses & Points — this module does not award.

First concrete: MCF3M M1 C2 Transformations (vertex form of ``y = x^2``).
"""

from __future__ import annotations

from typing import Any

ARTIFACT_KIND = "artifact"
ARTIFACT_CHANNEL = "live-prompt"
TRANSFORMATIONS_ARTIFACT_ID = "mcf3m-m1-c2-transformations"
TRANSFORMATIONS_STEM = (
    "Drag sliders to transform the parent function to match "
    "the target (transformed) function."
)
TRANSFORMATIONS_PARENT = {
    "kind": "quadratic",
    "formula": "x^2",
    "label": "f(x)=x²",
    "params": {"a": 1.0, "h": 0.0, "k": 0.0},
}
TRANSFORM_KEYS: tuple[str, ...] = ("a", "h", "k")
TRANSFORM_RANGES: dict[str, tuple[float, float]] = {
    "a": (-5.0, 5.0),
    "h": (-8.0, 8.0),
    "k": (-8.0, 8.0),
}
TARGET_MODES = frozenset({"graph", "equation"})
GRADE_RELATIVE_MARGIN = 0.10
GRADE_ABS_FLOOR = 1.0
LEAD_MATCH = "Matched."
LEAD_MISS = "Not yet — watch the meters."
ARTIFACT_SLIDE_BASE = 800
C2_TRANSFORM_MEDIA_URL = "/static/live-media/m1c2-transforms.html"


def is_artifact_payload(payload: Any) -> bool:
    """True when a live-prompt payload was minted from an Artifact.

    Args:
        payload: Prompt JSON object.
    """
    if not isinstance(payload, dict):
        return False
    kind = str(payload.get("kind") or "").strip().lower()
    if kind == ARTIFACT_KIND:
        return True
    return (
        str(payload.get("artifact_id") or "").strip() == TRANSFORMATIONS_ARTIFACT_ID
    )


def is_transformations_artifact(payload: Any) -> bool:
    """True when the payload is the MCF3M M1 C2 Transformations Artifact.

    Args:
        payload: Prompt JSON object.
    """
    if not is_artifact_payload(payload):
        return False
    return (
        str(payload.get("artifact_id") or "").strip() == TRANSFORMATIONS_ARTIFACT_ID
    )


def normalize_target_mode(raw: Any) -> str:
    """Return ``graph`` or ``equation``.

    Args:
        raw: Posted target presentation.

    Raises:
        ValueError: If ``raw`` is present but not a known mode.
    """
    text = str(raw or "graph").strip().lower()
    if text in {"eq", "formula", "equation"}:
        return "equation"
    if text in {"graph", "curve", "picture"}:
        return "graph"
    if not text:
        return "graph"
    raise ValueError("target_mode must be graph or equation.")


def normalize_transform_params(raw: Any) -> dict[str, float]:
    """Clamp vertex-form ``a``, ``h``, ``k``. ``a`` cannot be 0.

    Args:
        raw: Mapping of parameter names, or ``None`` for the parent.

    Returns:
        Dict with float ``a``, ``h``, ``k``.

    Raises:
        ValueError: If a provided value is not a finite number.
    """
    values = dict(TRANSFORMATIONS_PARENT["params"])
    if raw is None:
        return values
    if not isinstance(raw, dict):
        raise ValueError("snapshot must be an object with a, h, and k.")
    for key, (low, high) in TRANSFORM_RANGES.items():
        if key not in raw:
            continue
        try:
            number = float(raw[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"snapshot.{key} must be a number.") from exc
        if number != number or number in (float("inf"), float("-inf")):
            raise ValueError(f"snapshot.{key} must be a finite number.")
        values[key] = max(low, min(high, number))
    if abs(values["a"]) < 0.05:
        values["a"] = 0.05 if values["a"] >= 0 else -0.05
    return values


def format_vertex_equation(params: dict[str, float]) -> str:
    """Pretty-print ``f(x)=a(x−h)^2+k`` with unicode minus and superscript.

    Args:
        params: Clamped ``{a, h, k}``.
    """
    a = float(params["a"])
    h = float(params["h"])
    k = float(params["k"])

    def _coeff(value: float) -> str:
        if abs(value - 1) < 0.001:
            return ""
        if abs(value + 1) < 0.001:
            return "−"
        text = f"{value:.4f}".rstrip("0").rstrip(".")
        return text.replace("-", "−")

    a_bit = _coeff(a)
    if abs(h) < 0.001:
        core = f"{a_bit}x²"
    else:
        h_abs = f"{abs(h):.4f}".rstrip("0").rstrip(".")
        inner = f"x−{h_abs}" if h > 0 else f"x+{h_abs}"
        inner = inner.replace("-", "−")
        core = f"{a_bit}({inner})²"
    if abs(k) < 0.001:
        tail = ""
    else:
        k_abs = f"{abs(k):.4f}".rstrip("0").rstrip(".")
        tail = f"+{k_abs}" if k > 0 else f"−{k_abs}"
    return f"f(x)={core}{tail}"


def slider_margin(target: float, key: str) -> float:
    """Return the ±10% allowed error for one slider.

    Uses 10% of ``|target|``, with a floor of 1 so a zero (or tiny) target
    is still reachable: ``|student − target| ≤ 0.10 × max(|target|, 1)``.

    Args:
        target: Teacher snapshot value.
        key: ``a``, ``h``, or ``k`` (reserved for range-aware callers).
    """
    _ = key
    return GRADE_RELATIVE_MARGIN * max(abs(float(target)), GRADE_ABS_FLOOR)


def grade_transform_snapshot(
    student: Any,
    target: Any,
    *,
    margin: float = GRADE_RELATIVE_MARGIN,
) -> dict[str, Any]:
    """Auto-check student sliders against the minted target.

    Args:
        student: Posted ``{a, h, k}``.
        target: Stored snapshot ``{a, h, k}``.
        margin: Relative tolerance (default ±10%).

    Returns:
        ``{match, per_key, student, target}``. ``match`` is True when every
        slider is inside its ±10% band.
    """
    _ = margin
    got = normalize_transform_params(student)
    want = normalize_transform_params(target)
    per_key: dict[str, dict[str, float | bool]] = {}
    ok = True
    for key in TRANSFORM_KEYS:
        allowed = slider_margin(want[key], key)
        delta = abs(got[key] - want[key])
        hit = delta <= allowed + 1e-9
        per_key[key] = {
            "student": got[key],
            "target": want[key],
            "delta": delta,
            "allowed": allowed,
            "match": hit,
        }
        if not hit:
            ok = False
    return {
        "match": ok,
        "per_key": per_key,
        "student": got,
        "target": want,
    }


def artifact_feedback_fragment(payload: Any, response: Any) -> dict[str, Any] | None:
    """Student-safe match / miss line for an Artifact submit.

    Args:
        payload: Live-prompt payload (includes teacher snapshot).
        response: Student answer JSON (``params`` or top-level a/h/k).

    Returns:
        ``{text, lead, source, match}`` or ``None`` when this is not an
        Artifact prompt.
    """
    if not is_transformations_artifact(payload):
        return None
    snapshot = (payload or {}).get("snapshot") if isinstance(payload, dict) else None
    posted = response if isinstance(response, dict) else {}
    raw = posted.get("params") if isinstance(posted.get("params"), dict) else posted
    result = grade_transform_snapshot(raw, snapshot)
    match = bool(result["match"])
    lead = LEAD_MATCH if match else LEAD_MISS
    return {
        "text": lead,
        "lead": lead,
        "source": "artifact_grade",
        "match": match,
    }


def transformations_prompt_payload(
    *,
    snapshot: Any,
    target_mode: Any = "graph",
    parent: dict[str, Any] | None = None,
    slide_index: int = ARTIFACT_SLIDE_BASE,
) -> dict[str, Any]:
    """Build the live-prompt payload stored on the current page.

    Args:
        snapshot: Teacher slider values at mint time.
        target_mode: ``graph`` (frozen target curve) or ``equation``.
        parent: Optional parent-function override.
        slide_index: Page index this question is minted onto.

    Returns:
        Staff payload including the snapshot (student GET strips nothing
        the meters need; keys/cement stay unused).
    """
    params = normalize_transform_params(snapshot)
    mode = normalize_target_mode(target_mode)
    parent_fn = dict(parent) if isinstance(parent, dict) else dict(TRANSFORMATIONS_PARENT)
    equation = format_vertex_equation(params)
    return {
        "kind": ARTIFACT_KIND,
        "artifact_id": TRANSFORMATIONS_ARTIFACT_ID,
        "channel": ARTIFACT_CHANNEL,
        "prompt": TRANSFORMATIONS_STEM,
        "stem": TRANSFORMATIONS_STEM,
        "parent": parent_fn,
        "snapshot": params,
        "target_mode": mode,
        "equation": equation,
        "slider_keys": list(TRANSFORM_KEYS),
        "slider_ranges": {key: list(TRANSFORM_RANGES[key]) for key in TRANSFORM_KEYS},
        "media_url": C2_TRANSFORM_MEDIA_URL,
        "slide_index": int(slide_index),
        "ephemeral": False,
        "durable_store": True,
    }


def student_artifact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Student-facing Artifact fields (snapshot kept for live hot/cold meters).

    Args:
        payload: Staff prompt payload.
    """
    mode = normalize_target_mode(payload.get("target_mode"))
    out = {
        "kind": ARTIFACT_KIND,
        "artifact_id": str(payload.get("artifact_id") or TRANSFORMATIONS_ARTIFACT_ID),
        "channel": ARTIFACT_CHANNEL,
        "prompt": TRANSFORMATIONS_STEM,
        "stem": TRANSFORMATIONS_STEM,
        "parent": payload.get("parent") or dict(TRANSFORMATIONS_PARENT),
        "snapshot": normalize_transform_params(payload.get("snapshot")),
        "target_mode": mode,
        "slider_keys": list(payload.get("slider_keys") or TRANSFORM_KEYS),
        "slider_ranges": payload.get("slider_ranges")
        or {key: list(TRANSFORM_RANGES[key]) for key in TRANSFORM_KEYS},
        "media_url": str(payload.get("media_url") or C2_TRANSFORM_MEDIA_URL),
    }
    if mode == "equation":
        out["equation"] = str(
            payload.get("equation")
            or format_vertex_equation(out["snapshot"])
        )
    return out


def format_artifact_answer(response: Any) -> str:
    """Return a compact ``a, h, k`` readout for Responses & Points.

    Args:
        response: Student answer JSON (``params`` or top-level a/h/k).
    """
    posted = response if isinstance(response, dict) else {}
    raw = posted.get("params") if isinstance(posted.get("params"), dict) else posted
    try:
        params = normalize_transform_params(raw)
    except ValueError:
        return ""
    parts = []
    for key in TRANSFORM_KEYS:
        text = f"{params[key]:.4f}".rstrip("0").rstrip(".")
        parts.append(f"{key}={text}")
    return ", ".join(parts)


def public_artifact_media(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Frozen target for the student iframe (disconnected from teacher sliders).

    Args:
        payload: Stored ``artifact`` object on active media, or ``None``.
    """
    if not isinstance(payload, dict):
        return None
    try:
        snapshot = normalize_transform_params(payload.get("snapshot"))
        mode = normalize_target_mode(payload.get("target_mode"))
    except ValueError:
        return None
    return {
        "artifact_id": str(
            payload.get("artifact_id") or TRANSFORMATIONS_ARTIFACT_ID
        ),
        "parent": payload.get("parent") or dict(TRANSFORMATIONS_PARENT),
        "snapshot": snapshot,
        "target_mode": mode,
        "equation": format_vertex_equation(snapshot),
    }


def default_c2_transform_media() -> dict[str, Any]:
    """Return the C2 transform iframe seed (no live teacher-slider leak).

    Teacher sliders stay in the iframe until Artifact mint. Students see
    the parent curve only until a snapshot is frozen onto ``artifact``.
    """
    return {
        "url": C2_TRANSFORM_MEDIA_URL,
        "title": "",
        "caption": "",
        "stem": "",
        "entry_chip": "",
        "student_controls_unlocked": False,
        "param_push": {"a": False, "b": False, "c": False},
        "param_frozen": {"a": True, "b": True, "c": True},
        "reveal_axes": True,
        "reveal_lateral": False,
        "allow_3d_limited": False,
        "show_z_axis": False,
        "student_zoom": 0.0,
        "freeze_zoom": False,
        "surface_transparency": 0.0,
        "freeze_surface": False,
        "student_yaw_range": 0.0,
        "freeze_yaw": False,
        "frozen": False,
        "unlock_flags": {
            "L0": True,
            "L1": False,
            "L2": False,
            "L3": False,
            "L4": False,
        },
        "answers": [],
        "params": {"a": 1.0, "b": 0.0, "c": 0.0},
        "transform": dict(TRANSFORMATIONS_PARENT["params"]),
        "artifact": None,
        "challenge": "C2",
        "cons_item": "",
        "toast": "",
        "toast_key": "",
    }


def live_media_catalog() -> dict[str, dict[str, str]]:
    """Modulified live-media registry keyed by live slot.

    Returns:
        ``{C1, C2}`` rows with ``url`` + ``title``. C3 stays text-only.
    """
    return {
        "C1": {
            "url": "/static/live-media/m1c1-c1-real-slice.html",
            "title": "C1 Real-slice",
        },
        "C2": {
            "url": C2_TRANSFORM_MEDIA_URL,
            "title": "C2 Transformations",
        },
    }

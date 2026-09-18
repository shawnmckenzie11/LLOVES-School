"""Course-wide Artifact pattern: media buttons that mint connected questions.

An Artifact is a control **inside** live media. Teacher click mints a **new**
Question on the current live-class page (playlist + ``live_session_items``)
so it sits in the normal Questions section. The media snapshot is stored on
that prompt. Student submit grades against the snapshot. Teacher points stay
on existing Responses & Points — this module does not award.

First concrete: MCF3M M1 C2 Transformations (vertex form of ``y = x^2``).
MCF3M M1 C3 reuses that same Artifact. MCR3U M1 C3 is the parent-function
variant (radio parent + ``a, k, d, c`` sliders).
"""

from __future__ import annotations

from typing import Any

ARTIFACT_KIND = "artifact"
ARTIFACT_CHANNEL = "live-prompt"
TRANSFORMATIONS_ARTIFACT_ID = "mcf3m-m1-c2-transformations"
PARENT_TRANSFORMATIONS_ARTIFACT_ID = "mcr3u-m1-c3-parent-transformations"
REGISTERED_ARTIFACT_IDS = frozenset(
    {TRANSFORMATIONS_ARTIFACT_ID, PARENT_TRANSFORMATIONS_ARTIFACT_ID}
)
TRANSFORMATIONS_STEM = (
    "Drag sliders to transform the parent function to match "
    "the target (transformed) function."
)
PARENT_TRANSFORMATIONS_STEM = TRANSFORMATIONS_STEM
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
C3_PARENT_MEDIA_URL = "/static/live-media/mcr3u-m1c3-parent-transformations.html"
PARENT_KINDS = frozenset(
    {"linear", "quadratic", "abs", "sqrt", "reciprocal"}
)
PARENT_KIND_LABELS: dict[str, str] = {
    "linear": "f(x)=x",
    "quadratic": "f(x)=x²",
    "abs": "f(x)=|x|",
    "sqrt": "f(x)=√x",
    "reciprocal": "f(x)=1/x",
}
PARENT_KIND_FORMULAS: dict[str, str] = {
    "linear": "x",
    "quadratic": "x^2",
    "abs": "|x|",
    "sqrt": "sqrt(x)",
    "reciprocal": "1/x",
}
PARENT_TRANSFORM_KEYS: tuple[str, ...] = ("a", "k", "d", "c")
PARENT_TRANSFORM_RANGES: dict[str, tuple[float, float]] = {
    "a": (-3.0, 3.0),
    "k": (-3.0, 3.0),
    "d": (-6.0, 6.0),
    "c": (-6.0, 6.0),
}
PARENT_TRANSFORMATIONS_PARENT = {
    "kind": "linear",
    "formula": "x",
    "label": "f(x)=x",
    "params": {"a": 1.0, "k": 1.0, "d": 0.0, "c": 0.0},
}


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
    return str(payload.get("artifact_id") or "").strip() in REGISTERED_ARTIFACT_IDS


def is_transformations_artifact(payload: Any) -> bool:
    """True when the payload is the MCF3M vertex-form Transformations Artifact.

    Used on MCF3M M1 C2 and the C3 copy.

    Args:
        payload: Prompt JSON object.
    """
    if not is_artifact_payload(payload):
        return False
    artifact_id = str(payload.get("artifact_id") or "").strip()
    return artifact_id == TRANSFORMATIONS_ARTIFACT_ID or not artifact_id


def is_parent_transformations_artifact(payload: Any) -> bool:
    """True when the payload is the MCR3U M1 C3 parent-function Artifact.

    Args:
        payload: Prompt JSON object.
    """
    if not is_artifact_payload(payload):
        return False
    return (
        str(payload.get("artifact_id") or "").strip()
        == PARENT_TRANSFORMATIONS_ARTIFACT_ID
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


def normalize_parent_kind(raw: Any) -> str:
    """Return a known parent-function id.

    Args:
        raw: Posted parent kind, mapping with ``kind``, or empty.

    Raises:
        ValueError: If ``raw`` is present but not a known parent.
    """
    if isinstance(raw, dict):
        text = str(raw.get("kind") or raw.get("parent") or "").strip().lower()
    else:
        text = str(raw or "").strip().lower()
    aliases = {
        "absvalue": "abs",
        "absolute": "abs",
        "absolute_value": "abs",
        "square_root": "sqrt",
        "root": "sqrt",
        "line": "linear",
        "quad": "quadratic",
        "parabola": "quadratic",
    }
    text = aliases.get(text, text)
    if not text:
        return "linear"
    if text not in PARENT_KINDS:
        raise ValueError("parent must be linear, quadratic, abs, sqrt, or reciprocal.")
    return text


def parent_function_dict(kind: Any) -> dict[str, Any]:
    """Return the stored parent-function object for one kind.

    Args:
        kind: Parent id or mapping with ``kind``.
    """
    token = normalize_parent_kind(kind)
    return {
        "kind": token,
        "formula": PARENT_KIND_FORMULAS[token],
        "label": PARENT_KIND_LABELS[token],
    }


def normalize_parent_params(raw: Any) -> dict[str, float]:
    """Clamp ``a``, ``k``, ``d``, ``c`` for ``y = a f(k(x − d)) + c``.

    ``a`` and ``k`` cannot be 0.

    Args:
        raw: Mapping of parameter names, or ``None`` for the identity.

    Returns:
        Dict with float ``a``, ``k``, ``d``, ``c``.

    Raises:
        ValueError: If a provided value is not a finite number.
    """
    values = dict(PARENT_TRANSFORMATIONS_PARENT["params"])
    if raw is None:
        return values
    if not isinstance(raw, dict):
        raise ValueError("snapshot must be an object with a, k, d, and c.")
    for key, (low, high) in PARENT_TRANSFORM_RANGES.items():
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
    if abs(values["k"]) < 0.05:
        values["k"] = 0.05 if values["k"] >= 0 else -0.05
    return values


def format_parent_equation(kind: Any, params: dict[str, float]) -> str:
    """Pretty-print ``y = a · parent(k(x − d)) + c``.

    Args:
        kind: Parent function id.
        params: Clamped ``{a, k, d, c}``.
    """
    token = normalize_parent_kind(kind)
    a = float(params["a"])
    k = float(params["k"])
    d = float(params["d"])
    c = float(params["c"])

    def _num(value: float) -> str:
        text = f"{value:.4f}".rstrip("0").rstrip(".")
        return text.replace("-", "−")

    inner = "k(x−d)" if abs(d) > 0.001 else "kx"
    if token == "quadratic":
        body = f"({inner})²"
    elif token == "abs":
        body = f"|{inner}|"
    elif token == "sqrt":
        body = f"√({inner})"
    elif token == "reciprocal":
        body = f"1/({inner})"
    else:
        body = inner
    a_bit = "" if abs(a - 1) < 0.001 else ("−" if abs(a + 1) < 0.001 else _num(a))
    core = f"{a_bit}{body}" if a_bit else body
    if a_bit and a_bit not in {"−"}:
        core = f"{a_bit}·{body}"
    tail = ""
    if abs(c) > 0.001:
        tail = f"+{_num(abs(c))}" if c > 0 else f"−{_num(abs(c))}"
    return (
        f"y={core}{tail} · a={_num(a)} k={_num(k)} d={_num(d)} c={_num(c)}"
    )


def grade_parent_snapshot(
    student: Any,
    target: Any,
    *,
    parent: Any = None,
    target_parent: Any = None,
) -> dict[str, Any]:
    """Auto-check parent radio plus ``a, k, d, c`` sliders.

    Args:
        student: Posted ``{a, k, d, c}`` and optional ``parent``.
        target: Stored snapshot ``{a, k, d, c}``.
        parent: Posted parent kind when not inside ``student``.
        target_parent: Teacher parent kind.

    Returns:
        ``{match, parent_match, per_key, student, target, parent}``.
    """
    posted = student if isinstance(student, dict) else {}
    want_parent = normalize_parent_kind(
        target_parent
        if target_parent is not None
        else (target.get("parent") if isinstance(target, dict) else None)
    )
    got_parent = normalize_parent_kind(
        parent
        if parent is not None
        else posted.get("parent") or posted.get("kind")
    )
    parent_match = got_parent == want_parent
    got = normalize_parent_params(posted)
    want = normalize_parent_params(target)
    per_key: dict[str, dict[str, float | bool]] = {}
    ok = parent_match
    for key in PARENT_TRANSFORM_KEYS:
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
        "parent_match": parent_match,
        "per_key": per_key,
        "student": got,
        "target": want,
        "parent": got_parent,
        "target_parent": want_parent,
    }


def artifact_feedback_fragment(payload: Any, response: Any) -> dict[str, Any] | None:
    """Student-safe match / miss line for an Artifact submit.

    Args:
        payload: Live-prompt payload (includes teacher snapshot).
        response: Student answer JSON (``params`` or top-level sliders).

    Returns:
        ``{text, lead, source, match}`` or ``None`` when this is not an
        Artifact prompt.
    """
    if not is_artifact_payload(payload):
        return None
    snapshot = (payload or {}).get("snapshot") if isinstance(payload, dict) else None
    posted = response if isinstance(response, dict) else {}
    raw = posted.get("params") if isinstance(posted.get("params"), dict) else posted
    if is_parent_transformations_artifact(payload):
        parent = None
        if isinstance(payload, dict):
            parent = (payload.get("parent") or {}).get("kind") if isinstance(
                payload.get("parent"), dict
            ) else payload.get("parent")
        result = grade_parent_snapshot(
            raw,
            snapshot,
            target_parent=parent,
        )
    elif is_transformations_artifact(payload):
        result = grade_transform_snapshot(raw, snapshot)
    else:
        return None
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


def parent_transformations_prompt_payload(
    *,
    snapshot: Any,
    target_mode: Any = "graph",
    parent: dict[str, Any] | str | None = None,
    slide_index: int = ARTIFACT_SLIDE_BASE,
) -> dict[str, Any]:
    """Build the MCR3U M1 C3 parent-function Artifact prompt.

    Args:
        snapshot: Teacher slider values at mint time.
        target_mode: ``graph`` (frozen target curve) or ``equation``.
        parent: Parent kind or ``{kind, ...}`` object.
        slide_index: Page index this question is minted onto.
    """
    params = normalize_parent_params(snapshot)
    mode = normalize_target_mode(target_mode)
    posted = snapshot if isinstance(snapshot, dict) else {}
    parent_fn = parent_function_dict(
        parent
        if parent is not None
        else posted.get("parent") or posted.get("kind")
    )
    equation = format_parent_equation(parent_fn["kind"], params)
    return {
        "kind": ARTIFACT_KIND,
        "artifact_id": PARENT_TRANSFORMATIONS_ARTIFACT_ID,
        "channel": ARTIFACT_CHANNEL,
        "prompt": PARENT_TRANSFORMATIONS_STEM,
        "stem": PARENT_TRANSFORMATIONS_STEM,
        "parent": parent_fn,
        "snapshot": params,
        "target_mode": mode,
        "equation": equation,
        "slider_keys": list(PARENT_TRANSFORM_KEYS),
        "slider_ranges": {
            key: list(PARENT_TRANSFORM_RANGES[key]) for key in PARENT_TRANSFORM_KEYS
        },
        "media_url": C3_PARENT_MEDIA_URL,
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
    if is_parent_transformations_artifact(payload):
        parent_fn = parent_function_dict(payload.get("parent"))
        snapshot = normalize_parent_params(payload.get("snapshot"))
        out = {
            "kind": ARTIFACT_KIND,
            "artifact_id": PARENT_TRANSFORMATIONS_ARTIFACT_ID,
            "channel": ARTIFACT_CHANNEL,
            "prompt": PARENT_TRANSFORMATIONS_STEM,
            "stem": PARENT_TRANSFORMATIONS_STEM,
            "parent": parent_fn,
            "snapshot": snapshot,
            "target_mode": mode,
            "slider_keys": list(payload.get("slider_keys") or PARENT_TRANSFORM_KEYS),
            "slider_ranges": payload.get("slider_ranges")
            or {
                key: list(PARENT_TRANSFORM_RANGES[key])
                for key in PARENT_TRANSFORM_KEYS
            },
            "media_url": str(payload.get("media_url") or C3_PARENT_MEDIA_URL),
        }
        if mode == "equation":
            out["equation"] = str(
                payload.get("equation")
                or format_parent_equation(parent_fn["kind"], snapshot)
            )
        return out
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
    """Return a compact slider readout for Responses & Points.

    Args:
        response: Student answer JSON (``params`` or top-level sliders).
    """
    posted = response if isinstance(response, dict) else {}
    raw = posted.get("params") if isinstance(posted.get("params"), dict) else posted
    if not isinstance(raw, dict):
        return ""
    parent = raw.get("parent") or posted.get("parent")
    uses_parent = bool(parent) or "d" in raw or (
        "c" in raw and "h" not in raw
    )
    try:
        if uses_parent:
            params = normalize_parent_params(raw)
            keys = PARENT_TRANSFORM_KEYS
        else:
            params = normalize_transform_params(raw)
            keys = TRANSFORM_KEYS
    except ValueError:
        return ""
    parts = []
    if uses_parent:
        try:
            parts.append(f"parent={normalize_parent_kind(parent)}")
        except ValueError:
            if parent:
                parts.append(f"parent={parent}")
    for key in keys:
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
    artifact_id = str(
        payload.get("artifact_id") or TRANSFORMATIONS_ARTIFACT_ID
    )
    if artifact_id == PARENT_TRANSFORMATIONS_ARTIFACT_ID:
        try:
            parent_fn = parent_function_dict(payload.get("parent"))
            snapshot = normalize_parent_params(payload.get("snapshot"))
            mode = normalize_target_mode(payload.get("target_mode"))
        except ValueError:
            return None
        return {
            "artifact_id": PARENT_TRANSFORMATIONS_ARTIFACT_ID,
            "parent": parent_fn,
            "snapshot": snapshot,
            "target_mode": mode,
            "equation": format_parent_equation(parent_fn["kind"], snapshot),
        }
    return {
        "artifact_id": artifact_id,
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


def default_c3_parent_media() -> dict[str, Any]:
    """Return the MCR3U M1 C3 parent-function iframe seed.

    Teacher radios and sliders stay iframe-local until Artifact mint.
    """
    seeded = default_c2_transform_media()
    seeded["url"] = C3_PARENT_MEDIA_URL
    seeded["challenge"] = "C3"
    seeded["transform"] = dict(PARENT_TRANSFORMATIONS_PARENT["params"])
    seeded["parent"] = parent_function_dict("linear")
    return seeded


def live_media_catalog() -> dict[str, dict[str, str]]:
    """Modulified live-media registry keyed by live slot.

    Returns:
        ``{C1, C2, C3}`` rows with ``url`` + ``title``. Generic C3 still
        clears unless a playlist or Artifact URL is posted.
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
        "C3": {
            "url": C2_TRANSFORM_MEDIA_URL,
            "title": "C3 Transformations",
        },
    }


def artifact_prompt_payload(
    artifact_id: str,
    *,
    snapshot: Any,
    target_mode: Any = "graph",
    parent: dict[str, Any] | None = None,
    slide_index: int = ARTIFACT_SLIDE_BASE,
) -> dict[str, Any]:
    """Build the stored prompt for a registered Artifact.

    Args:
        artifact_id: Registered Artifact id.
        snapshot: Teacher slider values at mint time.
        target_mode: ``graph`` or ``equation``.
        parent: Optional parent-function override.
        slide_index: Page index this question is minted onto.

    Raises:
        ValueError: Unknown artifact id.
    """
    wanted = str(artifact_id or "").strip() or TRANSFORMATIONS_ARTIFACT_ID
    if wanted == PARENT_TRANSFORMATIONS_ARTIFACT_ID:
        return parent_transformations_prompt_payload(
            snapshot=snapshot,
            target_mode=target_mode,
            parent=parent,
            slide_index=slide_index,
        )
    if wanted == TRANSFORMATIONS_ARTIFACT_ID:
        return transformations_prompt_payload(
            snapshot=snapshot,
            target_mode=target_mode,
            parent=parent,
            slide_index=slide_index,
        )
    raise ValueError(f"unknown artifact: {wanted}")

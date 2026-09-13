"""Deterministic checkers for M4-L2 completing-the-square tasks. Independent of JSXGraph."""

from __future__ import annotations

from typing import Any


def parse_num(value: Any) -> float:
    """Parse a student number, including unicode minus and simple fractions."""
    if value is None or value == "":
        return float("nan")
    if isinstance(value, bool):
        return float("nan")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("−", "-").replace(" ", "")
    if not text:
        return float("nan")
    if "/" in text:
        num, den = text.split("/", 1)
        try:
            return float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            return float("nan")
    try:
        return float(text)
    except ValueError:
        return float("nan")


def _near(value: float, target: float, tol: float) -> bool:
    if value != value:  # NaN
        return False
    return abs(value - target) <= tol


def _axis_is(text: Any, n: float) -> bool:
    raw = " ".join(str(text or "").lower().replace("−", "-").split())
    compact = raw.replace(" ", "")
    n_int = int(n) if float(n) == int(n) else n
    candidates = {f"x={n_int}", f"x={n}", f"x=+{n_int}", f"x=+{n}"}
    if n < 0:
        candidates.add(f"x={n_int}")
    return compact in {c.replace(" ", "") for c in candidates}


def _extreme_is(text: Any, kind: str) -> bool:
    token = str(text or "").strip().lower()
    if kind == "minimum":
        return token.startswith("min")
    return token.startswith("max")


def _choice_kind(text: Any) -> str:
    token = (
        str(text or "")
        .strip()
        .lower()
        .replace("_", "-")
        .replace(" ", "-")
    )
    if token in {"factor", "factoring"}:
        return "factor"
    if token in {
        "complete-the-square",
        "completing-the-square",
        "cts",
        "complete",
    }:
        return "complete-the-square"
    return token


def classify_expand_cts_revisit(data: dict[str, Any]) -> str:
    """First matching condition for expand-cts-revisit (abc {3,-6,7} or ahk {3,1,4})."""
    sa = parse_num(data.get("standard_a"))
    sb = parse_num(data.get("standard_b"))
    sc = parse_num(data.get("standard_c"))
    a = parse_num(data.get("a"))
    h = parse_num(data.get("h"))
    k = parse_num(data.get("k"))
    abc_ok = _near(sa, 3, 0) and _near(sb, -6, 0) and _near(sc, 7, 0)
    ahk_ok = _near(a, 3, 0) and _near(h, 1, 0) and _near(k, 4, 0)
    if abc_ok or ahk_ok:
        return "all-match"
    return "standard-or-recovered-off"


def classify_diagram_ex960(data: dict[str, Any]) -> str:
    """First matching condition for diagram-ex960 (side 3, leftover −4)."""
    side = parse_num(data.get("side_length"))
    leftover = parse_num(data.get("leftover_constant"))
    if _near(side, 3, 0) and _near(leftover, -4, 0):
        return "all-match"
    if not _near(side, 3, 0):
        return "side-off"
    if _near(side, 3, 0) and not _near(leftover, -4, 0):
        return "leftover-off"
    return "other-mismatch"


def classify_algebraic_ex960(data: dict[str, Any]) -> str:
    """First matching condition for algebraic-ex960 (ahk {1, −3, −4})."""
    a = parse_num(data.get("a"))
    h = parse_num(data.get("h"))
    k = parse_num(data.get("k"))
    if _near(a, 1, 0) and _near(h, -3, 0) and _near(k, -4, 0):
        return "all-match"
    if _near(h, 3, 0) and _near(a, 1, 0):
        return "wrong-h-sign"
    if _near(h, -3, 0) and not _near(k, -4, 0) and _near(a, 1, 0):
        return "correct-h-wrong-k"
    if not _near(h, -3, 0) and not _near(h, 3, 0) and _near(a, 1, 0):
        return "wrong-h-sign-or-half"
    return "other-mismatch"


def classify_algebraic_try9119(data: dict[str, Any]) -> str:
    """First matching condition for algebraic-try9119 (ahk {1, −1, −4})."""
    a = parse_num(data.get("a"))
    h = parse_num(data.get("h"))
    k = parse_num(data.get("k"))
    if _near(a, 1, 0) and _near(h, -1, 0) and _near(k, -4, 0):
        return "all-match"
    if _near(a, 1, 0) and _near(h, -3, 0) and _near(k, -4, 0):
        return "copied-960-target"
    if _near(h, 1, 0) and _near(a, 1, 0):
        return "wrong-h-positive"
    if _near(h, -1, 0) and not _near(k, -4, 0) and _near(a, 1, 0):
        return "correct-h-wrong-k"
    if not _near(h, -1, 0) and not _near(h, 1, 0) and _near(a, 1, 0):
        return "wrong-h-sign-or-half"
    return "other-mismatch"


def classify_factor_a_ex959(data: dict[str, Any]) -> str:
    """First matching condition for factor-a-ex959 (ahk {−3, −1, 2})."""
    a = parse_num(data.get("a"))
    h = parse_num(data.get("h"))
    k = parse_num(data.get("k"))
    if _near(a, -3, 0) and _near(h, -1, 0) and _near(k, 2, 0):
        return "all-match"
    if _near(a, 1, 0) or _near(a, 3, 0):
        return "forget-factor-a"
    if _near(a, -3, 0) and (not _near(h, -1, 0) or not _near(k, 2, 0)):
        return "correct-a-wrong-hk"
    return "other-mismatch"


def classify_rational_half(data: dict[str, Any]) -> str:
    """First matching condition for rational-half (ahk {1, −1/4, 15/16})."""
    a = parse_num(data.get("a"))
    h = parse_num(data.get("h"))
    k = parse_num(data.get("k"))
    if _near(a, 1, 0) and _near(h, -0.25, 0.0001) and _near(k, 0.9375, 0.0001):
        return "all-match"
    if not _near(h, -0.25, 0.0001) and _near(a, 1, 0):
        return "wrong-half-of-b"
    if _near(h, -0.25, 0.0001) and not _near(k, 0.9375, 0.0001) and _near(a, 1, 0):
        return "correct-h-wrong-k"
    return "other-mismatch"


def classify_verify_try9118(data: dict[str, Any]) -> str:
    """First matching condition for verify-try9118 (ahk {2, 2, −5})."""
    a = parse_num(data.get("a"))
    h = parse_num(data.get("h"))
    k = parse_num(data.get("k"))
    if _near(a, 2, 0) and _near(h, 2, 0) and _near(k, -5, 0):
        return "all-match"
    if _near(a, 1, 0):
        return "factor-2-needed"
    if _near(a, 2, 0) and (not _near(h, 2, 0) or not _near(k, -5, 0)):
        return "correct-a-wrong-hk"
    return "other-mismatch"


def classify_sketch_try9120(data: dict[str, Any]) -> str:
    """First matching condition for sketch-try9120 (vertex (4, −4), axis x = 4, min)."""
    a = parse_num(data.get("a"))
    h = parse_num(data.get("h"))
    k = parse_num(data.get("k"))
    vx = parse_num(data.get("named_vertex_x"))
    vy = parse_num(data.get("named_vertex_y"))
    axis_ok = _axis_is(data.get("named_axis"), 4)
    extreme_ok = _extreme_is(data.get("named_extreme"), "minimum")
    ahk_ok = _near(a, 1, 0) and _near(h, 4, 0) and _near(k, -4, 0)
    vertex_ok = _near(vx, 4, 0) and _near(vy, -4, 0)
    if ahk_ok and vertex_ok and axis_ok and extreme_ok:
        return "reading-match"
    if not vertex_ok:
        return "named-vertex-differs"
    if vertex_ok and not axis_ok:
        return "named-axis-differs"
    if vertex_ok and axis_ok and not extreme_ok:
        return "named-extreme-differs"
    return "named-vertex-differs"


def classify_strategy_cts_vs_factor(data: dict[str, Any]) -> str:
    """First matching condition for strategy-cts-vs-factor (A factor, B CTS)."""
    choice_a = _choice_kind(data.get("choice_A"))
    choice_b = _choice_kind(data.get("choice_B"))
    present = bool(choice_a) and bool(choice_b)
    if choice_a == "factor" and choice_b == "complete-the-square":
        return "choices-match"
    if present:
        return "choices-differ"
    return "choices-differ"


def classify_fresh_hook_cts(data: dict[str, Any]) -> str:
    """First matching condition for fresh-hook-cts (vertex (3, 4), axis x = 3, max)."""
    a = parse_num(data.get("a"))
    h = parse_num(data.get("h"))
    k = parse_num(data.get("k"))
    vx = parse_num(data.get("named_vertex_x"))
    vy = parse_num(data.get("named_vertex_y"))
    axis_ok = _axis_is(data.get("named_axis"), 3)
    extreme_ok = _extreme_is(data.get("named_extreme"), "maximum")
    ahk_ok = _near(a, -1, 0) and _near(h, 3, 0) and _near(k, 4, 0)
    vertex_ok = _near(vx, 3, 0) and _near(vy, 4, 0)
    rewrite_submitted = a == a or h == h or k == k
    if ahk_ok and vertex_ok and axis_ok and extreme_ok:
        return "reading-match"
    if rewrite_submitted and _near(a, 1, 0):
        return "factor-neg-needed"
    if not vertex_ok:
        return "named-vertex-differs"
    if vertex_ok and not axis_ok:
        return "named-axis-differs"
    if vertex_ok and axis_ok and not extreme_ok:
        return "named-extreme-differs"
    return "named-vertex-differs"


_CLASSIFIERS = {
    "expand-cts-revisit": classify_expand_cts_revisit,
    "diagram-ex960": classify_diagram_ex960,
    "algebraic-ex960": classify_algebraic_ex960,
    "algebraic-try9119": classify_algebraic_try9119,
    "factor-a-ex959": classify_factor_a_ex959,
    "rational-half": classify_rational_half,
    "verify-try9118": classify_verify_try9118,
    "sketch-try9120": classify_sketch_try9120,
    "strategy-cts-vs-factor": classify_strategy_cts_vs_factor,
    "fresh-hook-cts": classify_fresh_hook_cts,
}


def run_scenario(task_id: str, data: dict[str, Any]) -> str:
    """Dispatch one M4-L2 feedback-spec scenario_tests input."""
    fn = _CLASSIFIERS.get(task_id)
    if fn is None:
        raise ValueError(f"unsupported scenario task {task_id}")
    return fn(data)

"""Deterministic checkers for vertex-form formative tasks. Independent of JSXGraph."""

from __future__ import annotations

from typing import Any


def expand_standard(a: float, h: float, k: float) -> dict[str, float]:
    """Expand a(x − h)^2 + k to ax^2 + bx + c."""
    return {"a": a, "b": -2 * a * h, "c": a * h * h + k}


def _near(value: float, target: float, tol: float) -> bool:
    return abs(value - target) <= tol


def classify_match(a: float, h: float, k: float, *, tol: float = 0.15) -> str:
    """First matching condition for match-plus-inside (target a=1, h=-3, k=1)."""
    if abs(a) < 0.15:
        return "a-near-zero"
    if _near(a, 1, tol) and _near(h, -3, tol) and _near(k, 1, tol):
        return "all-match"
    if _near(h, -3, tol) and _near(k, 1, tol) and (a * 1) < 0 and abs(a) >= 0.15:
        return "correct-vertex-wrong-opening"
    if _near(h, -3, tol) and _near(k, 1, tol) and a > 0 and not _near(a, 1, tol):
        return "correct-position-wrong-abs-a"
    if _near(h, -3, tol) and not _near(k, 1, tol) and abs(a) >= 0.15:
        return "correct-h-wrong-k"
    return "other-mismatch"


def classify_expand(standard_a: float, standard_b: float, standard_c: float) -> str:
    """Check expansion of f(x)=2(x+3)^2+1 only (not the Ministry A2.7 sample)."""
    expected = expand_standard(2, -3, 1)
    if (
        _near(standard_a, expected["a"], 1e-6)
        and _near(standard_b, expected["b"], 1e-6)
        and _near(standard_c, expected["c"], 1e-6)
    ):
        return "coefficients-match"
    return "coefficients-differ"


def classify_fresh(
    named_vertex_x: float,
    named_vertex_y: float,
    named_axis: str,
    named_extreme: str,
    *,
    tol: float = 0.15,
) -> str:
    """Check named reading of f(x)=−0.5(x−2)^2+4 without printing the key in student copy."""
    axis = " ".join(named_axis.lower().replace(" ", "").replace("−", "-").split())
    axis_ok = axis in {"x=2", "x=+2"}
    extreme = named_extreme.strip().lower()
    extreme_ok = extreme.startswith("max")
    if not (_near(named_vertex_x, 2, tol) and _near(named_vertex_y, 4, tol)):
        return "named-vertex-differs"
    if not axis_ok:
        return "named-axis-differs"
    if not extreme_ok:
        return "named-extreme-differs"
    return "reading-match"


def classify_predict_h(
    a: float,
    h: float,
    k: float,
    predicted_vertex_x: float,
    *,
    initial: dict[str, float] | None = None,
    destination_h: float = 2.0,
    tol: float = 0.15,
) -> str:
    """Require only-h move to the task destination (x = 2), matching the student prompt."""
    start = initial or {"a": 2, "h": -3, "k": 1}
    if predicted_vertex_x != predicted_vertex_x:  # NaN
        return "missing-prediction"
    if not _near(a, start["a"], tol) or not _near(k, start["k"], tol):
        return "uncertain-cause"
    if _near(h, start["h"], tol):
        return "h-not-moved"
    if not _near(h, destination_h, tol) or not _near(predicted_vertex_x, destination_h, tol):
        return "vertex-x-differs-from-prediction"
    return "vertex-x-matches-prediction"


def run_scenario(task_id: str, data: dict[str, Any]) -> str:
    """Dispatch one feedback-spec scenario_tests input."""
    if task_id == "match-plus-inside":
        return classify_match(float(data["a"]), float(data["h"]), float(data["k"]))
    if task_id == "expand-same-graph":
        return classify_expand(
            float(data["standard_a"]),
            float(data["standard_b"]),
            float(data["standard_c"]),
        )
    raise ValueError(f"unsupported scenario task {task_id}")

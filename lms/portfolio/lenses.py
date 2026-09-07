"""Draft module-lens stems that parallel the course cores (no invented wording)."""

from __future__ import annotations

from typing import Any

from portfolio.store import load_core_questions


def representations_for_module(module_row: dict[str, Any] | None) -> list[str]:
    """Pick representation phrases from the module title / strand (metadata pattern).

    Exponential → graph/table/equation/growth; discrete → recursive/explicit/table/diagram;
    trig → circle/triangle/graph/equation. Other modules keep a generic quadratic-model set
    from the M1 pattern (not new curriculum sentences).

    Args:
        module_row: One ``modules[]`` entry from ``module-strand-map.json``.
    """
    blob = " ".join(
        str(module_row.get(k) or "")
        for k in ("title", "strand_name", "strand")
        if module_row
    ).lower()
    if any(token in blob for token in ("exponential", "compound interest", "annuit")):
        return ["graph", "table", "equation", "growth"]
    if any(token in blob for token in ("discrete", "sequence", "series", "financial")):
        return ["recursive", "explicit", "table", "diagram"]
    if any(token in blob for token in ("trig", "sinusoidal", "periodic")):
        return ["circle", "triangle", "graph", "equation"]
    return ["graph", "table", "equation", "diagram"]


def draft_lenses(
    *,
    module_number: int,
    module_row: dict[str, Any] | None,
    representations: list[str] | None = None,
) -> dict[str, str]:
    """Three lens stems that parallel CONNECT / JUSTIFY / TRANSFER cores.

    Args:
        module_number: 1-based module index (for the lens label only).
        module_row: Map row used to choose representations.
        representations: Optional override list.

    Returns:
        Keys ``connect``, ``justify``, ``transfer``.
    """
    reps = representations or representations_for_module(module_row)
    joined = ", ".join(reps)
    cores = load_core_questions()
    _ = cores  # cores stay the course questions; lenses only parallel them
    return {
        "connect": (
            f"Connect two of these: {joined}. What does each show about the same "
            "mathematical idea?"
        ),
        "justify": (
            "Make a claim about a pattern or relationship in this module. "
            "Support it with your model, values, equation, or graph."
        ),
        "transfer": (
            "Explain how your thinking changed, why the new idea helped, "
            "then use that thinking on a new problem in this module."
        ),
    }

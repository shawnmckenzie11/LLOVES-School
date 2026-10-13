"""MCK-208: student dashboard after PIN sign-in (pure helpers).

Participation math reuses ``participation`` (MCK-198). Storage and queries
live in ``school_db``.
"""

from __future__ import annotations

import re
from typing import Any

from participation import level_for, level_line, more_to_next, percent, summary

SLOTS = ("C1", "C2", "C3", "C4")
PIN_PORTAL_SESSION_S = 30 * 60

# Wonder copy, verbatim (MCK-208 student dashboard).
COPY_LIVE_STRIP = "Live now \u00b7 Rejoin class"
COPY_CARD_THIS_MODULE = "This module"
COPY_CARD_FINISH = "Finish these"
COPY_CARD_PAST = "Past modules"
COPY_DOT_DONE = "Done"
COPY_DOT_MISSED = "Missed"
COPY_DOT_UPCOMING = "Coming up"
COPY_FINISH_EMPTY = "All caught up."
COPY_PAST_ROW = "Module {n} \u00b7 Level {level} \u00b7 Complete"
COPY_PHASE2 = "+1 level from group work"
COPY_PHASE3 = "Accuracy coming soon"

DOT_LABELS = {
    "done": COPY_DOT_DONE,
    "missed": COPY_DOT_MISSED,
    "upcoming": COPY_DOT_UPCOMING,
}


def dashboard_copy() -> dict[str, str]:
    """Template-facing Wonder strings for the student dashboard."""
    return {
        "live_strip": COPY_LIVE_STRIP,
        "card_this_module": COPY_CARD_THIS_MODULE,
        "card_finish": COPY_CARD_FINISH,
        "card_past": COPY_CARD_PAST,
        "dot_done": COPY_DOT_DONE,
        "dot_missed": COPY_DOT_MISSED,
        "dot_upcoming": COPY_DOT_UPCOMING,
        "finish_empty": COPY_FINISH_EMPTY,
        "phase2": COPY_PHASE2,
        "phase3": COPY_PHASE3,
    }


def slot_state_label(state: str) -> str:
    """Accessible label for one C-slot dot state."""
    return DOT_LABELS.get(str(state or "").lower(), COPY_DOT_UPCOMING)


def past_module_summary(live_module: str, level: int | None) -> str:
    """One past-module summary line, e.g. ``Module 1 · Level 3 · Complete``."""
    n = module_number(live_module)
    lv = int(level if level is not None else 0)
    return COPY_PAST_ROW.format(n=n, level=lv)


def module_number(live_module: str) -> int:
    """Sort key for ``M1``, ``M2``, …"""
    text = str(live_module or "").strip().upper()
    digits = re.sub(r"^M", "", text)
    return int(digits) if digits.isdigit() else 0


def module_label(course_code: str, live_module: str) -> str:
    """Header subtitle, e.g. ``MCF3M · Module 2``."""
    course = str(course_code or "").strip().upper()
    n = module_number(live_module)
    return f"{course} · Module {n}" if n else course


def level_bar_segments(level: int | None) -> list[dict[str, Any]]:
    """Four steps for the level bar (1–4); unfilled below the current band."""
    lv = int(level or 0)
    return [{"step": n, "filled": lv >= n} for n in range(1, 5)]


def slot_dot_state(offered: int, answered: int, ran: bool) -> str:
    """One C-slot dot: ``done``, ``missed``, or ``upcoming``."""
    if not ran:
        return "upcoming"
    if offered <= 0:
        return "done"
    if answered >= offered:
        return "done"
    return "missed"


def build_module_card(
    answered: int,
    offered: int,
    *,
    slots: list[dict[str, Any]],
) -> dict[str, Any]:
    """This-module card: calm line, bar, and slot dots."""
    summ = summary(answered, offered)
    line = level_line(answered, offered)
    if line is None and offered > 0:
        k = more_to_next(answered, offered)
        a = min(max(answered, 0), offered)
        line = f"{a}/{offered} completed · {k} more" if k else f"{a}/{offered} completed"
    return {
        "line": line,
        "answered": summ["answered"],
        "offered": summ["offered"],
        "level": summ["level"],
        "percent": summ["percent"],
        "level_bar": level_bar_segments(summ["level"]),
        "slots": slots,
    }


def pick_current_module(modules: list[str], complete: dict[str, bool]) -> str | None:
    """First incomplete module by number, else the latest module seen."""
    ordered = sorted({str(m) for m in modules if m}, key=module_number)
    if not ordered:
        return None
    for mod in ordered:
        if not complete.get(mod):
            return mod
    return ordered[-1]

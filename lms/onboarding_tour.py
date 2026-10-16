"""Teacher-tour helpers for MCK-267.

The live-class steps and the "Run a test live class" checklist item need to
know whether a run was started as Test. That choice lands with MCK-269.
"""

from __future__ import annotations

from typing import Any

#: Session keys MCK-269 may set when Test/Live is chosen at the date step.
_RUN_TYPE_KEYS = ("run_type", "start_run_mode", "session_run_type")


def tour_run_type(session: dict[str, Any] | None) -> str:
    """Return ``test``, ``live``, or ``""`` for the teacher tour.

    TODO(MCK-269): Test or Live is chosen at the date step and stored on
    the live session. Until that field exists this returns ``""``. The
    live-class spotlight still runs (it defaults to a Test class) and
    "Run a test live class" does not tick, so a Live run is not counted.

    Args:
        session: Live-session row, or None when nothing is running.

    Returns:
        ``test`` or ``live`` when a known field is set, else ``""``.
    """
    if not isinstance(session, dict):
        return ""
    for key in _RUN_TYPE_KEYS:
        raw = str(session.get(key) or "").strip().lower()
        if raw in {"test", "live"}:
            return raw
    return ""

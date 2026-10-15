"""Shared End Live Class form payloads for tests (MCK-233)."""

from __future__ import annotations

from typing import Any

# Teacher chose Live on the End dialog (saves A&P + catch-up + lesson slides).
END_LIVE_LIVE_FORM: dict[str, str] = {
    "end_options": "1",
    "save_attendance": "1",
    "save_participation": "1",
    "run_mode": "live",
}

# Teacher chose Test (no A&P persistence).
END_LIVE_TEST_FORM: dict[str, str] = {
    "end_options": "1",
    "save_attendance": "1",
    "save_participation": "1",
    "run_mode": "test",
}


def end_live_live_form(**extra: Any) -> dict[str, str]:
    """Return a copy of the Live End form with optional overrides."""
    data = dict(END_LIVE_LIVE_FORM)
    for key, value in extra.items():
        data[str(key)] = str(value)
    return data

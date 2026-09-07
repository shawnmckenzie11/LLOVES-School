"""Local-only gate for the staff Portfolio tab."""

from __future__ import annotations

import os


def portfolio_tab_enabled() -> bool:
    """True on localhost/tests; false when ``FLASK_ENV=production``.

    A later merge to ``main`` must not show this tab on Fly.

    Returns:
        Whether the Portfolio tab and its APIs may run.
    """
    env = (os.getenv("FLASK_ENV") or "").strip().lower()
    if env == "production":
        return False
    try:
        from flask import current_app, has_app_context

        if has_app_context() and current_app.config.get("TESTING"):
            return True
    except Exception:  # noqa: BLE001 — gate must never raise
        pass
    return (os.getenv("LOCAL_DEV_LOGIN") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }

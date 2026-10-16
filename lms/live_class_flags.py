"""Strict parsing for live-class Test vs Live flags (MCK-269)."""

from __future__ import annotations

from typing import Any


def parse_is_test_json(raw: Any) -> bool:
    """Return True only when JSON decoded the value as boolean ``true``.

    Args:
        raw: ``body.get("is_test")`` from ``request.get_json()``.

    Returns:
        True only for Python ``True``; all other values are False.
    """
    return raw is True


def parse_is_test_form(raw: Any) -> bool:
    """Return True for form fields ``true`` or ``1`` (case-insensitive).

    Args:
        raw: ``request.form.get("is_test")``.

    Returns:
        True for explicit truthy form strings; otherwise False.
    """
    if isinstance(raw, str):
        return raw.strip().lower() in {"true", "1"}
    return False


def parse_is_test_value(raw: Any) -> bool:
    """Backward-compatible helper: JSON rules plus form strings.

    Prefer :func:`parse_is_test_json` or :func:`parse_is_test_form` at the
    HTTP boundary so JSON string ``"true"`` is not treated as test mode.
    """
    if parse_is_test_json(raw):
        return True
    return parse_is_test_form(raw)

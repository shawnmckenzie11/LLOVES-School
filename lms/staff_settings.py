"""Teacher Settings page data (MCK-539). Read-only; no writes."""

from __future__ import annotations

from typing import Any

from features import (
    PLAN_BASIC,
    PLAN_PRO,
    feature_override_notes,
    has_feature,
    teacher_plan_comparison_rows,
)
from live_class_constants import is_slides_operator_email


VALID_SETTINGS_SECTIONS = frozenset({"profile", "plan", "connected", "billing"})

SETTINGS_SECTION_LABELS: dict[str, str] = {
    "profile": "Profile",
    "plan": "Plan",
    "connected": "Connected apps",
    "billing": "Billing",
}


def normalize_settings_section(raw: str | None) -> str:
    """Return a valid Settings sub-nav section id.

    Args:
        raw: Query parameter value.

    Returns:
        One of ``profile``, ``plan``, ``connected``, or ``billing``.
    """
    section = str(raw or "profile").strip().lower()
    if section not in VALID_SETTINGS_SECTIONS:
        return "profile"
    return section


def plan_status_heading(plan: str | None) -> str:
    """Wonder plan card headline for the signed-in teacher.

    Args:
        plan: ``users.plan`` (``basic`` or ``pro``).

    Returns:
        ``You're on Basic`` or ``You're on Pro``.
    """
    normalized = str(plan or PLAN_BASIC).strip().lower()
    if normalized == PLAN_PRO:
        return "You're on Pro"
    return "You're on Basic"


def google_slides_connected_apps_row(school: Any, user: Any) -> dict[str, str] | None:
    """Optional Google Slides row for Connected apps (boolean only).

    Hides the row when ``slides_import`` is off or the user is not a slides
    operator. Never includes tokens or connect URLs.

    Args:
        school: Open school database.
        user: Signed-in staff user.

    Returns:
        ``name`` and ``status`` when the row should show, else ``None``.
    """
    if not has_feature(user, "slides_import"):
        return None
    if not is_slides_operator_email(user.get("email")):
        return None
    connected = bool(school.get_google_api_token(int(user["id"])))
    status = "Connected" if connected else "Not connected"
    return {"name": "Google Slides", "status": status}


def settings_view_model(school: Any, user: Any, section: str) -> dict[str, Any]:
    """Template context for ``staff/settings.html``.

    Args:
        school: Open school database.
        user: Signed-in teacher (staff or IT in staff portal).
        section: Active sub-nav section.

    Returns:
        Read-only fields for Profile, Plan, Connected apps, and Billing.
    """
    plan = str(user.get("plan") or PLAN_BASIC).strip().lower()
    slides_row = google_slides_connected_apps_row(school, user)
    return {
        "settings_section": section,
        "settings_sections": SETTINGS_SECTION_LABELS,
        "plan_status_heading": plan_status_heading(plan),
        "plan_is_basic": plan != PLAN_PRO,
        "plan_comparison_rows": teacher_plan_comparison_rows(user),
        "feature_override_notes": feature_override_notes(school, user),
        "google_slides_app": slides_row,
        "display_name": str(user.get("display_name") or "").strip() or "—",
        "email": str(user.get("email") or "").strip() or "—",
    }
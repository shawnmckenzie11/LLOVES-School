"""Open Question (and future round) action profiles for live scoring.

Profiles define the action buttons (label + point amount) teachers tap during
Open Question rounds. Builtin Default matches the historical hardcoded chips.
Pack seed file ``libraries/<id>/ap_round_profiles.json`` copies into the
offering on load; teachers edit the offering copy on the Profiles tab.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

MAX_PROFILES = 20
MAX_ACTIONS = 20

BUILTIN_OPEN_ACTIONS: list[dict[str, Any]] = [
    {"id": "asks_hwk", "label": "Asks Q re: Hwk", "amount": 2},
    {"id": "asks_followup", "label": "Asks follow-up Q", "amount": 1},
    {
        "id": "asks_prior_group",
        "label": "Asks Q re: Prior Live Class Group Problem",
        "amount": 3,
    },
    {
        "id": "asks_formative",
        "label": "Asks Q about Teacher’s Formative Question Feedback",
        "amount": 2,
    },
    {"id": "answers_peer", "label": "Answers another student’s Q", "amount": 2},
    {"id": "asks_first", "label": "Asks Q for first time", "amount": 1},
]

_SLUG_RE = re.compile(r"[^a-z0-9_]+")


def builtin_open_document() -> dict[str, Any]:
    """Return the default Open Question profiles document."""
    return {
        "open": {
            "active_id": "default",
            "profiles": [
                {
                    "id": "default",
                    "name": "Default",
                    "actions": [dict(row) for row in BUILTIN_OPEN_ACTIONS],
                }
            ],
        }
    }


def pack_profiles_path(data_dir: Path, library_id: int) -> Path:
    """Path to the pack-seed profiles JSON for a content library."""
    return Path(data_dir) / "libraries" / str(int(library_id)) / "ap_round_profiles.json"


def _slug_id(raw: str, *, fallback: str) -> str:
    """Normalize a profile/action id to a safe slug."""
    text = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    text = _SLUG_RE.sub("_", text).strip("_")
    return text or fallback


def _normalize_action(raw: Any, *, index: int) -> dict[str, Any]:
    """Validate one action row."""
    if not isinstance(raw, dict):
        raise ValueError("Each action must be an object")
    label = str(raw.get("label") or "").strip()
    if not label:
        raise ValueError("Action label is required")
    if len(label) > 120:
        raise ValueError("Action label is too long")
    try:
        amount = float(raw.get("amount"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Action amount must be a number") from exc
    if not (amount == amount) or abs(amount) > 1000:  # NaN check
        raise ValueError("Action amount is out of range")
    # Keep integer-looking amounts as ints for cleaner UI.
    if float(amount).is_integer():
        amount_out: float | int = int(amount)
    else:
        amount_out = round(amount, 1)
    action_id = _slug_id(str(raw.get("id") or ""), fallback=f"action_{index + 1}")
    return {"id": action_id, "label": label, "amount": amount_out}


def _normalize_profile(raw: Any, *, index: int, seen_ids: set[str]) -> dict[str, Any]:
    """Validate one profile."""
    if not isinstance(raw, dict):
        raise ValueError("Each profile must be an object")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("Profile name is required")
    if len(name) > 80:
        raise ValueError("Profile name is too long")
    profile_id = _slug_id(str(raw.get("id") or ""), fallback=f"profile_{index + 1}")
    if profile_id in seen_ids:
        profile_id = f"{profile_id}_{uuid.uuid4().hex[:6]}"
    seen_ids.add(profile_id)
    actions_raw = raw.get("actions")
    if not isinstance(actions_raw, list):
        raise ValueError("Profile actions must be a list")
    if len(actions_raw) > MAX_ACTIONS:
        raise ValueError(f"At most {MAX_ACTIONS} actions per profile")
    action_ids: set[str] = set()
    actions: list[dict[str, Any]] = []
    for i, item in enumerate(actions_raw):
        action = _normalize_action(item, index=i)
        if action["id"] in action_ids:
            action["id"] = f"{action['id']}_{i + 1}"
        action_ids.add(action["id"])
        actions.append(action)
    return {"id": profile_id, "name": name, "actions": actions}


def normalize_profiles_document(raw: Any) -> dict[str, Any]:
    """Validate and normalize a full profiles document.

    Args:
        raw: Parsed JSON object or None.

    Returns:
        Document with at least an ``open`` section and one profile.

    Raises:
        ValueError: When structure or limits are invalid.
    """
    if raw is None or raw == "" or raw == {}:
        return builtin_open_document()
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return builtin_open_document()
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("Profiles JSON is invalid") from exc
    if not isinstance(raw, dict):
        raise ValueError("Profiles document must be an object")
    open_raw = raw.get("open")
    if open_raw is None:
        return builtin_open_document()
    if not isinstance(open_raw, dict):
        raise ValueError("open section must be an object")
    profiles_raw = open_raw.get("profiles")
    if not isinstance(profiles_raw, list) or not profiles_raw:
        return builtin_open_document()
    if len(profiles_raw) > MAX_PROFILES:
        raise ValueError(f"At most {MAX_PROFILES} Open Question profiles")
    seen: set[str] = set()
    profiles = [
        _normalize_profile(item, index=i, seen_ids=seen)
        for i, item in enumerate(profiles_raw)
    ]
    active_id = str(open_raw.get("active_id") or "").strip()
    ids = {p["id"] for p in profiles}
    if active_id not in ids:
        active_id = profiles[0]["id"]
    return {"open": {"active_id": active_id, "profiles": profiles}}


def parse_profiles_json(text: str | None) -> dict[str, Any]:
    """Parse offering JSON, falling back to builtin on empty/corrupt input."""
    if not text or not str(text).strip():
        return builtin_open_document()
    try:
        return normalize_profiles_document(text)
    except ValueError:
        return builtin_open_document()


def dump_profiles_json(doc: dict[str, Any]) -> str:
    """Serialize a normalized profiles document."""
    return json.dumps(normalize_profiles_document(doc), ensure_ascii=False)


def active_open_actions(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the action list for the active Open Question profile."""
    normalized = normalize_profiles_document(doc)
    section = normalized["open"]
    active = section["active_id"]
    for profile in section["profiles"]:
        if profile["id"] == active:
            return [dict(a) for a in profile["actions"]]
    return [dict(a) for a in section["profiles"][0]["actions"]]


def load_pack_profiles(data_dir: Path, library_id: int | None) -> dict[str, Any] | None:
    """Load pack-seed profiles when the file exists.

    Args:
        data_dir: LMS data root.
        library_id: ``content_libraries.id`` or None.

    Returns:
        Normalized document, or None when no pack file is present.
    """
    if not library_id:
        return None
    path = pack_profiles_path(data_dir, int(library_id))
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return normalize_profiles_document(payload)
    except ValueError:
        return None


def seed_document_for_offering(
    data_dir: Path, library_id: int | None, existing_json: str | None
) -> dict[str, Any]:
    """Choose offering profiles: existing → pack seed → builtin."""
    if existing_json and str(existing_json).strip():
        return parse_profiles_json(existing_json)
    pack = load_pack_profiles(data_dir, library_id)
    if pack is not None:
        return pack
    return builtin_open_document()

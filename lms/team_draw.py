"""MCK-242: Team Draw group whiteboard icebreaker (no scoring, session images)."""

from __future__ import annotations

import base64
import json
import re
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from school_db import SchoolDB

TEAM_DRAW_KIND = "team_draw"
TEAM_DRAW_RESPONSE_MODE = "team_draw"
MAX_TITLE_LEN = 80
MAX_IMAGE_BYTES = 750_000

BUILTIN_PROMPTS: tuple[dict[str, str], ...] = (
    {
        "id": "team_logo",
        "label": "Team logo",
        "text": "Draw your team logo. Bonus if it has an animal in it.",
    },
    {
        "id": "mystery_rule",
        "label": "Mystery rule",
        "text": "Mystery rule: write the rule on the board with the Text tool after you draw step 4.",
    },
    {
        "id": "finish_pattern",
        "label": "Finish the pattern",
        "text": "Finish the pattern.",
    },
    {
        "id": "team_common",
        "label": "Something in common",
        "text": "Draw something every person on your team has in common.",
    },
)

DEFAULT_MYSTERY_STEPS: tuple[str, ...] = (
    "Everyone must use at least two colours.",
    "Include one curved line and one straight line.",
    "Leave a blank corner for the next step.",
)


def is_team_draw_item(item: dict[str, Any] | None) -> bool:
    """True when a lifecycle row or catalogue payload is Team Draw."""
    if not isinstance(item, dict):
        return False
    if str(item.get("response_mode") or "") == TEAM_DRAW_RESPONSE_MODE:
        return True
    question = item.get("item") if isinstance(item.get("item"), dict) else item
    if not isinstance(question, dict):
        return False
    token = str(question.get("type") or question.get("kind") or "").strip().lower()
    return token == TEAM_DRAW_KIND


def prompt_catalog() -> list[dict[str, str]]:
    """Built-in prompts for the teacher picker."""
    return [dict(row) for row in BUILTIN_PROMPTS]


def resolved_prompt_text(item: dict[str, Any]) -> str:
    """Return the stem students and staff should see for one item."""
    question = item.get("item") if isinstance(item.get("item"), dict) else {}
    custom = str(question.get("team_draw_custom_text") or "").strip()
    if custom:
        return custom
    prompt_id = str(question.get("team_draw_prompt_id") or "").strip()
    for row in BUILTIN_PROMPTS:
        if row["id"] == prompt_id:
            return row["text"]
    return str(question.get("text") or question.get("prompt") or "Team Draw").strip()


def is_mystery_rule_item(item: dict[str, Any] | None) -> bool:
    """True when the Team Draw item uses the mystery-rule built-in prompt."""
    if not isinstance(item, dict):
        return False
    question = item.get("item") if isinstance(item.get("item"), dict) else item
    if not isinstance(question, dict):
        return False
    return str(question.get("team_draw_prompt_id") or "").strip() == "mystery_rule"


def mystery_steps_for_item(item: dict[str, Any]) -> list[str]:
    """Return three locked mystery-rule steps (teacher-authored or defaults)."""
    question = item.get("item") if isinstance(item.get("item"), dict) else {}
    raw = question.get("team_draw_mystery_steps")
    steps: list[str] = []
    if isinstance(raw, list):
        for entry in raw[:3]:
            text = str(entry or "").strip()
            if text:
                steps.append(text[:200])
    while len(steps) < 3:
        steps.append(DEFAULT_MYSTERY_STEPS[len(steps)])
    return steps[:3]


def decode_png_payload(image_b64: str | None) -> bytes:
    """Decode a data-URL or raw base64 PNG, or raise ValueError."""
    raw = str(image_b64 or "").strip()
    if not raw:
        raise ValueError("Drawing image is required.")
    if raw.startswith("data:"):
        comma = raw.find(",")
        if comma < 0:
            raise ValueError("Invalid image data.")
        raw = raw[comma + 1 :]
    raw = re.sub(r"\s+", "", raw)
    try:
        blob = base64.b64decode(raw, validate=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid image data.") from exc
    if len(blob) > MAX_IMAGE_BYTES:
        raise ValueError("Image is too large.")
    if not blob.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Only PNG drawings are supported.")
    return blob


def team_draw_answer_json(title: str = "") -> dict[str, Any]:
    """Persisted draw answer. The drawing itself is not stored here.

    Args:
        title: Optional leftover label. New submits send none.
    """
    clean = str(title or "").strip()[:MAX_TITLE_LEN]
    return {"kind": TEAM_DRAW_KIND, "title": clean, "value": clean}


def status_label(submitted: bool) -> str:
    """Teacher grid status copy."""
    return "✓ submitted" if submitted else "drawing…"


def gallery_row_title(row: dict[str, Any]) -> str:
    """Optional title line for one draw tile. Empty when the submit had none."""
    final = row.get("final_answer") if isinstance(row.get("final_answer"), dict) else {}
    return str(final.get("title") or "").strip()


def should_snapshot_team_draw_run(
    school: SchoolDB,
    class_id: int,
    run_key: str,
    *,
    live_run: bool = False,
) -> bool:
    """Skip persisting Team Draw rows into live_result_snapshots on test runs."""
    from participation_exclusion import participation_run_counts_in_tab

    kind = school.participation_run_kind(class_id, run_key)
    if kind is None:
        # End Live writes the snapshot before ``participation_runs`` exists.
        return bool(live_run)
    return participation_run_counts_in_tab(kind)


def strip_image_from_snapshot_answer(answer: dict[str, Any]) -> dict[str, Any]:
    """Remove any ephemeral image field before writing snapshots."""
    clean = dict(answer)
    clean.pop("image", None)
    clean.pop("image_png", None)
    return clean

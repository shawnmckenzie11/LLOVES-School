"""File-backed metadata for four live classes per course module.

Metadata lives under ``lms/seeds/live_classes/{COURSE}/M{n}/C{i}.json``.
These files describe shared media, slide references, ordered questions, and
per-stage defaults without coupling authored live content to the SQLite volume.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

COURSE_RE = re.compile(r"^[A-Z]{3,}\d[A-Z]?$")
MODULE_RE = re.compile(r"^M(?:[1-8])$")
SLOT_RE = re.compile(r"^C(?:[1-4])$")
QUESTION_TYPES = frozenset({"poll", "mc", "numeric"})
STAGES = frozenset({"join", "teams", "meet", "round", "play"})


def metadata_root() -> Path:
    """Return the repository directory containing live-class metadata."""

    return Path(__file__).resolve().parent / "seeds" / "live_classes"


def normalize_course_code(raw: Any) -> str:
    """Return a safe uppercase Ontario course code."""

    code = str(raw or "").strip().upper()
    return code if COURSE_RE.fullmatch(code) else "MCF3M"


def normalize_module(raw: Any) -> str:
    """Return ``M1`` through ``M8``, defaulting to ``M1``."""

    module = str(raw or "").strip().upper()
    return module if MODULE_RE.fullmatch(module) else "M1"


def normalize_slot(raw: Any) -> str:
    """Return ``C1`` through ``C4``, defaulting to ``C1``."""

    slot = str(raw or "").strip().upper()
    return slot if SLOT_RE.fullmatch(slot) else "C1"


def metadata_path(
    course: Any,
    module: Any,
    slot: Any,
    *,
    root: Path | None = None,
) -> Path:
    """Return the canonical JSON path for one course live class."""

    base = root if root is not None else metadata_root()
    return (
        base
        / normalize_course_code(course)
        / normalize_module(module)
        / f"{normalize_slot(slot)}.json"
    )


def empty_live_class_metadata(course: Any, module: Any, slot: Any) -> dict[str, Any]:
    """Return a complete empty metadata document for one live class."""

    return {
        "schema_version": 1,
        "course": normalize_course_code(course),
        "module": normalize_module(module),
        "live_class": normalize_slot(slot),
        "media": None,
        "slides": {"deck_ref": None, "page_numbers": []},
        "questions": [],
        "round_defaults": {
            stage: {"timer": False, "timer_minutes": 3, "teams": False}
            for stage in ("join", "teams", "meet", "round", "play")
        },
    }


def _clean_question(raw: Any, *, index: int) -> dict[str, Any] | None:
    """Normalize one metadata question or return ``None`` when invalid."""

    if not isinstance(raw, dict):
        return None
    text = str(raw.get("text") or raw.get("prompt") or "").strip()
    if not text:
        return None
    kind = str(raw.get("type") or raw.get("kind") or "poll").strip().lower()
    if kind not in QUESTION_TYPES:
        kind = "poll"
    stage = str(raw.get("stage") or "round").strip().lower()
    if stage not in STAGES:
        stage = "round"
    options = [
        str(item).strip()
        for item in (raw.get("options") or raw.get("choices") or [])
        if str(item).strip()
    ]
    if kind == "numeric":
        options = []
    key = str(raw.get("correct_answer") or raw.get("correct") or "").strip()
    if kind == "poll":
        key = ""
    try:
        order = max(1, int(raw.get("order") or index + 1))
    except (TypeError, ValueError):
        order = index + 1
    page = raw.get("page_number")
    try:
        page_number = int(page) if page not in (None, "") else None
    except (TypeError, ValueError):
        page_number = None
    question_id = str(raw.get("id") or f"{stage}-{order}").strip()
    return {
        "id": question_id,
        "stage": stage,
        "type": kind,
        "text": text,
        "options": options,
        "correct_answer": key or None,
        "page_number": page_number,
        "order": order,
        "default_visibility": bool(raw.get("default_visibility")),
    }


def normalize_live_class_metadata(
    raw: Any,
    *,
    course: Any,
    module: Any,
    slot: Any,
) -> dict[str, Any]:
    """Normalize one metadata JSON document into the public schema."""

    base = empty_live_class_metadata(course, module, slot)
    if not isinstance(raw, dict):
        return base
    media = raw.get("media")
    if isinstance(media, dict):
        file_name = str(media.get("file") or media.get("url") or "").strip()
        if file_name:
            base["media"] = {
                "file": file_name,
                "title": str(media.get("title") or "").strip(),
                "shared_across_rounds": bool(
                    media.get("shared_across_rounds", True)
                ),
            }
    slides = raw.get("slides")
    if isinstance(slides, dict):
        pages: list[int] = []
        for value in slides.get("page_numbers") or []:
            try:
                pages.append(int(value))
            except (TypeError, ValueError):
                continue
        base["slides"] = {
            "deck_ref": str(slides.get("deck_ref") or "").strip() or None,
            "page_numbers": pages,
        }
    questions = [
        cleaned
        for index, item in enumerate(raw.get("questions") or [])
        if (cleaned := _clean_question(item, index=index)) is not None
    ]
    questions.sort(key=lambda item: (item["stage"], item["order"], item["id"]))
    base["questions"] = questions
    defaults = raw.get("round_defaults")
    if isinstance(defaults, dict):
        for stage in STAGES:
            row = defaults.get(stage)
            if not isinstance(row, dict):
                continue
            try:
                minutes = max(1, min(30, int(row.get("timer_minutes") or 3)))
            except (TypeError, ValueError):
                minutes = 3
            base["round_defaults"][stage] = {
                "timer": bool(row.get("timer")),
                "timer_minutes": minutes,
                "teams": bool(row.get("teams")),
            }
    return base


def load_live_class_metadata(
    course: Any,
    module: Any,
    slot: Any,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Load and normalize one live-class JSON file.

    Missing or malformed files return a complete empty document so the live
    shell remains usable while content is authored.
    """

    path = metadata_path(course, module, slot, root=root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    return normalize_live_class_metadata(
        raw,
        course=course,
        module=module,
        slot=slot,
    )


def questions_for_stage(metadata: Any, stage: Any) -> list[dict[str, Any]]:
    """Return ordered metadata questions associated with one stage."""

    if not isinstance(metadata, dict):
        return []
    wanted = str(stage or "").strip().lower()
    return [
        dict(row)
        for row in metadata.get("questions") or []
        if isinstance(row, dict) and row.get("stage") == wanted
    ]

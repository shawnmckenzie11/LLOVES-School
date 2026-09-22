"""Resolve file-backed live-class playlists into the public metadata shape.

Schema-v1 documents keep their inline ``questions``, ``media``, and ``slides``.
Schema-v2 documents are thin playlists whose refs resolve through the
school-owned catalogue at ``lms/seeds/live_items.json``.
"""

from __future__ import annotations

from copy import deepcopy
import json
import re
from pathlib import Path
from typing import Any

SCHEMA_V1 = 1
SCHEMA_V2 = 2
COURSE_RE = re.compile(r"^[A-Z]{3,}\d[A-Z]?$")
MODULE_RE = re.compile(r"^M(?:[1-8])$")
SLOT_RE = re.compile(r"^C(?:[1-4])$")
ITEM_REF_RE = re.compile(
    r"^(?:universal|course/[A-Z]{3,}\d[A-Z]?|"
    r"live-class/[A-Z]{3,}\d[A-Z]?/M[1-8]/C[1-4])/"
    r"(?:question|media|whiteboard|slides)/[a-z0-9][a-z0-9-]*$"
)
QUESTION_TYPES = frozenset({"poll", "mc", "numeric"})
STAGE_ORDER = ("join", "teams", "meet", "round", "play", "round_3", "summary")
STAGES = frozenset(STAGE_ORDER)

def default_math_pages() -> list[dict[str, str]]:
    """Return the seven named pages used by math live lessons."""

    return [
        {"id": "join", "name": "Join", "stage": "join"},
        {"id": "welcome", "name": "Welcome", "stage": "teams"},
        {"id": "meet", "name": "Meet", "stage": "meet"},
        {"id": "round_1", "name": "Round 1", "stage": "round"},
        {"id": "round_2", "name": "Round 2", "stage": "play"},
        {"id": "round_3", "name": "Round 3", "stage": "round_3"},
        {"id": "summary", "name": "Summary", "stage": "summary"},
    ]


def normalize_live_pages(raw: Any) -> list[dict[str, str]]:
    """Return named lesson pages, falling back to the math default.

    Args:
        raw: Optional authored ``pages`` array.
    """

    rows = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        stage = str(item.get("stage") or item.get("id") or "").strip().lower()
        if stage not in STAGES:
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            name = next(
                (row["name"] for row in default_math_pages() if row["stage"] == stage),
                stage.replace("_", " ").title(),
            )
        rows.append(
            {
                "id": str(item.get("id") or stage).strip() or stage,
                "name": name,
                "stage": stage,
            }
        )
    return rows or default_math_pages()


def normalize_team_challenge(raw: Any) -> dict[str, str]:
    """Return the live-lesson team-challenge copy block.

    Args:
        raw: Optional authored ``team_challenge`` object.
    """

    body = raw if isinstance(raw, dict) else {}
    return {
        "context": str(body.get("context") or "").strip(),
        "question": str(body.get("question") or "").strip(),
        "speaker_notes": str(body.get("speaker_notes") or "").strip(),
    }


ITEM_TYPES = frozenset({"question", "media", "whiteboard", "slides"})
SCOPES = frozenset({"universal", "course", "live_class"})
PUBLISH_MODES = frozenset({"individual", "group_consensus", "group_shared"})
RESPONSE_MODES = frozenset({"individual", "group_consensus"})
CATALOGUE_FILENAME = "live_items.json"
TEACHER_ONLY_ITEM_FIELDS = frozenset(
    {
        "correct",
        "correct_answer",
        "key",
        "feedback",
        "feedback_id",
        "soft_key",
        "teacher_key",
        "by_choice",
        "on_submit",
        "on_weak",
    }
)


def seeds_root() -> Path:
    """Return the repository directory containing committed LMS seed data."""

    return Path(__file__).resolve().parent / "seeds"


def metadata_root() -> Path:
    """Return the repository directory containing live-class playlists."""

    return seeds_root() / "live_classes"


def live_item_catalogue_path(*, root: Path | None = None) -> Path:
    """Return the canonical live-item catalogue path.

    ``root`` may be either the catalogue file itself or a directory containing
    ``live_items.json``. This supports isolated loader tests without changing
    the production seed path.
    """

    if root is None:
        return seeds_root() / CATALOGUE_FILENAME
    candidate = Path(root)
    return candidate if candidate.suffix == ".json" else candidate / CATALOGUE_FILENAME


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
    """Return a complete schema-v1 fallback document for one live class."""

    return {
        "schema_version": SCHEMA_V1,
        "course": normalize_course_code(course),
        "module": normalize_module(module),
        "live_class": normalize_slot(slot),
        "media": None,
        "slides": {"deck_ref": None, "page_numbers": []},
        "questions": [],
        "pages": default_math_pages(),
        "team_challenge": normalize_team_challenge(None),
        "round_defaults": {
            stage: {"timer": False, "timer_minutes": 3, "teams": False}
            for stage in STAGE_ORDER
        },
    }


def empty_live_item_catalogue() -> dict[str, Any]:
    """Return an empty, safe schema-v2 live-item catalogue."""

    return {
        "schema_version": SCHEMA_V2,
        "catalogue_id": "lloves-live-items",
        "items": {},
    }


def _scope_for_ref(ref: str) -> str | None:
    """Return the declared scope encoded by one stable item ref."""

    if ref.startswith("universal/"):
        return "universal"
    if ref.startswith("course/"):
        return "course"
    if ref.startswith("live-class/"):
        return "live_class"
    return None


def _clean_modes(raw: Any, *, allowed: frozenset[str]) -> list[str]:
    """Return distinct supported modes in their authored order."""

    if not isinstance(raw, (list, tuple)):
        return []
    modes: list[str] = []
    for value in raw:
        mode = str(value or "").strip().lower()
        if mode in allowed and mode not in modes:
            modes.append(mode)
    return modes


def normalize_live_item_catalogue(raw: Any) -> dict[str, Any]:
    """Normalize a canonical catalogue and discard malformed definitions."""

    base = empty_live_item_catalogue()
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_V2:
        return base
    catalogue_id = str(raw.get("catalogue_id") or "").strip()
    if catalogue_id:
        base["catalogue_id"] = catalogue_id
    definitions = raw.get("items")
    if not isinstance(definitions, dict):
        return base
    items: dict[str, dict[str, Any]] = {}
    for raw_ref, raw_item in definitions.items():
        ref = str(raw_ref or "").strip()
        if not ITEM_REF_RE.fullmatch(ref) or not isinstance(raw_item, dict):
            continue
        item_type = str(raw_item.get("item_type") or "").strip().lower()
        item_id = str(raw_item.get("id") or "").strip()
        scope = str(raw_item.get("scope") or "").strip().lower()
        if (
            item_type not in ITEM_TYPES
            or not item_id
            or scope not in SCOPES
            or scope != _scope_for_ref(ref)
        ):
            continue
        capabilities = raw_item.get("capabilities")
        capability_rows = capabilities if isinstance(capabilities, dict) else {}
        publish_modes = _clean_modes(
            capability_rows.get("publish_modes"),
            allowed=PUBLISH_MODES,
        )
        response_modes = _clean_modes(
            capability_rows.get("response_modes"),
            allowed=RESPONSE_MODES,
        )
        if "individual" not in publish_modes:
            publish_modes.insert(0, "individual")
        if "individual" not in response_modes:
            response_modes.insert(0, "individual")
        item = deepcopy(raw_item)
        item["item_type"] = item_type
        item["id"] = item_id
        item["scope"] = scope
        item["capabilities"] = {
            **capability_rows,
            "publish_modes": publish_modes,
            "response_modes": response_modes,
        }
        items[ref] = item
    base["items"] = items
    return base


def load_live_item_catalogue(*, root: Path | None = None) -> dict[str, Any]:
    """Load the canonical catalogue, returning an empty catalogue on failure."""

    path = live_item_catalogue_path(root=root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    return normalize_live_item_catalogue(raw)


def resolve_live_item(
    item_ref: Any,
    *,
    catalogue: Any = None,
) -> dict[str, Any] | None:
    """Return a detached canonical item definition for one stable ref."""

    ref = str(item_ref or "").strip()
    if not ITEM_REF_RE.fullmatch(ref):
        return None
    resolved_catalogue = (
        normalize_live_item_catalogue(catalogue)
        if catalogue is not None
        else load_live_item_catalogue()
    )
    item = resolved_catalogue.get("items", {}).get(ref)
    if not isinstance(item, dict):
        return None
    return {"ref": ref, "item_ref": ref, **deepcopy(item)}


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
    cleaned = {
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
    if kind == "numeric":
        cleaned["integer_only"] = bool(raw.get("integer_only", True))
        placeholder = str(raw.get("placeholder") or "").strip()
        if placeholder:
            cleaned["placeholder"] = placeholder
    return cleaned


def _apply_round_defaults(base: dict[str, Any], raw: Any) -> None:
    """Apply valid per-stage timer defaults to a public metadata document."""

    defaults = raw.get("round_defaults") if isinstance(raw, dict) else None
    if not isinstance(defaults, dict):
        return
    for stage in STAGE_ORDER:
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


def _normalize_v1(
    raw: dict[str, Any],
    *,
    course: Any,
    module: Any,
    slot: Any,
) -> dict[str, Any]:
    """Normalize one legacy inline document without changing v1 behavior."""

    base = empty_live_class_metadata(course, module, slot)
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
    base["pages"] = normalize_live_pages(raw.get("pages"))
    base["team_challenge"] = normalize_team_challenge(raw.get("team_challenge"))
    _apply_round_defaults(base, raw)
    return base


def _placement_sort_key(row: dict[str, Any]) -> tuple[int, int, int, str]:
    """Return deterministic stage, page, order, and ref placement ordering."""

    stage = str(row.get("stage") or "")
    stage_index = STAGE_ORDER.index(stage) if stage in STAGES else len(STAGE_ORDER)
    page = row.get("page_number")
    page_number = int(page) if isinstance(page, int) else 0
    try:
        order = int(row.get("order") or 0)
    except (TypeError, ValueError):
        order = 0
    return stage_index, page_number, order, str(row.get("ref") or row.get("id") or "")


def _resolve_v2_placement(
    raw: Any,
    *,
    catalogue: dict[str, Any],
    index: int,
) -> dict[str, Any] | None:
    """Resolve and normalize one schema-v2 playlist placement."""

    if not isinstance(raw, dict):
        return None
    ref = str(raw.get("ref") or raw.get("item_ref") or "").strip()
    item = resolve_live_item(ref, catalogue=catalogue)
    if item is None:
        return None
    stage = str(raw.get("stage") or "round").strip().lower()
    if stage not in STAGES:
        stage = "round"
    page = raw.get("page_number")
    try:
        page_number = max(1, int(page)) if page not in (None, "") else None
    except (TypeError, ValueError):
        page_number = None
    try:
        order = max(1, int(raw.get("order") or index + 1))
    except (TypeError, ValueError):
        order = index + 1
    capabilities = item.get("capabilities")
    capability_rows = capabilities if isinstance(capabilities, dict) else {}
    supported_publish = _clean_modes(
        capability_rows.get("publish_modes"),
        allowed=PUBLISH_MODES,
    )
    requested_publish = _clean_modes(raw.get("publish_modes"), allowed=PUBLISH_MODES)
    publish_modes = [
        mode for mode in requested_publish if mode in supported_publish
    ]
    if not publish_modes:
        publish_modes = ["individual"]
    response_modes = _clean_modes(
        capability_rows.get("response_modes"),
        allowed=RESPONSE_MODES,
    )
    response_mode = str(raw.get("response_mode") or "individual").strip().lower()
    if (
        response_mode not in response_modes
        or (
            response_mode == "group_consensus"
            and "group_consensus" not in publish_modes
        )
        or (
            response_mode == "group_consensus"
            and item.get("item_type") != "question"
        )
    ):
        response_mode = "individual"
    resolved = {
        **item,
        "stage": stage,
        "page_number": page_number,
        "order": order,
        "default_status": "inactive",
        "default_visibility": False,
        "publish_modes": publish_modes,
        "response_mode": response_mode,
    }
    if resolved.get("item_type") == "question":
        question = _clean_question(resolved, index=index)
        if question is None:
            return None
        resolved.update(question)
        resolved["default_visibility"] = False
        resolved["default_status"] = "inactive"
        resolved["publish_modes"] = publish_modes
        resolved["response_mode"] = response_mode
    return resolved


def _legacy_media_projection(item: dict[str, Any]) -> dict[str, Any] | None:
    """Project one resolved media item into the schema-v1 media slot."""

    file_name = str(item.get("file") or item.get("url") or "").strip()
    if not file_name:
        return None
    return {
        "file": file_name,
        "title": str(item.get("title") or "").strip(),
        "shared_across_rounds": bool(item.get("shared_across_rounds", True)),
        "id": str(item.get("id") or "").strip(),
        "ref": str(item.get("ref") or "").strip(),
        "item_ref": str(item.get("item_ref") or item.get("ref") or "").strip(),
        "item_type": "media",
        "stage": item.get("stage"),
        "page_number": item.get("page_number"),
        "order": item.get("order"),
        "default_status": item.get("default_status"),
        "publish_modes": list(item.get("publish_modes") or []),
        "response_mode": item.get("response_mode"),
        "capabilities": deepcopy(item.get("capabilities") or {}),
    }


def _legacy_slides_projection(item: dict[str, Any]) -> dict[str, Any]:
    """Project one resolved slides item into the schema-v1 slides slot."""

    pages: list[int] = []
    for value in item.get("page_numbers") or []:
        try:
            pages.append(int(value))
        except (TypeError, ValueError):
            continue
    return {
        "deck_ref": str(item.get("deck_ref") or "").strip() or None,
        "page_numbers": pages,
        "id": str(item.get("id") or "").strip(),
        "ref": str(item.get("ref") or "").strip(),
        "item_ref": str(item.get("item_ref") or item.get("ref") or "").strip(),
        "item_type": "slides",
        "stage": item.get("stage"),
        "page_number": item.get("page_number"),
        "order": item.get("order"),
        "default_status": item.get("default_status"),
        "publish_modes": list(item.get("publish_modes") or []),
        "response_mode": item.get("response_mode"),
        "capabilities": deepcopy(item.get("capabilities") or {}),
    }


def _safe_item_projection(item: dict[str, Any]) -> dict[str, Any]:
    """Return a generic resolved item without teacher-only answer fields."""

    return {
        key: deepcopy(value)
        for key, value in item.items()
        if key not in TEACHER_ONLY_ITEM_FIELDS
    }


def _normalize_v2(
    raw: dict[str, Any],
    *,
    course: Any,
    module: Any,
    slot: Any,
    catalogue: Any,
) -> dict[str, Any]:
    """Resolve a thin schema-v2 playlist into compatible public metadata."""

    resolved_catalogue = (
        normalize_live_item_catalogue(catalogue)
        if catalogue is not None
        else load_live_item_catalogue()
    )
    base = empty_live_class_metadata(course, module, slot)
    base["schema_version"] = SCHEMA_V2
    base["catalogue_id"] = resolved_catalogue.get("catalogue_id")
    base["catalogue_version"] = resolved_catalogue.get("schema_version")
    items = [
        resolved
        for index, placement in enumerate(raw.get("items") or [])
        if (
            resolved := _resolve_v2_placement(
                placement,
                catalogue=resolved_catalogue,
                index=index,
            )
        )
        is not None
    ]
    items.sort(key=_placement_sort_key)
    base["questions"] = [
        deepcopy(item) for item in items if item.get("item_type") == "question"
    ]
    media = next(
        (item for item in items if item.get("item_type") == "media"),
        None,
    )
    if media is not None:
        base["media"] = _legacy_media_projection(media)
    slides = next(
        (item for item in items if item.get("item_type") == "slides"),
        None,
    )
    if slides is not None:
        base["slides"] = _legacy_slides_projection(slides)
    base["items"] = [_safe_item_projection(item) for item in items]
    base["pages"] = normalize_live_pages(raw.get("pages"))
    base["team_challenge"] = normalize_team_challenge(raw.get("team_challenge"))
    _apply_round_defaults(base, raw)
    return base


def normalize_live_class_metadata(
    raw: Any,
    *,
    course: Any,
    module: Any,
    slot: Any,
    catalogue: Any = None,
) -> dict[str, Any]:
    """Normalize a v1 document or resolve a v2 playlist into public metadata."""

    if not isinstance(raw, dict):
        return empty_live_class_metadata(course, module, slot)
    if raw.get("schema_version") == SCHEMA_V2:
        return _normalize_v2(
            raw,
            course=course,
            module=module,
            slot=slot,
            catalogue=catalogue,
        )
    return _normalize_v1(
        raw,
        course=course,
        module=module,
        slot=slot,
    )


def load_live_class_metadata(
    course: Any,
    module: Any,
    slot: Any,
    *,
    root: Path | None = None,
    catalogue_root: Path | None = None,
) -> dict[str, Any]:
    """Load and normalize one live-class JSON file.

    Missing or malformed files return the complete schema-v1 fallback so the
    live shell remains usable while content is authored. ``catalogue_root`` is
    optional and is primarily intended for isolated tests.
    """

    path = metadata_path(course, module, slot, root=root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    catalogue = (
        load_live_item_catalogue(root=catalogue_root)
        if catalogue_root is not None
        else None
    )
    return normalize_live_class_metadata(
        raw,
        course=course,
        module=module,
        slot=slot,
        catalogue=catalogue,
    )


def questions_for_stage(metadata: Any, stage: Any) -> list[dict[str, Any]]:
    """Return page/order-sorted public questions associated with one stage."""

    if not isinstance(metadata, dict):
        return []
    wanted = str(stage or "").strip().lower()
    rows = [
        deepcopy(row)
        for row in metadata.get("questions") or []
        if isinstance(row, dict) and row.get("stage") == wanted
    ]
    rows.sort(key=_placement_sort_key)
    return rows



def _clip_scan_text(raw: Any, limit: int = 140) -> str:
    """Return one line of scan text, clipped so the inventory stays readable.

    Args:
        raw: Stem, title, or other teacher-facing string.
        limit: Maximum characters including the ellipsis.
    """

    text = " ".join(str(raw or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _question_rows_for_summary(meta: dict[str, Any]) -> list[dict[str, Any]]:
    """Return deduped questions from merged live-lesson metadata.

    The ``questions`` list is the source of truth after class add/remove
    overlays. Item rows are only a fallback when that list was never built.

    Args:
        meta: Normalized live-class metadata, optionally class-merged.
    """

    questions = meta.get("questions")
    if isinstance(questions, list):
        source = [row for row in questions if isinstance(row, dict)]
    else:
        items = meta.get("items") if isinstance(meta.get("items"), list) else []
        source = [
            row
            for row in items
            if isinstance(row, dict)
            and str(row.get("item_type") or "").strip().lower() == "question"
        ]
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for row in source:
        key = str(row.get("id") or row.get("item_id") or row.get("ref") or "").strip()
        if key:
            if key in seen:
                continue
            seen.add(key)
        rows.append(row)
    return rows


def _media_rows_for_summary(meta: dict[str, Any]) -> list[dict[str, Any]]:
    """Return media items, or the legacy single media slot when items omit it.

    Args:
        meta: Normalized live-class metadata.
    """

    items = meta.get("items") if isinstance(meta.get("items"), list) else []
    rows = [
        row
        for row in items
        if isinstance(row, dict)
        and str(row.get("item_type") or "").strip().lower() == "media"
    ]
    if rows:
        return rows
    media = meta.get("media")
    if isinstance(media, dict) and (
        media.get("file") or media.get("title") or media.get("pack_label")
    ):
        return [media]
    return []


def _pack_role(item: dict[str, Any]) -> str:
    """Return ``questions_artifact`` or ``exploratory_media`` for one pack.

    Args:
        item: Resolved media catalogue row.
    """

    role = str(item.get("pack_role") or "").strip().lower()
    if role in {"questions_artifact", "exploratory_media"}:
        return role
    blob = " ".join(
        str(item.get(key) or "")
        for key in ("pack_label", "title", "file", "id")
    ).lower()
    if "artifact" in blob:
        return "questions_artifact"
    return "exploratory_media"


def _pack_label(item: dict[str, Any]) -> str:
    """Return a teacher-facing pack name, never a bare filename when titled.

    Args:
        item: Resolved media catalogue row.
    """

    label = str(item.get("pack_label") or item.get("title") or "").strip()
    if label:
        return label
    file_name = Path(str(item.get("file") or item.get("url") or "")).stem
    pretty = file_name.replace("-", " ").replace("_", " ").strip()
    return pretty or "Media"


def summarize_live_lesson(
    meta: dict[str, Any],
    *,
    module: Any = "",
    slot: Any = "",
) -> dict[str, Any]:
    """Summarize one live lesson for the Lesson Slides inventory.

    Question counts come from the merged question list (seed plus class
    add/remove). Media and artifact labels describe the pack. ``questions``
    holds short stems so a teacher can scan without opening the lesson.

    Args:
        meta: Normalized, optionally class-merged live-class metadata.
        module: Module token such as ``M1`` when metadata omits it.
        slot: Live-class token such as ``C2`` when metadata omits it.
    """

    question_rows = _question_rows_for_summary(meta)
    media_rows = _media_rows_for_summary(meta)
    media_labels: list[str] = []
    artifact_labels: list[str] = []
    media_file = ""
    for item in media_rows:
        label = _pack_label(item)
        file_name = Path(str(item.get("file") or item.get("url") or "")).name
        if _pack_role(item) == "questions_artifact":
            if label not in artifact_labels:
                artifact_labels.append(label)
        elif label not in media_labels:
            media_labels.append(label)
        if not media_file and file_name:
            media_file = file_name
    pages = meta.get("pages") if isinstance(meta.get("pages"), list) else []
    question_count = len(question_rows)
    question_word = "question" if question_count == 1 else "questions"
    scan_parts = [f"{question_count} {question_word}"]
    if media_labels:
        scan_parts.append("Media: " + "; ".join(media_labels))
    if artifact_labels:
        scan_parts.append("Artifact: " + "; ".join(artifact_labels))
    return {
        "course": normalize_course_code(meta.get("course")),
        "module": str(meta.get("module") or module),
        "live_class": str(meta.get("live_class") or slot),
        "page_count": len(pages) or len(default_math_pages()),
        "question_count": question_count,
        "questions": [
            {
                "id": str(row.get("id") or ""),
                "text": _clip_scan_text(
                    row.get("text")
                    or row.get("prompt")
                    or row.get("title")
                    or row.get("id")
                ),
            }
            for row in question_rows
        ],
        "media_file": media_file,
        "media_label": "; ".join(media_labels),
        "artifact_label": "; ".join(artifact_labels),
        "scan": " · ".join(scan_parts),
    }


def list_live_lesson_summaries(
    course: Any,
    *,
    root: Path | None = None,
    metadata_for: Any = None,
) -> list[dict[str, Any]]:
    """Return saved live-lesson files for a course, grouped by module and class.

    Args:
        course: Course code such as MCR3U.
        root: Optional metadata root. Defaults to the live-class seed tree.
        metadata_for: Optional ``(module, slot) -> metadata`` callback. When
            it returns a dict, that class-merged document is summarized so
            added and removed questions change the inventory count.
    """

    course_code = normalize_course_code(course)
    base = (root or metadata_root()) / course_code
    rows: list[dict[str, Any]] = []
    if not course_code or not base.is_dir():
        return rows
    for path in sorted(base.glob("M*/C*.json")):
        module = path.parent.name
        slot = path.stem
        meta = load_live_class_metadata(course_code, module, slot, root=root)
        if metadata_for is not None:
            merged = metadata_for(module, slot)
            if isinstance(merged, dict):
                meta = merged
        rows.append(summarize_live_lesson(meta, module=module, slot=slot))
    return rows


def playlist_document_from_metadata(
    meta: dict[str, Any],
    *,
    module: Any,
    slot: Any,
) -> dict[str, Any]:
    """Return a schema-v2 playlist document ready to write to disk.

    Args:
        meta: Normalized live-class metadata from the current session.
        module: Destination module code such as M2.
        slot: Destination live-class slot such as C3.
    """

    items: list[dict[str, Any]] = []
    source_items = meta.get("items") if isinstance(meta.get("items"), list) else []
    for index, row in enumerate(source_items, start=1):
        if not isinstance(row, dict):
            continue
        ref = str(row.get("ref") or row.get("item_ref") or row.get("id") or "").strip()
        if not ref:
            continue
        items.append(
            {
                "ref": ref,
                "stage": str(row.get("stage") or "round").strip().lower() or "round",
                "page_number": int(row.get("page_number") or index),
                "order": int(row.get("order") or index),
                "default_status": "inactive",
                "publish_modes": list(row.get("publish_modes") or ["individual"]),
                "response_mode": str(row.get("response_mode") or "individual"),
            }
        )
    return {
        "schema_version": 2,
        "course": normalize_course_code(meta.get("course")),
        "module": normalize_module(module),
        "live_class": normalize_slot(slot),
        "pages": normalize_live_pages(meta.get("pages")),
        "team_challenge": normalize_team_challenge(meta.get("team_challenge")),
        "items": items,
    }


def write_live_lesson_playlist(
    course: Any,
    module: Any,
    slot: Any,
    meta: dict[str, Any],
    *,
    root: Path | None = None,
) -> Path:
    """Write a live-lesson playlist JSON file and return its path.

    Args:
        course: Course code such as MCF3M.
        module: Destination module such as M2.
        slot: Destination live class such as C3.
        meta: Current normalized metadata to persist.
        root: Optional metadata root. Defaults to the live-class seed tree.
    """

    dest_root = root or metadata_root()
    course_code = normalize_course_code(course)
    module_code = normalize_module(module)
    slot_code = normalize_slot(slot)
    dest = dest_root / course_code / module_code
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{slot_code}.json"
    document = playlist_document_from_metadata(meta, module=module_code, slot=slot_code)
    document["course"] = course_code
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return path



def parse_live_lesson_code(raw: Any) -> tuple[str, str] | None:
    """Parse a compact live-lesson code such as ``M2C3``.

    Args:
        raw: Teacher-entered Save As value.
    """

    text_value = re.sub(r"[^A-Z0-9]", "", str(raw or "").strip().upper())
    match = re.fullmatch(r"(M[1-8])(C[1-4])", text_value)
    if not match:
        return None
    return match.group(1), match.group(2)

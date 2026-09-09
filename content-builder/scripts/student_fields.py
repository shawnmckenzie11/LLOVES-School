"""Whitelist of fields the student HTML renderer may read."""

from __future__ import annotations

ALLOWED_PART_IDS = frozenset({"hook", "minds-on", "action", "consolidation"})
ALLOWED_BLOCK_TYPES = frozenset(
    {
        "p",
        "h2",
        "equation",
        "task",
        "figure",
        "interactive",
        "practice-set",
        "example",
        "self-check",
        "goal",
        "reflect",
        "key-idea",
        "need-to-know",
        "dual-path",
        "support-fade",
    }
)
FORBIDDEN_STUDENT_SUBSTRINGS = (
    "JSXGraph",
    "GeoGebra",
    "lesson_id",
    "interactive_bridge",
    "teacher_notes",
    "YOUR_API_KEY",
    "rank 1",
    "catalogue",
    "agent",
    "verified seed",
    "no student login",
)


def assert_student_content(doc: dict) -> None:
    """Raise ValueError if a student document contains internal fields or types."""
    if doc.get("schema_version") != "student-content.v1":
        raise ValueError("student-content.v1 required")
    if "teacher_notes" in doc or "provenance" in doc:
        raise ValueError("teacher_notes and provenance must not live in student-content.json")
    allowed_root = {
        "schema_version",
        "lesson_id",
        "title",
        "credits",
        "parts",
        "goal",
        "you_will_need",
    }
    for key in doc:
        if key not in allowed_root:
            raise ValueError(f"unpermitted student field: {key}")
    for part in doc.get("parts", []):
        if part.get("id") not in ALLOWED_PART_IDS:
            raise ValueError(f"unpermitted part id: {part.get('id')}")
        _walk_blocks(part.get("blocks") or [])


def _walk_blocks(blocks: list) -> None:
    for block in blocks:
        kind = block.get("type")
        if kind not in ALLOWED_BLOCK_TYPES:
            raise ValueError(f"unpermitted block type: {kind}")
        if kind == "example":
            _walk_blocks(block.get("blocks") or [])
        elif kind == "dual-path":
            for path in block.get("paths") or []:
                _walk_blocks(path.get("blocks") or [])
        elif kind == "reflect":
            for q in block.get("questions") or []:
                if isinstance(q, dict):
                    _walk_blocks(q.get("blocks") or [])
        blob = " ".join(
            str(v)
            for k, v in block.items()
            if k not in {"blocks", "paths", "questions", "items", "stages"}
            and not isinstance(v, (dict, list))
        )
        for needle in FORBIDDEN_STUDENT_SUBSTRINGS:
            if needle.lower() in blob.lower() and needle in (
                "interactive_bridge",
                "teacher_notes",
                "YOUR_API_KEY",
            ):
                raise ValueError(f"internal token in student block: {needle}")

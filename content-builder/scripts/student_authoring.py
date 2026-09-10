#!/usr/bin/env python3
"""Canonical student-content save → validate → rebuild path.

The review UI and tests edit ``student-content.json`` only. ``instruction.json``
is not an authoring target.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "catalogue" / "contracts" / "student-content.schema.json"
PATCH_SCHEMA_PATH = ROOT / "catalogue" / "contracts" / "student-content-patch.schema.json"

from student_fields import assert_student_content  # noqa: E402

CANONICAL_PART_IDS = ("minds-on", "action", "consolidation")
CANONICAL_NAV = {
    "minds-on": "Minds On",
    "action": "Action",
    "consolidation": "Consolidation",
}
EDITABLE_FIELDS = {
    "p": "text",
    "h2": "text",
    "equation": "text",
    "task": "prompt",
    "self-check": "prompt",
    "figure": "alt",
    "card": "label",
}


def utc_now() -> str:
    """Return an aware UTC timestamp."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON object."""
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, payload: dict[str, Any] | list[Any]) -> None:
    """Write JSON with a trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def content_revision(doc: dict[str, Any]) -> str:
    """Stable SHA-256 of canonical student-content JSON."""
    blob = json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def student_schema() -> dict[str, Any]:
    """Load the student-content JSON Schema."""
    return load_json(SCHEMA_PATH)


def validate_student_content(doc: dict[str, Any]) -> None:
    """Raise if the document fails whitelist or schema checks."""
    assert_student_content(doc)
    jsonschema.validate(doc, student_schema())
    ids = [part.get("id") for part in doc.get("parts") or []]
    if tuple(ids) != CANONICAL_PART_IDS:
        raise ValueError(f"parts must be {CANONICAL_PART_IDS} in order, got {ids}")
    seen: set[str] = set()
    for block in iter_blocks(doc):
        bid = block.get("id")
        if not bid or not isinstance(bid, str):
            raise ValueError("every block needs a stable id")
        if bid in seen:
            raise ValueError(f"duplicate block id: {bid}")
        seen.add(bid)


def iter_blocks(doc: dict[str, Any]):
    """Yield every block, including nested example blocks."""
    for part in doc.get("parts") or []:
        yield from _walk_blocks(part.get("blocks") or [])


def _walk_blocks(blocks: list[Any]):
    for block in blocks:
        if not isinstance(block, dict):
            continue
        yield block
        if block.get("type") in {"example", "card"}:
            yield from _walk_blocks(block.get("blocks") or [])


def find_block(doc: dict[str, Any], block_id: str) -> dict[str, Any]:
    """Return the block with ``block_id`` or raise KeyError."""
    for block in iter_blocks(doc):
        if block.get("id") == block_id:
            return block
    raise KeyError(f"unknown block id: {block_id}")


def assign_missing_ids(doc: dict[str, Any]) -> dict[str, Any]:
    """Fill stable ``part--type--n`` ids when missing. Existing ids are kept."""
    used = {block.get("id") for block in iter_blocks(doc) if block.get("id")}
    counters: dict[str, int] = {}

    def _assign(blocks: list[Any], prefix: str) -> None:
        for block in blocks:
            kind = str(block.get("type") or "block")
            key = f"{prefix}--{kind}"
            if not block.get("id"):
                n = counters.get(key, 0) + 1
                counters[key] = n
                candidate = f"{key}--{n}"
                while candidate in used:
                    n += 1
                    candidate = f"{key}--{n}"
                counters[key] = n
                block["id"] = candidate
                used.add(candidate)
            if block.get("type") in {"example", "card"}:
                _assign(block.get("blocks") or [], block["id"])

    for part in doc.get("parts") or []:
        _assign(part.get("blocks") or [], str(part.get("id")))
    return doc


def fold_hook_into_minds_on(doc: dict[str, Any]) -> dict[str, Any]:
    """Move a ``hook`` part into Minds On and keep the three canonical tabs."""
    parts = list(doc.get("parts") or [])
    hook = next((p for p in parts if p.get("id") == "hook"), None)
    minds = next((p for p in parts if p.get("id") == "minds-on"), None)
    if minds is None:
        raise ValueError("student-content needs a minds-on part")
    if hook is not None:
        minds["blocks"] = list(hook.get("blocks") or []) + list(minds.get("blocks") or [])
        parts = [p for p in parts if p.get("id") != "hook"]
    minds["nav_label"] = CANONICAL_NAV["minds-on"]
    by_id = {p["id"]: p for p in parts}
    ordered = []
    for pid in CANONICAL_PART_IDS:
        if pid not in by_id:
            raise ValueError(f"missing part {pid}")
        part = by_id[pid]
        part["nav_label"] = CANONICAL_NAV[pid]
        ordered.append(part)
    extra = [p for p in parts if p["id"] not in CANONICAL_PART_IDS]
    if extra:
        raise ValueError(f"unexpected parts: {[p.get('id') for p in extra]}")
    doc["parts"] = ordered
    return doc


def locked_block_ids(lesson_dir: Path) -> set[str]:
    """Return block ids locked in ``locks.json``."""
    path = lesson_dir / "locks.json"
    if not path.is_file():
        return set()
    data = load_json(path)
    keys = data.get("locked_keys") or []
    locked = set()
    for key in keys:
        if isinstance(key, str) and key.startswith("block:"):
            locked.add(key.split(":", 1)[1])
    return locked


def apply_patch(
    doc: dict[str, Any],
    ops: list[dict[str, Any]],
    *,
    locked: set[str] | None = None,
) -> dict[str, Any]:
    """Apply replace ops against block fields. Does not write disk."""
    locked = locked or set()
    if PATCH_SCHEMA_PATH.is_file():
        jsonschema.validate({"ops": ops}, load_json(PATCH_SCHEMA_PATH))
    for op in ops:
        if op.get("op", "replace") != "replace":
            raise ValueError(f"unsupported patch op: {op.get('op')}")
        block_id = op["block_id"]
        field = op["field"]
        if block_id in locked:
            raise PermissionError(f"locked block: {block_id}")
        block = find_block(doc, block_id)
        expected = EDITABLE_FIELDS.get(block.get("type"))
        if expected is None:
            raise ValueError(f"block {block_id} is not text-editable")
        if field != expected:
            raise ValueError(f"block {block_id} editable field is {expected}, not {field}")
        block[field] = op["value"]
    return doc


def revision_path(lesson_dir: Path) -> Path:
    """Sidecar that stores the current student-content revision hash."""
    return lesson_dir / "student-content.revision.json"


def read_revision(lesson_dir: Path) -> str | None:
    """Return the stored revision hash, if any."""
    path = revision_path(lesson_dir)
    if not path.is_file():
        return None
    data = load_json(path)
    rev = data.get("revision")
    return str(rev) if rev else None


def write_revision(lesson_dir: Path, doc: dict[str, Any]) -> str:
    """Persist the revision sidecar and return the hash."""
    rev = content_revision(doc)
    dump_json(
        revision_path(lesson_dir),
        {
            "schema_version": "student-content-revision.v1",
            "lesson_id": doc.get("lesson_id"),
            "revision": rev,
            "updated_at": utc_now(),
        },
    )
    return rev


def save_student_content(
    lesson_dir: Path,
    doc: dict[str, Any],
    *,
    out_dir: Path | None = None,
    components: Path | None = None,
    rebuild: bool = True,
) -> dict[str, Any]:
    """Validate, write student-content.json, and rebuild the export HTML."""
    from build_lesson import build_lesson

    validate_student_content(doc)
    dest = lesson_dir / "student-content.json"
    dump_json(dest, doc)
    rev = write_revision(lesson_dir, doc)
    rebuilt = None
    if rebuild:
        course = lesson_dir.parent.name
        lesson_id = lesson_dir.name
        target = out_dir or (lesson_dir.parents[2] / "build" / course / lesson_id)
        rebuilt = str(build_lesson(lesson_dir, target, components=components))
    return {"ok": True, "revision": rev, "path": str(dest), "rebuilt": rebuilt}


def save_patch(
    lesson_dir: Path,
    ops: list[dict[str, Any]],
    *,
    base_revision: str | None = None,
    out_dir: Path | None = None,
    components: Path | None = None,
    rebuild: bool = True,
) -> dict[str, Any]:
    """Apply a patch against the on-disk student-content document and rebuild."""
    dest = lesson_dir / "student-content.json"
    doc = load_json(dest)
    current = content_revision(doc)
    stored = read_revision(lesson_dir)
    if stored and stored != current:
        write_revision(lesson_dir, doc)
        stored = current
    if base_revision and stored and base_revision != stored:
        raise RuntimeError("stale revision: reload student-content before saving")
    if base_revision and not stored and base_revision != current:
        raise RuntimeError("stale revision: reload student-content before saving")
    apply_patch(doc, ops, locked=locked_block_ids(lesson_dir))
    return save_student_content(
        lesson_dir,
        doc,
        out_dir=out_dir,
        components=components,
        rebuild=rebuild,
    )


def content_diff(current: dict[str, Any], previous: dict[str, Any] | None) -> list[dict[str, str]]:
    """Editable-field changes between two student-content documents."""
    previous = previous or {"parts": []}
    old_by = {block.get("id"): block for block in iter_blocks(previous) if block.get("id")}
    rows = []
    for block in iter_blocks(current):
        field = EDITABLE_FIELDS.get(block.get("type"))
        if not field:
            continue
        bid = block.get("id") or ""
        before = str((old_by.get(bid) or {}).get(field) or "")
        after = str(block.get(field) or "")
        if before != after:
            rows.append({"block_id": bid, "field": field, "before": before, "after": after})
    return rows


def approved_path(lesson_dir: Path) -> Path:
    """Snapshot of the last in-tool approved student-content document."""
    return lesson_dir / "student-content.approved.json"


def approval_meta_path(lesson_dir: Path) -> Path:
    """Metadata sidecar for in-tool approval (not LMS publish)."""
    return lesson_dir / "student-content.approval.json"


def read_approved(lesson_dir: Path) -> dict[str, Any] | None:
    """Load the approved snapshot if present."""
    path = approved_path(lesson_dir)
    if not path.is_file():
        return None
    return load_json(path)


def approve_student_content(lesson_dir: Path) -> dict[str, Any]:
    """Record an in-tool approval. Does not publish to the LMS."""
    dest = lesson_dir / "student-content.json"
    doc = load_json(dest)
    validate_student_content(doc)
    dump_json(approved_path(lesson_dir), doc)
    meta = {
        "schema_version": "student-content-approval.v1",
        "lesson_id": doc.get("lesson_id"),
        "revision": content_revision(doc),
        "approved_at": utc_now(),
        "state": "approved",
        "note": "In-tool approval. Not an LMS publish.",
    }
    dump_json(approval_meta_path(lesson_dir), meta)
    return {"ok": True, "approval": meta}


def prepare_canonical_document(doc: dict[str, Any]) -> dict[str, Any]:
    """Fold the hook tab, assign ids, and validate."""
    fold_hook_into_minds_on(doc)
    assign_missing_ids(doc)
    validate_student_content(doc)
    return doc

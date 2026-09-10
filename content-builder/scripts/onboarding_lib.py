"""MCF3M onboarding store: import, reconcile, context, change tracking.

JSON under ``content-builder/catalogue/onboarding/``. Not the school LMS.
Student HTML never reads this module.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

SCHEMA_VERSION = "onboarding-store.v1"
STORE_FILES = (
    "course.json",
    "identities.json",
    "curriculum-sources.json",
    "mappings.json",
    "rules.json",
    "conflicts.json",
    "lesson-revisions.json",
    "change-index.json",
    "known-concerns.json",
)

RULE_SECTIONS = (
    {
        "id": "rule.authority-and-scope",
        "heading": "Authority and scope",
        "scope": ["identity", "locks"],
        "affects": ["identity"],
        "enforcement_owner": "parent",
    },
    {
        "id": "rule.teaching-purpose",
        "heading": "Teaching purpose",
        "scope": ["async_tabs"],
        "affects": ["brief", "hook", "flow"],
        "enforcement_owner": "lesson-director",
    },
    {
        "id": "rule.three-part-html",
        "heading": "Three-part HTML contract",
        "scope": ["async_tabs"],
        "affects": ["minds-on", "action", "consolidation", "student_html"],
        "enforcement_owner": "lesson-engineer",
    },
    {
        "id": "rule.student-language",
        "heading": "Student language and flow",
        "scope": ["student_language"],
        "affects": ["student_prose"],
        "enforcement_owner": "student-copywriter",
    },
    {
        "id": "rule.practice-feedback",
        "heading": "Practice, reinforcement and feedback",
        "scope": ["practice", "feedback"],
        "affects": ["practice", "feedback"],
        "enforcement_owner": "practice-designer",
    },
    {
        "id": "rule.model-star",
        "heading": "Every generated word-problem solution: model-STAR plus diagram",
        "scope": ["practice"],
        "affects": ["word_problem_solutions"],
        "enforcement_owner": "student-copywriter",
    },
    {
        "id": "rule.questions-hooks-resources",
        "heading": "Questions, hooks and resources",
        "scope": ["hooks_resources"],
        "affects": ["hooks", "resources"],
        "enforcement_owner": "hook-curator",
    },
    {
        "id": "rule.live-slides",
        "heading": "Live Slides and portfolio boundary",
        "scope": ["live_slides"],
        "affects": ["live_slides"],
        "enforcement_owner": "parent",
    },
    {
        "id": "rule.review-production",
        "heading": "Review and production",
        "scope": ["review"],
        "affects": ["review"],
        "enforcement_owner": "parent",
    },
)

ROLE_RULE_SCOPES = {
    "lesson-director": {"identity", "async_tabs", "locks"},
    "student-copywriter": {"student_language", "practice"},
    "practice-designer": {"practice", "feedback"},
    "hook-curator": {"hooks_resources", "async_tabs"},
    "interaction-designer": {"feedback", "async_tabs"},
    "formative-feedback-designer": {"feedback"},
    "visual-experience-designer": {"async_tabs"},
    "lesson-engineer": {"async_tabs"},
    "lesson-verifier": {
        "identity",
        "async_tabs",
        "student_language",
        "practice",
        "feedback",
        "hooks_resources",
        "review",
    },
}

KNOWN_CONCERN_IDS = ("B1.6-partial-M1L2", "B3.4-tvm", "B2-C3-data-application")


class BuilderPaths:
    """Resolved paths for one content-builder root."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.packages = self.root / "packages"
        self.store_root = self.root / "catalogue" / "onboarding"
        self.lessons = self.root / "lessons"
        self.resources = self.root / "catalogue" / "resources"
        self.repo = self.root.parent
        self.ontario_seed = self.repo / "lms" / "seeds" / "mcf3m_expectations.json"

    def store(self, course: str) -> Path:
        """Course onboarding directory."""
        return self.store_root / course

    def package(self, name: str = "MCF3M-builder-input") -> Path:
        """Verified input package directory."""
        return self.packages / name


def utc_now() -> str:
    """ISO-8601 UTC timestamp without microseconds."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_bytes(data: bytes) -> str:
    """Hex digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """Hex digest of UTF-8 text."""
    return sha256_bytes(text.encode("utf-8"))


def load_json(path: Path) -> Any:
    """Load JSON from path."""
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, payload: Any) -> None:
    """Write pretty JSON with a trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def canonical_hash(payload: Any) -> str:
    """Stable hash of a JSON-serialisable value."""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return sha256_text(blob)


def empty_store() -> dict[str, Any]:
    """Empty in-memory store."""
    return {
        "course": None,
        "identities": [],
        "curriculum": [],
        "mappings": [],
        "rules": [],
        "conflicts": [],
        "revisions": [],
        "change_index": [],
        "known_concerns": [],
        "batches": [],
        "head": None,
    }


def load_store(paths: BuilderPaths, course: str) -> dict[str, Any]:
    """Load onboarding JSON, or empty if the course has not been imported."""
    folder = paths.store(course)
    head_path = folder / "HEAD.json"
    if not head_path.is_file():
        return empty_store()
    store = empty_store()
    store["head"] = load_json(head_path)
    mapping = {
        "course": "course.json",
        "identities": "identities.json",
        "curriculum": "curriculum-sources.json",
        "mappings": "mappings.json",
        "rules": "rules.json",
        "conflicts": "conflicts.json",
        "revisions": "lesson-revisions.json",
        "change_index": "change-index.json",
        "known_concerns": "known-concerns.json",
    }
    for key, name in mapping.items():
        path = folder / name
        if path.is_file():
            store[key] = load_json(path)
    batches_index = folder / "batches" / "index.json"
    if batches_index.is_file():
        store["batches"] = load_json(batches_index)
    return store


def operational_fingerprint(store: dict[str, Any]) -> str:
    """Hash of records a second import must not duplicate."""
    return canonical_hash(
        {
            "course": store.get("course"),
            "identities": store.get("identities"),
            "curriculum": store.get("curriculum"),
            "mappings": store.get("mappings"),
            "rules": store.get("rules"),
            "conflicts": [c for c in store.get("conflicts") or [] if c.get("status") == "unresolved"],
            "revisions": store.get("revisions"),
            "known_concerns": store.get("known_concerns"),
        }
    )


def _write_store_files(folder: Path, store: dict[str, Any], *, fingerprint: str, batch_id: str | None) -> list[str]:
    """Persist store files. Returns relative names written."""
    written = []
    payload = {
        "course.json": store["course"],
        "identities.json": store["identities"],
        "curriculum-sources.json": store["curriculum"],
        "mappings.json": store["mappings"],
        "rules.json": store["rules"],
        "conflicts.json": store["conflicts"],
        "lesson-revisions.json": store["revisions"],
        "change-index.json": store["change_index"],
        "known-concerns.json": store["known_concerns"],
    }
    for name, data in payload.items():
        dump_json(folder / name, data)
        written.append(name)
    dump_json(
        folder / "HEAD.json",
        {
            "schema_version": SCHEMA_VERSION,
            "course_code": (store.get("course") or {}).get("course_code"),
            "fingerprint": fingerprint,
            "latest_batch_id": batch_id,
        },
    )
    written.append("HEAD.json")
    dump_json(folder / "batches" / "index.json", store.get("batches") or [])
    written.append("batches/index.json")
    return written


def validate_input_package(package: Path) -> dict[str, Any]:
    """Run the package validator (no network)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("validate_package", package / "validate_package.py")
    if spec is None or spec.loader is None:
        raise FileNotFoundError(package / "validate_package.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main()


def package_hash(package: Path) -> str:
    """Hash of SHA256SUMS.json (covers every packaged file except itself)."""
    return sha256_bytes((package / "SHA256SUMS.json").read_bytes())


def scan_builder_lessons(paths: BuilderPaths, course: str) -> list[dict[str, Any]]:
    """Existing authored lesson folders for one course."""
    found: list[dict[str, Any]] = []
    course_dir = paths.lessons / course
    if not course_dir.is_dir():
        return found
    for brief_path in sorted(course_dir.glob("*/lesson-brief.json")):
        brief = load_json(brief_path)
        folder = brief_path.parent
        locks = load_json(folder / "locks.json") if (folder / "locks.json").is_file() else None
        student = folder / "student-content.json"
        student_hash = sha256_bytes(student.read_bytes()) if student.is_file() else None
        found.append(
            {
                "internal_id": brief.get("lesson_id") or folder.name,
                "course_code": brief.get("course_code") or course,
                "module_number": brief.get("module"),
                "lesson_number": brief.get("lesson_number"),
                "title": brief.get("title"),
                "builder_path": str(folder.relative_to(paths.root)),
                "locks": (locks or {}).get("locked_keys") or [],
                "student_content_hash": student_hash,
                "brief_codes": [e.get("code") for e in brief.get("expectations") or [] if e.get("code")],
                "content_status": "drafted" if student.is_file() else "not_drafted",
            }
        )
    return found


def load_ontario_seed_texts(paths: BuilderPaths) -> dict[str, str]:
    """Code → statement from the committed Ontario seed, if present."""
    if not paths.ontario_seed.is_file():
        return {}
    data = load_json(paths.ontario_seed)
    out: dict[str, str] = {}
    for strand in data.get("strands") or []:
        for spec in strand.get("specific") or []:
            code = spec.get("code")
            statement = spec.get("statement")
            if code and statement:
                out[code] = statement
    return out


def parse_instructional_rules(markdown: str, source_hash: str) -> list[dict[str, Any]]:
    """Split instructional-rules.md on ## headings into versioned records."""
    rules: list[dict[str, Any]] = []
    for spec in RULE_SECTIONS:
        heading = spec["heading"]
        pattern = rf"^## {re.escape(heading)}\s*$"
        match = re.search(pattern, markdown, re.M)
        if not match:
            raise ValueError(f"missing rule heading: {heading}")
        start = match.end()
        nxt = re.search(r"^## ", markdown[start:], re.M)
        end = start + nxt.start() if nxt else len(markdown)
        body = markdown[start:end].strip()
        rules.append(
            {
                "id": spec["id"],
                "scope": spec["scope"],
                "requirement": body,
                "authority": "package instructional-rules.md; current repo rules take priority where they conflict",
                "version": 1,
                "source_hash": source_hash,
                "enforcement_owner": spec["enforcement_owner"],
                "supersedes": [],
                "affects": spec["affects"],
            }
        )
    return rules


def existing_resource_ids(paths: BuilderPaths) -> list[str]:
    """Approved/selected catalogue resource ids (not the empty package list)."""
    if not paths.resources.is_dir():
        return []
    ids: list[str] = []
    for path in sorted(paths.resources.glob("*.json")):
        rec = load_json(path)
        if isinstance(rec, dict) and rec.get("id"):
            ids.append(rec["id"])
    return ids


def _conflict(
    cid: str,
    record: str,
    field: str,
    existing: Any,
    incoming: Any,
    evidence: str,
    *,
    auto: bool = False,
) -> dict[str, Any]:
    return {
        "id": cid,
        "record": record,
        "field": field,
        "existing": existing,
        "incoming": incoming,
        "evidence": evidence,
        "status": "unresolved",
        "auto_resolvable": auto,
    }


def _attach_builder_fields(
    match: dict[str, Any] | None, builder: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Keep live brief codes and locks even when the store already has a Drive id."""
    if match is None:
        return None
    merged = dict(match)
    for rec in builder:
        same_id = rec.get("internal_id") and rec.get("internal_id") == merged.get("internal_id")
        same_slot = (
            rec.get("course_code") == merged.get("course_code")
            and rec.get("module_number") == merged.get("module_number")
            and rec.get("lesson_number") == merged.get("lesson_number")
        )
        if not (same_id or same_slot):
            continue
        for key in ("brief_codes", "locks", "student_content_hash", "builder_path", "content_status"):
            if rec.get(key) not in (None, [], ""):
                merged[key] = rec[key]
        break
    return merged


def _match_identity(
    incoming: dict[str, Any],
    store_identities: list[dict[str, Any]],
    builder: list[dict[str, Any]],
) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
    """Return (method, match, extras). extras is used for conflicts."""
    drive_id = incoming["drive_folder_id"]
    for rec in store_identities:
        if rec.get("drive_folder_id") == drive_id:
            return "drive_id", _attach_builder_fields(rec, builder), []
    for rec in builder:
        if rec.get("drive_folder_id") == drive_id:
            return "drive_id", rec, []

    keyed = [
        rec
        for rec in store_identities + builder
        if rec.get("course_code") == incoming["course_code"]
        and rec.get("module_number") == incoming["module_number"]
        and rec.get("lesson_number") == incoming["lesson_number"]
    ]
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rec in keyed:
        token = rec.get("internal_id") or rec.get("stable_key") or rec.get("builder_path") or ""
        if token in seen:
            continue
        seen.add(token)
        unique.append(rec)
    if len(unique) > 1:
        return "conflict", None, unique
    if len(unique) == 1:
        return "course_module_lesson", _attach_builder_fields(unique[0], builder), []
    return "create", None, []


def _curriculum_records(pack_cur: dict[str, Any], processes: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    edition = pack_cur.get("curriculum_edition") or "2007"
    course = pack_cur["course_code"]
    for item in pack_cur.get("overall_expectations") or []:
        text = item["text"]
        records.append(
            {
                "id": f"{course}.overall.{item['code']}",
                "course_code": course,
                "edition": edition,
                "expectation_type": "overall",
                "code": item["code"],
                "text": text,
                "sample_problem": None,
                "source_locator": {
                    "source_id": item.get("source_id"),
                    "printed_page": item.get("printed_page"),
                    "pdf_page_1_based": item.get("pdf_page_1_based"),
                },
                "text_hash": sha256_text(text),
                "revision": 1,
                "verification_status": "package_verified",
            }
        )
    for item in pack_cur.get("specific_expectations") or []:
        text = item["text"]
        records.append(
            {
                "id": f"{course}.specific.{item['code']}",
                "course_code": course,
                "edition": edition,
                "expectation_type": "specific",
                "code": item["code"],
                "text": text,
                "sample_problem": item.get("sample_problem"),
                "source_block": item.get("source_block"),
                "source_locator": {"source_id": item.get("source_id")},
                "text_hash": sha256_text(text),
                "revision": 1,
                "verification_status": "package_verified",
            }
        )
    for item in processes.get("expectations") or []:
        name = item["name"]
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        text = item["text"]
        records.append(
            {
                "id": f"{course}.process.{slug}",
                "course_code": course,
                "edition": edition,
                "expectation_type": "process",
                "code": slug,
                "name": name,
                "text": text,
                "sample_problem": None,
                "source_locator": {
                    "source_id": item.get("source_id"),
                    "printed_page": item.get("printed_page"),
                    "pdf_page_1_based": item.get("pdf_page_1_based"),
                    "scope": item.get("scope"),
                },
                "text_hash": sha256_text(text),
                "revision": 1,
                "verification_status": "package_verified",
            }
        )
    return records


def _merge_curriculum(
    incoming: list[dict[str, Any]],
    existing: list[dict[str, Any]],
    seed_texts: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Add versioned sources; flag text disagreements. Never mutate old text."""
    by_id = {r["id"]: deepcopy(r) for r in existing}
    conflicts: list[dict[str, Any]] = []
    for rec in incoming:
        prior = by_id.get(rec["id"])
        if prior is None:
            by_id[rec["id"]] = rec
        elif prior.get("text") != rec["text"]:
            new_rev = deepcopy(rec)
            new_rev["revision"] = int(prior.get("revision") or 1) + 1
            by_id[rec["id"]] = new_rev
            conflicts.append(
                _conflict(
                    f"conflict.source-text.{rec['id']}",
                    "curriculum_source",
                    "text",
                    prior.get("text"),
                    rec["text"],
                    "Incoming exact text disagrees with an existing source revision.",
                )
            )
        if rec.get("expectation_type") == "specific":
            seed = seed_texts.get(rec["code"])
            if seed is not None and seed != rec["text"]:
                conflicts.append(
                    _conflict(
                        f"conflict.seed-text.{rec['code']}",
                        "curriculum_source",
                        "text",
                        seed,
                        rec["text"],
                        f"lms/seeds wording differs from package source for {rec['code']}.",
                    )
                )
    return list(by_id.values()), conflicts


def _known_concerns(lessons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    notes = []
    for lesson in lessons:
        for note in lesson.get("mapping_notes") or []:
            notes.append(
                {
                    "id": f"note.{lesson['lesson_id']}.{sha256_text(note)[:10]}",
                    "stable_key": lesson["lesson_id"],
                    "codes": lesson.get("proposed_specific_expectation_codes") or [],
                    "detail": note,
                    "review_status": "unresolved",
                }
            )
    # Stable labels the review UI must always surface.
    labels = {
        "B1.6-partial-M1L2": "Partial B1.6 comparison in M1L2 (linear/quadratic only).",
        "B3.4-tvm": "B3.4 requires TVM Solver work for rate or number of periods.",
        "B2-C3-data-application": "B2 and C3 need actual data-collection/application tasks, not titles alone.",
    }
    out = [{"id": cid, "detail": labels[cid], "review_status": "unresolved"} for cid in KNOWN_CONCERN_IDS]
    for note in notes:
        out.append(note)
    return out


def _identity_record(
    incoming: dict[str, Any],
    method: str,
    match: dict[str, Any] | None,
    title_diffs: dict[str, dict[str, Any]],
    prior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    internal = incoming["lesson_id"]
    builder_path = None
    locks: list[str] = []
    content_status = "not_drafted"
    student_hash = None
    display = None
    if match:
        internal = match.get("internal_id") or internal
        builder_path = match.get("builder_path")
        locks = match.get("locks") or []
        content_status = match.get("content_status") or content_status
        student_hash = match.get("student_content_hash")
        display = match.get("display_title")
    if prior:
        internal = prior.get("internal_id") or internal
        builder_path = prior.get("builder_path") or builder_path
        locks = prior.get("locks") or locks
        content_status = prior.get("content_status") or content_status
        student_hash = prior.get("student_content_hash") or student_hash
        display = prior.get("display_title") if prior.get("display_title") is not None else display
        method = prior.get("match_method") or method
    diff = title_diffs.get(incoming["lesson_id"])
    return {
        "stable_key": incoming["lesson_id"],
        "internal_id": internal,
        "course_code": incoming["course_code"],
        "module_number": incoming["module_number"],
        "lesson_number": incoming["lesson_number"],
        "module_title": incoming["module_title"],
        "drive_module_folder_id": incoming["drive_module_folder_id"],
        "drive_module_folder_title": incoming["drive_module_folder_title"],
        "drive_parent_folder_id": incoming["drive_parent_folder_id"],
        "drive_folder_id": incoming["drive_folder_id"],
        "drive_folder_url": incoming["drive_folder_url"],
        "source_title": incoming["drive_title"],
        "original_title": incoming["original_title"],
        "display_title": display,
        "title_difference": diff,
        "order_source": incoming.get("order_source"),
        "builder_path": builder_path,
        "match_method": method,
        "content_status": content_status,
        "historical_package_content_status": incoming.get("content_status"),
        "locks": locks,
        "student_content_hash": student_hash,
        "live_class": False,
    }


def _mapping_records(
    incoming: dict[str, Any],
    identity: dict[str, Any],
    builder_match: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    mappings: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    proposed = incoming.get("proposed_specific_expectation_codes") or []
    notes = incoming.get("mapping_notes") or []
    note_text = " ".join(notes)
    for code in proposed:
        mappings.append(
            {
                "id": f"map.{incoming['lesson_id']}.{code}.proposal",
                "stable_key": incoming["lesson_id"],
                "internal_id": identity["internal_id"],
                "section": None,
                "expectation_ref": f"{incoming['course_code']}.specific.{code}",
                "code": code,
                "rationale": incoming.get("mapping_rationale"),
                "partial_coverage_note": note_text or None,
                "origin": "package_proposal",
                "review_status": "proposed_review_required",
                "coverage_evidence": "mapped",
            }
        )
    teacher_codes = (builder_match or {}).get("brief_codes") or []
    for code in teacher_codes:
        mappings.append(
            {
                "id": f"map.{incoming['lesson_id']}.{code}.teacher",
                "stable_key": incoming["lesson_id"],
                "internal_id": identity["internal_id"],
                "section": None,
                "expectation_ref": f"{incoming['course_code']}.specific.{code}",
                "code": code,
                "rationale": "Present on the existing lesson brief; not auto-approved by import.",
                "partial_coverage_note": None,
                "origin": "teacher_brief",
                "review_status": "present_unreviewed",
                "coverage_evidence": "mapped",
            }
        )
    if teacher_codes and sorted(teacher_codes) != sorted(proposed):
        conflicts.append(
            _conflict(
                f"conflict.mapping.codes.{incoming['lesson_id']}",
                "curriculum_mapping",
                "specific_codes",
                teacher_codes,
                proposed,
                "Package proposal differs from existing brief expectation codes. Keep both; do not auto-approve.",
            )
        )
    return mappings, conflicts


def _revision_for(
    identity: dict[str, Any], rules: list[dict[str, Any]], mappings: list[dict[str, Any]]
) -> dict[str, Any]:
    rule_ids = [r["id"] for r in rules]
    sources = sorted(
        {m["expectation_ref"] for m in mappings if m.get("stable_key") == identity["stable_key"]}
    )
    return {
        "internal_id": identity["internal_id"],
        "stable_key": identity["stable_key"],
        "content_revision": identity.get("student_content_hash"),
        "source_dependencies": sources,
        "rule_dependencies": rule_ids,
        "teacher_overrides": [],
        "locks": identity.get("locks") or [],
        "review_state": {
            "source_verification": "package_verified",
            "mapping_review": "proposed_review_required",
            "content_approval": "not_approved",
            "validation_result": None,
        },
        "needs_review": [],
    }


def build_proposed_store(
    paths: BuilderPaths,
    package: Path,
    existing: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reconcile the package against the current store. Does not write."""
    course_doc = load_json(package / "data" / "course.json")
    lessons_doc = load_json(package / "data" / "lessons.json")
    curriculum_doc = load_json(package / "data" / "curriculum.json")
    processes_doc = load_json(package / "data" / "curriculum-processes.json")
    recon = load_json(package / "data" / "reconciliation.json")
    resources_doc = load_json(package / "data" / "resources.json")
    rules_md = (package / "rules" / "instructional-rules.md").read_text(encoding="utf-8")
    rules_hash = sha256_text(rules_md)

    course_code = course_doc["course_code"]
    builder = scan_builder_lessons(paths, course_code)
    seed_texts = load_ontario_seed_texts(paths)
    title_diffs = {d["lesson_id"]: d for d in recon.get("title_differences") or []}

    incoming_curriculum = _curriculum_records(curriculum_doc, processes_doc)
    curriculum, cur_conflicts = _merge_curriculum(
        incoming_curriculum, existing.get("curriculum") or [], seed_texts
    )
    incoming_rules = parse_instructional_rules(rules_md, rules_hash)
    # Do not replace a newer stored rule version with an older identical heading.
    merged_rules: list[dict[str, Any]] = []
    existing_rules = {r["id"]: r for r in existing.get("rules") or []}
    rule_conflicts: list[dict[str, Any]] = []
    for rule in incoming_rules:
        prior = existing_rules.get(rule["id"])
        if prior and int(prior.get("version") or 1) > int(rule.get("version") or 1):
            merged_rules.append(prior)
            rule_conflicts.append(
                _conflict(
                    f"conflict.rule.newer.{rule['id']}",
                    "instructional_rule",
                    "version",
                    prior.get("version"),
                    rule.get("version"),
                    "Stored rule is newer than the package copy; package did not overwrite it.",
                )
            )
        else:
            merged_rules.append(rule)

    identities: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = list(cur_conflicts) + list(rule_conflicts)
    counts = {"imported": 0, "matched": 0, "created": 0, "unchanged": 0, "conflicted": 0}
    existing_by_key = {i.get("stable_key"): i for i in existing.get("identities") or []}

    for incoming in lessons_doc["lessons"]:
        method, match, extras = _match_identity(incoming, existing.get("identities") or [], builder)
        if method == "conflict":
            counts["conflicted"] += 1
            conflicts.append(
                _conflict(
                    f"conflict.identity.{incoming['lesson_id']}",
                    "lesson_identity",
                    "match",
                    [e.get("internal_id") for e in extras],
                    incoming["lesson_id"],
                    "More than one existing record qualifies. Did not guess from title.",
                )
            )
            continue
        identity = _identity_record(
            incoming, method, match, title_diffs, prior=existing_by_key.get(incoming["lesson_id"])
        )
        prior = existing_by_key.get(identity["stable_key"])
        if prior and canonical_hash(prior) == canonical_hash(identity):
            counts["unchanged"] += 1
        elif method == "create":
            counts["created"] += 1
        else:
            counts["matched"] += 1
        counts["imported"] += 1
        identities.append(identity)
        maps, map_conflicts = _mapping_records(incoming, identity, match)
        mappings.extend(maps)
        conflicts.extend(map_conflicts)

    # Preserve unresolved conflicts that are not regenerated with the same id.
    new_ids = {c["id"] for c in conflicts}
    for old in existing.get("conflicts") or []:
        if old["id"] not in new_ids:
            if old.get("status") == "resolved":
                conflicts.append(old)
            elif old.get("status") == "unresolved":
                # Keep teacher resolutions only; drop stale unresolved that no longer apply.
                pass

    revisions = []
    prior_revs = {r.get("internal_id"): r for r in existing.get("revisions") or []}
    for ident in identities:
        rev = _revision_for(ident, merged_rules, mappings)
        prior_rev = prior_revs.get(ident["internal_id"])
        if prior_rev:
            rev["needs_review"] = prior_rev.get("needs_review") or []
            rev["review_state"] = prior_rev.get("review_state") or rev["review_state"]
            rev["teacher_overrides"] = prior_rev.get("teacher_overrides") or []
        if ident.get("student_content_hash") and ident.get("content_status") == "drafted":
            if ident.get("builder_path"):
                lesson_dir = paths.root / ident["builder_path"]
                student = lesson_dir / "student-content.json"
                if student.is_file():
                    rev["coverage_task_supported"] = True
        revisions.append(rev)

    if resources_doc.get("approved_resources"):
        conflicts.append(
            _conflict(
                "conflict.resources.unexpected",
                "resources",
                "approved_resources",
                existing_resource_ids(paths),
                resources_doc.get("approved_resources"),
                "Package unexpectedly listed approved resources.",
            )
        )

    store = empty_store()
    store["course"] = course_doc
    store["identities"] = identities
    store["curriculum"] = curriculum
    store["mappings"] = mappings
    store["rules"] = merged_rules
    store["conflicts"] = conflicts
    store["revisions"] = revisions
    store["change_index"] = existing.get("change_index") or []
    store["known_concerns"] = _known_concerns(lessons_doc["lessons"])
    store["batches"] = list(existing.get("batches") or [])

    counts["conflicted"] = len([c for c in conflicts if c.get("status") == "unresolved"])
    report = {
        "course": course_code,
        "counts": counts,
        "module_counts": [
            sum(1 for i in identities if i["module_number"] == n) for n in range(1, 9)
        ],
        "title_differences": recon.get("title_differences") or [],
        "unresolved_conflicts": [c for c in conflicts if c.get("status") == "unresolved"],
        "resources_preserved": existing_resource_ids(paths),
        "package_resources": resources_doc,
        "fingerprint": operational_fingerprint(store),
    }
    return store, report


def capture_pilot_baseline(paths: BuilderPaths, course: str, internal_id: str) -> dict[str, Any]:
    """Hash current pilot artefacts without modifying them."""
    folder = paths.lessons / course / internal_id
    files = {}
    for name in (
        "student-content.json",
        "teacher-notes.json",
        "provenance.json",
        "lesson-brief.json",
        "locks.json",
        "instruction.json",
    ):
        path = folder / name
        if path.is_file():
            files[name] = sha256_bytes(path.read_bytes())
    built = paths.root / "build" / course / internal_id / "index.html"
    if built.is_file():
        files["build/index.html"] = sha256_bytes(built.read_bytes())
    payload = {
        "internal_id": internal_id,
        "captured_at": utc_now(),
        "files": files,
        "note": "Pilot baseline after onboarding. Do not treat as approval.",
    }
    dest = paths.store(course) / "baselines" / f"{internal_id}.json"
    dump_json(dest, payload)
    return payload


def write_identity_sidecar(paths: BuilderPaths, identity: dict[str, Any]) -> None:
    """Small identity file beside an existing lesson. Does not touch student copy."""
    rel = identity.get("builder_path")
    if not rel:
        return
    dest = paths.root / rel / "identity.json"
    dump_json(
        dest,
        {
            "schema_version": "lesson-identity.v1",
            "internal_id": identity["internal_id"],
            "stable_key": identity["stable_key"],
            "drive_folder_id": identity["drive_folder_id"],
            "source_title": identity["source_title"],
            "display_title": identity.get("display_title"),
        },
    )


def preview_import(paths: BuilderPaths, package: Path, course: str = "MCF3M") -> dict[str, Any]:
    """Validate and reconcile without writing operational records."""
    validation = validate_input_package(package)
    existing = load_store(paths, course)
    before = operational_fingerprint(existing)
    store, report = build_proposed_store(paths, package, existing)
    after = operational_fingerprint(store)
    preview = {
        "schema_version": SCHEMA_VERSION,
        "previewed_at": utc_now(),
        "package_hash": package_hash(package),
        "validation": validation,
        "fingerprint_before": before,
        "fingerprint_after": after,
        "noop": before == after and bool(existing.get("head")),
        "report": report,
    }
    dest = paths.store(course) / "previews" / "latest.json"
    dump_json(dest, preview)
    return preview


def apply_import(
    paths: BuilderPaths,
    package: Path,
    course: str = "MCF3M",
    *,
    expected_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Atomically apply an import. Refuse if the store changed after preview."""
    existing = load_store(paths, course)
    before = operational_fingerprint(existing)
    if expected_fingerprint and expected_fingerprint != before:
        raise RuntimeError(
            "Store changed after preview. Re-run preview. Refusing to apply so later teacher work is not overwritten."
        )
    store, report = build_proposed_store(paths, package, existing)
    after = operational_fingerprint(store)
    folder = paths.store(course)
    folder.mkdir(parents=True, exist_ok=True)
    batch_id = f"import-{utc_now().replace(':', '').replace('-', '')}-{uuid4().hex[:8]}"
    noop = before == after and bool(existing.get("head"))

    rollback_dir = folder / "batches" / batch_id / "rollback"
    rollback_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    if folder.joinpath("HEAD.json").is_file():
        for name in STORE_FILES + ("HEAD.json",):
            src = folder / name
            if src.is_file():
                shutil.copy2(src, rollback_dir / name)
                saved.append(name)
        batches_index = folder / "batches" / "index.json"
        if batches_index.is_file():
            shutil.copy2(batches_index, rollback_dir / "batches-index.json")
            saved.append("batches/index.json")

    batch = {
        "id": batch_id,
        "package_version": load_json(package / "data" / "course.json").get("schema_version"),
        "package_hash": package_hash(package),
        "imported_at": utc_now(),
        "source_snapshot_date": load_json(package / "data" / "lessons.json").get("snapshot_date"),
        "change_summary": report["counts"],
        "outcome": "noop" if noop else "applied",
        "fingerprint_before": before,
        "fingerprint_after": after,
        "rollback_files": saved,
    }
    if noop:
        store["batches"] = list(existing.get("batches") or []) + [batch]
        dump_json(folder / "batches" / f"{batch_id}.json", batch)
        dump_json(folder / "batches" / "index.json", store["batches"])
        dump_json(
            folder / "HEAD.json",
            {
                "schema_version": SCHEMA_VERSION,
                "course_code": course,
                "fingerprint": after,
                "latest_batch_id": batch_id,
            },
        )
        report["batch"] = batch
        report["written"] = [f"batches/{batch_id}.json", "batches/index.json", "HEAD.json"]
        return report

    store["batches"] = list(existing.get("batches") or []) + [batch]
    written = _write_store_files(folder, store, fingerprint=after, batch_id=batch_id)
    dump_json(folder / "batches" / f"{batch_id}.json", batch)
    written.append(f"batches/{batch_id}.json")
    for ident in store["identities"]:
        if ident.get("builder_path"):
            write_identity_sidecar(paths, ident)
            written.append(f"{ident['builder_path']}/identity.json")
    baseline = capture_pilot_baseline(paths, course, "M4-L1-vertex-form")
    written.append("baselines/M4-L1-vertex-form.json")
    report["batch"] = batch
    report["written"] = written
    report["pilot_baseline"] = baseline
    return report


def rollback_import(paths: BuilderPaths, course: str, batch_id: str) -> dict[str, Any]:
    """Restore only files this import wrote. Later batches must not exist."""
    folder = paths.store(course)
    batches = load_json(folder / "batches" / "index.json") if (folder / "batches" / "index.json").is_file() else []
    if not batches or batches[-1].get("id") != batch_id:
        raise RuntimeError("Rollback can only reverse the latest import batch.")
    rollback_dir = folder / "batches" / batch_id / "rollback"
    if not rollback_dir.is_dir():
        raise FileNotFoundError(rollback_dir)
    restored = []
    if any(rollback_dir.iterdir()):
        for name in STORE_FILES + ("HEAD.json",):
            src = rollback_dir / name
            dest = folder / name
            if src.is_file():
                shutil.copy2(src, dest)
                restored.append(name)
            elif dest.is_file():
                dest.unlink()
                restored.append(f"deleted {name}")
        idx = rollback_dir / "batches-index.json"
        if idx.is_file():
            shutil.copy2(idx, folder / "batches" / "index.json")
            restored.append("batches/index.json")
    else:
        # First import: remove operational files created by that batch.
        for name in STORE_FILES + ("HEAD.json",):
            dest = folder / name
            if dest.is_file():
                dest.unlink()
                restored.append(f"deleted {name}")
        dump_json(folder / "batches" / "index.json", batches[:-1])
    return {"rolled_back": batch_id, "restored": restored}


def review_payload(paths: BuilderPaths, course: str = "MCF3M") -> dict[str, Any]:
    """Compact course-readiness document for the existing review UI."""
    store = load_store(paths, course)
    identities = store.get("identities") or []
    mappings = store.get("mappings") or []
    conflicts = [c for c in store.get("conflicts") or [] if c.get("status") == "unresolved"]
    rules = store.get("rules") or []
    rule_version = {r["id"]: r.get("version") for r in rules}
    latest = (store.get("batches") or [{}])[-1] if store.get("batches") else {}
    modules = []
    course_doc = store.get("course") or {}
    for mod in course_doc.get("modules") or []:
        n = mod["module_number"]
        rows = []
        for ident in identities:
            if ident["module_number"] != n:
                continue
            proposed = sorted(
                {
                    m["code"]
                    for m in mappings
                    if m["stable_key"] == ident["stable_key"] and m["origin"] == "package_proposal"
                }
            )
            teacher = sorted(
                {
                    m["code"]
                    for m in mappings
                    if m["stable_key"] == ident["stable_key"] and m["origin"] == "teacher_brief"
                }
            )
            rows.append(
                {
                    "stable_key": ident["stable_key"],
                    "internal_id": ident["internal_id"],
                    "source_title": ident["source_title"],
                    "display_title": ident.get("display_title"),
                    "title_difference": ident.get("title_difference"),
                    "drive_folder_id": ident["drive_folder_id"],
                    "drive_parent_folder_id": ident["drive_parent_folder_id"],
                    "drive_module_folder_title": ident["drive_module_folder_title"],
                    "content_status": ident["content_status"],
                    "proposed_codes": proposed,
                    "teacher_codes": teacher,
                    "unresolved_conflict_ids": [
                        c["id"]
                        for c in conflicts
                        if ident["stable_key"] in json.dumps(c, ensure_ascii=False)
                    ],
                    "builder_path": ident.get("builder_path"),
                }
            )
        modules.append(
            {
                "module_number": n,
                "title": mod.get("title"),
                "drive_folder_title": mod.get("drive_folder_title"),
                "drive_folder_id": mod.get("drive_folder_id"),
                "lesson_parent_folder_id": mod.get("lesson_parent_folder_id"),
                "lessons": rows,
            }
        )
    processes = [c for c in store.get("curriculum") or [] if c.get("expectation_type") == "process"]
    return {
        "schema_version": SCHEMA_VERSION,
        "course": course,
        "batch": latest,
        "counts": {
            "lessons": len(identities),
            "unresolved_conflicts": len(conflicts),
            "rules": len(rules),
            "catalogue_resources": len(existing_resource_ids(paths)),
        },
        "modules": modules,
        "conflicts": conflicts,
        "known_concerns": store.get("known_concerns") or [],
        "process_expectations": [
            {"name": p.get("name"), "id": p["id"], "scope": "course-wide"} for p in processes
        ],
        "rule_version": rule_version,
        "coverage_policy": "Mapped is not taught. Taught needs a task. Reviewed evidence is separate. Process expectations stay available without a completed checklist.",
    }


def resolve_conflict(
    paths: BuilderPaths,
    course: str,
    conflict_id: str,
    resolution: str,
    note: str | None = None,
) -> dict[str, Any]:
    """Record an explicit teacher resolution. Does not rewrite student copy."""
    store = load_store(paths, course)
    found = None
    for item in store.get("conflicts") or []:
        if item["id"] == conflict_id:
            item["status"] = "resolved"
            item["resolution"] = resolution
            item["resolved_at"] = utc_now()
            item["note"] = note
            found = item
            break
    if found is None:
        raise KeyError(conflict_id)
    folder = paths.store(course)
    dump_json(folder / "conflicts.json", store["conflicts"])
    dump_json(
        folder / "HEAD.json",
        {
            **(store.get("head") or {}),
            "schema_version": SCHEMA_VERSION,
            "fingerprint": operational_fingerprint(store),
        },
    )
    return found


def _curriculum_by_code(store: dict[str, Any], code: str) -> dict[str, Any] | None:
    for rec in store.get("curriculum") or []:
        if rec.get("code") == code and rec.get("expectation_type") == "specific":
            return rec
    return None


def resolve_lesson_context(
    paths: BuilderPaths,
    course: str,
    lesson: str,
    role: str,
) -> dict[str, Any]:
    """Bind generation to one lesson + role. Hashes, not the whole package."""
    store = load_store(paths, course)
    identity = None
    for ident in store.get("identities") or []:
        if ident.get("internal_id") == lesson or ident.get("stable_key") == lesson:
            identity = ident
            break
    if identity is None:
        raise KeyError(lesson)
    maps = [m for m in store.get("mappings") or [] if m.get("stable_key") == identity["stable_key"]]
    codes = sorted({m["code"] for m in maps})
    expectations = []
    for code in codes:
        rec = _curriculum_by_code(store, code)
        if rec:
            expectations.append(
                {
                    "code": rec["code"],
                    "text": rec["text"],
                    "sample_problem": rec.get("sample_problem"),
                    "text_hash": rec["text_hash"],
                    "verification_status": rec.get("verification_status"),
                }
            )
    scopes = ROLE_RULE_SCOPES.get(role) or set()
    rules = [
        {
            "id": r["id"],
            "version": r["version"],
            "scope": r["scope"],
            "requirement": r["requirement"],
            "source_hash": r["source_hash"],
        }
        for r in store.get("rules") or []
        if scopes & set(r.get("scope") or [])
    ]
    concerns = [
        c
        for c in store.get("known_concerns") or []
        if c.get("stable_key") == identity["stable_key"] or c.get("id") in KNOWN_CONCERN_IDS
    ]
    resources = []
    if identity.get("builder_path"):
        resources = existing_resource_ids(paths)
    blockers = []
    for conflict in store.get("conflicts") or []:
        if conflict.get("status") != "unresolved":
            continue
        if conflict.get("record") == "lesson_identity" and identity["stable_key"] in json.dumps(
            conflict, ensure_ascii=False
        ):
            blockers.append(conflict["id"])
    payload = {
        "schema_version": "resolved-context.v1",
        "role": role,
        "course_code": course,
        "identity": {
            "internal_id": identity["internal_id"],
            "stable_key": identity["stable_key"],
            "module_number": identity["module_number"],
            "lesson_number": identity["lesson_number"],
            "module_title": identity["module_title"],
            "source_title": identity["source_title"],
            "display_title": identity.get("display_title"),
            "position": f"Module {identity['module_number']} Lesson {identity['lesson_number']}",
        },
        "expectations": expectations,
        "mappings": maps,
        "rules": rules,
        "locks": identity.get("locks") or [],
        "content_revision": identity.get("student_content_hash"),
        "selected_resource_ids": resources if role in {"hook-curator", "lesson-engineer", "lesson-verifier"} else [],
        "concerns": concerns,
        "scope_notes": {
            "async_tabs": "Exactly three asynchronous tabs: Minds On, Action, Consolidation.",
            "live_slides": "Seven-slide live contract applies only to live decks.",
            "twelve_candidates": "The twelve-candidate fade is the vertex-form pilot, not a universal quota.",
            "model_star": "Model-STAR plus diagram applies when generating word-problem solutions.",
            "named_platform": "A platform named in a plan is not an approved resource.",
        },
        "generation_blockers": blockers,
        "student_renderer": "Do not copy this document into student-content.json.",
    }
    payload["context_revision"] = canonical_hash(payload)
    return payload


def write_resolved_context(paths: BuilderPaths, course: str, lesson: str, role: str) -> Path:
    """Write resolved context beside the lesson. Not a student field."""
    ctx = resolve_lesson_context(paths, course, lesson, role)
    identity = None
    store = load_store(paths, course)
    for ident in store.get("identities") or []:
        if ident.get("internal_id") == lesson or ident.get("stable_key") == lesson:
            identity = ident
            break
    rel = (identity or {}).get("builder_path")
    if not rel:
        dest = paths.store(course) / "contexts" / f"{lesson}.{role}.json"
    else:
        dest = paths.root / rel / f"resolved-context.{role}.json"
    dump_json(dest, ctx)
    return dest


def classify_rule_effect(rule_id: str) -> list[str]:
    """Sections a rule change should mark for review."""
    for spec in RULE_SECTIONS:
        if spec["id"] == rule_id:
            return list(spec["affects"])
    return ["review"]


def mark_rule_change(
    paths: BuilderPaths,
    course: str,
    rule_id: str,
    reason: str,
) -> dict[str, Any]:
    """Flag affected current sections. Does not rebuild or edit locked text."""
    store = load_store(paths, course)
    affects = classify_rule_effect(rule_id)
    live_only = affects == ["live_slides"]
    updated = []
    for rev in store.get("revisions") or []:
        if live_only:
            continue
        entry = {"rule_id": rule_id, "affects": affects, "reason": reason, "status": "needs_review"}
        locks = set(rev.get("locks") or [])
        if locks:
            entry["lock_conflict"] = sorted(locks)
        rev.setdefault("needs_review", []).append(entry)
        updated.append(rev["internal_id"])
    store.setdefault("change_index", []).append(
        {
            "at": utc_now(),
            "kind": "rule_change",
            "rule_id": rule_id,
            "affects": affects,
            "skipped_async_because_live_only": live_only,
            "lessons_marked": updated,
        }
    )
    folder = paths.store(course)
    dump_json(folder / "lesson-revisions.json", store["revisions"])
    dump_json(folder / "change-index.json", store["change_index"])
    return {"marked": updated, "live_only": live_only, "affects": affects}

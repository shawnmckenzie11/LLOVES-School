#!/usr/bin/env python3
"""Audit and pack specialist Task prompts so parent chats stay within budget.

Cursor already injects the specialist definition, always-on school rules, and
``AGENTS.md``. Subagents do not see the parent conversation. This module:

* inventories that injected system prompt versus parent-owned inputs
* replaces unchanged file bodies with path + sha256
* summarizes older chat turns instead of pasting raw history
* warns at 80% of the token budget and truncates at the limit (default 500k)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

DEFAULT_TOKEN_LIMIT = 500_000
WARN_RATIO = 0.8
KEEP_RECENT_TURNS = 3
DIGEST_TURN_CHARS = 600
CHARS_PER_TOKEN = 4

CODE_FENCE_RE = re.compile(r"```[\s\S]*?```")
USER_QUERY_RE = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.S)
JSON_OBJECT_RE = re.compile(r"\{[^{}]{800,}\}")

SPECIALIST_ROLES = (
    "lesson-director",
    "practice-designer",
    "hook-curator",
    "visual-experience-designer",
    "interaction-designer",
    "formative-feedback-designer",
    "student-copywriter",
    "lesson-engineer",
    "lesson-verifier",
)

# Lesson-dir files the specialist should Read. Parent must not paste bodies.
LESSON_INPUTS: dict[str, tuple[str, ...]] = {
    "lesson-director": (
        "resolved-context.lesson-director.json",
        "lesson-brief.json",
    ),
    "practice-designer": (
        "resolved-context.practice-designer.json",
        "lesson-brief.json",
        "question-candidates.json",
        "practice-sequence.json",
    ),
    "hook-curator": (
        "resolved-context.hook-curator.json",
        "lesson-brief.json",
        "hook-proposals.json",
    ),
    "visual-experience-designer": (
        "resolved-context.visual-experience-designer.json",
        "lesson-brief.json",
    ),
    "interaction-designer": (
        "resolved-context.interaction-designer.json",
        "lesson-brief.json",
        "practice-sequence.json",
        "interaction-spec.json",
    ),
    "formative-feedback-designer": (
        "resolved-context.formative-feedback-designer.json",
        "lesson-brief.json",
        "practice-sequence.json",
        "interaction-spec.json",
        "feedback-spec.json",
    ),
    "student-copywriter": (
        "resolved-context.student-copywriter.json",
        "lesson-brief.json",
        "practice-sequence.json",
        "hook-proposals.json",
        "interaction-spec.json",
        "feedback-spec.json",
        "locks.json",
        "student-content.json",
    ),
    "lesson-engineer": (
        "resolved-context.lesson-engineer.json",
        "student-content.json",
        "interaction-spec.json",
        "feedback-spec.json",
        "locks.json",
    ),
    "lesson-verifier": (
        "resolved-context.lesson-verifier.json",
        "lesson-brief.json",
        "student-content.json",
        "practice-sequence.json",
        "interaction-spec.json",
        "feedback-spec.json",
        "locks.json",
    ),
}

# Repo-relative files. Cursor already injects the overlapping .mdc rules.
REPO_INPUTS: dict[str, tuple[str, ...]] = {
    "lesson-director": (
        "content-builder/catalogue/contracts/lesson-brief.schema.json",
    ),
    "practice-designer": (
        "content-builder/catalogue/contracts/practice-sequence.schema.json",
    ),
    "hook-curator": (
        "content-builder/catalogue/contracts/hook-proposals.schema.json",
    ),
    "visual-experience-designer": (
        "content-builder/catalogue/contracts/README.md",
        "content-builder/fixtures/M4-L1-vertex-form-v1/CHECKLIST.md",
    ),
    "interaction-designer": (
        "content-builder/catalogue/contracts/feedback-spec.schema.json",
        "content-builder/README.md",
    ),
    "formative-feedback-designer": (
        "content-builder/catalogue/contracts/feedback-spec.schema.json",
    ),
    "student-copywriter": (
        "content-builder/catalogue/contracts/student-content.schema.json",
        "content-builder/writer-reference/comparison-notes.md",
    ),
    "lesson-engineer": (
        "content-builder/catalogue/contracts/student-content.schema.json",
        "content-builder/scripts/student_fields.py",
        "content-builder/README.md",
    ),
    "lesson-verifier": (
        "content-builder/scripts/student_fields.py",
        "content-builder/fixtures/M4-L1-vertex-form-v1/CHECKLIST.md",
        "content-builder/README.md",
    ),
}

ALWAYS_ON_RULES = (
    "AGENTS.md",
    ".cursor/rules/elc-school.mdc",
    ".cursor/rules/content-builder-ownership.mdc",
    ".cursor/rules/semester-awareness.mdc",
    ".cursor/rules/local-first-workflow.mdc",
    ".cursor/rules/repo-conventions.mdc",
)

CONTENT_BUILDER_RULES = (
    ".cursor/rules/content-builder-parent.mdc",
    ".cursor/rules/content-builder-instruction.mdc",
    ".cursor/rules/content-builder-interactions.mdc",
    ".cursor/rules/content-builder-review.mdc",
    ".cursor/rules/content-builder-runtime.mdc",
    ".cursor/rules/content-builder-source-integrity.mdc",
    ".cursor/rules/content-builder-student-language.mdc",
)


def estimate_tokens(text: str) -> int:
    """Approximate tokens as UTF-8 bytes / 4 (English and JSON mix)."""
    if not text:
        return 0
    return max(1, (len(text.encode("utf-8")) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def sha256_bytes(data: bytes) -> str:
    """Hex digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """Hex digest of UTF-8 text."""
    return sha256_bytes(text.encode("utf-8"))


def repo_root_from_builder(builder_root: Path) -> Path:
    """LLOVES-School root (parent of ``content-builder/``)."""
    return builder_root.resolve().parent


def cursor_injected_relpaths(role: str) -> tuple[str, ...]:
    """Paths Cursor already puts in the specialist system prompt.

    Args:
        role: Specialist id (``lesson-director``, …).
    """
    agent = f".cursor/agents/{role}.md"
    return (agent, *ALWAYS_ON_RULES, *CONTENT_BUILDER_RULES)


def file_stat(path: Path) -> dict[str, Any]:
    """Describe one file without reading a huge body into a prompt."""
    if not path.is_file():
        return {
            "path": str(path),
            "status": "missing",
            "sha256": None,
            "bytes": 0,
        }
    data = path.read_bytes()
    return {
        "path": str(path),
        "status": "present",
        "sha256": sha256_bytes(data),
        "bytes": len(data),
    }


def mark_unchanged(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    """Set status to ``unchanged`` when sha256 matches the last pack.

    Args:
        current: Output of :func:`file_stat`.
        previous: Prior pack entry for the same path, if any.
    """
    row = dict(current)
    if (
        previous
        and row.get("status") == "present"
        and previous.get("sha256")
        and previous.get("sha256") == row.get("sha256")
    ):
        row["status"] = "unchanged"
        row["omit_body"] = True
    elif row.get("status") == "present":
        row["omit_body"] = True
        if previous and previous.get("sha256"):
            row["status"] = "changed"
        else:
            row["status"] = "new"
    return row


def compact_turn_text(text: str, *, max_chars: int = DIGEST_TURN_CHARS) -> str:
    """Drop code fences, giant JSON, and XML wrappers; keep a short gist."""
    raw = text or ""
    match = USER_QUERY_RE.search(raw)
    if match:
        raw = match.group(1)
    raw = CODE_FENCE_RE.sub("[omitted code/file fence]", raw)
    stripped = raw.strip()
    if stripped.startswith("{") and len(stripped) > 800:
        return "[omitted JSON body]"
    raw = JSON_OBJECT_RE.sub("[omitted JSON body]", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    if len(raw) > max_chars:
        return raw[: max_chars - 1] + "…"
    return raw


def summarize_history(
    turns: list[dict[str, str]],
    *,
    keep_recent: int = KEEP_RECENT_TURNS,
) -> dict[str, Any]:
    """Keep the last turns compact; digest older ones instead of raw history.

    Args:
        turns: ``{"role", "text"}`` objects, oldest first.
        keep_recent: Number of trailing turns to keep (still compacted).
    """
    if not turns:
        return {"digest": [], "recent": [], "omitted_turns": 0}
    keep = max(0, keep_recent)
    older = turns[:-keep] if keep else turns
    recent = turns[-keep:] if keep else []
    digest = []
    for turn in older:
        gist = compact_turn_text(str(turn.get("text") or ""))
        if gist:
            digest.append({"role": turn.get("role") or "unknown", "gist": gist})
    recent_out = []
    for turn in recent:
        recent_out.append(
            {
                "role": turn.get("role") or "unknown",
                "text": compact_turn_text(
                    str(turn.get("text") or ""), max_chars=DIGEST_TURN_CHARS * 2
                ),
            }
        )
    return {
        "digest": digest,
        "recent": recent_out,
        "omitted_turns": len(older),
    }


def strip_inline_file_bodies(prompt: str, files: list[dict[str, Any]]) -> str:
    """Replace pasted unchanged file text with a path pointer.

    Args:
        prompt: Draft Task prompt that may contain full file contents.
        files: Pack file rows with ``sha256`` and ``path``.
    """
    out = prompt
    for row in files:
        digest = row.get("sha256")
        path = row.get("relpath") or row.get("path")
        if not digest or not path:
            continue
        marker = f"Read `{path}` (unchanged, sha256={digest[:12]})"
        body = row.get("body")
        if isinstance(body, str) and len(body) >= 40 and body in out:
            out = out.replace(body, marker)
    return out


def _rel(path: Path, repo: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return str(path)


def collect_files(
    *,
    repo: Path,
    builder: Path,
    course: str,
    lesson: str,
    role: str,
    previous: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Stat lesson + repo inputs and mark unchanged hashes."""
    prev_by_rel = {}
    for row in (previous or {}).get("files") or []:
        key = row.get("relpath") or row.get("path")
        if key:
            prev_by_rel[key] = row
    lesson_dir = builder / "lessons" / course / lesson
    build_html = builder / "build" / course / lesson / "index.html"
    rows: list[dict[str, Any]] = []
    for name in LESSON_INPUTS.get(role, ()):
        path = lesson_dir / name
        rel = _rel(path, repo)
        stat = file_stat(path)
        tagged = mark_unchanged(stat, prev_by_rel.get(rel))
        tagged["relpath"] = rel
        tagged["kind"] = "lesson"
        rows.append(tagged)
    extra = list(REPO_INPUTS.get(role, ()))
    if role == "lesson-verifier" and build_html.is_file():
        extra = (*extra, _rel(build_html, repo))
    for rel in extra:
        path = repo / rel
        stat = file_stat(path)
        tagged = mark_unchanged(stat, prev_by_rel.get(rel))
        tagged["relpath"] = rel
        tagged["kind"] = "repo"
        rows.append(tagged)
    return rows


def audit_system_prompt(repo: Path, role: str) -> dict[str, Any]:
    """Byte inventory of context Cursor injects before the Task prompt."""
    injected = []
    total = 0
    for rel in cursor_injected_relpaths(role):
        path = repo / rel
        stat = file_stat(path)
        stat["relpath"] = rel
        stat["tokens"] = estimate_tokens(
            path.read_text(encoding="utf-8") if path.is_file() else ""
        )
        injected.append(stat)
        total += int(stat.get("bytes") or 0)
    token_estimate = sum(int(row.get("tokens") or 0) for row in injected)
    return {
        "role": role,
        "note": (
            "Cursor injects the specialist definition and always-on rules. "
            "Do not paste these files into the Task prompt. Subagents do not "
            "receive parent chat history unless you paste it."
        ),
        "files": injected,
        "bytes": total,
        "token_estimate": token_estimate,
    }


def render_prompt(
    *,
    role: str,
    course: str,
    lesson: str,
    files: list[dict[str, Any]],
    decisions: list[str],
    history: dict[str, Any],
) -> str:
    """Build the Task prompt: paths and hashes, not file bodies."""
    lines = [
        f"You are the {role}. Follow `.cursor/agents/{role}.md` (already in your system prompt).",
        f"Lesson: {course} / {lesson}.",
        "Read listed paths from disk. Do not wait for pasted file bodies.",
        "Do not edit `lms/`, Fly `/data`, or `.imscc` packs.",
        "",
        "## Already in your system prompt (do not re-read as Task payload)",
        f"- `.cursor/agents/{role}.md`",
        "- `AGENTS.md` and always-on school / ownership rules",
        "- content-builder `*.mdc` rules when you touch `content-builder/`",
        "",
        "## Load from disk",
    ]
    for row in files:
        rel = row.get("relpath") or row.get("path")
        status = row.get("status")
        digest = row.get("sha256") or "—"
        size = row.get("bytes") or 0
        if status == "missing":
            lines.append(f"- `{rel}` — missing (skip if optional)")
        elif status == "unchanged":
            lines.append(f"- `{rel}` — unchanged sha256={digest} ({size} bytes); do not treat as new")
        else:
            lines.append(f"- `{rel}` — {status} sha256={digest} ({size} bytes)")
    lines.append("")
    lines.append("## Parent decisions")
    if decisions:
        lines.extend(f"- {item}" for item in decisions)
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("## Prior work (digest, not raw chat)")
    digest = history.get("digest") or []
    recent = history.get("recent") or []
    if not digest and not recent:
        lines.append("- No parent history. Work from the files above.")
    else:
        if digest:
            lines.append(
                f"- {history.get('omitted_turns', 0)} earlier turns summarized:"
            )
            for item in digest:
                lines.append(f"  - {item.get('role')}: {item.get('gist')}")
        if recent:
            lines.append("- Recent turns (compacted):")
            for item in recent:
                lines.append(f"  - {item.get('role')}: {item.get('text')}")
    lines.append("")
    lines.append("## Return to parent")
    lines.append("Follow the Return to parent list in your agent definition. Paths only; no file dumps.")
    return "\n".join(lines) + "\n"


def guard_text(
    text: str,
    *,
    token_limit: int = DEFAULT_TOKEN_LIMIT,
    warn_ratio: float = WARN_RATIO,
) -> dict[str, Any]:
    """Warn at 80% of the budget; hard-truncate the string if still over."""
    tokens = estimate_tokens(text)
    warnings: list[str] = []
    truncated = False
    warn_at = int(token_limit * warn_ratio)
    if tokens >= warn_at and tokens <= token_limit:
        warnings.append(
            f"Prompt token estimate {tokens} is at or above {warn_at} "
            f"(80% of {token_limit}). Summarize further before invoking."
        )
    if tokens > token_limit:
        keep = max(0, token_limit * CHARS_PER_TOKEN)
        text = text[:keep] + "\n[truncated to token budget]\n"
        truncated = True
        tokens = estimate_tokens(text)
        warnings.append(
            f"Hard-truncated prompt to {tokens} tokens (limit {token_limit})."
        )
    return {
        "text": text,
        "token_estimate": tokens,
        "warnings": warnings,
        "truncated": truncated,
        "over_limit": tokens > token_limit,
    }


def shrink_history_to_budget(
    *,
    role: str,
    course: str,
    lesson: str,
    files: list[dict[str, Any]],
    decisions: list[str],
    history: dict[str, Any],
    token_limit: int,
) -> tuple[str, dict[str, Any], bool]:
    """Drop oldest digest then recent turns and re-render until under budget."""
    working = {
        "digest": list(history.get("digest") or []),
        "recent": list(history.get("recent") or []),
        "omitted_turns": int(history.get("omitted_turns") or 0),
    }
    truncated = False
    prompt = render_prompt(
        role=role,
        course=course,
        lesson=lesson,
        files=files,
        decisions=decisions,
        history=working,
    )
    while estimate_tokens(prompt) > token_limit and working["digest"]:
        working["digest"].pop(0)
        working["omitted_turns"] += 1
        truncated = True
        prompt = render_prompt(
            role=role,
            course=course,
            lesson=lesson,
            files=files,
            decisions=decisions,
            history=working,
        )
    while estimate_tokens(prompt) > token_limit and working["recent"]:
        working["recent"].pop(0)
        truncated = True
        prompt = render_prompt(
            role=role,
            course=course,
            lesson=lesson,
            files=files,
            decisions=decisions,
            history=working,
        )
    return prompt, working, truncated


def build_invocation(
    *,
    repo: Path,
    builder: Path,
    course: str,
    lesson: str,
    role: str,
    decisions: list[str] | None = None,
    turns: list[dict[str, str]] | None = None,
    previous: dict[str, Any] | None = None,
    token_limit: int = DEFAULT_TOKEN_LIMIT,
) -> dict[str, Any]:
    """Assemble an auditable specialist invocation pack."""
    if role not in SPECIALIST_ROLES:
        raise KeyError(role)
    system = audit_system_prompt(repo, role)
    files = collect_files(
        repo=repo,
        builder=builder,
        course=course,
        lesson=lesson,
        role=role,
        previous=previous,
    )
    decisions_list = list(decisions or [])
    history = summarize_history(turns or [])
    prompt, history, truncated_history = shrink_history_to_budget(
        role=role,
        course=course,
        lesson=lesson,
        files=files,
        decisions=decisions_list,
        history=history,
        token_limit=token_limit,
    )
    guarded = guard_text(prompt, token_limit=token_limit)
    prompt = guarded["text"]
    warnings = list(guarded["warnings"] or [])
    truncated = truncated_history or bool(guarded["truncated"])
    if truncated_history and not guarded["truncated"]:
        warnings.append(
            f"Truncated older history so the Task prompt fits {token_limit} tokens "
            f"(now {guarded['token_estimate']})."
        )
    injected_tokens = int(system.get("token_estimate") or 0)
    combined = int(guarded["token_estimate"]) + injected_tokens
    if combined > token_limit:
        warnings.append(
            f"System prompt (~{injected_tokens}) plus Task prompt "
            f"(~{guarded['token_estimate']}) is ~{combined} tokens "
            f"(limit {token_limit}). Shrink parent decisions or files listed."
        )
    pack = {
        "schema_version": "invocation-pack.v1",
        "role": role,
        "course": course,
        "lesson_id": lesson,
        "system_prompt": system,
        "files": [{k: v for k, v in row.items() if k != "body"} for row in files],
        "decisions": decisions_list,
        "history": history,
        "prompt": prompt,
        "token_estimate": guarded["token_estimate"],
        "injected_token_estimate": injected_tokens,
        "combined_token_estimate": combined,
        "token_limit": token_limit,
        "warnings": warnings,
        "truncated": truncated,
        "unchanged_paths": [
            row.get("relpath") for row in files if row.get("status") == "unchanged"
        ],
    }
    return pack


def pack_path(builder: Path, course: str, lesson: str, role: str) -> Path:
    """Where the last invocation pack is stored (not a student artefact)."""
    return (
        builder
        / "catalogue"
        / "coo"
        / "invocation-packs"
        / course
        / f"{lesson}.{role}.json"
    )


def load_previous_pack(builder: Path, course: str, lesson: str, role: str) -> dict[str, Any] | None:
    """Load the last pack for unchanged-hash comparison."""
    dest = pack_path(builder, course, lesson, role)
    if not dest.is_file():
        return None
    return json.loads(dest.read_text(encoding="utf-8"))


def save_pack(builder: Path, pack: dict[str, Any]) -> Path:
    """Persist a pack so the next invocation can omit unchanged bodies."""
    dest = pack_path(
        builder, str(pack["course"]), str(pack["lesson_id"]), str(pack["role"])
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(pack, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return dest


def _parse_turns(path: Path | None) -> list[dict[str, str]]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "turns" in payload:
        payload = payload["turns"]
    turns = []
    for item in payload:
        turns.append(
            {
                "role": str(item.get("role") or "unknown"),
                "text": str(item.get("text") or item.get("content") or ""),
            }
        )
    return turns


def main() -> int:
    """CLI: audit, print, or write an invocation pack."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", default="lesson-director")
    parser.add_argument("--course", default="MCF3M")
    parser.add_argument("--lesson", default="M4-L1-vertex-form")
    parser.add_argument("--decision", action="append", default=[], help="Parent decision bullet")
    parser.add_argument("--history-json", type=Path, help="Turns array or {turns: [...]}")
    parser.add_argument("--token-limit", type=int, default=DEFAULT_TOKEN_LIMIT)
    parser.add_argument("--audit", action="store_true", help="Print injected system-prompt inventory")
    parser.add_argument("--print-prompt", action="store_true")
    parser.add_argument("--write", action="store_true", help="Save pack under catalogue/coo/invocation-packs/")
    args = parser.parse_args()
    builder = Path(__file__).resolve().parents[1]
    repo = repo_root_from_builder(builder)
    if args.audit:
        audit = audit_system_prompt(repo, args.role)
        print(json.dumps(audit, indent=2))
        return 0
    previous = load_previous_pack(builder, args.course, args.lesson, args.role)
    pack = build_invocation(
        repo=repo,
        builder=builder,
        course=args.course,
        lesson=args.lesson,
        role=args.role,
        decisions=args.decision,
        turns=_parse_turns(args.history_json),
        previous=previous,
        token_limit=args.token_limit,
    )
    if args.write:
        dest = save_pack(builder, pack)
        print(dest)
    if args.print_prompt:
        print(pack["prompt"], end="")
        for warning in pack.get("warnings") or []:
            print(f"\n# warning: {warning}", file=sys.stderr)
        return 0
    if not args.write:
        slim = dict(pack)
        slim.pop("prompt", None)
        print(json.dumps(slim, indent=2))
        print("\n--- prompt ---\n")
        print(pack["prompt"], end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Exit 0 when a git revision range changes only user-visible copy.

Allowed surfaces: plain text nodes and alt/title/placeholder/aria-label values
(with no Jinja inside) in HTML/Jinja templates, string values in existing
copy/i18n JSON, and prose edits in existing docs markdown (no raw HTML).
Script/style bodies, renames, adds, deletes, mode changes, symlinks, and
binaries are rejected. Template markup is compared with html.parser after
masking Jinja; ``<``/``>`` counts must not change.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent

TRUSTED_LANE_PATHS = frozenset(
    {
        "scripts/copy_only_check.py",
        "scripts/copy_only_template_smoke.py",
        ".github/workflows/copy-only.yml",
    }
)

COPY_ATTRS = frozenset({"alt", "title", "placeholder", "aria-label"})
RAW_ELEMENTS = frozenset({"script", "style"})
FORBIDDEN_SUFFIXES = frozenset(
    {
        ".py",
        ".js",
        ".mjs",
        ".cjs",
        ".css",
        ".scss",
        ".less",
        ".ts",
        ".tsx",
        ".jsx",
        ".sql",
        ".sh",
        ".yml",
        ".yaml",
        ".toml",
        ".ini",
        ".env",
    }
)
FORBIDDEN_UNICODE = re.compile(
    "[\u200b\u200c\u200d\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]"
)
JINJA_IN_COPY = re.compile(r"(\{\{|\{%|\{#)")
HTML_IN_PROSE = re.compile(r"<\s*[a-zA-Z!/]")
FULLWIDTH_SCRIPT = re.compile(r"[＜<]\s*script\b", re.IGNORECASE)
HTML_ENTITY = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|\w+);")
JAVASCRIPT_SCHEME = re.compile(r"javascript\s*:", re.IGNORECASE)
JINJA_LINE_STMT = re.compile(r"#\s*for\b", re.IGNORECASE)
PSEUDO_JINJA = re.compile(r"\{\s+\{")
SENSITIVE_TEMPLATE_NAMES = frozenset(
    {
        "landing.html",
        "catchup.html",
        "student_join.html",
        "join.html",
        "login.html",
        "local_dev_login.html",
    }
)
SENSITIVE_PATH_PARTS = frozenset({"login", "join", "catchup", "auth", "verify"})
SENSITIVE_WORDING = re.compile(
    r"(?:\bjoin\s*code\b|\bcatch[- ]?up\s*pin\b|\bverification\s+code\b|"
    r"\b(?:wrong\s+)?pin\b|\blogin\b)",
    re.IGNORECASE,
)

Normalizer = Callable[[str], str]


def git_cwd() -> Path:
    """Directory passed to git (override with COPY_ONLY_GIT_DIR for tests)."""
    override = (os.environ.get("COPY_ONLY_GIT_DIR") or "").strip()
    return Path(override) if override else REPO_ROOT


def run_git(*args: str, text: bool = True) -> str:
    """Run a git command at the repo root and return stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=git_cwd(),
        check=False,
        capture_output=True,
        text=text,
    )
    if result.returncode != 0:
        stderr = (
            result.stderr.decode() if isinstance(result.stderr, bytes) else result.stderr
        )
        stdout = (
            result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout
        )
        raise RuntimeError((stderr or stdout or "git failed").strip())
    if isinstance(result.stdout, bytes):
        return result.stdout.decode()
    return result.stdout


def git_merge_base(base: str, head: str) -> str:
    """Return the merge base between base and head."""
    return run_git("merge-base", base, head).strip()


def changed_entries(base: str, head: str) -> list[tuple[str, str]]:
    """Return (status, path) rows between merge-base and head (no rename folding)."""
    merge = git_merge_base(base, head)
    text = run_git("diff", "--name-status", "--no-renames", f"{merge}..{head}")
    entries: list[tuple[str, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        path = parts[-1]
        entries.append((status, path))
    return entries


def diff_summary_errors(base: str, head: str) -> list[str]:
    """Flag mode changes and other non-content summary lines."""
    merge = git_merge_base(base, head)
    text = run_git("diff", "--summary", f"{merge}..{head}")
    errors: list[str] = []
    for line in text.splitlines():
        lowered = line.lower()
        if "mode change" in lowered or "create mode" in lowered or "delete mode" in lowered:
            errors.append(f"git summary: {line.strip()}")
        if " rename " in lowered:
            errors.append(f"git summary: {line.strip()}")
    return errors


def git_file_at(rev: str, path: str) -> str | None:
    """Return file contents at a revision, or None when the path is absent."""
    result = subprocess.run(
        ["git", "show", f"{rev}:{path}"],
        cwd=git_cwd(),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def is_symlink_at(rev: str, path: str) -> bool:
    """True when the path is a symlink (git mode 120000) at the given revision."""
    line = run_git("ls-tree", rev, path).strip()
    return line.startswith("120000")


def is_binary_at(rev: str, path: str) -> bool:
    """True when the blob at rev contains NUL bytes."""
    result = subprocess.run(
        ["git", "show", f"{rev}:{path}"],
        cwd=git_cwd(),
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return False
    return b"\x00" in result.stdout[:8192]


def is_sensitive_auth_template(path: str, old: str | None, new: str | None) -> bool:
    """True when a template touches login, student PIN, or join-code wording."""
    pure = PurePosixPath(path)
    parts_lower = [part.lower() for part in pure.parts]
    if "templates" not in parts_lower:
        return False
    name = pure.name.lower()
    if name in SENSITIVE_TEMPLATE_NAMES:
        return True
    for part in parts_lower:
        if part in SENSITIVE_PATH_PARTS:
            return True
        if "login" in part or "join" in part or "catchup" in part:
            return True
    combined = f"{old or ''}\n{new or ''}"
    return bool(SENSITIVE_WORDING.search(combined))


def classify_path(path: str) -> str:
    """Classify a repo path for copy-only rules.

    Returns one of: markup, markdown, json_copy, forbidden.
    """
    pure = PurePosixPath(path)
    suffix = pure.suffix.lower()
    if suffix in FORBIDDEN_SUFFIXES:
        return "forbidden"
    parts = [part.lower() for part in pure.parts]
    if "templates" in parts and suffix in {".html", ".jinja", ".jinja2"}:
        return "markup"
    if suffix == ".html":
        return "markup"
    if parts and parts[0] == "docs" and suffix == ".md":
        return "markdown"
    if suffix == ".json" and _is_copy_json_path(pure):
        return "json_copy"
    return "forbidden"


def _is_copy_json_path(path: PurePosixPath) -> bool:
    """True when a JSON path is treated as copy/i18n data."""
    lowered = [part.lower() for part in path.parts]
    if "i18n" in lowered or "copy" in lowered:
        return True
    name = path.name.lower()
    return name in {"copy.json", "strings.json", "locale.json"} or name.endswith(
        "-copy.json"
    )


def forbidden_copy_text(text: str) -> str | None:
    """Return a failure reason when copy text contains disallowed content."""
    if FORBIDDEN_UNICODE.search(text):
        return "forbidden bidi or zero-width unicode in copy"
    if JINJA_IN_COPY.search(text):
        return "jinja not allowed inside copy text or attributes"
    if PSEUDO_JINJA.search(text):
        return "jinja-like braces not allowed in copy"
    if JINJA_LINE_STMT.search(text):
        return "jinja-like line statements not allowed in copy"
    if FULLWIDTH_SCRIPT.search(text):
        return "script-like markup not allowed in copy"
    if HTML_ENTITY.search(text):
        return "HTML entities not allowed in copy"
    if JAVASCRIPT_SCHEME.search(text):
        return "javascript: URLs not allowed in copy"
    return None


def markup_angle_brackets_unchanged(old: str, new: str) -> bool:
    """True when ``<`` and ``>`` counts are identical (no new markup delimiters)."""
    return old.count("<") == new.count("<") and old.count(">") == new.count(">")


def _iter_markup_segments(text: str):
    """Yield ``('literal', chunk)`` and ``('jinja', chunk)`` in document order.

    Jinja markers inside quoted attribute values stay in literal segments so
    ``>`` inside ``{{ }}`` does not confuse the HTML tokenizer.
    """
    index = 0
    length = len(text)
    quote: str | None = None
    while index < length:
        char = text[index]
        if quote is not None:
            if char == quote:
                quote = None
            index += 1
            continue
        if char in "\"'":
            quote = char
            index += 1
            continue
        if text.startswith("{%", index):
            end = text.find("%}", index)
            if end == -1:
                raise ValueError("unclosed jinja statement")
            yield ("jinja", text[index : end + 2])
            index = end + 2
            continue
        if text.startswith("{{", index):
            end = text.find("}}", index)
            if end == -1:
                raise ValueError("unclosed jinja expression")
            yield ("jinja", text[index : end + 2])
            index = end + 2
            continue
        if text.startswith("{#", index):
            end = text.find("#}", index)
            if end == -1:
                raise ValueError("unclosed jinja comment")
            yield ("jinja", text[index : end + 2])
            index = end + 2
            continue
        next_special = length
        for marker in ("{%", "{{", "{#"):
            pos = text.find(marker, index)
            if pos != -1:
                next_special = min(next_special, pos)
        if next_special > index:
            yield ("literal", text[index:next_special])
            index = next_special
            continue
        yield ("literal", char)
        index += 1


def _parse_literal_html(literal: str, parser: _MarkupStreamParser) -> None:
    """Feed one literal (non-Jinja) chunk to the HTML parser."""
    if literal:
        parser.feed(literal)


@dataclass(frozen=True)
class _RawCapture:
    """Accumulate script/style element bodies."""

    tag: str
    attrs: tuple[tuple[str, str | None], ...]
    chunks: list[str]


class _MarkupStreamParser(HTMLParser):
    """Collect HTML parse events from Jinja-masked template text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.events: list[tuple] = []
        self._raw: _RawCapture | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record a start tag or begin capturing script/style."""
        normalized = tuple(sorted(attrs))
        if tag in RAW_ELEMENTS:
            self._raw = _RawCapture(tag=tag, attrs=normalized, chunks=[])
            return
        self.events.append(("start", tag, normalized))

    def handle_endtag(self, tag: str) -> None:
        """Record an end tag or finish script/style capture."""
        if self._raw and self._raw.tag == tag:
            body = "".join(self._raw.chunks)
            self.events.append(("raw", self._raw.tag, self._raw.attrs, body))
            self._raw = None
            return
        self.events.append(("end", tag))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record void elements."""
        self.events.append(("startend", tag, tuple(sorted(attrs))))

    def handle_data(self, data: str) -> None:
        """Record text nodes or script/style inner text."""
        if self._raw is not None:
            self._raw.chunks.append(data)
            return
        self.events.append(("data", data))

    def handle_comment(self, data: str) -> None:
        """Record HTML comments as structural."""
        self.events.append(("comment", data))

    def handle_entityref(self, name: str) -> None:
        """Keep entity references literal in text nodes for copy validation."""
        self.handle_data(f"&{name};")

    def handle_charref(self, name: str) -> None:
        """Keep numeric character references literal in text nodes."""
        self.handle_data(f"&#{name};")


def _canonical_attr_tuple(attrs: tuple[tuple[str, str | None], ...]) -> tuple[tuple[str, str], ...]:
    """Normalize attributes for structural comparison."""
    rows: list[tuple[str, str]] = []
    for name, value in attrs:
        key = name.lower()
        val = value if value is not None else ""
        if key in COPY_ATTRS:
            rows.append((key, "__COPY__"))
        else:
            rows.append((key, val))
    return tuple(sorted(rows))


def _canonical_markup_events(text: str) -> list[tuple]:
    """Parse template HTML into a structural event list (Jinja blocks are literal)."""
    parser = _MarkupStreamParser()
    canonical: list[tuple] = []
    for kind, chunk in _iter_markup_segments(text):
        if kind == "jinja":
            canonical.append(("jinja", chunk))
            continue
        _parse_literal_html(chunk, parser)
    parser.close()
    for event in parser.events:
        kind = event[0]
        if kind == "data":
            canonical.append(("data", "__COPY__"))
        elif kind in {"start", "startend"}:
            tag = event[1]
            attrs = _canonical_attr_tuple(event[2])
            canonical.append((kind, tag, attrs))
        elif kind == "end":
            canonical.append(("end", event[1]))
        elif kind == "comment":
            canonical.append(("comment", event[1]))
        elif kind == "raw":
            tag, attrs, body = event[1], event[2], event[3]
            canonical.append(("raw", tag, _canonical_attr_tuple(attrs), body))
        else:
            canonical.append(event)
    return canonical


def _validate_markup_copy_surfaces(text: str) -> str | None:
    """Scan new template text for disallowed copy in data nodes and copy attrs."""
    parser = _MarkupStreamParser()
    for kind, chunk in _iter_markup_segments(text):
        if kind == "jinja":
            continue
        _parse_literal_html(chunk, parser)
    parser.close()
    data_buffer: list[str] = []
    for event in parser.events:
        kind = event[0]
        if kind == "data":
            data_buffer.append(event[1])
            continue
        if data_buffer:
            reason = forbidden_copy_text("".join(data_buffer))
            data_buffer.clear()
            if reason:
                return reason
        if kind in {"start", "startend"}:
            for name, value in event[2]:
                if name.lower() not in COPY_ATTRS:
                    continue
                val = value if value is not None else ""
                reason = forbidden_copy_text(val)
                if reason:
                    return reason
    if data_buffer:
        return forbidden_copy_text("".join(data_buffer))
    return None


def markup_is_copy_only(old: str, new: str) -> tuple[bool, str]:
    """Compare two template bodies using the HTML tokenizer rules."""
    if not markup_angle_brackets_unchanged(old, new):
        return False, "angle bracket characters added or removed in template"
    reason = _validate_markup_copy_surfaces(new)
    if reason:
        return False, reason
    try:
        old_canon = _canonical_markup_events(old)
        new_canon = _canonical_markup_events(new)
    except ValueError as exc:
        return False, str(exc)
    if old_canon != new_canon:
        return False, "change is not limited to visible copy"
    return True, ""


def validate_markdown_prose(text: str) -> str | None:
    """Reject raw HTML/script patterns in markdown prose (outside fences)."""
    in_fence = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if HTML_IN_PROSE.search(line):
            return "HTML or tags not allowed in docs prose"
        reason = forbidden_copy_text(line)
        if reason:
            return reason
    return None


def normalize_markdown(text: str) -> str:
    """Collapse prose; keep fenced code blocks literal."""
    lines_out: list[str] = []
    in_fence = False
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            lines_out.append(line)
            in_fence = not in_fence
            continue
        if in_fence:
            lines_out.append(line)
            continue
        if line.strip():
            lines_out.append("__COPY__\n" if line.endswith("\n") else "__COPY__")
        else:
            lines_out.append(line)
    return "".join(lines_out)


def _normalize_json_value(value: object) -> object:
    """Replace JSON string leaves with a copy placeholder."""
    if isinstance(value, dict):
        return {key: _normalize_json_value(val) for key, val in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, str):
        return "__COPY__"
    return value


def normalize_copy_json(text: str) -> str:
    """Normalize copy/i18n JSON for structural comparison."""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return "__INVALID_JSON__"
    return json.dumps(_normalize_json_value(parsed), sort_keys=True)


def _validate_json_strings(value: object) -> str | None:
    """Ensure JSON string leaves contain only allowed copy."""
    if isinstance(value, dict):
        for item in value.values():
            reason = _validate_json_strings(item)
            if reason:
                return reason
    elif isinstance(value, list):
        for item in value:
            reason = _validate_json_strings(item)
            if reason:
                return reason
    elif isinstance(value, str):
        return forbidden_copy_text(value)
    return None


def file_is_copy_only(old: str | None, new: str | None, kind: str) -> tuple[bool, str]:
    """Return whether a modified file change is copy-only and a failure reason."""
    if kind == "forbidden":
        return False, "path is not in the copy-only allowlist"
    if new is None:
        return False, "deleted files are not allowed"
    if old is None:
        return False, "new files are not allowed"
    if kind == "markup":
        return markup_is_copy_only(old, new)
    if kind == "markdown":
        reason = validate_markdown_prose(new)
        if reason:
            return False, reason
        if normalize_markdown(old) != normalize_markdown(new):
            return False, "change is not limited to visible copy"
        return True, ""
    if kind == "json_copy":
        try:
            parsed = json.loads(new)
        except json.JSONDecodeError as exc:
            return False, f"invalid JSON: {exc}"
        reason = _validate_json_strings(parsed)
        if reason:
            return False, reason
        old_norm = normalize_copy_json(old)
        new_norm = normalize_copy_json(new)
        if old_norm == new_norm:
            return True, ""
        if old_norm == "__INVALID_JSON__":
            return False, "invalid JSON in base revision"
        if new_norm == "__INVALID_JSON__":
            return False, "invalid JSON in head revision"
        return False, "change is not limited to visible copy"
    return False, "path is not in the copy-only allowlist"


def check_range(base: str, head: str) -> list[str]:
    """Return human-readable errors for a revision range (empty when clean)."""
    merge = git_merge_base(base, head)
    entries = changed_entries(base, head)
    errors: list[str] = list(diff_summary_errors(base, head))
    if not entries and not errors:
        return ["empty diff: no file changes between merge-base and head"]
    for status, path in entries:
        if path in TRUSTED_LANE_PATHS:
            errors.append(f"{path}: trusted copy-only lane files cannot change")
            continue
        if status.startswith("D"):
            errors.append(f"{path}: deleted files are not allowed")
            continue
        if status.startswith("A"):
            errors.append(f"{path}: new files are not allowed")
            continue
        if status.startswith("T"):
            errors.append(f"{path}: symlinks or type changes not allowed")
            continue
        if status.startswith(("R", "C")):
            errors.append(f"{path}: {status} not allowed in copy-only lane")
            continue
        if not status.startswith("M"):
            errors.append(f"{path}: status {status} not allowed")
            continue
        if is_symlink_at(merge, path) or is_symlink_at(head, path):
            errors.append(f"{path}: symlinks not allowed")
            continue
        if is_binary_at(merge, path) or is_binary_at(head, path):
            errors.append(f"{path}: binary files not allowed")
            continue
        kind = classify_path(path)
        old = git_file_at(merge, path)
        new = git_file_at(head, path)
        ok, reason = file_is_copy_only(old, new, kind)
        if ok and is_sensitive_auth_template(path, old, new):
            errors.append(
                f"{path}: login, PIN, or join-code wording requires human review"
            )
        elif not ok:
            errors.append(f"{path}: {reason}")
    return errors


def workflow_checker_path() -> Path:
    """Resolve the checker script path the same way CI does."""
    override = (os.environ.get("COPY_ONLY_CHECKER") or "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve()


def main(argv: list[str] | None = None) -> int:
    """CLI entry: ``copy_only_check.py <base> <head>``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", help="Base git ref (e.g. main)")
    parser.add_argument("head", help="Head git ref (e.g. PR branch)")
    args = parser.parse_args(argv)
    try:
        errors = check_range(args.base, args.head)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 2
    if errors:
        for line in errors:
            print(line, file=sys.stderr)
        return 1
    print("copy-only: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

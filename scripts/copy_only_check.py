#!/usr/bin/env python3
"""Exit 0 when a git revision range changes only user-visible copy.

Allowed surfaces: plain text nodes and alt/title/placeholder/aria-label values
(with no Jinja inside) in HTML/Jinja templates, string values in existing
copy/i18n JSON, and prose edits in existing docs markdown (no raw HTML).
Script/style bodies, renames, adds, deletes, mode changes, symlinks, and
binaries are rejected.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
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
COPY_ATTR_RE = re.compile(
    r"(?<![\w:-])(?P<name>alt|title|placeholder|aria-label)(?![\w:-])"
    r"\s*=\s*"
    r'(?P<quote>["\'])(?P<val>.*?)(?P=quote)',
    re.IGNORECASE,
)
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


def git_object_type(rev: str, path: str) -> str | None:
    """Return the git object type for a path at rev (blob, tree, commit, tag)."""
    result = subprocess.run(
        ["git", "cat-file", "-t", f"{rev}:{path}"],
        cwd=git_cwd(),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


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
    if FULLWIDTH_SCRIPT.search(text):
        return "script-like markup not allowed in copy"
    return None


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


def _tag_name(open_tag: str) -> str:
    """Return the lowercased element name from an opening or closing tag."""
    match = re.match(r"^\s*/?\s*([\w:-]+)", open_tag, re.IGNORECASE)
    return match.group(1).lower() if match else ""


def _append_literal_element(text: str, index: int, out: list[str]) -> int:
    """Copy a script/style element and its body verbatim into out."""
    close_gt = text.find(">", index)
    if close_gt == -1:
        out.append(text[index:])
        return len(text)
    open_chunk = text[index : close_gt + 1]
    name = _tag_name(open_chunk[1:])
    if name not in RAW_ELEMENTS or open_chunk.lstrip().startswith("</"):
        out.append(open_chunk)
        return close_gt + 1
    close_re = re.compile(rf"</\s*{re.escape(name)}\s*>", re.IGNORECASE)
    close_match = close_re.search(text, close_gt + 1)
    if not close_match:
        out.append(text[index:])
        return len(text)
    out.append(text[index : close_match.end()])
    return close_match.end()


def validate_markup_surfaces(text: str) -> str | None:
    """Reject disallowed content inside copy text nodes and copy attributes."""
    index = 0
    length = len(text)
    while index < length:
        if text.startswith("{%", index):
            end = text.find("%}", index)
            if end == -1:
                break
            index = end + 2
            continue
        if text.startswith("{{", index):
            end = text.find("}}", index)
            if end == -1:
                break
            index = end + 2
            continue
        if text.startswith("{#", index):
            end = text.find("#}", index)
            if end == -1:
                break
            index = end + 2
            continue
        if text[index] == "<":
            close_gt = text.find(">", index)
            if close_gt == -1:
                break
            name = _tag_name(text[index + 1 : close_gt])
            if name in RAW_ELEMENTS and not text[index : close_gt + 1].lstrip().startswith(
                "</"
            ):
                close_re = re.compile(rf"</\s*{re.escape(name)}\s*>", re.IGNORECASE)
                close_match = close_re.search(text, close_gt + 1)
                index = close_match.end() if close_match else length
                continue
            chunk = text[index : close_gt + 1]
            for match in COPY_ATTR_RE.finditer(chunk):
                reason = forbidden_copy_text(match.group("val"))
                if reason:
                    return reason
            index = close_gt + 1
            continue
        next_special = _next_markup_special(text, index)
        if next_special > index:
            reason = forbidden_copy_text(text[index:next_special])
            if reason:
                return reason
            index = next_special
            continue
        index += 1
    return None


def normalize_markup(text: str) -> str:
    """Collapse visible copy; keep tags, script/style bodies, and Jinja literal."""
    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        if text.startswith("{%", index):
            end = text.find("%}", index)
            if end == -1:
                out.append(text[index:])
                break
            out.append(text[index : end + 2])
            index = end + 2
            continue
        if text.startswith("{{", index):
            end = text.find("}}", index)
            if end == -1:
                out.append(text[index:])
                break
            out.append(text[index : end + 2])
            index = end + 2
            continue
        if text.startswith("{#", index):
            end = text.find("#}", index)
            if end == -1:
                out.append(text[index:])
                break
            out.append(text[index : end + 2])
            index = end + 2
            continue
        if text[index] == "<":
            close_gt = text.find(">", index)
            if close_gt == -1:
                out.append("__COPY__")
                break
            name = _tag_name(text[index + 1 : close_gt])
            if name in RAW_ELEMENTS:
                index = _append_literal_element(text, index, out)
                continue
            out.append(_normalize_tag(text[index : close_gt + 1]))
            index = close_gt + 1
            continue
        next_special = _next_markup_special(text, index)
        if next_special > index:
            out.append("__COPY__")
            index = next_special
            continue
        index += 1
    return "".join(out)


def _next_markup_special(text: str, index: int) -> int:
    """Return the next index of markup structure, or len(text)."""
    candidates = [len(text)]
    for marker in ("<", "{%", "{{", "{#"):
        pos = text.find(marker, index)
        if pos != -1:
            candidates.append(pos)
    return min(candidates)


def _normalize_tag(tag: str) -> str:
    """Normalize one HTML/Jinja tag string."""

    def repl(match: re.Match[str]) -> str:
        name = match.group("name").lower()
        value = match.group("val")
        if name not in COPY_ATTRS:
            return match.group(0)
        if forbidden_copy_text(value):
            return match.group(0)
        quote = match.group("quote")
        return f"{match.group('name')}={quote}__COPY__{quote}"

    return COPY_ATTR_RE.sub(repl, tag)


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
    normalizers: dict[str, Normalizer] = {
        "markup": normalize_markup,
        "markdown": normalize_markdown,
        "json_copy": normalize_copy_json,
    }
    normalize = normalizers[kind]
    old_text = old
    new_text = new
    if kind == "markup":
        reason = validate_markup_surfaces(new_text)
        if reason:
            return False, reason
    if kind == "markdown":
        reason = validate_markdown_prose(new_text)
        if reason:
            return False, reason
    if kind == "json_copy":
        try:
            parsed = json.loads(new_text)
        except json.JSONDecodeError as exc:
            return False, f"invalid JSON: {exc}"
        reason = _validate_json_strings(parsed)
        if reason:
            return False, reason
    old_norm = normalize(old_text)
    new_norm = normalize(new_text)
    if old_norm == new_norm:
        return True, ""
    if kind == "json_copy" and old_norm == "__INVALID_JSON__":
        return False, "invalid JSON in base revision"
    if kind == "json_copy" and new_norm == "__INVALID_JSON__":
        return False, "invalid JSON in head revision"
    return False, "change is not limited to visible copy"


def check_range(base: str, head: str) -> list[str]:
    """Return human-readable errors for a revision range (empty when clean)."""
    merge = git_merge_base(base, head)
    errors: list[str] = list(diff_summary_errors(base, head))
    for status, path in changed_entries(base, head):
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
        if not ok:
            errors.append(f"{path}: {reason}")
    return errors


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

#!/usr/bin/env python3
"""Exit 0 when a git revision range changes only user-visible copy.

Allowed surfaces: text nodes and alt/title/placeholder/aria-label in HTML and
Jinja templates, string values in copy/i18n JSON, and prose in docs markdown.
Everything else (Python, JS, CSS, template logic, structural HTML, JSON keys)
fails with a printed reason.
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


def git_cwd() -> Path:
    """Directory passed to git (override with COPY_ONLY_GIT_DIR for tests)."""
    override = (os.environ.get("COPY_ONLY_GIT_DIR") or "").strip()
    return Path(override) if override else REPO_ROOT

COPY_ATTRS = frozenset({"alt", "title", "placeholder", "aria-label"})
COPY_ATTR_RE = re.compile(
    r"(?P<name>alt|title|placeholder|aria-label)\s*=\s*"
    r'(?P<quote>["\'])(?P<val>.*?)(?P=quote)',
    re.IGNORECASE,
)
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

Normalizer = Callable[[str], str]


def run_git(*args: str) -> str:
    """Run a git command at the repo root and return stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=git_cwd(),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or result.stdout or "git failed").strip()
        )
    return result.stdout


def git_merge_base(base: str, head: str) -> str:
    """Return the merge base between base and head."""
    return run_git("merge-base", base, head).strip()


def changed_paths(base: str, head: str) -> list[str]:
    """List paths changed between base and head (POSIX paths)."""
    merge = git_merge_base(base, head)
    text = run_git("diff", "--name-only", f"{merge}..{head}")
    return [line.strip() for line in text.splitlines() if line.strip()]


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


def normalize_markup(text: str) -> str:
    """Collapse visible copy; keep tags, attributes, and Jinja logic literal."""
    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        if text.startswith("{%", index):
            end = text.find("%}", index)
            if end == -1:
                out.append("__COPY__")
                break
            out.append(text[index : end + 2])
            index = end + 2
            continue
        if text.startswith("{{", index):
            end = text.find("}}", index)
            if end == -1:
                out.append("__COPY__")
                break
            out.append(text[index : end + 2])
            index = end + 2
            continue
        if text.startswith("{#", index):
            end = text.find("#}", index)
            if end == -1:
                out.append("__COPY__")
                break
            out.append(text[index : end + 2])
            index = end + 2
            continue
        if text[index] == "<":
            end = text.find(">", index)
            if end == -1:
                out.append("__COPY__")
                break
            out.append(_normalize_tag(text[index : end + 1]))
            index = end + 1
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
        if name in COPY_ATTRS:
            quote = match.group("quote")
            return f"{match.group('name')}={quote}__COPY__{quote}"
        return match.group(0)

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


def file_is_copy_only(old: str | None, new: str | None, kind: str) -> tuple[bool, str]:
    """Return whether a file change is copy-only and a failure reason."""
    if kind == "forbidden":
        return False, "path is not in the copy-only allowlist"
    normalizers: dict[str, Normalizer] = {
        "markup": normalize_markup,
        "markdown": normalize_markdown,
        "json_copy": normalize_copy_json,
    }
    normalize = normalizers[kind]
    old_text = old or ""
    new_text = new or ""
    if kind == "json_copy":
        if not old_text.strip() and new_text.strip():
            try:
                json.loads(new_text)
            except json.JSONDecodeError as exc:
                return False, f"invalid JSON: {exc}"
            return True, ""
        if old_text.strip() and not new_text.strip():
            return True, ""
    if kind == "markdown" and not old_text.strip() and new_text.strip():
        return True, ""
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
    errors: list[str] = []
    for path in changed_paths(base, head):
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

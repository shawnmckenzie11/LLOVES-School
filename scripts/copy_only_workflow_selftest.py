#!/usr/bin/env python3
"""Verify CI can invoke the base-branch copy checker (pr/ + base/ layout)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    """Run the workflow's checker command against the current pr checkout."""
    checker = (os.environ.get("COPY_ONLY_CHECKER") or "").strip()
    if not checker:
        print("COPY_ONLY_CHECKER is required", file=sys.stderr)
        return 2
    checker_path = Path(checker)
    if not checker_path.is_file():
        print(f"checker not found: {checker_path}", file=sys.stderr)
        return 2
    git_dir = (os.environ.get("COPY_ONLY_GIT_DIR") or "").strip()
    if not git_dir:
        print("COPY_ONLY_GIT_DIR is required", file=sys.stderr)
        return 2
    result = subprocess.run(
        [sys.executable, str(checker_path), "HEAD", "HEAD"],
        cwd=git_dir,
        env={**os.environ, "COPY_ONLY_GIT_DIR": git_dir},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr or result.stdout)
        return result.returncode
    if "copy-only: ok" not in (result.stdout or ""):
        print("unexpected checker output", file=sys.stderr)
        return 1
    print("workflow checker self-test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

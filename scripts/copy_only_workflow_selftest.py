#!/usr/bin/env python3
"""Verify CI can invoke the base-branch copy checker (pr/ + base/ layout)."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


def main() -> int:
    """Confirm the workflow checker path exists and exposes ``check_range``."""
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
    if not Path(git_dir).is_dir():
        print(f"COPY_ONLY_GIT_DIR is not a directory: {git_dir}", file=sys.stderr)
        return 2
    spec = importlib.util.spec_from_file_location("copy_only_check_ci", checker_path)
    if spec is None or spec.loader is None:
        print("failed to load checker module", file=sys.stderr)
        return 1
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if not callable(getattr(module, "check_range", None)):
        print("checker missing check_range", file=sys.stderr)
        return 1
    print("workflow checker self-test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Print content-builder toolchain versions. Exit 0 when imports succeed."""

from __future__ import annotations

import importlib
import importlib.metadata
import shutil
import subprocess
import sys
from pathlib import Path


def _run(cmd: list[str]) -> str:
    """Return stripped stdout for a command, or an error string."""
    try:
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        return f"ERROR: {exc}"
    return proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else "(no output)"


def main() -> int:
    """Check Git, Python, Node, and builder packages."""
    root = Path(__file__).resolve().parents[1]
    print(f"python {sys.version.split()[0]} ({sys.executable})")
    print(f"git    {_run(['git', '--version'])}")
    node = shutil.which("node")
    print(f"node   {_run(['node', '--version']) if node else 'missing'}")

    missing: list[str] = []
    for name in ("jsonschema", "datasets", "duckdb", "pytest", "playwright"):
        try:
            importlib.import_module(name)
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            version = "ok"
        except ImportError as exc:
            missing.append(f"{name}: {exc}")
            print(f"import {name}: FAIL ({exc})")
            continue
        print(f"import {name}: {version}")

    jsx = root / "node_modules" / "jsxgraph"
    print(f"jsxgraph dir: {'ok' if jsx.is_dir() else 'missing'} ({jsx})")

    if missing:
        print("FAILED:", "; ".join(missing))
        return 1
    print("setup check: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

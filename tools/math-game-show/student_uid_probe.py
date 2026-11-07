#!/usr/bin/env python3
"""Read-only inventory of a restored school sqlite file.

This module does not import the Flask app or ``GameShowDB``. Those
constructors create directories and run migrations. It does not open a
network connection. A path under ``/data`` is refused unless that mount
is a private tmpfs, which is how a Fly probe hides the host database.

The default action prints schema facts and does not write the file.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def data_mount_fstype(mountinfo: str) -> str | None:
    """Return the filesystem type mounted on ``/data``, if any.

    Args:
        mountinfo: Text of ``/proc/self/mountinfo``.

    Returns:
        The fstype, or None when ``/data`` is not a mount point.
    """
    for line in mountinfo.splitlines():
        parts = line.split()
        if len(parts) < 9:
            continue
        # mountinfo: ... mountpoint ... - fstype source super
        if parts[4] != "/data":
            continue
        if "-" not in parts:
            continue
        dash = parts.index("-")
        if dash + 1 < len(parts):
            return parts[dash + 1]
    return None


def refuse_reason(db_path: Path, mountinfo: str) -> str | None:
    """Why this path must not be opened.

    Args:
        db_path: Candidate sqlite file.
        mountinfo: Text of ``/proc/self/mountinfo``.

    Returns:
        A refusal string, or None when the path is a private file.
    """
    try:
        resolved = db_path.resolve()
    except OSError as exc:
        return f"path cannot be resolved ({exc.__class__.__name__})"
    if not str(resolved).startswith("/data/"):
        return None
    fstype = data_mount_fstype(mountinfo)
    if fstype == "tmpfs":
        return None
    return "refusing /data because it is not a private tmpfs"


def inventory(conn: sqlite3.Connection) -> dict[str, object]:
    """Schema facts only. No row values.

    Args:
        conn: Open sqlite connection.

    Returns:
        Counts and whether the legacy column and ``row_uid`` exist.
    """
    tables = [
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    students = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(students)")
    } if "students" in tables else set()
    violations = [
        (str(row[0]), int(row[1]), str(row[2]), int(row[3]))
        for row in conn.execute("PRAGMA foreign_key_check")
    ]
    return {
        "tables": len(tables),
        "legacy_column_present": "canvas_id" in students,
        "row_uid_present": "row_uid" in students,
        "foreign_key_violations": len(violations),
    }


def main(argv: list[str] | None = None) -> int:
    """Print an inventory or refuse a production mount.

    Args:
        argv: Arguments, excluding the program name.

    Returns:
        Process exit code. 2 when the path is refused.
    """
    parser = argparse.ArgumentParser(description="Inventory a restored school sqlite file")
    parser.add_argument("db_path", type=Path)
    parser.add_argument(
        "--mountinfo",
        type=Path,
        default=Path("/proc/self/mountinfo"),
        help="Mount table used to detect a private /data tmpfs",
    )
    args = parser.parse_args(argv)
    mountinfo = ""
    if args.mountinfo.is_file():
        mountinfo = args.mountinfo.read_text(encoding="utf-8", errors="replace")
    reason = refuse_reason(args.db_path, mountinfo)
    if reason:
        print(reason, file=sys.stderr)
        return 2
    if not args.db_path.is_file():
        print("sqlite file is missing", file=sys.stderr)
        return 2
    conn = sqlite3.connect(f"file:{args.db_path}?mode=ro", uri=True)
    try:
        facts = inventory(conn)
    finally:
        conn.close()
    print(
        "tables={tables} legacy_column_present={legacy_column_present} "
        "row_uid_present={row_uid_present} foreign_key_violations={foreign_key_violations}".format(
            **facts
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

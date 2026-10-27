#!/usr/bin/env python3
"""Rebase deck-copy module-file URLs onto the class that owns the row.

A copied deck used to keep ``/staff/class/{source}/module-files/...`` in
``item_json``. Students in the destination class were rewritten onto that
source id and received 403. This repairs rows already stored that way.
New copies are rewritten in ``apply_class_deck_seed``.

Dry-run is the default, including on production. It opens the database
read-only and does not run LMS startup migrations. It prints table, row
id, class id, and how many library pointers would be added. It does not
print people, join codes, or session secrets.

``--apply`` opens the file read-write, updates a column only when its
text still matches what was read, and inserts ``library_files`` pointers
for paths the source library has and the destination library does not.
A pointer is added only when the URL's class is a different class of the
same course and the same teacher. A URL for another course is listed
under ``cross-course`` and is never rewritten or copied. Blob bytes are
not copied. A second run writes zero rows. ``--dry-run`` wins over
``--apply``.

Production (Fly, ``FLASK_ENV=production``, a data dir on ``/data``, or a
database on the ``/data`` volume) is refused before the database is opened
unless both ``--production`` and ``--confirm MCK-282`` are passed.

Usage::

    python3 scripts/repair_deck_copy_images.py --db /tmp/local.sqlite
    python3 scripts/repair_deck_copy_images.py --apply --db /tmp/local.sqlite
    python3 scripts/repair_deck_copy_images.py --production --confirm MCK-282 --db /data/lloves.sqlite
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LMS_DIR = REPO_ROOT / "lms"
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

CONFIRM_TOKEN = "MCK-282"
EXIT_REFUSED = 2


def refusal_reason(db_path: Path | None) -> str | None:
    """Why this process must not open the database.

    Args:
        db_path: Sqlite path the operator passed, or ``None`` for the
            default local file (``LLOVES_DB`` or the LMS default).
    """
    if str(os.getenv("FLY_APP_NAME") or "").strip():
        return "FLY_APP_NAME is set"
    if (os.getenv("FLASK_ENV") or "").lower() == "production":
        return "FLASK_ENV=production"
    data_dir = str(os.getenv("LLOVES_DATA_DIR") or os.getenv("DATA_DIR") or "").strip()
    if data_dir == "/data" or data_dir.startswith("/data/"):
        return "LLOVES data dir is the production volume"
    candidates: list[Path] = []
    if db_path is not None:
        candidates.append(db_path)
    else:
        raw = str(os.getenv("LLOVES_DB") or "").strip()
        if raw:
            candidates.append(Path(raw))
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        text = str(resolved)
        if text == "/data" or text.startswith("/data/"):
            return f"database is on the production volume ({text})"
    return None


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments.

    Args:
        argv: Argument vector excluding the program name.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Rebase copied-deck module-file URLs onto the owning class. "
            "Dry-run by default. Production needs --production --confirm MCK-282."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print affected rows. This is the default.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the plan. Ignored when --dry-run is also set.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Sqlite path. Default is the LMS database.",
    )
    parser.add_argument(
        "--production",
        action="store_true",
        help="Allow a production target. Needs --confirm MCK-282 too.",
    )
    parser.add_argument(
        "--confirm",
        default=None,
        help="Must be MCK-282 together with --production.",
    )
    return parser.parse_args(argv)


def production_gate(args: argparse.Namespace, reason: str | None) -> str | None:
    """Return a refusal message, or None when the run may continue.

    Args:
        args: Parsed CLI arguments.
        reason: Output of :func:`refusal_reason`.
    """
    has_flag = bool(args.production)
    has_confirm = args.confirm is not None
    if has_flag != has_confirm:
        return (
            "Refusing: --production and --confirm MCK-282 must be passed together."
        )
    if has_confirm and str(args.confirm) != CONFIRM_TOKEN:
        return f"Refusing: --confirm must be exactly {CONFIRM_TOKEN}."
    if reason and not has_flag:
        return (
            f"Refusing to run against production ({reason}). "
            "Nothing was opened or written. To run on production, pass "
            f"--production --confirm {CONFIRM_TOKEN} (dry-run unless --apply)."
        )
    return None


def _open_database(db_path: Path, *, write: bool) -> sqlite3.Connection:
    """Open sqlite without running LMS boot migrations.

    Dry-run uses ``mode=ro`` plus ``query_only``. Apply opens the file
    read-write and still does not run boot migrations.

    Args:
        db_path: Sqlite file.
        write: When false, the connection cannot write.
    """
    if write:
        conn = sqlite3.connect(db_path)
    else:
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.execute("PRAGMA query_only = ON")
    return conn


def main(argv: list[str] | None = None) -> int:
    """Plan or apply the deck-copy image repair.

    Args:
        argv: Argument vector excluding the program name.

    Returns:
        ``0`` on success, ``2`` when production is refused.
    """
    args = _parse_args(argv)
    reason = refusal_reason(args.db)
    refusal = production_gate(args, reason)
    if refusal:
        print(refusal, file=sys.stderr)
        return EXIT_REFUSED
    from deck_copy_image_repair import (
        apply_deck_copy_image_repairs,
        format_deck_copy_image_report,
        plan_deck_copy_image_repairs,
    )

    if args.db is not None:
        db_path = args.db
    else:
        from paths import DEFAULT_DB_PATH

        db_path = Path(os.getenv("LLOVES_DB") or DEFAULT_DB_PATH)
    write = bool(args.apply) and not bool(args.dry_run)
    conn = _open_database(db_path, write=write)
    try:
        planned = plan_deck_copy_image_repairs(conn)
        print(f"target: {'PRODUCTION (' + reason + ')' if reason else 'local'}")
        print(f"database: {db_path}")
        if write:
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            written = apply_deck_copy_image_repairs(
                conn, planned.repairs, stamp=stamp
            )
            conn.commit()
            print(
                f"rows_written: {written['rows']} "
                f"file_pointers_written: {written['file_pointers']}"
            )
        print(
            format_deck_copy_image_report(
                planned, mode="apply" if write else "dry-run"
            )
        )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

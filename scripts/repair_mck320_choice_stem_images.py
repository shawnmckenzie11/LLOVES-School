#!/usr/bin/env python3
"""Clear MC heroes that were copied from a choice (MCK-320).

Dry-run is the default, including on production. It opens the database
read-only and does not run LMS startup migrations. It lists promoted
``image_url`` / ``image_src`` values on question rows and on class playlist
cards, with each question's library id and whether that library is shared.
Authored stem images are not targets, even when Canvas repeated the URL on
every choice.

``--apply`` opens the file read-write and updates only those JSON texts,
and only when the stored text still matches what was read. It does not run
boot migrations. A second run writes zero rows. ``--dry-run`` wins over
``--apply``.

Production (Fly, ``FLASK_ENV=production``, a data dir on ``/data``, or a
database on the ``/data`` volume) is refused before the database is opened
unless both ``--production`` and ``--confirm MCK-320`` are passed.

Usage::

    python3 scripts/repair_mck320_choice_stem_images.py --db /tmp/local.sqlite
    python3 scripts/repair_mck320_choice_stem_images.py --apply --db /tmp/local.sqlite
    python3 scripts/repair_mck320_choice_stem_images.py --production --confirm MCK-320 --apply --db /data/lloves.sqlite
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LMS_DIR = REPO_ROOT / "lms"
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

CONFIRM_TOKEN = "MCK-320"
EXIT_REFUSED = 2


def refusal_reason(db_path: Path | None) -> str | None:
    """Why this process must not open the database.

    Args:
        db_path: Sqlite path the operator passed, or ``None`` for the
            default local file (``LLOVES_DB`` or the LMS default).

    Returns:
        A short reason, or ``None`` when the target is a local file.
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
            "Remove multiple-choice choice images stored on the question stem. "
            "Dry-run by default. Production needs --production --confirm MCK-320."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print affected question ids and fields. This is the default.",
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
        help="Allow a production target. Needs --confirm MCK-320 too.",
    )
    parser.add_argument(
        "--confirm",
        default=None,
        help="Must be MCK-320 together with --production.",
    )
    return parser.parse_args(argv)


def production_gate(args: argparse.Namespace, reason: str | None) -> str | None:
    """Return a refusal message, or ``None`` when the run may continue.

    Args:
        args: Parsed CLI arguments.
        reason: Output of :func:`refusal_reason`.
    """
    has_flag = bool(args.production)
    has_confirm = args.confirm is not None
    if has_flag != has_confirm:
        return (
            "Refusing: --production and --confirm MCK-320 must be passed together."
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


class _Database:
    """Sqlite connection the repair can read, and write only in apply mode."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._lock = threading.Lock()

    def close(self) -> None:
        """Close the sqlite connection."""
        self.conn.close()


def _open_database(db_path: Path, *, write: bool) -> _Database:
    """Open sqlite without running LMS boot migrations.

    Dry-run uses ``mode=ro`` plus ``query_only``, so a backup is not migrated
    and not written. Apply opens the file read-write and still does not run
    boot migrations; it only updates the JSON columns the plan names.

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
    return _Database(conn)


def main(argv: list[str] | None = None) -> int:
    """Plan or apply the stem-image repair.

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
    from repair_choice_stem_images import (
        apply_choice_stem_image_repairs,
        format_choice_stem_image_report,
        plan_choice_stem_image_repairs,
    )

    if args.db is not None:
        db_path = args.db
    else:
        from paths import DEFAULT_DB_PATH

        db_path = Path(os.getenv("LLOVES_DB") or DEFAULT_DB_PATH)
    write = bool(args.apply) and not bool(args.dry_run)
    school = _open_database(db_path, write=write)
    try:
        repairs = plan_choice_stem_image_repairs(school)
        print(f"target: {'PRODUCTION (' + reason + ')' if reason else 'local'}")
        print(f"database: {db_path}")
        written = 0
        if write:
            written = apply_choice_stem_image_repairs(school, repairs)
            print(f"rows_written: {written}")
        for line in format_choice_stem_image_report(repairs, dry_run=not write):
            print(line)
    finally:
        school.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

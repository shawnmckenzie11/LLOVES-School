#!/usr/bin/env python3
"""Tag untagged participation sessions from each class schedule (MCK-265).

Dry-run is the default. It lists every counted participation session
whose run has no module: the date, the class and course, and the
proposed module and slot. A date the class calendar does not cover is
``UNMAPPED`` and is not given a guessed tag.

Only ``--apply`` writes, and it writes those tags onto blank
``participation_items`` rows. ``--dry-run`` wins over ``--apply``. A
second apply changes nothing, because a filled module is no longer
untagged.

Production (Fly, ``FLASK_ENV=production``, a data dir on ``/data``, or a
database on the ``/data`` volume) is refused before the database is
opened unless both ``--production`` and ``--confirm MCK-265`` are passed.

``app_state.current_class_id`` is never written. It is read before and
after; if it changed, the script exits 3.

Usage::

    python3 scripts/backfill_participation_tags.py --db /tmp/local.sqlite
    python3 scripts/backfill_participation_tags.py --apply --db /tmp/local.sqlite
    python3 scripts/backfill_participation_tags.py --production --confirm MCK-265
    python3 scripts/backfill_participation_tags.py --production --confirm MCK-265 --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LMS_DIR = REPO_ROOT / "lms"
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))


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


CONFIRM_TOKEN = "MCK-265"
EXIT_REFUSED = 2
EXIT_CURRENT_CLASS_CHANGED = 3


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments.

    Args:
        argv: Argument vector excluding the program name.

    Returns:
        Namespace with ``apply``, ``dry_run``, ``db``, ``production``,
        and ``confirm``.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Tag untagged participation sessions from each class schedule. "
            "Dry-run by default. Production needs --production --confirm MCK-265."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the untagged sessions and proposed tags. This is the default.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the proposed tags. Ignored when --dry-run is also set.",
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
        help="Allow a production target. Needs --confirm MCK-265 too.",
    )
    parser.add_argument(
        "--confirm",
        default=None,
        help="Must be MCK-265 together with --production.",
    )
    return parser.parse_args(argv)


def production_gate(args: argparse.Namespace, reason: str | None) -> str | None:
    """Return a refusal message, or ``None`` when the run may continue.

    Args:
        args: Parsed CLI arguments.
        reason: Output of :func:`refusal_reason`.

    Returns:
        A message for stderr, or ``None``.
    """
    has_flag = bool(args.production)
    has_confirm = args.confirm is not None
    if has_flag != has_confirm:
        return (
            "Refusing: --production and --confirm MCK-265 must be passed together."
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


def read_current_class_id(school) -> str | None:
    """Raw ``app_state.current_class_id`` value, or ``None`` when unset."""
    row = school.game.conn.execute(
        "SELECT value FROM app_state WHERE key = 'current_class_id'"
    ).fetchone()
    return None if row is None else str(row["value"])


def main(argv: list[str] | None = None) -> int:
    """Run the tag backfill, gated for production.

    Args:
        argv: Argument vector excluding the program name.

    Returns:
        ``0`` on success, ``2`` when refused, ``3`` when
        ``app_state.current_class_id`` changed.
    """
    args = _parse_args(argv)
    reason = refusal_reason(args.db)
    refusal = production_gate(args, reason)
    if refusal:
        print(refusal, file=sys.stderr)
        return EXIT_REFUSED
    from app import create_app
    from participation_tag_backfill import (
        apply_participation_tag_backfill,
        format_tag_plan,
        plan_participation_tag_backfill,
    )

    kwargs: dict = {"testing": True}
    if args.db is not None:
        kwargs["db_path"] = args.db
        kwargs["data_dir"] = args.db.parent
    app = create_app(**kwargs)
    school = app.config["SCHOOL_DB"]
    write = bool(args.apply) and not bool(args.dry_run)
    try:
        current_before = read_current_class_id(school)
        plan = plan_participation_tag_backfill(school)
        print(f"target: {'PRODUCTION (' + reason + ')' if reason else 'local'}")
        print(f"mode: {'APPLY' if write else 'DRY RUN (no writes; pass --apply to write)'}")
        for line in format_tag_plan(plan):
            print(line)
        written = 0
        if write:
            written = apply_participation_tag_backfill(school, plan)
        current_after = read_current_class_id(school)
        proposed = sum(1 for step in plan if not step.unmapped)
        unmapped = sum(1 for step in plan if step.unmapped)
        print(
            "summary: "
            f"untagged={len(plan)} proposed={proposed} unmapped={unmapped} "
            f"rows_changed={written} "
            f"current_class_id={current_before}->{current_after} "
            f"{'APPLIED' if write else 'DRY RUN, nothing written'}"
        )
        if current_after != current_before:
            print(
                "ERROR: app_state.current_class_id changed "
                f"({current_before} -> {current_after}). This script must never "
                "write it. Restore it before anyone opens the gradebook.",
                file=sys.stderr,
            )
            return EXIT_CURRENT_CLASS_CHANGED
    finally:
        school.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

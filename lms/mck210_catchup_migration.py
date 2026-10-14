"""One-time MCK-210 catch-up repair migration at app boot (prod-shaped DB only)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from catchup_backfill import RepairReport, repair_catchup

logger = logging.getLogger(__name__)

MIGRATION_KEY = "mck210_catchup_repair_v1"
OFFERING_IDS = (10, 11, 12)
RUN_TARGETS: tuple[tuple[int, str], ...] = (
    (9, "1b01"),
    (10, "9a20"),
    (10, "8312"),
    (9, "7e0c"),
    (10, "4373"),
    (9, "3b4b"),
    (9, "a5d4"),
    (8, "d20a"),
    (8, "0dae"),
    (8, "2af3"),
)
EXPECTED_REAL = 6
EXPECTED_TEST = 4
REOPEN_LINK_IDS = frozenset({5, 7, 8, 10})
REPLACE_LINK_IDS = frozenset({6, 9})
UNTOUCHED_LINK_IDS = frozenset({2, 4})
FORBIDDEN_RUN_KEY = "7656"


def ensure_migration_schema(conn: Any) -> None:
    """Create the data-migrations marker table if needed."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lms_data_migrations (
            migration_key TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            detail_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )


def migration_marker(conn: Any) -> dict[str, Any] | None:
    """Return an existing marker row for this migration, if any."""
    ensure_migration_schema(conn)
    row = conn.execute(
        "SELECT migration_key, status, applied_at, detail_json FROM lms_data_migrations WHERE migration_key = ?",
        (MIGRATION_KEY,),
    ).fetchone()
    if row is None:
        return None
    return {
        "migration_key": str(row[0]),
        "status": str(row[1]),
        "applied_at": str(row[2]),
        "detail_json": str(row[3] or "{}"),
    }


def prod_catchup_migration_eligible(school: Any) -> bool:
    """True only when this sqlite file looks like the prod MCK-210 repair target."""
    with school._lock:
        for oid in OFFERING_IDS:
            found = school.conn.execute(
                "SELECT id FROM course_offerings WHERE id = ?", (int(oid),)
            ).fetchone()
            if found is None:
                return False
        for class_id, needle in RUN_TARGETS:
            if school.get_catchup_repair_run_row(int(class_id), str(needle)) is None:
                return False
    return True


def _link_id_from_detail(detail: str, *, prefix: str) -> int | None:
    """Parse ``link_id=`` or ``discard link_id=`` from a report detail string."""
    text = str(detail or "")
    if prefix == "discard":
        match = re.search(r"discard link_id=(\d+)", text)
    else:
        match = re.search(r"link_id=(\d+)", text)
    if not match:
        return None
    return int(match.group(1))


def validate_mck210_repair_plan(report: RepairReport) -> tuple[bool, str]:
    """Return whether a dry-run repair plan matches ops expectations."""
    real = 0
    test = 0
    reopens: set[int] = set()
    replaces: set[int] = set()
    touched_forbidden: set[int] = set()

    for row in report.runs:
        action = str(row.action or "").lower()
        detail = str(row.detail or "")
        run_key = str(row.run_key or "")
        if FORBIDDEN_RUN_KEY in run_key:
            return False, f"plan touches forbidden run {FORBIDDEN_RUN_KEY}"

        if "classify real" in action:
            real += 1
        elif "classify test" in action:
            test += 1

        if "reopen" in action and "link" in action:
            lid = _link_id_from_detail(detail, prefix="link")
            if lid is not None:
                reopens.add(lid)

        if "discard" in action and "mint" in action:
            lid = _link_id_from_detail(detail, prefix="discard")
            if lid is not None:
                replaces.add(lid)

        for lid in UNTOUCHED_LINK_IDS:
            if f"link_id={lid}" in detail and (
                "reopen" in action or "discard" in action or "mint" in action
            ):
                touched_forbidden.add(lid)

    if real != EXPECTED_REAL:
        return False, f"expected {EXPECTED_REAL} real classifications, got {real}"
    if test != EXPECTED_TEST:
        return False, f"expected {EXPECTED_TEST} test classifications, got {test}"
    if reopens != set(REOPEN_LINK_IDS):
        return False, f"expected reopen link ids {sorted(REOPEN_LINK_IDS)}, got {sorted(reopens)}"
    if replaces != set(REPLACE_LINK_IDS):
        return False, f"expected replace link ids {sorted(REPLACE_LINK_IDS)}, got {sorted(replaces)}"
    if touched_forbidden:
        return False, f"plan would touch forbidden link ids {sorted(touched_forbidden)}"
    if any(r.action == "error" for r in report.runs):
        err = next(r for r in report.runs if r.action == "error")
        return False, err.detail or "repair error row"
    return True, "ok"


def plan_mck210_migration(school: Any) -> tuple[list[dict[str, Any]], RepairReport]:
    """Dry-run offering sync + catch-up repair (caller must hold school lock)."""
    plans = school.plan_sync_offering_live_schedules(offering_ids=list(OFFERING_IDS))
    if plans:
        school.apply_sync_offering_live_schedules(plans)
    targets = [(int(cid), str(key)) for cid, key in RUN_TARGETS]
    report = repair_catchup(school, run_targets=targets, apply=False)
    return plans, report


def run_mck210_catchup_migration_if_needed(school: Any) -> dict[str, Any] | None:
    """Apply the one-time catch-up repair on prod-shaped databases only.

    Returns a small status dict for tests/logs, or ``None`` when skipped.
    """
    from school_db import boot_schema_lock

    with boot_schema_lock(school.db_path):
        with school._lock:
            ensure_migration_schema(school.conn)
            if migration_marker(school.conn) is not None:
                logger.info("MCK-210 catch-up migration already applied (%s)", MIGRATION_KEY)
                return {"status": "already_applied"}

            if not prod_catchup_migration_eligible(school):
                logger.debug("MCK-210 catch-up migration skipped (database not eligible)")
                return {"status": "skipped_not_eligible"}

            school.conn.execute("BEGIN IMMEDIATE")
            try:
                plans, preview = plan_mck210_migration(school)
                ok, reason = validate_mck210_repair_plan(preview)
                if not ok:
                    school.conn.execute("ROLLBACK")
                    logger.warning(
                        "MCK-210 catch-up migration aborted with no writes: %s", reason
                    )
                    return {"status": "aborted", "reason": reason}

                targets = [(int(cid), str(key)) for cid, key in RUN_TARGETS]
                applied = repair_catchup(school, run_targets=targets, apply=True)
                now = datetime.now(timezone.utc).isoformat()
                detail = {
                    "offerings_updated": len(plans),
                    "repair_actions": len(applied.runs),
                    "run_targets": list(RUN_TARGETS),
                }
                school.conn.execute(
                    """
                    INSERT INTO lms_data_migrations (migration_key, status, applied_at, detail_json)
                    VALUES (?, 'applied', ?, ?)
                    """,
                    (MIGRATION_KEY, now, json.dumps(detail)),
                )
                school.conn.execute("COMMIT")
                logger.warning(
                    "MCK-210 catch-up migration applied offerings=%s repair_rows=%s",
                    len(plans),
                    len(applied.runs),
                )
                return {"status": "applied", **detail}
            except Exception:
                school.conn.execute("ROLLBACK")
                logger.exception("MCK-210 catch-up migration failed; rolled back")
                raise

"""One-time MCK-210 catch-up repair migration at app boot (prod-shaped DB only)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

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
FORBIDDEN_RUN_KEY = "7656"
FORBIDDEN_LINK_IDS = frozenset({2, 4})

LinkKind = Literal["none", "reopen", "discard", "mint"]


@dataclass(frozen=True)
class ExpectedRunPlan:
    """Ops-approved dry-run outcome for one ``(class_id, run_key suffix)`` pair."""

    class_id: int
    suffix: str
    classify: Literal["real", "test"]
    link: LinkKind
    link_id: int | None = None
    mint_module: str | None = None
    mint_slot: str | None = None


MIGRATION_RUN_ORDER: tuple[tuple[int, str], ...] = (
    (9, "1b01"),
    (10, "8312"),
    (10, "9a20"),
    (9, "7e0c"),
    (10, "4373"),
    (9, "3b4b"),
    (9, "a5d4"),
    (8, "0dae"),
    (8, "d20a"),
    (8, "2af3"),
)

EXPECTED_RUN_PLANS: tuple[ExpectedRunPlan, ...] = (
    ExpectedRunPlan(9, "1b01", "real", "reopen", link_id=5),
    ExpectedRunPlan(10, "9a20", "real", "mint", mint_module="M2", mint_slot="C3"),
    ExpectedRunPlan(10, "8312", "test", "discard", link_id=6),
    ExpectedRunPlan(9, "7e0c", "real", "reopen", link_id=8),
    ExpectedRunPlan(10, "4373", "real", "reopen", link_id=7),
    ExpectedRunPlan(9, "3b4b", "test", "none"),
    ExpectedRunPlan(9, "a5d4", "test", "none"),
    ExpectedRunPlan(8, "d20a", "real", "mint", mint_module="M2", mint_slot="C3"),
    ExpectedRunPlan(8, "0dae", "test", "discard", link_id=9),
    ExpectedRunPlan(8, "2af3", "real", "reopen", link_id=10),
)

EXPECTED_BY_KEY: dict[tuple[int, str], ExpectedRunPlan] = {
    (spec.class_id, spec.suffix): spec for spec in EXPECTED_RUN_PLANS
}


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


def _suffix_for_row(class_id: int, run_key: str) -> str | None:
    """Map a report row to a known run suffix for ``class_id``."""
    cid = int(class_id)
    key = str(run_key or "")
    if FORBIDDEN_RUN_KEY in key:
        return FORBIDDEN_RUN_KEY
    for expect_cid, suffix in RUN_TARGETS:
        if expect_cid == cid and suffix in key:
            return suffix
    return None


@dataclass
class _ParsedRunPlan:
    """Observed dry-run rows grouped for one target run."""

    classify: str | None = None
    link_action: str | None = None
    link_id: int | None = None
    mint_module: str | None = None
    mint_slot: str | None = None
    extras: list[str] = field(default_factory=list)


def _parse_run_plans(report: RepairReport) -> dict[tuple[int, str], _ParsedRunPlan]:
    """Group repair report rows by ``(class_id, suffix)``."""
    plans: dict[tuple[int, str], _ParsedRunPlan] = {}
    for row in report.runs:
        suffix = _suffix_for_row(row.class_id, row.run_key)
        if suffix is None:
            continue
        if suffix == FORBIDDEN_RUN_KEY:
            key = (int(row.class_id), FORBIDDEN_RUN_KEY)
            plans.setdefault(key, _ParsedRunPlan()).extras.append(row.action)
            continue
        key = (int(row.class_id), suffix)
        plan = plans.setdefault(key, _ParsedRunPlan())
        action = str(row.action or "").lower()
        detail = str(row.detail or "")

        if row.action == "error":
            plan.extras.append(f"error:{detail}")
            continue

        if "classify real" in action:
            if plan.classify is not None:
                plan.extras.append(f"duplicate classify:{row.action}")
            plan.classify = "real"
            continue
        if "classify test" in action:
            if plan.classify is not None:
                plan.extras.append(f"duplicate classify:{row.action}")
            plan.classify = "test"
            continue

        if "reopen" in action and "link" in action:
            match = re.search(r"link_id=(\d+)", detail)
            lid = int(match.group(1)) if match else None
            if plan.link_action is not None:
                plan.extras.append(f"duplicate link:{row.action}")
            plan.link_action = "reopen"
            plan.link_id = lid
            if lid in FORBIDDEN_LINK_IDS:
                plan.extras.append(f"forbidden link_id={lid}")
            continue

        if "discard" in action and "mint" in action:
            match = re.search(r"discard link_id=(\d+)", detail)
            lid = int(match.group(1)) if match else None
            if plan.link_action is not None:
                plan.extras.append(f"duplicate link:{row.action}")
            plan.link_action = "discard_mint"
            plan.link_id = lid
            if lid in FORBIDDEN_LINK_IDS:
                plan.extras.append(f"forbidden link_id={lid}")
            continue

        if "discard" in action and "link" in action:
            match = re.search(r"link_id=(\d+)", detail)
            lid = int(match.group(1)) if match else None
            if plan.link_action is not None:
                plan.extras.append(f"duplicate link:{row.action}")
            plan.link_action = "discard"
            plan.link_id = lid
            if lid in FORBIDDEN_LINK_IDS:
                plan.extras.append(f"forbidden link_id={lid}")
            continue

        if "mint" in action and "link" in action:
            if plan.link_action is not None:
                plan.extras.append(f"duplicate link:{row.action}")
            plan.link_action = "mint"
            plan.mint_module = str(row.live_module or "")
            plan.mint_slot = str(row.live_slot or "")
            continue

        if "participation" in action:
            continue
        if action.startswith("skip") and "link" not in action:
            continue
        plan.extras.append(row.action)

    return plans


def _audit_log_plan(parsed: dict[tuple[int, str], _ParsedRunPlan]) -> None:
    """Emit one WARNING audit line per expected run."""
    for spec in EXPECTED_RUN_PLANS:
        key = (spec.class_id, spec.suffix)
        observed = parsed.get(key, _ParsedRunPlan())
        logger.warning(
            "MCK-210 migration plan audit class_id=%s run=%s classify=%s link=%s link_id=%s extras=%s",
            spec.class_id,
            spec.suffix,
            observed.classify,
            observed.link_action,
            observed.link_id,
            observed.extras,
        )


def _match_spec_link(plan: _ParsedRunPlan, spec: ExpectedRunPlan) -> tuple[bool, str]:
    """Compare parsed link actions to one expected row."""
    if spec.link == "none":
        if plan.link_action is not None:
            return False, f"expected no link action, got {plan.link_action}"
        return True, "ok"
    if spec.link == "reopen":
        if plan.link_action != "reopen" or plan.link_id != spec.link_id:
            return False, f"expected reopen link {spec.link_id}, got {plan.link_action} {plan.link_id}"
        return True, "ok"
    if spec.link == "discard":
        if plan.link_action != "discard" or plan.link_id != spec.link_id:
            return False, f"expected discard link {spec.link_id}, got {plan.link_action} {plan.link_id}"
        return True, "ok"
    if spec.link == "mint":
        if plan.link_action != "mint":
            return False, f"expected mint open link, got {plan.link_action}"
        if plan.mint_module != spec.mint_module or plan.mint_slot != spec.mint_slot:
            return False, f"expected mint {spec.mint_module} {spec.mint_slot}, got {plan.mint_module} {plan.mint_slot}"
        return True, "ok"
    return False, f"unknown spec link {spec.link}"


def validate_mck210_single_run(report: RepairReport, spec: ExpectedRunPlan) -> tuple[bool, str]:
    """Validate dry-run output for exactly one target run."""
    parsed = _parse_run_plans(report)
    key = (spec.class_id, spec.suffix)
    for other_key, plan in parsed.items():
        if other_key != key:
            return False, f"unexpected planned run {other_key[0]}/{other_key[1]}"
        if other_key[1] == FORBIDDEN_RUN_KEY:
            return False, f"plan touches forbidden run {FORBIDDEN_RUN_KEY}"
    observed = parsed.get(key)
    if observed is None:
        return False, "missing planned actions"
    logger.warning(
        "MCK-210 migration plan audit class_id=%s run=%s classify=%s link=%s link_id=%s extras=%s",
        spec.class_id,
        spec.suffix,
        observed.classify,
        observed.link_action,
        observed.link_id,
        observed.extras,
    )
    if observed.classify != spec.classify:
        return False, f"expected classify {spec.classify}, got {observed.classify}"
    if observed.extras:
        return False, f"unexpected actions {observed.extras}"
    ok, detail = _match_spec_link(observed, spec)
    if not ok:
        return False, f"class_id={spec.class_id} run={spec.suffix}: {detail}"
    for row in report.runs:
        if row.action == "error":
            return False, row.detail or "repair error row"
        if FORBIDDEN_RUN_KEY in str(row.run_key):
            return False, f"plan touches forbidden run {FORBIDDEN_RUN_KEY}"
        suffix = _suffix_for_row(row.class_id, row.run_key)
        if suffix not in (None, spec.suffix):
            return False, f"unexpected report row: class={row.class_id} run={row.run_key} {row.action}"
        detail_text = str(row.detail or "")
        for lid in FORBIDDEN_LINK_IDS:
            if f"link_id={lid}" in detail_text and spec.suffix in str(row.run_key):
                return False, f"plan would touch forbidden link {lid}"
    return True, "ok"


def validate_mck210_repair_plan(report: RepairReport) -> tuple[bool, str]:
    """Validate a combined dry-run report (used in unit tests)."""
    for row in report.runs:
        if row.action == "error":
            return False, row.detail or "repair error row"
        if FORBIDDEN_RUN_KEY in str(row.run_key):
            return False, f"plan touches forbidden run {FORBIDDEN_RUN_KEY}"
        detail_text = str(row.detail or "")
        for lid in FORBIDDEN_LINK_IDS:
            if re.search(rf"link_id={lid}\b", detail_text):
                return False, f"plan would touch forbidden link {lid}"
    parsed = _parse_run_plans(report)
    expected_keys = {(spec.class_id, spec.suffix) for spec in EXPECTED_RUN_PLANS}
    parsed_keys = {k for k in parsed if k[1] != FORBIDDEN_RUN_KEY}
    if parsed_keys != expected_keys:
        extra = parsed_keys - expected_keys
        missing = expected_keys - parsed_keys
        return False, f"plan keys mismatch extra={sorted(extra)} missing={sorted(missing)}"
    if any(k[1] == FORBIDDEN_RUN_KEY for k in parsed):
        return False, f"plan touches forbidden run {FORBIDDEN_RUN_KEY}"
    _audit_log_plan(parsed)
    for spec in EXPECTED_RUN_PLANS:
        subset = RepairReport(
            dry_run=True,
            runs=[r for r in report.runs if _suffix_for_row(r.class_id, r.run_key) == spec.suffix],
        )
        ok, reason = validate_mck210_single_run(subset, spec)
        if not ok:
            return False, f"class_id={spec.class_id} run={spec.suffix}: {reason}"
    return True, "ok"


def plan_mck210_migration(school: Any) -> tuple[list[dict[str, Any]], RepairReport]:
    """Preview offering sync and catch-up repair. Does not write.

    Production apply stays in ``run_mck210_catchup_migration_if_needed``.
    Offering end times must already be filled or the repair preview will
    not match the ops table. The returned report is always ``dry_run``.
    """
    plans = school.plan_sync_offering_live_schedules(offering_ids=list(OFFERING_IDS))
    combined: list = []
    for class_id, suffix in MIGRATION_RUN_ORDER:
        spec = EXPECTED_BY_KEY[(int(class_id), str(suffix))]
        preview = repair_catchup(
            school, run_targets=[(int(class_id), str(suffix))], apply=False
        )
        ok, reason = validate_mck210_single_run(preview, spec)
        if not ok:
            return plans, RepairReport(dry_run=True, runs=combined)
        combined.extend(preview.runs)
    return plans, RepairReport(dry_run=True, runs=combined)


def run_mck210_catchup_migration_if_needed(school: Any) -> dict[str, Any] | None:
    """Apply the one-time catch-up repair on prod-shaped databases only.

    Returns a small status dict for tests/logs, or ``None`` when skipped.
    """
    from school_db import boot_schema_lock

    with boot_schema_lock(school.db_path):
        with school._lock:
            ensure_migration_schema(school.conn)
            if migration_marker(school.conn) is not None:
                logger.warning(
                    "MCK-210 catch-up migration already applied (%s)", MIGRATION_KEY
                )
                return {"status": "already_applied"}

            if not prod_catchup_migration_eligible(school):
                logger.debug("MCK-210 catch-up migration skipped (database not eligible)")
                return {"status": "skipped_not_eligible"}

            school.conn.execute("BEGIN IMMEDIATE")
            try:
                plans = school.plan_sync_offering_live_schedules(
                    offering_ids=list(OFFERING_IDS)
                )
                if plans:
                    school.apply_sync_offering_live_schedules(plans)
                applied_rows = []
                for class_id, suffix in MIGRATION_RUN_ORDER:
                    spec = EXPECTED_BY_KEY[(int(class_id), str(suffix))]
                    preview = repair_catchup(
                        school,
                        run_targets=[(int(class_id), str(suffix))],
                        apply=False,
                    )
                    ok, reason = validate_mck210_single_run(preview, spec)
                    if not ok:
                        school.conn.execute("ROLLBACK")
                        logger.warning(
                            "MCK-210 catch-up migration aborted with no writes: %s", reason
                        )
                        return {"status": "aborted", "reason": reason}
                    step = repair_catchup(
                        school,
                        run_targets=[(int(class_id), str(suffix))],
                        apply=True,
                    )
                    applied_rows.extend(step.runs)
                applied = RepairReport(dry_run=False, runs=applied_rows)
                for row in applied.runs:
                    suffix = _suffix_for_row(row.class_id, row.run_key)
                    if suffix and suffix != FORBIDDEN_RUN_KEY:
                        logger.warning(
                            "MCK-210 migration applied class_id=%s run=%s action=%s detail=%s",
                            row.class_id,
                            suffix,
                            row.action,
                            row.detail,
                        )
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

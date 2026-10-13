"""MCK-204: backfill participation + catch-up links from ``live_result_snapshots``.

Offered individual questions are exactly those with at least one student
answer in the snapshot (unpublished or unanswered prompts are omitted).
Runs with no snapshot rows (pre-MCK-45, before 2025-10-01) are skipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from participation import new_token

MCK45_SNAPSHOT_CUTOFF = "2025-10-01"
PRIORITY_MODULE = "M2"
PRIORITY_SLOT = "C1"


@dataclass
class BackfillRunReport:
    """One class run considered by the backfill."""

    class_id: int
    course_code: str
    run_key: str
    live_module: str
    live_slot: str
    action: str
    detail: str = ""
    items: int = 0
    answers: int = 0


@dataclass
class BackfillReport:
    """Aggregate CLI output."""

    dry_run: bool
    runs: list[BackfillRunReport] = field(default_factory=list)
    skipped_no_snapshots: list[BackfillRunReport] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        """Human-readable lines for stdout."""
        mode = "DRY RUN" if self.dry_run else "APPLIED"
        lines = [f"catch-up backfill ({mode})"]
        for row in self.runs:
            lines.append(
                f"  {row.course_code} class={row.class_id} {row.live_module} {row.live_slot} "
                f"run={row.run_key[:24]}… {row.action}"
                + (f" items={row.items} answers={row.answers}" if row.items else "")
                + (f" — {row.detail}" if row.detail else "")
            )
        if self.skipped_no_snapshots:
            lines.append("skipped (no MCK-45 snapshot data):")
            for row in self.skipped_no_snapshots:
                lines.append(
                    f"  {row.course_code} class={row.class_id} {row.live_module} {row.live_slot} "
                    f"{row.detail}"
                )
        return lines


def _run_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Process M2 C1 first, then module/slot lexicographically."""
    module = str(row.get("live_module") or "")
    slot = str(row.get("live_slot") or "")
    priority = 0 if module == PRIORITY_MODULE and slot.upper() == PRIORITY_SLOT else 1
    return (priority, str(row.get("course_code") or ""), module, slot, str(row.get("run_key") or ""))


def backfill_catchup_from_snapshots(
    school: Any,
    *,
    course: str | None = None,
    module: str | None = None,
    slot: str | None = None,
    apply: bool = False,
) -> BackfillReport:
    """Create participation rows and catch-up links from saved End snapshots.

    Args:
        school: ``SchoolDB`` instance.
        course: Optional Ontario course code filter (e.g. ``MCF3M``).
        module: Optional live module filter (e.g. ``M2``).
        slot: Optional live slot filter (e.g. ``C1``).
        apply: When False (default), only report planned writes.

    Returns:
        Structured report for logging or tests.
    """
    report = BackfillReport(dry_run=not apply)
    candidates = school.list_catchup_backfill_candidates(
        course_code=course, live_module=module, live_slot=slot
    )
    candidates.sort(key=_run_sort_key)
    for row in candidates:
        class_id = int(row["class_id"])
        run_key = str(row["run_key"])
        if not row.get("has_snapshots"):
            report.skipped_no_snapshots.append(
                BackfillRunReport(
                    class_id=class_id,
                    course_code=str(row.get("course_code") or ""),
                    run_key=run_key,
                    live_module=str(row.get("live_module") or ""),
                    live_slot=str(row.get("live_slot") or ""),
                    action="skip",
                    detail=row.get("skip_reason") or "no snapshot rows",
                )
            )
            continue
        if row.get("already_backfilled"):
            report.runs.append(
                BackfillRunReport(
                    class_id=class_id,
                    course_code=str(row.get("course_code") or ""),
                    run_key=run_key,
                    live_module=str(row.get("live_module") or ""),
                    live_slot=str(row.get("live_slot") or ""),
                    action="skip",
                    detail="already has catch-up link",
                )
            )
            continue
        offered = int(row.get("offered_count") or 0)
        if offered <= 0:
            report.runs.append(
                BackfillRunReport(
                    class_id=class_id,
                    course_code=str(row.get("course_code") or ""),
                    run_key=run_key,
                    live_module=str(row.get("live_module") or ""),
                    live_slot=str(row.get("live_slot") or ""),
                    action="skip",
                    detail="no individual answers in snapshot",
                )
            )
            continue
        if apply:
            written = school.apply_catchup_backfill_run(class_id, run_key)
            items = int(written.get("items") or 0)
            answers = int(written.get("answers") or 0)
            action = "wrote"
        else:
            items = offered
            answers = int(row.get("answer_rows") or 0)
            action = "would write"
        report.runs.append(
            BackfillRunReport(
                class_id=class_id,
                course_code=str(row.get("course_code") or ""),
                run_key=run_key,
                live_module=str(row.get("live_module") or ""),
                live_slot=str(row.get("live_slot") or ""),
                action=action,
                items=items,
                answers=answers,
            )
        )
    return report


def new_catchup_token() -> str:
    """Proxy for tests that patch token generation."""
    return new_token()

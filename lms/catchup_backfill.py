"""MCK-204 / MCK-210: backfill and repair catch-up links from snapshots.

Offered individual questions are exactly those with at least one student
answer in the snapshot (unpublished or unanswered prompts are omitted).
Runs with no snapshot rows (pre-MCK-45, before 2025-10-01) are skipped for
participation writes but may still be classified when a session ``ended_at``
exists.

Catch-up links follow MCK-207: one shared OPEN link per class lesson slot on
the latest *real* run (schedule from offerings + ``live_class_schedules.json``,
slack from ``config/live_class_run.json``). Test runs never own the link.
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
    """One class run considered by the backfill or repair."""

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
            prefix = str(row.run_key)[:4] if row.run_key else "????"
            lines.append(
                f"  [{prefix}] {row.course_code} class={row.class_id} "
                f"{row.live_module} {row.live_slot} "
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


@dataclass
class RepairReport:
    """Aggregate output for ``repair-catchup`` (same row shape as backfill)."""

    dry_run: bool
    runs: list[BackfillRunReport] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        """Human-readable lines for stdout."""
        mode = "DRY RUN" if self.dry_run else "APPLIED"
        lines = [f"catch-up repair ({mode})"]
        for row in self.runs:
            prefix = str(row.run_key)[:4] if row.run_key else "????"
            lines.append(
                f"  [{prefix}] {row.course_code} class={row.class_id} "
                f"{row.live_module} {row.live_slot} "
                f"run={row.run_key[:24]}… {row.action}"
                + (f" items={row.items} answers={row.answers}" if row.items else "")
                + (f" — {row.detail}" if row.detail else "")
            )
        return lines


def _run_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Process M2 C1 first, then module/slot lexicographically."""
    module = str(row.get("live_module") or "")
    slot = str(row.get("live_slot") or "")
    priority = 0 if module == PRIORITY_MODULE and slot.upper() == PRIORITY_SLOT else 1
    return (
        priority,
        str(row.get("course_code") or ""),
        module,
        slot,
        str(row.get("ended_at") or ""),
        str(row.get("run_key") or ""),
    )


def _slot_key(row: dict[str, Any]) -> tuple[int, str, str]:
    return (int(row["class_id"]), str(row.get("live_module") or ""), str(row.get("live_slot") or ""))


def sync_catchup_scope(
    school: Any,
    *,
    course: str | None = None,
    module: str | None = None,
    slot: str | None = None,
    apply: bool = False,
    include_legacy_without_snapshots: bool = False,
) -> tuple[list[BackfillRunReport], list[BackfillRunReport]]:
    """Classify runs and align shared catch-up links for one scope.

    Args:
        school: ``SchoolDB`` instance.
        course: Optional Ontario course code filter.
        module: Optional live module filter.
        slot: Optional live slot filter.
        apply: When False, only report planned changes.
        include_legacy_without_snapshots: When True, append legacy-only rows to
            ``skipped`` (backfill). Repair passes False.

    Returns:
        Tuple of per-run reports and skipped-no-snapshot rows.
    """
    reports: list[BackfillRunReport] = []
    skipped: list[BackfillRunReport] = []
    rows = school.list_catchup_sync_runs(
        course_code=course, live_module=module, live_slot=slot
    )
    rows.sort(key=_run_sort_key)

    by_slot: dict[tuple[int, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if not row.get("has_snapshots"):
            if include_legacy_without_snapshots:
                skipped.append(
                    BackfillRunReport(
                        class_id=int(row["class_id"]),
                        course_code=str(row.get("course_code") or ""),
                        run_key=str(row.get("run_key") or ""),
                        live_module=str(row.get("live_module") or ""),
                        live_slot=str(row.get("live_slot") or ""),
                        action="skip",
                        detail=row.get("skip_reason") or "no snapshot rows",
                    )
                )
            continue
        by_slot.setdefault(_slot_key(row), []).append(row)

    for slot_rows in by_slot.values():
        slot_reports = _sync_one_lesson_slot(school, slot_rows, apply=apply)
        reports.extend(slot_reports)
    reports.sort(key=lambda r: _run_sort_key({"course_code": r.course_code, "live_module": r.live_module, "live_slot": r.live_slot, "run_key": r.run_key, "ended_at": ""}))
    return reports, skipped


def _sync_one_lesson_slot(
    school: Any,
    slot_rows: list[dict[str, Any]],
    *,
    apply: bool,
) -> list[BackfillRunReport]:
    """Classify every run in a slot and point the shared link at the latest real run."""
    out: list[BackfillRunReport] = []
    classified: list[dict[str, Any]] = []
    for row in slot_rows:
        class_id = int(row["class_id"])
        run_key = str(row["run_key"])
        offered = int(row.get("offered_count") or 0)
        base = BackfillRunReport(
            class_id=class_id,
            course_code=str(row.get("course_code") or ""),
            run_key=run_key,
            live_module=str(row.get("live_module") or ""),
            live_slot=str(row.get("live_slot") or ""),
            action="skip",
        )
        if offered <= 0:
            base.detail = "no individual answers in snapshot"
            out.append(base)
            continue
        items = int(row.get("existing_items") or 0)
        answers = int(row.get("answer_rows") or 0)
        if apply:
            written = school.write_participation_from_snapshots(class_id, run_key)
            items = int(written.get("items") or 0)
            answers = int(written.get("answers") or 0)
            base.items = items
            base.answers = answers
            base.action = "wrote participation" if items else "skip"
        else:
            base.items = offered
            base.answers = answers
            base.action = "would write participation"

        ended_at = str(row.get("ended_at") or "")
        if apply:
            is_real = school.classify_participation_run(class_id, run_key, ended_at=ended_at)
        else:
            is_real = school.preview_participation_run_real(class_id, ended_at=ended_at)
        row["is_real"] = is_real
        row["ended_at"] = ended_at
        classified.append(row)

        out.append(base)
        kind_action = "classify real" if is_real else "classify test"
        if not apply:
            kind_action = f"would {kind_action}"
        out.append(
            BackfillRunReport(
                class_id=class_id,
                course_code=base.course_code,
                run_key=run_key,
                live_module=base.live_module,
                live_slot=base.live_slot,
                action=kind_action,
                items=base.items,
                answers=base.answers,
            )
        )

    real_runs = [r for r in classified if r.get("is_real")]
    real_runs.sort(key=lambda r: str(r.get("ended_at") or ""), reverse=True)
    winner = real_runs[0] if real_runs else None
    if winner is None:
        for row in classified:
            if not row.get("is_real"):
                link_note = "no shared link (test run)"
                if not apply:
                    link_note = f"would ensure {link_note}"
                out.append(
                    BackfillRunReport(
                        class_id=int(row["class_id"]),
                        course_code=str(row.get("course_code") or ""),
                        run_key=str(row["run_key"]),
                        live_module=str(row.get("live_module") or ""),
                        live_slot=str(row.get("live_slot") or ""),
                        action="skip link",
                        detail=link_note,
                    )
                )
        return out

    cid = int(winner["class_id"])
    mod = str(winner.get("live_module") or "")
    sl = str(winner.get("live_slot") or "")
    win_key = str(winner["run_key"])
    before = school.catchup_link_for_slot(cid, mod, sl)
    before_key = str(before.get("run_key") or "") if before else ""
    before_open = bool(int(before.get("is_open") or 0)) if before else False

    if apply:
        school.upsert_real_catchup_link_for_slot(cid, win_key, mod, sl)
        after = school.catchup_link_for_slot(cid, mod, sl)
        after_open = bool(int(after.get("is_open") or 0)) if after else False
        link_action = "open shared link"
        detail = f"run_key={win_key[:8]}…"
        if before_key and before_key != win_key:
            detail = f"repoint from {before_key[:8]}… to {win_key[:8]}…"
        elif before_key == win_key and not before_open and after_open:
            detail = "reopen existing link"
    else:
        link_action = "would open shared link"
        detail = f"run_key={win_key[:8]}…"
        if before_key and before_key != win_key:
            detail = f"repoint from {before_key[:8]}… to {win_key[:8]}…"
        elif before_key == win_key and not before_open:
            detail = "reopen existing link"

    out.append(
        BackfillRunReport(
            class_id=cid,
            course_code=str(winner.get("course_code") or ""),
            run_key=win_key,
            live_module=mod,
            live_slot=sl,
            action=link_action,
            detail=detail,
        )
    )

    for row in classified:
        if str(row["run_key"]) == win_key:
            continue
        if row.get("is_real"):
            continue
        note = "test run; no link"
        if before_key == str(row["run_key"]):
            note = "test run; would clear link ownership"
        out.append(
            BackfillRunReport(
                class_id=int(row["class_id"]),
                course_code=str(row.get("course_code") or ""),
                run_key=str(row["run_key"]),
                live_module=str(row.get("live_module") or ""),
                live_slot=str(row.get("live_slot") or ""),
                action="skip link" if apply else "would skip link",
                detail=note,
            )
        )
    return out


def backfill_catchup_from_snapshots(
    school: Any,
    *,
    course: str | None = None,
    module: str | None = None,
    slot: str | None = None,
    apply: bool = False,
) -> BackfillReport:
    """Create participation rows and catch-up links from saved End snapshots."""
    runs, skipped = sync_catchup_scope(
        school,
        course=course,
        module=module,
        slot=slot,
        apply=apply,
        include_legacy_without_snapshots=True,
    )
    return BackfillReport(dry_run=not apply, runs=runs, skipped_no_snapshots=skipped)


def repair_catchup(
    school: Any,
    *,
    course: str | None = None,
    module: str | None = None,
    slot: str | None = None,
    apply: bool = False,
) -> RepairReport:
    """Re-classify and fix catch-up links for existing snapshot runs (MCK-210)."""
    runs, _skipped = sync_catchup_scope(
        school,
        course=course,
        module=module,
        slot=slot,
        apply=apply,
        include_legacy_without_snapshots=False,
    )
    return RepairReport(dry_run=not apply, runs=runs)


def new_catchup_token() -> str:
    """Proxy for tests that patch token generation."""
    return new_token()

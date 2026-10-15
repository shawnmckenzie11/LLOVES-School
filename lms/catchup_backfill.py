"""MCK-204 / MCK-210: backfill and repair catch-up links from snapshots.

Offered individual questions are exactly those with at least one student
answer in the snapshot (unpublished or unanswered prompts are omitted).

Catch-up links follow MCK-207 / MCK-233: one shared OPEN link per class
lesson slot on the latest *real* run (teacher checkbox at End Live Class;
no schedule auto-discard). Repair targets explicit ``live_class_sessions.id``
values and never reclassifies sibling runs in the same slot.
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
    session_id: int | None = None


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
            sid = f" session={row.session_id}" if row.session_id else ""
            lines.append(
                f"  [{prefix}]{sid} {row.course_code} class={row.class_id} "
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
    """Aggregate output for ``repair-catchup``."""

    dry_run: bool
    runs: list[BackfillRunReport] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        """Human-readable lines for stdout."""
        mode = "DRY RUN" if self.dry_run else "APPLIED"
        lines = [f"catch-up repair ({mode})"]
        for row in self.runs:
            prefix = str(row.run_key)[:4] if row.run_key else "????"
            sid = f" session={row.session_id}" if row.session_id else ""
            lines.append(
                f"  [{prefix}]{sid} {row.course_code} class={row.class_id} "
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


def _run_real_for_link_decision(
    school: Any, class_id: int, run_key: str, *, ended_at: str | None = None
) -> bool:
    """Return whether ``run_key`` should be treated as the slot's real run."""
    kind = school.participation_run_kind(int(class_id), str(run_key))
    if kind is not None:
        return bool(kind.get("is_real"))
    return school.preview_participation_run_real(
        int(class_id), run_key=str(run_key)
    )


def _plan_real_run_link(
    school: Any,
    row: dict[str, Any],
    *,
    apply: bool,
) -> BackfillRunReport | None:
    """Mint or reopen a link for one real run without repointing test tokens."""
    cid = int(row["class_id"])
    key = str(row["run_key"])
    mod = str(row.get("live_module") or "")
    sl = str(row.get("live_slot") or "")
    slot_link = row.get("slot_link")
    if slot_link is None:
        action = "mint open link"
        detail = "new token"
        if apply:
            token = school.mint_open_catchup_link(cid, key, mod, sl)
            detail = f"token={token[:8]}…"
        else:
            action = f"would {action}"
        return BackfillRunReport(
            class_id=cid,
            course_code=str(row.get("course_code") or ""),
            run_key=key,
            live_module=mod,
            live_slot=sl,
            action=action,
            detail=detail,
            session_id=row.get("session_id"),
        )
    link_id = int(slot_link["id"])
    link_key = str(slot_link.get("run_key") or "")
    if link_key == key:
        closed = not bool(int(slot_link.get("is_open") or 0))
        if not closed:
            return None
        action = "reopen link"
        if apply:
            school.reopen_catchup_link(link_id)
        else:
            action = f"would {action}"
        return BackfillRunReport(
            class_id=cid,
            course_code=str(row.get("course_code") or ""),
            run_key=key,
            live_module=mod,
            live_slot=sl,
            action=action,
            detail=f"link_id={link_id}",
            session_id=row.get("session_id"),
        )
    if _run_real_for_link_decision(school, cid, link_key):
        return BackfillRunReport(
            class_id=cid,
            course_code=str(row.get("course_code") or ""),
            run_key=key,
            live_module=mod,
            live_slot=sl,
            action="skip link",
            detail=f"slot owned by real run {link_key[:8]}… (link_id={link_id})",
            session_id=row.get("session_id"),
        )
    action = "discard test link; mint open link"
    detail = f"discard link_id={link_id}; new token"
    if apply:
        token = school.mint_open_catchup_link(
            cid, key, mod, sl, discard_slot_link_id=link_id
        )
        detail = f"discard link_id={link_id}; token={token[:8]}…"
    else:
        action = f"would {action}"
    return BackfillRunReport(
        class_id=cid,
        course_code=str(row.get("course_code") or ""),
        run_key=key,
        live_module=mod,
        live_slot=sl,
        action=action,
        detail=detail,
        session_id=row.get("session_id"),
    )


def repair_catchup_sessions(
    school: Any,
    session_ids: list[int],
    *,
    apply: bool = False,
) -> RepairReport:
    """Repair only the listed ended live sessions (by ``live_class_sessions.id``)."""
    reports: list[BackfillRunReport] = []
    for raw_id in session_ids:
        sid = int(raw_id)
        reports.extend(_repair_one_live_session(school, sid, apply=apply))
    return RepairReport(dry_run=not apply, runs=reports)


def repair_catchup_run_keys(
    school: Any,
    targets: list[tuple[int, str]],
    *,
    apply: bool = False,
) -> RepairReport:
    """Repair snapshot runs identified by ``(class_id, run_key prefix)`` pairs."""
    from catchup_schedule import CatchupScheduleError

    reports: list[BackfillRunReport] = []
    for class_id, run_key_match in targets:
        cid = int(class_id)
        needle = str(run_key_match or "").strip()
        try:
            school.require_live_class_schedule_for_class(cid)
        except CatchupScheduleError as exc:
            raise CatchupScheduleError(str(exc)) from exc
        row = school.get_catchup_repair_run_row(cid, needle)
        if row is None:
            try:
                school.resolve_snapshot_run_key(cid, needle)
            except ValueError as exc:
                detail = str(exc)
            else:
                detail = f"no snapshot row for class_id={cid} run_key~{needle}"
            reports.append(
                BackfillRunReport(
                    class_id=cid,
                    course_code="",
                    run_key=needle,
                    live_module="",
                    live_slot="",
                    action="error",
                    detail=detail,
                )
            )
            continue
        reports.extend(_repair_one_run_row(school, row, apply=apply))
    return RepairReport(dry_run=not apply, runs=reports)


def _repair_one_live_session(
    school: Any,
    session_id: int,
    *,
    apply: bool,
) -> list[BackfillRunReport]:
    """Classify and fix catch-up state for a single live session only."""
    row = school.get_live_session_catchup_repair_row(session_id)
    if row is None:
        return [
            BackfillRunReport(
                class_id=0,
                course_code="",
                run_key="",
                live_module="",
                live_slot="",
                action="skip",
                detail=f"unknown session id={session_id}",
                session_id=session_id,
            )
        ]
    return _repair_one_run_row(school, row, apply=apply)


def _repair_one_run_row(
    school: Any,
    row: dict[str, Any],
    *,
    apply: bool,
) -> list[BackfillRunReport]:
    """Classify and fix catch-up state for one snapshot run."""
    from catchup_schedule import CatchupScheduleError

    out: list[BackfillRunReport] = []
    session_id = row.get("session_id")
    cid = int(row["class_id"])
    key = str(row["run_key"])
    mod = str(row["live_module"])
    sl = str(row["live_slot"])
    course = str(row["course_code"])
    ended_at = str(row.get("ended_at") or "")
    offered = int(row.get("offered_count") or 0)
    base = BackfillRunReport(
        class_id=cid,
        course_code=course,
        run_key=key,
        live_module=mod,
        live_slot=sl,
        session_id=session_id,
        action="skip",
    )
    try:
        if apply:
            is_real = school.classify_participation_run(cid, key, ended_at=ended_at)
        else:
            is_real = school.preview_participation_run_real(
                cid, ended_at=ended_at, run_key=key
            )
    except CatchupScheduleError as exc:
        out.append(
            BackfillRunReport(
                class_id=cid,
                course_code=course,
                run_key=key,
                live_module=mod,
                live_slot=sl,
                session_id=session_id,
                action="error",
                detail=str(exc),
            )
        )
        return out
    kind_action = "classify real" if is_real else "classify test"
    if not apply:
        kind_action = f"would {kind_action}"
    out.append(
        BackfillRunReport(
            class_id=cid,
            course_code=course,
            run_key=key,
            live_module=mod,
            live_slot=sl,
            session_id=session_id,
            action=kind_action,
        )
    )
    if is_real:
        if offered > 0:
            if apply:
                written = school.write_participation_from_snapshots(cid, key)
                base.items = int(written.get("items") or 0)
                base.answers = int(written.get("answers") or 0)
                base.action = "wrote participation"
            else:
                base.items = offered
                base.action = "would write participation"
            out.append(base)
        link_row = dict(row)
        link_row["session_id"] = session_id
        link_report = _plan_real_run_link(school, link_row, apply=apply)
        if link_report is not None:
            out.append(link_report)
        return out
    base.action = "skip participation"
    base.detail = "test run"
    if not apply:
        base.action = "would skip participation"
    out.append(base)
    for link in (row.get("run_link"), row.get("slot_link")):
        if not link:
            continue
        if str(link.get("run_key") or "") != key:
            continue
        lid = int(link["id"])
        action = "discard link"
        if not apply:
            action = f"would {action}"
        out.append(
            BackfillRunReport(
                class_id=cid,
                course_code=course,
                run_key=key,
                live_module=mod,
                live_slot=sl,
                session_id=session_id,
                action=action,
                detail=f"link_id={lid}",
            )
        )
        if apply:
            school.discard_catchup_link(lid)
        break
    return out


def sync_catchup_scope(
    school: Any,
    *,
    course: str | None = None,
    module: str | None = None,
    slot: str | None = None,
    apply: bool = False,
    include_legacy_without_snapshots: bool = False,
) -> tuple[list[BackfillRunReport], list[BackfillRunReport]]:
    """Classify runs and align shared catch-up links for one scope (backfill)."""
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
    reports.sort(
        key=lambda r: _run_sort_key(
            {
                "course_code": r.course_code,
                "live_module": r.live_module,
                "live_slot": r.live_slot,
                "run_key": r.run_key,
                "ended_at": "",
            }
        )
    )
    return reports, skipped


def _sync_one_lesson_slot(
    school: Any,
    slot_rows: list[dict[str, Any]],
    *,
    apply: bool,
) -> list[BackfillRunReport]:
    """Classify every run in a slot; shared link only on latest real run."""
    out: list[BackfillRunReport] = []
    classified: list[dict[str, Any]] = []
    for row in slot_rows:
        class_id = int(row["class_id"])
        run_key = str(row["run_key"])
        offered = int(row.get("offered_count") or 0)
        ended_at = str(row.get("ended_at") or "")
        if apply:
            is_real = school.classify_participation_run(class_id, run_key, ended_at=ended_at)
        else:
            is_real = school.preview_participation_run_real(
                class_id, ended_at=ended_at, run_key=run_key
            )
        row["is_real"] = is_real
        row["ended_at"] = ended_at
        kind_action = "classify real" if is_real else "classify test"
        if not apply:
            kind_action = f"would {kind_action}"
        out.append(
            BackfillRunReport(
                class_id=class_id,
                course_code=str(row.get("course_code") or ""),
                run_key=run_key,
                live_module=str(row.get("live_module") or ""),
                live_slot=str(row.get("live_slot") or ""),
                action=kind_action,
            )
        )
        if offered <= 0:
            out.append(
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
        if is_real:
            part = BackfillRunReport(
                class_id=class_id,
                course_code=str(row.get("course_code") or ""),
                run_key=run_key,
                live_module=str(row.get("live_module") or ""),
                live_slot=str(row.get("live_slot") or ""),
                action="wrote participation" if apply else "would write participation",
                items=offered,
            )
            if apply:
                written = school.write_participation_from_snapshots(class_id, run_key)
                part.items = int(written.get("items") or 0)
                part.answers = int(written.get("answers") or 0)
            out.append(part)
        else:
            out.append(
                BackfillRunReport(
                    class_id=class_id,
                    course_code=str(row.get("course_code") or ""),
                    run_key=run_key,
                    live_module=str(row.get("live_module") or ""),
                    live_slot=str(row.get("live_slot") or ""),
                    action="skip participation" if apply else "would skip participation",
                    detail="test run",
                )
            )
        classified.append(row)

    real_runs = [r for r in classified if r.get("is_real")]
    real_runs.sort(key=lambda r: str(r.get("ended_at") or ""), reverse=True)
    winner = real_runs[0] if real_runs else None
    if winner is None:
        return out
    slot_link = school.catchup_link_for_slot(
        int(winner["class_id"]),
        str(winner.get("live_module") or ""),
        str(winner.get("live_slot") or ""),
    )
    winner_row = dict(winner)
    winner_row["slot_link"] = slot_link
    link_report = _plan_real_run_link(school, winner_row, apply=apply)
    if link_report is not None:
        out.append(link_report)
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
    session_ids: list[int] | None = None,
    run_targets: list[tuple[int, str]] | None = None,
    apply: bool = False,
) -> RepairReport:
    """Re-classify and fix catch-up links for sessions or snapshot run keys."""
    if run_targets:
        return repair_catchup_run_keys(school, run_targets, apply=apply)
    return repair_catchup_sessions(school, list(session_ids or []), apply=apply)


def new_catchup_token() -> str:
    """Proxy for tests that patch token generation."""
    return new_token()

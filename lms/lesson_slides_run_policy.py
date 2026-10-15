"""MCK-229: Lesson Slides run eligibility (schedule, students, timestamps)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from live_class_run import (
    LiveClassSchedule,
    classify_run_start,
    load_real_class_start_window,
    parse_ended_at,
    schedule_from_row,
)
from participation_real_students import (
    exclude_participation_answer_student_ids,
    run_has_real_student_answer,
)


class _RunPolicySchool(Protocol):
    """Minimal ``SchoolDB`` surface for run policy."""

    conn: Any

    def live_class_schedule_for_class(self, class_id: int) -> dict[str, str] | None: ...

    def test_student_ids(self, class_id: int) -> list[int]: ...


@dataclass(frozen=True)
class RunEndInstant:
    """Parsed end time for ranking and display."""

    value: datetime
    inferred: bool
    iso: str


def schedule_for_class(school: _RunPolicySchool, class_id: int) -> LiveClassSchedule | None:
    """Build a schedule including start time from offering/course data."""
    row = school.live_class_schedule_for_class(int(class_id))
    if not row:
        return None
    return schedule_from_row(
        live_days=str(row.get("live_days") or ""),
        live_end_time=str(row.get("live_end_time") or ""),
        live_time=str(row.get("live_time") or ""),
    )


def participation_run_row(
    school: _RunPolicySchool, class_id: int, run_key: str
) -> dict[str, Any] | None:
    """Fetch one ``participation_runs`` row."""
    row = school.conn.execute(
        """
        SELECT is_real, auto_is_real, discarded, ended_at
        FROM participation_runs
        WHERE class_id = ? AND run_key = ?
        """,
        (int(class_id), str(run_key or "").strip()),
    ).fetchone()
    return dict(row) if row is not None else None


def run_start_for_schedule_window(
    school: _RunPolicySchool, class_id: int, run_key: str
) -> datetime | None:
    """Best-effort run start for scheduled real/test window checks.

    Prefers ``live_class_sessions.started_at``. When that is missing, falls back
    to the earliest ``participation_items.offered_at``, which can misclassify a
    run whose first question was offered late relative to the real class start.
    """
    cid = int(class_id)
    key = str(run_key or "").strip()
    if not key:
        return None
    sess = school.conn.execute(
        """
        SELECT MIN(started_at) AS started_at
        FROM live_class_sessions
        WHERE class_id = ? AND run_key = ?
        """,
        (cid, key),
    ).fetchone()
    started = str((dict(sess) if sess is not None else {}).get("started_at") or "").strip()
    if started:
        return parse_ended_at(started)
    offered = school.conn.execute(
        """
        SELECT MIN(offered_at) AS offered_at
        FROM participation_items
        WHERE class_id = ? AND run_key = ?
        """,
        (cid, key),
    ).fetchone()
    off = str((dict(offered) if offered is not None else {}).get("offered_at") or "").strip()
    if off:
        return parse_ended_at(off)
    return None


def teacher_overrode_run_kind(row: dict[str, Any] | None) -> bool:
    """True when a teacher toggle diverged from auto classification (MCK-207)."""
    if row is None:
        return False
    return int(row.get("is_real") or 0) != int(row.get("auto_is_real") or 0)


def run_end_instant_for_ranking(
    school: _RunPolicySchool, class_id: int, run_key: str
) -> RunEndInstant | None:
    """Best-effort end instant for ordering (may be inferred, never written to ``ended_at``)."""
    cid = int(class_id)
    key = str(run_key or "").strip()
    if not key:
        return None
    row = participation_run_row(school, cid, key)
    stored = str((row or {}).get("ended_at") or "").strip()
    if stored:
        dt = parse_ended_at(stored)
        return RunEndInstant(value=dt, inferred=False, iso=stored)
    snap = school.conn.execute(
        """
        SELECT MAX(snapshot_at) AS snapshot_at
        FROM live_result_snapshots
        WHERE class_id = ? AND run_key = ?
        """,
        (cid, key),
    ).fetchone()
    snap_at = str((dict(snap) if snap is not None else {}).get("snapshot_at") or "").strip()
    if snap_at:
        dt = parse_ended_at(snap_at)
        return RunEndInstant(value=dt, inferred=True, iso=snap_at)
    offered = school.conn.execute(
        """
        SELECT MAX(offered_at) AS offered_at
        FROM participation_items
        WHERE class_id = ? AND run_key = ?
        """,
        (cid, key),
    ).fetchone()
    off = str((dict(offered) if offered is not None else {}).get("offered_at") or "").strip()
    if off:
        dt = parse_ended_at(off)
        return RunEndInstant(value=dt, inferred=True, iso=off)
    return None


def effective_run_is_real(
    school: _RunPolicySchool,
    class_id: int,
    run_key: str,
    *,
    end_instant: RunEndInstant | None = None,
) -> bool:
    """Whether a run counts as a real class for Lesson Slides (not test/discarded).

    Schedule-based recomputation never overrides a teacher manual real/test toggle
    (``is_real`` diverged from ``auto_is_real``) or a discarded run.
    """
    row = participation_run_row(school, class_id, run_key)
    if row is not None and int(row.get("discarded") or 0):
        return False
    if teacher_overrode_run_kind(row):
        return bool(int(row.get("is_real") or 0))
    if row is not None:
        stored_real = int(row.get("is_real") or 0)
        auto_real = int(row.get("auto_is_real") or 0)
        # MCK-207 capture wrote matching flags from end-of-run classification.
        if stored_real == auto_real and stored_real == 1:
            return True
        if stored_real == auto_real and stored_real == 0:
            pass  # unset or test — recompute below
        elif stored_real == auto_real:
            return bool(stored_real)
    start_dt = run_start_for_schedule_window(school, class_id, run_key)
    if start_dt is None:
        instant = end_instant or run_end_instant_for_ranking(school, class_id, run_key)
        start_dt = instant.value if instant is not None else None
    if start_dt is None:
        return bool(int((row or {}).get("is_real") or 0))
    sched = schedule_for_class(school, class_id)
    if sched is None or not str(sched.start_time_label or "").strip():
        return bool(int((row or {}).get("is_real") or 0))
    return classify_run_start(
        start_dt,
        sched,
        window=load_real_class_start_window(),
    )


def run_qualifies_for_lesson_slides_latest(
    school: _RunPolicySchool, class_id: int, run_key: str
) -> tuple[RunEndInstant | None, bool]:
    """Return ranking instant and whether the run may backfill ``lesson_slides_latest``."""
    instant = run_end_instant_for_ranking(school, class_id, run_key)
    if instant is None:
        return None, False
    if not effective_run_is_real(school, class_id, run_key, end_instant=instant):
        return instant, False
    excluded = exclude_participation_answer_student_ids(class_id, school)
    if not run_has_real_student_answer(
        school, class_id, run_key, excluded_student_ids=excluded
    ):
        return instant, False
    return instant, True


def compare_run_end_instant(a: RunEndInstant, b: RunEndInstant) -> int:
    """Compare two instants for latest-run selection (newer wins)."""
    if a.value != b.value:
        return 1 if a.value > b.value else -1
    if a.inferred != b.inferred:
        return -1 if a.inferred else 1
    return 0


def display_ended_at_from_latest(latest: dict[str, Any] | None) -> str:
    """ISO timestamp for UI (uses inferred column when flagged)."""
    if not latest:
        return ""
    if int(latest.get("ended_at_inferred") or 0):
        return str(latest.get("inferred_ended_at") or "").strip()
    return str(latest.get("ended_at") or "").strip()

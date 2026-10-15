"""MCK-229: backfill ``lesson_slides_latest`` from historical real runs."""

from __future__ import annotations

from typing import Any, Protocol

from lesson_slides_run_policy import (
    RunEndInstant,
    compare_run_end_instant,
    display_ended_at_from_latest,
    run_qualifies_for_lesson_slides_latest,
)


class _LessonSlidesBackfillSchool(Protocol):
    """Minimal ``SchoolDB`` surface used by the backfill."""

    conn: Any
    _lock: Any

    def _upsert_lesson_slides_latest_unlocked(
        self,
        session_id: int | None,
        *,
        class_id: int,
        run_key: str,
        module: str,
        slot: str,
        ended_at: str,
        session_code: str,
        ended_at_inferred: bool = False,
        inferred_ended_at: str = "",
    ) -> None: ...


_RUN_SLOTS_SQL = """
SELECT DISTINCT
    class_id,
    run_key,
    UPPER(live_module) AS live_module,
    UPPER(live_slot) AS live_slot
FROM participation_items
WHERE TRIM(COALESCE(live_module, '')) <> ''
  AND TRIM(COALESCE(live_slot, '')) <> ''
ORDER BY class_id, live_module, live_slot, run_key
"""


def _existing_display_instant(
    school: _LessonSlidesBackfillSchool, class_id: int, module: str, slot: str
) -> RunEndInstant | None:
    """Read stored latest row as a ranking instant."""
    from lesson_slides_run_policy import run_end_instant_for_ranking

    row = school.conn.execute(
        """
        SELECT run_key, ended_at, ended_at_inferred, inferred_ended_at
        FROM lesson_slides_latest
        WHERE class_id = ? AND live_module = ? AND live_slot = ?
        """,
        (int(class_id), str(module), str(slot)),
    ).fetchone()
    if row is None:
        return None
    latest = dict(row)
    iso = display_ended_at_from_latest(latest)
    if not iso:
        return run_end_instant_for_ranking(school, int(class_id), str(latest.get("run_key") or ""))
    from live_class_run import parse_ended_at

    dt = parse_ended_at(iso)
    return RunEndInstant(
        value=dt,
        inferred=bool(int(latest.get("ended_at_inferred") or 0)),
        iso=iso,
    )


def _candidate_is_newer(candidate: RunEndInstant, existing: RunEndInstant | None) -> bool:
    """True when ``candidate`` should replace ``existing``."""
    if existing is None:
        return True
    return compare_run_end_instant(candidate, existing) > 0


def backfill_lesson_slides_latest(school: _LessonSlidesBackfillSchool) -> dict[str, int]:
    """Fill ``lesson_slides_latest`` from newest eligible real runs per slot.

    Idempotent: ranks by parsed datetimes; never writes guessed times into
    ``participation_runs.ended_at``; stores inferred end times separately.
    """
    inserted = 0
    updated = 0
    skipped = 0
    with school._lock:
        rows = school.conn.execute(_RUN_SLOTS_SQL).fetchall()
        by_slot: dict[tuple[int, str, str], list[str]] = {}
        for row in rows:
            key = (int(row["class_id"]), str(row["live_module"]), str(row["live_slot"]))
            by_slot.setdefault(key, []).append(str(row["run_key"] or ""))
        for (cid, mod, sl), run_keys in by_slot.items():
            best_instant: RunEndInstant | None = None
            best_key = ""
            for run_key in run_keys:
                instant, ok = run_qualifies_for_lesson_slides_latest(school, cid, run_key)
                if not ok or instant is None:
                    continue
                if best_instant is None or compare_run_end_instant(instant, best_instant) > 0:
                    best_instant = instant
                    best_key = run_key
            if not best_key or best_instant is None:
                continue
            existing_instant = _existing_display_instant(school, cid, mod, sl)
            if not _candidate_is_newer(best_instant, existing_instant):
                skipped += 1
                continue
            had_row = existing_instant is not None
            if best_instant.inferred:
                school._upsert_lesson_slides_latest_unlocked(
                    None,
                    class_id=cid,
                    run_key=best_key,
                    module=mod,
                    slot=sl,
                    ended_at="",
                    session_code="",
                    ended_at_inferred=True,
                    inferred_ended_at=best_instant.iso,
                )
            else:
                school._upsert_lesson_slides_latest_unlocked(
                    None,
                    class_id=cid,
                    run_key=best_key,
                    module=mod,
                    slot=sl,
                    ended_at=best_instant.iso,
                    session_code="",
                    ended_at_inferred=False,
                    inferred_ended_at="",
                )
            if had_row:
                updated += 1
            else:
                inserted += 1
    return {"inserted": inserted, "updated": updated, "skipped": skipped}

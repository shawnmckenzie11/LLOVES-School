"""MCK-229: backfill ``lesson_slides_latest`` from historical real runs."""

from __future__ import annotations

from typing import Any, Protocol


class _LessonSlidesBackfillSchool(Protocol):
    """Minimal ``SchoolDB`` surface used by the backfill."""

    conn: Any
    _lock: Any

    def _upsert_lesson_slides_latest_unlocked(
        self,
        session_id: int,
        *,
        class_id: int,
        run_key: str,
        module: str,
        slot: str,
        ended_at: str,
        session_code: str,
    ) -> None: ...


_BACKFILL_CANDIDATES_SQL = """
WITH run_slots AS (
    SELECT class_id, run_key, UPPER(live_module) AS live_module, UPPER(live_slot) AS live_slot
    FROM participation_items
    WHERE TRIM(COALESCE(live_module, '')) <> '' AND TRIM(COALESCE(live_slot, '')) <> ''
    UNION
    SELECT class_id, run_key, UPPER(live_module), UPPER(live_slot)
    FROM live_result_snapshots
    WHERE TRIM(COALESCE(live_module, '')) <> '' AND TRIM(COALESCE(live_slot, '')) <> ''
    UNION
    SELECT class_id, run_key, UPPER(live_module), UPPER(live_slot)
    FROM catchup_links
    WHERE TRIM(COALESCE(live_module, '')) <> '' AND TRIM(COALESCE(live_slot, '')) <> ''
),
ranked AS (
    SELECT
        rs.class_id,
        rs.live_module,
        rs.live_slot,
        rs.run_key,
        lcs.id AS session_id,
        lcs.session_code,
        lcs.ended_at,
        ROW_NUMBER() OVER (
            PARTITION BY rs.class_id, rs.live_module, rs.live_slot
            ORDER BY lcs.ended_at DESC, lcs.id DESC
        ) AS rn
    FROM run_slots rs
    INNER JOIN participation_runs pr
        ON pr.class_id = rs.class_id AND pr.run_key = rs.run_key
    INNER JOIN live_class_sessions lcs
        ON lcs.class_id = rs.class_id AND lcs.run_key = rs.run_key
    WHERE pr.is_real = 1
      AND pr.discarded = 0
      AND lcs.ended_at IS NOT NULL
      AND TRIM(lcs.ended_at) <> ''
)
SELECT class_id, live_module, live_slot, run_key, session_id, session_code, ended_at
FROM ranked
WHERE rn = 1
ORDER BY class_id, live_module, live_slot
"""


def _ended_at_newer(candidate: str, existing: str) -> bool:
    """Return True when ``candidate`` should replace ``existing`` (ISO timestamps)."""
    cand = str(candidate or "").strip()
    prev = str(existing or "").strip()
    if not cand:
        return False
    if not prev:
        return True
    return cand > prev


def backfill_lesson_slides_latest(school: _LessonSlidesBackfillSchool) -> dict[str, int]:
    """Fill ``lesson_slides_latest`` from newest real, non-discarded runs per slot.

    Idempotent: safe to call repeatedly; never replaces a row with an older ``ended_at``.
    Skips test runs (``is_real=0``) and discarded runs.
    """
    inserted = 0
    updated = 0
    skipped = 0
    with school._lock:
        rows = school.conn.execute(_BACKFILL_CANDIDATES_SQL).fetchall()
        for row in rows:
            cid = int(row["class_id"])
            mod = str(row["live_module"] or "").upper()
            sl = str(row["live_slot"] or "").upper()
            ended_at = str(row["ended_at"] or "")
            existing = school.conn.execute(
                """
                SELECT ended_at FROM lesson_slides_latest
                WHERE class_id = ? AND live_module = ? AND live_slot = ?
                """,
                (cid, mod, sl),
            ).fetchone()
            if existing is not None and not _ended_at_newer(ended_at, str(existing["ended_at"] or "")):
                skipped += 1
                continue
            had_row = existing is not None
            school._upsert_lesson_slides_latest_unlocked(
                int(row["session_id"]),
                class_id=cid,
                run_key=str(row["run_key"] or ""),
                module=mod,
                slot=sl,
                ended_at=ended_at,
                session_code=str(row["session_code"] or ""),
            )
            if had_row:
                updated += 1
            else:
                inserted += 1
    return {"inserted": inserted, "updated": updated, "skipped": skipped}

"""MCK-542: recompute End Class attendance and participation credit columns.

Between MCK-311 (Oct 21) and MCK-541, End Class marked present every student
who had joined *any* live session of the class, and credited participation
answers from every session. This module plans (and optionally writes) the
correction for each past End Class column, using the same session-scoped
lookup End Class uses today (``present_student_ids_for_live_class`` with a
``session_id``).

Rules:

* A column maps to its live session through ``live_class_sessions.mgs_session_id``.
  Test sessions, catch-up (``~cu``) runs, unmapped sessions, and columns that
  more than one session maps to are skipped.
* Attendance is only ever corrected present -> absent, and only for students
  the old union rule would have marked (they attended some other session of
  the class but not this one). A student the column shows present who is not
  explained by the bug is left alone. A student who attended this session
  but shows absent cannot come from the bug, so it is a manual edit: that
  whole column's attendance is skipped.
* End Class deletes a session's answers, so participation credit cannot be
  recomputed afterwards. The one provable case is lowered to 0: a student
  flipped to absent never joined this session, so their credit for the run
  came from another session. It is lowered through the run's credit ledger
  (``live_participation_credits``), so manual points stay. All other
  credits are only counted (``credits_unverifiable``).
* A session with no attendee rows at all (cleanup can delete them) is skipped:
  who joined is unknown, so nothing may be flipped.
* Nothing is deleted. Every write is logged in ``mck542_recompute_log`` in the
  same transaction so ``revert_run`` can undo it.

Output carries counts only: never names, ids, codes, or emails of students.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

DEFAULT_SINCE = "2026-10-21"
LOG_TABLE = "mck542_recompute_log"

_LOG_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {LOG_TABLE} (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    class_id INTEGER NOT NULL,
    column_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    run_key TEXT,
    before_present INTEGER,
    after_present INTEGER,
    before_ledger TEXT,
    after_ledger TEXT,
    before_scores TEXT,
    after_scores TEXT,
    reverted_at TEXT
);
"""


@dataclass
class ColumnPlan:
    """What the recompute found for one End Class column.

    Attributes:
        class_id: Game-show class id.
        column_id: ``sessions.id`` of the gradebook column.
        live_session_id: ``live_class_sessions.id`` that wrote it.
        run_key: Run key of that live session.
        date: Column date (``YYYY-MM-DD``).
        skip: Why the whole column was skipped, or ``""``.
        to_absent: Student ids to flip present -> absent.
        conflicts: Students who attended but show absent (manual edit).
        unexplained: Students shown present that the bug does not explain.
        credits: ``(student_id, ledger_points_before, points_after)`` rows.
        credits_unverifiable: Credits of students who did attend. The answers
            are deleted at End, so these cannot be checked and are left alone.
    """

    class_id: int
    column_id: int
    live_session_id: int
    run_key: str
    date: str
    skip: str = ""
    to_absent: list[int] = field(default_factory=list)
    conflicts: int = 0
    unexplained: int = 0
    credits: list[tuple[int, float, int]] = field(default_factory=list)
    credits_unverifiable: int = 0

    @property
    def attendance_changes(self) -> bool:
        """True when attendance would be written for this column."""
        return not self.skip and not self.conflicts and bool(self.to_absent)

    @property
    def would_change(self) -> bool:
        """True when this column would be written at all."""
        return not self.skip and (self.attendance_changes or bool(self.credits))


def _is_catchup(run_key: str | None) -> bool:
    """True for a catch-up run key."""
    return "~cu" in str(run_key or "")


def plan_recompute(school: Any, since: str = DEFAULT_SINCE) -> list[ColumnPlan]:
    """Compare each stored End Class column with its own session. Read only.

    Args:
        school: Open ``SchoolDB``.
        since: First day (``YYYY-MM-DD``) of live sessions to scan.

    Returns:
        One plan per mapped live session started on or after ``since``.
    """
    from participation_real_students import exclude_participation_answer_student_ids

    game = school.game
    with school._lock:
        live_rows = [
            dict(r)
            for r in school.conn.execute(
                """
                SELECT id, class_id, run_key, mgs_session_id,
                       COALESCE(is_test, 0) AS is_test, started_at
                FROM live_class_sessions
                WHERE status != 'active'
                ORDER BY class_id, id
                """
            ).fetchall()
        ]
    by_column: dict[int, list[dict[str, Any]]] = {}
    for row in live_rows:
        if row["mgs_session_id"] not in (None, ""):
            by_column.setdefault(int(row["mgs_session_id"]), []).append(row)
    plans: list[ColumnPlan] = []
    for row in live_rows:
        run_key = str(row["run_key"] or "").strip()
        if str(row["started_at"] or "")[:10] < since:
            continue
        if int(row["is_test"]) or _is_catchup(run_key):
            continue
        if row["mgs_session_id"] in (None, ""):
            continue
        cid, live_id, column = int(row["class_id"]), int(row["id"]), int(row["mgs_session_id"])
        with game._lock:
            col = game.conn.execute(
                "SELECT starts_at FROM sessions WHERE id = ? AND class_id = ?",
                (column, cid),
            ).fetchone()
        plan = ColumnPlan(
            cid, column, live_id, run_key,
            str(col["starts_at"] or "")[:10] if col else "",
        )
        plans.append(plan)
        if col is None:
            plan.skip = "missing_column"
            continue
        if len(by_column[column]) > 1:
            plan.skip = "ambiguous_column"
            continue
        with school._lock:
            attendee_rows = int(
                school.conn.execute(
                    "SELECT COUNT(*) FROM live_session_attendees "
                    "WHERE live_session_id = ?",
                    (live_id,),
                ).fetchone()[0]
            )
        if attendee_rows == 0:
            # Cleanup (discard, wipe, metrics purge, slides restore) can delete
            # every attendee row. Then "who joined" is unknown, not empty.
            plan.skip = "no_attendee_rows"
            continue
        excluded = exclude_participation_answer_student_ids(cid, school)
        correct = {
            int(s)
            for s in school.present_student_ids_for_live_class(cid, session_id=live_id)
        }
        with game._lock:
            roster = {
                int(r["id"])
                for r in game.conn.execute(
                    "SELECT id FROM students WHERE class_id = ? "
                    "AND COALESCE(is_test_student, 0) = 0",
                    (cid,),
                )
            }
            stored = {
                int(r["student_id"])
                for r in game.conn.execute(
                    "SELECT student_id FROM session_scores "
                    "WHERE session_id = ? AND present = 1",
                    (column,),
                )
            } & roster
        correct &= roster
        # What the old rule marked: everyone who joined any session of the
        # class that existed at that End (ids up to this one).
        with school._lock:
            old_union = {
                int(r["student_id"])
                for r in school.conn.execute(
                    """
                    SELECT DISTINCT a.student_id
                    FROM live_session_attendees a
                    JOIN live_class_sessions s ON s.id = a.live_session_id
                    WHERE s.class_id = ? AND s.id <= ? AND a.student_id IS NOT NULL
                    """,
                    (cid, live_id),
                )
            } - excluded
        plan.conflicts = len(correct - stored)
        plan.to_absent = sorted((stored & old_union) - correct)
        plan.unexplained = len(stored - old_union - correct)
        # End Class deletes a session's answers, so a past credit cannot be
        # recomputed. A student who never joined this session cannot have
        # answered in it, so that student's credit for this run is bogus
        # (it came from another session). Every other credit is unverifiable.
        key = run_key or f"session:{column}"
        gone = set(plan.to_absent)
        with game._lock:
            ledger = game.conn.execute(
                "SELECT student_id, points FROM live_participation_credits "
                "WHERE run_key = ? AND session_id = ? AND points > 0",
                (key, column),
            ).fetchall()
        for lrow in ledger:
            sid = int(lrow["student_id"])
            if sid in gone and not plan.conflicts:
                plan.credits.append((sid, float(lrow["points"]), 0))
            elif sid not in gone:
                plan.credits_unverifiable += 1
    return plans


def format_report(plans: list[ColumnPlan], *, applied: bool) -> list[str]:
    """Counts-only report: per class and date, then totals.

    Args:
        plans: Output of :func:`plan_recompute`.
        applied: True after a write, false for a dry run.

    Returns:
        Lines to print. No student identifiers.
    """
    lines = []
    for p in sorted(plans, key=lambda x: (x.class_id, x.date, x.column_id)):
        if p.skip:
            lines.append(f"class {p.class_id} {p.date}: skipped ({p.skip})")
            continue
        att = "skipped (manual edit suspected)" if p.conflicts else "ok"
        lines.append(
            f"class {p.class_id} {p.date}: present->absent={0 if p.conflicts else len(p.to_absent)} "
            f"absent->present_needed={p.conflicts} unexplained_kept={p.unexplained} "
            f"credit_students_lowered={len(p.credits)} "
            f"credits_unverifiable={p.credits_unverifiable} attendance={att}"
        )
    live = [p for p in plans if not p.skip]
    skipped = {}
    for p in plans:
        if p.skip:
            skipped[p.skip] = skipped.get(p.skip, 0) + 1
    lines.append(
        "summary: "
        f"columns_scanned={len(plans)} "
        f"columns_would_change={sum(1 for p in plans if p.would_change)} "
        f"students_present_to_absent={sum(len(p.to_absent) for p in live if not p.conflicts)} "
        f"students_absent_to_present={sum(p.conflicts for p in live)}(never written; column skipped) "
        f"columns_skipped_manual_edit={sum(1 for p in live if p.conflicts)} "
        f"unexplained_present_kept={sum(p.unexplained for p in live)} "
        f"credit_students_lowered={sum(len(p.credits) for p in live)} "
        f"credits_unverifiable_left_alone={sum(p.credits_unverifiable for p in live)} "
        f"columns_skipped_no_attendees={skipped.get('no_attendee_rows', 0)} "
        f"columns_skipped_other={sum(v for k, v in skipped.items() if k != 'no_attendee_rows')} "
        f"{'APPLIED' if applied else 'DRY RUN, nothing written'}"
    )
    lines.append(
        "warning: a teacher's manual 'present' for a student who attended in person "
        "without joining, but who joined on another day, cannot be detected and "
        "would be flipped to absent."
    )
    return lines


def _scores_json(game: Any, column: int, sid: int) -> str:
    """Current score cells of one student in one column, as JSON text."""
    import json

    row = game.conn.execute(
        "SELECT points, points_r1, points_r2, points_r3 FROM session_scores "
        "WHERE session_id = ? AND student_id = ?",
        (column, sid),
    ).fetchone()
    return json.dumps(list(row) if row else None)


def apply_recompute(school: Any, plans: list[ColumnPlan]) -> str:
    """Write the plan in one transaction and log every change.

    Re-checks each flip inside the transaction, so a student whose flag
    changed since the plan is not touched. Idempotent: a repeat finds
    nothing to change.

    Args:
        school: Open ``SchoolDB``.
        plans: Output of :func:`plan_recompute`.

    Returns:
        The run id for :func:`revert_run`.
    """
    import json

    game = school.game
    run_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with game._lock, game._write_txn():
        game.conn.execute(_LOG_SCHEMA)
        for p in plans:
            if not p.would_change:
                continue
            if p.attendance_changes:
                for sid in p.to_absent:
                    cur = game.conn.execute(
                        "UPDATE session_scores SET present = 0 "
                        "WHERE session_id = ? AND student_id = ? AND present = 1",
                        (p.column_id, sid),
                    )
                    if cur.rowcount == 1:
                        game.conn.execute(
                            f"INSERT INTO {LOG_TABLE} (run_id, applied_at, kind, class_id, "
                            "column_id, student_id, run_key, before_present, after_present) "
                            "VALUES (?, ?, 'attendance', ?, ?, ?, ?, 1, 0)",
                            (run_id, now, p.class_id, p.column_id, sid, p.run_key),
                        )
            key = p.run_key or f"session:{p.column_id}"
            for sid, before, after in p.credits:
                ledger = game.conn.execute(
                    "SELECT points, points_r1, points_r2, points_r3 "
                    "FROM live_participation_credits WHERE run_key = ? AND student_id = ?",
                    (key, sid),
                ).fetchone()
                if ledger is None or float(ledger["points"]) != before:
                    continue  # changed since the plan
                scores_before = _scores_json(game, p.column_id, sid)
                game._add_live_credit_unlocked(p.column_id, sid, key, after)
                new_ledger = game.conn.execute(
                    "SELECT points, points_r1, points_r2, points_r3 "
                    "FROM live_participation_credits WHERE run_key = ? AND student_id = ?",
                    (key, sid),
                ).fetchone()
                game.conn.execute(
                    f"INSERT INTO {LOG_TABLE} (run_id, applied_at, kind, class_id, column_id, "
                    "student_id, run_key, before_ledger, after_ledger, before_scores, "
                    "after_scores) VALUES (?, ?, 'credit', ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id, now, p.class_id, p.column_id, sid, p.run_key,
                        json.dumps(list(ledger)), json.dumps(list(new_ledger)),
                        scores_before, _scores_json(game, p.column_id, sid),
                    ),
                )
    return run_id


def revert_run(school: Any, run_id: str) -> dict[str, int]:
    """Undo one :func:`apply_recompute` run from its log, in one transaction.

    Attendance goes back only where the flag still holds the value the run
    wrote. Credit points get the run's change added back, so later manual
    points stay.

    Args:
        school: Open ``SchoolDB``.
        run_id: Id returned by :func:`apply_recompute`.

    Returns:
        ``{"attendance": n, "credits": n}`` rows reverted.
    """
    import json

    game = school.game
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    done = {"attendance": 0, "credits": 0}
    with game._lock, game._write_txn():
        rows = game.conn.execute(
            f"SELECT * FROM {LOG_TABLE} WHERE run_id = ? AND reverted_at IS NULL "
            "ORDER BY id DESC",
            (run_id,),
        ).fetchall()
        for r in rows:
            if r["kind"] == "attendance":
                cur = game.conn.execute(
                    "UPDATE session_scores SET present = ? "
                    "WHERE session_id = ? AND student_id = ? AND present = ?",
                    (r["before_present"], r["column_id"], r["student_id"], r["after_present"]),
                )
                done["attendance"] += int(cur.rowcount or 0)
            else:
                before_s = json.loads(r["before_scores"])
                after_s = json.loads(r["after_scores"])
                if before_s and after_s:
                    d = [b - a for b, a in zip(before_s, after_s)]
                    game.conn.execute(
                        "UPDATE session_scores SET points = MAX(0, ROUND(points + ?, 1)), "
                        "points_r1 = MAX(0, ROUND(points_r1 + ?, 1)), "
                        "points_r2 = MAX(0, ROUND(points_r2 + ?, 1)), "
                        "points_r3 = MAX(0, ROUND(points_r3 + ?, 1)) "
                        "WHERE session_id = ? AND student_id = ?",
                        (*d, r["column_id"], r["student_id"]),
                    )
                b = json.loads(r["before_ledger"])
                game.conn.execute(
                    "UPDATE live_participation_credits SET points = ?, points_r1 = ?, "
                    "points_r2 = ?, points_r3 = ? WHERE run_key = ? AND student_id = ?",
                    (*b, r["run_key"] or f"session:{r['column_id']}", r["student_id"]),
                )
                done["credits"] += 1
            game.conn.execute(
                f"UPDATE {LOG_TABLE} SET reverted_at = ? WHERE id = ?", (now, r["id"])
            )
    return done

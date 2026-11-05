"""Explain and restore an ended live class missing from Lesson Slides.

Lesson Slides lists a live class only after a real End. Closing the Take
Attendance card, ``/game/cancel``, IT force-end, and a failed End save all
mark the session ended without a real participation run, so the class
drops off the list. The live session, its published items, and the MCK-45
result snapshots stay. This module reports how each session on one day
ended and, for one session the teacher confirms was a real class, writes
the run, participation from snapshots, catch-up link, and Lesson Slides
row. Quit deletes the session, so a Quit class cannot be restored.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from live_class_run import parse_ended_at


@dataclass
class SessionDiagnosis:
    """How one live session ended and whether Lesson Slides lists it."""

    session_id: int
    class_id: int
    course: str
    status: str
    is_test: bool
    run_key: str
    module: str
    slot: str
    started_at: str
    ended_at: str
    run_is_real: bool | None
    run_discarded: bool | None
    dismissed: bool
    shown: bool
    unsaved_answers: bool
    snapshot_answers: int
    cause: str
    restorable: bool

    def summary_line(self) -> str:
        """One stdout line. Join codes are never printed."""
        when = _toronto_clock(self.started_at)
        slot = f"{self.module} {self.slot}".strip() or "(no module/slot)"
        run = "none" if self.run_is_real is None else (
            "real" if self.run_is_real and not self.run_discarded else "not real"
        )
        return (
            f"session={self.session_id} class={self.class_id} {self.course} {slot} "
            f"started={when} status={self.status} test={'yes' if self.is_test else 'no'} "
            f"run={run} on_lesson_slides={'yes' if self.shown else 'no'} "
            f"snapshot_answers={self.snapshot_answers} — {self.cause}"
            + (" [restorable]" if self.restorable else "")
        )


def _toronto_clock(stamp: str) -> str:
    """Format a stored timestamp as Toronto local time.

    Args:
        stamp: Zone-less UTC or offset ISO text from ``live_class_sessions``.
    """
    text = str(stamp or "").strip()
    if not text:
        return "?"
    try:
        return parse_ended_at(text).strftime("%Y-%m-%d %H:%M %Z")
    except (TypeError, ValueError):
        return text


def _course_for_class(school: Any, class_id: int) -> str:
    """Ontario course code for one class, upper case, or ``""``.

    Args:
        school: ``SchoolDB`` instance.
        class_id: Game-show ``classes.id``.
    """
    try:
        cls = school.enrich_class(school.game.get_class(int(class_id)))
    except Exception:  # noqa: BLE001 - a deleted class still gets a report line
        return ""
    offering = None
    if cls.get("offering_id"):
        try:
            offering = school.get_offering(int(cls["offering_id"]))
        except Exception:  # noqa: BLE001
            offering = None
    return str((offering or {}).get("ontario_code") or cls.get("course_code") or "").upper()


def _session_slot(school: Any, row: dict[str, Any]) -> tuple[str, str]:
    """Module and slot from teacher state, else from the run's stored rows.

    Args:
        school: ``SchoolDB`` instance.
        row: One ``live_class_sessions`` row.
    """
    try:
        teacher = json.loads(row.get("teacher_state_json") or "{}")
    except (TypeError, ValueError):
        teacher = {}
    if not isinstance(teacher, dict):
        teacher = {}
    module = str(teacher.get("live_module") or "").strip().upper()
    slot = str(teacher.get("live_slot") or "").strip().upper()
    if module and slot:
        return module, slot
    key = str(row.get("run_key") or "").strip()
    if not key:
        return module, slot
    with school._lock:
        anchor = school._participation_run_anchor_unlocked(int(row["class_id"]), key)
    if anchor:
        return (
            str(anchor.get("live_module") or "").strip().upper(),
            str(anchor.get("live_slot") or "").strip().upper(),
        )
    return module, slot


def _shown_run_keys(school: Any, class_id: int) -> set[str]:
    """Run keys the staff Lesson Slides table lists for one class.

    Args:
        school: ``SchoolDB`` instance.
        class_id: Game-show ``classes.id``.
    """
    from lesson_slides_table import build_lesson_slides_table

    payload = build_lesson_slides_table(school, int(class_id))
    return {
        str(row.get("run_key") or "")
        for row in payload.get("rows") or []
        if row.get("has_run") and str(row.get("run_key") or "").strip()
    }


def _cause(
    *,
    status: str,
    is_test: bool,
    shown: bool,
    dismissed: bool,
    run_row: dict[str, Any] | None,
    unsaved_answers: bool,
    has_slot: bool,
) -> str:
    """Plain-language reason for one session's Lesson Slides state.

    Args:
        status: ``live_class_sessions.status``.
        is_test: Session started as a test class.
        shown: Lesson Slides already lists this run.
        dismissed: The teacher deleted this run from Lesson Slides.
        run_row: ``participation_runs`` flags, or ``None`` when missing.
        unsaved_answers: Live answers End never saved are still stored.
        has_slot: The session knows its module and slot.
    """
    if status != "ended":
        return "still open: the class was never ended"
    if shown:
        return "already listed on Lesson Slides"
    if dismissed:
        return "deleted from Lesson Slides with the ⋯ menu"
    if is_test:
        return "started as a Test class"
    if not has_slot:
        return "session has no module/slot, so Lesson Slides cannot place it"
    if run_row is None:
        if unsaved_answers:
            return "End started but its save failed; the live answers are still stored"
        return (
            "closed without End Class (Take Attendance card closed, game cancel, "
            "or IT force-end), so no real run was saved"
        )
    if int(run_row.get("discarded") or 0) or not int(run_row.get("is_real") or 0):
        return "saved as a test or discarded run"
    return "real run saved but not listed (unexpected)"


def diagnose_lesson_slides_day(
    school: Any,
    day: date,
    *,
    class_id: int | None = None,
    course: str | None = None,
) -> list[SessionDiagnosis]:
    """Report every live session that started on one Toronto calendar day.

    Args:
        school: ``SchoolDB`` instance.
        day: Toronto local date the class started.
        class_id: Limit to one game-show class.
        course: Limit to one Ontario course code, such as ``MCF3M``.

    Returns:
        One diagnosis per session, oldest first.
    """
    lo = (day - timedelta(days=1)).isoformat()
    hi = (day + timedelta(days=2)).isoformat()
    params: list[Any] = [lo, hi]
    where = "started_at >= ? AND started_at < ?"
    if class_id is not None:
        where += " AND class_id = ?"
        params.append(int(class_id))
    with school._lock:
        rows = [
            dict(r)
            for r in school.conn.execute(
                f"SELECT * FROM live_class_sessions WHERE {where} ORDER BY id",
                params,
            ).fetchall()
        ]
    want_course = str(course or "").strip().upper()
    courses: dict[int, str] = {}
    shown_by_class: dict[int, set[str]] = {}
    out: list[SessionDiagnosis] = []
    for row in rows:
        started = str(row.get("started_at") or "")
        try:
            if parse_ended_at(started).date() != day:
                continue
        except (TypeError, ValueError):
            continue
        cid = int(row["class_id"])
        if cid not in courses:
            courses[cid] = _course_for_class(school, cid)
        if want_course and courses[cid] != want_course:
            continue
        diagnosis = _diagnose_row(school, row, courses[cid], shown_by_class)
        out.append(diagnosis)
    return out


def _diagnose_row(
    school: Any,
    row: dict[str, Any],
    course: str,
    shown_by_class: dict[int, set[str]],
) -> SessionDiagnosis:
    """Build the diagnosis for one ``live_class_sessions`` row.

    Args:
        school: ``SchoolDB`` instance.
        row: Session row.
        course: Course code for the row's class.
        shown_by_class: Cache of listed run keys per class, filled in place.
    """
    cid = int(row["class_id"])
    sid = int(row["id"])
    key = str(row.get("run_key") or "").strip()
    status = str(row.get("status") or "")
    is_test = bool(int(row.get("is_test") or 0))
    module, slot = _session_slot(school, row)
    with school._lock:
        run = school.conn.execute(
            """
            SELECT is_real, discarded FROM participation_runs
            WHERE class_id = ? AND run_key = ?
            """,
            (cid, key),
        ).fetchone() if key else None
        snaps = school.conn.execute(
            """
            SELECT COUNT(*) FROM live_result_snapshots
            WHERE class_id = ? AND run_key = ? AND student_id IS NOT NULL
            """,
            (cid, key),
        ).fetchone() if key else None
    run_row = dict(run) if run is not None else None
    dismissed = bool(key) and (module, slot, 0, key) in school.lesson_slides_dismissed_keys(cid)
    if cid not in shown_by_class:
        shown_by_class[cid] = _shown_run_keys(school, cid)
    shown = bool(key) and key in shown_by_class[cid]
    unsaved = status == "ended" and school._unsaved_live_answers_remain(sid)
    cause = _cause(
        status=status,
        is_test=is_test,
        shown=shown,
        dismissed=dismissed,
        run_row=run_row,
        unsaved_answers=unsaved,
        has_slot=bool(module and slot),
    )
    restorable = (
        status == "ended"
        and not is_test
        and not shown
        and bool(key)
        and bool(module and slot)
    )
    return SessionDiagnosis(
        session_id=sid,
        class_id=cid,
        course=course,
        status=status,
        is_test=is_test,
        run_key=key,
        module=module,
        slot=slot,
        started_at=str(row.get("started_at") or ""),
        ended_at=str(row.get("ended_at") or ""),
        run_is_real=None if run_row is None else bool(int(run_row.get("is_real") or 0)),
        run_discarded=None if run_row is None else bool(int(run_row.get("discarded") or 0)),
        dismissed=dismissed,
        shown=shown,
        unsaved_answers=unsaved,
        snapshot_answers=int(snaps[0]) if snaps is not None else 0,
        cause=cause,
        restorable=restorable,
    )


def diagnose_session(school: Any, session_id: int) -> SessionDiagnosis | None:
    """Diagnose one session by id.

    Args:
        school: ``SchoolDB`` instance.
        session_id: ``live_class_sessions.id``.

    Returns:
        The diagnosis, or ``None`` when the session row is gone (Quit).
    """
    with school._lock:
        row = school.conn.execute(
            "SELECT * FROM live_class_sessions WHERE id = ?", (int(session_id),)
        ).fetchone()
    if row is None:
        return None
    data = dict(row)
    return _diagnose_row(school, data, _course_for_class(school, int(data["class_id"])), {})


def restore_lesson_slides_session(
    school: Any,
    session_id: int,
    *,
    apply: bool = False,
    user_id: int | None = None,
) -> list[str]:
    """Mark one ended session as a real class and put it back on Lesson Slides.

    Dry-run unless ``apply``. Steps, in order: retry the End save when live
    answers are still stored; clear a Lesson Slides deletion for this run;
    rebuild participation from result snapshots; record the run as real
    with the teacher flag so schedule checks never flip it back (this also
    restores the catch-up link when items exist); write the Lesson Slides
    row, which carries the slot's deck link. Each step is idempotent.

    Args:
        school: ``SchoolDB`` instance.
        session_id: ``live_class_sessions.id`` the teacher confirmed was real.
        apply: Write rows. Default prints the plan only.
        user_id: Recorded on the run kind change, when known.

    Returns:
        Lines describing each step, prefixed ``would`` on a dry run.

    Raises:
        ValueError: The session is missing, open, a test class, already
            listed, or has no module, slot, or run key.
    """
    diag = diagnose_session(school, int(session_id))
    if diag is None:
        raise ValueError(
            f"session {session_id} not found. Quit deletes the session, so nothing can be restored."
        )
    if not diag.restorable:
        raise ValueError(f"session {session_id} is not restorable: {diag.cause}")
    sid = diag.session_id
    cid = diag.class_id
    key = diag.run_key
    verb = "" if apply else "would "
    lines = [diag.summary_line()]
    if diag.unsaved_answers:
        lines.append(f"{verb}retry the End save from the stored live answers")
        if apply:
            school.cleanup_live_session_response_data(sid, record_participation=True)
    if diag.dismissed:
        lines.append(f"{verb}clear the Lesson Slides deletion for this run")
        if apply:
            with school._lock:
                school.conn.execute(
                    """
                    DELETE FROM lesson_slides_dismissed
                    WHERE class_id = ? AND live_module = ? AND live_slot = ?
                      AND COALESCE(is_test, 0) = 0 AND run_key = ?
                    """,
                    (cid, diag.module, diag.slot, key),
                )
                school.conn.commit()
    if diag.snapshot_answers:
        lines.append(
            f"{verb}rebuild participation from {diag.snapshot_answers} snapshot answer rows"
        )
        if apply:
            written = school.write_participation_from_snapshots(cid, key)
            lines.append(
                f"  items={int(written.get('items') or 0)} answers={int(written.get('answers') or 0)}"
            )
    lines.append(f"{verb}record the run as a real class (teacher-confirmed)")
    if apply:
        school.set_participation_run_real(cid, key, True, user_id=user_id)
        with school._lock:
            school.conn.execute(
                """
                UPDATE participation_runs
                SET ended_at = COALESCE(NULLIF(TRIM(ended_at), ''), ?)
                WHERE class_id = ? AND run_key = ?
                """,
                (diag.ended_at or None, cid, key),
            )
            school.conn.commit()
    lines.append(
        f"{verb}write the catch-up link (when questions were answered) and the "
        f"Lesson Slides row for {diag.module} {diag.slot}"
    )
    if apply:
        with school._lock:
            session = school.conn.execute(
                "SELECT session_code, ended_at FROM live_class_sessions WHERE id = ?",
                (sid,),
            ).fetchone()
            ended_at = str(session["ended_at"] or diag.ended_at or "")
            school._upsert_real_catchup_link_unlocked(
                cid,
                key,
                diag.module,
                diag.slot,
                now=datetime.now().replace(microsecond=0).isoformat(),
                live_session_id=sid,
                join_code=str(session["session_code"] or ""),
                ended_at=ended_at,
                refresh_window=True,
            )
            school._upsert_lesson_slides_latest_unlocked(
                sid,
                class_id=cid,
                run_key=key,
                module=diag.module,
                slot=diag.slot,
                ended_at=ended_at,
                session_code=str(session["session_code"] or ""),
            )
            school.conn.commit()
        after = diagnose_session(school, sid)
        lines.append(
            "on_lesson_slides=yes" if after is not None and after.shown else "on_lesson_slides=no"
        )
    return lines

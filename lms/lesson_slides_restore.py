"""Explain and restore an ended live class missing from Lesson Slides.

Lesson Slides lists a live class only after a real End. Closing the Take
Attendance card, ``/game/cancel``, IT force-end, and a failed End save all
mark the session ended without a real participation run, so the class
drops off the list. The live session, its published items, and the MCK-45
result snapshots stay. This module reports how each session on one day
ended and, for one session the teacher confirms was a real class, writes
the run, participation from snapshots, catch-up link, and Lesson Slides
row. Quit deletes the session, so a Quit class cannot be restored.

A class started under the wrong module/class at Set Class replaces that
slot's Lesson Slides row and catch-up link on End. ``move_lesson_slides_run``
moves that class to the right slot and rebuilds the slot it overwrote.
"""

from __future__ import annotations

import json
import re
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


_SLOT_TOKEN = re.compile(r"^\s*(M\d+)\s*[·.\-_/ ]?\s*(C\d+)\s*$", re.IGNORECASE)


def parse_slot_token(text: str) -> tuple[str, str]:
    """Split ``M4C3`` (or ``M4 C3``, ``M4·C3``) into module and slot.

    Args:
        text: Slot text from the command line.

    Raises:
        ValueError: The text is not one module plus one class slot.
    """
    match = _SLOT_TOKEN.match(str(text or ""))
    if not match:
        raise ValueError(f"{text!r} is not a class slot such as M4C3")
    return match.group(1).upper(), match.group(2).upper()


def _catalogue_slots(course: str) -> set[tuple[str, str]]:
    """Every live-class (``C``) slot the course registry offers.

    Args:
        course: Ontario course code.
    """
    from live_class_packs import live_class_registry

    registry = live_class_registry(course or "MCF3M")
    out: set[tuple[str, str]] = set()
    for module in registry.get("modules") or []:
        for slot in (registry.get("slots_by_module") or {}).get(module) or []:
            token = str(slot).strip().upper()
            if token.startswith("C"):
                out.add((str(module).strip().upper(), token))
    return out


def _slot_owner_keys(school: Any, class_id: int, module: str, slot: str) -> set[str]:
    """Real run keys that currently own one slot on Lesson Slides.

    Covers the stored row, the real catch-up link, and ended real runs the
    table falls back to.

    Args:
        school: ``SchoolDB`` instance.
        class_id: Game-show ``classes.id``.
        module: Module token such as ``M4``.
        slot: Class slot such as ``C3``.
    """
    from lesson_slides_table import _real_runs_outside_latest

    cid = int(class_id)
    keys: set[str] = set()
    with school._lock:
        for table in ("lesson_slides_latest", "catchup_links"):
            for row in school.conn.execute(
                f"""
                SELECT run_key FROM {table}
                WHERE class_id = ? AND live_module = ? AND live_slot = ?
                  AND COALESCE(is_test, 0) = 0
                """,
                (cid, module, slot),
            ).fetchall():
                if str(row["run_key"] or "").strip():
                    keys.add(str(row["run_key"]).strip())
    run = _real_runs_outside_latest(school, cid).get((module, slot))
    if run and str(run.get("run_key") or "").strip():
        keys.add(str(run["run_key"]).strip())
    return keys


def _previous_slot_run(
    school: Any, class_id: int, module: str, slot: str, *, skip_key: str
) -> dict[str, Any] | None:
    """Newest other ended real run in one slot, for rebuilding its row.

    Args:
        school: ``SchoolDB`` instance.
        class_id: Game-show ``classes.id``.
        module: Module token.
        slot: Class slot.
        skip_key: Run being moved away; never returned.
    """
    from lesson_slides_table import _real_runs_outside_latest

    run = _real_runs_outside_latest(school, int(class_id)).get((module, slot))
    if not run or str(run.get("run_key") or "") == skip_key:
        return None
    key = str(run["run_key"])
    if (module, slot, 0, key) in school.lesson_slides_dismissed_keys(int(class_id)):
        return None
    out = dict(run)
    with school._lock:
        session = school.conn.execute(
            """
            SELECT id, session_code, ended_at FROM live_class_sessions
            WHERE class_id = ? AND run_key = ? AND COALESCE(is_test, 0) = 0
            ORDER BY id DESC LIMIT 1
            """,
            (int(class_id), key),
        ).fetchone()
    if session is not None:
        out["live_session_id"] = int(session["id"])
        out["session_code"] = str(session["session_code"] or "")
        out["ended_at"] = str(out.get("ended_at") or session["ended_at"] or "")
    return out


def move_lesson_slides_run(
    school: Any,
    session_id: int,
    to_module: str,
    to_slot: str,
    *,
    apply: bool = False,
) -> list[str]:
    """Move one ended real class from the slot it was started as to another.

    Run Live Class saves a run under the module and class picked at Set
    Class. Picking the wrong one makes End replace that slot's Lesson
    Slides row and take over its catch-up link, so the earlier class in
    that slot drops off. This relabels every row of the run (teacher
    state, participation items and answers, result snapshots, team
    items, deletions, exit feedback for that meeting date), stores it on
    the target slot with the deck link Lesson Slides showed for it and its
    own join code, then rebuilds the source slot from the newest earlier
    real run there. That run gets its old catch-up token and join code
    back. With no earlier run the source slot's row is removed.

    Dry-run unless ``apply``. All writes are in one transaction.

    Args:
        school: ``SchoolDB`` instance.
        session_id: ``live_class_sessions.id`` of the mislabelled class.
        to_module: Target module, such as ``M4``.
        to_slot: Target class slot, such as ``C3``.
        apply: Write rows. Default prints the plan only.

    Returns:
        Lines describing each step, prefixed ``would`` on a dry run.

    Raises:
        ValueError: The session is missing, open, a test, not a saved real
            run, already in the target slot, the target is not a class in
            this course, or another real run already owns the target.
    """
    diag = diagnose_session(school, int(session_id))
    if diag is None:
        raise ValueError(f"session {session_id} not found")
    if diag.status != "ended":
        raise ValueError(f"session {session_id} is still open; end it first")
    if diag.is_test:
        raise ValueError(f"session {session_id} is a test class")
    if not diag.run_key or diag.run_is_real is not True or diag.run_discarded:
        raise ValueError(
            f"session {session_id} has no saved real run; run restore-lesson-slides --apply first"
        )
    cid = diag.class_id
    sid = diag.session_id
    key = diag.run_key
    src_mod, src_slot = diag.module, diag.slot
    tgt_mod, tgt_slot = parse_slot_token(f"{to_module}{to_slot}")
    if (src_mod, src_slot) == (tgt_mod, tgt_slot):
        raise ValueError(f"session {session_id} is already {tgt_mod} {tgt_slot}")
    catalogue = _catalogue_slots(diag.course)
    if catalogue and (tgt_mod, tgt_slot) not in catalogue:
        raise ValueError(f"{tgt_mod} {tgt_slot} is not a live class in {diag.course or 'this course'}")
    others = _slot_owner_keys(school, cid, tgt_mod, tgt_slot) - {key}
    if others:
        raise ValueError(
            f"{tgt_mod} {tgt_slot} already has a saved real class; "
            "delete it from Lesson Slides first so nothing is overwritten"
        )

    with school._lock:
        session = dict(
            school.conn.execute(
                "SELECT * FROM live_class_sessions WHERE id = ?", (sid,)
            ).fetchone()
        )
        stored = school.conn.execute(
            """
            SELECT * FROM lesson_slides_latest
            WHERE class_id = ? AND live_module = ? AND live_slot = ?
              AND COALESCE(is_test, 0) = 0
            """,
            (cid, src_mod, src_slot),
        ).fetchone()
        link = school.conn.execute(
            "SELECT * FROM catchup_links WHERE class_id = ? AND run_key = ?",
            (cid, key),
        ).fetchone()
    stored = dict(stored) if stored is not None else None
    link = dict(link) if link is not None else None
    stored_is_ours = stored is not None and str(stored.get("run_key") or "") == key
    deck_url = (
        str(stored.get("presentation_url") or "") if stored_is_ours else ""
    ) or str(session.get("presentation_url") or "")
    ended_at = str(session.get("ended_at") or diag.ended_at or "")
    code = str(session.get("session_code") or "")
    meeting_date = str(session.get("meeting_date") or "").strip()

    verb = "" if apply else "would "
    lines = [diag.summary_line()]
    lines.append(f"{verb}move this class from {src_mod} {src_slot} to {tgt_mod} {tgt_slot}")
    lines.append(f"  deck link kept for {tgt_mod} {tgt_slot}: {deck_url or '(none)'}")
    target_deck = _slot_deck_url(school, cid, tgt_mod, tgt_slot)
    if target_deck and target_deck != deck_url:
        lines.append(f"  note: the {tgt_mod} {tgt_slot} deck is a different file: {target_deck}")

    if not apply:
        rows = _relabel_run_rows(
            school, cid, key, src_mod, src_slot, tgt_mod, tgt_slot, meeting_date, apply=False
        )
        lines.append(_relabel_line(verb, rows))
        _append_previous_line(
            lines, verb, src_mod, src_slot,
            _previous_slot_run_preview(school, cid, src_mod, src_slot, key),
        )
        return lines

    with school._lock:
        school.conn.execute("BEGIN IMMEDIATE")
        try:
            # Relabel first so the source slot's earlier run is found without this one.
            rows = _relabel_run_rows(
                school, cid, key, src_mod, src_slot, tgt_mod, tgt_slot, meeting_date, apply=True
            )
            lines.append(_relabel_line(verb, rows))
            previous = _previous_slot_run(school, cid, src_mod, src_slot, skip_key=key)
            _append_previous_line(lines, verb, src_mod, src_slot, previous)
            now = datetime.now().replace(microsecond=0).isoformat()
            old_token = str(link["token"]) if link else ""
            if link:
                school.conn.execute("DELETE FROM catchup_links WHERE id = ?", (int(link["id"]),))
            if stored_is_ours:
                school.conn.execute(
                    """
                    DELETE FROM lesson_slides_latest
                    WHERE class_id = ? AND live_module = ? AND live_slot = ?
                      AND COALESCE(is_test, 0) = 0
                    """,
                    (cid, src_mod, src_slot),
                )
            school._upsert_real_catchup_link_unlocked(
                cid,
                key,
                tgt_mod,
                tgt_slot,
                now=now,
                live_session_id=sid,
                join_code=code,
                ended_at=ended_at,
                refresh_window=True,
            )
            school._upsert_lesson_slides_latest_unlocked(
                sid,
                class_id=cid,
                run_key=key,
                module=tgt_mod,
                slot=tgt_slot,
                ended_at=ended_at,
                session_code=code,
            )
            if deck_url:
                school.conn.execute(
                    """
                    UPDATE lesson_slides_latest SET presentation_url = ?
                    WHERE class_id = ? AND live_module = ? AND live_slot = ?
                      AND COALESCE(is_test, 0) = 0
                    """,
                    (deck_url, cid, tgt_mod, tgt_slot),
                )
            if previous:
                _rebuild_slot_unlocked(
                    school, cid, src_mod, src_slot, previous, old_token=old_token, now=now
                )
            elif old_token:
                school.conn.execute(
                    "UPDATE catchup_links SET token = ? WHERE class_id = ? AND run_key = ?",
                    (old_token, cid, key),
                )
            school.conn.execute("COMMIT")
        except Exception:
            school.conn.execute("ROLLBACK")
            raise
    after = diagnose_session(school, sid)
    lines.append(
        f"on_lesson_slides=yes as {tgt_mod} {tgt_slot}"
        if after is not None and after.shown and (after.module, after.slot) == (tgt_mod, tgt_slot)
        else "on_lesson_slides=no"
    )
    return lines


def _relabel_line(verb: str, rows: dict[str, int]) -> str:
    """One output line with the row count per relabelled table.

    Args:
        verb: ``""`` or ``"would "``.
        rows: Counts from ``_relabel_run_rows``.
    """
    return f"{verb}relabel " + ", ".join(f"{name}={count}" for name, count in rows.items())


def _append_previous_line(
    lines: list[str], verb: str, module: str, slot: str, previous: dict[str, Any] | None
) -> None:
    """Say what happens to the source slot after the move.

    Args:
        lines: Output lines, appended in place.
        verb: ``""`` or ``"would "``.
        module: Source module.
        slot: Source slot.
        previous: Earlier run in that slot, or ``None``.
    """
    if previous:
        lines.append(
            f"{verb}put {module} {slot} back to its earlier class "
            f"(session={previous.get('live_session_id') or '?'}, "
            f"ended {_toronto_clock(str(previous.get('ended_at') or ''))})"
        )
    else:
        lines.append(f"{verb}clear {module} {slot}: no earlier real class was found there")


def _slot_deck_url(school: Any, class_id: int, module: str, slot: str) -> str:
    """Deck link stored for one ``M``/``C`` slot, or ``""``.

    Args:
        school: ``SchoolDB`` instance.
        class_id: Game-show ``classes.id``.
        module: Module token.
        slot: Class slot.
    """
    try:
        deck = school.get_lesson_slide_deck(int(class_id), int(module[1:]), int(slot[1:]))
    except (TypeError, ValueError):
        return ""
    return str((deck or {}).get("presentation_url") or "")


def _relabel_run_rows(
    school: Any,
    class_id: int,
    run_key: str,
    src_mod: str,
    src_slot: str,
    tgt_mod: str,
    tgt_slot: str,
    meeting_date: str,
    *,
    apply: bool,
) -> dict[str, int]:
    """Count, and on ``apply`` rewrite, every module/slot field of one run.

    Does not commit; ``move_lesson_slides_run`` commits once at the end.

    Args:
        school: ``SchoolDB`` instance.
        class_id: Game-show ``classes.id``.
        run_key: Run being moved.
        src_mod: Module the run was saved as.
        src_slot: Slot the run was saved as.
        tgt_mod: Module to save it as.
        tgt_slot: Slot to save it as.
        meeting_date: Session meeting date; scopes exit feedback, which has
            no run key. Empty skips feedback.
        apply: Write the changes instead of only counting.

    Returns:
        Row counts by table.
    """
    cid = int(class_id)
    by_run = (
        ("participation_items", True),
        ("participation_answers", False),
        ("live_result_snapshots", True),
        ("group_engagement_items", True),
        ("lesson_slides_dismissed", True),
    )
    counts: dict[str, int] = {}
    with school._lock:
        for table, has_slot in by_run:
            counts[table] = int(
                school.conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE class_id = ? AND run_key = ?",
                    (cid, run_key),
                ).fetchone()[0]
            )
            if apply and counts[table]:
                assign = "live_module = ?, live_slot = ?" if has_slot else "live_module = ?"
                params: tuple[Any, ...] = (tgt_mod, tgt_slot) if has_slot else (tgt_mod,)
                school.conn.execute(
                    f"UPDATE {table} SET {assign} WHERE class_id = ? AND run_key = ?",
                    (*params, cid, run_key),
                )
        feedback_where = (
            "class_id = ? AND meeting_date = ? AND UPPER(live_module) = ? AND UPPER(live_slot) = ?"
        )
        feedback_args = (cid, meeting_date, src_mod, src_slot)
        counts["live_class_feedback"] = (
            int(
                school.conn.execute(
                    f"SELECT COUNT(*) FROM live_class_feedback WHERE {feedback_where}",
                    feedback_args,
                ).fetchone()[0]
            )
            if meeting_date
            else 0
        )
        if apply and counts["live_class_feedback"]:
            school.conn.execute(
                f"UPDATE live_class_feedback SET live_module = ?, live_slot = ? WHERE {feedback_where}",
                (tgt_mod, tgt_slot, *feedback_args),
            )
        sessions = school.conn.execute(
            "SELECT id, teacher_state_json FROM live_class_sessions WHERE class_id = ? AND run_key = ?",
            (cid, run_key),
        ).fetchall()
        counts["live_class_sessions"] = len(sessions)
        if apply:
            for row in sessions:
                try:
                    state = json.loads(row["teacher_state_json"] or "{}")
                except (TypeError, ValueError):
                    state = {}
                if not isinstance(state, dict):
                    state = {}
                state["live_module"] = tgt_mod
                state["live_slot"] = tgt_slot
                school.conn.execute(
                    "UPDATE live_class_sessions SET teacher_state_json = ? WHERE id = ?",
                    (json.dumps(state), int(row["id"])),
                )
    return counts


def _previous_slot_run_preview(
    school: Any, class_id: int, module: str, slot: str, run_key: str
) -> dict[str, Any] | None:
    """Dry-run lookup of the earlier run in a slot, ignoring the moved run.

    The live fallback still sees the moved run in the source slot on a dry
    run, so this asks inside a savepoint that is rolled back.

    Args:
        school: ``SchoolDB`` instance.
        class_id: Game-show ``classes.id``.
        module: Source module.
        slot: Source slot.
        run_key: Run being moved.
    """
    with school._lock:
        school.conn.execute("SAVEPOINT move_preview")
    try:
        _relabel_run_rows(school, class_id, run_key, module, slot, "M0", "C0", "", apply=True)
        return _previous_slot_run(school, class_id, module, slot, skip_key=run_key)
    finally:
        with school._lock:
            school.conn.execute("ROLLBACK TO SAVEPOINT move_preview")
            school.conn.execute("RELEASE SAVEPOINT move_preview")


def _rebuild_slot_unlocked(
    school: Any,
    class_id: int,
    module: str,
    slot: str,
    previous: dict[str, Any],
    *,
    old_token: str,
    now: str,
) -> None:
    """Write the source slot's row and catch-up link for its earlier run.

    The earlier run gets back the catch-up token it had before End took it,
    and its own join code when that code is still free. Caller holds the
    lock and commits.

    Args:
        school: ``SchoolDB`` instance.
        class_id: Game-show ``classes.id``.
        module: Source module.
        slot: Source slot.
        previous: Entry from ``_previous_slot_run``.
        old_token: Token the moved run's link carried, or ``""``.
        now: Current time for ``created_at`` / ``updated_at``.
    """
    cid = int(class_id)
    key = str(previous["run_key"])
    sid = previous.get("live_session_id")
    code = str(previous.get("session_code") or "")
    if code and school.conn.execute(
        "SELECT 1 FROM catchup_links WHERE portal_join_code = ? LIMIT 1", (code,)
    ).fetchone():
        code = ""
    ended_at = str(previous.get("ended_at") or "")
    school.conn.execute(
        "DELETE FROM catchup_links WHERE class_id = ? AND run_key = ?", (cid, key)
    )
    school._upsert_real_catchup_link_unlocked(
        cid,
        key,
        module,
        slot,
        now=now,
        live_session_id=int(sid) if sid else None,
        join_code=code or None,
        ended_at=ended_at or None,
        refresh_window=True,
    )
    if old_token:
        school.conn.execute(
            "UPDATE catchup_links SET token = ? WHERE class_id = ? AND run_key = ?",
            (old_token, cid, key),
        )
    school._upsert_lesson_slides_latest_unlocked(
        int(sid) if sid else None,
        class_id=cid,
        run_key=key,
        module=module,
        slot=slot,
        ended_at=ended_at,
        session_code=str(previous.get("session_code") or ""),
    )

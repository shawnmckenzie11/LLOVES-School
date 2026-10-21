#!/usr/bin/env python3
"""Idempotent local/cloud-dev school seed (picker accounts + one MCF3M class).

Runs when ``LOCAL_DEV_LOGIN`` is on. Never runs in production. Does not copy
laptop or Fly sqlite and does not require a committed ``.imscc``.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
if str(LMS_DIR) not in sys.path:
    sys.path.insert(0, str(LMS_DIR))
for path in (str(REPO_ROOT), str(REPO_ROOT / "tools" / "math-game-show")):
    if path not in sys.path:
        sys.path.append(path)

from paths import DEFAULT_IT_EMAIL  # noqa: E402

LOCAL_DEV_IT_EMAIL = DEFAULT_IT_EMAIL.lower()
LOCAL_DEV_SHAWN_EMAIL = "shawnmckenzie11.sm@gmail.com"
LOCAL_DEV_STAFF_EMAIL = "rspercival10@gmail.com"
LOCAL_DEV_PICKER_EMAILS: tuple[str, ...] = (
    LOCAL_DEV_IT_EMAIL,
    LOCAL_DEV_SHAWN_EMAIL,
    LOCAL_DEV_STAFF_EMAIL,
)
LOCAL_DEV_STAFF: tuple[tuple[str, str], ...] = (
    (LOCAL_DEV_SHAWN_EMAIL, "Shawn"),
    (LOCAL_DEV_STAFF_EMAIL, "R. Percival"),
)
LOCAL_DEV_COURSE = "MCF3M"
LOCAL_DEV_CODENAMES: tuple[str, ...] = ("Maple",)
LOCAL_DEV_DAYS = "M/W/F"
LOCAL_DEV_TIME = "2:00pm"


def local_dev_login_enabled() -> bool:
    """True when the offline picker / local-dev seed should run.

    Flask ``TESTING`` is not enough — unit tests stay on their own fixtures.
    Production must never set ``LOCAL_DEV_LOGIN``.
    """
    return (os.getenv("LOCAL_DEV_LOGIN") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def local_dev_bind_host() -> str:
    """Return the Flask bind host: ``HOST`` env, else ``0.0.0.0`` under local-dev."""
    configured = (os.getenv("HOST") or "").strip()
    if configured:
        return configured
    if local_dev_login_enabled():
        return "0.0.0.0"
    return "127.0.0.1"


def seed_local_dev_school(school: Any) -> dict[str, Any]:
    """Ensure IT + two staff, active semester, and one Shawn-owned MCF3M class.

    Args:
        school: ``SchoolDB`` instance.

    Returns:
        Summary with user ids, offering id, and class id (or skip reason).

    Raises:
        RuntimeError: If ``LOCAL_DEV_LOGIN`` is off.
    """
    if not local_dev_login_enabled():
        raise RuntimeError("seed_local_dev_school requires LOCAL_DEV_LOGIN=1")

    semester = school.activate_from_semester_json()
    it_user = school.get_user_by_email(LOCAL_DEV_IT_EMAIL)
    if it_user and not it_user.get("verified_at"):
        it_user = school.mark_verified(int(it_user["id"]))

    staff_rows: list[dict[str, Any]] = []
    for email, name in LOCAL_DEV_STAFF:
        row = school.register_staff(email, name)
        if not row.get("verified_at"):
            row = school.mark_verified(int(row["id"]))
        staff_rows.append(row)

    shawn = school.get_user_by_email(LOCAL_DEV_SHAWN_EMAIL)
    if not shawn:
        raise RuntimeError(f"Failed to register {LOCAL_DEV_SHAWN_EMAIL}")

    offering = school.assign_course(
        teacher_user_id=int(shawn["id"]),
        ontario_code=LOCAL_DEV_COURSE,
    )
    if not str(offering.get("live_days") or "").strip() or not str(
        offering.get("live_time") or ""
    ).strip():
        offering = school.set_offering_schedule(
            int(offering["id"]),
            live_days=LOCAL_DEV_DAYS,
            live_time=LOCAL_DEV_TIME,
        )

    classes = school.classes_for_offering(int(offering["id"]))
    if classes:
        class_row = classes[0]
        created_class = False
    else:
        class_row = school.game.create_class(
            year=str(semester["year_display"]),
            semester=str(semester["term"]),
            course_code=LOCAL_DEV_COURSE,
            days_preset=str(offering.get("live_days") or LOCAL_DEV_DAYS),
            time_label=str(offering.get("live_time") or LOCAL_DEV_TIME),
            codenames=list(LOCAL_DEV_CODENAMES),
            offering_id=int(offering["id"]),
            teacher_user_id=int(shawn["id"]),
        )
        created_class = True

    summary = {
        "skipped": False,
        "semester_id": int(semester["id"]),
        "semester_label": str(semester.get("label") or ""),
        "it_user_id": int(it_user["id"]) if it_user else None,
        "staff_ids": [int(row["id"]) for row in staff_rows],
        "offering_id": int(offering["id"]),
        "class_id": int(class_row["id"]),
        "created_class": created_class,
        "picker_emails": list(LOCAL_DEV_PICKER_EMAILS),
    }
    library_id = _ensure_local_dev_library(school, int(offering["id"]))
    summary["library_id"] = library_id
    bank_seed = _seed_existing_mcf3m_builder_banks(school)
    if bank_seed is not None:
        summary["bank_seed"] = bank_seed
    summary["warmup_seed"] = school.seed_course_wide_warmups(library_id)
    try:
        from curriculum_bank_seed import seed_local_question_banks
    except ImportError:
        from lms.curriculum_bank_seed import seed_local_question_banks

    summary["import_banks"] = seed_local_question_banks(school)
    school.ensure_class_test_student(int(class_row["id"]))
    summary["participation_seed"] = _seed_demo_participation(
        school, int(class_row["id"]), int(shawn["id"])
    )
    test_ids = school.test_student_ids(int(class_row["id"]))
    if test_ids:
        school.set_student_pin(
            int(class_row["id"]),
            int(test_ids[0]),
            LOCAL_DEV_TEST_STUDENT_PIN,
            pin_set_by="teacher",
        )
        summary["test_student_pin"] = LOCAL_DEV_TEST_STUDENT_PIN
    summary["engagement_roster"] = _seed_engagement_ap_roster(
        school, int(class_row["id"])
    )
    return summary


LOCAL_DEV_PIN = "1234"
# Local demo only — never used in populate/production paths (MCK-229 Ops).
LOCAL_DEV_TEST_STUDENT_PIN = "9999"
LOCAL_DEV_M1_QUESTIONS: tuple[dict[str, Any], ...] = (
    {"text": "Which expression is the expanded form of $(x + 3)^2$?", "type": "mc",
     "options": [{"text": "$x^2 + 9$"}, {"text": "$x^2 + 6x + 9$"}, {"text": "$x^2 + 3x + 9$"}]},
    {"text": "The parabola $y = -2(x - 1)^2 + 5$ opens…", "type": "mc",
     "options": [{"text": "up, with vertex $(1, 5)$"}, {"text": "down, with vertex $(1, 5)$"}]},
    {"text": "Factor fully: $x^2 - 5x + 6$", "type": "mc",
     "options": [{"text": "$(x - 2)(x - 3)$"}, {"text": "$(x + 2)(x + 3)$"}]},
    {"text": "Evaluate $f(x) = 3x^2 - 4$ at $x = 2$.", "type": "numeric"},
)
# (live slot, how many of the questions Maple answered live)
LOCAL_DEV_M1_RUNS: tuple[tuple[str, int], ...] = (("C1", 2), ("C2", 3))
LOCAL_DEV_M3_QUESTIONS: tuple[dict[str, Any], ...] = (
    {"text": "Simplify: $(2x^3)(3x^2)$", "type": "mc",
     "options": [{"text": "$5x^5$"}, {"text": "$6x^5$"}]},
    {"text": "What is the slope of $y = -3x + 7$?", "type": "numeric"},
)
LOCAL_DEV_M3_RUN: tuple[str, int] = ("C1", 1)


def _seed_engagement_ap_roster(school: Any, class_id: int) -> dict[str, Any]:
    """Ensure Cedar exists on a team and Maple stays ungrouped for engagement UI demos.

    Idempotent: safe on every local-dev boot.
    """
    from datetime import date

    maple = school.game.find_student_by_codename(class_id, "Maple")
    if maple is None:
        return {"skipped": "no Maple"}
    cedar = school.game.find_student_by_codename(class_id, "Cedar")
    if cedar is None:
        school.game.add_student(class_id, codename="Cedar")
        cedar = school.game.find_student_by_codename(class_id, "Cedar")
    if cedar is None:
        return {"skipped": "no Cedar"}
    maple_id = int(maple["id"])
    cedar_id = int(cedar["id"])
    game = school.game
    game.begin_game(class_id, today=date(2026, 10, 12))
    for sid in (maple_id, cedar_id):
        game.mark_student_present_on_open_session(class_id, sid)
    game.assign_teams(class_id, 2, "random")
    with game._lock:
        game.conn.execute("DELETE FROM game_memberships WHERE student_id = ?", (maple_id,))
        game.conn.commit()
    cedar_team_id = school.student_team_id_for_class(class_id, cedar_id)
    if cedar_team_id is None:
        board = game.scoreboard(int(class_id)) or {}
        teams = list(board.get("teams") or [])
        if not teams:
            return {"skipped": "no teams"}
        cedar_team_id = int(teams[0]["id"])
        game_row = game.conn.execute(
            "SELECT id FROM games WHERE class_id = ? ORDER BY id DESC LIMIT 1",
            (int(class_id),),
        ).fetchone()
        if game_row is None:
            return {"skipped": "no game"}
        with game._lock:
            game.conn.execute(
                """
                INSERT INTO game_memberships (game_id, team_id, student_id)
                VALUES (?, ?, ?)
                ON CONFLICT(game_id, student_id) DO UPDATE SET team_id = excluded.team_id
                """,
                (int(game_row[0]), int(cedar_team_id), cedar_id),
            )
            game.conn.commit()
    return {
        "maple_id": maple_id,
        "cedar_id": cedar_id,
        "cedar_team_id": int(cedar_team_id) if cedar_team_id is not None else None,
    }


def local_dev_today() -> date:
    """Return the calendar day the demo seed treats as today.

    America/Toronto, matching the attendance grid. A UTC date just after
    midnight is still the previous evening in Ontario, so ``date.today()``
    on a class day would disagree with the columns this seed writes.

    Returns:
        Today's date in ``America/Toronto``.
    """
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("America/Toronto")).date()


def _ended_column_for_demo_run(school: Any, run_key: str, persisted: Any) -> int | None:
    """Return the attendance column id for one demo End.

    End Class writes the participation credit itself, so the follow-up
    persist often finds that row and returns no new session id. The column
    still has to move onto the scheduled class day.

    Args:
        school: ``SchoolDB`` instance.
        run_key: Participation run key for the demo End.
        persisted: Return value of ``_persist_demo_live_participation_credit``.

    Returns:
        ``sessions.id``, or None when this run has no column.
    """
    if isinstance(persisted, dict) and persisted.get("session_id"):
        return int(persisted["session_id"])
    key = str(run_key or "").strip()
    if not key:
        return None
    row = school.game.conn.execute(
        """
        SELECT session_id FROM live_participation_credits
        WHERE run_key = ?
        LIMIT 1
        """,
        (key,),
    ).fetchone()
    if row is None or row["session_id"] is None:
        return None
    return int(row["session_id"])


def _stamp_demo_column_on_class_day(
    school: Any, session_id: int | None, when: datetime
) -> None:
    """Point one ended demo column at the scheduled class day.

    End Class opens the attendance column on the next meeting, which can
    be a future day. The Set Class calendar then checks that future day
    and leaves the day the class actually ran blank.

    Args:
        school: ``SchoolDB`` instance.
        session_id: Ended ``sessions.id``, or None when nothing was written.
        when: Scheduled class end. Only the calendar day is kept; the
            column starts at the demo class time.
    """
    if not session_id:
        return
    day = when.date() if isinstance(when, datetime) else when
    meeting = datetime(day.year, day.month, day.day, 14, 0)
    from schedule import format_header_label

    header = format_header_label(meeting, LOCAL_DEV_TIME)
    game = school.game
    with game._lock:
        game.conn.execute(
            """
            UPDATE sessions
            SET starts_at = ?, header_label = ?
            WHERE id = ? AND status = 'ended'
            """,
            (meeting.isoformat(), header, int(session_id)),
        )
        game.conn.commit()


def _repair_demo_future_columns(school: Any, class_id: int) -> None:
    """Move ended demo columns onto past class days when none are in the past.

    Runs when there are exactly three ended columns (M1 C1, M1 C2, and M3)
    and every one is today or later. End Class can open those columns on
    the next meeting, which is today once UTC has crossed midnight ahead
    of the class timezone. A column already before today makes this a
    no-op, so a second seed does not shuffle dates. Active template
    sessions are left alone.

    Args:
        school: ``SchoolDB`` instance.
        class_id: Demo class id.
    """
    from gradebook import session_meeting_date
    from live_class_run import LiveClassSchedule, scheduled_class_end_datetime

    sched_row = school.live_class_schedule_for_class(int(class_id))
    if not sched_row:
        return
    schedule = LiveClassSchedule(
        days_key=str(sched_row["live_days"]),
        end_time_label=str(sched_row["live_end_time"]),
    )
    game = school.game
    with game._lock:
        rows = game.conn.execute(
            """
            SELECT id, starts_at FROM sessions
            WHERE class_id = ? AND status = 'ended'
            ORDER BY id
            """,
            (int(class_id),),
        ).fetchall()
    if len(rows) != 3:
        return
    today = local_dev_today()
    for row in rows:
        meeting = session_meeting_date(str(row["starts_at"] or ""))
        if meeting is None or meeting < today:
            return
    for row, which in zip(rows, (0, 1, 4)):
        ended = scheduled_class_end_datetime(
            schedule, which=which, reference=local_dev_today()
        )
        _stamp_demo_column_on_class_day(school, int(row["id"]), ended)


def _seed_demo_participation(school: Any, class_id: int, teacher_id: int) -> dict[str, Any]:
    """Run and End M1 C1 + C2 once so the teacher Participation page has data.

    Each run publishes individual questions, records some of Maple's answers,
    then goes through the real End path, which captures participation and
    creates that run's catch-up link. Maple's course PIN is set when empty.
    Skipped when the class already has participation data or a live class
    is in progress.

    Args:
        school: ``SchoolDB`` instance.
        class_id: Demo class id.
        teacher_id: Shawn's ``users.id``.

    Returns:
        ``{"skipped": reason}`` or ``{"runs": [...], "links": n}``.
    """
    import json

    conn = school.conn
    if conn.execute(
        "SELECT 1 FROM participation_items WHERE class_id = ? AND live_module = 'M3' LIMIT 1",
        (class_id,),
    ).fetchone():
        _backfill_demo_live_participation_credits(school, int(class_id))
        _repair_demo_future_columns(school, int(class_id))
        return {"skipped": "already seeded"}
    if conn.execute(
        "SELECT 1 FROM participation_items WHERE class_id = ? LIMIT 1", (class_id,)
    ).fetchone():
        _backfill_demo_live_participation_credits(school, int(class_id))
        seeded = _seed_demo_m3_participation(school, class_id, teacher_id)
        _repair_demo_future_columns(school, int(class_id))
        return seeded
    if school.get_active_live_session_for_class(class_id):
        return {"skipped": "live class in progress"}
    maple = school.game.find_student_by_codename(class_id, "Maple")
    if not maple:
        return {"skipped": "no Maple"}
    maple_id = int(maple["id"])
    from celebration import SECRET_KEY_DEV_DEFAULT

    pin_secret = (os.getenv("FLASK_SECRET_KEY") or "").strip() or SECRET_KEY_DEV_DEFAULT
    school.ensure_default_pins_for_class(class_id, pin_secret)
    from live_class_run import LiveClassSchedule, scheduled_class_end_datetime

    sched_row = school.live_class_schedule_for_class(class_id)
    if not sched_row:
        return {"skipped": "no live class schedule"}
    schedule = LiveClassSchedule(
        days_key=str(sched_row["live_days"]),
        end_time_label=str(sched_row["live_end_time"]),
    )
    runs: list[str] = []
    for run_index, (slot, answered) in enumerate(LOCAL_DEV_M1_RUNS):
        runs.append(
            _run_demo_individual_live_end(
                school,
                class_id,
                teacher_id,
                maple_id,
                schedule,
                run_index,
                live_module="M1",
                live_slot=slot,
                questions=LOCAL_DEV_M1_QUESTIONS,
                answered=answered,
            )
        )
    m2 = _seed_m2_c1_snapshot_only(school, class_id, maple_id)
    m3 = _seed_demo_m3_participation(school, class_id, teacher_id)
    return {
        "runs": runs,
        "links": len(school.list_catchup_links(class_id)),
        "m2_c1_snapshot": m2,
        "m3": m3,
    }


def _run_demo_individual_live_end(
    school: Any,
    class_id: int,
    teacher_id: int,
    maple_id: int,
    schedule: Any,
    run_index: int,
    *,
    live_module: str,
    live_slot: str,
    questions: tuple[dict[str, Any], ...],
    answered: int,
) -> str:
    """Publish individual items, record Maple's answers, and End one live class.

    Uses End Class (``finish_live_class``), which writes the participation
    run and catch-up link. A force-end or cancel does not, so this seed
    must not close the session through ``end_live_class_session``.

    Returns:
        The ``live_slot`` label written (e.g. ``C1``).
    """
    import json

    from live_class_run import scheduled_class_end_datetime

    conn = school.conn
    session_id = int(school.start_live_class_session(class_id, teacher_id)["id"])
    ended_dt = scheduled_class_end_datetime(
        schedule, which=run_index, reference=local_dev_today()
    )
    ended_iso = ended_dt.replace(microsecond=0).isoformat()
    stamp = ended_iso
    mod = str(live_module)
    conn.execute(
        "UPDATE live_class_sessions SET teacher_state_json = ? WHERE id = ?",
        (json.dumps({"live_module": mod, "live_slot": live_slot}), session_id),
    )
    school.record_engagement_event(
        "present.join", session_id=session_id, student_id=maple_id
    )
    for index, question in enumerate(questions):
        item_ref = f"demo-{mod}-q{index}"
        prompt_id = conn.execute(
            "INSERT INTO live_session_prompts (live_session_id, slide_index, kind, payload, "
            "created_at, updated_at) VALUES (?, ?, 'poll', '{}', ?, ?)",
            (session_id, index, stamp, stamp),
        ).lastrowid
        conn.execute(
            "INSERT INTO live_session_items (live_session_id, placement_key, item_id, stage, "
            "kind, item_json, prompt_id, status, publish_mode, response_mode, published_at, "
            "created_at, updated_at) VALUES (?, ?, ?, 'round', 'poll', ?, ?, 'closed', "
            "'individual', 'individual', ?, ?, ?)",
            (
                session_id,
                item_ref,
                item_ref,
                json.dumps(question),
                prompt_id,
                stamp,
                stamp,
                stamp,
            ),
        )
        school.record_engagement_event(
            "item.offered",
            session_id=session_id,
            item_ref=item_ref,
            payload={"mode": "individual"},
        )
        if index < answered:
            school.record_engagement_event(
                "answer.submit",
                session_id=session_id,
                student_id=maple_id,
                item_ref=item_ref,
            )
            conn.execute(
                "INSERT INTO live_session_responses (prompt_id, student_id, response_json, "
                "created_at, updated_at) VALUES (?, ?, '{}', ?, ?)",
                (prompt_id, maple_id, stamp, stamp),
            )
    conn.commit()
    conn.execute(
        "UPDATE live_class_sessions SET ended_at = ? WHERE id = ?",
        (ended_iso, session_id),
    )
    conn.commit()
    school.finish_live_class(int(class_id), celebrate=True, live_run=True)
    # End Class stamps ended_at as now. Keep the scheduled class end.
    conn.execute(
        "UPDATE live_class_sessions SET ended_at = ? WHERE id = ?",
        (ended_iso, session_id),
    )
    conn.commit()
    row = conn.execute(
        """
        SELECT run_key FROM participation_items
        WHERE class_id = ? AND live_module = ? AND live_slot = ?
        ORDER BY id DESC LIMIT 1
        """,
        (int(class_id), mod, str(live_slot)),
    ).fetchone()
    if row:
        run_key = str(row["run_key"])
        conn.execute(
            """
            UPDATE participation_runs
            SET is_real = 1, discarded = 0
            WHERE class_id = ? AND run_key = ?
            """,
            (int(class_id), run_key),
        )
        conn.commit()
        persisted = _persist_demo_live_participation_credit(
            school, int(class_id), maple_id, run_key, answered
        )
        column_id = _ended_column_for_demo_run(school, run_key, persisted)
        if column_id:
            _stamp_demo_column_on_class_day(school, column_id, ended_dt)
    return str(live_slot)


def _backfill_demo_live_participation_credits(school: Any, class_id: int) -> None:
    """Ensure LPC rows exist for every participation run already in the demo class."""
    maple = school.game.find_student_by_codename(class_id, "Maple")
    if maple is None:
        return
    maple_id = int(maple["id"])
    rows = school.conn.execute(
        """
        SELECT pi.run_key,
               COUNT(DISTINCT CASE WHEN a.student_id = ? THEN a.question_key END) AS answered
        FROM participation_items pi
        LEFT JOIN participation_answers a
            ON a.class_id = pi.class_id
           AND a.run_key = pi.run_key
           AND a.question_key = pi.question_key
        WHERE pi.class_id = ?
        GROUP BY pi.run_key
        """,
        (maple_id, int(class_id)),
    ).fetchall()
    for row in rows:
        _persist_demo_live_participation_credit(
            school,
            int(class_id),
            maple_id,
            str(row["run_key"]),
            int(row["answered"] or 0),
        )


def _persist_demo_live_participation_credit(
    school: Any,
    class_id: int,
    maple_id: int,
    run_key: str,
    answered: int,
) -> dict | None:
    """Link one demo live run to a game-show column via ``live_participation_credits``.

    Idempotent when a credit row already exists for ``run_key``. Gives the
    Participation tab session metadata and Finish Module actions in local dev.

    Returns:
        The End Class persist payload (including ``session_id``), or None
        when this run was already credited.
    """
    key = str(run_key or "").strip()
    if not key:
        return
    game = school.game
    existing = game.conn.execute(
        "SELECT 1 FROM live_participation_credits WHERE run_key = ? LIMIT 1",
        (key,),
    ).fetchone()
    if existing is not None:
        return
    credits = {int(maple_id): int(answered)} if answered > 0 else {}
    return game.persist_end_class_column(
        int(class_id),
        [int(maple_id)],
        credits,
        include_attendance=True,
        include_participation=bool(credits),
        run_key=key,
        create_game=True,
    )


def _seed_demo_m3_participation(school: Any, class_id: int, teacher_id: int) -> dict[str, Any]:
    """Add one real M3 C1 run with two individual questions (Maple answers one).

    Idempotent: skipped when M3 items already exist or a live class is open.
    """
    conn = school.conn
    if conn.execute(
        "SELECT 1 FROM participation_items WHERE class_id = ? AND live_module = 'M3' LIMIT 1",
        (class_id,),
    ).fetchone():
        return {"skipped": "m3 present"}
    if school.get_active_live_session_for_class(class_id):
        return {"skipped": "live class in progress"}
    maple = school.game.find_student_by_codename(class_id, "Maple")
    if not maple:
        return {"skipped": "no Maple"}
    maple_id = int(maple["id"])
    from live_class_run import LiveClassSchedule

    sched_row = school.live_class_schedule_for_class(class_id)
    if not sched_row:
        return {"skipped": "no live class schedule"}
    schedule = LiveClassSchedule(
        days_key=str(sched_row["live_days"]),
        end_time_label=str(sched_row["live_end_time"]),
    )
    slot, answered = LOCAL_DEV_M3_RUN
    run_index = 4
    slot_label = _run_demo_individual_live_end(
        school,
        class_id,
        teacher_id,
        maple_id,
        schedule,
        run_index,
        live_module="M3",
        live_slot=slot,
        questions=LOCAL_DEV_M3_QUESTIONS,
        answered=answered,
    )
    offered = conn.execute(
        """
        SELECT COUNT(DISTINCT question_key) FROM participation_items
        WHERE class_id = ? AND live_module = 'M3'
        """,
        (int(class_id),),
    ).fetchone()[0]
    return {"slot": slot_label, "offered": int(offered), "answered": answered}


def _seed_m2_c1_snapshot_only(school: Any, class_id: int, maple_id: int) -> str:
    """Insert M2 C1 End snapshots without participation rows (MCK-204 backfill demo).

    Returns ``"skipped"`` or the ``run_key`` written.
    """
    import json

    conn = school.conn
    run_key = "local-dev:m2-c1"
    if conn.execute(
        "SELECT 1 FROM live_result_snapshots WHERE class_id = ? AND run_key = ? LIMIT 1",
        (class_id, run_key),
    ).fetchone():
        return "skipped"
    stamp = "2026-10-14T09:00:00"
    sid = 88001
    for index, answered in enumerate((True, True, False)):
        qjson = json.dumps(LOCAL_DEV_M1_QUESTIONS[index % len(LOCAL_DEV_M1_QUESTIONS)])
        if answered:
            conn.execute(
                """
                INSERT INTO live_result_snapshots (
                    live_session_id, class_id, session_code, run_key,
                    live_module, live_slot, source, question_key, question_json,
                    student_id, participant_uuid, codename, team_id,
                    answer_json, awarded_points, answered_at, snapshot_at
                ) VALUES (?, ?, 'DEMO', ?, 'M2', 'C1', 'prompt', ?, ?, ?, NULL, 'Maple', NULL,
                          '{}', NULL, ?, ?)
                """,
                (
                    sid,
                    class_id,
                    run_key,
                    f"item:demo-M2-q{index}",
                    qjson,
                    maple_id,
                    stamp,
                    stamp,
                ),
            )
    conn.commit()
    return run_key


def _ensure_local_dev_library(school: Any, offering_id: int) -> int:
    """Attach an MCF3M content library so Course Wide Import has a bank.

    Leaves an offering that already points at a pack alone. Otherwise reuses
    the newest MCF3M library, or creates an upload library with no cartridge.

    Args:
        school: ``SchoolDB`` instance.
        offering_id: ``course_offerings.id`` for the demo section.

    Returns:
        ``content_libraries.id`` now attached to the offering.
    """
    offering = school.get_offering(int(offering_id)) or {}
    existing = offering.get("library_id")
    if existing:
        return int(existing)
    latest = school.latest_library_for_code(LOCAL_DEV_COURSE)
    if latest:
        school.attach_library(int(offering_id), int(latest["id"]))
        return int(latest["id"])
    created = school.create_library(LOCAL_DEV_COURSE, origin="upload")
    school.attach_library(int(offering_id), int(created["id"]))
    return int(created["id"])


def _seed_existing_mcf3m_builder_banks(school: Any) -> dict[str, Any] | None:
    """Seed builder banks only when an MCF3M content library already exists.

    Args:
        school: ``SchoolDB`` instance.

    Returns:
        Seed summary, or ``None`` when no MCF3M library is present.
    """
    library_ids = {
        int(row["id"])
        for row in school.conn.execute(
            "SELECT id FROM content_libraries WHERE ontario_code = ?",
            (LOCAL_DEV_COURSE,),
        ).fetchall()
    }
    if not library_ids:
        return None
    try:
        from builder_bank_seed import seed_mcf3m_builder_banks
    except ImportError:
        from lms.builder_bank_seed import seed_mcf3m_builder_banks

    bank_seed: dict[str, Any] = {"libraries": []}
    for library_id in sorted(library_ids):
        library_entry: dict[str, Any] = {
            "library_id": int(library_id),
            **seed_mcf3m_builder_banks(school, int(library_id)),
        }
        if os.getenv("LOCAL_DEV_MIRROR_BANK_IMAGES", "").strip() == "1":
            try:
                from bank_image_mirror import mirror_library_bank_images
            except ImportError:
                from lms.bank_image_mirror import mirror_library_bank_images

            mirror_limit = int(os.getenv("LOCAL_DEV_MIRROR_BANK_IMAGES_LIMIT", "0") or "0")
            limit = mirror_limit if mirror_limit > 0 else None
            library_entry["mirror"] = mirror_library_bank_images(
                school, int(library_id), limit=limit
            )
        bank_seed["libraries"].append(library_entry)
    return bank_seed


def main() -> int:
    """Seed the default local sqlite. Exit 0 when skipped or complete."""
    from dotenv import load_dotenv

    load_dotenv(LMS_DIR / ".env")
    load_dotenv(REPO_ROOT / ".env")
    if not local_dev_login_enabled():
        print("local-dev seed skipped: LOCAL_DEV_LOGIN is not set")
        return 0
    from curriculum import seed_curriculum
    from paths import DEFAULT_DB_PATH
    from school_db import SchoolDB

    db_file = Path(os.getenv("LLOVES_DB") or DEFAULT_DB_PATH)
    store = Path(os.getenv("LLOVES_DATA_DIR") or db_file.parent)
    school = SchoolDB(db_file, store)
    try:
        seed_curriculum(school)
        summary = seed_local_dev_school(school)
    except Exception as exc:  # noqa: BLE001 - start script must report and fail
        print(f"local-dev seed failed: {exc}", file=sys.stderr)
        return 1
    finally:
        school.close()
    print(
        "local-dev seed ok: "
        f"semester={summary['semester_label']} "
        f"class_id={summary['class_id']} "
        f"created_class={summary['created_class']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

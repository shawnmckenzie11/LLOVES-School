#!/usr/bin/env python3
"""Idempotent local/cloud-dev school seed (picker accounts + one MCF3M class).

Runs when ``LOCAL_DEV_LOGIN`` is on. Never runs in production. Does not copy
laptop or Fly sqlite and does not require a committed ``.imscc``.
"""

from __future__ import annotations

import os
import sys
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
    summary["participation_seed"] = _seed_demo_participation(
        school, int(class_row["id"]), int(shawn["id"])
    )
    return summary


LOCAL_DEV_PIN = "1234"
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
        "SELECT 1 FROM participation_items WHERE class_id = ? LIMIT 1", (class_id,)
    ).fetchone():
        return {"skipped": "already seeded"}
    if school.get_active_live_session_for_class(class_id):
        return {"skipped": "live class in progress"}
    maple = school.game.find_student_by_codename(class_id, "Maple")
    if not maple:
        return {"skipped": "no Maple"}
    maple_id = int(maple["id"])
    from celebration import SECRET_KEY_DEV_DEFAULT

    pin_secret = (os.getenv("FLASK_SECRET_KEY") or "").strip() or SECRET_KEY_DEV_DEFAULT
    school.ensure_default_pins_for_class(class_id, pin_secret)
    stamp = "2026-10-13T09:00:00"
    runs: list[str] = []
    for slot, answered in LOCAL_DEV_M1_RUNS:
        session_id = int(school.start_live_class_session(class_id, teacher_id)["id"])
        conn.execute(
            "UPDATE live_class_sessions SET teacher_state_json = ? WHERE id = ?",
            (json.dumps({"live_module": "M1", "live_slot": slot}), session_id),
        )
        for index, question in enumerate(LOCAL_DEV_M1_QUESTIONS):
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
                (session_id, f"demo-M1-q{index}", f"demo-q{index}", json.dumps(question),
                 prompt_id, stamp, stamp, stamp),
            )
            if index < answered:
                conn.execute(
                    "INSERT INTO live_session_responses (prompt_id, student_id, response_json, "
                    "created_at, updated_at) VALUES (?, ?, '{}', ?, ?)",
                    (prompt_id, maple_id, stamp, stamp),
                )
        conn.commit()
        school.cleanup_live_session_response_data(session_id)
        school.end_live_class_session(session_id)
        runs.append(slot)
    m2 = _seed_m2_c1_snapshot_only(school, class_id, maple_id)
    return {
        "runs": runs,
        "links": len(school.list_catchup_links(class_id)),
        "m2_c1_snapshot": m2,
    }


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

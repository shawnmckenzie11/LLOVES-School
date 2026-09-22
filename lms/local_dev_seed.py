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
    summary["warmup_seed"] = _seed_course_wide_warmup_copies(school)
    return summary


def _seed_course_wide_warmup_copies(school: Any) -> list[dict[str, Any]]:
    """Seed the locked warmup bank into MCF3M and MCR3U libraries.

    Creates a library for a course that does not have one yet. Does not
    attach the MCR3U copy to the MCF3M demo offering.

    Args:
        school: ``SchoolDB`` instance.

    Returns:
        One seed summary per course library.
    """
    try:
        from course_warmup_seed import COURSE_WIDE_WARMUP_COURSES
    except ImportError:
        from lms.course_warmup_seed import COURSE_WIDE_WARMUP_COURSES

    summaries: list[dict[str, Any]] = []
    for code in COURSE_WIDE_WARMUP_COURSES:
        library = school.latest_library_for_code(code)
        if library is None:
            library = school.create_library(code, origin="upload")
        summaries.append(school.seed_course_wide_warmups(int(library["id"])))
    return summaries


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
    from paths import DEFAULT_DB_PATH
    from school_db import SchoolDB

    db_file = Path(os.getenv("LLOVES_DB") or DEFAULT_DB_PATH)
    store = Path(os.getenv("LLOVES_DATA_DIR") or db_file.parent)
    school = SchoolDB(db_file, store)
    try:
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

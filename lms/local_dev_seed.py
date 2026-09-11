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

    return {
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

#!/usr/bin/env python3
"""Prepare three local-dev teachers for MCK-539 Settings QA screenshots."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LMS_DIR = ROOT / "lms"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(LMS_DIR))

os.environ.setdefault("LOCAL_DEV_LOGIN", "1")
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from features import PLAN_BASIC, set_feature_override  # noqa: E402

PLAIN_EMAIL = "mck539-plain@example.com"
WB_EMAIL = "mck539-wb@example.com"
GRANDFATHER_EMAIL = "shawnmckenzie11.sm@gmail.com"


def _ensure_staff(school, email: str, display_name: str) -> int:
    """Return ``users.id``, creating a staff row when missing."""
    row = school.get_user_by_email(email)
    if row is None:
        row = school.register_staff(email, display_name=display_name)
    return int(row["id"])


def prep() -> None:
    """Create plain Basic, whiteboard-only, and leave grandfathered Shawn as-is."""
    app = create_app()
    with app.app_context():
        from auth import school_db

        school = school_db()
        plain_id = _ensure_staff(school, PLAIN_EMAIL, "Teacher Plain")
        wb_id = _ensure_staff(school, WB_EMAIL, "Teacher WB")
        with school._lock:
            school.conn.execute("DELETE FROM user_features WHERE user_id = ?", (plain_id,))
            school.conn.execute("DELETE FROM user_features WHERE user_id = ?", (wb_id,))
            school.conn.execute(
                "UPDATE users SET plan = ? WHERE id IN (?, ?)",
                (PLAN_BASIC, plain_id, wb_id),
            )
        set_feature_override(school, wb_id, "whiteboard", True)
        grand = school.get_user_by_email(GRANDFATHER_EMAIL)
        if grand is None:
            raise SystemExit(f"Missing seeded teacher {GRANDFATHER_EMAIL}")
        uf = school.conn.execute(
            "SELECT COUNT(*) AS c FROM user_features WHERE user_id = ?",
            (int(grand["id"]),),
        ).fetchone()["c"]
        print(
            "mck539 settings shot prep ok:",
            f"plain={PLAIN_EMAIL}",
            f"wb={WB_EMAIL}",
            f"grandfathered={GRANDFATHER_EMAIL} user_features={uf}",
        )


if __name__ == "__main__":
    prep()
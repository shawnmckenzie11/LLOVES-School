#!/usr/bin/env python3
"""Upload local specific-expectation dumps into Curriculum course folders.

Uses the operator's stored Slides/Drive token. Prints file names and ids only.
"""

from __future__ import annotations

import sys
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(LMS_DIR / ".env")

from live_class_constants import CURRICULUM_COURSE_DRIVE_FOLDERS  # noqa: E402
from live_class_slides import GoogleSlidesClient  # noqa: E402
from math_expectations_pdf import upload_local_dump_to_drive  # noqa: E402
from paths import DATA_DIR, DEFAULT_DB_PATH  # noqa: E402
from school_db import SchoolDB  # noqa: E402


def main() -> int:
    """Refresh token and upload specific JSON/Markdown per course folder."""
    school = SchoolDB(db_path=DEFAULT_DB_PATH, data_dir=DATA_DIR)
    try:
        user = school.get_user(3)
        if not user:
            print("missing operator user 3", file=sys.stderr)
            return 1
        client = GoogleSlidesClient(school, user=user)
        token = client._access_token()
        created = upload_local_dump_to_drive(
            access_token=token,
            course_folders=CURRICULUM_COURSE_DRIVE_FOLDERS,
            skip_markdown_codes=frozenset({"MAP4C", "MBF3C", "MEL3E"}),
        )
        print(f"uploaded {len(created)}")
        for row in created:
            print(row.get("name"), row.get("id"))
    finally:
        school.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

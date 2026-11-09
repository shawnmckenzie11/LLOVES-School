#!/usr/bin/env python3
"""HTTP smoke for templates touched in a copy-only PR (Flask test client)."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LMS_DIR = REPO_ROOT / "lms"
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

from copy_only_check import changed_paths, classify_path  # noqa: E402

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)

from app import create_app  # noqa: E402


def _changed_templates(base: str, head: str) -> list[str]:
    """Return changed template paths under lms/templates/."""
    templates: list[str] = []
    for path in changed_paths(base, head):
        if classify_path(path) != "markup":
            continue
        if not path.startswith("lms/templates/"):
            continue
        templates.append(path)
    return templates


def _staff_login(client, school) -> None:
    """Sign in as the seeded staff user used in unit tests."""
    client.get("/auth/google?portal=staff")
    client.get(
        "/auth/google/callback?email=shawnmckenzie11.sm@gmail.com&name=Shawn"
    )
    user = school.get_user_by_email("shawnmckenzie11.sm@gmail.com")
    if not user:
        raise RuntimeError("staff user missing after mock login")
    client.post("/verify-email", data={"code": user["verification_code"]})


def _seed_staff_class(school) -> int:
    """Ensure one staff-owned class exists and return its id."""
    school.activate_from_semester_json()
    shawn = school.get_user_by_email("shawnmckenzie11.sm@gmail.com")
    if not shawn:
        shawn = school.register_staff("shawnmckenzie11.sm@gmail.com", "Shawn")
        shawn = school.mark_verified(int(shawn["id"]))
    teacher_id = int(shawn["id"])
    offering = school.assign_course(
        teacher_user_id=teacher_id,
        ontario_code="MCF3M",
    )
    semester = school.get_semester(int(offering["semester_id"]))
    created = school.game.create_class(
        year=str(semester["year_display"]),
        semester=str(semester["term"]),
        course_code=str(offering["ontario_code"]),
        days_preset="M/W/F",
        time_label="2:00pm",
        codenames=["Maple"],
        offering_id=int(offering["id"]),
        teacher_user_id=teacher_id,
    )
    return int(created["id"])


def smoke_urls_for_templates(rel_templates: list[str], class_id: int) -> list[str]:
    """Map changed templates to staff URLs that render them."""
    urls: set[str] = set()
    for path in rel_templates:
        rel = path.removeprefix("lms/templates/")
        if rel.startswith("staff/"):
            urls.add("/staff/")
            if rel == "staff/course.html" or rel.startswith("staff/_"):
                urls.add(f"/staff/course/{class_id}?tab=live")
                urls.add(f"/staff/course/{class_id}?tab=ap&view=attendance")
        if rel.startswith("student/"):
            urls.add("/student/")
    if not urls and rel_templates:
        urls.add("/staff/")
    return sorted(urls)


def run_smoke(base: str, head: str) -> list[str]:
    """Return error lines; empty when every smoke GET returns 200."""
    templates = _changed_templates(base, head)
    if not templates:
        return []

    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    app = create_app(
        db_path=root / "lloves.sqlite",
        data_dir=root,
        testing=True,
    )
    school = app.config["SCHOOL_DB"]
    client = app.test_client()
    errors: list[str] = []
    try:
        _staff_login(client, school)
        class_id = _seed_staff_class(school)
        for url in smoke_urls_for_templates(templates, class_id):
            response = client.get(url, follow_redirects=True)
            if response.status_code != 200:
                errors.append(
                    f"template smoke GET {url} returned {response.status_code}"
                )
    finally:
        school.close()
        tmp.cleanup()
    return errors


def main(argv: list[str] | None = None) -> int:
    """CLI entry: ``copy_only_template_smoke.py <base> <head>``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", help="Base git ref")
    parser.add_argument("head", help="Head git ref")
    args = parser.parse_args(argv)
    try:
        errors = run_smoke(args.base, args.head)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 2
    if errors:
        for line in errors:
            print(line, file=sys.stderr)
        return 1
    print("template smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

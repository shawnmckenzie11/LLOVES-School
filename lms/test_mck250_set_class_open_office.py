#!/usr/bin/env python3
"""MCK-250: Set Class defaults, open-office labels, template-copy exclusion."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from lesson_slides_table import build_lesson_slides_table  # noqa: E402
from live_class_metadata import write_live_lesson_playlist  # noqa: E402
from live_teacher_state import normalize_live_slot  # noqa: E402
from set_class import (  # noqa: E402
    copy_course_live_template,
    next_class_after_last_run,
    next_open_office_slot,
    open_office_label,
)

OFFICE_NOTE = (
    "Open office sessions record attendance and slides, "
    "but they aren't copied into the course template."
)


class SetClassOpenOfficeTests(unittest.TestCase):
    """Next-class default, O-label numbering, and template copy exclusion."""

    def setUp(self) -> None:
        """Isolated app with one MCF3M class."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.client.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Aspen"],
            },
        )
        self.class_id = int(created.get_json()["class"]["id"])

    def tearDown(self) -> None:
        """Close the throwaway database."""
        self.school.close()
        self.tmp.cleanup()

    def _record_run(self, module: str, slot: str, ended_at: str) -> None:
        """Insert one Lesson Slides latest-run row."""
        self.school.conn.execute(
            """
            INSERT INTO lesson_slides_latest (
                class_id, live_module, live_slot, run_key, ended_at,
                questions_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, '[]', ?)
            """,
            (
                self.class_id,
                module,
                slot,
                f"{module}{slot}-{ended_at}",
                ended_at,
                ended_at,
            ),
        )
        self.school.conn.commit()

    def test_default_selection_rules(self) -> None:
        """No run starts at the first class; after the last class, stay there."""
        catalogue = [("M1", "C1"), ("M1", "C2"), ("M8", "C4")]
        self.assertEqual(next_class_after_last_run(catalogue, None), ("M1", "C1"))
        self.assertEqual(
            next_class_after_last_run(catalogue, ("M1", "C1")), ("M1", "C2")
        )
        self.assertEqual(
            next_class_after_last_run(catalogue, ("M8", "C4")), ("M8", "C4")
        )
        self.assertEqual(next_class_after_last_run(catalogue, ("M3", "O1")), ("M1", "C1"))

    def test_picker_uses_last_template_run(self) -> None:
        """Set Class lands on the template class after the last real run."""
        fresh = self.school.set_class_picker(self.class_id)
        self.assertEqual((fresh["module"], fresh["slot"]), ("M1", "C1"))
        self.assertEqual(fresh["label"], "M1 · C1")
        self.assertEqual(fresh["open_office_next"]["M1"], "O1")
        self._record_run("M1", "C1", "2026-10-12T20:00:00")
        nxt = self.school.set_class_picker(self.class_id)
        self.assertEqual((nxt["module"], nxt["slot"]), ("M1", "C2"))
        self.assertEqual(nxt["label"], "M1 · C2")
        self._record_run("M8", "C4", "2026-10-14T20:00:00")
        stayed = self.school.set_class_picker(self.class_id)
        self.assertEqual((stayed["module"], stayed["slot"]), ("M8", "C4"))

    def test_open_office_numbering(self) -> None:
        """Open office is O1, then O2, inside one module, stored as M3 O1."""
        self.assertEqual(next_open_office_slot([]), "O1")
        self.assertEqual(next_open_office_slot(["C1", "C2", "o1"]), "O2")
        self.assertEqual(next_open_office_slot(["O1", "O2"]), "O3")
        self.assertEqual(open_office_label("m3", "o1"), "M3 O1")
        self.assertEqual(normalize_live_slot("o2"), "O2")
        self.assertEqual(normalize_live_slot("O1"), "O1")
        self.assertEqual(normalize_live_slot(""), "C1")
        self._record_run("M3", "O1", "2026-10-15T18:00:00")
        picker = self.school.set_class_picker(self.class_id)
        self.assertEqual(picker["open_office_next"]["M3"], "O2")
        self.assertEqual(picker["open_office_next"]["M1"], "O1")
        self.assertEqual((picker["module"], picker["slot"]), ("M1", "C1"))

    def test_template_copy_skips_open_office(self) -> None:
        """Copying a course template leaves O-slot files behind."""
        src = Path(self.tmp.name) / "src"
        (src / "M3").mkdir(parents=True)
        (src / "M3" / "C1.json").write_text("{}\n", encoding="utf-8")
        (src / "M3" / "O1.json").write_text("{}\n", encoding="utf-8")
        (src / "M3" / "notes.txt").write_text("skip\n", encoding="utf-8")
        dest = Path(self.tmp.name) / "dest"
        copied = copy_course_live_template(src, dest)
        self.assertEqual(copied, ["M3/C1.json"])
        self.assertTrue((dest / "M3" / "C1.json").is_file())
        self.assertFalse((dest / "M3" / "O1.json").exists())
        with self.assertRaises(ValueError):
            write_live_lesson_playlist(
                "MCF3M",
                "M3",
                "O1",
                {},
                root=Path(self.tmp.name) / "root",
            )

    def test_lesson_slides_marks_open_office_not_in_template(self) -> None:
        """An O-slot run is listed with the muted Not in template status."""
        self._record_run("M1", "C1", "2026-10-12T20:00:00")
        self._record_run("M3", "O1", "2026-10-15T18:00:00")
        payload = build_lesson_slides_table(self.school, self.class_id)
        rows = {(row["module"], row["slot"]): row for row in payload["rows"]}
        delivered = rows[("M1", "C1")]
        nxt = rows[("M1", "C2")]
        office = rows[("M3", "O1")]
        self.assertEqual(delivered["status_label"], "Delivered")
        self.assertEqual(delivered["day_label"], "Mon Oct 12")
        self.assertFalse(delivered["not_in_template"])
        self.assertEqual(nxt["status_label"], "Next")
        self.assertEqual(office["line_label"], "M3 O1 · Open office")
        self.assertEqual(office["office_code"], "M3 O1")
        self.assertTrue(office["not_in_template"])
        self.assertEqual(office["status"], "not_in_template")
        self.assertEqual(office["status_label"], "Not in template")
        self.assertEqual(office["day_label"], "Thu Oct 15")
        page = self.client.get(
            f"/api/classes/{self.class_id}/lesson-slides/table"
        )
        self.assertEqual(page.status_code, 200)
        body = page.get_json()
        office_row = next(
            row for row in body["rows"] if row["slot"] == "O1"
        )
        self.assertEqual(office_row["status_label"], "Not in template")

    def test_set_class_page_has_toggle_and_approved_note(self) -> None:
        """Run Live Class shows the session-type toggle and the approved note."""
        self._record_run("M1", "C1", "2026-10-12T20:00:00")
        html = self.client.get(
            f"/staff/class/{self.class_id}?tab=live"
        ).get_data(as_text=True)
        self.assertIn('id="set-class-kind-live"', html)
        self.assertIn('id="set-class-kind-office"', html)
        self.assertIn(">Live class<", html)
        self.assertIn(">Open office<", html)
        self.assertIn(OFFICE_NOTE, html)
        self.assertIn('data-next-module="M1"', html)
        self.assertIn('data-next-slot="C2"', html)
        self.assertIn("M1 · C2", html)
        self.assertIn('id="set-class-slides"', html)
        js = (LMS_DIR / "static" / "staff_lesson_slides.js").read_text(encoding="utf-8")
        self.assertIn("ls-not-in-template", js)
        self.assertIn("Not in template", js)


if __name__ == "__main__":
    unittest.main()

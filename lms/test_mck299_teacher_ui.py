#!/usr/bin/env python3
"""MCK-299: Open course, celebration titles, and the test-class deck prompt."""

from __future__ import annotations

import os
import re
import subprocess
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
from celebration import celebration_candidates  # noqa: E402

_OPEN_COURSE = re.compile(
    r'<a\s+class="course-quiet-link[^"]*"\s+href="([^"]+)"[^>]*>\s*Open course\s*</a>'
)
_CELEBRATE_SELECT = re.compile(
    r'<select id="celebrate-student".*?</select>',
    re.DOTALL,
)
_OPTION_TEXT = re.compile(r"<option\b[^>]*>(.*?)</option>", re.DOTALL)


def _option_labels(html: str) -> list[str]:
    """Visible labels in the Celebrate a student dropdown.

    Args:
        html: Staff home HTML.

    Returns:
        Stripped option texts, blank placeholder included.
    """
    match = _CELEBRATE_SELECT.search(html)
    if match is None:
        return []
    labels = []
    for raw in _OPTION_TEXT.findall(match.group(0)):
        labels.append(re.sub(r"\s+", " ", raw).strip())
    return labels


class OpenCourseLinkTests(unittest.TestCase):
    """Dashboard Open course opens Lesson Slides."""

    def setUp(self) -> None:
        """One MCF3M class for a signed-in teacher."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.client.post(
            "/verify-email",
            data={"code": self.school.get_user_by_email("teacher@gmail.com")["verification_code"]},
        )
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple"],
            },
        )
        self.assertEqual(created.status_code, 200, created.get_json())
        self.class_id = int(created.get_json()["class"]["id"])

    def tearDown(self) -> None:
        """Close the throwaway database."""
        self.school.close()
        self.tmp.cleanup()

    def test_open_course_links_to_lesson_slides(self) -> None:
        """Open course uses the Lesson Slides tab, not Run Live Class."""
        home = self.client.get("/staff").get_data(as_text=True)
        hrefs = _OPEN_COURSE.findall(home)
        self.assertTrue(hrefs, "Open course link missing")
        for href in hrefs:
            self.assertIn("tab=lesson-slides", href)
            self.assertNotIn("tab=live", href)
            self.assertIn(f"/staff/class/{self.class_id}", href)
        self.assertIn(
            f"/staff/class/{self.class_id}?tab=live&amp;run=1",
            home,
        )


class CelebrationCourseTitleTests(unittest.TestCase):
    """Student rows use the dashboard course title, not the stored index."""

    def setUp(self) -> None:
        """Two MCR3U sections; the second stored index is not its display rank."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        teacher_id = int(self.teacher["id"])
        first = self.school.assign_course(teacher_user_id=teacher_id, ontario_code="MCR3U")
        second = self.school.assign_course(
            teacher_user_id=teacher_id, ontario_code="MCR3U", new_section=True
        )
        with self.school._lock:
            self.school.conn.execute(
                "UPDATE course_offerings SET section_index = ? WHERE id = ?",
                (5, int(second["id"])),
            )
            self.school.conn.commit()
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.client.post(
            "/verify-email",
            data={"code": self.school.get_user_by_email("teacher@gmail.com")["verification_code"]},
        )
        self.first_id = self._populate(int(first["id"]), ["Cedar"])
        self.second_id = self._populate(int(second["id"]), ["Aspen"])
        listed = {
            int(row["id"]): str(row["section_code"])
            for row in self.school.list_staff_classes(teacher_id)
        }
        self.assertEqual(listed[self.second_id], "MCR3U-5")
        self.assertEqual(self.school.get_offering(int(second["id"]))["section_code"], "MCR3U-2")

    def tearDown(self) -> None:
        """Close the throwaway database."""
        self.school.close()
        self.tmp.cleanup()

    def _populate(self, offering_id: int, codenames: list[str]) -> int:
        """Create one class and return its id.

        Args:
            offering_id: Course offering to populate.
            codenames: Fake roster names.

        Returns:
            New class id.
        """
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": offering_id,
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": codenames,
            },
        )
        self.assertEqual(created.status_code, 200, created.get_json())
        return int(created.get_json()["class"]["id"])

    def test_dropdown_matches_the_dashboard_title(self) -> None:
        """Each student label uses the card title, including a gapped index."""
        rows = {
            str(row["name"]): str(row["course"])
            for row in celebration_candidates(self.school, int(self.teacher["id"]))
        }
        self.assertEqual(rows["Cedar"], "MCR3U")
        self.assertEqual(rows["Aspen"], "MCR3U-2")
        self.assertNotIn("MCR3U-5", rows.values())

        home = self.client.get("/staff").get_data(as_text=True)
        titles = re.findall(r'<h2 class="course-card-code">([^<]+)</h2>', home)
        self.assertEqual(titles, ["MCR3U", "MCR3U-2"])
        labels = _option_labels(home)
        self.assertIn("Cedar · MCR3U", labels)
        self.assertIn("Aspen · MCR3U-2", labels)
        self.assertFalse(any("MCR3U-5" in label for label in labels))


class TestClassDeckPromptTests(unittest.TestCase):
    """Test class asks for a deck the same way a live class does."""

    def test_live_and_test_prompt_office_does_not(self) -> None:
        """``setClassPromptsForDeck`` is true for live and test, false for office."""
        script = r"""
import { setClassPromptsForDeck } from "./static/set_class_mode.js";

const failed = [];
function check(cond, message) {
  if (!cond) failed.push(message);
}
check(setClassPromptsForDeck("live") === true, "live");
check(setClassPromptsForDeck("test") === true, "test");
check(setClassPromptsForDeck("office") === false, "office");
if (failed.length) {
  console.error(failed.join("\n"));
  process.exit(1);
}
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=LMS_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        sync_at = js.index("function syncSetClassRunKindUi")
        sync = js[sync_at : js.index("function validateLogContext", sync_at)]
        self.assertIn("setClassPromptsForDeck", sync)
        self.assertNotIn("if (test) deckSeed.setAttribute", sync)
        self.assertIn('deckSeed.removeAttribute("hidden")', sync)

        seed_at = js.index("async function applyDeckSeedChoice")
        seed = js[seed_at : js.index('$("live-deck-seed")?.addEventListener', seed_at)]
        self.assertIn('picked !== "previous" && picked !== "course"', seed)
        self.assertIn('body: JSON.stringify({ mode: "test_copy" })', seed)
        self.assertIn("body.source_module = sourceModule;", seed)

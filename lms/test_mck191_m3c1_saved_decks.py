#!/usr/bin/env python3
"""MCK-191 reopened: Real Slice in MCR3U M3 C1 for decks saved before #268.

#268 added ``course/MCR3U/media/real-slice`` to the M3 C1 seed file, but a
class deck that is a working copy (Previous, Course deck, Blank, or a copy
from the other section) never reads the seed file's items again, so prod's
existing MCR3U and MCR3U-2 M3 C1 decks did not show it. Pinned seed media
now rides along on those decks: once, never duplicated, MCR3U M3 C1 only.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402

REAL_SLICE = "/static/live-media/m1c1-c1-real-slice.html"


class SavedM3C1DeckTests(unittest.TestCase):
    """Two MCR3U sections with saved M3 C1 working copies, plus an MCF3M class."""

    def setUp(self) -> None:
        """Teacher with MCR3U and MCR3U-2 sections and one MCF3M class."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("t@gmail.com")
        self.sections = [self._class("MCR3U", "Ana"), self._class("MCR3U", "Ben", new_section=True)]
        self.math = self._class("MCF3M", "Cyd")

    def tearDown(self) -> None:
        """Close sqlite and remove the temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _class(self, code: str, name: str, new_section: bool = False) -> int:
        """Create one class on a new offering (a second MCR3U is MCR3U-2)."""
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code=code, new_section=new_section
        )
        created = self.school.game.create_class(
            year="2026/27", semester="Semester 1", course_code=code,
            days_preset="M/W/F", time_label="2:00pm", codenames=[name],
            offering_id=int(offering["id"]), teacher_user_id=int(self.teacher["id"]),
        )
        return int(created["id"])

    def _media(self, class_id: int, module: str = "M3", slot: str = "C1") -> list[str]:
        """Media URLs listed on one class deck."""
        meta = self.school.live_class_metadata_for_class_lesson(class_id, module, slot, fresh=True)
        return [
            str(row.get("file") or row.get("url") or "")
            for row in meta.get("items") or []
            if str(row.get("item_type") or "").lower() == "media"
        ]

    def test_sections_are_mcr3u_and_mcr3u_2(self) -> None:
        """The fixture really is the two prod sections."""
        codes = [self.school._section_code_for_class(cid) for cid in self.sections]
        self.assertEqual(codes, ["MCR3U", "MCR3U-2"])

    def test_saved_working_copies_in_both_sections_list_real_slice_once(self) -> None:
        """Course-deck copy (section 1), cross-section copy and Blank (section 2)."""
        one, two = self.sections
        self.school.apply_class_deck_seed(one, "M3", "C1", mode="course", source_module="M2", source_slot="C1")
        self.assertTrue(self.school.get_class_live_deck_seed(one, "M3", "C1")["suppress_authored"])
        self.assertEqual(self._media(one).count(REAL_SLICE), 1)
        self.school.apply_class_deck_seed(
            two, "M3", "C1", mode="course", source_module="M3", source_slot="C1",
            source_class_id=one, teacher_user_id=int(self.teacher["id"]),
        )
        self.assertEqual(self._media(two).count(REAL_SLICE), 1)
        self.school.apply_class_deck_seed(two, "M3", "C1", mode="blank")
        self.assertEqual(self._media(two).count(REAL_SLICE), 1)
        # Reading again (and the cached read) never duplicates it.
        for _ in range(3):
            self.assertEqual(self._media(one).count(REAL_SLICE), 1)
            meta = self.school.live_class_metadata_for_class_lesson(two, "M3", "C1", fresh=False)
            self.assertEqual(
                [r.get("file") for r in meta["items"] if r.get("item_type") == "media"].count(REAL_SLICE), 1
            )

    def test_live_session_keeps_real_slice_on_a_saved_deck(self) -> None:
        """On a working-copy deck the mounted Real Slice is deck media, not a leftover."""
        one = self.sections[0]
        self.school.apply_class_deck_seed(one, "M3", "C1", mode="blank")
        session = self.school.start_live_class_session(one, int(self.teacher["id"]), live_module="M3", live_slot="C1")
        sid = int(session["id"])
        self.assertIn(REAL_SLICE, self.school._class_deck_media_choice(sid)[1])
        self.school.set_live_session_active_media(sid, url=REAL_SLICE, title="Real Slice", stem="Real Slice")
        self.assertEqual((self.school.ensure_live_class_media(sid) or {}).get("url"), REAL_SLICE)

    def test_other_courses_and_challenges_are_untouched(self) -> None:
        """MCF3M M3 C1 and MCR3U M3 C2 working copies get nothing added."""
        self.school.apply_class_deck_seed(self.math, "M3", "C1", mode="blank")
        self.assertNotIn(REAL_SLICE, self._media(self.math))
        self.school.apply_class_deck_seed(self.sections[0], "M3", "C2", mode="blank")
        self.assertNotIn(REAL_SLICE, self._media(self.sections[0], "M3", "C2"))


if __name__ == "__main__":
    unittest.main()

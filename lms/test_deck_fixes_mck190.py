#!/usr/bin/env python3
"""MCK-190: deck fixes.

1. Add New imports a question from another module's confirmed bank in the
   same course (it used to fail "question N is not in confirmed banks for M3").
2. MCR3U M1 C1 lists Real Slice again, and a mounted Real Slice is not
   swapped back to the square-root seed by the next active-media read.
3. Use current keeps the class's own deck: a working copy's media is not
   replaced by the course seed when the live class opens.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

import test_module_bank_import as base  # noqa: E402
from live_class_metadata import load_live_class_metadata  # noqa: E402

REAL_SLICE = "/static/live-media/m1c1-c1-real-slice.html"
SQRT = "/static/live-media/mcr3u-m1c1-sqrt.html"
C2_MEDIA = "/static/live-media/mcr3u-m1c2-parent-transformations.html"
C3_MEDIA = "/static/live-media/mcr3u-m1c3-parent-transformations.html"


class DeckFixesTests(unittest.TestCase):
    """MCR3U class with an attached library, M1 bank confirmed, live session on M1 C1."""

    setUp = base.ModuleBankImportApiTests.setUp
    tearDown = base.ModuleBankImportApiTests.tearDown
    _login = base.ModuleBankImportApiTests._login

    def _import(self, question_id: int, module: str = "M1", slot: str = "C2"):
        return self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/{module}/{slot}/import-mc",
            json={"question_id": question_id, "page_number": 4, "stage": "round"},
        )

    # 1. Add New across modules
    def test_add_new_imports_from_another_modules_confirmed_bank(self) -> None:
        self.school.confirm_module_bank_links(self.library_id, 2, [self.m2_bank])
        rv = self._import(self.m2_q, "M1", "C2")
        self.assertEqual(rv.status_code, 200, rv.get_json())
        ids = [row["item_id"] for row in self.school.list_class_playlist_placements(self.class_id, "M1", "C2")]
        self.assertEqual(ids, [f"bank-import-{self.m2_q}"])

    def test_add_new_still_refuses_unconfirmed_and_other_course_banks(self) -> None:
        # The M2 bank is in this library but confirmed for no module.
        self.assertEqual(self._import(self.m2_q).status_code, 404)
        other = self.school.create_library("MCF3M", origin="upload")
        other_bank = base._insert_bank(self.school, int(other["id"]), title="Unit 1 Quiz", import_key="bank:other")
        other_q = base._insert_mc_question(self.school, other_bank, import_key="q-other", title="Other", stem="Other course?")
        self.school.confirm_module_bank_links(int(other["id"]), 1, [other_bank])
        self.assertEqual(self._import(other_q).status_code, 404)
        self.assertEqual(self.school.list_class_playlist_placements(self.class_id, "M1", "C2"), [])

    # 2. Real Slice in MCR3U M1 C1
    def test_mcr3u_m1c1_deck_lists_real_slice_after_the_square_root_page(self) -> None:
        meta = load_live_class_metadata("MCR3U", "M1", "C1")
        media = [row.get("file") for row in meta["items"] if row.get("item_type") == "media"]
        self.assertEqual(media, [SQRT, REAL_SLICE])
        self.assertEqual(meta["media"]["file"], SQRT)
        class_meta = self.school.live_class_metadata_for_class_lesson(self.class_id, "M1", "C1")
        self.assertIn(REAL_SLICE, [row.get("file") for row in class_meta["items"]])

    def test_mounted_real_slice_is_not_swapped_back_to_the_seed(self) -> None:
        self.assertEqual((self.school.ensure_live_class_media(self.session_id) or {}).get("url"), SQRT)
        self.school.set_live_session_active_media(
            self.session_id, url=REAL_SLICE, title="Real Slice", stem="Real Slice"
        )
        kept = self.school.ensure_live_class_media(self.session_id) or {}
        self.assertEqual(kept.get("url"), REAL_SLICE)
        rv = self.client.get(f"/api/live-sessions/{self.session_id}/active-media")
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(self.school.live_session_active_media_payload(self.session_id)["url"], REAL_SLICE)
        # A leftover that is not on this deck is still replaced by the seed.
        self.school.set_live_session_active_media(self.session_id, url=C2_MEDIA, title="C2", stem="C2")
        self.assertEqual((self.school.ensure_live_class_media(self.session_id) or {}).get("url"), SQRT)

    # 3. Use current keeps the class's deck
    def test_use_current_keeps_a_working_copys_media(self) -> None:
        self.school.apply_class_deck_seed(
            self.class_id, "M1", "C1", mode="course", source_module="M1", source_slot="C3", replace=True
        )
        deck = self.school.live_class_metadata_for_class_lesson(self.class_id, "M1", "C1")
        self.assertEqual(deck["media"]["file"], C3_MEDIA)
        before = self.school.class_deck_revision(self.class_id, "M1", "C1")
        rv = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C1/deck-seed", json={"mode": "current"}
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        self.assertEqual(self.school.class_deck_revision(self.class_id, "M1", "C1"), before)
        mounted = self.school.ensure_live_class_media(self.session_id) or {}
        self.assertEqual(mounted.get("url"), C3_MEDIA)
        self.client.get(f"/api/live-sessions/{self.session_id}/active-media")
        self.assertEqual(self.school.live_session_active_media_payload(self.session_id)["url"], C3_MEDIA)
        self.assertEqual(
            self.school.live_class_metadata_for_session(self.session_id)["media"]["file"], C3_MEDIA
        )


if __name__ == "__main__":
    unittest.main()

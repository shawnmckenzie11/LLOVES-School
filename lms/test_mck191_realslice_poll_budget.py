#!/usr/bin/env python3
"""MCK-191.

1. MCR3U M3 C1 lists Real Slice, the way MCK-190 restored it on M1 C1.
2. LLOVES-LMS-4: ``/student/home`` builds only the page shell. Its first
   ``/api/student/state`` poll paints the rest, so a page load no longer
   runs the full live snapshot twice, and a spent poll budget renders the
   waiting shell as a handled degrade (a warning, not an error event).
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

import serve_capacity as cap  # noqa: E402
import test_module_bank_import as base  # noqa: E402
import test_poll_capacity as polls  # noqa: E402
from live_class_metadata import load_live_class_metadata  # noqa: E402

REAL_SLICE = "/static/live-media/m1c1-c1-real-slice.html"


class RealSliceM3C1Tests(unittest.TestCase):
    """MCR3U class with an attached library (MCK-190 fixture)."""

    setUp = base.ModuleBankImportApiTests.setUp
    tearDown = base.ModuleBankImportApiTests.tearDown
    _login = base.ModuleBankImportApiTests._login

    def test_mcr3u_m3c1_deck_lists_real_slice(self) -> None:
        meta = load_live_class_metadata("MCR3U", "M3", "C1")
        media = [row for row in meta["items"] if row.get("item_type") == "media"]
        self.assertEqual([row.get("file") for row in media], [REAL_SLICE])
        self.assertEqual(int(media[0].get("page_number") or 0), 5)
        class_meta = self.school.live_class_metadata_for_class_lesson(self.class_id, "M3", "C1")
        self.assertIn(REAL_SLICE, [row.get("file") for row in class_meta["items"]])
        # M1 C1 (MCK-190) is unchanged.
        m1 = load_live_class_metadata("MCR3U", "M1", "C1")
        self.assertIn(REAL_SLICE, [row.get("file") for row in m1["items"]])


class StudentHomeShellTests(unittest.TestCase):
    """A joined student on a live session (poll-capacity fixture)."""

    setUp = polls.LivePollHardenTests.setUp
    tearDown = polls.LivePollHardenTests.tearDown

    def test_home_page_skips_the_heavy_slices(self) -> None:
        heavy = (
            "student_live_class_metadata_for_session",
            "apply_student_live_group_projection",
        )
        with mock.patch.multiple(
            type(self.school),
            **{name: mock.DEFAULT for name in heavy},
        ) as mocks:
            rv = self.student.get("/student/home")
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True)[:300])
        for name in heavy:
            self.assertFalse(mocks[name].called, name)
        # The poll still sends the full snapshot.
        state = self.student.get("/api/student/state").get_json()
        self.assertIn("live_metadata", state)
        self.assertIn("teacher_state", state)

    def test_home_page_over_budget_is_a_warning_not_an_error(self) -> None:
        cap._poll_budget.deadline = time.monotonic() - 1
        try:
            with self.assertNoLogs("app", level="ERROR"):
                with self.assertLogs("app", level="WARNING") as logs:
                    rv = self.student.get("/student/home")
        finally:
            cap._poll_budget.deadline = None
        text = rv.get_data(as_text=True)
        self.assertEqual(rv.status_code, 200, text[:300])
        self.assertIn("is-waiting-room", text)
        self.assertTrue(any("over poll budget" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()

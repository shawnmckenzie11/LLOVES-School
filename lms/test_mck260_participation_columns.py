#!/usr/bin/env python3
"""MCK-260: participation column labels, finished modules, no M1 placeholders."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from participation_bands import (  # noqa: E402
    build_participation_bands,
    format_participation_grouped_header,
)
from participation_exclusion import format_participation_session_header  # noqa: E402
from test_participation_mck198 import Base  # noqa: E402


class LabelTests(unittest.TestCase):
    """Stored module tokens are shown. Blank modules are not renamed M1."""

    def test_grouped_header_keeps_m2_and_does_not_invent_m1(self) -> None:
        """M2 stays M2. An empty module is not the M1 C1 placeholder."""
        self.assertEqual(
            format_participation_grouped_header("M2", "C1", "2026-10-05T16:00:00"),
            "M2 · C1 · Mon Oct 5",
        )
        self.assertEqual(format_participation_grouped_header("", "C1", None), "")
        self.assertEqual(format_participation_grouped_header(None, None, None), "")
        self.assertNotIn("M1", format_participation_grouped_header("M3", "", None))

    def test_session_header_does_not_default_module_or_slot(self) -> None:
        """The compact header uses the stored module and does not fill C1."""
        self.assertEqual(format_participation_session_header("", "", "2026-10-05T16:00:00"), "")
        self.assertEqual(format_participation_session_header("M2", "", "not-a-date"), "M2 · —")
        self.assertTrue(
            format_participation_session_header("M2", "C1", "2026-10-05T16:00:00").startswith("M2C1")
        )

    def test_placeholder_columns_do_not_follow_the_current_module(self) -> None:
        """Sessions with no stored module are dropped, not shown as M1 C1."""
        columns = [
            {"id": 1, "kind": "session"},
            {"id": 2, "kind": "session"},
            {"id": 3, "kind": "session"},
            {"id": 4, "kind": "session"},
            {"id": 5, "kind": "session"},
        ]
        meta = {
            "1": {
                "live_module": "M2",
                "live_slot": "C1",
                "ended_at": "2026-10-05T16:00:00",
                "class_ended": True,
            },
            "2": {
                "live_module": "M2",
                "live_slot": "C2",
                "ended_at": "2026-10-07T16:00:00",
                "class_ended": True,
            },
            "3": {
                "live_module": "M3",
                "live_slot": "C1",
                "ended_at": "2026-10-12T16:00:00",
                "class_ended": True,
            },
        }
        built = build_participation_bands(columns, meta, {})
        self.assertEqual(built["current_module"], "M3")
        roles = {band["module"]: band["role"] for band in built["bands"]}
        self.assertEqual(roles, {"M2": "finished", "M3": "current"})
        labels = [col["label"] for band in built["bands"] for col in band["columns"]]
        self.assertEqual(labels[0], "M2 · C1 · Mon Oct 5")
        self.assertTrue(all(label.startswith("M2") or label.startswith("M3") for label in labels))
        self.assertNotIn("M1", " ".join(labels))

    def test_earlier_module_stays_open_until_its_classes_end(self) -> None:
        """A class still in progress does not collapse the module."""
        columns = [{"id": 1, "kind": "session"}, {"id": 2, "kind": "session"}]
        meta = {
            "1": {
                "live_module": "M2",
                "live_slot": "C1",
                "ended_at": "2026-10-05T16:00:00",
                "class_ended": False,
            },
            "2": {
                "live_module": "M3",
                "live_slot": "C1",
                "ended_at": "2026-10-12T16:00:00",
                "class_ended": True,
            },
        }
        built = build_participation_bands(columns, meta, {})
        roles = {band["module"]: band["role"] for band in built["bands"]}
        self.assertEqual(roles["M2"], "open")
        self.assertEqual(roles["M3"], "current")

    def test_marked_complete_module_stays_finished(self) -> None:
        """The stored complete flag still collapses the module."""
        columns = [{"id": 1, "kind": "session"}, {"id": 2, "kind": "session"}]
        meta = {
            "1": {"live_module": "M2", "live_slot": "C1", "ended_at": "2026-10-05T16:00:00"},
            "2": {"live_module": "M3", "live_slot": "C1", "ended_at": "2026-10-12T16:00:00"},
        }
        built = build_participation_bands(columns, meta, {"M2": True, "M3": False})
        roles = {band["module"]: band["role"] for band in built["bands"]}
        self.assertEqual(roles, {"M2": "finished", "M3": "current"})


class GridModuleTests(Base):
    """The participation grid reads the stored module and does not write marks."""

    def _wire(self, run_key: str, answered: int) -> None:
        """Attach a participation column for one live run."""
        student = self.students[0]
        credits = {student: answered} if answered else {}
        self.school.game.persist_end_class_column(
            self.class_id,
            [student],
            credits,
            include_attendance=True,
            include_participation=bool(credits),
            run_key=run_key,
            create_game=True,
        )

    def test_blank_item_does_not_relabel_m2_and_placeholders_stay_off_the_table(self) -> None:
        """A blank participation row does not win over the stored M2 module.

        An extra game column with no module is not painted as M1 C1 after M3.
        Showing M2 finished does not insert a module mark.
        """
        m2 = self.run_class(
            "M2", "C1", 2, 0, {self.students[0]: 1},
            ended_iso="2026-10-05T16:00:00", wipe=False,
        )
        m3 = self.run_class(
            "M3", "C1", 1, 0, {self.students[0]: 1},
            ended_iso="2026-10-12T16:00:00", wipe=False,
        )
        self.school.conn.execute(
            """
            INSERT INTO participation_items (
                class_id, run_key, live_module, live_slot, question_key,
                item_json, offered_at
            ) VALUES (?, ?, '', '', 'blank-row', '{}', '2026-10-05T15:00:00')
            """,
            (self.class_id, m2),
        )
        self.school.conn.commit()
        self._wire(m2, 1)
        self._wire(m3, 1)
        self.school.game.add_session_column(self.class_id, date(2026, 11, 2), "2:00pm")
        before = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM participation_module_marks"
        ).fetchone()["n"]

        meta = self.school.participation_session_meta(self.class_id)
        modules = {str(row.get("live_module") or "") for row in meta.values()}
        self.assertIn("M2", modules)
        self.assertIn("M3", modules)
        self.assertNotIn("M1", modules)
        m2_headers = [
            str(row.get("header_label") or "")
            for row in meta.values()
            if str(row.get("live_module") or "") == "M2"
        ]
        self.assertTrue(m2_headers)
        self.assertTrue(all(label.startswith("M2") for label in m2_headers))

        grid = self.school.participation_week_grid(self.class_id)
        bands = grid["module_bands"]["bands"]
        roles = {band["module"]: band["role"] for band in bands}
        self.assertEqual(grid["module_bands"]["current_module"], "M3")
        self.assertEqual(roles.get("M2"), "finished")
        self.assertEqual(roles.get("M3"), "current")
        self.assertNotIn("M1", roles)
        labels = [col["label"] for band in bands for col in band["columns"]]
        self.assertTrue(labels)
        self.assertTrue(all(not label.startswith("M1") for label in labels))
        after = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM participation_module_marks"
        ).fetchone()["n"]
        self.assertEqual(int(after), int(before))
        self.assertFalse(self.school.module_complete(self.class_id, "M2"))


if __name__ == "__main__":
    unittest.main()

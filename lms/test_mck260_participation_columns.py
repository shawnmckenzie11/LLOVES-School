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
        """Sessions with no stored module stay visible as Untagged, not M1 C1."""
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
        self.assertEqual(roles["M2"], "finished")
        self.assertEqual(roles["M3"], "current")
        self.assertEqual(roles["Untagged"], "untagged")
        untagged = next(band for band in built["bands"] if band["module"] == "Untagged")
        self.assertEqual({col["id"] for col in untagged["columns"]}, {4, 5})
        self.assertTrue(all(col["label"] == "Untagged" for col in untagged["columns"]))
        labels = [col["label"] for band in built["bands"] for col in band["columns"]]
        self.assertEqual(labels[0], "M2 · C1 · Mon Oct 5")
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

        An extra game column with no module stays on the table. Its label
        comes from the class calendar, not an M1 C1 fallback, and the
        stored module text is not rewritten.
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
        added = self.school.game.add_session_column(self.class_id, date(2026, 9, 14), "2:00pm")
        added_id = max(
            int(column["id"])
            for column in added["columns"]
            if column.get("kind") == "session"
        )
        self.school.game.conn.execute(
            "UPDATE session_scores SET points = 5 WHERE session_id = ? AND student_id = ?",
            (added_id, self.students[0]),
        )
        self.school.game.conn.commit()
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
        labels = [col["label"] for band in bands for col in band["columns"]]
        self.assertTrue(labels)
        m1 = next(band for band in bands if band["module"] == "M1")
        self.assertEqual(m1["role"], "finished")
        self.assertEqual(m1["columns"][0]["slot"], "C1")
        self.assertEqual(m1["columns"][0]["id"], added_id)
        self.assertEqual(grid["cells"][f"{added_id}:{self.students[0]}"]["points"], 5)
        self.assertFalse(any(str(col["label"]).startswith("M1 · C1") and col["id"] != added_id for band in bands for col in band["columns"]))
        blank = self.school.conn.execute(
            """
            SELECT live_module FROM participation_items
            WHERE class_id = ? AND question_key = 'blank-row'
            """,
            (self.class_id,),
        ).fetchone()
        self.assertEqual(str(blank["live_module"] or ""), "")
        after = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM participation_module_marks"
        ).fetchone()["n"]
        self.assertEqual(int(after), int(before))
        self.assertFalse(self.school.module_complete(self.class_id, "M2"))


class ScheduleMapTests(unittest.TestCase):
    """Meeting dates follow the Mon/Wed/Fri course schedule."""

    def test_september_module_one_includes_two_slots_on_sept_23(self) -> None:
        """M1 C4 and M1 O2 share 23 Sep. O1 is the Friday before, when Friday meets."""
        from participation_schedule import participation_date_slots

        mapped = participation_date_slots("M/W/F", include_office=True)
        self.assertEqual(mapped[date(2026, 9, 14)], [("M1", "C1")])
        self.assertEqual(mapped[date(2026, 9, 16)], [("M1", "C2")])
        self.assertEqual(mapped[date(2026, 9, 18)], [("M1", "O1")])
        self.assertEqual(mapped[date(2026, 9, 21)], [("M1", "C3")])
        self.assertEqual(
            mapped[date(2026, 9, 23)],
            [("M1", "C4"), ("M1", "O2")],
        )
        self.assertNotIn(date(2026, 10, 12), mapped)
        self.assertEqual(mapped[date(2026, 10, 14)], [("M3", "C2")])
        self.assertEqual(mapped[date(2026, 10, 19)], [("M3", "C3")])
        self.assertNotIn(("M3", "C1"), [pair for pairs in mapped.values() for pair in pairs])
        self.assertEqual(mapped[date(2027, 1, 4)], [("M8", "C1")])

    def test_without_friday_office_sept_23_is_only_c4(self) -> None:
        """A course that does not meet Friday does not invent O1 or O2."""
        from participation_schedule import participation_date_slots

        mapped = participation_date_slots("M/W/F", include_office=False)
        self.assertNotIn(date(2026, 9, 18), mapped)
        self.assertEqual(mapped[date(2026, 9, 23)], [("M1", "C4")])
        self.assertEqual(mapped[date(2026, 9, 14)], [("M1", "C1")])
        self.assertEqual(mapped[date(2026, 9, 21)], [("M1", "C3")])

    def test_two_sessions_on_sept_23_take_c4_then_o2(self) -> None:
        """The earlier session is C4 and the later one is O2. Neither is stored."""
        from participation_schedule import (
            label_sessions_from_schedule,
            participation_date_slots,
        )

        mapped = participation_date_slots("M/W/F", include_office=True)
        sessions = [
            {"id": 10, "starts_at": "2026-09-23T14:00:00", "status": "ended"},
            {"id": 11, "starts_at": "2026-09-23T18:00:00", "status": "ended"},
            {"id": 12, "starts_at": "2026-09-11T14:00:00", "status": "ended"},
        ]
        labelled = label_sessions_from_schedule(sessions, {}, mapped, today=date(2026, 10, 16))
        self.assertEqual(labelled["10"]["live_module"], "M1")
        self.assertEqual(labelled["10"]["live_slot"], "C4")
        self.assertEqual(labelled["11"]["live_module"], "M1")
        self.assertEqual(labelled["11"]["live_slot"], "O2")
        self.assertTrue(labelled["11"]["open_office"])
        self.assertEqual(labelled["12"]["live_module"], "Untagged")
        self.assertNotIn("M1", labelled["12"]["header_label"])
        self.assertTrue(labelled["12"]["header_label"].startswith("Untagged"))


class SameDayGridTests(Base):
    """Two untagged sessions on 23 Sep stay visible and are not written back."""

    def test_sept_23_columns_keep_points_and_stored_rows(self) -> None:
        """C4 and O2 are display labels. Points and participation rows stay put."""
        before_items = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM participation_items"
        ).fetchone()["n"]
        before_runs = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM participation_runs"
        ).fetchone()["n"]
        before_marks = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM participation_module_marks"
        ).fetchone()["n"]
        self.school.game.add_session_column(self.class_id, date(2026, 9, 23), "2:00pm")
        self.school.game.add_session_column(self.class_id, date(2026, 9, 23), "3:15pm")
        sept = [
            int(row["id"])
            for row in self.school.game.conn.execute(
                """
                SELECT id FROM sessions
                WHERE class_id = ? AND starts_at LIKE '2026-09-23%'
                ORDER BY starts_at, id
                """,
                (self.class_id,),
            )
        ]
        self.assertEqual(len(sept), 2)
        student = self.students[0]
        self.school.game.conn.execute(
            "UPDATE session_scores SET points = ? WHERE session_id = ? AND student_id = ?",
            (4, sept[0], student),
        )
        self.school.game.conn.execute(
            "UPDATE session_scores SET points = ? WHERE session_id = ? AND student_id = ?",
            (6, sept[1], student),
        )
        self.school.game.conn.commit()
        grid = self.school.participation_week_grid(self.class_id)
        bands = grid["module_bands"]["bands"]
        columns = [col for band in bands for col in band["columns"]]
        by_id = {int(col["id"]): col for col in columns}
        self.assertIn(sept[0], by_id)
        self.assertIn(sept[1], by_id)
        self.assertEqual(by_id[sept[0]]["module"], "M1")
        self.assertEqual(by_id[sept[0]]["slot"], "C4")
        self.assertEqual(by_id[sept[1]]["module"], "M1")
        self.assertEqual(by_id[sept[1]]["slot"], "O2")
        self.assertEqual(grid["cells"][f"{sept[0]}:{student}"]["points"], 4)
        self.assertEqual(grid["cells"][f"{sept[1]}:{student}"]["points"], 6)
        self.assertEqual(
            int(self.school.conn.execute("SELECT COUNT(*) AS n FROM participation_items").fetchone()["n"]),
            int(before_items),
        )
        self.assertEqual(
            int(self.school.conn.execute("SELECT COUNT(*) AS n FROM participation_runs").fetchone()["n"]),
            int(before_runs),
        )
        self.assertEqual(
            int(self.school.conn.execute("SELECT COUNT(*) AS n FROM participation_module_marks").fetchone()["n"]),
            int(before_marks),
        )
        self.assertFalse(any(str(col.get("label") or "").startswith("M1 · C1") for col in columns))


if __name__ == "__main__":
    unittest.main()

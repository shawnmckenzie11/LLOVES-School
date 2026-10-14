#!/usr/bin/env python3
"""MCK-230: deck denominator, merged filters, finish module, session grid."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from local_dev_seed import LOCAL_DEV_SHAWN_EMAIL, seed_local_dev_school  # noqa: E402
from participation_weight import participation_fraction  # noqa: E402
from test_participation_mck198 import Base  # noqa: E402


class Mck230ParticipationUiTests(unittest.TestCase):
    """Participation tab chrome and grid payload."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env = mock.patch.dict(
            os.environ,
            {"LOCAL_DEV_LOGIN": "1", "FLASK_ENV": "development"},
            clear=False,
        )
        self.env.start()
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root)
        self.school = self.app.config["SCHOOL_DB"]
        first = seed_local_dev_school(self.school)
        self.class_id = int(first["class_id"])
        self.client = self.app.test_client()
        self.client.get(
            f"/auth/google/callback?portal=staff&email={LOCAL_DEV_SHAWN_EMAIL}&name=Shawn",
            follow_redirects=False,
        )

    def tearDown(self) -> None:
        self.school.close()
        self.env.stop()
        self.tmp.cleanup()

    def test_participation_tab_has_merged_filters(self) -> None:
        html = self.client.get(
            f"/staff/class/{self.class_id}?tab=ap&view=participation"
        ).get_data(as_text=True)
        self.assertIn('id="ap-include-individual"', html)
        self.assertIn('id="ap-include-group"', html)
        self.assertIn('id="ap-display-mode-select"', html)
        self.assertNotIn('id="ap-data-mode-select"', html)
        self.assertNotIn('id="ap-group-slice-select"', html)

    def test_grid_returns_dashboard_columns_and_session_meta(self) -> None:
        body = self.client.get(
            f"/api/classes/{self.class_id}/participation-grid?sort=az"
        ).get_json()
        self.assertTrue(body.get("ok"))
        self.assertIn("columns", body)
        self.assertIn("session_meta", body)
        self.assertIn("individual_cells", body)
        self.assertNotIn("weeks", body)

    def test_deck_denominator_counts_mc_with_live_responses_only(self) -> None:
        offered = self.school.participation_module_offered(
            self.class_id, "M1", include_individual=True, include_group=False
        )
        self.assertEqual(offered, 5)


    def test_finish_module_creates_subtotal_and_marks_complete(self) -> None:
        """Seeded M1 live columns can be finished into a locked module total."""
        self.assertFalse(self.school.module_complete(self.class_id, "M1"))
        rv = self.client.post(
            f"/api/classes/{self.class_id}/participation/modules/M1/finish",
            json={"sort": "az"},
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        self.assertTrue(self.school.module_complete(self.class_id, "M1"))
        dash = self.school.game.dashboard(self.class_id)
        names = [c.get("name") for c in dash["columns"] if c.get("kind") == "subtotal"]
        self.assertIn("Module 1 total", names)


class Mck230LiveDenominatorTests(Base):
    """Participation denominator follows live response collection (MCK-230)."""

    def test_deck_question_with_zero_live_answers_excluded(self) -> None:
        self.run_class("M1", "C1", 2, 0, {self.students[0]: 1})
        offered = self.school.participation_module_offered(
            self.class_id, "M1", include_individual=True, include_group=False
        )
        self.assertEqual(offered, 1)

    def test_added_in_class_question_with_live_answers_included(self) -> None:
        run_key = self.run_class("M2", "C1", 1, 0, {self.students[0]: 1})
        now = "2026-10-14T12:00:00"
        self.school.conn.execute(
            """
            INSERT INTO participation_items (
                class_id, run_key, live_module, live_slot, question_key,
                item_json, prompt_kind, offered_at
            ) VALUES (?, ?, 'M2', 'C1', 'item:M2-added', ?, 'poll', ?)
            """,
            (
                self.class_id,
                run_key,
                '{"type": "mc", "options": [{"text": "A"}, {"text": "B"}]}',
                now,
            ),
        )
        self.school.conn.execute(
            """
            INSERT INTO participation_answers (
                class_id, run_key, live_module, question_key, student_id,
                source, answer_json, answered_at
            ) VALUES (?, ?, 'M2', 'item:M2-added', ?, 'live', '{}', ?)
            """,
            (self.class_id, run_key, self.students[1], now),
        )
        self.school.conn.commit()
        offered = self.school.participation_module_offered(
            self.class_id, "M2", include_individual=True, include_group=False
        )
        self.assertEqual(offered, 2)

    def test_catchup_only_answers_do_not_count_in_denominator(self) -> None:
        run_key = self.run_class("M3", "C1", 2, 0, {})
        link = self.school.list_catchup_links(self.class_id)[0]
        self.assertEqual(link["run_key"], run_key)
        self.assertTrue(
            self.school.submit_catchup_answer(
                link["token"], self.students[0], "item:M3-q0", "x"
            )
        )
        offered = self.school.participation_module_offered(
            self.class_id, "M3", include_individual=True, include_group=False
        )
        self.assertEqual(offered, 0)

    def test_denominator_is_live_questions_not_response_volume(self) -> None:
        """Three live MC questions (14/11/16 student answers) plus silent deck MC → /3."""
        run_key = self.run_class("M4", "C1", 5, 0, {})
        c = self.school.conn
        now = "2026-10-14T15:00:00"
        extra_ids: list[int] = []
        for index in range(16):
            self.school.game.add_student(self.class_id, codename=f"L{index}")
            row = self.school.game.find_student_by_codename(self.class_id, f"L{index}")
            assert row is not None
            extra_ids.append(int(row["id"]))
        all_students = self.students + extra_ids
        live_counts = (14, 11, 16)
        for q_index, count in enumerate(live_counts):
            qkey = f"item:M4-q{q_index}"
            for stu_id in all_students[:count]:
                c.execute(
                    """
                    INSERT OR IGNORE INTO participation_answers (
                        class_id, run_key, live_module, question_key, student_id,
                        source, answer_json, answered_at
                    ) VALUES (?, ?, 'M4', ?, ?, 'live', '{}', ?)
                    """,
                    (self.class_id, run_key, qkey, stu_id, now),
                )
        c.commit()
        offered = self.school.participation_module_offered(
            self.class_id, "M4", include_individual=True, include_group=False
        )
        self.assertEqual(offered, 3)
        data = self.school.class_participation(self.class_id)
        m4 = next(m for m in data["modules"] if m["module"] == "M4")
        self.assertEqual(m4["offered"], 3)
        cell = data["students"][0]["modules"]["M4"]
        self.assertEqual(participation_fraction(cell["answered"], m4["offered"]), f"{cell['answered']}/3")


if __name__ == "__main__":
    unittest.main()


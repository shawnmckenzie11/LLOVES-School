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

    def _wire_participation_column(self, run_key: str, answered: int) -> int:
        """Attach a game-show session column to a captured participation run."""
        student = self.students[0]
        credits = {student: answered} if answered > 0 else {}
        self.school.game.persist_end_class_column(
            self.class_id,
            [student],
            credits,
            include_attendance=True,
            include_participation=bool(credits),
            run_key=run_key,
            create_game=True,
        )
        row = self.school.game.conn.execute(
            "SELECT session_id FROM live_participation_credits WHERE run_key = ?",
            (run_key,),
        ).fetchone()
        self.assertIsNotNone(row)
        return int(row["session_id"])

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

    def test_catchup_answers_credit_session_numerator(self) -> None:
        run_key = self.run_class(
            "M5", "C1", 2, 0, {self.students[0]: 1, self.students[1]: 2}
        )
        self._wire_participation_column(run_key, 1)
        link = self.school.list_catchup_links(self.class_id)[0]
        self.assertTrue(
            self.school.submit_catchup_answer(
                link["token"], self.students[0], "item:M5-q1", "x"
            )
        )
        meta = self.school.participation_session_meta(self.class_id)
        session_id = next(
            sid for sid, info in meta.items() if info.get("run_key") == run_key
        )
        cells = self.school.participation_individual_session_cells(self.class_id)
        self.assertEqual(cells.get(f"{session_id}:{self.students[0]}"), 2)


    def test_catchup_on_question_without_live_answers_does_not_credit(self) -> None:
        """Catch-up only counts toward questions already in the live denominator."""
        run_key = self.run_class("M5C", "C1", 2, 0, {self.students[0]: 1})
        self._wire_participation_column(run_key, 1)
        meta = self.school.participation_session_meta(self.class_id)
        session_id = next(
            sid for sid, info in meta.items() if info.get("run_key") == run_key
        )
        live_keys = meta[session_id].get("live_answered_keys") or []
        self.assertEqual(len(live_keys), 1)
        link = self.school.list_catchup_links(self.class_id)[0]
        self.assertTrue(
            self.school.submit_catchup_answer(
                link["token"], self.students[0], "item:M5C-q1", "x"
            )
        )
        cells = self.school.participation_individual_session_cells(self.class_id)
        answered = cells.get(f"{session_id}:{self.students[0]}")
        offered = self.school.participation_run_offered(
            self.class_id, run_key, include_individual=True, include_group=False
        )
        self.assertEqual(answered, 1)
        self.assertEqual(offered, 1)
        self.assertLessEqual(answered, offered)

    def test_rank_question_counts_in_module_denominator(self) -> None:
        run_key = self.run_class("M5B", "C1", 1, 0, {self.students[0]: 1})
        now = "2026-10-14T12:00:00"
        self.school.conn.execute(
            """
            INSERT INTO participation_items (
                class_id, run_key, live_module, live_slot, question_key,
                item_json, prompt_kind, offered_at
            ) VALUES (?, ?, 'M5B', 'C1', 'item:M5B-rank', ?, 'rank', ?)
            """,
            (
                self.class_id,
                run_key,
                '{"type": "rank"}',
                now,
            ),
        )
        self.school.conn.execute(
            """
            INSERT INTO participation_answers (
                class_id, run_key, live_module, question_key, student_id,
                source, answer_json, answered_at
            ) VALUES (?, ?, 'M5B', 'item:M5B-rank', ?, 'live', '{}', ?)
            """,
            (self.class_id, run_key, self.students[1], now),
        )
        self.school.conn.commit()
        offered = self.school.participation_module_offered(
            self.class_id, "M5B", include_individual=True, include_group=False
        )
        self.assertEqual(offered, 2)

    def test_module_rollup_percent_stays_at_or_below_100_without_clamp(self) -> None:
        run_key = self.run_class(
            "M8",
            "C1",
            3,
            0,
            {self.students[0]: 1, self.students[1]: 3},
        )
        link = self.school.list_catchup_links(self.class_id)[0]
        self.assertTrue(
            self.school.submit_catchup_answer(
                link["token"], self.students[0], "item:M8-q1", "x"
            )
        )
        self.assertTrue(
            self.school.submit_catchup_answer(
                link["token"], self.students[0], "item:M8-q2", "x"
            )
        )
        data = self.school.class_participation(self.class_id)
        m8 = next(m for m in data["modules"] if m["module"] == "M8")
        cell = data["students"][0]["modules"]["M8"]
        self.assertEqual(m8["offered"], 3)
        self.assertLessEqual(cell["answered"], m8["offered"])
        self.assertLessEqual(cell["percent"], 100)

    def test_test_run_session_omitted_from_participation_grid(self) -> None:
        real_key = self.run_class("M6", "C1", 1, 0, {self.students[0]: 1})
        self._wire_participation_column(real_key, 1)
        test_key = self.run_class("M6", "C2", 2, 0, {}, ended_iso="2026-10-20T15:00:00")
        self._wire_participation_column(test_key, 1)
        self.school.set_participation_run_real(self.class_id, test_key, False, user_id=1)
        meta = self.school.participation_session_meta(self.class_id)
        self.assertTrue(any(info.get("run_key") == real_key for info in meta.values()))
        self.assertFalse(any(info.get("run_key") == test_key for info in meta.values()))
        client = self.app.test_client()
        client.get("/auth/google?portal=staff")
        client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        code = self.school.get_user_by_email("teacher@gmail.com")["verification_code"]
        client.post("/verify-email", data={"code": code})
        body = client.get(
            f"/api/classes/{self.class_id}/participation-grid?sort=az"
        ).get_json()
        run_keys = {
            (body.get("session_meta") or {}).get(str(col["id"]), {}).get("run_key")
            for col in body.get("columns") or []
            if col.get("kind") == "session"
        }
        self.assertIn(real_key, run_keys)
        self.assertNotIn(test_key, run_keys)


class Mck230ParticipationHeaderTests(unittest.TestCase):
    """Participation column labels use lesson slot + Toronto date."""

    def test_header_label_slot_and_toronto_date(self) -> None:
        from participation_exclusion import format_participation_session_header

        label = format_participation_session_header(
            "M1", "C1", "2026-10-15T18:30:00"
        )
        self.assertTrue(label.startswith("M1C1"))
        self.assertIn("10/15/26", label)

    def test_header_uses_dash_when_date_unparseable(self) -> None:
        from participation_exclusion import format_participation_session_header

        label = format_participation_session_header("M2", "C3", "not-a-date")
        self.assertEqual(label, "M2C3 · —")


class Mck230ProdLikeModuleOfferedTests(Base):
    """Prod-shaped M2 denominators (class 9 → /5, class 10 → /6)."""

    def run_class_on(
        self,
        class_id: int,
        module: str,
        slot: str,
        individual: int,
        group: int,
        answers: dict[int, int],
        *,
        wipe: bool = True,
        ended_iso: str = "2026-10-14T14:30:00",
    ) -> str:
        """Like ``Base.run_class`` but targets an explicit class id."""
        live = self.school.start_live_class_session(class_id, int(self.teacher["id"]))
        sid = int(live["id"])
        c = self.school.conn
        cols = {r[1] for r in c.execute("PRAGMA table_info(live_class_sessions)")}
        if "teacher_state_json" in cols:
            import json

            c.execute(
                "UPDATE live_class_sessions SET teacher_state_json = ? WHERE id = ?",
                (json.dumps({"live_module": module, "live_slot": slot}), sid),
            )
        now = "2026-10-13T09:00:00"
        prompts = []
        for n in range(individual + group):
            mode = "individual" if n < individual else "group"
            pid = c.execute(
                "INSERT INTO live_session_prompts (live_session_id, slide_index, kind, payload, "
                "created_at, updated_at) VALUES (?, ?, 'poll', '{}', ?, ?)",
                (sid, n, now, now),
            ).lastrowid
            c.execute(
                "INSERT INTO live_session_items (live_session_id, placement_key, item_id, stage, kind, "
                "item_json, prompt_id, status, publish_mode, response_mode, published_at, created_at, updated_at) "
                "VALUES (?, ?, ?, 'round', 'poll', ?, ?, 'closed', ?, ?, ?, ?, ?)",
                (
                    sid,
                    f"{module}-{slot}-q{n}",
                    f"q{n}",
                    '{"question_title": "c1 Q%d", "text": "Real question %d with x^2", "type": "mc", '
                    '"options": [{"text": "Option A text"}, {"text": "Option B text"}], '
                    '"correct_answer": "B", "key": "B"}' % (n, n),
                    pid,
                    mode,
                    mode,
                    now,
                    now,
                    now,
                ),
            )
            prompts.append(pid)
        for stu, count in answers.items():
            for pid in prompts[:count] + prompts[individual:]:
                c.execute(
                    "INSERT OR IGNORE INTO live_session_responses (prompt_id, student_id, response_json, "
                    "created_at, updated_at) VALUES (?, ?, '{}', ?, ?)",
                    (pid, stu, now, now),
                )
        run_key = str(
            c.execute("SELECT run_key FROM live_class_sessions WHERE id = ?", (sid,)).fetchone()[0]
            or ""
        )
        c.execute(
            "UPDATE live_class_sessions SET ended_at = ?, status = 'ended' WHERE id = ?",
            (ended_iso, sid),
        )
        c.commit()
        self.school.cleanup_live_session_response_data(sid)
        if wipe:
            self.school.wipe_live_sessions_for_class(class_id)
        return run_key

    def setUp(self) -> None:
        super().setUp()
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCR3U",
            days_preset="M/W/F",
            time_label="9:15am",
            codenames=["Section5"],
            offering_id=int(
                self.school.conn.execute(
                    "SELECT offering_id FROM classes WHERE id = ?",
                    (self.class_id,),
                ).fetchone()[0]
            ),
            teacher_user_id=int(self.teacher["id"]),
        )
        self.class_10 = int(created["id"] if isinstance(created, dict) else created)
        self.school.game.add_student(self.class_10, codename="S5A")
        self.students_10 = [
            int(
                self.school.game.find_student_by_codename(self.class_10, "S5A")["id"]
            )
        ]
        self._seed_m2_slots(self.class_id, self.students[0], [("C3", 3), ("C4", 2)])
        self._seed_m2_slots(self.class_10, self.students_10[0], [("C3", 4), ("C4", 2)])

    def _seed_m2_slots(
        self, class_id: int, student_id: int, slots: list[tuple[str, int]]
    ) -> None:
        """End M2 live classes with known per-slot question counts."""
        for slot, count in slots:
            self.run_class_on(
                class_id, "M2", slot, count, 0, {student_id: count}, wipe=False
            )

    def test_class_9_shape_m2_offered_is_five(self) -> None:
        offered = self.school.participation_module_offered(
            self.class_id, "M2", include_individual=True, include_group=False
        )
        self.assertEqual(offered, 5)

    def test_class_10_shape_m2_offered_is_six(self) -> None:
        offered = self.school.participation_module_offered(
            self.class_10, "M2", include_individual=True, include_group=False
        )
        self.assertEqual(offered, 6)


if __name__ == "__main__":
    unittest.main()


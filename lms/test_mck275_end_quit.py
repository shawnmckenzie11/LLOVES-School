#!/usr/bin/env python3
"""MCK-275: End Class always saves; Quit saves nothing and clears the run."""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from test_participation_mck198 import Base  # noqa: E402


class EndAlwaysSavesTests(Base):
    """End writes attendance and participation. Quit writes neither."""

    def setUp(self) -> None:
        super().setUp()
        self.client = self.app.test_client()
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        code = self.school.get_user_by_email("teacher@gmail.com")["verification_code"]
        self.client.post("/verify-email", data={"code": code})

    def _count(self, sql: str, params: tuple = ()) -> int:
        """Return one integer count. The SQL must select ``n``."""
        row = self.school.conn.execute(sql, params).fetchone()
        return int(row["n"] or 0)

    def _gradebook_columns(self) -> int:
        """Class-day columns, not the blank template row."""
        row = self.school.game.conn.execute(
            """
            SELECT COUNT(*) AS n FROM sessions
            WHERE class_id = ? AND status != 'template'
            """,
            (self.class_id,),
        ).fetchone()
        return int(row["n"] or 0)

    def _arm(self, *, is_test: bool, module: str, slot: str) -> int:
        """Start a class, publish one answered item, and mark one student in.

        Args:
            is_test: True for a test class.
            module: Teacher module, such as ``M1``.
            slot: Teacher slot, such as ``C1`` or ``O1``.

        Returns:
            ``live_class_sessions.id``.
        """
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=is_test
        )
        sid = int(live["id"])
        now = "2026-10-17T13:20:00"
        self.school.conn.execute(
            """
            UPDATE live_class_sessions
            SET teacher_state_json = ?, started_at = ?
            WHERE id = ?
            """,
            (json.dumps({"live_module": module, "live_slot": slot}), now, sid),
        )
        prompt_id = self.school.conn.execute(
            """
            INSERT INTO live_session_prompts (
                live_session_id, slide_index, kind, payload, created_at, updated_at
            ) VALUES (?, 1, 'poll', ?, ?, ?)
            """,
            (
                sid,
                json.dumps({"text": "q0", "type": "mc", "item_id": "q0", "options": [{"text": "A"}]}),
                now,
                now,
            ),
        ).lastrowid
        self.school.conn.execute(
            """
            INSERT INTO live_session_items (
                live_session_id, placement_key, item_id, stage, kind, item_json,
                prompt_id, status, publish_mode, response_mode, published_at,
                created_at, updated_at
            ) VALUES (?, 'q0', 'q0', 'round', 'poll', ?, ?, 'closed',
                      'individual', 'individual', ?, ?, ?)
            """,
            (
                sid,
                json.dumps({"text": "q0", "type": "mc", "options": [{"text": "A"}]}),
                prompt_id,
                now,
                now,
                now,
            ),
        )
        self.school.conn.execute(
            """
            INSERT INTO live_session_responses (
                prompt_id, student_id, response_json, created_at, updated_at
            ) VALUES (?, ?, '{}', ?, ?)
            """,
            (int(prompt_id), int(self.students[0]), now, now),
        )
        self.school.conn.commit()
        self.school.join_live_class_session(sid, int(self.students[0]), codename="Ava")
        return sid

    def _post_end(self) -> None:
        """POST End Class with the old save flags turned off."""
        response = self.client.post(
            f"/staff/class/{self.class_id}/end-live",
            data={
                "end_options": "1",
                "save_attendance": "0",
                "save_participation": "0",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

    def _score_cell(self, session_id: int) -> tuple[int, float]:
        """Present flag and points for the joined student on one column.

        Args:
            session_id: Tracker ``sessions.id`` stored on the live row.
        """
        row = self.school.game.conn.execute(
            """
            SELECT present, points FROM session_scores
            WHERE session_id = ? AND student_id = ?
            """,
            (int(session_id), int(self.students[0])),
        ).fetchone()
        self.assertIsNotNone(row)
        return int(row["present"] or 0), float(row["points"] or 0)

    def _tracker_id(self, session_id: int) -> int:
        """Gradebook column id written for one live session.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        row = self.school.conn.execute(
            "SELECT mgs_session_id FROM live_class_sessions WHERE id = ?",
            (int(session_id),),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["mgs_session_id"])
        return int(row["mgs_session_id"])

    def test_finish_rejects_the_old_save_flags(self) -> None:
        """An old caller cannot pass the removed save switches."""
        with self.assertRaises(TypeError):
            self.school.finish_live_class(
                self.class_id,
                save_attendance=False,
                save_participation=False,
            )

    def test_end_saves_attendance_and_participation_in_each_mode(self) -> None:
        """Live, test, and open office each write both columns.

        Posted save flags stay off. End still writes present, points,
        and one catch-up row. Counts only: no link value is read.
        """
        cases = (
            ("live", False, "M1", "C1"),
            ("test", True, "M2", "C1"),
            ("open office", False, "M1", "O1"),
        )
        for label, is_test, module, slot in cases:
            with self.subTest(label=label):
                if self.school.get_active_live_session_for_class(self.class_id):
                    self.school.finish_live_class(self.class_id, celebrate=True)
                sid = self._arm(is_test=is_test, module=module, slot=slot)
                before_links = self._count(
                    "SELECT COUNT(*) AS n FROM catchup_links WHERE class_id = ?",
                    (self.class_id,),
                )
                self._post_end()
                tracker = self._tracker_id(sid)
                present, points = self._score_cell(tracker)
                self.assertEqual(present, 1)
                self.assertGreater(points, 0)
                links = self._count(
                    "SELECT COUNT(*) AS n FROM catchup_links WHERE live_session_id = ?",
                    (sid,),
                )
                self.assertEqual(links, 1)
                self.assertEqual(
                    self._count(
                        "SELECT COUNT(*) AS n FROM catchup_links WHERE class_id = ?",
                        (self.class_id,),
                    ),
                    before_links + 1,
                )
                if slot == "O1":
                    grid = self.school.participation_week_grid(self.class_id)
                    office = [
                        row
                        for row in (grid.get("session_meta") or {}).values()
                        if row.get("open_office")
                    ]
                    self.assertEqual(len(office), 1)
                    self.assertEqual(str(office[0]["live_module"]).upper(), "M1")
                    self.assertEqual(str(office[0]["live_slot"]).upper(), "O1")
                    marks = self.school.game.attendance_score_rows(self.class_id)
                    marked = sum(
                        1
                        for row in marks.get("scores") or []
                        if int(row.get("session_id") or 0) == tracker
                        and int(row.get("present") or 0) == 1
                    )
                    self.assertGreaterEqual(marked, 1)

    def test_quit_saves_nothing_and_clears_the_run(self) -> None:
        """Quit leaves no gradebook row, catch-up row, or live rows."""
        columns = self._gradebook_columns()
        ended = self._count(
            """
            SELECT COUNT(*) AS n FROM live_class_sessions
            WHERE class_id = ? AND status = 'ended'
            """,
            (self.class_id,),
        )
        sid = self._arm(is_test=False, module="M1", slot="C2")
        run_key = str(self.school.get_live_session(sid)["run_key"] or "")
        self.school.boards.append_ops(
            run_key,
            "shared",
            owner="teacher",
            ops=[{"type": "stroke_add", "id": "mark", "points": [[0.1, 0.2]]}],
        )
        self.assertEqual(self.school.boards.count_run_ops(run_key), 1)
        response = self.client.post(
            f"/staff/class/{self.class_id}/quit-live",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.school.get_live_session(sid))
        self.assertEqual(self._gradebook_columns(), columns)
        self.assertEqual(
            self._count(
                """
                SELECT COUNT(*) AS n FROM live_class_sessions
                WHERE class_id = ? AND status = 'ended'
                """,
                (self.class_id,),
            ),
            ended,
        )
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM catchup_links WHERE class_id = ?",
                (self.class_id,),
            ),
            0,
        )
        self.assertEqual(
            self._count("SELECT COUNT(*) AS n FROM live_session_attendees"),
            0,
        )
        self.assertEqual(
            self._count("SELECT COUNT(*) AS n FROM live_session_responses"),
            0,
        )
        self.assertEqual(self.school.boards.count_run_ops(run_key), 0)
        self.assertEqual(
            self._count(
                """
                SELECT COUNT(*) AS n FROM live_participation_credits lpc
                INNER JOIN sessions s ON s.id = lpc.session_id
                WHERE s.class_id = ?
                """,
                (self.class_id,),
            ),
            0,
        )

    def test_delete_test_removes_gradebook_and_catchup(self) -> None:
        """Deleting a test class removes the column and the catch-up row."""
        sid = self._arm(is_test=True, module="M3", slot="C2")
        self._post_end()
        tracker = self._tracker_id(sid)
        present, points = self._score_cell(tracker)
        self.assertEqual(present, 1)
        self.assertGreater(points, 0)
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM catchup_links WHERE live_session_id = ?",
                (sid,),
            ),
            1,
        )
        result = self.school.delete_test_live_class(self.class_id, sid)
        self.assertEqual(result["ok"], True)
        self.assertEqual(int(result["deleted_session_id"]), sid)
        self.assertEqual(
            self._count("SELECT COUNT(*) AS n FROM sessions WHERE id = ?", (tracker,)),
            0,
        )
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM session_scores WHERE session_id = ?",
                (tracker,),
            ),
            0,
        )
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM catchup_links WHERE live_session_id = ?",
                (sid,),
            ),
            0,
        )
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM live_participation_credits WHERE session_id = ?",
                (tracker,),
            ),
            0,
        )

    def test_an_already_ended_class_is_not_backfilled(self) -> None:
        """A class closed without End Class stays without a gradebook write."""
        sid = self._arm(is_test=False, module="M1", slot="C3")
        self.school.conn.execute(
            """
            UPDATE live_class_sessions
            SET status = 'ended', ended_at = ?
            WHERE id = ?
            """,
            ("2026-10-17T13:40:00", sid),
        )
        self.school.conn.commit()
        columns = self._gradebook_columns()
        links = self._count(
            "SELECT COUNT(*) AS n FROM catchup_links WHERE class_id = ?",
            (self.class_id,),
        )
        self.school.finish_live_class(self.class_id, celebrate=True)
        self.assertEqual(self._gradebook_columns(), columns)
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM catchup_links WHERE class_id = ?",
                (self.class_id,),
            ),
            links,
        )
        self.assertIsNone(
            self.school.conn.execute(
                "SELECT mgs_session_id FROM live_class_sessions WHERE id = ?",
                (sid,),
            ).fetchone()["mgs_session_id"]
        )

    def test_quit_dialog_copy_and_end_is_the_main_button(self) -> None:
        """The live header shows End Class, and Quit opens the confirm copy."""
        self._arm(is_test=False, module="M1", slot="C1")
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        body = page.get_data(as_text=True)
        self.assertIn('id="live-end-class-form"', body)
        self.assertIn('aria-label="End Class">End Class</button>', body)
        self.assertIn('id="live-quit-class"', body)
        self.assertIn('type="button"', body)
        self.assertIn('id="quit-live-dialog"', body)
        self.assertIn("Quit without saving?", body)
        self.assertIn(
            "Nothing from this class will be saved, including attendance, "
            "participation and the catch-up code. This can't be undone.",
            body,
        )
        self.assertIn(">Go back<", body)
        self.assertIn('class="btn-danger">Quit and delete</button>', body)
        self.assertNotIn('name="save_attendance"', body)
        self.assertNotIn('name="save_participation"', body)
        self.assertNotIn('id="end-live-dialog"', body)
        quit_at = body.index('id="live-quit-class-form"')
        end_at = body.index('id="live-end-class-form"')
        self.assertIn("/quit-live", body[quit_at : quit_at + 400])
        self.assertIn("/end-live", body[end_at : end_at + 400])
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        end_rule = css.split("body.staff-shell .live-header-end {", 1)[1].split("}", 1)[0]
        quit_rule = css.split("body.staff-shell .live-header-quit,", 1)[1].split("}", 1)[0]
        self.assertIn("min-height: 2.6rem", end_rule)
        self.assertIn("min-height: 1.7rem", quit_rule)
        script = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertIn("quit-live-dialog", script)
        self.assertIn("dialog.showModal()", script)
        self.assertNotIn("wireEndLiveDialog", script)

    def test_quit_purges_notes_engagement_and_board_assignments(self) -> None:
        """Quit leaves no notes, engagement rows, or board assignments."""
        sid = self._arm(is_test=False, module="M1", slot="C4")
        self.school.record_engagement_event(
            "present.join", session_id=sid, student_id=int(self.students[0])
        )
        self.school.create_observation(
            live_session_id=sid,
            class_id=self.class_id,
            observer_user_id=int(self.teacher["id"]),
            scope="student",
            note="office note",
            student_ids=[int(self.students[0])],
        )
        self.school.record_live_board_assignment_for_student(
            sid, int(self.students[0]), 0
        )
        self.assertGreater(self.school._count_engagement_rows([sid], []), 0)
        self.assertGreater(
            self._count(
                "SELECT COUNT(*) AS n FROM live_board_assignments WHERE live_session_id = ?",
                (sid,),
            ),
            0,
        )
        response = self.client.post(
            f"/staff/class/{self.class_id}/quit-live",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.school._count_engagement_rows([sid], []), 0)
        self.assertEqual(self.school.list_observation_history(self.class_id), [])
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM observation_history WHERE live_session = ?",
                (sid,),
            ),
            0,
        )
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM live_board_assignments WHERE live_session_id = ?",
                (sid,),
            ),
            0,
        )

    def test_delete_test_removes_engagement_rows(self) -> None:
        """Deleting a test class removes its engagement events and rollup."""
        sid = self._arm(is_test=True, module="M4", slot="C1")
        self.school.record_engagement_event(
            "present.join", session_id=sid, student_id=int(self.students[0])
        )
        self._post_end()
        self.assertGreater(self.school._count_engagement_rows([sid], []), 0)
        self.school.delete_test_live_class(self.class_id, sid)
        self.assertEqual(self.school._count_engagement_rows([sid], []), 0)

    def test_end_and_quit_at_once_have_one_outcome(self) -> None:
        """Whichever of End or Quit claims the class, the other changes nothing."""
        for winner in ("end", "quit"):
            with self.subTest(winner=winner):
                sid = self._arm(is_test=False, module="M2", slot="C2")
                columns = self._gradebook_columns()
                key = str(self.school.get_live_session(sid)["run_key"] or "")
                self.school.record_engagement_event(
                    "present.join", session_id=sid, student_id=int(self.students[0])
                )
                self.school.record_live_board_assignment_for_student(
                    sid, int(self.students[0]), 0
                )
                held = threading.Event()
                release = threading.Event()
                original = self.school._claim_class_finish
                box: dict[str, object] = {}

                def claim(class_id: int) -> bool:
                    """Hold the winner inside the claim so the other request loses."""
                    ok = original(class_id)
                    if ok:
                        held.set()
                        release.wait(timeout=5)
                    return ok

                def run_winner() -> None:
                    """Finish as the request that owns the claim."""
                    try:
                        if winner == "end":
                            box["result"] = self.school.finish_live_class(
                                self.class_id, celebrate=True, run_key=key
                            )
                        else:
                            box["result"] = self.school.finish_live_class(
                                self.class_id, persist=False, celebrate=False
                            )
                    except Exception as exc:  # noqa: BLE001 — surfaced below
                        box["error"] = exc

                self.school._claim_class_finish = claim
                worker = threading.Thread(target=run_winner)
                worker.start()
                try:
                    self.assertTrue(held.wait(timeout=5))
                    if winner == "end":
                        other = self.school.finish_live_class(
                            self.class_id, persist=False, celebrate=False
                        )
                        response = self.client.post(
                            f"/staff/class/{self.class_id}/quit-live",
                            follow_redirects=False,
                        )
                    else:
                        other = self.school.finish_live_class(
                            self.class_id, celebrate=True, run_key=key
                        )
                        response = self.client.post(
                            f"/staff/class/{self.class_id}/end-live",
                            follow_redirects=False,
                        )
                    self.assertTrue(other.get("already_finished"))
                    self.assertEqual(response.status_code, 409)
                    self.assertIn(
                        "This class was already ended or quit.",
                        response.get_data(as_text=True),
                    )
                    self.assertIsNotNone(self.school.get_live_session(sid))
                    self.assertEqual(self._gradebook_columns(), columns)
                finally:
                    release.set()
                    worker.join(timeout=5)
                    self.school._claim_class_finish = original
                self.assertFalse(worker.is_alive())
                self.assertNotIn("error", box)
                if winner == "end":
                    self.assertGreater(self._gradebook_columns(), columns)
                    self.assertIsNotNone(self.school.get_live_session(sid))
                    self.assertGreater(self.school._count_engagement_rows([sid], []), 0)
                else:
                    self.assertEqual(self._gradebook_columns(), columns)
                    self.assertIsNone(self.school.get_live_session(sid))
                    self.assertEqual(self.school._count_engagement_rows([], [key]), 0)
                    self.assertEqual(
                        self._count(
                            "SELECT COUNT(*) AS n FROM live_board_assignments WHERE live_session_id = ?",
                            (sid,),
                        ),
                        0,
                    )
                    self.assertEqual(
                        self._count(
                            "SELECT COUNT(*) AS n FROM catchup_links WHERE live_session_id = ?",
                            (sid,),
                        ),
                        0,
                    )

    def _seed_module_answer(self, module: str, slot: str, question_key: str) -> None:
        """Store one live answer in a module slot, with no class-slot run link.

        Args:
            module: Module label, such as ``M8``.
            slot: ``C1`` or an open-office slot such as ``O1``.
            question_key: Participation question key for this row only.
        """
        now = "2026-10-17T14:00:00"
        run_key = f"module-{slot.lower()}"
        self.school.conn.execute(
            """
            INSERT INTO participation_runs (
                class_id, run_key, is_real, auto_is_real, discarded
            ) VALUES (?, ?, 1, 1, 0)
            """,
            (self.class_id, run_key),
        )
        self.school.conn.execute(
            """
            INSERT INTO participation_items (
                class_id, run_key, live_module, live_slot, question_key,
                item_json, prompt_kind, offered_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'poll', ?)
            """,
            (
                self.class_id,
                run_key,
                module,
                slot,
                question_key,
                json.dumps({"type": "mc", "item_id": question_key}),
                now,
            ),
        )
        self.school.conn.execute(
            """
            INSERT INTO participation_answers (
                class_id, run_key, live_module, question_key, student_id,
                source, answer_json, answered_at
            ) VALUES (?, ?, ?, ?, ?, 'live', '{}', ?)
            """,
            (self.class_id, run_key, module, question_key, int(self.students[1]), now),
        )
        self.school.conn.commit()

    def test_open_office_only_module_is_not_zero_of_one(self) -> None:
        """A module with only an open office does not offer a class question."""
        self._seed_module_answer("M8", "O1", "q-office")
        offered = self.school.participation_module_offered(
            self.class_id, "M8", include_individual=True, include_group=False
        )
        self.assertEqual(offered, 0)
        cell = self.school.student_module_participation(
            self.class_id, int(self.students[0]), "M8"
        )
        self.assertEqual((cell["answered"], cell["offered"]), (0, 0))
        modules = [
            row["module"] for row in self.school.class_participation(self.class_id)["modules"]
        ]
        self.assertNotIn("M8", modules)
        self._seed_module_answer("M9", "C1", "q-class")
        class_offered = self.school.participation_module_offered(
            self.class_id, "M9", include_individual=True, include_group=False
        )
        self.assertEqual(class_offered, 1)


if __name__ == "__main__":
    unittest.main()

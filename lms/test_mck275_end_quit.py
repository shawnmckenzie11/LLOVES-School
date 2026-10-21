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
                    self.assertTrue(other.get("finish_busy"))
                    self.assertEqual(response.status_code, 409)
                    self.assertIn(
                        "Try again in a moment.",
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


    def _note(self, sid: int, text: str) -> None:
        """Store one teacher note on this live session.

        Args:
            sid: ``live_class_sessions.id``.
            text: Note text.
        """
        self.school.create_observation(
            live_session_id=sid,
            class_id=self.class_id,
            observer_user_id=int(self.teacher["id"]),
            scope="student",
            note=text,
            student_ids=[int(self.students[0])],
        )

    def _rollup(self, sid: int, run_key: str) -> None:
        """Store one engagement rollup cell for this run.

        Args:
            sid: ``live_class_sessions.id``.
            run_key: That session's run key.
        """
        self.school.engagement.insert_rollup_batch(
            [
                {
                    "class_id": int(self.class_id),
                    "live_session": int(sid),
                    "run_key": str(run_key),
                    "rolled_at": "2026-10-17T13:20:00",
                    "scope": "class",
                    "signal": "present",
                    "count": 1,
                }
            ]
        )

    def _rollup_count(self, run_key: str) -> int:
        """Count rollup cells for one run key on this class.

        Args:
            run_key: ``live_class_sessions.run_key``.

        Returns:
            Row count on sqlite plus Postgres when the log lives there.
        """
        rows = self.school.engagement._fetch(
            "SELECT COUNT(*) AS n FROM engagement_rollup WHERE class_id = ? AND run_key = ?",
            (int(self.class_id), str(run_key)),
        )
        if not rows:
            return 0
        row = rows[0]
        try:
            return int(row["n"])
        except (TypeError, KeyError, IndexError):
            return int(row[0])

    def test_end_then_quit_keeps_the_ended_run(self) -> None:
        """Quit after End deletes nothing and says the class already ended."""
        sid = self._arm(is_test=False, module="M3", slot="C1")
        key = str(self.school.get_live_session(sid)["run_key"] or "")
        self._note(sid, "ended note")
        self.school.record_engagement_event(
            "present.join", session_id=sid, student_id=int(self.students[0])
        )
        self._rollup(sid, key)
        self._post_end()
        notes_before = self._count(
            "SELECT COUNT(*) AS n FROM observations WHERE live_session_id = ?",
            (sid,),
        )
        events_before = len(
            self.school.list_engagement_events(self.class_id, run_key=key)
        )
        rolls_before = self._rollup_count(key)
        self.assertGreater(notes_before, 0)
        self.assertGreater(events_before, 0)
        self.assertGreater(rolls_before, 0)
        response = self.client.post(
            f"/staff/class/{self.class_id}/quit-live",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("This class already ended.", response.get_data(as_text=True))
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM observations WHERE live_session_id = ?",
                (sid,),
            ),
            notes_before,
        )
        self.assertEqual(
            len(self.school.list_engagement_events(self.class_id, run_key=key)),
            events_before,
        )
        self.assertEqual(self._rollup_count(key), rolls_before)
        leftover = self.school.get_live_session(sid)
        self.assertIsNotNone(leftover)
        self.assertEqual(leftover["status"], "ended")

    def test_quit_leaves_older_ended_runs(self) -> None:
        """Quit of the live run leaves an earlier ended run's notes and log."""
        first = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        first_id = int(first["id"])
        first_key = str(first["run_key"] or "")
        self._note(first_id, "older note")
        self.school.record_engagement_event(
            "present.join", session_id=first_id, student_id=int(self.students[0])
        )
        self._rollup(first_id, first_key)
        self.school.finish_live_class(self.class_id, celebrate=True, run_key=first_key)
        second = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        second_id = int(second["id"])
        second_key = str(second["run_key"] or "")
        self._note(second_id, "live note")
        self.school.record_engagement_event(
            "present.join", session_id=second_id, student_id=int(self.students[0])
        )
        self._rollup(second_id, second_key)
        response = self.client.post(
            f"/staff/class/{self.class_id}/quit-live",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(self.school.get_live_session(first_id))
        self.assertIsNone(self.school.get_live_session(second_id))
        kept = [row["note"] for row in self.school.list_observations(first_id)]
        self.assertEqual(kept, ["older note"])
        self.assertEqual(self.school.list_observations(second_id), [])
        older_kinds = [
            row["kind"]
            for row in self.school.list_engagement_events(self.class_id, run_key=first_key)
        ]
        self.assertIn("present.join", older_kinds)
        self.assertGreater(self._rollup_count(first_key), 0)
        self.assertEqual(
            self.school.list_engagement_events(self.class_id, run_key=second_key),
            [],
        )
        self.assertEqual(self._rollup_count(second_key), 0)

    def test_test_delete_reused_id_keeps_the_other_class(self) -> None:
        """A reused session id does not take another class's log or notes."""
        other = int(
            self.school.game.create_class(
                year="2026/27",
                semester="Semester 1",
                course_code="MCR3U",
                days_preset="M/W/F",
                time_label="3:15pm",
                codenames=["Cam", "Dee"],
                offering_id=int(self.school.game.get_class(self.class_id)["offering_id"]),
                teacher_user_id=int(self.teacher["id"]),
            )["id"]
        )
        kept = self.school.start_live_class_session(
            other, int(self.teacher["id"])
        )
        kept_id = int(kept["id"])
        kept_key = str(kept["run_key"] or "")
        self.school.record_engagement_event(
            "present.join", session_id=kept_id, student_id=int(self.students[0])
        )
        self.school.engagement.insert_rollup_batch(
            [
                {
                    "class_id": other,
                    "live_session": kept_id,
                    "run_key": kept_key,
                    "rolled_at": "2026-10-17T13:20:00",
                    "scope": "class",
                    "signal": "present",
                    "count": 1,
                }
            ]
        )
        self.school.engagement.append(
            {
                "class_id": other,
                "live_session": kept_id,
                "run_key": "",
                "student_id": int(self.students[0]),
                "first_name": "Ava",
                "team_id": None,
                "item_ref": "",
                "kind": "present.join",
                "ts": "2026-10-17T13:21:00",
                "payload": "{}",
            }
        )
        self.school.conn.execute(
            """
            INSERT INTO observation_history (
                source_observation_id, live_session, run_key, class_id,
                created_at, scope, note, kept_at, student_ids_json, process_keys_json
            ) VALUES (?, ?, ?, ?, 't', 'class', 'other class note', 't', '[]', '[]')
            """,
            (900000 + kept_id, kept_id, kept_key, other),
        )
        self.school.conn.execute(
            """
            INSERT INTO observation_history (
                source_observation_id, live_session, run_key, class_id,
                created_at, scope, note, kept_at, student_ids_json, process_keys_json
            ) VALUES (?, ?, '', ?, 't', 'class', 'blank key note', 't', '[]', '[]')
            """,
            (910000 + kept_id, kept_id, other),
        )
        self.school.conn.commit()
        self.school.conn.execute(
            "DELETE FROM live_class_sessions WHERE id = ?",
            (kept_id,),
        )
        self.school.conn.commit()
        test = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=True
        )
        test_id = int(test["id"])
        self.assertEqual(test_id, kept_id)
        self.school.record_engagement_event(
            "present.join", session_id=test_id, student_id=int(self.students[0])
        )
        self.school.delete_test_live_class(self.class_id, test_id)
        self.assertEqual(
            [
                row["note"]
                for row in self.school.list_observation_history(other)
            ],
            ["other class note", "blank key note"],
        )
        kept_events = self.school.list_engagement_events(other, run_key=kept_key)
        self.assertEqual([row["kind"] for row in kept_events], ["present.join"])
        blank = self.school.engagement._fetch(
            """
            SELECT COUNT(*) AS n FROM engagement_events
            WHERE class_id = ? AND live_session = ? AND TRIM(run_key) = ''
            """,
            (other, kept_id),
        )
        blank_row = blank[0]
        try:
            blank_n = int(blank_row["n"])
        except (TypeError, KeyError, IndexError):
            blank_n = int(blank_row[0])
        self.assertEqual(blank_n, 1)
        self.assertEqual(
            self.school._count_engagement_rows(
                [test_id],
                [str(test["run_key"] or "")],
                class_id=self.class_id,
                require_session=False,
            ),
            0,
        )

    def test_fresh_finish_claim_says_try_again(self) -> None:
        """A claim left by a crashed worker tells the teacher to try again."""
        from datetime import datetime, timedelta, timezone

        sid = self._arm(is_test=False, module="M4", slot="C2")
        self._note(sid, "still here")
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.school.game._lock:
            self.school.game.conn.execute(
                "INSERT INTO live_end_claims (run_key, claimed_at) VALUES (?, ?)",
                (f"finish:{int(self.class_id)}", stamp),
            )
        ended = self.client.post(
            f"/staff/class/{self.class_id}/end-live",
            follow_redirects=False,
        )
        self.assertEqual(ended.status_code, 409)
        self.assertIn("Try again in a moment.", ended.get_data(as_text=True))
        quit = self.client.post(
            f"/staff/class/{self.class_id}/quit-live",
            follow_redirects=False,
        )
        self.assertEqual(quit.status_code, 409)
        self.assertIn("Try again in a moment.", quit.get_data(as_text=True))
        still = self.school.get_live_session(sid)
        self.assertIsNotNone(still)
        self.assertEqual(still["status"], "active")
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM observations WHERE live_session_id = ?",
                (sid,),
            ),
            1,
        )
        aged = (datetime.now(timezone.utc) - timedelta(seconds=301)).isoformat(
            timespec="seconds"
        )
        with self.school.game._lock:
            self.school.game.conn.execute(
                "UPDATE live_end_claims SET claimed_at = ? WHERE run_key = ?",
                (aged, f"finish:{int(self.class_id)}"),
            )
        proceeded = self.client.post(
            f"/staff/class/{self.class_id}/end-live",
            follow_redirects=False,
        )
        self.assertEqual(proceeded.status_code, 302)
        closed = self.school.get_live_session(sid)
        self.assertIsNotNone(closed)
        self.assertEqual(closed["status"], "ended")

    def test_blank_slot_stays_in_the_module_total(self) -> None:
        """A participation row with no slot still counts. An open office does not."""
        self._seed_module_answer("M7", "", "q-blank")
        offered, answered = self.school._participation_counts(self.class_id, "M7")
        self.assertEqual(offered.get("M7"), 1)
        self.assertEqual(answered[(int(self.students[1]), "M7")]["n"], 1)
        modules = [
            row["module"] for row in self.school.class_participation(self.class_id)["modules"]
        ]
        self.assertIn("M7", modules)
        self._seed_module_answer("M8", "O1", "q-office-blank")
        office, _answered_office = self.school._participation_counts(self.class_id, "M8")
        self.assertNotIn("M8", office)

    def test_quit_without_a_run_key_clears_only_that_session(self) -> None:
        """A live session with no run key still quits, and only that session."""
        sid = self._arm(is_test=False, module="M1", slot="C3")
        self.school.conn.execute(
            "UPDATE live_class_sessions SET run_key = '' WHERE id = ?",
            (sid,),
        )
        self.school.conn.commit()
        self.assertTrue(
            self.school.record_engagement_event(
                "present.join", session_id=sid, student_id=int(self.students[0])
            )
        )
        other_sid = int(sid) + 1000
        self.school.engagement.append(
            {
                "class_id": int(self.class_id),
                "live_session": other_sid,
                "run_key": "",
                "student_id": int(self.students[1]),
                "kind": "present.join",
                "ts": "2026-10-17T13:21:00",
            }
        )
        response = self.client.post(
            f"/staff/class/{self.class_id}/quit-live",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.school.get_live_session(sid))
        self.assertEqual(
            self.school._count_engagement_rows(
                [sid], [], class_id=int(self.class_id), require_session=True
            ),
            0,
        )
        self.assertEqual(
            self.school._count_engagement_rows(
                [other_sid], [], class_id=int(self.class_id), require_session=True
            ),
            1,
        )

    def test_quit_drops_fresh_board_ink(self) -> None:
        """Quit deletes Fresh-board ink stored under the run key's ~g suffix."""
        sid = self._arm(is_test=False, module="M2", slot="C1")
        self.school.conn.execute(
            "UPDATE live_class_sessions SET board_gen = 1 WHERE id = ?",
            (sid,),
        )
        self.school.conn.commit()
        base = str(self.school.get_live_session(sid)["run_key"] or "")
        fresh = f"{base}~g1"
        stroke = [{"type": "stroke_add", "id": "mark", "points": [[0.1, 0.2]]}]
        self.school.boards.append_ops(base, "shared", owner="teacher", ops=stroke)
        self.school.boards.append_ops(
            fresh,
            "shared",
            owner="teacher",
            ops=[{"type": "stroke_add", "id": "fresh", "points": [[0.3, 0.4]]}],
        )
        self.assertGreater(self.school.boards.count_generation_ops(base), 0)
        response = self.client.post(
            f"/staff/class/{self.class_id}/quit-live",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.school.boards.count_run_ops(base), 0)
        self.assertEqual(self.school.boards.count_run_ops(fresh), 0)
        self.assertEqual(self.school.boards.count_generation_ops(base), 0)

    def test_quit_drops_a_team_draw_image(self) -> None:
        """Quit deletes a team draw image stored for that session."""
        sid = self._arm(is_test=False, module="M2", slot="C2")
        self.school.conn.execute(
            """
            INSERT INTO live_team_draw_images (
                live_session_id, live_item_id, team_id, image_png, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (sid, 1, 1, b"\x89PNG\r\n", "2026-10-17T13:20:00"),
        )
        kept_sid = int(sid) + 1000
        self.school.conn.execute(
            """
            INSERT INTO live_team_draw_images (
                live_session_id, live_item_id, team_id, image_png, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (kept_sid, 1, 1, b"\x89PNG\r\n", "2026-10-17T13:20:00"),
        )
        self.school.conn.commit()
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM live_team_draw_images WHERE live_session_id = ?",
                (sid,),
            ),
            1,
        )
        response = self.client.post(
            f"/staff/class/{self.class_id}/quit-live",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM live_team_draw_images WHERE live_session_id = ?",
                (sid,),
            ),
            0,
        )
        self.assertEqual(
            self._count(
                "SELECT COUNT(*) AS n FROM live_team_draw_images WHERE live_session_id = ?",
                (kept_sid,),
            ),
            1,
        )

    def test_start_and_quit_continue_when_presence_is_down(self) -> None:
        """A down presence store does not stop Start or Quit."""
        from unittest import mock

        from live_presence import LivePresenceUnavailable

        class _Down:
            """Presence stand-in that reports the store is down."""

            def delete_session_tree(self, session_id: int) -> None:
                """Raise the error a dead Postgres pool raises.

                Args:
                    session_id: ``live_class_sessions.id``.
                """
                raise LivePresenceUnavailable("live presence store unavailable")

            def upsert_session(self, session_row: dict) -> None:
                """Skip the mirror write while the store is down.

                Args:
                    session_row: ``live_class_sessions`` mapping.
                """
                return None

            def close(self) -> None:
                """No pool to close."""
                return None

        self.school.presence = _Down()
        with mock.patch("live_presence.note_presence_blip") as blip:
            started = self.client.post(
                f"/staff/class/{self.class_id}/run-live",
                follow_redirects=False,
            )
            self.assertEqual(started.status_code, 302)
            live = self.school.get_active_live_session_for_teacher(
                int(self.teacher["id"])
            )
            self.assertIsNotNone(live)
            self.assertEqual(int(live["class_id"]), int(self.class_id))
            quit_response = self.client.post(
                f"/staff/class/{self.class_id}/quit-live",
                follow_redirects=False,
            )
            self.assertEqual(quit_response.status_code, 302)
        details = [str(call.args[0]) for call in blip.call_args_list if call.args]
        self.assertGreaterEqual(details.count("presence delete failed"), 2)
        self.assertIsNone(
            self.school.get_active_live_session_for_teacher(int(self.teacher["id"]))
        )


    def _visit_token(self, session_id: int, student_id: int) -> str:
        """Current visit token for one attendee.

        Args:
            session_id: ``live_class_sessions.id``.
            student_id: Roster ``students.id``.

        Returns:
            The token. Callers must not log or assert its text.
        """
        attendee = self.school.get_live_session_attendee(
            int(session_id), int(student_id)
        )
        self.assertIsNotNone(attendee)
        return str((attendee or {}).get("visit_token") or "")

    def _open_signed_in(self, session_id: int, student_id: int):
        """Open the browser that student already joined with.

        Args:
            session_id: ``live_class_sessions.id``.
            student_id: Roster ``students.id``.

        Returns:
            A test client sitting on that student's live home.
        """
        self.school.game.set_character(self.class_id, int(student_id), "fox")
        attendee = self.school.get_live_session_attendee(
            int(session_id), int(student_id)
        )
        self.assertIsNotNone(attendee)
        client = self.app.test_client()
        resumed = client.get(
            "/student/s/" + str(attendee.get("visit_token") or ""),
            follow_redirects=True,
        )
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(resumed.request.path, "/student/home")
        return client

    def _saved_rows(self, session_id: int) -> dict:
        """Counts Done must leave unchanged.

        Args:
            session_id: ``live_class_sessions.id``.
        """
        tracker = self._tracker_id(session_id)
        scores = self.school.game.conn.execute(
            "SELECT COUNT(*) AS n FROM session_scores WHERE session_id = ?",
            (tracker,),
        ).fetchone()
        rows = {
            "status": str(self.school.get_live_session(session_id)["status"]),
            "celebrate": bool(self.school.session_is_celebrating(session_id)),
            "attendees": self._count(
                "SELECT COUNT(*) AS n FROM live_session_attendees WHERE live_session_id = ?",
                (session_id,),
            ),
            "left": self._count(
                """
                SELECT COUNT(*) AS n FROM live_session_attendees
                WHERE live_session_id = ? AND left_at IS NOT NULL
                """,
                (session_id,),
            ),
            "events": self._count("SELECT COUNT(*) AS n FROM engagement_events"),
            "history": self._count("SELECT COUNT(*) AS n FROM observation_history"),
            "links": self._count("SELECT COUNT(*) AS n FROM catchup_links"),
            "columns": self._gradebook_columns(),
            "scores": int(scores["n"] or 0),
        }
        presence = getattr(self.school, "presence", None)
        if presence is not None:
            rows["present"] = int(presence.present_count(session_id))
        return rows

    def _one_link(self) -> dict:
        """The catch-up row End wrote for this class."""
        rows = self.school.list_catchup_links(self.class_id)
        self.assertEqual(len(rows), 1)
        return rows[0]

    def _portal_code(self) -> str:
        """Catch-up code for the one link End just wrote. Not for logging."""
        code = str(self._one_link().get("portal_join_code") or "").strip()
        self.assertTrue(bool(code), "catch-up code missing")
        return code

    def _catchup_path(self) -> str:
        """Canonical catch-up path. The caller must not log it."""
        link = self._one_link()
        return self.catchup_url(str(link.get("token") or ""))

    def _assert_code_box(self, client) -> None:
        """Home shows the join form and not the celebration."""
        page = client.get("/", follow_redirects=True)
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertTrue('id="student-code-panel"' in html, "code box missing")
        self.assertTrue("How was class?" not in html, "still on celebration")
        self.assertTrue('id="celebration-done"' not in html, "Done still showing")

    def _assert_catchup_reachable(self, client) -> None:
        """The same browser can open the PIN prompt and the catch-up link."""
        code = self._portal_code()
        posted = client.post(
            "/auth/student-code",
            data={"code": code, "name": "Ava"},
            follow_redirects=False,
        )
        self.assertEqual(posted.status_code, 302)
        self.assertIn("/student/catchup", posted.headers.get("Location") or "")
        pin = client.get("/student/catchup")
        pin_html = pin.get_data(as_text=True)
        self.assertTrue('id="landing_pin"' in pin_html, "pin prompt missing")
        self.assertTrue("How was class?" not in pin_html, "pin page bounced")
        link = client.get(self._catchup_path(), follow_redirects=True)
        link_html = link.get_data(as_text=True)
        self.assertTrue('id="celebration-done"' not in link_html, "catch-up link bounced")
        self.assertTrue(
            'id="landing_pin"' in link_html
            or 'id="student-code-panel"' in link_html
            or "Your 4-digit PIN" in link_html,
            "catch-up link did not open",
        )

    def _press_done(self, client, session_id: int, *, submitted: bool) -> None:
        """Show Done, optionally skip How-was-class, then press Done.

        The row snapshot is taken after How-was-class so that skip can
        write its own event. Done itself must not change the snapshot.

        Args:
            client: The celebrating student's browser.
            session_id: ``live_class_sessions.id``.
            submitted: When True, skip How-was-class before Done.
        """
        home = client.get("/student/home")
        html = home.get_data(as_text=True)
        self.assertIn(">Done<", html)
        self.assertIn('id="celebration-done"', html)
        self.assertIn('name="visit_token"', html)
        self.assertIn("no-store", home.headers.get("Cache-Control") or "")
        if submitted:
            skipped = client.post(
                "/api/student/exit-feedback",
                json={"skip": "1"},
            )
            self.assertEqual(skipped.status_code, 200)
            still = client.get("/student/home").get_data(as_text=True)
            self.assertIn(">Done<", still)
        before = self._saved_rows(session_id)
        blocked = client.post(
            "/student/celebration-done",
            headers={"Sec-Fetch-Site": "cross-site"},
            follow_redirects=False,
        )
        self.assertEqual(blocked.status_code, 403)
        self.assertIn(">Done<", client.get("/student/home").get_data(as_text=True))
        done = client.post("/student/celebration-done", follow_redirects=False)
        self.assertEqual(done.status_code, 302)
        self.assertEqual(done.headers.get("Location"), "/")
        self.assertIn("no-store", done.headers.get("Cache-Control") or "")
        from student_portal import REJOIN_COOKIE_NAME

        self.assertIsNone(client.get_cookie(REJOIN_COOKIE_NAME))
        self.assertEqual(self._saved_rows(session_id), before)

    def test_done_is_hidden_and_refused_while_class_is_live(self) -> None:
        """A live class has no Done button, and the route refuses."""
        sid = self._arm(is_test=False, module="M1", slot="C1")
        student = self._open_signed_in(sid, int(self.students[0]))
        html = student.get("/student/home").get_data(as_text=True)
        self.assertNotIn('id="celebration-done"', html)
        self.assertNotIn(">Done<", html)
        token = self._visit_token(sid, int(self.students[0]))
        refused = student.post(
            "/student/celebration-done", follow_redirects=False
        )
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.get_data(as_text=True), "Class is still running.")
        self.assertIsNotNone(
            self.school.resolve_student_visit_token(token, allow_left=True)
        )
        self.assertEqual(student.get("/student/home").status_code, 200)

    def _check_done_after_end(self, *, submitted: bool, quit_after: bool) -> None:
        """End, optionally Quit, then Done on one browser only.

        Args:
            submitted: When True, skip How-was-class before Done.
            quit_after: When True, Quit after End and expect the 409 page.
        """
        sid = self._arm(is_test=False, module="M1", slot="C1")
        self.school.join_live_class_session(
            sid, int(self.students[1]), codename="Ben"
        )
        ava = self._open_signed_in(sid, int(self.students[0]))
        ben = self._open_signed_in(sid, int(self.students[1]))
        self._post_end()
        if quit_after:
            quit_response = self.client.post(
                f"/staff/class/{self.class_id}/quit-live",
                follow_redirects=False,
            )
            self.assertEqual(quit_response.status_code, 409)
        stuck = ava.get("/", follow_redirects=True)
        stuck_html = stuck.get_data(as_text=True)
        self.assertIn('id="celebration-done"', stuck_html)
        self.assertNotIn('id="student-code-panel"', stuck_html)
        landing = ava.get("/student/catchup", follow_redirects=True)
        self.assertIn('id="celebration-done"', landing.get_data(as_text=True))
        link_before = ava.get(self._catchup_path(), follow_redirects=True)
        self.assertIn('id="celebration-done"', link_before.get_data(as_text=True))
        self._press_done(ava, sid, submitted=submitted)
        self._assert_code_box(ava)
        self._assert_catchup_reachable(ava)
        ben_html = ben.get("/", follow_redirects=True).get_data(as_text=True)
        self.assertIn('id="celebration-done"', ben_html)
        self.assertNotIn('id="student-code-panel"', ben_html)

    def test_done_signs_out_after_end_without_feedback(self) -> None:
        """Done after End signs out a student who has not sent How-was-class."""
        self._check_done_after_end(submitted=False, quit_after=False)

    def test_done_signs_out_after_end_with_feedback(self) -> None:
        """Done after End still signs out once How-was-class was skipped."""
        self._check_done_after_end(submitted=True, quit_after=False)

    def test_done_works_after_end_then_quit(self) -> None:
        """End then Quit is a 409, and Done still signs that browser out."""
        self._check_done_after_end(submitted=False, quit_after=True)



    def test_done_revokes_the_visit_token(self) -> None:
        """Done then the old visit token cannot reopen home or save feedback."""
        sid = self._arm(is_test=False, module="M1", slot="C1")
        self.school.join_live_class_session(
            sid, int(self.students[1]), codename="Ben"
        )
        ava_token = self._visit_token(sid, int(self.students[0]))
        ben_token = self._visit_token(sid, int(self.students[1]))
        ava = self._open_signed_in(sid, int(self.students[0]))
        ben = self._open_signed_in(sid, int(self.students[1]))
        self._post_end()
        before_rows = self._saved_rows(sid)
        feedback_before = self._count("SELECT COUNT(*) AS n FROM live_class_feedback")
        submitted_before = self._count(
            """
            SELECT COUNT(*) AS n FROM live_class_feedback
            WHERE submitted_at IS NOT NULL
            """
        )
        pending = self.school.pending_exit_feedback(
            class_id=self.class_id, student_id=int(self.students[0])
        )
        self.assertIsNotNone(pending)
        feedback_token = str((pending or {}).get("token") or "")
        self._press_done(ava, sid, submitted=False)
        self.assertEqual(self._saved_rows(sid), before_rows)
        fresh = self.app.test_client()
        refused = fresh.get(
            "/student/home",
            query_string={"v": ava_token},
            follow_redirects=False,
        )
        self.assertEqual(refused.status_code, 302)
        self.assertEqual(refused.headers.get("Location"), "/")
        self.assertIn("no-store", refused.headers.get("Cache-Control") or "")
        landed = fresh.get(
            "/student/home",
            query_string={"v": ava_token},
            follow_redirects=True,
        )
        landed_html = landed.get_data(as_text=True)
        self.assertIn('id="student-code-panel"', landed_html)
        self.assertNotIn('id="celebration-done"', landed_html)
        posted = fresh.post(
            "/api/student/exit-feedback",
            json={
                "mood": "ok",
                "comment": "after done",
                "token": feedback_token,
                "visit_token": ava_token,
            },
            headers={"X-Student-Visit-Token": ava_token},
        )
        self.assertEqual(posted.status_code, 401)
        self.assertEqual(
            self._count("SELECT COUNT(*) AS n FROM live_class_feedback"),
            feedback_before,
        )
        self.assertEqual(
            self._count(
                """
                SELECT COUNT(*) AS n FROM live_class_feedback
                WHERE submitted_at IS NOT NULL
                """
            ),
            submitted_before,
        )
        self.assertFalse(
            any(
                (row.get("comment") or "") == "after done"
                for row in self.school.list_exit_feedback_for_class(self.class_id)
            )
        )
        other = self.app.test_client()
        ben_page = other.get(
            "/student/home",
            query_string={"v": ben_token},
            follow_redirects=True,
        )
        self.assertEqual(ben_page.status_code, 200)
        self.assertIn('id="celebration-done"', ben_page.get_data(as_text=True))
        self.assertIn(
            'id="celebration-done"', ben.get("/student/home").get_data(as_text=True)
        )
        self._assert_catchup_reachable(ava)
        again = fresh.post(
            "/student/celebration-done",
            data={"visit_token": ava_token},
            follow_redirects=False,
        )
        self.assertEqual(again.status_code, 302)
        self.assertEqual(again.headers.get("Location"), "/")
        self.assertNotIn("still running", again.get_data(as_text=True).lower())

    def test_done_works_from_a_token_only_page(self) -> None:
        """Done resolves the student from the visit token when no cookie is set."""
        sid = self._arm(is_test=False, module="M1", slot="C1")
        self.school.game.set_character(self.class_id, int(self.students[0]), "fox")
        token = self._visit_token(sid, int(self.students[0]))
        self._post_end()
        viewer = self.app.test_client()
        page = viewer.get("/student/home", query_string={"v": token})
        self.assertEqual(page.status_code, 200)
        self.assertIn(">Done<", page.get_data(as_text=True))
        self.assertIn("no-store", page.headers.get("Cache-Control") or "")
        client = self.app.test_client()
        before = self._saved_rows(sid)
        done = client.post(
            "/student/celebration-done",
            data={"visit_token": token},
            follow_redirects=False,
        )
        self.assertEqual(done.status_code, 302)
        self.assertEqual(done.headers.get("Location"), "/")
        self.assertIn("no-store", done.headers.get("Cache-Control") or "")
        self.assertEqual(self._saved_rows(sid), before)
        refused = client.get(
            "/student/home",
            query_string={"v": token},
            follow_redirects=False,
        )
        self.assertEqual(refused.status_code, 302)
        self.assertEqual(refused.headers.get("Location"), "/")
        self._assert_catchup_reachable(client)

    def test_done_signs_out_when_the_class_is_gone(self) -> None:
        """A missing class does not claim the class is still running."""
        client = self.app.test_client()
        done = client.post("/student/celebration-done", follow_redirects=False)
        self.assertEqual(done.status_code, 302)
        self.assertEqual(done.headers.get("Location"), "/")
        self.assertNotIn("still running", done.get_data(as_text=True).lower())

def _pg_admin_url() -> str:
    """Postgres admin URL for the live-presence tests, or empty."""
    return (os.environ.get("LIVE_PRESENCE_TEST_URL") or "").strip()


@unittest.skipUnless(_pg_admin_url(), "LIVE_PRESENCE_TEST_URL is not set")
class PostgresQuitScopeTests(EndAlwaysSavesTests):
    """The quit-scope cases again with the engagement log on Postgres."""

    def setUp(self) -> None:
        import uuid
        from urllib.parse import urlsplit, urlunsplit

        import psycopg

        admin = _pg_admin_url()
        self.pg_db = f"lloves_mck275_{uuid.uuid4().hex[:8]}"
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{self.pg_db}"')
        self.addCleanup(self._drop_pg, admin)
        parts = urlsplit(admin)
        os.environ["LIVE_DATABASE_URL"] = urlunsplit(
            (parts.scheme, parts.netloc, f"/{self.pg_db}", parts.query, "")
        )
        try:
            super().setUp()
        finally:
            os.environ.pop("LIVE_DATABASE_URL", None)
        self.assertIsNotNone(self.school.presence)

    def _drop_pg(self, admin: str) -> None:
        """Drop this test's Postgres database.

        Args:
            admin: Admin connection string.
        """
        import psycopg

        school = getattr(self, "school", None)
        if school is not None:
            try:
                school.close()
            except Exception:
                pass
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{self.pg_db}" WITH (FORCE)')

    def _presence_count(self, table: str, session_id: int) -> int:
        """Count presence rows for one session.

        Args:
            table: ``live_presence_sessions`` or ``live_presence_attendees``.
            session_id: ``live_class_sessions.id``.

        Returns:
            Row count.
        """
        import psycopg

        column = "id" if table == "live_presence_sessions" else "live_session_id"
        with psycopg.connect(self.school.presence.dsn) as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE {column} = %s",
                (int(session_id),),
            ).fetchone()
        return int(row[0] or 0)

    def test_quit_clears_presence_and_the_next_start_is_empty(self) -> None:
        """Quit leaves no presence rows, and the next Start shows nobody present."""
        sid = self._arm(is_test=False, module="M1", slot="C1")
        presence = self.school.presence
        self.assertGreater(presence.present_count(sid), 0)
        self.assertEqual(self._presence_count("live_presence_sessions", sid), 1)
        self.assertGreater(self._presence_count("live_presence_attendees", sid), 0)
        response = self.client.post(
            f"/staff/class/{self.class_id}/quit-live",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._presence_count("live_presence_sessions", sid), 0)
        self.assertEqual(self._presence_count("live_presence_attendees", sid), 0)
        nxt = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=False
        )
        nxt_id = int(nxt["id"])
        self.assertEqual(nxt_id, sid)
        self.assertEqual(presence.present_count(nxt_id), 0)
        self.assertEqual(self._presence_count("live_presence_attendees", nxt_id), 0)

    def tearDown(self) -> None:
        school = getattr(self, "school", None)
        if school is not None:
            try:
                school.close()
            except Exception:
                pass
            self.school = None
        super().tearDown()



if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""MCK-269: a test live class saves, stays out of real counts, and can be deleted."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from test_participation_mck198 import Base  # noqa: E402


class TestLiveClassTests(Base):
    """Run a real class, then a test class, then delete the test class."""

    def setUp(self) -> None:
        super().setUp()
        self.client = self.app.test_client()
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        code = self.school.get_user_by_email("teacher@gmail.com")["verification_code"]
        self.client.post("/verify-email", data={"code": code})

    def _publish_one_answer(self, session_id: int, module: str, slot: str) -> None:
        """Publish one individual item and one roster answer on a live session."""
        now = "2026-10-14T13:20:00"
        c = self.school.conn
        c.execute(
            """
            UPDATE live_class_sessions
            SET teacher_state_json = ?, started_at = ?
            WHERE id = ?
            """,
            (json.dumps({"live_module": module, "live_slot": slot}), now, int(session_id)),
        )
        prompt_id = c.execute(
            """
            INSERT INTO live_session_prompts (
                live_session_id, slide_index, kind, payload, created_at, updated_at
            ) VALUES (?, 1, 'poll', '{}', ?, ?)
            """,
            (int(session_id), now, now),
        ).lastrowid
        c.execute(
            """
            INSERT INTO live_session_items (
                live_session_id, placement_key, item_id, stage, kind, item_json,
                prompt_id, status, publish_mode, response_mode, published_at,
                created_at, updated_at
            ) VALUES (?, 'q0', 'q0', 'round', 'poll', ?, ?, 'closed',
                      'individual', 'individual', ?, ?, ?)
            """,
            (
                int(session_id),
                json.dumps({"text": "q0", "type": "mc", "options": [{"text": "A"}]}),
                prompt_id,
                now,
                now,
                now,
            ),
        )
        c.execute(
            """
            INSERT INTO live_session_responses (
                prompt_id, student_id, response_json, created_at, updated_at
            ) VALUES (?, ?, '{}', ?, ?)
            """,
            (int(prompt_id), int(self.students[0]), now, now),
        )
        c.commit()

    def _snapshot(self) -> dict:
        """Real counts a test class must not move."""
        totals = self.school.official_gradebook_totals(self.class_id)["totals"]
        picker = self.school.set_class_picker(self.class_id)
        attendance = self.school.attendance_week_grid(self.class_id)
        participation = self.school.participation_week_grid(self.class_id)
        slides = [
            (str(row["live_module"]), str(row["live_slot"]))
            for row in self.school.list_lesson_slides_latest(self.class_id)
            if not int(row.get("is_test") or 0)
        ]
        test_ids = self.school.test_mgs_session_ids(self.class_id)
        present = 0
        marks = self.school.game.attendance_score_rows(self.class_id)
        for row in marks.get("scores") or []:
            if int(row.get("session_id") or 0) in test_ids:
                continue
            if int(row.get("present") or 0):
                present += 1
        real_sessions = [
            row
            for row in marks.get("sessions") or []
            if str(row.get("status") or "") != "template"
            and int(row.get("id") or 0) not in test_ids
        ]
        return {
            "totals": dict(totals),
            "label": picker["label"],
            "module": picker["module"],
            "slot": picker["slot"],
            "part_totals": dict(participation.get("totals") or {}),
            "slides": slides,
            "attendance_columns": len(real_sessions),
            "grid_days": len(attendance.get("date_labels") or []),
            "present": present,
        }

    def test_is_test_column_migrates(self) -> None:
        cols = {
            row["name"]
            for row in self.school.conn.execute("PRAGMA table_info(live_class_sessions)")
        }
        self.assertIn("is_test", cols)
        self.assertIn("created_as_test", cols)
        indexed = {
            str(row["name"])
            for row in self.school.conn.execute(
                "PRAGMA index_info(idx_catchup_links_class_lesson)"
            )
        }
        self.assertIn("is_test", indexed)
        slide_cols = {
            row["name"]
            for row in self.school.conn.execute("PRAGMA table_info(lesson_slides_latest)")
        }
        self.assertIn("is_test", slide_cols)
        self.assertIn("live_session_id", slide_cols)

    def test_run_test_then_delete_leaves_real_counts(self) -> None:
        real = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=False
        )
        self._publish_one_answer(int(real["id"]), "M1", "C1")
        self.school.finish_live_class(
            self.class_id,
            celebrate=True,
            live_run=True,
        )
        tracker = self.school.game.conn.execute(
            """
            SELECT id FROM sessions
            WHERE class_id = ? AND status != 'template'
            ORDER BY id DESC LIMIT 1
            """,
            (self.class_id,),
        ).fetchone()
        self.assertIsNotNone(tracker)
        self.school.game.conn.execute(
            """
            UPDATE session_scores
            SET present = 1, points = 4
            WHERE session_id = ? AND student_id = ?
            """,
            (int(tracker["id"]), int(self.students[0])),
        )
        self.school.game.conn.commit()
        before = self._snapshot()
        self.assertEqual(before["present"], 1)
        self.assertGreater(float(list(before["totals"].values())[0]), 0)

        test = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=True
        )
        self.assertEqual(int(test["is_test"]), 1)
        self._publish_one_answer(int(test["id"]), "M2", "C1")
        self.school.game._set_current_class(self.class_id)
        current_before = self.school.game._current_class_id()
        self.school.begin_or_resume_live_game(self.class_id)
        self.assertEqual(self.school.game._current_class_id(), current_before)
        self.school.finish_live_class(
            self.class_id,
            celebrate=True,
            live_run=False,
        )
        self.assertEqual(self._snapshot(), before)
        test_slides = [
            row
            for row in self.school.list_lesson_slides_latest(self.class_id)
            if int(row.get("is_test") or 0) == 1
        ]
        self.assertEqual(len(test_slides), 1)
        link = self.school.conn.execute(
            """
            SELECT token, is_open FROM catchup_links
            WHERE live_session_id = ? AND COALESCE(is_test, 0) = 1
            """,
            (int(test["id"]),),
        ).fetchone()
        self.assertIsNotNone(link)
        resolved = self.school.resolve_catchup_link(str(link["token"]))
        self.assertIsNotNone(resolved)
        self.assertEqual(int(resolved["is_test"]), 1)
        part = self.school.participation_week_grid(self.class_id)
        flags = [meta.get("is_test") for meta in (part.get("session_meta") or {}).values()]
        self.assertIn(True, flags)

        with self.assertRaises(PermissionError):
            real_id = int(real["id"])
            if self.school.get_live_session(real_id) is None:
                real_id = int(
                    self.school.conn.execute(
                        """
                        SELECT id FROM live_class_sessions
                        WHERE class_id = ? AND COALESCE(is_test, 0) = 0
                        ORDER BY id DESC LIMIT 1
                        """,
                        (self.class_id,),
                    ).fetchone()["id"]
                )
            self.school.delete_test_live_class(self.class_id, real_id)

        class _Presence:
            def __init__(self) -> None:
                self.deleted: list[int] = []

            def delete_session_tree(self, session_id: int) -> None:
                """Record the presence delete. No join code is stored."""
                self.deleted.append(int(session_id))

        presence = _Presence()
        self.school.presence = presence
        result = self.school.delete_test_live_class(self.class_id, int(test["id"]))
        self.assertEqual(result, {"ok": True, "deleted_session_id": int(test["id"])})
        self.assertEqual(presence.deleted, [int(test["id"])])
        self.assertIsNone(self.school.get_live_session(int(test["id"])))
        self.assertIsNone(self.school.resolve_catchup_link(str(link["token"])))
        self.assertEqual(self._snapshot(), before)
        self.assertFalse(
            [
                row
                for row in self.school.list_lesson_slides_latest(self.class_id)
                if int(row.get("is_test") or 0) == 1
            ]
        )

    def test_attendance_only_test_is_tagged_in_participation(self) -> None:
        """A test class with no answers still shows as TEST, not the next real slot."""
        test = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=True
        )
        self.school.set_live_session_teacher_state(
            int(test["id"]), live_module="M2", live_slot="C3"
        )
        self.school.begin_or_resume_live_game(self.class_id)
        self.school.finish_live_class(
            self.class_id,
            celebrate=True,
            live_run=True,
        )
        grid = self.school.participation_week_grid(self.class_id)
        tagged = [
            column
            for band in (grid.get("module_bands") or {}).get("bands") or []
            for column in band.get("columns") or []
            if column.get("is_test")
        ]
        self.assertEqual(len(tagged), 1)
        self.assertEqual(tagged[0]["module"], "M2")
        self.assertEqual(tagged[0]["slot"], "C3")
        self.assertEqual(int(tagged[0]["live_session_id"]), int(test["id"]))
        roles = {
            str(band.get("module")): band.get("role")
            for band in (grid.get("module_bands") or {}).get("bands") or []
        }
        self.assertEqual(roles.get("M2"), "open")
        current = (grid.get("module_bands") or {}).get("current_module")
        self.assertNotEqual(current, "M2")

    def test_start_wipe_keeps_the_other_kind(self) -> None:
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=False
        )
        self.school.finish_live_class(self.class_id, celebrate=True, live_run=True)
        test = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=True
        )
        self.school.finish_live_class(self.class_id, celebrate=True, live_run=True)
        self.assertTrue(self.school.session_is_celebrating(int(live["id"])))
        self.assertTrue(self.school.session_is_celebrating(int(test["id"])))
        fresh_live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=False
        )
        self.assertIsNotNone(self.school.get_live_session(int(live["id"])))
        self.assertFalse(self.school.session_is_celebrating(int(live["id"])))
        self.assertTrue(self.school.session_is_celebrating(int(test["id"])))
        self.school.finish_live_class(self.class_id, celebrate=True, live_run=True)
        self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=True
        )
        self.assertIsNotNone(self.school.get_live_session(int(test["id"])))
        self.assertFalse(self.school.session_is_celebrating(int(test["id"])))
        self.assertTrue(self.school.session_is_celebrating(int(fresh_live["id"])))

    def test_catchup_answers_stay_on_test_run_key(self) -> None:
        """Catch-up answers for a test class credit only that test run_key."""
        real = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=False
        )
        self._publish_one_answer(int(real["id"]), "M1", "C1")
        self.school.finish_live_class(
            self.class_id,
            celebrate=True,
            live_run=True,
        )
        before = self._snapshot()

        test = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=True
        )
        self._publish_one_answer(int(test["id"]), "M2", "C2")
        self.school.finish_live_class(
            self.class_id,
            celebrate=True,
            live_run=True,
        )
        test_row = self.school.conn.execute(
            "SELECT run_key FROM live_class_sessions WHERE id = ?",
            (int(test["id"]),),
        ).fetchone()
        link = self.school.conn.execute(
            """
            SELECT token, run_key, live_session_id FROM catchup_links
            WHERE live_session_id = ? AND COALESCE(is_test, 0) = 1
            """,
            (int(test["id"]),),
        ).fetchone()
        self.assertIsNotNone(link)
        self.assertEqual(str(link["run_key"]), str(test_row["run_key"]))
        self.assertEqual(int(link["live_session_id"]), int(test["id"]))
        live_answer = self.school.conn.execute(
            """
            SELECT run_key, source, question_key FROM participation_answers
            WHERE class_id = ? AND run_key = ? AND source = 'live'
            ORDER BY id DESC LIMIT 1
            """,
            (self.class_id, str(test_row["run_key"])),
        ).fetchone()
        self.assertIsNotNone(live_answer)
        qkey = str(live_answer["question_key"])
        allowed = {
            q["question_key"]
            for q in self.school.catchup_questions(
                str(link["token"]), int(self.students[0])
            )
            if not q["answered"]
        }
        if qkey in allowed:
            self.assertTrue(
                self.school.submit_catchup_answer(
                    str(link["token"]), int(self.students[0]), qkey, "A"
                )
            )
            catchup_answer = self.school.conn.execute(
                """
                SELECT run_key FROM participation_answers
                WHERE class_id = ? AND run_key = ? AND source = 'catchup'
                  AND question_key = ?
                """,
                (self.class_id, str(test_row["run_key"]), qkey),
            ).fetchone()
            self.assertIsNotNone(catchup_answer)
        self.assertEqual(self._snapshot(), before)

    def test_delete_test_disables_catchup_immediately(self) -> None:
        """Deleting a test class removes the link so catch-up cannot resolve."""
        test = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=True
        )
        self._publish_one_answer(int(test["id"]), "M1", "C2")
        self.school.finish_live_class(
            self.class_id,
            celebrate=True,
            live_run=True,
        )
        link = self.school.conn.execute(
            """
            SELECT token FROM catchup_links
            WHERE live_session_id = ? AND COALESCE(is_test, 0) = 1
            """,
            (int(test["id"]),),
        ).fetchone()
        self.assertIsNotNone(link)
        token = str(link["token"])
        self.assertIsNotNone(self.school.resolve_catchup_link(token))
        self.school.delete_test_live_class(self.class_id, int(test["id"]))
        self.assertIsNone(self.school.resolve_catchup_link(token))
        with self.assertRaises(PermissionError):
            self.school.submit_catchup_answer(
                token, int(self.students[0]), "item:M1-q0", "A"
            )

    def test_course_page_picks_test_at_the_start(self) -> None:
        page = self.client.get(f"/staff/class/{self.class_id}?tab=live")
        body = page.get_data(as_text=True)
        self.assertIn('id="set-class-run-kind-test"', body)
        self.assertIn("set-class-run-kind-toggle", body)
        self.assertNotIn('name="run_mode"', body)
        self.assertIn("Delete this test class?", body)
        self.assertIn("info-popover-test-run-kind", body)
        self.assertNotIn('name="save_attendance"', body)
        self.assertNotIn('name="save_participation"', body)
        self.assertIn(">End Class<", body)
        self.assertIn("Quit without saving?", body)



def _real_deck_table_snapshot(school, class_id: int) -> str:
    """Stable dump of real (non-test-storage) deck rows for one class."""
    from live_deck_test_storage import TEST_DECK_SLOT_PREFIX

    prefix = TEST_DECK_SLOT_PREFIX.upper()
    chunks: list[str] = []
    for table in (
        "class_live_playlist_placements",
        "class_live_playlist_item_overrides",
        "class_live_playlist_pages",
        "class_live_media_overlays",
        "class_live_deck_seeds",
    ):
        rows = school.conn.execute(
            f"SELECT * FROM {table} WHERE class_id = ? ORDER BY id",
            (int(class_id),),
        ).fetchall()
        kept = [
            tuple(row)
            for row in rows
            if not str(row["slot"] or "").upper().startswith(prefix)
        ]
        chunks.append(repr((table, kept)))
    return "\n".join(chunks)


class TestLiveClassDeckIsolationTests(Base):
    """Test runs must not overwrite the real slot deck."""

    def setUp(self) -> None:
        super().setUp()

    def test_test_deck_edit_delete_preserves_real_deck(self) -> None:
        self.school.apply_class_deck_seed(
            self.class_id, "M1", "C1", mode="blank"
        )
        self.school.upsert_class_live_media_copy(
            self.class_id,
            "M1",
            "C1",
            stem="Real deck stem",
            caption="Real caption",
        )
        before = _real_deck_table_snapshot(self.school, self.class_id)
        test = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=True
        )
        self.school.set_live_session_teacher_state(
            int(test["id"]), live_module="M1", live_slot="C1"
        )
        self.school.apply_class_deck_seed(
            self.class_id, "M1", "C1", mode="test_copy"
        )
        self.school.upsert_class_live_media_copy(
            self.class_id,
            "M1",
            "C1",
            stem="Test-only stem",
            caption="Test-only caption",
        )
        from live_deck_test_storage import test_deck_storage_slot

        test_slot = test_deck_storage_slot(int(test["id"]))
        test_media = self.school.get_class_live_media_copy(
            self.class_id, "M1", "C1"
        )
        self.assertEqual(test_media["stem"], "Test-only stem")
        seed = self.school.conn.execute(
            """
            SELECT slot FROM class_live_deck_seeds
            WHERE class_id = ? AND slot = ?
            """,
            (self.class_id, test_slot),
        ).fetchone()
        self.assertIsNotNone(seed)
        self.school.delete_test_live_class(self.class_id, int(test["id"]))
        after = _real_deck_table_snapshot(self.school, self.class_id)
        self.assertEqual(before, after)
        real_media = self.school.get_class_live_media_copy(
            self.class_id, "M1", "C1", real_deck_only=True
        )
        self.assertEqual(real_media["stem"], "Real deck stem")


def _pg_admin_url() -> str:
    return (os.environ.get("LIVE_PRESENCE_TEST_URL") or "").strip()


@unittest.skipUnless(_pg_admin_url(), "LIVE_PRESENCE_TEST_URL is not set")
class TestLiveClassPostgresDeleteTests(Base):
    """Delete test class against a real Postgres presence store."""

    def setUp(self) -> None:
        import logging
        import tempfile
        import uuid
        from urllib.parse import urlsplit, urlunsplit

        import psycopg

        from app import create_app

        logging.disable(logging.WARNING)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        admin = _pg_admin_url()
        self.pg_db = f"lloves_mck269_{uuid.uuid4().hex[:8]}"
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{self.pg_db}"')
        self.addCleanup(self._drop_pg, admin)
        parts = urlsplit(admin)
        presence_url = urlunsplit(
            (parts.scheme, parts.netloc, f"/{self.pg_db}", parts.query, "")
        )
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
            live_database_url=presence_url,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
        )
        self.school.set_offering_schedule(
            int(offering["id"]),
            live_days="M/W/F",
            live_time="9:15am",
            live_end_time="10:30am",
        )
        self.class_id = int(
            self.school.game.create_class(
                year="2026/27",
                semester="Semester 1",
                course_code="MCR3U",
                days_preset="M/W/F",
                time_label="2:00pm",
                codenames=["Ava", "Ben"],
                offering_id=int(offering["id"]),
                teacher_user_id=int(self.teacher["id"]),
            )["id"]
        )
        g = self.school.game.conn
        self.students = [
            int(r[0])
            for r in g.execute(
                "SELECT id FROM students WHERE class_id = ? ORDER BY id",
                (self.class_id,),
            ).fetchall()
        ]

    def _drop_pg(self, admin: str) -> None:
        import psycopg

        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{self.pg_db}" WITH (FORCE)')

    def test_delete_test_live_class_removes_postgres_presence(self) -> None:
        test = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=True
        )
        sid = int(test["id"])
        store = self.school.presence
        self.assertIsNotNone(store)
        store.upsert_session(
            {
                "id": sid,
                "class_id": int(self.class_id),
                "status": "active",
                "started_at": "2026-10-14T12:00:00",
                "ended_at": None,
            }
        )
        student_id = int(self.students[0])
        store.upsert_attendee(
            {
                "id": 880_001,
                "live_session_id": sid,
                "student_id": student_id,
                "participant_uuid": "",
                "visit_token": f"mck269-delete-{sid}",
                "codename": "Maple",
                "unmatched": 0,
                "joined_at": "2026-10-14T12:01:00",
                "left_at": None,
                "last_heartbeat_at": None,
            }
        )
        self.school.delete_test_live_class(self.class_id, sid)
        import psycopg

        with psycopg.connect(store.dsn) as check:
            left = check.execute(
                "SELECT COUNT(*) AS n FROM live_presence_sessions WHERE id = %s",
                (sid,),
            ).fetchone()
            self.assertEqual(int(left[0]), 0)
            attendees = check.execute(
                "SELECT COUNT(*) AS n FROM live_presence_attendees WHERE live_session_id = %s",
                (sid,),
            ).fetchone()
            self.assertEqual(int(attendees[0]), 0)

    def test_boot_migration_deletes_removed_board_keys(self) -> None:
        """Postgres boot deletes leftover student: and fb: ink once.

        The LIKE patterns are bound parameters. A literal percent in the
        SQL crashes psycopg inside ``_migrate_mck272_whiteboard``. A
        ``solo:`` row stays. The next boot sees the done flag and does
        not delete a key inserted after the first run.
        """
        import psycopg

        store = self.school.presence
        self.assertIsNotNone(store)
        run_key = "mck272-boot-migration"
        with psycopg.connect(store.dsn) as conn:
            conn.execute(
                "DELETE FROM board_flags WHERE name = %s",
                ("mck272_removed_keys",),
            )
            for key, seq in (("student:1", 1), ("fb:1", 1), ("team:0", 1), ("solo:9", 1)):
                conn.execute(
                    """
                    INSERT INTO board_ops (
                        run_key, board_key, board_seq, op, owner, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run_key,
                        key,
                        seq,
                        '{"type":"stroke_end","id":"x"}',
                        "teacher",
                        "2026-10-17T00:00:00",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO board_counters (run_key, board_key, next_seq)
                    VALUES (%s, %s, %s)
                    """,
                    (run_key, key, seq),
                )
                conn.execute(
                    """
                    INSERT INTO board_batches (
                        run_key, board_key, owner, client_batch_id, first_seq, last_seq
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (run_key, key, "teacher", f"batch-{key}", 1, seq),
                )
            conn.commit()
        self.school._migrate_mck272_whiteboard()
        with psycopg.connect(store.dsn) as conn:
            ops = conn.execute(
                "SELECT board_key FROM board_ops WHERE run_key = %s ORDER BY board_key",
                (run_key,),
            ).fetchall()
            counters = conn.execute(
                "SELECT board_key FROM board_counters WHERE run_key = %s ORDER BY board_key",
                (run_key,),
            ).fetchall()
            batches = conn.execute(
                "SELECT board_key FROM board_batches WHERE run_key = %s ORDER BY board_key",
                (run_key,),
            ).fetchall()
        self.assertEqual([row[0] for row in ops], ["solo:9", "team:0"])
        self.assertEqual([row[0] for row in counters], ["solo:9", "team:0"])
        self.assertEqual([row[0] for row in batches], ["solo:9", "team:0"])
        with psycopg.connect(store.dsn) as conn:
            conn.execute(
                """
                INSERT INTO board_ops (
                    run_key, board_key, board_seq, op, owner, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    run_key,
                    "student:2",
                    1,
                    '{"type":"stroke_end","id":"later"}',
                    "teacher",
                    "2026-10-17T00:00:00",
                ),
            )
            conn.commit()
        self.school._migrate_mck272_whiteboard()
        with psycopg.connect(store.dsn) as conn:
            again = conn.execute(
                "SELECT board_key FROM board_ops WHERE run_key = %s ORDER BY board_key",
                (run_key,),
            ).fetchall()
        self.assertEqual(
            [row[0] for row in again],
            ["solo:9", "student:2", "team:0"],
        )

    def test_delete_counts_postgres_fresh_board_ink(self) -> None:
        """A Fresh-board row on Postgres is leftover after delete-test."""
        import psycopg

        test = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=True
        )
        sid = int(test["id"])
        run_key = self.school.live_board_run_key(sid)
        store = self.school.presence
        self.assertIsNotNone(store)
        with psycopg.connect(store.dsn) as conn:
            conn.execute(
                """
                INSERT INTO board_ops (
                    run_key, board_key, board_seq, op, owner, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    run_key + "~g9",
                    "team:0",
                    1,
                    '{"type":"stroke_end","id":"fresh"}',
                    "teacher",
                    "2026-10-17T00:00:00",
                ),
            )
            conn.commit()
        with self.assertRaises(RuntimeError) as caught:
            self.school.delete_test_live_class(self.class_id, sid)
        self.assertIn("still has rows", str(caught.exception))

if __name__ == "__main__":
    unittest.main()

class TestLiveClassKindGuardTests(TestLiveClassTests):
    """409 when Test/Live would change after run activity."""

    def setUp(self) -> None:
        super().setUp()
        self.client = self.app.test_client()
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        code = self.school.get_user_by_email("teacher@gmail.com")["verification_code"]
        self.client.post("/verify-email", data={"code": code})

    def test_kind_switch_after_run_live_ignores_minds_on_prompt(self) -> None:
        """Run Live then Test works while only the automatic Minds-On exists."""
        rv = self.client.post(f"/staff/class/{self.class_id}/run-live")
        self.assertEqual(rv.status_code, 302, rv.status_code)
        live = self.school.get_active_live_session_for_teacher(int(self.teacher["id"]))
        self.assertIsNotNone(live)
        sid = int(live["id"])
        prompt_count = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM live_session_prompts WHERE live_session_id = ?",
            (sid,),
        ).fetchone()["n"]
        self.assertGreater(int(prompt_count), 0)
        rv = self.client.post(
            f"/api/classes/{self.class_id}/live-session/kind",
            json={"is_test": True},
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        row = self.school.get_live_session(sid)
        self.assertEqual(int(row["is_test"]), 1)
        self.assertEqual(int(row["created_as_test"]), 1)

    def test_kind_switch_refuses_after_attendee(self) -> None:
        """Kind endpoint 409 once a student has joined."""
        from school_db import LiveRunKindConflict

        self.client.post(f"/staff/class/{self.class_id}/run-live")
        live = self.school.get_active_live_session_for_teacher(int(self.teacher["id"]))
        sid = int(live["id"])
        now = "2026-10-14T13:05:00"
        self.school.conn.execute(
            """
            INSERT INTO live_session_attendees (
                id, live_session_id, student_id, participant_uuid, visit_token,
                codename, unmatched, joined_at
            ) VALUES (?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (9001, sid, int(self.students[0]), "uuid-mck269", "tok-mck269", "Maple", now),
        )
        self.school.conn.commit()
        with self.assertRaises(LiveRunKindConflict):
            self.school.commit_live_run_kind(
                self.class_id, int(self.teacher["id"]), is_test=True
            )
        rv = self.client.post(
            f"/api/classes/{self.class_id}/live-session/kind",
            json={"is_test": True},
        )
        self.assertEqual(rv.status_code, 409, rv.get_json())

    def test_delete_test_succeeds_after_kind_switch_to_test(self) -> None:
        """Switching to Test stamps created_as_test so delete-test works."""
        self.client.post(f"/staff/class/{self.class_id}/run-live")
        live = self.school.get_active_live_session_for_teacher(int(self.teacher["id"]))
        sid = int(live["id"])
        rv = self.client.post(
            f"/api/classes/{self.class_id}/live-session/kind",
            json={"is_test": True},
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        rv = self.client.post(
            f"/api/classes/{self.class_id}/live-sessions/{sid}/delete-test"
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        self.assertIsNone(self.school.get_live_session(sid))

    def test_delete_test_refuses_mistagged_real_with_data(self) -> None:
        """delete-test returns 409 when is_test was flipped on a real run."""
        from school_db import DeleteTestLiveClassConflict

        real = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=False
        )
        sid = int(real["id"])
        self._publish_one_answer(sid, "M1", "C1")
        self.school.conn.execute(
            "UPDATE live_class_sessions SET is_test = 1, created_as_test = 0 WHERE id = ?",
            (sid,),
        )
        self.school.conn.commit()
        with self.assertRaises(DeleteTestLiveClassConflict):
            self.school.delete_test_live_class(self.class_id, sid)
        rv = self.client.post(
            f"/api/classes/{self.class_id}/live-sessions/{sid}/delete-test"
        )
        self.assertEqual(rv.status_code, 409, rv.get_json())


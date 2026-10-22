#!/usr/bin/env python3
"""MCK-315: permanent staff delete removes owned rows and rolls back on failure."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
import staff_invites  # noqa: E402

# The audit log is kept. It is not a child of the account.
_SKIP_TABLES = frozenset({"access_audit_log", "sqlite_sequence"})


def _column_kind(name: str) -> str | None:
    """Map a column name to the owned-id list it should be checked against.

    Args:
        name: Column name from ``PRAGMA table_info``.
    """
    lowered = name.lower()
    if lowered == "class_id" or lowered.endswith("_class_id"):
        return "class_ids"
    if lowered == "student_id" or lowered.endswith("_student_id"):
        return "student_ids"
    if (
        lowered == "user_id"
        or lowered.endswith("_user_id")
        or ("teacher" in lowered and lowered.endswith("id"))
    ):
        return "user_ids"
    if lowered == "offering_id" or lowered.endswith("offering_id"):
        return "offering_ids"
    if "session" in lowered and lowered.endswith("id"):
        if "live" in lowered:
            return "live_session_ids"
        return "game_session_ids"
    return None


class _Presence:
    """Stand-in for the Postgres presence store."""

    def __init__(self, *, fail: bool = False) -> None:
        """Remember session ids, or fail the first delete.

        Args:
            fail: When True, ``delete_session_tree`` raises.
        """
        self.fail = fail
        self.deleted: list[int] = []

    def delete_session_tree(self, session_id: int) -> None:
        """Record one presence session, or raise before changing anything.

        Args:
            session_id: Live session id.

        Raises:
            RuntimeError: When this stand-in was built to fail.
        """
        if self.fail:
            raise RuntimeError("presence store unavailable")
        self.deleted.append(int(session_id))

    def close(self) -> None:
        """Match the presence store the school database closes on shutdown."""


def _owned_hits(
    conn: sqlite3.Connection,
    owned: dict[str, list],
    *,
    session_ids_are_live: bool = False,
) -> list[str]:
    """Return leftover rows by walking every table's id columns.

    Column names are read from ``PRAGMA table_info``, not from the tables
    the test inserted. A teacher, class, student, offering, or session id
    column is checked against the deleted ids.

    Args:
        conn: Open sqlite connection.
        owned: Id lists for the deleted account, plus ``run_keys``.
        session_ids_are_live: When True, a ``session_id`` column is a live
            session id. The news tape uses that shape.
    """
    hits: list[str] = []
    tables = [
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
    ]
    for table in tables:
        if table in _SKIP_TABLES or table.startswith("sqlite_"):
            continue
        columns = [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")]
        id_kind = {
            "live_class_sessions": "live_session_ids",
            "sessions": "game_session_ids",
            "classes": "class_ids",
            "students": "student_ids",
            "users": "user_ids",
            "course_offerings": "offering_ids",
        }.get(table)
        for column in columns:
            if column == "run_key":
                values = list(owned.get("run_keys") or [])
            elif column == "id" and id_kind:
                values = list(owned.get(id_kind) or [])
            else:
                kind = _column_kind(column)
                if session_ids_are_live and kind == "game_session_ids":
                    kind = "live_session_ids"
                if kind is None:
                    continue
                values = list(owned.get(kind) or [])
            if not values:
                continue
            marks = ",".join("?" * len(values))
            count = conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE "{column}" IN ({marks})',
                tuple(values),
            ).fetchone()[0]
            if int(count):
                hits.append(f"{table}.{column}")
        if table == "live_seq_floors" and "scope" in columns:
            scopes = [f"class:{int(cid)}" for cid in owned.get("class_ids") or []]
            scopes.extend(
                f"sid:{int(sid)}" for sid in owned.get("live_session_ids") or []
            )
            if scopes:
                marks = ",".join("?" * len(scopes))
                count = conn.execute(
                    f"SELECT COUNT(*) FROM live_seq_floors WHERE scope IN ({marks})",
                    tuple(scopes),
                ).fetchone()[0]
                if int(count):
                    hits.append("live_seq_floors.scope")
    fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
    hits.extend(f"foreign_key_check.{row[0]}" for row in fk_rows)
    return hits


class PermanentStaffDeleteTests(unittest.TestCase):
    """Admin permanent delete of a teacher who already has class data."""

    def setUp(self) -> None:
        """Isolated sqlite and an IT client."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        self.actor_id = int(
            self.school.conn.execute(
                "SELECT id FROM users WHERE role = 'it' ORDER BY id LIMIT 1"
            ).fetchone()[0]
        )

    def tearDown(self) -> None:
        """Close the database and remove the temp dir."""
        from live_news_wire import reset_logs

        self.school.presence = None
        reset_logs()
        self.school.close()
        self.tmp.cleanup()

    def _login_admin(self) -> None:
        """Finish the dev IT login using the seeded admin row.

        The callback issues a new verification code, so the code posted here
        is read after that callback.
        """
        admin = self.school.get_user(self.actor_id)
        assert admin is not None
        self.client.get("/auth/google?portal=it")
        self.client.get(
            "/auth/google/callback?email=" + str(admin["email"]) + "&name=Admin"
        )
        fresh = self.school.get_user(self.actor_id)
        assert fresh is not None
        self.client.post("/verify-email", data={"code": fresh["verification_code"]})

    def _login_staff(self, user_id: int) -> None:
        """Finish the dev staff login for ``user_id``."""
        user = self.school.get_user(user_id)
        assert user is not None
        self.client.get("/auth/google?portal=staff")
        self.client.get(
            "/auth/google/callback?email=" + str(user["email"]) + "&name=Staff"
        )
        fresh = self.school.get_user(user_id)
        assert fresh is not None
        self.client.post("/verify-email", data={"code": fresh["verification_code"]})

    def _class(
        self, teacher_id: int, offering_id: int, codenames: list[str], time_label: str
    ) -> int:
        """Create one class for ``teacher_id`` and return its id."""
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCF3M",
            days_preset="M/W/F",
            time_label=time_label,
            codenames=codenames,
            offering_id=int(offering_id),
            teacher_user_id=int(teacher_id),
        )
        return int(created["id"])

    def _student_ids(self, class_id: int) -> list[int]:
        """Roster ids for one class."""
        return [
            int(row[0])
            for row in self.school.conn.execute(
                "SELECT id FROM students WHERE class_id = ? ORDER BY id",
                (int(class_id),),
            )
        ]

    def _seed_owned_graph(self) -> dict[str, list]:
        """Build a teacher who owns a roster, a finished class, and a test run.

        A second teacher keeps their own class, and a shared bank question
        stays on the library.

        Returns:
            Ids that must be gone after the owned account is deleted.
        """
        teacher = self.school.register_staff("owned@example.com", "Owned")
        other = self.school.register_staff("kept@example.com", "Kept")
        uid = int(teacher["id"])
        oid = int(other["id"])
        self.school.set_verification_code(uid, "111111")
        offering = self.school.assign_course(teacher_user_id=uid, ontario_code="MCF3M")
        other_offering = self.school.assign_course(
            teacher_user_id=oid, ontario_code="MCF3M"
        )
        self.school.conn.execute(
            """
            UPDATE course_offerings SET copied_from_offering_id = ?
            WHERE id = ?
            """,
            (int(offering["id"]), int(other_offering["id"])),
        )
        class_id = self._class(uid, int(offering["id"]), ["N1", "N2"], "2:00pm")
        other_class = self._class(oid, int(other_offering["id"]), ["N3"], "3:15pm")
        students = self._student_ids(class_id)
        self.school.conn.execute(
            """
            UPDATE students
            SET pin_hash = ?, default_pin_ciphertext = ?
            WHERE class_id = ?
            """,
            ("stored-hash", "stored-cipher", class_id),
        )
        self.school.conn.execute(
            """
            UPDATE students
            SET pin_hash = ?, default_pin_ciphertext = ?
            WHERE class_id = ?
            """,
            ("kept-hash", "kept-cipher", other_class),
        )
        ended = self.school.start_live_class_session(
            class_id, uid, live_module="M1", live_slot="C1"
        )
        ended_id = int(ended["id"])
        self.school.conn.execute(
            """
            INSERT INTO live_session_prompts (
                live_session_id, slide_index, kind, payload, active, created_at, updated_at
            ) VALUES (?, 0, 'poll', '{}', 1, 't', 't')
            """,
            (ended_id,),
        )
        prompt_id = int(self.school.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        self.school.conn.execute(
            """
            INSERT INTO live_session_responses (
                prompt_id, student_id, response_json, created_at, updated_at
            ) VALUES (?, ?, '{}', 't', 't')
            """,
            (prompt_id, students[0]),
        )
        self.school.conn.execute(
            """
            INSERT INTO live_session_items (
                live_session_id, placement_key, item_id, stage, kind, prompt_id,
                created_at, updated_at
            ) VALUES (?, 'p1', 'i1', 'play', 'poll', ?, 't', 't')
            """,
            (ended_id, prompt_id),
        )
        self.school.create_observation(
            live_session_id=ended_id,
            class_id=class_id,
            observer_user_id=uid,
            scope="class",
            note="class note",
        )
        self.school.end_live_class_session(ended_id, record_participation=True)
        rehearsal = self.school.start_live_class_session(
            class_id, uid, live_module="M1", live_slot="C2", is_test=True
        )
        rehearsal_id = int(rehearsal["id"])
        self.school.end_live_class_session(rehearsal_id, record_participation=False)
        kept_live = self.school.start_live_class_session(
            other_class, oid, live_module="M1", live_slot="C1"
        )
        self.school.end_live_class_session(int(kept_live["id"]), record_participation=False)
        self.kept_live_session_id = int(kept_live["id"])

        self.school.conn.execute(
            """
            INSERT INTO sessions (class_id, starts_at, header_label, status, source)
            VALUES (?, 't', 'H', 'ended', 'game')
            """,
            (class_id,),
        )
        game_session_id = int(
            self.school.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        )
        self.school.conn.execute(
            """
            INSERT INTO session_scores (session_id, student_id, present, points)
            VALUES (?, ?, 1, 1)
            """,
            (game_session_id, students[0]),
        )
        self.school.conn.execute(
            """
            INSERT INTO grade_category_weights (class_id, category, weight_pct, updated_at)
            VALUES (?, 'term', 65, 't')
            """,
            (class_id,),
        )
        self.school.conn.execute(
            """
            INSERT INTO catchup_links (
                token, class_id, run_key, live_module, live_slot, created_at, updated_at
            ) VALUES ('owned-link', ?, 'owned-run', 'M1', 'C1', 't', 't')
            """,
            (class_id,),
        )
        self.school.conn.execute(
            """
            INSERT INTO catchup_links (
                token, class_id, run_key, live_module, live_slot, created_at, updated_at
            ) VALUES ('kept-link', ?, 'kept-run', 'M1', 'C1', 't', 't')
            """,
            (other_class,),
        )
        self.school.conn.execute(
            """
            INSERT INTO catchup_pin_failures (class_id, student_id, failed_at)
            VALUES (?, ?, 1)
            """,
            (class_id, students[0]),
        )
        self.school.conn.execute(
            """
            INSERT INTO engagement_level_overrides (
                class_id, run_key, student_id, level, teacher_user_id, updated_at
            ) VALUES (?, 'owned-run', ?, 2, ?, 't')
            """,
            (class_id, students[0], uid),
        )
        self.school.conn.execute(
            """
            INSERT INTO live_board_assignments (
                class_id, run_key, board_key, student_id, team_sort_order
            ) VALUES (?, 'owned-run', 'team:1', ?, 1)
            """,
            (class_id, students[0]),
        )
        self.school.conn.execute(
            """
            INSERT INTO observation_history (
                source_observation_id, class_id, observer_user_id, created_at,
                scope, kept_at, run_key
            ) VALUES (1, ?, ?, 't', 'class', 't', 'owned-run')
            """,
            (class_id, uid),
        )
        run_keys = [
            str(row[0])
            for row in self.school.conn.execute(
                """
                SELECT run_key FROM live_class_sessions
                WHERE class_id = ? AND run_key IS NOT NULL AND TRIM(run_key) != ''
                """,
                (class_id,),
            )
            if str(row[0] or "").strip()
        ]
        self.assertGreaterEqual(len(run_keys), 2)
        for key in run_keys:
            self.school.conn.execute(
                """
                INSERT INTO board_ops (
                    run_key, board_key, board_seq, op, owner, created_at
                ) VALUES (?, 'teacher', 1, '{}', 'owner', 't')
                """,
                (key,),
            )
        self.school.conn.execute(
            """
            INSERT INTO board_ops (
                run_key, board_key, board_seq, op, owner, created_at
            ) VALUES ('kept-board', 'teacher', 1, '{}', 'kept', 't')
            """
        )
        library = self.school.create_library("MCF3M", origin="upload")
        self.school.conn.execute(
            """
            INSERT INTO question_banks (library_id, import_key, title, created_at)
            VALUES (?, 'shared', 'Shared', 't')
            """,
            (int(library["id"]),),
        )
        bank_id = int(self.school.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        self.school.conn.execute(
            """
            INSERT INTO questions (bank_id, import_key, title, created_at)
            VALUES (?, 'q1', 'Q', 't')
            """,
            (bank_id,),
        )
        question_id = int(
            self.school.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        )
        self.school.conn.execute(
            """
            INSERT INTO class_live_playlist_placements (
                class_id, module, slot, placement_key, item_id, source_question_id, created_at
            ) VALUES (?, 'M1', 'C1', 'pk', 'item', ?, 't')
            """,
            (class_id, question_id),
        )
        invite, _token = staff_invites.create_or_refresh_invite(
            self.school,
            email="owned@example.com",
            first_name="Owned",
            created_by_user_id=self.actor_id,
        )
        self.school.conn.execute(
            """
            INSERT INTO staff_invite_sends (invite_id, by_user_id, sent_at, delivered)
            VALUES (?, ?, 't', 1)
            """,
            (int(invite["id"]), self.actor_id),
        )
        self.owned_user_id = uid
        self.kept_user_id = oid
        self.owned_class_id = class_id
        self.kept_class_id = other_class
        self.question_id = question_id
        self.kept_offering_id = int(other_offering["id"])
        self.owned_instance = Path(self.school.data_dir) / str(offering["instance_relpath"])
        self.kept_instance = Path(self.school.data_dir) / str(
            other_offering["instance_relpath"]
        )
        session_ids = [
            int(row[0])
            for row in self.school.conn.execute(
                "SELECT id FROM live_class_sessions WHERE class_id = ?",
                (class_id,),
            )
        ]
        self.assertTrue(
            self.school.conn.execute(
                """
                SELECT 1 FROM live_class_sessions
                WHERE class_id = ? AND status = 'ended' AND COALESCE(is_test, 0) = 0
                """,
                (class_id,),
            ).fetchone()
        )
        self.assertTrue(
            self.school.conn.execute(
                """
                SELECT 1 FROM live_class_sessions
                WHERE class_id = ? AND COALESCE(is_test, 0) = 1
                """,
                (class_id,),
            ).fetchone()
        )
        game_session_ids = [
            int(row[0])
            for row in self.school.conn.execute(
                "SELECT id FROM sessions WHERE class_id = ?",
                (class_id,),
            )
        ]
        for session_id in session_ids:
            self._seed_news(int(session_id), "owned")
        self._seed_news(self.kept_live_session_id, "kept")
        return {
            "user_ids": [uid],
            "class_ids": [class_id],
            "student_ids": students,
            "offering_ids": [int(offering["id"])],
            "session_ids": session_ids,
            "live_session_ids": session_ids,
            "game_session_ids": game_session_ids,
            "run_keys": run_keys,
        }

    def test_finished_class_delete_leaves_no_owned_rows(self) -> None:
        """A roster and a finished class go away; the other teacher stays."""
        owned = self._seed_owned_graph()
        presence = _Presence()
        self.school.presence = presence
        self._login_admin()
        teacher = self.school.get_user(self.owned_user_id)
        assert teacher is not None
        response = self.client.post(
            f"/it/staff/{self.owned_user_id}/delete",
            data={"confirm_email": teacher["email"]},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        self.assertIsNone(self.school.get_user(self.owned_user_id))
        self.assertIsNone(self.school.get_user_by_email("owned@example.com"))
        self.assertEqual(self._every_owned_hit(owned), [])
        self.assertEqual(
            self.school.conn.execute(
                "SELECT COUNT(*) FROM students WHERE class_id = ?",
                (self.owned_class_id,),
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.school.conn.execute(
                """
                SELECT COUNT(*) FROM students
                WHERE pin_hash = ? OR default_pin_ciphertext = ?
                """,
                ("stored-hash", "stored-cipher"),
            ).fetchone()[0],
            0,
        )
        self.assertFalse(self.owned_instance.exists())
        kept = self.school.get_user(self.kept_user_id)
        self.assertIsNotNone(kept)
        self.assertEqual(
            self.school.conn.execute(
                "SELECT COUNT(*) FROM classes WHERE id = ?",
                (self.kept_class_id,),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.school.conn.execute(
                "SELECT pin_hash FROM students WHERE class_id = ?",
                (self.kept_class_id,),
            ).fetchone()[0],
            "kept-hash",
        )
        kept_offering = self.school.conn.execute(
            "SELECT teacher_user_id, copied_from_offering_id FROM course_offerings WHERE id = ?",
            (self.kept_offering_id,),
        ).fetchone()
        self.assertEqual(int(kept_offering["teacher_user_id"]), self.kept_user_id)
        self.assertIsNone(kept_offering["copied_from_offering_id"])
        self.assertTrue(self.kept_instance.exists())
        self.assertEqual(
            self.school.conn.execute(
                "SELECT COUNT(*) FROM questions WHERE id = ?",
                (self.question_id,),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.school.conn.execute(
                "SELECT COUNT(*) FROM catchup_links WHERE token = ?",
                ("kept-link",),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.school.conn.execute(
                "SELECT COUNT(*) FROM board_ops WHERE run_key = ?",
                ("kept-board",),
            ).fetchone()[0],
            1,
        )
        self.assertCountEqual(presence.deleted, owned["session_ids"])
        self._assert_news_gone(owned["live_session_ids"])
        self._assert_news_kept(self.kept_live_session_id)
        again = self.school.register_staff("owned@example.com", "Fresh")
        self.assertIsNone(again.get("verified_at"))

    def test_foreign_key_failure_rolls_back(self) -> None:
        """A blocked user delete keeps the class, roster, and account."""
        owned = self._seed_owned_graph()
        self.school.conn.execute(
            """
            CREATE TABLE staff_delete_probe (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id)
            )
            """
        )
        self.school.conn.execute(
            "INSERT INTO staff_delete_probe (user_id) VALUES (?)",
            (self.owned_user_id,),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.school.delete_staff_permanently(self.owned_user_id, self.actor_id)
        self.assertFalse(self.school.conn.in_transaction)
        self.assertEqual(
            int(self.school.conn.execute("PRAGMA foreign_keys").fetchone()[0]),
            1,
        )
        self.assertIsNotNone(self.school.get_user(self.owned_user_id))
        self.assertEqual(
            self.school.conn.execute(
                "SELECT COUNT(*) FROM classes WHERE id = ?",
                (self.owned_class_id,),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(len(self._student_ids(self.owned_class_id)), 2)
        self.assertEqual(
            self.school.conn.execute(
                "SELECT COUNT(*) FROM course_offerings WHERE id = ?",
                (owned["offering_ids"][0],),
            ).fetchone()[0],
            1,
        )
        self.assertIsNotNone(self.school.get_user(self.kept_user_id))

    def test_presence_failure_after_commit_keeps_the_delete(self) -> None:
        """A presence failure after commit does not restore the account."""
        self._seed_owned_graph()
        self.school.presence = _Presence(fail=True)
        with self.assertLogs("school_db", level="WARNING") as logs:
            self.school.delete_staff_permanently(self.owned_user_id, self.actor_id)
        self.assertIsNone(self.school.get_user(self.owned_user_id))
        self.assertFalse(self.school.conn.in_transaction)
        self.assertIsNotNone(self.school.get_user(self.kept_user_id))
        text = "\n".join(logs.output)
        self.assertIn("presence purge failed", text)
        self.assertNotIn("@", text)


    def _every_owned_hit(self, owned: dict[str, list]) -> list[str]:
        """Leftover ids in the school file and the live-news file."""
        from live_news_wire import news_db_path

        hits = _owned_hits(self.school.conn, owned)
        path = news_db_path(self.school.data_dir)
        if not path.exists():
            return hits
        news = sqlite3.connect(path)
        try:
            hits.extend(
                f"live_news.{hit}"
                for hit in _owned_hits(news, owned, session_ids_are_live=True)
            )
        finally:
            news.close()
        return hits

    def _seed_news(self, session_id: int, marker: str) -> None:
        """Store one postcard and one ink window for a live session."""
        from live_news_wire import log_for

        tape = log_for(self.school.data_dir)
        tape.append(int(session_id), [{"type": "stage", "stage": marker, "state_seq": 1}])
        with tape._lock:
            tape._conn.execute(
                """
                INSERT INTO board_ink_coalesce (
                    session_id, board_key, window_ends_at, trail_due
                ) VALUES (?, 'team:1', 1, 0)
                """,
                (int(session_id),),
            )
            tape._conn.commit()

    def _news_counts(self, session_id: int) -> tuple[int, int]:
        """Postcard and ink-window counts for one session."""
        from live_news_wire import news_db_path

        path = news_db_path(self.school.data_dir)
        if not path.exists():
            return 0, 0
        conn = sqlite3.connect(path)
        try:
            events = conn.execute(
                "SELECT COUNT(*) FROM live_news_events WHERE session_id = ?",
                (int(session_id),),
            ).fetchone()[0]
            ink = conn.execute(
                "SELECT COUNT(*) FROM board_ink_coalesce WHERE session_id = ?",
                (int(session_id),),
            ).fetchone()[0]
        finally:
            conn.close()
        return int(events), int(ink)

    def _assert_news_gone(self, session_ids: list[int]) -> None:
        """The deleted sessions have no tape rows."""
        for session_id in session_ids:
            self.assertEqual(self._news_counts(int(session_id)), (0, 0), session_id)

    def _assert_news_kept(self, session_id: int) -> None:
        """The other teacher's tape rows are still there."""
        self.assertEqual(self._news_counts(int(session_id)), (1, 1))

    def test_admin_cannot_delete_own_account(self) -> None:
        """The signed-in admin account stays when they confirm their own row."""
        self._login_admin()
        admin = self.school.get_user(self.actor_id)
        assert admin is not None
        response = self.client.post(
            f"/it/staff/{self.actor_id}/delete",
            data={"confirm_email": admin["email"]},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("@", response.get_data(as_text=True))
        self.assertIsNotNone(self.school.get_user(self.actor_id))

    def test_admin_cannot_delete_another_school(self) -> None:
        """An account in another school is refused and left in place."""
        other = self.school.create_tenant("other-school", "Other School")
        teacher = self.school.register_staff(
            "other-school@example.com",
            tenant_id=int(other["id"]),
        )
        self._login_admin()
        response = self.client.post(
            f"/it/staff/{int(teacher['id'])}/delete",
            data={"confirm_email": teacher["email"]},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("@", response.get_data(as_text=True))
        self.assertIsNotNone(self.school.get_user(int(teacher["id"])))
        with self.assertRaises(ValueError):
            self.school.delete_staff_permanently(int(teacher["id"]), self.actor_id)
        self.assertIsNotNone(self.school.get_user(int(teacher["id"])))

    def test_staff_cannot_permanently_delete(self) -> None:
        """A teacher session cannot permanently delete an account."""
        teacher = self.school.register_staff("actor@example.com", "Actor")
        target = self.school.register_staff("target@example.com", "Target")
        self._login_staff(int(teacher["id"]))
        response = self.client.post(
            f"/it/staff/{int(target['id'])}/delete",
            data={"confirm_email": target["email"]},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("@", response.get_data(as_text=True))
        self.assertIsNotNone(self.school.get_user(int(target["id"])))
        with self.assertRaises(ValueError):
            self.school.delete_staff_permanently(int(target["id"]), int(teacher["id"]))
        self.assertIsNotNone(self.school.get_user(int(target["id"])))

    def test_keeps_a_session_started_in_another_class(self) -> None:
        """A session in someone else's class stays, without the starter id."""
        starter = self.school.register_staff("starter@example.com", "Starter")
        owner = self.school.register_staff("classowner@example.com", "Owner")
        offering = self.school.assign_course(
            teacher_user_id=int(owner["id"]), ontario_code="MCF3M"
        )
        class_id = self._class(
            int(owner["id"]), int(offering["id"]), ["N9"], "10:40am"
        )
        started = self.school.start_live_class_session(
            class_id, int(starter["id"]), live_module="M1", live_slot="C1"
        )
        session_id = int(started["id"])
        self.school.conn.execute(
            """
            INSERT INTO live_session_attendees (
                live_session_id, participant_uuid, codename, joined_at
            ) VALUES (?, 'seat-kept', 'N9', 't')
            """,
            (session_id,),
        )
        self.school.conn.execute(
            """
            INSERT INTO live_session_prompts (
                live_session_id, slide_index, kind, payload, active, created_at, updated_at
            ) VALUES (?, 0, 'poll', '{}', 1, 't', 't')
            """,
            (session_id,),
        )
        prompt_id = int(self.school.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        self.school.conn.execute(
            """
            INSERT INTO live_session_responses (
                prompt_id, response_json, created_at, updated_at
            ) VALUES (?, '{}', 't', 't')
            """,
            (prompt_id,),
        )
        self.school.conn.commit()
        self.school.delete_staff_permanently(int(starter["id"]), self.actor_id)
        row = self.school.conn.execute(
            "SELECT class_id, teacher_user_id FROM live_class_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(int(row["class_id"]), class_id)
        self.assertIsNone(row["teacher_user_id"])
        self.assertEqual(
            self.school.conn.execute(
                "SELECT COUNT(*) FROM live_session_attendees WHERE live_session_id = ?",
                (session_id,),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.school.conn.execute(
                """
                SELECT COUNT(*) FROM live_session_responses
                WHERE prompt_id = ?
                """,
                (prompt_id,),
            ).fetchone()[0],
            1,
        )
        self.assertIsNotNone(self.school.get_user(int(owner["id"])))
        self.assertIsNone(self.school.get_user(int(starter["id"])))

    def _seat_and_cross_tag(
        self, session_id: int, tagged_class_id: int, other_user_id: int
    ) -> tuple[str, int]:
        """Store attendance and rows tagged with another class.

        The snapshot, participation, catch-up, Lesson Slides, engagement,
        and observation rows use ``tagged_class_id`` and the surviving
        session's run key. Their live-session column is left empty so a
        class tag cannot stand in for that run key.

        Args:
            session_id: Live session that must survive the delete.
            tagged_class_id: Class id written on the cross-tagged rows.
            other_user_id: Teacher who still owns the surviving session.

        Returns:
            The session run key and the prompt id.
        """
        row = self.school.conn.execute(
            "SELECT run_key FROM live_class_sessions WHERE id = ?",
            (int(session_id),),
        ).fetchone()
        run_key = str(row["run_key"] or "")
        self.school.conn.execute(
            """
            INSERT INTO live_session_attendees (
                live_session_id, participant_uuid, codename, joined_at
            ) VALUES (?, 'seat-cross', 'N2', 't')
            """,
            (int(session_id),),
        )
        self.school.conn.execute(
            """
            INSERT INTO live_session_prompts (
                live_session_id, slide_index, kind, payload, active, created_at, updated_at
            ) VALUES (?, 0, 'poll', '{}', 1, 't', 't')
            """,
            (int(session_id),),
        )
        prompt_id = int(self.school.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        self.school.conn.execute(
            """
            INSERT INTO live_session_responses (
                prompt_id, response_json, created_at, updated_at
            ) VALUES (?, '{}', 't', 't')
            """,
            (prompt_id,),
        )
        self.school.conn.execute(
            """
            INSERT INTO live_result_snapshots (
                live_session_id, class_id, session_code, run_key,
                live_module, live_slot, source, question_key, question_json,
                codename, answer_json, snapshot_at
            ) VALUES (?, ?, '', ?, 'M1', 'C1', 'live', 'item:1', '{}', '', '{}', 't')
            """,
            (int(session_id), int(tagged_class_id), run_key),
        )
        self.school.conn.execute(
            """
            INSERT INTO board_ops (
                run_key, board_key, board_seq, op, owner, created_at
            ) VALUES (?, 'teacher', 1, '{}', 'seat', 't')
            """,
            (run_key,),
        )
        self.school.conn.execute(
            """
            INSERT INTO live_seq_floors (scope, max_seq) VALUES (?, 3)
            """,
            (f"sid:{int(session_id)}",),
        )
        self._insert_cross_tagged_run_rows(
            int(session_id), int(tagged_class_id), run_key, int(other_user_id)
        )
        self.school.conn.commit()
        self._seed_news(int(session_id), "cross-tag")
        return run_key, prompt_id

    def _insert_cross_tagged_run_rows(
        self,
        session_id: int,
        tagged_class_id: int,
        run_key: str,
        other_user_id: int,
    ) -> None:
        """Insert one cross-tagged row in each run-key table.

        Args:
            session_id: Live session the run key belongs to.
            tagged_class_id: Class id stored on the row.
            run_key: Run key of the session that must survive.
            other_user_id: Teacher id stored where a teacher column is required.
        """
        student_id = int(
            self.school.conn.execute(
                """
                SELECT id FROM students
                WHERE class_id = (
                    SELECT class_id FROM live_class_sessions WHERE id = ?
                )
                ORDER BY id LIMIT 1
                """,
                (int(session_id),),
            ).fetchone()[0]
        )
        conn = self.school.conn
        conn.execute(
            """
            INSERT INTO participation_items (
                class_id, run_key, question_key, offered_at
            ) VALUES (?, ?, 'cross', 't')
            """,
            (tagged_class_id, run_key),
        )
        conn.execute(
            """
            INSERT INTO participation_answers (
                class_id, run_key, question_key, student_id, answered_at
            ) VALUES (?, ?, 'cross', ?, 't')
            """,
            (tagged_class_id, run_key, student_id),
        )
        conn.execute(
            """
            INSERT INTO participation_runs (class_id, run_key, is_real)
            VALUES (?, ?, 1)
            """,
            (tagged_class_id, run_key),
        )
        conn.execute(
            """
            INSERT INTO catchup_links (
                token, class_id, run_key, live_module, live_slot,
                live_session_id, created_at, updated_at
            ) VALUES (?, ?, ?, 'M1', 'C1', NULL, 't', 't')
            """,
            (f"cross-{int(session_id)}", tagged_class_id, run_key),
        )
        conn.execute(
            """
            INSERT INTO lesson_slides_latest (
                class_id, live_module, live_slot, is_test, live_session_id,
                run_key, ended_at, updated_at
            ) VALUES (?, 'M1', 'C1', 0, NULL, ?, 't', 't')
            """,
            (tagged_class_id, run_key),
        )
        conn.execute(
            """
            INSERT INTO lesson_slides_dismissed (
                class_id, live_module, live_slot, is_test, run_key, dismissed_at
            ) VALUES (?, 'M1', 'C1', 0, ?, 't')
            """,
            (tagged_class_id, run_key),
        )
        conn.execute(
            """
            INSERT INTO engagement_level_overrides (
                class_id, run_key, student_id, level, teacher_user_id, updated_at
            ) VALUES (?, ?, ?, 1, ?, 't')
            """,
            (tagged_class_id, run_key, student_id, other_user_id),
        )
        conn.execute(
            """
            INSERT INTO engagement_events (
                class_id, live_session, run_key, kind, ts
            ) VALUES (?, NULL, ?, 'note', 't')
            """,
            (tagged_class_id, run_key),
        )
        conn.execute(
            """
            INSERT INTO engagement_rollup (
                class_id, live_session, run_key, rolled_at, scope, signal
            ) VALUES (?, NULL, ?, 't', 'class', 'note')
            """,
            (tagged_class_id, run_key),
        )
        conn.execute(
            """
            INSERT INTO group_engagement (
                class_id, run_key, question_key, student_id, earned, possible,
                source, recorded_at
            ) VALUES (?, ?, 'cross', ?, 0, 1, 'live', 't')
            """,
            (tagged_class_id, run_key, student_id),
        )
        conn.execute(
            """
            INSERT INTO group_engagement_items (
                class_id, run_key, question_key, item_json, offered_at
            ) VALUES (?, ?, 'cross', '{}', 't')
            """,
            (tagged_class_id, run_key),
        )
        conn.execute(
            """
            INSERT INTO live_board_assignments (
                class_id, run_key, board_key, student_id, team_sort_order,
                live_session_id
            ) VALUES (?, ?, 'team:1', ?, 1, NULL)
            """,
            (tagged_class_id, run_key, student_id),
        )
        conn.execute(
            """
            INSERT INTO observation_history (
                source_observation_id, live_session, run_key, class_id,
                observer_user_id, created_at, scope, kept_at
            ) VALUES (1, NULL, ?, ?, ?, 't', 'class', 't')
            """,
            (run_key, tagged_class_id, other_user_id),
        )

    def _assert_cross_tagged_rows_kept(self, run_key: str) -> None:
        """Participation, catch-up, slides, engagement, and history stayed.

        Args:
            run_key: Run key of the session that had to survive.
        """
        tables = (
            "participation_items",
            "participation_answers",
            "participation_runs",
            "catchup_links",
            "lesson_slides_latest",
            "lesson_slides_dismissed",
            "engagement_level_overrides",
            "engagement_events",
            "engagement_rollup",
            "group_engagement",
            "group_engagement_items",
            "live_board_assignments",
            "observation_history",
        )
        for table in tables:
            count = self.school.conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE run_key = ?',
                (run_key,),
            ).fetchone()[0]
            self.assertEqual(count, 1, table)

    def test_cross_tagged_snapshot_keeps_the_other_session(self) -> None:
        """A snapshot tagged with this class does not delete another class's session."""
        target = self.school.register_staff("snapshot-target@example.com", "Target")
        other = self.school.register_staff("snapshot-other@example.com", "Other")
        target_offering = self.school.assign_course(
            teacher_user_id=int(target["id"]), ontario_code="MCF3M"
        )
        other_offering = self.school.assign_course(
            teacher_user_id=int(other["id"]), ontario_code="MCF3M"
        )
        target_class = self._class(
            int(target["id"]), int(target_offering["id"]), ["N1"], "8:00am"
        )
        other_class = self._class(
            int(other["id"]), int(other_offering["id"]), ["N2"], "9:15am"
        )
        started = self.school.start_live_class_session(
            other_class, int(other["id"]), live_module="M1", live_slot="C1"
        )
        session_id = int(started["id"])
        run_key, prompt_id = self._seat_and_cross_tag(session_id, target_class, int(other["id"]))
        self.assertTrue(run_key)
        import base64

        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        self.school.save_course_slide_deck(
            target_class,
            "M1",
            "C1",
            title="Target deck",
            source="pptx",
            page_pngs=[png],
            source_bytes=b"PK\x03\x04target-slide-source",
            source_suffix=".pptx",
        )
        self.school.save_course_slide_deck(
            other_class,
            "M1",
            "C1",
            title="Other deck",
            source="pptx",
            page_pngs=[png],
            source_bytes=b"PK\x03\x04other-slide-source",
            source_suffix=".pptx",
        )
        target_rows = self.school.conn.execute(
            """
            SELECT source_relpath FROM course_slide_decks WHERE class_id = ?
            """,
            (target_class,),
        ).fetchall()
        other_rows = self.school.conn.execute(
            """
            SELECT source_relpath FROM course_slide_decks WHERE class_id = ?
            """,
            (other_class,),
        ).fetchall()
        target_pages = self.school.conn.execute(
            """
            SELECT pages.image_relpath
            FROM course_slide_pages AS pages
            JOIN course_slide_decks AS decks ON decks.id = pages.deck_id
            WHERE decks.class_id = ?
            """,
            (target_class,),
        ).fetchall()
        other_pages = self.school.conn.execute(
            """
            SELECT pages.image_relpath
            FROM course_slide_pages AS pages
            JOIN course_slide_decks AS decks ON decks.id = pages.deck_id
            WHERE decks.class_id = ?
            """,
            (other_class,),
        ).fetchall()
        data_dir = Path(self.school.data_dir)
        target_files = [data_dir / str(row[0]) for row in (*target_rows, *target_pages)]
        other_files = [data_dir / str(row[0]) for row in (*other_rows, *other_pages)]
        self.assertTrue(target_files)
        self.assertTrue(all(path.is_file() for path in target_files))
        self.assertTrue(all(path.is_file() for path in other_files))
        self.school.delete_staff_permanently(int(target["id"]), self.actor_id)
        row = self.school.conn.execute(
            "SELECT class_id, teacher_user_id FROM live_class_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(int(row["class_id"]), other_class)
        self.assertEqual(int(row["teacher_user_id"]), int(other["id"]))
        self.assertEqual(
            self.school.conn.execute(
                "SELECT COUNT(*) FROM live_session_attendees WHERE live_session_id = ?",
                (session_id,),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.school.conn.execute(
                "SELECT COUNT(*) FROM live_session_responses WHERE prompt_id = ?",
                (prompt_id,),
            ).fetchone()[0],
            1,
        )
        snapshot = self.school.conn.execute(
            """
            SELECT class_id, run_key FROM live_result_snapshots
            WHERE live_session_id = ?
            """,
            (session_id,),
        ).fetchone()
        self.assertIsNotNone(snapshot)
        self.assertEqual(int(snapshot["class_id"]), target_class)
        self.assertEqual(str(snapshot["run_key"]), run_key)
        self.assertEqual(
            self.school.conn.execute(
                "SELECT COUNT(*) FROM board_ops WHERE run_key = ?",
                (run_key,),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.school.conn.execute(
                "SELECT max_seq FROM live_seq_floors WHERE scope = ?",
                (f"sid:{session_id}",),
            ).fetchone()[0],
            3,
        )
        self._assert_cross_tagged_rows_kept(run_key)
        self._assert_news_kept(session_id)
        self.assertIsNone(self.school.get_user(int(target["id"])))
        self.assertIsNotNone(self.school.get_user(int(other["id"])))
        self.assertIsNone(
            self.school.conn.execute(
                "SELECT id FROM classes WHERE id = ?",
                (target_class,),
            ).fetchone()
        )
        self.assertIsNotNone(
            self.school.conn.execute(
                "SELECT id FROM classes WHERE id = ?",
                (other_class,),
            ).fetchone()
        )
        self.assertIsNone(self.school.course_slide_deck_summary(target_class, "M1", "C1"))
        other_deck = self.school.course_slide_deck_summary(other_class, "M1", "C1")
        self.assertIsNotNone(other_deck)
        assert other_deck is not None
        self.assertEqual(other_deck["title"], "Other deck")
        self.assertTrue(all(not path.is_file() for path in target_files))
        self.assertTrue(all(path.is_file() for path in other_files))
        self.assertEqual(
            self.school.conn.execute(
                """
                SELECT COUNT(*) FROM course_slide_pages
                WHERE image_relpath LIKE ?
                """,
                (f"live-question-images/{target_class}/%",),
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.school.conn.execute(
                """
                SELECT COUNT(*) FROM course_slide_pages
                WHERE image_relpath LIKE ?
                """,
                (f"live-question-images/{other_class}/%",),
            ).fetchone()[0],
            1,
        )

    def test_cross_tagged_snapshot_keeps_a_session_the_target_started(self) -> None:
        """A cross-tag does not delete a session this account started elsewhere."""
        target = self.school.register_staff("starter-target@example.com", "Target")
        other = self.school.register_staff("starter-other@example.com", "Other")
        target_offering = self.school.assign_course(
            teacher_user_id=int(target["id"]), ontario_code="MCF3M"
        )
        other_offering = self.school.assign_course(
            teacher_user_id=int(other["id"]), ontario_code="MCF3M"
        )
        target_class = self._class(
            int(target["id"]), int(target_offering["id"]), ["N1"], "2:00pm"
        )
        other_class = self._class(
            int(other["id"]), int(other_offering["id"]), ["N2"], "3:15pm"
        )
        started = self.school.start_live_class_session(
            other_class, int(target["id"]), live_module="M1", live_slot="C1"
        )
        session_id = int(started["id"])
        run_key, prompt_id = self._seat_and_cross_tag(session_id, target_class, int(other["id"]))
        self.school.delete_staff_permanently(int(target["id"]), self.actor_id)
        row = self.school.conn.execute(
            "SELECT class_id, teacher_user_id FROM live_class_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(int(row["class_id"]), other_class)
        self.assertIsNone(row["teacher_user_id"])
        self.assertEqual(
            self.school.conn.execute(
                "SELECT COUNT(*) FROM live_session_attendees WHERE live_session_id = ?",
                (session_id,),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.school.conn.execute(
                "SELECT COUNT(*) FROM live_session_responses WHERE prompt_id = ?",
                (prompt_id,),
            ).fetchone()[0],
            1,
        )
        self.assertIsNotNone(
            self.school.conn.execute(
                "SELECT id FROM live_result_snapshots WHERE live_session_id = ?",
                (session_id,),
            ).fetchone()
        )
        self.assertEqual(
            self.school.conn.execute(
                "SELECT COUNT(*) FROM board_ops WHERE run_key = ?",
                (run_key,),
            ).fetchone()[0],
            1,
        )
        self._assert_cross_tagged_rows_kept(run_key)
        self._assert_news_kept(session_id)
        self.assertIsNone(self.school.get_user(int(target["id"])))
        self.assertIsNotNone(self.school.get_user(int(other["id"])))

    def test_refuses_when_another_class_uses_the_offering(self) -> None:
        """Another teacher's class on this offering blocks the delete."""
        owner = self.school.register_staff("offering-owner@example.com", "Owner")
        other = self.school.register_staff("class-holder@example.com", "Holder")
        offering = self.school.assign_course(
            teacher_user_id=int(owner["id"]), ontario_code="MCF3M"
        )
        class_id = self._class(
            int(other["id"]), int(offering["id"]), ["N8"], "12:35pm"
        )
        started = self.school.start_live_class_session(
            class_id, int(other["id"]), live_module="M1", live_slot="C1"
        )
        session_id = int(started["id"])
        with self.assertRaises(ValueError) as caught:
            self.school.delete_staff_permanently(int(owner["id"]), self.actor_id)
        message = str(caught.exception)
        self.assertIn(f"class {class_id}", message)
        self.assertIn(f"live session {session_id}", message)
        self.assertIn(f"course offering {int(offering['id'])}", message)
        self.assertNotIn("@", message)
        self.assertIsNotNone(self.school.get_user(int(owner["id"])))
        self.assertIsNotNone(self.school.get_user(int(other["id"])))
        self.assertEqual(
            self.school.conn.execute(
                "SELECT offering_id, teacher_user_id FROM classes WHERE id = ?",
                (class_id,),
            ).fetchone()["offering_id"],
            int(offering["id"]),
        )
        self.assertIsNotNone(
            self.school.conn.execute(
                "SELECT id FROM course_offerings WHERE id = ?",
                (int(offering["id"]),),
            ).fetchone()
        )

    def test_half_deleted_account_leaves_no_owned_rows(self) -> None:
        """Rows left after a partial delete are removed with the account."""
        owned = self._seed_owned_graph()
        class_id = self.owned_class_id
        offering_id = owned["offering_ids"][0]
        self.school.conn.execute(
            """
            UPDATE catchup_links
            SET live_session_id = ?
            WHERE token = 'owned-link'
            """,
            (owned["live_session_ids"][0],),
        )
        for key in owned["run_keys"]:
            self.school.conn.execute(
                """
                INSERT OR IGNORE INTO participation_runs (
                    class_id, run_key, is_real, auto_is_real, discarded
                ) VALUES (?, ?, 1, 1, 0)
                """,
                (class_id, key),
            )
        for index, session_id in enumerate(owned["live_session_ids"]):
            self.school.conn.execute(
                """
                INSERT INTO catchup_links (
                    token, class_id, run_key, live_module, live_slot,
                    live_session_id, created_at, updated_at
                ) VALUES (?, ?, ?, 'M9', ?, ?, 't', 't')
                """,
                (
                    f"owned-session-{index}",
                    class_id,
                    f"owned-session-{index}",
                    f"S{index}",
                    int(session_id),
                ),
            )
        self.school.conn.execute(
            "DELETE FROM live_class_sessions WHERE class_id = ?",
            (class_id,),
        )
        self.school.conn.execute("DELETE FROM classes WHERE id = ?", (class_id,))
        self.school.conn.commit()
        self.assertGreater(
            self.school.conn.execute(
                "SELECT COUNT(*) FROM catchup_links WHERE token = 'owned-link'"
            ).fetchone()[0],
            0,
        )
        self.school.delete_staff_permanently(self.owned_user_id, self.actor_id)
        self.assertIsNone(self.school.get_user(self.owned_user_id))
        self.assertIsNone(
            self.school.conn.execute(
                "SELECT id FROM course_offerings WHERE id = ?",
                (offering_id,),
            ).fetchone()
        )
        self.assertEqual(self._every_owned_hit(owned), [])
        self.assertEqual(
            self.school.conn.execute(
                "SELECT COUNT(*) FROM catchup_links WHERE token = 'kept-link'"
            ).fetchone()[0],
            1,
        )
        self.assertIsNotNone(self.school.get_user(self.kept_user_id))
        self._assert_news_gone(owned["live_session_ids"])

    def test_database_error_returns_a_clean_page(self) -> None:
        """A database failure is a 400 page and keeps the account."""
        teacher = self.school.register_staff("blocked@example.com", "Blocked")
        self.school.conn.execute(
            """
            CREATE TABLE staff_delete_probe (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id)
            )
            """
        )
        self.school.conn.execute(
            "INSERT INTO staff_delete_probe (user_id) VALUES (?)",
            (int(teacher["id"]),),
        )
        self._login_admin()
        response = self.client.post(
            f"/it/staff/{int(teacher['id'])}/delete",
            data={"confirm_email": teacher["email"]},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 400)
        body = response.get_data(as_text=True)
        self.assertNotIn("@", body)
        self.assertIn("could not be deleted", body)
        self.assertIsNotNone(self.school.get_user(int(teacher["id"])))


    def test_nullable_starter_rebuild_keeps_attendance(self) -> None:
        """An older NOT NULL starter column can be cleared without losing children."""
        from school_db import ensure_live_session_teacher_nullable

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            CREATE TABLE live_class_sessions (
                id INTEGER PRIMARY KEY,
                class_id INTEGER NOT NULL,
                teacher_user_id INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE live_session_attendees (
                id INTEGER PRIMARY KEY,
                live_session_id INTEGER NOT NULL
                    REFERENCES live_class_sessions(id) ON DELETE CASCADE,
                codename TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO live_class_sessions (id, class_id, teacher_user_id) VALUES (1, 7, 3)"
        )
        conn.execute(
            "INSERT INTO live_session_attendees (live_session_id, codename) VALUES (1, 'N1')"
        )
        conn.commit()
        ensure_live_session_teacher_nullable(conn)
        conn.execute(
            "UPDATE live_class_sessions SET teacher_user_id = NULL WHERE id = 1"
        )
        conn.commit()
        row = conn.execute(
            "SELECT teacher_user_id FROM live_class_sessions WHERE id = 1"
        ).fetchone()
        self.assertIsNone(row["teacher_user_id"])
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM live_session_attendees").fetchone()[0],
            1,
        )
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(list(conn.execute("PRAGMA foreign_key_check")), [])
        conn.close()




    def test_other_school_classless_rows_stay(self) -> None:
        """Another school's class-less rows are not part of this delete."""
        self._seed_owned_graph()
        other_tenant = self.school.create_tenant("other-school", "Other School")
        other = self.school.register_staff(
            "other-orphan@example.com",
            "Other",
            tenant_id=int(other_tenant["id"]),
        )
        offering = self.school.assign_course(
            teacher_user_id=int(other["id"]), ontario_code="MCF3M"
        )
        missing_offering = 900001
        missing_class = 900002
        missing_session = 900003
        self.school.conn.execute(
            """
            UPDATE course_offerings
            SET copied_from_offering_id = ?
            WHERE id = ?
            """,
            (missing_offering, int(offering["id"])),
        )
        self.school.conn.execute(
            """
            INSERT INTO catchup_links (
                token, class_id, run_key, live_module, live_slot, created_at, updated_at
            ) VALUES ('other-orphan', ?, 'other-orphan-run', 'M1', 'C1', 't', 't')
            """,
            (missing_class,),
        )
        self.school.conn.execute(
            "INSERT INTO live_seq_floors (scope, max_seq) VALUES (?, 4)",
            (f"class:{missing_class}",),
        )
        self.school.conn.execute(
            "INSERT INTO live_end_claims (run_key, claimed_at) VALUES (?, 't')",
            (f"finish:{missing_class}",),
        )
        self.school.conn.commit()
        self._seed_news(missing_session, "other")
        self.school.delete_staff_permanently(self.owned_user_id, self.actor_id)
        self.assertIsNone(self.school.get_user(self.owned_user_id))
        self.assertIsNotNone(self.school.get_user(int(other["id"])))
        self.assertEqual(
            self.school.conn.execute(
                "SELECT copied_from_offering_id FROM course_offerings WHERE id = ?",
                (int(offering["id"]),),
            ).fetchone()[0],
            missing_offering,
        )
        self.assertEqual(
            self.school.conn.execute(
                "SELECT COUNT(*) FROM catchup_links WHERE token = 'other-orphan'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.school.conn.execute(
                "SELECT COUNT(*) FROM live_seq_floors WHERE scope = ?",
                (f"class:{missing_class}",),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.school.conn.execute(
                "SELECT COUNT(*) FROM live_end_claims WHERE run_key = ?",
                (f"finish:{missing_class}",),
            ).fetchone()[0],
            1,
        )
        self._assert_news_kept(missing_session)

    def test_news_purge_logs_rows_left_when_it_stops(self) -> None:
        """A news purge that stops early logs how many rows remain."""
        from unittest.mock import patch

        owned = self._seed_owned_graph()
        session_ids = [int(session_id) for session_id in owned["live_session_ids"]]
        self.assertGreaterEqual(len(session_ids), 2)

        class _Tape:
            """Fail on the second session and delete nothing."""

            def __init__(self) -> None:
                self.calls = 0

            def purge_session(self, session_id: int) -> int:
                """Count calls, then raise before the tape is cleared."""
                self.calls += 1
                if self.calls >= 2:
                    raise RuntimeError("tape stopped")
                return 0

        with patch("live_news_wire.log_for", return_value=_Tape()):
            with self.assertLogs("school_db", level="WARNING") as logs:
                self.school._purge_news_tape_best_effort(
                    self.school.data_dir, session_ids
                )
        text = "\n".join(logs.output)
        self.assertIn("rows_left=", text)
        self.assertNotIn("@", text)
        self.assertNotIn("rows_left=0", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)

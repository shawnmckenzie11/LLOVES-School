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

# Columns that store an account, class, roster, offering, or live-session id.
_ID_COLUMNS = {
    "class_id": "class_ids",
    "student_id": "student_ids",
    "finalizer_student_id": "student_ids",
    "last_submitter_student_id": "student_ids",
    "last_editor_student_id": "student_ids",
    "teacher_user_id": "user_ids",
    "observer_user_id": "user_ids",
    "user_id": "user_ids",
    "created_by_user_id": "user_ids",
    "by_user_id": "user_ids",
    "reviewed_by_user_id": "user_ids",
    "marked_by": "user_ids",
    "discarded_by": "user_ids",
    "updated_by": "user_ids",
    "offering_id": "offering_ids",
    "copied_from_offering_id": "offering_ids",
    "live_session_id": "session_ids",
}
# The audit log is kept. It is not a child of the account.
_SKIP_TABLES = frozenset({"access_audit_log", "sqlite_sequence"})


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


def _owned_hits(conn: sqlite3.Connection, owned: dict[str, list]) -> list[str]:
    """Return ``table.column`` pairs that still hold a deleted id.

    Args:
        conn: Open sqlite connection.
        owned: Id lists keyed like ``_ID_COLUMNS`` values, plus ``run_keys``.
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
        for column in columns:
            kind = _ID_COLUMNS.get(column)
            values = owned.get(kind or "", [])
            if column == "run_key":
                values = owned.get("run_keys") or []
            if not values:
                continue
            marks = ",".join("?" * len(values))
            count = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} IN ({marks})",
                tuple(values),
            ).fetchone()[0]
            if int(count):
                hits.append(f"{table}.{column}")
        if table == "live_seq_floors" and "scope" in columns:
            scopes = [f"class:{int(cid)}" for cid in owned.get("class_ids") or []]
            scopes.extend(f"sid:{int(sid)}" for sid in owned.get("session_ids") or [])
            if scopes:
                marks = ",".join("?" * len(scopes))
                count = conn.execute(
                    f"SELECT COUNT(*) FROM live_seq_floors WHERE scope IN ({marks})",
                    tuple(scopes),
                ).fetchone()[0]
                if int(count):
                    hits.append("live_seq_floors.scope")
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
        return {
            "user_ids": [uid],
            "class_ids": [class_id],
            "student_ids": students,
            "offering_ids": [int(offering["id"])],
            "session_ids": session_ids,
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
        self.assertEqual(_owned_hits(self.school.conn, owned), [])
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

    def test_presence_failure_rolls_back(self) -> None:
        """A presence failure undoes the sqlite delete."""
        owned = self._seed_owned_graph()
        self.school.presence = _Presence(fail=True)
        with self.assertRaises(RuntimeError):
            self.school.delete_staff_permanently(self.owned_user_id, self.actor_id)
        self.assertIsNotNone(self.school.get_user(self.owned_user_id))
        self.assertEqual(
            self.school.conn.execute(
                "SELECT COUNT(*) FROM live_class_sessions WHERE id = ?",
                (owned["session_ids"][0],),
            ).fetchone()[0],
            1,
        )
        self.assertIsNotNone(self.school.get_user(self.kept_user_id))

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


if __name__ == "__main__":
    unittest.main(verbosity=2)

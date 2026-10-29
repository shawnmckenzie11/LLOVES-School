"""MCK-345: basic-plan defaults, overrides, session owner, and the grant."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from auth import it_emails  # noqa: E402
from features import (  # noqa: E402
    BASIC_OFF,
    BASIC_ON,
    FEATURE_KEYS,
    FEATURE_REGISTRY,
    ensure_feature_schema,
    feature_for_session,
    feature_required,
    grandfather_features_once,
    has_feature,
    migrate_existing_feature_access,
    plan_default,
    set_feature_override,
)


def _stamp() -> str:
    """ISO timestamp without microseconds."""
    return datetime.now().replace(microsecond=0).isoformat()


def _install_probe_routes(app) -> None:
    """Register one test-only probe per feature key.

    Production boot does not call this. Each probe is behind
    ``feature_required`` so the suite can see 404 versus 200.

    Args:
        app: Flask app under test.
    """

    def _view(feature_key: str):
        """Build a probe that echoes its key when the feature is on."""

        def probe() -> tuple[str, int]:
            """Return the feature key. The decorator answers 404 when off."""
            return feature_key, 200

        probe.__name__ = f"feature_probe_{feature_key}"
        return probe

    for key in FEATURE_KEYS:
        app.add_url_rule(
            f"/__test/feature/{key}",
            endpoint=f"feature_probe_{key}",
            view_func=feature_required(key)(_view(key)),
        )


class FeatureSchemaTests(unittest.TestCase):
    """Older sqlite files gain the column and the table."""

    def test_registry_defaults_and_unknown_keys(self) -> None:
        """Basic matches the registry. Pro turns every registry key on."""
        self.assertEqual(len(FEATURE_KEYS), len(set(FEATURE_KEYS)))
        self.assertEqual(set(BASIC_ON) | set(BASIC_OFF), set(FEATURE_KEYS))
        for key in BASIC_ON:
            self.assertTrue(FEATURE_REGISTRY[key], key)
            self.assertTrue(plan_default("basic", key), key)
            self.assertTrue(plan_default(None, key), key)
        for key in BASIC_OFF:
            self.assertFalse(FEATURE_REGISTRY[key], key)
            self.assertFalse(plan_default("basic", key), key)
        self.assertTrue(plan_default("basic", "whiteboard_submit"))
        self.assertFalse(plan_default("basic", "whiteboard"))
        self.assertTrue(plan_default("basic", "catchup_links"))
        self.assertFalse(plan_default("basic", "catchup_full"))
        for key in FEATURE_KEYS:
            self.assertTrue(plan_default("pro", key), key)
        self.assertFalse(plan_default("pro", "not_in_registry"))
        self.assertFalse(plan_default("basic", "not_in_registry"))
        self.assertFalse(plan_default("other", "mc_questions"))

    def test_alter_adds_plan_on_an_old_users_table(self) -> None:
        """A users table from before this column still opens, default basic."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL,
                role TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO users (email, role) VALUES ('legacy@example.com', 'staff')"
        )
        ensure_feature_schema(conn)
        ensure_feature_schema(conn)
        plan = conn.execute("SELECT plan FROM users").fetchone()["plan"]
        self.assertEqual(plan, "basic")
        names = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertIn("user_features", names)
        conn.close()


class FeatureAccessTests(unittest.TestCase):
    """Defaults, overrides, the launch grant, and the 404 decorator."""

    def setUp(self) -> None:
        """Fresh school. Local demo seed stays off so the cohort is the test's."""
        self._prev_local = os.environ.get("LOCAL_DEV_LOGIN")
        os.environ.pop("LOCAL_DEV_LOGIN", None)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
            live_database_url="",
        )
        self.school = self.app.config["SCHOOL_DB"]
        _install_probe_routes(self.app)

    def tearDown(self) -> None:
        """Close the database and restore the process environment."""
        self.school.close()
        self.tmp.cleanup()
        if self._prev_local is None:
            os.environ.pop("LOCAL_DEV_LOGIN", None)
        else:
            os.environ["LOCAL_DEV_LOGIN"] = self._prev_local

    def _flags(self, user: dict) -> dict[str, bool]:
        """Read every registry key for ``user`` inside an app context."""
        with self.app.app_context():
            return {key: has_feature(user, key) for key in FEATURE_KEYS}

    def _rows_for(self, user_id: int) -> int:
        """Count override rows for one user."""
        row = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM user_features WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()
        return int(row["n"])

    def _snapshot(self) -> list[tuple]:
        """Every override row, including timestamps, in a stable order."""
        rows = self.school.conn.execute(
            """
            SELECT user_id, feature_key, enabled, updated_at
            FROM user_features
            ORDER BY user_id, feature_key
            """
        ).fetchall()
        return [
            (
                int(row["user_id"]),
                str(row["feature_key"]),
                int(row["enabled"]),
                str(row["updated_at"]),
            )
            for row in rows
        ]

    def _add_user(self, email: str, role: str) -> dict:
        """Insert a user row and return it.

        Args:
            email: Synthetic address.
            role: Stored role. ``guest`` is outside staff and IT.
        """
        self.school.conn.execute(
            """
            INSERT INTO users (email, display_name, role, tenant_id, created_at)
            VALUES (?, '', ?, ?, ?)
            """,
            (email, role, self.school.default_tenant_id(), _stamp()),
        )
        user = self.school.get_user_by_email(email)
        self.assertIsNotNone(user)
        assert user is not None
        return user

    def _role_id(self, role: str) -> int:
        """First user id with this role."""
        row = self.school.conn.execute(
            "SELECT id FROM users WHERE role = ? ORDER BY id LIMIT 1",
            (role,),
        ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        return int(row["id"])

    def _client_for(self, user_id: int):
        """A test client signed in as ``user_id``."""
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["user_id"] = int(user_id)
            sess["portal"] = "staff"
            sess["role"] = "staff"
        return client

    def _session_for(self, teacher_id: int | None, code: str) -> int:
        """Insert a live session owned by ``teacher_id`` and return its id."""
        cur = self.school.conn.execute(
            """
            INSERT INTO live_class_sessions (
                class_id, offering_id, teacher_user_id, session_code,
                status, started_at
            ) VALUES (0, 0, ?, ?, 'active', ?)
            """,
            (teacher_id, code, _stamp()),
        )
        return int(cur.lastrowid)

    def test_new_user_gets_only_the_on_list(self) -> None:
        """A teacher created after boot has the basic ON keys and no OFF key."""
        user = self.school.register_staff("basic-teacher@example.com", "Basic")
        self.assertEqual(user.get("plan"), "basic")
        self.assertEqual(self._rows_for(int(user["id"])), 0)
        flags = self._flags(user)
        for key in BASIC_ON:
            self.assertTrue(flags[key], key)
        for key in BASIC_OFF:
            self.assertFalse(flags[key], key)
        self.assertTrue(flags["whiteboard_submit"])
        self.assertTrue(flags["catchup_links"])
        self.assertFalse(flags["whiteboard"])
        self.assertFalse(flags["catchup_full"])
        with self.app.app_context():
            self.assertFalse(has_feature(user, "not_in_registry"))

    def test_boot_grant_does_not_expand_for_a_later_teacher(self) -> None:
        """The boot marker does not turn every feature on for someone who joins later."""
        late = self.school.register_staff("later-teacher@example.com", "Later")
        again = grandfather_features_once(self.school)
        self.assertEqual(again["applied"], 0)
        self.assertEqual(again["rows_written"], 0)
        self.assertEqual(self._rows_for(int(late["id"])), 0)
        self.assertFalse(self._flags(late)["whiteboard"])
        self.assertFalse(self._flags(late)["catchup_full"])
        self.assertTrue(self._flags(late)["whiteboard_submit"])
        self.assertTrue(self._flags(late)["catchup_links"])
        self.assertTrue(self._flags(late)["mc_questions"])

    def test_it_role_is_granted_at_boot(self) -> None:
        """The seeded IT account has every registry key after boot."""
        user = self.school.get_user(self._role_id("it"))
        self.assertIsNotNone(user)
        assert user is not None
        flags = self._flags(user)
        for key in FEATURE_KEYS:
            self.assertTrue(flags[key], key)
        self.assertEqual(self._rows_for(int(user["id"])), len(FEATURE_KEYS))

    def test_overrides_beat_the_plan_default(self) -> None:
        """An on-override and an off-override both win over the basic plan."""
        user = self.school.register_staff("override-teacher@example.com", "Override")
        set_feature_override(self.school, int(user["id"]), "whiteboard", True)
        set_feature_override(self.school, int(user["id"]), "mc_questions", False)
        flags = self._flags(user)
        self.assertTrue(flags["whiteboard"])
        self.assertFalse(flags["mc_questions"])
        self.assertTrue(flags["rank_questions"])
        self.assertFalse(flags["media"])
        set_feature_override(self.school, int(user["id"]), "not_in_registry", True)
        with self.app.app_context():
            self.assertTrue(has_feature(user, "not_in_registry"))

    def test_pro_plan_turns_on_every_key_until_an_override(self) -> None:
        """Pro enables every registry key. An off override still wins."""
        user = self.school.register_staff("pro-teacher@example.com", "Pro")
        self.school.conn.execute(
            "UPDATE users SET plan = 'pro' WHERE id = ?",
            (int(user["id"]),),
        )
        fresh = self.school.get_user(int(user["id"]))
        self.assertIsNotNone(fresh)
        assert fresh is not None
        self.assertEqual(fresh.get("plan"), "pro")
        self.assertEqual(self._rows_for(int(user["id"])), 0)
        flags = self._flags(fresh)
        for key in FEATURE_KEYS:
            self.assertTrue(flags[key], key)
        with self.app.app_context():
            self.assertFalse(has_feature(fresh, "not_in_registry"))
        set_feature_override(self.school, int(user["id"]), "whiteboard", False)
        after = self._flags(fresh)
        self.assertFalse(after["whiteboard"])
        self.assertTrue(after["whiteboard_submit"])
        self.assertTrue(after["catchup_full"])

    def test_migration_grants_the_cohort_and_rerun_writes_nothing(self) -> None:
        """Staff, IT, allowlist, and test accounts get every key. The second run is a no-op."""
        staff = self.school.register_staff("cohort-staff@example.com", "Cohort")
        archived = self.school.register_staff("cohort-archived@example.com", "Archived")
        self.school.conn.execute(
            "UPDATE users SET archived_at = ? WHERE id = ?",
            (_stamp(), int(archived["id"])),
        )
        plain = self._add_user("plain-guest@example.com", "guest")
        flagged = self._add_user("flagged-tester@example.com", "guest")
        self.school.conn.execute(
            "UPDATE users SET is_test = 1 WHERE id = ?",
            (int(flagged["id"]),),
        )
        invited = self._add_user("invite-only@example.com", "guest")
        later = _stamp()
        expires = (datetime.now() + timedelta(days=7)).replace(microsecond=0).isoformat()
        self.school.conn.execute(
            """
            INSERT INTO staff_invites (
                tenant_id, user_id, email, first_name, kind, token_sha256,
                created_at, expires_at
            ) VALUES (?, ?, ?, 'Invite', 'test', ?, ?, ?)
            """,
            (
                self.school.default_tenant_id(),
                int(invited["id"]),
                "invite-only@example.com",
                "ab" * 32,
                later,
                expires,
            ),
        )
        allowlisted = self._add_user("allowlisted-operator@example.com", "guest")
        allow = set(it_emails()) | {"allowlisted-operator@example.com"}
        with patch("auth.it_emails", return_value=allow):
            first = migrate_existing_feature_access(self.school)
        self.assertGreater(first["rows_written"], 0)
        granted = (staff, archived, flagged, invited, allowlisted)
        for user in granted:
            fresh = self.school.get_user(int(user["id"]))
            assert fresh is not None
            flags = self._flags(fresh)
            for key in FEATURE_KEYS:
                self.assertTrue(flags[key], key)
            self.assertEqual(self._rows_for(int(user["id"])), len(FEATURE_KEYS))
        it_user = self.school.get_user(self._role_id("it"))
        assert it_user is not None
        for key in FEATURE_KEYS:
            self.assertTrue(self._flags(it_user)[key], key)
        plain_flags = self._flags(plain)
        for key in BASIC_OFF:
            self.assertFalse(plain_flags[key], key)
        self.assertEqual(self._rows_for(int(plain["id"])), 0)
        snap = self._snapshot()
        with patch("auth.it_emails", return_value=allow):
            second = migrate_existing_feature_access(self.school)
        self.assertEqual(second["rows_written"], 0)
        self.assertEqual(self._snapshot(), snap)
        with patch("auth.it_emails", return_value=allow):
            third = migrate_existing_feature_access(self.school)
        self.assertEqual(third["rows_written"], 0)
        self.assertEqual(self._snapshot(), snap)

    def test_session_uses_the_owning_teacher(self) -> None:
        """A student follows the session owner, not another teacher's flags."""
        owner = self.school.register_staff("session-owner@example.com", "Owner")
        other = self.school.register_staff("session-other@example.com", "Other")
        set_feature_override(self.school, int(owner["id"]), "whiteboard", True)
        set_feature_override(self.school, int(owner["id"]), "mc_questions", False)
        owner_session = self._session_for(int(owner["id"]), "synthetic-session-owner")
        other_session = self._session_for(int(other["id"]), "synthetic-session-other")
        empty_session = self._session_for(None, "synthetic-session-empty")
        with self.app.app_context():
            self.assertTrue(feature_for_session(owner_session, "whiteboard"))
            self.assertFalse(feature_for_session(owner_session, "mc_questions"))
            self.assertTrue(feature_for_session(owner_session, "rank_questions"))
            self.assertFalse(feature_for_session(owner_session, "media"))
            self.assertFalse(feature_for_session(other_session, "whiteboard"))
            self.assertTrue(feature_for_session(other_session, "mc_questions"))
            self.assertFalse(feature_for_session(empty_session, "mc_questions"))
            self.assertFalse(feature_for_session(999999, "mc_questions"))

    def test_probe_is_404_for_basic_and_200_for_allowlisted(self) -> None:
        """Each OFF key is 404 for a basic teacher and 200 for the migrated IT account."""
        basic = self.school.register_staff("probe-basic@example.com", "Probe")
        migrated_id = self._role_id("it")
        basic_client = self._client_for(int(basic["id"]))
        migrated_client = self._client_for(migrated_id)
        anonymous = self.app.test_client()
        for key in BASIC_OFF:
            denied = basic_client.get(f"/__test/feature/{key}")
            self.assertEqual(denied.status_code, 404, key)
            self.assertNotEqual(denied.status_code, 403, key)
            allowed = migrated_client.get(f"/__test/feature/{key}")
            self.assertEqual(allowed.status_code, 200, key)
            self.assertEqual(allowed.get_data(as_text=True), key)
            hidden = anonymous.get(f"/__test/feature/{key}")
            self.assertEqual(hidden.status_code, 404, key)
        opened = basic_client.get("/__test/feature/mc_questions")
        self.assertEqual(opened.status_code, 200)
        self.assertEqual(opened.get_data(as_text=True), "mc_questions")
        submit = basic_client.get("/__test/feature/whiteboard_submit")
        self.assertEqual(submit.status_code, 200)
        self.assertEqual(submit.get_data(as_text=True), "whiteboard_submit")
        recent = basic_client.get("/__test/feature/catchup_links")
        self.assertEqual(recent.status_code, 200)
        full = basic_client.get("/__test/feature/catchup_full")
        self.assertEqual(full.status_code, 404)

    def test_signed_out_probe_follows_the_session_teacher(self) -> None:
        """A signed-out caller uses the session owner. A signed-in teacher does not borrow it."""
        owner = self.school.register_staff("probe-owner@example.com", "Owner")
        basic = self.school.register_staff("probe-borrow@example.com", "Borrow")
        set_feature_override(self.school, int(owner["id"]), "whiteboard", True)
        session_id = self._session_for(int(owner["id"]), "synthetic-session-probe")
        anonymous = self.app.test_client()
        allowed = anonymous.get(f"/__test/feature/whiteboard?session_id={session_id}")
        self.assertEqual(allowed.status_code, 200)
        denied = anonymous.get(f"/__test/feature/media?session_id={session_id}")
        self.assertEqual(denied.status_code, 404)
        basic_client = self._client_for(int(basic["id"]))
        borrowed = basic_client.get(f"/__test/feature/whiteboard?session_id={session_id}")
        self.assertEqual(borrowed.status_code, 404)

    def test_decorator_is_not_wired_to_production_routes(self) -> None:
        """The registry PR ships the decorator without hanging it on a real route."""
        for path in LMS_DIR.rglob("*.py"):
            if path.name == "features.py" or path.name.startswith("test_"):
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(
                "feature_required(",
                text,
                str(path.relative_to(LMS_DIR)),
            )


if __name__ == "__main__":
    unittest.main()

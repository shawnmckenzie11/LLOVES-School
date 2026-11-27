"""MCK-538: admin teacher plan and feature override page."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from features import (  # noqa: E402
    FEATURE_KEYS,
    OVERRIDE_OFF,
    OVERRIDE_PLAN,
    OVERRIDE_ON,
    PLAN_BASIC,
    PLAN_PRO,
    grandfather_features_once,
    has_feature,
)
from feature_admin import (  # noqa: E402
    admin_reset_teacher_overrides,
    admin_set_teacher_override,
    admin_set_teacher_plan,
    assert_audit_append_only,
    audit_row_count,
)


def _stamp() -> str:
    """ISO timestamp without microseconds."""
    return datetime.now().replace(microsecond=0).isoformat()


class TeacherPlanAdminTests(unittest.TestCase):
    """Plan admin routes, audit log, and effective flags."""

    def setUp(self) -> None:
        """Fresh sqlite app without local dev seed."""
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
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        self.admin_id = self._add_user("admin-a@example.com", "it")
        self.teacher_id = self._add_user("teacher-a@example.com", "staff")

    def tearDown(self) -> None:
        """Release the temp database."""
        self.school.close()
        self.tmp.cleanup()
        if self._prev_local is None:
            os.environ.pop("LOCAL_DEV_LOGIN", None)
        else:
            os.environ["LOCAL_DEV_LOGIN"] = self._prev_local

    def _add_user(self, email: str, role: str) -> int:
        """Insert a synthetic user and return its id."""
        self.school.conn.execute(
            """
            INSERT INTO users (email, display_name, role, tenant_id, created_at, plan)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                email,
                f"Handle-{role}",
                role,
                self.school.default_tenant_id(),
                _stamp(),
                PLAN_BASIC,
            ),
        )
        row = self.school.conn.execute(
            "SELECT id FROM users WHERE email = ?", (email,)
        ).fetchone()
        return int(row["id"])

    def _login(self, user_id: int, portal: str) -> None:
        """Finish an offline Google callback login."""
        user = self.school.get_user(user_id)
        assert user is not None
        self.client.get(f"/auth/google?portal={portal}")
        self.client.get(
            "/auth/google/callback?"
            + f"email={user['email']}&name={user.get('display_name') or 'User'}"
        )
        fresh = self.school.get_user(user_id)
        assert fresh is not None
        self.client.post("/verify-email", data={"code": fresh["verification_code"]})

    def test_non_admin_gets_404(self) -> None:
        """Staff cannot open the page or call the API."""
        self._login(self.teacher_id, "staff")
        page = self.client.get("/it/teacher-plans")
        self.assertEqual(page.status_code, 404)
        api = self.client.post(
            f"/it/api/teacher-plans/{self.teacher_id}/plan",
            json={"plan": PLAN_PRO},
        )
        self.assertEqual(api.status_code, 404)

    def test_audit_triggers_refuse_mutation(self) -> None:
        """Audit rows are append-only on sqlite."""
        assert_audit_append_only(self.school.conn)

    def test_plan_and_override_next_request(self) -> None:
        """Changes apply on the next ``has_feature`` read."""
        admin_set_teacher_plan(self.school, self.admin_id, self.teacher_id, PLAN_PRO)
        with self.app.app_context():
            user = self.school.get_user(self.teacher_id)
            self.assertTrue(has_feature(user, "whiteboard"))
        admin_set_teacher_override(
            self.school,
            self.admin_id,
            self.teacher_id,
            "whiteboard",
            OVERRIDE_OFF,
        )
        with self.app.app_context():
            user = self.school.get_user(self.teacher_id)
            self.assertFalse(has_feature(user, "whiteboard"))

    def test_follow_plan_deletes_row(self) -> None:
        """Follow plan removes the override row."""
        admin_set_teacher_override(
            self.school,
            self.admin_id,
            self.teacher_id,
            "media",
            OVERRIDE_ON,
        )
        self.assertEqual(
            1,
            self.school.conn.execute(
                "SELECT COUNT(*) FROM user_features WHERE user_id = ? AND feature_key = ?",
                (self.teacher_id, "media"),
            ).fetchone()[0],
        )
        admin_set_teacher_override(
            self.school,
            self.admin_id,
            self.teacher_id,
            "media",
            OVERRIDE_PLAN,
        )
        self.assertEqual(
            0,
            self.school.conn.execute(
                "SELECT COUNT(*) FROM user_features WHERE user_id = ?",
                (self.teacher_id,),
            ).fetchone()[0],
        )

    def test_one_audit_row_per_real_change(self) -> None:
        """No audit row on a no-op; one row per actual change."""
        before = audit_row_count(self.school.conn, self.teacher_id)
        admin_set_teacher_plan(
            self.school, self.admin_id, self.teacher_id, PLAN_BASIC
        )
        self.assertEqual(before, audit_row_count(self.school.conn, self.teacher_id))
        admin_set_teacher_plan(self.school, self.admin_id, self.teacher_id, PLAN_PRO)
        self.assertEqual(before + 1, audit_row_count(self.school.conn, self.teacher_id))
        admin_set_teacher_override(
            self.school,
            self.admin_id,
            self.teacher_id,
            "game_show",
            OVERRIDE_OFF,
        )
        self.assertEqual(before + 2, audit_row_count(self.school.conn, self.teacher_id))

    def test_plan_change_leaves_overrides(self) -> None:
        """Pro/basic plan flip does not delete override rows."""
        admin_set_teacher_override(
            self.school,
            self.admin_id,
            self.teacher_id,
            "catchup_full",
            OVERRIDE_ON,
        )
        admin_set_teacher_plan(self.school, self.admin_id, self.teacher_id, PLAN_PRO)
        row = self.school.conn.execute(
            """
            SELECT enabled FROM user_features
            WHERE user_id = ? AND feature_key = 'catchup_full'
            """,
            (self.teacher_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(1, int(row["enabled"]))

    def test_grandfather_does_not_undo_forced_off(self) -> None:
        """Second grandfather pass leaves a forced-off override alone."""
        grandfather_features_once(self.school)
        admin_set_teacher_override(
            self.school,
            self.admin_id,
            self.teacher_id,
            "slides_import",
            OVERRIDE_OFF,
        )
        grandfather_features_once(self.school)
        with self.app.app_context():
            user = self.school.get_user(self.teacher_id)
            self.assertFalse(has_feature(user, "slides_import"))

    def test_reset_overrides_audits_each(self) -> None:
        """Reset removes rows and writes one audit entry per key."""
        admin_set_teacher_override(
            self.school, self.admin_id, self.teacher_id, "media", OVERRIDE_ON
        )
        admin_set_teacher_override(
            self.school, self.admin_id, self.teacher_id, "team_draw", OVERRIDE_OFF
        )
        before = audit_row_count(self.school.conn, self.teacher_id)
        removed = admin_reset_teacher_overrides(
            self.school, self.admin_id, self.teacher_id
        )
        self.assertEqual(2, removed)
        self.assertEqual(before + 2, audit_row_count(self.school.conn, self.teacher_id))

    def test_admin_page_lists_handle_not_email(self) -> None:
        """Rendered page shows user id and handle only."""
        self._login(self.admin_id, "it")
        body = self.client.get("/it/teacher-plans").get_data(as_text=True)
        self.assertIn(f"User {self.teacher_id}", body)
        self.assertIn("Handle-staff", body)
        self.assertNotIn("teacher-a@example.com", body)
        for key in FEATURE_KEYS:
            self.assertIn(key, body)

    def test_api_rejects_unknown_feature_key(self) -> None:
        """Unknown keys are 404."""
        self._login(self.admin_id, "it")
        bad = self.client.post(
            f"/it/api/teacher-plans/{self.teacher_id}/features/not_in_registry",
            json={"mode": OVERRIDE_ON},
        )
        self.assertEqual(bad.status_code, 404)


if __name__ == "__main__":
    unittest.main()
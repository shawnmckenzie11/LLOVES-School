"""MCK-540: admin teacher plans console (bulk, history, audit reads)."""

from __future__ import annotations

import json
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
from feature_admin import (  # noqa: E402
    admin_bulk_set_teacher_plan,
    admin_set_teacher_override,
    admin_set_teacher_plan,
    list_teacher_change_history,
    list_tenant_feature_audit,
)
from features import OVERRIDE_ON, OVERRIDE_PLAN, PLAN_BASIC, PLAN_PRO  # noqa: E402
from test_mck538_teacher_plans import (  # noqa: E402
    TeacherPlanAdminTests,
    audit_row_count,
)


def _stamp() -> str:
    """ISO timestamp without microseconds."""
    return datetime.now().replace(microsecond=0).isoformat()


class TeacherPlanConsoleTests(TeacherPlanAdminTests):
    """MCK-540 read APIs, bulk plan, and undo audit behaviour."""

    def test_bulk_all_or_nothing_cross_tenant(self) -> None:
        """One bad id rolls back the whole bulk batch."""
        other = self.school.create_tenant("bulk-other", "Other")
        outsider = self.school.register_staff(
            "bulk-outsider@example.com",
            tenant_id=int(other["id"]),
        )
        outsider_id = int(outsider["id"])
        before_audits = int(
            self.school.conn.execute(
                "SELECT COUNT(*) FROM teacher_feature_audit"
            ).fetchone()[0]
        )
        with self.assertRaises(ValueError):
            admin_bulk_set_teacher_plan(
                self.school,
                self.admin_id,
                [self.teacher_id, outsider_id],
                PLAN_PRO,
            )
        plan = self.school.conn.execute(
            "SELECT plan FROM users WHERE id = ?", (self.teacher_id,)
        ).fetchone()["plan"]
        self.assertEqual(str(plan), PLAN_BASIC)
        after_audits = int(
            self.school.conn.execute(
                "SELECT COUNT(*) FROM teacher_feature_audit"
            ).fetchone()[0]
        )
        self.assertEqual(before_audits, after_audits)

    def test_bulk_one_audit_row_per_changed_teacher(self) -> None:
        """Bulk writes one audit row per teacher whose plan flips."""
        teacher_b = self._add_user("teacher-b@example.com", "staff")
        before = audit_row_count(self.school.conn, self.teacher_id)
        before_b = audit_row_count(self.school.conn, teacher_b)
        changed = admin_bulk_set_teacher_plan(
            self.school,
            self.admin_id,
            [self.teacher_id, teacher_b],
            PLAN_PRO,
        )
        self.assertEqual(2, changed)
        self.assertEqual(before + 1, audit_row_count(self.school.conn, self.teacher_id))
        self.assertEqual(before_b + 1, audit_row_count(self.school.conn, teacher_b))

    def test_bulk_api_all_or_nothing(self) -> None:
        """HTTP bulk returns 400 and leaves storage unchanged on a bad id."""
        other = self.school.create_tenant("bulk-http", "Other")
        outsider = self.school.register_staff(
            "bulk-http@example.com",
            tenant_id=int(other["id"]),
        )
        self._login(self.admin_id, "it")
        resp = self.client.post(
            "/it/api/teacher-plans/bulk-plan",
            json={"teacher_ids": [self.teacher_id, int(outsider["id"])], "plan": PLAN_PRO},
        )
        self.assertEqual(400, resp.status_code)
        row = self.school.conn.execute(
            "SELECT plan FROM users WHERE id = ?", (self.teacher_id,)
        ).fetchone()
        self.assertEqual(PLAN_BASIC, str(row["plan"]))

    def test_undo_adds_audit_row(self) -> None:
        """Undo is a second write with its own audit entry."""
        admin_set_teacher_override(
            self.school,
            self.admin_id,
            self.teacher_id,
            "whiteboard",
            OVERRIDE_ON,
        )
        before = audit_row_count(self.school.conn, self.teacher_id)
        admin_set_teacher_override(
            self.school,
            self.admin_id,
            self.teacher_id,
            "whiteboard",
            OVERRIDE_PLAN,
        )
        self.assertEqual(before + 1, audit_row_count(self.school.conn, self.teacher_id))

    def test_cross_tenant_history_empty(self) -> None:
        """History read for another tenant's teacher returns nothing."""
        other = self.school.create_tenant("hist-other", "Other")
        outsider = self.school.register_staff(
            "hist@example.com",
            tenant_id=int(other["id"]),
        )
        outsider_id = int(outsider["id"])
        admin_set_teacher_plan(self.school, self.admin_id, self.teacher_id, PLAN_PRO)
        rows = list_teacher_change_history(self.school, self.admin_id, outsider_id)
        self.assertEqual([], rows)

    def test_cross_tenant_audit_read_empty(self) -> None:
        """Audit list never includes another tenant's teachers."""
        other = self.school.create_tenant("audit-other", "Other")
        outsider = self.school.register_staff(
            "audit-outsider@example.com",
            tenant_id=int(other["id"]),
        )
        outsider_id = int(outsider["id"])
        self.school.conn.execute(
            """
            INSERT INTO teacher_feature_audit (
                admin_user_id, teacher_user_id, key, old_value, new_value, created_at
            ) VALUES (?, ?, 'plan', 'basic', 'pro', ?)
            """,
            (self.admin_id, outsider_id, _stamp()),
        )
        admin_set_teacher_plan(self.school, self.admin_id, self.teacher_id, PLAN_PRO)
        rows = list_tenant_feature_audit(self.school, self.admin_id)
        teacher_ids = {int(r["teacher_id"]) for r in rows}
        self.assertNotIn(outsider_id, teacher_ids)
        self.assertIn(self.teacher_id, teacher_ids)

    def test_history_api_cross_tenant_404(self) -> None:
        """History HTTP route 404s outside the admin tenant."""
        other = self.school.create_tenant("hist-http", "Other")
        outsider = self.school.register_staff(
            "hist-http@example.com",
            tenant_id=int(other["id"]),
        )
        self._login(self.admin_id, "it")
        resp = self.client.get(
            f"/it/api/teacher-plans/{int(outsider['id'])}/history"
        )
        self.assertEqual(404, resp.status_code)

    def test_non_it_history_404(self) -> None:
        """Staff cannot read history JSON."""
        self._login(self.teacher_id, "staff")
        resp = self.client.get(f"/it/api/teacher-plans/{self.teacher_id}/history")
        self.assertEqual(404, resp.status_code)

    def test_admin_page_shell_and_api_handle(self) -> None:
        """Page shell loads; list API returns handle without email."""
        self._login(self.admin_id, "it")
        page = self.client.get("/it/teacher-plans")
        self.assertEqual(200, page.status_code)
        body = page.get_data(as_text=True)
        self.assertIn("Teacher plans", body)
        self.assertIn("Change history", body)
        self.assertNotIn("teacher-a@example.com", body)
        api = self.client.get("/it/api/teacher-plans")
        self.assertEqual(200, api.status_code)
        payload = json.loads(api.get_data(as_text=True))
        handles = [t["handle"] for t in payload["teachers"]]
        self.assertIn("Handle-staff", handles)


if __name__ == "__main__":
    unittest.main()
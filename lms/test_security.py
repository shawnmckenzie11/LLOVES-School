#!/usr/bin/env python3
"""Role isolation, tenant seams, privileged MFA, and access audit."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)

from app import create_app  # noqa: E402


class SecurityTests(unittest.TestCase):
    """Staff isolation, second-tenant seam, MFA, audit, headers."""

    def setUp(self) -> None:
        """Isolated sqlite + two staff teachers on the default tenant."""
        self._env_prev = {
            key: os.environ.get(key)
            for key in ("FLASK_ENV", "REQUIRE_PRIVILEGED_MFA")
        }
        os.environ.pop("FLASK_ENV", None)
        os.environ.pop("REQUIRE_PRIVILEGED_MFA", None)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher_a = self.school.register_staff("a@gmail.com")
        self.teacher_b = self.school.register_staff("b@gmail.com")
        self.offering_a = self.school.assign_course(
            teacher_user_id=int(self.teacher_a["id"]), ontario_code="MCF3M"
        )
        self.offering_b = self.school.assign_course(
            teacher_user_id=int(self.teacher_b["id"]), ontario_code="MCF3M"
        )
        self.class_a = self._create_class(self.teacher_a, self.offering_a, ["Aspen"])
        self.class_b = self._create_class(self.teacher_b, self.offering_b, ["Birch"])

    def tearDown(self) -> None:
        """Close db and restore MFA-related env."""
        self.school.close()
        self.tmp.cleanup()
        for key, value in self._env_prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _create_class(self, teacher: dict, offering: dict, names: list[str]) -> dict:
        """Insert an Attendance & Participation class for one teacher."""
        semester = self.school.get_active_semester()
        assert semester is not None
        created = self.school.game.create_class(
            year=str(semester["year_display"]),
            semester=str(semester["term"]),
            course_code=str(offering["ontario_code"]),
            days_preset="M/W/F",
            time_label="2:00pm",
            codenames=names,
            offering_id=int(offering["id"]),
            teacher_user_id=int(teacher["id"]),
        )
        return self.school.enrich_class(created)

    def _login(self, client, email: str, portal: str = "staff") -> None:
        """Complete mock Google + email 2SV on one test client."""
        client.get(f"/auth/google?portal={portal}")
        client.get(f"/auth/google/callback?email={email}&name=T")
        user = self.school.get_user_by_email(email)
        assert user is not None
        code = user.get("verification_code")
        if code:
            rv = client.post("/verify-email", data={"code": code})
            self.assertEqual(rv.status_code, 302)

    def test_staff_cannot_open_another_teachers_class(self) -> None:
        """Teacher A gets 403 on teacher B's course and gradebook."""
        self._login(self.client, "a@gmail.com")
        denied = self.client.get(f"/staff/class/{self.class_b['id']}")
        self.assertEqual(denied.status_code, 403)
        grades = self.client.get(f"/api/classes/{self.class_b['id']}/gradebook")
        self.assertEqual(grades.status_code, 403)
        own = self.client.get(f"/staff/class/{self.class_a['id']}")
        self.assertEqual(own.status_code, 200)

    def test_tenant_isolation_hides_other_school_records(self) -> None:
        """A second tenants row keeps roster/classes off the ELC IT/staff lists."""
        other = self.school.create_tenant("homeschool-a", "Homeschool A")
        outsider = self.school.register_staff(
            "other@gmail.com", tenant_id=int(other["id"])
        )
        offering = self.school.assign_course(
            teacher_user_id=int(outsider["id"]), ontario_code="MCF3M"
        )
        self.assertEqual(int(offering["tenant_id"]), int(other["id"]))
        cls = self._create_class(outsider, offering, ["Cedar"])
        elc = self.school.default_tenant_id()
        emails = {row["email"] for row in self.school.list_staff(tenant_id=elc)}
        self.assertNotIn("other@gmail.com", emails)
        offering_ids = {
            int(row["id"]) for row in self.school.list_offerings(tenant_id=elc)
        }
        self.assertNotIn(int(offering["id"]), offering_ids)
        it_user = self.school.get_user_by_email("solutions@mckenzian.com")
        assert it_user is not None
        self.assertFalse(
            self.school.teacher_owns_class(int(it_user["id"]), int(cls["id"]))
        )
        self.assertFalse(
            self.school.teacher_owns_class(int(self.teacher_a["id"]), int(cls["id"]))
        )
        self.assertTrue(
            self.school.teacher_owns_class(int(outsider["id"]), int(cls["id"]))
        )
        self._login(self.client, "a@gmail.com")
        self.assertEqual(self.client.get(f"/staff/class/{cls['id']}").status_code, 403)

    def test_production_requires_mfa_every_staff_login(self) -> None:
        """FLASK_ENV=production cannot skip email 2SV on later Google logins."""
        os.environ["FLASK_ENV"] = "production"
        self.school.register_staff("mfa@gmail.com")
        client = self.app.test_client()
        client.get("/auth/google?portal=staff")
        first = client.get("/auth/google/callback?email=mfa@gmail.com&name=T")
        self.assertIn("/verify-email", first.headers.get("Location", ""))
        user = self.school.get_user_by_email("mfa@gmail.com")
        assert user is not None
        client.post("/verify-email", data={"code": user["verification_code"]})
        home = client.get("/staff")
        self.assertEqual(home.status_code, 200)
        client.get("/logout")
        second = client.get("/auth/google?portal=staff")
        second = client.get("/auth/google/callback?email=mfa@gmail.com&name=T")
        self.assertEqual(second.status_code, 302)
        self.assertIn("/verify-email", second.headers.get("Location", ""))
        self.assertNotIn("/staff", second.headers.get("Location", ""))

    def test_login_and_record_view_are_audited(self) -> None:
        """Privileged login and class page views land in the tenant audit log."""
        self._login(self.client, "a@gmail.com")
        self.client.get(f"/staff/class/{self.class_a['id']}?tab=ap")
        rows = self.school.list_access_events(
            tenant_id=self.school.default_tenant_id()
        )
        actions = {row["action"] for row in rows}
        self.assertIn("login.success", actions)
        self.assertIn("student.record.view", actions)
        csv_denied = self.client.get("/it/audit.csv")
        self.assertIn(csv_denied.status_code, {302, 403})
        it_client = self.app.test_client()
        self._login(it_client, "solutions@mckenzian.com", portal="it")
        csv_ok = it_client.get("/it/audit.csv")
        self.assertEqual(csv_ok.status_code, 200)
        self.assertIn("login.success", csv_ok.get_data(as_text=True))
        dash = it_client.get("/it?tab=audit")
        self.assertEqual(dash.status_code, 200)
        self.assertIn("Access audit", dash.get_data(as_text=True))

    def test_security_headers_on_health(self) -> None:
        """Public responses carry browser isolation headers."""
        rv = self.client.get("/health")
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(rv.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(rv.headers.get("X-Frame-Options"), "DENY")
        self.assertIn("default-src 'self'", rv.headers.get("Content-Security-Policy", ""))

    def test_student_join_still_works_without_google(self) -> None:
        """Live-session student join is unchanged (no MFA on the student path)."""
        self._login(self.client, "a@gmail.com")
        started = self.client.post(f"/staff/class/{self.class_a['id']}/run-live")
        self.assertEqual(started.status_code, 302)
        live = self.school.get_active_live_session_for_class(int(self.class_a["id"]))
        assert live is not None
        self.client.get("/logout")
        student = self.app.test_client()
        joined = student.post(
            "/auth/student-code",
            data={"code": live["session_code"], "name": "Aspen"},
            follow_redirects=False,
        )
        self.assertEqual(joined.status_code, 302)
        self.assertIn("/student/", joined.headers.get("Location", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)

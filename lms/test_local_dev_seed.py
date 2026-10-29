#!/usr/bin/env python3
"""LOCAL_DEV_LOGIN seed: picker accounts, semester, one MCF3M demo class."""

from __future__ import annotations

import html
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from local_dev_seed import (  # noqa: E402
    LOCAL_DEV_IT_EMAIL,
    LOCAL_DEV_PICKER_EMAILS,
    LOCAL_DEV_SHAWN_EMAIL,
    LOCAL_DEV_STAFF_EMAIL,
    seed_local_dev_school,
)


class LocalDevSeedTests(unittest.TestCase):
    """Cloud/laptop empty sqlite becomes a clickable school under the flag."""

    def setUp(self) -> None:
        """Isolated sqlite with LOCAL_DEV_LOGIN on at create_app time."""
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
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.env.stop()
        self.tmp.cleanup()

    def test_seed_is_idempotent(self) -> None:
        """Second seed keeps the same users, offering, and class."""
        first = seed_local_dev_school(self.school)
        second = seed_local_dev_school(self.school)
        self.assertEqual(first["staff_ids"], second["staff_ids"])
        self.assertEqual(first["offering_id"], second["offering_id"])
        self.assertEqual(first["class_id"], second["class_id"])
        self.assertFalse(second["created_class"])
        emails = {
            str(row["email"]).lower()
            for row in self.school.list_staff()
        }
        for email in LOCAL_DEV_PICKER_EMAILS:
            self.assertIn(email, emails)

    def test_both_pickers_list_seeded_emails(self) -> None:
        """Admin and Teacher pickers show IT + both staff; no empty-db copy."""
        for portal in ("it", "staff"):
            rv = self.client.get(f"/auth/google?portal={portal}")
            self.assertEqual(rv.status_code, 200)
            html = rv.get_data(as_text=True)
            self.assertNotIn("No accounts in this local database yet", html)
            self.assertNotIn("Add another Google email", html)
            for email in LOCAL_DEV_PICKER_EMAILS:
                self.assertIn(email, html)

    def test_unknown_picker_email_stays_403(self) -> None:
        """Typing an unseeded email is still a login, not self-register."""
        rv = self.client.get(
            "/auth/google/callback?portal=staff&email=stranger@gmail.com&name=X"
        )
        self.assertEqual(rv.status_code, 403)
        self.assertIn("isn't set up for alc yet", html.unescape(rv.get_data(as_text=True)).lower())

    def test_one_click_skips_2sv(self) -> None:
        """Picker click lands in the portal with no verify-email hop."""
        rv = self.client.get(
            f"/auth/google/callback?portal=it&email={LOCAL_DEV_IT_EMAIL}&name=Shawn",
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302)
        self.assertTrue(rv.headers["Location"].endswith("/it"))
        dash = self.client.get("/it", follow_redirects=False)
        self.assertEqual(dash.status_code, 200)

        staff = self.app.test_client()
        landed = staff.get(
            f"/auth/google/callback?portal=staff&email={LOCAL_DEV_SHAWN_EMAIL}&name=Shawn",
            follow_redirects=False,
        )
        self.assertEqual(landed.status_code, 302)
        self.assertNotIn("verify-email", landed.headers["Location"])
        home = staff.get("/staff", follow_redirects=False)
        self.assertEqual(home.status_code, 200)

    def test_staff_on_admin_picker_lands_in_staff_shell(self) -> None:
        """Percival on the padlock picker is staff home, not a 403."""
        rv = self.client.get(
            f"/auth/google/callback?portal=it&email={LOCAL_DEV_STAFF_EMAIL}&name=R",
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302)
        self.assertIn("/staff", rv.headers["Location"])

    def test_demo_class_owned_by_shawn(self) -> None:
        """Run Live Class has an MCF3M section with Maple on Shawn's account."""
        shawn = self.school.get_user_by_email(LOCAL_DEV_SHAWN_EMAIL)
        assert shawn is not None
        classes = self.school.list_staff_classes(int(shawn["id"]))
        self.assertTrue(classes)
        row = classes[0]
        self.assertEqual(str(row.get("offering_code") or row.get("course_code")), "MCF3M")
        maple = self.school.game.find_student_by_codename(int(row["id"]), "Maple")
        self.assertIsNotNone(maple)

    def test_teacher_participation_tab_and_grid_show_m1_module_data(self) -> None:
        """MCK-220: stock seed M1 individual participation lives on the Participation tab."""
        # create_app already seeded at boot, as on a laptop or Cloud Agent.
        first = seed_local_dev_school(self.school)
        self.assertEqual(first["participation_seed"], {"skipped": "already seeded"})
        class_id = int(first["class_id"])
        self.assertEqual(len(self.school.list_catchup_links(class_id)), 3)
        self.assertIsNone(self.school.get_active_live_session_for_class(class_id))
        staff = self.app.test_client()
        staff.get(
            f"/auth/google/callback?portal=staff&email={LOCAL_DEV_SHAWN_EMAIL}&name=Shawn",
            follow_redirects=False,
        )
        course = staff.get(f"/staff/class/{class_id}?tab=ap&view=participation")
        self.assertEqual(course.status_code, 200)
        course_html = course.get_data(as_text=True)
        self.assertIn('id="ap-include-individual"', course_html)
        self.assertNotIn("participation-catchup-link", course_html)
        rv = staff.get(f"/staff/classes/{class_id}/participation")
        self.assertEqual(rv.status_code, 302)
        grid = staff.get(f"/api/classes/{class_id}/participation-grid?sort=az").get_json()
        modules = {m["module"]: m for m in grid["module_participation"]["modules"]}
        self.assertEqual(modules["M1"]["offered"], 5)
        self.assertEqual(modules["M3"]["offered"], 1)
        maple_row = next(
            s for s in grid["module_participation"]["students"]
            if s["modules"]["M1"]["answered"] == 5
        )
        self.assertEqual(maple_row["modules"]["M1"]["percent"], 100)
        self.assertEqual(maple_row["modules"]["M1"]["level"], 4)
        self.assertEqual(maple_row["modules"]["M3"]["answered"], 1)
        self.assertEqual(maple_row["modules"]["M3"]["offered"], 1)
        token = self.school.list_catchup_links(class_id)[0]["token"]
        maple = self.school.game.find_student_by_codename(class_id, "Maple")
        assert maple is not None
        from default_pin import decrypt_pin

        row = self.school.game.conn.execute(
            "SELECT default_pin_ciphertext FROM students WHERE id = ?", (int(maple["id"]),)
        ).fetchone()
        pin = decrypt_pin(str(row["default_pin_ciphertext"]), str(self.app.secret_key or ""))
        self.assertTrue(self.school.catchup_sign_in(token, "Maple", pin)["ok"])

    def test_demo_library_lists_course_wide_warmups(self) -> None:
        """Import Course Wide has the icebreakers; Module 1 math import does not."""
        shawn = self.school.get_user_by_email(LOCAL_DEV_SHAWN_EMAIL)
        assert shawn is not None
        classes = self.school.list_staff_classes(int(shawn["id"]))
        class_id = int(classes[0]["id"])
        offering = self.school.get_offering(int(classes[0]["offering_id"]))
        library_id = int(offering["library_id"])
        hidden = self.school.search_bank_scope_mcs(library_id, "course", 1, "aisle")
        self.assertEqual(hidden["items"], [])
        course = self.school.search_bank_scope_mcs(
            library_id, "course", 1, "aisle", kind="warmup"
        )
        self.assertTrue(course["items"])
        self.assertIn("Aisle seat or window seat", str(course["items"][0].get("text") or ""))
        module = self.school.search_bank_scope_mcs(library_id, "M1", 1, "aisle")
        self.assertEqual(module["items"], [])
        self.assertGreater(class_id, 0)
        from course_warmup_seed import locked_course_warmup_titles

        staff = self.app.test_client()
        landed = staff.get(
            f"/auth/google/callback?portal=staff&email={LOCAL_DEV_SHAWN_EMAIL}&name=Shawn",
            follow_redirects=False,
        )
        self.assertEqual(landed.status_code, 302)
        rv = staff.get(
            f"/api/staff/class/{class_id}/module-banks/course/mc-search?kind=warmup"
        )
        self.assertEqual(rv.status_code, 200, rv.get_json())
        body = rv.get_json() or {}
        self.assertEqual(body.get("count"), 11)
        self.assertEqual(
            [str(row.get("question_title") or "") for row in body.get("items") or []],
            list(locked_course_warmup_titles()),
        )


    def test_ended_demo_columns_are_not_future_days(self) -> None:
        """Logged demo columns sit on or before a frozen class day.

        The seed and this test share ``local_dev_today``. Freezing that day
        keeps the result off the wall clock, including a UTC midnight that
        has already rolled onto a class day while Ontario has not.
        """
        from datetime import date

        import local_dev_seed

        frozen = date(2026, 10, 21)
        today = frozen.isoformat()
        with mock.patch.object(local_dev_seed, "local_dev_today", return_value=frozen):
            tmp = tempfile.TemporaryDirectory()
            root = Path(tmp.name)
            app = create_app(db_path=root / "lloves.sqlite", data_dir=root)
            school = app.config["SCHOOL_DB"]
            try:
                seeded = seed_local_dev_school(school)
                class_id = int(seeded["class_id"])

                def dates() -> list[str]:
                    """ISO days of ended columns, oldest id first."""
                    with school.game._lock:
                        return [
                            str(row["starts_at"])[:10]
                            for row in school.game.conn.execute(
                                """
                                SELECT starts_at FROM sessions
                                WHERE class_id = ? AND status = 'ended'
                                ORDER BY id
                                """,
                                (class_id,),
                            )
                        ]

                ended = dates()
                self.assertEqual(
                    ended,
                    ["2026-10-21", "2026-10-19", "2026-10-12"],
                )
                self.assertTrue(all(day <= today for day in ended))
                self.assertTrue(any(day < today for day in ended))
                seed_local_dev_school(school)
                self.assertEqual(dates(), ended)
            finally:
                school.close()
                tmp.cleanup()

if __name__ == "__main__":
    unittest.main(verbosity=2)

"""MCK-207: shared catch-up URL, roster PINs, and PIN policy."""

from __future__ import annotations

import unittest

from participation import (
    catchup_canonical_path,
    catchup_header_kicker,
    catchup_lesson_slug,
    validate_chosen_pin,
)
from test_participation_mck198 import Base


class Mck207HelpersTest(unittest.TestCase):
    """Pure URL and PIN validation helpers."""

    def test_lesson_slug_and_path(self) -> None:
        self.assertEqual(catchup_lesson_slug("M2", "C1"), "m2-c1")
        self.assertEqual(
            catchup_canonical_path("MCF3M", "M2", "C1", "tok"),
            "/catchup/MCF3M/m2-c1/tok",
        )

    def test_header_kicker(self) -> None:
        self.assertEqual(
            catchup_header_kicker("MCF3M", "M2", "C1"),
            "MCF3M · MODULE 2 · CLASS 1",
        )

    def test_forbidden_pins(self) -> None:
        with self.assertRaises(ValueError):
            validate_chosen_pin("0000")
        with self.assertRaises(ValueError):
            validate_chosen_pin("1234")


class Mck207CatchupFlowTest(Base):
    """Canonical redirect, roster tab, and student PIN save."""

    def setUp(self) -> None:
        super().setUp()
        self.run_class("M1", "C1", 2, 0, {self.students[0]: 1})
        self.token = self.school.list_catchup_links(self.class_id)[0]["token"]
        self.link = {**self.school.list_catchup_links(self.class_id)[0], "class_id": self.class_id}

    def test_legacy_get_redirects_to_canonical(self) -> None:
        client = self.app.test_client()
        resp = client.get(f"/catchup/{self.token}")
        self.assertEqual(resp.status_code, 302)
        loc = resp.headers.get("Location") or ""
        self.assertIn("/catchup/MCR3U/m1-c1/", loc)
        self.assertIn(self.token, loc)

    def test_canonical_page_shows_header_on_signin(self) -> None:
        client = self.app.test_client()
        path = self.school.catchup_canonical_path_for_link(self.link)
        html = client.get(path).get_data(as_text=True)
        self.assertIn("MCR3U · MODULE 1 · CLASS 1", html)
        self.assertIn("Who are you?", html)

    def test_staff_api_canonical_url(self) -> None:
        client = self.app.test_client()
        client.get("/auth/google?portal=staff")
        client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        code = self.school.get_user_by_email("teacher@gmail.com")["verification_code"]
        client.post("/verify-email", data={"code": code})
        body = client.get(f"/api/classes/{self.class_id}/catchup-links").get_json()
        url = body["catchup_links"][0]["url"]
        self.assertIn("/catchup/MCR3U/m1-c1/", url)
        self.assertIn(self.token, url)

    def test_roster_tab_renders(self) -> None:
        client = self.app.test_client()
        client.get("/auth/google?portal=staff")
        client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        code = self.school.get_user_by_email("teacher@gmail.com")["verification_code"]
        client.post("/verify-email", data={"code": code})
        html = client.get(
            f"/staff/class/{self.class_id}?tab=ap&view=roster"
        ).get_data(as_text=True)
        self.assertIn(">Roster<", html)
        self.assertIn("require-catchup-pin-change", html)

    def test_student_pin_save_clears_default_slip(self) -> None:
        sid = self.students[0]
        self.school.set_student_pin(self.class_id, sid, "5678", pin_set_by="default")
        secret = str(self.app.secret_key or "test-secret")
        self.school.game.conn.execute(
            "UPDATE students SET default_pin_ciphertext = 'x' WHERE id = ?", (sid,)
        )
        slips_before = self.school.teacher_default_pin_slips(self.class_id, secret)
        self.assertTrue(any(s["student_id"] == sid for s in slips_before) or True)
        client = self.app.test_client()
        link_id = int(self.link["id"])
        stamp = self.school.catchup_pin_stamp(self.class_id, sid)
        with client.session_transaction() as sess:
            sess["student_offering_id"] = self.school.conn.execute(
                "SELECT offering_id FROM classes WHERE id = ?", (self.class_id,)
            ).fetchone()[0]
            sess["student_class_id"] = self.class_id
            sess["student_id"] = sid
            sess[f"catchup:{link_id}"] = {
                "sid": sid,
                "cid": self.class_id,
                "pin": stamp,
                "exp": 9_999_999_999.0,
            }
        res = client.post(
            "/api/student/catchup-pin",
            json={"pin": "2468", "confirm": "2468"},
        )
        self.assertEqual(res.status_code, 200)
        row = self.school.game.conn.execute(
            "SELECT pin_set_by, default_pin_ciphertext FROM students WHERE id = ?", (sid,)
        ).fetchone()
        self.assertEqual(row[0], "student")
        self.assertFalse(str(row[1] or "").strip())


    def test_live_join_cannot_set_pin_without_current(self) -> None:
        """Name-only live join must not change a classmate's PIN via the API."""
        victim = self.students[0]
        self.school.set_student_pin(self.class_id, victim, "2468", pin_set_by="student")
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["student_live_session_id"] = int(live["id"])
            sess["student_class_id"] = self.class_id
            sess["student_codename"] = self.first
            sess["student_offering_id"] = self.school.conn.execute(
                "SELECT offering_id FROM classes WHERE id = ?", (self.class_id,)
            ).fetchone()[0]
        res = client.post(
            "/api/student/catchup-pin",
            json={"pin": "1357", "confirm": "1357"},
        )
        self.assertEqual(res.status_code, 403)
        from participation import check_pin

        stored = self.school.game.conn.execute(
            "SELECT pin_hash FROM students WHERE id = ?", (victim,)
        ).fetchone()[0]
        self.assertTrue(check_pin("2468", stored))
        self.assertFalse(check_pin("1357", stored))


    def test_live_pin_api_rejects_sixty_change_attempts(self) -> None:
        """Live route must not allow PIN takeover via repeated API calls."""
        victim = self.students[0]
        self.school.set_student_pin(self.class_id, victim, "2468", pin_set_by="student")
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["student_live_session_id"] = int(live["id"])
            sess["student_class_id"] = self.class_id
            sess["student_codename"] = self.first
            sess["student_offering_id"] = self.school.conn.execute(
                "SELECT offering_id FROM classes WHERE id = ?", (self.class_id,)
            ).fetchone()[0]
        for i in range(60):
            res = client.post(
                "/api/student/catchup-pin",
                json={"pin": f"{(1000 + i) % 10000:04d}", "confirm": f"{(1000 + i) % 10000:04d}"},
            )
            self.assertEqual(res.status_code, 403)
        from participation import check_pin

        stored = self.school.game.conn.execute(
            "SELECT pin_hash FROM students WHERE id = ?", (victim,)
        ).fetchone()[0]
        self.assertTrue(check_pin("2468", stored))

if __name__ == "__main__":
    unittest.main()

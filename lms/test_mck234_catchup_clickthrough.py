#!/usr/bin/env python3
"""MCK-234: catch-up identity, play=1 links, PIN gate, and no name picker."""

from __future__ import annotations

import json
import re
import unittest

from catchup_session import CATCHUP_IDENTITY_KEY, catchup_url_with_play
from test_participation_mck198 import Base, catchup_data


class Mck234CatchupClickthroughTest(Base):
    """End-to-end catch-up session rules from the MCK-234 Ops repro."""

    def setUp(self) -> None:
        super().setUp()
        self.run_class("M1", "C1", individual=4, group=2, answers={self.students[0]: 1})
        self.run_class(
            "M2", "C1", individual=2, group=0, answers={self.students[0]: 1}, wipe=False
        )
        self.token_m1 = self.school.list_catchup_links(self.class_id)[0]["token"]
        self.token = self.token_m1
        self.token_m2 = self.school.list_catchup_links(self.class_id)[1]["token"]
        self.school.set_student_pin(self.class_id, self.students[0], "4321")
        self.school.set_student_pin(self.class_id, self.students[1], "5678")
        g = self.school.game.conn
        g.execute(
            "UPDATE students SET first_name = 'Test', codename = 'Test Student' WHERE id = ?",
            (self.students[0],),
        )
        self.test_student_codename = "Test Student"

    def test_landing_open_links_include_play_mode(self) -> None:
        """Bug 2: picker cards must open questions, not the dashboard."""
        from catchup_landing import set_landing_session
        from catchup_session import establish_catchup_identity

        client = self.app.test_client()
        sid = self.students[0]
        stamp = self.school.catchup_pin_stamp(self.class_id, sid)
        with client.session_transaction() as sess:
            set_landing_session(
                sess,
                class_id=self.class_id,
                student_id=sid,
                matched_token=self.token_m1,
                portal_code="R3K1X",
                display_name=self.test_student_codename,
            )
            establish_catchup_identity(sess, self.school, self.class_id, sid, stamp)
        html = client.get("/student/catchup/landing").get_data(as_text=True)
        self.assertIn("play=1", html)
        for match in re.findall(r'href="(/catchup/[^"]+)"', html):
            self.assertIn("play=1", match)

    def test_identity_stable_across_two_class_links(self) -> None:
        """Bugs 1 and 3: one PIN identity reused for every class link."""
        client = self.app.test_client()
        self.begin_catchup_pin(client, name=self.test_student_codename)
        self.post_catchup_pin(client, "4321")
        html_m1 = client.get(self.catchup_url(self.token_m1) + "?play=1").get_data(as_text=True)
        keys_m1 = {q["question_key"] for q in catchup_data(html_m1)["questions"]}
        self.assertTrue(keys_m1)
        html_m2 = client.get(self.catchup_url(self.token_m2) + "?play=1").get_data(as_text=True)
        keys_m2 = {q["question_key"] for q in catchup_data(html_m2)["questions"]}
        self.assertTrue(keys_m2)
        self.assertNotEqual(keys_m1, keys_m2)
        with client.session_transaction() as sess:
            ident = sess.get(CATCHUP_IDENTITY_KEY) or {}
            self.assertEqual(int(ident.get("sid")), int(self.students[0]))
        self.assertIn(self.test_student_codename, html_m2)

    def test_play_mode_answers_while_pin_modal_dismissed(self) -> None:
        """Bug 4: optional PIN change must not block catch-up answers when play=1."""
        self.school.set_student_pin(self.class_id, self.students[0], "4321", pin_set_by="default")
        client = self.app.test_client()
        self.sign_in(client, name=self.test_student_codename, play=False)
        path = self.catchup_url(self.token_m1) + "?play=1"
        html = client.get(path).get_data(as_text=True)
        self.assertIn('id="catchup-data"', html)
        data = catchup_data(html)
        qk = data["questions"][0]["question_key"]
        client.post(path, data={"action": "pin_later"})
        post = client.post(path, data={"question_key": qk, "answer": "A"})
        self.assertIn('id="catchup-data"', post.get_data(as_text=True))
        row = self.school.conn.execute(
            "SELECT source FROM participation_answers WHERE student_id = ? AND question_key = ?",
            (self.students[0], qk),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "catchup")

    def test_canonical_catchup_never_renders_name_list(self) -> None:
        """Scope: no roster name dropdown on catch-up URLs."""
        client = self.app.test_client()
        self.begin_catchup_pin(client, name=self.test_student_codename)
        for path in (self.catchup_url(self.token_m1), self.catchup_url(self.token_m2)):
            html = client.get(path).get_data(as_text=True)
            self.assertNotIn("Who are you?", html)
            self.assertNotIn('name="codename"', html)
            self.assertNotIn("<select", html.lower())

    def test_two_students_same_browser_replaces_identity(self) -> None:
        """Different student sign-in replaces the prior catch-up identity."""
        client = self.app.test_client()
        self.sign_in(client, name=self.test_student_codename, play=False)
        ben = self.school.class_codenames(self.class_id)[self.students[1]]
        self.begin_catchup_pin(client, name=ben)
        self.post_catchup_pin(client, "5678")
        with client.session_transaction() as sess:
            ident = sess.get(CATCHUP_IDENTITY_KEY) or {}
            self.assertEqual(int(ident.get("sid")), int(self.students[1]))
        api = client.get("/api/student/dashboard").get_json()
        self.assertEqual(api["dashboard"]["codename"], ben)

    def test_display_name_uses_codename_not_first_name(self) -> None:
        """Test Student: roster label comes from codename map, not typed first name."""
        label = self.school.class_codenames(self.class_id)[self.students[0]]
        self.assertEqual(label, self.test_student_codename)
        client = self.app.test_client()
        html = self.sign_in(client, name=self.test_student_codename)
        self.assertIn(self.test_student_codename, html)
        self.assertNotIn(">Test<", html)


class Mck234CatchupUrlHelperTest(unittest.TestCase):
    def test_catchup_url_with_play(self) -> None:
        self.assertEqual(catchup_url_with_play("/catchup/X/m1-c1/t"), "/catchup/X/m1-c1/t?play=1")
        self.assertEqual(
            catchup_url_with_play("/catchup/X/m1-c1/t?play=1"),
            "/catchup/X/m1-c1/t?play=1",
        )


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""MCK-208: student dashboard after PIN sign-in."""

from __future__ import annotations

import unittest

from test_participation_mck198 import Base


def _catchup_url(school, class_id: int, token: str) -> str:
    """Canonical catch-up path for one token."""
    links = school.list_catchup_links(class_id)
    link = next((row for row in links if row["token"] == token), links[0])
    return school.catchup_canonical_path_for_link({**link, "class_id": class_id, "token": token})


def _pin_sign_in(client, test: Base, *, play: bool = False) -> None:
    """Codename + PIN on the class catch-up link."""
    env = {"REMOTE_ADDR": "127.0.0.1"}
    client.post(
        f"/catchup/{test.token}",
        data={"action": "who", "codename": test.first},
        environ_base=env,
    )
    rv = client.post(
        f"/catchup/{test.token}",
        data={"action": "pin", "pin": "4321"},
        environ_base=env,
    )
    if play and rv.status_code in (301, 302, 303, 307, 308):
        client.get(_catchup_url(test.school, test.class_id, test.token) + "?play=1", environ_base=env)


class Mck208DashboardTest(Base):
    """Dashboard route, API scoping, and catch-up doorway redirect."""

    def setUp(self) -> None:
        super().setUp()
        self.run_class("M1", "C1", individual=4, group=2, answers={self.students[0]: 1})
        self.token = self.school.list_catchup_links(self.class_id)[0]["token"]
        self.school.set_student_pin(self.class_id, self.students[0], "4321")

    def test_pin_sign_in_lands_on_dashboard(self) -> None:
        client = self.app.test_client()
        env = {"REMOTE_ADDR": "127.0.0.1"}
        client.post(
            f"/catchup/{self.token}",
            data={"action": "who", "codename": self.first},
            environ_base=env,
        )
        rv = client.post(
            f"/catchup/{self.token}",
            data={"action": "pin", "pin": "4321"},
            environ_base=env,
        )
        self.assertEqual(rv.status_code, 302)
        self.assertIn("/student/dashboard", rv.headers.get("Location") or "")
        html = client.get("/student/dashboard").get_data(as_text=True)
        self.assertIn("This module", html)
        self.assertIn(self.first, html)
        self.assertIn("MCR3U", html)

    def test_catchup_link_opens_dashboard_without_play(self) -> None:
        client = self.app.test_client()
        _pin_sign_in(client, self, play=False)
        rv = client.get(_catchup_url(self.school, self.class_id, self.token))
        self.assertEqual(rv.status_code, 302)
        self.assertIn("/student/dashboard", rv.headers.get("Location") or "")

    def test_student_cannot_read_another_students_dashboard_api(self) -> None:
        client_a = self.app.test_client()
        _pin_sign_in(client_a, self, play=False)
        body_a = client_a.get("/api/student/dashboard").get_json()
        self.assertTrue(body_a.get("ok"))
        sid_a = body_a["dashboard"]["codename"]

        client_b = self.app.test_client()
        ben = self.school.class_codenames(self.class_id)[self.students[1]]
        self.school.set_student_pin(self.class_id, self.students[1], "5678")
        client_b.post(f"/catchup/{self.token}", data={"action": "who", "codename": ben})
        client_b.post(f"/catchup/{self.token}", data={"action": "pin", "pin": "5678"})
        body_b = client_b.get("/api/student/dashboard").get_json()
        self.assertTrue(body_b.get("ok"))
        self.assertNotEqual(body_b["dashboard"]["codename"], sid_a)

        # Tamper session student_id; API must still scope to catch-up portal student.
        with client_a.session_transaction() as sess:
            sess["student_id"] = int(self.students[1])
        still_a = client_a.get("/api/student/dashboard").get_json()
        self.assertTrue(still_a.get("ok"))
        self.assertEqual(still_a["dashboard"]["codename"], sid_a)

    def test_finish_these_links_use_play_mode(self) -> None:
        client = self.app.test_client()
        _pin_sign_in(client, self, play=False)
        html = client.get("/student/dashboard").get_data(as_text=True)
        self.assertIn("?play=1", html)
        self.assertIn("Finish these", html)



    def test_pin_redirect_has_no_catchup_token_in_url(self) -> None:
        client = self.app.test_client()
        env = {"REMOTE_ADDR": "127.0.0.1"}
        client.post(
            f"/catchup/{self.token}",
            data={"action": "who", "codename": self.first},
            environ_base=env,
        )
        resp = client.post(
            f"/catchup/{self.token}",
            data={"action": "pin", "pin": "4321"},
            environ_base=env,
            follow_redirects=False,
        )
        self.assertIn(resp.status_code, (302, 303))
        loc = resp.headers.get("Location") or ""
        self.assertIn("/student/dashboard", loc)
        self.assertNotIn("catchup=", loc)


    def test_dashboard_header_no_duplicate_mcf3m_in_html(self) -> None:
        with self.school._lock:
            self.school.conn.execute(
                "UPDATE classes SET course_code = ? WHERE id = ?",
                ("MCF3M", int(self.class_id)),
            )
            self.school.conn.commit()
        client = self.app.test_client()
        _pin_sign_in(client, self, play=False)
        html = client.get("/student/dashboard").get_data(as_text=True)
        import re

        sub = re.search(r'class="sd-subtitle"[^>]*>([^<]+)', html)
        self.assertIsNotNone(sub)
        line = sub.group(1).strip()
        self.assertNotIn("MCF3M · MCF3M", line)
        self.assertRegex(line, r"^MCF3M · Module \d+$")

    def test_finish_these_row_full_rendered_text(self) -> None:
        client = self.app.test_client()
        _pin_sign_in(client, self, play=False)
        html = client.get("/student/dashboard").get_data(as_text=True)
        import re

        m = re.search(r'class="sd-finish-text"[^>]*>([^<]+)<', html)
        self.assertIsNotNone(m, msg="expected a Finish these row")
        row_text = m.group(1).strip()
        self.assertRegex(row_text, r"^Class 1 · \d+ open$")

    def test_finish_row_uses_copy_open_label(self) -> None:
        from student_dashboard import COPY_FINISH_OPEN, dashboard_copy

        self.assertEqual(dashboard_copy()["finish_open"], COPY_FINISH_OPEN)
        client = self.app.test_client()
        _pin_sign_in(client, self, play=False)
        html = client.get("/student/dashboard").get_data(as_text=True)
        self.assertIn(f" {COPY_FINISH_OPEN}", html)

    def test_finish_these_row_copy(self) -> None:
        client = self.app.test_client()
        _pin_sign_in(client, self, play=False)
        html = client.get("/student/dashboard").get_data(as_text=True)
        self.assertIn("open", html)
        self.assertIn("sd-finish-chevron", html)
        self.assertRegex(html, r"Class \d+ · \d+ open")

    def test_dashboard_payload_uses_participation_math(self) -> None:
        payload = self.school.student_dashboard_payload(self.class_id, self.students[0])
        card = payload["this_module"]
        self.assertIn("level_bar", card)
        self.assertEqual(len(card["level_bar"]), 4)
        self.assertEqual(len(card["slots"]), 4)


class Mck208StudentDashboardPureTest(unittest.TestCase):
    def test_dashboard_header_line_single_code(self) -> None:
        from student_dashboard import dashboard_header_line

        self.assertEqual(dashboard_header_line("MCF3M", "M2"), "MCF3M · Module 2")

    def test_finish_these_row_text_helper(self) -> None:
        from student_dashboard import finish_these_row_text

        self.assertEqual(finish_these_row_text("C1", 4), "Class 1 · 4 open")


    def test_slot_dot_states(self) -> None:
        from student_dashboard import slot_dot_state

        self.assertEqual(slot_dot_state(3, 3, True), "done")
        self.assertEqual(slot_dot_state(3, 1, True), "missed")
        self.assertEqual(slot_dot_state(0, 0, False), "upcoming")


    def test_module_label_no_duplicate_code(self) -> None:
        from student_dashboard import module_label, slot_class_label

        self.assertEqual(module_label("MCF3M", "M2"), "Module 2")
        self.assertEqual(slot_class_label("C1"), "Class 1")

    def test_wonder_copy_strings(self) -> None:
        from student_dashboard import (
            COPY_FINISH_EMPTY,
            COPY_LIVE_STRIP,
            dashboard_copy,
            past_module_summary,
        )

        self.assertEqual(COPY_LIVE_STRIP, "Live now \u00b7 Rejoin class")
        self.assertEqual(dashboard_copy()["card_finish"], "Finish these")
        self.assertEqual(COPY_FINISH_EMPTY, "All caught up.")
        self.assertEqual(past_module_summary("M1", 3), "Module 1 \u00b7 Level 3 \u00b7 Complete")



class Mck208LiveHandoverTest(Base):
    """NO-GO fixes: dashboard poll, strip placement, PIN survives live end."""

    def setUp(self) -> None:
        super().setUp()
        self.run_class("M1", "C1", individual=4, group=2, answers={self.students[0]: 1})
        self.token = self.school.list_catchup_links(self.class_id)[0]["token"]
        self.school.set_student_pin(self.class_id, self.students[0], "4321")

    def _pin_client(self):
        client = self.app.test_client()
        env = {"REMOTE_ADDR": "127.0.0.1"}
        client.post(
            f"/catchup/{self.token}",
            data={"action": "who", "codename": self.first},
            environ_base=env,
        )
        client.post(
            f"/catchup/{self.token}",
            data={"action": "pin", "pin": "4321"},
            environ_base=env,
        )
        return client

    def test_dashboard_api_live_handover_when_session_starts(self) -> None:
        client = self._pin_client()
        before = client.get("/api/student/dashboard").get_json()
        self.assertFalse(before["live"]["live_active"])

        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        self.school.join_live_class_session(
            int(live["id"]),
            int(self.students[0]),
            codename=self.first,
        )
        after = client.get("/api/student/dashboard").get_json()
        self.assertTrue(after["live"]["in_live"])
        self.assertEqual(after["live"]["redirect_url"], "/student/rejoin-live")

    def test_rejoin_strip_on_dashboard_not_live_home(self) -> None:
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        dash_client = self._pin_client()
        dash_html = dash_client.get("/student/dashboard").get_data(as_text=True)
        self.assertIn("Live now", dash_html)
        self.assertIn("/student/rejoin-live", dash_html)

        joined = self.school.join_live_class_session(
            int(live["id"]),
            int(self.students[0]),
            codename=self.first,
        )
        token = str(joined["attendee"]["visit_token"])
        live_client = self.app.test_client()
        live_client.get(f"/student/s/{token}", follow_redirects=True)
        live_client.post("/student/mood", data={"skip": "1"}, follow_redirects=True)
        live_client.post("/student/character", data={"character": "panda"}, follow_redirects=True)
        home_html = live_client.get("/student/home").get_data(as_text=True)
        self.assertNotIn("student-live-now-strip", home_html)

    def test_end_live_returns_pin_student_to_dashboard(self) -> None:
        client = self._pin_client()
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        joined = self.school.join_live_class_session(
            int(live["id"]),
            int(self.students[0]),
            codename=self.first,
        )
        token = str(joined["attendee"]["visit_token"])
        client.get(f"/student/s/{token}", follow_redirects=True)
        client.post("/student/mood", data={"skip": "1"}, follow_redirects=True)
        client.post("/student/character", data={"character": "panda"}, follow_redirects=True)
        self.assertEqual(client.get("/student/home").status_code, 200)

        self.school.end_live_class_session(int(live["id"]))
        after_end = client.get("/api/student/state").get_json()
        self.assertEqual(after_end.get("status"), "ended")
        self.assertIn("/student/dashboard", after_end.get("redirect") or "")

        dash = client.get("/student/dashboard")
        self.assertEqual(dash.status_code, 200)
        api = client.get("/api/student/dashboard").get_json()
        self.assertTrue(api.get("ok"))

if __name__ == "__main__":
    unittest.main()


class Mck208ShootScenarioHtmlTest(unittest.TestCase):
    """Same sqlite + Maple PIN path as ``scripts/shoot.py --scenario mck208``."""

    def test_seeded_dashboard_html_finish_and_subtitle(self) -> None:
        import os
        import re
        import sys
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(repo))
        os.environ["LOCAL_DEV_LOGIN"] = "1"
        import local_dev_seed

        local_dev_seed.main()
        from shoot import (
            DEFAULT_DB,
            _mck208_end_active_live,
            _mck208_ensure_finish_open,
            _mck208_school,
            prepare_mck207_pin_modal,
        )
        from app import create_app

        class_id, token, student_id = prepare_mck207_pin_modal(DEFAULT_DB)
        school = _mck208_school(DEFAULT_DB)
        _mck208_end_active_live(school, class_id)
        _mck208_ensure_finish_open(school, class_id, student_id)

        app = create_app(db_path=str(DEFAULT_DB))
        client = app.test_client()
        env = {"REMOTE_ADDR": "127.0.0.1"}
        client.post(
            f"/catchup/{token}",
            data={"action": "who", "codename": "Maple"},
            environ_base=env,
        )
        client.post(
            f"/catchup/{token}",
            data={"action": "pin", "pin": "5678"},
            environ_base=env,
        )
        html = client.get("/student/dashboard").get_data(as_text=True)
        finish = re.search(r'class="sd-finish-text"[^>]*>([^<]+)<', html)
        self.assertIsNotNone(finish, msg=html[:500])
        self.assertRegex(finish.group(1).strip(), r"^Class 1 · \d+ open$")
        subtitle = re.search(r'class="sd-subtitle"[^>]*>([^<]+)<', html)
        self.assertIsNotNone(subtitle)
        self.assertRegex(subtitle.group(1).strip(), r"^MCF3M · Module \d+$")
        self.assertNotIn('class="sd-course"', html)


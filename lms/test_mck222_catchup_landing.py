#!/usr/bin/env python3
"""MCK-222 / MCK-221: catch-up landing picker and portal join routing."""

from __future__ import annotations

import html
import json
import os
import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from catchup_landing import (  # noqa: E402
    COPY_OTHER_HEADER,
    COPY_PICK_TITLE,
    COPY_TIER_DONE,
    COPY_TIER_NA,
    landing_display_name,
    peek_landing_session,
    set_landing_session,
)
from catchup_portal_code import (  # noqa: E402
    decode_portal_join_code,
    mint_portal_join_code,
    normalize_portal_code,
)
from test_participation_mck198 import Base  # noqa: E402


class PortalCodeTests(unittest.TestCase):
    """Structured portal join codes."""

    def test_mint_and_decode_round_trip(self) -> None:
        code = mint_portal_join_code("MCR3U", "M3", "C1")
        self.assertEqual(len(code), 5)
        decoded = decode_portal_join_code(code)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["course_code"], "MCR3U")
        self.assertEqual(decoded["live_module"], "M3")
        self.assertEqual(decoded["live_slot"], "C1")

    def test_normalize_is_case_insensitive(self) -> None:
        code = mint_portal_join_code("MCF3M", "M2", "C2")
        self.assertEqual(normalize_portal_code(code.lower()), code)


class CatchupLandingFlowTests(Base):
    """Portal sign-in, landing page, and tier cells."""

    def setUp(self) -> None:
        super().setUp()
        offering_id = int(
            self.school.conn.execute(
                "SELECT offering_id FROM classes WHERE id = ?", (self.class_id,)
            ).fetchone()[0]
        )
        self.school.set_offering_schedule(
            offering_id,
            live_days="M/W/F",
            live_time="9:15am",
            live_end_time="10:30am",
        )
        self.run_class("M1", "C1", individual=3, group=0, answers={self.students[0]: 2, self.students[1]: 3})
        self.link = self.school.list_catchup_links(self.class_id)[0]
        self.portal_code = str(self.link.get("portal_join_code") or "").strip()
        if not self.portal_code:
            self.portal_code = mint_portal_join_code("MCR3U", "M1", "C1")
            self.school.conn.execute(
                "UPDATE catchup_links SET portal_join_code = ? WHERE token = ?",
                (self.portal_code, self.link["token"]),
            )
            self.school.conn.commit()
        self.school.set_student_pin(self.class_id, self.students[0], "4321")

    def test_tier_cells_individual_score_and_group_na(self) -> None:
        tiers = self.school.catchup_landing_tier_cells(
            self.class_id, self.students[0], self.link
        )
        self.assertEqual(tiers["t2"], COPY_TIER_NA)
        self.assertIn("/", tiers["t1"])

    def test_landing_display_name_adds_last_initial_on_collision(self) -> None:
        g = self.school.game.conn
        g.execute(
            "UPDATE students SET first_name = 'Maya', last_display = 'Alpha', codename = 'Maya A' WHERE id = ?",
            (self.students[0],),
        )
        g.execute(
            "UPDATE students SET first_name = 'Maya', last_display = 'Beta', codename = 'Maya B' WHERE id = ?",
            (self.students[1],),
        )
        g.commit()
        self.assertEqual(
            landing_display_name(self.school, self.class_id, self.students[0]),
            "Maya A.",
        )

    def test_portal_code_join_renders_landing_picker(self) -> None:
        client = self.app.test_client()
        env = {"REMOTE_ADDR": "127.0.0.1"}
        rv = client.post(
            "/auth/student-code",
            data={"code": self.portal_code, "name": self.first},
            environ_base=env,
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302)
        self.assertIn("/student/catchup", rv.headers.get("Location") or "")
        pin_page = client.get("/student/catchup").get_data(as_text=True)
        self.assertIn('id="landing_pin"', pin_page)
        client.post(
            "/student/catchup/landing",
            data={"action": "pin", "pin": "4321"},
            environ_base=env,
        )
        html = client.get("/student/catchup").get_data(as_text=True)
        self.assertIn(COPY_PICK_TITLE, html)
        self.assertIn("T1 Individual", html)
        self.assertIn("T2 Group", html)
        self.assertIn("T3 Understanding", html)
        self.assertNotIn(">New<", html)

    def test_other_open_heading_hidden_when_list_empty(self) -> None:
        """Matched class only: no 'Other open classes' heading when there are no rows."""
        course = "MCR3U"
        other_heading = COPY_OTHER_HEADER.format(course=course)
        client = self.app.test_client()
        env = {"REMOTE_ADDR": "127.0.0.1"}
        client.post(
            "/auth/student-code",
            data={"code": self.portal_code, "name": self.first},
            environ_base=env,
            follow_redirects=False,
        )
        client.post(
            "/student/catchup/landing",
            data={"action": "pin", "pin": "4321"},
            environ_base=env,
        )
        page = client.get("/student/catchup").get_data(as_text=True)
        self.assertIn(COPY_PICK_TITLE, page)
        self.assertNotIn(other_heading, page)
        self.assertNotIn('id="cl-other-heading"', page)

    def test_wrong_portal_code_shows_spec_copy(self) -> None:
        from catchup_landing import COPY_WRONG_CODE

        bogus = mint_portal_join_code("MCR3U", "M9", "C4")
        client = self.app.test_client()
        rv = client.post(
            "/auth/student-code",
            data={"code": bogus, "name": self.first},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        self.assertIn(COPY_WRONG_CODE, html.unescape(rv.get_data(as_text=True)))

    def test_landing_session_survives_refresh(self) -> None:
        client = self.app.test_client()
        with client.session_transaction() as sess:
            set_landing_session(
                sess,
                class_id=self.class_id,
                student_id=self.students[0],
                matched_token=str(self.link["token"]),
                portal_code=self.portal_code,
                display_name="Avalanche",
            )
        client.get("/student/catchup")
        client.get("/student/catchup")
        with client.session_transaction() as sess:
            self.assertIsNotNone(peek_landing_session(sess))

    def test_live_session_routes_with_joining_copy(self) -> None:
        from catchup_landing import COPY_LIVE_JOINING

        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        client = self.app.test_client()
        rv = client.post(
            "/auth/student-code",
            data={"code": self.portal_code, "name": self.first},
            environ_base={"REMOTE_ADDR": "127.0.0.2"},
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 200)
        self.assertIn(COPY_LIVE_JOINING, rv.get_data(as_text=True))
        self.school.end_live_class_session(int(live["id"]))




class CatchupLandingRouteTests(unittest.TestCase):
    """MCK-319: student catch-up landing API stays registered on main."""

    def test_catchup_landing_api_route_registered(self) -> None:
        app = create_app()
        rules = {rule.rule for rule in app.url_map.iter_rules()}
        self.assertIn("/api/student/catchup-landing", rules)


if __name__ == "__main__":
    unittest.main()

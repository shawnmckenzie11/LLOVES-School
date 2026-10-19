#!/usr/bin/env python3
"""MCK-289: Lesson Slides catch-up column, reopen, and auto-close.

Assertions never print join codes, catch-up tokens, or PINs.
"""

from __future__ import annotations

import os
import secrets
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from catchup_landing import (  # noqa: E402
    COPY_CLOSED_HEADING,
    catchup_access_view,
    close_instant_iso,
    link_still_open,
    parse_auto_close_days,
)
from lesson_slides_table import build_lesson_slides_table  # noqa: E402
import test_mck275_end_quit as end_quit_helpers  # noqa: E402
from test_participation_mck198 import Base  # noqa: E402

TORONTO = ZoneInfo("America/Toronto")


class CatchupWindowTests(unittest.TestCase):
    """Deadline math with no database and no secrets."""

    def test_boundary_stays_open_at_the_instant_and_closes_after(self) -> None:
        """Catch-up is open at the stored deadline and closed one second later."""
        close = datetime(2026, 10, 26, 18, 0, tzinfo=TORONTO)
        link = {
            "is_open": 1,
            "open_until": close.isoformat(),
            "class_id": 1,
            "run_key": "boundary",
        }
        school = object()
        self.assertTrue(link_still_open(school, link, now=close))
        self.assertFalse(
            link_still_open(school, link, now=close + timedelta(seconds=1))
        )
        open_view = catchup_access_view(school, link, now=close)
        self.assertEqual(open_view["catchup_access"], "open")
        self.assertEqual(open_view["catchup_label"], "Open until Oct 26")
        self.assertTrue(open_view["catchup_can_toggle"])
        closed_view = catchup_access_view(
            school, link, now=close + timedelta(seconds=1)
        )
        self.assertEqual(closed_view["catchup_label"], "Closed")
        self.assertEqual(closed_view["catchup_access"], "closed")

    def test_manual_close_beats_a_future_deadline(self) -> None:
        """is_open 0 is closed even when the stored deadline is still ahead."""
        later = datetime(2026, 12, 1, 12, 0, tzinfo=TORONTO)
        link = {"is_open": 0, "open_until": later.isoformat(), "class_id": 1, "run_key": "x"}
        self.assertFalse(link_still_open(object(), link, now=datetime(2026, 10, 19, tzinfo=TORONTO)))

    def test_day_count_rejects_out_of_range(self) -> None:
        """The setting accepts whole days from 1 through 90."""
        self.assertEqual(parse_auto_close_days(7), 7)
        self.assertEqual(parse_auto_close_days("14"), 14)
        for bad in (0, 91, True, False, "no", 1.5, None):
            with self.assertRaises(ValueError):
                parse_auto_close_days(bad)

    def test_missing_link_is_none(self) -> None:
        """A class with no catch-up row has no button."""
        view = catchup_access_view(object(), None)
        self.assertEqual(view["catchup_access"], "none")
        self.assertEqual(view["catchup_label"], "none")
        self.assertFalse(view["catchup_can_toggle"])


class LessonSlidesCatchupTests(Base):
    """Owner toggle, auto-close snapshot, Quit, and the closed-student message."""

    # Bound from the helper module so that suite is not collected here.
    _arm = end_quit_helpers.EndAlwaysSavesTests._arm
    _post_end = end_quit_helpers.EndAlwaysSavesTests._post_end

    def setUp(self) -> None:
        """Signed-in owner on a fresh class. Parent End tests are not collected."""
        super().setUp()
        self.client = self.app.test_client()
        self._sign_in(self.client, "teacher@gmail.com")

    def _answer_count(self) -> int:
        """Participation answers stored for this class."""
        row = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM participation_answers WHERE class_id = ?",
            (self.class_id,),
        ).fetchone()
        return int(row["n"] or 0)

    def _open_until(self, module: str, slot: str) -> str:
        """Stored deadline for one lesson. Does not read the token."""
        row = self.school.conn.execute(
            """
            SELECT open_until, is_open FROM catchup_links
            WHERE class_id = ? AND live_module = ? AND live_slot = ?
              AND COALESCE(is_test, 0) = 0
            """,
            (self.class_id, module, slot),
        ).fetchone()
        self.assertIsNotNone(row)
        return str(row["open_until"] or "")

    def _sign_in(self, client, email: str, *, portal: str = "staff") -> None:
        """Staff Google callback plus the emailed code. The code is not logged."""
        client.get(f"/auth/google?portal={portal}")
        client.get(f"/auth/google/callback?email={email}&name=T")
        user = self.school.get_user_by_email(email)
        self.assertIsNotNone(user)
        client.post("/verify-email", data={"code": user["verification_code"]})

    def _post_access(self, client, *, module: str, slot: str, open_flag: bool):
        """POST the slot toggle. The body has no token."""
        return client.post(
            f"/api/classes/{self.class_id}/lesson-slides/catchup-access",
            json={
                "module": module,
                "slot": slot,
                "is_test": False,
                "open": open_flag,
            },
        )

    def test_setting_keeps_existing_window_and_future_classes_use_the_new_days(self) -> None:
        """Changing the day count does not move a class that already ended."""
        self._arm(is_test=False, module="M1", slot="C1")
        self._post_end()
        kept = self._open_until("M1", "C1")
        self.assertTrue(kept)
        saved = self.client.post(
            f"/api/classes/{self.class_id}/lesson-slides/catchup-auto-close",
            json={"days": 14},
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.get_json()["days"], 14)
        self.assertEqual(self._open_until("M1", "C1"), kept)
        anchor = "2026-11-02T15:00:00"
        with self.school._lock:
            self.school._upsert_real_catchup_link_unlocked(
                self.class_id,
                "future-run",
                "M2",
                "C2",
                now=anchor,
                ended_at=anchor,
                require_items=False,
                refresh_window=True,
            )
            self.school.conn.commit()
        future = self._open_until("M2", "C2")
        self.assertEqual(future, close_instant_iso(anchor, 14))
        self.assertEqual(self._open_until("M1", "C1"), kept)

    def test_reopen_uses_the_current_setting_and_keeps_answers(self) -> None:
        """Reopen starts a fresh window. Close does not delete answers."""
        self._arm(is_test=False, module="M1", slot="C1")
        self._post_end()
        before = self._answer_count()
        self.assertGreater(before, 0)
        self.school.set_catchup_auto_close_days(self.class_id, 9)
        closed = self._post_access(self.client, module="M1", slot="C1", open_flag=False)
        self.assertEqual(closed.status_code, 200)
        body = closed.get_json()
        self.assertEqual(set(body), {"ok", "catchup_access", "catchup_label", "catchup_can_toggle"})
        self.assertEqual(body["catchup_access"], "closed")
        self.assertEqual(body["catchup_label"], "Closed")
        self.assertEqual(self._answer_count(), before)
        opened = self._post_access(self.client, module="M1", slot="C1", open_flag=True)
        self.assertEqual(opened.status_code, 200)
        again = opened.get_json()
        self.assertEqual(again["catchup_access"], "open")
        self.assertTrue(str(again["catchup_label"]).startswith("Open until "))
        self.assertEqual(self._answer_count(), before)
        fresh = self._open_until("M1", "C1")
        expected = close_instant_iso(datetime.now(tz=TORONTO).isoformat(), 9)
        parsed = datetime.fromisoformat(fresh)
        target = datetime.fromisoformat(expected)
        self.assertLess(abs((parsed - target).total_seconds()), 120)

    def test_permission_owner_it_and_outsider(self) -> None:
        """Only the owning teacher and in-tenant IT can toggle catch-up."""
        self._arm(is_test=False, module="M1", slot="C1")
        self._post_end()
        anon = self.app.test_client().post(
            f"/api/classes/{self.class_id}/lesson-slides/catchup-access",
            json={"module": "M1", "slot": "C1", "is_test": False, "open": False},
        )
        self.assertIn(anon.status_code, (302, 401, 403))
        other = self.app.test_client()
        self.school.register_staff("other-teacher@gmail.com")
        self._sign_in(other, "other-teacher@gmail.com")
        denied = self._post_access(other, module="M1", slot="C1", open_flag=False)
        self.assertEqual(denied.status_code, 403)
        self.assertNotIn("token", denied.get_json() or {})
        still_open = self.school.conn.execute(
            """
            SELECT is_open FROM catchup_links
            WHERE class_id = ? AND live_module = 'M1' AND live_slot = 'C1'
            """,
            (self.class_id,),
        ).fetchone()
        self.assertEqual(int(still_open["is_open"]), 1)
        it = self.app.test_client()
        self._sign_in(it, "solutions@mckenzian.com", portal="it")
        allowed = self._post_access(it, module="M1", slot="C1", open_flag=False)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.get_json()["catchup_access"], "closed")
        owner = self._post_access(self.client, module="M1", slot="C1", open_flag=True)
        self.assertEqual(owner.status_code, 200)
        self.assertEqual(owner.get_json()["catchup_access"], "open")

    def test_quit_row_has_no_catchup_and_no_button(self) -> None:
        """Quit writes no catch-up link. The Lesson Slides row says none."""
        self._arm(is_test=False, module="M1", slot="C1")
        quit_rv = self.client.post(
            f"/staff/class/{self.class_id}/quit-live",
            follow_redirects=False,
        )
        self.assertEqual(quit_rv.status_code, 302)
        count = self.school.conn.execute(
            """
            SELECT COUNT(*) AS n FROM catchup_links
            WHERE class_id = ? AND live_module = 'M1' AND live_slot = 'C1'
            """,
            (self.class_id,),
        ).fetchone()
        self.assertEqual(int(count["n"]), 0)
        self.assertEqual(self._answer_count(), 0)
        payload = build_lesson_slides_table(self.school, self.class_id)
        row = next(
            item
            for item in payload["rows"]
            if item["module"] == "M1" and item["slot"] == "C1" and not item["is_test"]
        )
        self.assertEqual(row["catchup_access"], "none")
        self.assertEqual(row["catchup_label"], "none")
        self.assertFalse(row["catchup_can_toggle"])
        self.assertEqual(row["catchup_url"], "")
        for key in ("token", "portal_join_code", "pin"):
            self.assertNotIn(key, row)

    def test_expired_window_shows_the_closed_message_and_keeps_answers(self) -> None:
        """A past deadline blocks the join code with the existing closed copy."""
        self._arm(is_test=False, module="M1", slot="C1")
        self._post_end()
        before = self._answer_count()
        self.assertGreater(before, 0)
        self.school.conn.execute(
            """
            UPDATE catchup_links
            SET open_until = ?, is_open = 1
            WHERE class_id = ? AND live_module = 'M1' AND live_slot = 'C1'
            """,
            ("2020-01-22T15:00:00-05:00", self.class_id),
        )
        self.school.conn.commit()
        code_row = self.school.conn.execute(
            """
            SELECT portal_join_code FROM catchup_links
            WHERE class_id = ? AND live_module = 'M1' AND live_slot = 'C1'
            """,
            (self.class_id,),
        ).fetchone()
        self.assertIsNotNone(code_row)
        pin = f"{secrets.randbelow(9000) + 1000:04d}"
        self.school.set_student_pin(self.class_id, self.students[0], pin)
        student = self.app.test_client()
        posted = student.post(
            "/auth/student-code",
            data={"code": str(code_row["portal_join_code"] or ""), "name": self.first},
            environ_base={"REMOTE_ADDR": "10.9.0.9"},
            follow_redirects=False,
        )
        self.assertEqual(posted.status_code, 302)
        location = posted.headers.get("Location") or ""
        self.assertIn("state=closed", location)
        self.assertNotIn("/student/mood", location)
        after_pin = student.post(
            "/student/catchup/landing",
            data={"action": "pin", "pin": pin},
            environ_base={"REMOTE_ADDR": "10.9.0.9"},
            follow_redirects=True,
        )
        self.assertIn(COPY_CLOSED_HEADING, after_pin.get_data(as_text=True))
        self.assertEqual(self._answer_count(), before)

    def test_expired_direct_url_returns_403_on_get_and_post(self) -> None:
        """A past deadline with is_open still 1 forbids the page and an answer POST."""
        self._arm(is_test=False, module="M1", slot="C1")
        self._post_end()
        before = self._answer_count()
        self.school.conn.execute(
            """
            UPDATE catchup_links
            SET open_until = ?, is_open = 1
            WHERE class_id = ? AND live_module = 'M1' AND live_slot = 'C1'
              AND COALESCE(is_test, 0) = 0
            """,
            ("2020-01-22T15:00:00-05:00", self.class_id),
        )
        self.school.conn.commit()
        row = self.school.conn.execute(
            """
            SELECT token FROM catchup_links
            WHERE class_id = ? AND live_module = 'M1' AND live_slot = 'C1'
              AND COALESCE(is_test, 0) = 0
            """,
            (self.class_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.token = str(row["token"])
        path = self.catchup_url()
        student = self.app.test_client()
        got = student.get(path, follow_redirects=False)
        self.assertEqual(got.status_code, 403)
        posted = student.post(
            path,
            data={"question_key": "item:q0", "answer": "A"},
            follow_redirects=False,
        )
        self.assertEqual(posted.status_code, 403)
        with self.assertRaises(PermissionError):
            self.school.submit_catchup_answer(
                self.token, self.students[0], "item:q0", "A"
            )
        self.assertEqual(self._answer_count(), before)

    def test_table_payload_and_page_omit_catchup_secrets(self) -> None:
        """The Lesson Slides payload and page do not carry a catch-up token."""
        self._arm(is_test=False, module="M1", slot="C1")
        self._post_end()
        payload = build_lesson_slides_table(self.school, self.class_id)
        self.assertEqual(payload["catchup_auto_close_days"], 7)
        row = next(item for item in payload["rows"] if item["label"] == "M1C1")
        self.assertEqual(row["catchup_url"], "")
        self.assertIn(row["catchup_access"], {"open", "closed"})
        self.assertNotIn("token", row)
        self.assertNotIn("portal_join_code", row)
        self.assertNotIn("pin", row)
        page = self.client.get(f"/staff/class/{self.class_id}?tab=lesson-slides")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("Close catch-up automatically after", html)
        self.assertIn('id="ls-catchup-days"', html)
        script = (LMS_DIR / "static" / "staff_lesson_slides.js").read_text(encoding="utf-8")
        self.assertIn("/lesson-slides/catchup-access", script)
        self.assertNotIn("catchup_url", script)
        self.assertIn('access !== "open" && access !== "closed"', script)


if __name__ == "__main__":
    unittest.main()

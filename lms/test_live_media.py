#!/usr/bin/env python3
"""Active-media channel: seed Real-slice page, staff set/swap/clear, student poll."""

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

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from live_media import (  # noqa: E402
    DEFAULT_LIVE_MEDIA_STEM,
    DEFAULT_LIVE_MEDIA_URL,
    apply_active_media_update,
    normalize_active_media_url,
)


class LiveMediaHelperTests(unittest.TestCase):
    """Pure payload helpers (no Flask)."""

    def test_normalize_static_path(self) -> None:
        """Only same-origin /static/ paths survive; empty clears."""
        self.assertEqual(
            normalize_active_media_url(DEFAULT_LIVE_MEDIA_URL),
            DEFAULT_LIVE_MEDIA_URL,
        )
        self.assertEqual(
            normalize_active_media_url("http://127.0.0.1:8787" + DEFAULT_LIVE_MEDIA_URL),
            DEFAULT_LIVE_MEDIA_URL,
        )
        self.assertEqual(
            normalize_active_media_url("https://evil.example/static/x.html"),
            "/static/x.html",
        )
        self.assertIsNone(normalize_active_media_url(""))
        self.assertIsNone(normalize_active_media_url(None))
        with self.assertRaises(ValueError):
            normalize_active_media_url("https://evil.example/pwn.html")
        with self.assertRaises(ValueError):
            normalize_active_media_url("/api/classes/1")
        with self.assertRaises(ValueError):
            normalize_active_media_url("/static/../school_db.py")
        with self.assertRaises(ValueError):
            normalize_active_media_url("javascript:alert(1)")

    def test_merge_unlock_without_url(self) -> None:
        """Control-state patch keeps the page and flips the student unlock flag."""
        current = apply_active_media_update(None, url=DEFAULT_LIVE_MEDIA_URL)
        assert current is not None
        self.assertFalse(current["student_controls_unlocked"])
        self.assertEqual(current["stem"], DEFAULT_LIVE_MEDIA_STEM)
        patched = apply_active_media_update(
            current, student_controls_unlocked=True, params={"a": -1, "b": 2}
        )
        assert patched is not None
        self.assertEqual(patched["url"], DEFAULT_LIVE_MEDIA_URL)
        self.assertTrue(patched["student_controls_unlocked"])
        self.assertEqual(patched["params"]["a"], -1.0)
        self.assertEqual(patched["params"]["b"], 2.0)
        self.assertEqual(patched["stem"], DEFAULT_LIVE_MEDIA_STEM)

    def test_clear_drops_payload(self) -> None:
        """Clear returns None so the student iframe hides."""
        current = apply_active_media_update(None, url=DEFAULT_LIVE_MEDIA_URL)
        self.assertIsNone(apply_active_media_update(current, clear=True))


class LiveMediaChannelTests(unittest.TestCase):
    """Staff POST → student /api/student/state → clear."""

    def setUp(self) -> None:
        """Isolated app with one rostered class and staff + student clients."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.staff = self.app.test_client()
        self.student = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        self.staff.get("/auth/google?portal=staff")
        self.staff.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.staff.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )
        created = self.staff.post(
            "/api/staff/classes",
            json={
                "offering_id": self.offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple"],
            },
        )
        self.assertEqual(created.status_code, 200)
        self.class_id = int(created.get_json()["class"]["id"])
        run = self.staff.post(
            f"/staff/class/{self.class_id}/run-live",
            follow_redirects=False,
        )
        self.assertEqual(run.status_code, 302)
        live = self.school.get_active_live_session_for_class(self.class_id)
        self.assertIsNotNone(live)
        assert live is not None
        self.session_code = str(live["session_code"])
        self.live_session_id = int(live["id"])
        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.student.post("/student/mood", data={"mood": "good"})

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _join_home(self) -> None:
        """Student is already on home after setUp mood."""
        home = self.student.get("/student/home")
        self.assertEqual(home.status_code, 200)
        html = home.get_data(as_text=True)
        self.assertIn("media-pane", html)
        self.assertIn("media-stem", html)

    def test_seed_page_served_and_quarantined(self) -> None:
        """Seed Real-slice HTML exists, frames same-origin, no jigsaw chrome."""
        rv = self.staff.get(DEFAULT_LIVE_MEDIA_URL)
        self.assertEqual(rv.status_code, 200)
        body = rv.get_data(as_text=True).lower()
        self.assertIn("real-slice", body)
        self.assertIn("ax²", body.replace("ax^2", "ax²"))
        self.assertIn("parabola", body)
        self.assertNotIn("jigsaw", body)
        self.assertNotIn("mean±d", body)
        self.assertNotIn("mean+/-d", body)
        self.assertNotIn("people-groups", body)
        self.assertEqual(rv.headers.get("X-Frame-Options"), "SAMEORIGIN")
        csp = rv.headers.get("Content-Security-Policy", "")
        self.assertIn("frame-ancestors 'self'", csp)
        path = LMS_DIR / "static" / "live-media" / "m1c1-c1-real-slice.html"
        self.assertTrue(path.is_file())
        rv.close()

    def test_set_student_state_swap_unlock_clear(self) -> None:
        """Set seed → student sees it → swap URL → unlock → clear hides it."""
        self._join_home()
        idle = self.student.get("/api/student/state").get_json()
        self.assertTrue(idle["ok"])
        self.assertIsNone(idle.get("active_media"))

        posted = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"url": DEFAULT_LIVE_MEDIA_URL},
        )
        self.assertEqual(posted.status_code, 200, posted.get_json())
        media = posted.get_json()["active_media"]
        self.assertEqual(media["url"], DEFAULT_LIVE_MEDIA_URL)
        self.assertEqual(media["stem"], DEFAULT_LIVE_MEDIA_STEM)
        self.assertFalse(media["student_controls_unlocked"])
        self.assertEqual(media["params"]["a"], 1.0)

        state = self.student.get("/api/student/state").get_json()
        self.assertEqual(state["active_media"]["url"], DEFAULT_LIVE_MEDIA_URL)
        self.assertEqual(state["active_media"]["stem"], DEFAULT_LIVE_MEDIA_STEM)
        self.assertFalse(state["active_media"]["student_controls_unlocked"])

        swapped = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"url": "/static/mood/good.svg"},
        )
        self.assertEqual(swapped.status_code, 200, swapped.get_json())
        self.assertEqual(
            swapped.get_json()["active_media"]["url"], "/static/mood/good.svg"
        )
        after_swap = self.student.get("/api/student/state").get_json()
        self.assertEqual(after_swap["active_media"]["url"], "/static/mood/good.svg")

        unlocked = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"student_controls_unlocked": True, "params": {"a": 0.5, "b": 1, "c": -2}},
        )
        self.assertEqual(unlocked.status_code, 200, unlocked.get_json())
        frag = unlocked.get_json()["active_media"]
        self.assertEqual(frag["url"], "/static/mood/good.svg")
        self.assertTrue(frag["student_controls_unlocked"])
        self.assertEqual(frag["params"]["a"], 0.5)
        self.assertEqual(frag["params"]["c"], -2.0)

        student_unlocked = self.student.get("/api/student/state").get_json()
        self.assertTrue(student_unlocked["active_media"]["student_controls_unlocked"])
        self.assertEqual(student_unlocked["active_media"]["params"]["b"], 1.0)

        cleared = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"clear": True},
        )
        self.assertEqual(cleared.status_code, 200)
        self.assertIsNone(cleared.get_json()["active_media"])
        gone = self.student.get("/api/student/state").get_json()
        self.assertIsNone(gone.get("active_media"))

    def test_prompt_still_works_alongside_media(self) -> None:
        """Game-show prompt push is unchanged when media is active."""
        self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"url": DEFAULT_LIVE_MEDIA_URL},
        )
        set_prompt = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/prompts",
            json={
                "slide_index": 1,
                "kind": "mc",
                "payload": {"prompt": "Pick one", "choices": ["A", "B"]},
            },
        )
        self.assertEqual(set_prompt.status_code, 200, set_prompt.get_json())
        state = self.student.get("/api/student/state").get_json()
        self.assertEqual(state["active_media"]["url"], DEFAULT_LIVE_MEDIA_URL)
        self.assertEqual(state["prompt"]["kind"], "mc")

    def test_invalid_url_and_foreign_staff_rejected(self) -> None:
        """External URLs 400; another teacher cannot set this session's media."""
        bad = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"url": "https://example.com/pwn.html"},
        )
        self.assertEqual(bad.status_code, 400)

        self.school.register_staff("other@gmail.com")
        other_client = self.app.test_client()
        other_client.get("/auth/google?portal=staff")
        other_client.get("/auth/google/callback?email=other@gmail.com&name=O")
        other_client.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("other@gmail.com")[
                    "verification_code"
                ]
            },
        )
        denied = other_client.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"url": DEFAULT_LIVE_MEDIA_URL},
        )
        self.assertEqual(denied.status_code, 403)

    def test_staff_live_tab_has_media_controls(self) -> None:
        """Run Live Class chrome includes set / clear / unlock controls."""
        page = self.staff.get(f"/staff/class/{self.class_id}?tab=live")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("ap-active-media", html)
        self.assertIn("Show Real-slice", html)
        self.assertIn("Unlock a, b, c sliders", html)
        self.assertIn(DEFAULT_LIVE_MEDIA_URL, html)

    def test_student_home_csp_allows_same_origin_iframe(self) -> None:
        """Student home may frame same-origin live-media pages."""
        home = self.student.get("/student/home")
        self.assertEqual(home.status_code, 200)
        csp = home.headers.get("Content-Security-Policy", "")
        self.assertIn("frame-src 'self'", csp)


if __name__ == "__main__":
    unittest.main(verbosity=2)

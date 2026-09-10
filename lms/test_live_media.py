#!/usr/bin/env python3
"""Active-media channel: seed Real-slice page, staff set/swap/clear, student poll."""

from __future__ import annotations

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

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from live_media import (  # noqa: E402
    DEFAULT_LIVE_MEDIA_CHIP,
    DEFAULT_LIVE_MEDIA_LATERAL_CHIP,
    DEFAULT_LIVE_MEDIA_STEM,
    DEFAULT_LIVE_MEDIA_URL,
    ENCORE_LABEL,
    ENCORE_YOUTUBE_URL,
    TOAST_CONS_1,
    TOAST_CONS_4,
    TOAST_FREEZE,
    TOAST_REVEAL_AXES,
    TOAST_STUDENT_UNLOCK,
    apply_active_media_update,
    c1_cons_catalog,
    challenge_clears_active_media,
    get_c1_cons_item,
    live_media_url_swap_allowed,
    normalize_active_media_url,
    public_active_media_payload,
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
        self.assertFalse(current["reveal_axes"])
        self.assertFalse(current["reveal_lateral"])
        self.assertFalse(current["allow_3d_limited"])
        self.assertFalse(current["frozen"])
        self.assertEqual(current["entry_chip"], DEFAULT_LIVE_MEDIA_CHIP)
        self.assertEqual(current["unlock_flags"]["L0"], True)
        self.assertEqual(current["unlock_flags"]["L1"], False)
        self.assertEqual(current["unlock_flags"]["L4"], False)
        self.assertEqual(current["stem"], DEFAULT_LIVE_MEDIA_STEM)
        patched = apply_active_media_update(
            current,
            student_controls_unlocked=True,
            reveal_axes=True,
            unlock_flags={"L1": True},
            answers=["opens upward"],
            params={"a": -1, "b": 2},
        )
        assert patched is not None
        self.assertEqual(patched["url"], DEFAULT_LIVE_MEDIA_URL)
        self.assertTrue(patched["student_controls_unlocked"])
        self.assertTrue(patched["reveal_axes"])
        self.assertTrue(patched["unlock_flags"]["L1"])
        self.assertTrue(patched["unlock_flags"]["L0"])
        self.assertEqual(patched["answers"], ["opens upward"])
        self.assertEqual(patched["params"]["a"], -1.0)
        self.assertEqual(patched["params"]["b"], 2.0)
        self.assertEqual(patched["stem"], DEFAULT_LIVE_MEDIA_STEM)

    def test_lateral_peel_holds_stem_and_gates_encore(self) -> None:
        """L4 / reveal_lateral yaws in-pane; encore URL exists but frozen is off."""
        current = apply_active_media_update(None, url=DEFAULT_LIVE_MEDIA_URL)
        assert current is not None
        via_flag = apply_active_media_update(current, reveal_lateral=True)
        assert via_flag is not None
        self.assertTrue(via_flag["reveal_lateral"])
        self.assertTrue(via_flag["unlock_flags"]["L4"])
        self.assertEqual(via_flag["chip"], DEFAULT_LIVE_MEDIA_LATERAL_CHIP)
        self.assertEqual(via_flag["entry_chip"], DEFAULT_LIVE_MEDIA_CHIP)
        self.assertEqual(via_flag["stem"], DEFAULT_LIVE_MEDIA_STEM)
        self.assertFalse(via_flag["frozen"])
        self.assertFalse(via_flag["allow_3d_limited"])
        self.assertEqual(via_flag["encore_url"], ENCORE_YOUTUBE_URL)
        self.assertEqual(via_flag["encore_label"], ENCORE_LABEL)
        via_l4 = apply_active_media_update(
            current, unlock_flags={"L4": True}, allow_3d_limited=True
        )
        assert via_l4 is not None
        self.assertTrue(via_l4["reveal_lateral"])
        self.assertTrue(via_l4["allow_3d_limited"])
        frozen = apply_active_media_update(via_flag, frozen=True)
        assert frozen is not None
        self.assertTrue(frozen["frozen"])
        self.assertEqual(frozen["stem"], DEFAULT_LIVE_MEDIA_STEM)
        self.assertEqual(frozen["chip"], DEFAULT_LIVE_MEDIA_LATERAL_CHIP)
        self.assertEqual(frozen["entry_chip"], DEFAULT_LIVE_MEDIA_CHIP)
        off = apply_active_media_update(frozen, reveal_lateral=False)
        assert off is not None
        self.assertFalse(off["reveal_lateral"])
        self.assertFalse(off["unlock_flags"]["L4"])
        self.assertEqual(off["chip"], DEFAULT_LIVE_MEDIA_CHIP)
        self.assertEqual(off["entry_chip"], DEFAULT_LIVE_MEDIA_CHIP)
        self.assertEqual(off["stem"], DEFAULT_LIVE_MEDIA_STEM)

    def test_clear_drops_payload(self) -> None:
        """Clear returns None so the student iframe hides."""
        current = apply_active_media_update(None, url=DEFAULT_LIVE_MEDIA_URL)
        self.assertIsNone(apply_active_media_update(current, clear=True))

    def test_toast_and_caption_on_peel_edges(self) -> None:
        """Axes / unlock / freeze / CONS-1 / CONS-4 fill toast+caption."""
        current = apply_active_media_update(None, url=DEFAULT_LIVE_MEDIA_URL)
        assert current is not None
        self.assertEqual(current["toast"], "")
        self.assertEqual(current["caption"], "")
        axes = apply_active_media_update(current, reveal_axes=True)
        assert axes is not None
        self.assertEqual(axes["toast"], TOAST_REVEAL_AXES)
        self.assertEqual(axes["toast_key"], "reveal_axes")
        self.assertEqual(axes["caption"], TOAST_REVEAL_AXES)
        self.assertEqual(axes["stem"], DEFAULT_LIVE_MEDIA_STEM)
        unlocked = apply_active_media_update(axes, student_controls_unlocked=True)
        assert unlocked is not None
        self.assertEqual(unlocked["toast"], TOAST_STUDENT_UNLOCK)
        self.assertEqual(unlocked["toast_key"], "unlock")
        self.assertEqual(unlocked["caption"], TOAST_STUDENT_UNLOCK)
        frozen = apply_active_media_update(unlocked, frozen=True)
        assert frozen is not None
        self.assertEqual(frozen["toast"], TOAST_FREEZE)
        self.assertEqual(frozen["toast_key"], "freeze")
        self.assertEqual(frozen["caption"], TOAST_FREEZE)
        self.assertEqual(frozen["stem"], DEFAULT_LIVE_MEDIA_STEM)
        cons1 = apply_active_media_update(frozen, cons_item="C1-CONS-1")
        assert cons1 is not None
        self.assertEqual(cons1["toast"], TOAST_CONS_1)
        self.assertEqual(cons1["toast_key"], "cons_1")
        self.assertEqual(cons1["caption"], TOAST_CONS_1)
        cons4 = apply_active_media_update(cons1, cons_item="C1-CONS-4")
        assert cons4 is not None
        self.assertEqual(cons4["toast"], TOAST_CONS_4)
        self.assertEqual(cons4["toast_key"], "cons_4")
        self.assertEqual(cons4["caption"], TOAST_CONS_4)
        self.assertEqual(cons4["encore_label"], ENCORE_LABEL)

    def test_cons_gated_on_freeze(self) -> None:
        """CONS-1…5 raise before freeze and attach after freeze."""
        current = apply_active_media_update(None, url=DEFAULT_LIVE_MEDIA_URL)
        assert current is not None
        with self.assertRaises(ValueError) as ctx:
            apply_active_media_update(current, cons_item="C1-CONS-1")
        self.assertIn("after freeze", str(ctx.exception))
        frozen = apply_active_media_update(current, frozen=True)
        assert frozen is not None
        cons = apply_active_media_update(frozen, cons_item=1)
        assert cons is not None
        self.assertEqual(cons["cons_item"], "C1-CONS-1")
        self.assertTrue(cons["frozen"])
        self.assertEqual(cons["toast"], TOAST_CONS_1)
        self.assertEqual(cons["toast_key"], "cons_1")
        catalog = c1_cons_catalog()
        self.assertEqual(len(catalog), 5)
        self.assertEqual(get_c1_cons_item("CONS-5")["id"], "C1-CONS-5")

    def test_peel_map_lives_on_active_media_blob(self) -> None:
        """Peels stay on reveal_axes / L0–L4 / reveal_lateral / allow_3d_limited."""
        current = apply_active_media_update(None, url=DEFAULT_LIVE_MEDIA_URL)
        assert current is not None
        for key in (
            "reveal_axes",
            "unlock_flags",
            "reveal_lateral",
            "allow_3d_limited",
            "frozen",
        ):
            self.assertIn(key, current)
        self.assertNotIn("flag_strip", current)
        self.assertNotIn("FlagStrip", current)
        self.assertEqual(
            set(current["unlock_flags"]), {"L0", "L1", "L2", "L3", "L4"}
        )

    def test_c2_c3_do_not_seed_active_media(self) -> None:
        """C2/C3 clear the blob instead of seeding a no-URL stub."""
        self.assertTrue(challenge_clears_active_media("C2"))
        self.assertTrue(challenge_clears_active_media("C3"))
        self.assertFalse(challenge_clears_active_media("C1"))
        self.assertIsNone(apply_active_media_update(None, challenge="C2"))
        current = apply_active_media_update(None, url=DEFAULT_LIVE_MEDIA_URL)
        frozen = apply_active_media_update(current, frozen=True)
        assert frozen is not None
        with_cons = apply_active_media_update(frozen, cons_item="C1-CONS-1")
        assert with_cons is not None
        self.assertEqual(with_cons["cons_item"], "C1-CONS-1")
        self.assertIsNone(apply_active_media_update(with_cons, challenge="C3"))
        self.assertIsNone(
            public_active_media_payload({"challenge": "C2", "url": "", "stem": "nope"})
        )
        with self.assertRaises(ValueError):
            apply_active_media_update(None, cons_item="C1-CONS-1")

    def test_production_seed_locks_real_slice_url(self) -> None:
        """Without LOCAL_DEV/testing, only the seed Real-slice URL may be set."""
        with mock.patch.dict(os.environ, {"LOCAL_DEV_LOGIN": ""}, clear=False):
            self.assertFalse(live_media_url_swap_allowed(testing=False))
        self.assertTrue(live_media_url_swap_allowed(testing=True))
        current = apply_active_media_update(None, url=DEFAULT_LIVE_MEDIA_URL)
        with self.assertRaises(ValueError) as ctx:
            apply_active_media_update(
                current,
                url="/static/mood/good.svg",
                allow_url_swap=False,
            )
        self.assertIn("seed-lock", str(ctx.exception).lower())
        locked = apply_active_media_update(
            None, url=DEFAULT_LIVE_MEDIA_URL, allow_url_swap=False
        )
        assert locked is not None
        self.assertEqual(locked["url"], DEFAULT_LIVE_MEDIA_URL)


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
        self.assertIn("media-chip", html)
        self.assertIn("media-encore", html)
        self.assertIn("media-toast", html)
        self.assertIn("media-caption", html)

    def test_seed_page_served_and_quarantined(self) -> None:
        """Seed Real-slice HTML exists, frames same-origin, no jigsaw chrome."""
        rv = self.staff.get(DEFAULT_LIVE_MEDIA_URL)
        self.assertEqual(rv.status_code, 200)
        body = rv.get_data(as_text=True).lower()
        self.assertIn("real-slice", body)
        self.assertIn("ax²", body.replace("ax^2", "ax²"))
        self.assertIn("from this view only", body)
        self.assertIn("this picture was always a slice", body)
        self.assertIn("reveallateral", body.replace("_", "").replace(" ", ""))
        self.assertIn("lateral_yaw", body)
        self.assertIn("parabola", body)
        self.assertNotIn("youtube.com", body)
        self.assertNotIn("autoplay", body)
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
        self.assertEqual(media["entry_chip"], DEFAULT_LIVE_MEDIA_CHIP)
        self.assertEqual(media["chip"], DEFAULT_LIVE_MEDIA_CHIP)
        self.assertFalse(media["student_controls_unlocked"])
        self.assertFalse(media["reveal_axes"])
        self.assertFalse(media["reveal_lateral"])
        self.assertFalse(media["allow_3d_limited"])
        self.assertFalse(media["frozen"])
        self.assertEqual(media["answers"], [])
        self.assertEqual(media["params"]["a"], 1.0)

        state = self.student.get("/api/student/state").get_json()
        self.assertEqual(state["active_media"]["url"], DEFAULT_LIVE_MEDIA_URL)
        self.assertEqual(state["active_media"]["stem"], DEFAULT_LIVE_MEDIA_STEM)
        self.assertEqual(state["active_media"]["entry_chip"], DEFAULT_LIVE_MEDIA_CHIP)
        self.assertFalse(state["active_media"]["student_controls_unlocked"])
        self.assertFalse(state["active_media"]["reveal_axes"])
        self.assertFalse(state["active_media"]["reveal_lateral"])
        self.assertFalse(state["active_media"]["frozen"])

        peeled = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={
                "reveal_axes": True,
                "unlock_flags": {"L1": True},
                "answers": ["a is positive"],
            },
        )
        self.assertEqual(peeled.status_code, 200, peeled.get_json())
        peeled_media = peeled.get_json()["active_media"]
        self.assertEqual(peeled_media["stem"], DEFAULT_LIVE_MEDIA_STEM)
        self.assertTrue(peeled_media["reveal_axes"])
        self.assertTrue(peeled_media["unlock_flags"]["L1"])
        self.assertEqual(peeled_media["answers"], ["a is positive"])
        after_peel = self.student.get("/api/student/state").get_json()
        self.assertTrue(after_peel["active_media"]["reveal_axes"])
        self.assertEqual(after_peel["active_media"]["stem"], DEFAULT_LIVE_MEDIA_STEM)
        self.assertEqual(after_peel["active_media"]["answers"], ["a is positive"])
        self.assertEqual(after_peel["active_media"]["toast"], TOAST_REVEAL_AXES)
        self.assertEqual(after_peel["active_media"]["toast_key"], "reveal_axes")
        self.assertEqual(after_peel["active_media"]["caption"], TOAST_REVEAL_AXES)

        lateral = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"reveal_lateral": True, "allow_3d_limited": True},
        )
        self.assertEqual(lateral.status_code, 200, lateral.get_json())
        lat_media = lateral.get_json()["active_media"]
        self.assertEqual(lat_media["stem"], DEFAULT_LIVE_MEDIA_STEM)
        self.assertEqual(lat_media["chip"], DEFAULT_LIVE_MEDIA_LATERAL_CHIP)
        self.assertEqual(lat_media["entry_chip"], DEFAULT_LIVE_MEDIA_CHIP)
        self.assertTrue(lat_media["reveal_lateral"])
        self.assertTrue(lat_media["unlock_flags"]["L4"])
        self.assertTrue(lat_media["allow_3d_limited"])
        self.assertFalse(lat_media["frozen"])
        after_lat = self.student.get("/api/student/state").get_json()
        self.assertEqual(
            after_lat["active_media"]["chip"], DEFAULT_LIVE_MEDIA_LATERAL_CHIP
        )
        self.assertEqual(
            after_lat["active_media"]["entry_chip"], DEFAULT_LIVE_MEDIA_CHIP
        )
        self.assertEqual(after_lat["active_media"]["stem"], DEFAULT_LIVE_MEDIA_STEM)
        self.assertFalse(after_lat["active_media"]["frozen"])

        froze = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"frozen": True},
        )
        self.assertEqual(froze.status_code, 200, froze.get_json())
        froze_media = froze.get_json()["active_media"]
        self.assertTrue(froze_media["frozen"])
        self.assertEqual(froze_media["encore_url"], ENCORE_YOUTUBE_URL)
        self.assertEqual(froze_media["stem"], DEFAULT_LIVE_MEDIA_STEM)
        after_freeze = self.student.get("/api/student/state").get_json()
        self.assertTrue(after_freeze["active_media"]["frozen"])
        self.assertEqual(after_freeze["active_media"]["encore_url"], ENCORE_YOUTUBE_URL)
        self.assertEqual(after_freeze["active_media"]["toast"], TOAST_FREEZE)
        self.assertEqual(after_freeze["active_media"]["toast_key"], "freeze")
        self.assertEqual(after_freeze["active_media"]["caption"], TOAST_FREEZE)
        self.assertEqual(after_freeze["active_media"]["encore_label"], ENCORE_LABEL)

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
        self.assertIn("Reveal axes on student view", html)
        self.assertIn("L4 — in-pane lateral reveal", html)
        self.assertIn("Freeze — offer optional encore", html)
        self.assertIn("Limited student yaw after lateral", html)
        self.assertIn("CONS-1 · a", html)
        self.assertIn("C2 — no immersive media", html)
        self.assertIn("C3 — no immersive media", html)
        self.assertIn("do not seed active_media_json", html)
        self.assertIn("not a FlagStrip", html)
        self.assertNotIn("FlagStrip", html.replace("not a FlagStrip", ""))
        self.assertIn("out-of-page / lateral beat", html)
        self.assertIn(DEFAULT_LIVE_MEDIA_URL, html)

    def test_student_home_csp_allows_same_origin_iframe(self) -> None:
        """Student home may frame same-origin live-media pages."""
        home = self.student.get("/student/home")
        self.assertEqual(home.status_code, 200)
        csp = home.headers.get("Content-Security-Policy", "")
        self.assertIn("frame-src 'self'", csp)

    def test_cons_hidden_until_freeze_then_student_sees_prompt(self) -> None:
        """CONS-1 is 400 before freeze; after freeze students poll the prompt."""
        self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"url": DEFAULT_LIVE_MEDIA_URL},
        )
        blocked = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"cons_item": "C1-CONS-1"},
        )
        self.assertEqual(blocked.status_code, 400, blocked.get_json())
        self.assertIn("freeze", blocked.get_json()["error"].lower())
        idle = self.student.get("/api/student/state").get_json()
        self.assertIsNone(idle.get("prompt"))
        self.assertFalse(idle["active_media"]["frozen"])
        self.assertEqual(idle["active_media"]["cons_item"], "")

        froze = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"frozen": True},
        )
        self.assertEqual(froze.status_code, 200, froze.get_json())
        still_idle = self.student.get("/api/student/state").get_json()
        self.assertTrue(still_idle["active_media"]["frozen"])
        self.assertEqual(still_idle["active_media"]["toast"], TOAST_FREEZE)
        self.assertIsNone(still_idle.get("prompt"))

        cons = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"cons_item": "C1-CONS-1"},
        )
        self.assertEqual(cons.status_code, 200, cons.get_json())
        media = cons.get_json()["active_media"]
        self.assertEqual(media["cons_item"], "C1-CONS-1")
        self.assertEqual(media["toast"], TOAST_CONS_1)
        self.assertEqual(media["toast_key"], "cons_1")
        state = self.student.get("/api/student/state").get_json()
        self.assertEqual(state["active_media"]["cons_item"], "C1-CONS-1")
        self.assertEqual(state["active_media"]["toast"], TOAST_CONS_1)
        self.assertIsNotNone(state.get("prompt"))
        self.assertEqual(state["prompt"]["kind"], "mc")
        self.assertEqual(state["prompt"]["payload"]["item_id"], "C1-CONS-1")
        self.assertIn("this picture", state["prompt"]["payload"]["prompt"].lower())
        self.assertNotIn("key", state["prompt"]["payload"])
        self.assertNotIn("cement", state["prompt"]["payload"])

        cons4 = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"cons_item": "C1-CONS-4"},
        )
        self.assertEqual(cons4.status_code, 200, cons4.get_json())
        cons4_media = cons4.get_json()["active_media"]
        self.assertEqual(cons4_media["cons_item"], "C1-CONS-4")
        self.assertEqual(cons4_media["toast"], TOAST_CONS_4)
        self.assertEqual(cons4_media["toast_key"], "cons_4")
        after_cons4 = self.student.get("/api/student/state").get_json()
        self.assertEqual(after_cons4["active_media"]["toast"], TOAST_CONS_4)
        self.assertEqual(after_cons4["prompt"]["kind"], "draw")

        cons5 = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"cons_item": 5},
        )
        self.assertEqual(cons5.status_code, 200, cons5.get_json())
        later = self.student.get("/api/student/state").get_json()
        self.assertEqual(later["prompt"]["kind"], "share")
        self.assertEqual(later["prompt"]["payload"]["item_id"], "C1-CONS-5")

        unfreeze = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"frozen": False},
        )
        self.assertEqual(unfreeze.status_code, 200, unfreeze.get_json())
        hidden = self.student.get("/api/student/state").get_json()
        self.assertFalse(hidden["active_media"]["frozen"])
        self.assertEqual(hidden["active_media"]["cons_item"], "")
        self.assertIsNone(hidden.get("prompt"))

    def test_c2_c3_api_does_not_seed_active_media(self) -> None:
        """C2/C3 POST clears the blob; GET defaults say they do not seed."""
        seeded = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"url": DEFAULT_LIVE_MEDIA_URL},
        )
        self.assertEqual(seeded.status_code, 200)
        c2 = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"challenge": "C2"},
        )
        self.assertEqual(c2.status_code, 200, c2.get_json())
        self.assertIsNone(c2.get_json()["active_media"])
        state = self.student.get("/api/student/state").get_json()
        self.assertIsNone(state.get("active_media"))
        cons = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"cons_item": "C1-CONS-1"},
        )
        self.assertEqual(cons.status_code, 400)
        defaults = self.staff.get(
            f"/api/live-sessions/{self.live_session_id}/active-media"
        ).get_json()
        self.assertIsNone(defaults["active_media"])
        self.assertIsNone(defaults["defaults"]["c2"]["url"])
        self.assertIsNone(defaults["defaults"]["c3"]["url"])
        self.assertFalse(defaults["defaults"]["c2"]["seed"])
        self.assertFalse(defaults["defaults"]["c3"]["seed"])
        self.assertIn("not seed", defaults["defaults"]["c2"]["note"].lower())
        self.assertEqual(defaults["defaults"]["url"], DEFAULT_LIVE_MEDIA_URL)
        self.assertEqual(len(defaults["cons_pack"]), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)

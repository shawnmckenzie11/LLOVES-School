#!/usr/bin/env python3
"""Student live-class portal: join, mood, character, home, and rank flag."""

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
from live_class_metadata import empty_live_class_metadata  # noqa: E402
from meet_team import (  # noqa: E402
    MEET_TEAM_FIXED_CHOICES,
    MEET_TEAM_PROMPT,
    MEET_TEAM_WARMUP_POOL,
    is_meet_team_payload,
)
from minds_on import (  # noqa: E402
    MINDS_ON_C2_CHOICES,
    MINDS_ON_CHOICES,
    MINDS_ON_PROMPT,
    MINDS_ON_SLIDE_INDEX,
    is_minds_on_payload,
    minds_on_prompt_payload,
)
from live_prompt_feedback import LEAD_MISS  # noqa: E402
from teams_spark import TEAMS_SPARK_PROMPT  # noqa: E402


class StudentPortalTests(unittest.TestCase):
    """Mood → character → home, plus staff show-rank for student state."""

    def setUp(self) -> None:
        """Isolated app with one rostered class and separate staff/student clients."""
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
                "codenames": ["Maple", "Aspen"],
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

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _maple_row(self) -> dict:
        """Return Maple's roster row after join."""
        row = self.school.game.find_student_by_codename(self.class_id, "Maple")
        assert row is not None
        return self.school.game.get_student(self.class_id, int(row["id"]))

    def _pick_character(self, key: str = "fox") -> None:
        """POST the character pick step."""
        self.student.post("/student/character", data={"character": key})

    def _use_legacy_live_metadata(self) -> None:
        """Route this test through the schema-v1 singleton compatibility path."""

        self.school.live_class_metadata_for_session = (
            self._legacy_live_metadata_for_session
        )

    def _legacy_live_metadata_for_session(self, _session_id: int) -> dict:
        """Return empty schema-v1 metadata for legacy prompt tests."""

        return empty_live_class_metadata("MCF3M", "M1", "C1")

    def test_join_mood_character_home_and_show_rank(self) -> None:
        """Join → mood → character → home; rank hidden until enabled."""
        self._use_legacy_live_metadata()
        join = self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.assertEqual(join.status_code, 302)
        self.assertIn("/student/mood", join.headers.get("Location", ""))
        attendees = self.school.list_live_session_attendees(
            self.live_session_id, present_only=True
        )
        self.assertEqual(len(attendees), 1)
        self.assertEqual(attendees[0]["codename"], "Maple")

        before_char = self.student.get("/student/character", follow_redirects=False)
        self.assertEqual(before_char.status_code, 302)
        self.assertIn("/student/mood", before_char.headers.get("Location", ""))

        mood_page = self.student.get("/student/mood")
        self.assertEqual(mood_page.status_code, 200)
        mood_html = mood_page.get_data(as_text=True)
        self.assertIn("Join Class", mood_html)
        self.assertNotIn("Choose your Avatar", mood_html)
        self.assertNotIn("Optional — pick a face", mood_html)
        self.assertNotIn("mood-label", mood_html)
        self.assertEqual(mood_html.count('name="mood"'), 3)
        self.assertIn('value="good"', mood_html)
        self.assertIn('value="ok"', mood_html)
        self.assertIn('value="low"', mood_html)
        self.assertNotIn('value="tired"', mood_html)
        self.assertNotIn("Energetic", mood_html)
        self.assertIn("/static/mood/good.svg", mood_html)
        self.assertIn("/static/mood/ok.svg", mood_html)
        self.assertIn("/static/mood/low.svg", mood_html)
        self.assertNotIn("😊", mood_html)

        mood = self.student.post("/student/mood", data={"mood": "good"}, follow_redirects=False)
        self.assertEqual(mood.status_code, 302)
        self.assertIn("/student/character", mood.headers.get("Location", ""))
        self.assertNotIn("/student/home", mood.headers.get("Location", ""))

        home_early = self.student.get("/student/home", follow_redirects=False)
        self.assertEqual(home_early.status_code, 302)
        self.assertIn("/student/character", home_early.headers.get("Location", ""))

        char_page = self.student.get("/student/character", follow_redirects=False)
        self.assertEqual(char_page.status_code, 200)
        char_html = char_page.get_data(as_text=True)
        self.assertIn("Choose your Avatar", char_html)
        self.assertIn("🦊", char_html)
        self.assertIn("🦄", char_html)
        self.assertIn('value="fox"', char_html)

        character = self.student.post(
            "/student/character",
            data={"character": "fox"},
            follow_redirects=False,
        )
        self.assertEqual(character.status_code, 302)
        self.assertIn("/student/home", character.headers.get("Location", ""))
        maple = self._maple_row()
        self.assertEqual(maple.get("character_key"), "fox")
        self.assertEqual(maple.get("mood"), "good")

        again = self.student.get("/student/character", follow_redirects=False)
        self.assertEqual(again.status_code, 302)
        self.assertIn("/student/home", again.headers.get("Location", ""))

        home = self.student.get("/student/home")
        self.assertEqual(home.status_code, 200)
        home_html = home.get_data(as_text=True)
        self.assertIn("Waiting room — class is about to begin.", home_html)
        self.assertNotIn("Waiting for your teacher to start scoring.", home_html)
        self.assertLess(home_html.index('id="me-avatar"'), home_html.index('id="me-name"'))
        overlay = self.school.get_live_session_state(self.live_session_id)
        maple_att = next(
            row
            for row in overlay["attendees"]
            if str(row.get("codename") or "") == "Maple"
        )
        self.assertEqual(maple_att.get("character"), "fox")

        state = self.student.get("/api/student/state")
        self.assertEqual(state.status_code, 200)
        payload = state.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual((payload.get("me") or {}).get("character"), "fox")
        self.assertFalse(payload["show_rank"])
        self.assertNotIn("rank", payload.get("me") or {})
        self.assertTrue(payload.get("waiting_room"))
        self.assertFalse(payload.get("scoring"))
        self.assertIsNone(payload.get("active_media"))
        self.assertIsNotNone(payload.get("prompt"))
        self.assertEqual(payload["prompt"]["kind"], "mc")
        self.assertEqual(payload["prompt"]["payload"]["item_id"], "minds_on")
        self.assertEqual(payload["prompt"]["payload"]["label"], "Minds-On")
        self.assertEqual(payload["prompt"]["payload"]["prompt"], MINDS_ON_PROMPT)
        self.assertEqual(
            payload["prompt"]["payload"]["choices"],
            list(MINDS_ON_CHOICES),
        )
        self.assertNotIn("key", payload["prompt"]["payload"])
        self.assertEqual(len(payload["prompt"]["payload"]["items"]), 1)
        self.assertNotIn("key", payload["prompt"]["payload"]["items"][0])

        toggle = self.staff.post(
            f"/api/classes/{self.class_id}/show-rank",
            json={"enabled": True},
        )
        self.assertEqual(toggle.status_code, 200)
        self.assertTrue(toggle.get_json().get("show_rank"))

        ranked = self.student.get("/api/student/state").get_json()
        self.assertTrue(ranked["show_rank"])

    def test_mood_skip_then_character_then_home(self) -> None:
        """Skip on mood still requires a character pick before home."""
        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        skipped = self.student.post(
            "/student/mood",
            data={"skip": "1"},
            follow_redirects=False,
        )
        self.assertEqual(skipped.status_code, 302)
        self.assertIn("/student/character", skipped.headers.get("Location", ""))

        char_page = self.student.get("/student/character")
        self.assertEqual(char_page.status_code, 200)
        self.assertIn("Choose your Avatar", char_page.get_data(as_text=True))

        character = self.student.post(
            "/student/character",
            data={"character": "panda"},
            follow_redirects=False,
        )
        self.assertEqual(character.status_code, 302)
        self.assertIn("/student/home", character.headers.get("Location", ""))
        maple = self._maple_row()
        self.assertEqual(maple.get("character_key"), "panda")
        self.assertIsNone(maple.get("mood"))

        mood_again = self.student.get("/student/mood", follow_redirects=False)
        self.assertEqual(mood_again.status_code, 302)
        self.assertIn("/student/home", mood_again.headers.get("Location", ""))
        home = self.student.get("/student/home")
        self.assertEqual(home.status_code, 200)

    def test_home_rejects_ended_session(self) -> None:
        """Ending the live session clears student access on home/state."""
        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.student.post("/student/mood", data={"mood": "good"})
        self._pick_character()
        home_ok = self.student.get("/student/home")
        self.assertEqual(home_ok.status_code, 200)

        attendees = self.school.list_live_session_attendees(self.live_session_id)
        token = attendees[0].get("visit_token")
        self.assertTrue(token)

        self.school.end_live_class_session(self.live_session_id)
        home_gone = self.student.get("/student/home", follow_redirects=False)
        self.assertEqual(home_gone.status_code, 302)
        self.assertIn("/", home_gone.headers.get("Location", ""))
        self.assertNotIn("/student/home", home_gone.headers.get("Location", ""))

        # Fresh client: visit token no longer resumes an ended session.
        other = self.app.test_client()
        visit = other.get(f"/student/s/{token}", follow_redirects=False)
        self.assertEqual(visit.status_code, 302)
        self.assertNotIn("/student/", visit.headers.get("Location", ""))

    def test_visit_token_resumes_home(self) -> None:
        """Opaque /student/s/<token> rebinds cookie and reaches home."""
        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.student.post("/student/mood", data={"mood": "good"})
        attendees = self.school.list_live_session_attendees(self.live_session_id)
        token = str(attendees[0]["visit_token"])

        mid = self.app.test_client()
        mid_visit = mid.get(f"/student/s/{token}", follow_redirects=False)
        self.assertEqual(mid_visit.status_code, 302)
        self.assertIn("/student/character", mid_visit.headers.get("Location", ""))

        self._pick_character()
        fresh = self.app.test_client()
        visit = fresh.get(f"/student/s/{token}", follow_redirects=False)
        self.assertEqual(visit.status_code, 302)
        self.assertIn("/student/home", visit.headers.get("Location", ""))
        home = fresh.get("/student/home")
        self.assertEqual(home.status_code, 200)
        self.assertIn("live-response", home.get_data(as_text=True))

    def test_prompt_stub_round_trip(self) -> None:
        """Staff sets MC prompt; student polls and submits a response."""
        self._use_legacy_live_metadata()
        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.student.post("/student/mood", data={"mood": "good"})
        self._pick_character()

        set_prompt = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/prompts",
            json={
                "slide_index": 2,
                "kind": "mc",
                "payload": {"prompt": "Pick one", "choices": ["A", "B"]},
            },
        )
        self.assertEqual(set_prompt.status_code, 200, set_prompt.get_json())
        prompt = set_prompt.get_json()["prompt"]
        self.assertEqual(prompt["kind"], "mc")
        self.assertEqual(prompt["slide_index"], 2)

        state = self.student.get("/api/student/state").get_json()
        self.assertIsNotNone(state.get("prompt"))
        self.assertEqual(state["prompt"]["payload"]["item_id"], "minds_on")
        self.assertIsNone(state.get("my_response"))

        submit = self.student.post(
            "/api/student/live-prompt/response",
            json={"prompt_id": state["prompt"]["id"], "response": {"choice": "B"}},
        )
        self.assertEqual(submit.status_code, 200, submit.get_json())
        self.assertTrue(submit.get_json().get("ack"))

        again = self.student.get("/api/student/live-prompt").get_json()
        payload = (again.get("prompt") or {}).get("payload") or {}
        self.assertEqual(payload.get("item_id"), "minds_on")
        self.assertTrue(again.get("my_response"))

    def test_pick_preserves_live_session_id(self) -> None:
        """student_pick rebind keeps the live session + visit token keys."""
        from student_portal import bind_student_session

        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        with self.student.session_transaction() as sess:
            live_id = sess.get("student_live_session_id")
            token = sess.get("student_visit_token")
            offering_id = sess.get("student_offering_id")
            code = sess.get("student_live_code")
        self.assertEqual(live_id, self.live_session_id)
        self.assertTrue(token)

        cls = self.school.game.get_class(self.class_id)
        student = self.school.game.find_student_by_codename(self.class_id, "Maple")
        assert student is not None
        offering = self.school.get_offering(int(offering_id))
        # Rebind the way student_pick does after section choice.
        with self.student.session_transaction() as sess:
            bind_student_session(
                sess,
                offering,
                cls,
                student,
                live_session_id=int(live_id),
                session_code=str(code),
                visit_token=str(token),
            )
            self.assertEqual(sess.get("student_live_session_id"), self.live_session_id)
            self.assertEqual(sess.get("student_visit_token"), token)

    def test_multi_tab_visit_token_isolates_identity(self) -> None:
        """Two student joins in one browser: token header keeps each codename."""
        join_maple = self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.assertEqual(join_maple.status_code, 302)
        maple_loc = join_maple.headers.get("Location", "")
        self.assertIn("v=", maple_loc)
        attendees = self.school.list_live_session_attendees(self.live_session_id)
        maple_token = str(
            next(row for row in attendees if row["codename"] == "Maple")["visit_token"]
        )

        join_aspen = self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Aspen"},
            follow_redirects=False,
        )
        self.assertEqual(join_aspen.status_code, 302)
        attendees = self.school.list_live_session_attendees(self.live_session_id)
        aspen_token = str(
            next(row for row in attendees if row["codename"] == "Aspen")["visit_token"]
        )
        self.assertNotEqual(maple_token, aspen_token)

        # Cookie now reflects Aspen; Maple token header must still resolve Maple.
        maple_state = self.student.get(
            "/api/student/state",
            headers={"X-Student-Visit-Token": maple_token},
        )
        self.assertEqual(maple_state.status_code, 200)
        self.assertEqual(maple_state.get_json()["me"]["codename"], "Maple")

        aspen_state = self.student.get(
            "/api/student/state",
            headers={"X-Student-Visit-Token": aspen_token},
        )
        self.assertEqual(aspen_state.status_code, 200)
        self.assertEqual(aspen_state.get_json()["me"]["codename"], "Aspen")

        mood_maple = self.student.post(
            "/student/mood",
            data={"mood": "good", "visit_token": maple_token},
            headers={"X-Student-Visit-Token": maple_token},
            follow_redirects=False,
        )
        self.assertEqual(mood_maple.status_code, 302)
        maple = self.school.game.get_student(
            self.class_id,
            int(
                self.school.game.find_student_by_codename(self.class_id, "Maple")["id"]
            ),
        )
        aspen = self.school.game.get_student(
            self.class_id,
            int(
                self.school.game.find_student_by_codename(self.class_id, "Aspen")["id"]
            ),
        )
        self.assertEqual(maple.get("mood"), "good")
        self.assertIsNone(aspen.get("mood"))

        leave_maple = self.student.post(
            "/api/student/leave",
            json={"visit_token": maple_token},
            headers={"X-Student-Visit-Token": maple_token},
        )
        self.assertEqual(leave_maple.status_code, 204)
        aspen_state_after = self.student.get(
            "/api/student/state",
            headers={"X-Student-Visit-Token": aspen_token},
        )
        self.assertEqual(aspen_state_after.status_code, 200)
        self.assertEqual(aspen_state_after.get_json()["me"]["codename"], "Aspen")

    def test_second_join_same_name_rejected(self) -> None:
        """A name already present in the live class cannot sign in again."""
        first = self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.assertEqual(first.status_code, 302)
        attendees = self.school.list_live_session_attendees(
            self.live_session_id, present_only=True
        )
        self.assertEqual(len(attendees), 1)
        first_token = str(attendees[0]["visit_token"])

        other = self.app.test_client()
        second = other.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.assertEqual(second.status_code, 409)
        self.assertIn("already in class", second.get_data(as_text=True).lower())
        still = self.school.list_live_session_attendees(
            self.live_session_id, present_only=True
        )
        self.assertEqual(len(still), 1)
        self.assertEqual(str(still[0]["visit_token"]), first_token)

    def test_rejoin_after_leave_allowed(self) -> None:
        """After leaving, the same name may join the live class again."""
        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        first_token = str(
            self.school.list_live_session_attendees(self.live_session_id)[0][
                "visit_token"
            ]
        )
        leave = self.student.post("/api/student/leave")
        self.assertEqual(leave.status_code, 204)
        again = self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.assertEqual(again.status_code, 302)
        second = self.school.list_live_session_attendees(
            self.live_session_id, present_only=True
        )
        self.assertEqual(len(second), 1)
        self.assertEqual(str(second[0]["visit_token"]), first_token)
        self.assertEqual(
            str(second[0]["participant_uuid"]),
            str(
                self.school.list_live_session_attendees(self.live_session_id)[0][
                    "participant_uuid"
                ]
            ),
        )

    def test_join_mints_stable_uuid_and_token(self) -> None:
        """One logical person keeps the same uuid and rejoin token on refresh."""
        from student_portal import REJOIN_COOKIE_NAME

        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        first = self.school.list_live_session_attendees(self.live_session_id)[0]
        uuid1 = str(first["participant_uuid"])
        token1 = str(first["visit_token"])
        self.assertTrue(uuid1)
        self.assertTrue(token1)

        self.student.post("/student/mood", data={"mood": "good"})
        self._pick_character()
        home = self.student.get("/student/home", follow_redirects=False)
        self.assertEqual(home.status_code, 200)
        still = self.school.list_live_session_attendees(
            self.live_session_id, present_only=True
        )
        self.assertEqual(len(still), 1)
        self.assertEqual(str(still[0]["visit_token"]), token1)
        self.assertEqual(str(still[0]["participant_uuid"]), uuid1)

        landing = self.student.get("/", follow_redirects=False)
        self.assertEqual(landing.status_code, 302)
        self.assertIn("/student/home", landing.headers.get("Location", ""))
        self.assertTrue(self.student.get_cookie(REJOIN_COOKIE_NAME))

        again = self.school.list_live_session_attendees(self.live_session_id)
        self.assertEqual(str(again[0]["visit_token"]), token1)
        self.assertEqual(str(again[0]["participant_uuid"]), uuid1)

    def test_landing_and_legacy_routes_keep_avatar_after_mood(self) -> None:
        """Cookie rejoin and /waiting / /game must not skip the avatar step."""
        join = self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.assertEqual(join.status_code, 302)
        self.assertIn("/student/mood", join.headers.get("Location", ""))

        mood = self.student.post(
            "/student/mood",
            data={"mood": "good"},
            follow_redirects=False,
        )
        self.assertEqual(mood.status_code, 302)
        self.assertIn("/student/character", mood.headers.get("Location", ""))
        self.assertNotIn("/student/home", mood.headers.get("Location", ""))

        landing = self.student.get("/", follow_redirects=False)
        self.assertEqual(landing.status_code, 302)
        self.assertIn("/student/character", landing.headers.get("Location", ""))
        self.assertNotIn("/student/home", landing.headers.get("Location", ""))

        for path in ("/student/waiting", "/student/game"):
            bounced = self.student.get(path, follow_redirects=False)
            self.assertEqual(bounced.status_code, 302, path)
            self.assertIn("/student/character", bounced.headers.get("Location", ""), path)
            self.assertNotIn("/student/home", bounced.headers.get("Location", ""), path)

        char_page = self.student.get("/student/character", follow_redirects=False)
        self.assertEqual(char_page.status_code, 200)
        self.assertIn("Choose your Avatar", char_page.get_data(as_text=True))

    def test_rejoin_cookie_cleared_when_session_ends(self) -> None:
        """Ended sessions drop the httpOnly auto-resume cookie."""
        from student_portal import REJOIN_COOKIE_NAME

        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.assertTrue(self.student.get_cookie(REJOIN_COOKIE_NAME))
        self.school.end_live_class_session(self.live_session_id)
        landing = self.student.get("/", follow_redirects=False)
        self.assertEqual(landing.status_code, 200)
        self.assertIsNone(self.student.get_cookie(REJOIN_COOKIE_NAME))

    def test_pagehide_script_does_not_leave(self) -> None:
        """student-live-session.js heartbeats instead of leaving on pagehide."""
        text = (LMS_DIR / "static" / "student-live-session.js").read_text(
            encoding="utf-8"
        )
        self.assertNotIn('addEventListener("pagehide"', text)
        self.assertIn("/api/student/heartbeat", text)
        self.assertIn("lloves-live-link", text)
        self.assertIn("lloves-live-retry", text)
        portal = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn("lloves-live-link", portal)
        self.assertIn("setStudentReconnectBanner(!ok)", portal)
        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.student.get("/student/home")
        present = self.school.list_live_session_attendees(
            self.live_session_id, present_only=True
        )
        self.assertEqual(len(present), 1)

    def test_heartbeat_stale_marks_left(self) -> None:
        """Missed heartbeats set left_at; the same token can still resume."""
        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        with self.school._lock:
            self.school.conn.execute(
                """
                UPDATE live_session_attendees
                SET last_heartbeat_at = '2000-01-01T00:00:00'
                WHERE live_session_id = ?
                """,
                (self.live_session_id,),
            )
            self.school.conn.commit()
            self.school._sweep_at.pop(self.live_session_id, None)
        state = self.staff.get(f"/api/live-sessions/{self.live_session_id}/state")
        self.assertEqual(state.status_code, 200)
        payload = state.get_json()
        self.assertEqual(payload["count"], 0)
        self.assertTrue(payload["attendees"][0].get("left_at"))
        beat = self.student.post("/api/student/heartbeat")
        self.assertEqual(beat.status_code, 200)
        present = self.school.list_live_session_attendees(
            self.live_session_id, present_only=True
        )
        self.assertEqual(len(present), 1)

    def test_guest_empty_name_uses_delight_copy(self) -> None:
        """Allow guests with a blank name shows the Wonder empty-name line."""
        toggle = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/guests",
            json={"allow_unmatched_guests": True},
        )
        self.assertEqual(toggle.status_code, 200)
        guest = self.app.test_client()
        rv = guest.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": ""},
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 401)
        self.assertIn("First name only", rv.get_data(as_text=True))

    def test_guest_flag_off_rejects_unmatched(self) -> None:
        """Allow guests defaults off; unmatched names are rejected."""
        other = self.app.test_client()
        rv = other.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "River"},
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 401)
        self.assertEqual(
            len(self.school.list_live_session_attendees(self.live_session_id)),
            0,
        )

    def test_guest_flag_on_mints_unmatched_uuid(self) -> None:
        """Checked Allow guests mints a uuid and flags the overlay row."""
        toggle = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/guests",
            json={"allow_unmatched_guests": True},
        )
        self.assertEqual(toggle.status_code, 200)
        self.assertTrue(toggle.get_json()["allow_unmatched_guests"])
        guest = self.app.test_client()
        rv = guest.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "River Smith"},
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302)
        self.assertIn("/student/home", rv.headers.get("Location", ""))
        rows = self.school.list_live_session_attendees(self.live_session_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["codename"], "River")
        self.assertTrue(int(rows[0]["unmatched"]))
        self.assertTrue(rows[0]["participant_uuid"])
        self.assertIn(rows[0]["student_id"], (None, 0, ""))
        state = self.staff.get(f"/api/live-sessions/{self.live_session_id}/state")
        body = state.get_json()
        attendee = body["attendees"][0]
        self.assertTrue(attendee["unmatched"])
        self.assertEqual(attendee["codename"], "River")
        self.assertNotIn("visit_token", attendee)
        dumped = state.get_data(as_text=True)
        self.assertNotIn(str(rows[0]["visit_token"]), dumped)

    def test_state_api_strips_rejoin_token(self) -> None:
        """Staff overlay state must not leak visit_token / rejoin secrets."""
        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        token = str(
            self.school.list_live_session_attendees(self.live_session_id)[0][
                "visit_token"
            ]
        )
        state = self.staff.get(f"/api/live-sessions/{self.live_session_id}/state")
        payload = state.get_json()
        self.assertNotIn("visit_token", payload.get("session") or {})
        for row in payload["attendees"]:
            self.assertNotIn("visit_token", row)
        self.assertNotIn(token, state.get_data(as_text=True))

    def test_name_collision_picker_without_last_names(self) -> None:
        """Two roster first names collide into a picker of Codenames."""
        with self.school.game._lock:
            self.school.game.conn.execute(
                "UPDATE students SET first_name = 'Alex' WHERE class_id = ?",
                (self.class_id,),
            )
            self.school.game.conn.commit()
        other = self.app.test_client()
        rv = other.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Alex"},
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 409)
        html = rv.get_data(as_text=True)
        self.assertIn("More than one student", html)
        self.assertIn("Maple", html)
        self.assertIn("Aspen", html)
        self.assertNotIn("last_display", html.lower())

    def _join_maple_home(self) -> None:
        """Join Maple through mood and character so /api/student/state is on home."""
        self.student.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": "Maple"},
            follow_redirects=False,
        )
        self.student.post("/student/mood", data={"mood": "good"})
        self._pick_character()

    def _publish_live_item(self, item_id: str) -> dict:
        """Publish one lifecycle item so students can see it.

        Args:
            item_id: Playlist or engine-ride id such as ``minds_on``.

        Returns:
            The published ``live_session_items`` row.
        """
        token = str(item_id or "").strip().lower().replace("-", "_")
        items = list(self.school.ensure_live_session_items(self.live_session_id))
        row = next(
            (
                item
                for item in items
                if str(item.get("item_id") or "").strip().lower().replace("-", "_")
                == token
            ),
            None,
        )
        if row is None:
            row = next(
                item
                for item in self.school.list_live_session_items(self.live_session_id)
                if str(item.get("item_id") or "").strip().lower().replace("-", "_")
                == token
            )
        return self.school.publish_live_session_item(
            self.live_session_id, int(row["id"]), publish_mode="individual"
        )

    def _staff_assign_two_teams(self) -> None:
        """Mark the roster present and Generate teams (n=2, random)."""
        begin = self.staff.post(
            f"/api/classes/{self.class_id}/begin",
            json={"meeting_date": "2026-09-09"},
        )
        self.assertEqual(begin.status_code, 200, begin.get_json())
        ids = [int(student["id"]) for student in begin.get_json()["students"]]
        self.staff.post(
            f"/api/classes/{self.class_id}/game/attendance",
            json={"present_ids": ids, "meeting_date": "2026-09-09"},
        )
        assigned = self.staff.post(
            f"/api/classes/{self.class_id}/game/assign",
            json={"n_teams": 2, "mode": "random"},
        )
        self.assertEqual(assigned.status_code, 200, assigned.get_json())

    def test_waiting_room_minds_on_and_wait_copy(self) -> None:
        """Join with no challenge media: Wonder wait line + M1C1 Minds-On MC."""
        self._join_maple_home()
        home = self.student.get("/student/home")
        html = home.get_data(as_text=True)
        self.assertIn("Waiting room — class is about to begin.", html)
        self.assertNotIn("start scoring", html)
        self.assertNotIn("meet-math", html.lower())

        idle = self.student.get("/api/student/state").get_json()
        self.assertTrue(idle["waiting_room"])
        self.assertIsNone(idle.get("active_media"))
        self.assertIsNone(idle.get("prompt"))
        self.assertEqual(idle.get("active_questions") or [], [])
        self._publish_live_item("minds_on")
        state = self.student.get("/api/student/state").get_json()
        self.assertTrue(state["waiting_room"])
        self.assertIsNone(state.get("active_media"))
        prompt = state["prompt"]
        self.assertEqual(prompt["payload"]["item_id"], "minds_on")
        self.assertEqual(prompt["payload"]["label"], "Minds-On")
        self.assertEqual(prompt["payload"]["artifact_id"], "quick-hitter-question-chain")
        self.assertEqual(prompt["payload"]["ride"], "minds_on")
        self.assertTrue(prompt["payload"]["ephemeral"])
        self.assertFalse(prompt["payload"]["durable_store"])
        self.assertEqual(prompt["payload"]["clear_on"], "team_challenge_start")
        self.assertEqual(prompt["kind"], "mc")
        self.assertEqual(prompt["payload"]["prompt"], MINDS_ON_PROMPT)
        self.assertEqual(prompt["payload"]["choices"], list(MINDS_ON_CHOICES))
        self.assertNotIn("Not sure", prompt["payload"]["choices"])
        self.assertNotIn("key", prompt["payload"])
        self.assertNotIn("cement", prompt["payload"])
        self.assertEqual(len(prompt["payload"]["items"]), 1)
        self.assertEqual(prompt["payload"]["items"][0]["prompt"], MINDS_ON_PROMPT)
        self.assertNotIn("key", prompt["payload"]["items"][0])

        staff_active = self.staff.get(
            f"/api/live-sessions/{self.live_session_id}/prompts/active"
        ).get_json()
        self.assertEqual(staff_active["prompt"]["payload"]["key"], "A")
        self.assertEqual(len(staff_active["prompt"]["payload"]["items"]), 1)
        self.assertEqual(staff_active["prompt"]["payload"]["items"][0]["key"], "A")
        staff_live = self.staff.get(
            f"/api/live-sessions/{self.live_session_id}/state"
        ).get_json()
        self.assertEqual(
            (staff_live.get("join_prompt") or {}).get("payload", {}).get("prompt"),
            MINDS_ON_PROMPT,
        )
        self.assertEqual(
            (staff_live.get("teams_spark") or {}).get("prompt"),
            TEAMS_SPARK_PROMPT,
        )

        live_prompt = self.student.get("/api/student/live-prompt").get_json()
        self.assertTrue(live_prompt["ok"])
        self.assertTrue(live_prompt["waiting_room"])
        self.assertEqual(live_prompt["prompt"]["payload"]["item_id"], "minds_on")
        self.assertNotIn("key", live_prompt["prompt"]["payload"])

        for field in (
            "key",
            "cement",
            "soft_key",
            "by_choice",
            "on_submit",
            "on_weak",
            "feedback",
        ):
            self.assertNotIn(field, prompt["payload"])

        submit = self.student.post(
            "/api/student/live-prompt/response",
            json={
                "prompt_id": prompt["id"],
                "response": {"choice": MINDS_ON_CHOICES[0]},
            },
        )
        self.assertEqual(submit.status_code, 200, submit.get_json())
        body = submit.get_json()
        self.assertTrue(body.get("ack"))
        self.assertEqual(body["feedback"]["source"], "by_choice")
        self.assertEqual(body["feedback"]["lead"], "Good work.")
        self.assertTrue(body["feedback"]["match"])
        self.assertEqual(
            body["feedback"]["text"],
            "Same step, same change — that’s a constant rate.",
        )
        self.assertEqual(body["my_response"]["feedback"]["text"], body["feedback"]["text"])
        self.assertEqual(body["my_response"]["feedback"]["lead"], "Good work.")
        again = self.student.get("/api/student/live-prompt").get_json()
        self.assertEqual(
            (again.get("prompt") or {}).get("payload", {}).get("item_id"),
            "minds_on",
        )
        moved = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/teacher-state",
            json={"stage": "teams"},
        )
        self.assertEqual(moved.status_code, 200, moved.get_json())
        hidden_spark = self.student.get("/api/student/live-prompt").get_json()
        self.assertIsNone(hidden_spark.get("prompt"))
        self.assertEqual(hidden_spark.get("active_questions") or [], [])
        self._publish_live_item("teams_spark")
        again = self.student.get("/api/student/live-prompt").get_json()
        self.assertEqual(
            (again.get("prompt") or {}).get("payload", {}).get("item_id"),
            "teams-spark",
        )
        self.assertEqual(
            (again.get("prompt") or {}).get("payload", {}).get("prompt"),
            TEAMS_SPARK_PROMPT,
        )
        self.assertIsNone(again.get("my_response"))
        spark_submit = self.student.post(
            "/api/student/live-prompt/response",
            json={
                "prompt_id": again["prompt"]["id"],
                "response": {"value": 7},
            },
        )
        self.assertEqual(spark_submit.status_code, 200, spark_submit.get_json())
        spark_body = spark_submit.get_json()
        self.assertTrue(spark_body.get("ack"))
        self.assertEqual(
            (spark_body.get("my_response") or {}).get("response", {}).get("value"),
            7,
        )
        spark_fb = spark_body.get("feedback")
        self.assertTrue(spark_fb in (None, {}, "") or "feedback" not in spark_body)
        self.assertNotIn("feedback", spark_body.get("my_response") or {})
        after_spark = self.student.get("/api/student/live-prompt").get_json()
        self.assertIsNotNone(after_spark.get("my_response"), after_spark)
        self.assertNotIn("feedback", after_spark.get("my_response") or {})
        tally = after_spark.get("mc_tally")
        self.assertIsNotNone(tally, after_spark)
        tally_choices = tally.get("choices") or []
        self.assertTrue(tally_choices, tally)
        labels = [
            str(row.get("label") or row.get("id") or "")
            for row in tally_choices
            if isinstance(row, dict)
        ]
        self.assertIn("7", labels, tally)
        self.assertGreater(
            int(tally.get("response_count") or tally.get("responded") or 0),
            0,
            tally,
        )

    def test_waiting_room_integer_poll_after_c2_slot(self) -> None:
        """Any Join slot, including C2, keeps Minds-On until Welcome."""
        self._join_maple_home()
        self.school.ensure_live_session_items(self.live_session_id)
        slotted = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/teacher-state",
            json={"live_slot": "C2"},
        )
        self.assertEqual(slotted.status_code, 200, slotted.get_json())
        self.assertEqual(slotted.get_json()["teacher_state"]["live_slot"], "C2")
        idle = self.student.get("/api/student/state").get_json()
        self.assertIsNone(idle.get("prompt"))
        self.assertEqual(idle.get("active_questions") or [], [])
        self._publish_live_item("minds_on")
        state = self.student.get("/api/student/state").get_json()
        prompt = state.get("prompt") or {}
        payload = prompt.get("payload") or {}
        self.assertEqual(payload.get("item_id"), "minds_on")
        self.assertEqual(payload.get("live_slot"), "C2")
        self.assertEqual(payload.get("choices"), list(MINDS_ON_C2_CHOICES))
        submit = self.student.post(
            "/api/student/live-prompt/response",
            json={
                "prompt_id": prompt["id"],
                "response": {"choice": MINDS_ON_C2_CHOICES[0]},
            },
        )
        self.assertEqual(submit.status_code, 200, submit.get_json())
        again = self.student.get("/api/student/live-prompt").get_json()
        payload = (again.get("prompt") or {}).get("payload") or {}
        self.assertEqual(payload.get("item_id"), "minds_on", again)
        moved = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/teacher-state",
            json={"stage": "teams"},
        )
        self.assertEqual(moved.status_code, 200, moved.get_json())
        hidden = self.student.get("/api/student/live-prompt").get_json()
        self.assertIsNone(hidden.get("prompt"), hidden)
        self.assertEqual(hidden.get("active_questions") or [], [])
        self._publish_live_item("teams_spark")
        welcome = self.student.get("/api/student/live-prompt").get_json()
        spark = (welcome.get("prompt") or {}).get("payload") or {}
        self.assertEqual(spark.get("item_id"), "teams-spark", welcome)
        self.assertEqual(spark.get("prompt"), TEAMS_SPARK_PROMPT)

    def test_waiting_room_refreshes_authoritative_stem(self) -> None:
        """Active waiting-room Minds-On updates when the copywriter stem lands."""
        stale = minds_on_prompt_payload()
        stale["prompt"] = "A line has constant rate of change. Which best matches that?"
        stale["choices"] = [
            "Every step up adds the same amount",
            "The graph curves",
            "Second differences are constant",
            "Not sure",
        ]
        self.school.set_live_session_prompt(
            self.live_session_id,
            slide_index=MINDS_ON_SLIDE_INDEX,
            kind="mc",
            payload=stale,
            activate=True,
        )
        self.school.ensure_waiting_room_minds_on(self.live_session_id)
        active = self.school.get_active_live_prompt(self.live_session_id)
        self.assertEqual(active["payload"]["prompt"], MINDS_ON_PROMPT)
        self.assertEqual(active["payload"]["choices"], list(MINDS_ON_CHOICES))
        self.assertEqual(active["payload"]["key"], "A")
        self.assertEqual(len(active["payload"]["items"]), 1)

    def test_join_teacher_preview_media_keeps_minds_on(self) -> None:
        """Teacher Real-slice seed on JOIN does not project media or drop Minds-On."""
        from live_media import DEFAULT_LIVE_MEDIA_URL

        self._use_legacy_live_metadata()
        self._join_maple_home()
        idle = self.student.get("/api/student/state").get_json()
        self.assertEqual(idle["prompt"]["payload"]["item_id"], "minds_on")
        self.assertNotIn("ax^2", str(idle["prompt"]["payload"]).lower())

        posted = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/active-media",
            json={"url": DEFAULT_LIVE_MEDIA_URL},
        )
        self.assertEqual(posted.status_code, 200, posted.get_json())
        state = self.student.get("/api/student/state").get_json()
        self.assertTrue(state.get("waiting_room"), state)
        self.assertEqual(state["active_media"]["url"], DEFAULT_LIVE_MEDIA_URL)
        self.assertFalse(state["teacher_state"]["student_frames"]["media"])
        prompt = state.get("prompt")
        self.assertIsNotNone(prompt)
        self.assertTrue(is_minds_on_payload((prompt or {}).get("payload")))

    def test_assigned_team_list_is_names_and_avatars_only(self) -> None:
        """After Generate teams, state.my_team is names + avatars, no scores or moods."""
        self._join_maple_home()
        before = self.student.get("/api/student/state").get_json()
        self.assertIsNone(before.get("my_team"))
        self._staff_assign_two_teams()
        after = self.student.get("/api/student/state").get_json()
        mine = after.get("my_team")
        self.assertIsInstance(mine, dict)
        self.assertTrue(str(mine.get("name") or "").strip())
        self.assertNotEqual(str(mine.get("name") or "").strip(), "Class")
        members = mine.get("members") or []
        self.assertGreaterEqual(len(members), 1)
        names = {str(row.get("codename") or "") for row in members}
        self.assertIn("Maple", names)
        for row in members:
            self.assertEqual(set(row), {"id", "codename", "character"})
            self.assertNotIn("mood", row)
            self.assertNotIn("points", row)
            self.assertNotIn("session_points", row)
        maple = next(row for row in members if row["codename"] == "Maple")
        self.assertEqual(maple["character"], "fox")

    def test_assign_and_meet_teams_swap_minds_on_for_teammate_warmup(self) -> None:
        """SID=18: Generate teams + Meet Teams leave waiting-room Minds-On."""
        self._use_legacy_live_metadata()
        self._join_maple_home()
        idle = self.student.get("/api/student/live-prompt").get_json()
        self.assertTrue(idle["waiting_room"])
        self.assertEqual(idle["prompt"]["payload"]["item_id"], "minds_on")

        self._staff_assign_two_teams()
        meet = self.staff.post(
            f"/api/classes/{self.class_id}/game/meet-teams",
            json={"minutes": 3},
        )
        self.assertEqual(meet.status_code, 200, meet.get_json())
        live_prompt = self.student.get("/api/student/live-prompt").get_json()
        self.assertTrue(live_prompt["ok"])
        self.assertFalse(live_prompt["waiting_room"])
        payload = live_prompt["prompt"]["payload"]
        self.assertEqual(payload["item_id"], "meet-team")
        self.assertEqual(payload["step"], "A")
        self.assertEqual(payload["chain"], ["A", "C", "B"])
        self.assertEqual(payload["prompt"], MEET_TEAM_PROMPT)
        self.assertEqual(len(payload["choices"]), 5)
        for fixed in MEET_TEAM_FIXED_CHOICES:
            self.assertIn(fixed, payload["choices"])
        extra = [
            choice
            for choice in payload["choices"]
            if choice not in MEET_TEAM_FIXED_CHOICES
        ]
        self.assertEqual(len(extra), 2)
        for choice in extra:
            self.assertIn(choice, MEET_TEAM_WARMUP_POOL)
        self.assertFalse(is_minds_on_payload(payload))

    def test_minds_on_clears_when_scoring_starts(self) -> None:
        """Start-rounds (live scoring) keeps Join C1 until the student answers."""
        self._use_legacy_live_metadata()
        self._join_maple_home()
        idle = self.student.get("/api/student/state").get_json()
        self.assertEqual(idle["prompt"]["payload"]["item_id"], "minds_on")

        self._staff_assign_two_teams()
        assigned = self.school.game.game_state(self.class_id)
        teams = [
            {"id": t["id"], "name": t["name"]}
            for t in assigned["teams"]
        ]
        self.staff.post(
            f"/api/classes/{self.class_id}/game/rename",
            json={"teams": teams, "go_live": False},
        )
        live = self.staff.post(
            f"/api/classes/{self.class_id}/game/start-rounds",
            json={"rounds": [{"kind": "challenge", "minutes": 15}]},
        )
        self.assertEqual(live.status_code, 200, live.get_json())
        self.assertEqual(live.get_json()["game"]["status"], "live")

        state = self.student.get("/api/student/state").get_json()
        self.assertTrue(state.get("scoring"))
        self.assertFalse(state.get("waiting_room"))
        prompt = state.get("prompt")
        self.assertTrue(is_minds_on_payload((prompt or {}).get("payload")))

    def test_waiting_room_js_has_no_start_scoring_copy(self) -> None:
        """Student portal JS must not use the scoring-phase wait line in waiting-room."""
        js = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn("formatQuestionHtml", js)
        self.assertNotIn("formatPromptHtml", js)
        self.assertIn("Waiting room — class is about to begin.", js)
        self.assertIn("studentProjection", js)
        self.assertIn("unmountStudentMedia", js)
        self.assertIn("lastStateSeq", js)
        self.assertIn("function isJoinMindsOnPrompt(", js)
        self.assertIn("function isJoinMindsOnPrompt(", js)
        self.assertIn("function studentMeetPollsHtml(", js)
        meet_polls = js.split("function studentMeetPollsHtml(")[1].split("function showFeedbackPanel(")[0]
        self.assertIn("item.show_live_results === false", meet_polls)
        self.assertNotIn("meet-progress-dots", js)
        self.assertNotIn("Waiting for your teacher to start scoring.", js)
        self.assertNotIn("meet-math", js)
        self.assertIn('["cue.meet_open", "cue.meet_clear"]', js)
        self.assertIn("cue.teams_spark", js)
        self.assertIn("Enter an integer…", js)
        self.assertNotIn("cue.meet_a", js)
        self.assertNotIn("cue.meet_b", js)
        self.assertNotIn("carousel", js.lower())
        self.assertNotIn("data.items", js)
        self.assertNotIn("payload.items", js)
        html = (LMS_DIR / "templates" / "student" / "home.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("Waiting room — class is about to begin.", html)
        self.assertNotIn("Waiting for your teacher to start scoring.", html)
        self.assertNotIn("meet-math", html)
        self.assertIn("feedbackObject", js)
        self.assertIn("fb.text", js)
        self.assertIn("showFeedbackPanel", js)
        self.assertIn("dismissFeedbackPanel", js)
        self.assertIn("hideFeedbackPanel", js)
        self.assertIn("Escape", js)
        self.assertIn("Good work.", js)
        self.assertNotIn("Wrong.", js)
        self.assertNotIn("Wrong.", html)
        css = (LMS_DIR / "static" / "student-portal.css").read_text(encoding="utf-8")
        self.assertIn(".prompt-feedback", css)
        self.assertIn(".question-frame", css)
        self.assertIn("max-height: min(88dvh, 52rem)", css)
        self.assertIn("font-size: 0.86rem", css)
        self.assertIn("font-size: 0.69rem", css)
        self.assertIn("font-size: 0.75rem", css)
        self.assertIn("Choose an answer before submitting.", js)
        self.assertIn("Enter a number before submitting.", js)
        self.assertIn("Could not submit that answer.", js)
        self.assertIn("function submitPromptShellAnswer(", js)
        self.assertIn("saved_cards", js)
        self.assertIn("function studentMcSummary(", js)
        self.assertNotIn("Your answer:", js)
        self.assertIn("item.show_live_results === false", js.split("function paintPollIfQuestionsVisible(")[1].split("function hidePollTotals(")[0])
        self.assertIn("function mcRevealBarsHtml(", js)
        self.assertIn("student-mc-reveal-bars", js)
        self.assertIn(".question-frame .mc-reveal-row", css)

    def test_join_reveal_closes_poll_and_shares_summary(self) -> None:
        """Beat 10: JOIN Reveal closes submits and binds the class MC tally."""
        from live_teacher_state import MINDS_ON_PROMPT_REF

        self._use_legacy_live_metadata()
        self._join_maple_home()
        aspen = self.app.test_client()
        live = self.school.get_live_session(self.live_session_id)
        aspen.post(
            "/auth/student-code",
            data={"code": live["session_code"], "name": "Aspen"},
            follow_redirects=False,
        )
        aspen.post("/student/mood", data={"mood": "good"})
        aspen.post("/student/character", data={"character": "fox"})
        maple = self.student.get("/api/student/state").get_json()
        prompt = maple["prompt"]
        self.assertEqual(prompt["payload"]["item_id"], "minds_on")
        self.assertNotIn("mc_tally", maple)
        self.assertFalse(maple.get("poll_closed"))
        submit = self.student.post(
            "/api/student/live-prompt/response",
            json={
                "prompt_id": prompt["id"],
                "response": {"choice": MINDS_ON_CHOICES[0]},
            },
        )
        self.assertEqual(submit.status_code, 200, submit.get_json())
        submitted = submit.get_json()
        self.assertIsNotNone(submitted.get("mc_tally"), submitted)
        self.assertIsNotNone((submitted.get("feedback") or submitted.get("my_response") or {}).get("text") or (submitted.get("my_response") or {}).get("feedback"), submitted)
        after_answer = self.student.get("/api/student/state").get_json()
        self.assertEqual(
            (after_answer.get("prompt") or {}).get("payload", {}).get("item_id"),
            "minds_on",
            after_answer,
        )
        unanswered_before_reveal = aspen.get("/api/student/state").get_json()
        self.assertNotIn("mc_tally", unanswered_before_reveal)
        shown = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/teacher-state",
            json={
                "mc_ui": {
                    "prompt_ref": MINDS_ON_PROMPT_REF,
                    "reveal": True,
                }
            },
        )
        self.assertEqual(shown.status_code, 200, shown.get_json())
        ui = shown.get_json()["teacher_state"]["mc_ui"]
        self.assertTrue(ui["reveal"])
        self.assertTrue(ui["reveal_to_students"])
        self.assertTrue(ui["poll_closed"])
        self.assertIsNone(shown.get_json()["teacher_state"].get("cue_id"))
        shared = self.student.get("/api/student/state").get_json()
        self.assertEqual(
            (shared.get("prompt") or {}).get("payload", {}).get("item_id"),
            "minds_on",
        )
        self.assertTrue(shared.get("poll_closed"))
        unanswered = aspen.get("/api/student/state").get_json()
        self.assertTrue(unanswered.get("poll_closed"))
        self.assertIsNotNone(unanswered.get("mc_tally"))
        self.assertEqual(unanswered["mc_tally"]["choices"][0]["pct"], 100)
        blocked_maple = self.student.post(
            "/api/student/live-prompt/response",
            json={
                "prompt_id": prompt["id"],
                "response": {"choice": MINDS_ON_CHOICES[1]},
            },
        )
        self.assertEqual(blocked_maple.status_code, 409, blocked_maple.get_json())
        blocked_aspen = aspen.post(
            "/api/student/live-prompt/response",
            json={
                "prompt_id": prompt["id"],
                "response": {"choice": MINDS_ON_CHOICES[1]},
            },
        )
        self.assertEqual(blocked_aspen.status_code, 409, blocked_aspen.get_json())
        self.assertTrue(blocked_aspen.get_json().get("poll_closed"))

    def test_generic_mc_submit_has_no_feedback(self) -> None:
        """A staff MC that is not Minds-On / CONS returns ack only."""
        self._use_legacy_live_metadata()
        self._join_maple_home()
        set_prompt = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/prompts",
            json={
                "slide_index": 2,
                "kind": "mc",
                "payload": {"prompt": "Pick one", "choices": ["A", "B"]},
            },
        )
        self.assertEqual(set_prompt.status_code, 200, set_prompt.get_json())
        prompt = set_prompt.get_json()["prompt"]
        submit = self.student.post(
            "/api/student/live-prompt/response",
            json={"prompt_id": prompt["id"], "response": {"choice": "B"}},
        )
        self.assertEqual(submit.status_code, 200, submit.get_json())
        body = submit.get_json()
        self.assertTrue(body.get("ack"))
        self.assertNotIn("feedback", body)
        self.assertNotIn("feedback", body.get("my_response") or {})

    def test_minds_on_miss_uses_soft_lead(self) -> None:
        """Incorrect Minds-On choice returns the Wonder miss lead, never Wrong."""
        self._use_legacy_live_metadata()
        self._join_maple_home()
        prompt = self.student.get("/api/student/state").get_json()["prompt"]
        submit = self.student.post(
            "/api/student/live-prompt/response",
            json={
                "prompt_id": prompt["id"],
                "response": {"choice": MINDS_ON_CHOICES[1]},
            },
        )
        self.assertEqual(submit.status_code, 200, submit.get_json())
        body = submit.get_json()
        self.assertEqual(body["feedback"]["lead"], LEAD_MISS)
        self.assertEqual(
            body["feedback"]["text"],
            "A curve changes steepness as you go. Constant rate stays even.",
        )
        self.assertFalse(body["feedback"]["match"])
        self.assertNotEqual(body["feedback"]["lead"], "Wrong.")

    def test_beat6_feedback_panel_lives_in_question_frame(self) -> None:
        """Panel markup sits in the Question frame, not ClassList or ResultsStrip."""
        html = (LMS_DIR / "templates" / "student" / "home.html").read_text(
            encoding="utf-8"
        )
        frame_i = html.index('id="question-frame"')
        shell_i = html.index('id="prompt-shell"')
        panel_i = html.index('id="prompt-feedback"')
        close_i = html.index('id="prompt-feedback-close"')
        board_i = html.index('id="class-board"')
        self.assertLess(frame_i, shell_i)
        self.assertLess(shell_i, panel_i)
        self.assertLess(panel_i, close_i)
        self.assertLess(close_i, board_i)
        self.assertIn('aria-label="Close feedback">×<', html)
        self.assertIn('id="prompt-dismiss"', html)
        self.assertNotIn("You can Close whenever you’re ready.", html)
        self.assertNotIn('id="prompt-feedback-helper"', html)
        self.assertNotIn("Wrong.", html)
        self.assertNotIn('id="results-strip"', html)
        js = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn('getElementById("question-frame")', js)
        self.assertIn("dismissedPromptIds.has(promptId)", js)
        self.assertIn('proj.stage === "meet" && Boolean(ts.meet_chain)', js)
        self.assertIn("function showFeedbackPanel(", js)
        self.assertIn("function dismissFeedbackPanel()", js)
        dismiss = js.split("function dismissFeedbackPanel()")[1].split(
            "function hideFeedbackPanel()"
        )[0]
        self.assertIn("holdJoinFeedback = false", dismiss)
        self.assertIn("tick()", dismiss)
        self.assertIn("holdJoinFeedback = true", js)
        self.assertIn("holdJoinFeedback && !isJoinMindsOnPrompt(payload)", js)
        self.assertIn("if (answered) {", js)
        self.assertIn("paintPollIfQuestionsVisible(payload)", js)
        self.assertIn("bindFloatingPane(mediaPane)", js)
        self.assertIn('event.key === "Escape"', js)
        self.assertNotIn("innerHTML = feedback", js)

    def test_projected_panes_have_visible_bounded_resize_handles(self) -> None:
        """Media, Canvas, and Slides expose pointer resize controls."""
        html = (LMS_DIR / "templates" / "student" / "home.html").read_text(
            encoding="utf-8"
        )
        js = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        css = (LMS_DIR / "static" / "student-portal.css").read_text(
            encoding="utf-8"
        )
        for pane in ("media", "canvas", "slides"):
            self.assertIn(f'data-pane-resize="{pane}"', html)
        self.assertIn('aria-label="Resize media"', html)
        self.assertIn('aria-label="Resize whiteboard"', html)
        self.assertIn('aria-label="Resize slides"', html)
        self.assertIn("function floatPaneAtCurrentPosition(", js)
        self.assertIn("host.appendChild(pane)", js)
        self.assertIn("is-pane-dragging", css)
        self.assertIn('pane.querySelector("[data-pane-resize]")', js)
        self.assertIn("hostRect.width - resizeDrag.left", js)
        self.assertIn("hostRect.height - resizeDrag.top", js)
        self.assertIn("cursor: nwse-resize;", css)

    def test_ordered_lifecycle_cards_and_consensus_controls_are_wired(self) -> None:
        """Students get dismissible multi-question and private team flows."""

        html = (LMS_DIR / "templates" / "student" / "home.html").read_text(
            encoding="utf-8"
        )
        js = (LMS_DIR / "static" / "student-portal.js").read_text(
            encoding="utf-8"
        )
        css = (LMS_DIR / "static" / "student-portal.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".artifact-parents", css)
        self.assertIn(".artifact-parents-label", css)
        self.assertIn('id="live-question-stack"', html)
        self.assertIn('id="student-question-dock"', html)
        self.assertIn(">Whiteboard<", html)
        self.assertNotIn(">Canvas<", html)
        self.assertIn("payload?.active_questions", js)
        self.assertIn("payload?.closed_results", js)
        self.assertIn("dockedLiveCardKeys", js)
        self.assertIn("dismissedLiveCardKeys", js)
        self.assertIn('aria-label="Dismiss question"', js)
        self.assertNotIn('aria-label="Dock question"', js)
        self.assertNotIn("Pop question back out", js)
        self.assertIn("student-question-dock", js)
        self.assertIn("paintQuestionDock", js)
        self.assertIn("surface:media", js)
        self.assertIn('kind: "whiteboard"', js)
        self.assertIn("is-${row.kind}", js)
        self.assertIn("bindFloatingPane(card)", js)
        self.assertNotIn("bindFloatingPane(liveQuestionStack)", js)
        self.assertIn("function lifecycleAnswerKind", js)
        self.assertIn('return "artifact"', js)
        self.assertIn("data-artifact-kind", js)
        self.assertIn("function parentChoiceRadiosHtml", js)
        self.assertIn("Parent function", js)
        self.assertIn("artifact-parents-label", js)
        self.assertIn("data-artifact-parent", js)
        self.assertIn("function liveChoiceLabels", js)
        self.assertIn("payload?.meet_chip", js)
        self.assertIn("content.integer_only", js)
        self.assertIn("drag.pending", js)
        self.assertIn("const activePaneDrags = new Set()", js)
        self.assertIn("host.appendChild(pane)", js)
        self.assertIn("grabX:", js)
        self.assertIn("is-pane-dragging", js)
        self.assertIn("function paneIsBeingGrabbed(", js)
        self.assertIn("if (wasPending) return", js)
        self.assertIn("data-dismiss-surface", html)
        self.assertIn('aria-label="Dock media"', html)
        self.assertIn('aria-label="Dock whiteboard"', html)
        self.assertIn("Submit Group Answer", js)
        self.assertIn("teammates have responded", js)
        self.assertIn("GROUP ANSWER SENT", js)
        self.assertIn("Team Answer", js)
        self.assertIn("/api/student/live-items/${itemId}/vote", js)
        self.assertIn("/api/student/live-items/${itemId}/team-answer", js)
        self.assertIn("TEAM RESPONSES", js)
        self.assertIn("Class team answers", js)
        self.assertIn(".live-question-stack", css)
        self.assertIn("position: absolute", css.split(".live-question-stack {")[1].split("}")[0])
        self.assertIn("right: 0.7rem", css.split(".live-question-stack {")[1].split("}")[0])
        self.assertIn("function namedScoreboardTeams(", js)
        self.assertIn("function promptAsLifecycleItem(", js)
        self.assertIn("function isLeftoverJoinMindsOnCard(", js)
        self.assertIn("function isArtifactLifecycleItem(", js)
        self.assertIn("formatQuestionHtml(", js)
        self.assertNotIn("formatPromptHtml", js)
        self.assertIn("data-live-prompt-id", js)
        self.assertIn("promptIdFromCard", js)
        self.assertIn(".student-team-distribution", css)

    def test_student_metadata_strips_answer_keys_from_items(self) -> None:
        """Student metadata never leaks catalogue keys before a team vote."""

        self._join_maple_home()
        payload = self.student.get("/api/student/state").get_json()
        metadata = payload.get("live_metadata") or {}
        for collection in ("questions", "items"):
            for item in metadata.get(collection) or []:
                self.assertNotIn("correct_answer", item)
                self.assertNotIn("key", item)
                self.assertNotIn("soft_key", item)
                self.assertNotIn("by_choice", item)

    def test_student_payload_strips_keyed_numeric_answer(self) -> None:
        """Published keyed numeric prompts never leak correct_answer or key."""

        numeric = {
            "id": "evaluate-f2",
            "ref": "test/question/evaluate-f2",
            "item_type": "question",
            "stage": "join",
            "page_number": 1,
            "order": 1,
            "type": "numeric",
            "text": "Evaluate f(2)",
            "options": [],
            "correct_answer": "-2",
            "integer_only": True,
            "placeholder": "Enter a number",
            "default_status": "inactive",
            "publish_modes": ["individual"],
            "response_mode": "individual",
        }
        self.school.live_class_metadata_for_session = lambda _sid: {
            "schema_version": 2,
            "course": "MCF3M",
            "module": "M1",
            "live_class": "C1",
            "questions": [numeric],
            "items": [numeric],
            "media": None,
            "slides": {"deck_ref": None, "page_numbers": []},
            "round_defaults": {},
        }
        self._join_maple_home()
        items = self.school.ensure_live_session_items(self.live_session_id)
        row = next(item for item in items if item["item_id"] == "evaluate-f2")
        self.school.publish_live_session_item(
            self.live_session_id, int(row["id"]), publish_mode="individual"
        )
        payload = self.student.get("/api/student/state").get_json()
        metadata = payload.get("live_metadata") or {}
        for collection in ("questions", "items"):
            for item in metadata.get(collection) or []:
                self.assertNotIn("correct_answer", item)
                self.assertNotIn("key", item)
        for item in payload.get("active_questions") or []:
            content = item.get("content") or {}
            prompt_body = (item.get("prompt") or {}).get("payload") or {}
            self.assertNotIn("correct_answer", content)
            self.assertNotIn("key", content)
            self.assertNotIn("correct_answer", prompt_body)
            self.assertNotIn("key", prompt_body)
        live_prompt = self.student.get("/api/student/live-prompt").get_json()
        prompt_payload = ((live_prompt.get("prompt") or {}).get("payload") or {})
        self.assertNotIn("correct_answer", prompt_payload)
        self.assertNotIn("key", prompt_payload)

    def test_beat32_save_work_sits_under_name_row(self) -> None:
        """Beat 32: Save View is under the name row, not timer or Question."""
        html = (LMS_DIR / "templates" / "student" / "home.html").read_text(
            encoding="utf-8"
        )
        js = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        css = (LMS_DIR / "static" / "student-portal.css").read_text(encoding="utf-8")
        self.assertLess(html.index('id="me-display-time"'), html.index('id="me-board"'))
        self.assertLess(html.index('id="me-name-row"'), html.index('id="me-save-slot"'))
        self.assertLess(html.index('id="me-avatar"'), html.index('id="me-name"'))
        self.assertLess(html.index('id="me-save-slot"'), html.index('id="save-work"'))
        self.assertLess(html.index('id="save-work"'), html.index('id="me-stats"'))
        self.assertLess(html.index('id="me-stats"'), html.index('id="me-team-list"'))
        self.assertLess(html.index('id="me-board"'), html.index('id="me-team-list"'))
        self.assertLess(html.index('id="me-team-list"'), html.index('id="student-round-banner"'))
        self.assertLess(html.index('id="me-stats"'), html.index('id="student-round-banner"'))
        self.assertGreater(html.index('id="save-work"'), html.index('id="me-board"'))
        self.assertLess(html.index('id="save-work"'), html.index('id="question-frame"'))
        self.assertLess(html.index('id="save-work"'), html.index('id="media-pane"'))
        self.assertLess(html.index('id="save-work"'), html.index('id="class-board"'))
        self.assertNotIn('id="me-card-footer"', html)
        self.assertIn(">Save View<", html)
        self.assertNotIn("Saved to your downloads.", html)
        chrome = html[html.index('id="me-board"') : html.index('id="student-round-banner"')]
        self.assertIn('id="save-work"', chrome)
        self.assertNotIn('id="save-work"', html[html.index("student-chrome-bottom") :])
        self.assertNotIn('id="save-work"', html[html.index('id="me-display-time"') : html.index('id="me-board"')])
        for name in ("join.html", "mood.html", "character.html", "pick.html", "waiting.html"):
            page = (LMS_DIR / "templates" / "student" / name).read_text(
                encoding="utf-8"
            )
            self.assertNotIn("save-work", page)
            self.assertNotIn("Save Work", page)
            self.assertNotIn("Save View", page)
        paint = js.split("function paintMe(")[1].split("function paintMyTeam(")[0]
        self.assertNotIn("innerHTML", paint)
        self.assertNotIn("save-work", paint)
        self.assertIn("meAvatarEl.textContent", paint)
        self.assertIn("avatarGlyph", paint)
        self.assertIn("meNameEl.textContent", paint)
        self.assertIn("function saveStudentWork()", js)
        self.assertIn("Saved to your downloads.", js)
        self.assertIn("Nothing to save yet.", js)
        self.assertIn("live-class-view.png", js)
        self.assertIn("function collectViewCanvases(", js)
        self.assertIn("captureMediaFrame(mediaFrame)", js)
        self.assertIn('canvas.nodeName !== "CANVAS"', js)
        self.assertIn("function captureEntireStudentView(", js)
        self.assertIn("prompt-poll-totals", js)
        home = (LMS_DIR / "templates" / "student" / "home.html").read_text(encoding="utf-8")
        self.assertIn('id="prompt-poll-totals"', home)
        self.assertIn('id="student-canvas-undo"', home)
        self.assertNotIn("JSZip", js)
        self.assertIn(".student-me .me-save-slot {", css)
        self.assertIn(".student-me .save-work {", css)
        self.assertIn(".me-team-list {", css)
        self.assertIn("function paintMyTeam(", js)
        self.assertIn("paintMyTeam(data)", js)
        paint_team = js.split("function paintMyTeam(")[1].split("function showSaveWorkToast(")[0]
        self.assertIn("nameWithAvatar", paint_team)
        self.assertNotIn("mood", paint_team)
        self.assertNotIn("points", paint_team)
        self.assertNotIn("score", paint_team)
        self.assertIn('id="student-winner-name"', html)
        self.assertIn('id="student-winner-players"', html)
        self.assertIn("payload.celebrate", js)
        self.assertIn("Waiting for the next question…", js)
        self.assertIn("celebrating || welcomeOn", js)
        self.assertNotIn("iframeOwnsAsk", js)

    def test_teams_welcome_screen_is_wired_on_student_home(self) -> None:
        """TEAMS clears the student prompt face and paints the VLC welcome card."""
        html = (LMS_DIR / "templates" / "student" / "home.html").read_text(
            encoding="utf-8"
        )
        js = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        css = (LMS_DIR / "static" / "student-portal.css").read_text(encoding="utf-8")
        self.assertIn('id="game-show-welcome"', html)
        self.assertLess(html.index('id="student-wait"'), html.index('id="game-show-welcome"'))
        self.assertLess(html.index('id="game-show-welcome"'), html.index('id="question-frame"'))
        self.assertIn("function paintGameShowWelcome(", js)
        self.assertIn("game_show_welcome", js)
        self.assertIn("VLC Math Game Show", js)
        self.assertIn("nameWithAvatar", js)
        self.assertIn(".game-show-welcome", css)
        self.assertIn("gs-orbit", css)
        self.assertIn("body.student-home.is-game-show-welcome .live-question-stack", css)
        self.assertNotIn(
            "body.student-home.is-game-show-welcome .live-question-stack,",
            css,
        )

    def test_overlay_and_student_names_use_avatars_not_moods(self) -> None:
        """Live overlay + student home paint avatars left of names, never mood glyphs."""
        overlay_js = (
            LMS_DIR.parent / "tools" / "math-game-show" / "static" / "live_session_overlay.js"
        ).read_text(encoding="utf-8")
        avatars = (LMS_DIR / "static" / "student_avatars.js").read_text(encoding="utf-8")
        portal = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn("nameWithAvatar", overlay_js)
        self.assertIn("function rosterStudentId(", overlay_js)
        self.assertIn("row?.student_id ?? row?.id", overlay_js)
        self.assertIn("namedGroups.length ? namedGroups : boardTeams", overlay_js)
        self.assertIn("student_avatars.js", overlay_js)
        self.assertNotIn("moodGlyph", overlay_js)
        self.assertNotIn("mood_faces.js", overlay_js)
        self.assertIn("avatarGlyph", avatars)
        self.assertIn("nameWithAvatar", avatars)
        self.assertIn("student_avatars.js", portal)
        self.assertIn("avatarGlyph", portal)

    def test_student_state_tick_sends_seq_and_handles_unchanged(self) -> None:
        """Student poll sends seq/stamp and returns early when unchanged."""

        portal = (Path(__file__).resolve().parent / "static" / "student-portal.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('params.set("seq"', portal)
        self.assertIn('params.set("stamp"', portal)
        self.assertIn("data.unchanged", portal)
        self.assertIn("lastPollStamp", portal)

    def test_c2_join_mc_submit_is_not_leftover_minds_on(self) -> None:
        """MCF3M M1 C2 page-1 Submit records function-notation, not leftover minds-on."""

        self._join_maple_home()
        idle = self.student.get("/api/student/state").get_json()
        self.assertIsNone(idle.get("prompt"), idle)
        self.assertEqual(idle.get("active_questions") or [], [])
        slotted = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/teacher-state",
            json={"live_module": "M1", "live_slot": "C2"},
        )
        self.assertEqual(slotted.status_code, 200, slotted.get_json())
        items = self.school.ensure_live_session_items(self.live_session_id)
        notation = next(
            row
            for row in items
            if str(row.get("item_id") or "") == "function-notation"
        )
        self.school.publish_live_session_item(
            self.live_session_id, int(notation["id"]), publish_mode="individual"
        )
        state = self.student.get("/api/student/state").get_json()
        active_ids = [
            str(row.get("item_id") or "")
            for row in state.get("active_questions") or []
        ]
        self.assertNotIn("minds_on", active_ids)
        self.assertIn("function-notation", active_ids)
        card = next(
            row
            for row in state["active_questions"]
            if row.get("item_id") == "function-notation"
        )
        choice = "the output of rule f when the input is x"
        submit = self.student.post(
            "/api/student/live-prompt/response",
            json={
                "prompt_id": int((card.get("prompt") or {})["id"]),
                "response": {"choice": choice},
            },
        )
        self.assertEqual(submit.status_code, 200, submit.get_json())
        after = self.student.get("/api/student/state").get_json()
        answered = next(
            row
            for row in after.get("active_questions") or []
            if row.get("item_id") == "function-notation"
        )
        self.assertEqual(
            (answered.get("my_response") or {}).get("response", {}).get("choice"),
            choice,
        )
        facing_id = str(
            ((after.get("prompt") or {}).get("payload") or {}).get("item_id") or ""
        )
        self.assertEqual(facing_id, "function-notation", after)


    def test_mcf3m_m1_c2_every_slide_submit_via_student_api(self) -> None:
        """Every MCF3M M1 C2 answerable slide accepts Submit via the student API."""

        from live_class_metadata import load_live_class_metadata

        self._join_maple_home()
        slotted = self.staff.post(
            f"/api/live-sessions/{self.live_session_id}/teacher-state",
            json={"live_module": "M1", "live_slot": "C2"},
        )
        self.assertEqual(slotted.status_code, 200, slotted.get_json())
        items = self.school.ensure_live_session_items(self.live_session_id)
        answerable = [
            row
            for row in items
            if str(row.get("kind") or "").strip().lower()
            not in {"media", "whiteboard", "slides"}
            and str((row.get("item") or {}).get("item_type") or "").strip().lower()
            not in {"media", "whiteboard", "slides"}
        ]
        self.assertTrue(answerable, items)
        recorded: list[str] = []
        for row in answerable:
            item_id = str(row.get("item_id") or "")
            stage = str(row.get("stage") or "join").strip().lower() or "join"
            self.staff.post(
                f"/api/live-sessions/{self.live_session_id}/teacher-state",
                json={"stage": stage},
            )
            if item_id.replace("-", "_") == "meet_team":
                self.school.set_live_session_teacher_state(
                    self.live_session_id, stage="meet"
                )
                self.school.activate_meet_team_question(self.live_session_id)
            pub = self.staff.post(
                f"/api/live-sessions/{self.live_session_id}/items/{int(row['id'])}/publish",
                json={"publish_mode": "individual"},
            )
            self.assertEqual(pub.status_code, 200, pub.get_json())
            state = self.student.get("/api/student/state").get_json()
            active_ids = [
                str(item.get("item_id") or "")
                for item in state.get("active_questions") or []
            ]
            self.assertIn(item_id, active_ids, state)
            card = next(
                item
                for item in state["active_questions"]
                if str(item.get("item_id") or "") == item_id
            )
            self.assertTrue(card.get("can_submit"), card)
            prompt_id = int((card.get("prompt") or {})["id"])
            facing_id = str(
                ((state.get("prompt") or {}).get("payload") or {}).get("item_id") or ""
            )
            if item_id.replace("-", "_") not in {"minds_on", "meet_team", "teams_spark"}:
                self.assertNotIn("minds_on", facing_id.replace("-", "_"), state)
                self.assertIn(
                    prompt_id,
                    [
                        int((row.get("prompt") or {}).get("id") or 0)
                        for row in state.get("active_questions") or []
                    ],
                    state,
                )
            content = card.get("content") or {}
            prompt_body = (card.get("prompt") or {}).get("payload") or {}
            options = (
                prompt_body.get("choices")
                or prompt_body.get("options")
                or content.get("options")
                or []
            )
            if item_id.replace("-", "_") == "evaluate_f2" or str(
                content.get("type") or prompt_body.get("kind") or ""
            ).lower() == "numeric":
                response = {"value": -2}
            elif options:
                first = options[0]
                response = {
                    "choice": first
                    if isinstance(first, str)
                    else str(first.get("label") or first.get("text") or first.get("choice") or "")
                }
            else:
                response = {"choice": "notices details"}
            submit = self.student.post(
                "/api/student/live-prompt/response",
                json={"prompt_id": prompt_id, "response": response},
            )
            self.assertEqual(submit.status_code, 200, submit.get_json())
            after = self.student.get("/api/student/state").get_json()
            answered = next(
                item
                for item in after.get("active_questions") or []
                if str(item.get("item_id") or "") == item_id
            )
            mine = (answered.get("my_response") or {}).get("response") or {}
            if "value" in response:
                self.assertEqual(mine.get("value"), response["value"], answered)
            else:
                self.assertEqual(mine.get("choice"), response["choice"], answered)
            recorded.append(item_id)
        self.assertEqual(len(recorded), len(answerable))


if __name__ == "__main__":
    unittest.main(verbosity=2)

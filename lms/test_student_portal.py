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
from meet_team import (  # noqa: E402
    MEET_TEAM_FIXED_CHOICES,
    MEET_TEAM_PROMPT,
    MEET_TEAM_WARMUP_POOL,
    is_meet_team_payload,
)
from minds_on import (  # noqa: E402
    MINDS_ON_CHOICES,
    MINDS_ON_PROMPT,
    MINDS_ON_SLIDE_INDEX,
    is_minds_on_payload,
    minds_on_prompt_payload,
)


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

    def test_join_mood_character_home_and_show_rank(self) -> None:
        """Join → mood → character → home; rank hidden until enabled."""
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

        state = self.student.get("/api/student/state")
        self.assertEqual(state.status_code, 200)
        payload = state.get_json()
        self.assertTrue(payload["ok"])
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
        self.assertEqual(state["prompt"]["id"], prompt["id"])
        self.assertIsNone(state.get("my_response"))

        submit = self.student.post(
            "/api/student/live-prompt/response",
            json={"prompt_id": prompt["id"], "response": {"choice": "B"}},
        )
        self.assertEqual(submit.status_code, 200, submit.get_json())
        self.assertTrue(submit.get_json().get("ack"))

        again = self.student.get("/api/student/live-prompt").get_json()
        self.assertEqual(again["my_response"]["response"]["choice"], "B")

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

        live_prompt = self.student.get("/api/student/live-prompt").get_json()
        self.assertTrue(live_prompt["ok"])
        self.assertTrue(live_prompt["waiting_room"])
        self.assertEqual(live_prompt["prompt"]["payload"]["item_id"], "minds_on")
        self.assertNotIn("key", live_prompt["prompt"]["payload"])

        for field in ("key", "cement", "soft_key", "by_choice", "on_submit", "feedback"):
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
        self.assertEqual(
            body["feedback"]["text"],
            "Same step, same change — that’s a constant rate.",
        )
        self.assertEqual(body["my_response"]["feedback"]["text"], body["feedback"]["text"])
        again = self.student.get("/api/student/live-prompt").get_json()
        self.assertEqual(
            again["my_response"]["response"]["choice"],
            MINDS_ON_CHOICES[0],
        )
        self.assertEqual(
            again["my_response"]["feedback"]["text"],
            body["feedback"]["text"],
        )
        for field in ("key", "cement", "soft_key", "by_choice", "on_submit"):
            self.assertNotIn(field, again["prompt"]["payload"])

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

    def test_minds_on_clears_when_challenge_media_mounts(self) -> None:
        """Real-slice / active_media replaces Minds-On; it is not the stem."""
        from live_media import DEFAULT_LIVE_MEDIA_URL

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
        self.assertFalse(state.get("waiting_room"))
        self.assertEqual(state["active_media"]["url"], DEFAULT_LIVE_MEDIA_URL)
        prompt = state.get("prompt")
        if prompt is not None:
            self.assertFalse(is_minds_on_payload(prompt.get("payload")))
            self.assertFalse(is_meet_team_payload(prompt.get("payload")))

    def test_assign_and_meet_teams_swap_minds_on_for_teammate_warmup(self) -> None:
        """SID=18: Generate teams + Meet Teams leave waiting-room Minds-On."""
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
        """Start-rounds (live scoring) drops waiting-room Minds-On."""
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
        if prompt is not None:
            self.assertFalse(is_minds_on_payload(prompt.get("payload")))
            self.assertFalse(is_meet_team_payload(prompt.get("payload")))

    def test_waiting_room_js_has_no_start_scoring_copy(self) -> None:
        """Student portal JS must not use the scoring-phase wait line in waiting-room."""
        js = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn("Waiting room — class is about to begin.", js)
        self.assertNotIn("Waiting for your teacher to start scoring.", js)
        self.assertNotIn("meet-math", js)
        self.assertNotIn("carousel", js.lower())
        self.assertNotIn("data.items", js)
        self.assertNotIn("payload.items", js)
        html = (LMS_DIR / "templates" / "student" / "home.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("Waiting room — class is about to begin.", html)
        self.assertNotIn("Waiting for your teacher to start scoring.", html)
        self.assertNotIn("meet-math", html)
        self.assertIn("feedback.text", js)
        self.assertIn("is-feedback", js)
        css = (LMS_DIR / "static" / "student-portal.css").read_text(encoding="utf-8")
        self.assertIn(".prompt-ack.is-feedback", css)

    def test_generic_mc_submit_has_no_feedback(self) -> None:
        """A staff MC that is not Minds-On / CONS returns ack only."""
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


if __name__ == "__main__":
    unittest.main(verbosity=2)

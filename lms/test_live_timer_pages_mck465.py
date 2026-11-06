#!/usr/bin/env python3
"""MCK-465: SessionTimer start, pause, resume, and restart on every page.

The countdown is one clock for the live lesson. It is not tied to a
stage, a question, or whether groups exist. Next and page changes leave
a running or paused clock alone. Resume keeps the remainder. Restart
(the start route with the selected minutes) replaces it. End and Quit
reject further writes. A running clock does not change the student
poll stamp on each second. Start Round and group transitions keep a
teacher-held clock and still apply the round plan when the clock is idle.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from live_class_metadata import default_math_pages  # noqa: E402

# Named lesson pages, including the old stage ids, plus one page whose
# id is not a stage. The timer must not special-case those ids.
LESSON_PAGES: tuple[tuple[str, str], ...] = tuple(
    (row["id"], row["stage"]) for row in default_math_pages()
) + (("blank-extra", "play"),)


class SessionTimerEveryPageTests(unittest.TestCase):
    """Staff and student SessionTimer across pages and group shapes."""

    def setUp(self) -> None:
        """Isolated app, one class, and staff plus student clients."""
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
        self.assertEqual(created.status_code, 200, created.get_json())
        self.class_id = int(created.get_json()["class"]["id"])
        run = self.staff.post(
            f"/staff/class/{self.class_id}/run-live",
            follow_redirects=False,
        )
        self.assertEqual(run.status_code, 302)
        live = self.school.get_active_live_session_for_class(self.class_id)
        self.assertIsNotNone(live)
        assert live is not None
        self.session_id = int(live["id"])
        self.student.post(
            "/auth/student-code",
            data={"code": str(live["session_code"]), "name": "Maple"},
            follow_redirects=False,
        )
        self.student.post("/student/mood", data={"mood": "good"})

    def tearDown(self) -> None:
        """Close the isolated database."""
        self.school.close()
        self.tmp.cleanup()

    def _start(self, minutes: int) -> dict:
        """POST timer start and return the game block."""
        response = self.staff.post(
            f"/api/classes/{self.class_id}/game/timer/start",
            json={"minutes": minutes},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        game = response.get_json()["game"]
        self.assertFalse(game.get("timer_paused"))
        self.assertIsInstance(game.get("round_ends_at_ms"), int)
        return game

    def _pause(self) -> dict:
        """POST timer pause and return the game block."""
        response = self.staff.post(
            f"/api/classes/{self.class_id}/game/timer/pause",
            json={},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        game = response.get_json()["game"]
        self.assertTrue(game.get("timer_paused"))
        self.assertIsNone(game.get("round_ends_at_ms"))
        self.assertGreater(int(game.get("round_remaining_sec") or 0), 0)
        return game

    def _resume(self) -> dict:
        """POST timer resume and return the game block."""
        response = self.staff.post(
            f"/api/classes/{self.class_id}/game/timer/resume",
            json={},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        game = response.get_json()["game"]
        self.assertFalse(game.get("timer_paused"))
        self.assertIsInstance(game.get("round_ends_at_ms"), int)
        return game

    def _goto(self, page_id: str, stage: str) -> dict:
        """Move the live lesson to one named page. Returns teacher state."""
        response = self.staff.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"stage": stage, "page_id": page_id},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        state = response.get_json()["teacher_state"]
        self.assertEqual(state["stage"], stage)
        self.assertEqual(state["page_id"], page_id)
        return state

    def _display(self) -> dict:
        """Student-facing clock snapshot for this class."""
        return self.school.live_session_display_time(self.class_id)

    def _student_clock(self) -> dict:
        """Clock on the joined student's full state payload."""
        body = self.student.get("/api/student/state").get_json()
        self.assertIn("display_time", body)
        return body["display_time"]

    def test_controls_on_every_page_with_no_groups(self) -> None:
        """Start, pause, resume, and restart work before any groups exist."""
        for page_id, stage in LESSON_PAGES:
            self._goto(page_id, stage)
            started = self._start(4)
            self.assertGreaterEqual(int(started["round_remaining_sec"]), 239)
            paused = self._pause()
            frozen = int(paused["round_remaining_sec"])
            self.assertLessEqual(frozen, 240)
            resumed = self._resume()
            self.assertGreaterEqual(int(resumed["round_remaining_sec"]), frozen - 2)
            self.assertLess(int(resumed["round_remaining_sec"]), 240 + 2)
            restarted = self._start(2)
            self.assertGreaterEqual(int(restarted["round_remaining_sec"]), 119)
            self.assertLessEqual(int(restarted["round_remaining_sec"]), 120)
            self.assertEqual(self._display()["ends_at_ms"], restarted["round_ends_at_ms"])

    def test_controls_after_groups_and_on_the_class_team(self) -> None:
        """Two teams, then the single Class team, do not block the timer."""
        students = self.school.game.dashboard(self.class_id)["students"]
        ids = [int(row["id"]) for row in students]
        self.school.game.begin_game(self.class_id)
        assigned = self.staff.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={
                "assign": {
                    "n_teams": 2,
                    "mode": "balanced",
                    "present_ids": ids,
                }
            },
        )
        self.assertEqual(assigned.status_code, 200, assigned.get_json())
        self.assertTrue(assigned.get_json()["teacher_state"]["groups_configured"])
        for page_id, stage in (("meet", "meet"), ("round_1", "round"), ("summary", "summary")):
            self._goto(page_id, stage)
            self._start(3)
            self._pause()
            self._resume()
        running = self._start(6)
        deadline = running["round_ends_at_ms"]
        solo = self.staff.post(
            f"/api/classes/{self.class_id}/game/ungamified",
            json={"present_ids": ids, "go_live": False, "meeting_date": "2026-09-09"},
        )
        self.assertEqual(solo.status_code, 200, solo.get_json())
        self.assertEqual(solo.get_json()["teams"][0]["name"], "Class")
        self.assertEqual(solo.get_json()["game"]["round_ends_at_ms"], deadline)
        self.assertFalse(solo.get_json()["game"].get("timer_paused"))
        self._goto("welcome", "teams")
        self.assertEqual(self._display()["ends_at_ms"], deadline)

    def test_navigation_keeps_running_and_paused_clocks(self) -> None:
        """Forward and Back do not stop, reset, or pause the countdown."""
        started = self._start(8)
        deadline = started["round_ends_at_ms"]
        for page_id, stage in LESSON_PAGES:
            self._goto(page_id, stage)
            shown = self._display()
            self.assertTrue(shown["running"], page_id)
            self.assertFalse(shown["paused"], page_id)
            self.assertEqual(shown["ends_at_ms"], deadline, page_id)
            student = self._student_clock()
            self.assertTrue(student["running"], page_id)
            self.assertEqual(student["ends_at_ms"], deadline, page_id)
        paused = self._pause()
        frozen = int(paused["round_remaining_sec"])
        for page_id, stage in reversed(LESSON_PAGES):
            self._goto(page_id, stage)
            shown = self._display()
            self.assertTrue(shown["paused"], page_id)
            self.assertFalse(shown["running"], page_id)
            self.assertEqual(shown["remaining_sec"], frozen, page_id)
            student = self._student_clock()
            self.assertTrue(student["paused"], page_id)
            self.assertEqual(student["remaining_sec"], frozen, page_id)

    def test_meet_without_groups_does_not_replace_a_running_clock(self) -> None:
        """Opening Meet with nobody grouped leaves the selected countdown."""
        started = self._start(7)
        deadline = started["round_ends_at_ms"]
        self._goto("meet", "meet")
        game = self.school.game.game_state(self.class_id)["game"]
        self.assertNotEqual(game.get("overlay_phase"), "meet_teams")
        self.assertEqual(game.get("round_ends_at_ms"), deadline)
        self.assertEqual(self._student_clock()["ends_at_ms"], deadline)

    def test_resume_keeps_remainder_and_restart_uses_selected_minutes(self) -> None:
        """Resume is not reset. Restart uses the minutes in the request."""
        self._start(8)
        self._pause()
        adjusted = self.staff.post(
            f"/api/classes/{self.class_id}/game/timer/remaining",
            json={"minutes": 2},
        )
        self.assertEqual(adjusted.status_code, 200, adjusted.get_json())
        self.assertEqual(adjusted.get_json()["game"]["round_remaining_sec"], 120)
        resumed = self._resume()
        self.assertGreaterEqual(int(resumed["round_remaining_sec"]), 118)
        self.assertLessEqual(int(resumed["round_remaining_sec"]), 120)
        self._pause()
        restarted = self._start(6)
        self.assertGreaterEqual(int(restarted["round_remaining_sec"]), 359)
        self.assertLessEqual(int(restarted["round_remaining_sec"]), 360)

    def test_new_start_after_expiry(self) -> None:
        """A clock that has already hit 0:00 can start again."""
        self._start(1)
        started = (datetime.now() - timedelta(seconds=90)).replace(microsecond=0)
        with self.school.game._lock:
            self.school.game.conn.execute(
                "UPDATE games SET round_started_at = ? WHERE class_id = ? "
                "AND status != 'ended'",
                (started.isoformat(), self.class_id),
            )
            self.school.game.conn.commit()
        self.assertEqual(self._display()["remaining_sec"], 0)
        self.assertTrue(self._display()["running"])
        restarted = self._start(3)
        self.assertGreaterEqual(int(restarted["round_remaining_sec"]), 179)
        self.assertLessEqual(int(restarted["round_remaining_sec"]), 180)

    def test_pause_while_scoring_is_live_keeps_the_remainder(self) -> None:
        """A live scoring row must not swap the frozen remainder for the plan."""
        self._start(5)
        plan = '[{"kind": "open", "minutes": 20}]'
        with self.school.game._lock:
            self.school.game.conn.execute(
                "UPDATE games SET status = 'live', rounds_json = ? "
                "WHERE class_id = ? AND status != 'ended'",
                (plan, self.class_id),
            )
            self.school.game.conn.commit()
        paused = self._pause()
        remaining = int(paused["round_remaining_sec"])
        self.assertGreaterEqual(remaining, 298)
        self.assertLessEqual(remaining, 300)
        shown = self._display()
        self.assertTrue(shown["paused"])
        self.assertEqual(shown["remaining_sec"], remaining)

    def test_end_and_a_student_cannot_write_the_timer(self) -> None:
        """End closes the control. A student session cannot start it."""
        self._start(3)
        self.school.end_live_class_session(self.session_id)
        denied = self.staff.post(
            f"/api/classes/{self.class_id}/game/timer/start",
            json={"minutes": 3},
        )
        self.assertEqual(denied.status_code, 400, denied.get_json())
        self.assertIn("live class", denied.get_json()["error"])
        paused = self.staff.post(
            f"/api/classes/{self.class_id}/game/timer/pause",
            json={},
        )
        self.assertEqual(paused.status_code, 400)
        student = self.student.post(
            f"/api/classes/{self.class_id}/game/timer/start",
            json={"minutes": 3},
        )
        self.assertNotEqual(student.status_code, 200)

    def test_poll_stamp_changes_on_start_and_stays_put_while_running(self) -> None:
        """One stamp change per teacher write, not one per countdown second."""
        idle = self.school.live_student_poll_stamp(self.session_id, self.class_id)
        self.assertTrue(idle.endswith(":idle"))
        self._start(5)
        running = self.school.live_student_poll_stamp(self.session_id, self.class_id)
        self.assertNotEqual(running, idle)
        self.assertIn(":run:", running)
        self.assertEqual(
            self.school.live_student_poll_stamp(self.session_id, self.class_id),
            running,
        )
        self._pause()
        paused = self.school.live_student_poll_stamp(self.session_id, self.class_id)
        self.assertNotEqual(paused, running)
        self.assertIn(":paused:", paused)
        self._resume()
        again = self.school.live_student_poll_stamp(self.session_id, self.class_id)
        self.assertIn(":run:", again)
        self.assertNotEqual(again, paused)

    def test_close_at_zero_still_follows_the_same_clock_after_a_page_change(self) -> None:
        """The opt-in flag and the deadline survive a page change."""
        self.school.set_live_session_teacher_state(
            self.session_id, timer_closes_answers=False
        )
        self._start(1)
        started = (datetime.now() - timedelta(seconds=90)).replace(microsecond=0)
        with self.school.game._lock:
            self.school.game.conn.execute(
                "UPDATE games SET round_started_at = ? WHERE class_id = ? "
                "AND status != 'ended'",
                (started.isoformat(), self.class_id),
            )
            self.school.game.conn.commit()
        self._goto("round_3", "round_3")
        self.assertEqual(self.school.close_answers_if_timer_expired(self.session_id), [])
        self.assertIsNotNone(self.school.session_timer_deadline(self.class_id))
        self.school.set_live_session_teacher_state(
            self.session_id, timer_closes_answers=True
        )
        state = self.school.live_session_teacher_state_payload(self.session_id)
        self.assertTrue(state["timer_closes_answers"])
        self.assertIsNotNone(self.school.session_timer_deadline(self.class_id))

    def test_restart_control_is_separate_from_resume(self) -> None:
        """The shell labels Restart on its own button and keeps Resume."""
        html = self.staff.get(f"/staff/class/{self.class_id}?tab=live").get_data(
            as_text=True
        )
        panel = html[html.index('id="session-timer"') :]
        self.assertIn('id="ap-meet-restart"', panel)
        self.assertIn(">Restart<", panel)
        self.assertLess(panel.index('id="ap-meet-start"'), panel.index('id="ap-meet-restart"'))
        self.assertIn('id="live-timer-closes"', panel)
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        restart = js.split('$("ap-meet-restart")?.addEventListener("click"')[1].split(
            '$("live-timer-toggle")'
        )[0]
        self.assertIn("startSessionTimer()", restart)
        self.assertNotIn("timer/resume", restart)
        self.assertNotIn("timer/pause", restart)
        start = js.split('$("ap-meet-start")?.addEventListener("click"')[1].split(
            '$("ap-meet-restart")'
        )[0]
        self.assertIn("/game/timer/pause", start)
        self.assertIn("/game/timer/resume", start)
        self.assertIn("startSessionTimer()", start)
        apply = js.split("function applySessionTimerUi(")[1].split(
            "function paintSessionClock("
        )[0]
        self.assertIn('btn.textContent = "Resume"', apply)
        self.assertIn('btn.dataset.meetState = "expired"', apply)
        self.assertNotIn("running || paused", apply)

    def _park_rounds(self) -> dict:
        """Move individual tracking to rounds setup without going live.

        Returns:
            Game payload after the Class team is parked on rounds.
        """
        prepared = self.staff.post(
            f"/api/classes/{self.class_id}/game/ungamified",
            json={"go_live": False, "meeting_date": "2026-11-06"},
        )
        self.assertEqual(prepared.status_code, 200, prepared.get_json())
        game = prepared.get_json()["game"]
        self.assertEqual(game["status"], "rounds")
        return game

    def _start_open_round(self, minutes: int) -> dict:
        """Start one Open Question round of ``minutes``.

        Args:
            minutes: Plan length. Used only when no teacher timer is held.

        Returns:
            Game payload after Start Round.
        """
        live = self.staff.post(
            f"/api/classes/{self.class_id}/game/start-rounds",
            json={"rounds": [{"kind": "open", "minutes": minutes}]},
        )
        self.assertEqual(live.status_code, 200, live.get_json())
        game = live.get_json()["game"]
        self.assertEqual(game["status"], "live")
        self.assertEqual(int(game["round"]), 1)
        return game

    def _append_open_round(self, minutes: int) -> dict:
        """Append and start the next Open Question round.

        Args:
            minutes: Plan length for the new round when the clock is unheld.

        Returns:
            Game payload on the new round.
        """
        nxt = self.staff.post(
            f"/api/classes/{self.class_id}/game/append-round",
            json={"kind": "open", "minutes": minutes},
        )
        self.assertEqual(nxt.status_code, 200, nxt.get_json())
        return nxt.get_json()["game"]

    def _assert_teacher_student_clock(self, game: dict) -> None:
        """Teacher game clock and student display describe the same countdown.

        Args:
            game: Staff game block from the latest mutation.
        """
        shown = self._student_clock()
        display = self._display()
        self.assertEqual(shown["paused"], display["paused"])
        self.assertEqual(shown["running"], display["running"])
        self.assertEqual(shown["remaining_sec"], display["remaining_sec"])
        self.assertEqual(bool(game.get("timer_paused")), bool(shown["paused"]))
        if game.get("round_ends_at_ms") is None:
            self.assertIsNone(shown["ends_at_ms"])
        else:
            self.assertEqual(shown["ends_at_ms"], game["round_ends_at_ms"])
        if shown["paused"] or not shown["running"]:
            self.assertEqual(
                int(shown["remaining_sec"]), int(game["round_remaining_sec"])
            )

    def test_running_timer_survives_start_round_and_the_next_round(self) -> None:
        """A running teacher timer is not replaced by the round plan."""
        started = self._start(4)
        deadline = started["round_ends_at_ms"]
        self._park_rounds()
        game = self._start_open_round(15)
        self.assertEqual(game["round_ends_at_ms"], deadline)
        self.assertEqual(int(game["round_duration_sec"]), 4 * 60)
        self.assertGreaterEqual(int(game["round_remaining_sec"]), 238)
        self.assertLessEqual(int(game["round_remaining_sec"]), 240)
        self._assert_teacher_student_clock(game)
        nxt = self._append_open_round(10)
        self.assertEqual(int(nxt["round"]), 2)
        self.assertEqual(nxt["round_ends_at_ms"], deadline)
        self.assertEqual(int(nxt["round_duration_sec"]), 4 * 60)
        self._assert_teacher_student_clock(nxt)
        state = self.school.game.game_state(self.class_id)
        student_id = int(state["students"][0]["id"])
        scored = self.staff.post(
            f"/api/classes/{self.class_id}/game/score",
            json={"kind": "student", "id": student_id, "amount": 1},
        )
        self.assertEqual(scored.status_code, 200, scored.get_json())
        after = self.school.game.game_state(self.class_id)["game"]
        self.assertEqual(after["round_ends_at_ms"], deadline)
        self.assertEqual(int(after["round"]), 2)

    def test_paused_timer_keeps_frozen_remainder_through_start_round(self) -> None:
        """Start Round does not unpause or swap the frozen remainder."""
        self._start(5)
        paused = self._pause()
        remaining = int(paused["round_remaining_sec"])
        self.assertGreaterEqual(remaining, 298)
        self.assertLessEqual(remaining, 300)
        self._park_rounds()
        game = self._start_open_round(20)
        self.assertTrue(game["timer_paused"])
        self.assertIsNone(game["round_ends_at_ms"])
        self.assertEqual(int(game["round_remaining_sec"]), remaining)
        self._assert_teacher_student_clock(game)
        nxt = self._append_open_round(8)
        self.assertEqual(int(nxt["round"]), 2)
        self.assertTrue(nxt["timer_paused"])
        self.assertEqual(int(nxt["round_remaining_sec"]), remaining)
        self._assert_teacher_student_clock(nxt)

    def test_expired_teacher_timer_is_not_restarted_by_start_round(self) -> None:
        """An expired teacher countdown stays at zero through Start Round."""
        self._start(1)
        started = (datetime.now() - timedelta(seconds=90)).replace(microsecond=0)
        with self.school.game._lock:
            self.school.game.conn.execute(
                "UPDATE games SET round_started_at = ? WHERE class_id = ? "
                "AND status != 'ended'",
                (started.isoformat(), self.class_id),
            )
            self.school.game.conn.commit()
        self.assertEqual(self._display()["remaining_sec"], 0)
        self._park_rounds()
        game = self._start_open_round(15)
        self.assertEqual(int(game["round_remaining_sec"]), 0)
        self.assertEqual(int(game["round_duration_sec"]), 60)
        self.assertEqual(self._student_clock()["remaining_sec"], 0)
        self._assert_teacher_student_clock(game)

    def test_idle_start_round_still_uses_the_round_plan(self) -> None:
        """With no teacher timer, Start Round and the next round use the plan."""
        begun = self.staff.post(
            f"/api/classes/{self.class_id}/begin",
            json={"meeting_date": "2026-11-06"},
        )
        self.assertEqual(begun.status_code, 200, begun.get_json())
        self._park_rounds()
        game = self._start_open_round(15)
        self.assertEqual(int(game["round_duration_sec"]), 15 * 60)
        self.assertGreaterEqual(int(game["round_remaining_sec"]), 15 * 60 - 5)
        self.assertLessEqual(int(game["round_remaining_sec"]), 15 * 60)
        self._assert_teacher_student_clock(game)
        nxt = self._append_open_round(10)
        self.assertEqual(int(nxt["round"]), 2)
        self.assertEqual(int(nxt["round_duration_sec"]), 10 * 60)
        self.assertGreaterEqual(int(nxt["round_remaining_sec"]), 10 * 60 - 5)
        self.assertLessEqual(int(nxt["round_remaining_sec"]), 10 * 60)
        self._assert_teacher_student_clock(nxt)

    def test_class_team_go_live_keeps_a_running_teacher_timer(self) -> None:
        """Forming the Class team and going live does not reset a held timer."""
        started = self._start(6)
        deadline = started["round_ends_at_ms"]
        live = self.staff.post(
            f"/api/classes/{self.class_id}/game/ungamified",
            json={"go_live": True, "meeting_date": "2026-11-06"},
        )
        self.assertEqual(live.status_code, 200, live.get_json())
        game = live.get_json()["game"]
        self.assertEqual(game["status"], "live")
        self.assertEqual(game["round_ends_at_ms"], deadline)
        self.assertEqual(int(game["round_duration_sec"]), 6 * 60)
        self._assert_teacher_student_clock(game)

    def test_team_challenge_phone_hide_is_display_only(self) -> None:
        """MCK-171 hides the phone digits and does not write the clock."""
        js = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn(
            "MCK-171: no countdown on phones during a Team challenge (no rushing).",
            js,
        )
        paint = js.split("function paintDisplayTime(")[1].split(
            "function teamChallengeOpen("
        )[0]
        hide = paint.split("if (teamChallengeOpen(payload))")[1].split(
            "if (dt.running"
        )[0]
        self.assertIn('displayTimeEl.textContent = "—"', hide)
        self.assertNotIn("fetch(", hide)
        self.assertNotIn("/game/timer", hide)
        started = self._start(4)
        shown = self._student_clock()
        self.assertEqual(shown["ends_at_ms"], started["round_ends_at_ms"])
        self.assertTrue(shown["running"])

    def _held_flag(self) -> int:
        """Return ``timer_held`` for this class, or 0 when no game exists."""
        row = self.school.game.conn.execute(
            "SELECT COALESCE(timer_held, 0) FROM games WHERE class_id = ?",
            (self.class_id,),
        ).fetchone()
        return int(row[0]) if row is not None else 0

    def _stop(self) -> dict:
        """POST Stop and return the idle game block."""
        response = self.staff.post(
            f"/api/classes/{self.class_id}/game/timer/stop",
            json={},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        game = response.get_json()["game"]
        self.assertFalse(game.get("timer_paused"))
        self.assertIsNone(game.get("round_ends_at_ms"))
        self.assertEqual(self._held_flag(), 0)
        row = self.school.game.conn.execute(
            "SELECT round_started_at, round_duration_sec FROM games WHERE class_id = ?",
            (self.class_id,),
        ).fetchone()
        self.assertIsNone(row[0])
        self.assertIsNone(row[1])
        shown = self._student_clock()
        self.assertFalse(shown["running"])
        self.assertFalse(shown["paused"])
        self.assertEqual(int(shown["remaining_sec"]), 0)
        return game

    def test_stop_releases_a_running_clock_for_the_round_plan(self) -> None:
        """Stop clears a running hold. The next Start Round uses the plan."""
        self._start(4)
        self.assertEqual(self._held_flag(), 1)
        self._stop()
        begun = self.staff.post(
            f"/api/classes/{self.class_id}/begin",
            json={"meeting_date": "2026-11-06"},
        )
        self.assertIn(begun.status_code, (200, 400), begun.get_json())
        self._park_rounds()
        game = self._start_open_round(15)
        self.assertEqual(int(game["round_duration_sec"]), 15 * 60)
        self.assertEqual(self._held_flag(), 0)
        self._assert_teacher_student_clock(game)

    def test_stop_releases_paused_and_expired_clocks(self) -> None:
        """Stop drops a frozen remainder and an expired hold."""
        self._start(5)
        paused = self._pause()
        self.assertGreater(int(paused["round_remaining_sec"]), 0)
        self.assertEqual(self._held_flag(), 1)
        self._stop()
        self._start(1)
        started = (datetime.now() - timedelta(seconds=90)).replace(microsecond=0)
        with self.school.game._lock:
            self.school.game.conn.execute(
                "UPDATE games SET round_started_at = ? WHERE class_id = ?",
                (started.isoformat(), self.class_id),
            )
            self.school.game.conn.commit()
        self.assertEqual(self._display()["remaining_sec"], 0)
        self.assertEqual(self._held_flag(), 1)
        self._stop()
        self.assertEqual(self._student_clock()["remaining_sec"], 0)
        self.assertFalse(self._student_clock()["running"])

    def test_stop_with_no_groups_one_group_and_several_groups(self) -> None:
        """Stop is available before groups, on one Class team, and on two teams."""
        self._start(3)
        self._stop()
        students = self.school.game.dashboard(self.class_id)["students"]
        ids = [int(row["id"]) for row in students]
        assigned = self.staff.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"assign": {"n_teams": 2, "mode": "balanced", "present_ids": ids}},
        )
        self.assertEqual(assigned.status_code, 200, assigned.get_json())
        self._start(4)
        self.assertEqual(self._held_flag(), 1)
        self._stop()
        solo = self.staff.post(
            f"/api/classes/{self.class_id}/game/ungamified",
            json={"present_ids": ids, "go_live": False, "meeting_date": "2026-11-06"},
        )
        self.assertEqual(solo.status_code, 200, solo.get_json())
        self.assertEqual(solo.get_json()["teams"][0]["name"], "Class")
        self._start(6)
        deadline = self.school.game.game_state(self.class_id)["game"]["round_ends_at_ms"]
        self._goto("round_1", "round")
        self.assertEqual(self._display()["ends_at_ms"], deadline)
        self.assertEqual(self._held_flag(), 1)
        self._stop()

    def test_navigation_does_not_release_a_held_clock(self) -> None:
        """Forward and Back leave timer_held set. Only Stop clears it."""
        started = self._start(8)
        deadline = started["round_ends_at_ms"]
        self.assertEqual(self._held_flag(), 1)
        self._goto("play", "play")
        self._goto("summary", "summary")
        self.assertEqual(self._held_flag(), 1)
        self.assertEqual(self._display()["ends_at_ms"], deadline)
        self.assertEqual(self._student_clock()["ends_at_ms"], deadline)
        html = self.staff.get(f"/staff/class/{self.class_id}?tab=live").get_data(as_text=True)
        self.assertIn('id="ap-meet-stop"', html)
        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        stop = js.split('$("ap-meet-stop")?.addEventListener("click"')[1].split(
            '$("live-timer-toggle")'
        )[0]
        self.assertIn("/game/timer/stop", stop)
        self.assertNotIn("startSessionTimer()", stop)

    def test_end_quit_and_a_student_cannot_stop_the_timer(self) -> None:
        """End and Quit reject Stop. A student session receives 401."""
        self._start(3)
        student = self.student.post(
            f"/api/classes/{self.class_id}/game/timer/stop",
            json={},
        )
        self.assertEqual(student.status_code, 401, student.get_json())
        self.assertEqual(self._held_flag(), 1)
        self.school.end_live_class_session(self.session_id)
        ended = self.staff.post(
            f"/api/classes/{self.class_id}/game/timer/stop",
            json={},
        )
        self.assertEqual(ended.status_code, 400, ended.get_json())
        self.assertIn("live class", ended.get_json()["error"])

    def test_quit_rejects_stop(self) -> None:
        """Quit ends the live session, so Stop is refused."""
        self._start(3)
        quits = self.staff.post(
            f"/staff/class/{self.class_id}/quit-live",
            headers={"Accept": "application/json"},
        )
        self.assertIn(quits.status_code, (200, 302), quits.get_json(silent=True) or quits.status_code)
        self.assertIsNone(self.school.get_active_live_session_for_class(self.class_id))
        denied = self.staff.post(
            f"/api/classes/{self.class_id}/game/timer/stop",
            json={},
        )
        self.assertEqual(denied.status_code, 400, denied.get_json())

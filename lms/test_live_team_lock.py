#!/usr/bin/env python3
"""Locked live-class teams survive refresh, remint, and a dead /state poll."""

from __future__ import annotations

import logging
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

from app import _gone_live_sessions_logged, create_app  # noqa: E402


def _membership(state: dict) -> dict[str, tuple[int, ...]]:
    """Map team name to sorted roster ids.

    Args:
        state: Game-state payload from ``game_state``.
    """
    out: dict[str, tuple[int, ...]] = {}
    for team in state.get("teams") or []:
        name = str(team.get("name") or "")
        if name == "Class":
            continue
        ids = tuple(sorted(int(member["id"]) for member in team.get("members") or []))
        out[name] = ids
    return out


class LiveTeamLockTests(unittest.TestCase):
    """Teams stay put for the life of a Meet, then /state dies cleanly."""

    def setUp(self) -> None:
        """Isolated app, one staff login, and a four-student class."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.staff = self.app.test_client()
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
                "codenames": ["Maple", "Aspen", "Birch", "Cedar"],
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

    def tearDown(self) -> None:
        """Close sqlite and remove the temp dir."""
        self.school.close()
        self.tmp.cleanup()

    def _assign(self) -> dict[str, tuple[int, ...]]:
        """Begin, mark everyone present, and assign two balanced teams.

        Returns:
            Team name to sorted student ids.
        """
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
            json={"n_teams": 2, "mode": "balanced", "present_ids": ids},
        )
        self.assertEqual(assigned.status_code, 200, assigned.get_json())
        locked = _membership(self.school.game.game_state(self.class_id))
        self.assertEqual(len(locked), 2)
        self.assertEqual(sum(len(members) for members in locked.values()), 4)
        self.assertIsNotNone(self.school.game.team_lock(self.class_id))
        return locked

    def _wipe_team_rows(self) -> None:
        """Drop named-team rows while leaving the saved lock in place."""
        with self.school.game._lock:
            game = self.school.game._game_row(self.class_id)
            game_id = int(game["id"])
            self.school.game.conn.execute(
                "DELETE FROM game_memberships WHERE game_id = ?", (game_id,)
            )
            self.school.game.conn.execute(
                "DELETE FROM game_teams WHERE game_id = ?", (game_id,)
            )
            self.school.game.conn.commit()

    def test_refresh_and_begin_keep_the_same_teams(self) -> None:
        """A later /begin and /state poll do not re-roll or empty teams."""
        locked = self._assign()
        again = self.staff.post(
            f"/api/classes/{self.class_id}/begin",
            json={"meeting_date": "2026-09-09"},
        )
        self.assertEqual(again.status_code, 200, again.get_json())
        self.assertEqual(_membership(again.get_json()), locked)
        state = self.staff.get(f"/api/live-sessions/{self.session_id}/state")
        self.assertEqual(state.status_code, 200, state.get_data(as_text=True)[:500])
        body = state.get_json()
        self.assertEqual(body.get("phase"), "live")
        self.assertFalse(body.get("fault"))
        projected = {
            str(team.get("name") or ""): tuple(
                sorted(int(member["id"]) for member in team.get("members") or [])
            )
            for team in body.get("groups") or []
        }
        self.assertEqual(projected, locked)
        reroll = self.staff.post(
            f"/api/classes/{self.class_id}/game/assign",
            json={
                "n_teams": 2,
                "mode": "random",
                "present_ids": [
                    int(student["id"])
                    for student in again.get_json()["students"]
                ],
            },
        )
        self.assertEqual(reroll.status_code, 400, reroll.get_json())
        self.assertIn("already", str(reroll.get_json().get("error") or "").lower())
        self.assertEqual(
            _membership(self.school.game.game_state(self.class_id)), locked
        )

    def test_wiped_rows_restore_on_state_and_remint(self) -> None:
        """A crash that drops team rows, then a new Meet, puts the same kids back."""
        locked = self._assign()
        ids = [student_id for members in locked.values() for student_id in members]
        self._wipe_team_rows()
        self.assertEqual(
            _membership(self.school.game.game_state(self.class_id)), {}
        )
        reroll = self.staff.post(
            f"/api/classes/{self.class_id}/game/assign",
            json={"n_teams": 2, "mode": "random", "present_ids": ids},
        )
        self.assertEqual(reroll.status_code, 200, reroll.get_json())
        self.assertEqual(
            _membership(self.school.game.game_state(self.class_id)), locked
        )
        self._wipe_team_rows()
        state = self.staff.get(f"/api/live-sessions/{self.session_id}/state")
        self.assertEqual(state.status_code, 200, state.get_data(as_text=True)[:500])
        self.assertEqual(
            _membership(self.school.game.game_state(self.class_id)), locked
        )
        self.school.conn.execute(
            """
            UPDATE live_class_sessions
            SET status = 'ended', ended_at = '2026-09-09T12:00:00'
            WHERE id = ?
            """,
            (self.session_id,),
        )
        self.school.conn.commit()
        self._wipe_team_rows()
        remint = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.assertNotEqual(int(remint["id"]), self.session_id)
        self.assertEqual(
            _membership(self.school.game.game_state(self.class_id)), locked
        )
        teacher = self.school.live_session_teacher_state_payload(int(remint["id"]))
        self.assertTrue(teacher.get("groups_configured"))
        self.assertTrue(teacher.get("run_as_group"))

    def test_end_clears_the_lock(self) -> None:
        """End Live Class drops the saved assignment so the next Meet is fresh."""
        self._assign()
        self.school.finish_live_class(self.class_id, celebrate=False)
        self.assertIsNone(self.school.game.team_lock(self.class_id))

    def test_missing_session_state_is_ended_not_500(self) -> None:
        """A gone session id is a 200 ended fault, including a builder KeyError."""
        missing = self.staff.get("/api/live-sessions/13/state")
        self.assertEqual(missing.status_code, 200, missing.get_data(as_text=True)[:500])
        body = missing.get_json()
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("phase"), "ended")
        self.assertIn("no longer running", str(body.get("fault") or ""))
        self.assertNotEqual(missing.status_code, 500)

        def boom(_session_id: int, *, light: bool = False) -> dict:
            """Raise the poller KeyError seen after the MCR3U artifact load."""
            raise KeyError(f"live session {_session_id}")

        self.school.get_live_session_state = boom  # type: ignore[method-assign]
        dead = self.staff.get(f"/api/live-sessions/{self.session_id}/state")
        self.assertEqual(dead.status_code, 200, dead.get_data(as_text=True)[:500])
        payload = dead.get_json()
        self.assertTrue(payload.get("ok"))
        self.assertEqual(payload.get("error"), "state unavailable")
        self.assertTrue(payload.get("fault"))

    def test_student_poll_missing_session_is_stable_ended(self) -> None:
        """Hammering a gone Meet returns ended JSON and does not traceback.

        Production browsers kept polling session 9 after the row was gone.
        ``live_session_teacher_state_payload`` raised ``KeyError`` and each
        poll logged a full traceback until the shared CPU wedged.
        """
        student = self.app.test_client()
        live = self.school.get_live_session(self.session_id)
        assert live is not None
        joined = student.post(
            "/auth/student-code",
            data={"code": live["session_code"], "name": "Maple"},
            follow_redirects=True,
        )
        self.assertEqual(joined.status_code, 200, joined.get_data(as_text=True)[:400])

        def _still_here(*_args, **_kwargs):
            """Presence still says this student is in a Meet sqlite no longer has."""
            return {
                "live_session_id": self.session_id,
                "codename": "Maple",
                "left_at": None,
            }

        def _still_active(*_args, **_kwargs) -> bool:
            """Keep the student gate open after the Meet row is deleted."""
            return True

        real_get = self.school.get_live_session
        removed = {"done": False}

        def _vanish_after_read(session_id: int):
            """Drop the Meet after the route's first read so /state raises KeyError."""
            row = real_get(session_id)
            if (
                row is not None
                and int(session_id) == self.session_id
                and not removed["done"]
            ):
                removed["done"] = True
                self.school.conn.execute(
                    "DELETE FROM live_class_sessions WHERE id = ?",
                    (int(session_id),),
                )
                self.school.conn.commit()
            return row

        _gone_live_sessions_logged.clear()
        handler = logging.Handler()
        handler.setLevel(logging.DEBUG)
        records: list[logging.LogRecord] = []
        handler.emit = records.append  # type: ignore[method-assign]
        log = logging.getLogger("app")
        previous = log.level
        log.setLevel(logging.DEBUG)
        log.addHandler(handler)
        try:
            self.school.get_live_session = _vanish_after_read  # type: ignore[method-assign]
            vanished = self.staff.get(
                f"/api/live-sessions/{self.session_id}/state"
            )
            self.school.get_live_session = real_get  # type: ignore[method-assign]
            self.assertTrue(removed["done"])
            self.assertEqual(
                vanished.status_code, 200, vanished.get_data(as_text=True)[:500]
            )
            vanished_body = vanished.get_json()
            self.assertEqual(vanished_body.get("phase"), "ended")
            self.assertIn("no longer running", str(vanished_body.get("fault") or ""))
            self.assertNotEqual(vanished_body.get("error"), "state unavailable")

            self.school.conn.execute(
                "DELETE FROM live_class_sessions WHERE id = ?",
                (self.session_id,),
            )
            self.school.conn.commit()
            self.school.touch_live_session_heartbeat = _still_here  # type: ignore[method-assign]
            self.school.student_is_active_live_attendee = _still_active  # type: ignore[method-assign]

            direct = [
                self.school.student_live_poll_unchanged(
                    9, self.class_id, 1, "gone"
                )
                for _ in range(20)
            ]
            self.assertTrue(all(body == direct[0] for body in direct))
            self.assertEqual(direct[0]["status"], "ended")
            self.assertEqual(direct[0]["phase"], "ended")
            self.assertFalse(direct[0]["celebrate"])
            self.assertIn("no longer running", direct[0]["fault"])

            seen: list[dict] = []
            for _ in range(30):
                rv = student.get("/api/student/state?seq=1&stamp=gone")
                self.assertEqual(
                    rv.status_code, 200, rv.get_data(as_text=True)[:500]
                )
                self.assertNotIn(
                    "Traceback", rv.get_data(as_text=True)
                )
                seen.append(rv.get_json())
            self.assertTrue(all(body == seen[0] for body in seen))
            self.assertEqual(seen[0]["status"], "ended")
            self.assertEqual(seen[0]["phase"], "ended")
            self.assertFalse(seen[0]["celebrate"])
            self.assertTrue(seen[0].get("redirect"))
            self.assertIn("no longer running", seen[0]["fault"])

            for session_id in (self.session_id, 9):
                for _ in range(15):
                    staff = self.staff.get(
                        f"/api/live-sessions/{session_id}/state"
                    )
                    self.assertEqual(
                        staff.status_code,
                        200,
                        staff.get_data(as_text=True)[:500],
                    )
                    staff_body = staff.get_json()
                    self.assertEqual(staff_body.get("phase"), "ended")
                    self.assertTrue(staff_body.get("ok"))
                    self.assertNotIn("Traceback", staff.get_data(as_text=True))
        finally:
            log.removeHandler(handler)
            log.setLevel(previous)

        for record in records:
            self.assertFalse(
                record.exc_info,
                f"poll logged a traceback: {record.getMessage()}",
            )
            self.assertNotIn("Traceback", record.getMessage())
        gone_notes = [
            record
            for record in records
            if "is gone" in record.getMessage()
        ]
        self.assertTrue(gone_notes)
        counts: dict[str, int] = {}
        for record in gone_notes:
            self.assertEqual(record.levelno, logging.WARNING)
            counts[record.getMessage()] = counts.get(record.getMessage(), 0) + 1
        self.assertTrue(all(count == 1 for count in counts.values()))

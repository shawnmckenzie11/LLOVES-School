#!/usr/bin/env python3
"""MCK-116 S1: Celebrations avatar reward pop-up, grants and ownership."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from celebration import (  # noqa: E402
    CELEBRATIONS_FROZEN_ENV,
    SETTING_FEATURED_AWARD,
    SETTING_FEATURED_AWARD_ID,
    SETTING_FREEZE_EPOCH,
    build_celebration_board,
    clear_public_celebration_memo,
    reward_appearances,
    set_featured_award,
)
from db import EARNED_CHARACTERS  # noqa: E402

TITLE = "You made Celebrations!"
CLAIM = "/api/student/avatar-reward/claim"


class AvatarRewardTests(unittest.TestCase):
    """Featured students get one reward per appearance; others never do."""

    def setUp(self) -> None:
        """MCF3M class: Maple attended a saved day (Most Engaged), Aspen did not."""
        clear_public_celebration_memo()
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(CELEBRATIONS_FROZEN_ENV, None)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
            live_database_url=self._presence_url(),
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.staff = self.app.test_client()
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("teacher@gmail.com")
        self.teacher_id = int(teacher["id"])
        offering = self.school.assign_course(teacher_user_id=self.teacher_id, ontario_code="MCF3M")
        self.staff.get("/auth/google?portal=staff")
        self.staff.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.staff.post(
            "/verify-email",
            data={"code": self.school.get_user_by_email("teacher@gmail.com")["verification_code"]},
        )
        created = self.staff.post(
            "/api/staff/classes",
            json={
                "offering_id": offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple", "Aspen"],
            },
        )
        self.assertEqual(created.status_code, 200)
        self.class_id = int(created.get_json()["class"]["id"])
        self.maple = self._sid("Maple")
        self.aspen = self._sid("Aspen")
        self._log_day("2026-09-09", [self.maple])
        # A public read outside class stores the frozen board (MCK-118/125).
        board = self.app.test_client().get("/api/celebrations").get_json()
        engaged = [c for c in board["cards"] if c.get("key") == "engaged"]
        self.assertEqual([c.get("names") for c in engaged], [["Maple"]])
        self._run_live()

    def tearDown(self) -> None:
        """Close db and temp dir."""
        self.school.close()
        self.tmp.cleanup()
        clear_public_celebration_memo()

    # helpers ---------------------------------------------------------------

    def _presence_url(self) -> str:
        """Live presence store: sqlite here; Postgres in the subclass below."""
        return ""

    def _sid(self, name: str) -> int:
        row = self.school.game.find_student_by_codename(self.class_id, name)
        assert row is not None
        return int(row["id"])

    def _log_day(self, meeting_date: str, present_ids: list[int]) -> None:
        begin = self.staff.post(
            f"/api/classes/{self.class_id}/begin", json={"meeting_date": meeting_date}
        )
        self.assertEqual(begin.status_code, 200)
        done = self.staff.post(
            f"/api/classes/{self.class_id}/game/finalize-attendance",
            json={"present_ids": present_ids, "meeting_date": meeting_date},
        )
        self.assertEqual(done.status_code, 200)

    def _run_live(self) -> None:
        run = self.staff.post(f"/staff/class/{self.class_id}/run-live", follow_redirects=False)
        self.assertEqual(run.status_code, 302)
        live = self.school.get_active_live_session_for_class(self.class_id)
        assert live is not None
        self.session_code = str(live["session_code"])
        self.live_id = int(live["id"])

    def _end_live(self) -> None:
        self.staff.post(f"/staff/class/{self.class_id}/end-live", data={"end_options": "1"})
        self.assertIsNone(self.school.get_active_live_session_for_class(self.class_id))

    def _login(self, name: str):
        """A fresh student login (new browser): code, then mood skip."""
        client = self.app.test_client()
        join = client.post(
            "/auth/student-code",
            data={"code": self.session_code, "name": name},
            follow_redirects=False,
        )
        self.assertEqual(join.status_code, 302)
        client.post("/student/mood", data={"skip": "1"}, follow_redirects=False)
        return client

    def _next_login(self, name: str):
        """Log in again at the next class (End, then Run Live Class)."""
        self._end_live()
        self._run_live()
        return self._login(name)

    def _landing(self, client) -> str:
        """The page a student lands on after login (picker, else home)."""
        page = client.get("/student/character", follow_redirects=True)
        self.assertEqual(page.status_code, 200)
        return page.get_data(as_text=True)

    def _grants(self, student_id: int) -> list[dict]:
        with self.school._lock:
            rows = self.school.conn.execute(
                "SELECT * FROM avatar_reward_grants WHERE class_id = ? AND student_id = ? ORDER BY id",
                (self.class_id, student_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def _claim(self, client, grant_id: int, key: str):
        return client.post(CLAIM, json={"grant_id": grant_id, "avatar_key": key})

    def _push_question(self) -> None:
        """Teacher pushes an MC question to students (still in JOIN)."""
        res = self.staff.post(
            f"/api/live-sessions/{self.live_id}/prompts",
            json={
                "slide_index": 20,
                "kind": "mc",
                "payload": {"question": "Slope of y=2x?", "choices": ["1", "2", "3"]},
            },
        )
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True)[:200])

    def _advance(self) -> str:
        """Teacher Next: JOIN -> TEAMS -> MEET ..."""
        res = self.staff.post(
            f"/api/live-sessions/{self.live_id}/teacher-state", json={"advance": "next"}
        )
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True)[:200])
        return str((res.get_json().get("teacher_state") or {}).get("stage") or "")

    def _state(self, client) -> dict:
        res = client.get("/api/student/state")
        self.assertEqual(res.status_code, 200)
        return res.get_json()

    def _assert_shut(self, client, student_id: int) -> None:
        """MED-1: no pop-up or entry point, state says shut, claim is 409."""
        html = self._landing(client)
        self.assertNotIn('id="avatar-reward"', html)
        self.assertNotIn("Pick your reward", html)
        home = client.get("/student/home").get_data(as_text=True)
        self.assertNotIn('id="avatar-reward"', home)
        self.assertNotIn("Reward waiting", home)
        self.assertIs(self._state(client).get("reward_window"), False)
        before = self.school.game.get_student(self.class_id, student_id).get("character_key")
        grant = self._grants(student_id)[0]
        res = self._claim(client, int(grant["id"]), "fox_scarf")
        self.assertEqual(res.status_code, 409)
        self.assertTrue(res.get_json()["closed"])
        self.assertEqual(self._grants(student_id)[0]["status"], "pending")
        self.assertEqual(self.school.earned_avatar_keys(self.class_id, student_id), [])
        self.assertEqual(
            self.school.game.get_student(self.class_id, student_id).get("character_key"), before
        )

    def _add_student(self, name: str) -> int:
        res = self.staff.post(f"/api/classes/{self.class_id}/students", json={"codename": name})
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True)[:200])
        return self._sid(name)

    # tests -------------------------------------------------------------------

    def test_featured_student_gets_the_popup_once_per_login(self) -> None:
        """Most Engaged Maple: pop-up opens on the picker, then not on reload."""
        client = self._login("Maple")
        html = self._landing(client)
        self.assertIn("Choose your Avatar", html)
        self.assertIn('id="avatar-reward"', html)
        self.assertIn('data-auto-open="1"', html)
        self.assertIn(TITLE, html)
        self.assertIn("You're on the board for Most Engaged.", html)
        self.assertIn("Pick a new avatar. It's yours to keep.", html)
        self.assertIn("Keep this one", html)
        self.assertIn("Pick later", html)
        self.assertIn("Your reward will wait for you.", html)
        self.assertEqual(html.count('name="rw-avatar"'), 20)
        self.assertIn('id="rw-count" hidden', html)
        grants = self._grants(self.maple)
        self.assertEqual(len(grants), 1)
        self.assertEqual(grants[0]["source"], "engaged")
        self.assertEqual(grants[0]["status"], "pending")
        self.assertIn(":MCF3M", grants[0]["source_ref"])
        again = self._landing(client)
        self.assertIn('data-auto-open="0"', again)
        self.assertIn("Pick your reward", again)
        self.assertEqual(len(self._grants(self.maple)), 1)

    def test_skip_keeps_the_reward_waiting_for_the_next_login(self) -> None:
        """No pick: still pending, and the next login opens it again."""
        first = self._login("Maple")
        self.assertIn('data-auto-open="1"', self._landing(first))
        second = self._next_login("Maple")
        html = self._landing(second)
        self.assertIn('data-auto-open="1"', html)
        grants = self._grants(self.maple)
        self.assertEqual([g["status"] for g in grants], ["pending"])

    def test_non_featured_student_never_sees_it(self) -> None:
        """Aspen is on no card: no pop-up, chip or grant, on the picker or home."""
        client = self._login("Aspen")
        html = self._landing(client)
        self.assertNotIn("avatar-reward", html)
        self.assertNotIn(TITLE, html)
        self.assertNotIn("Pick your reward", html)
        client.post("/student/character", data={"character": "owl"})
        home = client.get("/student/home").get_data(as_text=True)
        self.assertNotIn("avatar-reward", home)
        self.assertNotIn("Reward waiting", home)
        self.assertEqual(self._grants(self.aspen), [])

    def test_pick_grants_the_avatar_permanently(self) -> None:
        """Keep this one: unlocked, set as current, listed as Earned next time."""
        client = self._login("Maple")
        self._landing(client)
        grant = self._grants(self.maple)[0]
        res = self._claim(client, int(grant["id"]), "koala_pencil")
        self.assertEqual(res.status_code, 200, res.get_json())
        body = res.get_json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["already"])
        self.assertEqual(body["avatar_key"], "koala_pencil")
        self.assertEqual(body["src"], "/static/avatars/earned/koala_pencil.svg")
        self.assertIsNone(body["next"])
        self.assertEqual(self.school.earned_avatar_keys(self.class_id, self.maple), ["koala_pencil"])
        self.assertEqual(
            self.school.game.get_student(self.class_id, self.maple).get("character_key"),
            "koala_pencil",
        )
        repeat = self._claim(client, int(grant["id"]), "fox_scarf")
        self.assertEqual(repeat.status_code, 200)
        self.assertTrue(repeat.get_json()["already"])
        self.assertEqual(repeat.get_json()["avatar_key"], "koala_pencil")
        # Next class: the avatar is still theirs, and nothing new is granted.
        later = self._next_login("Maple")
        html = self._landing(later)
        self.assertNotIn('id="avatar-reward"', html)
        self.assertIn('value="koala_pencil"', html)
        self.assertIn('<span class="tag-earned">Earned</span>', html)
        picked = later.post("/student/character", data={"character": "koala_pencil"})
        self.assertEqual(picked.status_code, 302)
        self.assertEqual(len(self._grants(self.maple)), 1)

    def test_no_double_grant_across_logins_or_epochs(self) -> None:
        """Same epoch: one grant however often they log in. New epoch: one more."""
        client = self._login("Maple")
        self._landing(client)
        for _ in range(2):
            client = self._next_login("Maple")
            self._landing(client)
        self.assertEqual(len(self._grants(self.maple)), 1)
        first = self._grants(self.maple)[0]
        self.assertEqual(self._claim(client, int(first["id"]), "bear_medal").status_code, 200)
        self.school.set_school_setting(SETTING_FREEZE_EPOCH, "epoch-2")
        for _ in range(2):
            self._landing(self._next_login("Maple"))
        grants = self._grants(self.maple)
        self.assertEqual(len(grants), 2)
        self.assertEqual([g["status"] for g in grants], ["claimed", "pending"])
        self.assertNotEqual(grants[0]["source_ref"], grants[1]["source_ref"])
        self.assertIn(":epoch-2:", grants[1]["source_ref"])
        self.assertEqual(self.school.earned_avatar_keys(self.class_id, self.maple), ["bear_medal"])
        # The second pick can't be the one they already own.
        client = self._next_login("Maple")
        html = self._landing(client)
        self.assertIn('value="bear_medal" disabled', html)
        owned = self._claim(client, int(grants[1]["id"]), "bear_medal")
        self.assertEqual(owned.status_code, 400)
        self.assertEqual(self._grants(self.maple)[1]["status"], "pending")

    def test_shoutout_once_per_feature_and_blurb_edit_does_not_regrant(self) -> None:
        """Shoutout Aspen: one grant; a new blurb keeps it; re-featuring is new."""
        set_featured_award(
            self.school, teacher_user_id=self.teacher_id,
            class_id=self.class_id, student_id=self.aspen, blurb="Great work",
        )
        html = self._landing(self._login("Aspen"))
        self.assertIn("You're on the board for Shoutout.", html)
        set_featured_award(
            self.school, teacher_user_id=self.teacher_id,
            class_id=self.class_id, student_id=self.aspen, blurb="Even better",
        )
        self._landing(self._next_login("Aspen"))
        self.assertEqual(len(self._grants(self.aspen)), 1)
        set_featured_award(
            self.school, teacher_user_id=self.teacher_id,
            class_id=self.class_id, student_id=self.maple, blurb="",
        )
        set_featured_award(
            self.school, teacher_user_id=self.teacher_id,
            class_id=self.class_id, student_id=self.aspen, blurb="",
        )
        html = self._landing(self._next_login("Aspen"))
        self.assertEqual([g["source"] for g in self._grants(self.aspen)], ["shoutout", "shoutout"])
        self.assertIn("Reward 1 of 2", html)

    def test_two_cards_stack_on_one_screen_and_cap_at_three(self) -> None:
        """Shoutout + Most Engaged: two grants, one dialog; never more than 3 waiting."""
        set_featured_award(
            self.school, teacher_user_id=self.teacher_id,
            class_id=self.class_id, student_id=self.maple, blurb="",
        )
        client = self._login("Maple")
        html = self._landing(client)
        self.assertEqual(html.count('role="dialog"'), 1)
        self.assertIn("Reward 1 of 2", html)
        grants = self._grants(self.maple)
        self.assertEqual(sorted(g["source"] for g in grants), ["engaged", "shoutout"])
        res = self._claim(client, int(grants[0]["id"]), "fox_scarf").get_json()
        self.assertEqual(res["next"]["grant_id"], int(grants[1]["id"]))
        self.assertEqual(res["remaining"], 1)
        added = self.school.sync_avatar_reward_grants(
            self.class_id, self.maple,
            [{"source": "engaged", "source_ref": f"x{i}"} for i in range(5)],
            catalogue_size=len(EARNED_CHARACTERS),
        )
        self.assertEqual(added, 2)
        self.assertEqual(len(self.school.pending_avatar_reward_grants(self.class_id, self.maple)), 3)

    def test_ownership_enforced_on_character_and_claim(self) -> None:
        """Only the owner can pick an earned avatar or claim their grant."""
        maple = self._login("Maple")
        self._landing(maple)
        grant = self._grants(self.maple)[0]
        aspen = self._login("Aspen")
        self._landing(aspen)
        self.assertEqual(self._claim(aspen, int(grant["id"]), "fox_scarf").status_code, 404)
        self.assertEqual(self._claim(maple, int(grant["id"]), "fox").status_code, 400)
        self.assertEqual(self._claim(maple, int(grant["id"]), "../x").status_code, 400)
        self.assertEqual(self._claim(maple, int(grant["id"]), "fox_scarf").status_code, 200)
        refused = aspen.post("/student/character", data={"character": "fox_scarf"})
        self.assertEqual(refused.status_code, 200)
        self.assertIn("Choose an avatar.", refused.get_data(as_text=True))
        self.assertIsNone(self.school.game.get_student(self.class_id, self.aspen).get("character_key"))
        self.assertEqual(self.school.earned_avatar_keys(self.class_id, self.aspen), [])
        anon = self.app.test_client()
        self.assertIn(self._claim(anon, int(grant["id"]), "owl_glasses").status_code, (302, 401, 403))

    def test_never_shown_once_the_class_is_under_way(self) -> None:
        """Out of the waiting room (game, Meet, media...): grant kept, no pop-up or chip."""
        self.assertTrue(self.school.live_session_reward_window_open(self.live_id))
        # Stand-in for "the class is under way": the app's own waiting-room
        # test (game live, Meet/round/play, media or canvas frames, ...).
        with patch.object(type(self.school), "_session_left_waiting_room", return_value=True):
            self.assertFalse(self.school.live_session_reward_window_open(self.live_id))
            client = self._login("Maple")
            html = self._landing(client)
            self.assertNotIn('id="avatar-reward"', html)
            self.assertNotIn("Pick your reward", html)
            client.post("/student/character", data={"character": "owl"})
            home = client.get("/student/home").get_data(as_text=True)
            self.assertNotIn('id="avatar-reward"', home)
            self.assertNotIn("Reward waiting", home)
        self.assertEqual([g["status"] for g in self._grants(self.maple)], ["pending"])
        # Back in a waiting room (next class): it opens.
        html = self._landing(self._next_login("Maple"))
        self.assertIn('data-auto-open="1"', html)

    def test_home_shows_it_on_a_rejoin_with_an_avatar_already_set(self) -> None:
        """Avatar already picked: the pop-up and the Reward waiting chip are on home."""
        self.school.game.set_character(self.class_id, self.maple, "owl")
        client = self._login("Maple")
        page = client.get("/student/home")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="avatar-reward"', html)
        self.assertIn('data-auto-open="1"', html)
        self.assertIn("Reward waiting", html)
        self.assertIn('src="/static/avatar_reward.js"', html)

    def test_removed_student_loses_rows_so_a_reused_id_gets_nothing(self) -> None:
        """Roster delete drops grant rows (SQLite can hand the id to someone new)."""
        client = self._login("Maple")
        self._landing(client)
        grant = self._grants(self.maple)[0]
        self._claim(client, int(grant["id"]), "owl_glasses")
        self.assertEqual(self.school.delete_avatar_reward_grants(self.class_id, [self.maple]), 1)
        self.assertEqual(self.school.earned_avatar_keys(self.class_id, self.maple), [])

    def test_schema_is_created_on_an_existing_file(self) -> None:
        """Reopening an existing SQLite file keeps the table (CREATE IF NOT EXISTS)."""
        path = Path(self.tmp.name) / "lloves.sqlite"
        again = create_app(db_path=path, data_dir=Path(self.tmp.name), testing=True)
        school = again.config["SCHOOL_DB"]
        try:
            cols = {
                row["name"]
                for row in school.conn.execute("PRAGMA table_info(avatar_reward_grants)")
            }
        finally:
            school.close()
        self.assertTrue(
            {"class_id", "student_id", "source", "source_ref", "status", "avatar_key"} <= cols
        )

    # MED-1: JOIN with Minds-On idle only ----------------------------------

    def test_med1_waiting_room_minds_on_keeps_it_open(self) -> None:
        """JOIN with the waiting-room Minds-On showing is still the reward window."""
        self.school.ensure_waiting_room_minds_on(self.live_id)
        self.assertTrue(self.school.live_session_reward_window_open(self.live_id))
        client = self._login("Maple")
        self.assertIn('data-auto-open="1"', self._landing(client))
        self.assertIs(self._state(client).get("reward_window"), True)

    def test_med1_pushed_question_in_join_no_popup_and_claim_409(self) -> None:
        """A teacher-pushed question in JOIN: no auto-open, no chip, claim 409."""
        open_client = self._login("Maple")
        self.assertIn('data-auto-open="1"', self._landing(open_client))
        self._push_question()
        self.assertFalse(self.school.live_session_reward_window_open(self.live_id))
        # The page that was already open learns it from the state poll.
        self.assertIs(self._state(open_client).get("reward_window"), False)
        self._assert_shut(open_client, self.maple)
        # Question cleared, still JOIN: the window opens again.
        self.school.clear_active_live_prompt(self.live_id)
        self.assertTrue(self.school.live_session_reward_window_open(self.live_id))
        self.assertIs(self._state(open_client).get("reward_window"), True)

    def test_med1_fresh_join_while_a_question_is_pushed(self) -> None:
        """Joining while the question is up: grant recorded, nothing shown."""
        self._push_question()
        client = self._login("Maple")
        self._assert_shut(client, self.maple)

    def test_med1_teams_counts_as_live(self) -> None:
        """TEAMS stage: no auto-open, no chip, claim 409."""
        client = self._login("Maple")
        self.assertIn('data-auto-open="1"', self._landing(client))
        self.assertEqual(self._advance(), "teams")
        self.assertFalse(self.school.live_session_reward_window_open(self.live_id))
        self._assert_shut(client, self.maple)

    def test_med1_meet_is_live(self) -> None:
        """MEET (and later): no auto-open, no chip, claim 409."""
        client = self._login("Maple")
        self._landing(client)
        self.assertEqual(self._advance(), "teams")
        self.assertEqual(self._advance(), "meet")
        self.assertFalse(self.school.live_session_reward_window_open(self.live_id))
        self._assert_shut(client, self.maple)

    def test_med1_client_closes_on_reward_window_or_409(self) -> None:
        """avatar_reward.js shuts on is-reward-shut / reward_window false / 409."""
        js = (LMS_DIR / "static" / "avatar_reward.js").read_text(encoding="utf-8")
        self.assertIn('classList.contains("is-reward-shut")', js)
        self.assertIn("data.reward_window === false", js)
        self.assertIn("res.status === 409", js)
        portal = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        self.assertIn('body.classList.toggle("is-reward-shut", !payload.reward_window)', portal)
        css = (LMS_DIR / "static" / "student-portal.css").read_text(encoding="utf-8")
        self.assertIn(".me-meet-chip[hidden] {\n  display: none;", css)

    # MED-2: a reused student id never inherits the Shoutout ---------------

    def _feature_zara(self) -> int:
        """Ops repro setup: Zara (highest id) gets the Shoutout and claims it."""
        self._end_live()
        zara = self._add_student("Zara")
        set_featured_award(
            self.school, teacher_user_id=self.teacher_id,
            class_id=self.class_id, student_id=zara, blurb="",
        )
        self._run_live()
        client = self._login("Zara")
        self._landing(client)
        grant = [g for g in self._grants(zara) if g["source"] == "shoutout"][0]
        self.assertEqual(self._claim(client, int(grant["id"]), "lion_cub_laurel").status_code, 200)
        self._end_live()
        return zara

    def _assert_yuri_gets_nothing(self, zara: int) -> None:
        self._run_live()
        client = self._login("Yuri")
        html = self._landing(client)
        self.assertNotIn('id="avatar-reward"', html)
        self.assertEqual(self._grants(zara), [])
        self.assertEqual(self.school.earned_avatar_keys(self.class_id, zara), [])
        self.assertNotIn(
            zara, [r["student_id"] for r in reward_appearances(self.school)]
        )
        award = build_celebration_board(self.school)["cards"][0]
        self.assertFalse(award.get("name"))
        refused = client.post("/student/character", data={"character": "lion_cub_laurel"})
        self.assertIn("Choose an avatar.", refused.get_data(as_text=True))
        clear_public_celebration_memo()
        board = self.app.test_client().get("/api/celebrations").get_json()
        self.assertNotIn("Yuri", json.dumps(board))

    def test_med2_deleted_featured_student_id_reused_by_new_student(self) -> None:
        """Delete featured Zara, add Yuri on her id: no Shoutout, grant or claim."""
        zara = self._feature_zara()
        res = self.staff.post(
            f"/api/classes/{self.class_id}/students/delete", json={"student_id": zara}
        )
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True)[:200])
        self.assertEqual(self.school.get_school_setting(SETTING_FEATURED_AWARD, ""), "")
        self.assertEqual(self.school.get_school_setting(SETTING_FEATURED_AWARD_ID, ""), "")
        self.assertEqual(self._add_student("Yuri"), zara)  # SQLite reused the id
        self._assert_yuri_gets_nothing(zara)

    def test_med2_roster_replace_drops_the_featured_student(self) -> None:
        """Roster replace that drops featured Zara clears the Shoutout too."""
        zara = self._feature_zara()
        res = self.staff.put(
            f"/api/staff/classes/{self.class_id}/roster",
            json={"codenames": ["Maple", "Aspen"]},
        )
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True)[:200])
        self.assertEqual(self.school.get_school_setting(SETTING_FEATURED_AWARD, ""), "")
        self.assertEqual(self._add_student("Yuri"), zara)
        self._assert_yuri_gets_nothing(zara)

    def test_med2_stale_pointer_fails_the_fingerprint(self) -> None:
        """Even if the pointer survives (no hook ran), the fp check refuses Yuri."""
        zara = self._feature_zara()
        self.school.game.delete_student(self.class_id, zara)  # no app hook
        self.school.delete_avatar_reward_grants(self.class_id, [zara])
        self.assertNotEqual(self.school.get_school_setting(SETTING_FEATURED_AWARD, ""), "")
        self.assertEqual(self._add_student("Yuri"), zara)
        self._assert_yuri_gets_nothing(zara)

    def test_med2_legacy_shoutout_is_pinned_before_reuse(self) -> None:
        """A pre-#221 Shoutout (no fp) is pinned on first read, then guarded."""
        self._end_live()
        zara = self._add_student("Zara")
        self.school.set_school_setting(
            SETTING_FEATURED_AWARD,
            json.dumps({"class_id": self.class_id, "student_id": zara, "blurb": "old"}),
        )
        self.assertIn(zara, [r["student_id"] for r in reward_appearances(self.school)])
        self.assertTrue(json.loads(self.school.get_school_setting(SETTING_FEATURED_AWARD_ID))["fp"])
        self.school.game.delete_student(self.class_id, zara)  # no app hook
        self.assertEqual(self._add_student("Yuri"), zara)
        self._assert_yuri_gets_nothing(zara)

    # LOW-4: one Shoutout, one grant, across a rollback ---------------------

    def _shoutout_grants(self, student_id: int) -> list[dict]:
        return [g for g in self._grants(student_id) if g["source"] == "shoutout"]

    def test_low4_rollback_rewrite_then_roll_forward_does_not_regrant(self) -> None:
        """Base rewrites the setting without featured_at: still one grant."""
        set_featured_award(
            self.school, teacher_user_id=self.teacher_id,
            class_id=self.class_id, student_id=self.aspen, blurb="Great work",
        )
        self._landing(self._login("Aspen"))
        first = self._shoutout_grants(self.aspen)
        self.assertEqual(len(first), 1)
        self.assertNotIn(":c", first[0]["source_ref"])
        # Rollback build edits the blurb: only class_id/student_id/blurb remain.
        legacy = {"class_id": self.class_id, "student_id": self.aspen, "blurb": "Edited on base"}
        self.school.set_school_setting(SETTING_FEATURED_AWARD, json.dumps(legacy))
        self._landing(self._next_login("Aspen"))
        self.assertEqual(self._shoutout_grants(self.aspen), first)
        # Even with the identity setting gone, the fallback key cannot mint.
        self.school.set_school_setting(SETTING_FEATURED_AWARD_ID, "")
        self._landing(self._next_login("Aspen"))
        self.assertEqual(self._shoutout_grants(self.aspen), first)
        # Back on head, a blurb edit keeps the same Shoutout.
        set_featured_award(
            self.school, teacher_user_id=self.teacher_id,
            class_id=self.class_id, student_id=self.aspen, blurb="Edited on head",
        )
        self._landing(self._next_login("Aspen"))
        self.assertEqual(len(self._shoutout_grants(self.aspen)), 1)

    def test_low4_fallback_grant_then_featured_at_key_does_not_regrant(self) -> None:
        """Fallback grant made after featured_at: the featured_at key is the same Shoutout."""
        set_featured_award(
            self.school, teacher_user_id=self.teacher_id,
            class_id=self.class_id, student_id=self.aspen, blurb="",
        )
        stamped = self.school.get_school_setting(SETTING_FEATURED_AWARD)
        stamped_id = self.school.get_school_setting(SETTING_FEATURED_AWARD_ID)
        legacy = {"class_id": self.class_id, "student_id": self.aspen, "blurb": ""}
        self.school.set_school_setting(SETTING_FEATURED_AWARD, json.dumps(legacy))
        self.school.set_school_setting(SETTING_FEATURED_AWARD_ID, "")
        self._landing(self._login("Aspen"))
        grants = self._shoutout_grants(self.aspen)
        self.assertEqual([g["source_ref"] for g in grants], [f"shoutout:c{self.class_id}:s{self.aspen}"])
        self.school.set_school_setting(SETTING_FEATURED_AWARD, stamped)
        self.school.set_school_setting(SETTING_FEATURED_AWARD_ID, stamped_id)
        self._landing(self._next_login("Aspen"))
        self.assertEqual(self._shoutout_grants(self.aspen), grants)

    def test_low4_a_real_new_shoutout_still_grants(self) -> None:
        """Legacy Shoutout grant, then featured again later: that is a new reward."""
        legacy = {"class_id": self.class_id, "student_id": self.aspen, "blurb": ""}
        self.school.set_school_setting(SETTING_FEATURED_AWARD, json.dumps(legacy))
        self._landing(self._login("Aspen"))
        self.assertEqual(len(self._shoutout_grants(self.aspen)), 1)
        with self.school._lock:  # that legacy grant was made last week
            self.school.conn.execute(
                "UPDATE avatar_reward_grants SET created_at = '2026-09-25T10:00:00'"
                " WHERE class_id = ? AND student_id = ?",
                (self.class_id, self.aspen),
            )
            self.school.conn.commit()
        for sid in (self.maple, self.aspen):
            set_featured_award(
                self.school, teacher_user_id=self.teacher_id,
                class_id=self.class_id, student_id=sid, blurb="",
            )
        self._landing(self._next_login("Aspen"))
        self.assertEqual(len(self._shoutout_grants(self.aspen)), 2)


def _pg_admin_url() -> str:
    return os.environ.get("LIVE_PRESENCE_TEST_URL", "").strip()


@unittest.skipUnless(_pg_admin_url(), "LIVE_PRESENCE_TEST_URL is not set")
class AvatarRewardPostgresPresenceTests(AvatarRewardTests):
    """The same flow with live presence on Postgres (as CI runs the suite)."""

    def _presence_url(self) -> str:
        import uuid
        from urllib.parse import urlsplit, urlunsplit

        import psycopg

        admin = _pg_admin_url()
        self.pg_db = f"lloves_rw_{uuid.uuid4().hex[:8]}"
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{self.pg_db}"')
        self.addCleanup(self._drop_pg, admin)
        parts = urlsplit(admin)
        return urlunsplit((parts.scheme, parts.netloc, f"/{self.pg_db}", parts.query, ""))

    def _drop_pg(self, admin: str) -> None:
        import psycopg

        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{self.pg_db}" WITH (FORCE)')

    def test_presence_is_on_postgres(self) -> None:
        """Sanity: this class really runs against Postgres presence."""
        self.assertEqual(self.school.live_presence_status(), "postgres")

    def test_schema_is_created_on_an_existing_file(self) -> None:
        """Covered by the sqlite run (a second app would open a second pool)."""


class AvatarRewardJsTests(unittest.TestCase):
    """Static checks on the pop-up script and copy."""

    def test_script_uses_wonder_copy_and_no_innerhtml(self) -> None:
        js = (LMS_DIR / "static" / "avatar_reward.js").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", js)
        for text in ("Saving…", "Keep this one", "You're on the board for ", "Reward "):
            self.assertIn(text, js)
        partial = (LMS_DIR / "templates" / "student" / "_avatar_reward.html").read_text(
            encoding="utf-8"
        )
        for text in (
            "You made Celebrations!",
            "Pick a new avatar. It's yours to keep.",
            "Keep this one",
            "Pick later",
            "Your reward will wait for you.",
            "Added to your avatars.",
            "Couldn't save that. Try again.",
        ):
            self.assertIn(text, partial)


if __name__ == "__main__":
    unittest.main()

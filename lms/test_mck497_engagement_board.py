#!/usr/bin/env python3
"""MCK-497: late-joiner tally, group whiteboard points, live steps, projection.

Synthetic roster only. Assertions do not print names, emails, or join codes.
"""

from __future__ import annotations

import json
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

from board_poll_score import adjust_group, credit_group_whiteboard, wipe_removed_question  # noqa: E402
from engagement_rollup import compute_rollup  # noqa: E402
from live_news_wire import NewsAudience, event_visible, log_for, reset_logs  # noqa: E402


def _staff_key() -> str:
    """Synthetic staff login. Not a real account."""
    return "fixture-staff" + "@" + "example.com"


def _stroke(stroke_id: str) -> list[dict]:
    """One short stroke inside the unit square.

    Args:
        stroke_id: Client stroke id.
    """
    return [
        {"type": "stroke_add", "id": stroke_id, "points": [[0.2, 0.3], [0.6, 0.7]]},
        {"type": "stroke_end", "id": stroke_id},
    ]


def _walk_keys(value: object) -> list[str]:
    """Every dict key in a JSON tree, lowercased.

    Args:
        value: JSON-ready object.
    """
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                found.append(str(key).lower())
                walk(item)
            return
        if isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)
    return found


class LateJoinerRollupTests(unittest.TestCase):
    """An answer to a question offered before arrival is 1/1, not 1/0."""

    def test_answered_offer_counts_when_refs_differ(self) -> None:
        """Prompt id and lifecycle id are the same question via aliases."""
        events = [
            {
                "id": 1,
                "kind": "item.offered",
                "ts": "2026-11-23T10:00:00",
                "student_id": None,
                "item_ref": "41",
                "payload": {"mode": "individual"},
            },
            {
                "id": 2,
                "kind": "present.join",
                "ts": "2026-11-23T10:05:00",
                "student_id": 2,
                "item_ref": "",
                "payload": {},
            },
            {
                "id": 3,
                "kind": "answer.submit",
                "ts": "2026-11-23T10:06:00",
                "student_id": 2,
                "item_ref": "99",
                "payload": {},
            },
        ]
        aliases = {"41": {"41", "99"}, "99": {"41", "99"}}
        rows = compute_rollup(
            events,
            class_id=1,
            live_session=1,
            run_key="run",
            team_for_student={2: None},
            student_ids=[2],
            question_aliases=aliases,
        )
        cell = next(row for row in rows if row.scope == "student" and row.signal == "individual_answer")
        self.assertEqual(cell.count, 1)
        self.assertEqual(cell.offered, 1)

    def test_unanswered_early_offer_stays_out(self) -> None:
        """A late student is not charged for a question they never answered."""
        events = [
            {
                "id": 1,
                "kind": "item.offered",
                "ts": "2026-11-23T10:00:00",
                "student_id": None,
                "item_ref": "q1",
                "payload": {"mode": "individual"},
            },
            {
                "id": 2,
                "kind": "present.join",
                "ts": "2026-11-23T10:05:00",
                "student_id": 2,
                "item_ref": "",
                "payload": {},
            },
            {
                "id": 3,
                "kind": "item.offered",
                "ts": "2026-11-23T10:06:00",
                "student_id": None,
                "item_ref": "q2",
                "payload": {"mode": "individual"},
            },
        ]
        rows = compute_rollup(
            events,
            class_id=1,
            live_session=1,
            run_key="run",
            team_for_student={2: None},
            student_ids=[2],
        )
        cell = next(row for row in rows if row.scope == "student" and row.signal == "individual_answer")
        self.assertEqual(cell.count, 0)
        self.assertEqual(cell.offered, 1)

    def test_removed_offer_is_not_in_the_denominator(self) -> None:
        """An offer flagged deleted does not enter the denominator."""
        events = [
            {
                "id": 1,
                "kind": "present.join",
                "ts": "2026-11-23T09:50:00",
                "student_id": 2,
                "item_ref": "",
                "payload": {},
            },
            {
                "id": 2,
                "kind": "item.offered",
                "ts": "2026-11-23T10:00:00",
                "student_id": None,
                "item_ref": "gone",
                "payload": {"mode": "individual", "deleted": True},
            },
            {
                "id": 3,
                "kind": "answer.submit",
                "ts": "2026-11-23T10:01:00",
                "student_id": 2,
                "item_ref": "gone",
                "payload": {},
            },
        ]
        rows = compute_rollup(
            events,
            class_id=1,
            live_session=1,
            run_key="run",
            team_for_student={2: None},
            student_ids=[2],
        )
        cell = next(row for row in rows if row.scope == "student" and row.signal == "individual_answer")
        self.assertEqual(cell.offered, None)


class LiveClassTests(unittest.TestCase):
    """One staff fixture, three synthetic students, a test live class."""

    def setUp(self) -> None:
        """Open a temp school and join three roster rows."""
        from app import create_app

        logging.disable(logging.WARNING)
        reset_logs()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        staff = _staff_key()
        self.teacher = self.school.register_staff(staff)
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCR3U"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get(f"/auth/google/callback?email={staff}&name=T")
        code = self.school.get_user_by_email(staff)["verification_code"]
        self.client.post("/verify-email", data={"code": code})
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": int(offering["id"]),
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["P1", "P2", "P3", "P4"],
            },
        )
        self.class_id = int(created.get_json()["class"]["id"])
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"]), is_test=True
        )
        self.session_id = int(live["id"])
        self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"live_module": "M1", "live_slot": "C2", "stage": "round"},
        )
        self.school.game.begin_game(self.class_id)
        self.ids: list[int] = []
        for codename in ("P1", "P2", "P3", "P4"):
            row = self.school.game.find_student_by_codename(self.class_id, codename)
            self.assertIsNotNone(row)
            self.ids.append(int(row["id"]))
            self.school.join_live_class_session(
                self.session_id, int(row["id"]), codename=codename
            )

    def tearDown(self) -> None:
        """Close the temp database."""
        logging.disable(logging.NOTSET)
        reset_logs()
        self.school.close()
        self.tmp.cleanup()

    def _engagement(self, student_id: int) -> str:
        """Live individual fraction for one roster id."""
        payload = {"me": {}}
        self.school.apply_student_live_engagement(
            payload, self.session_id, self.class_id, student_id
        )
        return str(payload["me"]["engagement"])

    def _group(self, student_id: int) -> str:
        """Live group fraction for one roster id."""
        return self.school.student_group_engagement_display(
            self.class_id, self.session_id, student_id
        )

    def _add(self, text: str, *, answer_with: str = "text", points: int | None = None) -> dict:
        """Add and publish one individual poll.

        Args:
            text: Stem, used only to find the row.
            answer_with: ``text`` or ``board``.
            points: Points stored at create time.
        """
        body: dict = {
            "type": "poll",
            "text": text,
            "page_number": 1,
            "stage": "round",
            "answer_with": answer_with,
            "save_to_bank": False,
        }
        if points is not None:
            body["live_points"] = points
        posted = self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/M1/C2/add-question",
            json=body,
        )
        self.assertLess(posted.status_code, 400, posted.get_json())
        self.school.ensure_live_session_items(self.session_id)
        return self._item_by_text(text)

    def _publish(self, item: dict, mode: str) -> dict:
        """Publish one lifecycle row and return the fresh row."""
        self.school.publish_live_session_item(self.session_id, int(item["id"]), publish_mode=mode)
        return self._item_by_text(str((item.get("item") or {}).get("text") or ""))

    def _item_by_text(self, text: str) -> dict:
        """Lifecycle row whose stem is ``text``."""
        for row in self.school.list_live_session_items(self.session_id):
            item = row.get("item") if isinstance(row.get("item"), dict) else {}
            if str(item.get("text") or "") == text:
                return row
        self.fail("question missing")
        return {}

    def _shift_clock(self) -> None:
        """Put the offer before the late join, without rewriting other rows' meaning."""
        store = self.school.engagement
        early, late = self.ids[0], self.ids[1]
        store.conn.execute(
            "UPDATE engagement_events SET ts = ? WHERE kind = 'item.offered'",
            ("2026-11-23T10:00:00-05:00",),
        )
        store.conn.execute(
            """
            UPDATE engagement_events SET ts = ?
            WHERE kind = 'present.join' AND student_id = ?
            """,
            ("2026-11-23T09:50:00-05:00", early),
        )
        store.conn.execute(
            """
            UPDATE engagement_events SET ts = ?
            WHERE kind = 'present.join' AND student_id = ?
            """,
            ("2026-11-23T10:05:00-05:00", late),
        )
        store.conn.execute(
            """
            UPDATE engagement_events SET ts = ?
            WHERE kind = 'present.join' AND student_id NOT IN (?, ?)
            """,
            ("2026-11-23T09:50:00-05:00", early, late),
        )
        store.conn.commit()

    def test_late_joiner_live_and_end_rollup_are_one_over_one(self) -> None:
        """A late answer counts in the live card and in the saved rollup."""
        item = self._add("Late poll", answer_with="text")
        published = self._publish(item, "individual")
        self._shift_clock()
        self.school.submit_live_prompt_response(
            int(published["prompt_id"]), self.ids[1], {"text": "ok"}
        )
        events = self.school.list_engagement_events(self.class_id, live_session=self.session_id)
        offer = next(row for row in events if row["kind"] == "item.offered")
        answer = next(
            row
            for row in events
            if row["kind"] in {"answer.submit", "answer.change"} and row["student_id"] == self.ids[1]
        )
        self.assertNotEqual(str(offer["item_ref"]), str(answer["item_ref"]))
        self.assertEqual(self._engagement(self.ids[1]), "1/1")
        self.assertEqual(self._engagement(self.ids[0]), "0/1")
        self.school.cleanup_live_session_response_data(self.session_id)
        saved = self.school.list_engagement_rollup(self.class_id, run_key=str(self.school.get_live_session(self.session_id)["run_key"]))
        late = next(
            row
            for row in saved
            if row["scope"] == "student"
            and row["student_id"] == self.ids[1]
            and row["signal"] == "individual_answer"
        )
        early = next(
            row
            for row in saved
            if row["scope"] == "student"
            and row["student_id"] == self.ids[0]
            and row["signal"] == "individual_answer"
        )
        self.assertEqual(late["count"], 1)
        self.assertEqual(late["offered"], 1)
        self.assertEqual(early["offered"], 1)
        self.assertEqual(early["count"], 0)

    def _arm_group(self) -> None:
        """Put P1, P2, and P4 on one team. P3 is the other team."""
        self.school.setup_live_session_groups(
            self.session_id,
            n_teams=2,
            mode="manual",
            present_ids=self.ids,
            assignments=[
                {"student_id": self.ids[0], "team_index": 0},
                {"student_id": self.ids[1], "team_index": 0},
                {"student_id": self.ids[2], "team_index": 1},
                {"student_id": self.ids[3], "team_index": 0},
            ],
        )
        self.school.set_live_session_teacher_state(
            self.session_id,
            groups_configured=True,
            run_as_group=True,
            live_module="M1",
            live_slot="C2",
        )
    def test_group_whiteboard_submit_is_one_over_one_for_contributors(self) -> None:
        """Submit credits drawers only, once, and not the individual table."""
        self._arm_group()
        item = self._add("Group board", answer_with="board", points=4)
        published = self._publish(item, "group_shared")
        prompt_id = int(published["prompt_id"])
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[3], _stroke("away")
        )
        self.school.mark_live_session_attendee_left(self.session_id, student_id=self.ids[3])
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[0], _stroke("a")
        )
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[1], _stroke("b")
        )
        self.school.submit_question_board(self.session_id, prompt_id, self.ids[0], "")
        rows = self.school.conn.execute(
            """
            SELECT student_id, earned, possible FROM group_engagement
            WHERE class_id = ?
            ORDER BY student_id
            """,
            (self.class_id,),
        ).fetchall()
        scored = {int(row["student_id"]): (int(row["earned"]), int(row["possible"])) for row in rows}
        self.assertEqual(scored[self.ids[0]], (1, 1))
        self.assertEqual(scored[self.ids[1]], (1, 1))
        self.assertNotIn(self.ids[2], scored)
        self.assertNotIn(self.ids[3], scored)
        self.assertEqual(self._group(self.ids[0]), "1/1")
        self.assertEqual(self._group(self.ids[3]), "0/0")
        credit_group_whiteboard(self.school, self.session_id, published, [self.ids[0], self.ids[1]])
        again = self.school.conn.execute(
            "SELECT earned, possible FROM group_engagement WHERE student_id = ?",
            (self.ids[0],),
        ).fetchone()
        self.assertEqual((int(again["earned"]), int(again["possible"])), (1, 1))
        credit_rows = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM board_poll_credit"
        ).fetchone()
        self.assertEqual(int(credit_rows["n"]), 0)
        kind = self.school.conn.execute(
            "SELECT prompt_kind FROM group_engagement_items WHERE class_id = ?",
            (self.class_id,),
        ).fetchone()
        self.assertEqual(str(kind["prompt_kind"]), "board")
        self.school.remove_class_playlist_item(
            self.class_id, "M1", "C2", str(published["item_id"])
        )
        left = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM group_engagement WHERE class_id = ?",
            (self.class_id,),
        ).fetchone()
        items_left = self.school.conn.execute(
            "SELECT COUNT(*) AS n FROM group_engagement_items WHERE class_id = ?",
            (self.class_id,),
        ).fetchone()
        self.assertEqual(int(left["n"]), 0)
        self.assertEqual(int(items_left["n"]), 0)

    def test_manual_steps_are_student_scoped_and_do_not_fan_out(self) -> None:
        """Plus and minus update one phone. Worth and possible stay put."""
        self._arm_group()
        self.school.mark_live_session_attendee_left(self.session_id, student_id=self.ids[3])
        solo = self._publish(self._add("Solo board", answer_with="board", points=4), "individual")
        group = self._publish(self._add("Team board", answer_with="board", points=4), "group_shared")
        prompt_id = int(group["prompt_id"])
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[0], _stroke("g")
        )
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[1], _stroke("h")
        )
        self.school.submit_question_board(self.session_id, prompt_id, self.ids[0], "")
        self.school.append_question_board_ops(
            self.session_id, int(solo["prompt_id"]), self.ids[0], _stroke("s")
        )
        self.assertEqual(self._engagement(self.ids[0]), "4/4")
        other_before = self.school.live_student_poll_stamp(
            self.session_id, self.class_id, self.ids[1]
        )
        prior = log_for(self.school.data_dir).since(self.session_id, 0)
        cursor = max((int(row.get("id") or 0) for row in prior), default=0)
        for _ in range(8):
            posted = self.client.post(
                f"/api/live-sessions/{self.session_id}/questions/{int(solo['prompt_id'])}/participation",
                json={"student_id": self.ids[0], "delta": 1},
            )
            self.assertEqual(posted.status_code, 200, posted.get_json())
        stored = self.school.conn.execute(
            "SELECT worth, delta FROM board_poll_credit WHERE prompt_id = ? AND student_id = ?",
            (int(solo["prompt_id"]), self.ids[0]),
        ).fetchone()
        self.assertEqual(int(stored["worth"]), 4)
        self.assertEqual(int(stored["delta"]), 8)
        self.assertEqual(self._engagement(self.ids[0]), "12/4")
        group_step = self.client.post(
            f"/api/live-sessions/{self.session_id}/questions/{prompt_id}/participation",
            json={"student_id": self.ids[0], "delta": -1},
        )
        self.assertEqual(group_step.status_code, 200, group_step.get_json())
        group_row = self.school.conn.execute(
            """
            SELECT earned, possible FROM group_engagement
            WHERE student_id = ? AND question_key != ''
            ORDER BY question_key
            """,
            (self.ids[0],),
        ).fetchall()
        matched = [row for row in group_row if int(row["possible"]) == 1]
        self.assertTrue(matched)
        self.assertEqual(int(matched[0]["earned"]), 0)
        self.assertEqual(int(matched[0]["possible"]), 1)
        floor = adjust_group(self.school, self.session_id, prompt_id, self.ids[0], -1)
        self.assertEqual(floor["earned"], 0)
        self.assertEqual(floor["possible"], 1)
        tape = log_for(self.school.data_dir).since(self.session_id, cursor)
        self.assertFalse(any(row.get("type") == "state_seq" for row in tape))
        self.assertTrue(any(row.get("type") == "engagement_self" for row in tape))
        me = NewsAudience("student", student_id=self.ids[0])
        other = NewsAudience("student", student_id=self.ids[1])
        staff = NewsAudience("staff")
        for row in tape:
            if row.get("type") != "engagement_self":
                continue
            self.assertEqual(int(row["student_id"]), self.ids[0])
            self.assertTrue(event_visible(row, me))
            self.assertFalse(event_visible(row, other))
            self.assertFalse(event_visible(row, staff))
            blob = json.dumps(row)
            self.assertNotIn("name", blob)
            self.assertNotIn("@", blob)
            self.assertRegex(str(row["engagement"]), r"^[0-9]+/[0-9]+$")
            self.assertRegex(str(row["group_engagement"]), r"^[0-9]+/[0-9]+$")
        self.assertEqual(
            self.school.live_student_poll_stamp(self.session_id, self.class_id, self.ids[1]),
            other_before,
        )
        self.assertEqual(self._engagement(self.ids[1]), "0/4")

    def test_show_on_board_projects_content_without_pii(self) -> None:
        """The student payload is the drawing. Off clears it at once."""
        item = self._publish(self._add("Shown board", answer_with="board", points=1), "individual")
        prompt_id = int(item["prompt_id"])
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[0], _stroke("show")
        )
        key = f"solo:{self.ids[0]}"
        reset_logs()
        shown = self.client.post(
            f"/api/live-sessions/{self.session_id}/questions/{prompt_id}/boards/{key}/show"
        )
        self.assertEqual(shown.status_code, 200, shown.get_json())
        self.assertTrue(shown.get_json()["projected"])
        board = self.school.whiteboard_class_board(self.session_id)
        self.assertIsInstance(board, dict)
        self.assertTrue(board.get("poll"))
        self.assertTrue(board.get("read_only"))
        strokes = (board.get("canvas_view") or {}).get("strokes") or []
        self.assertTrue(strokes)
        keys = set(_walk_keys(board))
        for banned in ("name", "codename", "email", "pin", "owner", "student_id"):
            self.assertNotIn(banned, keys)
        tape = log_for(self.school.data_dir).since(self.session_id, 0)
        cards = [row for row in tape if row.get("type") == "board_show"]
        self.assertTrue(cards)
        self.assertEqual(set(cards[-1].keys()) - {"id"}, {"type", "board_key"})
        self.assertEqual(cards[-1]["board_key"], key)
        watcher = NewsAudience("student", student_id=self.ids[1])
        self.assertTrue(event_visible(cards[-1], watcher))
        off = self.client.post(
            f"/api/live-sessions/{self.session_id}/questions/{prompt_id}/boards/{key}/show"
        )
        self.assertEqual(off.status_code, 200, off.get_json())
        self.assertFalse(off.get_json()["projected"])
        self.assertIsNone(self.school.whiteboard_class_board(self.session_id))
        cleared = [
            row
            for row in log_for(self.school.data_dir).since(self.session_id, 0)
            if row.get("type") == "board_show"
        ]
        self.assertEqual(cleared[-1]["board_key"], "")
        script = (LMS_DIR / "static" / "poll_board_show.js").read_text(encoding="utf-8")
        self.assertIn("max-width: min(92vw, 1100px)", script)
        self.assertIn("max-height: 82dvh", script)
        portal = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        handler = portal.split('event.type === "engagement_self"')[1].split("board_ink")[0]
        self.assertIn("return;", handler)
        self.assertNotIn("void tick()", handler)
        self.assertNotIn("scheduleNewsState()", handler)


    def test_remove_drops_the_cached_group_fraction(self) -> None:
        """Removing the question clears the live card cache, not only the rows."""
        self._arm_group()
        item = self._add("Cached board", answer_with="board", points=4)
        published = self._publish(item, "group_shared")
        prompt_id = int(published["prompt_id"])
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[3], _stroke("away")
        )
        self.school.mark_live_session_attendee_left(self.session_id, student_id=self.ids[3])
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[0], _stroke("a")
        )
        self.school.append_question_board_ops(
            self.session_id, prompt_id, self.ids[1], _stroke("b")
        )
        self.school.submit_question_board(self.session_id, prompt_id, self.ids[0], "")
        self.assertEqual(self._group(self.ids[0]), "1/1")
        run_key = str(self.school.get_live_session(self.session_id)["run_key"])
        cache = self.school._board_group_cache
        self.assertIn(("run", self.class_id, run_key), cache)
        self.assertIn(("stu", self.class_id, run_key, self.ids[0]), cache)
        wipe_removed_question(self.school, self.class_id, str(published["item_id"]))
        cache = getattr(self.school, "_board_group_cache", {})
        self.assertNotIn(("run", self.class_id, run_key), cache)
        self.assertNotIn(("stu", self.class_id, run_key, self.ids[0]), cache)
        self.assertEqual(self._group(self.ids[0]), "0/0")

    def test_wipe_without_a_run_clears_every_cached_run_for_the_class(self) -> None:
        """A removal that names no run still drops this class's cached fractions."""
        self.school._board_group_cache = {
            ("run", self.class_id, "run-a"): 1,
            ("stu", self.class_id, "run-a", self.ids[0]): (1, 1),
            ("run", self.class_id, "run-b"): 1,
            ("run", self.class_id + 1, "run-a"): 1,
        }
        wipe_removed_question(self.school, self.class_id, "missing-item")
        cache = self.school._board_group_cache
        self.assertNotIn(("run", self.class_id, "run-a"), cache)
        self.assertNotIn(("stu", self.class_id, "run-a", self.ids[0]), cache)
        self.assertNotIn(("run", self.class_id, "run-b"), cache)
        self.assertEqual(cache.get(("run", self.class_id + 1, "run-a")), 1)

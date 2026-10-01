#!/usr/bin/env python3
"""MCK-27: opt-in "Close answers at 0:00" for the SessionTimer.

With the switch on, questions that were open when the timer reached 0:00
close the same way as the teacher's Close button. The server enforces it
on every answer write, so a late answer is rejected even if the teacher
tab is asleep. A question published after the clock ran out stays open.
The switch is off by default.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from datetime import datetime, timedelta
from typing import Any

import test_live_class_backend_state as backend
from live_teacher_state import apply_teacher_state_update, default_teacher_state

STAFF_JS = backend.Path(backend.__file__).resolve().parent / "static" / "staff_ap.js"
STUDENT_JS = STAFF_JS.parent / "student-portal.js"


class TimerCloseTests(backend.LiveBackendStateTests):
    """Reuse the backend fixture. Only the tests below run here."""

    def _publish(self, item_id: str = "q-one", mode: str = "individual") -> dict:
        items = self.school.ensure_live_session_items(self.session_id)
        row = next(r for r in items if r["item_id"] == item_id)
        self.school.set_live_session_teacher_state(
            self.session_id, stage="round", student_view={"questions": "student"}
        )
        return self.school.publish_live_session_item(
            self.session_id, int(row["id"]), publish_mode=mode
        )

    def _set_flag(self, on: bool) -> None:
        self.school.set_live_session_teacher_state(
            self.session_id, timer_closes_answers=on
        )

    def _timer(self, *, ago_sec: int, minutes: int = 1) -> datetime:
        """Start a SessionTimer that began ``ago_sec`` ago; return its 0:00."""
        self.school.game.start_session_timer(self.class_id, minutes)
        started = (datetime.now() - timedelta(seconds=ago_sec)).replace(microsecond=0)
        with self.school.game._lock:
            self.school.game.conn.execute(
                "UPDATE games SET round_started_at = ? WHERE class_id = ? "
                "AND status != 'ended'",
                (started.isoformat(), self.class_id),
            )
            self.school.game.conn.commit()
        return started + timedelta(minutes=minutes)

    def _backdate(self, item: dict, sec: int = 120) -> None:
        """Pretend the question was published ``sec`` seconds ago."""
        stamp = (datetime.now() - timedelta(seconds=sec)).replace(microsecond=0)
        with self.school._lock:
            self.school.conn.execute(
                "UPDATE live_session_items SET published_at = ? WHERE id = ?",
                (stamp.isoformat(), int(item["id"])),
            )
            self.school.conn.commit()

    def _answer(self, item: dict, student_index: int = 0) -> Any:
        prompt = self.school._prompt_for_live_item(item)
        assert prompt is not None
        return self.school.submit_live_prompt_response(
            int(prompt["id"]), self.student_ids[student_index], {"choice": "A"}
        )

    def _status(self, item: dict) -> str:
        return str(
            self.school.get_live_session_item(self.session_id, int(item["id"]))["status"]
        )

    def test_flag_defaults_off_and_round_trips(self) -> None:
        """Off by default; the teacher patch sets and clears it."""
        self.assertFalse(default_teacher_state()["timer_closes_answers"])
        self.assertFalse(
            self.school.live_session_teacher_state_payload(self.session_id)[
                "timer_closes_answers"
            ]
        )
        self._set_flag(True)
        self.assertTrue(
            self.school.live_session_teacher_state_payload(self.session_id)[
                "timer_closes_answers"
            ]
        )
        self._set_flag(False)
        self.assertFalse(
            self.school.live_session_teacher_state_payload(self.session_id)[
                "timer_closes_answers"
            ]
        )
        with self.assertRaises(ValueError):
            apply_teacher_state_update({}, timer_closes_answers="maybe")

    def test_late_answer_is_rejected_when_on(self) -> None:
        """At 0:00 the open question closes and a late answer fails."""
        self._begin_and_join(2)
        item = self._publish()
        self._answer(item, 0)
        self._backdate(item)
        self._set_flag(True)
        self._timer(ago_sec=61)
        with self.assertRaisesRegex(ValueError, "not accepting"):
            self._answer(item, 1)
        self.assertEqual(self._status(item), "closed")
        # The on-time answer is kept in the frozen results.
        results = self.school.live_session_item_results(self.session_id, int(item["id"]))
        self.assertIn("A", json.dumps(results))

    def test_off_by_default_keeps_taking_answers(self) -> None:
        """Without the switch, an expired timer changes nothing (today)."""
        self._begin_and_join(2)
        item = self._publish()
        self._backdate(item)
        self._timer(ago_sec=61)
        self._answer(item, 1)
        self.assertEqual(self._status(item), "active")
        self.assertEqual(self.school.close_answers_if_timer_expired(self.session_id), [])

    def test_running_and_paused_timers_do_not_close(self) -> None:
        """Before 0:00, or while paused, the question stays open."""
        self._begin_and_join(2)
        item = self._publish()
        self._set_flag(True)
        self._timer(ago_sec=10)
        self._answer(item, 0)
        self.assertEqual(self._status(item), "active")
        self.school.game.pause_round_timer(self.class_id)
        self.assertEqual(self.school.close_answers_if_timer_expired(self.session_id), [])
        self._answer(item, 1)
        self.assertEqual(self._status(item), "active")

    def test_question_published_after_zero_stays_open(self) -> None:
        """A new question under a stale 0:00 still takes answers."""
        self._begin_and_join(2)
        self._set_flag(True)
        deadline = self._timer(ago_sec=120)
        item = self._publish()
        self.assertGreater(
            datetime.fromisoformat(str(item["published_at"])), deadline
        )
        self._answer(item, 0)
        self.assertEqual(self._status(item), "active")

    def test_group_vote_after_zero_is_rejected(self) -> None:
        """Group consensus voting closes at 0:00 too."""
        self._begin_and_join(4)
        self._setup_groups()
        self.school.set_live_session_teacher_state(
            self.session_id, groups_configured=True, run_as_group=True
        )
        item = self._publish("q-two", "group_consensus")
        self._backdate(item)
        self._set_flag(True)
        self._timer(ago_sec=61)
        teams = self.school.game.game_state(self.class_id)["teams"]
        member = int(teams[0]["members"][0]["id"])
        with self.assertRaises(ValueError):
            self.school.submit_group_consensus_vote(
                self.session_id, int(item["id"]), member, {"choice": "A"}
            )
        self.assertEqual(self._status(item), "closed")

    def test_teacher_endpoint_closes_once_and_rechecks_clock(self) -> None:
        """POST timer-expired closes only after the server's own 0:00."""
        client = self.app.test_client()
        client.get("/auth/google?portal=staff")
        client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        client.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )
        self._begin_and_join(1)
        item = self._publish()
        self._backdate(item)
        self._set_flag(True)
        self._timer(ago_sec=5)
        early = client.post(f"/api/live-sessions/{self.session_id}/timer-expired", json={})
        self.assertEqual(early.status_code, 200, early.get_data(as_text=True))
        self.assertEqual(early.get_json()["closed"], [])
        self.assertEqual(self._status(item), "active")
        self._timer(ago_sec=61)
        done = client.post(f"/api/live-sessions/{self.session_id}/timer-expired", json={})
        self.assertEqual(done.get_json()["closed"], [int(item["id"])])
        self.assertEqual(self._status(item), "closed")
        again = client.post(f"/api/live-sessions/{self.session_id}/timer-expired", json={})
        self.assertEqual(again.get_json()["closed"], [])
        anon = self.app.test_client().post(
            f"/api/live-sessions/{self.session_id}/timer-expired", json={}
        )
        self.assertIn(anon.status_code, (302, 401, 403))


    # --- Ops gate follow-up (smoke-pr203, 2026-10-01) -------------------

    def _backdate_all_active(self, sec: int = 120) -> None:
        stamp = (datetime.now() - timedelta(seconds=sec)).replace(microsecond=0)
        with self.school._lock:
            self.school.conn.execute(
                "UPDATE live_session_items SET published_at = ? "
                "WHERE live_session_id = ? AND status = 'active'",
                (stamp.isoformat(), self.session_id),
            )
            self.school.conn.commit()

    def _item_by_ref(self, ref: str) -> dict:
        for row in self.school.ensure_live_session_items(self.session_id):
            if str(row.get("item_id") or "").replace("_", "-") == ref:
                return row
        raise AssertionError(f"no {ref} item")

    def _staff_client(self) -> Any:
        client = self.app.test_client()
        client.get("/auth/google?portal=staff")
        client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        client.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )
        return client

    def _close_postcards(self) -> list[dict]:
        from live_news_wire import LiveNewsLog, news_db_path

        tape = LiveNewsLog(news_db_path(self.school.data_dir)).since(
            self.session_id, 0
        )
        return [
            row
            for row in tape
            if (row.get("event") or row).get("type") == "flag_work"
            and (row.get("event") or row).get("kind") == "close"
        ]

    def test_meet_item_survives_zero(self) -> None:
        """H1: 0:00 closes the question but never the Meet team item."""
        self.school.live_class_metadata_for_session = self.original_metadata
        self.school.set_live_session_teacher_state(
            self.session_id, live_module="M1", live_slot="C2"
        )
        self._begin_and_join(2)
        question = self._publish("parabola-a")
        self.school.set_live_session_teacher_state(self.session_id, stage="meet")
        self.school.activate_meet_team_question(self.session_id)
        meet = self._item_by_ref("meet-team")
        self.assertEqual(self._status(meet), "active")
        self._backdate_all_active()
        self._set_flag(True)
        self._timer(ago_sec=61)
        closed = self.school.close_answers_if_timer_expired(self.session_id)
        self.assertEqual(closed, [int(question["id"])])
        self.assertEqual(self._status(question), "closed")
        self.assertEqual(self._status(meet), "active")
        # A Meet answer after 0:00 still goes through (it runs the lazy close).
        prompt = self.school._prompt_for_live_item(
            self.school.get_live_session_item(self.session_id, int(meet["id"]))
        )
        self.assertIsNotNone(prompt)
        self.school.submit_live_prompt_response(
            int(prompt["id"]), self.student_ids[0], {"choice": "A"}
        )
        self.assertEqual(self._status(meet), "active")
        self.assertEqual(self.school.close_answers_if_timer_expired(self.session_id), [])

    def test_teams_spark_survives_zero(self) -> None:
        """H1: the Teams spark is not a lifecycle question either."""
        self.school.live_class_metadata_for_session = self.original_metadata
        self._begin_and_join(2)
        self.school.set_live_session_teacher_state(self.session_id, stage="teams")
        self.school.ensure_teams_spark(self.session_id)
        spark = self._item_by_ref("teams-spark")
        self.school.publish_live_session_item(
            self.session_id, int(spark["id"]), publish_mode="individual"
        )
        self.assertEqual(self._status(spark), "active")
        self._backdate_all_active()
        self._set_flag(True)
        self._timer(ago_sec=61)
        self.assertEqual(self.school.close_answers_if_timer_expired(self.session_id), [])
        self.assertEqual(self._status(spark), "active")

    def test_same_second_as_zero_stays_open(self) -> None:
        """L1: published in the 0:00 second (e.g. +0.3 s) is not closed."""
        self._begin_and_join(2)
        self._set_flag(True)
        deadline = self._timer(ago_sec=61)
        late = self._publish("q-one")
        early = self._publish("q-two")
        with self.school._lock:
            for item, stamp in (
                (late, deadline),
                (early, deadline - timedelta(seconds=1)),
            ):
                self.school.conn.execute(
                    "UPDATE live_session_items SET published_at = ? WHERE id = ?",
                    (stamp.isoformat(), int(item["id"])),
                )
            self.school.conn.commit()
        self.assertEqual(
            self.school.close_answers_if_timer_expired(self.session_id),
            [int(early["id"])],
        )
        self._answer(late, 0)
        self.assertEqual(self._status(late), "active")

    def test_lazy_close_publishes_close_news(self) -> None:
        """M3: a close triggered by a late answer reaches the news tape."""
        self._begin_and_join(2)
        item = self._publish()
        self._backdate(item)
        self._set_flag(True)
        self._timer(ago_sec=61)
        self.assertEqual(self._close_postcards(), [])
        with self.assertRaises(ValueError):
            self._answer(item, 1)
        self.assertEqual(len(self._close_postcards()), 1)

    def test_teacher_endpoint_reports_server_due_and_emits_once(self) -> None:
        """M2: the endpoint says when the server's 0:00 is; one close, one postcard."""
        client = self._staff_client()
        self._begin_and_join(1)
        item = self._publish()
        self._backdate(item)
        self._set_flag(True)
        self._timer(ago_sec=50)
        early = client.post(
            f"/api/live-sessions/{self.session_id}/timer-expired", json={}
        ).get_json()
        self.assertEqual(early["closed"], [])
        self.assertTrue(0 < early["due_in_ms"] <= 10_000, early["due_in_ms"])
        self._timer(ago_sec=61)
        done = client.post(
            f"/api/live-sessions/{self.session_id}/timer-expired", json={}
        ).get_json()
        self.assertEqual(done["closed"], [int(item["id"])])
        self.assertEqual(done["due_in_ms"], 0)
        self.assertEqual(len(self._close_postcards()), 1)
        self.school.game.pause_round_timer(self.class_id)
        paused = client.post(
            f"/api/live-sessions/{self.session_id}/timer-expired", json={}
        ).get_json()
        self.assertIsNone(paused["due_in_ms"])

    def test_student_poll_closes_without_teacher_tab(self) -> None:
        """M1: a student /state poll alone closes the card at the server's 0:00."""
        self.school.game.begin_game(self.class_id)
        item = self._publish()
        self._backdate(item)
        self._set_flag(True)
        self._timer(ago_sec=61)
        code = str(self.school.get_live_session(self.session_id)["session_code"])
        student = self.app.test_client()
        joined = student.post(
            "/auth/student-code", data={"code": code, "name": "Aspen"}
        )
        self.assertEqual(joined.status_code, 302, joined.get_data(as_text=True)[:300])
        student.post("/student/mood", data={"mood": "good"})
        student.post("/student/character", data={"character": "fox"})
        self.assertEqual(self._status(item), "active")
        polled = student.get("/api/student/state")
        self.assertEqual(polled.status_code, 200, polled.get_data(as_text=True)[:300])
        self.assertEqual(self._status(item), "closed")
        self.assertEqual(len(self._close_postcards()), 1)

    def test_staff_poll_closes_and_is_throttled(self) -> None:
        """M1: the staff /state poll closes too; checks run at most once a second."""
        client = self._staff_client()
        self._begin_and_join(1)
        item = self._publish()
        self._backdate(item)
        self._set_flag(True)
        self._timer(ago_sec=61)
        rv = client.get(f"/api/live-sessions/{self.session_id}/state?light=1")
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(self._status(item), "closed")
        second = self._publish("q-two")
        self._backdate(second)
        # Within the interval the poll hook skips; the answer gate still closes.
        self.assertEqual(self.school.close_answers_if_timer_due(self.session_id), [])
        self.assertEqual(self._status(second), "active")
        self.school._timer_close_checked.clear()
        self.assertEqual(
            self.school.close_answers_if_timer_due(self.session_id),
            [int(second["id"])],
        )


HARNESS = r"""
const vm = require("vm");
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
let calls = 0;
let refreshed = 0;
let fakeNow = 1000000;
const callTimes = [];
const ctx = {
  teacherState: { timer_closes_answers: input.flag },
  liveSessionId: 7,
  readLiveSessionId: () => 7,
  Date: { now: () => fakeNow },
  Number,
  Math,
  Array,
  api: async (url) => {
    calls += 1;
    callTimes.push(fakeNow - 1000000);
    if (input.fail) throw new Error("offline");
    const list = input.responses;
    return list[Math.min(calls - 1, list.length - 1)];
  },
  refreshLiveQuestionCards: async () => { refreshed += 1; },
  refreshLifecycleResults: async () => {},
  window: { setTimeout: (fn) => fn() },
};
vm.createContext(ctx);
vm.runInContext(input.src, ctx);
(async () => {
  // One clock tick a second for 40 s after the teacher's local 0:00.
  for (let i = 0; i < 40; i += 1) {
    await vm.runInContext("closeAnswersAtZero(1000)", ctx);
    fakeNow += 1000;
  }
  console.log(JSON.stringify({ calls, refreshed, callTimes }));
})();
"""


def _js_block(js: str) -> str:
    start = js.index("/** Deadline (epoch ms) already sent to ``timer-expired``. */")
    end = js.index("\n}\n", js.index("async function closeAnswersAtZero(")) + 3
    return js[start:end].replace("let ", "var ").replace("const TIMER", "var TIMER")


@unittest.skipUnless(shutil.which("node"), "node is required for the staff_ap.js harness")
class TimerCloseClientTests(unittest.TestCase):
    """The teacher tab calls timer-expired per deadline on the server's schedule."""

    def _run(self, flag: bool, responses: list[dict], fail: bool = False) -> dict:
        src = _js_block(STAFF_JS.read_text(encoding="utf-8"))
        done = subprocess.run(
            ["node", "-e", HARNESS],
            input=json.dumps(
                {"src": src, "flag": flag, "responses": responses, "fail": fail}
            ),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout.strip().splitlines()[-1])

    def test_off_never_calls(self) -> None:
        self.assertEqual(self._run(False, [{"closed": [1]}])["calls"], 0)

    def test_closes_once_then_stops(self) -> None:
        out = self._run(True, [{"closed": [1], "due_in_ms": 0}])
        self.assertEqual((out["calls"], out["refreshed"]), (1, 1))

    def test_nothing_left_on_server_stops_and_repaints(self) -> None:
        """Past the server's 0:00 with nothing open (a poll closed it): stop."""
        out = self._run(True, [{"closed": [], "due_in_ms": 0}])
        self.assertEqual((out["calls"], out["refreshed"]), (1, 1))

    def test_fast_teacher_clock_retries_on_server_schedule(self) -> None:
        """M1/M2: teacher clock 10 s fast. The retry waits for the server's 0:00."""
        out = self._run(
            True,
            [{"closed": [], "due_in_ms": 10_000}, {"closed": [5], "due_in_ms": 0}],
        )
        self.assertEqual(out["calls"], 2)
        self.assertEqual(out["callTimes"], [0, 11_000])
        self.assertEqual(out["refreshed"], 1)

    def test_errors_retry_with_a_cap(self) -> None:
        out = self._run(True, [{}], fail=True)
        self.assertEqual(out["calls"], 12)
        self.assertEqual(out["callTimes"][:3], [0, 2000, 4000])

    def test_unsent_draft_note(self) -> None:
        """L2: a closed card says the typed answer was not sent."""
        js = STUDENT_JS.read_text(encoding="utf-8")
        start = js.index("function unsentDraftNoteHtml(")
        end = js.index("\n}\n", start) + 3
        script = r"""
const vm = require("vm");
const src = require("fs").readFileSync(0, "utf8");
const ctx = {
  liveCardDrafts: new Map([["4:individual", { text: "x = <3>" }], ["5:individual", { text: "  " }]]),
  escapeText: (v) => String(v).replace(/</g, "&lt;").replace(/>/g, "&gt;"),
  String, Number,
};
vm.createContext(ctx);
vm.runInContext(src, ctx);
const out = [
  vm.runInContext('unsentDraftNoteHtml({id: 4, status: "closed"})', ctx),
  vm.runInContext('unsentDraftNoteHtml({id: 4, status: "active"})', ctx),
  vm.runInContext('unsentDraftNoteHtml({id: 4, status: "closed", my_response: {}})', ctx),
  vm.runInContext('unsentDraftNoteHtml({id: 5, status: "closed"})', ctx),
  vm.runInContext('unsentDraftNoteHtml({id: 6, status: "closed"})', ctx),
];
console.log(JSON.stringify(out));
"""
        done = subprocess.run(
            ["node", "-e", script],
            input=js[start:end],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertIn("Answers closed before yours was sent", out[0])
        self.assertIn("x = &lt;3&gt;", out[0])
        self.assertEqual(out[1:], ["", "", "", ""])
        self.assertIn("${unsentDraftNoteHtml(item)}", js)

    def test_switch_is_in_the_timer_panel(self) -> None:
        html = (STAFF_JS.parent.parent / "templates" / "staff" / "course.html").read_text(
            encoding="utf-8"
        )
        panel = html[html.index('id="session-timer"') :]
        panel = panel[: panel.index("</section>")]
        self.assertIn('id="live-timer-closes"', panel)
        self.assertIn("Close answers at 0:00", panel)


def load_tests(
    loader: unittest.TestLoader, _tests: Any, _pattern: Any
) -> unittest.TestSuite:
    """Run this file's tests only, not the inherited backend ones."""
    suite = unittest.TestSuite()
    names = [
        name
        for name, value in vars(TimerCloseTests).items()
        if name.startswith("test_") and callable(value)
    ]
    suite.addTests(TimerCloseTests(name) for name in sorted(names))
    suite.addTests(loader.loadTestsFromTestCase(TimerCloseClientTests))
    return suite


if __name__ == "__main__":
    unittest.main()

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


HARNESS = r"""
const vm = require("vm");
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
let calls = 0;
let refreshed = 0;
const ctx = {
  teacherState: { timer_closes_answers: input.flag },
  liveSessionId: 7,
  readLiveSessionId: () => 7,
  api: async (url) => { calls += 1; return { closed: input.closed }; },
  refreshLiveQuestionCards: async () => { refreshed += 1; },
  refreshLifecycleResults: async () => {},
  window: { setTimeout: (fn) => fn() },
};
vm.createContext(ctx);
vm.runInContext(input.src, ctx);
(async () => {
  for (let i = 0; i < 10; i += 1) await vm.runInContext("closeAnswersAtZero(1000)", ctx);
  console.log(JSON.stringify({ calls, refreshed }));
})();
"""


def _js_block(js: str) -> str:
    start = js.index("/** Deadline (epoch ms) already sent to ``timer-expired``. */")
    end = js.index("\n}\n", js.index("async function closeAnswersAtZero(")) + 3
    return js[start:end].replace("let ", "var ").replace("const TIMER", "var TIMER")


@unittest.skipUnless(shutil.which("node"), "node is required for the staff_ap.js harness")
class TimerCloseClientTests(unittest.TestCase):
    """The teacher tab calls timer-expired once per deadline, capped."""

    def _run(self, flag: bool, closed: list[int]) -> dict:
        src = _js_block(STAFF_JS.read_text(encoding="utf-8"))
        done = subprocess.run(
            ["node", "-e", HARNESS],
            input=json.dumps({"src": src, "flag": flag, "closed": closed}),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout.strip().splitlines()[-1])

    def test_off_never_calls(self) -> None:
        self.assertEqual(self._run(False, [1])["calls"], 0)

    def test_closes_once_then_stops(self) -> None:
        out = self._run(True, [1])
        self.assertEqual(out, {"calls": 1, "refreshed": 1})

    def test_nothing_to_close_is_capped(self) -> None:
        self.assertEqual(self._run(True, [])["calls"], 3)

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

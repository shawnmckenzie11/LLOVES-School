#!/usr/bin/env python3
"""MCK-108 follow-up: End then Start on a reused session id, and End's postcard.

After End, open student tabs hold the celebration's ``state_seq``. Their
poll guard and their LiveNewsWire drop any lower seq. The next Start can
reuse the wiped session id, so the new run must start above that seq.
End must also tell streaming tabs, or they wait for the 20 s safety poll.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from typing import Any

import test_live_shell as shell
from live_news_wire import (
    LiveNewsLog,
    NewsAudience,
    event_visible,
    news_db_path,
    reset_logs,
)

LMS_DIR = shell.LMS_DIR


def _run_guards(cases: list[dict[str, Any]]) -> list[dict[str, bool]]:
    """Run the student tab's two seq guards in node.

    Args:
        cases: ``{"body": ..., "last": seq}`` pairs. ``body`` is a /state
            body or a postcard.

    Returns:
        ``{"apply": shouldApplyLiveSnapshot, "stale": isStaleNews,
        "fetch": newsWantsLightFetch}`` per case.
    """
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("node is not installed")
    feel = (LMS_DIR / "static" / "live_poll_feel.js").resolve().as_uri()
    wire = (LMS_DIR / "static" / "live_news_wire.js").resolve().as_uri()
    script = f"""
import {{ shouldApplyLiveSnapshot }} from {json.dumps(feel)};
import {{ isStaleNews, newsWantsLightFetch }} from {json.dumps(wire)};
const cases = {json.dumps(cases)};
console.log(JSON.stringify(cases.map((c) => ({{
  apply: shouldApplyLiveSnapshot(c.body, c.last),
  stale: isStaleNews(c.body, c.last),
  fetch: newsWantsLightFetch(c.body),
}}))));
"""
    done = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if done.returncode != 0:
        raise AssertionError(done.stderr)
    return json.loads(done.stdout.strip().splitlines()[-1])


class EndStartSeqTests(shell.LiveShellTests):
    """Reuse the live-shell fixture. Only the tests below run here."""

    def tearDown(self) -> None:
        """Close the wire tape before the fixture drops the temp dir."""
        reset_logs()
        super().tearDown()

    def _tape(self, sid: int, after_id: int = 0) -> list[dict[str, Any]]:
        """Read this session's postcards newer than ``after_id``."""
        log = LiveNewsLog(news_db_path(self.tmp.name))
        try:
            return log.since(sid, after_id, limit=500)
        finally:
            log.close()

    def _end_with_old_tab(self) -> tuple[int, int, dict[str, Any]]:
        """Run a class, End it, and return the old tab's celebration body."""
        sid, student_id = self._open_live_with_aspen_answers()
        self._bind_aspen_live_session(sid, student_id)
        ended = self.client.post(
            f"/staff/class/{self.class_id}/end-live", follow_redirects=False
        )
        self.assertEqual(ended.status_code, 302)
        held = self.client.get("/api/student/state").get_json()
        self.assertTrue(held.get("celebrate"), held)
        return sid, student_id, held

    def test_start_after_end_reaches_the_old_tab(self) -> None:
        """The old tab holds End's seq. The next class must pass its guards."""
        sid, student_id, held = self._end_with_old_tab()
        old_seq = int(held["state_seq"])
        self.assertGreaterEqual(old_seq, 2)
        cursor = max([int(row["id"]) for row in self._tape(sid)] or [0])

        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        new_sid = int(live["id"])
        # SQLite gives the wiped row's id to the new run (the Ops repro).
        self.assertEqual(new_sid, sid)
        self.school.join_live_class_session(new_sid, student_id, codename="Aspen")
        self._bind_aspen_live_session(new_sid, student_id)

        body = self.client.get(
            "/api/student/state",
            query_string={"seq": old_seq, "stamp": held.get("stamp")},
        ).get_json()
        self.assertFalse(body.get("unchanged"), body)
        self.assertFalse(body.get("celebrate"), body)
        self.assertGreater(int(body["state_seq"]), old_seq)

        # A teacher write on the new run reaches a tab still streaming sid 1.
        moved = self.client.post(
            f"/api/live-sessions/{new_sid}/teacher-state",
            json={"advance": "next"},
        )
        self.assertEqual(moved.status_code, 200, moved.get_json())
        news = [row for row in self._tape(new_sid, cursor) if row["type"] == "state_seq"]
        self.assertTrue(news)

        got = _run_guards(
            [{"body": body, "last": old_seq}]
            + [{"body": row, "last": old_seq} for row in news]
        )
        self.assertTrue(got[0]["apply"], "poll tab drops the new class body")
        for row, verdict in zip(news, got[1:]):
            self.assertFalse(verdict["stale"], f"stream tab drops {row}")

    def test_new_run_seq_is_only_raised_after_an_earlier_run(self) -> None:
        """A class with no earlier run still starts at seq 0."""
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        state = self.school.live_session_teacher_state_payload(int(live["id"]))
        self.assertLessEqual(int(state.get("state_seq") or 0), 1)

    def test_end_route_sends_a_postcard_to_streaming_tabs(self) -> None:
        """End emits ``state_seq`` so a streaming tab fetches the celebration."""
        sid, student_id = self._open_live_with_aspen_answers()
        self._bind_aspen_live_session(sid, student_id)
        cursor = max([int(row["id"]) for row in self._tape(sid)] or [0])
        ended = self.client.post(
            f"/staff/class/{self.class_id}/end-live", follow_redirects=False
        )
        self.assertEqual(ended.status_code, 302)
        seq = int(self.school.live_session_teacher_state_payload(sid)["state_seq"])
        news = [row for row in self._tape(sid, cursor) if row["type"] == "state_seq"]
        self.assertTrue(news, "End sent no postcard")
        self.assertEqual(int(news[-1]["state_seq"]), seq)
        student = NewsAudience("student", student_id=student_id)
        self.assertTrue(event_visible(news[-1], student))
        stream = self.client.get(
            f"/api/live/session/{sid}/events",
            headers={"Last-Event-ID": str(cursor)},
        )
        self.assertEqual(stream.status_code, 200)
        self.assertIn("event: state_seq", stream.get_data(as_text=True))
        # The tab had applied the pre-End seq; End's postcard must fetch.
        got = _run_guards([{"body": news[-1], "last": seq - 1}])
        self.assertFalse(got[0]["stale"])
        self.assertTrue(got[0]["fetch"])


def load_tests(
    loader: unittest.TestLoader, _tests: Any, _pattern: Any
) -> unittest.TestSuite:
    """Run only this file's tests, not the inherited live-shell ones."""
    names = [
        name
        for name, value in vars(EndStartSeqTests).items()
        if name.startswith("test_") and callable(value)
    ]
    return unittest.TestSuite(EndStartSeqTests(name) for name in sorted(names))


if __name__ == "__main__":
    unittest.main()

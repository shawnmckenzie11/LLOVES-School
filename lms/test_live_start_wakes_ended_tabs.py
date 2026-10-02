#!/usr/bin/env python3
"""MCK-107 part 2: a new run wakes tabs still on End's celebration.

Old tabs keep their LiveNewsWire stream to the ended session id and poll at
the slow ended pace. Start must send them a postcard so they fetch the new
class at once instead of on the next 24-36 s ended poll.
"""

from __future__ import annotations

import unittest
from typing import Any

import test_live_end_start_seq as seqtests


class StartWakesEndedTabsTests(seqtests.EndStartSeqTests):
    """Reuse the End/Start fixture. Only the tests below run here."""

    def _cursor(self, sid: int) -> int:
        """Last postcard id on this session's tape."""
        return max([int(row["id"]) for row in self._tape(sid)] or [0])

    def _assert_wakes(self, sid: int, cursor: int, old_seq: int, new_sid: int) -> None:
        """A tab on ``sid`` past ``cursor`` gets a fresh, fetching postcard."""
        news = [
            row for row in self._tape(sid, cursor) if row["type"] == "state_seq"
        ]
        self.assertTrue(news, "Start sent no postcard to the old stream")
        seq = int(self.school.live_session_teacher_state_payload(new_sid)["state_seq"])
        self.assertEqual(int(news[-1]["state_seq"]), seq)
        got = seqtests._run_guards([{"body": news[-1], "last": old_seq}])
        self.assertFalse(got[0]["stale"], "old tab drops the wake postcard")
        self.assertTrue(got[0]["fetch"], "wake postcard does not fetch")
        stream = self.client.get(
            f"/api/live/session/{sid}/events",
            headers={"Last-Event-ID": str(cursor)},
        )
        self.assertEqual(stream.status_code, 200)
        self.assertIn("event: state_seq", stream.get_data(as_text=True))

    def test_api_start_wakes_the_old_celebration_stream(self) -> None:
        """``live-session/start`` after End posts to the reused session id."""
        sid, _student_id, held = self._end_with_old_tab()
        cursor = self._cursor(sid)
        started = self.client.post(
            f"/api/classes/{self.class_id}/live-session/start", json={}
        )
        self.assertEqual(started.status_code, 200, started.get_json())
        new_sid = int(started.get_json()["live_session_id"])
        self.assertEqual(new_sid, sid)
        self._assert_wakes(sid, cursor, int(held["state_seq"]), new_sid)

    def test_run_live_wakes_the_old_celebration_stream(self) -> None:
        """The Run Live Class button does the same."""
        sid, _student_id, held = self._end_with_old_tab()
        cursor = self._cursor(sid)
        ran = self.client.post(
            f"/staff/class/{self.class_id}/run-live", follow_redirects=False
        )
        self.assertEqual(ran.status_code, 302)
        live = self.school.get_active_live_session_for_class(self.class_id)
        assert live is not None
        self._assert_wakes(sid, cursor, int(held["state_seq"]), int(live["id"]))

    def test_resuming_the_open_session_sends_nothing(self) -> None:
        """Start on an already-open run is a resume, not news."""
        started = self.client.post(
            f"/api/classes/{self.class_id}/live-session/start", json={}
        )
        sid = int(started.get_json()["live_session_id"])
        cursor = self._cursor(sid)
        again = self.client.post(
            f"/api/classes/{self.class_id}/live-session/start", json={}
        )
        self.assertEqual(int(again.get_json()["live_session_id"]), sid)
        self.assertEqual(self._tape(sid, cursor), [])


def load_tests(
    loader: unittest.TestLoader, _tests: Any, _pattern: Any
) -> unittest.TestSuite:
    """Run only this file's tests, not the inherited ones."""
    names = [
        name
        for name, value in vars(StartWakesEndedTabsTests).items()
        if name.startswith("test_") and callable(value)
    ]
    return unittest.TestSuite(
        StartWakesEndedTabsTests(name) for name in sorted(names)
    )


if __name__ == "__main__":
    unittest.main()

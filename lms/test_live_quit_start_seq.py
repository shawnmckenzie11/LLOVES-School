#!/usr/bin/env python3
"""MCK-108 follow-up #2: End, Quit, then Start, and the reused id's tape.

Quit deletes every ``live_class_sessions`` row for the class. The Start
floor from follow-up #1 read those rows, so after Quit it fell to 0 and
the new run began below the seq old tabs still held. Those tabs stayed on
the celebration. Quit also sent no postcard, and Start left the old run's
postcards on the reused id's tape, where a first connect replayed them.
"""

from __future__ import annotations

import unittest
from typing import Any

from test_live_end_start_seq import EndStartSeqTests, _run_guards


class QuitStartSeqTests(EndStartSeqTests):
    """Reuse the End/Start fixture. Only the tests below run here."""

    def _quit(self) -> None:
        """Press Quit on the class page."""
        quit_resp = self.client.post(
            f"/staff/class/{self.class_id}/quit-live", follow_redirects=False
        )
        self.assertEqual(quit_resp.status_code, 302)

    def test_end_quit_start_reaches_the_old_tab(self) -> None:
        """Quit between End and Start must not reset the seq floor."""
        sid, student_id, held = self._end_with_old_tab()
        old_seq = int(held["state_seq"])
        self.assertGreaterEqual(old_seq, 2)
        self._quit()
        self.assertEqual(self.school.list_live_sessions_for_class(self.class_id), [])

        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        new_sid = int(live["id"])
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
        got = _run_guards([{"body": body, "last": old_seq}])
        self.assertTrue(got[0]["apply"], "poll tab drops the new class body")

    def test_quit_sends_a_postcard_above_the_held_seq(self) -> None:
        """A streaming tab on the celebration fetches as soon as Quit lands."""
        sid, _student_id, held = self._end_with_old_tab()
        old_seq = int(held["state_seq"])
        cursor = max([int(row["id"]) for row in self._tape(sid)] or [0])
        self._quit()
        news = [row for row in self._tape(sid, cursor) if row["type"] == "state_seq"]
        self.assertTrue(news, "Quit sent no postcard")
        self.assertGreater(int(news[-1]["state_seq"]), old_seq)
        got = _run_guards([{"body": news[-1], "last": old_seq}])
        self.assertFalse(got[0]["stale"])
        self.assertTrue(got[0]["fetch"])

    def test_quit_on_an_active_run_also_sends_a_postcard(self) -> None:
        """Quit without End: tabs in the running class leave at once too."""
        sid, student_id = self._open_live_with_aspen_answers()
        self._bind_aspen_live_session(sid, student_id)
        seq = int(self.school.live_session_teacher_state_payload(sid)["state_seq"])
        cursor = max([int(row["id"]) for row in self._tape(sid)] or [0])
        self._quit()
        news = [row for row in self._tape(sid, cursor) if row["type"] == "state_seq"]
        self.assertTrue(news, "Quit sent no postcard")
        self.assertGreater(int(news[-1]["state_seq"]), seq)

    def test_start_clears_the_reused_ids_tape(self) -> None:
        """A first connect on the new run must not replay the old run."""
        sid, _student_id, _held = self._end_with_old_tab()
        self._quit()
        old_ids = [int(row["id"]) for row in self._tape(sid)]
        self.assertTrue(old_ids, "fixture left no postcards to replay")
        live = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        new_sid = int(live["id"])
        self.assertEqual(new_sid, sid)
        self.assertEqual(self._tape(new_sid), [], "old postcards replay")
        stream = self.client.get(f"/api/live/session/{new_sid}/events")
        self.assertEqual(stream.status_code, 200)
        text = stream.get_data(as_text=True)
        for old_id in old_ids:
            self.assertNotIn(f"id: {old_id}\n", text)
        # A tab still holding the old Last-Event-ID gets new postcards.
        moved = self.client.post(
            f"/api/live-sessions/{new_sid}/teacher-state",
            json={"advance": "next"},
        )
        self.assertEqual(moved.status_code, 200, moved.get_json())
        fresh = self._tape(new_sid, max(old_ids))
        self.assertTrue(fresh, "new postcards hidden behind the old cursor")

    def test_restart_without_end_keeps_its_tape(self) -> None:
        """Start on an already-active run reuses it and keeps its postcards."""
        sid, _student_id = self._open_live_with_aspen_answers()
        before = [int(row["id"]) for row in self._tape(sid)]
        again = self.school.start_live_class_session(
            self.class_id, int(self.teacher["id"])
        )
        self.assertEqual(int(again["id"]), sid)
        self.assertEqual([int(row["id"]) for row in self._tape(sid)], before)

    def test_wipe_saves_class_and_session_floors(self) -> None:
        """The floor survives the wipe for both the class and the id."""
        sid, _student_id, held = self._end_with_old_tab()
        seq = int(held["state_seq"])
        self.school.wipe_live_sessions_for_class(self.class_id)
        self.assertEqual(self.school.stored_seq_floor(class_id=self.class_id), seq)
        self.assertEqual(self.school.stored_seq_floor(session_id=sid), seq)
        self.assertEqual(self.school.stored_seq_floor(class_id=999999), 0)
        self.assertEqual(self.school.stored_seq_floor(), 0)


def load_tests(
    loader: unittest.TestLoader, _tests: Any, _pattern: Any
) -> unittest.TestSuite:
    """Run only this file's tests, not the inherited ones."""
    names = [
        name
        for name, value in vars(QuitStartSeqTests).items()
        if name.startswith("test_") and callable(value)
    ]
    return unittest.TestSuite(QuitStartSeqTests(name) for name in sorted(names))


if __name__ == "__main__":
    unittest.main()

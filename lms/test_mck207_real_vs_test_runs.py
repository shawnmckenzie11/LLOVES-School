#!/usr/bin/env python3
"""MCK-207: real vs test live runs, shared catch-up link, teacher toggle."""

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from live_class_run import (  # noqa: E402
    RealClassWindow,
    LiveClassSchedule,
    classify_run_end,
    load_real_class_window,
)
from test_participation_mck198 import Base  # noqa: E402

TORONTO = ZoneInfo("America/Toronto")


class ClassifyWindowTests(unittest.TestCase):
    """Window edges and weekday rules use course schedule data."""

    def setUp(self) -> None:
        self.sched = LiveClassSchedule(days_key="M/W/F", end_time_label="10:30am")
        self.window = RealClassWindow(before_min=20, after_min=45)

    def _at(self, text: str) -> datetime:
        return datetime.fromisoformat(text).replace(tzinfo=TORONTO)

    def test_wednesday_inside_window(self) -> None:
        self.assertTrue(
            classify_run_end(self._at("2026-10-14T10:25:00"), self.sched, window=self.window)
        )

    def test_window_start_edge(self) -> None:
        self.assertTrue(
            classify_run_end(self._at("2026-10-14T10:10:00"), self.sched, window=self.window)
        )

    def test_window_end_edge(self) -> None:
        self.assertTrue(
            classify_run_end(self._at("2026-10-14T11:15:00"), self.sched, window=self.window)
        )

    def test_before_window_is_test(self) -> None:
        self.assertFalse(
            classify_run_end(self._at("2026-10-14T10:09:59"), self.sched, window=self.window)
        )

    def test_after_window_is_test(self) -> None:
        self.assertFalse(
            classify_run_end(self._at("2026-10-14T11:15:01"), self.sched, window=self.window)
        )

    def test_tuesday_is_test(self) -> None:
        self.assertFalse(
            classify_run_end(self._at("2026-10-20T10:30:00"), self.sched, window=self.window)
        )

    def test_slack_defaults_from_config(self) -> None:
        loaded = load_real_class_window()
        self.assertEqual((loaded.before_min, loaded.after_min), (20, 45))


class RealVsTestFlowTests(Base):
    """End-of-run classification, toggle, and shared catch-up resolution."""

    def setUp(self) -> None:
        super().setUp()
        offering_id = int(
            self.school.game.conn.execute(
                "SELECT offering_id FROM classes WHERE id = ?", (self.class_id,)
            ).fetchone()[0]
        )
        self.school.set_offering_schedule(
            offering_id,
            live_days="M/W/F",
            live_time="9:15am",
            live_end_time="10:30am",
        )

    def _end_run(
        self,
        module: str,
        slot: str,
        *,
        ended_iso: str,
        individual: int = 2,
        live_run: bool = True,
    ) -> str:
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        sid = int(live["id"])
        c = self.school.conn
        c.execute(
            "UPDATE live_class_sessions SET teacher_state_json = ? WHERE id = ?",
            (json.dumps({"live_module": module, "live_slot": slot}), sid),
        )
        now = ended_iso
        pid = c.execute(
            "INSERT INTO live_session_prompts (live_session_id, slide_index, kind, payload, "
            "created_at, updated_at) VALUES (?, 0, 'poll', '{}', ?, ?)",
            (sid, now, now),
        ).lastrowid
        c.execute(
            "INSERT INTO live_session_items (live_session_id, placement_key, item_id, stage, kind, "
            "item_json, prompt_id, status, publish_mode, response_mode, published_at, created_at, updated_at) "
            "VALUES (?, 'q0', 'q0', 'round', 'poll', ?, ?, 'closed', 'individual', 'individual', ?, ?, ?)",
            (
                sid,
                '{"text": "Q", "type": "mc", "options": [{"text": "A"}], "correct_answer": "A", "key": "A"}',
                pid,
                now,
                now,
                now,
            ),
        )
        c.execute(
            "INSERT INTO live_session_responses (prompt_id, student_id, response_json, created_at, updated_at) "
            "VALUES (?, ?, '{}', ?, ?)",
            (int(pid), int(self.students[0]), now, now),
        )
        c.commit()
        c.execute(
            "UPDATE live_class_sessions SET ended_at = ?, status = 'ended' WHERE id = ?",
            (ended_iso, sid),
        )
        c.commit()
        run_key = str(
            c.execute("SELECT run_key FROM live_class_sessions WHERE id = ?", (sid,)).fetchone()[0]
            or ""
        )
        self.school.cleanup_live_session_response_data(sid, live_run=live_run)
        self.school.wipe_live_sessions_for_class(self.class_id)
        return run_key

    def test_real_run_creates_catchup_test_run_does_not(self) -> None:
        real_key = self._end_run("M1", "C1", ended_iso="2026-10-14T14:30:00", live_run=True)
        test_key = self._end_run("M1", "C1", ended_iso="2026-10-20T15:00:00", live_run=False)
        links = self.school.list_catchup_links(self.class_id)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["run_key"], real_key)
        self.assertTrue(self.school.participation_run_kind(self.class_id, real_key)["is_real"])
        self.assertFalse(self.school.participation_run_kind(self.class_id, test_key)["is_real"])
        self.assertTrue(self.school.participation_run_discarded(self.class_id, test_key))

    def test_shared_link_points_at_latest_real_run(self) -> None:
        self._end_run("M2", "C1", ended_iso="2026-10-12T14:30:00")
        token = str(self.school.list_catchup_links(self.class_id)[0]["token"])
        second = self._end_run("M2", "C1", ended_iso="2026-10-14T14:30:00")
        links = self.school.list_catchup_links(self.class_id)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["run_key"], second)
        self.assertEqual(links[0]["token"], token)
        resolved = self.school.resolve_catchup_link(token)
        assert resolved is not None
        self.assertEqual(resolved["run_key"], second)
        client = self.app.test_client()
        resp = client.get(f"/catchup/{token}")
        self.assertEqual(resp.status_code, 302)
        self.assertIn(f"/catchup/MCR3U/m2-c1/{token}", resp.headers.get("Location") or "")

    def test_teacher_toggle_both_ways(self) -> None:
        run_key = self._end_run("M3", "C1", ended_iso="2026-10-20T12:00:00", live_run=False)
        self.assertFalse(self.school.list_catchup_links(self.class_id))
        self.school.set_participation_run_real(self.class_id, run_key, True, user_id=1)
        self.assertEqual(len(self.school.list_catchup_links(self.class_id)), 1)
        self.school.set_participation_run_real(self.class_id, run_key, False, user_id=1)
        self.assertFalse(self.school.list_catchup_links(self.class_id))
        staff = self.app.test_client()
        staff.get("/auth/google?portal=staff")
        staff.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        code = self.school.get_user_by_email("teacher@gmail.com")["verification_code"]
        staff.post("/verify-email", data={"code": code})
        ok = staff.post(
            f"/api/classes/{self.class_id}/participation/runs/{run_key}/class-kind",
            json={"real": True},
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(len(self.school.list_catchup_links(self.class_id)), 1)
        again = staff.post(
            f"/api/classes/{self.class_id}/participation/runs/{run_key}/class-kind",
            json={"real": False},
        )
        self.assertEqual(again.status_code, 200)
        self.assertFalse(self.school.list_catchup_links(self.class_id))


if __name__ == "__main__":
    unittest.main()

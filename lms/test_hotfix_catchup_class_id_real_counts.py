#!/usr/bin/env python3
"""Hotfix: catch-up link class_id on list rows + real-run participation denominators."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from test_participation_mck198 import Base  # noqa: E402


class CatchupClassIdTests(Base):
    """``list_catchup_links`` must carry class_id for canonical URL building."""

    def setUp(self) -> None:
        super().setUp()
        self.run_class("M1", "C1", 2, 0, {self.students[0]: 1})

    def test_list_catchup_links_includes_class_id(self) -> None:
        links = self.school.list_catchup_links(self.class_id)
        self.assertEqual(len(links), 1)
        self.assertEqual(int(links[0]["class_id"]), self.class_id)

    def test_open_catchup_portal_url_without_class_id_on_row(self) -> None:
        row = dict(self.school.list_catchup_links(self.class_id)[0])
        row.pop("class_id", None)
        path = self.school.catchup_canonical_path_for_link(
            {**row, "class_id": row.get("class_id") or self.class_id}
        )
        self.assertIn("/catchup/MCR3U/m1-c1/", path)
        url = self.school.open_catchup_portal_url_for_class(self.class_id)
        self.assertIn("/catchup/MCR3U/m1-c1/", url or "")


class RealRunParticipationDenominatorTests(Base):
    """Test-run items must not inflate module or slot denominators."""

    def test_m3_c1_counts_only_real_run_not_test_rehearsal(self) -> None:
        real_key = self.run_class("M3", "C1", 2, 0, {})
        test_key = self.run_class(
            "M3", "C2", 2, 0, {}, ended_iso="2026-10-20T15:00:00"
        )
        self.school.conn.execute(
            """
            UPDATE participation_runs
            SET discarded = 0, is_real = 0
            WHERE class_id = ? AND run_key = ?
            """,
            (self.class_id, test_key),
        )
        self.school.conn.commit()
        data = self.school.class_participation(self.class_id)
        m3 = next(m for m in data["modules"] if m["module"] == "M3")
        self.assertEqual(m3["offered"], 2)
        slot_off, _, _ = self.school._student_slot_participation_counts(
            self.class_id, "M3", self.students[0]
        )
        self.assertEqual(slot_off.get("C1"), 2)
        self.assertNotIn("C2", slot_off)
        self.assertTrue(self.school.participation_run_kind(self.class_id, real_key)["is_real"])


class StudentStateCatchupPinTests(Base):
    """Catch-up PIN prompt must not 500 when portal URL building fails."""

    def setUp(self) -> None:
        super().setUp()
        self.run_class("M1", "C1", 1, 0, {})
        self.student_id = self.students[0]
        self.school.set_student_pin(
            self.class_id, self.student_id, "4321", pin_set_by="default"
        )
        self.client = self.app.test_client()
        offering_id = self.school.conn.execute(
            "SELECT offering_id FROM classes WHERE id = ?", (self.class_id,)
        ).fetchone()[0]
        live = self.school.start_live_class_session(self.class_id, int(self.teacher["id"]))
        sid = int(live["id"])
        self.school.join_live_class_session(sid, self.student_id, codename="Aspen")
        with self.client.session_transaction() as sess:
            sess["student_offering_id"] = offering_id
            sess["student_class_id"] = self.class_id
            sess["student_id"] = self.student_id
            sess["student_live_session_id"] = sid
            sess["student_participant_uuid"] = "test-uuid"

    def test_student_state_survives_catchup_url_failure(self) -> None:
        stamp_payload = {
            "active_questions": [],
            "teacher_state": {"state_seq": 1},
            "status": "waiting",
        }

        def _payload(*_args: object, **_kwargs: object) -> dict:
            return dict(stamp_payload)

        with patch.object(
            self.school, "student_live_poll_unchanged", return_value=None
        ), patch.object(
            self.school, "live_student_poll_stamp", return_value="s1"
        ), patch.object(
            self.school, "assemble_student_live_payload", side_effect=_payload
        ), patch.object(
            self.school,
            "catchup_pin_prompt_for_student",
            return_value={"show": True, "forced": False},
        ), patch.object(
            self.school,
            "open_catchup_portal_url_for_class",
            side_effect=KeyError("class_id"),
        ):
            resp = self.client.get("/api/student/state")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json() or {}
        prompt = body.get("catchup_pin_prompt") or {}
        self.assertTrue(prompt.get("show"))
        self.assertIsNone(prompt.get("catchup_url"))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Join-code lookup and landing list refuse non-real catch-up runs (prod v250)."""

from __future__ import annotations

import html
import os
import sys
import unittest
import uuid
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from catchup_landing import COPY_OTHER_HEADER, COPY_WRONG_CODE, list_open_catchup_rows  # noqa: E402
from catchup_portal_code import mint_portal_join_code  # noqa: E402
from test_mck207_real_vs_test_runs import RealVsTestFlowTests  # noqa: E402


class JoinCodeRealRunGateTests(RealVsTestFlowTests):
    """Portal join codes and Other open classes honor real-run policy."""

    def _insert_catchup_row(
        self,
        *,
        module: str,
        slot: str,
        run_key: str,
        portal_code: str,
        is_open: int = 1,
    ) -> str:
        """Insert a catch-up link row (prod-shaped orphan or test run)."""
        token = uuid.uuid4().hex
        now = "2026-10-14T12:00:00"
        self.school.conn.execute(
            """
            INSERT INTO catchup_links (
                class_id, token, run_key, live_module, live_slot, is_open,
                portal_join_code, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(self.class_id),
                token,
                run_key,
                module,
                slot,
                int(is_open),
                portal_code,
                now,
                now,
            ),
        )
        self.school.conn.commit()
        return token

    def test_real_portal_code_resolves(self) -> None:
        real_key = self._end_run("M1", "C1", ended_iso="2026-10-14T14:30:00", live_run=True)
        links = self.school.list_catchup_links(self.class_id)
        self.assertEqual(len(links), 1)
        code = str(links[0].get("portal_join_code") or "").strip()
        self.assertTrue(code)
        resolved = self.school.resolve_catchup_link_by_portal_code(code)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["run_key"], real_key)

    def test_test_run_portal_code_refused(self) -> None:
        test_key = self._end_run("M2", "C1", ended_iso="2026-10-20T15:00:00", live_run=False)
        code = mint_portal_join_code("MCR3U", "M2", "C1")
        self._insert_catchup_row(
            module="M2",
            slot="C1",
            run_key=test_key,
            portal_code=code,
        )
        self.assertIsNone(self.school.resolve_catchup_link_by_portal_code(code))
        client = self.app.test_client()
        rv = client.post(
            "/auth/student-code",
            data={"code": code, "name": self.first},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        self.assertIn(COPY_WRONG_CODE, html.unescape(rv.get_data(as_text=True)))

    def test_missing_participation_run_refused(self) -> None:
        code = mint_portal_join_code("MCR3U", "M3", "C2")
        ghost_key = uuid.uuid4().hex
        self._insert_catchup_row(
            module="M3",
            slot="C2",
            run_key=ghost_key,
            portal_code=code,
        )
        self.assertIsNone(self.school.resolve_catchup_link_by_portal_code(code))

    def test_other_open_classes_hides_test_runs(self) -> None:
        real_key = self._end_run("M4", "C1", ended_iso="2026-10-12T14:30:00", live_run=True)
        real_links = self.school.list_catchup_links(self.class_id)
        real_token = str(real_links[0]["token"])
        test_key = self._end_run("M5", "C1", ended_iso="2026-10-13T15:00:00", live_run=False)
        test_code = mint_portal_join_code("MCR3U", "M5", "C1")
        test_token = self._insert_catchup_row(
            module="M5",
            slot="C1",
            run_key=test_key,
            portal_code=test_code,
        )
        listed = self.school.list_catchup_links(self.class_id)
        tokens = {str(row["token"]) for row in listed}
        self.assertIn(real_token, tokens)
        self.assertNotIn(test_token, tokens)
        self.assertEqual(listed[0]["run_key"], real_key)

        rows, total = list_open_catchup_rows(
            self.school,
            self.class_id,
            self.students[0],
            exclude_token=real_token,
        )
        other_tokens = {str(row["token"]) for row in rows}
        self.assertNotIn(test_token, other_tokens)
        self.assertEqual(total, 0)


if __name__ == "__main__":
    unittest.main()

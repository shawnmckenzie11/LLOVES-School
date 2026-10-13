#!/usr/bin/env python3
"""MCK-206 security: catch-up PIN rate limits, portal session, and CLI guard."""

from __future__ import annotations

import os
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from participation import (  # noqa: E402
    CATCHUP_PIN_IP_LIMIT,
    PIN_MAX_ATTEMPTS,
    PIN_WINDOW_S,
    catchup_ip_limit_key,
    catchup_link_ip_limit_key,
    hash_pin,
)
from test_participation_mck198 import Base  # noqa: E402


class CatchupPinLimitTests(Base):
    def test_reserve_catchup_pin_attempts_is_atomic(self) -> None:
        ip_key = catchup_ip_limit_key("ip:203.0.113.99")
        limits = {ip_key: CATCHUP_PIN_IP_LIMIT}
        for _ in range(CATCHUP_PIN_IP_LIMIT - 1):
            ids = self.school.reserve_catchup_pin_attempts(
                [ip_key], limits, window_s=PIN_WINDOW_S
            )
            self.assertIsNotNone(ids)

        blocked: list[bool] = []

        def worker() -> None:
            ids = self.school.reserve_catchup_pin_attempts(
                [ip_key], limits, window_s=PIN_WINDOW_S
            )
            blocked.append(ids is None)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertIn(True, blocked)
        self.assertIn(False, blocked)

    def test_portal_session_requires_exp(self) -> None:
        session = {
            "catchup:1": {
                "sid": self.students[0],
                "cid": self.class_id,
                "pin": self.school.catchup_pin_stamp(self.class_id, self.students[0]),
                "exp": 1.0,
            }
        }
        self.assertFalse(
            self.school.catchup_portal_session_valid(
                session, self.class_id, self.students[0]
            )
        )

    def test_set_student_pin_rejects_same_pin(self) -> None:
        self.school.set_student_pin(self.class_id, self.students[0], "4321", pin_set_by="student")
        with self.assertRaises(ValueError):
            self.school.set_student_pin(self.class_id, self.students[0], "4321", pin_set_by="student")

    def test_teacher_unlock_clears_soft_cap(self) -> None:
        sid = self.students[0]
        for _ in range(16):
            self.school.record_catchup_pin_failure(self.class_id, sid)
        self.assertIsNotNone(self.school.catchup_pin_student_gate(self.class_id, sid))
        self.school.teacher_unlock_catchup_pin(self.class_id, sid)
        self.assertIsNone(self.school.catchup_pin_student_gate(self.class_id, sid))


class CliMainGuardTests(unittest.TestCase):
    def test_main_allows_only_backfill_on_fly(self) -> None:
        from cli import main

        with mock.patch.dict(os.environ, {"FLY_APP_NAME": "lloves-lms"}, clear=False):
            with mock.patch.object(sys, "argv", ["cli", "routes"]):
                with self.assertRaises(SystemExit) as ctx:
                    main()
                self.assertIn("backfill-catchup", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

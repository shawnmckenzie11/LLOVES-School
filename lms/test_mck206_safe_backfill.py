#!/usr/bin/env python3
"""MCK-206: safe prod backfill CLI, discard run, and CLI guards."""

from __future__ import annotations

import io
import logging
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from cli import register_cli  # noqa: E402
from test_participation_mck198 import Base  # noqa: E402


class CliBackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        register_cli(self.app)
        self.runner = self.app.test_cli_runner()

    def test_pins_without_apply_errors(self) -> None:
        result = self.runner.invoke(args=["backfill-catchup", "--pins"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--pins requires --apply", result.output)

    def test_prod_apply_requires_override_flag(self) -> None:
        with mock.patch.dict(os.environ, {"FLASK_ENV": "production"}, clear=False):
            result = self.runner.invoke(args=["backfill-catchup", "--apply"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--i-know-this-is-prod", result.output)

    def test_prod_override_requires_filters(self) -> None:
        with mock.patch.dict(os.environ, {"FLASK_ENV": "production"}, clear=False):
            result = self.runner.invoke(
                args=["backfill-catchup", "--apply", "--i-know-this-is-prod", "--course", "MCF3M"]
            )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--module", result.output)


class DiscardRunTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.run_class("M1", "C1", individual=3, group=0, answers={self.students[0]: 2})
        self.link = self.school.list_catchup_links(self.class_id)[0]
        self.run_key = str(self.link["run_key"])

    def test_discard_run_excludes_participation_counts(self) -> None:
        before = self.school.class_participation(self.class_id)
        offered_before = before["modules"][0]["offered"]
        self.assertGreater(offered_before, 0)
        self.school.set_participation_run_discarded(self.class_id, self.run_key, True, user_id=1)
        after = self.school.class_participation(self.class_id)
        self.assertEqual(after["modules"], [])
        self.assertTrue(self.school.participation_run_discarded(self.class_id, self.run_key))

    def test_teacher_discard_api(self) -> None:
        staff = self.app.test_client()
        staff.get("/auth/google?portal=staff")
        staff.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        code = self.school.get_user_by_email("teacher@gmail.com")["verification_code"]
        staff.post("/verify-email", data={"code": code})
        denied = staff.post(
            f"/api/classes/{self.class_id}/participation/runs/{self.run_key}/discard",
            json={},
        )
        self.assertEqual(denied.status_code, 400)
        ok = staff.post(
            f"/api/classes/{self.class_id}/participation/runs/{self.run_key}/discard",
            json={"confirm": True},
        )
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(self.school.participation_run_discarded(self.class_id, self.run_key))


if __name__ == "__main__":
    logging.disable(logging.NOTSET)
    unittest.main()

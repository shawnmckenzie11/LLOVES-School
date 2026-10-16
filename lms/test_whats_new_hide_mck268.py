#!/usr/bin/env python3
"""MCK-268: hide deploy-only What's New rows without touching production.

``python -m cli whats-new`` lists ``whats_new_releases``. ``--apply --id``
hides those ids. A second apply is a no-op. Dry-run writes nothing.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

import whats_new_store as store  # noqa: E402
from app import create_app  # noqa: E402

FIX = "In Run Live Class, lifecycle is not defined."


def _deploy_fix() -> dict:
    """One generated row like a post-Oct-4 deploy wrote it."""
    sha = "ffff001" + "a" * 33
    return {
        "id": sha[:7],
        "sha": sha,
        "deployed_at": "2026-10-06T12:00:00-04:00",
        "day": "2026-10-06",
        "source": "deploy",
        "items": [{"text": FIX, "audience": "Teacher", "refs": "#300"}],
    }


def _write(path: Path, releases: list[dict]) -> Path:
    path.write_text(json.dumps({"schema": 2, "releases": releases}), encoding="utf-8")
    return path


class HideStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = store.sqlite3.connect(":memory:", isolation_level=None)
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_hide_is_idempotent_and_survives_a_deploy_resync(self) -> None:
        entry = _deploy_fix()
        path = _write(Path(self.tmp.name) / "a.json", [entry])
        store.sync_from_file(self.conn, path)
        listed = store.list_stored_releases(self.conn)
        self.assertEqual(listed[0]["id"], entry["id"])
        self.assertFalse(listed[0]["hidden"])
        self.assertEqual(listed[0]["items"], [FIX])

        first = store.hide_stored_releases(self.conn, [entry["id"], entry["id"]])
        self.assertEqual(first, {"hidden": [entry["id"]], "already": [], "missing": []})
        self.assertEqual(store.teacher_payload(store.stored_releases(self.conn))["releases"], [])

        second = store.hide_stored_releases(self.conn, [entry["id"]])
        self.assertEqual(second["hidden"], [])
        self.assertEqual(second["already"], [entry["id"]])

        store.sync_from_file(self.conn, path)
        again = store.list_stored_releases(self.conn)
        self.assertTrue(again[0]["hidden"])
        self.assertNotIn("lifecycle", json.dumps(store.teacher_payload(store.stored_releases(self.conn))))

        missing = store.hide_stored_releases(self.conn, ["no-such-id", ""])
        self.assertEqual(missing["missing"], ["no-such-id"])
        self.assertEqual(missing["hidden"], [])


class HideCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "s.db", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.runner = self.app.test_cli_runner()
        self.entry = _deploy_fix()
        self.school.sync_whats_new(_write(root / "extra.json", [self.entry]))

    def tearDown(self) -> None:
        self.school.close()
        self.tmp.cleanup()

    def _texts(self) -> str:
        """Teacher payload JSON, which must not include hidden fix notes."""
        payload = self.school.whats_new_teacher_payload()
        return json.dumps(payload)

    def test_dry_run_lists_rows_and_writes_nothing(self) -> None:
        before = self._texts()
        result = self.runner.invoke(args=["whats-new"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("DRY RUN", result.output)
        self.assertIn(f"id={self.entry['id']}", result.output)
        self.assertIn(FIX, result.output)
        self.assertIn("hidden=no", result.output)
        self.assertEqual(self._texts(), before)

        preview = self.runner.invoke(args=["whats-new", "--id", self.entry["id"]])
        self.assertEqual(preview.exit_code, 0, preview.output)
        self.assertIn(f"would hide: {self.entry['id']}", preview.output)
        self.assertIn("no rows written", preview.output)
        self.assertIn("lifecycle", self._texts())
        committed = self.runner.invoke(args=["whats-new", "--id", "65d69db"])
        self.assertEqual(committed.exit_code, 0, committed.output)
        self.assertIn("source=committed", committed.output)
        self.assertIn("would hide: 65d69db", committed.output)

    def test_apply_hides_and_a_second_apply_is_clean(self) -> None:
        rid = self.entry["id"]
        applied = self.runner.invoke(args=["whats-new", "--apply", "--id", rid])
        self.assertEqual(applied.exit_code, 0, applied.output)
        self.assertIn("APPLIED", applied.output)
        self.assertIn(f"hidden: {rid}", applied.output)
        self.assertNotIn("lifecycle", self._texts())

        again = self.runner.invoke(args=["whats-new", "--apply", "--id", rid])
        self.assertEqual(again.exit_code, 0, again.output)
        self.assertIn(f"already hidden: {rid}", again.output)
        self.assertIn("hidden: (none)", again.output)
        self.assertNotIn("lifecycle", self._texts())

        self.school.sync_whats_new(_write(Path(self.tmp.name) / "again.json", [self.entry]))
        self.assertNotIn("lifecycle", self._texts())

    def test_apply_without_an_id_writes_nothing(self) -> None:
        result = self.runner.invoke(args=["whats-new", "--apply"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--id", result.output)
        self.assertIn("lifecycle", self._texts())

    def test_missing_id_exits_nonzero_and_leaves_other_rows(self) -> None:
        result = self.runner.invoke(args=["whats-new", "--apply", "--id", "does-not-exist"])
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("missing: does-not-exist", result.output)
        self.assertIn("lifecycle", self._texts())

    def test_production_apply_refuses_without_the_override(self) -> None:
        with mock.patch.dict(os.environ, {"FLASK_ENV": "production", "FLY_APP_NAME": ""}, clear=False):
            result = self.runner.invoke(args=["whats-new", "--apply", "--id", self.entry["id"]])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--i-know-this-is-prod", result.output)
        self.assertIn("lifecycle", self._texts())

        listed = self.runner.invoke(args=["whats-new"])
        self.assertEqual(listed.exit_code, 0, listed.output)
        self.assertIn("DRY RUN", listed.output)


if __name__ == "__main__":
    unittest.main()

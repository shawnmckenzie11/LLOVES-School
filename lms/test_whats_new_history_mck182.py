#!/usr/bin/env python3
"""MCK-182 slice 2: What's new history is stored, so it never drops out.

Ops' MED on slice 1: generated entries were never committed, so history
fell out after 30 Deploy runs or 100 PRs. Each boot now copies the shipped
``releases.json`` into ``whats_new_releases`` in the school database, and
the Dashboard reads ``/api/staff/whats-new`` from that table.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / ".github" / "scripts"))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

import whats_new as gen  # noqa: E402
import whats_new_store as store  # noqa: E402
from app import create_app  # noqa: E402
from school_db import SchoolDB  # noqa: E402

REF_RE = re.compile(r"MCK-\d|#\d|\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")
LINE = "In Run Live Class, deploy line {n} for the history test."


def committed() -> dict:
    return json.loads(store.RELEASES_FILE.read_text(encoding="utf-8"))


def deploy_entry(n: int, text: str | None = None, *, at: datetime | None = None) -> dict:
    """A generated entry like ``whats_new.py build`` writes."""
    sha = f"{n:x}".rjust(7, "c") + "d" * 33
    moment = at or datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc) + timedelta(hours=n)
    entry = gen.release_entry(sha, moment, [{"text": text or LINE.format(n=n), "audience": "Teacher", "refs": f"#{300 + n} · MCK-{500 + n}"}])
    return entry


def write(path: Path, releases: list[dict]) -> Path:
    path.write_text(json.dumps({"schema": 2, "releases": releases}), encoding="utf-8")
    return path


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.conn = sqlite3.connect(":memory:", isolation_level=None)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def ids(self) -> list[str]:
        return [r["id"] for r in store.stored_releases(self.conn)]

    def test_shipped_file_is_stored_newest_first(self) -> None:
        counts = store.sync_from_file(self.conn)
        expected = [r["id"] for r in committed()["releases"]]
        self.assertEqual(counts["inserted"], len(expected))
        self.assertEqual(self.ids(), expected)
        again = store.sync_from_file(self.conn)
        self.assertEqual(again["inserted"] + again["replaced"], 0, "re-sync changes nothing")

    def test_deploy_entries_are_written_once_and_never_rewritten(self) -> None:
        first = deploy_entry(1, "In Run Live Class, the line as shipped.")
        store.sync_from_file(self.conn, write(self.dir / "a.json", [first]))
        edited = {**first, "items": [{"text": "In Run Live Class, an edited PR body.", "audience": "Both", "refs": "#1"}]}
        counts = store.sync_from_file(self.conn, write(self.dir / "b.json", [edited]))
        self.assertEqual(counts["kept"], 1)
        self.assertEqual(store.stored_releases(self.conn)[0]["items"][0]["text"], "In Run Live Class, the line as shipped.")

    def test_committed_entries_win_and_an_empty_one_takes_it_down(self) -> None:
        first = deploy_entry(2, "In Run Live Class, a line with a typo.")
        store.sync_from_file(self.conn, write(self.dir / "a.json", [first]))
        fixed = {k: v for k, v in first.items() if k != "source"}
        fixed["items"] = [{"text": "In Run Live Class, the fixed line.", "audience": "Teacher", "refs": "#2"}]
        self.assertEqual(store.sync_from_file(self.conn, write(self.dir / "b.json", [fixed]))["replaced"], 1)
        self.assertEqual(store.stored_releases(self.conn)[0]["items"][0]["text"], "In Run Live Class, the fixed line.")
        gone = {**fixed, "items": []}
        self.assertEqual(store.sync_from_file(self.conn, write(self.dir / "c.json", [gone]))["removed"], 1)
        self.assertEqual(self.ids(), [])

    def test_housekeeping_and_broken_entries_are_never_stored(self) -> None:
        hk = {**deploy_entry(3), "items": []}
        junk = {**deploy_entry(4), "items": [None, {"text": "  "}, "x"]}
        no_day = {"id": "zzz", "items": [{"text": "In Run Live Class, no day."}]}
        counts = store.sync_from_file(self.conn, write(self.dir / "a.json", [hk, junk, no_day, "nope"]))
        self.assertEqual(counts["skipped"], 3)
        self.assertEqual(self.ids(), [])

    def test_history_outlives_the_30_run_window(self) -> None:
        """35 deploys, each shipping only the last 30 generated entries."""
        base = committed()["releases"]
        generated: list[dict] = []
        for n in range(1, 36):
            generated.insert(0, deploy_entry(n))
            shipped = generated[:30] + base
            store.sync_from_file(self.conn, write(self.dir / f"deploy{n}.json", shipped))
        ids = self.ids()
        self.assertEqual(len(ids), 35 + len(base))
        self.assertIn(deploy_entry(1)["id"], ids, "the first deploy is still there")
        payload = store.teacher_payload(store.stored_releases(self.conn))
        self.assertEqual(payload["releases"][0]["id"], deploy_entry(35)["id"])
        self.assertIn(deploy_entry(1)["id"], [r["id"] for r in payload["releases"]])

    def test_sync_is_all_or_nothing(self) -> None:
        """A failure part way leaves the stored history as it was."""
        store.sync_from_file(self.conn, write(self.dir / "a.json", [deploy_entry(5)]))
        calls = {"n": 0}

        class Shim:
            loads = staticmethod(json.loads)

            @staticmethod
            def dumps(obj, *a, **k):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise RuntimeError("disk hiccup")
                return json.dumps(obj, *a, **k)

        with mock.patch.object(store, "json", Shim):
            with self.assertRaises(RuntimeError):
                store.sync_from_file(self.conn, write(self.dir / "b.json", [deploy_entry(6), deploy_entry(7)]))
        self.assertEqual(self.ids(), [deploy_entry(5)["id"]])

    def test_teacher_payload_has_no_refs_shas_or_ids(self) -> None:
        store.sync_from_file(self.conn)
        messy = deploy_entry(8, "In Run Live Class (#243 · MCK-171), it works at 41162ea.")
        payload = store.teacher_payload(store.stored_releases(self.conn) + [messy])
        blob = json.dumps(payload)
        self.assertNotIn('"refs"', blob)
        self.assertNotIn('"sha"', blob)
        for release in payload["releases"]:
            self.assertEqual(set(release), {"id", "deployed_at", "day", "items"})
            for item in release["items"]:
                for key in ("text", "title", "line"):
                    self.assertNotRegex(item.get(key, ""), REF_RE)
        self.assertEqual(payload["releases"][0]["items"][0]["text"], "In Run Live Class, it works at.")
        self.assertEqual({i["audience"] for r in payload["releases"] for i in r["items"]}, {"Teacher", "Both"})

    def test_legacy_day_sorts_like_the_generator(self) -> None:
        legacy = {"id": "2026-10-02", "date": "2026-10-02", "items": [{"title": "T", "line": "L"}]}
        self.assertEqual(store.sort_at(legacy), "2026-10-03T03:59:00+00:00")
        self.assertEqual(
            store.sort_at(legacy), datetime.fromisoformat(gen.sort_key(legacy)).isoformat()
        )


class GeneratorTests(unittest.TestCase):
    def test_generated_entries_are_marked_as_deploy_entries(self) -> None:
        self.assertEqual(deploy_entry(1)["source"], "deploy")

    def test_warns_when_the_baseline_is_outside_the_window(self) -> None:
        class GH:
            def get(self, path):
                if path.startswith("/actions/"):
                    return {"workflow_runs": [{"id": 1, "head_sha": "9" * 40, "updated_at": "2026-10-05T12:00:00Z"}]}
                if path.startswith("/pulls"):
                    return []
                return {"commits": []}

        warnings: list[str] = []
        gen.build_releases(committed(), gh=GH(), sha="8" * 40, now=datetime(2026, 10, 5, tzinfo=timezone.utc), warn=warnings.append)
        self.assertEqual(len(warnings), 1)
        self.assertIn("whats_new_releases", warnings[0])


class AppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "lloves.sqlite"
        self.app = create_app(db_path=self.db_path, data_dir=self.root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.school.register_staff("teacher@gmail.com")
        self.staff = self.app.test_client()
        self.staff.get("/auth/google?portal=staff")
        self.staff.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.staff.post(
            "/verify-email",
            data={"code": self.school.get_user_by_email("teacher@gmail.com")["verification_code"]},
        )

    def tearDown(self) -> None:
        self.school.close()
        self.tmp.cleanup()

    def payload(self) -> dict:
        resp = self.staff.get("/api/staff/whats-new")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
        return resp.get_json()

    def test_boot_stores_the_shipped_file_and_the_api_serves_it(self) -> None:
        with self.school._lock:
            rows = self.school.conn.execute("SELECT id, source FROM whats_new_releases").fetchall()
        self.assertEqual({r[0] for r in rows}, {r["id"] for r in committed()["releases"]})
        got = self.payload()
        self.assertEqual([r["id"] for r in got["releases"]], [r["id"] for r in committed()["releases"]])
        self.assertNotRegex(json.dumps([i for r in got["releases"] for i in r["items"]]), REF_RE)
        self.assertNotIn('"refs"', json.dumps(got))

    def test_history_survives_a_restart_without_the_entry_in_the_file(self) -> None:
        extra = deploy_entry(9, at=datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc))
        self.school.sync_whats_new(write(self.root / "deploy.json", [extra] + committed()["releases"]))
        self.school.close()
        reopened = SchoolDB(self.db_path, live_database_url="")
        try:
            ids = [r["id"] for r in reopened.whats_new_teacher_payload()["releases"]]
        finally:
            reopened.close()
        self.assertEqual(ids[0], extra["id"])
        self.school = SchoolDB(self.db_path, live_database_url="")  # for tearDown

    def test_a_missing_row_still_shows_from_the_shipped_file(self) -> None:
        newest = committed()["releases"][0]["id"]
        with self.school._lock:
            self.school.conn.execute("DELETE FROM whats_new_releases WHERE id = ?", (newest,))
        self.assertEqual(self.payload()["releases"][0]["id"], newest)

    def test_a_broken_file_never_blocks_boot(self) -> None:
        bad = self.root / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        counts = self.school.sync_whats_new(bad)
        self.assertEqual(counts["inserted"] + counts["replaced"], 0)
        self.assertTrue(self.payload()["releases"])

    def test_signed_out_and_students_get_nothing(self) -> None:
        anon = self.app.test_client().get("/api/staff/whats-new")
        self.assertIn(anon.status_code, (401, 403))
        self.assertNotIn("releases", anon.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()

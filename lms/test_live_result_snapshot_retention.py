#!/usr/bin/env python3
"""MCK-45 follow-up: End results snapshots are read per run, deleted, pruned.

Ops MED-1 (PR #194): Start reuses the wiped session id, so a read keyed on
``live_session_id`` mixes every run of a class. Reads take ``run_key``.
Ops MED-2: the rows hold codenames and raw answers. Class delete and roster
delete remove them, and a retention prune drops old runs.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from typing import Any

import school_db
import test_live_shell as shell


class SnapshotRetentionTests(shell.LiveShellTests):
    """Reuse the live-shell fixture. Only the tests below run here."""

    def _run_once(self, *, birch: bool = False) -> tuple[int, str, int]:
        """Start, answer, and End one class run.

        Args:
            birch: Also have Birch join and answer Minds-On.

        Returns:
            ``(session_id, run_key, aspen_id)``.
        """
        sid, aspen = self._open_live_with_aspen_answers()
        if birch:
            row = self.school.game.find_student_by_codename(self.class_id, "Birch")
            assert row is not None
            birch_id = int(row["id"])
            self.school.join_live_class_session(sid, birch_id, codename="Birch")
            with self.school._lock:
                prompt_id = self.school.conn.execute(
                    "SELECT id FROM live_session_prompts WHERE live_session_id = ? "
                    "ORDER BY id LIMIT 1",
                    (sid,),
                ).fetchone()[0]
            self.school.submit_live_prompt_response(
                int(prompt_id), birch_id, {"choice": shell.MINDS_ON_CHOICES[1]}
            )
        run_key = str(self.school.get_live_session(sid)["run_key"])
        self.assertTrue(run_key)
        ended = self.client.post(
            f"/staff/class/{self.class_id}/end-live", follow_redirects=False
        )
        self.assertEqual(ended.status_code, 302)
        return sid, run_key, aspen

    def _count(self, where: str = "1", params: tuple[Any, ...] = ()) -> int:
        """Count snapshot rows matching ``where``."""
        with self.school._lock:
            return int(
                self.school.conn.execute(
                    f"SELECT COUNT(*) FROM live_result_snapshots WHERE {where}",
                    params,
                ).fetchone()[0]
            )

    def test_reused_session_id_does_not_mix_runs(self) -> None:
        """Two runs share a session id; each read returns only its run."""
        sid1, key1, _ = self._run_once()
        first = self.school.live_result_snapshot_rows(self.class_id, run_key=key1)
        self.assertTrue(first)
        sid2, key2, _ = self._run_once()
        self.assertEqual(sid2, sid1)  # the Ops repro: the id is reused
        self.assertNotEqual(key2, key1)
        again = self.school.live_result_snapshot_rows(self.class_id, run_key=key1)
        second = self.school.live_result_snapshot_rows(self.class_id, run_key=key2)
        self.assertEqual([r["id"] for r in again], [r["id"] for r in first])
        self.assertTrue(second)
        self.assertTrue(all(r["run_key"] == key2 for r in second))
        self.assertFalse({r["id"] for r in first} & {r["id"] for r in second})
        runs = self.school.live_result_snapshot_runs(self.class_id)
        self.assertEqual([r["run_key"] for r in runs], [key2, key1])
        self.assertEqual(runs[1]["rows"], len(first))
        self.assertEqual({r["live_session_id"] for r in runs}, {sid1})
        with self.assertRaises(ValueError):
            self.school.live_result_snapshot_rows(self.class_id, run_key="")

    def test_run_read_uses_the_run_index(self) -> None:
        """The read and the prune have matching indexes."""
        with self.school._lock:
            names = {
                row["name"]
                for row in self.school.conn.execute(
                    "PRAGMA index_list(live_result_snapshots)"
                )
            }
            cols = [
                row["name"]
                for row in self.school.conn.execute(
                    "PRAGMA index_info(idx_live_result_snapshots_run)"
                )
            ]
            plan = " ".join(
                str(row["detail"])
                for row in self.school.conn.execute(
                    "EXPLAIN QUERY PLAN SELECT * FROM live_result_snapshots "
                    "WHERE class_id = ? AND run_key = ? ORDER BY id",
                    (1, "x"),
                )
            )
        self.assertIn("idx_live_result_snapshots_snapshot_at", names)
        self.assertEqual(cols, ["class_id", "run_key", "id"])
        self.assertIn("idx_live_result_snapshots_run", plan)
        self.assertNotIn("TEMP B-TREE", plan)

    def test_staff_delete_removes_class_snapshots(self) -> None:
        """Permanently deleting the teacher drops their classes' snapshots."""
        self._run_once()
        self.assertGreater(self._count("class_id = ?", (self.class_id,)), 0)
        it_user = self.school.register_staff("it-desk@gmail.com")
        self.school.delete_staff_permanently(
            int(self.teacher["id"]), int(it_user["id"])
        )
        self.assertEqual(self._count("class_id = ?", (self.class_id,)), 0)

    def test_roster_delete_removes_only_that_student(self) -> None:
        """Deleting Birch drops Birch's rows; Aspen's stay."""
        _, key, aspen = self._run_once(birch=True)
        rows = self.school.live_result_snapshot_rows(self.class_id, run_key=key)
        birch = int(
            self.school.game.find_student_by_codename(self.class_id, "Birch")["id"]
        )
        self.assertTrue(any(r["student_id"] == birch for r in rows))
        aspen_rows = [r for r in rows if r["student_id"] == aspen]
        self.assertTrue(aspen_rows)
        resp = self.client.post(
            f"/api/classes/{self.class_id}/students/delete",
            json={"student_id": birch},
        )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        left = self.school.live_result_snapshot_rows(self.class_id, run_key=key)
        self.assertFalse(any(r["student_id"] == birch for r in left))
        self.assertEqual(
            [r["id"] for r in left if r["student_id"] == aspen],
            [r["id"] for r in aspen_rows],
        )

    def test_roster_replace_removes_dropped_students(self) -> None:
        """Replacing the roster without Birch drops Birch's rows too.

        SQLite gives Birch's freed id to Cedar, so the purge must use the
        ids removed, not "ids no longer on the roster".
        """
        _, key, aspen = self._run_once(birch=True)
        resp = self.client.put(
            f"/api/staff/classes/{self.class_id}/roster",
            json={"codenames": ["Aspen", "Cedar"]},
        )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        left = self.school.live_result_snapshot_rows(self.class_id, run_key=key)
        self.assertTrue(left)
        self.assertNotIn("Birch", {r["codename"] for r in left})
        self.assertTrue(
            all(r["student_id"] in (None, aspen) for r in left), left
        )

    def test_prune_drops_runs_older_than_retention(self) -> None:
        """Rows past 365 days go; recent rows stay. End and boot prune."""
        self.assertEqual(school_db.LIVE_RESULT_SNAPSHOT_RETENTION_DAYS, 365)
        _, key1, _ = self._run_once()
        old = (datetime.now() - timedelta(days=366)).replace(microsecond=0)
        with self.school._lock:
            self.school.conn.execute(
                "UPDATE live_result_snapshots SET snapshot_at = ? WHERE run_key = ?",
                (old.isoformat(), key1),
            )
            self.school.conn.commit()
        # A row just inside the window stays.
        self.assertEqual(
            self.school.prune_live_result_snapshots(days=367), 0
        )
        # The next End prunes the old run and keeps its own.
        _, key2, _ = self._run_once()
        self.assertEqual(self._count("run_key = ?", (key1,)), 0)
        kept = self._count("run_key = ?", (key2,))
        self.assertGreater(kept, 0)
        # Boot prunes too.
        with self.school._lock:
            self.school.conn.execute(
                "UPDATE live_result_snapshots SET snapshot_at = ? WHERE run_key = ?",
                (old.isoformat(), key2),
            )
            self.school.conn.commit()
        path = self.school.db_path
        self.school.close()
        self.school = school_db.SchoolDB(path)
        self.assertEqual(self._count(), 0)
        self.assertEqual(self.school.prune_live_result_snapshots(days=0), 0)


def load_tests(
    loader: unittest.TestLoader, _tests: Any, _pattern: Any
) -> unittest.TestSuite:
    """Run only this file's tests, not the inherited live-shell ones."""
    names = [
        name
        for name, value in vars(SnapshotRetentionTests).items()
        if name.startswith("test_") and callable(value)
    ]
    return unittest.TestSuite(SnapshotRetentionTests(name) for name in sorted(names))


if __name__ == "__main__":
    unittest.main()

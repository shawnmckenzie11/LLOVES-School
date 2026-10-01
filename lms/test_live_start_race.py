#!/usr/bin/env python3
"""MCK-114: two simultaneous Starts must not open two live sessions.

Production runs four gunicorn workers, each with its own sqlite connection.
A double-click on Run Live Class, or two teacher tabs, can send two Starts
for one class at once. ``start_live_class_session`` used to check for an
active session and then insert, with no guard in between, so both workers
could pass the check and the class ended up with two active sessions and
two different join codes (Ops smoke on #205: 3/24 rounds on 9054ce9, 7/24
on main 2a908cc).

These tests start real worker processes on one sqlite file, release them
at the same instant, and check that every Start returns the same session.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)

from curriculum import seed_curriculum  # noqa: E402
from school_db import SchoolDB  # noqa: E402

WORKERS = 4

#: One "gunicorn worker": open SchoolDB, say ready, then run one Start per
#: round the moment the parent drops that round's go-file.
CHILD = r"""
import json, os, sys, time
from pathlib import Path
lms, db_path, root, prefix, teacher_id, rounds = sys.argv[1:7]
class_ids = [int(x) for x in sys.argv[7].split(",")]
sys.path.insert(0, lms)
sys.path.insert(0, str(Path(lms).parent))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)
from school_db import SchoolDB
db = SchoolDB(Path(db_path), Path(root), live_database_url="")
print("ready", flush=True)
for r in range(int(rounds)):
    go = Path(f"{prefix}{r}")
    while not go.exists():
        time.sleep(0.0002)
    class_id = class_ids[r % len(class_ids)]
    try:
        row = db.start_live_class_session(class_id, int(teacher_id))
        out = {"class_id": class_id, "id": int(row["id"]),
               "code": str(row["session_code"]),
               "class_of_row": int(row["class_id"])}
    except ValueError as exc:
        out = {"class_id": class_id, "refused": str(exc)}
    except Exception as exc:
        out = {"class_id": class_id, "error": repr(exc)}
    print(json.dumps(out), flush=True)
db.close()
"""


class LiveStartRaceTests(unittest.TestCase):
    """Several worker processes pressing Start at the same moment."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "lloves.sqlite"
        self.school = SchoolDB(self.db_path, self.root, live_database_url="")
        seed_curriculum(self.school)
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        self.offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        self.class_ids = [self._new_class("2:00pm"), self._new_class("3:15pm")]

    def tearDown(self) -> None:
        self.school.close()
        self._tmp.cleanup()

    def _new_class(self, time_label: str) -> int:
        created = self.school.game.create_class(
            year="2026/27",
            semester="Semester 1",
            course_code="MCF3M",
            days_preset="M/W/F",
            time_label=time_label,
            codenames=["Aspen", "Birch", "Cedar", "Maple"],
            offering_id=int(self.offering["id"]),
            teacher_user_id=int(self.teacher["id"]),
        )
        return int(created["id"])

    def _active_rows(self) -> list[dict[str, Any]]:
        with self.school._lock:
            rows = self.school.conn.execute(
                "SELECT id, class_id, session_code FROM live_class_sessions "
                "WHERE status = 'active' ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def _race(self, rounds: int, class_ids: list[int]) -> list[list[dict]]:
        """Run ``rounds`` simultaneous Starts across ``WORKERS`` processes.

        Every session is ended between rounds, so each round is a fresh
        Start. Returns, per round, each worker's result.
        """
        prefix = str(self.root / "go-")
        args = [
            str(LMS_DIR),
            str(self.db_path),
            str(self.root),
            prefix,
            str(int(self.teacher["id"])),
            str(rounds),
        ]
        procs = []
        logs = []
        for index in range(WORKERS):
            # Alternate classes per worker when two are given.
            ordered = class_ids[index % len(class_ids) :] + class_ids[
                : index % len(class_ids)
            ]
            # stderr goes to a file: a full pipe would stall the worker.
            log = open(self.root / f"worker-{index}.log", "w+", encoding="utf-8")
            logs.append(log)
            procs.append(
                subprocess.Popen(
                    [sys.executable, "-c", CHILD, *args, ",".join(map(str, ordered))],
                    stdout=subprocess.PIPE,
                    stderr=log,
                    text=True,
                )
            )
        results: list[list[dict]] = []
        try:
            for index, proc in enumerate(procs):
                line = proc.stdout.readline().strip()  # type: ignore[union-attr]
                if line != "ready":
                    self.fail(f"worker {index} did not boot: {self._log(index)}")
            for r in range(rounds):
                Path(f"{prefix}{r}").touch()
                outs = []
                for index, proc in enumerate(procs):
                    line = proc.stdout.readline().strip()  # type: ignore[union-attr]
                    if not line:
                        self.fail(f"worker {index} died: {self._log(index)}")
                    outs.append(json.loads(line))
                results.append(outs)
                results[-1].append({"active": self._active_rows()})
                self.school.end_all_active_live_sessions()
                time.sleep(0.05)
        finally:
            # Let every worker finish its rounds, then stop it.
            for r in range(rounds):
                Path(f"{prefix}{r}").touch()
            for proc in procs:
                try:
                    proc.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                if proc.stdout is not None:
                    proc.stdout.close()
            for log in logs:
                log.close()
        return results

    def _log(self, index: int) -> str:
        path = self.root / f"worker-{index}.log"
        return path.read_text(encoding="utf-8")[-2000:] if path.exists() else ""

    def test_simultaneous_starts_for_one_class_share_one_session(self) -> None:
        """Every worker's Start returns the same session and code."""
        class_id = self.class_ids[0]
        bad: list[str] = []
        for r, outs in enumerate(self._race(6, [class_id])):
            active = outs.pop()["active"]
            errors = [o for o in outs if "id" not in o]
            ids = {o.get("id") for o in outs}
            codes = {o.get("code") for o in outs}
            if errors or len(active) != 1 or len(ids) != 1 or len(codes) != 1:
                bad.append(f"round {r}: active={active} outs={outs}")
                continue
            if int(active[0]["id"]) not in ids:
                bad.append(f"round {r}: returned {ids}, active {active}")
        self.assertEqual(bad, [], "\n".join(bad))

    def test_one_teacher_two_classes_at_once_opens_one(self) -> None:
        """Same teacher, two classes at once: one opens, the other is refused."""
        bad: list[str] = []
        for r, outs in enumerate(self._race(4, self.class_ids)):
            active = outs.pop()["active"]
            if any("error" in o for o in outs) or len(active) != 1:
                bad.append(f"round {r}: active={active} outs={outs}")
                continue
            winner = int(active[0]["class_id"])
            for o in outs:
                if o["class_id"] == winner:
                    ok = o.get("id") == int(active[0]["id"])
                else:
                    ok = "refused" in o
                if not ok:
                    bad.append(f"round {r}: winner={winner} out={o}")
        self.assertEqual(bad, [], "\n".join(bad))

    def test_insert_rechecks_when_the_early_check_was_stale(self) -> None:
        """Without the lock (busy past its wait), the insert's re-check still
        returns the session another worker made a moment earlier."""
        class_id = self.class_ids[0]
        first = self.school.start_live_class_session(
            class_id, int(self.teacher["id"])
        )
        # Pretend this worker's early check ran before ``first`` committed.
        self.school._active_live_session_blocking_start = (  # type: ignore[method-assign]
            lambda _c, _t: None
        )
        again = self.school._start_live_class_session_locked(
            class_id,
            int(self.teacher["id"]),
            int(self.offering["id"]),
            live_module=None,
            live_slot=None,
        )
        self.assertEqual(int(again["id"]), int(first["id"]))
        self.assertEqual(again["session_code"], first["session_code"])
        self.assertEqual(len(self._active_rows()), 1)
        self.assertFalse(self.school.conn.in_transaction)

    def test_other_teacher_cannot_open_a_second_session_for_the_class(self) -> None:
        """A class has at most one active session, whoever presses Start."""
        class_id = self.class_ids[0]
        self.school.start_live_class_session(class_id, int(self.teacher["id"]))
        other = self.school.register_staff("other@gmail.com")
        with self.assertRaisesRegex(ValueError, "already has a live class"):
            self.school.start_live_class_session(class_id, int(other["id"]))
        self.assertEqual(len(self._active_rows()), 1)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""MCK-251: module bands, finish-before-start dialog, local backfill."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))
sys.path.insert(0, str(LMS_DIR.parent / "scripts"))
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from participation_bands import (  # noqa: E402
    build_participation_bands,
    format_participation_grouped_header,
    is_office_slot,
    start_prompt_for,
)
from participation_module_backfill import (  # noqa: E402
    apply_participation_module_backfill,
    backfill_counts,
    finish_module_subtotal_name,
    plan_participation_module_backfill,
)
from test_participation_mck198 import Base  # noqa: E402


class BandTests(unittest.TestCase):
    """Column grouping and the approved dialog copy."""

    def test_grouped_header_uses_weekday_and_month(self) -> None:
        label = format_participation_grouped_header("M3", "C1", "2026-10-12T16:00:00")
        self.assertEqual(label, "M3 · C1 · Mon Oct 12")

    def test_office_slot_is_muted_label_without_a_date(self) -> None:
        self.assertTrue(is_office_slot("O1"))
        self.assertFalse(is_office_slot("C1"))
        self.assertEqual(
            format_participation_grouped_header("M3", "O1", "2026-10-16T16:00:00"),
            "M3 · O1",
        )

    def test_finished_modules_collapse_and_current_is_the_latest_open(self) -> None:
        columns = [
            {"id": 1, "kind": "session"},
            {"id": 2, "kind": "session"},
            {"id": 9, "kind": "subtotal"},
            {"id": 3, "kind": "session"},
            {"id": 4, "kind": "session"},
        ]
        meta = {
            "1": {"live_module": "M1", "live_slot": "C1", "ended_at": "2026-10-05T16:00:00"},
            "2": {"live_module": "M1", "live_slot": "C2", "ended_at": "2026-10-07T16:00:00"},
            "3": {"live_module": "M3", "live_slot": "C1", "ended_at": "2026-10-12T16:00:00"},
            "4": {"live_module": "M3", "live_slot": "O1", "ended_at": "2026-10-16T16:00:00"},
        }
        built = build_participation_bands(columns, meta, {"M1": True, "M3": False})
        self.assertEqual(built["current_module"], "M3")
        roles = {band["module"]: band["role"] for band in built["bands"]}
        self.assertEqual(roles, {"M1": "finished", "M3": "current"})
        m3 = next(band for band in built["bands"] if band["module"] == "M3")
        self.assertEqual([col["label"] for col in m3["columns"]][0], "M3 · C1 · Mon Oct 12")
        self.assertTrue(m3["columns"][1]["office"])
        self.assertTrue(all(band["module"] != "subtotal" for band in built["bands"]))

    def test_start_prompt_matches_the_approved_dialog(self) -> None:
        bands = {"current_module": "M3", "bands": []}
        prompt = start_prompt_for(bands, "M4")
        assert prompt is not None
        self.assertEqual(prompt["title"], "Module 3 isn't finished yet")
        self.assertEqual(
            prompt["body"],
            "Finish it in the gradebook first? This locks the Module 3 total and collapses the module.",
        )
        self.assertEqual(prompt["finish_label"], "Finish M3 and start M4")
        self.assertEqual(prompt["skip_label"], "Start without finishing")
        self.assertEqual(prompt["cancel_label"], "Cancel")
        self.assertIsNone(start_prompt_for(bands, "M3"))
        self.assertIsNone(start_prompt_for(bands, "M2"))


class BackfillTests(Base):
    """Past modules finish once; the current module stays open."""

    def _wire(self, run_key: str, answered: int) -> None:
        """Attach a participation column so the module has a session to freeze."""
        student = self.students[0]
        credits = {student: answered} if answered else {}
        self.school.game.persist_end_class_column(
            self.class_id,
            [student],
            credits,
            include_attendance=True,
            include_participation=bool(credits),
            run_key=run_key,
            create_game=True,
        )

    def _session_id(self, run_key: str) -> int:
        """Game session id captured for one live run."""
        meta = self.school.participation_session_meta(self.class_id)
        for session_id, info in meta.items():
            if str(info.get("run_key") or "") == run_key:
                return int(session_id)
        self.fail(f"no session for {run_key}")
        return 0

    def _subtotal_rows(self) -> list[dict]:
        """Every subtotal row for this class."""
        return [
            dict(row)
            for row in self.school.game.conn.execute(
                "SELECT name, through_session_id FROM subtotals WHERE class_id = ?",
                (self.class_id,),
            )
        ]

    def _levels(self) -> dict[tuple[int, str], int | None]:
        """Participation level for each student and module."""
        data = self.school.class_participation(self.class_id)
        return {
            (int(row["student_id"]), mod): cell.get("level")
            for row in data["students"]
            for mod, cell in row["modules"].items()
        }

    def test_backfill_writes_one_subtotal_through_the_module_last_session(self) -> None:
        """A second apply, even of the same plan, does not mint Finish Module 1 2."""
        m1_late = self.run_class(
            "M1", "C2", 1, 0, {self.students[0]: 1},
            ended_iso="2026-10-07T16:00:00", wipe=False,
        )
        m1_early = self.run_class(
            "M1", "C1", 1, 0, {self.students[0]: 1},
            ended_iso="2026-10-05T16:00:00", wipe=False,
        )
        m2 = self.run_class(
            "M2", "C1", 1, 0, {self.students[0]: 1},
            ended_iso="2026-10-12T16:00:00", wipe=False,
        )
        self._wire(m1_late, 1)
        self._wire(m1_early, 1)
        self._wire(m2, 1)
        late_id = self._session_id(m1_late)
        early_id = self._session_id(m1_early)
        self.assertLess(late_id, early_id)
        self.assertNotEqual(late_id, self._session_id(m2))
        self.assertFalse(self.school.module_complete(self.class_id, "M1"))

        before = backfill_counts(self.school)
        plan = plan_participation_module_backfill(self.school)
        actions = {
            step.module: step for step in plan if step.class_id == self.class_id
        }
        self.assertEqual(actions["M1"].action, "finish")
        self.assertEqual(actions["M1"].through_session_id, late_id)
        self.assertEqual(actions["M2"].action, "skip-current")
        self.assertEqual(before["finish_subtotals"], 0)

        apply_participation_module_backfill(self.school, plan)
        self.assertTrue(self.school.module_complete(self.class_id, "M1"))
        self.assertFalse(self.school.module_complete(self.class_id, "M2"))
        rows = self._subtotal_rows()
        self.assertEqual([row["name"] for row in rows], ["Finish Module 1"])
        self.assertEqual(int(rows[0]["through_session_id"]), late_id)
        marks = self.school.conn.execute(
            """
            SELECT COUNT(*) AS n FROM participation_module_marks
            WHERE class_id = ? AND live_module = 'M1'
            """,
            (self.class_id,),
        ).fetchone()["n"]
        self.assertEqual(int(marks), 1)

        apply_participation_module_backfill(self.school, plan)
        again = plan_participation_module_backfill(self.school)
        again_actions = {
            (step.module, step.action) for step in again if step.class_id == self.class_id
        }
        self.assertIn(("M1", "skip-finished"), again_actions)
        self.assertIn(("M2", "skip-current"), again_actions)
        apply_participation_module_backfill(self.school, again)
        names = [row["name"] for row in self._subtotal_rows()]
        self.assertEqual(names, ["Finish Module 1"])
        self.assertNotIn("Finish Module 1 2", names)
        after = backfill_counts(self.school)
        self.assertEqual(after["finish_subtotals"], before["finish_subtotals"] + 1)
        self.assertEqual(after["marks"], before["marks"] + 1)

    def test_existing_mark_or_finish_subtotal_is_left_alone(self) -> None:
        """Either existing row skips the whole module; the other is not filled in."""
        marked = self.run_class(
            "M1", "C1", 1, 0, {self.students[0]: 1}, ended_iso="2026-10-05T16:00:00"
        )
        current = self.run_class(
            "M3", "C1", 1, 0, {self.students[0]: 1}, ended_iso="2026-10-12T16:00:00"
        )
        self._wire(marked, 1)
        self._wire(current, 1)
        self.school.set_module_complete(self.class_id, "M1", True)
        plan = plan_participation_module_backfill(self.school)
        m1 = next(step for step in plan if step.module == "M1")
        self.assertEqual(m1.action, "skip-finished")
        apply_participation_module_backfill(self.school, plan)
        self.assertEqual(self._subtotal_rows(), [])

        frozen = self.run_class(
            "M2", "C1", 1, 0, {self.students[0]: 1}, ended_iso="2026-10-08T16:00:00"
        )
        self._wire(frozen, 1)
        through = self._session_id(frozen)
        self.school.game.freeze_subtotal(
            self.class_id,
            name=finish_module_subtotal_name("M2"),
            sort="az",
            through_session_id=through,
        )
        plan = plan_participation_module_backfill(self.school)
        m2 = next(step for step in plan if step.module == "M2")
        self.assertEqual(m2.action, "skip-finished")
        apply_participation_module_backfill(self.school, plan)
        mark = self.school.conn.execute(
            """
            SELECT 1 FROM participation_module_marks
            WHERE class_id = ? AND live_module = 'M2'
            """,
            (self.class_id,),
        ).fetchone()
        self.assertIsNone(mark)
        self.assertEqual(
            [row["name"] for row in self._subtotal_rows()],
            ["Finish Module 2"],
        )

    def test_level_and_l4_needs_group_do_not_shift(self) -> None:
        """Finishing a module locks the flag; it does not rescore Level 4."""
        m1 = self.run_class(
            "M1", "C1", 3, 0, {self.students[0]: 3}, ended_iso="2026-10-05T16:00:00"
        )
        m2 = self.run_class(
            "M2", "C1", 3, 0, {self.students[0]: 2}, ended_iso="2026-10-12T16:00:00"
        )
        self._wire(m1, 1)
        self._wire(m2, 1)
        self.assertTrue(self.school.set_module_l4_needs_group(self.class_id, "M1", True))
        levels = self._levels()
        self.assertEqual(levels[(self.students[0], "M1")], 4)
        l4_before = self.school.module_l4_needs_group(self.class_id, "M1")
        apply_participation_module_backfill(self.school)
        self.assertEqual(self._levels(), levels)
        self.assertEqual(self.school.module_l4_needs_group(self.class_id, "M1"), l4_before)
        self.assertTrue(self.school.module_complete(self.class_id, "M1"))
        with self.assertRaises(PermissionError):
            self.school.set_module_l4_needs_group(self.class_id, "M1", False)

    def test_students_do_not_see_subtotal_columns(self) -> None:
        """The student dashboard is participation levels, not frozen subtotals.

        Subtotal columns live on the staff game dashboard. The student
        payload has no subtotal name, points, or column kind.
        """
        m1 = self.run_class(
            "M1", "C1", 1, 0, {self.students[0]: 1}, ended_iso="2026-10-05T16:00:00"
        )
        m2 = self.run_class(
            "M2", "C1", 1, 0, {self.students[0]: 1}, ended_iso="2026-10-12T16:00:00"
        )
        self._wire(m1, 1)
        self._wire(m2, 1)
        apply_participation_module_backfill(self.school)
        payload = self.school.student_dashboard_payload(self.class_id, self.students[0])
        blob = json.dumps(payload)
        self.assertNotIn("subtotal", blob.casefold())
        self.assertNotIn("Finish Module", blob)
        staff = self.school.game.dashboard(self.class_id, sort="az")
        staff_names = [
            col.get("name") for col in staff["columns"] if col.get("kind") == "subtotal"
        ]
        self.assertIn("Finish Module 1", staff_names)

    def test_participation_page_and_start_check(self) -> None:
        m1 = self.run_class("M1", "C1", 1, 0, {self.students[0]: 1})
        m3 = self.run_class("M3", "C1", 1, 0, {self.students[0]: 1}, ended_iso="2026-10-19T16:00:00")
        self._wire(m1, 1)
        self._wire(m3, 1)
        self.school.set_module_complete(self.class_id, "M1", True)
        client = self.app.test_client()
        client.get("/auth/google?portal=staff")
        client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        code = self.school.get_user_by_email("teacher@gmail.com")["verification_code"]
        client.post("/verify-email", data={"code": code})
        html = client.get(
            f"/staff/class/{self.class_id}?tab=ap&view=participation"
        ).get_data(as_text=True)
        self.assertIn('id="ap-expand-all"', html)
        self.assertIn('id="ap-collapse-all"', html)
        self.assertIn('id="ap-display-mode-select"', html)
        self.assertIn('id="ap-include-individual"', html)
        self.assertIn('id="ap-include-group"', html)
        self.assertIn("Page loads scrolled to the current module.", html)
        self.assertNotIn("Process evidence (this live session)", html)
        self.assertNotIn("Qualitative observations from Team Challenge", html)
        live = client.get(f"/staff/class/{self.class_id}?tab=live").get_data(as_text=True)
        self.assertIn('id="finish-module-dialog"', live)
        self.assertIn("Start without finishing", live)
        self.assertIn(">Cancel<", live)
        grid = client.get(f"/api/classes/{self.class_id}/participation-grid?sort=az").get_json()
        self.assertEqual(grid["module_bands"]["current_module"], "M3")
        roles = {band["module"]: band["role"] for band in grid["module_bands"]["bands"]}
        self.assertEqual(roles.get("M1"), "finished")
        self.assertEqual(roles.get("M3"), "current")
        quiet = client.get(
            f"/api/classes/{self.class_id}/participation/start-check?module=M3"
        ).get_json()
        self.assertIsNone(quiet["prompt"])
        prompted = client.get(
            f"/api/classes/{self.class_id}/participation/start-check?module=M4"
        ).get_json()
        self.assertEqual(prompted["prompt"]["title"], "Module 3 isn't finished yet")
        self.assertEqual(
            prompted["prompt"]["body"],
            "Finish it in the gradebook first? This locks the Module 3 total and collapses the module.",
        )
        self.assertEqual(prompted["prompt"]["finish_label"], "Finish M3 and start M4")


class BackfillScriptTests(unittest.TestCase):
    """The script refuses production and supports --dry-run."""

    def test_production_is_refused_before_any_write(self) -> None:
        from backfill_participation_modules import main, refusal_reason

        quiet = {
            "FLY_APP_NAME": "",
            "FLASK_ENV": "development",
            "LLOVES_DATA_DIR": "",
            "DATA_DIR": "",
            "LLOVES_DB": "",
        }
        with mock.patch.dict(os.environ, quiet, clear=False):
            self.assertIsNone(refusal_reason(Path("/tmp/local.sqlite")))
        with mock.patch.dict(os.environ, {"FLY_APP_NAME": "lloves-lms"}, clear=False):
            self.assertIn("FLY_APP_NAME", refusal_reason(Path("/tmp/local.sqlite")) or "")
            code = main(["--dry-run", "--db", "/tmp/does-not-matter.sqlite"])
        self.assertEqual(code, 2)
        with mock.patch.dict(os.environ, quiet, clear=False):
            self.assertIsNotNone(refusal_reason(Path("/data/lloves.sqlite")))
        with mock.patch.dict(
            os.environ,
            {"FLASK_ENV": "production", "FLY_APP_NAME": "", "LLOVES_DB": ""},
            clear=False,
        ):
            self.assertIn("production", refusal_reason(None) or "")
        with mock.patch.dict(os.environ, {**quiet, "LLOVES_DB": "/data/lloves.sqlite"}, clear=False):
            self.assertIsNotNone(refusal_reason(None))

    def test_dry_run_is_the_default_and_prints_counts(self) -> None:
        from backfill_participation_modules import main

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "lloves.sqlite"
            env = {
                "FLY_APP_NAME": "",
                "FLASK_ENV": "development",
                "LLOVES_DATA_DIR": "",
                "DATA_DIR": "",
                "LLOVES_DB": "",
                "LOCAL_DEV_LOGIN": "",
            }
            buf = io.StringIO()
            with mock.patch.dict(os.environ, env, clear=False), mock.patch("sys.stdout", buf):
                code = main(["--db", str(db)])
            text = buf.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("DRY RUN", text)
            self.assertIn("before: participation_module_marks=0 finish_module_subtotals=0", text)
            self.assertIn("after: participation_module_marks=0 finish_module_subtotals=0", text)
            self.assertNotIn("APPLIED", text)
            self.assertTrue(db.exists())


class SafeProdScriptTests(Base):
    """MCK-251 prod opt-in: refuse by default, dry-run writes nothing,
    apply leaves current_class_id alone, and a second apply is a no-op."""

    _wire = BackfillTests._wire

    def _seed(self) -> None:
        """Two finished-able modules plus a current one, and a pinned current class."""
        runs = [
            self.run_class("M1", "C1", 1, 0, {self.students[0]: 1},
                           ended_iso="2026-10-05T16:00:00", wipe=False),
            self.run_class("M2", "C1", 1, 0, {self.students[0]: 1},
                           ended_iso="2026-10-07T16:00:00", wipe=False),
            self.run_class("M3", "C1", 1, 0, {self.students[0]: 1},
                           ended_iso="2026-10-12T16:00:00", wipe=False),
        ]
        for run in runs:
            self._wire(run, 1)
        self.pinned = str(self.class_id + 4242)
        g = self.school.game.conn
        g.execute(
            "INSERT INTO app_state (key, value) VALUES ('current_class_id', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (self.pinned,),
        )
        g.commit()
        self.school.conn.commit()

    def _run(self, argv: list[str], *, prod: str | None = None) -> tuple[int, str, str]:
        import backfill_participation_modules as script

        out, err = io.StringIO(), io.StringIO()
        env = {"FLY_APP_NAME": "", "FLASK_ENV": "development", "LLOVES_DATA_DIR": "",
               "DATA_DIR": "", "LLOVES_DB": "", "LOCAL_DEV_LOGIN": ""}
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(script, "refusal_reason", return_value=prod), \
                mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            code = script.main(argv)
        return code, out.getvalue(), err.getvalue()

    def _db(self) -> str:
        return str(Path(self.tmp.name) / "lloves.sqlite")

    def _dump(self) -> list[str]:
        """Logical dump of every sqlite file in the temp data dir."""
        import sqlite3

        lines: list[str] = []
        for path in sorted(Path(self.tmp.name).rglob("*.sqlite*")):
            if path.suffix in {".sqlite-wal", ".sqlite-shm"} or path.name.endswith(("-wal", "-shm")):
                continue
            conn = sqlite3.connect(str(path))
            try:
                lines.append(f"== {path.name}")
                lines.extend(conn.iterdump())
            finally:
                conn.close()
        return lines

    def _current(self) -> str | None:
        row = self.school.game.conn.execute(
            "SELECT value FROM app_state WHERE key = 'current_class_id'"
        ).fetchone()
        return None if row is None else str(row["value"])

    def test_refuses_without_both_flags(self) -> None:
        prod = "database is on the production volume (/data/lloves.sqlite)"
        for argv in (
            [],
            ["--apply"],
            ["--production"],
            ["--confirm", "MCK-251"],
            ["--production", "--confirm", "nope"],
            ["--production", "--apply"],
        ):
            with self.subTest(argv=argv):
                with mock.patch("app.create_app") as opened:
                    code, out, err = self._run(argv + ["--db", self._db()], prod=prod)
                self.assertEqual(code, 2)
                self.assertIn("Refusing", err)
                self.assertIn("MCK-251", err)
                opened.assert_not_called()
        code, _, err = self._run(["--production", "--db", self._db()], prod=None)
        self.assertEqual(code, 2)
        self.assertIn("must be passed together", err)

    def test_prod_dry_run_writes_nothing(self) -> None:
        self._seed()
        before = self._dump()
        prod = "database is on the production volume (/data/lloves.sqlite)"
        code, out, _ = self._run(
            ["--production", "--confirm", "MCK-251", "--db", self._db()], prod=prod)
        self.assertEqual(code, 0)
        self.assertIn("target: PRODUCTION", out)
        self.assertIn("DRY RUN", out)
        self.assertIn("plan:", out)
        self.assertIn("subtotal=Finish Module 1", out)
        self.assertIn("summary: ", out)
        self.assertIn("marks_changed=0 subtotals_changed=0", out)
        self.assertEqual(self._dump(), before)

    def test_apply_keeps_current_class_and_second_apply_changes_nothing(self) -> None:
        self._seed()
        prod = "database is on the production volume (/data/lloves.sqlite)"
        argv = ["--production", "--confirm", "MCK-251", "--apply", "--db", self._db()]
        code, out, err = self._run(argv, prod=prod)
        self.assertEqual(code, 0, err)
        self.assertIn("marks_changed=2 subtotals_changed=2", out)
        self.assertEqual(self._current(), self.pinned)
        after_first = self._dump()
        code, out, err = self._run(argv, prod=prod)
        self.assertEqual(code, 0, err)
        self.assertIn("planned_finishes=0", out)
        self.assertIn("marks_changed=0 subtotals_changed=0", out)
        self.assertEqual(self._current(), self.pinned)
        self.assertEqual(self._dump(), after_first)

    def test_changed_current_class_exits_loudly(self) -> None:
        import participation_module_backfill as mod

        self._seed()
        real = mod.apply_participation_module_backfill

        def clobber(school, plan=None):
            school.game.set_current_class(self.class_id)
            return real(school, plan)

        with mock.patch.object(mod, "apply_participation_module_backfill", clobber):
            code, _, err = self._run(["--apply", "--db", self._db()])
        self.assertEqual(code, 3)
        self.assertIn("current_class_id changed", err)


if __name__ == "__main__":
    unittest.main()

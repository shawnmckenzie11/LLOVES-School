#!/usr/bin/env python3
"""MCK-181: second-tab repaint and the #244 gate LOWs.

- MED: a teacher tab that hears the reopen while its menu is closed
  repaints Active/Closed at once, and Reopen board repaints too.
- LOW-1: a failed student board refetch after a Fresh reopen keeps the
  Fresh cue, not "Board refreshed for the new class."
- LOW-2: past 200 label saves, the solo snapshot is built from the
  student's own solo board, so labels still come back after reload.
- LOW-3: a non-whiteboard item is 400; a missing item in an ended class is
  404, before the class_ended reason.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest

import test_whiteboard_reopen_mck174 as base  # noqa: E402
from test_whiteboard_reopen_mck174 import COPY, LMS_DIR  # noqa: E402

class WhiteboardReopenFollowupTests(unittest.TestCase):
    """Same class setup as MCK-174 (borrowed methods, not the suite).

    Only methods are borrowed: a TestCase class in module globals would run
    the whole MCK-174 suite again here.
    """

    setUp = base.WhiteboardReopenTests.setUp
    tearDown = base.WhiteboardReopenTests.tearDown
    _whiteboard = base.WhiteboardReopenTests._whiteboard
    _join_two_groups = base.WhiteboardReopenTests._join_two_groups
    _wb_id = base.WhiteboardReopenTests._wb_id
    _wb_id_any = base.WhiteboardReopenTests._wb_id_any
    _student = base.WhiteboardReopenTests._student
    _close = base.WhiteboardReopenTests._close
    _reopen = base.WhiteboardReopenTests._reopen

    def _other_item_id(self) -> int:
        """A lifecycle row of this session that is not the whiteboard."""
        self.school.ensure_live_session_items(self.session_id)
        for row in self.school.list_live_session_items(self.session_id):
            if str(row.get("kind") or "") != "whiteboard":
                return int(row["id"])
        self.skipTest("this lesson seeds no non-whiteboard item")
        return 0

    def _url(self, item_id: int) -> str:
        return f"/api/live-sessions/{self.session_id}/items/{item_id}/reopen"

    # -- LOW-3 -------------------------------------------------------------

    def test_non_whiteboard_and_missing_items_on_active_and_ended(self) -> None:
        """400 for a non-whiteboard, 404 for a missing item, ended or not."""
        self._join_two_groups("group_shared")
        wb_id = self._wb_id()
        other_id = self._other_item_id()
        all_ids = {int(r["id"]) for r in self.school.list_live_session_items(self.session_id)}
        missing = max(all_ids) + 1000

        not_wb = self.client.post(self._url(other_id), json={"start": "last"})
        self.assertEqual(not_wb.status_code, 400, not_wb.get_json())
        self.assertNotIn("reason", not_wb.get_json())
        self.assertEqual(not_wb.get_json()["error"], "Only a whiteboard can be reopened.")
        gone = self.client.post(self._url(missing), json={"start": "last"})
        self.assertEqual(gone.status_code, 404, gone.get_json())
        active = self.client.post(self._url(wb_id), json={"start": "last"})
        self.assertEqual((active.status_code, active.get_json()["reason"]), (409, "already_open"))

        self._close()
        self.school.end_live_class_session(self.session_id)
        before = self.school.conn.execute(
            "SELECT COUNT(*) FROM live_session_items WHERE live_session_id = ?",
            (self.session_id,),
        ).fetchone()[0]
        ended_missing = self.client.post(self._url(missing), json={"start": "last"})
        self.assertEqual(ended_missing.status_code, 404, ended_missing.get_json())
        self.assertNotIn("reason", ended_missing.get_json())
        ended_other = self.client.post(self._url(other_id), json={"start": "fresh"})
        self.assertEqual(ended_other.status_code, 400, ended_other.get_json())
        self.assertNotIn("reason", ended_other.get_json())
        ended_wb = self.client.post(self._url(wb_id), json={"start": "last"})
        self.assertEqual(ended_wb.status_code, 409, ended_wb.get_json())
        self.assertEqual(ended_wb.get_json()["reason"], "class_ended")
        self.assertEqual(ended_wb.get_json()["error"], COPY["wb.reopen.error.ended"])
        # The ended lookups are read-only: no lifecycle rows were minted.
        after = self.school.conn.execute(
            "SELECT COUNT(*) FROM live_session_items WHERE live_session_id = ?",
            (self.session_id,),
        ).fetchone()[0]
        self.assertEqual(after, before)
        # The store answers the same way.
        with self.assertRaises(KeyError):
            self.school.reopen_live_whiteboard(self.session_id, missing)
        with self.assertRaises(ValueError) as caught:
            self.school.reopen_live_whiteboard(self.session_id, other_id)
        self.assertNotIsInstance(caught.exception, base.WhiteboardReopenConflict)

    # -- LOW-2 -------------------------------------------------------------

    def _labels(self, client, student_id: int) -> tuple[dict, dict[str, str]]:
        got = client.get(f"/api/student/board/solo:{student_id}?since=0")
        self.assertEqual(got.status_code, 200, got.get_json())
        reply = got.get_json()
        if reply.get("snapshot"):
            rows = (reply.get("canvas_view") or {}).get("texts") or []
        else:
            latest: dict[str, dict] = {}
            for op in reply.get("ops") or []:
                if op.get("type") == "text_upsert":
                    latest[str(op.get("id") or op.get("text_id"))] = op
            rows = list(latest.values())
        return reply, {str(r.get("id") or r.get("text_id")): str(r.get("text")) for r in rows}

    def _join_no_groups_individual(self) -> dict[str, str]:
        """Gate repro shape: students join, no groups, Individual publish.

        The session's ``canvas_align`` stays ``teacher`` here, which is
        what made the solo snapshot fold the teacher board (LOW-2).
        """
        students = [
            dict(row)
            for row in self.school.game.conn.execute(
                "SELECT id, codename FROM students WHERE class_id = ? ORDER BY id",
                (self.class_id,),
            ).fetchall()
        ]
        staged = self.client.post(
            f"/api/live-sessions/{self.session_id}/teacher-state",
            json={"class_set": True, "live_module": "M1", "live_slot": "C1", "stage": "round"},
        )
        self.assertEqual(staged.status_code, 200, staged.get_json())
        self.school.game.begin_game(self.class_id)
        tokens: dict[str, str] = {}
        for student in students:
            joined = self.school.join_live_class_session(
                self.session_id, int(student["id"]), codename=str(student["codename"])
            )
            tokens[str(student["codename"])] = str(joined["attendee"]["visit_token"])
        published = self.client.post(
            f"/api/live-sessions/{self.session_id}/items/{self._wb_id()}/publish",
            json={"publish_mode": "individual"},
        )
        self.assertEqual(published.status_code, 200, published.get_json())
        return tokens

    def test_labels_survive_reload_past_200_saves(self) -> None:
        """210 label saves: the solo snapshot still holds every label."""
        for shape in ("no_groups", "groups"):
            with self.subTest(shape=shape):
                if shape == "groups":
                    self.tearDown()
                    self.setUp()
                self._check_210_labels(shape)

    def _check_210_labels(self, shape: str) -> None:
        if shape == "no_groups":
            tokens = self._join_no_groups_individual()
            align = self.school.live_session_teacher_state_payload(self.session_id).get(
                "canvas_align"
            )
            self.assertEqual(align, "teacher", "the gate's shape")
        else:
            tokens = self._join_two_groups("individual")
        aspen = self._student(tokens["Aspen"])
        birch = self._student(tokens["Birch"])
        find = self.school.game.find_student_by_codename
        aspen_id = int(find(self.class_id, "Aspen")["id"])
        birch_id = int(find(self.class_id, "Birch")["id"])
        run_key = self.school.live_board_run_key(self.session_id)

        def save(client, text_id: str, text: str) -> None:
            res = client.post(
                "/api/student/canvas-presence",
                json={"run_key": run_key, "text_id": text_id, "text": text, "x": 0.3, "y": 0.4},
            )
            self.assertEqual(res.status_code, 200, res.get_json())

        save(birch, "tx-birch", "birch only")
        expected: dict[str, str] = {}
        for n in range(210):
            text_id = f"tx-{n % 7}"
            expected[text_id] = f"label {n % 7} v{n // 7}"
            save(aspen, text_id, expected[text_id])

        reply, labels = self._labels(aspen, aspen_id)
        self.assertTrue(reply["snapshot"], "210 saves is past MAX_DELTA_OPS")
        self.assertEqual(labels, expected)
        self.assertNotIn("birch only", labels.values())
        # Birch (few saves, op list) still reads only Birch's label.
        _reply, birch_labels = self._labels(birch, birch_id)
        self.assertEqual(birch_labels, {"tx-birch": "birch only"})
        # Close + Last keeps them; another student still cannot read them.
        self._close()
        self.assertEqual(self._reopen("last").status_code, 200)
        _reply, again = self._labels(aspen, aspen_id)
        self.assertEqual(again, expected)
        self.assertEqual(
            birch.get(f"/api/student/board/solo:{aspen_id}?since=0").status_code, 403
        )
        # The teacher board snapshot is unchanged: no student labels.
        teacher_view = self.school.live_session_canvas_view(self.session_id, as_teacher=True)
        self.assertFalse(
            [t for t in teacher_view.get("texts") or [] if str(t.get("owner")) != "teacher"]
        )

    # -- MED: second tab repaint (client wiring) ---------------------------

    def test_teacher_tab_repaints_when_poll_hears_status_change(self) -> None:
        """Every /state live_items adoption repaints on a status change, and
        Reopen board repaints instead of doing nothing."""
        staff_js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        helper = staff_js.split("function adoptLiveItemsSnapshot(items) {")[1].split("\n}\n")[0]
        self.assertIn("surfaceStatusSignature()", helper)
        self.assertIn("paintSurfacePublishing();", helper)
        # The helper is the only place a snapshot replaces lastLiveItems.
        self.assertEqual(staff_js.count("lastLiveItems = absorbSaveToCardSnapshot("), 1)
        self.assertGreaterEqual(staff_js.count("adoptLiveItemsSnapshot("), 5)
        poll = staff_js.split("async function pollLiveSessionAttendees(")[1].split(
            "\nasync function "
        )[0]
        self.assertIn("adoptLiveItemsSnapshot(payload.live_items);", poll)
        pop = staff_js.split("function openWhiteboardReopenPopover() {")[1].split("\n}\n")[0]
        not_closed = pop.split('if (String(item.status || "") !== "closed") {')[1].split("}")[0]
        self.assertIn("paintSurfacePublishing();", not_closed)

    # -- LOW-1: failed student refetch keeps the Fresh cue ------------------

    def test_failed_board_fetch_branch_uses_reopen_cue(self) -> None:
        student_js = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        refresh = student_js.split("function refreshStudentBoard()")[1].split(
            "function failedBoardFetchCue()"
        )[0]
        failed = refresh.split("if (!data || data.ended) {")[1].split("return;")[0]
        self.assertIn("failedBoardFetchCue()", failed)
        self.assertIn("studentSawFreshReopen = fresh;", student_js)

    @unittest.skipUnless(shutil.which("node"), "node is required for the cue check")
    def test_failed_board_fetch_cue_cases(self) -> None:
        """Run the real failedBoardFetchCue in node for each signal."""
        student_js = (LMS_DIR / "static" / "student-portal.js").read_text(encoding="utf-8")
        start = student_js.index("const WB_STUDENT_REOPEN_CUE = Object.freeze({")
        cue_end = student_js.index("});", start) + 3
        fn = student_js.index("function failedBoardFetchCue() {")
        fn_end = student_js.index("\n}\n", fn) + 3
        wb = (LMS_DIR / "static" / "live_whiteboard.js").resolve().as_uri()
        harness = (
            f'import {{ isFreshBoardRunKey }} from "{wb}";\n'
            + student_js[start:cue_end]
            + "\nlet studentReopenCue = \"\";\nlet studentSawFreshReopen = false;\n"
            + "const studentBoardRun = { key: \"\" };\n"
            + student_js[fn:fn_end]
            + """
const out = {};
studentBoardRun.key = "run-abc"; out.none = failedBoardFetchCue() ?? null;
studentBoardRun.key = "run-abc~g2"; out.fresh_key = failedBoardFetchCue() ?? null;
studentBoardRun.key = "run-abc"; studentSawFreshReopen = true; out.saw_fresh = failedBoardFetchCue() ?? null;
studentSawFreshReopen = false; studentReopenCue = WB_STUDENT_REOPEN_CUE.last; out.cue_last = failedBoardFetchCue() ?? null;
console.log(JSON.stringify(out));
"""
        )
        done = subprocess.run(
            [shutil.which("node"), "--input-type=module", "-e", harness],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertIsNone(out["none"])
        self.assertEqual(out["fresh_key"], COPY["wb.student.fresh"])
        self.assertEqual(out["saw_fresh"], COPY["wb.student.fresh"])
        self.assertEqual(out["cue_last"], COPY["wb.student.last"])


if __name__ == "__main__":
    unittest.main()

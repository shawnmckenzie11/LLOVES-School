#!/usr/bin/env python3
"""MCK-217: Student view hides student detail on teacher Questions cards."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
STAFF_JS = LMS_DIR / "static" / "staff_ap.js"


def _function_source(js: str, name: str) -> str:
    """Return the source of a top-level ``function name(...) {...}``."""
    start = js.index(f"\nfunction {name}(") + 1
    open_at = js.index("{", js.index(")", start))
    depth = 0
    for index in range(open_at, len(js)):
        if js[index] == "{":
            depth += 1
        elif js[index] == "}":
            depth -= 1
            if depth == 0:
                return js[start : index + 1]
    raise AssertionError(f"unbalanced {name}")


HARNESS = r"""
const vm = require("vm");
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
const ctx = {
  Math,
  Date,
  escapeHtml: (s) => String(s ?? ""),
  hideResponseNames: Boolean(input.hideResponseNames),
  studentViewOn: () => Boolean(input.studentView),
  MARK_COPY: {
    correct: "Correct",
    incorrect: "Incorrect",
    summary: "{k} of {n} groups correct",
    hideKey: "Hide key",
  },
  PICKS_COPY: {
    progress: "{k} of {n} picked · ",
    label: "Each student's pick",
    moveOn: "Start group step",
  },
  TURNS_COPY: {
    progress: "{k} of {n} placed",
    holder: " — {name}'s turn",
    waitingOn: " — waiting on {names}",
    sent: "Sent",
    skip: "Skip",
    skipLabel: "Skip {names}",
  },
  turnSkipNotes: new Map(),
  lifecycleResults: new Map(),
  quietMemberStripHtml: () => "",
  liveProjectingForQuietStrip: () => false,
  syncQuietStripSuppressed: () => {},
  rankRaceTeacherHtml: () => "",
  rankRaceEnterHtml: () => "",
  rankScoreReady: () => false,
  scoreTeamsButtonHtml: () => "",
  fullOrderHtml: () => "",
  rankTurnsTeacherHtml: () => "",
  groupResultsHostId: (result, id) => Number(id) || 1,
  liveGroupMcKeepsResponsePoints: () => false,
  liveQuestionHasSingularKey: () => false,
  groupAnswerLabel: (a) => String(a?.value ?? "—"),
  groupMemberAnswerValues: (team) => team.member_answers || [],
};
vm.createContext(ctx);
vm.runInContext(input.src, ctx);
const result = {
  group: vm.runInContext(
    "groupSubmitTeacherHtml(" + JSON.stringify(input.groupResult) + ", false, 9);",
    ctx
  ),
  board: vm.runInContext("groupStatusBoardHtml(" + JSON.stringify(input.groupResult) + ");", ctx),
};
console.log(JSON.stringify(result));
"""


GROUP_RESULT = {
    "answer_key": "A",
    "group_response_seq": "1",
    "status_board": [
        {
            "team_id": 1,
            "team_name": "Team Maple",
            "submitted": True,
            "correct": True,
            "picks": [
                {"name": "Eva", "value": "The vertex is (4, 5)", "correct": True},
                {"name": "Iggy", "value": "The y-intercept is 0", "correct": False},
            ],
            "pick_step": False,
            "member_count": 2,
        },
        {
            "team_id": 2,
            "team_name": "Team Cedar",
            "submitted": True,
            "correct": False,
            "picks": [{"name": "James", "value": "B", "correct": False}],
            "pick_step": False,
            "member_count": 1,
        },
    ],
    "submitter_log": [
        {"team_id": 1, "team_name": "Team Maple", "last_submitter": "Eva"},
        {"team_id": 2, "team_name": "Team Cedar", "last_submitter": "James"},
    ],
}


@unittest.skipUnless(shutil.which("node"), "node is required for the staff_ap.js harness")
class StudentViewQuestionsTests(unittest.TestCase):
    """Group Questions cards respect Student view masking."""

    def _render(self, *, student_view: bool, hide_names: bool = False) -> dict:
        js = STAFF_JS.read_text(encoding="utf-8")
        names = (
            "submitterDisplayName",
            "submitterAt",
            "softRelativeTime",
            "groupSubmitterLine",
            "questionsMaskStudentDetail",
            "maskedGroupLabel",
            "answerMarkHtml",
            "groupCorrectSummaryHtml",
            "groupPicksHtml",
            "submitterLogRow",
            "groupStatusBoardHtml",
            "groupSubmitTeacherHtml",
            "groupResultsHostId",
        )
        src = "\n".join(_function_source(js, name) for name in names)
        done = subprocess.run(
            ["node", "-e", HARNESS],
            input=json.dumps(
                {
                    "src": src,
                    "groupResult": GROUP_RESULT,
                    "studentView": student_view,
                    "hideResponseNames": hide_names,
                }
            ),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout.strip().splitlines()[-1])

    def test_wiring_helpers_present(self) -> None:
        js = STAFF_JS.read_text(encoding="utf-8")
        self.assertIn("function questionsMaskStudentDetail()", js)
        self.assertIn("function maskedGroupLabel(", js)
        self.assertIn("questionsMaskStudentDetail()", js.split("function groupPicksHtml")[1][:200])
        on_change = js.split("mountStudentView({")[1].split("});")[0]
        self.assertIn("paintLiveQuestionCards();", on_change)
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn("body.staff-shell.is-student-view .group-picks", css)

    def test_student_view_hides_names_picks_and_submitter_lines(self) -> None:
        out = self._render(student_view=True)
        html = out["group"] + out["board"]
        for name in ("Eva", "Iggy", "James", "Team Maple", "Team Cedar", "vertex", "y-intercept"):
            self.assertNotIn(name, html, html)
        self.assertIn("groups correct", html)
        self.assertIn("Group 1", html)
        self.assertIn("Group 2", html)
        self.assertNotIn("group-picks", html)
        self.assertNotIn("group-submitter-log", html)

    def test_show_strength_restores_detail(self) -> None:
        out = self._render(student_view=False)
        html = out["board"]
        self.assertIn("Eva", html)
        self.assertIn("vertex is (4, 5)", html)
        self.assertIn("group-picks", html)
        self.assertIn("last: Eva", html)


if __name__ == "__main__":
    unittest.main()

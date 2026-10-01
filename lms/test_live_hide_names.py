#!/usr/bin/env python3
"""MCK-29: Hide names switch on the teacher Responses list.

With the switch on, the list shows "Student N" / "Guest N" in list order
instead of names, so the shell is safe to screen-share. Answers, marks and
the +1 checkboxes stay. The choice is kept in localStorage.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
STAFF_JS = LMS_DIR / "static" / "staff_ap.js"
COURSE_HTML = LMS_DIR / "templates" / "staff" / "course.html"


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
const host = {
  innerHTML: "",
  hidden: null,
  classList: { toggle(name, on) { host.hidden = on; } },
};
const ctx = {
  host,
  Math,
  hideResponseNames: input.hidden,
  lastResponseRows: [],
  responseLabelBook: null,
  HIDE_LABELS_KEY: "k",
  localStorage: { setItem() {}, getItem() { return null; } },
  $: (id) => (id === "live-responses-list" ? host : null),
  escapeHtml: (s) => String(s),
  projectedClassListStudents: () => input.roster,
  classListRosterOrder: (students) => [{ name: "", students }],
};
vm.createContext(ctx);
vm.runInContext(input.src + "\npaintQuestionResponses(" + JSON.stringify(input.rows) + ");", ctx);
const names = [...host.innerHTML.matchAll(/live-response-name">([^<]*)</g)].map((m) => m[1]);
const answers = [...host.innerHTML.matchAll(/live-response-answer">([^<]*)</g)].map((m) => m[1]);
console.log(JSON.stringify({ names, answers, hiddenClass: host.hidden,
  boxes: (host.innerHTML.match(/data-response-student=/g) || []).length }));
"""

ROWS = [
    {"student_id": 2, "name": "Birch", "answer": "B", "correct": False},
    {"student_id": 1, "name": "Aspen", "answer": "A", "correct": True},
    {"student_id": None, "name": "Guesty", "answer": "C"},
]
ROSTER = [{"id": 1}, {"id": 2}]


@unittest.skipUnless(shutil.which("node"), "node is required for the staff_ap.js harness")
class HideNamesTests(unittest.TestCase):
    """The Responses list hides names only when the switch is on."""

    def _paint(self, hidden: bool) -> dict:
        js = STAFF_JS.read_text(encoding="utf-8")
        src = "\n".join(
            _function_source(js, name)
            for name in (
                "responseRowLabel",
                "hiddenLabelHash",
                "newHiddenLabelBook",
                "assignHiddenLabels",
                "saveHiddenLabelBook",
                "currentResponseTicks",
                "paintQuestionResponses",
            )
        )
        done = subprocess.run(
            ["node", "-e", HARNESS],
            input=json.dumps(
                {"src": src, "rows": ROWS, "roster": ROSTER, "hidden": hidden}
            ),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout.strip().splitlines()[-1])

    def test_names_show_by_default(self) -> None:
        """Switch off: names in roster order, as before."""
        out = self._paint(False)
        self.assertEqual(out["names"], ["Aspen", "Birch", "Guesty"])
        self.assertFalse(out["hiddenClass"])

    def test_hide_names_numbers_rows_and_keeps_answers(self) -> None:
        """Switch on: no names in the markup; answers and checkboxes stay."""
        out = self._paint(True)
        self.assertEqual(out["names"], ["Student 1", "Student 2", "Guest 1"])
        # MCK-111: rows sort by a shuffled, stable label, not A-Z.
        self.assertEqual(sorted(out["answers"][:2]), ["A", "B"])
        self.assertEqual(out["answers"][2], "C")
        self.assertEqual(out["boxes"], 2)
        self.assertTrue(out["hiddenClass"])

    def test_switch_is_in_the_dialog_and_remembered(self) -> None:
        """The dialog has the switch; the JS stores the choice."""
        html = COURSE_HTML.read_text(encoding="utf-8")
        dialog = html[html.index('id="live-responses-dialog"') :]
        dialog = dialog[: dialog.index("</dialog>")]
        self.assertIn('id="live-hide-names"', dialog)
        self.assertIn("Hide names", dialog)
        js = STAFF_JS.read_text(encoding="utf-8")
        self.assertIn('localStorage.getItem(HIDE_NAMES_KEY) === "1"', js)
        self.assertIn("localStorage.setItem(HIDE_NAMES_KEY", js)
        self.assertIn("setHideResponseNames(toggle.checked, { store: true })", js)


if __name__ == "__main__":
    unittest.main()

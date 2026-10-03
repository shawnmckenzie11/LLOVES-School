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
  HIDE_KEY_STORE: "lloves.live.hideKey",
  window: {
    localStorage: {
      setItem() {},
      getItem(k) { return input.keyHidden && k === "lloves.live.hideKey" ? "1" : null; },
    },
  },
  $: (id) => (id === "live-responses-list" ? host : null),
  escapeHtml: (s) => String(s),
  projectedClassListStudents: () => input.roster,
  classListRosterOrder: (students) => [{ name: "", students }],
};
vm.createContext(ctx);
vm.runInContext(input.src + "\npaintQuestionResponses(" + JSON.stringify(input.rows) + ");", ctx);
const names = [...host.innerHTML.matchAll(/live-response-name">([^<]*)</g)].map((m) => m[1]);
const answers = [...host.innerHTML.matchAll(/live-response-answer">([^<]*)</g)].map((m) => m[1]);
const points = [...host.innerHTML.matchAll(/live-response-points">\s*([^<]*?)\s*</g)].map((m) => m[1]);
const keyTicks = (host.innerHTML.match(/is-key-tick/g) || []).length;
const checked = (host.innerHTML.match(/ checked/g) || []).length;
const marks = [...host.innerHTML.matchAll(/live-response-mark">\s*([^<]*?)\s*</g)].map((m) => m[1]);
console.log(JSON.stringify({ names, answers, marks, points, keyTicks, checked, hiddenClass: host.hidden,
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

    def _paint(self, hidden: bool, key_hidden: bool = False, rows: list | None = None) -> dict:
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
                "hideKeyOn",
                "paintQuestionResponses",
            )
        )
        done = subprocess.run(
            ["node", "-e", HARNESS],
            input=json.dumps(
                {"src": src, "rows": rows or ROWS, "roster": ROSTER, "hidden": hidden, "keyHidden": key_hidden}
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

    def test_marks_follow_hide_key(self) -> None:
        """MCK-155 gate MED-1: Hide key on, every row reads Answered."""
        shown = self._paint(False)
        self.assertEqual(shown["marks"], ["Correct", "Incorrect", "Guest"])
        hidden = self._paint(False, key_hidden=True)
        self.assertEqual(hidden["marks"], ["Answered", "Answered", "Guest"])
        both = self._paint(True, key_hidden=True)
        self.assertNotIn("Correct", both["marks"])
        self.assertNotIn("Incorrect", both["marks"])

    def test_awarded_ticks_and_points_follow_hide_key(self) -> None:
        """MCK-155 gate N-1: +1 given before Hide key does not name the key."""
        awarded = [dict(ROWS[0]), dict(ROWS[1], awarded_points=1), dict(ROWS[2])]
        shown = self._paint(False, rows=awarded)
        self.assertEqual(shown["points"], ["+1", ""])
        self.assertEqual((shown["checked"], shown["keyTicks"]), (1, 0))
        hidden = self._paint(False, key_hidden=True, rows=awarded)
        self.assertEqual(hidden["points"], ["", ""])
        # The tick state is kept (still checked) but rendered as a hidden tick.
        self.assertEqual((hidden["checked"], hidden["keyTicks"]), (1, 2))
        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn("#live-responses-dialog.is-key-hidden input.is-key-tick", css)

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

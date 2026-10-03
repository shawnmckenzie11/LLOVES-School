#!/usr/bin/env python3
"""MCK-111: Hide names follow-ups from the #201 Ops gate.

* Each student keeps one label for the run, so labels don't reshuffle on
  reopen or after a late join (M2).
* Labels follow a seeded shuffle and rows sort by label, so "Student k"
  can't be read off the A-Z Class List (M1). While the dialog is open
  with names hidden, the Class List is blurred and the backdrop darker.
* Toggling the switch keeps unsaved ticks (M3).
* The responses JSON leaves names out when hidden (L3). Other tabs follow
  the switch (L1). Focus starts on the close button (L2).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from typing import Any

import test_live_shell as shell
from test_live_hide_names import COURSE_HTML, STAFF_JS, _function_source

HELPERS = (
    "responseRowLabel",
    "hiddenLabelHash",
    "newHiddenLabelBook",
    "assignHiddenLabels",
    "saveHiddenLabelBook",
    "currentResponseTicks",
    "hideKeyOn",
    "paintQuestionResponses",
)

HARNESS = r"""
const vm = require("vm");
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
const store = {};
const host = {
  innerHTML: "",
  classList: { toggle() {} },
  querySelectorAll() {
    return [...host.innerHTML.matchAll(/data-response-student="(\d+)"( checked)?/g)].map(
      (m) => ({
        getAttribute: () => m[1],
        checked: input.ticks && m[1] in input.ticks ? input.ticks[m[1]] : Boolean(m[2]),
      })
    );
  },
};
const ctx = {
  host,
  Math,
  hideResponseNames: true,
  lastResponseRows: [],
  responseLabelBook: input.book,
  HIDE_LABELS_KEY: "k",
  localStorage: { setItem(k, v) { store[k] = v; }, getItem(k) { return store[k] ?? null; } },
  $: (id) => (id === "live-responses-list" ? host : null),
  escapeHtml: (s) => String(s),
  projectedClassListStudents: () => input.roster,
  classListRosterOrder: (students) => [{ name: "", students }],
};
vm.createContext(ctx);
vm.runInContext(input.src, ctx);
const out = [];
for (const paint of input.paints) {
  vm.runInContext(
    "paintQuestionResponses(" + JSON.stringify(paint.rows) + ", " + JSON.stringify(Boolean(paint.keep)) + ");",
    ctx
  );
  const rows = [...host.innerHTML.matchAll(
    /data-response-student="(\d+)"( checked)?>\s*<span class="live-response-name">([^<]*)</g
  )].map((m) => ({ id: Number(m[1]), checked: Boolean(m[2]), label: m[3] }));
  out.push({ rows, book: JSON.parse(JSON.stringify(ctx.responseLabelBook)) });
}
console.log(JSON.stringify({ out, stored: store.k ? JSON.parse(store.k) : null }));
"""


def _rows(ids: list[int]) -> list[dict[str, Any]]:
    """Response rows; names sort A-Z with id."""
    return [
        {"student_id": i, "name": f"Name{i:02d}", "answer": f"a{i}", "awarded_points": 0}
        for i in ids
    ]


@unittest.skipUnless(shutil.which("node"), "node is required for the staff_ap.js harness")
class StableLabelTests(unittest.TestCase):
    """Labels are fixed per student for the run and not A-Z."""

    def _run(self, paints: list[dict[str, Any]], roster: list[int], **extra: Any) -> dict:
        js = STAFF_JS.read_text(encoding="utf-8")
        src = "\n".join(_function_source(js, name) for name in HELPERS)
        payload = {
            "src": src,
            "roster": [{"id": i} for i in roster],
            "paints": paints,
            "book": {"run": "run-a", "seed": "seed-1", "next": 1, "byStudent": {}},
            **extra,
        }
        done = subprocess.run(
            ["node", "-e", HARNESS],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout.strip().splitlines()[-1])

    def test_labels_do_not_move_after_a_late_answer(self) -> None:
        """Ops M2 repro: an earlier-A-Z student answers late; nobody renumbers."""
        roster = list(range(1, 21))
        first = [i for i in roster if i != 1]
        got = self._run([{"rows": _rows(first)}, {"rows": _rows(roster)}], roster)
        before = {r["id"]: r["label"] for r in got["out"][0]["rows"]}
        after = {r["id"]: r["label"] for r in got["out"][1]["rows"]}
        for sid, label in before.items():
            self.assertEqual(after[sid], label, f"student {sid} moved")
        self.assertEqual(after[1], "Student 20", "the late joiner takes the next number")
        self.assertEqual(got["stored"]["byStudent"], got["out"][1]["book"]["byStudent"])

    def test_labels_are_not_class_list_order_and_rows_sort_by_label(self) -> None:
        """Ops M1: Student k must not be the k-th name in the Class List."""
        roster = list(range(1, 21))
        got = self._run([{"rows": _rows(roster)}], roster)
        rows = got["out"][0]["rows"]
        labels = [r["label"] for r in rows]
        self.assertEqual(labels, [f"Student {n}" for n in range(1, 21)])
        self.assertNotEqual([r["id"] for r in rows], roster, "rows still in A-Z order")
        self.assertEqual(sorted(r["id"] for r in rows), roster)

    def test_class_list_neighbours_get_near_random_labels(self) -> None:
        """Ops MED-1: adjacent ids got adjacent labels 58% of the time.

        Over many seeds, the share of Class List neighbours (ids i, i+1)
        whose labels differ by 1 must be close to a true shuffle (2/n).
        """
        js = STAFF_JS.read_text(encoding="utf-8")
        src = "\n".join(
            _function_source(js, name) for name in ("hiddenLabelHash", "assignHiddenLabels")
        )
        script = r"""
const vm = require("vm");
const src = require("fs").readFileSync(0, "utf8");
const ctx = { Math, Number, Set, String };
vm.createContext(ctx);
vm.runInContext(src, ctx);
const n = 24, seeds = 400;
let adjacent = 0, pairs = 0, longest = 0;
for (let s = 0; s < seeds; s += 1) {
  const book = { seed: String(1000003 * s + 17), next: 1, byStudent: {} };
  const ids = Array.from({ length: n }, (_, i) => 101 + i);
  ctx.book = book; ctx.ids = ids;
  vm.runInContext("assignHiddenLabels(book, ids)", ctx);
  let run = 1;
  for (let i = 0; i + 1 < n; i += 1) {
    const d = book.byStudent[String(ids[i + 1])] - book.byStudent[String(ids[i])];
    pairs += 1;
    if (Math.abs(d) === 1) { adjacent += 1; run += 1; longest = Math.max(longest, run); }
    else run = 1;
  }
}
console.log(JSON.stringify({ share: adjacent / pairs, longest }));
"""
        done = subprocess.run(
            ["node", "-e", script], input=src, capture_output=True, text=True,
            timeout=60, check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout.strip().splitlines()[-1])
        # True shuffle: 2/24 = 8.3%. Plain FNV-1a measured about 58%.
        self.assertLess(out["share"], 0.13, out)
        self.assertGreater(out["share"], 0.04, out)

    def test_toggle_repaint_keeps_unsaved_ticks(self) -> None:
        """Ops M3: ticks (or un-ticks) made before the toggle survive it."""
        roster = [1, 2, 3, 4]
        rows = _rows(roster)
        rows[2]["awarded_points"] = 1
        got = self._run(
            [{"rows": rows}, {"rows": rows, "keep": True}],
            roster,
            ticks={"1": True, "4": True, "3": False},
        )
        checked = {r["id"]: r["checked"] for r in got["out"][1]["rows"]}
        self.assertEqual(checked, {1: True, 2: False, 3: False, 4: True})

    def test_open_paints_ticks_from_awarded_points(self) -> None:
        """A fresh open still starts from what was awarded."""
        roster = [1, 2]
        rows = _rows(roster)
        rows[1]["awarded_points"] = 1
        got = self._run([{"rows": rows}], roster, ticks={"1": True})
        checked = {r["id"]: r["checked"] for r in got["out"][0]["rows"]}
        self.assertEqual(checked, {1: False, 2: True})

    def test_markup_and_switch_wiring(self) -> None:
        """Blur, darker backdrop, cross-tab sync, autofocus, bigger target."""
        html = COURSE_HTML.read_text(encoding="utf-8")
        dialog = html[html.index('id="live-responses-dialog"') :]
        dialog = dialog[: dialog.index("</dialog>")]
        self.assertIn('aria-label="Close responses" autofocus', dialog)
        css = (STAFF_JS.parent / "staff-shell.css").read_text(encoding="utf-8")
        self.assertIn("body.staff-shell.is-response-names-hidden #ap-att-list", css)
        self.assertIn("#live-responses-dialog::backdrop", css)
        self.assertIn("min-height: 2.25rem", css)
        js = STAFF_JS.read_text(encoding="utf-8")
        self.assertIn('window.addEventListener("storage"', js)
        self.assertIn("syncHiddenNamesBackdrop()", js)
        self.assertIn("hide_names: hideResponseNames", js)
        self.assertIn("paintQuestionResponses(result.responses, true)", js)


class HideNamesJsonTests(shell.LiveShellTests):
    """Reuse the live-shell fixture. Only the tests below run here."""

    def _prompt_with_aspen(self) -> tuple[int, int, int]:
        """A live mc prompt Aspen answered. Returns ``(sid, prompt_id, aspen)``."""
        sid, aspen = self._open_live_with_aspen_answers()
        prompt = self.school.set_live_session_prompt(
            sid,
            slide_index=902,
            kind="mc",
            payload={"question": "Pick one", "choices": ["A", "B"], "correct_answer": "A"},
            activate=False,
        )
        self.school.submit_live_prompt_response(int(prompt["id"]), aspen, {"choice": "A"})
        return sid, int(prompt["id"]), aspen

    def _open_game(self, student_id: int) -> None:
        """Open a live game with this student present (Award needs one)."""
        game = self.school.game
        with game._lock:
            roster = [
                int(row["id"])
                for row in game.conn.execute(
                    "SELECT id FROM students WHERE class_id = ? ORDER BY id",
                    (self.class_id,),
                )
            ]
        other = next(x for x in roster if x != int(student_id))
        game.begin_game(self.class_id)
        game.save_attendance(self.class_id, [student_id, other])
        teams = game.assign_teams(
            self.class_id,
            2,
            "manual",
            assignments=[
                {"student_id": student_id, "team_index": 0},
                {"student_id": other, "team_index": 1},
            ],
        )
        game.rename_teams(
            self.class_id,
            [{"id": team["id"], "name": team["name"]} for team in teams["teams"]],
        )

    def test_hidden_reply_has_no_names_and_carries_the_run(self) -> None:
        """``?hide_names=1`` blanks names; the plain reply is unchanged."""
        sid, prompt_id, aspen = self._prompt_with_aspen()
        url = f"/api/live-sessions/{sid}/questions/{prompt_id}/responses"
        plain = self.client.get(url).get_json()
        self.assertTrue(any(r.get("name") == "Aspen" for r in plain["responses"]), plain)
        hidden = self.client.get(url, query_string={"hide_names": "1"})
        self.assertEqual(hidden.status_code, 200)
        body = hidden.get_json()
        self.assertNotIn("Aspen", hidden.get_data(as_text=True))
        self.assertEqual(
            body["run_key"], self.school.get_live_session(sid)["run_key"]
        )
        ids = [r["student_id"] for r in body["responses"]]
        self.assertIn(aspen, ids)

    def test_hidden_award_reply_has_no_response_names(self) -> None:
        """Award with ``hide_names`` credits the id and leaves names out of rows."""
        sid, prompt_id, aspen = self._prompt_with_aspen()
        self._open_game(aspen)
        url = f"/api/live-sessions/{sid}/questions/{prompt_id}/responses"
        resp = self.client.post(
            url,
            json={
                "mode": "manual",
                "student_ids": [aspen],
                "amount": 1,
                "replace": True,
                "hide_names": True,
            },
        )
        self.assertEqual(resp.status_code, 200, resp.get_json())
        rows = resp.get_json().get("responses") or []
        mine = [r for r in rows if r.get("student_id") == aspen]
        self.assertTrue(mine, rows)
        self.assertEqual(mine[0]["name"], "")
        self.assertTrue(mine[0].get("awarded_points"))


def load_tests(
    loader: unittest.TestLoader, _tests: Any, _pattern: Any
) -> unittest.TestSuite:
    """Run the label tests and only this file's HTTP tests."""
    suite = unittest.TestSuite(loader.loadTestsFromTestCase(StableLabelTests))
    names = [
        name
        for name, value in vars(HideNamesJsonTests).items()
        if name.startswith("test_") and callable(value)
    ]
    suite.addTests(HideNamesJsonTests(name) for name in sorted(names))
    return suite


if __name__ == "__main__":
    unittest.main()

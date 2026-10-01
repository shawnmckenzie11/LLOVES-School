"""Teacher "answered N / M" counter repaints on light /state ticks (MCK-84).

Light polls only carry ``lifecycle_response_counts``. Individual question
cards must patch ``.live-question-progress`` in place, the way group cards do
through ``paintGroupResultsInPlace``, instead of waiting for a full tick.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
STAFF_JS = LMS_DIR / "static" / "staff_ap.js"


def _function_source(js: str, name: str) -> str:
    """Return the full source of a top-level ``function name(...) {...}``.

    Args:
        js: Whole script text.
        name: Function name to extract.
    """
    start = js.index(f"\nfunction {name}(") + 1
    open_at = js.index("{", js.index(")", start))
    depth = 0
    for index in range(open_at, len(js)):
        char = js[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return js[start : index + 1]
    raise AssertionError(f"unbalanced {name}")


HARNESS = r"""
const vm = require("vm");
const src = require("fs").readFileSync(0, "utf8");
class Node {
  constructor(attrs) { this.attrs = attrs || {}; this.children = []; this.textContent = ""; this.className = ""; }
  getAttribute(k) { return this.attrs[k] ?? null; }
  querySelector(sel) {
    for (const child of this.children) {
      if (child.matches(sel)) return child;
      const deep = child.querySelector(sel);
      if (deep) return deep;
    }
    return null;
  }
  matches(sel) {
    const m = sel.match(/^\.([\w-]+)(?:\[data-live-item-id="(\d+)"\])?$/);
    if (!m) return false;
    if (!this.className.split(" ").includes(m[1])) return false;
    return m[2] == null || this.attrs["data-live-item-id"] === m[2];
  }
}
function el(cls, attrs, text) { const n = new Node(attrs); n.className = cls; n.textContent = text || ""; return n; }
const host = el("live-question-list");
const card = el("live-question-card", { "data-live-item-id": "7" });
const progress = el("live-question-progress", {}, "3 / 12 answered");
card.children.push(progress);
host.children.push(card);
let refreshed = 0;
const ctx = {
  $: (id) => (id === "live-question-list" ? host : null),
  lifecycleResults: new Map([[7, { response_count: 3, eligible_count: 12, tally: { responded: 3, present: 12 } }]]),
  lastLiveItems: [{ id: 7, response_mode: "individual", item: { type: "numeric" } }],
  refreshLifecycleResults: async () => { refreshed += 1; },
};
vm.createContext(ctx);
vm.runInContext(src, ctx);
ctx.adoptLifecycleResponseCounts({ "7": 4 }, {});
const afterOne = progress.textContent;
ctx.adoptLifecycleResponseCounts({ "7": 5 }, {});
const afterTwo = progress.textContent;
console.log(JSON.stringify({ afterOne, afterTwo, refreshed }));
"""


@unittest.skipUnless(shutil.which("node"), "node is required for the staff_ap.js harness")
class AnsweredCounterRepaintTests(unittest.TestCase):
    """Light count ticks patch the individual counter in place."""

    def test_light_tick_repaints_individual_answered_counter(self) -> None:
        """3 / 12 becomes 4 / 12 then 5 / 12 without a full card repaint."""
        js = STAFF_JS.read_text(encoding="utf-8")
        src = "\n".join(
            _function_source(js, name)
            for name in (
                "lifecycleRowIsRank",
                "adoptLifecycleResponseCounts",
                "paintLifecycleProgressInPlace",
            )
        )
        result = subprocess.run(
            ["node", "-e", HARNESS],
            input=src,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        out = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(out["afterOne"], "4 / 12 answered")
        self.assertEqual(out["afterTwo"], "5 / 12 answered")
        # Individual light ticks stay light: no per-item results refetch.
        self.assertEqual(out["refreshed"], 0)


if __name__ == "__main__":
    unittest.main()

"""Publish paints the new question before other items' /results load."""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node is required")
class PublishPaintTests(unittest.TestCase):
    """A publish body that omits active_questions still paints the new item."""

    def test_publish_without_active_questions_keeps_the_item(self) -> None:
        """The published item is upserted when the response has no active list."""
        staff = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        start = staff.index("function activeQuestionsAfterPublish(")
        end = staff.index("async function publishLifecycleItem(")
        src = staff[start:end]
        script = r"""
const vm = require("vm");
const ctx = {};
vm.createContext(ctx);
vm.runInContext(require("fs").readFileSync(0, "utf8") + "\nthis.next = activeQuestionsAfterPublish;", ctx);
const previous = [{ id: 4, item_id: "old", payload: { item_id: "old" } }];
const replaced = ctx.next(previous, { active_questions: [{ id: 9, payload: { item_id: "mc" } }], item: { id: 8 } });
if (replaced.length !== 1 || replaced[0].id !== 9) throw new Error("explicit list " + JSON.stringify(replaced));
const kept = ctx.next(previous, { item: { id: 8, item_id: "mc", status: "active" } });
if (!kept.some((row) => row.id === 8 && row.payload.item_id === "mc")) throw new Error("missing item " + JSON.stringify(kept));
if (!kept.some((row) => row.id === 4)) throw new Error("dropped other row");
const same = ctx.next(kept, { item: { id: 8, item_id: "mc", status: "active" } });
if (same.filter((row) => row.id === 8).length !== 1) throw new Error("duplicated " + JSON.stringify(same));
console.log("ok");
"""
        completed = subprocess.run(
            [NODE, "-e", script],
            input=src,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertIn("ok", completed.stdout)
        publish = staff.split("async function publishLifecycleItem(")[1].split(
            "async function closeLifecycleItem("
        )[0]
        self.assertLess(
            publish.index("paintLiveQuestionCards()"),
            publish.index("await refreshLifecycleResults()"),
        )
        self.assertNotIn("staff_reconnect_log", publish)
        self.assertNotIn("staffReconnectNextCause", publish)


if __name__ == "__main__":
    unittest.main()

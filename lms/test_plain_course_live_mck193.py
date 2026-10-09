#!/usr/bin/env python3
"""MCK-193: a plain course (no pack, no decks) can start Run Live Class.

Repro: a teacher on SBI4U (non-math, no module pack) opens Run Live Class.
Set Class lands on M1 C1 with no deck, no previous challenge and no other
course decks. Every chip is greyed, the fallback pick is Previous, and
confirming POSTed ``{"mode": "previous"}``. The server answered
"No previous challenge in this module." and Set Class stayed stuck, so
the teacher could not move forward.

Fix: when Previous cannot run for the loaded slot, Set Class sends
``{"mode": "blank", "keep_existing": true}``. The helper line says so
calmly. Math courses with a deck (current, previous or course) send
exactly what they sent before.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
REPO_ROOT = LMS_DIR.parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(REPO_ROOT))

os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.setdefault("ALLOW_DEV_VERIFICATION_CODE", "1")

from app import create_app  # noqa: E402
from test_deck_seed_sticky_mode import NODE, STAFF_AP_HARNESS  # noqa: E402

# The MCK-77 harness with an empty course deck list and the real server
# message on M1 C1, then the plain-course confirm.
PLAIN_HARNESS = (
    STAFF_AP_HARNESS.split("const results = {};")[0]
    .replace(
        "  } else if (/\\/deck-seed$/.test(target)) {",
        "    if (globalThis.__plainCourse) data.decks = [];\n"
        "  } else if (/\\/deck-seed$/.test(target)) {",
        1,
    )
    .replace(
        "message: 'No earlier challenge in this module.'",
        "message: 'No previous challenge in this module.'",
        1,
    )
    + r"""
const results = {};
globalThis.__plainCourse = true;
mod.enterSetClassPhase();
await settle();
results.note = els['live-deck-seed-previous-note'].textContent;
posts.length = 0;
try {
  await mod.applyDeckSeedChoice();
  results.error = '';
} catch (err) {
  results.error = String(err && err.message || err);
}
results.posts = posts.map((p) => p.body);
console.log(JSON.stringify(results));
"""
)

HELPER_CASES = r"""
import { deckSeedConfirmMode } from "./static/deck_seed_help.js";
const plain = { module: "M1", slot: "C1", current: { available: false }, previous: { available: false }, decks: [] };
const withDeck = { module: "M1", slot: "C1", current: { available: true }, previous: { available: false }, decks: [] };
const withPrev = { module: "M1", slot: "C4", current: { available: false }, previous: { available: true }, decks: [] };
const withCourse = { module: "M1", slot: "C1", current: { available: false }, previous: { available: false }, decks: [{}] };
const out = {
  plainAuto: deckSeedConfirmMode({ selected: "previous", chosen: false, catalog: plain, pack: { module: "M1", slot: "C1" } }),
  plainChosenPrevious: deckSeedConfirmMode({ selected: "previous", chosen: true, catalog: plain, pack: { module: "M1", slot: "C1" } }),
  plainStale: deckSeedConfirmMode({ selected: "previous", chosen: false, catalog: plain, pack: { module: "M1", slot: "C2" } }),
  mathCurrent: deckSeedConfirmMode({ selected: "current", chosen: false, catalog: withDeck, pack: { module: "M1", slot: "C1" } }),
  mathPrevious: deckSeedConfirmMode({ selected: "previous", chosen: false, catalog: withPrev, pack: { module: "M1", slot: "C4" } }),
  mathCourse: deckSeedConfirmMode({ selected: "course", chosen: false, catalog: withCourse, pack: { module: "M1", slot: "C1" } }),
};
console.log(JSON.stringify(out));
"""


def _run_node(args: list[str], script_text: str | None = None) -> dict:
    """Run node and return the JSON on its last stdout line.

    Args:
        args: Extra node arguments (``-e`` source or a file path).
        script_text: When set, written to a temp ``.mjs`` file and run.
    """

    with tempfile.TemporaryDirectory() as tmp:
        env = os.environ.copy()
        cmd = [NODE, *args]
        if script_text is not None:
            script = Path(tmp) / "harness.mjs"
            script.write_text(script_text, encoding="utf-8")
            env["LLOVES_LMS_STATIC"] = str(LMS_DIR / "static")
            env["LLOVES_COMMON_JS"] = str(
                REPO_ROOT / "tools" / "math-game-show" / "static" / "common.js"
            )
            env["LLOVES_HARNESS_OUT"] = str(Path(tmp) / "staff_ap_under_test.mjs")
            cmd = [NODE, str(script)]
        proc = subprocess.run(
            cmd,
            cwd=str(LMS_DIR),
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
            check=False,
        )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return json.loads(proc.stdout.strip().splitlines()[-1])


@unittest.skipUnless(NODE, "node is required for the staff_ap.js harness")
class PlainCourseSetClassTests(unittest.TestCase):
    """Drive the real staff_ap.js Set Class confirm for a plain course."""

    def test_plain_course_confirm_starts_blank_without_error(self) -> None:
        """No 'No previous challenge' error; a blank keep-existing POST."""

        got = _run_node([], PLAIN_HARNESS)
        self.assertEqual(got["error"], "")
        self.assertNotIn("previous challenge", got["note"].lower())
        self.assertIn("blank deck", got["note"])
        self.assertEqual(got["posts"], [{"mode": "blank", "keep_existing": True}])

    def test_confirm_mode_only_changes_when_previous_cannot_run(self) -> None:
        """Math slots with a deck, a previous deck or course decks are unchanged."""

        got = _run_node(["--input-type=module", "-e", HELPER_CASES])
        self.assertEqual(
            got,
            {
                "plainAuto": "blank",
                "plainChosenPrevious": "blank",
                "plainStale": "current",
                "mathCurrent": "current",
                "mathPrevious": "previous",
                "mathCourse": "course",
            },
        )


class PlainCourseServerTests(unittest.TestCase):
    """SBI4U with no pack: options, the old error, then a blank start."""

    def setUp(self) -> None:
        """Teacher-owned SBI4U class signed in through the staff portal."""

        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite", data_dir=root, testing=True
        )
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(teacher["id"]), ontario_code="SBI4U"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.client.post(
            "/verify-email",
            data={
                "code": self.school.get_user_by_email("teacher@gmail.com")[
                    "verification_code"
                ]
            },
        )
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple"],
            },
        )
        self.assertEqual(created.status_code, 200, created.get_json())
        self.class_id = int(created.get_json()["class"]["id"])

    def tearDown(self) -> None:
        """Close sqlite and remove the temp dir."""

        self.school.close()
        self.tmp.cleanup()

    def test_plain_course_starts_and_advances(self) -> None:
        """Previous is refused (the old error); blank seeds, begins and steps."""

        base = f"/api/staff/class/{self.class_id}/live-lessons/M1/C1"
        options = self.client.get(f"{base}/deck-seed-options").get_json()
        self.assertFalse(options["current"]["available"])
        self.assertFalse(options["previous"]["available"])
        self.assertEqual(options["decks"], [])
        old = self.client.post(
            f"{base}/deck-seed", json={"mode": "previous", "keep_existing": True}
        )
        self.assertEqual(old.status_code, 400)
        self.assertIn("No previous challenge", old.get_json()["error"])

        begun = self.client.post(f"/api/classes/{self.class_id}/begin", json={})
        self.assertEqual(begun.status_code, 200, begun.get_json())
        live = self.client.post(
            f"/api/classes/{self.class_id}/live-session/start",
            json={"live_module": "M1", "live_slot": "C1"},
        )
        self.assertEqual(live.status_code, 200, live.get_json())
        seeded = self.client.post(
            f"{base}/deck-seed", json={"mode": "blank", "keep_existing": True}
        )
        self.assertEqual(seeded.status_code, 200, seeded.get_json())
        self.assertEqual(seeded.get_json()["mode"], "blank")
        lesson = self.client.get(f"/api/classes/{self.class_id}/live-lessons/M1/C1")
        self.assertEqual(lesson.status_code, 200, lesson.get_json())
        stepped = self.client.post(
            f"/api/classes/{self.class_id}/game/step", json={"status": "teams"}
        )
        self.assertEqual(stepped.status_code, 200, stepped.get_json())
        session_id = live.get_json()["live_session_id"]
        state = self.client.get(f"/api/live-sessions/{session_id}/state")
        self.assertEqual(state.status_code, 200)
        # A poll and a quick question still post on a blank deck.
        for kind, payload in (
            ("mc", {"question": "Ready?", "choices": ["Yes", "No"]}),
            ("share", {"prompt": "One word for today"}),
        ):
            prompt = self.client.post(
                f"/api/live-sessions/{session_id}/prompts",
                json={"slide_index": 1, "kind": kind, "payload": payload},
            )
            self.assertEqual(prompt.status_code, 200, prompt.get_json())
        grid = self.client.get(f"/api/classes/{self.class_id}/participation-grid")
        self.assertEqual(grid.status_code, 200)


if __name__ == "__main__":
    unittest.main()

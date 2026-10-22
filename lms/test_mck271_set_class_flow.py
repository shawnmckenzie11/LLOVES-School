#!/usr/bin/env python3
"""MCK-271: Set Class if/then UI still posts the same start fields."""

from __future__ import annotations

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

TEST_DECK_LINE = "Test classes use a copy of this deck, so your real deck won't change."
SUMMARY_EXAMPLE = "Starting test class: Module 2, Class 1, current deck."


class SetClassStartFieldTests(unittest.TestCase):
    """Each start choice still maps onto the requests Set Class already sends."""

    def test_modes_keep_the_existing_post_bodies(self) -> None:
        """Live, test, and office keep kind, is_test, begin, and deck-seed fields."""
        script = r"""
import { setClassStartFields, setClassSummaryLine } from "./static/set_class_mode.js";

const live = setClassStartFields("live");
const test = setClassStartFields("test");
const office = setClassStartFields("office");
const other = setClassStartFields("nope");
const line = setClassSummaryLine({ mode: "test", module: "M2", slot: "C1", deck: "current" });
const previous = setClassSummaryLine({ mode: "live", module: "M3", slot: "C3", deck: "previous" });
const course = setClassSummaryLine({ mode: "live", module: "M1", slot: "C2", deck: "course" });
const openOffice = setClassSummaryLine({ mode: "office", module: "M3", slot: "O1", deck: "current" });
const named = setClassSummaryLine({ mode: "live", module: "M1", slot: "C2", deck: "current", slides: "Google Slides" });
const officeNamed = setClassSummaryLine({ mode: "office", module: "M3", slot: "O1", slides: "Google Slides" });

const failed = [];
function check(cond, message) {
  if (!cond) failed.push(message);
}
check(live.kind === "live" && live.run === "live" && live.is_test === false && live.deck === "chip", "live");
check(test.kind === "live" && test.run === "test" && test.is_test === true && test.deck === "test_copy", "test");
check(office.kind === "office" && office.run === "live" && office.is_test === false && office.deck === "chip", "office");
check(other.kind === "live" && other.run === "live" && other.is_test === false, "unknown stays live");
check(line === "Starting test class: Module 2, Class 1, current deck.", line);
check(previous === "Starting live class: Module 3, Class 3, previous deck.", previous);
check(course === "Starting live class: Module 1, Class 2, course deck.", course);
check(openOffice === "Starting open office: Module 3, Open office 1.", openOffice);
check(named === "Starting live class: Module 1, Class 2, current deck, Google Slides.", named);
check(officeNamed === "Starting open office: Module 3, Open office 1.", officeNamed);
if (failed.length) {
  console.error(failed.join("\n"));
  process.exit(1);
}
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=LMS_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        kind = js[js.index("async function commitLiveRunKind") : js.index("async function proceedRunLiveBegin")]
        self.assertIn("const isTest = selectedRunKind() === \"test\"", kind)
        self.assertIn("body: JSON.stringify({ is_test: isTest })", kind)
        begin = js[js.index("async function proceedRunLiveBegin") : js.index("export async function openRunLiveClass")]
        self.assertIn("body: JSON.stringify({ meeting_date: iso })", begin)
        seed_at = js.index("async function applyDeckSeedChoice")
        seed = js[seed_at : js.index('$("live-deck-seed")?.addEventListener', seed_at)]
        self.assertIn('body: JSON.stringify({ mode: "test_copy" })', seed)
        self.assertIn("const body = { mode };", seed)
        self.assertIn("body.source_module = sourceModule;", seed)
        self.assertIn("body.source_slot = sourceSlot;", seed)
        self.assertIn("body.source_class_id = sourceClass;", seed)
        minted = js[js.index("async function ensureLiveSessionMinted") : js.index("async function applyValidateDateChoice")]
        self.assertIn("live_module:", minted)
        self.assertIn("live_slot:", minted)
        start = js[js.index("function chooseSetClassStart") : js.index("function chooseSetClassKind")]
        self.assertIn("setClassStartFields(mode)", start)


class SetClassFlowPageTests(unittest.TestCase):
    """The new questions render, and the switch sits only on lists it filters."""

    def setUp(self) -> None:
        """Isolated app with one MCF3M class."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.client = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        self.client.get("/auth/google?portal=staff")
        self.client.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.client.post(
            "/verify-email",
            data={"code": self.school.get_user_by_email("teacher@gmail.com")["verification_code"]},
        )
        created = self.client.post(
            "/api/staff/classes",
            json={
                "offering_id": offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Aspen"],
            },
        )
        self.class_id = int(created.get_json()["class"]["id"])

    def tearDown(self) -> None:
        """Close the throwaway database."""
        self.school.close()
        self.tmp.cleanup()

    def _page(self, **query: str) -> str:
        """Return one staff course page."""
        return self.client.get(f"/staff/class/{self.class_id}", query_string=query).get_data(as_text=True)

    def test_set_class_asks_one_question_at_a_time(self) -> None:
        """The three start choices replace Session type and Run as."""
        html = self._page(tab="live")
        self.assertIn("What are you starting?", html)
        self.assertIn("Which class?", html)
        self.assertIn("Which deck?", html)
        self.assertIn("Defaulted to the next class", html)
        self.assertIn(">Use current</span>", html)
        self.assertLess(html.index(">Use current</span>"), html.index(">Previous</span>"))
        self.assertIn(TEST_DECK_LINE, html)
        self.assertIn('id="set-class-kind-live"', html)
        self.assertIn('id="set-class-run-kind-test"', html)
        self.assertIn('id="set-class-kind-office"', html)
        self.assertIn("set-class-run-kind-toggle", html)
        self.assertIn(">Start live class<", html)
        self.assertNotIn(">Start session<", html)
        self.assertNotIn("Session type", html)
        self.assertNotIn(">Run as<", html)
        self.assertNotIn('id="set-class-default-note"', html)
        self.assertIn("Starting live class:", html)

    def test_show_test_students_sits_on_filtered_lists_only(self) -> None:
        """The same switch is in the lists it filters, not under every tab."""
        live = self._page(tab="live")
        attendance = self._page(tab="ap", view="attendance")
        grades = self._page(tab="gradebook")
        slides = self._page(tab="lesson-slides")
        modules = self._page(tab="modules")
        home = self.client.get("/staff").get_data(as_text=True)
        for page in (live, attendance, grades, home):
            self.assertIn('id="show-test-students"', page)
            self.assertIn("Show test students", page)
        self.assertNotIn('id="show-test-students"', slides)
        self.assertNotIn('id="show-test-students"', modules)
        self.assertIn("class-list-head", live)
        self.assertLess(live.index("class-list-head"), live.index('id="show-test-students"'))
        self.assertIn("ap-tab-head", attendance)
        self.assertIn("list-filter-head", grades)
        self.assertIn("roster-names-switch", home)
        self.assertNotIn(SUMMARY_EXAMPLE, live)

    def test_set_class_keeps_one_forward_button(self) -> None:
        """Which day sits above Start, and header Start and Advance stay off."""
        html = self._page(tab="live")
        self.assertIn("Which day?", html)
        self.assertLess(html.index("Which deck?"), html.index("Which day?"))
        self.assertLess(html.index('id="set-class-day-step"'), html.index('id="set-class-footer"'))
        self.assertLess(html.index('id="ap-day-grid"'), html.index('id="set-class-start"'))
        day = html[html.index('id="set-class-day-step"') : html.index('id="set-class-footer"')]
        self.assertIn('id="ap-validate-hint"', day)
        self.assertIn('id="ap-day-grid"', day)
        self.assertIn(">Start live class<", html)
        start = html[html.index('id="live-start"') : html.index('id="live-start"') + 160]
        advance = html[html.index('id="live-advance"') : html.index('id="live-advance"') + 180]
        self.assertIn("live-legacy-control", start)
        self.assertIn("live-legacy-control", advance)
        self.assertIn(">Quit<", html)

        css = (LMS_DIR / "static" / "staff-shell.css").read_text(encoding="utf-8")
        hide = css[
            css.index("/* MCK-271: Set Class keeps one forward action. */") : css.index(
                "/* MCK-271 end */"
            )
        ]
        self.assertIn(".is-set-class", hide)
        for token in (
            "#live-stage-prev",
            "#live-stage-next",
            "#live-add-page",
            "#live-delete-page",
            "#live-eyes-up",
            "#live-student-view",
            "#live-end-class",
            "#live-start",
            "#live-advance",
        ):
            self.assertIn(token, hide)
        self.assertNotIn("#live-quit-class", hide)

        js = (LMS_DIR / "static" / "staff_ap.js").read_text(encoding="utf-8")
        self.assertNotIn('classList.remove("live-legacy-control")', js)
        self.assertNotIn('$("live-start").hidden = false', js)
        self.assertNotIn('$("live-advance").hidden = false', js)
        hold = js[
            js.index("function holdSetClassForwardActions()") : js.index(
                "function releaseSetClassForwardActions()"
            )
        ]
        self.assertIn('classList.add("live-legacy-control")', hold)
        self.assertIn("btn.hidden = true", hold)
        lock = js[
            js.index("function lockClassListPane()") : js.index(
                "function optionCardHasVisibleControls("
            )
        ]
        self.assertIn("holdSetClassForwardActions()", lock)
        self.assertIn("releaseSetClassForwardActions()", lock)
        sync = js[
            js.index("function syncSetClassRunKindUi()") : js.index(
                "function validateLogContext()"
            )
        ]
        self.assertIn("holdSetClassForwardActions()", sync)


if __name__ == "__main__":
    unittest.main()

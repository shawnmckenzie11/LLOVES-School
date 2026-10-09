#!/usr/bin/env python3
"""MCK-183 slice D: the Dashboard tour, You're set, and the Help menu."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

LMS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LMS_DIR))
sys.path.insert(0, str(LMS_DIR.parent))

import test_welcome_gate_mck183 as gate  # noqa: E402

NEW = gate.NEW
API = "/api/staff/onboarding/course"


class TourPageTests(unittest.TestCase):
    """Server side: who is offered the tour, where Help shows, Done."""

    setUp = gate.WelcomeGateTests.setUp
    tearDown = gate.WelcomeGateTests.tearDown
    _sign_in = gate.WelcomeGateTests._sign_in

    def _teacher_with_class(self, email: str = NEW, codes=("SBI4U",)) -> list[int]:
        if email != NEW:
            self.teacher = self.school.register_staff(email)
        self._sign_in(email, "/welcome")
        rv = self.client.post(API, json={"classes": [
            {"ontario_code": c, "live_days": "M/W/F", "live_time": "2:00pm"} for c in codes
        ]})
        self.assertEqual(rv.status_code, 200, rv.get_data(as_text=True))
        ids = [int(o["id"]) for o in rv.get_json()["offerings"]]
        classes = []
        for oid in ids[:1]:
            cls = self.client.post(
                "/api/staff/classes", json={"offering_id": oid, "codenames": ["Ana", "Sam"]}
            ).get_json()["class"]
            classes.append(int(cls["id"]))
        return classes

    def _body(self, html: str) -> str:
        return re.search(r"<body[^>]*>", html).group(0)

    def test_new_teacher_gets_the_tour_and_help(self) -> None:
        class_id, = self._teacher_with_class()
        html = self.client.get("/staff?tour=1").get_data(as_text=True)
        self.assertIn('data-tour-offer="1"', self._body(html))
        self.assertIn('id="onb-tour"', html)
        self.assertIn("onboarding_tour.js", html)
        self.assertIn(f'/staff/class/{class_id}?tab=ap&amp;view=attendance&amp;tour=done', html)
        for line in (
            "<b>Start class.</b> Press <b>Run Live Class</b> on your Dashboard. "
            "A join code appears on the screen for students.",
            "<b>Students join.</b> They go to alc.mckenzian.com and type their first name and the code. "
            "Attendance fills in as they arrive.",
            "<b>Ask a quick question.</b> Pick a question or poll and press <b>Publish</b>. "
            "Each answer counts toward participation. You don't need a math question; "
            "a check-in like 'How ready do you feel?' works.",
            "<b>End class.</b> Press <b>End Live Class</b> to save attendance and participation. "
            "You'll find them under <b>Attendance &amp; Participation</b>. Quit closes the class without saving.",
            "Skip tour", ">Back<", ">Next<", "Go to my class",
        ):
            self.assertIn(line, html)
        # Help menu in the Dashboard corner.
        self.assertIn("data-staff-help", html)
        self.assertIn('href="/staff?tour=1"', html)
        self.assertIn("data-help-whats-new", html)
        self.assertIn('href="mailto:solutions@mckenzian.com?subject=ALC%20help">Ask Shawn for help', html)

    def test_help_contact_env(self) -> None:
        self._teacher_with_class()
        os.environ["HELP_CONTACT_EMAIL"] = "help@example.org"
        try:
            html = self.client.get("/staff").get_data(as_text=True)
        finally:
            os.environ.pop("HELP_CONTACT_EMAIL", None)
        self.assertIn("mailto:help@example.org?", html)

    def test_owner_is_never_offered_the_tour(self) -> None:
        os.environ.pop("SENTRY_OWNER_EMAILS", None)
        self._teacher_with_class("solutions@mckenzian.com")
        html = self.client.get("/staff").get_data(as_text=True)
        self.assertIn('data-tour-offer="0"', self._body(html))
        # He can still take it from Help.
        self.assertIn('id="onb-tour"', html)
        self.assertIn('href="/staff?tour=1"', html)

    def test_owner_list_from_env(self) -> None:
        os.environ["SENTRY_OWNER_EMAILS"] = "boss@example.org, new.teacher@gmail.com"
        try:
            self._teacher_with_class()
            html = self.client.get("/staff").get_data(as_text=True)
        finally:
            os.environ.pop("SENTRY_OWNER_EMAILS", None)
        self.assertIn('data-tour-offer="0"', self._body(html))

    def test_last_names_step_leads_to_the_tour(self) -> None:
        self._sign_in(NEW, "/welcome")
        ids = [int(o["id"]) for o in self.client.post(API, json={"classes": [
            {"ontario_code": c, "live_days": "M/W/F", "live_time": "2:00pm"} for c in ("SBI4U", "SCH4U")
        ]}).get_json()["offerings"]]
        html = self.client.get("/staff/welcome?step=names").get_data(as_text=True)
        self.assertIn('data-next="/staff/welcome?step=names"', html)
        self.client.post("/api/staff/classes", json={"offering_id": ids[0], "codenames": ["Ana"]})
        html = self.client.get("/staff/welcome?step=names").get_data(as_text=True)
        self.assertIn("Who's in SCH4U?", html)
        self.assertIn('data-next="/staff?tour=1"', html)

    def test_done_dialog_on_attendance_and_participation(self) -> None:
        class_id, = self._teacher_with_class()
        html = self.client.get(
            f"/staff/class/{class_id}?tab=ap&view=attendance&tour=done"
        ).get_data(as_text=True)
        self.assertIn('id="onb-done"', html)
        self.assertIn("You're set", html)
        self.assertIn("Questions or something not working? Open <b>Help</b> any time, top right.", html)
        self.assertIn(">Take the tour again</a>", html)
        self.assertIn(">Done</button>", html)
        # Help in the course top bar: What's new links to the Dashboard panel.
        self.assertIn("data-staff-help", html)
        self.assertIn('href="/staff?whats_new=1"', html)
        plain = self.client.get(f"/staff/class/{class_id}?tab=ap").get_data(as_text=True)
        self.assertNotIn('id="onb-done"', plain)
        other = self.client.get(f"/staff/class/{class_id}?tab=modules&tour=done").get_data(as_text=True)
        self.assertNotIn('id="onb-done"', other)

    def test_live_class_holds_the_tour(self) -> None:
        class_id, = self._teacher_with_class()
        self.school.start_live_class_session(class_id, int(self.teacher["id"]))
        html = self.client.get("/staff?tour=1").get_data(as_text=True)
        self.assertIn('data-tour-offer="0"', self._body(html))
        self.assertNotIn('id="onb-tour"', html)
        self.assertNotIn("Take the tour again", html)
        self.assertIn("Ask Shawn for help", html)

    def test_no_class_no_tour(self) -> None:
        """Before any class there is nothing to point at."""
        self._sign_in(NEW, "/welcome")
        self.client.post(API, json={"classes": [
            {"ontario_code": "SBI4U", "live_days": "M/W/F", "live_time": "2:00pm"}
        ]})
        self.school.assign_course(teacher_user_id=int(self.teacher["id"]), ontario_code="SCH4U")
        rv = self.client.get("/staff?tour=1")
        self.assertEqual(rv.status_code, 302)  # names step first, never the tour
        self.assertIn("step=names", rv.headers["Location"])


HARNESS = r"""
import fs from "node:fs";
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const tour = await import(input.tour);
const wn = await import(input.wn);

class El {
  constructor(attrs = {}) {
    this.attrs = { ...attrs }; this.hidden = attrs.hidden !== undefined; this.listeners = {};
    this.classes = new Set(); this.children = []; this.textContent = ""; this.open = false; this.scrolled = 0;
    this.classList = { add: (c) => this.classes.add(c), remove: (c) => this.classes.delete(c),
                       contains: (c) => this.classes.has(c) };
  }
  getAttribute(n) { return n in this.attrs ? this.attrs[n] : null; }
  setAttribute(n, v) { this.attrs[n] = v; }
  addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }
  click() { for (const f of this.listeners.click || []) f({ preventDefault() {} }); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) {
    const m = /^\[([\w-]+)\]$/.exec(sel);
    return m ? this.children.filter((c) => m[1] in c.attrs) : [];
  }
  scrollIntoView() { this.scrolled += 1; }
  contains() { return true; }
}

function makeDoc(offer) {
  const root = new El({ id: "onb-tour", "data-class-href": "/staff/class/7?tab=ap&view=attendance&tour=done" });
  root.hidden = true;
  const targets = { ".course-action-live": new El(), ".course-card": new El(),
                    'a[aria-label="Take Attendance"]': new El() };
  const sel = [".course-action-live", ".course-card", ".course-action-live", 'a[aria-label="Take Attendance"]'];
  for (let i = 1; i <= 4; i++) root.children.push(new El({ "data-tour-step": String(i), "data-tour-target": sel[i - 1] }));
  for (const k of ["data-tour-back", "data-tour-next", "data-tour-finish", "data-tour-skip", "data-tour-count"]) {
    root.children.push(new El({ [k]: "" }));
  }
  const listeners = {};
  const doc = {
    body: { dataset: { userId: "5", tourOffer: offer ? "1" : "0" }, classList: { contains: () => false } },
    getElementById: (id) => (id === "onb-tour" ? root : null),
    querySelector: (s) => targets[s] || null,
    querySelectorAll: () => [],
    addEventListener: (t, f) => { (listeners[t] ||= []).push(f); },
    dispatchEvent: (e) => { for (const f of listeners[e.type] || []) f(e); return true; },
    root, targets, listeners,
  };
  return doc;
}
class CE { constructor(type, init) { this.type = type; this.detail = init && init.detail; } }
function makeStorage() {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), m };
}
const btn = (doc, k) => doc.root.children.find((c) => k in c.attrs);
const visible = (doc) => doc.root.children.filter((c) => "data-tour-step" in c.attrs && !c.hidden)
  .map((c) => c.attrs["data-tour-step"]);
const out = {};
{
  const storage = makeStorage(); const doc = makeDoc(true); const assigned = [];
  const win = { location: { search: "", assign: (h) => assigned.push(h) }, CustomEvent: CE };
  const ends = []; doc.addEventListener("alc-tour-end", (e) => ends.push(e.detail.status));
  out.first = tour.initTour({ document: doc, window: win, storage });
  out.firstVisible = visible(doc);
  out.firstLit = doc.targets[".course-action-live"].classList.contains("onb-tour-target");
  out.backHidden = btn(doc, "data-tour-back").hidden;
  btn(doc, "data-tour-next").click(); btn(doc, "data-tour-next").click();
  out.third = visible(doc);
  out.saved = JSON.parse(storage.getItem("alc-onboarding:5"));
  // Reload mid-tour resumes at step 3.
  const doc2 = makeDoc(true);
  doc2.addEventListener("alc-tour-end", (e) => ends.push(e.detail.status));
  out.resume = tour.initTour({ document: doc2, window: win, storage });
  btn(doc2, "data-tour-next").click();
  out.finishShown = !btn(doc2, "data-tour-finish").hidden && btn(doc2, "data-tour-next").hidden;
  btn(doc2, "data-tour-finish").click();
  out.assigned = assigned;
  out.done = JSON.parse(storage.getItem("alc-onboarding:5"));
  out.afterDone = tour.initTour({ document: makeDoc(true), window: win, storage });
  out.forced = tour.initTour({ document: makeDoc(true), window: { location: { search: "?tour=1" }, CustomEvent: CE }, storage });
  out.ends = ends;
}
{
  const storage = makeStorage(); const doc = makeDoc(true);
  const win = { location: { search: "" }, CustomEvent: CE };
  const ends = []; doc.addEventListener("alc-tour-end", (e) => ends.push(e.detail.status));
  tour.initTour({ document: doc, window: win, storage });
  btn(doc, "data-tour-skip").click();
  out.skipHidden = doc.root.hidden;
  out.skipEnds = ends;
  out.afterSkip = tour.initTour({ document: makeDoc(true), window: win, storage });
  out.notOffered = tour.initTour({ document: makeDoc(false), window: win, storage: makeStorage() });
  out.notOfferedForced = tour.initTour({ document: makeDoc(false), window: { location: { search: "?x=1&tour=1" }, CustomEvent: CE }, storage: makeStorage() });
}
{
  const s = makeStorage();
  out.holdFresh = wn.tourHolds({ doc: makeDoc(true), storage: s, userId: "5", search: "" });
  s.setItem("alc-onboarding:5", JSON.stringify({ step: 2, status: "skipped" }));
  out.holdSkipped = wn.tourHolds({ doc: makeDoc(true), storage: s, userId: "5", search: "" });
  out.holdForced = wn.tourHolds({ doc: makeDoc(false), storage: s, userId: "5", search: "?tour=1" });
  out.holdNoOffer = wn.tourHolds({ doc: makeDoc(false), storage: makeStorage(), userId: "5", search: "" });
  const noTour = makeDoc(true); noTour.getElementById = () => null;
  out.holdNoTourMarkup = wn.tourHolds({ doc: noTour, storage: makeStorage(), userId: "5", search: "" });
}
{
  // What's new waits for the tour, then opens after alc-tour-end.
  const dialog = { open: false, listeners: {}, getAttribute: () => null, setAttribute() {},
    querySelector: () => null, querySelectorAll: () => [], addEventListener(t, f) { (this.listeners[t] ||= []).push(f); },
    showModal() { this.open = true; } };
  const doc = makeDoc(true);
  const tourRoot = doc.root;
  doc.getElementById = (id) => (id === "whats-new-dialog" ? dialog : id === "onb-tour" ? tourRoot : null);
  doc.createElement = () => ({});
  const release = { releases: [{ id: "r1", day: "2026-10-04", items: [{ text: "x" }] }] };
  const fetch = async () => ({ ok: true, json: async () => release });
  const storage = makeStorage();
  out.wnHeld = await wn.initWhatsNew({ document: doc, window: { location: { search: "" } }, fetch, storage });
  out.wnOpenBefore = dialog.open;
  doc.dispatchEvent(new CE("alc-tour-end", { detail: { status: "skipped" } }));
  out.wnOpenAfter = dialog.open;
  const dialog2 = { ...dialog, open: false, listeners: {} };
  const doc2 = makeDoc(false);
  doc2.getElementById = (id) => (id === "whats-new-dialog" ? dialog2 : null);
  storage.setItem("alc-whats-new:5", "r1");
  out.wnForced = await wn.initWhatsNew({ document: doc2, window: { location: { search: "?whats_new=1" } }, fetch, storage });
}
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node is required for the onboarding_tour.js harness")
class TourScriptTests(unittest.TestCase):
    """The real onboarding_tour.js and whats_new.js in node with a fake DOM."""

    @classmethod
    def setUpClass(cls) -> None:
        payload = json.dumps({
            "tour": (LMS_DIR / "static" / "onboarding_tour.js").as_uri(),
            "wn": (LMS_DIR / "static" / "whats_new.js").as_uri(),
        })
        done = subprocess.run(
            [shutil.which("node"), "--input-type=module", "-e", HARNESS],
            input=payload, capture_output=True, text=True, timeout=60, check=False,
        )
        if done.returncode != 0:
            raise AssertionError(done.stderr)
        cls.out = json.loads(done.stdout)

    def test_walks_four_steps_and_finishes_on_the_class(self) -> None:
        o = self.out
        self.assertEqual(o["first"], 1)
        self.assertEqual(o["firstVisible"], ["1"])
        self.assertTrue(o["firstLit"])
        self.assertTrue(o["backHidden"])
        self.assertEqual(o["third"], ["3"])
        self.assertEqual(o["saved"], {"step": 3, "status": "active"})
        self.assertEqual(o["resume"], 3)
        self.assertTrue(o["finishShown"])
        self.assertEqual(o["assigned"], ["/staff/class/7?tab=ap&view=attendance&tour=done"])
        self.assertEqual(o["done"], {"step": 4, "status": "done"})
        self.assertEqual(o["afterDone"], 0)
        self.assertEqual(o["forced"], 1)
        self.assertEqual(o["ends"], ["done"])

    def test_skip_is_remembered_and_take_again_restarts(self) -> None:
        o = self.out
        self.assertTrue(o["skipHidden"])
        self.assertEqual(o["skipEnds"], ["skipped"])
        self.assertEqual(o["afterSkip"], 0)
        self.assertEqual(o["notOffered"], 0)
        self.assertEqual(o["notOfferedForced"], 1)

    def test_whats_new_waits_for_the_tour(self) -> None:
        o = self.out
        self.assertTrue(o["holdFresh"])
        self.assertFalse(o["holdSkipped"])
        self.assertTrue(o["holdForced"])
        self.assertFalse(o["holdNoOffer"])
        self.assertFalse(o["holdNoTourMarkup"])
        self.assertEqual(o["wnHeld"], "held")
        self.assertFalse(o["wnOpenBefore"])
        self.assertTrue(o["wnOpenAfter"])
        self.assertEqual(o["wnForced"], "opened")


if __name__ == "__main__":
    unittest.main()

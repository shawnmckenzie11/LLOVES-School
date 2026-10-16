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
        self.assertIn('data-tour-page="dashboard"', self._body(html))
        self.assertIn('id="onb-tour"', html)
        self.assertIn("onboarding_tour.js", html)
        self.assertNotIn("data-tour-next", html)
        self.assertNotIn("Go to my class", html)
        for line in (
            "Skip tour",
            ">Show me<",
            "Add a class first to start the tour.",
            "Populate your class",
            "Open the Question Bank",
            "Run a test live class",
            "Take attendance",
            "Check Participation",
            "Getting started 0 of 5",
            "You're all set. Replay the tour anytime from Help.",
            "▾",
        ):
            self.assertIn(line, html)
        script = (LMS_DIR / "static" / "onboarding_tour.js").read_text(encoding="utf-8")
        for line in (
            "Click <b>Populate Class</b> to add your roster.",
            "<b>Find questions.</b> Open the <b>Question Bank</b> to browse ready-made questions and polls.",
            "Switch to <b>Test</b> so this run doesn't go in your gradebook.",
            "Click <b>Run Live Class</b>. Share the join code with your students.",
            "<b>Ask a quick question.</b> Pick a question and click <b>Publish</b>.",
            "<b>Answers show up here.</b> Each one counts toward participation.",
            "Click <b>End Live Class</b>. Leave Save Attendance and Save Participation ticked to keep them.",
            "<b>Check who came.</b> Click <b>Take Attendance</b>.",
            "<b>See who took part.</b> Open <b>Participation</b>.",
        ):
            self.assertIn(line, script)
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

    def test_run_type_helper_degrades_until_mck269(self) -> None:
        """Missing Test/Live field is unknown. A real value is passed through."""
        from onboarding_tour import tour_run_type

        self.assertEqual(tour_run_type(None), "")
        self.assertEqual(tour_run_type({"id": 4, "status": "active"}), "")
        self.assertEqual(tour_run_type({"run_type": "test"}), "test")
        self.assertEqual(tour_run_type({"start_run_mode": "Live"}), "live")
        self.assertEqual(tour_run_type({"session_run_type": "nope"}), "")

    def test_course_pages_carry_the_tour_shell(self) -> None:
        """Live class, Attendance, and Question Bank get the checklist."""
        class_id, = self._teacher_with_class()
        live = self.client.get(f"/staff/class/{class_id}?tab=live").get_data(as_text=True)
        self.assertIn('data-tour-page="live"', self._body(live))
        self.assertIn('data-tour-run-type=""', self._body(live))
        self.assertIn('id="onb-check"', live)
        ap = self.client.get(
            f"/staff/class/{class_id}?tab=ap&view=attendance"
        ).get_data(as_text=True)
        self.assertIn('data-tour-page="ap"', self._body(ap))
        self.assertIn('data-tour-view="attendance"', self._body(ap))
        part = self.client.get(
            f"/staff/class/{class_id}?tab=ap&view=participation"
        ).get_data(as_text=True)
        self.assertIn('data-tour-view="participation"', self._body(part))
        bank = self.client.get(
            f"/staff/class/{class_id}?tab=question-banks"
        ).get_data(as_text=True)
        self.assertIn('data-tour-page="question_bank"', self._body(bank))
        self.assertGreaterEqual(int(re.search(r'data-tour-roster="(\d+)"', bank).group(1)), 1)

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
const out = {};
{
  const s = { getItem: () => "{", setItem() {} };
  out.garbage = tour.readTour(s, "5");
  const mem = new Map();
  const storage = {
    getItem: (k) => (mem.has(k) ? mem.get(k) : null),
    setItem: (k, v) => mem.set(k, String(v)),
  };
  out.oldSkip = tour.readTour({ getItem: () => JSON.stringify({ step: 2, status: "skipped" }) }, "5");
  out.oldDone = tour.readTour({ getItem: () => JSON.stringify({ step: 4, status: "done" }) }, "5");
  out.oldActive = tour.readTour({ getItem: () => JSON.stringify({ step: 3, status: "active" }) }, "5");
  const fresh = tour.blankState();
  const rostered = tour.applySignals(fresh, { roster: 2, now: "t1" });
  out.roster = rostered.done;
  const bank = tour.applySignals(rostered, { page: "question_bank", now: "t2" });
  out.bank = Boolean(bank.done.question_bank);
  const part = tour.applySignals(bank, { view: "participation", now: "t3" });
  out.part = Boolean(part.done.participation);
  const saved = tour.applySignals(part, { attendanceSaved: true, now: "t4" });
  out.attendance = saved.done.attendance && saved.done.take_attendance;
  const unknownEnd = tour.applySignals(saved, { liveEnded: true, runType: "", live: true, now: "t5" });
  out.unknownNoTick = !unknownEnd.done.test_live;
  const liveEnd = tour.applySignals(saved, { liveEnded: true, runType: "live", live: true, now: "t5" });
  out.liveNoTick = !liveEnd.done.test_live && liveEnd.liveTest == null;
  const started = tour.applySignals(saved, { runType: "test", live: true, session: "9", now: "t6" });
  out.started = started.liveTest;
  const ended = tour.applySignals(started, { liveEnded: true, runType: "test", live: true, session: "9", now: "t7" });
  out.ended = {
    test: ended.done.test_live, run: ended.done.run_live, end: ended.done.end_live,
    status: ended.status, toast: ended.checklist.toast, collapsed: ended.checklist.collapsed,
  };
  out.kinds = [tour.liveRunIsTest(""), tour.liveRunIsTest("test"), tour.liveRunIsTest("live")];
  const click = tour.completeStep(rostered, "run_live", "t8");
  out.clickKeepsChecklist = Boolean(click.done.run_live) && !click.done.test_live;
  const replay = tour.replayPage(click, "dashboard");
  out.replay = { run: replay.done.run_live || "", populate: Boolean(replay.done.populate), status: replay.status };
  out.skip = tour.skipTour(fresh).checklist.collapsed && tour.skipTour(fresh).status;
  const noCard = tour.planSpotlight({
    page: "dashboard", runType: "", done: {}, noCourseCard: true,
    controls: { set_class: "absent", question_bank: "absent", test_toggle: "absent", run_live: "absent" },
  });
  out.empty = noCard.mode;
  const skipped = tour.planSpotlight({
    page: "dashboard", runType: "", done: { set_class: "t" }, noCourseCard: false,
    controls: { question_bank: "absent", test_toggle: "skip", run_live: "ready" },
  });
  out.planRun = skipped;
  const waiting = tour.planSpotlight({
    page: "live", runType: "", done: {},
    controls: { publish: "wait", watch_answers: "wait", end_live: "ready" },
  });
  out.wait = waiting;
  const explicitLive = tour.planSpotlight({
    page: "live", runType: "live", done: {},
    controls: { publish: "ready", end_live: "ready" },
  });
  out.noLiveSteps = explicitLive.mode;
  out.defaultTest = tour.stepsFor("live", "").map((step) => step.id);
  tour.writeTour(storage, "5", ended);
  out.roundTrip = tour.readTour(storage, "5").status;
}
function El(attrs = {}) {
  this.nodeType = 1;
  this.attrs = { ...attrs };
  this.hidden = false;
  this.textContent = "";
  this.innerHTML = "";
  this.style = {};
  this.kids = [];
  this.listeners = {};
  this.scrolled = 0;
  const classes = new Set();
  this.classList = {
    add: (c) => classes.add(c),
    remove: (c) => classes.delete(c),
    contains: (c) => classes.has(c),
    toggle: (c, on) => {
      const next = on === undefined ? !classes.has(c) : Boolean(on);
      if (next) classes.add(c); else classes.delete(c);
      return next;
    },
  };
}
El.prototype.getAttribute = function (n) { return n in this.attrs ? this.attrs[n] : null; };
El.prototype.setAttribute = function (n, v) { this.attrs[n] = String(v); };
El.prototype.addEventListener = function (t, f) { (this.listeners[t] ||= []).push(f); };
El.prototype.matches = function (sel) {
  const m = /^\[([\w-]+)\]$/.exec(sel);
  return Boolean(m && m[1] in this.attrs);
};
El.prototype.querySelectorAll = function (sel) {
  const found = [];
  for (const kid of this.kids) {
    if (kid.matches && kid.matches(sel)) found.push(kid);
    if (kid.querySelectorAll) found.push(...kid.querySelectorAll(sel));
  }
  return found;
};
El.prototype.querySelector = function (sel) { return this.querySelectorAll(sel)[0] || null; };
El.prototype.contains = function (node) {
  if (node === this) return true;
  return this.kids.some((kid) => kid === node || (kid.contains && kid.contains(node)));
};
El.prototype.closest = function () { return null; };
El.prototype.scrollIntoView = function () { this.scrolled += 1; };
function fire(el, type, event) {
  for (const fn of (el.listeners && el.listeners[type]) || []) fn(event);
}
function taskRow(id) {
  const button = new El({ "data-check-show": "" });
  button.hidden = true;
  const row = new El({ "data-task": id });
  row.kids = [button];
  row.closest = function (sel) { return this.matches(sel) ? this : null; };
  button.closest = function (sel) { return this.matches(sel) ? this : row.closest(sel); };
  return row;
}
function makeDoc(dataset, catalog) {
  const count = new El({ "data-tour-count": "" });
  const copy = new El({ "data-tour-copy": "" });
  const show = new El({ "data-tour-show": "" });
  const skip = new El({ "data-tour-skip": "" });
  const actions = new El();
  actions.kids = [show, skip];
  const pop = new El({ "data-tour-pop": "" });
  pop.hidden = true;
  pop.kids = [count, copy, actions];
  const emptySkip = new El({ "data-tour-skip": "" });
  const empty = new El({ "data-tour-empty": "" });
  empty.hidden = true;
  empty.kids = [emptySkip];
  const dim = new El({ "data-tour-dim": "" });
  const root = new El({ id: "onb-tour" });
  root.hidden = true;
  root.kids = [dim, pop, empty];
  const title = new El({ "data-check-title": "" });
  const collapse = new El({ "data-check-collapse": "" });
  const fill = new El({ "data-check-fill": "" });
  const bar = new El({ "data-check-bar": "" });
  bar.kids = [fill];
  const rows = ["populate", "question_bank", "test_live", "attendance", "participation"].map(taskRow);
  const list = new El();
  list.kids = rows;
  const card = new El({ "data-check-card": "" });
  card.kids = [title, collapse, bar, list];
  const pillLabel = new El({ "data-check-pill-label": "" });
  const expand = new El({ "data-check-expand": "" });
  expand.kids = [pillLabel];
  const dismiss = new El({ "data-check-dismiss": "" });
  dismiss.hidden = true;
  const pill = new El({ "data-check-pill": "" });
  pill.hidden = true;
  pill.kids = [expand, dismiss];
  const check = new El({ id: "onb-check" });
  check.hidden = true;
  check.kids = [card, pill];
  const toast = new El({ id: "onb-toast" });
  toast.hidden = true;
  const listeners = {};
  const doc = {
    body: { dataset },
    listeners,
    root, pop, copy, count, show, skip, empty, check, title, rows, toast, dim,
    getElementById: (id) => ({ "onb-tour": root, "onb-check": check, "onb-toast": toast })[id] || null,
    querySelectorAll: (sel) => (catalog[sel] || []).slice(),
    querySelector: (sel) => (catalog[sel] || [])[0] || null,
    addEventListener: (t, f) => { (listeners[t] ||= []).push(f); },
    dispatchEvent: (e) => { for (const f of listeners[e.type] || []) f(e); return true; },
  };
  return doc;
}
class CE { constructor(type, init) { this.type = type; this.detail = init && init.detail; } }
function memory() {
  const mem = new Map();
  return { getItem: (k) => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, String(v)), mem };
}
{
  const live = new El();
  live.textContent = "Run Live Class";
  const card = new El();
  const catalog = {
    ".course-card": [card],
    "a.course-action-live": [live],
  };
  const storage = memory();
  const doc = makeDoc({
    userId: "5", tourOffer: "1", tourPage: "dashboard", tourRoster: "1",
    tourLive: "0", tourBlock: "0", tourRunType: "", tourSession: "",
    tourDashboard: "/staff", tourNav: "/staff/class/7", tourAp: "/staff/class/7?tab=ap&view=attendance",
    tourLiveHref: "/staff/class/7?tab=live",
  }, catalog);
  const assigned = [];
  const ends = [];
  doc.addEventListener("alc-tour-end", (e) => ends.push(e.detail.status));
  const win = { location: { href: "http://127.0.0.1:8787/staff", pathname: "/staff", search: "" }, assign: (h) => assigned.push(h), CustomEvent: CE, matchMedia: () => ({ matches: false }), innerWidth: 1280, addEventListener() {} };
  win.location.assign = (h) => assigned.push(h);
  out.opened = tour.initTour({ document: doc, window: win, storage, now: () => "t9" });
  out.count = doc.count.textContent;
  out.copy = doc.copy.innerHTML;
  out.lit = live.classList.contains("onb-spotlight");
  out.checkTitle = doc.title.textContent;
  out.nextShow = doc.rows[1].querySelector("[data-check-show]").hidden === false;
  fire(doc.show, "click", { preventDefault() {}, target: doc.show });
  out.afterShow = JSON.parse(storage.getItem("alc-onboarding:5")).done.run_live || "";
  for (const fn of doc.listeners.click || []) fn({ target: live });
  const afterClick = JSON.parse(storage.getItem("alc-onboarding:5"));
  out.afterClick = Boolean(afterClick.done.run_live);
  out.clickNotChecklist = !afterClick.done.test_live;
  out.ends = ends.slice();
  fire(doc.skip, "click", { preventDefault() {}, target: doc.skip });
  const skipped = JSON.parse(storage.getItem("alc-onboarding:5"));
  out.skipped = { status: skipped.status, collapsed: skipped.checklist.collapsed, hidden: doc.root.hidden };
  out.skipEnds = ends.slice();
  const doc2 = makeDoc(doc.body.dataset, catalog);
  out.resumeSkipped = tour.initTour({ document: doc2, window: win, storage, now: () => "t10" });
  const winForced = { ...win, location: { href: "http://127.0.0.1:8787/staff?tour=1", pathname: "/staff", search: "?tour=1", assign: win.location.assign } };
  const doc3 = makeDoc(doc.body.dataset, catalog);
  out.replayOpened = tour.initTour({ document: doc3, window: winForced, storage, now: () => "t11" });
  const pausedStore = memory();
  const doc4 = makeDoc(doc.body.dataset, catalog);
  tour.initTour({ document: doc4, window: win, storage: pausedStore, now: () => "t12" });
  for (const fn of doc4.listeners.keydown || []) fn({ key: "Escape" });
  out.paused = JSON.parse(pausedStore.getItem("alc-onboarding:5")).status;
  const doc5 = makeDoc(doc.body.dataset, catalog);
  out.resumePaused = tour.initTour({ document: doc5, window: win, storage: pausedStore, now: () => "t13" });
  const narrow = makeDoc({ ...doc.body.dataset, tourOffer: "1" }, catalog);
  const narrowWin = { ...win, matchMedia: () => ({ matches: true }) };
  out.narrow = tour.initTour({ document: narrow, window: narrowWin, storage: memory(), now: () => "t14" });
  out.narrowHidden = narrow.root.hidden;
  out.narrowCheck = narrow.check.hidden;
  const owner = makeDoc({ ...doc.body.dataset, tourOffer: "0" }, catalog);
  out.owner = tour.initTour({ document: owner, window: win, storage: memory(), now: () => "t15" });
  out.ownerCheck = owner.check.hidden;
}
{
  function holdDoc(offer) {
    const root = { hidden: true };
    const classes = new Set();
    return {
      body: {
        dataset: { userId: "5", tourOffer: offer ? "1" : "0" },
        classList: { contains: (c) => classes.has(c), add: (c) => classes.add(c), remove: (c) => classes.delete(c) },
      },
      getElementById: (id) => (id === "onb-tour" ? root : null),
    };
  }
  const s = memory();
  out.holdFresh = wn.tourHolds({ doc: holdDoc(true), storage: s, userId: "5", search: "" });
  s.setItem("alc-onboarding:5", JSON.stringify({ version: 2, status: "skipped", done: {}, checklist: {} }));
  out.holdSkipped = wn.tourHolds({ doc: holdDoc(true), storage: s, userId: "5", search: "" });
  s.setItem("alc-onboarding:5", JSON.stringify({ version: 2, status: "paused", done: {}, checklist: {} }));
  out.holdPaused = wn.tourHolds({ doc: holdDoc(true), storage: s, userId: "5", search: "" });
  out.holdForced = wn.tourHolds({ doc: holdDoc(false), storage: s, userId: "5", search: "?tour=1" });
  out.holdNoOffer = wn.tourHolds({ doc: holdDoc(false), storage: memory(), userId: "5", search: "" });
  const noTour = holdDoc(true);
  noTour.getElementById = () => null;
  out.holdNoTourMarkup = wn.tourHolds({ doc: noTour, storage: memory(), userId: "5", search: "" });
  const dialog = { open: false, listeners: {}, getAttribute: () => null, setAttribute() {},
    querySelector: () => null, querySelectorAll: () => [], addEventListener(t, f) { (this.listeners[t] ||= []).push(f); },
    showModal() { this.open = true; } };
  const doc = holdDoc(true);
  const tourRoot = {};
  doc.getElementById = (id) => (id === "whats-new-dialog" ? dialog : id === "onb-tour" ? tourRoot : null);
  doc.addEventListener = (t, f) => { (doc.listeners ||= {})[t] = f; };
  doc.dispatchEvent = (e) => { const fn = (doc.listeners || {})[e.type]; if (fn) fn(e); return true; };
  doc.createElement = () => ({});
  const release = { releases: [{ id: "r1", day: "2026-10-04", items: [{ text: "x" }] }] };
  const fetch = async () => ({ ok: true, json: async () => release });
  const storage = memory();
  out.wnHeld = await wn.initWhatsNew({ document: doc, window: { location: { search: "" } }, fetch, storage });
  out.wnOpenBefore = dialog.open;
  doc.dispatchEvent(new CE("alc-tour-end", { detail: { status: "skipped" } }));
  out.wnOpenAfter = dialog.open;
}
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node is required for the onboarding_tour.js harness")
class TourScriptTests(unittest.TestCase):
    """The real onboarding_tour.js and whats_new.js in node."""

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
            raise AssertionError(done.stderr or done.stdout)
        cls.out = json.loads(done.stdout)

    def test_state_checklist_and_test_run_helper(self) -> None:
        o = self.out
        self.assertIsNone(o["garbage"])
        self.assertEqual(o["oldSkip"]["status"], "skipped")
        self.assertTrue(o["oldSkip"]["checklist"]["collapsed"])
        self.assertEqual(o["oldDone"]["status"], "done")
        self.assertEqual(o["oldActive"]["status"], "active")
        self.assertEqual(o["oldActive"]["version"], 2)
        self.assertTrue(o["roster"]["populate"])
        self.assertTrue(o["roster"]["set_class"])
        self.assertTrue(o["bank"])
        self.assertTrue(o["part"])
        self.assertTrue(o["attendance"])
        self.assertTrue(o["unknownNoTick"])
        self.assertTrue(o["liveNoTick"])
        self.assertEqual(o["started"]["kind"], "test")
        self.assertEqual(o["ended"]["status"], "done")
        self.assertTrue(o["ended"]["toast"])
        self.assertTrue(o["ended"]["collapsed"])
        self.assertEqual(o["ended"]["test"], "t7")
        self.assertEqual(o["kinds"], [None, True, False])
        self.assertTrue(o["clickKeepsChecklist"])
        self.assertEqual(o["replay"]["run"], "")
        self.assertTrue(o["replay"]["populate"])
        self.assertEqual(o["replay"]["status"], "active")
        self.assertEqual(o["skip"], "skipped")
        self.assertEqual(o["empty"], "empty")
        self.assertEqual(o["planRun"]["id"], "run_live")
        self.assertEqual(o["planRun"]["n"], 4)
        self.assertEqual(o["wait"]["waiting"], "publish")
        self.assertEqual(o["noLiveSteps"], "off")
        self.assertEqual(o["defaultTest"], ["publish", "watch_answers", "end_live"])
        self.assertEqual(o["roundTrip"], "done")

    def test_spotlight_click_skip_pause_and_narrow(self) -> None:
        o = self.out
        self.assertEqual(o["opened"], "run_live")
        self.assertEqual(o["count"], "Step 4 of 4")
        self.assertIn("Run Live Class", o["copy"])
        self.assertTrue(o["lit"])
        self.assertEqual(o["checkTitle"], "Getting started 1 of 5")
        self.assertTrue(o["nextShow"])
        self.assertEqual(o["afterShow"], "")
        self.assertTrue(o["afterClick"])
        self.assertTrue(o["clickNotChecklist"])
        self.assertEqual(o["ends"], [])
        self.assertEqual(o["skipped"]["status"], "skipped")
        self.assertTrue(o["skipped"]["collapsed"])
        self.assertTrue(o["skipped"]["hidden"])
        self.assertEqual(o["skipEnds"], ["skipped"])
        self.assertEqual(o["resumeSkipped"], "")
        self.assertEqual(o["replayOpened"], "run_live")
        self.assertEqual(o["paused"], "paused")
        self.assertEqual(o["resumePaused"], "run_live")
        self.assertEqual(o["narrow"], "")
        self.assertTrue(o["narrowHidden"])
        self.assertFalse(o["narrowCheck"])
        self.assertEqual(o["owner"], "")
        self.assertTrue(o["ownerCheck"])

    def test_whats_new_waits_for_the_tour(self) -> None:
        o = self.out
        self.assertTrue(o["holdFresh"])
        self.assertFalse(o["holdSkipped"])
        self.assertTrue(o["holdPaused"])
        self.assertTrue(o["holdForced"])
        self.assertFalse(o["holdNoOffer"])
        self.assertFalse(o["holdNoTourMarkup"])
        self.assertEqual(o["wnHeld"], "held")
        self.assertFalse(o["wnOpenBefore"])
        self.assertTrue(o["wnOpenAfter"])


if __name__ == "__main__":
    unittest.main()

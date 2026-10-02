#!/usr/bin/env python3
"""MCK-124 slice 1: staff "What's new" data file and Dashboard panel.

Three groups:

* the ``releases.json`` limits from the spec (Wonder's copy rules);
* server pages: the panel is on the staff Dashboard only, never on the
  Live tab (``body.course-live``) and never on a student page;
* the real ``whats_new.js`` in node with a small fake DOM: it opens once
  per user per release, and on ``body.course-live`` it renders nothing,
  fetches nothing and never opens, not even from the link.
"""

from __future__ import annotations

import json
import os
import re
import shutil
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

RELEASES_JSON = LMS_DIR / "static" / "whats-new" / "releases.json"
WHATS_NEW_JS = LMS_DIR / "static" / "whats_new.js"
TEMPLATES = LMS_DIR / "templates"
AUDIENCES = {"Teacher", "Both", "Both (projector)"}
RELEASE_ID = re.compile(r"^\d{4}-\d{2}-\d{2}(-\d+)?$")
# Markers that only the What's new panel puts on a page.
PANEL_MARKERS = ("whats-new-dialog", "whats_new.js", "whats-new-open", "whats-new/releases.json")


def _releases() -> list[dict]:
    """Return the ``releases`` list from the data file."""
    data = json.loads(RELEASES_JSON.read_text(encoding="utf-8"))
    return data["releases"]


class ReleaseNotesDataTests(unittest.TestCase):
    """``releases.json`` keeps to the spec's limits."""

    def test_ids_unique_well_formed_and_newest_first(self) -> None:
        releases = _releases()
        self.assertTrue(releases, "at least one release")
        ids = [r["id"] for r in releases]
        self.assertEqual(len(ids), len(set(ids)), ids)
        for rid in ids:
            self.assertRegex(rid, RELEASE_ID)
        keys = [(r["id"][:10], int(r["id"][11:] or 1)) for r in releases]
        self.assertEqual(keys, sorted(keys, reverse=True), "newest release first")

    def test_items_keep_to_wonder_limits(self) -> None:
        for release in _releases():
            self.assertRegex(release["date"], r"^\d{4}-\d{2}-\d{2}$")
            items = release["items"]
            self.assertTrue(1 <= len(items) <= 7, (release["id"], len(items)))
            for item in items:
                with self.subTest(release=release["id"], title=item.get("title")):
                    title, line = item["title"], item["line"]
                    self.assertTrue(item["refs"].strip())
                    self.assertLessEqual(len(title.split()), 5)
                    self.assertLessEqual(len(title), 40)
                    self.assertFalse(title.endswith("."), "titles have no period")
                    self.assertLessEqual(len(line), 120)
                    self.assertIn(item["audience"], AUDIENCES)
                    for text in (title, line, item["refs"]):
                        self.assertNotRegex(text, r"[<>]", "plain text only")
                    self.assertIn("image", item)
                    self.assertIn("screenshot", item)

    def test_not_yet_footer_is_optional_and_short(self) -> None:
        """The footer is its own field, so it can be dropped without edits elsewhere."""
        for release in _releases():
            if "not_yet" not in release:
                self.assertNotIn("not_yet_refs", release)
                continue
            self.assertIsInstance(release["not_yet"], str)
            self.assertLessEqual(len(release["not_yet"]), 120)
            self.assertNotRegex(release["not_yet"], r"[<>]")

    def test_first_release_is_the_ac2fcf0_copy_without_images(self) -> None:
        """Slice 1 seeds the Oct 2 release (Wonder v1) and ships no images."""
        oldest = _releases()[-1]
        self.assertEqual(oldest["id"], "2026-10-02")
        self.assertEqual(oldest["covers_main"], "ac2fcf0")
        self.assertEqual(
            [i["refs"] for i in oldest["items"]],
            [
                "#209 · MCK-112",
                "#208 · MCK-112",
                "#198 · MCK-72",
                "#202 (+#200) · MCK-46",
                "#207 · MCK-111",
                "#210 · MCK-114",
                "#211 · MCK-83",
            ],
        )
        self.assertTrue(all(i["image"] is None for i in oldest["items"]))


class WhatsNewPagesTests(unittest.TestCase):
    """Server side: staff Dashboard only; never Live, never students."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(db_path=root / "lloves.sqlite", data_dir=root, testing=True)
        self.school = self.app.config["SCHOOL_DB"]
        self.staff = self.app.test_client()
        self.student = self.app.test_client()
        self.school.activate_from_semester_json()
        self.teacher = self.school.register_staff("teacher@gmail.com")
        offering = self.school.assign_course(
            teacher_user_id=int(self.teacher["id"]), ontario_code="MCF3M"
        )
        self.staff.get("/auth/google?portal=staff")
        self.staff.get("/auth/google/callback?email=teacher@gmail.com&name=T")
        self.staff.post(
            "/verify-email",
            data={"code": self.school.get_user_by_email("teacher@gmail.com")["verification_code"]},
        )
        created = self.staff.post(
            "/api/staff/classes",
            json={
                "offering_id": offering["id"],
                "days": "M/W/F",
                "time": "2:00pm",
                "codenames": ["Maple", "Aspen"],
            },
        )
        self.assertEqual(created.status_code, 200)
        self.class_id = int(created.get_json()["class"]["id"])

    def tearDown(self) -> None:
        self.school.close()
        self.tmp.cleanup()

    def assertNoPanel(self, html: str, where: str) -> None:  # noqa: N802
        for marker in PANEL_MARKERS:
            self.assertNotIn(marker, html, f"{marker} on {where}")

    def test_staff_dashboard_has_the_panel(self) -> None:
        resp = self.staff.get("/staff")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        body = re.search(r"<body[^>]*>", html).group(0)
        self.assertIn(f'data-user-id="{int(self.teacher["id"])}"', body)
        self.assertNotIn("course-live", body)
        self.assertIn('<dialog id="whats-new-dialog"', html)
        self.assertIn('id="whats-new-open"', html)
        self.assertIn('src="/static/whats_new.js?v=', html)
        self.assertIn('data-releases-src="/static/whats-new/releases.json?v=', html)
        self.assertIn("What's new in ALC", html)
        self.assertIn("Got it", html)

    def test_live_tab_never_has_the_panel(self) -> None:
        """Hard rule: no dialog, link or script on ``body.course-live``."""
        resp = self.staff.get(f"/staff/class/{self.class_id}?tab=live")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertRegex(re.search(r"<body[^>]*>", html).group(0), r"\bcourse-live\b")
        self.assertNoPanel(html, "the Live tab")
        # Still absent once the class is actually running.
        run = self.staff.post(f"/staff/class/{self.class_id}/run-live", follow_redirects=False)
        self.assertEqual(run.status_code, 302)
        live = self.staff.get(f"/staff/class/{self.class_id}?tab=live").get_data(as_text=True)
        self.assertRegex(re.search(r"<body[^>]*>", live).group(0), r"\bcourse-live\b")
        self.assertNoPanel(live, "the running Live tab")

    def test_course_tabs_wait_for_slice_2(self) -> None:
        """Slice 1 is Dashboard only; course tabs get it in slice 2."""
        for tab in ("modules", "attendance", "grades"):
            html = self.staff.get(f"/staff/class/{self.class_id}?tab={tab}").get_data(as_text=True)
            self.assertNoPanel(html, f"tab={tab}")

    def test_student_pages_never_have_the_panel(self) -> None:
        """Students never see it: the join flow and live home carry no panel."""
        run = self.staff.post(f"/staff/class/{self.class_id}/run-live", follow_redirects=False)
        self.assertEqual(run.status_code, 302)
        live = self.school.get_active_live_session_for_class(self.class_id)
        self.assertNoPanel(self.student.get("/").get_data(as_text=True), "landing")
        joined = self.student.post(
            "/auth/student-code",
            data={"code": str(live["session_code"]), "name": "Maple"},
            follow_redirects=True,
        )
        self.assertNoPanel(joined.get_data(as_text=True), "student join")
        self.student.post("/student/mood", data={"mood": "good"})
        self.student.post("/student/character", data={"character": "fox"})
        home = self.student.get("/student/home")
        self.assertEqual(home.status_code, 200)
        self.assertNoPanel(home.get_data(as_text=True), "student home")

    def test_only_the_staff_dashboard_template_includes_the_partial(self) -> None:
        users = sorted(
            str(path.relative_to(TEMPLATES))
            for path in TEMPLATES.rglob("*.html")
            if path.name != "_whats_new.html"
            and any(m in path.read_text(encoding="utf-8") for m in ("_whats_new.html", "whats_new.js"))
        )
        self.assertEqual(users, ["staff/home.html"])
        for path in (TEMPLATES / "student").rglob("*.html"):
            self.assertNoPanel(path.read_text(encoding="utf-8"), str(path))


HARNESS = r"""
import fs from "node:fs";
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const mod = await import(input.module);

class ClassList {
  constructor(names = []) { this.set = new Set(names); }
  contains(c) { return this.set.has(c); }
  add(c) { this.set.add(c); }
}
class El {
  constructor(tag, attrs = {}) {
    this.tag = tag; this.attrs = { ...attrs }; this.children = []; this.listeners = {};
    this.className = ""; this.textContent = ""; this.hidden = "hidden" in attrs; this.dataset = {};
  }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  hasAttribute(k) { return k in this.attrs; }
  addEventListener(t, fn) { (this.listeners[t] ||= []).push(fn); }
  dispatch(t) { for (const fn of this.listeners[t] || []) fn({ type: t, preventDefault() {} }); }
  click() { this.dispatch("click"); }
  append(...els) { this.children.push(...els); }
  appendChild(el) { this.children.push(el); return el; }
  replaceChildren() { this.children = []; }
  all() { return this.children.flatMap((c) => [c, ...c.all()]); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) {
    const m = /^\[([\w-]+)\]$/.exec(sel);
    return m ? this.all().filter((c) => c.hasAttribute(m[1])) : [];
  }
  set innerHTML(_v) { throw new Error("innerHTML must not be used"); }
}

function makePage({ live, userId, withDialog = true }) {
  const counters = { showModal: 0 };
  const dialog = new El("dialog", { id: "whats-new-dialog", "data-releases-src": "/static/whats-new/releases.json?v=abc" });
  dialog.open = false;
  dialog.showModal = () => { counters.showModal += 1; dialog.open = true; };
  dialog.close = () => { if (!dialog.open) return; dialog.open = false; dialog.dispatch("close"); };
  const date = new El("span", { "data-whats-new-date": "" });
  const x = new El("button", { "data-whats-new-close": "" });
  const list = new El("ol", { "data-whats-new-list": "" });
  const notYet = new El("p", { "data-whats-new-notyet": "", hidden: "" });
  const notYetText = new El("span", { "data-whats-new-notyet-text": "" });
  notYet.append(notYetText);
  const ok = new El("button", { "data-whats-new-close": "" });
  dialog.append(date, x, list, notYet, ok);
  const link = new El("button", { id: "whats-new-open", hidden: "" });
  const body = new El("body");
  body.classList = new ClassList(live ? ["staff-shell", "course-live"] : ["staff-shell", "staff-home"]);
  if (userId) body.dataset.userId = String(userId);
  const byId = { "whats-new-open": link };
  if (withDialog) byId["whats-new-dialog"] = dialog;
  const document = {
    body,
    getElementById: (id) => byId[id] || null,
    createElement: (tag) => new El(tag),
  };
  return { document, dialog, list, date, notYet, notYetText, x, ok, link, counters };
}

function makeStorage(seed = {}) {
  const data = { ...seed };
  return { data, getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); } };
}

function makeFetch(body, { ok = true, onCall } = {}) {
  const calls = [];
  const fn = async (url) => { calls.push(url); if (onCall) onCall(); return { ok, json: async () => body }; };
  fn.calls = calls;
  return fn;
}

const releases = input.releases;
const out = {};
const snapshot = (p) => ({
  showModal: p.counters.showModal,
  open: p.dialog.open,
  items: p.list.children.length,
  linkHidden: p.link.hidden,
});

// 1. Live class page: nothing at all, even with an unseen release and a link click.
{
  const p = makePage({ live: true, userId: 7 });
  const fetch = makeFetch(releases);
  const storage = makeStorage();
  const result = await mod.initWhatsNew({ document: p.document, fetch, storage });
  p.link.click();
  out.live = { result, fetches: fetch.calls.length, stored: storage.data, ...snapshot(p) };
}
// 2. The page goes live while releases.json loads: still nothing.
{
  const p = makePage({ live: false, userId: 7 });
  const fetch = makeFetch(releases, { onCall: () => p.document.body.classList.add("course-live") });
  const result = await mod.initWhatsNew({ document: p.document, fetch, storage: makeStorage() });
  p.link.click();
  out.wentLive = { result, ...snapshot(p) };
}
// 3. First Dashboard visit after the deploy: opens once; Got it marks seen.
const storage = makeStorage();
{
  const p = makePage({ live: false, userId: 7 });
  const fetch = makeFetch(releases);
  const result = await mod.initWhatsNew({ document: p.document, fetch, storage });
  out.first = {
    result,
    fetchUrl: fetch.calls[0],
    date: p.date.textContent,
    titles: p.list.children.map((li) => li.children[0].textContent),
    lines: p.list.children.map((li) => li.children[1].textContent),
    chips: p.list.children.map((li) => [li.children[2].children[0].className, li.children[2].children[0].textContent]),
    refs: p.list.children.map((li) => li.children[2].children[1].textContent),
    notYet: p.notYetText.textContent,
    notYetHidden: p.notYet.hidden,
    ...snapshot(p),
  };
  out.first.seenBeforeClose = storage.getItem("alc-whats-new:7");
  p.ok.click();
  out.first.openAfterOk = p.dialog.open;
  out.first.seenAfterOk = storage.getItem("alc-whats-new:7");
}
// 4. Reload: no auto-open; the link reopens; × closes.
{
  const p = makePage({ live: false, userId: 7 });
  const result = await mod.initWhatsNew({ document: p.document, fetch: makeFetch(releases), storage });
  const before = snapshot(p);
  p.link.click();
  const afterLink = snapshot(p);
  p.x.click();
  out.reload = { result, before, afterLink, openAfterX: p.dialog.open };
}
// 5. Another staff user in the same browser has their own key.
{
  const p = makePage({ live: false, userId: 8 });
  const result = await mod.initWhatsNew({ document: p.document, fetch: makeFetch(releases), storage });
  out.otherUser = { result, ...snapshot(p) };
}
// 6. No releases / failed fetch / footer removed / no dialog.
{
  const p = makePage({ live: false, userId: 7 });
  const result = await mod.initWhatsNew({ document: p.document, fetch: makeFetch({ releases: [] }), storage: makeStorage() });
  out.empty = { result, ...snapshot(p) };
}
{
  const p = makePage({ live: false, userId: 7 });
  const result = await mod.initWhatsNew({ document: p.document, fetch: makeFetch(null, { ok: false }), storage: makeStorage() });
  out.failed = { result, ...snapshot(p) };
}
{
  const p = makePage({ live: false, userId: 7 });
  const trimmed = { releases: [{ ...releases.releases[0] }] };
  delete trimmed.releases[0].not_yet;
  delete trimmed.releases[0].not_yet_refs;
  const result = await mod.initWhatsNew({ document: p.document, fetch: makeFetch(trimmed), storage: makeStorage() });
  out.noFooter = { result, notYetHidden: p.notYet.hidden, ...snapshot(p) };
}
{
  const p = makePage({ live: false, userId: 7, withDialog: false });
  const fetch = makeFetch(releases);
  const result = await mod.initWhatsNew({ document: p.document, fetch, storage: makeStorage() });
  out.noDialog = { result, fetches: fetch.calls.length };
}
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node is required for the whats_new.js harness")
class WhatsNewScriptTests(unittest.TestCase):
    """The real whats_new.js in node, with a fake DOM and localStorage."""

    @classmethod
    def setUpClass(cls) -> None:
        payload = {
            "module": WHATS_NEW_JS.resolve().as_uri(),
            "releases": json.loads(RELEASES_JSON.read_text(encoding="utf-8")),
        }
        done = subprocess.run(
            [shutil.which("node"), "--input-type=module", "-e", HARNESS],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if done.returncode != 0:
            raise AssertionError(done.stderr or done.stdout)
        cls.out = json.loads(done.stdout.strip().splitlines()[-1])
        cls.newest = _releases()[0]

    def test_never_renders_or_opens_on_course_live(self) -> None:
        """Hard rule, JS side: no fetch, no render, no open, link stays hidden."""
        live = self.out["live"]
        self.assertEqual(live["result"], "live", live)
        self.assertEqual(live["fetches"], 0, live)
        self.assertEqual(live["showModal"], 0, live)
        self.assertFalse(live["open"], live)
        self.assertEqual(live["items"], 0, live)
        self.assertTrue(live["linkHidden"], live)
        self.assertEqual(live["stored"], {}, live)

    def test_going_live_while_loading_still_never_opens(self) -> None:
        went = self.out["wentLive"]
        self.assertEqual(went["result"], "live", went)
        self.assertEqual(went["showModal"], 0, went)
        self.assertEqual(went["items"], 0, went)
        self.assertTrue(went["linkHidden"], went)

    def test_first_visit_opens_the_newest_release_in_wonder_order(self) -> None:
        first = self.out["first"]
        items = self.newest["items"]
        self.assertEqual(first["result"], "opened", first)
        self.assertEqual(first["showModal"], 1, first)
        self.assertTrue(first["fetchUrl"].startswith("/static/whats-new/releases.json"), first)
        self.assertEqual(first["titles"], [i["title"] for i in items])
        self.assertEqual(first["lines"], [i["line"] for i in items])
        self.assertEqual(first["refs"], [i["refs"] for i in items])
        self.assertEqual(
            first["chips"],
            [
                ["whats-new-aud is-both" if i["audience"].startswith("Both") else "whats-new-aud", i["audience"]]
                for i in items
            ],
        )
        self.assertFalse(first["linkHidden"], first)
        if self.newest.get("not_yet"):
            self.assertEqual(first["notYet"], self.newest["not_yet"])
            self.assertFalse(first["notYetHidden"])

    def test_got_it_marks_seen_and_reload_does_not_reopen(self) -> None:
        first, reload = self.out["first"], self.out["reload"]
        self.assertIsNone(first["seenBeforeClose"], first)
        self.assertFalse(first["openAfterOk"], first)
        self.assertEqual(first["seenAfterOk"], self.newest["id"], first)
        self.assertEqual(reload["result"], "seen", reload)
        self.assertEqual(reload["before"]["showModal"], 0, reload)
        self.assertTrue(reload["afterLink"]["open"], reload)
        self.assertFalse(reload["openAfterX"], reload)

    def test_seen_state_is_per_staff_user(self) -> None:
        self.assertEqual(self.out["otherUser"]["result"], "opened", self.out["otherUser"])

    def test_no_release_failed_fetch_or_no_dialog_does_nothing(self) -> None:
        for key, result in (("empty", "empty"), ("failed", "error")):
            got = self.out[key]
            self.assertEqual(got["result"], result, got)
            self.assertEqual(got["showModal"], 0, got)
            self.assertTrue(got["linkHidden"], got)
        self.assertEqual(self.out["noDialog"], {"result": "absent", "fetches": 0})

    def test_footer_drops_out_cleanly_when_removed(self) -> None:
        got = self.out["noFooter"]
        self.assertEqual(got["result"], "opened", got)
        self.assertTrue(got["notYetHidden"], got)

    def test_script_writes_text_only(self) -> None:
        src = WHATS_NEW_JS.read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", src.replace("never innerHTML", ""))
        self.assertIn('classList.contains("course-live")', src)


if __name__ == "__main__":
    unittest.main()

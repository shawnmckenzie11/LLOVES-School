#!/usr/bin/env python3
"""MCK-124 slice 1 + MCK-182: staff "What's new" data file and Dashboard panel.

Three groups:

* the ``releases.json`` limits from the spec (Wonder's copy rules). MCK-182
  schema 2 adds one ``{id, sha, deployed_at, day, items: [{text, audience,
  refs}]}`` release per deploy with notes; the Oct 2 legacy release keeps
  ``title`` + ``line``;
* server pages: the panel is on the staff Dashboard only, never on the
  Live tab (``body.course-live``), never on a student page, and held off
  the Dashboard while any of the teacher's classes is live;
* the real ``whats_new.js`` in node with a small fake DOM (MCK-182 slice 2):
  unseen items only, at most once a day, a plain dot, the full list, legacy
  seen values, no refs on screen; on ``body.course-live`` it renders
  nothing, fetches nothing and never opens, not even from the button.
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

sys.path.insert(0, str(REPO_ROOT / ".github" / "scripts"))
import whats_new as whats_new_script  # noqa: E402

RELEASES_JSON = LMS_DIR / "static" / "whats-new" / "releases.json"
WHATS_NEW_JS = LMS_DIR / "static" / "whats_new.js"
TEMPLATES = LMS_DIR / "templates"
AUDIENCES = {"Teacher", "Both", "Both (projector)"}
LEGACY_ID = re.compile(r"^\d{4}-\d{2}-\d{2}(-\d+)?$")
SHA_ID = re.compile(r"^[0-9a-f]{7}$")
# Generated lines are held to 30 words by .github/scripts/whats_new.py. The
# hand-written v216-v218 backfill (Wonder v1.3) runs to 39; trim it here.
MAX_BACKFILL_WORDS = 40
# Markers that only the What's new panel puts on a page.
PANEL_MARKERS = (
    "whats-new-dialog",
    "whats_new.js",
    "whats-new-open",
    "whats-new/releases.json",
    "/api/staff/whats-new",
)


def _releases() -> list[dict]:
    """Return the ``releases`` list from the data file."""
    data = json.loads(RELEASES_JSON.read_text(encoding="utf-8"))
    return data["releases"]


def _is_legacy(release: dict) -> bool:
    """MCK-124 slice 1 release (title + line items, no ``deployed_at``)."""
    return "deployed_at" not in release


class ReleaseNotesDataTests(unittest.TestCase):
    """``releases.json`` keeps to the spec's limits."""

    def test_schema_2(self) -> None:
        data = json.loads(RELEASES_JSON.read_text(encoding="utf-8"))
        self.assertEqual(data.get("schema"), 2)

    def test_ids_unique_well_formed_and_newest_first(self) -> None:
        releases = _releases()
        self.assertTrue(releases, "at least one release")
        ids = [r["id"] for r in releases]
        self.assertEqual(len(ids), len(set(ids)), ids)
        for release in releases:
            self.assertRegex(release["id"], LEGACY_ID if _is_legacy(release) else SHA_ID)
        keys = [whats_new_script.sort_key(r) for r in releases]
        self.assertEqual(keys, sorted(keys, reverse=True), "newest release first")

    def test_deploy_releases_keep_to_the_contract(self) -> None:
        """Schema 2: id = sha[:7], ISO deployed_at in Toronto, day = its date."""
        deploys = [r for r in _releases() if not _is_legacy(r)]
        self.assertEqual([r.get("fly") for r in deploys], ["v218", "v217", "v216"])
        for release in deploys:
            with self.subTest(release=release["id"]):
                self.assertRegex(release["sha"], r"^[0-9a-f]{40}$")
                self.assertEqual(release["id"], release["sha"][:7])
                self.assertRegex(release["deployed_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}-0[45]:00$")
                self.assertEqual(release["day"], release["deployed_at"][:10])
                self.assertTrue(1 <= len(release["items"]) <= 7)
                for item in release["items"]:
                    text = item["text"]
                    self.assertEqual(set(item), {"text", "audience", "refs"})
                    self.assertIn(item["audience"], {"Teacher", "Both"})
                    self.assertTrue(item["refs"].strip())
                    self.assertLessEqual(len(text.split()), MAX_BACKFILL_WORDS, text)
                    self.assertNotRegex(text, r"[<>*`]", "plain text only")
                    self.assertNotRegex(text, r"MCK-\d|#\d", "no ids in teacher text")
                    self.assertNotIn("Import from Bank", text, "UI label is 'Import from bank'")
                    problems = [
                        p for p in whats_new_script.note_problems(text) if "words" not in p
                    ]
                    self.assertEqual(problems, [], text)

    def test_backfill_maps_to_the_fly_versions(self) -> None:
        """v216-v218 SHAs from fly releases + Deploy runs (MCK-182 research)."""
        by_fly = {r["fly"]: r for r in _releases() if r.get("fly")}
        self.assertEqual(by_fly["v216"]["id"], "e640589")
        self.assertEqual(by_fly["v217"]["id"], "5bd4edf")
        self.assertEqual(by_fly["v218"]["id"], "65d69db")
        self.assertEqual(len(by_fly["v216"]["items"]), 4)

    def test_items_keep_to_wonder_limits(self) -> None:
        for release in _releases():
            if not _is_legacy(release):
                continue
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
        """The footer is its own field; absent or empty means no footer."""
        for release in _releases():
            if "not_yet" not in release:
                self.assertNotIn("not_yet_refs", release)
                continue
            self.assertIsInstance(release["not_yet"], str)
            self.assertIsInstance(release.get("not_yet_refs", ""), str)
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
        # Wonder dropped the #214 footer (#214 merged before this ships).
        self.assertEqual(oldest.get("not_yet", ""), "")
        self.assertEqual(oldest.get("not_yet_refs", ""), "")


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
        self.assertIn('data-releases-src="/api/staff/whats-new"', html)
        # MCK-182 slice 2: no build SHA, ticket id or PR number on screen.
        self.assertNotIn("Build ", html)
        self.assertIn("data-whats-new-dot", html)
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

    def _end_live(self) -> None:
        """End Live Class through the staff route (the real Dashboard form)."""
        ended = self.staff.post(
            f"/staff/class/{self.class_id}/end-live",
            data={"end_options": "1"},
            follow_redirects=False,
        )
        self.assertEqual(ended.status_code, 302)
        self.assertIsNone(self.school.get_active_live_session_for_class(self.class_id))

    def test_dashboard_holds_the_panel_while_a_class_is_live(self) -> None:
        """Run Live Class: no panel on /staff; End Live Class: it is back."""
        self.assertIn("whats-new-dialog", self.staff.get("/staff").get_data(as_text=True))
        run = self.staff.post(f"/staff/class/{self.class_id}/run-live", follow_redirects=False)
        self.assertEqual(run.status_code, 302)
        self.assertIsNotNone(self.school.get_active_live_session_for_class(self.class_id))
        during = self.staff.get("/staff")
        self.assertEqual(during.status_code, 200)
        self.assertNoPanel(during.get_data(as_text=True), "/staff while a class is live")
        self._end_live()
        after = self.staff.get("/staff").get_data(as_text=True)
        self.assertIn('<dialog id="whats-new-dialog"', after)
        self.assertIn('id="whats-new-open"', after)
        self.assertIn('src="/static/whats_new.js?v=', after)

    def test_dashboard_holds_the_panel_when_someone_else_runs_my_class(self) -> None:
        """A session on this teacher's class counts even if another user started it."""
        other = self.school.register_staff("helper@gmail.com")
        self.school.start_live_class_session(self.class_id, int(other["id"]))
        self.assertNoPanel(self.staff.get("/staff").get_data(as_text=True), "/staff (my class, other starter)")

    def _signed_in_staff(self, email: str):
        """Return a test client signed in as a fresh staff user with a class.

        Args:
            email: New staff email.

        Returns:
            ``(client, class_id)``.
        """
        user = self.school.register_staff(email)
        offering = self.school.assign_course(
            teacher_user_id=int(user["id"]), ontario_code="MCF3M"
        )
        client = self.app.test_client()
        client.get("/auth/google?portal=staff")
        client.get(f"/auth/google/callback?email={email}&name=O")
        client.post(
            "/verify-email",
            data={"code": self.school.get_user_by_email(email)["verification_code"]},
        )
        created = client.post(
            "/api/staff/classes",
            json={
                "offering_id": offering["id"],
                "days": "T/Th/F",
                "time": "9:15am",
                "codenames": ["Cedar"],
            },
        )
        self.assertEqual(created.status_code, 200, created.get_json())
        return client, int(created.get_json()["class"]["id"])

    def test_another_teachers_live_class_does_not_hold_my_panel(self) -> None:
        other, other_class_id = self._signed_in_staff("other@gmail.com")
        run = other.post(f"/staff/class/{other_class_id}/run-live", follow_redirects=False)
        self.assertEqual(run.status_code, 302)
        self.assertIsNotNone(self.school.get_active_live_session_for_class(other_class_id))
        # The other teacher's own Dashboard is held...
        self.assertNoPanel(other.get("/staff").get_data(as_text=True), "other teacher's /staff")
        # ...mine is not.
        html = self.staff.get("/staff").get_data(as_text=True)
        self.assertIn('<dialog id="whats-new-dialog"', html)
        self.assertIn('id="whats-new-open"', html)

    def test_course_tabs_never_have_the_panel(self) -> None:
        """Dashboard only (Mobbin S2): the course tabs carry no panel."""
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
    this.className = ""; this._text = ""; this.hidden = "hidden" in attrs; this.dataset = {}; this.open = false;
  }
  get textContent() { return this._text + this.children.map((c) => (typeof c === "string" ? c : c.textContent)).join(""); }
  set textContent(v) { this._text = String(v); this.children = []; }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  hasAttribute(k) { return k in this.attrs; }
  addEventListener(t, fn) { (this.listeners[t] ||= []).push(fn); }
  dispatch(t) { for (const fn of this.listeners[t] || []) fn({ type: t, preventDefault() {} }); }
  click() { this.dispatch("click"); }
  append(...els) { this.children.push(...els); }
  appendChild(el) { this.children.push(el); return el; }
  replaceChildren() { this.children = []; this._text = ""; }
  all() { return this.children.filter((c) => typeof c !== "string").flatMap((c) => [c, ...c.all()]); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) {
    const attr = /^\[([\w-]+)\]$/.exec(sel);
    if (attr) return this.all().filter((c) => c.hasAttribute(attr[1]));
    const cls = /^\.([\w-]+)$/.exec(sel);
    if (cls) return this.all().filter((c) => c.className.split(" ").includes(cls[1]));
    return this.all().filter((c) => c.tag === sel);
  }
  set innerHTML(_v) { throw new Error("innerHTML must not be used"); }
}

function makePage({ live = false, userId = 7, withDialog = true } = {}) {
  const counters = { showModal: 0 };
  const dialog = new El("dialog", { id: "whats-new-dialog", "data-releases-src": "/api/staff/whats-new" });
  dialog.showModal = () => { counters.showModal += 1; dialog.open = true; };
  dialog.close = () => { if (!dialog.open) return; dialog.open = false; dialog.dispatch("close"); };
  const sub = new El("span", { "data-whats-new-sub": "" });
  const x = new El("button", { "data-whats-new-close": "" });
  const body = new El("div", { "data-whats-new-list": "" });
  const all = new El("button", { "data-whats-new-all": "", hidden: "" });
  const ok = new El("button", { "data-whats-new-close": "" });
  dialog.append(sub, x, body, all, ok);
  const dot = new El("span", { "data-whats-new-dot": "", hidden: "" });
  const link = new El("button", { id: "whats-new-open", hidden: "", "aria-label": "What's new" });
  link.append(dot);
  const page = new El("body");
  page.classList = new ClassList(live ? ["staff-shell", "course-live"] : ["staff-shell", "staff-home"]);
  if (userId) page.dataset.userId = String(userId);
  const byId = { "whats-new-open": link };
  if (withDialog) byId["whats-new-dialog"] = dialog;
  const document = {
    body: page,
    getElementById: (id) => byId[id] || null,
    createElement: (tag) => new El(tag),
    createTextNode: (t) => String(t),
  };
  return { document, dialog, sub, body, all, ok, x, link, dot, counters };
}

function makeStorage(seed = {}) {
  const data = { ...seed };
  return { data, getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); } };
}

function makeFetch(payload, { ok = true, onCall } = {}) {
  const calls = [];
  const fn = async (url) => { calls.push(url); if (onCall) onCall(); return { ok, json: async () => payload }; };
  fn.calls = calls;
  return fn;
}

const view = (p) => ({
  mode: p.dialog.getAttribute("data-mode"),
  showModal: p.counters.showModal,
  open: p.dialog.open,
  sub: p.sub.textContent,
  days: p.body.querySelectorAll(".whats-new-day-head").map((h) => h.textContent),
  lines: p.body.querySelectorAll(".whats-new-item-line").map((l) => l.textContent),
  titles: p.body.querySelectorAll(".whats-new-item-title").map((l) => l.textContent),
  chips: p.body.querySelectorAll(".whats-new-aud").map((c) => c.textContent),
  tags: p.body.querySelectorAll(".whats-new-tag").length,
  more: p.body.querySelectorAll(".whats-new-more").map((b) => b.textContent),
  earlier: p.body.querySelectorAll(".whats-new-earlier").map((d) => d.children[0].textContent),
  earlierRows: p.body.querySelectorAll(".whats-new-earlier-day").map((d) => d.children[0].textContent),
  allHidden: p.all.hidden,
  linkHidden: p.link.hidden,
  dotHidden: p.dot.hidden,
  aria: p.link.getAttribute("aria-label"),
  text: p.dialog.textContent,
});

const payload = input.payload;
const NOON_OCT4 = new Date("2026-10-04T16:00:00Z");
const NOON_OCT5 = new Date("2026-10-05T16:00:00Z");
const out = {};

// 1. Live class page: nothing at all, even with unseen items and a click.
{
  const p = makePage({ live: true });
  const fetch = makeFetch(payload);
  const storage = makeStorage();
  const result = await mod.initWhatsNew({ document: p.document, fetch, storage, now: NOON_OCT4 });
  p.link.click();
  out.live = { result, fetches: fetch.calls.length, stored: storage.data, ...view(p) };
}
// 2. The page goes live while the history loads: still nothing.
{
  const p = makePage();
  const fetch = makeFetch(payload, { onCall: () => p.document.body.classList.add("course-live") });
  const result = await mod.initWhatsNew({ document: p.document, fetch, storage: makeStorage(), now: NOON_OCT4 });
  p.link.click();
  out.wentLive = { result, ...view(p) };
}
// 3. First Dashboard visit: unseen-only pop-up, 6 items, "+n more", dot.
const storage = makeStorage();
{
  const p = makePage();
  const fetch = makeFetch(payload);
  const result = await mod.initWhatsNew({ document: p.document, fetch, storage, now: NOON_OCT4 });
  out.first = { result, fetchUrl: fetch.calls[0], popped: storage.getItem("alc-whats-new:7:popped"), ...view(p) };
  p.body.querySelector(".whats-new-more").click();
  out.firstMore = view(p);
  p.ok.click();
  out.firstClosed = { seen: storage.getItem("alc-whats-new:7"), ...view(p) };
}
// 4. Reload the same day: no pop-up, no dot; the button opens the full list.
{
  const p = makePage();
  const result = await mod.initWhatsNew({ document: p.document, fetch: makeFetch(payload), storage, now: NOON_OCT4 });
  const before = view(p);
  p.link.click();
  const afterLink = view(p);
  p.x.click();
  out.reload = { result, before, afterLink, openAfterX: p.dialog.open };
}
// 5. A second deploy the same day: only the dot (once a day), "New" in the list.
const later = {
  schema: 2,
  releases: [
    { id: "eeeeeee", deployed_at: "2026-10-04T22:00:00+00:00", day: "2026-10-04",
      items: [{ text: "In Run Live Class, a later same-day line.", audience: "Teacher" }] },
    ...payload.releases,
  ],
};
{
  const p = makePage();
  const result = await mod.initWhatsNew({ document: p.document, fetch: makeFetch(later), storage, now: new Date("2026-10-04T23:00:00Z") });
  const before = view(p);
  p.link.click();
  const list = view(p);
  p.ok.click();
  out.sameDay = { result, before, list, after: view(p) };
}
// 6. Next day, another unseen deploy: it pops again with just that item.
const nextDay = {
  schema: 2,
  releases: [
    { id: "fffffff", deployed_at: "2026-10-05T14:00:00+00:00", day: "2026-10-05",
      items: [{ text: "In Import from bank, a next-day line.", audience: "Both" }] },
    ...later.releases,
  ],
};
{
  const p = makePage();
  const result = await mod.initWhatsNew({ document: p.document, fetch: makeFetch(nextDay), storage, now: NOON_OCT5 });
  out.nextDay = { result, ...view(p) };
}
// 7. Another staff user in the same browser has their own keys.
{
  const p = makePage({ userId: 8 });
  const result = await mod.initWhatsNew({ document: p.document, fetch: makeFetch(payload), storage, now: NOON_OCT4 });
  out.otherUser = { result, ...view(p) };
}
// 8. Legacy seen values: a bare date (MCK-124) and a slice 1 release id.
for (const [key, seed] of [["legacyDate", "2026-10-02"], ["legacyId", payload.releases[0].id], ["popped", null]]) {
  const p = makePage();
  const s = makeStorage(seed ? { "alc-whats-new:7": seed } : { "alc-whats-new:7:popped": "2026-10-04" });
  const result = await mod.initWhatsNew({ document: p.document, fetch: makeFetch(payload), storage: s, now: NOON_OCT4 });
  out[key] = { result, ...view(p) };
}
// 8b. MCK-192: ids are deploy times now; a stored SHA is asked about once.
{
  const p = makePage();
  const s = makeStorage({ "alc-whats-new:7": "65d69dbABC" });
  const opaque = { ...payload, legacy_seen_at: payload.releases[0].deployed_at,
    releases: payload.releases.map((r) => ({ ...r, id: r.deployed_at })) };
  const fetch = makeFetch(opaque);
  const result = await mod.initWhatsNew({ document: p.document, fetch, storage: s, now: NOON_OCT4 });
  out.legacyMigrated = { result, url: fetch.calls[0], seen: s.getItem("alc-whats-new:7"), ...view(p) };
}
// 9. Housekeeping release (no items) on top of a seen history: silent.
{
  const p = makePage();
  const s = makeStorage({ "alc-whats-new:7": payload.releases[0].deployed_at });
  const hk = { releases: [{ id: "aaaaaaa", deployed_at: "2026-10-04T20:00:00+00:00", day: "2026-10-04", items: [] },
    { id: "bbbbbbb", deployed_at: "2026-10-04T20:30:00+00:00", day: "2026-10-04", items: [null, { text: "  " }, "x"] },
    ...payload.releases] };
  const result = await mod.initWhatsNew({ document: p.document, fetch: makeFetch(hk), storage: s, now: NOON_OCT4 });
  out.housekeeping = { result, ...view(p) };
}
// 10. Empty history / failed fetch / no dialog.
{
  const p = makePage();
  const result = await mod.initWhatsNew({ document: p.document, fetch: makeFetch({ releases: [] }), storage: makeStorage(), now: NOON_OCT4 });
  out.empty = { result, ...view(p) };
}
{
  const p = makePage();
  const result = await mod.initWhatsNew({ document: p.document, fetch: makeFetch(null, { ok: false }), storage: makeStorage(), now: NOON_OCT4 });
  out.failed = { result, ...view(p) };
}
{
  const p = makePage({ withDialog: false });
  const fetch = makeFetch(payload);
  const result = await mod.initWhatsNew({ document: p.document, fetch, storage: makeStorage(), now: NOON_OCT4 });
  out.noDialog = { result, fetches: fetch.calls.length };
}
// 11. Full list: 3 open days, then "Earlier" back 30 days; refs scrubbed.
{
  const day = (d, n, extra = {}) => ({ id: `d${d}`, deployed_at: `${d}T15:00:00+00:00`, day: d,
    items: Array.from({ length: n }, (_, i) => ({ text: `In Run Live Class, line ${i + 1} on ${d}.`, audience: "Teacher", ...extra })) });
  const long = { releases: [
    day("2026-10-04", 1, { text: "In Run Live Class (#243 · MCK-171), see MCK-9 and #12 at abc1234.", audience: "Both" }),
    day("2026-10-03", 1), day("2026-10-01", 2), day("2026-09-29", 1), day("2026-09-28", 2), day("2026-09-10", 1),
    day("2026-08-20", 3),
  ] };
  const p = makePage();
  const s = makeStorage({ "alc-whats-new:7": "2026-10-02T12:00:00-04:00", "alc-whats-new:7:popped": "2026-10-04" });
  const result = await mod.initWhatsNew({ document: p.document, fetch: makeFetch(long), storage: s, now: NOON_OCT4 });
  p.link.click();
  out.earlier = { result, ...view(p) };
}
// 12. Helpers.
out.helpers = {
  header: mod.formatDayHeader("2026-10-04"),
  headerSat: mod.formatDayHeader("2026-10-03"),
  torontoLateNight: mod.torontoDay(new Date("2026-10-05T03:30:00Z")),
  minus: mod.dayMinus("2026-10-04", 30),
  scrub: mod.scrubRefs("Export to CSV (#202 (+#200) · MCK-46) now works, see #9 and MCK-1 at 41162ea."),
};
console.log(JSON.stringify(out));
"""


def _teacher_payload() -> dict:
    """What ``/api/staff/whats-new`` sends for the committed file alone."""
    import whats_new_store

    return whats_new_store.teacher_payload(_releases())


REF_RE = re.compile(r"MCK-\d|#\d|\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")


@unittest.skipUnless(shutil.which("node"), "node is required for the whats_new.js harness")
class WhatsNewScriptTests(unittest.TestCase):
    """The real whats_new.js in node, with a fake DOM and localStorage."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = _teacher_payload()
        payload = {"module": WHATS_NEW_JS.resolve().as_uri(), "payload": cls.payload}
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
        cls.all_items = [i for r in cls.payload["releases"] for i in r["items"]]

    def test_never_renders_or_opens_on_course_live(self) -> None:
        """Hard rule, JS side: no fetch, no render, no open, button stays hidden."""
        live = self.out["live"]
        self.assertEqual(live["result"], "live", live)
        self.assertEqual(live["fetches"], 0, live)
        self.assertEqual(live["showModal"], 0, live)
        self.assertFalse(live["open"], live)
        self.assertEqual(live["lines"], [], live)
        self.assertTrue(live["linkHidden"], live)
        self.assertTrue(live["dotHidden"], live)
        self.assertEqual(live["stored"], {}, live)

    def test_going_live_while_loading_still_never_opens(self) -> None:
        went = self.out["wentLive"]
        self.assertEqual(went["result"], "live", went)
        self.assertEqual(went["showModal"], 0, went)
        self.assertEqual(went["lines"], [], went)
        self.assertTrue(went["linkHidden"], went)

    def test_first_visit_pops_unseen_items_grouped_by_day_max_six(self) -> None:
        first = self.out["first"]
        total = len(self.all_items)
        self.assertEqual(first["result"], "opened", first)
        self.assertEqual(first["fetchUrl"], "/api/staff/whats-new")
        self.assertEqual(first["showModal"], 1, first)
        self.assertEqual(first["mode"], "popup")
        self.assertEqual(first["sub"], f"{total} changes")
        self.assertEqual(len(first["lines"]), 6, first)
        self.assertEqual(first["lines"], [i.get("text") or i.get("line") for i in self.all_items][:6])
        self.assertEqual(first["days"], ["Sun Oct 4", "Sat Oct 3"])
        self.assertEqual(first["more"], [f"+{total - 6} more"])
        self.assertEqual(first["tags"], 0, "no New tags in the pop-up")
        self.assertFalse(first["allHidden"], "See all updates shows in the pop-up")
        self.assertEqual(first["popped"], "2026-10-04")
        self.assertFalse(first["dotHidden"])
        self.assertEqual(first["aria"], f"What's new, {total} new")
        self.assertFalse(first["linkHidden"])

    def test_only_both_items_get_a_chip_and_it_reads_students_see_this_too(self) -> None:
        first = self.out["first"]
        shown = self.all_items[:6]
        self.assertEqual(first["chips"], ["Students see this too"] * sum(i["audience"] == "Both" for i in shown))

    def test_more_opens_the_full_list_with_new_tags(self) -> None:
        more = self.out["firstMore"]
        self.assertEqual(more["mode"], "all")
        self.assertEqual(more["sub"], "All updates")
        self.assertEqual(more["days"], ["Sun Oct 4", "Sat Oct 3", "Fri Oct 2"])
        self.assertEqual(len(more["lines"]), len(self.all_items))
        self.assertEqual(more["tags"], len(self.all_items))
        self.assertEqual(more["titles"][0], "Group work is one switch")
        self.assertTrue(more["allHidden"])
        self.assertEqual(more["earlier"], [])

    def test_closing_marks_everything_seen_and_clears_the_dot(self) -> None:
        closed = self.out["firstClosed"]
        self.assertFalse(closed["open"])
        self.assertEqual(closed["seen"], self.payload["releases"][0]["deployed_at"])
        self.assertTrue(closed["dotHidden"])
        self.assertEqual(closed["aria"], "What's new")
        reload = self.out["reload"]
        self.assertEqual(reload["result"], "seen", reload)
        self.assertEqual(reload["before"]["showModal"], 0)
        self.assertTrue(reload["before"]["dotHidden"])
        self.assertTrue(reload["afterLink"]["open"])
        self.assertEqual(reload["afterLink"]["mode"], "all")
        self.assertEqual(reload["afterLink"]["tags"], 0, "nothing is New after closing")
        self.assertFalse(reload["openAfterX"])

    def test_second_deploy_the_same_day_only_lights_the_dot(self) -> None:
        same = self.out["sameDay"]
        self.assertEqual(same["result"], "dot", same)
        self.assertEqual(same["before"]["showModal"], 0)
        self.assertFalse(same["before"]["dotHidden"])
        self.assertEqual(same["before"]["aria"], "What's new, 1 new")
        self.assertEqual(same["list"]["mode"], "all")
        self.assertEqual(same["list"]["tags"], 1)
        self.assertEqual(same["list"]["lines"][0], "NewIn Run Live Class, a later same-day line.")
        self.assertTrue(same["after"]["dotHidden"])

    def test_next_day_pops_again_with_only_the_new_item(self) -> None:
        nxt = self.out["nextDay"]
        self.assertEqual(nxt["result"], "opened", nxt)
        self.assertEqual(nxt["sub"], "1 change")
        self.assertEqual(nxt["days"], ["Mon Oct 5"])
        self.assertEqual(nxt["lines"], ["In Import from bank, a next-day line."])
        self.assertEqual(nxt["chips"], ["Students see this too"])
        self.assertEqual(nxt["more"], [])

    def test_seen_state_is_per_staff_user(self) -> None:
        self.assertEqual(self.out["otherUser"]["result"], "opened", self.out["otherUser"])

    def test_legacy_seen_values_carry_over(self) -> None:
        """A bare date reads as that day 23:59; a slice 1 id as that release."""
        legacy = self.out["legacyDate"]
        newer = [i for r in self.payload["releases"] if r["day"] > "2026-10-02" for i in r["items"]]
        self.assertEqual(legacy["result"], "opened")
        self.assertEqual(legacy["sub"], f"{len(newer)} changes")
        self.assertNotIn("Fri Oct 2", legacy["days"])
        self.assertEqual(self.out["legacyId"]["result"], "seen")
        self.assertTrue(self.out["legacyId"]["dotHidden"])

    def test_a_stored_sha_is_migrated_to_the_deploy_time_mck192(self) -> None:
        got = self.out["legacyMigrated"]
        self.assertEqual(got["url"], "/api/staff/whats-new?legacy_seen=65d69dbABC")
        self.assertEqual(got["result"], "seen", got)
        self.assertRegex(got["seen"], r"^\d{4}-\d{2}-\d{2}T")

    def test_already_popped_today_shows_the_dot_only(self) -> None:
        popped = self.out["popped"]
        self.assertEqual(popped["result"], "dot")
        self.assertEqual(popped["showModal"], 0)
        self.assertFalse(popped["dotHidden"])

    def test_housekeeping_release_gives_no_dot_and_no_pop_up(self) -> None:
        hk = self.out["housekeeping"]
        self.assertEqual(hk["result"], "seen", hk)
        self.assertEqual(hk["showModal"], 0)
        self.assertTrue(hk["dotHidden"])

    def test_no_history_failed_fetch_or_no_dialog_does_nothing(self) -> None:
        for key, result in (("empty", "empty"), ("failed", "error")):
            got = self.out[key]
            self.assertEqual(got["result"], result, got)
            self.assertEqual(got["showModal"], 0, got)
            self.assertTrue(got["linkHidden"], got)
        self.assertEqual(self.out["noDialog"], {"result": "absent", "fetches": 0})

    def test_full_list_has_three_open_days_then_earlier_back_30_days(self) -> None:
        got = self.out["earlier"]
        self.assertEqual(got["result"], "dot")
        self.assertEqual(got["days"], ["Sun Oct 4", "Sat Oct 3", "Thu Oct 1"])
        self.assertEqual(got["earlier"], ["Earlier"])
        self.assertEqual(got["earlierRows"], ["Tue Sep 29 · 1 change", "Mon Sep 28 · 2 changes", "Thu Sep 10 · 1 change"])
        self.assertNotIn("2026-08-20", got["text"])
        # Only releases after the stored seen time are New.
        self.assertEqual(got["tags"], 2)

    def test_no_refs_or_shas_reach_the_screen(self) -> None:
        for key in ("first", "firstMore", "earlier", "nextDay"):
            self.assertNotRegex(self.out[key]["text"], REF_RE, key)
        self.assertEqual(self.out["earlier"]["lines"][0], "NewIn Run Live Class, see and at.")
        self.assertEqual(self.out["helpers"]["scrub"], "Export to CSV now works, see and at.")

    def test_helpers(self) -> None:
        h = self.out["helpers"]
        self.assertEqual(h["header"], "Sun Oct 4")
        self.assertEqual(h["headerSat"], "Sat Oct 3")
        self.assertEqual(h["torontoLateNight"], "2026-10-04")
        self.assertEqual(h["minus"], "2026-09-04")

    def test_script_writes_text_only(self) -> None:
        src = WHATS_NEW_JS.read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", src)
        self.assertIn('classList.contains("course-live")', src)


if __name__ == "__main__":
    unittest.main()

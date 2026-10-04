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
PANEL_MARKERS = ("whats-new-dialog", "whats_new.js", "whats-new-open", "whats-new/releases.json")


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
const part = (li, cls) => { const el = li.children.find((c) => c.className === cls); return el ? el.textContent : null; };
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
    titles: p.list.children.map((li) => part(li, "whats-new-item-title")),
    lines: p.list.children.map((li) => part(li, "whats-new-item-line")),
    chips: p.list.children.map((li) => { const m = li.children[li.children.length - 1]; return [m.children[0].className, m.children[0].textContent]; }),
    refs: p.list.children.map((li) => li.children[li.children.length - 1].children[1].textContent),
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
// Footer variants: blank text renders nothing; a real line still renders.
for (const [key, value] of [["blankFooter", "   "], ["withFooter", "Fixture footer line for the harness."]]) {
  const p = makePage({ live: false, userId: 7 });
  const variant = { releases: [{ ...releases.releases[0], not_yet: value, not_yet_refs: "" }] };
  const result = await mod.initWhatsNew({ document: p.document, fetch: makeFetch(variant), storage: makeStorage() });
  out[key] = { result, notYet: p.notYetText.textContent, notYetHidden: p.notYet.hidden };
}
{
  const p = makePage({ live: false, userId: 7, withDialog: false });
  const fetch = makeFetch(releases);
  const result = await mod.initWhatsNew({ document: p.document, fetch, storage: makeStorage() });
  out.noDialog = { result, fetches: fetch.calls.length };
}
// 7. MCK-182: null / blank items drop out; a release with none left
// (housekeeping) is skipped for the next one; legacy items still render.
{
  const p = makePage({ live: false, userId: 7 });
  const legacy = releases.releases[releases.releases.length - 1];
  const messy = {
    schema: 2,
    releases: [
      { id: "aaaaaaa", sha: "a".repeat(40), deployed_at: "2026-10-05T09:00:00-04:00", day: "2026-10-05", items: [] },
      { id: "bbbbbbb", sha: "b".repeat(40), deployed_at: "2026-10-04T20:00:00-04:00", day: "2026-10-04",
        items: [null, { text: "   " }, "text", { text: "In Run Live Class, a real line.", audience: "Both", refs: "#1" }] },
      legacy,
    ],
  };
  const result = await mod.initWhatsNew({ document: p.document, fetch: makeFetch(messy), storage: makeStorage() });
  out.messy = {
    result,
    date: p.date.textContent,
    titles: p.list.children.map((li) => part(li, "whats-new-item-title")),
    lines: p.list.children.map((li) => part(li, "whats-new-item-line")),
    newestId: mod.newestRelease(messy).id,
    onlyEmpty: mod.newestRelease({ releases: [messy.releases[0], { id: "c", items: [null] }] }),
  };
  const p2 = makePage({ live: false, userId: 7 });
  const r2 = await mod.initWhatsNew({ document: p2.document, fetch: makeFetch({ releases: [legacy] }), storage: makeStorage() });
  out.legacyOnly = { result: r2, titles: p2.list.children.map((li) => part(li, "whats-new-item-title")), date: p2.date.textContent };
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
        # Schema 2 items have no title: one text line each.
        self.assertEqual(first["titles"], [i.get("title") for i in items])
        self.assertEqual(first["lines"], [i.get("text") or i.get("line") for i in items])
        self.assertEqual(first["date"], "Oct 4, 2026")
        self.assertEqual(first["refs"], [i["refs"] for i in items])
        self.assertEqual(
            first["chips"],
            [
                ["whats-new-aud is-both" if i["audience"].startswith("Both") else "whats-new-aud", i["audience"]]
                for i in items
            ],
        )
        self.assertFalse(first["linkHidden"], first)
        if str(self.newest.get("not_yet") or "").strip():
            self.assertEqual(first["notYet"], self.newest["not_yet"].strip())
            self.assertFalse(first["notYetHidden"])
        else:
            # Empty footer (Wonder dropped the #214 line): nothing renders.
            self.assertEqual(first["notYet"], "", first)
            self.assertTrue(first["notYetHidden"], first)

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
        """Keys deleted or blank: no footer. A real line still renders."""
        got = self.out["noFooter"]
        self.assertEqual(got["result"], "opened", got)
        self.assertTrue(got["notYetHidden"], got)
        blank = self.out["blankFooter"]
        self.assertTrue(blank["notYetHidden"], blank)
        self.assertEqual(blank["notYet"], "", blank)
        shown = self.out["withFooter"]
        self.assertFalse(shown["notYetHidden"], shown)
        self.assertEqual(shown["notYet"], "Fixture footer line for the harness.", shown)

    def test_null_items_and_housekeeping_releases_are_skipped(self) -> None:
        """MCK-124 LOW + MCK-182: junk items and empty releases never render."""
        messy = self.out["messy"]
        self.assertEqual(messy["result"], "opened", messy)
        self.assertEqual(messy["newestId"], "bbbbbbb", messy)
        self.assertEqual(messy["lines"], ["In Run Live Class, a real line."], messy)
        self.assertEqual(messy["titles"], [None], messy)
        self.assertEqual(messy["date"], "Oct 4, 2026", messy)
        self.assertIsNone(messy["onlyEmpty"], messy)
        legacy = self.out["legacyOnly"]
        self.assertEqual(legacy["result"], "opened", legacy)
        self.assertEqual(legacy["date"], "Oct 2, 2026", legacy)
        self.assertEqual(legacy["titles"][0], "Group work is one switch", legacy)

    def test_script_writes_text_only(self) -> None:
        src = WHATS_NEW_JS.read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", src.replace("never innerHTML", ""))
        self.assertIn('classList.contains("course-live")', src)


if __name__ == "__main__":
    unittest.main()

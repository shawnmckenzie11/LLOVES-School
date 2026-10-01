#!/usr/bin/env python3
"""MCK-77 H1: an auto-picked Course deck must not stick across Set Class slots.

Ops repro on #187 (574ada0): Set Class on an empty slot auto-picks Course
deck. The teacher then switches Live class to M1 C3, which already has a
deck. The stale Course pick stayed on, and confirming POSTed
``deck-seed {"mode": "course", "source_module": "M2", "source_slot": "C1"}``
over the existing M1 C3 deck.

These tests load the real ``static/staff_ap.js`` in node (DOM and fetch
stubbed) and drive the real change listeners, then check the server-side
``keep_existing`` guard.

Ops smoke (:8789 / :8790):

1. Staff portal, a class with a saved M1 C3 deck. Run Live Class.
2. Set Class: Module M1, Live class C1. Course deck is auto-selected.
3. Switch Live class to C3. Use current is selected, not Course deck.
4. Confirm. No ``deck-seed`` POST with ``mode: course``; M1 C3 is unchanged.
5. Back on C1, click Course deck yourself, switch to C3: Course deck stays.
"""

from __future__ import annotations

import json
import os
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

NODE = shutil.which("node")

# Loads the real staff_ap.js with its /static imports rewritten to file URLs.
# Class 5: M1 C1 is empty with no earlier challenge, M1 C3 already has a deck,
# M1 C4 has an earlier deck (M1 C3). The course list holds M1 C3 and M2 C1.
STAFF_AP_HARNESS = r"""import { readFileSync, writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const staticDir = process.env.LLOVES_LMS_STATIC.replace(/\/$/, '');
class HTMLElement {}
class HTMLInputElement extends HTMLElement {}
class HTMLSelectElement extends HTMLElement {}
class HTMLButtonElement extends HTMLElement {}
class HTMLCanvasElement extends HTMLElement {}
class HTMLIFrameElement extends HTMLElement {}
class HTMLTextAreaElement extends HTMLElement {}
class Element {}
class HTMLDialogElement extends HTMLElement {}
class HTMLFormElement extends HTMLElement {}
class HTMLImageElement extends HTMLElement {}
class HTMLVideoElement extends HTMLElement {}
class HTMLAnchorElement extends HTMLElement {}
class HTMLOptionElement extends HTMLElement {}
class HTMLDivElement extends HTMLElement {}
class HTMLSpanElement extends HTMLElement {}
class HTMLLabelElement extends HTMLElement {}
class HTMLDetailsElement extends HTMLElement {}
class HTMLTemplateElement extends HTMLElement {}
class HTMLMediaElement extends HTMLElement {}
class HTMLAudioElement extends HTMLElement {}
class Node {}
Object.assign(globalThis, { Node, HTMLDialogElement, HTMLFormElement, HTMLImageElement, HTMLVideoElement, HTMLAnchorElement, HTMLOptionElement, HTMLDivElement, HTMLSpanElement, HTMLLabelElement, HTMLDetailsElement, HTMLTemplateElement, HTMLMediaElement, HTMLAudioElement, HTMLElement, HTMLInputElement, HTMLSelectElement, HTMLButtonElement,
  HTMLCanvasElement, HTMLIFrameElement, HTMLTextAreaElement, Element });

function makeEl(id, proto = HTMLElement.prototype) {
  const listeners = {};
  const node = {
    id, hidden: false, value: '', checked: false, disabled: false, name: '',
    dataset: {}, style: {}, title: '', textContent: '', innerHTML: '',
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    children: [], options: [],
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    removeEventListener() {},
    setAttribute() {}, removeAttribute() {}, getAttribute() { return null; },
    closest() { return null; }, querySelector() { return null; }, querySelectorAll() { return []; },
    appendChild(c) { node.children.push(c); return c; },
    replaceChildren() { node.options = []; node.children = []; },
    add(opt) { node.options.push(opt); if (node.options.length === 1) node.value = opt.value; },
    append(...kids) { for (const c of kids) { if (c && c.value !== undefined && !c.children) node.add(c); else node.children.push(c); } },
    dispatch(type, target = node) { for (const fn of listeners[type] || []) fn({ type, target, preventDefault() {} }); },
  };
  Object.setPrototypeOf(node, proto);
  return node;
}
const els = {};
function el(id, proto) { els[id] = makeEl(id, proto); return els[id]; }
el('ap-root').dataset.classId = '5';
el('live-deck-seed');
for (const mode of ['previous', 'course', 'current']) {
  const input = el(`live-deck-seed-${mode}`, HTMLInputElement.prototype);
  input.name = 'live-deck-seed';
  input.value = mode;
  let on = false;
  Object.defineProperty(input, 'checked', {
    get() { return on; },
    set(v) { if (v) inputs().forEach((i) => { if (i !== input) i.checked = false; }); on = Boolean(v); },
  });
}
el('live-deck-seed-source', HTMLSelectElement.prototype);
el('live-deck-seed-picker');
el('live-deck-seed-previous-note');
el('live-deck-seed-previous-label');
el('live-deck-seed-current-label');
const moduleSelect = el('live-module-select', HTMLSelectElement.prototype);
moduleSelect.value = 'M1';
const slotSelect = el('live-class-select', HTMLSelectElement.prototype);
slotSelect.value = 'C1';
const inputs = () => ['previous', 'course', 'current'].map((m) => els[`live-deck-seed-${m}`]);

globalThis.Option = function Option(text, value) { return { text, value: value ?? text, disabled: false }; };
globalThis.document = {
  body: makeEl('body'),
  documentElement: makeEl('html'),
  title: '',
  getElementById(id) { return els[id] || null; },
  createElement(tag) { return makeEl(tag); },
  querySelector(sel) {
    if (sel === 'input[name="live-deck-seed"]:checked') return inputs().find((i) => i.checked) || null;
    const m = sel.match(/^input\[name="live-deck-seed"\]\[value="(\w+)"\]$/);
    if (m) return inputs().find((i) => i.value === m[1]) || null;
    return null;
  },
  querySelectorAll() { return []; },
  addEventListener() {},
  removeEventListener() {},
  visibilityState: 'visible',
};
globalThis.window = globalThis;
window.addEventListener = () => {};
window.removeEventListener = () => {};
window.location = { origin: 'http://localhost', pathname: '/staff/class/5', search: '', hash: '', href: 'http://localhost/staff/class/5' };
window.history = { replaceState() {}, pushState() {} };
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
window.requestAnimationFrame = (fn) => { fn(); return 1; };
window.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
globalThis.localStorage = window.localStorage;
window.sessionStorage = window.localStorage;
globalThis.sessionStorage = window.localStorage;
Object.defineProperty(globalThis, 'navigator', { value: { userAgent: 'node', clipboard: { writeText() {} } }, configurable: true, writable: true });
window.setInterval = () => 0;
window.clearInterval = () => {};
globalThis.setInterval = window.setInterval;
globalThis.EventSource = undefined;

// Class 5. M1 C1 is empty (no earlier deck, course has decks). M1 C3 has its own deck.
// M1 C4 has a Previous (M1 C3). M2 C1 is the other course deck.
const options = {
  'M1/C1': { current: false, previous: { available: false, slot: null, message: 'No earlier challenge in this module.' } },
  'M1/C3': { current: true, previous: { available: false, module: 'M1', slot: 'C2', label: 'M1 C2', message: 'No deck saved for M1 C2.' } },
  'M1/C4': { current: false, previous: { available: true, module: 'M1', slot: 'C3', label: 'M1 C3', message: '' } },
};
const posts = [];
let holdOptions = false;
globalThis.fetch = async (url, init = {}) => {
  const target = String(url);
  let data = { ok: true };
  const opt = target.match(/live-lessons\/(M\d)\/(C\d)\/deck-seed-options/);
  if (opt && holdOptions) return new Promise(() => {});
  if (opt) {
    const row = options[`${opt[1]}/${opt[2]}`];
    data = {
      ok: true, course: 'SBI3U', module: opt[1], slot: opt[2],
      previous: row.previous,
      current: { available: row.current, label: 'Use current', message: '' },
      decks: [
        { class_id: 5, module: 'M1', slot: 'C3', label: 'M1 C3', section_code: 'SBI3U' },
        { class_id: 5, module: 'M2', slot: 'C1', label: 'M2 C1', section_code: 'SBI3U' },
      ].filter((d) => !(d.module === opt[1] && d.slot === opt[2])),
    };
  } else if (/\/deck-seed$/.test(target)) {
    posts.push({ url: target, body: JSON.parse(init.body || '{}') });
  }
  const text = JSON.stringify(data);
  return { ok: true, status: 200, async text() { return text; }, async json() { return data; },
    headers: { get() { return 'application/json'; } } };
};

let src = readFileSync(staticDir + '/staff_ap.js', 'utf8');
const staticHref = pathToFileURL(staticDir + '/').href;
src = src.replaceAll('"/static/common.js"', JSON.stringify(pathToFileURL(process.env.LLOVES_COMMON_JS).href));
src = src.replaceAll('"/static/', '"' + staticHref);
src += '\nexport { enterSetClassPhase, refreshDeckSeedOptions, applyDeckSeedChoice };\n';
const out = process.env.LLOVES_HARNESS_OUT;
writeFileSync(out, src);
const mod = await import(pathToFileURL(out).href);

const settle = () => new Promise((resolve) => setTimeout(resolve, 30));
const checked = () => (inputs().find((i) => i.checked) || {}).value || '';
async function switchSlot(slot) {
  slotSelect.value = slot;
  slotSelect.dispatch('change');
  await settle();
}
// A real radio fires click every time, and change only when it was not checked.
function clickChip(mode) {
  const input = els[`live-deck-seed-${mode}`];
  const wasChecked = input.checked;
  input.checked = true;
  els['live-deck-seed'].dispatch('click', input);
  if (!wasChecked) els['live-deck-seed'].dispatch('change', input);
}

const results = {};
mod.enterSetClassPhase();
await settle();
// 1. Empty slot: auto-picks Course (#187 behaviour).
results.emptyPick = checked();
// 2. Switch to M1 C3, which already has this class's deck.
await switchSlot('C3');
results.afterSwitchExisting = checked();
posts.length = 0;
await mod.applyDeckSeedChoice();
results.confirmExistingPosts = posts.map((p) => p.body);
// 3. Back to empty C1, then to C4 with an earlier deck: Previous.
await switchSlot('C1');
results.backToEmpty = checked();
await switchSlot('C4');
results.afterSwitchPrevious = checked();
posts.length = 0;
await mod.applyDeckSeedChoice();
results.autoPreviousPosts = posts.map((p) => p.body);
// 3b. Auto Course on C1, switch to C3 and confirm before C3's options load.
await switchSlot('C1');
holdOptions = true;
await switchSlot('C3');
results.staleChecked = checked();
posts.length = 0;
await mod.applyDeckSeedChoice();
results.staleConfirmPosts = posts.map((p) => p.body);
holdOptions = false;
// 4. Teacher clicks Course explicitly on C1 (already auto-selected there),
// then switches to C3: Course stays.
await switchSlot('C1');
results.beforeExplicitClick = checked();
clickChip('course');
await switchSlot('C3');
results.explicitCourseKept = checked();
posts.length = 0;
await mod.applyDeckSeedChoice();
results.explicitConfirmPosts = posts.map((p) => p.body);
// 5. A fresh Set Class phase forgets the explicit pick.
mod.enterSetClassPhase();
await settle();
results.freshPhase = checked();
console.log(JSON.stringify(results));
"""


def run_staff_ap_harness(static_dir: Path) -> dict:
    """Run the Set Class harness against one ``static`` folder.

    Args:
        static_dir: Folder holding ``staff_ap.js`` and its imports.

    Returns:
        The JSON results the harness prints.
    """

    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "harness.mjs"
        script.write_text(STAFF_AP_HARNESS, encoding="utf-8")
        env = os.environ.copy()
        env["LLOVES_LMS_STATIC"] = str(static_dir)
        env["LLOVES_COMMON_JS"] = str(
            REPO_ROOT / "tools" / "math-game-show" / "static" / "common.js"
        )
        env["LLOVES_HARNESS_OUT"] = str(Path(tmp) / "staff_ap_under_test.mjs")
        proc = subprocess.run(
            [NODE, str(script)],
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
class SetClassAutoPickResetTests(unittest.TestCase):
    """Drive the real staff_ap.js Set Class listeners in node."""

    @classmethod
    def setUpClass(cls) -> None:
        """Run the harness once; each test checks one step."""

        cls.results = run_staff_ap_harness(LMS_DIR / "static")

    def test_empty_slot_still_auto_picks_course(self) -> None:
        """#187 behaviour stays: an empty slot with no Previous picks Course."""

        self.assertEqual(self.results["emptyPick"], "course")
        self.assertEqual(self.results["backToEmpty"], "course")

    def test_switch_to_slot_with_deck_resets_to_use_current(self) -> None:
        """Ops repro: auto Course on C1, switch to M1 C3, confirm keeps C3."""

        self.assertEqual(self.results["afterSwitchExisting"], "current")
        self.assertEqual(self.results["confirmExistingPosts"], [])

    def test_switch_to_slot_with_previous_picks_previous(self) -> None:
        """A slot with an earlier deck re-defaults to Previous, guarded."""

        self.assertEqual(self.results["afterSwitchPrevious"], "previous")
        self.assertEqual(
            self.results["autoPreviousPosts"],
            [{"mode": "previous", "keep_existing": True}],
        )

    def test_confirm_before_new_slot_loads_writes_nothing(self) -> None:
        """A stale auto pick is not sent while the new slot is loading."""

        self.assertEqual(self.results["staleConfirmPosts"], [])

    def test_explicit_course_click_survives_slot_switch(self) -> None:
        """Course stays across a switch only when the teacher clicked it."""

        self.assertEqual(self.results["beforeExplicitClick"], "course")
        self.assertEqual(self.results["explicitCourseKept"], "course")
        self.assertEqual(
            self.results["explicitConfirmPosts"],
            [
                {
                    "mode": "course",
                    "source_module": "M2",
                    "source_slot": "C1",
                    "source_class_id": "5",
                }
            ],
        )

    def test_new_set_class_forgets_explicit_pick(self) -> None:
        """Run Live Class again starts from the slot default."""

        self.assertEqual(self.results["freshPhase"], "current")


HELPER_CASES = r"""
import {
  deckSeedConfirmMode,
  deckSeedDefaultMode,
} from "./static/deck_seed_help.js";

const out = {};
// chosen:false ignores the carried mode and re-derives the slot default.
out.autoCourseOnDeck = deckSeedDefaultMode({ mode: "course", chosen: false, currentAvailable: true, previousAvailable: false, courseDeckCount: 2 });
out.autoCourseOnPrevious = deckSeedDefaultMode({ mode: "course", chosen: false, currentAvailable: false, previousAvailable: true, courseDeckCount: 2 });
out.autoCourseOnEmpty = deckSeedDefaultMode({ mode: "course", chosen: false, currentAvailable: false, previousAvailable: false, courseDeckCount: 2 });
out.chosenCourseOnDeck = deckSeedDefaultMode({ mode: "course", chosen: true, currentAvailable: true, previousAvailable: false, courseDeckCount: 2 });
out.legacyCourseOnDeck = deckSeedDefaultMode({ mode: "course", currentAvailable: true, previousAvailable: true, courseDeckCount: 2 });
const c3 = { module: "M1", slot: "C3", current: { available: true }, previous: { available: false }, decks: [{}] };
const c1 = { module: "M1", slot: "C1", current: { available: false }, previous: { available: false }, decks: [{}, {}] };
out.confirmAutoOnDeck = deckSeedConfirmMode({ selected: "course", chosen: false, catalog: c3, pack: { module: "M1", slot: "C3" } });
out.confirmAutoStale = deckSeedConfirmMode({ selected: "course", chosen: false, catalog: c1, pack: { module: "M1", slot: "C3" } });
out.confirmAutoNoCatalog = deckSeedConfirmMode({ selected: "previous", chosen: false, catalog: null, pack: { module: "M1", slot: "C3" } });
out.confirmAutoEmpty = deckSeedConfirmMode({ selected: "course", chosen: false, catalog: c1, pack: { module: "m1", slot: "c1" } });
out.confirmChosenOnDeck = deckSeedConfirmMode({ selected: "course", chosen: true, catalog: c3, pack: { module: "M1", slot: "C3" } });
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is required for the deck_seed_help.js checks")
class DeckSeedHelperTests(unittest.TestCase):
    """Pure helpers behind the reset and the confirm guard."""

    def test_default_and_confirm_modes(self) -> None:
        """Auto picks re-derive; explicit picks are honoured."""

        proc = subprocess.run(
            [NODE, "--input-type=module", "-e", HELPER_CASES],
            cwd=str(LMS_DIR),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        got = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(
            got,
            {
                "autoCourseOnDeck": "current",
                "autoCourseOnPrevious": "previous",
                "autoCourseOnEmpty": "course",
                "chosenCourseOnDeck": "course",
                "legacyCourseOnDeck": "course",
                "confirmAutoOnDeck": "current",
                "confirmAutoStale": "current",
                "confirmAutoNoCatalog": "current",
                "confirmAutoEmpty": "course",
                "confirmChosenOnDeck": "course",
            },
        )


def _question_signature(metadata: dict) -> list[tuple[str, str]]:
    """Return ``(id, text)`` pairs for merged deck questions."""

    rows = []
    for row in metadata.get("questions") or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        text = str(
            row.get("text") or row.get("prompt") or row.get("title") or ""
        )
        rows.append((str(row["id"]), text))
    return rows


class DeckSeedKeepExistingServerTests(unittest.TestCase):
    """Server guard: an auto-picked mode never overwrites an existing deck."""

    def setUp(self) -> None:
        """Teacher-owned MCF3M class signed in through the staff portal."""

        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app(
            db_path=root / "lloves.sqlite",
            data_dir=root,
            testing=True,
        )
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

    def _deck(self, module: str, slot: str) -> list[tuple[str, str]]:
        """Question signature of this class's deck for one slot."""

        return _question_signature(
            self.school.live_class_metadata_for_class_lesson(
                self.class_id, module, slot, fresh=True
            )
        )

    def _seed(self, module: str, slot: str, body: dict):
        """POST Set Class deck-seed for one slot."""

        return self.client.post(
            f"/api/staff/class/{self.class_id}/live-lessons/{module}/{slot}/deck-seed",
            json=body,
        )

    def test_auto_course_keeps_existing_deck(self) -> None:
        """The Ops repro body plus keep_existing leaves M1 C3 alone."""

        self.assertTrue(self.school.class_has_current_live_deck(self.class_id, "M1", "C3"))
        before = self._deck("M1", "C3")
        source = self._deck("M1", "C2")
        self.assertTrue(before)
        self.assertNotEqual(before, source)
        kept = self._seed(
            "M1",
            "C3",
            {
                "mode": "course",
                "source_module": "M1",
                "source_slot": "C2",
                "keep_existing": True,
            },
        )
        self.assertEqual(kept.status_code, 200, kept.get_json())
        self.assertEqual(kept.get_json()["mode"], "current")
        self.assertTrue(kept.get_json()["kept_existing"])
        self.assertEqual(self._deck("M1", "C3"), before)
        auto_previous = self._seed("M1", "C3", {"mode": "previous", "keep_existing": True})
        self.assertEqual(auto_previous.status_code, 200, auto_previous.get_json())
        self.assertTrue(auto_previous.get_json()["kept_existing"])
        self.assertEqual(self._deck("M1", "C3"), before)

    def test_auto_course_still_seeds_an_empty_slot(self) -> None:
        """keep_existing does not block the first copy into an empty slot."""

        # Every MCF3M slot ships a seed file; an unedited Blank empties M2 C1.
        self.school.apply_class_deck_seed(self.class_id, "M2", "C1", mode="blank")
        self.assertFalse(self.school.class_has_current_live_deck(self.class_id, "M2", "C1"))
        source = self._deck("M1", "C2")
        seeded = self._seed(
            "M2",
            "C1",
            {
                "mode": "course",
                "source_module": "M1",
                "source_slot": "C2",
                "keep_existing": True,
            },
        )
        self.assertEqual(seeded.status_code, 200, seeded.get_json())
        self.assertEqual(seeded.get_json()["mode"], "course")
        self.assertFalse(seeded.get_json().get("kept_existing", False))
        self.assertEqual(self._deck("M2", "C1"), source)

    def test_explicit_course_still_replaces_existing_deck(self) -> None:
        """Without keep_existing (a clicked chip) the copy goes through."""

        source = self._deck("M1", "C2")
        seeded = self._seed(
            "M1", "C3", {"mode": "course", "source_module": "M1", "source_slot": "C2"}
        )
        self.assertEqual(seeded.status_code, 200, seeded.get_json())
        self.assertEqual(seeded.get_json()["mode"], "course")
        self.assertEqual(self._deck("M1", "C3"), source)


if __name__ == "__main__":
    unittest.main()

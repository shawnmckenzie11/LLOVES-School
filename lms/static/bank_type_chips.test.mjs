/**
 * MCK-170: Type chip row. Pure logic: chip model (All first, counts, the
 * selected type kept at 0), the one-line fit with a More panel at 1280px
 * and 360px widths (All pinned), and the per-view sessionStorage
 * read/write. Mounted on a small fake DOM: focus comes back to the chosen
 * chip after a redraw, the More panel's keyboard behaviour, and the
 * document listener / ResizeObserver are removed (Ops notes MED-1, LOW-2,
 * LOW-3, LOW-7).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  fitTypeChips,
  focusTarget,
  mountTypeChips,
  readStoredType,
  storeType,
  typeChipModel,
} from "./bank_type_chips.js";

let failures = 0;
let checks = 0;
function check(name, fn) {
  checks += 1;
  try {
    fn();
  } catch (err) {
    failures += 1;
    console.error(`FAIL ${name}: ${err.message}`);
  }
}

const counts = [
  { type: "mc", label: "Multiple choice", count: 12 },
  { type: "true_false", label: "True/false", count: 2 },
  { type: "rank", label: "Rank", count: 4 },
  { type: "open", label: "Open-ended", count: 0 },
];

check("model: All first with the summed count, zero rows dropped", () => {
  const chips = typeChipModel(counts, "");
  assert.deepEqual(
    chips.map((c) => [c.type, c.label, c.count, c.pressed]),
    [
      ["", "All", 18, true],
      ["mc", "Multiple choice", 12, false],
      ["true_false", "True/false", 2, false],
      ["rank", "Rank", 4, false],
    ]
  );
});

check("model: single select presses only the chosen type", () => {
  const chips = typeChipModel(counts, "rank");
  assert.deepEqual(chips.filter((c) => c.pressed).map((c) => c.type), ["rank"]);
});

check("model: a selected type missing from results stays visible at 0", () => {
  const chips = typeChipModel(counts.slice(0, 1), "rank");
  assert.deepEqual(chips.at(-1), { type: "rank", label: "Rank", count: 0, pressed: true });
});

check("model: no counts still gives the All chip", () => {
  assert.deepEqual(typeChipModel(null, ""), [{ type: "", label: "All", count: 0, pressed: true }]);
});

// Widths roughly as rendered: All 18, Multiple choice 12, True/false 2,
// Numeric 3, Short answer 1, Rank 4, Open-ended 5.
const widths = [56, 140, 100, 90, 118, 72, 116];
const gap = 6;

check("fit: everything fits at 1280px", () => {
  const out = fitTypeChips({ widths, available: 1200, moreWidth: 76, gap, selectedIndex: 0 });
  assert.deepEqual(out.overflow, []);
  assert.equal(out.visible.length, widths.length);
});

check("fit: 360px keeps order, reserves More, and stays within the row", () => {
  const available = 320;
  const out = fitTypeChips({ widths, available, moreWidth: 76, gap, selectedIndex: 0 });
  assert.deepEqual(out.visible, [0, 1]);
  assert.deepEqual(out.overflow, [2, 3, 4, 5, 6]);
  const used = out.visible.reduce((s, i) => s + widths[i], 0) + out.visible.length * gap + 76;
  assert.ok(used <= available, `${used} > ${available}`);
});

check("fit: the selected chip is never hidden in More", () => {
  const out = fitTypeChips({ widths, available: 320, moreWidth: 76, gap, selectedIndex: 5 });
  assert.ok(out.visible.includes(5));
  assert.deepEqual(out.visible, [0, 5]);
  assert.ok(out.overflow.includes(1));
});

check("fit: All stays pinned first even when it and the selected chip barely fit (Import 360)", () => {
  // Import at 360px: row 252px. All 132 + True/false 4 + More is a few px too wide.
  const w = [72, 104, 100, 90, 118, 72, 116];
  const out = fitTypeChips({ widths: w, available: 252, moreWidth: 72, gap, selectedIndex: 2 });
  assert.deepEqual(out.visible, [0, 2]);
  assert.ok(!out.overflow.includes(0), "All never moves into More");
  for (let sel = 0; sel < w.length; sel += 1) {
    for (const available of [120, 200, 252, 316, 468]) {
      const o = fitTypeChips({ widths: w, available, moreWidth: 72, gap, selectedIndex: sel });
      assert.equal(o.visible[0], 0, `All first at ${available} sel ${sel}`);
      assert.ok(o.visible.includes(sel), `selected shown at ${available} sel ${sel}`);
      assert.deepEqual([...o.visible].sort((a, b) => a - b), o.visible, "order kept");
    }
  }
});

check("focusTarget: chosen chip, else More, else the pressed chip", () => {
  const chips = [
    { type: "", visible: true, pressed: false },
    { type: "mc", visible: false, pressed: false },
    { type: "rank", visible: true, pressed: true },
  ];
  assert.deepEqual(focusTarget(chips, "rank", true), { chip: "rank" });
  assert.deepEqual(focusTarget(chips, "", true), { chip: "" });
  assert.deepEqual(focusTarget(chips, "mc", true), { more: true });
  assert.deepEqual(focusTarget(chips, null, true), { more: true });
  assert.deepEqual(focusTarget(chips, null, false), { chip: "rank" });
});

check("fit: exact fit needs no More chip", () => {
  const exact = widths.reduce((s, w) => s + w, 0) + gap * (widths.length - 1);
  assert.deepEqual(fitTypeChips({ widths, available: exact, moreWidth: 76, gap, selectedIndex: 0 }).overflow, []);
});

function memoryStorage() {
  const data = new Map();
  return {
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => data.set(k, String(v)),
    removeItem: (k) => data.delete(k),
  };
}

check("storage: per view, cleared by All, unknown reads as All", () => {
  const store = memoryStorage();
  storeType(store, "lloves.bankType.import", "rank");
  assert.equal(readStoredType(store, "lloves.bankType.import"), "rank");
  assert.equal(readStoredType(store, "lloves.bankType.question-banks"), "");
  storeType(store, "lloves.bankType.import", "");
  assert.equal(readStoredType(store, "lloves.bankType.import"), "");
  store.setItem("lloves.bankType.import", "bogus");
  assert.equal(readStoredType(store, "lloves.bankType.import"), "");
  assert.equal(readStoredType(null, "x"), "");
});

// ---------------------------------------------------------------------------
// A small fake DOM: just what mountTypeChips touches. innerHTML parses the
// flat markup the component writes; widths come from text length.
// ---------------------------------------------------------------------------

function decode(text) {
  return text.replace(/&#(\d+);/g, (_, n) => String.fromCharCode(Number(n)));
}

class FakeNode {
  constructor(doc) {
    this.ownerDocument = doc;
    this.parentNode = null;
    this.nodeType = 3;
    this.children = [];
  }
  get isConnected() {
    let node = this;
    while (node.parentNode) node = node.parentNode;
    return node === this.ownerDocument;
  }
}

class FakeText extends FakeNode {
  constructor(doc, text) {
    super(doc);
    this.text = text;
  }
  get textContent() {
    return this.text;
  }
}

const listenerCount = (target) => [...target.listeners.values()].reduce((n, set) => n + set.size, 0);

class FakeElement extends FakeNode {
  constructor(doc, tag) {
    super(doc);
    this.nodeType = 1;
    this.tagName = tag.toUpperCase();
    this.attrs = new Map();
    this.listeners = new Map();
    const el = this;
    this.classList = {
      add: (...names) => el.setAttribute("class", [...new Set([...el.classes(), ...names])].join(" ")),
      remove: (...names) => el.setAttribute("class", el.classes().filter((c) => !names.includes(c)).join(" ")),
      contains: (name) => el.classes().includes(name),
    };
  }
  classes() {
    return (this.getAttribute("class") || "").split(/\s+/).filter(Boolean);
  }
  getAttribute(name) {
    return this.attrs.has(name) ? this.attrs.get(name) : null;
  }
  setAttribute(name, value) {
    this.attrs.set(name, String(value));
  }
  hasAttribute(name) {
    return this.attrs.has(name);
  }
  removeAttribute(name) {
    this.attrs.delete(name);
  }
  get hidden() {
    return this.hasAttribute("hidden");
  }
  set hidden(value) {
    if (value) this.setAttribute("hidden", "");
    else this.removeAttribute("hidden");
  }
  get textContent() {
    return this.children.map((c) => c.textContent).join("");
  }
  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter((c) => c !== this);
    this.parentNode = null;
    this.ownerDocument.fixFocus();
  }
  set innerHTML(html) {
    for (const child of this.children) child.parentNode = null;
    this.children = [];
    const stack = [this];
    const re = /<(\/?)([a-z]+)([^>]*)>|([^<]+)/g;
    let m;
    while ((m = re.exec(html))) {
      const top = stack[stack.length - 1];
      if (m[4] !== undefined) {
        if (m[4].trim() || top !== this) top.appendChild(new FakeText(this.ownerDocument, decode(m[4])));
      } else if (m[1]) {
        stack.pop();
      } else {
        const el = new FakeElement(this.ownerDocument, m[2]);
        for (const a of m[3].matchAll(/([\w-]+)(?:="([^"]*)")?/g)) el.setAttribute(a[1], decode(a[2] ?? ""));
        top.appendChild(el);
        stack.push(el);
      }
    }
    // Like a browser: a removed focused element leaves focus on <body>.
    this.ownerDocument.fixFocus();
  }
  contains(node) {
    for (let n = node; n; n = n.parentNode) if (n === this) return true;
    return false;
  }
  matches(selector) {
    return selector.split(",").some((part) => {
      const sel = part.trim();
      let rest = sel;
      const tag = /^[a-z]+/.exec(rest);
      if (tag) {
        if (this.tagName !== tag[0].toUpperCase()) return false;
        rest = rest.slice(tag[0].length);
      }
      for (const t of rest.matchAll(/\.([\w-]+)|\[([\w-]+)(?:="([^"]*)")?\]/g)) {
        if (t[1] && !this.classes().includes(t[1])) return false;
        if (t[2] && !this.hasAttribute(t[2])) return false;
        if (t[2] && t[3] !== undefined && this.getAttribute(t[2]) !== t[3]) return false;
      }
      return true;
    });
  }
  querySelectorAll(selector) {
    const out = [];
    const walk = (el) => {
      for (const c of el.children) {
        if (!(c instanceof FakeElement)) continue;
        if (c.matches(selector)) out.push(c);
        walk(c);
      }
    };
    walk(this);
    return out;
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
  closest(selector) {
    for (let n = this; n instanceof FakeElement; n = n.parentNode) if (n.matches(selector)) return n;
    return null;
  }
  addEventListener(type, fn) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(fn);
  }
  removeEventListener(type, fn) {
    this.listeners.get(type)?.delete(fn);
  }
  dispatch(type, init = {}) {
    const event = { type, target: this, stopped: false, stopPropagation() { this.stopped = true; }, ...init };
    for (let n = this; n && !event.stopped; n = n.parentNode) {
      for (const fn of [...(n.listeners?.get(type) || [])]) fn.call(n, event);
    }
    return event;
  }
  click() {
    this.focus(); // Chrome focuses a clicked button
    this.dispatch("click");
  }
  focus() {
    const doc = this.ownerDocument;
    const prev = doc.activeElement;
    if (prev === this) return;
    if (prev && prev !== doc.body) prev.dispatch("focusout", { relatedTarget: this });
    doc.activeElement = this;
  }
  get clientWidth() {
    return this.fixedWidth ?? 0;
  }
  getBoundingClientRect() {
    if (this.hidden) return { width: 0 };
    if (this.fixedWidth !== undefined) return { width: this.fixedWidth };
    return { width: this.textContent.trim().length * 8 + 24 };
  }
}

class FakeDocument {
  constructor() {
    this.listeners = new Map();
    this.nodeType = 9;
    this.parentNode = null;
    this.body = new FakeElement(this, "body");
    this.body.parentNode = this;
    this.children = [this.body];
    this.activeElement = this.body;
  }
  fixFocus() {
    if (this.activeElement !== this.body && !this.activeElement.isConnected) this.activeElement = this.body;
  }
  createElement(tag) {
    return new FakeElement(this, tag);
  }
  addEventListener(type, fn) {
    FakeElement.prototype.addEventListener.call(this, type, fn);
  }
  removeEventListener(type, fn) {
    FakeElement.prototype.removeEventListener.call(this, type, fn);
  }
}

const observers = [];
class FakeResizeObserver {
  constructor(cb) {
    this.cb = cb;
    this.connected = false;
    observers.push(this);
  }
  observe() {
    this.connected = true;
  }
  disconnect() {
    this.connected = false;
  }
}

globalThis.Element = FakeElement;
globalThis.ResizeObserver = FakeResizeObserver;
globalThis.getComputedStyle = () => ({ columnGap: "6px" });
globalThis.window = { sessionStorage: memoryStorage() };

const sevenTypes = [
  { type: "mc", label: "Multiple choice", count: 9 },
  { type: "true_false", label: "True/false", count: 4 },
  { type: "numeric", label: "Numeric", count: 2 },
  { type: "rank", label: "Rank", count: 2 },
  { type: "open", label: "Open-ended", count: 115 },
];

/** Mount on a fresh fake page; ``width`` is the row's clientWidth. */
function mountFake(width, view = `t${observers.length}`) {
  const doc = new FakeDocument();
  const outside = doc.body.appendChild(doc.createElement("input"));
  const host = doc.body.appendChild(doc.createElement("div"));
  const changes = [];
  const ui = mountTypeChips(host, { view, onChange: (t) => changes.push(t) });
  host.querySelector("[data-bank-type-row]").fixedWidth = width;
  ui.update(sevenTypes);
  const row = host.querySelector("[data-bank-type-row]");
  const menu = host.querySelector("[data-bank-type-menu]");
  const shown = () => row.querySelectorAll("[data-bank-type]").filter((c) => !c.hidden).map((c) => c.getAttribute("data-bank-type"));
  const active = () => {
    const a = doc.activeElement;
    if (a.hasAttribute("data-bank-type")) return `chip:${a.getAttribute("data-bank-type")}`;
    if (a.hasAttribute("data-bank-type-more")) return "more";
    if (a.hasAttribute("data-bank-type-pick")) return `pick:${a.getAttribute("data-bank-type-pick")}`;
    return a.tagName;
  };
  return { doc, host, ui, row, menu, outside, changes, shown, active, more: () => row.querySelector("[data-bank-type-more]") };
}

check("MED-1: Enter/click on a chip keeps focus on that chip after the redraw and the reply", () => {
  const f = mountFake(1200);
  assert.equal(f.shown().length, 6);
  const mc = f.row.querySelector('[data-bank-type="mc"]');
  mc.click();
  assert.deepEqual(f.changes, ["mc"]);
  assert.notEqual(f.row.querySelector('[data-bank-type="mc"]'), mc, "row was rebuilt");
  assert.equal(f.active(), "chip:mc");
  f.ui.update(sevenTypes); // the search reply repaints again
  assert.equal(f.active(), "chip:mc");
  f.row.querySelector('[data-bank-type=""]').click();
  assert.equal(f.active(), "chip:");
  // A repaint while focus is elsewhere (typing in search) leaves it alone.
  f.outside.focus();
  f.ui.update(sevenTypes);
  assert.equal(f.doc.activeElement, f.outside);
});

check("MED-1: picking from More focuses the chosen chip, now on the row", () => {
  const f = mountFake(252);
  assert.deepEqual(f.shown(), [""]);
  f.more().click();
  assert.equal(f.menu.hidden, false);
  f.menu.querySelector('[data-bank-type-pick="true_false"]').click();
  assert.equal(f.menu.hidden, true);
  assert.equal(f.active(), "chip:true_false");
  f.ui.update(sevenTypes);
  assert.equal(f.active(), "chip:true_false");
  assert.equal(f.more().getAttribute("aria-expanded"), "false");
});

check("MED-1: focus on More survives a repaint", () => {
  const f = mountFake(252);
  f.more().focus();
  f.ui.update(sevenTypes);
  assert.equal(f.active(), "more");
});

check("MED-1: a resize keeps focus on a panel item, or moves it to More when its chip hides", () => {
  const f = mountFake(1200);
  const ro = observers.at(-1);
  f.row.querySelector('[data-bank-type="open"]').focus();
  f.row.fixedWidth = 252; // narrower: Open-ended moves into More
  ro.cb([]);
  assert.ok(f.row.querySelector('[data-bank-type="open"]').hidden);
  assert.equal(f.active(), "more");
  f.more().click();
  f.menu.querySelector('[data-bank-type-pick="rank"]').focus();
  ro.cb([]); // the panel is rebuilt
  assert.equal(f.active(), "pick:rank");
  assert.equal(f.menu.hidden, false);
});

check("LOW-3: All stays first on the row and out of More (Import 360, True/false)", () => {
  const f = mountFake(252);
  f.more().click();
  f.menu.querySelector('[data-bank-type-pick="true_false"]').click();
  assert.deepEqual(f.shown(), ["", "true_false"]);
  assert.equal(f.row.querySelector("[data-bank-type]").getAttribute("data-bank-type"), "");
  const picks = f.menu.querySelectorAll("[data-bank-type-pick]").map((b) => b.getAttribute("data-bank-type-pick"));
  assert.ok(!picks.includes(""), `All in More: ${picks}`);
  assert.ok(!picks.includes("true_false"));
  for (const t of ["mc", "numeric", "rank", "open"]) {
    f.more().click();
    f.menu.querySelector(`[data-bank-type-pick="${t}"]`).click();
    assert.equal(f.shown()[0], "", `All first with ${t}`);
    assert.ok(f.shown().includes(t));
  }
});

check("LOW-2: More is a disclosure: no ARIA menu, Escape and Tab close it", () => {
  const f = mountFake(252);
  const more = f.more();
  assert.equal(f.menu.getAttribute("role"), "group");
  assert.equal(more.getAttribute("aria-haspopup"), null);
  assert.equal(more.getAttribute("aria-controls"), f.menu.getAttribute("id"));
  more.click(); // Enter/Space on a button fire click
  assert.equal(more.getAttribute("aria-expanded"), "true");
  const items = f.menu.querySelectorAll("button");
  assert.ok(items.length > 0);
  assert.ok(items.every((b) => b.hasAttribute("aria-pressed") && !b.getAttribute("role")));
  // Tab from More goes into the panel (DOM order); Escape returns to More.
  items[0].focus();
  items[0].dispatch("keydown", { key: "Escape" });
  assert.equal(f.menu.hidden, true);
  assert.equal(f.active(), "more");
  assert.equal(more.getAttribute("aria-expanded"), "false");
  // Tabbing out of the panel closes it.
  more.click();
  f.menu.querySelectorAll("button").at(-1).focus();
  f.outside.focus();
  assert.equal(f.menu.hidden, true);
  // Moving between More and an item keeps it open.
  more.click();
  f.menu.querySelector("button").focus();
  assert.equal(f.menu.hidden, false);
  more.focus();
  assert.equal(f.menu.hidden, false);
  // A click outside closes it.
  f.outside.dispatch("click");
  assert.equal(f.menu.hidden, true);
});

check("LOW-7: the document click listener is only attached while More is open", () => {
  const f = mountFake(252);
  assert.equal(listenerCount(f.doc), 0);
  for (let i = 0; i < 5; i += 1) {
    f.more().click();
    assert.equal(listenerCount(f.doc), 1);
    f.more().click();
    assert.equal(listenerCount(f.doc), 0);
  }
  f.more().click();
  f.ui.destroy();
  assert.equal(listenerCount(f.doc), 0);
});

check("LOW-7: destroy() disconnects the ResizeObserver; reopening does not pile up", () => {
  const before = observers.length;
  const doc = new FakeDocument();
  for (let i = 0; i < 6; i += 1) {
    // Like the Import picker: a fresh host per open, destroyed on close.
    const host = doc.body.appendChild(doc.createElement("div"));
    const ui = mountTypeChips(host, { view: "import", onChange: () => {} });
    host.querySelector("[data-bank-type-row]").fixedWidth = 252;
    ui.update(sevenTypes);
    host.querySelector("[data-bank-type-more]").click();
    ui.destroy();
    host.remove();
  }
  const mine = observers.slice(before);
  assert.equal(mine.length, 6);
  assert.equal(mine.filter((o) => o.connected).length, 0);
  assert.equal(listenerCount(doc), 0);
  assert.equal(listenerCount(doc.body), 0);
});

check("LOW-7: a host removed without destroy() stops observing on the next resize", () => {
  const f = mountFake(252);
  const ro = observers.at(-1);
  f.more().click();
  f.host.remove();
  ro.cb([]);
  assert.equal(ro.connected, false);
  assert.equal(listenerCount(f.doc), 0);
});

check("LOW-7: the Import picker destroys its chips on close and before a new mount", () => {
  const src = readFileSync(new URL("./bank_mc_picker.js", import.meta.url), "utf8");
  const close = src.slice(src.indexOf("function closePickerModal()"));
  assert.match(close.slice(0, close.indexOf("}")), /destroyTypeChips\(\)/);
  assert.match(src, /activeTypeChips = typeChips;/);
  assert.match(src, /if \(mountTarget\) \{\s*destroyTypeChips\(\);/);
});

if (failures) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log(`bank_type_chips.test.mjs: all ${checks} passed`);

/**
 * MCK-203 Catch-up links card: four slots, newest run_key, earlier expander,
 * closed copy, plain preview path.
 */
import {
  CATCHUP_LOAD_ERROR,
  PENDING_COPY,
  buildLessonSlideCatchup,
  copyPlainLink,
  moduleKeyOf,
  paintLessonModuleSections,
  bindCatchupLinks,
  postCatchupOpen,
} from "./catchup_links_card.js";

function fail(message) {
  console.error(message);
  process.exit(1);
}

function assert(cond, message) {
  if (!cond) fail(message);
}

class El {
  constructor(tag, doc) {
    this.tagName = String(tag).toUpperCase();
    this.ownerDocument = doc;
    this.className = "";
    this.textContent = "";
    this.children = [];
    this.attrs = {};
    this.parentElement = null;
    this.type = "";
    this.checked = false;
    this.listeners = {};
  }

  setAttribute(key, value) {
    this.attrs[key] = String(value);
  }

  getAttribute(key) {
    return Object.prototype.hasOwnProperty.call(this.attrs, key) ? this.attrs[key] : null;
  }

  append(...nodes) {
    for (const node of nodes) {
      node.parentElement = this;
      this.children.push(node);
    }
  }

  replaceChildren(...nodes) {
    for (const child of this.children) child.parentElement = null;
    this.children = [];
    this.append(...nodes);
  }

  addEventListener(type, fn) {
    this.listeners[type] = fn;
  }

  querySelector(sel) {
    const want = String(sel).replace(/^\./, "");
    for (const node of this.walk()) {
      if (String(node.className || "").split(/\s+/).includes(want)) return node;
    }
    return null;
  }

  *walk() {
    for (const child of this.children) {
      yield child;
      yield* child.walk();
    }
  }
}

function makeDoc() {
  return {
    createElement(tag) {
      return new El(tag, this);
    },
  };
}

function mountDoc() {
  const doc = makeDoc();
  const mount = new El("div", doc);
  mount.ownerDocument = doc;
  return mount;
}

function texts(mount) {
  const out = [];
  for (const node of mount.walk()) {
    if (node.textContent) out.push(node.textContent);
  }
  return out;
}

function byClass(mount, name) {
  return [...mount.walk()].filter((node) => String(node.className || "").split(/\s+/).includes(name));
}

const empty = buildLessonSlideCatchup([], []);
assert(empty.length === 1 && empty[0].moduleKey === "M1", "empty class still shows module 1");
assert(empty[0].slots.length === 4, "four slots");
assert(empty[0].slots.every((slot) => slot.current === null), "empty slots have no link");
assert(empty[0].decks.length === 0, "empty deck list");

const links = [
  {
    token: "olderTok",
    url: "https://alc.example/catchup/olderTok",
    live_module: "M1",
    live_slot: "C1",
    is_open: true,
    created_at: "2026-10-13T15:00:00",
    run_key: "session:1:2026-09-01T09:00:00",
  },
  {
    token: "newerTok",
    url: "https://alc.example/catchup/newerTok",
    live_module: "m1",
    live_slot: "c1",
    is_open: 0,
    created_at: "2026-09-02T09:00:00",
    run_key: "session:2:2026-10-13T09:00:00",
  },
  {
    token: "midTok",
    url: "https://alc.example/catchup/midTok",
    live_module: "M1",
    live_slot: "C1",
    is_open: false,
    created_at: "2026-09-15T09:00:00",
    run_key: "session:3:2026-09-15T09:00:00",
  },
  {
    token: "openTok",
    url: "https://alc.example/catchup/openTok",
    live_module: "M1",
    live_slot: "C2",
    is_open: true,
    created_at: "2026-10-01T09:00:00",
    run_key: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  },
  {
    token: "uuidOld",
    url: "https://alc.example/catchup/uuidOld",
    live_module: "M1",
    live_slot: "C3",
    is_open: true,
    created_at: "2026-08-01T09:00:00",
    run_key: "ffffffffffffffffffffffffffffffff",
  },
  {
    token: "uuidNew",
    url: "https://alc.example/catchup/uuidNew",
    live_module: "M1",
    live_slot: "C3",
    is_open: false,
    created_at: "2026-10-12T09:00:00",
    run_key: "00000000000000000000000000000000",
  },
];
const decks = [
  { module_number: 2, live_index: 3, presentation_url: "https://docs.example/deck" },
  { module_number: 1, live_index: 1, presentation_url: "https://docs.example/m1" },
];
const model = buildLessonSlideCatchup(decks, links);
assert(model.map((row) => row.moduleKey).join(",") === "M1,M2", `modules ${model.map((r) => r.moduleKey)}`);
const c1 = model[0].slots[0];
assert(c1.current.token === "newerTok", `newest run_key should win, got ${c1.current.token}`);
assert(c1.earlier.map((row) => row.token).join(",") === "midTok,olderTok", "earlier runs follow run_key time");
const c3 = model[0].slots[2];
assert(c3.current.token === "uuidNew", "uuid runs use created_at when run_key has no clock");
assert(model[0].decks.length === 1 && model[0].decks[0].live_index === 1, "module 1 decks stay on module 1");
assert(model[1].decks.length === 1, "module 2 deck list");
assert(model[1].slots.every((slot) => slot.current === null), "module 2 has not run");

const mount = mountDoc();
paintLessonModuleSections(mount, { decks, links });
const tags = [...mount.walk()].map((node) => node.tagName);
assert(!tags.includes("IFRAME") && !tags.includes("EMBED") && !tags.includes("OBJECT"), "no frame or embed");
const pending = texts(mount).filter((text) => text === PENDING_COPY);
assert(pending.length === 5, `pending rows C4 plus module 2, got ${pending.length}`);
const summaries = [...mount.walk()].filter((node) => node.tagName === "SUMMARY").map((node) => node.textContent);
assert(summaries.includes("Earlier links (2)"), `expander ${summaries}`);
assert(summaries.includes("Earlier links (1)"), `c3 expander ${summaries}`);
const closedBadges = byClass(mount, "is-muted");
assert(closedBadges.length >= 1, "closed link uses the muted badge");
const newerRow = [...mount.walk()].find((node) => node.getAttribute("data-run-key") === "session:2:2026-10-13T09:00:00");
assert(newerRow, "newest row is in the card");
const newerCopy = newerRow.querySelector(".catchup-copy");
assert(newerCopy && newerCopy.textContent === "Copy link", "copy stays available when closed");
assert(newerCopy.getAttribute("data-url") === "https://alc.example/catchup/newerTok", "copy is the plain url");
assert(!newerCopy.getAttribute("disabled"), "copy is not disabled");
const preview = newerRow.querySelector(".catchup-preview");
assert(preview.getAttribute("href") === "/catchup/newerTok", "preview is the plain path");
assert(preview.getAttribute("target") === "_blank", "preview opens a new tab");
assert(texts(mount).includes("Class C1") && texts(mount).includes("Class C4"), "class names");
assert(newerRow.querySelector(".catchup-date").textContent === "Sep 2, 2026", "row shows created date");
assert(texts(mount).includes("Module 2"), "module 2 deck section");
assert(texts(mount).includes("Class C3"), "deck class name under its module");

const emptyMount = mountDoc();
paintLessonModuleSections(emptyMount, { decks: [], links: [] });
assert(byClass(emptyMount, "catchup-links-card").length === 1, "one card when nothing has run");
assert([...emptyMount.walk()].filter((node) => node.textContent === PENDING_COPY).length === 4, "C1–C4 grey rows");
assert(texts(emptyMount).includes("No Google decks saved for this module yet."), "empty deck list");

let posted = null;
globalThis.fetch = async (url, init) => {
  posted = { url, body: JSON.parse(init.body), method: init.method };
  return { ok: true };
};
const toggleMount = mountDoc();
paintLessonModuleSections(toggleMount, { decks: [], links: [links[3]] });
bindCatchupLinks(toggleMount, 42);
const toggle = toggleMount.querySelector(".catchup-toggle");
toggle.checked = false;
await toggleMount.listeners.change({ target: toggle });
await new Promise((resolve) => setTimeout(resolve, 0));
assert(posted.method === "POST", "toggle posts");
assert(posted.url === "/api/classes/42/catchup-links/openTok", posted.url);
assert(posted.body.open === false, "body is {open:false}");
assert(toggleMount.querySelector(".catchup-badge").textContent === "Closed", "badge follows the toggle");
assert(toggleMount.querySelector(".catchup-badge").className.includes("is-muted"), "toggled closed is muted");

const abortMount = mountDoc();
paintLessonModuleSections(abortMount, { decks: [], links: [links[3]] });
bindCatchupLinks(abortMount, 99);
const abortToggle = abortMount.querySelector(".catchup-toggle");
abortToggle.checked = true;
globalThis.fetch = async () => {
  const err = new Error("The user aborted a request.");
  err.name = "AbortError";
  throw err;
};
await abortMount.listeners.change({ target: abortToggle });
await new Promise((resolve) => setTimeout(resolve, 0));
assert(abortToggle.checked === false, "aborted toggle reverts checkbox");

assert(moduleKeyOf("M1") === "M1" && moduleKeyOf("m2") === "M2", "live_module M#");
assert(moduleKeyOf("bogus") === "" && moduleKeyOf("M1-extra") === "", "reject bad live_module");

const errMount = mountDoc();
paintLessonModuleSections(errMount, { decks: [], links: [], error: CATCHUP_LOAD_ERROR });
assert(texts(errMount).includes(CATCHUP_LOAD_ERROR), "load error on card");
assert(!texts(errMount).includes(PENDING_COPY), "no grey rows when load failed");

const copied = [];
globalThis.navigator ??= {};
Object.defineProperty(globalThis.navigator, "clipboard", {
  configurable: true,
  value: { writeText: async (value) => { copied.push(value); } },
});
const copyButton = toggleMount.querySelector(".catchup-copy");
await toggleMount.listeners.click({
  target: copyButton,
  preventDefault() {},
});
assert(copied.length === 1 && copied[0] === "https://alc.example/catchup/openTok", "clipboard gets the plain link");
assert(copyButton.textContent === "Copied", "copy confirms");
const refused = await copyPlainLink("<iframe src='https://alc.example/catchup/x'></iframe>");
assert(refused === false, "markup is not copied");

const direct = await postCatchupOpen(7, "abc", true, async (_url, init) => {
  assert(JSON.parse(init.body).open === true, "open true");
  return { ok: false };
});
assert(direct === false, "failed post reports false");

console.log("ok");

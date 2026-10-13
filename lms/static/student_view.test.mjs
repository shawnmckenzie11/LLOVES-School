// MCK-199: node harness for the Student view mask (tiny fake DOM).
import {
  GuestBook, MASK, STUDENT_VIEW_COPY, STUDENT_VIEW_KEY, applyMask, maskName,
  nameMasks, restoreMask, storeStudentView, studentNumbers, studentViewOn,
} from "./student_view.js";

const fail = (msg) => { console.error(`FAIL: ${msg}`); process.exit(1); };

// ---- minimal DOM -------------------------------------------------------
class Text {
  constructor(v) { this.nodeType = 3; this.nodeValue = v; this.parentNode = null; }
  get textContent() { return this.nodeValue; }
}
class El {
  constructor(tag, attrs = {}, kids = []) {
    this.nodeType = 1; this.tagName = tag.toUpperCase(); this.attrs = { ...attrs };
    this.childNodes = []; this.parentNode = null;
    for (const k of kids) this.append(typeof k === "string" ? new Text(k) : k);
  }
  append(n) { n.parentNode = this; this.childNodes.push(n); return this; }
  get children() { return this.childNodes.filter((n) => n.nodeType === 1); }
  get parentElement() { return this.parentNode && this.parentNode.nodeType === 1 ? this.parentNode : null; }
  get textContent() { return this.childNodes.map((n) => n.textContent).join(""); }
  set textContent(v) { this.childNodes = []; this.append(new Text(v)); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  hasAttribute(k) { return k in this.attrs; }
  get classes() { return String(this.attrs.class || "").split(/\s+/).filter(Boolean); }
  matchCompound(c) {
    const m = c.match(/^([a-z]*)((?:\.[\w-]+|\[[^\]]+\]|:first-child)*)$/i);
    if (!m) return false;
    if (m[1] && this.tagName !== m[1].toUpperCase()) return false;
    for (const part of m[2].match(/\.[\w-]+|\[[^\]]+\]|:first-child/g) || []) {
      if (part[0] === ".") { if (!this.classes.includes(part.slice(1))) return false; }
      else if (part === ":first-child") { if (!this.parentElement || this.parentElement.children[0] !== this) return false; }
      else {
        const [k, v] = part.slice(1, -1).split("=");
        if (!this.hasAttribute(k)) return false;
        if (v != null && this.getAttribute(k) !== v.replace(/"/g, "")) return false;
      }
    }
    return true;
  }
  matchOne(sel) {
    const toks = sel.trim().replace(/\s*>\s*/g, " > ").split(/\s+/);
    const test = (el, i) => {
      if (!el || !el.matchCompound(toks[i])) return false;
      if (i === 0) return true;
      if (toks[i - 1] === ">") return test(el.parentElement, i - 2);
      for (let up = el.parentElement; up; up = up.parentElement) if (test(up, i - 1)) return true;
      return false;
    };
    return test(this, toks.length - 1);
  }
  matches(sel) { return sel.split(",").some((s) => this.matchOne(s)); }
  closest(sel) { for (let e = this; e; e = e.parentElement) if (e.matches(sel)) return e; return null; }
  querySelectorAll(sel) {
    const out = [];
    const walk = (n) => { for (const c of n.children) { if (c.matches(sel)) out.push(c); walk(c); } };
    walk(this);
    return out;
  }
}
const h = (tag, attrs, ...kids) => new El(tag, attrs || {}, kids);

// ---- stable numbering --------------------------------------------------
const roster = [
  { student_id: 42, first_name: "Zoe", last_name: "Park", codename: "Maple" },
  { id: 7, first_name: "Ava", last_name: "Li" },
  { student_id: 19, first_name: "Ben", last_name: "Ong", codename: "Birch" },
];
const a = studentNumbers(roster);
const b = studentNumbers([...roster].reverse());
if (a.get(7) !== 1 || a.get(19) !== 2 || a.get(42) !== 3) fail(`numbers ${[...a]}`);
if (JSON.stringify([...a]) !== JSON.stringify([...b])) fail("order must not matter");

// ---- storage -----------------------------------------------------------
const mem = new Map();
const store = {
  getItem: (k) => (mem.has(k) ? mem.get(k) : null),
  setItem: (k, v) => mem.set(k, String(v)),
  removeItem: (k) => mem.delete(k),
};
if (studentViewOn(store)) fail("off by default");
storeStudentView(true, store);
if (!studentViewOn(store) || mem.get(STUDENT_VIEW_KEY) !== "1") fail("stored on");
storeStudentView(false, store);
if (studentViewOn(store)) fail("stored off");

const pairs = nameMasks(roster);
const guests = new GuestBook(store);
if (maskName("😀 Ava", pairs, guests) !== "😀 Student 1") fail("mood prefix kept");
if (maskName("+1 Maple", pairs, guests) !== "+1 Student 3") fail("score pop");

// ---- page: Class List (COURSE/GAME), guest, popup, question text -------
const page = h("body", {},
  h("div", { class: "ap-att-row" },
    h("span", { class: "ap-att-name", title: "Ava" }, "Ava"),
    h("span", { class: "ap-att-course", title: "Course" }, "4"),
    h("span", { class: "ap-att-game", title: "Game" }, "2"),
    h("button", { class: "ap-att-plus", "data-kind": "student", "data-pop-name": "Ava", "aria-label": "Add one point" }, "+1")),
  h("div", { class: "ap-att-row is-guest" },
    h("span", { class: "ap-att-name" }, "Zephyrine ", h("span", { class: "ap-guest-flag" }, "guest"))),
  h("div", { class: "ap-att-team-sep" },
    h("span", { class: "ap-att-team-sep-name" }, "Red Foxes"),
    h("button", { class: "ap-att-plus", "data-kind": "team", "data-pop-name": "Red Foxes" }, "+1")),
  h("div", { class: "live-response-row" },
    h("span", { class: "live-response-name" }, "Birch"),
    h("span", { class: "live-response-answer" }, "Ava said Maple"),
    h("span", { class: "live-response-points" }, "+1")),
  h("div", { class: "live-response-row" },
    h("span", { class: "live-response-name" }, "Zephyrine"),
    h("span", { class: "live-response-answer" }, "42")),
  h("p", { class: "live-question" }, "Why did Ava plant a Maple?"),
  h("p", { class: "hint" }, "3 answers"));
applyMask(page, pairs, guests);
const q = (sel) => page.querySelectorAll(sel);
if (q(".ap-att-course")[0].textContent !== MASK) fail("COURSE masked");
if (q(".ap-att-game")[0].textContent !== MASK) fail("GAME masked");
if (q(".live-response-points")[0].textContent !== MASK) fail("popup points masked");
if (q(".ap-att-name")[0].textContent !== "Student 1") fail("class list name");
if (q(".ap-att-name")[0].getAttribute("title") !== "Student 1") fail("title masked");
const plus = q(".ap-att-plus");
if (plus[0].getAttribute("data-pop-name") !== "Student 1") fail("student pop name masked");
if (plus[0].getAttribute("aria-label") !== "Add one point") fail("generic aria-label kept");
if (plus[1].getAttribute("data-pop-name") !== "Red Foxes") fail("team pop name kept");
if (q(".ap-att-name")[1].textContent !== "Guest 1 guest") fail(`guest list: ${q(".ap-att-name")[1].textContent}`);
if (q(".live-response-name")[1].textContent !== "Guest 1") fail("guest in popup, same number");
if (q(".live-response-name")[0].textContent !== "Student 2") fail("popup name");
if (q(".live-response-answer")[0].textContent !== "Ava said Maple") fail("answer text unchanged");
if (q(".live-response-answer")[1].textContent !== "42") fail("answer numbers unchanged");
if (q(".live-question")[0].textContent !== "Why did Ava plant a Maple?") fail("question unchanged");
if (q(".hint")[0].textContent !== "3 answers") fail("answer counts stay");
if (q(".ap-att-team-sep-name")[0].textContent !== "Red Foxes") fail("team names stay");
if (new GuestBook(store).label("zephyrine") !== "Guest 1") fail("guest number persists");

restoreMask();
if (q(".ap-att-course")[0].textContent !== "4" || q(".ap-att-name")[0].textContent !== "Ava") fail("restore");
if (q(".ap-att-name")[0].getAttribute("title") !== "Ava") fail("restore attr");

if (STUDENT_VIEW_COPY.banner !== "Student view · names and points hidden") fail("banner copy");
if (STUDENT_VIEW_COPY.title !== "Hide names and points so you can share your screen") fail("tooltip copy");
console.log("ok");

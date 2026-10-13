// MCK-199: node harness for the Student view mask (no DOM needed).
import {
  MASK, STUDENT_VIEW_COPY, STUDENT_VIEW_KEY, maskNames, nameMasks,
  storeStudentView, studentNumbers, studentViewOn,
} from "./student_view.js";

const fail = (msg) => { console.error(`FAIL: ${msg}`); process.exit(1); };

// Stable numbering: by student id, whatever order a screen lists them in.
const roster = [
  { student_id: 42, first_name: "Zoe", last_name: "Park", codename: "Maple" },
  { id: 7, first_name: "Ava", last_name: "Li" },
  { student_id: 19, first_name: "Ben", last_name: "Ong", codename: "Birch" },
];
const a = studentNumbers(roster);
const b = studentNumbers([...roster].reverse());
if (a.get(7) !== 1 || a.get(19) !== 2 || a.get(42) !== 3) fail(`numbers ${[...a]}`);
if (JSON.stringify([...a]) !== JSON.stringify([...b])) fail("order must not matter");

// Masking names/codenames, whole words, longest first.
const pairs = nameMasks(roster);
const out = maskNames("Ava Li, Maple and Birch beat Ben. Avalon stays.", pairs);
if (out !== "Student 1, Student 3 and Student 2 beat Student 2. Avalon stays.") fail(`mask: ${out}`);
if (maskNames("Team Red · 3 answers", pairs) !== "Team Red · 3 answers") fail("teams/counts stay");
if (MASK !== "—") fail("points mask is an em dash");

// Persistence (per teacher browser, localStorage).
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

if (STUDENT_VIEW_COPY.banner !== "Student view · names and points hidden") fail("banner copy");
if (STUDENT_VIEW_COPY.title !== "Hide names and points so you can share your screen") fail("tooltip copy");
console.log("ok");

/**
 * MCK-170: Type chip row pure logic. Chip model (All first, counts, the
 * selected type kept at 0), the one-line fit with a More menu at 1280px
 * and 360px widths, and the per-view sessionStorage read/write.
 */
import assert from "node:assert/strict";
import { fitTypeChips, readStoredType, storeType, typeChipModel } from "./bank_type_chips.js";

let failures = 0;
function check(name, fn) {
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

if (failures) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("bank_type_chips.test.mjs: all passed");

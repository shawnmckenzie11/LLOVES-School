/**
 * MCK-169 S1: rank rows in Import from bank. Chip (Answer order vs
 * Opinion), preview in display order with "+{k} more", Answer-order-first
 * sort with the Rank chip on, and the import-default / none-keyed hints.
 */
import assert from "node:assert/strict";
import {
  RANK_ROW_COPY,
  hasAnswerOrder,
  rankChip,
  rankListHints,
  rankMetaParts,
  rankPreview,
  sortRankRows,
} from "./bank_rank_rows.js";

let failures = 0;
function check(name, fn) {
  try {
    fn();
  } catch (err) {
    failures += 1;
    console.error(`FAIL ${name}: ${err.message}`);
  }
}

const opts = (labels) => labels.map((label, i) => ({ id: `o${i + 1}`, label }));
const keyed = (id, labels, key) => ({ question_id: id, type: "rank", rank_options: opts(labels), options: labels, rank_key: key });
const opinion = (id, labels) => ({ question_id: id, type: "rank", rank_options: opts(labels), options: labels });
const mc = (id) => ({ question_id: id, type: "mc", options: ["a", "b"] });

check("copy is Wonder's, word for word", () => {
  assert.equal(RANK_ROW_COPY["bank.rank.chip.key"], "Answer order");
  assert.equal(RANK_ROW_COPY["bank.rank.chip.key.help"], "Has a correct order. Can run as a Team challenge.");
  assert.equal(RANK_ROW_COPY["bank.rank.chip.opinion"], "Opinion");
  assert.equal(RANK_ROW_COPY["bank.rank.chip.opinion.help"], "No right order. Shows how the class voted.");
  assert.equal(RANK_ROW_COPY["bank.rank.meta"], "Rank · {n} items · {chip}");
  assert.equal(RANK_ROW_COPY["bank.rank.import_default"], "Imports as Group · take turns. You can change it.");
  assert.equal(RANK_ROW_COPY["bank.rank.more"], "+{k} more");
  assert.equal(RANK_ROW_COPY["bank.rank.none_keyed"], "No answer-order rank questions in Module {m} yet.");
});

check("chip: rank_key gives Answer order, none gives Opinion", () => {
  assert.deepEqual(rankChip(keyed(1, ["a", "b", "c"], ["o2", "o1", "o3"])), {
    kind: "key",
    label: "Answer order",
    help: "Has a correct order. Can run as a Team challenge.",
  });
  assert.deepEqual(rankChip(opinion(2, ["a", "b", "c"])), {
    kind: "opinion",
    label: "Opinion",
    help: "No right order. Shows how the class voted.",
  });
  assert.equal(rankChip({ type: "rank", rank_key: [] }).kind, "opinion", "empty key is Opinion");
  assert.equal(hasAnswerOrder({ type: "mc", rank_key: ["o1"] }), false, "only rank rows");
});

check("meta splits around the chip", () => {
  assert.deepEqual(rankMetaParts(4), { before: "Rank · 4 items · ", after: "" });
});

check("preview: three options in display order, then +k more", () => {
  const item = keyed(1, ["Divide", "Subtract", "Check", "Write", "Graph"], ["o4", "o2", "o1", "o5", "o3"]);
  assert.deepEqual(rankPreview(item), { text: "Divide · Subtract · Check", more: "+2 more" });
  assert.deepEqual(rankPreview(opinion(2, ["x", "y", "z"])), { text: "x · y · z", more: "" });
  assert.deepEqual(rankPreview({ type: "rank", options: ["p", "q", "r", "s"] }), { text: "p · q · r", more: "+1 more" });
});

check("sort: Rank chip on puts Answer order first, stable within groups", () => {
  const rows = [opinion(1, ["a", "b", "c"]), keyed(2, ["a", "b", "c"], ["o1", "o2", "o3"]), opinion(3, ["a", "b", "c"]), keyed(4, ["a", "b", "c"], ["o3", "o2", "o1"])];
  assert.deepEqual(sortRankRows(rows, "rank").map((r) => r.question_id), [2, 4, 1, 3]);
  assert.deepEqual(sortRankRows(rows, "").map((r) => r.question_id), [1, 2, 3, 4], "All keeps server order");
  assert.deepEqual(sortRankRows([mc(9), ...rows], "mc").map((r) => r.question_id), [9, 1, 2, 3, 4]);
});

check("hints: import default only with Rank on and a keyed row listed", () => {
  const rows = [keyed(2, ["a", "b", "c"], ["o1", "o2", "o3"]), opinion(1, ["a", "b", "c"])];
  assert.deepEqual(rankListHints({ typeValue: "rank", items: rows, scope: "M2", mode: "import" }), {
    importDefault: "Imports as Group · take turns. You can change it.",
    noneKeyed: "",
  });
  assert.equal(rankListHints({ typeValue: "", items: rows, scope: "M2" }).importDefault, "", "All chip: no hint");
  assert.equal(rankListHints({ typeValue: "rank", items: rows, scope: "M2", mode: "browse" }).importDefault, "");
});

check("hints: none_keyed for a module with only Opinion rows", () => {
  const only = [opinion(1, ["a", "b", "c"])];
  assert.deepEqual(rankListHints({ typeValue: "rank", items: only, scope: "M5" }), {
    importDefault: "",
    noneKeyed: "No answer-order rank questions in Module 5 yet.",
  });
  assert.equal(rankListHints({ typeValue: "rank", items: [], scope: "m7" }).noneKeyed,
    "No answer-order rank questions in Module 7 yet.", "no rank rows at all");
  assert.equal(rankListHints({ typeValue: "rank", items: only, scope: "course" }).noneKeyed, "", "not Course Wide");
  assert.equal(rankListHints({ typeValue: "rank", items: only, scope: "M5", query: "x" }).noneKeyed, "", "not a typed search");
  assert.equal(rankListHints({ typeValue: "", items: only, scope: "M5" }).noneKeyed, "", "Rank chip off");
});

if (failures) {
  console.error(`bank_rank_rows.test.mjs: ${failures} failed`);
  process.exit(1);
}
console.log("bank_rank_rows.test.mjs: all passed");

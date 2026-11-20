/**
 * MCK-479 / MCK-478: result order, answer-key toggle, counts, one options list.
 */
import {
  histogramEdges,
  mcGraphSlot,
  mcResultsGraphHtml,
  NUMERIC_VALUE_BAR_MAX,
  numericResultsGraphHtml,
  orderMcChoices,
  planNumericResults,
  stripDuplicateOptionTable,
  tallyFromChoiceLabels,
} from "./mc_results_graph.js";

const fail = (msg) => {
  console.error(`FAIL: ${msg}`);
  process.exit(1);
};

const tally = {
  kind: "mc",
  choices: [
    { id: "A", label: "north", count: 1, pct: 25, correct: false },
    { id: "B", label: "east", count: 3, pct: 75, correct: true },
    { id: "C", label: "south", count: 0, pct: 0, correct: false },
  ],
};

const hidden = mcResultsGraphHtml(tally, {
  order: "default",
  showKey: false,
  controls: true,
  itemKey: "item:4",
});
if (!hidden.includes('data-mc-order="default"')) fail("default order is not the starting order");
if (!hidden.includes('aria-pressed="true">Option order')) fail("option order toggle is not pressed");
if (!hidden.includes('aria-pressed="false">Most picked')) fail("most picked starts off");
if (!hidden.includes(">Show answer<")) fail("answer starts hidden");
if (hidden.includes("is-correct") || hidden.includes("Correct")) fail("hidden key still marks the answer");
if (!hidden.includes(">A<") || hidden.indexOf(">A<") > hidden.indexOf(">B<")) {
  fail("default order is not A then B");
}
if (!hidden.includes("1 · 25%") || !hidden.includes("3 · 75%") || !hidden.includes("0 · 0%")) {
  fail(`counts missing: ${hidden}`);
}
if (hidden.includes("live-question-card-options") || hidden.includes("<table")) {
  fail("graph rendered a second options table");
}

const ranked = mcResultsGraphHtml(tally, { order: "ranked", showKey: false, controls: true, itemKey: "item:4" });
if (!ranked.includes('data-mc-order="ranked"')) fail("ranked toggle did not stick");
if (ranked.indexOf(">east<") > ranked.indexOf(">north<")) fail("most picked did not move the high count first");
const rankedRows = orderMcChoices(tally.choices, "ranked");
if (rankedRows[0].label !== "east" || rankedRows[1].label !== "north" || rankedRows[2].label !== "south") {
  fail(`ranked order ${rankedRows.map((row) => row.label).join(",")}`);
}

const shown = mcResultsGraphHtml(tally, { order: "default", showKey: true, controls: true, itemKey: "item:4" });
if (!shown.includes(">Hide answer<")) fail("show-answer toggle did not flip its label");
if (!shown.includes('aria-pressed="true"')) fail("show-answer is not pressed");
const correctAt = shown.indexOf('class="mc-results-row is-correct"');
if (correctAt < 0) fail("correct bar is not highlighted");
const correctSlice = shown.slice(correctAt, correctAt + 400);
if (!correctSlice.includes(">east<") || !correctSlice.includes("Correct")) fail("highlight is not on the keyed option");
if ((shown.match(/is-correct/g) || []).length !== 1) fail("more than the keyed option is highlighted");

const tied = orderMcChoices(
  [
    { id: "A", label: "north", count: 2 },
    { id: "B", label: "east", count: 2 },
  ],
  "ranked"
);
if (tied[0].id !== "A" || tied[1].id !== "B") fail("equal counts did not keep authored order");

const stem = '<p>Which way?</p><table><tr><td>north</td><td>east</td></tr><tr><td>south</td><td>west</td></tr></table>';
const stripped = stripDuplicateOptionTable(stem, ["north", "east", "south", "west"]);
if (stripped.includes("<table") || stripped.includes("north")) fail("options table was left in the stem");
if (!stripped.includes("Which way?")) fail("stem question was removed with the options table");
const dataTable = '<p>Read the table.</p><table><tr><td>Hours</td><td>1</td></tr><tr><td>Grade</td><td>2</td></tr></table>';
const kept = stripDuplicateOptionTable(dataTable, ["1", "2", "3", "4"]);
if (!kept.includes("<table") || !kept.includes("Hours")) fail("data table was treated as the options table");

const body = `${stripped}${hidden}`;
if (body.includes("live-question-card-options")) fail("teacher MC still renders an options list");
if ((body.match(/<table/gi) || []).length !== 0) fail("body still has an options table");
if ((body.match(/mc-results-graph/g) || []).length !== 1) fail("results graph rendered more than once");

const counted = tallyFromChoiceLabels(
  ["north", "east", "south"],
  {
    status_board: [{ picks: [{ value: "east" }, { value: "east" }, { value: "north" }] }],
    reveal: [{ answer: "south" }],
  },
  "B"
);
if (!counted || counted.choices.map((row) => row.count).join(",") !== "1,2,1") {
  fail(`choice counts ${JSON.stringify(counted)}`);
}
if (counted.choices[1].correct !== true || counted.choices[0].correct !== false) fail("key letter did not mark B");
if (JSON.stringify(counted).includes("name")) fail("tally copied a name field");

const again = mcGraphSlot(hidden, "<section class=\"mc-results-graph\">second</section>");
if (again !== "") fail("a card that already has a graph painted a second one");
const first = mcGraphSlot("<p>Group status</p>", hidden);
if (!first.includes("mc-results-graph") || first.includes("Group status")) fail("missing graph was not filled in");

if (mcResultsGraphHtml({ choices: [] }, { controls: true })) fail("empty tally still rendered a graph");

console.log("ok");


const numericBars = {
  kind: "numeric",
  choices: [
    { id: "12", label: "12", count: 1, correct: false },
    { id: "8.2", label: "8.2", count: 1, correct: false },
    { id: "8", label: "8", count: 2, correct: true },
    { id: "2", label: "2", count: 1, correct: false },
    { id: "5", label: "5", count: 4, correct: false },
    { id: "other", label: "Other", count: 2, other: true, correct: false },
  ],
};
const bars = numericResultsGraphHtml(numericBars, {
  order: "default",
  showKey: true,
  controls: true,
  correctValue: 8,
  tolerance: 0.5,
});
if (!bars.includes('data-mc-mode="bars"')) fail("small numeric tally is not value bars");
if (!bars.includes('aria-pressed="true">Number order')) fail("number order is not the default");
const barLabels = [...bars.matchAll(/mc-results-label">([^<]+)/g)].map((m) => m[1]);
if (barLabels.join(",") !== "2,5,8,8.2,12") fail(`numeric order ${barLabels.join(",")}`);
if ((bars.match(/is-correct/g) || []).length !== 2) fail("tolerance window did not mark both bars");
if (!bars.includes(">8.2<")) fail("within-tolerance bar missing");
if (!bars.includes("Other · 2")) fail("other count missing");
if (bars.includes('mc-results-label">Other')) fail("other became a bar");
const rankedBars = numericResultsGraphHtml(numericBars, { order: "ranked", showKey: false });
const rankedLabels = [...rankedBars.matchAll(/mc-results-label">([^<]+)/g)].map((m) => m[1]);
if (rankedLabels.join(",") !== "5,8,2,8.2,12") fail(`most picked ${rankedLabels.join(",")}`);
if (rankedBars.includes("is-correct")) fail("hidden numeric key is highlighted");

const many = [];
for (let n = 1; n <= 12; n += 1) {
  many.push({ id: String(n), label: String(n), count: n === 7 ? 5 : 1, correct: n === 7 });
}
if (many.length <= NUMERIC_VALUE_BAR_MAX) fail("fixture is not past the bar threshold");
const hist = numericResultsGraphHtml(
  { kind: "numeric", choices: many },
  { order: "default", showKey: true, controls: true, correctValue: 7 }
);
if (!hist.includes('data-mc-mode="histogram"')) fail("many values did not bin");
const histLabels = [...hist.matchAll(/mc-results-label">([^<]+)/g)].map((m) => m[1]);
if (histLabels.length < 6 || histLabels.length > 10) fail(`bin count ${histLabels.length}`);
const plan = planNumericResults({ choices: many }, { correctValue: 7 });
const binSum = plan.rows.reduce((sum, row) => sum + row.count, 0);
if (binSum !== many.reduce((sum, row) => sum + row.count, 0)) fail("histogram dropped counts");
const marked = plan.rows.filter((row) => row.correct);
if (marked.length !== 1 || !(marked[0].start <= 7 && marked[0].end > 7)) {
  fail("key bin was not the only highlighted bin");
}
const edges = histogramEdges(0, 100);
if (edges.step !== 10 || edges.count !== 10 || edges.start !== 0) {
  fail(`0..100 edges ${JSON.stringify(edges)}`);
}
const spread = [];
for (let n = 0; n <= 100; n += 10) {
  spread.push({ id: String(n), label: String(n), count: 1, correct: n === 40 });
}
const spreadPlan = planNumericResults({ choices: spread }, { correctValue: 40 });
if (spreadPlan.mode !== "histogram") fail("0..100 stayed as bars");
if (spreadPlan.rows.length < 6 || spreadPlan.rows.length > 10) fail("0..100 bin count");
const step = spreadPlan.rows[1].start - spreadPlan.rows[0].start;
if (step !== 10 || spreadPlan.rows[0].start !== 0) fail(`0..100 step ${step} start ${spreadPlan.rows[0].start}`);
if (spreadPlan.rows.some((row, i) => i && row.start !== spreadPlan.rows[0].start + i * step)) {
  fail("bin edges are not regular");
}
if (spreadPlan.rows.reduce((s, r) => s + r.count, 0) !== spread.length) fail("spread counts");
const widthLabels = mcResultsGraphHtml(
  {
    kind: "mc",
    choices: [
      { id: "A", label: "128", count: 2, pct: 40, correct: false },
      { id: "B", label: "4096", count: 1, pct: 20, correct: true },
      {
        id: "C",
        label: "A harbour wind that keeps going past twenty four",
        count: 2,
        pct: 40,
        correct: false,
      },
    ],
  },
  { showKey: false, markKey: true, controls: false }
);
if (!widthLabels.includes(">128<") || !widthLabels.includes(">4096<")) fail("short numbers were dropped");
const fourAt = widthLabels.indexOf(">4096<");
const fourTag = widthLabels.slice(widthLabels.lastIndexOf("mc-results-row", fourAt), fourAt);
if (fourTag.includes("is-long")) fail("4-digit answer was marked for ellipsis");
if (!fourTag.includes("is-correct")) fail("teacher mark stayed off when Show answer is off");
const longAt = widthLabels.indexOf("harbour wind");
const longTag = widthLabels.slice(widthLabels.lastIndexOf("mc-results-row", longAt), longAt);
if (!longTag.includes("is-long")) fail("long text was not expandable");
const eight = Array.from({ length: 8 }, (_, i) => ({ id: String(i), label: String(i), count: 1 }));
const nine = Array.from({ length: 9 }, (_, i) => ({ id: String(i), label: String(i), count: 1 }));
if (planNumericResults({ choices: eight }).mode !== "bars") fail("eight values should stay bars");
if (planNumericResults({ choices: nine }).mode !== "histogram") fail("nine values should bin");
const keyOnly = planNumericResults(
  { choices: spread.map((row) => ({ ...row, correct: false })) },
  { correctValue: 40 }
);
if (!keyOnly.rows.some((row) => row.correct && row.start <= 40 && row.end > 40)) {
  fail("unanswered key did not highlight its bin");
}

/**
 * MCK-479 / MCK-478: result order, answer-key toggle, counts, one options list.
 */
import {
  mcGraphSlot,
  mcResultsGraphHtml,
  orderMcChoices,
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

const optionsList = '<ol class="live-question-card-options" type="A"><li>north</li><li>east</li><li>south</li><li>west</li></ol>';
const body = `${stripped}${optionsList}${hidden}`;
if ((body.match(/live-question-card-options/g) || []).length !== 1) fail("options list rendered more than once");
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

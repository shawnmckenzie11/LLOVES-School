/**
 * MCK-484: the student graph is the teacher graph, with no controls.
 * The correct mark appears only when showKey is on.
 */
import { mcResultsGraphHtml, numericResultsGraphHtml } from "./mc_results_graph.js";

const fail = (msg) => {
  console.error(`FAIL: ${msg}`);
  process.exit(1);
};

const mc = {
  kind: "mc",
  choices: [
    { id: "A", label: "kite", count: 1, pct: 50, correct: true },
    { id: "B", label: "moth", count: 1, pct: 50, correct: false },
  ],
};

const hidden = mcResultsGraphHtml(mc, { order: "default", showKey: false, controls: false });
if (!hidden.includes("mc-results-graph")) fail("student MC graph did not render");
if (hidden.includes("is-correct") || hidden.includes("Correct")) fail("hidden MC graph marks the key");
if (hidden.includes("Show answer") || hidden.includes('data-mc-key="1"')) fail("student MC graph has teacher controls");
if (!hidden.includes("1 · 50%")) fail("student MC graph dropped the count");

const shown = mcResultsGraphHtml(mc, { order: "ranked", showKey: true, controls: false });
if (!shown.includes('class="mc-results-row is-correct"')) fail("shown MC graph does not mark the key");
if (!shown.includes(">Correct<")) fail("shown MC graph does not say Correct");
if (shown.includes("Show answer") || shown.includes("Hide answer")) fail("shown MC graph has a toggle");

const numeric = {
  kind: "numeric",
  choices: [
    { id: "1", label: "1", count: 1, pct: 50, correct: false },
    { id: "3", label: "3", count: 1, pct: 50, correct: false },
  ],
};
const numbers = numericResultsGraphHtml(numeric, { showKey: false, controls: false });
if (!numbers.includes("mc-results-graph")) fail("student numeric graph did not render");
if (numbers.includes("is-correct") || numbers.includes("Correct")) fail("numeric graph marked a key");
if (numbers.includes("2.5")) fail("numeric graph invented the key");
if (!numbers.includes(">1<") || !numbers.includes(">3<")) fail("numeric graph dropped a submitted value");

console.log("ok");

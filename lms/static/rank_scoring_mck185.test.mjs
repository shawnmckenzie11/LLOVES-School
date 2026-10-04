// MCK-185 spots results + teacher Scoring rows (node harness, no DOM).
import {
  RESULTS_COPY,
  lastResultsStep,
  raceResultsHtml,
  rankScoringHtml,
  spotsResults,
} from "./rank_challenge_view.js";
import { phoneResultsHtml } from "./rank_challenge_phone.js";

let failures = 0;
function check(cond, label) {
  if (!cond) {
    failures += 1;
    console.error(`FAIL ${label}`);
  }
}

const spots = {
  challenge: false,
  pays: false,
  total: 4,
  podium_step: 5,
  spots: [
    { n: 1, item: "An equation", teams: { 1: true, 2: false } },
    { n: 2, item: "Two points", teams: { 1: true, 2: false } },
    { n: 3, item: "A table", teams: { 1: true, 2: true } },
    { n: 4, item: "A graph", teams: { 1: false, 2: true } },
  ],
  teams: [
    { team_id: 1, team_name: "Cosines", slot: 0, right: 3, total: 4, points: 0, scored: true, awarded_points: null },
    { team_id: 2, team_name: "Tangents", slot: 1, right: 2, total: 4, points: 0, scored: true, awarded_points: 5 },
    { team_id: 3, team_name: "Sines", slot: 2, right: 0, total: 4, points: 0, scored: false, awarded_points: null },
  ],
  podium: {
    steps: [
      { step: 1, points: 3, right: 3, team_ids: [1] },
      { step: 2, points: 2, right: 2, team_ids: [2] },
    ],
    others: [],
  },
};
const challenge = { ...spots, challenge: true, pays: true };

// Steps: rows, then the podium (no Points step) for spots; 171 unchanged.
check(spotsResults(spots) && !spotsResults(challenge), "spotsResults");
check(lastResultsStep(spots) === 5, "spots last step is n+1");
check(lastResultsStep(challenge) === 6, "challenge last step is n+2");
check(lastResultsStep({ total: 4 }) === 6, "older payload keeps n+2");
const afterRows = raceResultsHtml(spots, 9, { step: 5, scoringHtml: "<b>SCORING</b>" });
check(afterRows.includes("race-podium"), "n+1 is the podium for spots");
check(!afterRows.includes("race-points"), "no Points frame for spots");
check(afterRows.includes("SCORING"), "Scoring under the podium on the last step");
check(afterRows.includes("3 of 4 spots") && !afterRows.includes("points each"), "podium shows spots, not points");
check(raceResultsHtml(challenge, 9, { step: 5 }).includes("race-points"), "challenge keeps Points");
check(!raceResultsHtml(spots, 9, { step: 3, scoringHtml: "<b>SCORING</b>" }).includes("SCORING"), "no Scoring mid-reveal");
check(raceResultsHtml(spots, 9, { step: 4 }).includes('data-race-next="9"'), "Next while rows remain");
check(raceResultsHtml(spots, 9, { step: 5 }).includes("data-race-done"), "Back to question at the end");
const others = raceResultsHtml(
  { ...spots, podium: { steps: [{ step: 1, points: 3, right: 3, team_ids: [1] }], others: [2] } },
  9,
  { step: 5 }
);
check(others.includes("Also on the board: Tangents · 2 of 4 spots"), "others line shows spots");

// Scoring rows: Class list team order, hint, chips, +n, No order sent.
const html = rankScoringHtml(spots, 9, {
  controls: (id) => `<i data-chips="${id}"></i>`,
  teamOrder: [2, 1, 3],
});
check(html.includes('data-rank-scoring="9"'), "scoring host");
check(html.indexOf("Tangents") < html.indexOf("Cosines"), "Class list team order");
check(html.includes("3 of 4 spots right") && html.includes("2 of 4 spots right"), "spot hint");
check(html.includes('data-chips="1"') && html.includes('data-chips="2"'), "chips for groups that sent");
check(!html.includes('data-chips="3"'), "no chips without an order");
check(html.includes(RESULTS_COPY.noOrder), "No order sent");
check(html.includes(">+5<"), "+n after an award");
check(!html.includes("checked") && !html.includes("is-on"), "nothing preselected");
const hidden = rankScoringHtml(spots, 9, { hideKey: true, controls: (id) => `<i data-chips="${id}"></i>` });
check(!hidden.includes("spots right") && hidden.includes('data-chips="1"'), "Hide key hides the hint, keeps chips");
check(rankScoringHtml(challenge, 9, { controls: () => "x" }) === "", "no manual row in a Team challenge");
check(RESULTS_COPY.spotsHint === "{k} of {n} spots right", "hint copy");
check(RESULTS_COPY.noOrder === "No order sent", "none copy");

// Phone: own results, no points, mini podium once shown.
const phone = phoneResultsHtml({
  team_name: "Tangents",
  race: {
    slot: 1,
    challenge: false,
    results: {
      spots: [{ n: 1, item: "A graph", right: false, answer: "An equation" }],
      right: 2,
      total: 4,
      points: null,
      show_points: false,
      show_podium: true,
      on_podium: true,
      from_draft: false,
      podium: [{ step: 2, points: 2, teams: [{ name: "Tangents", mine: true }] }],
    },
  },
});
check(phone.includes("2 of 4 spots right."), "phone cue");
check(!phone.includes("points each") && !phone.includes("point each"), "phone shows no points");
check(phone.includes("race-mini-podium"), "phone mini podium");

if (failures) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ok");

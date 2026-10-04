// MCK-185: results on every group answer-order rank, the "Score teams"
// pop-up, and the full-order line (node harness, no DOM).
import {
  SCORE_COPY,
  SCORE_RULES,
  lastResultsStep,
  raceResultsHtml,
  scoreTeamsDialogHtml,
  scoreTeamsPending,
  scoreTeamsRows,
  spotsResults,
} from "./rank_challenge_view.js";
import { phoneResultsHtml } from "./rank_challenge_phone.js";
import { FULL_ORDER_COPY, fullOrderHtml, fullOrderText, joinTeamNames } from "./rank_full_order.js";

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
    { team_id: 1, team_name: "Cosines", slot: 0, right: 3, total: 4, points: 0, auto: 6, scored: true, present: true, assigned: null },
    { team_id: 2, team_name: "Tangents", slot: 1, right: 2, total: 4, points: 0, auto: 4, scored: true, present: true, assigned: { points: 4, team_rule: "each_member" } },
    { team_id: 3, team_name: "Sines", slot: 2, right: 0, total: 4, points: 0, auto: 0, scored: false, present: true, assigned: null },
    { team_id: 4, team_name: "Radians", slot: 3, right: 0, total: 4, points: 0, auto: 0, scored: false, present: false, assigned: null },
  ],
  podium: {
    steps: [
      { step: 1, points: 3, right: 3, team_ids: [1] },
      { step: 2, points: 2, right: 2, team_ids: [2] },
    ],
    others: [],
  },
};
const challenge = { ...spots, challenge: true };
const paying = { ...spots, challenge: true, pays: true };

// Steps: rows, then the podium; a Team challenge too (no Points step).
check(spotsResults(spots) && spotsResults(challenge) && !spotsResults(paying), "hand-scored results");
check(lastResultsStep(spots) === 5 && lastResultsStep(challenge) === 5, "last step is n+1");
check(lastResultsStep(paying) === 6, "MCK-171 payout on keeps n+2");
check(lastResultsStep({ total: 4 }) === 6, "older payload keeps n+2");
const podium = raceResultsHtml(challenge, 9, { step: 5 });
check(podium.includes("race-podium") && !podium.includes("race-points"), "n+1 is the podium, no Points frame");
check(podium.includes("3 of 4 spots") && !podium.includes("points each"), "podium ranks spots, not points");
check(podium.includes('data-rank-score-open="9"') && podium.includes(">Score teams<"), "Score teams in the header");
check(podium.indexOf("data-rank-score-open") < podium.indexOf("</header>"), "button sits in the results header");
check(!raceResultsHtml(spots, 9, { step: 4 }).includes("data-rank-score-open"), "no Score teams mid-reveal");
check(!raceResultsHtml(paying, 9, { step: 6 }).includes("data-rank-score-open"), "no pop-up when MCK-171 pays");
check(raceResultsHtml(spots, 9, { step: 5, footHtml: "<p>FOOT</p>" }).includes("FOOT"), "full-order line under results");
check(podium.includes('aria-label="Team challenge results"'), "challenge label");

// Pop-up rows: Class list order, defaults 2 per spot, states.
const rows = scoreTeamsRows(spots, { teamOrder: [2, 1, 3, 4] });
check(rows.map((r) => r.teamId).join() === "2,1,3", "Class list order; absent team not listed");
check(rows[1].value === 6 && rows[1].auto === 6 && rows[1].state === "new", "default is 2 per right spot");
check(rows[0].state === "assigned" && rows[0].value === 4, "assigned row");
check(rows[2].state === "none" && rows[2].value === 0, "no order sent: 0");
check(scoreTeamsPending(rows).map((r) => r.teamId).join() === "1", "Assign all: new rows only");
const changed = scoreTeamsRows(spots, { teamOrder: [2, 1, 3], drafts: new Map([[2, 5]]) });
check(changed[0].state === "changed" && changed[0].value === 5, "a changed assigned row");
check(scoreTeamsPending(changed).length === 2, "Assign all counts new and changed rows");

const html = scoreTeamsDialogHtml(spots, 9, { stem: "Order the steps", teamOrder: [2, 1, 3, 4] });
check(html.includes(">Score teams<") && html.includes("Order the steps"), "title and stem");
check(html.includes(SCORE_COPY.help), "help line");
check(html.includes("3 of 4 spots") && html.includes("2 of 4 spots"), "k of n spots");
check(html.includes('value="6"') && html.includes('value="4"'), "stepper values");
check(html.includes("Assigned · 4 each"), "assigned label");
check(html.includes(">Assign<"), "assign button");
check(html.includes("No order sent"), "no-order row");
const noneRow = html.slice(html.indexOf('data-score-team="3"'));
check(!noneRow.slice(0, noneRow.indexOf("</li>")).includes("data-score-value"), "no stepper without an order");
check(!html.includes("Radians"), "absent team not listed");
check(html.includes("Assign all (1)"), "Assign all count");
check(!html.includes("(auto"), "no (auto n) until changed");
check(SCORE_RULES.map((r) => r.id).join() === "each_member,split_members,team_only", "Give as = TEAM_RULES");
check(html.includes('<option value="each_member" selected>'), "Each member by default");
check(html.includes("data-score-close"), "Close");
const edited = scoreTeamsDialogHtml(spots, 9, { teamOrder: [2, 1, 3], drafts: new Map([[2, 5], [1, 7]]) });
check(edited.includes(">Update<"), "Update after a change on an assigned row");
check(edited.includes("(auto 4)") && edited.includes("(auto 6)"), "(auto n) after a change");
check(edited.includes("Assign all (2)"), "Assign all counts changed rows");
const one = scoreTeamsDialogHtml(
  { ...spots, teams: [{ ...spots.teams[0], assigned: { points: 1, team_rule: "each_member" } }] },
  9,
  { drafts: new Map() }
);
check(one.includes("Assigned · 1 each"), "1 each");
check(scoreTeamsDialogHtml(spots, 9, { busy: true }).includes("Assign all (1)</button>") , "busy still renders");

// Phone: "{pts} points each" once assigned, as in MCK-171.
const phone = (points) =>
  phoneResultsHtml({
    team_name: "Tangents",
    race: {
      slot: 1,
      challenge: false,
      results: {
        spots: [{ n: 1, item: "A graph", right: false, answer: "An equation" }],
        right: 2,
        total: 4,
        points,
        show_points: points !== null,
        credited: true,
        show_podium: true,
        on_podium: true,
        from_draft: false,
        podium: [{ step: 2, points: 2, teams: [{ name: "Tangents", mine: true }] }],
      },
    },
  });
check(!phone(null).includes("points each"), "phone: nothing before Assign");
check(phone(4).includes("4 points each") && phone(1).includes("1 point each"), "phone: points each after Assign");

// Full-order line (Wonder copy, joining rules).
check(joinTeamNames(["Maple", "Aspen"]) === "Maple and Aspen", "two names");
check(joinTeamNames(["Maple", "Aspen", "Birch"]) === "Maple, Aspen and Birch", "three names, no Oxford comma");
check(joinTeamNames(["Maple", "Aspen", "Birch", "Cedar"]) === "Maple, Aspen and 2 more teams", "4+ names");
check(fullOrderText({ you: false, teams: [{ name: "Maple" }] }) === "Maple put every item in the right order.", "one");
check(
  fullOrderText({ you: false, teams: [{ name: "Maple" }, { name: "Aspen" }] }) === "Maple and Aspen put every item in the right order.",
  "many"
);
check(fullOrderText({ you: true, teams: [] }) === "Your team put every item in the right order.", "you");
check(
  fullOrderText({ you: true, teams: [{ name: "Maple" }, { name: "Aspen" }] }) ===
    "Your team and Maple and Aspen put every item in the right order.",
  "you_many (others only)"
);
check(fullOrderText({ you: false, teams: [] }) === "" && fullOrderHtml(null) === "", "nothing when no team got it all");
const pill = fullOrderHtml({ you: true, slot: 1, teams: [{ name: "Maple", slot: 2 }] });
check(pill.includes("full-order-line") && (pill.match(/full-order-dot"/g) || []).length === 2, "pill with colour dots");
check(!/[!🎉]/u.test(pill) && !pill.includes("confetti"), "calm: no exclamation, emoji or confetti");
check(Object.keys(FULL_ORDER_COPY).join() === "one,many,you,youMany,more", "copy keys");

if (failures) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ok");

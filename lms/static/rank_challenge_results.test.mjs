// MCK-171 results moment (projector + phone), node harness.
import { RESULTS_COPY, lastResultsStep, pointsText, raceResultsHtml, rightText } from "./rank_challenge_view.js";
import { PHONE_RESULTS_COPY, phoneResultsHtml } from "./rank_challenge_phone.js";

let failures = 0;
function check(cond, label) {
  if (!cond) {
    failures += 1;
    console.error(`FAIL ${label}`);
  }
}

// Wonder v3 final keys.
check(RESULTS_COPY.revealTitle === "The right order", "results.reveal.title");
check(RESULTS_COPY.pointsTitle === "Points", "results.points.title");
check(RESULTS_COPY.podiumTitle === "Top teams", "results.podium.title");
check(RESULTS_COPY.points === "{pts} points each" && RESULTS_COPY.pointsOne === "1 point each", "results.points(.one)");
check(RESULTS_COPY.spotChip === "Spot {k} of {n}" && RESULTS_COPY.fromDraft === "Scored from your group's draft.", "results.spot.chip / from_draft");
check(PHONE_RESULTS_COPY.pill === "Results" && PHONE_RESULTS_COPY.fromDraft === RESULTS_COPY.fromDraft, "results.pill / phone from_draft");
check(RESULTS_COPY.tie === "{n} teams tied", "results.tie");
check(RESULTS_COPY.done === "Back to question", "results.done");
check(RESULTS_COPY.next === "Next" && RESULTS_COPY.others === "Also on the board", "next / others");
check(PHONE_RESULTS_COPY.answer === "Answer: {item}", "results.spot.right_item");
check(PHONE_RESULTS_COPY.podium === "Your team made the podium." && PHONE_RESULTS_COPY.you === "Your team", "podium / you");
check(pointsText(1) === "1 point each" && pointsText(6) === "6 points each" && pointsText(0) === "0 points each", "points text");
check(rightText(3, 5) === "3 of 5 spots right." && rightText(5, 5) === "All 5 spots right.", "right text");
const allCopy = JSON.stringify(RESULTS_COPY) + JSON.stringify(PHONE_RESULTS_COPY);
check(!/race|bonus|fastest|best move|hardest|first/i.test(allCopy), "no race / bonus / speed words");

// Example class (mock v1): Secants 10, Tangents 6, Cosines 6 (tie), Radians 4,
// Vectors 2 (draft), Sines placed nothing.
const T = (id, name, slot, right, extra = {}) => ({ team_id: id, team_name: name, slot, right, total: 5, points: 2 * right, scored: true, locked: true, ...extra });
const results = {
  total: 5,
  teams: [
    T(1, "Tangents", 0, 3), T(2, "Secants", 1, 5), T(3, "Cosines", 2, 3), T(4, "Radians", 3, 2),
    T(5, "Vectors", 4, 1, { locked: false }), { team_id: 6, team_name: "Sines", slot: 5, right: 0, total: 5, points: 0, scored: false },
  ],
  spots: [1, 2, 3, 4, 5].map((n) => ({ n, item: ["¼", "2/7", "29%", "0.3", "⅓"][n - 1], teams: { 1: n === 1 || n === 4 || n === 5, 2: true, 3: n < 4, 4: n < 3, 5: n === 2 } })),
  podium: { steps: [{ step: 1, points: 10, team_ids: [2] }, { step: 2, points: 6, team_ids: [3, 1] }, { step: 3, points: 4, team_ids: [4] }], others: [6, 5] },
};
check(lastResultsStep(results) === 7, "steps: 5 rows + points + podium");
const r0 = raceResultsHtml(results, 9, { step: 0, stem: "Put these in order." });
check(r0.includes("The right order") && r0.includes("Spot 1 · ?") && !r0.includes("¼"), "step 0: nothing revealed");
check(!r0.includes("Spot 0 of") && !r0.includes("Spot 1 of 5"), "no chip before the first row");
const r4 = raceResultsHtml(results, 9, { step: 4 });
check(r4.includes("Spot 4 · 0.3") && r4.includes("Spot 5 · ?") && !r4.includes("⅓"), "rows one by one");
check(r4.includes("Spot 4 of 5"), "spot chip");
check(r4.includes('aria-label="right"') && r4.includes('aria-label="not this one"'), "✓ and ring");
check(!r4.includes("✗") && !r4.includes("Ava"), "no red ✗, no names");
check(r4.includes('aria-label="no answer">—'), "— for a team that placed nothing");
check(r4.includes('data-race-next="9"') && r4.includes("Next ▸"), "Next");
const pts = raceResultsHtml(results, 9, { step: 6 });
check(pts.includes(">Points<") && pts.includes("5 of 5 spots right.") === false && pts.includes("All 5 spots right."), "points: all right");
check(pts.includes("3 of 5 spots right.") && pts.includes("10 points each") && pts.includes("2 points each"), "points bars");
const draftNotes = pts.split("race-bar-note").length - 1;
check(draftNotes === 1 && pts.indexOf("Vectors") < pts.indexOf("Scored from your group"), "from-draft note on the unlocked team only");
check(pts.indexOf("Tangents") < pts.indexOf("Secants") && pts.indexOf("Secants") < pts.indexOf("Cosines"), "fixed team order");
check(pts.includes("No one here yet") && !/bonus|gold/i.test(pts), "absent row, base only");
const pod = raceResultsHtml(results, 9, { step: 7 });
check(pod.includes("Top teams") && pod.includes("2 teams tied") && pod.includes("6 points"), "podium tie");
check(pod.includes("Also on the board: Sines 0 points each · Vectors 2 points each"), "others alphabetical, each its own points");
check(!/\b(4th|5th|6th)\b/.test(pod), "nobody ranked below 3");
check(pod.includes('data-race-done="9"') && pod.includes("Back to question") && !pod.includes("data-race-next"), "done at the end");
check(raceResultsHtml(results, 9, { step: 99 }).includes("Top teams"), "step clamps");

// Phone results.
const group = (res) => ({ team_name: "Cosines", members: ["Gus", "Hana", "Ivy"], race: { mode: "together", slot: 2, results: res } });
const own = {
  total: 5, right: 3, points: 6, scored: true, on_podium: true,
  spots: [
    { n: 1, item: "¼", right: true, answer: "" }, { n: 2, item: "2/7", right: true, answer: "" }, { n: 3, item: "29%", right: true, answer: "" },
    { n: 4, item: "⅓", right: false, answer: "0.3" }, { n: 5, item: "0.3", right: false, answer: "⅓" },
  ],
  podium: [
    { step: 1, points: 10, teams: [{ name: "Secants", slot: 1, mine: false }] },
    { step: 2, points: 6, teams: [{ name: "Tangents", slot: 0, mine: false }, { name: "Cosines", slot: 2, mine: true }] },
    { step: 3, points: 4, teams: [{ name: "Radians", slot: 3, mine: false }] },
  ],
};
const ph = phoneResultsHtml(group(own));
check(ph.includes("3 of 5 spots right.") && ph.includes("Spot 4 · ⅓") && ph.includes("Answer: 0.3"), "phone spots");
check(!ph.includes("Answer: ¼"), "no answer line on a right spot");
check(ph.includes("Your team") && ph.includes("6 points each") && ph.includes("Your team made the podium."), "on podium");
check(ph.includes(">Results<") && !ph.includes("race-phone-draft"), "Results pill, no draft line when locked");
const held = phoneResultsHtml(group({ ...own, points: null, podium: [], on_podium: false }));
check(held.includes("3 of 5 spots right.") && !held.includes("Your team") && !held.includes("race-mini-podium"), "points and podium held until the projector step");
check(phoneResultsHtml(group({ ...own, from_draft: true })).includes("race-phone-draft\">Scored from your group"), "phone from-draft line");
check(ph.includes("is-mine") && ph.includes("race-phone-burst"), "own step outlined + small burst");
const off = phoneResultsHtml(group({ ...own, right: 1, points: 2, on_podium: false, podium: own.podium.map((s) => ({ ...s, teams: s.teams.map((t) => ({ ...t, mine: false })) })) }));
check(!off.includes("made the podium") && !off.includes("race-phone-burst") && !off.includes("is-mine"), "off podium: calm, no place");
check(phoneResultsHtml(group({ ...own, right: 5, points: 10 })).includes("All 5 spots right."), "all right");
check(!/\b(4th|5th|6th|place)\b/i.test(off), "no place number");

if (failures) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ok");

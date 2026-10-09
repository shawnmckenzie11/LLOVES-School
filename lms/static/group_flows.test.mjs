/**
 * MCK-155 PR C node checks: option B MC steps and the take-turns card.
 */
import {
  GROUP_FLOW_COPY,
  mcAgreeStepHtml,
  mcFlowStep,
  mcLockedHtml,
  mcMemberPicksHtml,
  mcPickStepHtml,
  rankTurnCue,
  rankTurnsHtml,
  stillChoosingLine,
  turnsCardIsStale,
} from "./group_flows.js";
import { groupSetupHtml, groupSetupOptionsHtml, groupSubmitVariant, rankModeHtml } from "./group_setup.js";

let failures = 0;
function check(cond, msg) {
  if (!cond) {
    failures += 1;
    console.error(`FAIL: ${msg}`);
  }
}

// Wonder v2 copy, exact.
check(GROUP_FLOW_COPY.pickButton === "Send my pick", "pick button");
check(GROUP_FLOW_COPY.stillChoosing === "Still choosing: {names}", "still choosing");
check(GROUP_FLOW_COPY.whyPlaceholder === "Why this answer?", "why placeholder");
check(GROUP_FLOW_COPY.sendReason === "Add your why to send.", "send reason");
check(GROUP_FLOW_COPY.locked === "Your group's answer is locked.", "locked");
check(GROUP_FLOW_COPY.blocked === "You've placed one. Waiting for {names}.", "blocked");
check(GROUP_FLOW_COPY.done === "Your group's order is in.", "done");

// MC steps.
const flow = { flow: "pick_then_agree" };
check(mcFlowStep({}) === "", "not option B");
check(mcFlowStep({ ...flow, pick_step: true, my_pick: "" }) === "pick", "pick");
check(mcFlowStep({ ...flow, pick_step: true, my_pick: "B" }) === "wait", "wait");
check(mcFlowStep({ ...flow, pick_step: false }) === "agree", "agree");
check(mcFlowStep({ ...flow, pick_step: false, submitted: true }) === "locked", "locked");
check(stillChoosingLine(["Ben", "Cy"]) === "Still choosing: Ben, Cy", "names");
check(stillChoosingLine(["Cy Twin", "Cy Other", "🦊 Fox"]) === "Still choosing: Cy Twin, Cy Other, 🦊 Fox", "server-shortened names show as sent");
check(stillChoosingLine(["Cy", "Cy"]) === "Still choosing: Cy", "exact repeats once");

const pick = mcPickStepHtml({ ...flow, pick_step: true }, { choices: ["2", "1"], itemId: 4 });
check(pick.includes("Send my pick") && pick.includes("data-group-pick-send disabled"), "pick button disabled until a choice");
check(!pick.includes("data-group-why"), "no why in step 1");
const wait = mcPickStepHtml(
  { ...flow, pick_step: true, my_pick: "2", waiting_count: 1, waiting_names: ["Cy"] },
  { choices: ["2", "1"], itemId: 4 }
);
check(wait.includes("Your pick: 2"), "my pick");
check(wait.includes("Waiting for 1 more teammate.") && wait.includes("Still choosing: Cy"), "wait lines");
check(wait.includes('aria-live="polite"'), "wait is live");

const picks = mcMemberPicksHtml([
  { name: "Ava", value: "2", mine: false },
  { name: "Cy", value: "1", mine: true },
]);
check(picks.includes("Ava") && picks.includes("You") && !picks.includes(">Cy<"), "mine reads You");
check(!picks.includes("✓") && !picks.includes("Correct"), "no marks for students");
const agree = mcAgreeStepHtml(
  { ...flow, pick_step: false, member_picks: [{ name: "Ava", value: "2" }] },
  { choices: ["2", "1"], choice: "2", why: "", itemId: 4 }
);
check(agree.includes('placeholder="Why this answer?"'), "why placeholder");
check(agree.includes("Send group answer") && agree.includes('data-live-submit="group" aria-describedby="group-send-reason-4" disabled'), "send disabled");
check(agree.includes("Add your why to send.") && !agree.includes("data-group-send-reason hidden"), "reason shown");
const ready = mcAgreeStepHtml({ ...flow }, { choices: ["2"], choice: "2", why: "because", itemId: 4 });
check(ready.includes("data-group-send-reason hidden"), "reason hidden when ready");
const locked = mcLockedHtml({ last_submitter: "Cy", submitted_choice: "2", submitted_why: "Ava's idea" });
check(locked.includes("Cy sent your group&#39;s answer.") && locked.includes("Your group&#39;s answer is locked."), "locked lines");
check(!locked.includes("<button"), "no change button");
const lockedSam2 = mcLockedHtml({ last_submitter: "Sam 2", submitted_choice: "2" });
check(lockedSam2.includes("Sam 2 sent your group&#39;s answer."), "sent by keeps the number (MCK-183)");

// Take turns.
const options = [
  { id: "o1", label: "Two points" },
  { id: "o2", label: "A graph" },
  { id: "o3", label: "An equation" },
];
const myTurn = rankTurnsHtml({ turns: { spots: [], total: 3, can_place: true } }, { options, itemId: 9 });
check(myTurn.includes("Your turn. Place one item."), "my turn cue");
check((myTurn.match(/data-rank-turn="/g) || []).length === 3 && !myTurn.includes("aria-disabled"), "3 enabled options");
check(myTurn.includes("rank-turn-spot is-next"), "next spot outlined");
const placed = {
  turns: {
    spots: [{ option_id: "o2", label: "A graph", by_name: "Ava", mine: true }],
    total: 3,
    can_place: false,
    can_undo: true,
    placed_spot: 1,
    waiting_names: ["Cy"],
  },
};
const placedHtml = rankTurnsHtml(placed, { options, itemId: 9 });
check(placedHtml.includes("Placed in spot 1.") && placedHtml.includes("data-rank-turn-undo"), "placed + undo");
check((placedHtml.match(/data-rank-turn="/g) || []).length === 2, "placed option leaves the list");
check(placedHtml.includes('aria-disabled="true"'), "options disabled after my pick");
check(placedHtml.includes("Spot 1, A graph, placed by You"), "spot a11y label");
check(
  rankTurnCue({ ...placed.turns, can_undo: false, placed_spot: 0, placed_last: true }) === "You've placed one. Waiting for Cy.",
  "blocked cue",
);
// Gate LOW-2: a skipped student who placed nothing just waits, and reads
// "Your turn was skipped." instead of their own name.
check(
  rankTurnCue({ can_place: false, waiting_names: ["Ava"], placed_last: false, skipped_me: true }) === "Waiting for Ava.",
  "skipped student never reads You've placed one",
);
const skippedMe = rankTurnsHtml(
  { turns: { spots: [], total: 3, can_place: false, waiting_names: ["Ava"], skipped_me: true, skipped_names: ["Gus"], placed_last: false } },
  { options, itemId: 9 },
);
check(skippedMe.includes("Your turn was skipped."), "skipped-you line");
check(skippedMe.includes("Gus&#39;s turn was skipped."), "teammate skipped line");
check(!skippedMe.includes("placed one"), "no placed-one cue for a skipped student");
check(rankTurnCue({ done: true }) === "Your group's order is in.", "done cue");
const skipped = rankTurnsHtml({ turns: { spots: [], total: 3, can_place: true, skipped_names: ["Cy"] } }, { options, itemId: 9 });
check(skipped.includes("Cy&#39;s turn was skipped."), "skipped line");
const done = rankTurnsHtml({ turns: { spots: [], total: 3, done: true } }, { options, itemId: 9 });
check(!done.includes("data-rank-turn="), "no options when done");
const note = rankTurnsHtml({ turns: { total: 3, can_place: true } }, { options, itemId: 9, note: "Ava just placed that one. Pick another." });
check(note.includes('role="status"') && note.includes("Ava just placed that one."), "conflict note");

// Teacher Group mode control (rank only, before Publish).
check(groupSubmitVariant({ type: "mc" }) === "mc", "mc variant");
check(groupSubmitVariant({ type: "rank" }, { item: { group_rank_mode: "turns" } }) === "rank_turns", "turns variant");
check(groupSubmitVariant({ type: "rank" }) === "rank_together", "together variant");
const seg = rankModeHtml("q:5", "turns");
check(seg.includes("Group mode") && seg.includes("Rank together") && seg.includes("Take turns"), "labels");
check(seg.includes('value="turns" data-group-rank-mode="q:5" checked'), "turns checked");
const base = { key: "q:5", style: "submit", status: "inactive", mode: "group", teamsReady: true };
const rankOpts = groupSetupOptionsHtml({ ...base, variant: "rank_turns" });
check(rankOpts.includes("Each person places one item, then passes."), "turns helper line");
check(rankOpts.includes("data-group-rank-mode"), "rank mode control");
check(!groupSetupOptionsHtml({ ...base, variant: "mc" }).includes("data-group-rank-mode"), "no control for MC");
check(groupSetupOptionsHtml({ ...base, variant: "mc" }).includes("Everyone picks, then each group agrees on one."), "mc style line");
check(groupSetupHtml({ ...base, status: "active", variant: "rank_turns" }).includes("● Group · take turns"), "turns chip");
check(groupSetupHtml({ ...base, status: "active", variant: "mc" }).includes("● Group · pick, then agree"), "mc chip");
check(groupSetupHtml({ ...base, status: "active" }).includes("● Group · one answer each"), "old chip unchanged");

// MCK-184: a slow POST reply (older rev) never repaints over a newer turn.
check(turnsCardIsStale({ turns: { rev: 3 } }, { turns: { rev: 2 } }), "older rev is stale");
check(!turnsCardIsStale({ turns: { rev: 3 } }, { turns: { rev: 3 } }), "same rev applies");
check(!turnsCardIsStale({ turns: { rev: 3 } }, { turns: { rev: 4 } }), "newer rev applies");
check(!turnsCardIsStale({}, { turns: { rev: 1 } }) && !turnsCardIsStale({ turns: { rev: 2 } }, { order: [] }), "non-turns cards apply");
// MCK-184: Undo only for the teammate whose pick is the last spot.
const undoOpts = { options: [{ id: "a", label: "A" }, { id: "b", label: "B" }, { id: "c", label: "C" }], itemId: 7 };
const lastMine = rankTurnsHtml({ turns: { mode: "turns", total: 3, rev: 2, can_undo: true, spots: [{ option_id: "a", label: "A", by_name: "Eva", mine: false }, { option_id: "b", label: "B", by_name: "Iggy", mine: true }] } }, undoOpts);
check(lastMine.includes("data-rank-turn-undo"), "last placer sees Undo");
const notMine = rankTurnsHtml({ turns: { mode: "turns", total: 3, rev: 2, can_undo: false, can_place: true, spots: [{ option_id: "a", label: "A", by_name: "Iggy", mine: false }, { option_id: "b", label: "B", by_name: "Eva", mine: true }].slice(0, 1) } }, undoOpts);
check(!notMine.includes("data-rank-turn-undo"), "teammate sees no Undo");
const staleUndo = rankTurnsHtml({ turns: { mode: "turns", total: 3, rev: 3, can_undo: true, spots: [{ option_id: "a", label: "A", by_name: "Iggy", mine: true }, { option_id: "b", label: "B", by_name: "Eva", mine: false }] } }, undoOpts);
check(!staleUndo.includes("data-rank-turn-undo"), "no Undo when the last spot is a teammate's (stale can_undo)");
const doneUndo = rankTurnsHtml({ turns: { mode: "turns", total: 1, rev: 1, done: true, can_undo: true, spots: [{ option_id: "a", label: "A", by_name: "Iggy", mine: true }] } }, undoOpts);
check(!doneUndo.includes("data-rank-turn-undo"), "no Undo once the order is in");

if (failures) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ok");

// MCK-171 Team challenge views (node harness, no DOM).
import {
  RACE_COPY,
  TEAM_SHAPES,
  fill,
  lanePips,
  laneStatus,
  raceLanesHtml,
  raceOptionLabels,
  raceStem,
  teamMark,
} from "./rank_challenge_view.js";

let failures = 0;
function check(cond, label) {
  if (!cond) {
    failures += 1;
    console.error(`FAIL ${label}`);
  }
}

// Wonder v3 strings (projector side).
check(RACE_COPY.sentCount === "{k} of {n} teams locked in", "race.sent_count");
check(RACE_COPY.agreeCount === "{k} of {m} agree", "race.agree.count");
check(RACE_COPY.thinking === "Thinking", "race.lane.thinking");
check(RACE_COPY.laneSent === "All agreed ✓", "race.lane.sent");
check(RACE_COPY.laneTurnsDone === "Locked in ✓", "race.lane.turns_done");
check(RACE_COPY.laneAbsent === "No one here yet", "race.lane.absent");
check(RACE_COPY.lockFor === "Lock in for {team}", "race.lock_for");
check(RACE_COPY.lockForConfirm === "Lock in {team}'s current order?", "race.lock_for.confirm");
check(RACE_COPY.skip === "Skip waiting turn", "race.skip");
check(RACE_COPY.skipConfirm === "Skip the waiting turn for {team}?", "race.skip.confirm");
check(RACE_COPY.exit === "Exit challenge view", "race.exit");
check(RACE_COPY.view === "Team challenge view", "race.view");
check(RACE_COPY.close === "Close & reveal", "race.close");
check(RACE_COPY.chipTogether === "● Group · rank together · challenge", "race.chip.together");
const everything = JSON.stringify(RACE_COPY);
check(!/race/i.test(everything), "no 'race' in copy");
check(!/\bsent\b/i.test(everything.replace("sent_count", "")), "no 'Sent'");
check(!/bonus|hardest|best move|first|fastest/i.test(everything), "no bonus / speed copy");

// Shapes + colours: fixed by slot, outline after six.
check(teamMark(0).shape === "●" && teamMark(5).shape === "⬟", "shapes");
check(teamMark(6).shape === "○" && teamMark(6).colour === teamMark(0).colour, "outline repeat");
check(TEAM_SHAPES.length === 6, "six shapes");
check(fill("{a}-{b}-{c}", { a: 1, b: "x" }) === "1-x-{c}", "fill");

// Lane status: Rank together counts from 0; Thinking is Take turns only.
check(laneStatus({ agree: 0, agree_of: 3 }, "together").text === "0 of 3 agree", "together 0 of 3");
check(laneStatus({ agree: 2, agree_of: 3 }, "together").text === "2 of 3 agree", "together 2 of 3");
check(laneStatus({ locked: true, locked_by: "team" }, "together").text === "All agreed ✓", "together locked");
check(laneStatus({ locked: true, locked_by: "teacher" }, "together").text === "Locked in ✓", "teacher locked");
check(laneStatus({ absent: true }, "together").text === "No one here yet", "absent");
check(laneStatus({ placed: 2, total: 4 }, "turns").text === "2 of 4 placed", "turns placed");
check(laneStatus({ placed: 0, total: 4 }, "turns").text === "Thinking", "turns thinking");
check(laneStatus({ locked: true, locked_by: "turns" }, "turns").text === "Locked in ✓", "turns locked");

// Pips: agreed / present / away (dashed, never counted).
const kinds = (pips) => pips.map((p) => p.kind).join(",");
check(kinds(lanePips({ agree: 1, agree_of: 2, members: 3 }, "together")) === "on,off,away", "pips with away");
check(kinds(lanePips({ agree: 0, agree_of: 3, members: 3 }, "together")) === "off,off,off", "0 agreed pips");
check(kinds(lanePips({ locked: true, locked_by: "team", agree_of: 3, members: 3 }, "together")) === "on,on,on", "all agreed pips");
check(kinds(lanePips({ locked: true, locked_by: "teacher", agree: 1, agree_of: 2, members: 3 }, "together")) === "on,off,away", "teacher lock keeps real agrees");
check(kinds(lanePips({ placed: 1, total: 3 }, "turns")) === "on,off,off", "turns pips");
check(raceStem({ item: { text: "Put these in order." } }) === "Put these in order.", "stem");
check(raceOptionLabels({ item: { rank_options: [{ id: "o1", label: "¼" }, { id: "o2", label: "0.3" }] } }).join("|") === "¼|0.3", "options");

// Lanes.
const race = {
  mode: "together",
  teams_locked: 1,
  teams_total: 3,
  teams: [
    { team_id: 11, team_name: "Tangents", slot: 0, locked: true, locked_by: "team", agree: 3, agree_of: 3 },
    { team_id: 12, team_name: "Cosines <b>", slot: 1, agree: 1, agree_of: 3, can_lock: true },
    { team_id: 13, team_name: "Radians", slot: 2, absent: true, agree: 0, agree_of: 0, members: 3 },
    { team_id: 14, team_name: "Sines", slot: 3, agree: 0, agree_of: 3, members: 3 },
  ],
};
const popped = new Set();
const html = raceLanesHtml(race, 7, { popped, stem: "Put these in order.", options: ["¼", "0.3"] });
// MCK-176 hook: race.options (server display order) wins over item order.
const shuffledHead = raceLanesHtml({ ...race, options: [{ id: "o2", label: "0.3" }, { id: "o1", label: "¼" }] }, 7, { stem: "Q", options: ["¼", "0.3"] });
check(shuffledHead.includes("0.3 · ¼ · "), "header follows race.options order");
check(html.includes("1 of 3 teams locked in"), "count line");
check(html.includes("Put these in order.") && html.includes("¼ · 0.3 · ● Group · rank together · challenge"), "header");
check(html.includes("0 of 3 agree"), "0 of 3 lane");
check(html.includes('data-close-live-item="7"') && html.includes("Close &amp; reveal"), "close & reveal");
check(html.indexOf("Tangents") < html.indexOf("Cosines") && html.indexOf("Cosines") < html.indexOf("Radians"), "fixed order");
check(html.includes("Cosines &lt;b&gt;") && !html.includes("<b>"), "escaped");
check(html.includes('data-race-lock="7" data-team-id="12"') && html.includes("Lock in for Cosines &lt;b&gt;"), "lock in for team");
check(!html.includes('data-team-id="11" data-team-name="Tangents">Lock'), "no lock on locked lane");
check(html.includes("is-popping") && popped.has(11), "pop once");
check(!raceLanesHtml(race, 7, { popped }).includes("is-popping"), "no second pop");
check(html.includes("No one here yet") && html.includes("is-absent"), "absent lane");
check(!/\b(1st|2nd|3rd|first)\b/i.test(html), "no place numbers");
check(html.includes('data-race-view-toggle="7"') && html.includes("Exit challenge view"), "exit control");
const turns = raceLanesHtml(
  { mode: "turns", teams_locked: 0, teams_total: 1, teams: [{ team_id: 5, team_name: "Vectors", placed: 1, total: 4, can_skip: true }] },
  9
);
check(turns.includes('data-race-skip="9"') && turns.includes("Skip waiting turn"), "turns skip");
check(!turns.includes("data-race-lock"), "no lock for turns");

if (failures) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ok");

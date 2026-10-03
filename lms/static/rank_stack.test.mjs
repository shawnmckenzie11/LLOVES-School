/**
 * MCK-154 S1 node checks for the one vertical rank results stack.
 */
import {
  RANK_STACK_COPY,
  rankStackColumns,
  rankStackHtml,
  rankStackStatus,
  shortTeamLabel,
} from "./rank_stack.js";

let failures = 0;
function check(cond, msg) {
  if (!cond) {
    failures += 1;
    console.error(`FAIL: ${msg}`);
  }
}
function count(haystack, needle) {
  return haystack.split(needle).length - 1;
}

const classOrder = [
  { option_id: "o2", label: "Slope", rank: 1, points: 7, first_picks: 2, bar_pct: 100 },
  { option_id: "o1", label: "Intercept", rank: 2, points: 4, first_picks: 0, bar_pct: 57 },
  { option_id: "o3", label: "Domain", rank: 3, points: 1, first_picks: 0, bar_pct: 14 },
];
const groupRank = {
  unit: "team",
  responded: 1,
  present: 2,
  class_order: classOrder,
  teams: [
    { team_id: 11, team_name: "Comets", order: ["o2", "o1", "o3"] },
    { team_id: 12, team_name: "Lighthouse Keepers", order: null },
  ],
};

// Status prints once, with Wonder v2 copy.
check(RANK_STACK_COPY.statusGroups === "{k} of {n} groups sent", "group status copy");
check(rankStackStatus(groupRank) === "1 of 2 groups sent", "group status text");
check(
  rankStackStatus({ unit: "student", responded: 18, present: 23 }) === "18 of 23 answered",
  "student status text"
);
const teacher = rankStackHtml(groupRank, { audience: "teacher" });
check(count(teacher, "1 of 2 groups sent") === 1, "teacher status once");
check(count(teacher, 'class="rank-stack-status"') === 1, "one status line");
check(count(teacher, "<table") === 1, "one table");

// Each option label prints once (as its row header).
for (const row of classOrder) {
  check(count(teacher, `<span>${row.label}</span>`) === 1, `label once: ${row.label}`);
}
check(count(teacher, 'scope="row"') === 3, "row headers per option");

// No star glyph; score is a bar + points with an aria label.
check(!teacher.includes("★"), "no star glyph");
check(teacher.includes('aria-label="7 points, 2 first picks"'), "score aria label");
check(teacher.includes("rank-stack-score"), "teacher has score column");

// A waiting group: every cell is "·" and the header says waiting.
const cols = rankStackColumns(groupRank.teams, { audience: "teacher" });
check(cols.length === 2, "two columns");
check(cols[1].waiting === true, "second group waiting");
check(cols[1].title === "Lighthouse Keepers · waiting", "waiting title");
check(cols[1].label === "Lighth", "long name shortened");
check(shortTeamLabel("Comets") === "Comets", "short name kept");
const waitingCells = count(teacher, '<td class="rank-stack-team is-waiting"><span aria-label="waiting">·</span></td>');
check(waitingCells === 3, `waiting group shows · in all rows (got ${waitingCells})`);

// Teacher column title carries "last: name" from the submitter log.
const withLast = rankStackHtml(groupRank, {
  audience: "teacher",
  lastByTeam: new Map([[11, "Ava"]]),
});
check(withLast.includes('title="Comets · last: Ava"'), "last submitter in title");

// Places land in the right rows: Comets put o2 first.
const slopeRow = teacher.split('data-rank-option="o2"')[1].split("</tr>")[0];
check(slopeRow.includes('<td class="rank-stack-team">1</td>'), "Comets place 1 on Slope");

// Student: own group first as "You", no score column, no last names.
const student = rankStackHtml(
  {
    ...groupRank,
    responded: 2,
    teams: [
      { team_id: 11, team_name: "Comets", order: ["o2", "o1", "o3"] },
      { team_id: 12, team_name: "Lighthouse Keepers", order: ["o1", "o2", "o3"] },
    ],
  },
  { audience: "student", ownTeamId: 12, lastByTeam: new Map([[11, "Ava"]]) }
);
check(!student.includes("rank-stack-score"), "student has no score");
check(!student.includes("points"), "student sees no points");
check(!student.includes("Ava"), "student sees no submitter names");
const studentHead = student.split("<thead>")[1].split("</thead>")[0];
check(
  studentHead.indexOf(">You<") > -1 && studentHead.indexOf(">You<") < studentHead.indexOf(">Comets<"),
  "own group first as You"
);

// Individual mode: no group columns; empty rows render nothing.
const individual = rankStackHtml(
  { unit: "student", responded: 3, present: 4, class_order: classOrder },
  { audience: "teacher" }
);
check(individual.includes('data-rank-cols="0"'), "individual has no group columns");
check(individual.includes("3 of 4 answered"), "individual status");
check(rankStackHtml({ unit: "team", class_order: [] }) === "", "empty -> empty string");
check(!rankStackHtml(groupRank, { showStatus: false }).includes("rank-stack-status"), "status can be hidden");

if (failures) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ok");

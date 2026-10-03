/**
 * MCK-171 (a) node checks: the Team challenge control in the Group line.
 */
import {
  GROUP_SETUP_COPY,
  groupSetupHtml,
  groupSetupOptionsHtml,
  rankRaceHtml,
  rankRaceSettings,
} from "./group_setup.js";

let failures = 0;
function check(cond, msg) {
  if (!cond) {
    failures += 1;
    console.error(`FAIL: ${msg}`);
  }
}

// Wonder v3 copy, exact.
check(GROUP_SETUP_COPY.raceToggle === "Team challenge", "race.toggle");
check(GROUP_SETUP_COPY.raceHelp === "Teams lock in an order together. Points for every right spot.", "race.help");
check(!("raceBonus" in GROUP_SETUP_COPY), "no bonus switch (removed 9:41 ET)");
check(GROUP_SETUP_COPY.liveChip.rank_turns_race === "● Group · take turns · challenge", "race.chip.turns");
check(GROUP_SETUP_COPY.liveChip.rank_together_race === "● Group · rank together · challenge", "race.chip.together");
for (const text of Object.values(GROUP_SETUP_COPY).flatMap((v) => (typeof v === "string" ? [v] : Object.values(v)))) {
  check(!/race/i.test(text), `no "race" in copy: ${text}`);
}

// Settings off the teacher payload.
check(!rankRaceSettings({ type: "rank" }).available, "no key, not available");
check(rankRaceSettings({ rank_key: ["o2", "o1", "o3"] }).available, "key, available");
check(!rankRaceSettings({ rank_key: ["o2", "o1", "o3"] }).on, "off by default");
check(!("bonus" in rankRaceSettings({ rank_key: ["a", "b", "c"], group_rank_race: true })), "no bonus setting");
check(!rankRaceSettings({ group_rank_race: true }).on, "no key means never on");

// Group line: hidden (never disabled) without a key.
const base = { key: "q:7", style: "submit", status: "inactive", mode: "group", teamsReady: true, variant: "rank_together" };
const noKey = groupSetupOptionsHtml({ ...base, race: { available: false, on: false } });
check(!noKey.includes("Team challenge"), "hidden without a key");
const off = groupSetupOptionsHtml({ ...base, race: { available: true, on: false } });
check(off.includes("Team challenge") && off.includes('data-group-rank-race="q:7"'), "toggle shows with a key");
check(!off.includes("Teams lock in"), "help only when on");
check(off.indexOf("Group mode") < off.indexOf("Team challenge"), "after Group mode");
const on = groupSetupOptionsHtml({ ...base, race: { available: true, on: true } });
check(on.includes("Teams lock in an order together.") && on.includes('data-group-rank-race="q:7" checked'), "on: checked + help");
check(!/bonus|hardest/i.test(on + rankRaceHtml("q:7", true)), "no bonus control");
check(!groupSetupOptionsHtml({ ...base, mode: "individual", race: { available: true, on: true } }), "only for Group");
check(!groupSetupOptionsHtml({ ...base, status: "active", race: { available: true, on: true } }).includes("Team challenge"), "before Publish only");
check(!groupSetupOptionsHtml({ ...base, variant: "mc", race: { available: true, on: true } }).includes("Team challenge"), "rank only");

// Live chips.
const chip = (variant, challenge) =>
  groupSetupHtml({ key: "q:7", style: "submit", status: "active", mode: "group", teamsReady: true, variant, challenge });
check(chip("rank_turns", true).includes("● Group · take turns · challenge"), "turns chip");
check(chip("rank_together", true).includes("● Group · rank together · challenge"), "together chip");
check(chip("rank_turns", false).includes("● Group · take turns<"), "plain turns chip unchanged");
check(chip("rank_together", false).includes("● Group · one answer each"), "plain together chip unchanged");

if (failures) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ok");

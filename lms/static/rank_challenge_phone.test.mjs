// MCK-171 Team challenge phone states (node harness, no DOM).
import {
  PHONE_COPY,
  agreeButtonHtml,
  agreeLineHtml,
  phoneCue,
  phoneState,
  phoneStripHtml,
  readOnlyOrderHtml,
} from "./rank_challenge_phone.js";

let failures = 0;
function check(cond, label) {
  if (!cond) {
    failures += 1;
    console.error(`FAIL ${label}`);
  }
}

// Wonder v3 (final) strings.
check(PHONE_COPY.cue === "Talk it through. Lock in when your whole team agrees.", "race.cue.together");
check(PHONE_COPY.agree === "I agree" && PHONE_COPY.undo === "Not yet", "agree / undo");
check(PHONE_COPY.waiting === "Waiting for {names} to agree.", "race.agree.waiting");
check(PHONE_COPY.reset === "The order changed. Agree again when you're ready.", "race.agree.reset");
check(PHONE_COPY.agreed === "Your whole team agreed.", "race.agreed");
check(PHONE_COPY.locked === "Your group's order is in.", "race.locked");
check(PHONE_COPY.timesup === "Time's up. We'll score what your group placed.", "race.timesup");
check(PHONE_COPY.closed === "Answers are closed. We'll score what your group placed.", "race.closed");
check(!/race|fastest|bonus|first|\bsent\b/i.test(Object.values(PHONE_COPY).join(" ")), "no race / speed / bonus words");

const base = (race, extra = {}) => ({ team_name: "Cosines", members: ["Gus", "Hana", "Ivy"], race: { mode: "together", slot: 2, teams_locked: 2, teams_total: 6, ...race }, ...extra });
const agree = (a) => ({ count: 0, of: 3, mine: false, reset: false, waiting_names: [], can_agree: true, ...a });

// Every state and its one cue line.
check(phoneState(base({ agree: agree({}) }), "active") === "ranking", "ranking");
check(phoneCue(base({ agree: agree({}) }), "active") === PHONE_COPY.cue, "ranking cue");
const waiting = base({ agree: agree({ count: 2, mine: true, waiting_names: ["Ivy"] }) });
check(phoneState(waiting, "active") === "waiting" && phoneCue(waiting, "active") === "Waiting for Ivy to agree.", "waiting");
const two = base({ agree: agree({ mine: true, waiting_names: ["Gus", "Ivy"] }) });
check(phoneCue(two, "active") === "Waiting for Gus and Ivy to agree.", "two names");
const reset = base({ agree: agree({ reset: true }) });
check(phoneState(reset, "active") === "reset" && phoneCue(reset, "active") === PHONE_COPY.reset, "reset");
const agreed = base({ locked: true, locked_by: "team", agree: agree({ count: 3 }) });
check(phoneCue(agreed, "active") === "Your whole team agreed.", "agreed");
const teacher = base({ locked: true, locked_by: "teacher", agree: agree({ count: 1 }) });
check(phoneCue(teacher, "active") === "Your group's order is in.", "teacher locked");
const turns = base({ mode: "turns", locked: true, locked_by: "turns" });
check(phoneCue(turns, "active") === "Your group's order is in.", "turns locked");
const timer = base({ agree: agree({}), closed_unlocked: "timer" });
check(phoneState(timer, "closed") === "timesup" && phoneCue(timer, "closed") === PHONE_COPY.timesup, "time's up at 0:00");
const closedT = base({ agree: agree({}), closed_unlocked: "teacher" });
check(phoneState(closedT, "closed") === "closed" && phoneCue(closedT, "closed") === PHONE_COPY.closed, "teacher close");
check(phoneCue(agreed, "closed") === "Your whole team agreed.", "locked stays locked after close");

// Strip, pips, buttons.
const strip = phoneStripHtml(base({}));
check(strip.includes("Cosines") && strip.includes("Gus, Hana, Ivy") && strip.includes("2 of 6 teams locked in"), "strip");
check(strip.includes("■") && strip.includes("#6d28d9"), "slot 2 shape + colour match the projector");
check(agreeLineHtml(base({ agree: agree({ count: 0 }) })).includes("0 of 3 agree"), "0 of 3");
const line = agreeLineHtml(waiting);
check(line.includes("2 of 3 agree") && (line.match(/is-on/g) || []).length === 2, "2 pips on");
check(agreeLineHtml(agreed).includes("3 of 3 agree"), "all agreed pips");
check(agreeButtonHtml(base({ agree: agree({}) }), false).includes("disabled"), "I agree waits for a full order");
check(!/\sdisabled[\s>]/.test(agreeButtonHtml(base({ agree: agree({}) }), true)), "I agree ready");
check(agreeButtonHtml(waiting, true).includes('data-race-agree="0"') && agreeButtonHtml(waiting, true).includes("Not yet"), "Not yet");
const ro = readOnlyOrderHtml([{ id: "o1", label: "¼" }, { id: "o2", label: "<b>" }], ["o2", "o1"]);
check(ro.indexOf("&lt;b&gt;") < ro.indexOf("¼") && !ro.includes("<b>"), "read-only order, escaped");
check(!/\d+\s*pts|points|place/i.test(strip + line + ro), "no score or place before the reveal");

if (failures) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ok");

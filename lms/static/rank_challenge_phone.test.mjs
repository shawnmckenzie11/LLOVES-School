// MCK-171 / MCK-216 Team challenge phone states (node harness, no DOM).
import {
  raceDisplayOptions,
  PHONE_COPY,
  lockButtonHtml,
  undoButtonHtml,
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

check(PHONE_COPY.cue === "Talk it through. Lock in when your whole team agrees.", "race.cue.together");
check(PHONE_COPY.lockIn === "Lock in our order", "lock in label");
check(PHONE_COPY.undo === "Undo", "undo label");
check(PHONE_COPY.locked === "Locked in. Waiting for the reveal.", "locked copy");
check(PHONE_COPY.timesup === "Time's up. We'll score what your group placed.", "race.timesup");
check(PHONE_COPY.closed === "Answers are closed. We'll score what your group placed.", "race.closed");
check(!/race|fastest|bonus|first|\bsent\b/i.test(Object.values(PHONE_COPY).join(" ")), "no race / speed / bonus words");

const base = (race, extra = {}) => ({
  team_name: "Cosines",
  members: ["Gus", "Hana", "Ivy"],
  race: { mode: "together", slot: 2, teams_locked: 2, teams_total: 6, ...race },
  ...extra,
});

check(phoneState(base({}), "active") === "ranking", "ranking");
check(phoneCue(base({}), "active") === PHONE_COPY.cue, "ranking cue");
const ready = base({ can_lock: true });
check(phoneState(ready, "active") === "ready" && phoneCue(ready, "active") === PHONE_COPY.cue, "ready cue");
const locked = base({ locked: true, locked_by: "team" });
check(phoneCue(locked, "active") === PHONE_COPY.locked, "locked waiting");
const teacher = base({ locked: true, locked_by: "teacher" });
check(phoneCue(teacher, "active") === PHONE_COPY.locked, "teacher locked copy");
const timer = base({ closed_unlocked: "timer" });
check(phoneState(timer, "closed") === "timesup" && phoneCue(timer, "closed") === PHONE_COPY.timesup, "time's up at 0:00");
const closedT = base({ closed_unlocked: "teacher" });
check(phoneState(closedT, "closed") === "closed" && phoneCue(closedT, "closed") === PHONE_COPY.closed, "teacher close");

check(phoneStripHtml(base({})) === "", "no team strip on student phones");
check(lockButtonHtml(base({ can_lock: true })).includes("Lock in our order"), "lock button");
check(lockButtonHtml(base({})).includes("disabled"), "lock waits for can_lock");
check(undoButtonHtml(base({ can_undo: true })).includes("Undo"), "undo when allowed");
check(!undoButtonHtml(base({})).includes("Undo"), "no undo without flag");

const ro = readOnlyOrderHtml([{ id: "o1", label: "¼" }, { id: "o2", label: "<b>" }], ["o2", "o1"]);
check(ro.indexOf("&lt;b&gt;") < ro.indexOf("¼") && !ro.includes("<b>"), "read-only order, escaped");

{
  const opts = [{ id: "o1", label: "¼" }, { id: "o2", label: "0.3" }, { id: "o3", label: "2/7" }];
  const shown = raceDisplayOptions(opts, { options: [{ id: "o3", label: "2/7" }, { id: "o1", label: "¼" }] });
  check(shown.map((o) => o.id).join() === "o3,o1,o2", "display order, unlisted ids last");
  check(shown[0] === opts[2], "same option objects");
  check(raceDisplayOptions(opts, {}) === opts, "no race.options keeps the card order");
}
if (failures) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}

console.log("ok");

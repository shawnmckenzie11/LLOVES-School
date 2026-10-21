// MCK-171 / MCK-216 Team challenge phone states (node harness, no DOM).
import { GROUP_FLOW_COPY } from "./group_flows.js";
import {
  raceDisplayOptions,
  PHONE_COPY,
  lockButtonHtml,
  showLockButton,
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

check(PHONE_COPY.cue === undefined, "old ranking cue is gone");
check(PHONE_COPY.lockIn === "Lock in our order", "lock in label");
check(PHONE_COPY.undo === "Undo", "undo label");
check(PHONE_COPY.locked === "Waiting for the reveal.", "locked copy");
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
check(phoneCue(base({}), "active") === GROUP_FLOW_COPY.rankInstruction, "ranking cue");
const ready = base({ can_lock: true });
check(phoneState(ready, "active") === "ready" && phoneCue(ready, "active") === GROUP_FLOW_COPY.rankInstruction, "ready cue");
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
check(lockButtonHtml(base({ locked: true, can_lock: true })) === "", "locked hides the lock button");
check(!showLockButton(base({ locked: true, can_lock: true })), "locked is not clickable");
check(!showLockButton({ ...base({ can_lock: true }), submitted: true }), "sent hides the lock button");
const turnsOpen = { race: { mode: "turns", locked: false }, turns: { done: false }, submitted: false };
const turnsReady = { race: { mode: "turns", locked: false, can_lock: true }, turns: { done: true }, submitted: false };
const turnsLocked = { race: { mode: "turns", locked: true, can_lock: true }, turns: { done: true }, submitted: false };
check(!showLockButton(turnsOpen), "take turns hides lock until the order is in");
check(showLockButton(turnsReady) && lockButtonHtml(turnsReady).includes("Lock in our order"), "take turns shows lock when ready");
check(!showLockButton(turnsLocked) && lockButtonHtml(turnsLocked) === "", "take turns hides lock after lock-in");
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

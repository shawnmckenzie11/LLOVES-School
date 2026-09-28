/**
 * Node checks for live-poll soft chrome. Busy JSON must not imply a reload.
 */
import {
  POLL_BACKOFF_BASE_MS,
  POLL_BACKOFF_MAX_MS,
  isLiveStateBusy,
  jitterPollDelay,
  nextPollBackoffMs,
  reconnectCopy,
  reconnectMode,
  shouldApplyLiveSnapshot,
} from "./live_poll_feel.js";

const shed = {
  ok: true,
  error: "state unavailable",
  status: "waiting",
  retry: true,
  fault: "Live class is busy. Retry.",
};

if (!isLiveStateBusy(shed)) {
  console.error("boot shed JSON must be busy, not a navigation");
  process.exit(1);
}
if (shouldApplyLiveSnapshot(shed, 4)) {
  console.error("shed JSON must not replace the last frame");
  process.exit(1);
}
if (isLiveStateBusy({ phase: "ended", fault: "Live class state is gone." })) {
  console.error("ended Meet is not a busy retry");
  process.exit(1);
}

let delay = 0;
const seen = [];
for (let i = 0; i < 6; i += 1) {
  delay = nextPollBackoffMs(delay);
  seen.push(delay);
  if (delay <= 0) {
    console.error("backoff returned 0");
    process.exit(1);
  }
}
if (seen[0] !== POLL_BACKOFF_BASE_MS || seen[seen.length - 1] !== POLL_BACKOFF_MAX_MS) {
  console.error("backoff did not grow to the cap", seen);
  process.exit(1);
}
const student = nextPollBackoffMs(0, 4000);
if (student < 4000) {
  console.error("student backoff must not be shorter than the healthy poll");
  process.exit(1);
}
if (jitterPollDelay(4000, 0) < 250 || jitterPollDelay(4000, 1) > 4800) {
  console.error("jitter left the ±20% band");
  process.exit(1);
}

if (reconnectMode(1_000, 1_100, "inflight") !== "ok") {
  console.error("pending chrome must wait ~800ms");
  process.exit(1);
}
if (reconnectMode(1_000, 1_900, "inflight") !== "pending") {
  console.error("slow poll should say Reconnecting");
  process.exit(1);
}
if (reconnectCopy(reconnectMode(1_000, 1_900, "inflight")) !== "Reconnecting…") {
  console.error("pending copy");
  process.exit(1);
}
if (reconnectCopy(reconnectMode(1_000, 1_050, "busy")) !== "Live class is busy") {
  console.error("busy copy");
  process.exit(1);
}
if (reconnectCopy(reconnectMode(1_000, 5_200, "busy")) !== "Still reconnecting — Retry") {
  console.error("stuck copy");
  process.exit(1);
}
if (shouldApplyLiveSnapshot({ state_seq: 3, teacher_state: { state_seq: 3 } }, 5)) {
  console.error("older state_seq must keep the last frame");
  process.exit(1);
}
if (!shouldApplyLiveSnapshot({ state_seq: 5, ok: true }, 5)) {
  console.error("same state_seq may refresh light fields");
  process.exit(1);
}

console.log("ok");

/**
 * A burst of board kicks is one read, and that read replaces the next poll slot.
 */
import {
  BOARD_DELTA_POLL_MS,
  BOARD_INK_DEBOUNCE_MS,
  BOARD_INK_JITTER_MS,
  createBoardDeltaPoll,
} from "./live_whiteboard.js";

globalThis.window = globalThis;
window.setTimeout = setTimeout;
window.clearTimeout = clearTimeout;

function fail(message) {
  console.error(message);
  process.exit(1);
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

let reads = 0;
const poll = createBoardDeltaPoll({
  isShown: () => true,
  poll: async () => {
    reads += 1;
    return { ok: true };
  },
});

await delay(40);
if (reads !== 1) fail(`open poll read ${reads} times`);
poll.kick();
poll.kick();
await delay(BOARD_INK_DEBOUNCE_MS + BOARD_INK_JITTER_MS + 80);
if (reads !== 2) fail(`burst fetched ${reads} times`);
await delay(BOARD_DELTA_POLL_MS);
if (reads !== 2) fail(`pulled slot was fetched again, reads=${reads}`);
poll.stop();
console.log("ok");

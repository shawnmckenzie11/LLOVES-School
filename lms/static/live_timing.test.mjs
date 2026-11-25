/**
 * MCK-522 live_timing.js rules, with a fake clock, DOM, and Sentry.
 * Run by test_live_timing_mck522.py. Exits non-zero on the first failure.
 */
import { createLiveTiming, READY_TIMEOUT_MS } from "./live_timing.js";

function fail(message) {
  console.error(message);
  process.exit(1);
}

function assert(cond, message) {
  if (!cond) fail(message);
}

/** Build a recorder with a manual clock and frame/timer queues. */
function rig({ skewMs = 0, images = {} } = {}) {
  const state = { t: 1_000_000, frames: [], timers: [], spans: [], hidden: false };
  const fakeSentry = {
    startInactiveSpan(options) {
      const span = { options, endedAt: null, end(at) { span.endedAt = at; } };
      state.spans.push(span);
      return span;
    },
  };
  const timing = createLiveTiming({
    now: () => state.t,
    sentry: () => fakeSentry,
    findImages: (id) => images[id] || [],
    nextFrame: (fn) => state.frames.push(fn),
    setTimer: (fn, ms) => {
      const timer = { fn, ms, live: true };
      state.timers.push(timer);
      return timer;
    },
    clearTimer: (timer) => {
      timer.live = false;
    },
    isHidden: () => state.hidden,
  });
  const runFrames = () => {
    const batch = state.frames.splice(0);
    for (const fn of batch) fn();
  };
  /** One clock sample: the server reads `skewMs` ahead of this phone. */
  const sampleClock = (rtt = 40) => {
    const sentAt = state.t;
    const receivedAt = sentAt + rtt;
    timing.noteClock(sentAt, receivedAt, sentAt + rtt / 2 + skewMs);
    state.t = receivedAt;
  };
  return { state, timing, runFrames, sampleClock };
}

// 1. Clock offset: the smallest round trip wins, and a publish stamp maps
// onto the phone's clock.
{
  const { state, timing, sampleClock } = rig({ skewMs: 5000 });
  sampleClock(400);
  sampleClock(40);
  sampleClock(900);
  const server = state.t + 5000;
  assert(Math.round(timing.toLocal(server) - state.t) === 0, "offset must come from the 40 ms sample");
  const bad = createLiveTiming({ now: () => 1 });
  bad.noteClock(0, 99999, 5);
  assert(bad.toLocal(777) === 777, "a huge round trip must not set an offset");
}

// 2. Push card, then snapshot: one span from the publish stamp, text before ready.
{
  const { state, timing, runFrames, sampleClock } = rig({ skewMs: 5000 });
  sampleClock();
  timing.noteSnapshot({ active_questions: [] });
  const publishedServer = state.t + 5000 - 600;
  timing.notePush({ item_id: 9, published_ms: publishedServer, kind: "mc" });
  state.t += 30;
  runFrames();
  assert(state.spans.length === 0, "no span until the snapshot paints");
  state.t += 500;
  timing.noteSnapshot({ active_questions: [{ id: 9, status: "active", published_ms: publishedServer }] });
  assert(state.spans.length === 1, "snapshot must finish a text-only card");
  const span = state.spans[0];
  const attrs = span.options.attributes;
  assert(span.options.name === "live.client.publish_to_paint", "span name");
  assert(span.options.forceTransaction === true && span.options.parentSpan === null, "root span");
  assert(attrs["live.via"] === "push", "via push");
  assert(attrs["live.text_ms"] === 630, `text_ms ${attrs["live.text_ms"]}`);
  assert(attrs["live.ready_ms"] === 1130, `ready_ms ${attrs["live.ready_ms"]}`);
  assert(attrs["live.skew_ms"] === 5000 && attrs["live.skew_known"] === true, "skew recorded");
  assert(attrs["live.item_id"] === 9 && attrs["live.images"] === 0, "item and image count");
  assert(Math.round((span.endedAt - span.options.startTime) * 1000) === 1130, "span duration is publish to ready");
  assert(timing.pendingCount() === 0, "nothing pending");
  // A repeat snapshot or card for the same item does not measure again.
  timing.notePush({ item_id: 9, published_ms: publishedServer, kind: "mc" });
  timing.noteSnapshot({ active_questions: [{ id: 9, status: "active", published_ms: publishedServer }] });
  assert(state.spans.length === 1, "one span per item");
}

// 3. Images: ready waits for every image to load, and a failed image counts as settled.
{
  const pic = { complete: false, listeners: {}, addEventListener(type, fn) { this.listeners[type] = fn; } };
  const pic2 = { complete: true, addEventListener() {} };
  const { state, timing, runFrames, sampleClock } = rig({ images: { 4: [pic, pic2] } });
  sampleClock();
  timing.noteSnapshot({ active_questions: [] });
  timing.notePush({ item_id: 4, published_ms: state.t - 200, kind: "mc" });
  runFrames();
  timing.noteSnapshot({ active_questions: [{ id: 4, status: "active" }] });
  assert(state.spans.length === 0, "an unloaded image holds the span");
  state.t += 700;
  pic.complete = true;
  pic.listeners.load();
  assert(state.spans.length === 1, "image load finishes the span");
  const attrs = state.spans[0].options.attributes;
  assert(attrs["live.images"] === 2 && attrs["live.images_loaded"] === 2, "image counts");
  assert(attrs["live.ready_ms"] > attrs["live.text_ms"], "ready after text");
}

// 4. Timeout: report text time only, flagged, with no ready_ms.
{
  const pic = { complete: false, addEventListener() {} };
  const { state, timing, runFrames, sampleClock } = rig({ images: { 5: [pic] } });
  sampleClock();
  timing.noteSnapshot({ active_questions: [] });
  timing.notePush({ item_id: 5, published_ms: state.t - 100, kind: "mc" });
  runFrames();
  const timer = state.timers.find((t) => t.live && t.ms === READY_TIMEOUT_MS);
  assert(timer, "a timeout timer is set");
  state.t += READY_TIMEOUT_MS;
  timer.fn();
  assert(state.spans.length === 1, "timeout reports");
  const attrs = state.spans[0].options.attributes;
  assert(attrs["live.timed_out"] === true && !("live.ready_ms" in attrs), "timeout flags and drops ready_ms");
}

// 5. Not live arrivals: the first snapshot seeds, a stale stamp and a hidden tab skip.
{
  const { state, timing, runFrames, sampleClock } = rig();
  sampleClock();
  timing.noteSnapshot({ active_questions: [{ id: 1, status: "active", published_ms: state.t - 1000 }] });
  timing.noteSnapshot({ active_questions: [{ id: 1, status: "active", published_ms: state.t - 1000 }] });
  runFrames();
  assert(state.spans.length === 0, "first snapshot items are not measured");
  timing.noteSnapshot({ active_questions: [{ id: 2, status: "active", published_ms: state.t - 10 * 60 * 1000 }] });
  runFrames();
  assert(state.spans.length === 0 && timing.pendingCount() === 0, "old publish skipped");
  state.hidden = true;
  timing.noteSnapshot({ active_questions: [{ id: 3, status: "active", published_ms: state.t - 500 }] });
  runFrames();
  assert(state.spans.length === 0 && timing.pendingCount() === 0, "hidden tab skipped");
  state.hidden = false;
  timing.noteSnapshot({ active_questions: [{ id: 6, status: "active", published_ms: state.t - 500, content: { type: "mc" } }] });
  runFrames();
  assert(state.spans.length === 1 && state.spans[0].options.attributes["live.via"] === "state", "a state-only arrival measures via state");
}

// 6. Tally update: a classmate's landing is measured, our own submit is not, repeats are not.
{
  const { state, timing, runFrames, sampleClock } = rig({ skewMs: 2000 });
  sampleClock();
  timing.noteSubmit({ startedAt: state.t - 300, ackedAt: state.t - 100, paintedAt: state.t - 90, status: 200, action: "individual" });
  const own = state.spans.pop();
  assert(own.options.name === "live.client.submit_to_paint", "submit span name");
  assert(own.options.attributes["live.rtt_ms"] === 200 && own.options.attributes["live.paint_ms"] === 10, "submit parts");
  timing.noteTallyPatch({ tally_at_ms: state.t - 100 + 2000 });
  runFrames();
  assert(state.spans.length === 0, "our own landing is skipped");
  state.t += 5000;
  const landed = state.t + 2000 - 3000;
  timing.noteTallyPatch({ tally_at_ms: landed });
  state.t += 20;
  runFrames();
  assert(state.spans.length === 1, "a classmate landing is measured");
  assert(state.spans[0].options.name === "live.client.tally_update", "tally span name");
  assert(state.spans[0].options.attributes["live.update_ms"] === 3020, `update_ms ${state.spans[0].options.attributes["live.update_ms"]}`);
  timing.noteTallyPatch({ tally_at_ms: landed });
  runFrames();
  assert(state.spans.length === 1, "the same landing is not measured twice");
}

// 7. Safety: no Sentry, a throwing Sentry, and junk input never throw.
{
  const quiet = createLiveTiming({ now: () => 5, sentry: () => undefined, isHidden: () => false, nextFrame: (fn) => fn(), findImages: () => [] });
  quiet.noteSnapshot({ active_questions: [] });
  quiet.notePush({ item_id: 1, published_ms: 4, kind: "mc" });
  quiet.noteSnapshot({ active_questions: [{ id: 1, status: "active" }] });
  quiet.noteTallyPatch({});
  quiet.noteTallyPatch(null);
  quiet.notePush(null);
  quiet.noteSnapshot(null);
  const boom = createLiveTiming({
    now: () => 5,
    sentry: () => ({ startInactiveSpan() { throw new Error("sdk down"); } }),
    isHidden: () => false,
    nextFrame: (fn) => fn(),
    findImages: () => [],
  });
  boom.noteSnapshot({ active_questions: [] });
  boom.notePush({ item_id: 2, published_ms: 4, kind: "mc" });
  boom.noteSnapshot({ active_questions: [{ id: 2, status: "active" }] });
}

// 8. Privacy: spans carry only numbers, booleans, and fixed labels.
{
  const { state, timing, runFrames, sampleClock } = rig();
  sampleClock();
  timing.noteSnapshot({ active_questions: [] });
  timing.notePush({
    item_id: 8,
    published_ms: state.t - 100,
    kind: "mc",
    text: "What is Zorblax's PIN 9876?",
    options: ["Q7Z9K"],
  });
  runFrames();
  timing.noteSnapshot({ active_questions: [{ id: 8, status: "active", content: { text: "Zorblax" } }] });
  const blob = JSON.stringify(state.spans.map((span) => span.options));
  for (const secret of ["Zorblax", "9876", "Q7Z9K", "PIN"]) {
    assert(!blob.includes(secret), `span leaked ${secret}`);
  }
  for (const [key, value] of Object.entries(state.spans[0].options.attributes)) {
    assert(key.startsWith("live."), `unexpected attribute ${key}`);
    assert(["number", "boolean", "string"].includes(typeof value), `bad value type for ${key}`);
  }
}

console.log("ok");

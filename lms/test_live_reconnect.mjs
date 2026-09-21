/**
 * Two-stage reconnect copy: brief Reconnecting… then sticky
 * Still reconnecting — Retry without restarting on repeated show().
 */
import assert from "node:assert/strict";
import {
  bindReconnectBanner,
  RECONNECT_BRIEF_MS,
  RECONNECT_COPY_BRIEF,
  RECONNECT_COPY_STICKY,
} from "./static/live_reconnect.js";

function fakeStrip() {
  const copy = { textContent: "" };
  const retry = { hidden: false };
  const attrs = {};
  const root = {
    hidden: true,
    querySelector(sel) {
      if (sel === ".live-reconnect-copy") return copy;
      if (sel === ".live-reconnect-retry") return retry;
      return null;
    },
    setAttribute(name, value) {
      attrs[name] = String(value);
    },
    removeAttribute(name) {
      delete attrs[name];
    },
  };
  return { root, copy, retry, attrs };
}

const pending = [];
const setTimer = (fn, ms) => {
  const id = pending.length + 1;
  pending.push({ id, fn, ms });
  return id;
};
const clearTimer = (id) => {
  const idx = pending.findIndex((row) => row.id === id);
  if (idx >= 0) pending.splice(idx, 1);
};

const { root, copy, retry, attrs } = fakeStrip();
const banner = bindReconnectBanner({ root, retry, setTimer, clearTimer });

assert.equal(copy.textContent, RECONNECT_COPY_BRIEF);
assert.equal(retry.hidden, true);
assert.equal(attrs["data-reconnect-stage"], "brief");
assert.equal(RECONNECT_BRIEF_MS, 2500);
assert.equal(RECONNECT_COPY_STICKY, "Still reconnecting — Retry");

banner.show();
assert.equal(root.hidden, false);
assert.equal(copy.textContent, RECONNECT_COPY_BRIEF);
assert.equal(retry.hidden, true);
assert.equal(pending.length, 1);
assert.equal(pending[0].ms, RECONNECT_BRIEF_MS);

banner.show();
assert.equal(pending.length, 1, "repeated show() must not restart the brief timer");
assert.equal(copy.textContent, RECONNECT_COPY_BRIEF);

const upgrade = pending[0];
pending.splice(0, 1);
upgrade.fn();
assert.equal(copy.textContent, RECONNECT_COPY_STICKY);
assert.equal(retry.hidden, false);
assert.equal(attrs["data-reconnect-stage"], "sticky");

banner.show();
assert.equal(copy.textContent, RECONNECT_COPY_STICKY);
assert.equal(pending.length, 0, "sticky stage must not schedule another brief timer");

banner.hide();
assert.equal(root.hidden, true);
assert.equal(copy.textContent, RECONNECT_COPY_BRIEF);
assert.equal(retry.hidden, true);
assert.equal(attrs["data-reconnect-stage"], "brief");

banner.show();
assert.equal(copy.textContent, RECONNECT_COPY_BRIEF);
assert.equal(pending.length, 1);
pending[0].fn();
assert.equal(copy.textContent, RECONNECT_COPY_STICKY);

console.log("ok two-stage reconnect");

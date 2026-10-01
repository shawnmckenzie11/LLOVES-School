/**
 * LiveNewsWire client. One EventSource per tab.
 *
 * The server taps stage, media version, and answer-landed. This module
 * does not reload, does not remount an Artifact, and does not invent a
 * second poll. Callers light-fetch the slice they already paint.
 */

import { artifactMountAction, jitterPollDelay, mediaMountKey } from "./live_poll_feel.js";

/** Slow /state safety net when the wire is quiet or down. */
export const FALLBACK_POLL_MS = 20000;
/**
 * /state pace for a tab with no open stream: shed at the per-worker
 * stream cap (4 per gunicorn worker, 16 per machine), still connecting,
 * or reconnecting. That tab hears no postcards, so the slow 20s net would
 * leave a published question or a new joiner up to 20s late. This is the
 * pre-wire student pace (#166 moved every tab to 20s).
 */
export const NO_STREAM_POLL_MS = 4000;

/**
 * Fallback /state delay for one tab.
 * A tab holding an open stream is told about every change, so it polls
 * on the slow safety net. Any other tab polls at ``NO_STREAM_POLL_MS``.
 * @param {{hasStream?: () => boolean}|null|undefined} wire
 * @returns {number}
 */
export function fallbackPollMs(wire) {
  const streaming =
    Boolean(wire) && typeof wire.hasStream === "function" && wire.hasStream();
  return streaming ? FALLBACK_POLL_MS : NO_STREAM_POLL_MS;
}

/**
 * Student /state pace once End Live Class has run: the session is ended,
 * or the tab is on the celebrate screen (200 ``{celebrate: true, status:
 * "waiting"}``). No question can publish after End, so the tab only needs
 * a slow check for Quit / the next class instead of 4s forever (MCK-88 M1).
 */
export const ENDED_POLL_MS = 30000;

/**
 * True when a full student /state body says the class is over.
 * The tiny ``unchanged`` body carries neither field; callers keep the last value.
 * @param {any} payload
 * @returns {boolean}
 */
export function studentSessionOver(payload) {
  if (!payload || typeof payload !== "object") return false;
  return Boolean(payload.celebrate) || String(payload.status || "") === "ended";
}

/**
 * Healthy student fallback delay, jittered ±20% so shed tabs that stalled
 * together do not re-align into bursts (MCK-88 L1).
 * Over: ``ENDED_POLL_MS``. Otherwise the stream-aware ``fallbackPollMs``.
 * @param {{hasStream?: () => boolean}|null|undefined} wire
 * @param {boolean} over ``studentSessionOver`` of the last full body.
 * @param {number} [rand] ``Math.random()`` stand-in for tests.
 * @returns {number}
 */
export function studentFallbackPollMs(wire, over, rand = Math.random()) {
  const base = over ? ENDED_POLL_MS : fallbackPollMs(wire);
  return jitterPollDelay(base, rand);
}

/** Soft line when a news-driven fetch is still out after ~800ms. */
export const CATCHING_UP_COPY = "Catching up…";

const LIGHT_TYPES = new Set([
  "state_seq",
  "stage",
  "slide",
  "active_media",
  "flags",
  "prompt",
  "response_landed",
  "flag_work",
]);

/**
 * Integer ``state_seq`` on a postcard, or null when the event has none.
 * @param {any} event
 * @returns {number|null}
 */
export function newsStateSeq(event) {
  if (!event || event.state_seq == null || event.state_seq === "") return null;
  const seq = Number(event.state_seq);
  return Number.isFinite(seq) ? seq : null;
}

/**
 * True when the postcard is older than the frame already painted.
 * Equal seq still applies: a response can land without bumping seq.
 * @param {any} event
 * @param {number} lastSeq
 * @returns {boolean}
 */
export function isStaleNews(event, lastSeq) {
  const seq = newsStateSeq(event);
  if (seq == null) return false;
  return seq < (Number(lastSeq) || 0);
}

/**
 * Busy / shed is a soft strip. Callers must not navigate or remount.
 * @param {any} event
 * @returns {boolean}
 */
export function newsSkipsReload(event) {
  if (!event || typeof event !== "object") return false;
  return event.type === "busy" || event.retry === true;
}

/**
 * True when the tab should light-fetch. Ping, hello, and busy do not.
 * @param {any} event
 * @returns {boolean}
 */
export function newsWantsLightFetch(event) {
  if (!event || newsSkipsReload(event)) return false;
  return LIGHT_TYPES.has(String(event.type || ""));
}

/**
 * Teacher board taps paint SubmitterLog / status chips in place.
 * @param {any} event
 * @returns {boolean}
 */
export function newsPaintsBoardInPlace(event) {
  const type = String(event?.type || "");
  return type === "response_landed" || type === "flag_work";
}

/**
 * True when an ``active_media`` postcard names the Artifact already mounted.
 * A different ``media_version`` or ``artifact_id`` is the only remount.
 * @param {any} event
 * @param {string|number|null} mountedVersion
 * @param {string|null} mountedArtifactId
 * @returns {boolean}
 */
export function newsKeepsArtifact(event, mountedVersion, mountedArtifactId) {
  if (!event || event.type !== "active_media") return true;
  const version = String(event.media_version ?? "");
  const artifact = String(event.artifact_id ?? "");
  return (
    version === String(mountedVersion ?? "") &&
    artifact === String(mountedArtifactId ?? "")
  );
}

/**
 * Compose the flicker lock with a news postcard.
 * Same version keeps the iframe. A version bump is a mount. Busy is keep.
 * @param {string} mountedKey
 * @param {any} event
 * @param {string} [role]
 * @returns {"keep"|"mount"|"clear"}
 */
export function newsArtifactAction(mountedKey, event, role = "") {
  if (!event || event.type === "busy" || event.type === "ping") return "keep";
  if (event.type !== "active_media") return "keep";
  const next = mediaMountKey(
    {
      media_version: event.media_version,
      artifact_id: event.artifact_id,
      ref: "",
    },
    role
  );
  if (!next) return "keep";
  return artifactMountAction(mountedKey, next, false);
}

/**
 * Open one SSE stream. Reconnect is the browser's, paced by the server
 * ``retry:`` field. ``stop`` closes it so a quit cannot leave the socket up.
 *
 * @param {number} sessionId
 * @param {{onNews?: (event: any) => void, onBusy?: (event: any) => void, onHello?: (event: any) => void}} handlers
 * @returns {{stop: () => void, noteSeq: (seq: number) => void, sessionId: number}}
 */
export function connectLiveNewsWire(sessionId, handlers) {
  const sid = Number(sessionId) || 0;
  let source = null;
  let stopped = false;
  let lastSeq = 0;
  let debounce = 0;
  /** @type {any} */
  let pending = null;

  /**
   * Parse one SSE message. Stale and busy never become a fetch.
   * @param {MessageEvent} message
   */
  function onFrame(message) {
    if (stopped) return;
    let event = null;
    try {
      event = JSON.parse(String(message.data || ""));
    } catch (_err) {
      return;
    }
    if (!event || typeof event !== "object") return;
    if (newsSkipsReload(event)) {
      pending = null;
      if (debounce) window.clearTimeout(debounce);
      debounce = 0;
      handlers.onBusy?.(event);
      return;
    }
    if (isStaleNews(event, lastSeq)) return;
    const seq = newsStateSeq(event);
    if (seq != null && seq > lastSeq) lastSeq = seq;
    if (event.type === "hello") {
      handlers.onHello?.(event);
      return;
    }
    if (!newsWantsLightFetch(event)) return;
    pending = event;
    if (debounce) return;
    debounce = window.setTimeout(() => {
      debounce = 0;
      const next = pending;
      pending = null;
      if (next) handlers.onNews?.(next);
    }, 40);
  }

  /**
   * Attach listeners for every catalogue name the server emits.
   * @param {EventSource} stream
   */
  function listen(stream) {
    const names = [
      "hello",
      "state_seq",
      "stage",
      "slide",
      "active_media",
      "flags",
      "prompt",
      "response_landed",
      "flag_work",
      "busy",
      "ping",
    ];
    names.forEach((name) => stream.addEventListener(name, onFrame));
    stream.onerror = () => {
      // The browser reconnects with Last-Event-ID. Do not reload.
    };
  }

  if (sid && typeof EventSource !== "undefined") {
    source = new EventSource(`/api/live/session/${sid}/events`, {
      withCredentials: true,
    });
    listen(source);
  }

  return {
    sessionId: sid,
    /**
     * Remember a seq applied from /state so an older tap is ignored.
     * @param {number} seq
     */
    noteSeq(seq) {
      const next = Number(seq) || 0;
      if (next > lastSeq) lastSeq = next;
    },
    /**
     * True while the EventSource is open.
     *
     * A tab still connecting, or one the server did not give a slot,
     * does not have a stream. Those tabs poll board ops instead.
     * @returns {boolean}
     */
    hasStream() {
      return Boolean(source && source.readyState === 1);
    },
    /** Close the stream. A late frame must not fetch. */
    stop() {
      stopped = true;
      pending = null;
      if (debounce) window.clearTimeout(debounce);
      debounce = 0;
      if (source) {
        source.close();
        source = null;
      }
    },
  };
}

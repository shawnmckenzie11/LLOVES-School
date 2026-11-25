/**
 * MCK-522: measure how long live-class questions and results take to reach a
 * student's screen. Measurement only. Nothing here changes what is fetched,
 * cached, or painted.
 *
 * Spans go through the existing browser Sentry (``sentry-live.js``), named
 * ``live.client.*`` so its sampler applies ``SENTRY_LIVE_TRACES_SAMPLE_RATE``.
 * Attributes are whole milliseconds, counts, and fixed labels. They never
 * hold a name, join code, PIN, catch-up code, answer, or question text. The
 * only id is the lifecycle item id, to group one publish across phones.
 *
 * Clock skew: ``/api/student/state`` replies carry ``server_ms``. Each poll
 * gives one offset sample, ``server_ms - midpoint(send, receive)``, and the
 * sample with the smallest round trip wins. A publish stamp from the server
 * is mapped onto this phone's clock with it.
 *
 * Spans:
 * - ``live.client.publish_to_paint``: publish stamp to the card painted with
 *   its images loaded. ``live.text_ms`` is when the stem showed.
 * - ``live.client.tally_update``: a classmate's answer landing to this
 *   phone's results table repainting (one-second server resolution).
 * - ``live.client.submit_to_paint``: submit tap to the answer card repainted.
 */

/** Longest offset sample we keep trusting, in ms. */
export const CLOCK_SAMPLE_MAX_AGE_MS = 10 * 60 * 1000;
/** Round trips slower than this give a useless offset, in ms. */
export const CLOCK_RTT_MAX_MS = 5000;
/** A card first seen later than this after its publish is not a live arrival. */
export const MEASURE_MAX_AGE_MS = 120 * 1000;
/** Give up waiting for the snapshot and images after this long, in ms. */
export const READY_TIMEOUT_MS = 20 * 1000;
/** Same-answer window: a tally change this soon after our own submit is ours. */
export const OWN_SUBMIT_WINDOW_MS = 2000;
/** Most publishes tracked at once. */
export const PENDING_MAX = 40;
/** A measured span longer than this is a clock fault, not a delay. */
export const SPAN_MAX_MS = 10 * 60 * 1000;

/**
 * Find the ``<img>`` elements inside one lifecycle card.
 * @param {number} itemId
 * @returns {Array<{complete?: boolean, addEventListener?: Function}>}
 */
function defaultFindImages(itemId) {
  if (typeof document === "undefined") return [];
  return Array.from(
    document.querySelectorAll(`[data-live-card-id="${Number(itemId)}"] img`)
  );
}

/**
 * Run ``fn`` after the next animation frame, or soon if frames are unavailable.
 * @param {() => void} fn
 */
function defaultNextFrame(fn) {
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(() => fn());
    return;
  }
  setTimeout(fn, 0);
}

/**
 * Build one timing recorder. Tests inject the clock, Sentry, and DOM.
 * @param {{
 *   now?: () => number,
 *   sentry?: () => any,
 *   findImages?: (itemId: number) => any[],
 *   nextFrame?: (fn: () => void) => void,
 *   setTimer?: (fn: () => void, ms: number) => any,
 *   clearTimer?: (id: any) => void,
 *   isHidden?: () => boolean,
 * }} [deps]
 */
export function createLiveTiming(deps = {}) {
  const now = deps.now || (() => Date.now());
  const sentry = deps.sentry || (() => globalThis.Sentry);
  const findImages = deps.findImages || defaultFindImages;
  const nextFrame = deps.nextFrame || defaultNextFrame;
  const setTimer = deps.setTimer || ((fn, ms) => setTimeout(fn, ms));
  const clearTimer = deps.clearTimer || ((id) => clearTimeout(id));
  const isHidden =
    deps.isHidden ||
    (() => typeof document !== "undefined" && document.visibilityState === "hidden");

  /** @type {{offsetMs: number, rttMs: number, at: number}|null} */
  let clock = null;
  /** @type {Map<number, any>} */
  const pending = new Map();
  /** @type {Set<number>} */
  const done = new Set();
  let snapshotSeen = false;
  let ownSubmitServerMs = 0;
  let lastTallyLandedMs = 0;
  /** @type {Array<{name: string, start: number, end: number, attrs: any}>} */
  const sent = [];

  /**
   * Record one clock-offset sample from a ``/state`` reply.
   * @param {number} sentAt Local ms just before the request.
   * @param {number} receivedAt Local ms when the reply arrived.
   * @param {number} serverMs ``server_ms`` from the reply body.
   */
  function noteClock(sentAt, receivedAt, serverMs) {
    try {
      const server = Number(serverMs);
      const rtt = Number(receivedAt) - Number(sentAt);
      if (!Number.isFinite(server) || server <= 0) return;
      if (!Number.isFinite(rtt) || rtt < 0 || rtt > CLOCK_RTT_MAX_MS) return;
      const offset = server - (Number(sentAt) + Number(receivedAt)) / 2;
      const stale = clock && Number(receivedAt) - clock.at > CLOCK_SAMPLE_MAX_AGE_MS;
      if (!clock || stale || rtt <= clock.rttMs) {
        clock = { offsetMs: offset, rttMs: rtt, at: Number(receivedAt) };
      }
    } catch (_err) {
      /* telemetry never breaks the page */
    }
  }

  /**
   * Map a server epoch ms onto this phone's clock.
   * @param {number} serverMs
   * @returns {number}
   */
  function toLocal(serverMs) {
    return clock ? serverMs - clock.offsetMs : serverMs;
  }

  /**
   * Send one span through Sentry when the SDK is present.
   * @param {string} name
   * @param {number} startMs Local epoch ms.
   * @param {number} endMs Local epoch ms.
   * @param {Record<string, number|string|boolean>} attrs
   * @returns {boolean}
   */
  function emit(name, startMs, endMs, attrs) {
    try {
      let end = Number(endMs);
      const start = Number(startMs);
      if (!Number.isFinite(start) || !Number.isFinite(end)) return false;
      const attributes = { ...attrs };
      if (end < start) {
        attributes["live.clamped"] = true;
        end = start;
      }
      if (end - start > SPAN_MAX_MS) return false;
      attributes["live.skew_known"] = Boolean(clock);
      if (clock) {
        attributes["live.skew_ms"] = Math.round(clock.offsetMs);
        attributes["live.skew_rtt_ms"] = Math.round(clock.rttMs);
      }
      sent.push({ name, start, end, attrs: attributes });
      if (sent.length > 200) sent.shift();
      const client = sentry();
      if (!client || typeof client.startInactiveSpan !== "function") return false;
      const span = client.startInactiveSpan({
        name,
        op: "live.client",
        startTime: start / 1000,
        forceTransaction: true,
        parentSpan: null,
        attributes,
      });
      if (!span || typeof span.end !== "function") return false;
      span.end(end / 1000);
      return true;
    } catch (_err) {
      return false;
    }
  }

  /**
   * Start tracking one published item.
   * @param {number} itemId
   * @param {number} publishedServerMs
   * @param {"push"|"state"} via
   * @param {string} kind
   */
  function register(itemId, publishedServerMs, via, kind) {
    if (!itemId || done.has(itemId) || pending.has(itemId)) return;
    const age = now() - toLocal(publishedServerMs);
    if (!(publishedServerMs > 0) || age > MEASURE_MAX_AGE_MS) {
      done.add(itemId);
      return;
    }
    if (isHidden()) {
      done.add(itemId);
      return;
    }
    if (pending.size >= PENDING_MAX) {
      const oldest = pending.keys().next().value;
      finishItem(oldest, true);
    }
    const entry = {
      itemId,
      publishedServerMs,
      via,
      kind: String(kind || ""),
      paintedAt: now(),
      frameAt: 0,
      sawSnapshot: via === "state",
      timedOut: false,
      timer: null,
      listening: new WeakSet(),
    };
    entry.timer = setTimer(() => {
      entry.timedOut = true;
      checkItem(itemId);
    }, READY_TIMEOUT_MS);
    pending.set(itemId, entry);
    nextFrame(() => {
      entry.frameAt = now();
      checkItem(itemId);
    });
  }

  /**
   * Finish one item when its snapshot and images are in, or on timeout.
   * @param {number} itemId
   */
  function checkItem(itemId) {
    try {
      const entry = pending.get(itemId);
      if (!entry || !entry.frameAt) return;
      if (!entry.sawSnapshot && !entry.timedOut) return;
      const images = findImages(itemId) || [];
      const unsettled = images.filter((img) => !img.complete);
      if (unsettled.length && !entry.timedOut) {
        for (const img of unsettled) {
          if (entry.listening.has(img) || typeof img.addEventListener !== "function") continue;
          entry.listening.add(img);
          img.addEventListener("load", () => checkItem(itemId), { once: true });
          img.addEventListener("error", () => checkItem(itemId), { once: true });
        }
        return;
      }
      finishItem(itemId, false);
    } catch (_err) {
      pending.delete(itemId);
    }
  }

  /**
   * Emit the publish-to-paint span for one item and stop tracking it.
   * @param {number} itemId
   * @param {boolean} dropped True when evicted unfinished.
   */
  function finishItem(itemId, dropped) {
    const entry = pending.get(itemId);
    if (!entry) return;
    pending.delete(itemId);
    done.add(itemId);
    if (entry.timer) clearTimer(entry.timer);
    if (dropped) return;
    const images = findImages(itemId) || [];
    const loaded = images.filter((img) => img.complete).length;
    const start = toLocal(entry.publishedServerMs);
    const textAt = entry.frameAt || entry.paintedAt;
    const readyAt = now();
    const attrs = {
      "live.item_id": itemId,
      "live.via": entry.via,
      "live.kind": entry.kind,
      "live.text_ms": Math.round(textAt - start),
      "live.images": images.length,
      "live.images_loaded": loaded,
      "live.timed_out": entry.timedOut,
    };
    if (!entry.timedOut) attrs["live.ready_ms"] = Math.round(readyAt - start);
    emit("live.client.publish_to_paint", start, entry.timedOut ? textAt : readyAt, attrs);
  }

  /**
   * A pushed question card was painted from the news postcard.
   * @param {{item_id?: number, published_ms?: number, kind?: string}} card
   */
  function notePush(card) {
    try {
      const itemId = Number(card && card.item_id) || 0;
      const published = Number(card && card.published_ms) || 0;
      if (!itemId || !published) return;
      register(itemId, published, "push", card.kind);
    } catch (_err) {
      /* ignore */
    }
  }

  /**
   * A full ``/state`` snapshot was painted.
   * The first snapshot of a page only seeds the known set: a reload or a
   * late join is not a publish arriving.
   * @param {any} payload
   */
  function noteSnapshot(payload) {
    try {
      const items = Array.isArray(payload && payload.active_questions)
        ? payload.active_questions
        : [];
      if (!snapshotSeen) {
        snapshotSeen = true;
        for (const item of items) {
          const id = Number(item && item.id) || 0;
          if (id) done.add(id);
        }
        return;
      }
      for (const item of items) {
        const id = Number(item && item.id) || 0;
        if (!id || String(item.status || "active") !== "active") continue;
        if (pending.has(id)) {
          pending.get(id).sawSnapshot = true;
          checkItem(id);
          continue;
        }
        const kind = item.content && item.content.type ? item.content.type : "";
        register(id, Number(item.published_ms) || 0, "state", kind);
      }
    } catch (_err) {
      /* ignore */
    }
  }

  /**
   * An unchanged poll brought a thin results patch that was just painted.
   * @param {{tally_at_ms?: number}} body
   */
  function noteTallyPatch(body) {
    try {
      const landed = Number(body && body.tally_at_ms) || 0;
      if (!landed || landed === lastTallyLandedMs) return;
      lastTallyLandedMs = landed;
      if (ownSubmitServerMs && landed <= ownSubmitServerMs + OWN_SUBMIT_WINDOW_MS) return;
      nextFrame(() => {
        const start = toLocal(landed);
        const end = now();
        emit("live.client.tally_update", start, end, {
          "live.update_ms": Math.round(end - start),
          "live.resolution_s": 1,
        });
      });
    } catch (_err) {
      /* ignore */
    }
  }

  /**
   * This student's own submit was acknowledged.
   * @param {{startedAt: number, ackedAt: number, paintedAt: number, status: number, action: string}} info
   */
  function noteSubmit(info) {
    try {
      ownSubmitServerMs = clock ? info.ackedAt + clock.offsetMs : info.ackedAt;
      emit("live.client.submit_to_paint", info.startedAt, info.paintedAt, {
        "live.action": String(info.action || "individual"),
        "live.status": Number(info.status) || 0,
        "live.rtt_ms": Math.round(info.ackedAt - info.startedAt),
        "live.paint_ms": Math.round(info.paintedAt - info.ackedAt),
      });
    } catch (_err) {
      /* ignore */
    }
  }

  /**
   * Drop all state. Tests and a new live session use this.
   */
  function reset() {
    for (const entry of pending.values()) {
      if (entry.timer) clearTimer(entry.timer);
    }
    pending.clear();
    done.clear();
    clock = null;
    snapshotSeen = false;
    ownSubmitServerMs = 0;
    lastTallyLandedMs = 0;
    sent.length = 0;
  }

  return {
    noteClock,
    notePush,
    noteSnapshot,
    noteTallyPatch,
    noteSubmit,
    reset,
    toLocal,
    /** @returns {Array<any>} Spans handed to Sentry, newest last. */
    emitted: () => sent.slice(),
    /** @returns {number} Items still waiting on a snapshot or images. */
    pendingCount: () => pending.size,
  };
}

/** The recorder the student portal uses. */
export const liveTiming = createLiveTiming();

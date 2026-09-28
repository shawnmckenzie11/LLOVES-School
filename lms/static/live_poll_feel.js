/**
 * Soft chrome for a live /state poll that is slow, shed, or locked.
 *
 * Busy JSON is not a navigation event. Callers keep the last calm frame
 * and must not reload, remount, or reset the poll delay to 0.
 */

/** Show "Reconnecting…" only after the poll has already waited this long. */
export const RECONNECT_PENDING_MS = 800;
/** User-tap Retry appears only after the outage has lasted this long. */
export const RECONNECT_STUCK_MS = 4000;
/** First automatic delay after a busy/503 response. Never zero. */
export const POLL_BACKOFF_BASE_MS = 2000;
/** Cap so a long lock does not wait forever between calm retries. */
export const POLL_BACKOFF_MAX_MS = 16000;

/**
 * True when a /state body is a shed, lock, or 503-class retry.
 *
 * An ended Meet is not busy. The caller stops polling on ``phase`` /
 * ``status`` ended and may show that fault as a single calm line.
 *
 * @param {any} payload
 * @returns {boolean}
 */
export function isLiveStateBusy(payload) {
  if (!payload || typeof payload !== "object") return false;
  const phase = String(payload.phase || "");
  const status = String(payload.status || payload.session?.status || "");
  if (phase === "ended" || status === "ended") return false;
  if (payload.retry === true) return true;
  if (payload.error === "state unavailable") return true;
  return /busy/i.test(String(payload.fault || ""));
}

/**
 * Next automatic poll delay after busy/503. The result is never 0.
 *
 * @param {number} current Current backoff, or 0 when the poll was healthy.
 * @param {number} [floor] Minimum delay. Student polls pass their base interval.
 * @returns {number}
 */
export function nextPollBackoffMs(current, floor = POLL_BACKOFF_BASE_MS) {
  const min = Math.max(POLL_BACKOFF_BASE_MS, Number(floor) || 0);
  const prev = Number(current) || 0;
  if (prev <= 0) return min;
  return Math.min(POLL_BACKOFF_MAX_MS, Math.max(min, prev * 2));
}

/**
 * Spread a backoff delay by ±20% so clients do not retry in lockstep.
 *
 * @param {number} ms
 * @param {number} [rand] Unit interval. Defaults to ``Math.random``.
 * @returns {number}
 */
export function jitterPollDelay(ms, rand = Math.random()) {
  const base = Math.max(0, Number(ms) || 0);
  const spread = Math.round(base * 0.2);
  const unit = Math.min(1, Math.max(0, Number(rand)));
  const delta = Math.round((unit * 2 - 1) * spread);
  return Math.max(250, base + delta);
}

/**
 * One calm strip line. Retry is named only on the stuck line.
 *
 * @param {"ok"|"pending"|"busy"|"stuck"} mode
 * @returns {string}
 */
export function reconnectCopy(mode) {
  if (mode === "pending") return "Reconnecting…";
  if (mode === "busy") return "Live class is busy";
  if (mode === "stuck") return "Still reconnecting — Retry";
  return "";
}

/**
 * Which soft strip to paint for an outage that began at ``startedAt``.
 *
 * ``inflight`` stays blank until 800ms, then "Reconnecting…".
 * ``busy`` shows the busy line immediately, then Retry after 4s.
 *
 * @param {number} startedAt Epoch ms, or 0 when there is no outage.
 * @param {number} now
 * @param {"ok"|"inflight"|"busy"} kind
 * @returns {"ok"|"pending"|"busy"|"stuck"}
 */
export function reconnectMode(startedAt, now, kind) {
  if (kind === "ok" || !startedAt) return "ok";
  const elapsed = Math.max(0, Number(now) - Number(startedAt));
  if (elapsed >= RECONNECT_STUCK_MS) return "stuck";
  if (kind === "busy") return "busy";
  if (elapsed >= RECONNECT_PENDING_MS) return "pending";
  return "ok";
}

/**
 * True when a snapshot may replace the last calm frame.
 *
 * Busy bodies never apply. An older ``state_seq`` is ignored so a lagged
 * worker cannot wipe a newer question stack.
 *
 * @param {any} payload
 * @param {number} lastGoodSeq Last applied sequence. ``0`` accepts the first frame.
 * @returns {boolean}
 */
export function shouldApplyLiveSnapshot(payload, lastGoodSeq) {
  if (isLiveStateBusy(payload)) return false;
  const raw = payload?.state_seq ?? payload?.teacher_state?.state_seq;
  if (raw == null || raw === "") return true;
  const seq = Number(raw);
  if (!Number.isFinite(seq)) return true;
  const last = Number(lastGoodSeq) || 0;
  return seq >= last;
}

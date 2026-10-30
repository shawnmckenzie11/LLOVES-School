/**
 * One Sentry payload for a teacher live-class reconnect strip episode.
 *
 * The yellow strip is painted by ``staff_ap.js``. This module decides when
 * that episode is reported and builds the only object that may be sent:
 * cause, duration, timestamp, and class id. It never accepts a URL, a
 * header, a body, a name, an email, a PIN, or a join code.
 */

/** Log a strip that is still up once it has lasted this long. */
export const STAFF_RECONNECT_LOG_AFTER_MS = 16000;

/** The only keys a logged episode may contain, in payload order. */
export const STAFF_RECONNECT_KEYS = Object.freeze([
  "cause",
  "duration_ms",
  "timestamp",
  "class_id",
]);

/** One allowlist. sentry-live.js reads this string from the page global. */
export const STAFF_RECONNECT_CAUSE_SOURCE =
  "^(?:fetch_timeout|network_error|stream_closed|visibility_resume|busy|slow_poll|unknown|http_[1-5]\\d\\d)$";

const CAUSE_PATTERN = new RegExp(STAFF_RECONNECT_CAUSE_SOURCE);

if (typeof globalThis !== "undefined") {
  globalThis.LLOVES_STAFF_RECONNECT_CAUSE_SOURCE = STAFF_RECONNECT_CAUSE_SOURCE;
}

/**
 * True when ``cause`` is one of the allowlisted episode reasons.
 * @param {unknown} cause
 * @returns {boolean}
 */
function isAllowedCause(cause) {
  return CAUSE_PATTERN.test(String(cause || ""));
}

/**
 * Read an HTTP status only from an ``HTTP 503`` style message.
 * A URL, a query string, or a JSON body is not a status.
 * @param {unknown} err
 * @returns {string} Three-digit status, or ``""``.
 */
function httpStatusFromError(err) {
  if (!err || typeof err !== "object") return "";
  const match = String(err.message || "").match(/\bHTTP\s+([1-5]\d\d)\b/);
  return match ? match[1] : "";
}

/**
 * True when the fetch was aborted (timeout or the teacher's Retry).
 * @param {unknown} err
 * @returns {boolean}
 */
function isAbortError(err) {
  if (!err || typeof err !== "object") return false;
  if (String(err.name || "") === "AbortError") return true;
  return /abort/i.test(String(err.message || ""));
}

/**
 * True for a failed fetch that never received a status.
 * @param {unknown} err
 * @returns {boolean}
 */
function isNetworkError(err) {
  if (!err || typeof err !== "object") return false;
  if (String(err.name || "") === "AbortError") return false;
  return /failed to fetch|networkerror|network request failed|load failed/i.test(
    String(err.message || "")
  );
}

/**
 * True when the server answered with a shed / lock / busy body.
 * The message itself is not returned.
 * @param {unknown} err
 * @returns {boolean}
 */
function isBusyMessage(err) {
  if (!err || typeof err !== "object") return false;
  return /state unavailable|server overloaded|database is locked|\bbusy\b/i.test(
    String(err.message || "")
  );
}

/**
 * How specific a cause is. A later hint may replace only a weaker one.
 * @param {string} cause
 * @returns {number}
 */
function causeRank(cause) {
  if (cause === "slow_poll") return 1;
  if (cause && cause !== "unknown") return 2;
  return 0;
}

/**
 * Name the reason the teacher strip appeared.
 *
 * The return value is an allowlisted token. Error text, URLs, and status
 * bodies are not copied. A wake from sleep wins over a timeout or a dropped
 * stream, because that is why the request died. An HTTP status wins over a
 * dropped stream, because the server did answer. A dropped stream wins over
 * a bare timeout or a slow poll.
 *
 * @param {unknown} err Thrown fetch error, or null when the strip is only slow.
 * @param {{timeout?: boolean, busy?: boolean, slowPoll?: boolean, streamClosed?: boolean, visibilityResume?: boolean}} [hints]
 * @returns {string}
 */
export function classifyStaffReconnectCause(err, hints) {
  const hint = hints && typeof hints === "object" ? hints : {};
  const timeout = hint.timeout === true;
  const aborted = isAbortError(err);
  const network = isNetworkError(err);
  const status = httpStatusFromError(err);
  const woke = hint.visibilityResume === true;
  const stream = hint.streamClosed === true;
  const slow = hint.slowPoll === true;
  const busy = hint.busy === true || isBusyMessage(err);

  if (woke && (timeout || aborted || network || slow)) return "visibility_resume";
  if (status) return `http_${status}`;
  if (stream && (timeout || network || aborted || slow)) return "stream_closed";
  if (timeout) return "fetch_timeout";
  if (network) return "network_error";
  if (busy) return "busy";
  if (slow) return "slow_poll";
  if (stream) return "stream_closed";
  if (woke) return "visibility_resume";
  return "unknown";
}

/**
 * Build the only object sent for one teacher reconnect episode.
 *
 * Extra fields on ``input`` are ignored. A cause that is not allowlisted,
 * a non-numeric time, or a class id below 1 returns null so nothing is sent.
 *
 * @param {object} input
 * @param {unknown} [input.cause]
 * @param {unknown} [input.durationMs]
 * @param {unknown} [input.duration_ms]
 * @param {unknown} [input.timestamp]
 * @param {unknown} [input.classId]
 * @param {unknown} [input.class_id]
 * @returns {{cause: string, duration_ms: number, timestamp: number, class_id: number}|null}
 */
export function staffReconnectPayload(input) {
  const src = input && typeof input === "object" ? input : null;
  if (!src) return null;
  const cause = isAllowedCause(src.cause) ? String(src.cause) : "";
  const duration = Number(
    Object.prototype.hasOwnProperty.call(src, "durationMs") ? src.durationMs : src.duration_ms
  );
  const timestamp = Number(src.timestamp);
  const classRaw = Object.prototype.hasOwnProperty.call(src, "classId")
    ? src.classId
    : src.class_id;
  const classId = Number(classRaw);
  if (!cause || !Number.isFinite(duration) || !Number.isFinite(timestamp) || !Number.isFinite(classId)) {
    return null;
  }
  const class_id = Math.round(classId);
  const ts = Math.round(timestamp);
  if (class_id < 1 || ts < 1) return null;
  return {
    cause,
    duration_ms: Math.max(0, Math.round(duration)),
    timestamp: ts,
    class_id,
  };
}

/**
 * Track one yellow-strip episode and log it once.
 *
 * ``shown`` opens the episode. A stronger cause may replace a weaker one
 * until the log fires. ``hidden`` logs immediately when the strip clears.
 * If the strip is still up after ``STAFF_RECONNECT_LOG_AFTER_MS``, the
 * timer logs that same episode and ``hidden`` does not log again.
 *
 * @param {{log: (payload: object) => void, after: (ms: number, fn: () => void) => unknown, now?: () => number, cancel?: (id: unknown) => void}} deps
 * @returns {{shown: (cause: string, classId: number) => void, hidden: () => void}}
 */
export function createStaffReconnectLog(deps) {
  const log = deps && deps.log;
  const after = deps && deps.after;
  if (typeof log !== "function" || typeof after !== "function") {
    return {
      shown() {},
      hidden() {},
    };
  }
  const now = typeof deps.now === "function" ? deps.now : () => Date.now();
  const cancel = typeof deps.cancel === "function" ? deps.cancel : () => {};
  /** @type {{cause: string, classId: number, startedAt: number, logged: boolean, timer: unknown}|null} */
  let episode = null;

  /**
   * Send the open episode once. Later calls are ignored.
   */
  function send() {
    if (!episode || episode.logged) return;
    episode.logged = true;
    if (episode.timer) {
      cancel(episode.timer);
      episode.timer = 0;
    }
    const payload = staffReconnectPayload({
      cause: episode.cause,
      durationMs: Math.max(0, now() - episode.startedAt),
      timestamp: now(),
      classId: episode.classId,
    });
    if (payload) log(payload);
  }

  return {
    /**
     * The yellow strip became visible, or was painted again while up.
     * @param {string} cause Allowlisted token, or empty to keep the current cause.
     * @param {number} classId Staff class id. Not a session id.
     */
    shown(cause, classId) {
      const next = isAllowedCause(cause) ? String(cause) : "";
      if (episode) {
        if (next && !episode.logged && causeRank(next) > causeRank(episode.cause)) {
          episode.cause = next;
        }
        return;
      }
      episode = {
        cause: next || "unknown",
        classId,
        startedAt: now(),
        logged: false,
        timer: 0,
      };
      episode.timer = after(STAFF_RECONNECT_LOG_AFTER_MS, () => {
        if (!episode || episode.logged) return;
        episode.timer = 0;
        send();
      });
    },
    /**
     * The yellow strip hid. Log the open episode if the timer has not.
     */
    hidden() {
      if (!episode) return;
      const current = episode;
      send();
      if (current.timer) cancel(current.timer);
      episode = null;
    },
  };
}

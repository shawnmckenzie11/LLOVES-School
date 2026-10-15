/**
 * One unsent live-class answer per student and question.
 *
 * A failed submit used to show an error and drop the answer. This module
 * keeps that answer in localStorage and decides whether a later reconnect
 * may send it. A closed question is never resent.
 */

/** Calm line shown while an answer is waiting for the network. */
export const UNSENT_ANSWER_COPY = "Saved, will send when you're back online";

/** localStorage key for the outbox map. */
export const UNSENT_ANSWER_STORE_KEY = "lloves-unsent-answers";

/** Unsent answers older than this are dropped (shared classroom devices). */
export const UNSENT_ANSWER_TTL_MS = 2 * 60 * 60 * 1000;

/** Same-tab fallback when localStorage refuses the write. */
const memoryFallback = new Map();

/**
 * Stable person key. Roster id wins; otherwise the visit token.
 * @param {{studentId?: unknown, visitToken?: unknown}} identity
 * @returns {string} Empty when this browser cannot tell students apart.
 */
export function unsentStudentKey(identity = {}) {
  const studentId = Number(identity.studentId);
  if (Number.isFinite(studentId) && studentId > 0) return `s:${studentId}`;
  const visitKey = unsentVisitKey(identity.visitToken);
  if (visitKey) return `v:${visitKey}`;
  return "";
}

/**
 * One-way tag for a visit token so the raw token is never written to storage.
 * FNV-1a 64-bit (two 32-bit lanes); enough to tell visits apart on one device.
 * @param {unknown} visitToken
 * @returns {string} Empty when there is no token.
 */
export function unsentVisitKey(visitToken) {
  const text = String(visitToken || "").trim();
  if (!text) return "";
  let a = 0x811c9dc5;
  let b = 0x01000193 ^ text.length;
  for (let i = 0; i < text.length; i += 1) {
    const c = text.charCodeAt(i);
    a = Math.imul(a ^ c, 0x01000193) >>> 0;
    b = Math.imul(b ^ c ^ (a >>> 7), 0x5bd1e995) >>> 0;
  }
  return `h${a.toString(16).padStart(8, "0")}${b.toString(16).padStart(8, "0")}`;
}

/**
 * Storage key for one student and one prompt.
 * @param {string} studentKey
 * @param {unknown} promptId
 * @returns {string}
 */
export function unsentRecordKey(studentKey, promptId) {
  return `${String(studentKey || "")}:p:${Number(promptId) || 0}`;
}

/**
 * Read the outbox object. Bad JSON becomes an empty map.
 * @param {Storage|null|undefined} store
 * @returns {Record<string, any>}
 */
export function readUnsentStore(store) {
  const merged = {};
  memoryFallback.forEach((row, key) => {
    merged[key] = row;
  });
  if (!store || typeof store.getItem !== "function") return merged;
  try {
    const raw = store.getItem(UNSENT_ANSWER_STORE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return merged;
    return { ...merged, ...parsed };
  } catch (_err) {
    return merged;
  }
}

/**
 * Write the outbox. Returns false when the store rejects it.
 * @param {Storage|null|undefined} store
 * @param {Record<string, any>} data
 * @returns {boolean}
 */
export function writeUnsentStore(store, data) {
  if (!store || typeof store.setItem !== "function") return false;
  try {
    store.setItem(UNSENT_ANSWER_STORE_KEY, JSON.stringify(data || {}));
    return true;
  } catch (_err) {
    return false;
  }
}

/**
 * True when two outbox rows are the same person.
 * @param {any} left
 * @param {any} right
 * @returns {boolean}
 */
function sameUnsentStudent(left, right) {
  const leftId = Number(left?.studentId) || 0;
  const rightId = Number(right?.studentId) || 0;
  if (leftId && rightId && leftId === rightId) return true;
  const leftVisit = String(left?.visitKey || "") || unsentVisitKey(left?.visitToken);
  const rightVisit = String(right?.visitKey || "") || unsentVisitKey(right?.visitToken);
  if (leftVisit && rightVisit && leftVisit === rightVisit) return true;
  const leftKey = String(left?.studentKey || "");
  const rightKey = String(right?.studentKey || "");
  return Boolean(leftKey) && leftKey === rightKey;
}

/**
 * Keep one answer for this student and question, replacing an older unsent one.
 * @param {Storage|null|undefined} store
 * @param {any} record
 * @returns {any|null} The stored record, or null when it cannot be keyed.
 */
export function rememberUnsentAnswer(store, record) {
  const studentKey =
    unsentStudentKey(record || {}) || String(record?.studentKey || "");
  const promptId = Number(record?.promptId) || 0;
  if (!studentKey || !promptId) return null;
  const response =
    record.response && typeof record.response === "object" ? record.response : {};
  const stored = {
    studentKey,
    studentId: Number(record.studentId) || 0,
    // Never the raw per-visit token: resend re-reads it from the page.
    visitKey: String(record.visitKey || "") || unsentVisitKey(record.visitToken),
    promptId,
    itemId: Number(record.itemId) || 0,
    sessionId: Number(record.sessionId) || 0,
    action: String(record.action || "individual"),
    url: String(record.url || "/api/student/live-prompt/response"),
    body:
      record.body && typeof record.body === "object"
        ? record.body
        : { prompt_id: promptId, response },
    response,
    savedAt: Number(record.savedAt) || Date.now(),
    doNotSend: Boolean(record.doNotSend),
  };
  const all = readUnsentStore(store);
  Object.entries(all).forEach(([key, row]) => {
    if (Number(row?.promptId) !== promptId) return;
    if (!sameUnsentStudent(row, stored)) return;
    delete all[key];
    memoryFallback.delete(key);
  });
  const key = unsentRecordKey(studentKey, promptId);
  all[key] = stored;
  if (!writeUnsentStore(store, all)) memoryFallback.set(key, stored);
  else memoryFallback.delete(key);
  return stored;
}

/**
 * Drop one stored answer for this person and prompt.
 * @param {Storage|null|undefined} store
 * @param {any} record
 */
export function forgetUnsentAnswer(store, record) {
  const promptId = Number(record?.promptId) || 0;
  if (!promptId) return;
  const all = readUnsentStore(store);
  Object.entries(all).forEach(([key, row]) => {
    if (promptId && Number(row?.promptId) !== promptId) return;
    if (!sameUnsentStudent(row, record)) return;
    delete all[key];
    memoryFallback.delete(key);
  });
  writeUnsentStore(store, all);
}

/**
 * Keep the text for the closed-question note, and never send it.
 * @param {Storage|null|undefined} store
 * @param {any} record
 * @returns {any|null}
 */
export function markUnsentClosed(store, record) {
  return rememberUnsentAnswer(store, { ...record, doNotSend: true });
}

/**
 * Drop stale rows: older than the TTL, from another live session, or from an
 * older build that stored the raw visit token. Run on load and after a flush.
 * @param {Storage|null|undefined} store
 * @param {{now?: number, ttlMs?: number, liveSessionId?: unknown, sessionEnded?: boolean}} [opts]
 * @returns {number} Rows removed.
 */
export function pruneUnsentAnswers(store, opts = {}) {
  const now = Number(opts.now) || Date.now();
  const ttlMs = Number(opts.ttlMs) || UNSENT_ANSWER_TTL_MS;
  const liveSession = Number(opts.liveSessionId) || 0;
  const all = readUnsentStore(store);
  let removed = 0;
  Object.entries(all).forEach(([key, row]) => {
    const savedAt = Number(row?.savedAt) || 0;
    const sessionId = Number(row?.sessionId) || 0;
    const stale =
      !row ||
      typeof row !== "object" ||
      !savedAt ||
      savedAt > now + 60 * 1000 ||
      now - savedAt > ttlMs ||
      Object.prototype.hasOwnProperty.call(row, "visitToken") ||
      (liveSession && sessionId && sessionId !== liveSession) ||
      (opts.sessionEnded && (!liveSession || sessionId === liveSession || !sessionId));
    if (!stale) return;
    delete all[key];
    memoryFallback.delete(key);
    removed += 1;
  });
  if (removed) writeUnsentStore(store, all);
  return removed;
}

/**
 * Remove every unsent answer on this device (sign-out / student switch).
 * @param {Storage|null|undefined} store
 */
export function clearUnsentAnswers(store) {
  memoryFallback.clear();
  if (!store) return;
  try {
    if (typeof store.removeItem === "function") store.removeItem(UNSENT_ANSWER_STORE_KEY);
    else writeUnsentStore(store, {});
  } catch (_err) {
    /* storage blocked: nothing persisted to clear */
  }
}

/**
 * Rows for this person. ``includeHeld`` keeps answers we will not send.
 * @param {Storage|null|undefined} store
 * @param {{studentId?: unknown, visitToken?: unknown}} identity
 * @param {{includeHeld?: boolean}} [opts]
 * @returns {any[]}
 */
export function listUnsentAnswers(store, identity = {}, opts = {}) {
  const probe = {
    studentKey: unsentStudentKey(identity),
    studentId: Number(identity.studentId) || 0,
    visitKey: unsentVisitKey(identity.visitToken),
  };
  if (!probe.studentKey && !probe.studentId && !probe.visitKey) return [];
  return Object.values(readUnsentStore(store)).filter((row) => {
    if (!opts.includeHeld && row?.doNotSend) return false;
    return sameUnsentStudent(row, probe);
  });
}

/**
 * Classify a failed submit.
 * @param {{message?: unknown, error?: unknown, status?: unknown, pollClosed?: unknown}} failure
 * @returns {"offline"|"closed"|"rejected"}
 */
export function classifyUnsentFailure(failure = {}) {
  const status = Number(failure.status) || 0;
  const error = String(failure.error || failure.message || "").trim();
  const lower = error.toLowerCase();
  if (failure.pollClosed) return "closed";
  if (
    lower.includes("no longer active") ||
    lower.includes("no active prompt") ||
    lower.includes("poll is closed") ||
    lower.includes("not accepting") ||
    lower.includes("not open for answers") ||
    lower.includes("voting has ended")
  ) {
    return "closed";
  }
  if (status === 409) return "closed";
  if (
    /choose an answer|enter a (whole )?number|enter an integer|before submitting|rank every/.test(
      lower
    )
  ) {
    return "rejected";
  }
  if (status === 401 || status === 403) return "rejected";
  if (status >= 400 && status < 500) return "rejected";
  return "offline";
}

/**
 * Find the lifecycle card for a stored answer.
 * @param {any} payload
 * @param {any} record
 * @returns {any|null}
 */
function cardForUnsent(payload, record) {
  const promptId = Number(record?.promptId) || 0;
  const itemId = Number(record?.itemId) || 0;
  const pools = []
    .concat(payload?.active_questions || [])
    .concat(payload?.closed_results || [])
    .concat(payload?.saved_cards || []);
  for (const row of pools) {
    const pid = Number(row?.prompt?.id) || 0;
    const iid = Number(row?.id) || 0;
    if ((promptId && pid === promptId) || (itemId && iid === itemId)) return row;
  }
  return null;
}

/**
 * Whether a stored answer may be posted again.
 * @param {any} payload Latest ``/api/student/state`` body, or null.
 * @param {any} record
 * @returns {"send"|"landed"|"closed"|"forget"|"wait"}
 */
export function unsentResendDecision(payload, record) {
  if (!record || record.doNotSend) return "closed";
  if (!payload || typeof payload !== "object") return "wait";
  const savedSession = Number(record.sessionId) || 0;
  const liveSession = Number(payload.live_session_id) || 0;
  if (savedSession && liveSession && savedSession !== liveSession) return "forget";
  if (payload.status === "ended" || payload.celebrate) return "forget";
  const card = cardForUnsent(payload, record);
  if (card) {
    if (card.my_response) return "landed";
    const status = String(card.status || "").toLowerCase();
    if (status === "closed") return "closed";
    if (status === "active" && card.can_submit === false) return "landed";
    if (status === "active") return "send";
    return "closed";
  }
  const promptId = Number(record.promptId) || 0;
  if (promptId && Number(payload.prompt?.id) === promptId) {
    if (payload.poll_closed) return "closed";
    if (payload.my_response) return "landed";
    return "send";
  }
  return "wait";
}

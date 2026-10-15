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
  const visitToken = String(identity.visitToken || "").trim();
  if (visitToken) return `v:${visitToken}`;
  return "";
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
  const leftVisit = String(left?.visitToken || "").trim();
  const rightVisit = String(right?.visitToken || "").trim();
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
  const studentKey = String(record?.studentKey || "");
  const promptId = Number(record?.promptId) || 0;
  if (!studentKey || !promptId) return null;
  const response =
    record.response && typeof record.response === "object" ? record.response : {};
  const stored = {
    studentKey,
    studentId: Number(record.studentId) || 0,
    visitToken: String(record.visitToken || "").trim(),
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
    visitToken: String(identity.visitToken || "").trim(),
  };
  if (!probe.studentKey && !probe.studentId && !probe.visitToken) return [];
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

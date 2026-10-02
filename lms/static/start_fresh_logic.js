/**
 * MCK-133 / MCK-160: pure helpers for the staff "Start fresh" control (no
 * DOM), so node tests can cover the copy and the error handling.
 */

/** Wonder v1 copy (MCK-133) plus MCK-160 additions. */
export const FRESH_COPY = {
  title: "Start Celebrations fresh?",
  body: "Awards start counting again from today. Attendance and participation records stay in each course.",
  toastAll: "Celebrations started fresh for all your classes. Past records are kept.",
  toastOne: "Celebrations started fresh for {class}. Past records are kept.",
  error: "Couldn't start fresh. Try again.",
  skipped: "{courses} skipped: shared with another teacher's class.",
  skippedMany: "{courses} skipped: shared with other teachers' classes.",
};

export const START_FRESH_URL = "/api/staff/celebrations/start-fresh";

/**
 * "MCR3U", "MCR3U and SBI3U", "MCF3M, MCR3U and SBI3U".
 * @param {string[]} names
 * @returns {string}
 */
export function joinCourses(names) {
  const list = (Array.isArray(names) ? names : []).map((n) => String(n || "").trim()).filter(Boolean);
  if (list.length <= 1) return list.join("");
  return `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}`;
}

/**
 * Success line after Start fresh. With All and skipped (shared) courses it
 * names the classes that did start fresh, so the two sentences agree.
 * @param {{value: string, name?: string, started?: string[], skipped?: string[]}} opts
 * @returns {string}
 */
export function successText({ value, name = "", started = [], skipped = [] }) {
  const skippedNames = (skipped || []).map((n) => String(n || "").trim()).filter(Boolean);
  const startedNames = (started || []).map((n) => String(n || "").trim()).filter(Boolean);
  let text;
  if (value !== "all") {
    text = FRESH_COPY.toastOne.replace("{class}", name);
  } else if (skippedNames.length && startedNames.length) {
    text = FRESH_COPY.toastOne.replace("{class}", joinCourses(startedNames));
  } else {
    text = FRESH_COPY.toastAll;
  }
  if (skippedNames.length) {
    const tpl = skippedNames.length > 1 ? FRESH_COPY.skippedMany : FRESH_COPY.skipped;
    text = `${text} ${tpl.replace("{courses}", joinCourses(skippedNames))}`;
  }
  return text;
}

/** An error from the Start fresh request; ``reason`` is safe to show. */
export class FreshError extends Error {
  /**
   * @param {string} reason server reason ("" when none or not showable)
   * @param {number} status HTTP status (0 when unknown)
   */
  constructor(reason, status) {
    super(reason || FRESH_COPY.error);
    this.name = "FreshError";
    this.reason = reason;
    this.status = status;
  }
}

/**
 * True for text that is markup or too long to be a one-line reason.
 * @param {string} text
 */
function notShowable(text) {
  const t = String(text || "").trim();
  return !t || t.startsWith("<") || /<\/?[a-z!][^>]*>/i.test(t) || t.length > 240 || /[\r\n]/.test(t);
}

/**
 * POST Start fresh. Resolves with the JSON body on success. Rejects with a
 * FreshError whose ``reason`` is the server's JSON ``error`` (a class is
 * running, a shared course) or "" for anything else: HTML 500 pages, proxy
 * text, empty bodies (LOW-11). Network failures reject as thrown by fetch.
 * @param {typeof fetch} fetchImpl
 * @param {object} payload
 * @returns {Promise<object>}
 */
export async function postStartFresh(fetchImpl, payload) {
  const res = await fetchImpl(START_FRESH_URL, {
    method: "POST",
    credentials: "same-origin",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  let text = "";
  try {
    text = await res.text();
  } catch {
    text = "";
  }
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (res.ok && data && typeof data === "object" && data.ok !== false) return data;
  const raw = data && typeof data === "object" && typeof data.error === "string" ? data.error : "";
  throw new FreshError(notShowable(raw) ? "" : raw.trim(), Number(res.status) || 0);
}

/**
 * The message to show for a failed Start fresh: the server's reason when it
 * sent a showable one, else Wonder's generic error.
 * @param {unknown} err
 * @returns {string}
 */
export function errorText(err) {
  if (err instanceof FreshError && err.reason) return err.reason;
  return FRESH_COPY.error;
}

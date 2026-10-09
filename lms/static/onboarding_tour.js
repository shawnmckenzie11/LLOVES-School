/**
 * MCK-183 slice D: the 4-step Dashboard tour and the Help menu.
 *
 * State per teacher in localStorage ``alc-onboarding:<user id>`` =
 * ``{"step": 1-4, "status": "active"|"skipped"|"done"}`` (IA §4). The tour
 * opens by itself when the page offers it (``body[data-tour-offer="1"]``:
 * a staff teacher, not Shawn, no class live) and she has not skipped or
 * finished it. ``/staff?tour=1`` (Help → Take the tour again) restarts it.
 * The tour never clicks anything or starts a live class.
 * It dispatches ``alc-tour-end`` on the document when skipped or finished,
 * so What's new (held while the tour is pending) can open after it.
 */

export const TOUR_STEPS = 4;

/**
 * @param {string} userId
 * @returns {string}
 */
export function tourKey(userId) {
  return `alc-onboarding:${String(userId || "").trim()}`;
}

/**
 * Read the saved state; anything unreadable is "no state".
 * @param {Storage|null} storage
 * @param {string} userId
 * @returns {{step: number, status: string}|null}
 */
export function readTour(storage, userId) {
  if (!storage || !userId) return null;
  try {
    const raw = storage.getItem(tourKey(userId));
    if (!raw) return null;
    const data = JSON.parse(raw);
    const step = Math.min(TOUR_STEPS, Math.max(1, Number(data.step) || 1));
    const status = ["active", "skipped", "done"].includes(data.status) ? data.status : "active";
    return { step, status };
  } catch (_err) {
    return null;
  }
}

/**
 * @param {Storage|null} storage
 * @param {string} userId
 * @param {{step: number, status: string}} state
 */
export function writeTour(storage, userId, state) {
  if (!storage || !userId) return;
  try {
    storage.setItem(tourKey(userId), JSON.stringify({ step: state.step, status: state.status }));
  } catch (_err) {
    /* blocked storage: the tour may be offered again, harmless */
  }
}

/**
 * Is the tour still owed to this teacher (so What's new should wait)?
 * @param {{doc: Document, storage: Storage|null, userId: string}} args
 * @returns {boolean}
 */
export function tourPending({ doc, storage, userId }) {
  const body = doc && doc.body;
  const offer = body && body.dataset ? body.dataset.tourOffer : "";
  if (offer !== "1") return false;
  const state = readTour(storage, userId);
  return !state || state.status === "active";
}

/**
 * Which step should open now, or 0 for none.
 * @param {{offer: boolean, forced: boolean, state: {step: number, status: string}|null}} args
 * @returns {number}
 */
export function startStep({ offer, forced, state }) {
  if (forced) return 1;
  if (!offer) return 0;
  if (!state) return 1;
  return state.status === "active" ? state.step : 0;
}

/**
 * Wire the tour on the Dashboard. Safe on pages without it.
 * @param {{document: Document, window?: any, storage?: Storage|null}} env
 * @returns {number} the step opened, or 0
 */
export function initTour(env) {
  const doc = env.document;
  const win = env.window || {};
  const root = doc.getElementById("onb-tour");
  const userId = String((doc.body && doc.body.dataset && doc.body.dataset.userId) || "").trim();
  initHelpMenu(doc);
  if (!root || !userId) return 0;
  const storage = env.storage === undefined ? null : env.storage;
  const search = String((win.location && win.location.search) || "");
  const forced = /(?:^|[?&])tour=1(?:&|$)/.test(search);
  const offer = Boolean(doc.body.dataset && doc.body.dataset.tourOffer === "1");
  const first = startStep({ offer, forced, state: readTour(storage, userId) });
  if (!first) return 0;

  const steps = Array.from(root.querySelectorAll("[data-tour-step]"));
  const back = root.querySelector("[data-tour-back]");
  const next = root.querySelector("[data-tour-next]");
  const finish = root.querySelector("[data-tour-finish]");
  const skip = root.querySelector("[data-tour-skip]");
  const count = root.querySelector("[data-tour-count]");
  let step = first;
  let lit = null;

  const end = (status) => {
    writeTour(storage, userId, { step, status });
    root.hidden = true;
    if (lit) lit.classList.remove("onb-tour-target");
    if (typeof doc.dispatchEvent === "function" && typeof win.CustomEvent === "function") {
      doc.dispatchEvent(new win.CustomEvent("alc-tour-end", { detail: { status } }));
    }
  };
  const show = (n) => {
    step = n;
    for (const el of steps) el.hidden = Number(el.getAttribute("data-tour-step")) !== n;
    if (back) back.hidden = n === 1;
    if (next) next.hidden = n === TOUR_STEPS;
    if (finish) finish.hidden = n !== TOUR_STEPS;
    if (count) count.textContent = `Step ${n} of ${TOUR_STEPS}`;
    if (lit) lit.classList.remove("onb-tour-target");
    const current = steps.find((el) => Number(el.getAttribute("data-tour-step")) === n);
    const selector = current ? current.getAttribute("data-tour-target") : "";
    lit = selector ? doc.querySelector(selector) : null;
    if (lit) {
      lit.classList.add("onb-tour-target");
      if (typeof lit.scrollIntoView === "function") lit.scrollIntoView({ block: "center", behavior: "smooth" });
    }
    writeTour(storage, userId, { step: n, status: "active" });
  };

  if (back) back.addEventListener("click", () => show(Math.max(1, step - 1)));
  if (next) next.addEventListener("click", () => show(Math.min(TOUR_STEPS, step + 1)));
  if (skip) skip.addEventListener("click", () => end("skipped"));
  if (finish) {
    finish.addEventListener("click", () => {
      end("done");
      const href = root.getAttribute("data-class-href") || "";
      if (href && win.location && typeof win.location.assign === "function") win.location.assign(href);
    });
  }
  root.hidden = false;
  show(first);
  return first;
}

/**
 * Help menu: "What's new" opens the Dashboard panel in place; Escape and a
 * click outside close the menu.
 * @param {Document} doc
 */
export function initHelpMenu(doc) {
  if (!doc || typeof doc.querySelectorAll !== "function") return;
  for (const menu of doc.querySelectorAll("[data-staff-help]")) {
    const wn = menu.querySelector("[data-help-whats-new]");
    if (wn) {
      wn.addEventListener("click", () => {
        menu.open = false;
        const open = doc.getElementById("whats-new-open");
        if (open && typeof open.click === "function") open.click();
      });
    }
    if (typeof doc.addEventListener === "function") {
      doc.addEventListener("keydown", (event) => {
        if (event && event.key === "Escape") menu.open = false;
      });
      doc.addEventListener("click", (event) => {
        if (menu.open && event && event.target && typeof menu.contains === "function" && !menu.contains(event.target)) {
          menu.open = false;
        }
      });
    }
  }
}

if (typeof document !== "undefined" && typeof window !== "undefined") {
  let storage = null;
  try {
    storage = window.localStorage;
  } catch (_err) {
    storage = null;
  }
  initTour({ document, window, storage });
}

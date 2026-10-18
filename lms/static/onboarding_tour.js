/**
 * MCK-267: spotlight tour and the Getting started checklist.
 *
 * State per teacher in localStorage ``alc-onboarding:<user id>`` =
 * ``{version:2, status, page, step, done:{task:iso}, checklist:{collapsed, hidden}}``.
 * A step finishes when the teacher clicks its real control. Checklist rows
 * tick from real events (roster, page view, a Test run started and ended,
 * attendance saved), not from the tour itself. ``/staff?tour=1`` (Help →
 * Take the tour again) resets the current page's steps and keeps ticks.
 * The tour dispatches ``alc-tour-end`` when it is skipped or finished so
 * What's new can open after it. It never starts over a live class.
 */

/** @typedef {{id:string, selector:string, text?:string, copy:string, wait?:boolean, dwell?:number}} TourStep */

/** Dashboard, live class, and Attendance & Participation steps. Copy is final. */
export const PAGES = {
  dashboard: [
    {
      id: "set_class",
      selector: ".course-card:first-of-type .btn-populate",
      text: "Populate Class",
      copy: "Click <b>Populate Class</b> to add your roster.",
    },
    {
      id: "question_bank",
      selector: 'nav a[href*="question-banks"]',
      text: "Question bank",
      copy: "<b>Find questions.</b> Open the <b>Question Bank</b> to browse ready-made questions and polls.",
    },
    {
      id: "test_toggle",
      selector: '[aria-label="Test or Live class"]',
      copy: "Switch to <b>Test</b> so this run doesn't go in your gradebook.",
    },
    {
      id: "run_live",
      selector: "a.course-action-live",
      text: "Run Live Class",
      copy: "Click <b>Run Live Class</b>. Share the join code with your students.",
    },
  ],
  live: [
    {
      id: "publish",
      selector: "button[data-publish-live-item], button[data-surface-publish]",
      text: "Publish",
      wait: true,
      copy: "<b>Ask a quick question.</b> Pick a question and click <b>Publish</b>.",
    },
    {
      id: "watch_answers",
      selector: ".live-question-progress",
      wait: true,
      dwell: 3000,
      copy: "<b>Answers show up here.</b> Each one counts toward participation.",
    },
    {
      id: "end_live",
      selector: '[aria-label="End Class"]',
      text: "End Class",
      copy: "Click <b>End Class</b>. Attendance and participation are saved.",
    },
  ],
  ap: [
    {
      id: "take_attendance",
      selector: '#take-attendance, a[aria-label="Take Attendance"]',
      text: "Take Attendance",
      copy: "<b>Check who came.</b> Click <b>Take Attendance</b>.",
    },
    {
      id: "participation",
      selector: 'nav.ap-subtabs a[href*="view=participation"]',
      text: "Participation",
      copy: "<b>See who took part.</b> Open <b>Participation</b>.",
    },
  ],
};

/** Getting started rows, in order. ``step`` is the spotlight a tick also closes. */
export const TASKS = [
  { id: "populate", step: "set_class", label: "Populate your class", href: "dashboard" },
  { id: "question_bank", step: "question_bank", label: "Open the Question Bank", href: "nav" },
  { id: "test_live", step: "run_live", label: "Run a test live class", href: "dashboard" },
  { id: "attendance", step: "take_attendance", label: "Take attendance", href: "ap" },
  { id: "participation", step: "participation", label: "Check Participation", href: "ap" },
];

const TASK_IDS = TASKS.map((task) => task.id);
const STATUSES = ["active", "paused", "skipped", "done"];
const HREF_KEYS = {
  set_class: "tourDashboard",
  question_bank: "tourNav",
  test_toggle: "tourDashboard",
  run_live: "tourDashboard",
  publish: "tourLiveHref",
  watch_answers: "tourLiveHref",
  end_live: "tourLiveHref",
  take_attendance: "tourAp",
  participation: "tourAp",
};

/**
 * Storage key for one teacher.
 * @param {string} userId
 * @returns {string}
 */
export function tourKey(userId) {
  return `alc-onboarding:${String(userId || "").trim()}`;
}

/**
 * Whether this live run was chosen as Test.
 *
 * TODO(MCK-269): Test/Live is chosen at the date step. The page passes
 * that field through as ``data-tour-run-type``. Until the field exists the
 * value is empty: live-class steps still run (they default to a Test class)
 * and the checklist does not tick, so a Live run is not counted.
 *
 * @param {string|null|undefined} value
 * @returns {boolean|null} true when Test, false when Live, null when unknown
 */
export function liveRunIsTest(value) {
  const raw = String(value || "").trim().toLowerCase();
  if (raw === "test") return true;
  if (raw === "live") return false;
  return null;
}

/**
 * Steps for one page. An explicit Live run has no live-class spotlight.
 * @param {string} page
 * @param {string} [runType]
 * @returns {TourStep[]}
 */
export function stepsFor(page, runType) {
  if (page === "live" && liveRunIsTest(runType) === false) return [];
  return (PAGES[page] || []).slice();
}

/**
 * @param {string} id
 * @returns {TourStep|null}
 */
export function stepById(id) {
  for (const steps of Object.values(PAGES)) {
    const found = steps.find((step) => step.id === id);
    if (found) return found;
  }
  return null;
}

/**
 * @param {string} id
 * @returns {{page:string, index:number, total:number}|null}
 */
function locate(id) {
  for (const [page, steps] of Object.entries(PAGES)) {
    const index = steps.findIndex((step) => step.id === id);
    if (index >= 0) return { page, index, total: steps.length };
  }
  return null;
}

/**
 * Empty v2 state.
 * @param {object} [over]
 * @returns {object}
 */
export function blankState(over = {}) {
  const checklist = { collapsed: false, hidden: false, toast: false, ...(over.checklist || {}) };
  return {
    version: 2,
    status: STATUSES.includes(over.status) ? over.status : "active",
    page: over.page || "dashboard",
    step: over.step || "set_class",
    focus: over.focus || "",
    done: { ...(over.done || {}) },
    checklist,
    liveTest: over.liveTest || null,
  };
}

/**
 * Read saved state. Old done/skipped values carry over. Unreadable is null.
 * @param {Storage|null} storage
 * @param {string} userId
 * @returns {object|null}
 */
export function readTour(storage, userId) {
  if (!storage || !userId) return null;
  let data;
  try {
    const raw = storage.getItem(tourKey(userId));
    if (!raw) return null;
    data = JSON.parse(raw);
  } catch (_err) {
    return null;
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  if (Number(data.version) === 2) {
    const status = STATUSES.includes(data.status) ? data.status : "active";
    const checklist = data.checklist && typeof data.checklist === "object" ? data.checklist : {};
    return blankState({
      status,
      page: data.page || "dashboard",
      step: data.step || "set_class",
      focus: data.focus || "",
      done: data.done && typeof data.done === "object" ? data.done : {},
      checklist: {
        collapsed: Boolean(checklist.collapsed),
        hidden: Boolean(checklist.hidden),
        toast: Boolean(checklist.toast),
      },
      liveTest: data.liveTest && typeof data.liveTest === "object" ? data.liveTest : null,
    });
  }
  if (data.status === "done" || data.status === "skipped") {
    return blankState({
      status: data.status,
      checklist: { collapsed: data.status === "skipped", hidden: false, toast: false },
    });
  }
  if (data.status === "active" || Number(data.step) >= 1) return blankState({ status: "active" });
  return null;
}

/**
 * Persist v2 state. Last write wins across tabs.
 * @param {Storage|null} storage
 * @param {string} userId
 * @param {object} state
 */
export function writeTour(storage, userId, state) {
  if (!storage || !userId || !state) return;
  const checklist = state.checklist || {};
  const payload = {
    version: 2,
    status: state.status,
    page: state.page || "dashboard",
    step: state.step || "set_class",
    focus: state.focus || "",
    done: state.done || {},
    checklist: {
      collapsed: Boolean(checklist.collapsed),
      hidden: Boolean(checklist.hidden),
      toast: Boolean(checklist.toast),
    },
    liveTest: state.liveTest || null,
  };
  try {
    storage.setItem(tourKey(userId), JSON.stringify(payload));
  } catch (_err) {
    /* blocked storage: the tour may be offered again */
  }
}

/**
 * Copy checklist ticks onto the matching spotlight steps.
 * @param {object} state
 * @returns {object}
 */
export function syncTicks(state) {
  const done = { ...(state.done || {}) };
  if (done.populate) done.set_class = done.set_class || done.populate;
  if (done.attendance) done.take_attendance = done.take_attendance || done.attendance;
  if (done.test_live) {
    done.run_live = done.run_live || done.test_live;
    done.end_live = done.end_live || done.test_live;
    done.test_toggle = done.test_toggle || done.test_live;
  }
  return { ...state, done };
}

/**
 * How many Getting started rows are ticked.
 * @param {object} done
 * @returns {number}
 */
export function checklistCount(done) {
  return TASK_IDS.filter((id) => done && done[id]).length;
}

/**
 * Fold real events into state. Spotlight clicks do not come through here.
 * @param {object} state
 * @param {object} signals
 * @returns {object}
 */
export function applySignals(state, signals = {}) {
  const now = signals.now || new Date().toISOString();
  const done = { ...(state.done || {}) };
  const wasComplete = checklistCount(done) === TASK_IDS.length;
  if (Number(signals.roster) >= 1) {
    if (!done.populate) done.populate = now;
    if (!done.set_class) done.set_class = now;
  }
  if (signals.page === "question_bank" && !done.question_bank) done.question_bank = now;
  if ((signals.view === "participation" || signals.page === "participation") && !done.participation) {
    done.participation = now;
  }
  if (signals.attendanceSaved) {
    if (!done.attendance) done.attendance = now;
    if (!done.take_attendance) done.take_attendance = now;
  }
  let liveTest = state.liveTest || null;
  const kind = liveRunIsTest(signals.runType);
  if (kind === true && signals.live) {
    const session = String(signals.session || "");
    if (!liveTest || liveTest.kind !== "test" || liveTest.session !== session) {
      liveTest = { session, started: now, kind: "test" };
    }
  }
  if (signals.liveEnded && kind === true) {
    if (!liveTest || liveTest.kind !== "test") {
      liveTest = { session: String(signals.session || ""), started: now, kind: "test" };
    }
    if (!done.test_live) done.test_live = now;
    if (!done.run_live) done.run_live = now;
    if (!done.end_live) done.end_live = now;
    if (!done.test_toggle) done.test_toggle = now;
  }
  let status = state.status;
  let checklist = { ...(state.checklist || {}) };
  const complete = checklistCount(done) === TASK_IDS.length;
  if (complete) {
    status = "done";
    checklist = { ...checklist, collapsed: true, toast: checklist.toast || !wasComplete };
  }
  return syncTicks({ ...state, version: 2, status, done, liveTest, checklist });
}

/**
 * Mark a spotlight step done because its control was clicked.
 * Does not tick a checklist row unless that row uses the same id.
 * @param {object} state
 * @param {string} stepId
 * @param {string} [now]
 * @returns {object}
 */
export function completeStep(state, stepId, now) {
  if (!stepId || (state.done && state.done[stepId])) return state;
  const done = { ...(state.done || {}), [stepId]: now || new Date().toISOString() };
  return {
    ...state,
    done,
    step: stepId,
    focus: state.focus === stepId ? "" : state.focus || "",
  };
}

/**
 * Help → Take the tour again. Clears this page's steps and keeps ticks.
 * @param {object} state
 * @param {string} page
 * @returns {object}
 */
export function replayPage(state, page) {
  const ids = new Set((PAGES[page] || []).map((step) => step.id));
  const ticks = new Set(TASK_IDS);
  const done = {};
  for (const [key, value] of Object.entries(state.done || {})) {
    if (ticks.has(key) || !ids.has(key)) done[key] = value;
  }
  const first = (PAGES[page] || [])[0];
  return syncTicks({
    ...state,
    status: "active",
    page: page || state.page,
    step: first ? first.id : state.step,
    focus: "",
    done,
    checklist: { ...(state.checklist || {}), toast: false },
  });
}

/**
 * Esc or a click on the dim. The tour resumes on the next load.
 * @param {object} state
 * @returns {object}
 */
export function pauseTour(state) {
  return { ...state, status: "paused", focus: "" };
}

/**
 * Skip tour. The checklist stays collapsed.
 * @param {object} state
 * @returns {object}
 */
export function skipTour(state) {
  return {
    ...state,
    status: "skipped",
    focus: "",
    checklist: { ...(state.checklist || {}), collapsed: true, toast: false },
  };
}

/**
 * Checklist "Show me": arm that row's spotlight.
 * @param {object} state
 * @param {string} taskId
 * @returns {object}
 */
export function showMeTask(state, taskId) {
  const task = TASKS.find((row) => row.id === taskId);
  if (!task) return state;
  let focus = task.step;
  if (taskId === "test_live" && !(state.done || {}).test_toggle) focus = "test_toggle";
  return {
    ...state,
    status: "active",
    page: locate(focus)?.page || state.page,
    step: focus,
    focus,
    checklist: { ...(state.checklist || {}), collapsed: false },
  };
}

/**
 * What the spotlight should show. ``controls`` maps a step id to
 * ``ready`` | ``wait`` | ``skip`` | ``absent``.
 * @param {object} args
 * @returns {{mode:string, id?:string, n?:number, total?:number, waiting?:string}}
 */
export function planSpotlight(args) {
  const controls = args.controls || {};
  const done = args.done || {};
  if (args.focus && !done[args.focus] && controls[args.focus] === "ready") {
    const loc = locate(args.focus);
    if (loc) return { mode: "step", id: args.focus, n: loc.index + 1, total: loc.total };
  }
  const steps = stepsFor(args.page, args.runType);
  if (!steps.length) return { mode: "off" };
  const first = steps[0];
  if (!done[first.id] && controls[first.id] === "absent" && args.noCourseCard && args.page === "dashboard") {
    return { mode: "empty" };
  }
  for (let i = 0; i < steps.length; i += 1) {
    const step = steps[i];
    if (done[step.id]) continue;
    const kind = controls[step.id] || "skip";
    if (kind === "ready") return { mode: "step", id: step.id, n: i + 1, total: steps.length };
    if (kind === "wait") return { mode: "off", waiting: step.id };
  }
  return { mode: "off" };
}

/**
 * May the spotlight open by itself (or because Help replayed it)?
 * @param {{offer:boolean, forced:boolean, status:string}} args
 * @returns {boolean}
 */
export function spotlightAllowed({ offer, forced, status }) {
  if (forced) return true;
  if (!offer) return false;
  return status !== "skipped" && status !== "done";
}

/**
 * Visible label of a control, collapsed to single spaces.
 * @param {Element} el
 * @returns {string}
 */
function labelOf(el) {
  return String((el && el.textContent) || "").replace(/\s+/g, " ").trim();
}

/**
 * True when the teacher can click this control.
 * @param {Element} el
 * @returns {boolean}
 */
function usable(el) {
  if (!el || el.hidden || el.disabled) return false;
  if (typeof el.getAttribute === "function") {
    if (el.getAttribute("aria-disabled") === "true") return false;
    if (el.getAttribute("hidden") != null) return false;
  }
  if (el.classList && (el.classList.contains("is-disabled") || el.classList.contains("is-parked"))) return false;
  if (typeof el.closest === "function") {
    if (el.closest("[hidden], .is-parked, .pack-busy, dialog:not([open])")) return false;
    const card = el.closest(".course-card");
    if (card && typeof card.getAttribute === "function" && card.getAttribute("data-pack-busy") === "1") return false;
  }
  return true;
}

/**
 * Classify one step's control on the live page.
 * @param {Document} doc
 * @param {TourStep} step
 * @returns {{kind:string, el:Element|null}}
 */
export function findControl(doc, step) {
  const parts = String(step.selector || "").split(",").map((part) => part.trim()).filter(Boolean);
  const found = [];
  for (const part of parts) {
    if (!doc || typeof doc.querySelectorAll !== "function") break;
    const nodes = doc.querySelectorAll(part);
    if (nodes && typeof nodes.forEach === "function") nodes.forEach((el) => found.push(el));
    else if (nodes && typeof nodes.length === "number") {
      for (let i = 0; i < nodes.length; i += 1) found.push(nodes[i]);
    }
  }
  const matched = found.filter((el) => !step.text || labelOf(el).includes(step.text));
  const ready = matched.find((el) => usable(el));
  if (ready) return { kind: "ready", el: ready };
  if (matched.length) return { kind: "skip", el: matched[0] };
  if (step.wait) return { kind: "wait", el: null };
  return { kind: "absent", el: null };
}

/**
 * Is What's new still waiting on this teacher?
 * @param {{doc: Document, storage: Storage|null, userId: string}} args
 * @returns {boolean}
 */
export function tourPending({ doc, storage, userId }) {
  const body = doc && doc.body;
  const offer = body && body.dataset ? body.dataset.tourOffer : "";
  if (offer !== "1") return false;
  const state = readTour(storage, userId);
  return !state || (state.status !== "skipped" && state.status !== "done");
}

/**
 * Help menu: What's new opens the Dashboard panel; Escape and an outside
 * click close the menu.
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

/**
 * @param {Window} win
 * @returns {boolean}
 */
function isNarrow(win) {
  if (win && typeof win.matchMedia === "function") return win.matchMedia("(max-width: 1023px)").matches;
  const width = win && typeof win.innerWidth === "number" ? win.innerWidth : 1280;
  return width < 1024;
}

/**
 * @param {Window} win
 * @param {string} href
 * @returns {boolean}
 */
function samePath(win, href) {
  try {
    const loc = (win && win.location) || { href: "http://localhost/" };
    const url = new URL(href, loc.href);
    return url.pathname === loc.pathname && url.search === (loc.search || "");
  } catch (_err) {
    return false;
  }
}

/**
 * @param {Document} doc
 * @param {string} stepId
 * @returns {string}
 */
function hrefFor(doc, stepId) {
  const key = HREF_KEYS[stepId];
  const data = doc.body && doc.body.dataset;
  return key && data ? String(data[key] || "") : "";
}

/**
 * @param {EventTarget|null} target
 * @returns {Element|null}
 */
function eventElement(target) {
  if (!target) return null;
  if (target.nodeType === 1) return /** @type {Element} */ (target);
  return target.parentElement || null;
}

/**
 * Place the popover beside the lit control.
 * @param {HTMLElement} pop
 * @param {Element} target
 * @param {Window} win
 */
function placePopover(pop, target, win) {
  if (!pop || !target || typeof target.getBoundingClientRect !== "function") return;
  const rect = target.getBoundingClientRect();
  const width = pop.offsetWidth || 320;
  const height = pop.offsetHeight || 160;
  const gap = 12;
  const viewW = (win && win.innerWidth) || 1280;
  const viewH = (win && win.innerHeight) || 800;
  let left = rect.right + gap;
  if (left + width > viewW - 8) left = Math.max(8, rect.left - width - gap);
  let top = rect.top;
  if (top + height > viewH - 8) top = Math.max(8, viewH - height - 8);
  pop.style.left = `${left}px`;
  pop.style.top = `${top}px`;
}

/**
 * Wire the spotlight and the checklist. Safe on pages without the markup.
 * @param {{document: Document, window?: any, storage?: Storage|null, now?: Function, schedule?: Function, cancel?: Function}} env
 * @returns {string} step id opened, ``empty``, or ``""``
 */
export function initTour(env) {
  const doc = env.document;
  const win = env.window || {};
  const nowFn = env.now || (() => new Date().toISOString());
  const schedule = env.schedule || ((fn, ms) => setTimeout(fn, ms));
  const cancel = env.cancel || ((id) => clearTimeout(id));
  initHelpMenu(doc);
  const body = doc.body || { dataset: {} };
  const userId = String((body.dataset && body.dataset.userId) || "").trim();
  if (!userId) return "";
  const storage = env.storage === undefined ? null : env.storage;
  const search = String((win.location && win.location.search) || "");
  const forced = /(?:^|[?&])tour=1(?:&|$)/.test(search);
  const offer = Boolean(body.dataset && body.dataset.tourOffer === "1");
  const page = String((body.dataset && body.dataset.tourPage) || "");
  const block = Boolean(body.dataset && body.dataset.tourBlock === "1");
  const narrow = isNarrow(win);
  const existing = readTour(storage, userId);
  const engaged = Boolean(existing) || offer || forced;
  let state = existing || blankState();
  if (forced) state = replayPage(state, page || "dashboard");
  if (engaged) {
    state = applySignals(state, {
      roster: Number((body.dataset && body.dataset.tourRoster) || 0),
      page,
      view: String((body.dataset && body.dataset.tourView) || ""),
      runType: String((body.dataset && body.dataset.tourRunType) || ""),
      live: Boolean(body.dataset && body.dataset.tourLive === "1"),
      session: String((body.dataset && body.dataset.tourSession) || ""),
      now: nowFn(),
    });
    writeTour(storage, userId, state);
  }
  let pausedHere = false;

  const root = doc.getElementById ? doc.getElementById("onb-tour") : null;
  const check = doc.getElementById ? doc.getElementById("onb-check") : null;
  const toast = doc.getElementById ? doc.getElementById("onb-toast") : null;
  let currentEl = null;
  let dwellId = null;
  let opened = "";
  let painted = "";
  let lastTarget = null;
  let checkSig = "";
  let painting = false;

  const persist = (next, eventStatus) => {
    state = next;
    writeTour(storage, userId, state);
    if (eventStatus && typeof doc.dispatchEvent === "function" && typeof win.CustomEvent === "function") {
      doc.dispatchEvent(new win.CustomEvent("alc-tour-end", { detail: { status: eventStatus } }));
    }
  };

  const clearLit = () => {
    if (currentEl && currentEl.classList) currentEl.classList.remove("onb-spotlight", "is-pulse");
    currentEl = null;
    if (dwellId != null) {
      cancel(dwellId);
      dwellId = null;
    }
  };

  const showChecklist = () => {
    if (!check) return;
    const hidden = Boolean(state.checklist && state.checklist.hidden);
    const visible = !hidden && engaged;
    const done = state.done || {};
    const n = checklistCount(done);
    const collapsed = Boolean(state.checklist && state.checklist.collapsed) || n === TASK_IDS.length;
    let nextId = "";
    for (const task of TASKS) {
      if (!done[task.id]) {
        nextId = task.id;
        break;
      }
    }
    const sig = `${visible ? 1 : 0}|${n}|${collapsed ? 1 : 0}|${nextId}`;
    if (sig === checkSig) return;
    checkSig = sig;
    check.hidden = !visible;
    if (!visible) return;
    const card = check.querySelector("[data-check-card]");
    const pill = check.querySelector("[data-check-pill]");
    const title = check.querySelector("[data-check-title]");
    const bar = check.querySelector("[data-check-fill]");
    const pillLabel = check.querySelector("[data-check-pill-label]");
    const dismiss = check.querySelector("[data-check-dismiss]");
    if (card) card.hidden = collapsed;
    if (pill) pill.hidden = !collapsed;
    if (title) title.textContent = `Getting started ${n} of 5`;
    if (bar) bar.style.width = `${(n / TASK_IDS.length) * 100}%`;
    if (pillLabel) pillLabel.textContent = n === TASK_IDS.length ? "✓ Getting started 5/5" : `Getting started ${n}/5`;
    if (dismiss) dismiss.hidden = n !== TASK_IDS.length;
    for (const row of check.querySelectorAll("[data-task]")) {
      const id = row.getAttribute("data-task");
      const ticked = Boolean(done[id]);
      row.classList.toggle("is-done", ticked);
      row.classList.toggle("is-next", id === nextId);
      const button = row.querySelector("[data-check-show]");
      if (button) button.hidden = id !== nextId;
    }
  };

  const hideSpot = () => {
    clearLit();
    if (root) root.hidden = true;
    const pop = root && root.querySelector("[data-tour-pop]");
    const empty = root && root.querySelector("[data-tour-empty]");
    if (pop) pop.hidden = true;
    if (empty) empty.hidden = true;
  };

  const showToast = () => {
    if (!toast || !state.checklist || !state.checklist.toast) return;
    toast.hidden = false;
    toast.textContent = "You're all set. Replay the tour anytime from Help.";
    persist({ ...state, checklist: { ...state.checklist, toast: false } });
    schedule(() => {
      toast.hidden = true;
    }, 6000);
  };

  const paint = () => {
    if (painting) return opened;
    painting = true;
    try {
      return renderTour();
    } finally {
      painting = false;
    }
  };

  const renderTour = () => {
    showChecklist();
    showToast();
    const allowed = !pausedHere && spotlightAllowed({ offer, forced, status: state.status });
    if (!root || !allowed || block || narrow) {
      hideSpot();
      opened = "";
      return opened;
    }
    const wanted = new Set(stepsFor(page, body.dataset.tourRunType).map((step) => step.id));
    if (state.focus) wanted.add(state.focus);
    const controls = {};
    const els = {};
    for (const id of wanted) {
      const step = stepById(id);
      if (!step) continue;
      const found = findControl(doc, step);
      controls[id] = found.kind;
      els[id] = found.el;
    }
    const noCourseCard = !(doc.querySelector && doc.querySelector(".course-card"));
    let plan = planSpotlight({
      page,
      runType: body.dataset.tourRunType,
      done: state.done,
      controls,
      focus: state.focus,
      noCourseCard,
    });
    if (plan.mode !== "step" && state.focus) {
      const href = hrefFor(doc, state.focus);
      if (href && win.location && typeof win.location.assign === "function" && !samePath(win, href)) {
        win.location.assign(href);
        opened = "";
        return opened;
      }
      plan = planSpotlight({
        page,
        runType: body.dataset.tourRunType,
        done: state.done,
        controls,
        focus: "",
        noCourseCard,
      });
    }
    const target = plan.mode === "step" ? els[plan.id] || null : null;
    const sig = [
      plan.mode,
      plan.id || "",
      plan.waiting || "",
      state.status,
      String(checklistCount(state.done || {})),
      state.checklist && state.checklist.collapsed ? "1" : "0",
      state.checklist && state.checklist.hidden ? "1" : "0",
    ].join("|");
    if (sig === painted && target === lastTarget) {
      opened = plan.mode === "step" ? String(plan.id || "") : plan.mode === "empty" ? "empty" : "";
      return opened;
    }
    painted = sig;
    lastTarget = target;
    const pop = root.querySelector("[data-tour-pop]");
    const empty = root.querySelector("[data-tour-empty]");
    clearLit();
    if (plan.mode === "empty") {
      root.hidden = false;
      if (pop) pop.hidden = true;
      if (empty) empty.hidden = false;
      opened = "empty";
      return opened;
    }
    if (plan.mode !== "step") {
      hideSpot();
      opened = "";
      return opened;
    }
    const step = stepById(plan.id);
    const el = els[plan.id];
    root.hidden = false;
    if (empty) empty.hidden = true;
    if (pop) {
      pop.hidden = false;
      const count = pop.querySelector("[data-tour-count]");
      const copy = pop.querySelector("[data-tour-copy]");
      if (count) count.textContent = `Step ${plan.n} of ${plan.total}`;
      if (copy) copy.innerHTML = step ? step.copy : "";
    }
    if (el) {
      currentEl = el;
      if (el.classList) el.classList.add("onb-spotlight");
      if (typeof el.scrollIntoView === "function") el.scrollIntoView({ block: "center", inline: "nearest" });
      placePopover(pop, el, win);
    }
    if (step && step.dwell) {
      const dwellStep = plan.id;
      dwellId = schedule(() => {
        if (state.step !== dwellStep || (state.done && state.done[dwellStep])) return;
        finishStep(dwellStep);
      }, step.dwell);
    }
    if (state.step !== plan.id || state.page !== (page || state.page)) {
      state = { ...state, page: page || state.page, step: plan.id };
      writeTour(storage, userId, state);
    }
    opened = plan.id;
    return opened;
  };

  const finishStep = (stepId) => {
    const before = checklistCount(state.done);
    state = completeStep(state, stepId, nowFn());
    const after = applySignals(state, { now: nowFn() });
    const became = checklistCount(after.done) === TASK_IDS.length && before < TASK_IDS.length;
    persist(after, became ? "done" : "");
    paint();
  };

  const endTour = (status) => {
    clearLit();
    if (status === "skipped") persist(skipTour(state), "skipped");
    else {
      pausedHere = true;
      persist(pauseTour(state));
    }
    hideSpot();
    showChecklist();
    opened = "";
  };

  if (root) {
    const dim = root.querySelector("[data-tour-dim]");
    if (dim) dim.addEventListener("click", () => endTour("paused"));
    for (const skip of root.querySelectorAll("[data-tour-skip]")) {
      skip.addEventListener("click", (event) => {
        if (event && typeof event.preventDefault === "function") event.preventDefault();
        endTour("skipped");
      });
    }
    const show = root.querySelector("[data-tour-show]");
    if (show) {
      show.addEventListener("click", (event) => {
        if (event && typeof event.preventDefault === "function") event.preventDefault();
        if (currentEl && currentEl.classList) {
          currentEl.classList.remove("is-pulse");
          if (typeof currentEl.offsetWidth === "number") void currentEl.offsetWidth;
          currentEl.classList.add("is-pulse");
        }
        if (currentEl && typeof currentEl.scrollIntoView === "function") {
          currentEl.scrollIntoView({ block: "center", inline: "nearest" });
        }
        const pop = root.querySelector("[data-tour-pop]");
        const copy = pop && pop.querySelector("[data-tour-copy]");
        const step = stepById(state.step);
        if (copy && step) copy.innerHTML = step.copy;
      });
    }
  }

  if (typeof doc.addEventListener === "function") {
    doc.addEventListener("keydown", (event) => {
      if (event && event.key === "Escape" && root && !root.hidden) endTour("paused");
    });
    doc.addEventListener("click", (event) => {
      if (!root || root.hidden || !currentEl) return;
      const el = eventElement(event && event.target);
      if (!el) return;
      if (root.contains && root.contains(el)) return;
      if (check && check.contains && check.contains(el)) return;
      if (el === currentEl || (typeof currentEl.contains === "function" && currentEl.contains(el))) {
        finishStep(state.step);
      }
    }, true);
    doc.addEventListener("submit", (event) => {
      const form = event && event.target;
      if (!form || (form.id !== "live-end-class-form" && form.id !== "end-live-form")) return;
      const ended = applySignals(state, {
        runType: String((body.dataset && body.dataset.tourRunType) || ""),
        live: true,
        session: String((body.dataset && body.dataset.tourSession) || ""),
        liveEnded: true,
        now: nowFn(),
      });
      const became = state.status !== "done" && ended.status === "done";
      persist(ended, became ? "done" : "");
    }, true);
  }

  if (win.addEventListener) {
    win.addEventListener("alc-attendance-saved", () => {
      const next = applySignals(state, { attendanceSaved: true, now: nowFn() });
      const became = state.status !== "done" && next.status === "done";
      persist(next, became ? "done" : "");
      paint();
    });
    win.addEventListener("storage", (event) => {
      if (!event || event.key !== tourKey(userId)) return;
      const incoming = readTour(storage, userId);
      if (!incoming) return;
      state = incoming;
      paint();
    });
  }

  if (check) {
    check.addEventListener("click", (event) => {
      const el = eventElement(event && event.target);
      if (!el || typeof el.closest !== "function") return;
      if (el.closest("[data-check-collapse]")) {
        persist({ ...state, checklist: { ...state.checklist, collapsed: true } });
        showChecklist();
        return;
      }
      if (el.closest("[data-check-expand]")) {
        if (checklistCount(state.done) === TASK_IDS.length) return;
        persist({ ...state, checklist: { ...state.checklist, collapsed: false } });
        showChecklist();
        return;
      }
      if (el.closest("[data-check-dismiss]")) {
        persist({ ...state, checklist: { ...state.checklist, hidden: true, collapsed: true } });
        showChecklist();
        return;
      }
      const showBtn = el.closest("[data-check-show]");
      if (showBtn) {
        const row = showBtn.closest("[data-task]");
        const taskId = row ? row.getAttribute("data-task") : "";
        state = showMeTask(state, taskId || "");
        persist(state);
        paint();
      }
    });
  }

  if (root && typeof win.MutationObserver === "function" && doc.body) {
    const observer = new win.MutationObserver(() => paint());
    observer.observe(doc.body, { childList: true, subtree: true });
  }

  paint();
  return opened;
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

/**
 * Staff "What's new" panel (MCK-124, MCK-182).
 *
 * Reads the Dashboard's history from ``/api/staff/whats-new`` (newest
 * release first). Every release is ``{id, deployed_at, day, items: [{text |
 * title + line, audience}]}``; the server keeps every entry that has shipped
 * and never sends ticket ids, PR numbers or SHAs.
 *
 * MCK-182 slice 2 (Mobbin S2/S3):
 * - Unseen = released after the newest one this staff user closed the
 *   panel on (``alc-whats-new:<user id>`` holds that release's
 *   ``deployed_at``; a legacy bare date reads as that day 23:59, a legacy
 *   release id as that release), and no older than 30 days.
 * - The pop-up opens by itself at most once a day per user
 *   (``alc-whats-new:<user id>:popped`` = Toronto date) and shows unseen
 *   items only, grouped by day ("Sun Oct 4"), newest first, at most 6, then
 *   a "+n more" row. A later deploy the same day only lights the dot.
 * - A plain dot on the What's new button while anything is unseen; the
 *   count is in the button's aria-label only. Closing clears it.
 * - The button opens the full list: the newest 3 days open, unseen items
 *   tagged "New", then an "Earlier" tail of older days (30 days back).
 * - "Students see this too" is the only chip; teacher-only items get none.
 *
 * Hard rule: never on the live class. On ``body.course-live`` (the teacher
 * may be projecting) this module renders nothing, fetches nothing and never
 * opens, not even from the button. Text goes in with ``textContent`` only.
 */

export const SEEN_KEY_PREFIX = "alc-whats-new:";
export const POP_MAX_ITEMS = 6;
export const OPEN_DAYS = 3;
export const EARLIER_DAYS = 30;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const DAY_RE = /^(\d{4})-(\d{2})-(\d{2})$/;
const DAY_MS = 86400000;
// Last guard: teachers never see ticket ids, PR numbers or SHAs.
const REF_RES = [
  /\(\s*[^()]*?(?:#\d+|MCK-\d+)[^()]*\)/gi,
  /\bMCK-\d+\b/gi,
  /(^|[^\w&])#\d+\b/g,
  /\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b/g,
];

/**
 * True on a page where the panel must never render or open.
 * @param {Document} doc
 * @returns {boolean}
 */
export function isLiveClassPage(doc) {
  const body = doc && doc.body;
  return !body || body.classList.contains("course-live");
}

/** localStorage key for this staff user's newest seen release. */
export function seenKey(userId) {
  return `${SEEN_KEY_PREFIX}${String(userId || "").trim()}`;
}

/** localStorage key for the Toronto day the pop-up last opened by itself. */
export function poppedKey(userId) {
  return `${seenKey(userId)}:popped`;
}

/**
 * Text with ticket ids, PR numbers and SHAs taken out.
 * @param {any} value
 * @returns {string}
 */
export function scrubRefs(value) {
  let out = String(value == null ? "" : value);
  for (let prev = ""; prev !== out; ) {
    prev = out;
    out = out.replace(REF_RES[0], ""); // nested "(#202 (+#200) · MCK-46)"
  }
  out = out.replace(REF_RES[1], "").replace(REF_RES[2], "$1").replace(REF_RES[3], "");
  return out.replace(/\([\s·,+]*\)/g, "").replace(/\s+([,.;:])/g, "$1").replace(/\s{2,}/g, " ").trim();
}

/**
 * Items worth showing: objects with some ``text``, ``title`` or ``line``.
 * @param {any} release
 * @returns {any[]}
 */
export function cleanItems(release) {
  const items = release && Array.isArray(release.items) ? release.items : [];
  return items.filter(
    (item) =>
      item &&
      typeof item === "object" &&
      ["text", "title", "line"].some((k) => scrubRefs(item[k]).length > 0),
  );
}

/**
 * Sort time (ms) for a release: ``deployed_at``, or its day at 23:59 UTC-4.
 * @param {any} release
 * @returns {number} NaN when unknown.
 */
export function releaseTime(release) {
  if (!release) return NaN;
  const stamp = Date.parse(String(release.deployed_at || ""));
  if (!Number.isNaN(stamp)) return stamp;
  const day = releaseDay(release);
  return day ? Date.parse(`${day}T23:59:00-04:00`) : NaN;
}

/** Toronto ``YYYY-MM-DD`` for a release. */
export function releaseDay(release) {
  for (const key of ["day", "date", "id"]) {
    const value = String((release && release[key]) || "").slice(0, 10);
    if (DAY_RE.test(value)) return value;
  }
  return "";
}

/**
 * Today's date in Toronto as ``YYYY-MM-DD``.
 * @param {Date} [now]
 * @returns {string}
 */
export function torontoDay(now = new Date()) {
  try {
    return new Intl.DateTimeFormat("en-CA", {
      timeZone: "America/Toronto",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(now);
  } catch (_err) {
    return new Date(now.getTime() - 4 * 3600000).toISOString().slice(0, 10);
  }
}

/** ``day`` minus ``n`` days (``YYYY-MM-DD``, no timezone shift). */
export function dayMinus(day, n) {
  const m = DAY_RE.exec(String(day || ""));
  if (!m) return "";
  const t = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])) - n * DAY_MS;
  return new Date(t).toISOString().slice(0, 10);
}

/**
 * Wonder's group header, "Sun Oct 4", from "2026-10-04".
 * @param {string} day
 * @returns {string}
 */
export function formatDayHeader(day) {
  const m = DAY_RE.exec(String(day || ""));
  if (!m) return String(day || "");
  const weekday = WEEKDAYS[new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]))).getUTCDay()];
  return `${weekday} ${MONTHS[Number(m[2]) - 1]} ${Number(m[3])}`;
}

/**
 * Releases with something to show, newest first, items cleaned.
 * @param {any} data
 * @returns {any[]}
 */
export function usableReleases(data) {
  const releases = data && Array.isArray(data.releases) ? data.releases : [];
  const out = [];
  const ids = new Set();
  for (const release of releases) {
    if (!release || typeof release.id !== "string" || !release.id || ids.has(release.id)) continue;
    const items = cleanItems(release);
    const time = releaseTime(release);
    const day = releaseDay(release);
    if (!items.length || Number.isNaN(time) || !day) continue;
    ids.add(release.id);
    out.push({ ...release, items, time, day });
  }
  // Stable: same-time releases keep their order.
  return out.map((r, i) => [r, i]).sort((a, b) => b[0].time - a[0].time || a[1] - b[1]).map(([r]) => r);
}

/** Newest release with items, or null (kept for callers of slice 1). */
export function newestRelease(data) {
  return usableReleases(data)[0] || null;
}

/**
 * The seen time (ms) from a stored value, or null.
 * @param {string|null} stored
 * @param {any[]} releases Usable releases.
 * @returns {number|null}
 */
export function seenTime(stored, releases) {
  const value = String(stored == null ? "" : stored).trim();
  if (!value) return null;
  if (DAY_RE.test(value)) return Date.parse(`${value}T23:59:00-04:00`);
  const match = (releases || []).find((r) => r.id === value);
  if (match) return match.time;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

/**
 * Group releases by day, newest day first; items keep deploy then file order.
 * @param {any[]} releases Usable releases, newest first.
 * @param {number|null} seenAt
 * @returns {{day: string, items: {item: any, isNew: boolean}[]}[]}
 */
export function groupByDay(releases, seenAt) {
  const groups = [];
  for (const release of releases) {
    let group = groups[groups.length - 1];
    if (!group || group.day !== release.day) {
      group = groups.find((g) => g.day === release.day);
      if (!group) {
        group = { day: release.day, items: [] };
        groups.push(group);
      }
    }
    const isNew = seenAt == null || release.time > seenAt;
    for (const item of release.items) group.items.push({ item, isNew });
  }
  return groups;
}

/**
 * Everything the panel needs to decide and draw.
 * @param {any} data Payload.
 * @param {{stored: string|null, today: string}} args
 */
export function buildModel(data, { stored, today }) {
  const releases = usableReleases(data);
  const cutoff = dayMinus(today, EARLIER_DAYS);
  const seenAt = seenTime(stored, releases);
  const unseenReleases = releases.filter(
    (r) => (seenAt == null || r.time > seenAt) && (!cutoff || r.day >= cutoff),
  );
  const unseenCount = unseenReleases.reduce((n, r) => n + r.items.length, 0);
  const newest = releases[0] || null;
  return { releases, seenAt, unseenReleases, unseenCount, newest, today, cutoff };
}

/**
 * Should the panel open by itself now?
 * @param {{doc: Document, storage: Storage|null, userId: string, model: any}} args
 * @returns {boolean}
 */
export function shouldAutoOpen({ doc, storage, userId, model }) {
  if (isLiveClassPage(doc) || !model || !userId || !model.unseenCount) return false;
  let popped = null;
  try {
    popped = storage ? storage.getItem(poppedKey(userId)) : null;
  } catch (_err) {
    popped = null;
  }
  return popped !== model.today;
}

function el(doc, tag, className, text) {
  const node = doc.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function changes(n) {
  return n === 1 ? "1 change" : `${n} changes`;
}

function renderItem(doc, { item, isNew }, showNew) {
  const li = el(doc, "li", "whats-new-item");
  const main = el(doc, "div", "whats-new-item-main");
  const titleText = scrubRefs(item.title);
  if (titleText) main.append(el(doc, "h4", "whats-new-item-title", titleText));
  const line = el(doc, "p", "whats-new-item-line");
  if (showNew && isNew) line.append(el(doc, "span", "whats-new-tag", "New"));
  line.append(doc.createTextNode ? doc.createTextNode(scrubRefs(item.text || item.line)) : scrubRefs(item.text || item.line));
  main.append(line);
  li.append(main);
  if (String(item.audience || "").startsWith("Both")) {
    li.append(el(doc, "span", "whats-new-aud is-both", "Students see this too"));
  }
  return li;
}

function renderDay(doc, group, showNew, tag = "section") {
  const section = el(doc, tag, "whats-new-day");
  section.append(el(doc, "h3", "whats-new-day-head", formatDayHeader(group.day)));
  const ul = el(doc, "ul", "whats-new-items");
  for (const entry of group.items) ul.append(renderItem(doc, entry, showNew));
  section.append(ul);
  return section;
}

/**
 * MCK-183: while the Dashboard tour is still owed to this teacher (offered
 * and not skipped or done), or restarted with ``?tour=1``, What's new waits
 * and opens after the tour ends (``alc-tour-end``). Same storage key as
 * ``onboarding_tour.js``; read here so this module needs no import.
 * @param {{doc: Document, storage: Storage|null, userId: string, search?: string}} args
 * @returns {boolean}
 */
export function tourHolds({ doc, storage, userId, search }) {
  if (!doc || !doc.getElementById || !doc.getElementById("onb-tour")) return false;
  if (/(?:^|[?&])tour=1(?:&|$)/.test(String(search || ""))) return true;
  const body = doc.body;
  if (!body || !body.dataset || body.dataset.tourOffer !== "1") return false;
  let status = "";
  try {
    const raw = storage ? storage.getItem(`alc-onboarding:${String(userId || "").trim()}`) : null;
    status = raw ? String((JSON.parse(raw) || {}).status || "") : "";
  } catch (_err) {
    status = "";
  }
  return status !== "skipped" && status !== "done";
}

/**
 * Pop-up: unseen items only, at most ``POP_MAX_ITEMS``, then "+n more".
 * @returns {{shown: number, more: number}}
 */
export function renderPopup(doc, dialog, model, onMore) {
  const body = dialog.querySelector("[data-whats-new-list]");
  const sub = dialog.querySelector("[data-whats-new-sub]");
  const all = dialog.querySelector("[data-whats-new-all]");
  dialog.setAttribute("data-mode", "popup");
  if (sub) sub.textContent = changes(model.unseenCount);
  if (all) all.hidden = false;
  if (!body) return { shown: 0, more: 0 };
  body.replaceChildren();
  let left = POP_MAX_ITEMS;
  for (const group of groupByDay(model.unseenReleases, model.seenAt)) {
    if (left <= 0) break;
    const kept = { day: group.day, items: group.items.slice(0, left) };
    left -= kept.items.length;
    body.append(renderDay(doc, kept, false));
  }
  const shown = POP_MAX_ITEMS - left;
  const more = Math.max(0, model.unseenCount - shown);
  if (more) {
    const btn = el(doc, "button", "whats-new-more", `+${more} more`);
    btn.setAttribute("type", "button");
    btn.addEventListener("click", () => onMore && onMore());
    body.append(btn);
  }
  return { shown, more };
}

/**
 * Full list: newest ``OPEN_DAYS`` days open, then an "Earlier" tail.
 * @returns {{open: number, earlier: number}}
 */
export function renderAll(doc, dialog, model) {
  const body = dialog.querySelector("[data-whats-new-list]");
  const sub = dialog.querySelector("[data-whats-new-sub]");
  const all = dialog.querySelector("[data-whats-new-all]");
  dialog.setAttribute("data-mode", "all");
  if (sub) sub.textContent = "All updates";
  if (all) all.hidden = true;
  if (!body) return { open: 0, earlier: 0 };
  body.replaceChildren();
  const groups = groupByDay(model.releases, model.seenAt);
  const open = groups.slice(0, OPEN_DAYS);
  for (const group of open) body.append(renderDay(doc, group, true));
  const earlier = groups.slice(OPEN_DAYS).filter((g) => !model.cutoff || g.day >= model.cutoff);
  if (earlier.length) {
    const tail = el(doc, "details", "whats-new-earlier");
    tail.append(el(doc, "summary", "", "Earlier"));
    for (const group of earlier) {
      const row = el(doc, "details", "whats-new-earlier-day");
      row.append(el(doc, "summary", "", `${formatDayHeader(group.day)} · ${changes(group.items.length)}`));
      const ul = el(doc, "ul", "whats-new-items");
      for (const entry of group.items) ul.append(renderItem(doc, entry, true));
      row.append(ul);
      tail.append(row);
    }
    body.append(tail);
  }
  return { open: open.length, earlier: earlier.length };
}

/** Show or clear the dot and the button's aria count. */
export function setDot(button, count) {
  if (!button) return;
  const dot = button.querySelector("[data-whats-new-dot]");
  if (dot) dot.hidden = !count;
  button.setAttribute("aria-label", count ? `What's new, ${count} new` : "What's new");
}

/**
 * Wire the panel on a staff page. Safe to call on any page.
 * @param {{document: Document, window?: any, fetch?: Function, storage?: Storage|null, now?: Date}} env
 * @returns {Promise<"live"|"absent"|"empty"|"error"|"opened"|"held"|"dot"|"seen">}
 */
export async function initWhatsNew(env) {
  const doc = env.document;
  if (isLiveClassPage(doc)) return "live";
  const dialog = doc.getElementById("whats-new-dialog");
  const userId = String((doc.body.dataset && doc.body.dataset.userId) || "").trim();
  if (!dialog || !userId || typeof env.fetch !== "function") return "absent";
  const storage = env.storage === undefined ? null : env.storage;
  const read = (key) => {
    try {
      return storage ? storage.getItem(key) : null;
    } catch (_err) {
      return null;
    }
  };
  const write = (key, value) => {
    try {
      if (storage) storage.setItem(key, value);
    } catch (_err) {
      /* storage full or blocked: it shows again next time, harmless */
    }
  };
  const src = dialog.getAttribute("data-releases-src") || "/api/staff/whats-new";
  let data = null;
  try {
    const res = await env.fetch(src, { credentials: "same-origin", cache: "no-store" });
    if (!res || !res.ok) return "error";
    data = await res.json();
  } catch (_err) {
    return "error";
  }
  const today = torontoDay(env.now || new Date());
  let model = buildModel(data, { stored: read(seenKey(userId)), today });
  if (!model.newest) return "empty";
  // The class might have gone live while the history loaded.
  if (isLiveClassPage(doc)) return "live";

  const button = doc.getElementById("whats-new-open");
  const open = (mode) => {
    if (isLiveClassPage(doc)) return;
    if (mode === "popup") renderPopup(doc, dialog, model, () => renderAll(doc, dialog, model));
    else renderAll(doc, dialog, model);
    if (dialog.open) return;
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  };
  dialog.addEventListener("close", () => {
    // Everything up to the newest release is now seen.
    const newest = model.newest;
    write(seenKey(userId), String(newest.deployed_at || new Date(newest.time).toISOString()));
    model = buildModel(data, { stored: read(seenKey(userId)), today });
    setDot(button, 0);
  });
  for (const btn of dialog.querySelectorAll("[data-whats-new-close]")) {
    btn.addEventListener("click", () => dialog.close());
  }
  for (const btn of dialog.querySelectorAll("[data-whats-new-all]")) {
    btn.addEventListener("click", () => renderAll(doc, dialog, model));
  }
  if (button) {
    button.hidden = false;
    setDot(button, model.unseenCount);
    button.addEventListener("click", (event) => {
      if (event && typeof event.preventDefault === "function") event.preventDefault();
      open("all");
    });
  }
  const loc = env.window && env.window.location;
  const search = String((loc && loc.search) || "");
  // MCK-183: Help → What's new from another staff page lands here with ?whats_new=1.
  if (/(?:^|[?&])whats_new=1(?:&|$)/.test(search)) {
    open("all");
    return "opened";
  }
  if (shouldAutoOpen({ doc, storage, userId, model })) {
    // MCK-183: the Dashboard tour goes first; the once-a-day pop-up waits
    // for it to end (Skip or Done) and is only counted when it shows.
    if (tourHolds({ doc, storage, userId, search })) {
      if (typeof doc.addEventListener === "function") {
        doc.addEventListener("alc-tour-end", () => {
          if (!shouldAutoOpen({ doc, storage, userId, model })) return;
          write(poppedKey(userId), today);
          open("popup");
        });
      }
      return "held";
    }
    write(poppedKey(userId), today);
    open("popup");
    return "opened";
  }
  return model.unseenCount ? "dot" : "seen";
}

if (typeof document !== "undefined" && typeof window !== "undefined") {
  let storage = null;
  try {
    storage = window.localStorage;
  } catch (_err) {
    storage = null;
  }
  void initWhatsNew({ document, window, fetch: window.fetch.bind(window), storage });
}

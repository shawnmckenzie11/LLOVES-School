/** Shared helpers for the Math Game Show teacher UI. */

/**
 * Escape text for safe insertion into HTML.
 * @param {unknown} value
 * @returns {string}
 */
export function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * Parse a class id out of paths like /class/3/game or /scoreboard/3.
 * @returns {number}
 */
export function classIdFromPath() {
  const parts = location.pathname.split("/").filter(Boolean);
  const n = Number(parts[1]);
  if (!Number.isFinite(n) || n < 1) {
    throw new Error("Missing class id in URL");
  }
  return n;
}

/**
 * JSON fetch with {ok:false,error} handling.
 * Always sends same-origin cookies (staff session) and prefers JSON.
 * @param {string} url
 * @param {RequestInit} [options]
 * @returns {Promise<any>}
 */
export async function api(url, options = {}) {
  const headers = {
    Accept: "application/json",
    ...(options.headers || {}),
  };
  if (options.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(url, {
    credentials: "same-origin",
    ...options,
    headers,
  });
  const text = await response.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      throw new Error(text || `HTTP ${response.status}`);
    }
  }
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

/**
 * Show an error string on a page-level banner.
 * @param {string} selector
 * @param {unknown} err
 */
export function showError(selector, err) {
  const el = document.querySelector(selector);
  if (!el) return;
  el.hidden = false;
  el.textContent = err instanceof Error ? err.message : String(err);
}

/**
 * Hide a page-level banner.
 * @param {string} selector
 */
export function hideError(selector) {
  const el = document.querySelector(selector);
  if (!el) return;
  el.hidden = true;
  el.textContent = "";
}

/**
 * Student display name.
 * @param {{first_name:string, last_display:string}} student
 * @param {"first"|"last"} sort
 */
export function displayName(student, sort) {
  const code = (student.codename || "").trim();
  if (code) return code;
  const first = (student.first_name || "").trim();
  const last = (student.last_display || "").trim();
  if (sort === "first") {
    return [first, last].filter(Boolean).join(" ");
  }
  return first ? `${last}, ${first}` : last;
}

/**
 * Dashboard name-order preference for this class (localStorage).
 * @param {number} classId
 * @returns {"first"|"last"}
 */
export function dashboardSort(classId) {
  return localStorage.getItem(`mgs-sort-${classId}`) === "first" ? "first" : "last";
}

/**
 * Sort a roster the same way as the class spreadsheet.
 * @param {Array<{first_name:string, last_display:string}>} students
 * @param {"first"|"last"} sort
 */
export function sortStudents(students, sort) {
  const copy = [...(students || [])];
  copy.sort((a, b) => {
    const primaryA = (sort === "first" ? a.first_name : a.last_display) || "";
    const primaryB = (sort === "first" ? b.first_name : b.last_display) || "";
    const cmp = primaryA.toLowerCase().localeCompare(primaryB.toLowerCase());
    if (cmp !== 0) return cmp;
    const secondaryA = (sort === "first" ? a.last_display : a.first_name) || "";
    const secondaryB = (sort === "first" ? b.last_display : b.first_name) || "";
    return secondaryA.toLowerCase().localeCompare(secondaryB.toLowerCase());
  });
  return copy;
}

/**
 * Show whole points as integers; tenths otherwise (e.g. 3.3).
 * @param {unknown} value
 */
export function formatPoints(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "0";
  const rounded = Math.round(n * 10) / 10;
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
}

/**
 * Format remaining seconds as ``m:ss`` (e.g. ``20:00``, ``0:00``).
 * @param {unknown} seconds
 * @returns {string}
 */
export function formatCountdown(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/**
 * Seconds left until an epoch-ms deadline. Never below 0.
 * @param {unknown} endsAtMs
 * @returns {number}
 */
export function remainingUntilMs(endsAtMs) {
  const end = Number(endsAtMs);
  if (!Number.isFinite(end) || end <= 0) return 0;
  return Math.max(0, (end - Date.now()) / 1000);
}

/**
 * Keep a stable deadline unless the server started a new round.
 * @param {number} currentMs
 * @param {unknown} nextMs
 * @returns {number}
 */
export function lockRoundDeadline(currentMs, nextMs) {
  const next = Number(nextMs);
  if (!Number.isFinite(next) || next <= 0) return currentMs || 0;
  if (!currentMs || Math.abs(next - currentMs) > 750) return next;
  return currentMs;
}

const SCOREBOARD_OVERLAY_NAME = "mgs-scoreboard";
const LIVE_SESSION_OVERLAY_NAME = "mgs-live-session";

/** Opener-side handle for the Zoom-share live overlay (not a named-window lookup). */
let liveSessionOverlayWindow = null;

/**
 * Popup chrome for the student-facing ESPN overlay (Zoom share window).
 * @returns {string}
 */
function scoreboardOverlayFeatures() {
  const height = 400;
  const width = Math.max(960, Math.round(Number(screen.availWidth) || 1280));
  const top = Math.max(0, Math.round((Number(screen.availHeight) || 900) - height));
  return `popup=yes,width=${width},height=${height},left=0,top=${top}`;
}

/**
 * Narrow vertical strip (~10% viewport width) for the live-session overlay.
 * @returns {string}
 */
function liveSessionOverlayFeatures() {
  const width = Math.max(160, Math.round((Number(screen.availWidth) || 1280) * 0.1));
  const height = Math.max(480, Math.round(Number(screen.availHeight) || 900));
  return `popup=yes,width=${width},height=${height},left=0,top=0`;
}

/**
 * Navigate a reserved/named popup to a URL with given window features.
 * @param {string} url
 * @param {string} features
 * @param {Window|null} [existing]
 * @param {string} [windowName]
 * @returns {Window|null}
 */
function openNamedOverlay(url, features, existing, windowName = SCOREBOARD_OVERLAY_NAME) {
  if (existing && !existing.closed) {
    try {
      existing.location.href = url;
      existing.focus();
      return existing;
    } catch {
      existing.close();
    }
  }
  const win = window.open(url, windowName, features);
  return win && !win.closed ? win : null;
}

/**
 * Reserve a popup during a click, before any ``await`` (avoids blockers).
 * @returns {Window|null}
 */
export function reserveScoreboardOverlay() {
  const win = window.open("about:blank", SCOREBOARD_OVERLAY_NAME, scoreboardOverlayFeatures());
  return win && !win.closed ? win : null;
}

/**
 * Keep the last script-opened live-overlay Window so Quit can close it.
 * @param {Window|null|undefined} win
 * @returns {Window|null}
 */
function rememberLiveSessionOverlay(win) {
  if (win && !win.closed) {
    liveSessionOverlayWindow = win;
    return win;
  }
  return null;
}

/**
 * Close a popup handle if it is still open.
 * @param {Window|null|undefined} win
 * @returns {boolean} whether close was attempted on an open window
 */
function tryCloseWindow(win) {
  if (!win || win.closed) return false;
  try {
    win.close();
    return true;
  } catch {
    return false;
  }
}

/**
 * Reserve the narrow live-session overlay during a user click (avoids blockers).
 * @returns {Window|null}
 */
export function reserveLiveSessionOverlay() {
  const win = window.open("about:blank", LIVE_SESSION_OVERLAY_NAME, liveSessionOverlayFeatures());
  return rememberLiveSessionOverlay(win);
}

/**
 * Point the reserved (or a new) window at the overlay scoreboard.
 * @param {Window|null} [existing]
 * @returns {Window|null}
 */
export function openScoreboardOverlay(existing) {
  return openNamedOverlay(
    "/scoreboard?overlay=1",
    scoreboardOverlayFeatures(),
    existing,
    SCOREBOARD_OVERLAY_NAME
  );
}

/**
 * Open the vertical live-session overlay (code + roster + optional teams).
 * @param {number} sessionId
 * @param {Window|null} [existing]
 * @param {{classId?: number}} [opts]
 * @returns {Window|null}
 */
export function openLiveSessionOverlay(sessionId, existing, opts = {}) {
  const id = Number(sessionId);
  if (!Number.isFinite(id) || id < 1) return null;
  const classId = Number(opts.classId || 0);
  const qs = new URLSearchParams({ overlay: "1" });
  if (classId > 0) qs.set("class_id", String(classId));
  const url = `/live-overlay/${id}?${qs.toString()}`;
  return rememberLiveSessionOverlay(
    openNamedOverlay(
      url,
      liveSessionOverlayFeatures(),
      existing,
      LIVE_SESSION_OVERLAY_NAME
    )
  );
}

/**
 * Close the Zoom-share live overlay from the staff opener.
 *
 * Uses the module-level Window from reserve/open first. A named
 * ``window.open("", name)`` lookup is only a fallback when no stored
 * handle exists (for example after a staff-page reload).
 */
export function closeLiveSessionOverlay() {
  const stored = liveSessionOverlayWindow;
  liveSessionOverlayWindow = null;
  if (tryCloseWindow(stored)) return;
  try {
    const named = window.open("", LIVE_SESSION_OVERLAY_NAME);
    tryCloseWindow(named);
  } catch {
    /* ignore named-window lookup failures */
  }
}

/**
 * Escape prompt copy and keep light markdown / exponent markers.
 * @param {unknown} value
 * @returns {string}
 */
const KATEX_VERSION = "0.16.22";
const KATEX_CDN = `https://cdn.jsdelivr.net/npm/katex@${KATEX_VERSION}/dist`;

/**
 * Load KaTeX once so staff and student surfaces share one renderer.
 * @returns {Promise<unknown>}
 */
function loadKatex() {
  if (globalThis.katex) return Promise.resolve(globalThis.katex);
  if (globalThis.__llovesKatexPromise) return globalThis.__llovesKatexPromise;
  globalThis.__llovesKatexPromise = new Promise((resolve) => {
    if (!document.querySelector('link[data-lloves-katex]')) {
      const css = document.createElement("link");
      css.rel = "stylesheet";
      css.href = `${KATEX_CDN}/katex.min.css`;
      css.setAttribute("data-lloves-katex", "1");
      document.head.appendChild(css);
    }
    const script = document.createElement("script");
    script.src = `${KATEX_CDN}/katex.min.js`;
    script.async = true;
    script.onload = () => resolve(globalThis.katex || null);
    script.onerror = () => resolve(null);
    document.head.appendChild(script);
  });
  return globalThis.__llovesKatexPromise;
}

/**
 * Wrap dollar-delimited TeX that survived HTML escaping.
 * @param {string} html
 * @returns {string}
 */
function wrapDollarMath(html) {
  return String(html || "").replace(
    /(?<!\\)\$(?!\$)((?:\\.|[^$])+?)(?<!\\)\$(?!\$)/g,
    (_all, latex) => {
      const inner = String(latex || "").trim();
      if (!inner) return _all;
      if (!/[\\^_A-Za-z=+\-*/]/.test(inner)) return _all;
      return `<span class="math-latex" data-latex="${inner}"></span>`;
    }
  );
}

export function formatQuestionHtml(value) {
  const raw = String(value ?? "");
  const pieces = [];
  let last = 0;
  const token =
    /\$\$(.+?)\$\$|\\\[(.+?)\\\]|\\\((.+?)\\\)|(?<!\\)\$(?!\$)((?:\\.|[^$])+?)(?<!\\)\$(?!\$)/gs;
  let match;
  while ((match = token.exec(raw))) {
    pieces.push(_formatPlainQuestionChunk(raw.slice(last, match.index)));
    const latex = String(match[1] || match[2] || match[3] || match[4] || "").trim();
    const isDollar = Boolean(match[4]);
    if (isDollar && !/[\\^_A-Za-z=+\-*/]/.test(latex)) {
      pieces.push(_formatPlainQuestionChunk(match[0]));
    } else {
      pieces.push(
        `<span class="math-latex" data-latex="${escapeHtml(latex)}"></span>`
      );
    }
    last = match.index + match[0].length;
  }
  pieces.push(_formatPlainQuestionChunk(raw.slice(last)));
  return pieces.join("");
}

/**
 * Escape prose and turn caret exponents into superscripts.
 * @param {string} chunk
 * @returns {string}
 */
function _formatPlainQuestionChunk(chunk) {
  let html = escapeHtml(chunk);
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\(([^)]+)\)\^(\d+)/g, "($1)<sup>$2</sup>");
  html = html.replace(/([a-zA-Z])\^(\d+)/g, "$1<sup>$2</sup>");
  return html;
}

/**
 * Prefer server-rendered HTML for a question stem or option.
 * @param {any} item
 * @param {"text"|"option"} field
 * @param {number} [index]
 * @returns {string}
 */
export function questionFieldHtml(item, field, index) {
  if (!item || typeof item !== "object") return "";
  if (field === "text") {
    const html = String(item.text_html || item.stem_html || "").trim();
    return html ? `<span class="live-question-html">${html}</span>` : "";
  }
  if (field === "option") {
    const htmls = item.options_html || item.option_htmls;
    if (Array.isArray(htmls)) {
      const html = String(htmls[index] || "").trim();
      if (html) return `<span class="live-question-html">${html}</span>`;
    }
    const options = Array.isArray(item.options) ? item.options : [];
    const row = options[index];
    if (row && typeof row === "object") {
      const html = String(row.html || row.text_html || "").trim();
      if (html) return `<span class="live-question-html">${html}</span>`;
    }
  }
  return "";
}

/**
 * Render an optional question graph image.
 * @param {unknown} imageUrl
 * @param {{variant?: string}} [opts]
 * @returns {string}
 */
export function questionImageHtml(imageUrl, opts = {}) {
  const url = String(imageUrl || "").trim();
  if (!url) return "";
  const variant = opts.variant === "thumb" ? "is-thumb" : "is-full";
  return `<img class="live-question-image ${variant}" src="${escapeHtml(
    url
  )}" alt="Question graph" loading="lazy">`;
}

/**
 * Typeset ``.math-latex[data-latex]`` nodes when KaTeX is present.
 * @param {Element|null|undefined} root
 * @returns {Promise<void>}
 */
export async function renderLiveQuestionMath(root) {
  if (!(root instanceof Element)) return;
  wrapBareDollarMath(root);
  const nodes = root.querySelectorAll(".math-latex[data-latex]");
  if (!nodes.length) return;
  const katex = await loadKatex();
  nodes.forEach((el) => {
    if (el.querySelector(".katex")) return;
    const latex = String(el.getAttribute("data-latex") || "").trim();
    if (!latex) return;
    const display = el.classList.contains("math-display");
    if (katex && typeof katex.render === "function") {
      try {
        katex.render(latex, el, { throwOnError: false, displayMode: display });
        return;
      } catch {
        /* fall through to plain text */
      }
    }
    el.textContent = latex;
  });
}

/**
 * Convert leftover ``$...$`` text nodes under a live question root.
 * @param {Element} root
 */
function wrapBareDollarMath(root) {
  const hosts = root.querySelectorAll(
    ".live-question-html, .bank-q-stem, .prompt-title, .prompt-choice"
  );
  const targets = hosts.length ? hosts : [root];
  targets.forEach((host) => {
    if (!(host instanceof Element)) return;
    if (host.querySelector(".math-latex")) return;
    const html = host.innerHTML;
    if (!html || !html.includes("$")) return;
    const next = wrapDollarMath(html);
    if (next !== html) host.innerHTML = next;
  });
}

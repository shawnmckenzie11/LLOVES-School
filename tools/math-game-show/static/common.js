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
 * @param {string} url
 * @param {RequestInit} [options]
 * @returns {Promise<any>}
 */
export async function api(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(url, { ...options, headers });
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
 * Reserve a popup during a click, before any ``await`` (avoids blockers).
 * @returns {Window|null}
 */
export function reserveScoreboardOverlay() {
  const win = window.open("about:blank", SCOREBOARD_OVERLAY_NAME, scoreboardOverlayFeatures());
  return win && !win.closed ? win : null;
}

/**
 * Point the reserved (or a new) window at the overlay scoreboard.
 * @param {Window|null} [existing]
 * @returns {Window|null}
 */
export function openScoreboardOverlay(existing) {
  const url = "/scoreboard?overlay=1";
  const features = scoreboardOverlayFeatures();
  if (existing && !existing.closed) {
    try {
      existing.location.href = url;
      existing.focus();
      return existing;
    } catch {
      existing.close();
    }
  }
  const win = window.open(url, SCOREBOARD_OVERLAY_NAME, features);
  return win && !win.closed ? win : null;
}

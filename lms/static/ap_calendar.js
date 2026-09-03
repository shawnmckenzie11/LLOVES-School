/**
 * School-day calendar picker for Attendance & Participation overlays.
 */

/**
 * Allowed ISO dates from log context, sorted ascending.
 * @param {any} logContext
 * @returns {string[]}
 */
export function allowedDates(logContext) {
  return (logContext?.valid_dates || []).map((row) => row.iso).sort();
}

/**
 * Today if it is a valid school day, otherwise the next school day in the semester.
 * @param {any} logContext
 * @returns {string}
 */
export function defaultSchoolDay(logContext) {
  const allowed = allowedDates(logContext);
  const today = String(logContext?.today || "").trim();
  if (today && allowed.includes(today)) return today;
  const next = allowed.find((d) => d >= today);
  return next || allowed[0] || today;
}

/**
 * Next school day to log: first valid date with no attendance yet, else default.
 * @param {any} logContext
 * @returns {string}
 */
export function suggestedLogDay(logContext) {
  const suggested = String(logContext?.suggested_date || "").trim();
  const allowed = allowedDates(logContext);
  if (suggested && allowed.includes(suggested)) return suggested;
  return defaultSchoolDay(logContext);
}

/**
 * Nearest allowed school day on or after ``iso`` (wraps forward through list).
 * @param {string} iso
 * @param {string[]} allowed
 * @returns {string}
 */
export function snapToAllowed(iso, allowed) {
  if (!allowed.length) return iso;
  if (allowed.includes(iso)) return iso;
  for (const d of allowed) {
    if (d >= iso) return d;
  }
  return allowed[allowed.length - 1];
}

/**
 * Configure a visible ``<input type="date">`` for semester school-day picking.
 * @param {HTMLInputElement|null} input
 * @param {any} logContext
 * @param {{onInvalid?: (msg: string) => void}} [opts]
 */
export function bindSchoolDayPicker(input, logContext, opts = {}) {
  if (!input || !logContext) return;
  const allowed = allowedDates(logContext);
  const allowedSet = new Set(allowed);
  const min = logContext.first_day || allowed[0] || "";
  const max = logContext.last_day || allowed[allowed.length - 1] || "";
  if (min) input.min = min;
  if (max) input.max = max;
  input.required = true;

  const preferred = opts.value || suggestedLogDay(logContext);
  if (preferred) input.value = preferred;

  if (input.dataset.apCalendarBound === "1") return;
  input.dataset.apCalendarBound = "1";

  input.addEventListener("change", () => {
    const value = input.value;
    if (!value) return;
    if (allowedSet.has(value)) return;
    const snapped = snapToAllowed(value, allowed);
    input.value = snapped;
    opts.onInvalid?.(
      "Only school days in the current semester can be selected. Date adjusted."
    );
  });
}

/**
 * Set every overlay date picker to the same school day.
 * @param {any} logContext
 * @param {string} iso
 */
export function syncOverlayPickers(logContext, iso) {
  const value = iso || suggestedLogDay(logContext);
  for (const id of ["ap-valid-date", "ap-meeting-date", "ap-gamify-date"]) {
    const el = document.getElementById(id);
    if (el) el.value = value;
  }
}

/**
 * Read the picker value, snapping when needed.
 * @param {HTMLInputElement|null} input
 * @param {any} logContext
 * @returns {string}
 */
export function pickerValue(input, logContext) {
  if (!input?.value) return "";
  const allowed = allowedDates(logContext);
  if (!allowed.length) return input.value;
  return allowed.includes(input.value) ? input.value : snapToAllowed(input.value, allowed);
}

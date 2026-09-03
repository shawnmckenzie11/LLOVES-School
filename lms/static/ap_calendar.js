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
  const today = String(logContext?.today || logContext?.default_date || "").trim();
  if (today && allowed.includes(today)) return today;
  if (logContext?.default_date && allowed.includes(logContext.default_date)) {
    return logContext.default_date;
  }
  const next = allowed.find((d) => d >= today);
  return next || allowed[0] || today;
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
 * @param {{onInvalid?: (msg: string) => void, value?: string, forceValue?: boolean}} [opts]
 */
export function bindSchoolDayPicker(input, logContext, opts = {}) {
  if (!input || !logContext) return;
  const allowed = allowedDates(logContext);
  const allowedSet = new Set(allowed);
  const min = allowed[0] || logContext.first_day || "";
  const max = allowed[allowed.length - 1] || logContext.last_day || "";
  if (min) input.min = min;
  if (max) input.max = max;
  input.required = true;

  const alreadyBound = input.dataset.apCalendarBound === "1";
  const current = String(input.value || "").trim();
  // Keep a user-chosen date across rebinds (loadContext must not reset it).
  if (opts.forceValue && opts.value) {
    input.value = opts.value;
  } else if (!alreadyBound) {
    input.value = opts.value || defaultSchoolDay(logContext);
  } else if (current && allowedSet.has(current)) {
    /* keep current */
  } else if (opts.value && allowedSet.has(opts.value)) {
    input.value = opts.value;
  } else {
    input.value = defaultSchoolDay(logContext);
  }

  if (alreadyBound) return;
  input.dataset.apCalendarBound = "1";

  input.addEventListener("change", () => {
    const value = input.value;
    if (!value) return;
    const liveAllowed = new Set(allowedDates(logContext));
    if (liveAllowed.has(value)) return;
    const snapped = snapToAllowed(value, allowedDates(logContext));
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
  const value = iso || defaultSchoolDay(logContext);
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

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
 * Parse YYYY-MM-DD into UTC date parts.
 * @param {string} iso
 * @returns {{ y: number, m: number, d: number }}
 */
function parseIso(iso) {
  const [y, m, d] = String(iso || "").split("-").map(Number);
  return { y, m, d };
}

/**
 * Format a Date as YYYY-MM-DD (local calendar).
 * @param {Date} date
 * @returns {string}
 */
function formatIsoLocal(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

/**
 * Month label for grid heading.
 * @param {number} year
 * @param {number} monthIndex0
 * @returns {string}
 */
function monthLabel(year, monthIndex0) {
  return new Date(year, monthIndex0, 1).toLocaleString(undefined, {
    month: "long",
    year: "numeric",
  });
}

/**
 * Decide how to pick a meeting date before beginning a session.
 * @param {any} logContext
 * @param {{ todayLogged: boolean }} dayState
 * @param {{ flow?: 'take'|'run_live' }} [opts]
 * @returns {{ mode: 'auto'|'picker'|'confirm_override', iso: string }}
 */
export function resolveLogDate(logContext, dayState, opts = {}) {
  const flow = opts.flow || "take";
  const today = String(logContext?.today || "").trim();
  const allowed = new Set(allowedDates(logContext));
  const todayValid = allowed.has(today);
  const suggested = suggestedLogDay(logContext);

  if (todayValid && !dayState.todayLogged) {
    return { mode: "auto", iso: today };
  }
  if (flow === "run_live" && todayValid && dayState.todayLogged) {
    return { mode: "confirm_override", iso: today };
  }
  return { mode: "picker", iso: suggested };
}

/**
 * Render a semester-bounded month grid of selectable school days.
 * @param {HTMLElement|null} container
 * @param {any} logContext
 * @param {{ selected?: string, onSelect?: (iso: string) => void, minIso?: string, loggedDates?: Set<string> }} [opts]
 * @returns {string} Selected ISO after render
 */
export function renderSemesterDayGrid(container, logContext, opts = {}) {
  if (!container || !logContext) return opts.selected || "";
  const allowed = allowedDates(logContext);
  const allowedSet = new Set(allowed);
  const today = String(logContext.today || "").trim();
  const minIso = opts.minIso || today;
  const logged = opts.loggedDates || new Set();
  let selected = opts.selected || suggestedLogDay(logContext);
  if (!allowedSet.has(selected)) {
    selected = snapToAllowed(selected, allowed);
  }

  const first = logContext.first_day || allowed[0] || today;
  const last = logContext.last_day || allowed[allowed.length - 1] || today;
  const start = parseIso(first);
  const end = parseIso(last);
  const months = [];
  let cursor = new Date(start.y, start.m - 1, 1);
  const endMonth = new Date(end.y, end.m - 1, 1);
  while (cursor <= endMonth) {
    months.push({ year: cursor.getFullYear(), month: cursor.getMonth() });
    cursor = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1);
  }

  const weekdayLabels = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];
  const parts = months.map(({ year, month }) => {
    const firstOfMonth = new Date(year, month, 1);
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const lead = firstOfMonth.getDay();
    let cells = "";
    for (let i = 0; i < lead; i += 1) {
      cells += `<span class="ap-day-cell is-empty" aria-hidden="true"></span>`;
    }
    for (let day = 1; day <= daysInMonth; day += 1) {
      const iso = formatIsoLocal(new Date(year, month, day));
      const inSemester = iso >= first && iso <= last;
      const isValid = allowedSet.has(iso);
      const isFutureOrToday = iso >= minIso;
      const selectable = inSemester && isValid && isFutureOrToday;
      const classes = ["ap-day-cell"];
      if (!inSemester || !isValid) classes.push("is-disabled");
      else if (!isFutureOrToday) classes.push("is-disabled");
      else classes.push("is-valid");
      if (logged.has(iso)) classes.push("is-logged");
      if (iso === selected) classes.push("is-selected");
      if (iso === today) classes.push("is-today");
      const label = logged.has(iso) ? `${day} (logged)` : String(day);
      cells += `<button type="button" class="${classes.join(" ")}" data-day-iso="${iso}" ${
        selectable ? "" : "disabled"
      } aria-pressed="${iso === selected ? "true" : "false"}">${label}</button>`;
    }
    return `<section class="ap-day-month">
      <h3 class="ap-day-month-title">${monthLabel(year, month)}</h3>
      <div class="ap-day-weekdays">${weekdayLabels.map((w) => `<span>${w}</span>`).join("")}</div>
      <div class="ap-day-month-grid">${cells}</div>
    </section>`;
  });

  container.innerHTML = `<div class="ap-day-grid">${parts.join("")}</div>`;
  container.dataset.selectedIso = selected;

  container.onclick = (event) => {
    const btn = event.target.closest("[data-day-iso]");
    if (!(btn instanceof HTMLButtonElement) || btn.disabled) return;
    const iso = btn.getAttribute("data-day-iso") || "";
    if (!iso || !allowedSet.has(iso)) return;
    selected = iso;
    container.querySelectorAll(".ap-day-cell.is-selected").forEach((el) => {
      el.classList.remove("is-selected");
      el.setAttribute("aria-pressed", "false");
    });
    btn.classList.add("is-selected");
    btn.setAttribute("aria-pressed", "true");
    container.dataset.selectedIso = iso;
    opts.onSelect?.(iso);
  };

  return selected;
}

/**
 * Read the selected ISO from a rendered day grid container.
 * @param {HTMLElement|null} container
 * @returns {string}
 */
export function gridSelectedIso(container) {
  if (!container) return "";
  return String(container.dataset.selectedIso || "").trim();
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
  if (opts.forceValue && opts.value) {
    input.value = opts.value;
  } else if (!alreadyBound) {
    input.value = opts.value || suggestedLogDay(logContext);
  } else if (current && allowedSet.has(current)) {
    /* keep current */
  } else if (opts.value && allowedSet.has(opts.value)) {
    input.value = opts.value;
  } else {
    input.value = suggestedLogDay(logContext);
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
  const value = iso || suggestedLogDay(logContext);
  for (const id of ["ap-valid-date", "ap-meeting-date"]) {
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

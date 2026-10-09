import { api, hideError, showError } from "/static/common.js";

const POPULATE_STEPS_FULL = ["roster", "days", "time"];
const POPULATE_STEPS_ROSTER = ["roster"];
const EDIT_STEPS = ["roster"];
const TITLES = {
  offering: "Assigned course",
  roster: "Codenames",
  days: "Days",
  time: "Time",
};

let step = 0;
let steps = POPULATE_STEPS_FULL;
let editingClassId = 0;
let lockedDays = "";
let lockedTime = "";
const names = [];
const nameIds = [];
/** @type {Array<{first_name: string, last_name: string, display_name: string}|null>} */
const nameFields = [];

const $ = (id) => document.getElementById(id);

/**
 * Drop the in-memory Codename list and its student ids.
 */
function clearNames() {
  names.length = 0;
  nameIds.length = 0;
  nameFields.length = 0;
}

/**
 * Escape text for an HTML attribute.
 * @param {string} value
 */
function escapeAttr(value) {
  return escapeText(value).replace(/"/g, "&quot;");
}

/**
 * Paint one existing student as an in-place name editor.
 * @param {number} index
 */
function rosterEditorRow(index) {
  const meta = nameFields[index];
  const studentId = nameIds[index];
  const name = names[index];
  const first = meta?.first_name || name;
  const last = meta?.last_name || "";
  const display = meta?.display_name || name;
  const secondKind = meta?.second_kind || (last ? "last_name" : "display_name");
  const secondLabel = secondKind === "last_name" ? "Last name" : "Display name";
  const secondValue = secondKind === "last_name" ? last : display;
  const secondName = secondKind;
  return `<li class="roster-name-row" data-class-id="${editingClassId}" data-student-id="${studentId}" data-first-name="${escapeAttr(first)}" data-last-name="${escapeAttr(last)}" data-display-name="${escapeAttr(display)}" data-second-kind="${secondKind}">
      <div class="roster-name-view">
        <button type="button" class="roster-name-label">${escapeText(name)}</button>
        <button type="button" class="roster-name-pencil" aria-label="Edit name for ${escapeAttr(name)}">
          <svg width="1em" height="1em" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg>
        </button>
        <button type="button" class="secondary" data-i="${index}">Remove</button>
      </div>
      <form class="roster-name-form" hidden>
        <label>First name
          <input name="first_name" maxlength="80" autocomplete="off" required value="${escapeAttr(first)}">
        </label>
        <label>${secondLabel}
          <input name="${secondName}" maxlength="80" autocomplete="off" required value="${escapeAttr(secondValue)}">
        </label>
        <button type="submit" class="roster-name-save">Save</button>
        <p class="roster-name-error" role="alert" hidden></p>
      </form>
    </li>`;
}

/**
 * Paint the repeatable Codename list and live count.
 */
function renderRoster() {
  const list = $("codename-list");
  if (!list) return;
  list.innerHTML = names
    .map((name, i) =>
      nameIds[i]
        ? rosterEditorRow(i)
        : `<div>${escapeText(name)} <button type="button" class="secondary" data-i="${i}">Remove</button></div>`
    )
    .join("");
  const count = $("roster-count");
  if (count) count.textContent = String(names.length);
}

/**
 * Escape text for HTML insertion.
 * @param {string} value
 */
function escapeText(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/**
 * Add one or more Codenames from a field or pasted lines.
 * @param {string} raw
 * @returns {boolean} False when validation failed.
 */
function addNames(raw) {
  const lines = String(raw || "")
    .split(/\n/)
    .map((s) => s.trim())
    .filter(Boolean);
  for (const line of lines) {
    if (line.includes(",")) {
      showError("#error", "Codenames cannot contain commas.");
      return false;
    }
    if (line.length < 2 || line.length > 32) {
      showError("#error", "Codenames must be 2–32 characters.");
      return false;
    }
    if (names.some((n) => n.toLowerCase() === line.toLowerCase())) {
      showError("#error", `Duplicate Codename: ${line}`);
      return false;
    }
    names.push(line);
    nameIds.push(0);
    nameFields.push(null);
  }
  hideError("#error");
  renderRoster();
  return true;
}

/**
 * Apply pending single-line and multi-line fields (only on Add or final save).
 * @returns {boolean}
 */
function harvestPendingNames() {
  const paste = String($("codename-paste")?.value || "");
  const single = String($("codename-input")?.value || "");
  if (paste.trim()) {
    if (!addNames(paste)) return false;
    if ($("codename-paste")) $("codename-paste").value = "";
  }
  if (single.trim()) {
    if (!addNames(single)) return false;
    if ($("codename-input")) $("codename-input").value = "";
  }
  return true;
}

/**
 * Show the assigned-course dashboard.
 */
function showPicker() {
  $("course-dash")?.classList.remove("hidden");
  $("wizard")?.classList.add("hidden");
  editingClassId = 0;
  lockedDays = "";
  lockedTime = "";
  steps = POPULATE_STEPS_FULL;
  hideError("#error");
}

/**
 * MCK-183: pack-less non-math courses say "first names", not Codenames.
 * Elements carry their plain wording in ``data-plain`` /
 * ``data-plain-placeholder``; the math wording is kept on first swap.
 * @param {boolean} plain
 */
export function applyNameWording(plain, root = document) {
  for (const el of root.querySelectorAll("[data-plain]")) {
    if (!el.hasAttribute("data-math")) el.setAttribute("data-math", el.textContent);
    el.textContent = plain ? el.getAttribute("data-plain") : el.getAttribute("data-math");
  }
  for (const el of root.querySelectorAll("[data-plain-placeholder]")) {
    if (!el.hasAttribute("data-math-placeholder")) {
      el.setAttribute("data-math-placeholder", el.getAttribute("placeholder") || "");
    }
    el.setAttribute(
      "placeholder",
      plain ? el.getAttribute("data-plain-placeholder") : el.getAttribute("data-math-placeholder")
    );
  }
  TITLES.roster = plain ? "First names" : "Codenames";
}

/**
 * Open the wizard for an offering (populate or edit existing roster).
 * @param {HTMLElement} btn
 */
async function startWizard(btn) {
  const offeringId = btn.getAttribute("data-offering-id") || "";
  const classId = Number(btn.getAttribute("data-class-id") || 0);
  lockedDays = (btn.getAttribute("data-live-days") || "").trim();
  lockedTime = (btn.getAttribute("data-live-time") || "").trim();
  applyNameWording(btn.getAttribute("data-simple") === "1");
  const select = $("offering");
  if (select) select.value = String(offeringId);
  const label = select?.selectedOptions?.[0]?.textContent || "";
  const chosen = $("offering-chosen");
  if (chosen) chosen.textContent = label;
  $("course-dash")?.classList.add("hidden");
  $("wizard")?.classList.remove("hidden");
  step = 0;
  clearNames();
  editingClassId = classId;
  if ($("codename-paste")) $("codename-paste").value = "";
  if ($("codename-input")) $("codename-input").value = "";
  if (classId) {
    steps = EDIT_STEPS;
  } else if (lockedDays && lockedTime) {
    steps = POPULATE_STEPS_ROSTER;
    if ($("days")) $("days").value = lockedDays;
    if ($("time")) $("time").value = lockedTime;
  } else {
    steps = POPULATE_STEPS_FULL;
  }
  if (classId) {
    try {
      const data = await api(`/api/classes/${classId}/dashboard?sort=az`);
      for (const student of data.students || []) {
        const code = String(student.codename || "").trim();
        const first = String(student.first_name || "").trim();
        const last = String(student.last_display || "").trim();
        const name = code || `${first} ${last}`.trim() || first;
        if (!name) continue;
        names.push(name);
        nameIds.push(Number(student.id) || 0);
        nameFields.push({
          first_name: first || name,
          last_name: last,
          display_name: code || name,
          second_kind: last && !code ? "last_name" : "display_name",
        });
      }
    } catch (err) {
      showError("#error", err);
      showPicker();
      return;
    }
  }
  renderRoster();
  renderStep();
}

/**
 * Show one wizard step.
 */
function renderStep() {
  const key = steps[step];
  $("wiz-progress").textContent = `Step ${step + 1} of ${steps.length}`;
  if (editingClassId) {
    $("wiz-title").textContent = "Edit Roster";
  } else {
    $("wiz-title").textContent = TITLES[key] || "Populate Class";
  }
  for (const name of ["offering", "roster", "days", "time"]) {
    const el = $(`step-${name}`);
    if (!el) continue;
    el.classList.toggle("hidden", name !== key);
  }
  $("wiz-back").textContent = step === 0 ? "Cancel" : "Back";
  if (step !== steps.length - 1) {
    $("wiz-next").textContent = "Next";
  } else {
    $("wiz-next").textContent = editingClassId ? "Save roster" : "Populate class";
  }
}

/**
 * Validate the current step.
 * @returns {boolean}
 */
function validateStep() {
  hideError("#error");
  const key = steps[step];
  if (key === "offering" && !$("offering")?.value) {
    showError("#error", "Choose an assigned course.");
    return false;
  }
  if (key === "roster") {
    if (!harvestPendingNames()) return false;
    if (names.length < 1) {
      showError("#error", "Add at least one Codename.");
      return false;
    }
  }
  return true;
}

/**
 * Create a new class (Populate Class), then return to the teacher dashboard.
 */
async function submitClass() {
  const offering = $("offering");
  const payload = {
    offering_id: Number(offering?.value),
    days: lockedDays || $("days")?.value,
    time: lockedTime || $("time")?.value,
    codenames: names,
  };
  await api("/api/staff/classes", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  location.href = "/staff";
}

/**
 * Update an existing class roster, then return to the teacher dashboard.
 */
async function submitRosterEdit() {
  await api(`/api/staff/classes/${editingClassId}/roster`, {
    method: "PUT",
    body: JSON.stringify({ codenames: names }),
  });
  location.href = "/staff";
}

document.querySelectorAll(".btn-populate").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.disabled || btn.getAttribute("aria-disabled") === "true") {
      showError(
        "#error",
        new Error("End the live class before editing the roster.")
      );
      return;
    }
    startWizard(btn).catch((err) => showError("#error", err));
  });
});

$("wiz-back")?.addEventListener("click", () => {
  if (step === 0) {
    showPicker();
    return;
  }
  step -= 1;
  renderStep();
});

$("wiz-next")?.addEventListener("click", async () => {
  if (!validateStep()) return;
  if (step === steps.length - 1) {
    try {
      if (editingClassId) {
        await submitRosterEdit();
      } else {
        await submitClass();
      }
    } catch (err) {
      showError("#error", err);
    }
    return;
  }
  step += 1;
  renderStep();
});

$("add-codename")?.addEventListener("click", () => {
  addNames($("codename-input").value);
  $("codename-input").value = "";
});

$("codename-input")?.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    addNames($("codename-input").value);
    $("codename-input").value = "";
  }
});

$("codename-list")?.addEventListener("click", (event) => {
  const btn = event.target instanceof Element ? event.target.closest("[data-i]") : null;
  if (!btn) return;
  const index = Number(btn.dataset.i);
  names.splice(index, 1);
  nameIds.splice(index, 1);
  nameFields.splice(index, 1);
  renderRoster();
});

document.addEventListener("roster-name-saved", (event) => {
  const detail = event.detail || {};
  if (Number(detail.classId) !== editingClassId) return;
  const index = nameIds.indexOf(Number(detail.studentId));
  if (index < 0) return;
  names[index] = String(detail.name || names[index]);
  const previous = nameFields[index] || {};
  nameFields[index] = {
    first_name: String(detail.first_name || ""),
    last_name: String(detail.last_name || ""),
    display_name: String(detail.display_name || detail.name || ""),
    second_kind: previous.second_kind || "display_name",
  };
});


/**
 * Grey non-roster actions and show a bottom-of-card pack progress bar.
 * @param {HTMLElement} card
 * @param {{busy?: boolean, line?: string, detail?: string, error?: string|null}} status
 */
function applyPackBusy(card, status) {
  const busy = Boolean(status.busy);
  const bar = card.querySelector(".course-card-pack-progress");
  const meter = bar?.querySelector("progress");
  const detail = bar?.querySelector(".pack-progress-detail");
  if (bar) {
    bar.hidden = !busy && !status.error;
    if (detail) {
      detail.textContent = status.line || status.detail || status.error || "";
    }
    if (meter) {
      if (busy) meter.removeAttribute("value");
      else meter.value = 100;
    }
  }
  card.querySelectorAll(".course-action").forEach((el) => {
    if (el.classList.contains("course-action-end")) return;
    // Roster setup does not need a module pack.
    if (el.classList.contains("btn-populate")) return;
    el.classList.toggle("is-disabled", busy);
    if (busy) {
      el.setAttribute("aria-disabled", "true");
      el.setAttribute("tabindex", "-1");
    } else {
      el.removeAttribute("aria-disabled");
      el.removeAttribute("tabindex");
    }
  });
  card.classList.toggle("pack-busy", busy);
}

/**
 * Poll each course card's pack-status URL while a library is loading.
 */
function initPackProgress() {
  document.addEventListener(
    "click",
    (event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (target?.closest(".course-action.is-disabled")) {
        event.preventDefault();
        event.stopPropagation();
      }
    },
    true
  );
  document.querySelectorAll("article[data-pack-status-url]").forEach((card) => {
    const url = card.getAttribute("data-pack-status-url");
    if (!url) return;
    let keepPolling = card.getAttribute("data-pack-busy") === "1";
    const tick = async () => {
      try {
        const rv = await fetch(url, { headers: { Accept: "application/json" } });
        if (rv.ok) {
          const status = await rv.json();
          applyPackBusy(card, status);
          keepPolling = Boolean(status.busy);
          if (!keepPolling) return;
        }
      } catch (_) {
        /* keep polling through a blip while still busy */
      }
      if (keepPolling) window.setTimeout(tick, 700);
    };
    tick();
  });
}

/**
 * Open / close the End Live Class save-options dialog.
 */
function wireEndLiveDialog() {
  const dialog = document.getElementById("end-live-dialog");
  if (!(dialog instanceof HTMLDialogElement)) return;
  document.querySelectorAll("[data-open-end-live]").forEach((btn) => {
    btn.addEventListener("click", () => dialog.showModal());
  });
  dialog.querySelector("[data-close-end-live]")?.addEventListener("click", () => {
    dialog.close();
  });
}

initPackProgress();
wireEndLiveDialog();

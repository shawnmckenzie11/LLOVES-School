/**
 * In-place roster name editor.
 *
 * A pencil button or the name itself opens first name plus last name or
 * display name. Enter or Save writes the staff rename route. Esc cancels.
 */
import { api, escapeHtml } from "/static/common.js";

const PENCIL_SVG =
  '<svg width="1em" height="1em" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg>';

/**
 * Markup for one in-place name editor.
 *
 * The closed state is the name plus a pencil. The form is the same first
 * name and last name or display name used on the staff home roster.
 *
 * @param {{
 *   classId: number|string,
 *   studentId: number|string,
 *   name: string,
 *   firstName?: string,
 *   lastName?: string,
 *   displayName?: string,
 *   secondKind?: string,
 *   tag?: string,
 * }} fields
 * @returns {string}
 */
export function rosterNameEditorHtml(fields) {
  const name = String(fields.name || "").trim();
  const first = String(fields.firstName ?? name);
  const last = String(fields.lastName || "");
  const display = String(fields.displayName || name);
  const secondKind = fields.secondKind === "last_name" ? "last_name" : "display_name";
  const secondLabel = secondKind === "last_name" ? "Last name" : "Display name";
  const secondValue = secondKind === "last_name" ? last : display;
  const tag = fields.tag === "li" ? "li" : "div";
  return `<${tag} class="roster-name-row" data-class-id="${escapeHtml(fields.classId)}" data-student-id="${escapeHtml(fields.studentId)}" data-first-name="${escapeHtml(first)}" data-last-name="${escapeHtml(last)}" data-display-name="${escapeHtml(display)}" data-second-kind="${secondKind}">
      <div class="roster-name-view">
        <button type="button" class="roster-name-label">${escapeHtml(name)}</button>
        <button type="button" class="roster-name-pencil" aria-label="Edit name for ${escapeHtml(name)}">${PENCIL_SVG}</button>
      </div>
      <form class="roster-name-form" hidden>
        <label>First name
          <input name="first_name" maxlength="80" autocomplete="off" required value="${escapeHtml(first)}">
        </label>
        <label>${secondLabel}
          <input name="${secondKind}" maxlength="80" autocomplete="off" required value="${escapeHtml(secondValue)}">
        </label>
        <button type="submit" class="roster-name-save">Save</button>
        <p class="roster-name-error" role="alert" hidden></p>
      </form>
    </${tag}>`;
}

/**
 * Open the editor on one roster row and focus the first field.
 * @param {HTMLElement} row
 */
function openEditor(row) {
  const form = row.querySelector(".roster-name-form");
  const view = row.querySelector(".roster-name-view");
  if (!(form instanceof HTMLFormElement) || !(view instanceof HTMLElement)) return;
  view.hidden = true;
  form.hidden = false;
  const first = form.querySelector("input");
  if (first instanceof HTMLElement) first.focus();
}

/**
 * Close the editor and restore the saved field values.
 * @param {HTMLElement} row
 */
function closeEditor(row) {
  const form = row.querySelector(".roster-name-form");
  const view = row.querySelector(".roster-name-view");
  if (!(form instanceof HTMLFormElement) || !(view instanceof HTMLElement)) return;
  const first = form.querySelector('[name="first_name"]');
  const second = form.querySelector('[name="last_name"], [name="display_name"]');
  if (first instanceof HTMLInputElement) {
    first.value = row.dataset.firstName || "";
  }
  if (second instanceof HTMLInputElement) {
    const kind = row.dataset.secondKind || "display_name";
    second.value =
      kind === "last_name"
        ? row.dataset.lastName || ""
        : row.dataset.displayName || "";
  }
  const error = row.querySelector(".roster-name-error");
  if (error instanceof HTMLElement) {
    error.hidden = true;
    error.textContent = "";
  }
  form.hidden = true;
  view.hidden = false;
}

/**
 * Show a validation or server message on the open row.
 * @param {HTMLElement} row
 * @param {string} message
 */
function showRowError(row, message) {
  const error = row.querySelector(".roster-name-error");
  if (!(error instanceof HTMLElement)) return;
  error.hidden = false;
  error.textContent = message;
}

/**
 * Paint the saved name back onto the row.
 * @param {HTMLElement} row
 * @param {{name?: string, first_name?: string, last_name?: string, display_name?: string}} saved
 */
function applySavedName(row, saved) {
  const name = String(saved.name || saved.display_name || "").trim();
  const label = row.querySelector(".roster-name-label");
  const pencil = row.querySelector(".roster-name-pencil");
  if (label) label.textContent = name;
  if (pencil instanceof HTMLElement) {
    pencil.setAttribute("aria-label", `Edit name for ${name}`);
  }
  row.dataset.firstName = String(saved.first_name || "");
  row.dataset.lastName = String(saved.last_name || "");
  row.dataset.displayName = String(saved.display_name || name);
  const form = row.querySelector(".roster-name-form");
  if (form instanceof HTMLFormElement) {
    const first = form.querySelector('[name="first_name"]');
    const second = form.querySelector('[name="last_name"], [name="display_name"]');
    if (first instanceof HTMLInputElement) first.value = row.dataset.firstName;
    if (second instanceof HTMLInputElement) {
      second.value =
        second.name === "last_name"
          ? row.dataset.lastName
          : row.dataset.displayName;
    }
  }
  document.dispatchEvent(
    new CustomEvent("roster-name-saved", {
      detail: {
        classId: Number(row.dataset.classId || 0),
        studentId: Number(row.dataset.studentId || 0),
        name,
        first_name: row.dataset.firstName,
        last_name: row.dataset.lastName,
        display_name: row.dataset.displayName,
      },
    })
  );
}

/**
 * POST the open row and close it when the server accepts the name.
 * @param {HTMLElement} row
 * @returns {Promise<void>}
 */
async function saveEditor(row) {
  const form = row.querySelector(".roster-name-form");
  if (!(form instanceof HTMLFormElement)) return;
  const classId = Number(row.dataset.classId || 0);
  const studentId = Number(row.dataset.studentId || 0);
  const body = {};
  for (const input of form.querySelectorAll("input")) {
    if (input instanceof HTMLInputElement && input.name) {
      body[input.name] = input.value;
    }
  }
  const saved = await api(
    `/api/classes/${classId}/students/${studentId}/name`,
    { method: "POST", body: JSON.stringify(body) }
  );
  applySavedName(row, saved);
  closeEditor(row);
}

if (typeof document !== "undefined") {
  wireRosterNameEditor();
}

/**
 * Bind pencil, Save, and Escape for every roster name editor on the page.
 */
function wireRosterNameEditor() {
  document.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    const opener = target.closest(".roster-name-label, .roster-name-pencil");
    if (!opener) return;
    const row = opener.closest(".roster-name-row");
    if (!(row instanceof HTMLElement)) return;
    event.preventDefault();
    openEditor(row);
  });

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.classList.contains("roster-name-form")) {
      return;
    }
    event.preventDefault();
    const row = form.closest(".roster-name-row");
    if (!(row instanceof HTMLElement)) return;
    saveEditor(row).catch((err) => {
      const message = err instanceof Error ? err.message : "Could not save that name.";
      showRowError(row, message);
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const target = event.target instanceof Element ? event.target : null;
    const row = target?.closest(".roster-name-row");
    if (!(row instanceof HTMLElement)) return;
    const form = row.querySelector(".roster-name-form");
    if (!(form instanceof HTMLElement) || form.hidden) return;
    event.preventDefault();
    closeEditor(row);
    const pencil = row.querySelector(".roster-name-pencil");
    if (pencil instanceof HTMLElement) pencil.focus();
  });
}

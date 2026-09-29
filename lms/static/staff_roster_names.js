/**
 * In-place roster name editor.
 *
 * A pencil button or the name itself opens first name plus last name or
 * display name. Enter or Save writes the staff rename route. Esc cancels.
 */
import { api } from "/static/common.js";

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

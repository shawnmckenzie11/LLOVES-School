import { api, hideError, showError } from "/static/common.js";

/**
 * Wire the staff-home Shoutout picker (featured Codename + optional blurb).
 */
function bindCelebrateForm() {
  const form = document.getElementById("celebrate-form");
  if (!form) return;
  const select = document.getElementById("celebrate-student");
  const blurb = document.getElementById("celebrate-blurb");
  const clearBtn = document.getElementById("celebrate-clear");

  /**
   * POST the featured award, then reload so the preview line updates.
   * @param {object} payload
   */
  function save(payload) {
    hideError("#error");
    api("/api/staff/celebration-award", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(() => {
        window.location.reload();
      })
      .catch((err) => {
        showError("#error", err.message || "Could not update Shoutout.");
      });
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const raw = (select && select.value) || "";
    const parts = raw.split(":");
    if (parts.length !== 2) {
      showError("#error", "Pick a Codename.");
      return;
    }
    save({
      class_id: Number(parts[0]),
      student_id: Number(parts[1]),
      blurb: blurb ? blurb.value : "",
    });
  });

  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      save({ clear: true });
    });
  }
}

/** MCK-133 Start fresh copy (Wonder v1). */
const FRESH_COPY = {
  title: "Start Celebrations fresh?",
  body: "Awards start counting again from today. Attendance and participation records stay in each course.",
  toastAll: "Celebrations started fresh for all your classes. Past records are kept.",
  toastOne: "Celebrations started fresh for {class}. Past records are kept.",
  error: "Couldn't start fresh. Try again.",
};
const FRESH_TOAST_KEY = "mck133-start-fresh-toast";

/**
 * Ask in the page's dialog (Cancel / Start fresh); resolves true on confirm.
 * The dialog names the scope and the period each class closes
 * ("MCR3U · Sep 8 – Oct 1, 2026"); Cancel has focus. Falls back to the
 * browser confirm where <dialog> is missing.
 * @param {string} value "all" or a class id
 * @returns {Promise<boolean>}
 */
function confirmStartFresh(value) {
  const dialog = document.getElementById("award-fresh-dialog");
  const rows = dialog ? Array.from(dialog.querySelectorAll("#award-fresh-summary li")) : [];
  const shown = rows.filter((row) => value === "all" || row.dataset.classId === value);
  rows.forEach((row) => {
    row.hidden = !shown.includes(row);
  });
  if (!(dialog instanceof HTMLDialogElement) || typeof dialog.showModal !== "function") {
    const scopeLines = shown.map((row) => (row.textContent || "").trim()).join("\n");
    return Promise.resolve(window.confirm(`${FRESH_COPY.title}\n\n${scopeLines}\n\n${FRESH_COPY.body}`));
  }
  return new Promise((resolve) => {
    dialog.returnValue = "";
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), { once: true });
    dialog.showModal();
    const cancel = document.getElementById("award-fresh-cancel");
    if (cancel) cancel.focus();
  });
}

/** Show the success toast saved before the reload, once. */
function showStartFreshToast() {
  const toast = document.getElementById("award-fresh-toast");
  let text = "";
  try {
    text = window.sessionStorage.getItem(FRESH_TOAST_KEY) || "";
    window.sessionStorage.removeItem(FRESH_TOAST_KEY);
  } catch {
    text = "";
  }
  if (!toast || !text) return;
  toast.textContent = text;
  toast.hidden = false;
}

/**
 * MCK-133: "Start fresh" restarts the Celebrations award tally for all of
 * this teacher's classes or one class.
 */
function bindStartFresh() {
  const button = document.getElementById("award-fresh-go");
  const scope = document.getElementById("award-fresh-scope");
  if (!button || !scope) return;
  showStartFreshToast();
  let busy = false;
  button.addEventListener("click", async () => {
    // LOW-5: one request at a time; disabled before the confirm so a double
    // click cannot open two dialogs.
    if (busy) return;
    busy = true;
    button.disabled = true;
    const value = scope.value || "all";
    const option = scope.options[scope.selectedIndex];
    const name = (option && option.dataset.className) || "";
    const ok = await confirmStartFresh(value);
    if (!ok) {
      busy = false;
      button.disabled = false;
      return;
    }
    const payload = value === "all" ? { scope: "all" } : { scope: "class", class_id: Number(value) };
    hideError("#error");
    try {
      await api("/api/staff/celebrations/start-fresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const toast = value === "all" ? FRESH_COPY.toastAll : FRESH_COPY.toastOne.replace("{class}", name);
      try {
        window.sessionStorage.setItem(FRESH_TOAST_KEY, toast);
      } catch {
        // No storage: reload without the toast.
      }
      window.location.reload();
    } catch (err) {
      busy = false;
      button.disabled = false;
      // Server reasons (a class is running, a shared course) are shown as
      // sent; anything else gets Wonder's generic error.
      const fromServer = err instanceof Error && !(err instanceof TypeError) && !/^HTTP \d+$/.test(err.message);
      const msg = fromServer && err.message ? err.message : FRESH_COPY.error;
      showError("#error", msg);
    }
  });
}

bindCelebrateForm();
bindStartFresh();

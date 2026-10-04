/**
 * MCK-183 teacher onboarding (screen 2, Your class).
 *
 * Three dropdowns per class, all starting at "Choose…". "+ Add another
 * class" adds one more set. Next posts every row; any empty field shows
 * Wonder's one error and outlines the empty dropdowns.
 */
(function initYourClass() {
  "use strict";
  const form = document.getElementById("ob-class-form");
  if (!form) return;
  const rowsEl = document.getElementById("ob-class-rows");
  const addBtn = document.getElementById("ob-add-class");
  const errorEl = document.getElementById("ob-class-error");
  const nextBtn = document.getElementById("ob-class-next");
  const MAX_ROWS = 6;
  const DEFAULT_ERROR = errorEl ? errorEl.textContent : "";

  function rows() {
    return Array.from(rowsEl.querySelectorAll("[data-ob-class-row]"));
  }

  function renumber() {
    rows().forEach((row, index) => {
      const n = index + 1;
      const legend = row.querySelector("legend");
      if (legend) legend.textContent = `Class ${n}`;
      row.querySelectorAll("select").forEach((select) => {
        const field = select.getAttribute("data-ob-field") || "";
        const prefix = { ontario_code: "ob-code", live_days: "ob-days", live_time: "ob-time" }[field];
        if (!prefix) return;
        const label = row.querySelector(`label[for="${select.id}"]`);
        select.id = `${prefix}-${n}`;
        if (label) label.setAttribute("for", select.id);
      });
    });
    if (addBtn) addBtn.hidden = rows().length >= MAX_ROWS;
  }

  function showError(text) {
    if (!errorEl) return;
    errorEl.textContent = text || DEFAULT_ERROR;
    errorEl.hidden = false;
  }

  if (addBtn) {
    addBtn.addEventListener("click", () => {
      const all = rows();
      if (!all.length || all.length >= MAX_ROWS) return;
      const copy = all[0].cloneNode(true);
      copy.querySelectorAll("select").forEach((select) => {
        select.value = "";
        select.classList.remove("is-missing");
      });
      rowsEl.appendChild(copy);
      renumber();
      const first = copy.querySelector("select");
      if (first) first.focus();
    });
  }

  form.addEventListener("change", (event) => {
    const target = event.target;
    if (target instanceof HTMLSelectElement && target.value) target.classList.remove("is-missing");
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    let missing = false;
    const classes = rows().map((row) => {
      const out = {};
      row.querySelectorAll("select[data-ob-field]").forEach((select) => {
        out[select.getAttribute("data-ob-field")] = select.value;
        const empty = !select.value;
        select.classList.toggle("is-missing", empty);
        if (empty) missing = true;
      });
      return out;
    });
    if (missing) {
      showError();
      return;
    }
    if (errorEl) errorEl.hidden = true;
    nextBtn.disabled = true;
    try {
      const res = await fetch(form.getAttribute("data-endpoint") || "", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ classes }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        showError(data.error);
        return;
      }
      window.location.assign(data.next || "/staff");
    } catch (_err) {
      showError("That didn't save. Check your connection and press Next again.");
    } finally {
      nextBtn.disabled = false;
    }
  });

  renumber();
})();

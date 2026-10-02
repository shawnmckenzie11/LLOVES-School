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

/**
 * MCK-133: "Start fresh" restarts the Celebrations award tally for all of
 * this teacher's classes or one class. Placeholder copy (Wonder pending).
 */
function bindStartFresh() {
  const button = document.getElementById("award-fresh-go");
  const scope = document.getElementById("award-fresh-scope");
  if (!button || !scope) return;
  button.addEventListener("click", () => {
    const value = scope.value || "all";
    const label = value === "all" ? "all your classes" : scope.options[scope.selectedIndex].text;
    // TODO(Wonder): confirm copy.
    const ok = window.confirm(
      `Start the award tally fresh for ${label}? Past attendance and points stay saved.`,
    );
    if (!ok) return;
    const payload = value === "all" ? { scope: "all" } : { scope: "class", class_id: Number(value) };
    hideError("#error");
    button.disabled = true;
    api("/api/staff/celebrations/start-fresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(() => {
        window.location.reload();
      })
      .catch((err) => {
        button.disabled = false;
        showError("#error", err.message || "Could not start fresh.");
      });
  });
}

bindCelebrateForm();
bindStartFresh();

import { api, hideError, showError } from "/static/common.js";

/**
 * Wire the staff-home Awards picker (featured Codename + optional blurb).
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
        showError("#error", err.message || "Could not update Awards.");
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

bindCelebrateForm();

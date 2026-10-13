/**
 * First-login catch-up PIN pop-up for Live Class (MCK-207).
 */

const TOAST_TEXT = "PIN saved. You're set for catch-up.";
let dialogEl = null;
let openForForced = false;
let lastCatchupPinPayload = null;

/**
 * Show or hide the PIN dialog from a /api/student/state payload.
 * @param {Record<string, unknown> | null | undefined} payload
 */
export function paintCatchupPinPrompt(payload) {
  lastCatchupPinPayload = payload;
  const prompt = lastCatchupPinPayload && lastCatchupPinPayload.catchup_pin_prompt;
  const active = (payload && payload.active_questions) || [];
  if (active.length) {
    return;
  }
  if (!prompt || !prompt.show) {
    if (dialogEl && !openForForced) {
      dialogEl.close();
      dialogEl.remove();
      dialogEl = null;
    }
    return;
  }
  const forced = Boolean(prompt.forced);
  if (dialogEl && openForForced && !forced) {
    return;
  }
  if (dialogEl) {
    return;
  }
  openForForced = forced;
  dialogEl = document.createElement("dialog");
  dialogEl.className = "catchup-pin-dialog student-pin-dialog";
  dialogEl.setAttribute("aria-labelledby", "live-catchup-pin-title");
  dialogEl.innerHTML = `
    <form method="dialog" class="catchup-pin-form" id="live-catchup-pin-form">
      <h2 id="live-catchup-pin-title">Set your catch-up PIN</h2>
      <p class="hint">Choose a 4-digit PIN you'll use to open catch-up links. It's not your join code.</p>
      ${forced ? "<p class=\"hint\">Set a PIN now to open catch-up links.</p>" : ""}
      <p class="error" id="live-catchup-pin-error" hidden></p>
      <label class="field" for="live-catchup-pin-new">New PIN</label>
      <input class="full" id="live-catchup-pin-new" type="password" inputmode="numeric" pattern="[0-9]{4}"
             minlength="4" maxlength="4" autocomplete="off" required>
      <label class="field" for="live-catchup-pin-confirm">Confirm PIN</label>
      <input class="full" id="live-catchup-pin-confirm" type="password" inputmode="numeric" pattern="[0-9]{4}"
             minlength="4" maxlength="4" autocomplete="off" required>
      <div class="catchup-pin-actions">
        <button type="button" id="live-catchup-pin-save">Save my PIN</button>
        ${forced ? "" : '<button type="button" class="secondary" id="live-catchup-pin-later">Later</button>'}
      </div>
    </form>`;
  document.body.appendChild(dialogEl);
  dialogEl.showModal();

  const err = dialogEl.querySelector("#live-catchup-pin-error");
  const saveBtn = dialogEl.querySelector("#live-catchup-pin-save");
  const laterBtn = dialogEl.querySelector("#live-catchup-pin-later");

  const closeDialog = () => {
    dialogEl.close();
    dialogEl.remove();
    dialogEl = null;
    openForForced = false;
  };

  laterBtn?.addEventListener("click", async () => {
    await fetch("/api/student/catchup-pin/later", { method: "POST" });
    closeDialog();
  });

  saveBtn?.addEventListener("click", async () => {
    const pin = String(dialogEl.querySelector("#live-catchup-pin-new")?.value || "");
    const confirm = String(dialogEl.querySelector("#live-catchup-pin-confirm")?.value || "");
    if (pin !== confirm) {
      if (err) {
        err.textContent = "PINs must match.";
        err.hidden = false;
      }
      return;
    }
    const res = await fetch("/api/student/catchup-pin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pin, confirm }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (data.needs_catchup_portal) {
        const url = String((lastCatchupPinPayload && lastCatchupPinPayload.catchup_pin_prompt && lastCatchupPinPayload.catchup_pin_prompt.catchup_url) || "");
        if (url) {
          window.location.assign(url);
          return;
        }
      }
      if (err) {
        err.textContent = data.error || "Could not save PIN.";
        err.hidden = false;
      }
      return;
    }
    closeDialog();
    const toast = data.toast || TOAST_TEXT;
    window.dispatchEvent(new CustomEvent("catchup-pin-saved", { detail: { toast } }));
  });
}

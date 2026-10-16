/**
 * MCK-269: click/keyboard info popovers (no hover).
 */

/** @type {HTMLButtonElement|null} */
let staffInfoPopoverFocusTrigger = null;

/**
 * Close every open staff info popover.
 * @param {HTMLElement|null} except
 * @param {{ focusTrigger?: boolean }} [options]
 * @returns {void}
 */
function closeStaffInfoPopovers(except = null, options = {}) {
  const focusTrigger = Boolean(options.focusTrigger);
  const triggerToFocus = focusTrigger ? staffInfoPopoverFocusTrigger : null;
  staffInfoPopoverFocusTrigger = null;
  for (const pop of document.querySelectorAll(".staff-info-popover")) {
    if (!(pop instanceof HTMLElement) || pop === except) continue;
    pop.hidden = true;
    const id = pop.id;
    if (!id) continue;
    for (const trigger of document.querySelectorAll(`[aria-controls="${id}"]`)) {
      if (trigger instanceof HTMLElement) trigger.setAttribute("aria-expanded", "false");
    }
  }
  if (triggerToFocus instanceof HTMLButtonElement) triggerToFocus.focus();
}

/**
 * Toggle one popover for a trigger button.
 * @param {HTMLButtonElement} trigger
 * @returns {void}
 */
function toggleStaffInfoPopover(trigger) {
  const targetId = String(trigger.getAttribute("aria-controls") || "");
  const pop = targetId ? document.getElementById(targetId) : null;
  if (!(pop instanceof HTMLElement)) return;
  const willOpen = pop.hidden;
  closeStaffInfoPopovers(willOpen ? pop : null);
  pop.hidden = !willOpen;
  trigger.setAttribute("aria-expanded", willOpen ? "true" : "false");
  if (willOpen) {
    staffInfoPopoverFocusTrigger = trigger;
    const rect = trigger.getBoundingClientRect();
    pop.style.top = `${Math.min(rect.bottom + 6, window.innerHeight - 12)}px`;
    pop.style.left = `${Math.min(rect.left, window.innerWidth - 292)}px`;
    pop.querySelector(".staff-info-popover-close")?.focus();
  }
}

/**
 * Wire triggers, close buttons, Esc, and outside clicks.
 * @returns {void}
 */
export function bindStaffInfoPopovers() {
  document.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const trigger = target?.closest(".staff-info-trigger");
    if (trigger instanceof HTMLButtonElement) {
      event.preventDefault();
      event.stopPropagation();
      toggleStaffInfoPopover(trigger);
      return;
    }
    const inside = target?.closest(".staff-info-popover");
    if (inside) return;
    closeStaffInfoPopovers();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeStaffInfoPopovers(null, { focusTrigger: true });
  });
  for (const closeBtn of document.querySelectorAll(".staff-info-popover-close")) {
    closeBtn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      closeStaffInfoPopovers();
      const pop = closeBtn.closest(".staff-info-popover");
      const id = pop?.id;
      if (id) {
        const trigger = document.querySelector(`[aria-controls="${id}"]`);
        if (trigger instanceof HTMLElement) trigger.focus();
      }
    });
  }
  for (const trigger of document.querySelectorAll(".staff-info-trigger")) {
    trigger.addEventListener("keydown", (event) => {
      if (!(trigger instanceof HTMLButtonElement)) return;
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggleStaffInfoPopover(trigger);
      }
    });
  }
}

/**
 * Build a 16px info trigger wired to an existing popover id.
 * @param {string} popoverId
 * @param {string} ariaLabel
 * @returns {string}
 */
export function staffInfoTriggerHtml(popoverId, ariaLabel) {
  const label = String(ariaLabel || "More information").replace(/"/g, "&quot;");
  const id = String(popoverId || "").replace(/"/g, "&quot;");
  return `<button type="button" class="staff-info-trigger" aria-controls="${id}" aria-expanded="false" aria-label="${label}">ⓘ</button>`;
}

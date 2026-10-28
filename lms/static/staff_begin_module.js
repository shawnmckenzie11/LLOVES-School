/**
 * Begin new module on Attendance & Participation (one section).
 * Opens the confirm dialog, posts the open module, and reloads.
 */
(function () {
  const openButton = document.getElementById("begin-new-module");
  const dialog = document.getElementById("begin-mod-dialog");
  const cancel = document.getElementById("begin-mod-cancel");
  const confirmButton = document.getElementById("begin-mod-confirm");
  const reason = document.getElementById("begin-mod-reason");
  const pastSelect = document.getElementById("begin-mod-past-select");
  const toast = document.getElementById("begin-mod-toast");

  if (toast && !toast.hidden) {
    const url = new URL(window.location.href);
    url.searchParams.delete("wrapped");
    url.searchParams.delete("started");
    window.history.replaceState({}, "", url);
  }

  function showPast(module) {
    document.querySelectorAll(".begin-mod-past-panel").forEach((panel) => {
      panel.hidden = panel.getAttribute("data-past-module") !== module;
    });
  }

  if (pastSelect) {
    showPast(pastSelect.value);
    pastSelect.addEventListener("change", () => showPast(pastSelect.value));
  }

  if (openButton && dialog && cancel) {
    openButton.addEventListener("click", () => {
      if (openButton.disabled) return;
      if (!dialog.open) dialog.showModal();
      cancel.focus();
    });
    cancel.addEventListener("click", () => dialog.close());
  }

  async function post(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    return { response, payload };
  }

  if (confirmButton && openButton && dialog) {
    confirmButton.addEventListener("click", async () => {
      if (confirmButton.disabled || openButton.disabled) return;
      confirmButton.disabled = true;
      openButton.disabled = true;
      try {
        const { response, payload } = await post(openButton.dataset.url, {
          from_module: openButton.dataset.fromModule,
        });
        if (!response.ok) {
          if (reason) {
            reason.hidden = false;
            reason.textContent = payload.error || "Try again.";
          }
          confirmButton.disabled = false;
          openButton.disabled = false;
          return;
        }
        const url = new URL(window.location.href);
        if (payload.changed) {
          url.searchParams.set("wrapped", String(payload.from_number));
          url.searchParams.set("started", String(payload.next_number));
        }
        window.location.assign(url);
      } catch (err) {
        confirmButton.disabled = false;
        openButton.disabled = false;
      }
    });
  }
})();

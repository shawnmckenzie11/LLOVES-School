/**
 * XHR upload progress + status poll for staff Common Cartridge installs.
 */

const form = document.getElementById("module-pack-form");
const fileInput =
  document.getElementById("attach_module_pack") ||
  document.getElementById("module_pack");
const submitBtn = document.getElementById("module-pack-submit");
const progressWrap = document.getElementById("pack-progress");
const progressBar = document.getElementById("pack-progress-bar");
const progressDetail = document.getElementById("pack-progress-detail");
const classId = Number(form?.dataset.classId || 0);
const POLL_MS = 700;

/**
 * Status URL for the current form (IT library upload or leftover staff path).
 * @returns {string}
 */
function statusEndpoint() {
  return form?.dataset.statusUrl || (classId ? `/staff/class/${classId}/module-pack/status` : "");
}

let pollTimer = 0;

/**
 * Show the progress region with a status sentence.
 * @param {string} detail
 */
function showProgress(detail) {
  if (progressWrap) progressWrap.hidden = false;
  if (progressDetail) progressDetail.textContent = detail;
}

/**
 * Set a determinate percent, or omit value for an indeterminate bar.
 * @param {number|null} percent
 */
function setBar(percent) {
  if (!progressBar) return;
  if (percent == null || !Number.isFinite(percent)) {
    progressBar.removeAttribute("value");
    return;
  }
  progressBar.max = 100;
  progressBar.value = Math.max(0, Math.min(100, percent));
}

/**
 * Re-enable the form after a failed install.
 * @param {string} message
 */
function fail(message) {
  stopPoll();
  setBar(null);
  showProgress(message);
  if (submitBtn) submitBtn.disabled = false;
  if (fileInput) fileInput.disabled = false;
  const banner = document.querySelector(".error");
  if (banner && banner.closest("section")) {
    banner.textContent = message;
  }
}

/**
 * Clear the status poll interval.
 */
function stopPoll() {
  if (pollTimer) {
    window.clearInterval(pollTimer);
    pollTimer = 0;
  }
}

/**
 * Read the server status file via GET.
 * @returns {Promise<{stage?: string, detail?: string, error?: string|null, busy?: boolean, ok?: boolean}>}
 */
async function fetchStatus() {
  const response = await fetch(statusEndpoint(), {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`Status HTTP ${response.status}`);
  }
  return response.json();
}

/**
 * Apply a status payload to the detail line.
 * @param {{stage?: string, detail?: string, error?: string|null, busy?: boolean}} status
 * @param {string} fallback
 */
function paintStatus(status, fallback) {
  const detail =
    (status.stage === "error" && (status.error || status.detail)) ||
    status.detail ||
    fallback;
  showProgress(detail);
}

/**
 * Poll until unpack finishes, then follow the success URL.
 * @param {string} redirectUrl
 */
function pollUntilDone(redirectUrl) {
  setBar(null);
  showProgress(
    "Unpacking Common Cartridge… this can take a few minutes"
  );
  const tick = async () => {
    try {
      const status = await fetchStatus();
      if (status.stage === "error") {
        fail(status.error || status.detail || "Could not install that module pack.");
        return;
      }
      if (status.stage === "done") {
        stopPoll();
        showProgress(status.detail || "Module pack installed.");
        window.location.href = redirectUrl;
        return;
      }
      if (status.busy || status.detail) {
        paintStatus(
          status,
          "Unpacking Common Cartridge… this can take a few minutes"
        );
      }
    } catch {
      // Keep polling; a single missed read should not abort a long unpack.
    }
  };
  stopPoll();
  tick();
  pollTimer = window.setInterval(tick, POLL_MS);
}

/**
 * POST the cartridge with upload-progress events, then poll unpack stages.
 * @param {FormData} body
 */
function uploadPack(body) {
  const xhr = new XMLHttpRequest();
  xhr.open("POST", form.action);
  xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
  xhr.setRequestHeader("Accept", "application/json");
  xhr.upload.onprogress = (event) => {
    if (!event.lengthComputable) {
      showProgress("Uploading…");
      setBar(null);
      return;
    }
    const percent = Math.round((event.loaded / event.total) * 100);
    setBar(percent);
    showProgress(`Uploading ${percent}%`);
  };
  xhr.onload = () => {
    let data = {};
    try {
      data = JSON.parse(xhr.responseText || "{}");
    } catch {
      data = {};
    }
    if (xhr.status >= 200 && xhr.status < 300 && data.ok) {
      const redirectUrl = data.redirect || `${window.location.pathname}?pack=ok`;
      if (data.installing) {
        pollUntilDone(redirectUrl);
        return;
      }
      stopPoll();
      window.location.href = redirectUrl;
      return;
    }
    const message =
      data.error ||
      (xhr.status === 413
        ? "Module pack is too large (max 250 MB)."
        : `Upload failed (HTTP ${xhr.status}).`);
    fail(message);
  };
  xhr.onerror = () => fail("Upload was interrupted. Try again.");
  xhr.onabort = () => fail("Upload was cancelled.");
  xhr.send(body);
}

form?.addEventListener("submit", (event) => {
  if (!fileInput?.files?.length) return;
  if (!statusEndpoint() && !classId) return;
  event.preventDefault();
  const body = new FormData(form);
  if (submitBtn) submitBtn.disabled = true;
  if (fileInput) fileInput.disabled = true;
  setBar(0);
  showProgress("Starting upload…");
  uploadPack(body);
});

/**
 * Resume the progress UI if a previous install is still running.
 */
async function resumeIfBusy() {
  if (!form || (!statusEndpoint() && !classId)) return;
  try {
    const status = await fetchStatus();
    if (status.stage === "error" && status.error) {
      showProgress(status.error);
      return;
    }
    if (!status.busy) return;
    if (submitBtn) submitBtn.disabled = true;
    if (fileInput) fileInput.disabled = true;
    paintStatus(
      status,
      "Unpacking Common Cartridge… this can take a few minutes"
    );
    setBar(null);
    pollUntilDone(`${window.location.pathname}?pack=ok`);
  } catch {
    // Leave the plain form available.
  }
}

resumeIfBusy();

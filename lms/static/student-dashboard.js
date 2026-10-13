/** MCK-208: dashboard live handover (poll + rejoin strip). */
const POLL_MS = 5000;

/**
 * Apply live handover from `/api/student/dashboard`.
 *
 * @param {Record<string, unknown>} live
 */
function applyDashboardLiveHandover(live) {
  if (!live || typeof live !== "object") return;
  if (live.in_live && live.redirect_url) {
    window.location.href = String(live.redirect_url);
    return;
  }
  const strip = document.getElementById("student-live-rejoin-strip");
  if (strip) {
    strip.hidden = !live.show_rejoin_strip;
  }
}

/** Poll for live start while the student stays on the dashboard. */
async function pollDashboardLive() {
  try {
    const res = await fetch("/api/student/dashboard", { credentials: "same-origin" });
    if (!res.ok) return;
    const data = await res.json();
    if (!data?.ok) return;
    applyDashboardLiveHandover(data.live);
  } catch (_err) {
    /* ignore transient network errors */
  }
}

pollDashboardLive();
window.setInterval(pollDashboardLive, POLL_MS);

/**
 * Attendance & Participation — Roster tab (MCK-207): PIN slips and teacher PIN set.
 */

const root = document.getElementById("roster-root");
if (root) {
  const classId = root.dataset.classId;
  const base = `/api/classes/${classId}`;
  const policy = document.getElementById("require-catchup-pin-change");

  const postJson = async (url, body) => {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      window.alert(data.error || "Could not save.");
      return false;
    }
    return true;
  };

  if (policy) {
    policy.addEventListener("change", async () => {
      const ok = await postJson(`${base}/catchup-pin-policy`, {
        require_pin_change: policy.checked,
      });
      if (!ok) policy.checked = !policy.checked;
    });
  }

  root.querySelectorAll(".pin-set").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const sid = btn.dataset.sid;
      const input = root.querySelector(`[data-pin="${sid}"]`);
      const pin = input && input.value ? String(input.value) : "";
      const res = await fetch(`${base}/students/${sid}/pin`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pin }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        window.alert(data.error || "Could not set PIN.");
        return;
      }
      btn.textContent = "Saved";
      window.setTimeout(() => {
        btn.textContent = "Set";
      }, 1500);
      if (input) input.value = "";
    });
  });
}

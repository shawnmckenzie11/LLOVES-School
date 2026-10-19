/**
 * Attendance & Participation — Roster & PINs.
 *
 * PINs stay masked. Show and print fetch a default slip only after a click,
 * and Show clears itself after 10 seconds. Nothing here writes a PIN to the console.
 */

const root = document.getElementById("roster-root");
const MASK = "••••";
const SHOW_MS = 10000;

if (root) {
  const classId = root.dataset.classId;
  const base = `/api/classes/${classId}`;
  const policy = document.getElementById("require-catchup-pin-change");
  let shownCell = null;
  let hideTimer = 0;

  /**
   * POST JSON and report a failure without including a PIN.
   * @param {string} url
   * @param {Record<string, unknown>} body
   */
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

  /**
   * Fetch one default slip. Returns ``""`` when the student has none.
   * @param {string} sid
   */
  const fetchSlip = async (sid) => {
    const res = await fetch(`${base}/students/${sid}/default-pin-slip`, { cache: "no-store" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return "";
    return String(data.slip || "");
  };

  /**
   * Put the mask back on the open PIN cell.
   */
  const maskShown = () => {
    window.clearTimeout(hideTimer);
    if (shownCell) shownCell.textContent = MASK;
    shownCell = null;
  };

  /**
   * Print slip lines in a throwaway frame so the roster page stays masked.
   * @param {string[]} lines
   */
  const printLines = (lines) => {
    if (!lines.length) {
      window.alert("No default slips to print.");
      return;
    }
    const frame = document.createElement("iframe");
    frame.setAttribute("aria-hidden", "true");
    frame.style.position = "fixed";
    frame.style.left = "-10000px";
    frame.style.width = "0";
    frame.style.height = "0";
    document.body.appendChild(frame);
    const doc = frame.contentDocument;
    if (!doc) {
      frame.remove();
      return;
    }
    const body = lines.map((line) => `<p>${line.replace(/[&<>]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[ch]))}</p>`).join("");
    doc.open();
    doc.write(`<!DOCTYPE html><html><body>${body}</body></html>`);
    doc.close();
    frame.contentWindow?.focus();
    frame.contentWindow?.print();
    window.setTimeout(() => frame.remove(), 1000);
  };

  if (policy) {
    policy.addEventListener("change", async () => {
      const ok = await postJson(`${base}/catchup-pin-policy`, {
        require_pin_change: policy.checked,
      });
      if (!ok) policy.checked = !policy.checked;
    });
  }

  root.querySelectorAll(".ap-roster-show").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const sid = btn.dataset.sid || "";
      const cell = root.querySelector(`.ap-roster-pin[data-sid="${sid}"]`);
      if (!cell) return;
      maskShown();
      const slip = await fetchSlip(sid);
      const digits = slip.replace(/\D/g, "").slice(0, 4);
      if (!digits) {
        window.alert("No default PIN to show.");
        return;
      }
      cell.textContent = digits;
      shownCell = cell;
      hideTimer = window.setTimeout(maskShown, SHOW_MS);
    });
  });

  root.querySelectorAll(".ap-roster-print").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const sid = btn.dataset.sid || "";
      const slip = await fetchSlip(sid);
      if (!slip) {
        window.alert("No default slip to print.");
        return;
      }
      const name = btn.closest("tr")?.querySelector("td")?.childNodes[0]?.textContent?.trim() || "";
      printLines([name ? `${name} — ${slip}` : slip]);
    });
  });

  const printAll = document.getElementById("print-all-default-slips");
  if (printAll) {
    printAll.addEventListener("click", async () => {
      const res = await fetch(`${base}/default-pin-slips`, { cache: "no-store" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        window.alert(data.error || "Could not print slips.");
        return;
      }
      const lines = (data.slips || []).map((row) => `${row.name} — ${row.slip}`);
      printLines(lines);
    });
  }

  root.querySelectorAll(".ap-roster-edit").forEach((btn) => {
    btn.addEventListener("click", () => {
      const editor = btn.parentElement?.querySelector(".ap-roster-editor");
      if (!editor) return;
      editor.hidden = false;
      editor.querySelector("input")?.focus();
    });
  });

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
      const row = btn.closest("tr");
      const status = row?.querySelector("[data-pin-status]");
      if (status) status.textContent = "Student set";
      row?.querySelectorAll(".ap-roster-show, .ap-roster-print").forEach((el) => el.remove());
      const cell = row?.querySelector(".ap-roster-pin");
      if (cell) {
        if (shownCell === cell) maskShown();
        cell.textContent = MASK;
      }
      if (input) input.value = "";
      const editor = btn.closest(".ap-roster-editor");
      if (editor) editor.hidden = true;
    });
  });
}

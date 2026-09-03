import { api, hideError, showError } from "/static/common.js";

const root = document.getElementById("modules-root");
const classId = Number(root?.dataset.classId || 0);
const nav = document.getElementById("module-nav");
const frame = document.getElementById("module-frame");

/**
 * Load the stored module outline and bind the left nav.
 */
async function loadNav() {
  hideError("#error");
  const data = await api(`/api/staff/class/${classId}/modules`);
  if (!nav) return;
  if (data.empty) {
    nav.innerHTML = `<p class="hint">${data.message || "No module pack for this course yet."}</p>`;
    return;
  }
  let html = '<ul class="nav-list">';
  for (const mod of data.modules || []) {
    html += `<li class="mod">${escapeText(mod.title)}</li>`;
    for (const item of mod.items || []) {
      if (item.kind === "header") {
        html += `<li class="sub-header">${escapeText(item.title)}</li>`;
        continue;
      }
      const params = new URLSearchParams({ item: String(item.id) });
      const url = `/staff/class/${classId}/module-item?${params}`;
      html += `<li><button type="button" data-kind="${escapeText(item.component_type)}" data-url="${escapeText(url)}">${escapeText(item.title)}</button></li>`;
    }
  }
  html += "</ul>";
  nav.innerHTML = html;
}

/**
 * Escape text for HTML.
 * @param {unknown} value
 */
function escapeText(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

nav?.addEventListener("click", (event) => {
  const btn = event.target instanceof Element ? event.target.closest("[data-url]") : null;
  if (!btn || !frame) return;
  frame.src = btn.dataset.url;
});

loadNav().catch((err) => {
  if (nav) nav.textContent = err instanceof Error ? err.message : String(err);
});

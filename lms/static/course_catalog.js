import { api } from "/static/common.js";

const root = document.getElementById("catalog-root");
const classId = Number(root?.dataset.classId || 0);
const kind = String(root?.dataset.kind || "pages");
const list = document.getElementById("catalog-list");
const frame = document.getElementById("catalog-frame");

const PREVIEW_KIND = {
  pages: "page",
  assignments: "assignment",
  quizzes: "quiz",
  "question-banks": "bank",
};

/**
 * Escape text for HTML interpolation.
 * @param {unknown} value
 */
function escapeText(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * Build the secondary label shown under a catalog entry.
 * @param {Record<string, unknown>} item
 */
function subtitle(item) {
  if (kind === "pages") return String(item.kind ?? "");
  if (kind === "assignments") {
    return item.points ? `${item.points} points` : "ungraded";
  }
  if (kind === "quizzes" || kind === "question-banks") {
    const n = Number(item.question_count || 0);
    return `${n} question${n === 1 ? "" : "s"}`;
  }
  return "";
}

/**
 * Load the catalog for this tab and bind the preview pane.
 */
async function loadCatalog() {
  const data = await api(`/api/staff/class/${classId}/components/${kind}`);
  if (!list) return;
  if (data.empty) {
    list.innerHTML = `<p class="hint">${escapeText(data.message || "Nothing imported yet.")}</p>`;
    return;
  }
  const previewKind = PREVIEW_KIND[kind];
  let html = '<ul class="nav-list">';
  for (const item of data.items || []) {
    const meta = subtitle(item);
    const label = `${escapeText(item.title)}${meta ? ` <span class="hint">(${escapeText(meta)})</span>` : ""}`;
    if (previewKind) {
      const url = `/staff/class/${classId}/component/${previewKind}/${item.id}`;
      html += `<li><button type="button" data-url="${escapeText(url)}">${label}</button></li>`;
    } else {
      html += `<li>${label}</li>`;
    }
  }
  html += "</ul>";
  list.innerHTML = html;
}

list?.addEventListener("click", (event) => {
  const btn = event.target instanceof Element ? event.target.closest("button") : null;
  if (!btn?.dataset.url || !frame) return;
  frame.removeAttribute("srcdoc");
  frame.src = btn.dataset.url;
});

loadCatalog().catch((err) => {
  if (list) list.textContent = err instanceof Error ? err.message : String(err);
});

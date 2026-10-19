import { api, escapeHtml } from "/static/common.js";

const root = document.getElementById("catalog-root");
const classId = Number(root?.dataset.classId || 0);
const kind = String(root?.dataset.kind || "pages");
const list = document.getElementById("catalog-list");
const frame = document.getElementById("catalog-frame");

const PREVIEW_KIND = {
  pages: "page",
  assignments: "assignment",
  quizzes: "quiz",
};

const KIND_LABEL = {
  pages: "Page",
  assignments: "Assignment",
  quizzes: "Quiz",
};

/**
 * Escape text for HTML interpolation.
 * @param {unknown} value
 */
function escapeText(value) {
  return escapeHtml(value);
}

/**
 * Build the secondary label shown under a catalog entry.
 * @param {Record<string, unknown>} item
 * @param {string} itemKind
 */
function subtitle(item, itemKind) {
  if (itemKind === "pages") return String(item.kind ?? "");
  if (itemKind === "assignments") {
    return item.points ? `${item.points} points` : "ungraded";
  }
  if (itemKind === "quizzes") {
    const n = Number(item.question_count || 0);
    return `${n} question${n === 1 ? "" : "s"}`;
  }
  return "";
}

/**
 * Render catalog rows. ``catalogKind`` picks the preview route.
 * @param {Array<Record<string, unknown>>} items
 * @param {string} emptyMessage
 */
function renderItems(items, emptyMessage) {
  if (!list) return;
  if (!items.length) {
    list.innerHTML = `<p class="hint">${escapeText(emptyMessage || "Nothing imported yet.")}</p>`;
    return;
  }
  let html = '<ul class="nav-list">';
  for (const item of items) {
    const itemKind = String(item.catalogKind || kind);
    const meta = subtitle(item, itemKind);
    const kindBit = kind === "all" ? `<span class="hint">${escapeText(KIND_LABEL[itemKind] || itemKind)}</span> ` : "";
    const label = `${kindBit}${escapeText(item.title)}${meta ? ` <span class="hint">(${escapeText(meta)})</span>` : ""}`;
    const previewKind = PREVIEW_KIND[itemKind];
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

/**
 * Load one catalog kind and tag each row with that kind.
 * @param {string} itemKind
 */
async function loadKind(itemKind) {
  const data = await api(`/api/staff/class/${classId}/components/${itemKind}`);
  return {
    message: String(data.message || ""),
    items: (data.items || []).map((item) => ({ ...item, catalogKind: itemKind })),
  };
}

/**
 * Load the catalog for this tab and bind the preview pane.
 * ``all`` merges Pages, Assignments and Quizzes.
 */
async function loadCatalog() {
  if (!root || !list) return;
  if (kind === "all") {
    const groups = await Promise.all(["pages", "assignments", "quizzes"].map(loadKind));
    renderItems(groups.flatMap((group) => group.items), "Nothing imported yet.");
    return;
  }
  const data = await loadKind(kind);
  if (!data.items.length) {
    renderItems([], data.message || "Nothing imported yet.");
    return;
  }
  renderItems(data.items, data.message);
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

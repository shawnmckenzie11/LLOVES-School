import { api, escapeHtml } from "/static/common.js";
import { mountBankMcPicker } from "/static/bank_mc_picker.js";

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

/** @type {Record<string, unknown> | null} */
let selectedMc = null;

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
 * Paint the overlay edit drawer for one selected MC row.
 * @param {Record<string, unknown>} item
 */
function paintMcEditor(item) {
  if (!(frame instanceof HTMLElement)) return;
  selectedMc = item;
  const options = Array.isArray(item.options) ? item.options : [];
  frame.removeAttribute("src");
  frame.srcdoc = `<!DOCTYPE html><html><head><meta charset="utf-8">
    <link rel="stylesheet" href="/static/staff-shell.css"></head>
    <body class="staff-shell bank-mc-editor">
      <form id="bank-mc-editor-form" class="card">
        <h2>Edit MC overlay</h2>
        <p class="hint compact">${escapeText(String(item.text || item.question_title || ""))}</p>
        <label>Stem
          <textarea name="stem_text" rows="3" required>${escapeText(
            item.text || ""
          )}</textarea>
        </label>
        <fieldset>
          <legend>Options</legend>
          ${options
            .map(
              (opt, index) => `<label>${String.fromCharCode(65 + index)}
                <input name="option_${index}" value="${escapeText(opt)}" required>
              </label>`
            )
            .join("")}
        </fieldset>
        <label>Correct answer
          <select name="correct_answer" required>
            ${options
              .map(
                (_opt, index) =>
                  `<option value="${String.fromCharCode(65 + index)}"${
                    String(item.correct_answer || "").toUpperCase() ===
                    String.fromCharCode(65 + index)
                      ? " selected"
                      : ""
                  }>${String.fromCharCode(65 + index)}</option>`
              )
              .join("")}
          </select>
        </label>
        <label>Points
          <input name="points" type="number" min="0" step="0.5" value="${escapeText(
            item.points ?? 1
          )}">
        </label>
        <p class="row-actions">
          <button type="submit">Save overlay</button>
        </p>
        <p class="hint compact" id="bank-mc-editor-status" hidden></p>
      </form>
    </body></html>`;
  frame.addEventListener(
    "load",
    () => {
      const doc = frame.contentDocument;
      const form = doc?.getElementById("bank-mc-editor-form");
      form?.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!(form instanceof HTMLFormElement)) return;
        const status = doc?.getElementById("bank-mc-editor-status");
        const stem = String(new FormData(form).get("stem_text") || "").trim();
        const nextOptions = options.map((_opt, index) =>
          String(new FormData(form).get(`option_${index}`) || "").trim()
        );
        const correct = String(new FormData(form).get("correct_answer") || "").trim();
        const pointsRaw = new FormData(form).get("points");
        const points =
          pointsRaw === null || pointsRaw === ""
            ? null
            : Number(pointsRaw);
        if (status instanceof HTMLElement) {
          status.hidden = false;
          status.textContent = "Saving…";
        }
        try {
          await api(
            `/api/staff/class/${classId}/question-overlays/${Number(
              item.question_id || 0
            )}`,
            {
              method: "PATCH",
              body: JSON.stringify({
                stem_text: stem,
                options: nextOptions,
                correct_answer: correct,
                points,
              }),
            }
          );
          if (status instanceof HTMLElement) {
            status.textContent = "Saved. Search results will use this overlay.";
          }
          await mountQuestionBankBrowser();
        } catch (err) {
          if (status instanceof HTMLElement) {
            status.textContent =
              err instanceof Error ? err.message : String(err || "Save failed");
          }
        }
      });
    },
    { once: true }
  );
}

/**
 * Question Banks tab: module-scoped search + overlay editor.
 */
async function mountQuestionBankBrowser() {
  if (!list || !frame) return;
  list.innerHTML = `<div id="bank-mc-picker-mount"></div>`;
  const mount = document.getElementById("bank-mc-picker-mount");
  frame.removeAttribute("srcdoc");
  frame.src = "about:blank";
  if (selectedMc) {
    paintMcEditor(selectedMc);
  } else {
    frame.srcdoc = `<!DOCTYPE html><html><body class="staff-shell"><p class="hint">Select an MC to edit its overlay.</p></body></html>`;
  }
  await mountBankMcPicker({
    classId,
    moduleNumber: "M1",
    mode: "browse",
    showModuleSelector: true,
    mount,
    onSelect: async (item) => {
      paintMcEditor(item);
    },
  });
}

/**
 * Load the catalog for this tab and bind the preview pane.
 */
async function loadCatalog() {
  if (kind === "question-banks") {
    await mountQuestionBankBrowser();
    return;
  }
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

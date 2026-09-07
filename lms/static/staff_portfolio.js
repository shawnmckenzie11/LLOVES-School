/**
 * Local Portfolio tab: Build (HTML/Doc) and Marking sheets.
 */
import { api, escapeHtml, hideError, showError } from "/static/common.js";

const root = document.getElementById("portfolio-root");
const classId = Number(root?.dataset.classId || 0);
const view = String(root?.dataset.view || "build");

/** @type {any} */
let context = null;

/**
 * @param {string} id
 * @returns {HTMLElement | null}
 */
function $(id) {
  return document.getElementById(id);
}

/**
 * Selected module number.
 * @returns {number}
 */
function moduleNumber() {
  const select = $("portfolio-module");
  if (select instanceof HTMLSelectElement) {
    return Number(select.value) || 1;
  }
  return 1;
}

/**
 * Collect teacher lens edits from the Build card.
 * @returns {object}
 */
function lensEdits() {
  const questions = {};
  root?.querySelectorAll("[data-lens-key]").forEach((el) => {
    if (!(el instanceof HTMLTextAreaElement)) return;
    const key = el.dataset.lensKey;
    if (!key) return;
    questions[key] = questions[key] || {};
    questions[key].lens = el.value;
  });
  root?.querySelectorAll("[data-words-key]").forEach((el) => {
    if (!(el instanceof HTMLInputElement)) return;
    const key = el.dataset.wordsKey;
    if (!key) return;
    questions[key] = questions[key] || {};
    questions[key].useful_words = el.value;
  });
  return { questions };
}

/**
 * Fill the module dropdown from context API.
 * @returns {Promise<any>}
 */
async function fillModuleSelect() {
  const data = await api(
    `/api/classes/${classId}/portfolio/context?module=${moduleNumber()}`
  );
  const select = $("portfolio-module");
  if (select instanceof HTMLSelectElement && Array.isArray(data.modules)) {
    const current = String(data.module_number || 1);
    select.innerHTML = data.modules
      .map(
        (row) =>
          `<option value="${row.module_number}"${String(row.module_number) === current ? " selected" : ""}>M${row.module_number} · ${escapeHtml(row.title || "")}</option>`
      )
      .join("");
  }
  return data;
}

/**
 * Paint assembled context and lens editors.
 */
function paintContext() {
  const box = $("portfolio-context");
  const lenses = $("portfolio-lenses");
  if (!box || !context) return;
  const overalls = (context.overalls || [])
    .map((row) => `<li><code>${escapeHtml(row.code)}</code> ${escapeHtml(row.statement)}</li>`)
    .join("");
  const specifics = (context.specifics || [])
    .slice(0, 8)
    .map((row) => `<li><code>${escapeHtml(row.code)}</code> ${escapeHtml(row.statement)}</li>`)
    .join("");
  box.innerHTML = `
    <p><b>${escapeHtml(context.course_code)}</b> · ${escapeHtml(context.module_title || "")}</p>
    <p class="hint compact">Strand ${escapeHtml(context.strand || "")} · ${escapeHtml(context.strand_name || "")} · lens source: ${escapeHtml(context.lens_source || "")}</p>
    <p class="hint compact"><b>Useful words:</b> ${(context.useful_words || []).map(escapeHtml).join(" · ") || "—"}</p>
    <h3>Overalls</h3>
    <ul>${overalls || "<li class='hint'>None on the map for this module.</li>"}</ul>
    <h3>Specifics (from curriculum JSON)</h3>
    <ul>${specifics || "<li class='hint'>None found.</li>"}</ul>
  `;
  const portfolio = context.portfolio || {};
  const cores = context.cores || {};
  const keys = ["connect", "justify", "transfer"];
  if (lenses) {
    lenses.innerHTML = keys
      .map((key) => {
        const core = cores[key] || {};
        const q = (portfolio.questions || {})[key] || {};
        const words = Array.isArray(q.useful_words) ? q.useful_words.join(" · ") : "";
        return `<section class="card">
          <h3>${escapeHtml(core.verb || key)}</h3>
          <p class="hint compact">${escapeHtml(core.course_question || "")}</p>
          <label class="field">Module lens
            <textarea data-lens-key="${escapeHtml(key)}" rows="3">${escapeHtml(q.lens || "")}</textarea>
          </label>
          <label class="field">Useful words
            <input type="text" data-words-key="${escapeHtml(key)}" value="${escapeHtml(words)}">
          </label>
        </section>`;
      })
      .join("");
  }
}

/**
 * Load Build context for the selected module.
 */
async function loadBuild() {
  hideError("#portfolio-error");
  context = await fillModuleSelect();
  paintContext();
}

/**
 * Open generated HTML in a new browser tab.
 * @param {string} html
 * @param {string} filename
 */
function openHtmlTab(html, filename) {
  const blob = new Blob([html], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const opened = window.open(url, "_blank", "noopener");
  if (!opened) {
    const a = document.createElement("a");
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener";
    a.download = filename || "portfolio.html";
    a.click();
  }
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/**
 * Trigger a local download.
 * @param {Blob} blob
 * @param {string} filename
 */
function downloadBlob(blob, filename) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  window.setTimeout(() => URL.revokeObjectURL(a.href), 30_000);
}

/**
 * Generate HTML and open it in a new tab.
 */
async function generateHtml() {
  hideError("#portfolio-error");
  const status = $("portfolio-build-status");
  const data = await api(`/api/classes/${classId}/portfolio/generate`, {
    method: "POST",
    body: JSON.stringify({
      module: moduleNumber(),
      edits: lensEdits(),
      drive: false,
    }),
  });
  context = { ...context, portfolio: data.portfolio };
  paintContext();
  if (data.html) {
    openHtmlTab(data.html, data.filename || "portfolio.html");
  }
  if (status) {
    status.hidden = false;
    status.textContent = "Saved JSON and opened HTML in a new tab.";
  }
}

/**
 * Download the two-column Google-Doc-shaped HTML as a .doc file.
 */
async function generateGoogleDoc() {
  hideError("#portfolio-error");
  const status = $("portfolio-build-status");
  const data = await api(`/api/classes/${classId}/portfolio/generate`, {
    method: "POST",
    body: JSON.stringify({
      module: moduleNumber(),
      edits: lensEdits(),
      drive: false,
    }),
  });
  context = { ...context, portfolio: data.portfolio };
  paintContext();
  if (data.doc_html) {
    downloadBlob(
      new Blob([data.doc_html], { type: "application/msword;charset=utf-8" }),
      data.doc_filename || "portfolio.doc"
    );
  }
  if (status) {
    status.hidden = false;
    status.textContent = "Saved JSON and downloaded the Google Doc file.";
  }
}

const LEVELS = ["", "L1", "L2", "L3", "L4"];
const CRITERIA = [
  ["connect", "Connect"],
  ["justify", "Justify"],
  ["transfer", "Transfer"],
  ["communicate", "Communicate"],
];

/**
 * List filenames found for this module folder.
 * @param {any} data
 */
function paintFileList(data) {
  const hint = $("portfolio-submissions-hint");
  const files = data.files || data.submissions || [];
  if (hint) {
    const driveN = files.filter((s) => s.source === "drive").length;
    const localN = files.filter((s) => s.source === "local").length;
    hint.textContent = data.drive_error
      ? `Drive unavailable (${data.drive_error}). Local fallback: ${localN} file(s) in ${data.drive_path || "the module folder"}.`
      : `Folder ${data.drive_path || ""} · ${driveN} Drive file(s), ${localN} local file(s).`;
  }
  const list = $("portfolio-file-list");
  if (!list) return;
  if (!files.length) {
    list.innerHTML = "<li class='hint'>No files found in this folder yet.</li>";
    return;
  }
  list.innerHTML = files
    .map(
      (row) =>
        `<li>${escapeHtml(row.name || "")}${row.source ? ` <span class="hint">(${escapeHtml(row.source)})</span>` : ""}</li>`
    )
    .join("");
}

/**
 * Paint rubric and look-for sheets.
 * @param {any} data
 */
function paintMarkingSheets(data) {
  const rubricBody = $("portfolio-rubric-sheet")?.querySelector("tbody");
  const rubricHead = $("portfolio-rubric-sheet")?.querySelector("thead tr");
  if (rubricHead) {
    rubricHead.innerHTML = `<th class="name">Student</th>${CRITERIA.map(
      ([, label]) => `<th>Sugg. ${escapeHtml(label)}</th><th>${escapeHtml(label)}</th>`
    ).join("")}`;
  }
  if (rubricBody) {
    rubricBody.innerHTML = (data.students || [])
      .map((row) => {
        const suggested = row.suggested?.levels || {};
        const override = row.override || {};
        const cells = CRITERIA.map(([key]) => {
          const sug = suggested[key] || "—";
          const ov = override[key] || "";
          const options = LEVELS.map(
            (lv) =>
              `<option value="${lv}"${lv === ov ? " selected" : ""}>${lv || "sugg."}</option>`
          ).join("");
          return `<td>${escapeHtml(sug)}</td><td><select data-override="${row.id}" data-criterion="${key}">${options}</select></td>`;
        }).join("");
        return `<tr><th class="name">${escapeHtml(row.codename || row.name || String(row.id))}</th>${cells}</tr>`;
      })
      .join("");
  }
  const lookHead = $("portfolio-lookfor-sheet")?.querySelector("thead tr");
  const lookBody = $("portfolio-lookfor-sheet")?.querySelector("tbody");
  const lookIds = data.lookfor_ids || [];
  if (lookHead) {
    lookHead.innerHTML = `<th class="name">Student</th>${lookIds
      .map((id) => `<th>${escapeHtml((data.lookfor_labels && data.lookfor_labels[id]) || id)}</th>`)
      .join("")}`;
  }
  if (lookBody) {
    lookBody.innerHTML = (data.students || [])
      .map((row) => {
        const tally = row.lookfors || {};
        const cells = lookIds.map((id) => `<td>${escapeHtml(String(tally[id] || 0))}</td>`).join("");
        return `<tr><th class="name">${escapeHtml(row.codename || row.name || String(row.id))}</th>${cells}</tr>`;
      })
      .join("");
  }
}

/**
 * Load marking sheets without running the classifier until the teacher asks.
 */
async function loadMarking() {
  const data = await api(
    `/api/classes/${classId}/portfolio/marking?module=${moduleNumber()}`
  );
  paintFileList(data);
  paintMarkingSheets(data);
}

/**
 * Run the local first-pass marker on files in the folder.
 */
async function runMarking() {
  hideError("#portfolio-error");
  const data = await api(`/api/classes/${classId}/portfolio/marking`, {
    method: "POST",
    body: JSON.stringify({ module: moduleNumber(), action: "run" }),
  });
  paintFileList(data);
  paintMarkingSheets(data);
}

$("portfolio-module")?.addEventListener("change", () => {
  const next = view === "marking" ? loadMarking() : loadBuild();
  next.catch((err) => showError("#portfolio-error", err));
});

$("portfolio-generate")?.addEventListener("click", () => {
  generateHtml().catch((err) => showError("#portfolio-error", err));
});

$("portfolio-drive")?.addEventListener("click", () => {
  generateGoogleDoc().catch((err) => showError("#portfolio-error", err));
});

$("portfolio-mark-run")?.addEventListener("click", () => {
  runMarking().catch((err) => showError("#portfolio-error", err));
});

$("portfolio-rubric-sheet")?.addEventListener("change", async (event) => {
  const select = event.target;
  if (!(select instanceof HTMLSelectElement) || !select.dataset.override) return;
  try {
    await api(`/api/classes/${classId}/portfolio/marking`, {
      method: "POST",
      body: JSON.stringify({
        module: moduleNumber(),
        student_id: Number(select.dataset.override),
        override: { [select.dataset.criterion]: select.value },
      }),
    });
  } catch (err) {
    showError("#portfolio-error", err);
  }
});

if (root && classId) {
  const start = view === "marking"
    ? fillModuleSelect().then(() => loadMarking())
    : loadBuild();
  start.catch((err) => showError("#portfolio-error", err));
}
